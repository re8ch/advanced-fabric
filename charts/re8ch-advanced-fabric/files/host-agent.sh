#!/bin/sh
set -eu

NODE_FILE="/desired/${NODE_NAME}.json"
READY=/run/advanced-fabric-ready
STATUS_FILE=/status/status.json
rm -f "${READY}"

host() { nsenter -t 1 -n chroot /host "$@"; }

publish_status() {
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if host ip link show cilium_vxlan >/dev/null 2>&1; then
    datapath=vxlan
    tunnel_interfaces='["cilium_vxlan"]'
  else
    datapath=native
    tunnel_interfaces='[]'
  fi
  frr_state=$(host systemctl is-active frr 2>/dev/null || true)
  bgp=$(host vtysh -c 'show bgp ipv4 unicast summary json' 2>/dev/null || printf '{}')
  rib=$(host vtysh -c 'show bgp ipv4 unicast json' 2>/dev/null || printf '{}')
  bgp_rib=$(printf '%s' "$rib" | jq -c '[((.routes // {}) | to_entries[]) | {prefix:.key,paths:[.value[] | {
    peer:(.peerId // .peerHostname // .nexthops[0].hostname // "unknown"),
    nextHops:[.nexthops[]? | (.ip // .hostname // "unknown")],asPath:(.path // ""),
    best:(if (.bestpath | type) == "object" then (.bestpath.overall == true) else (.bestpath == true) end),
    multipath:(.multipath == true)}]}]' 2>/dev/null || printf '[]')
  bfd=$(host vtysh -c 'show bfd peers json' 2>/dev/null || printf '[]')
  routes=$(host ip -j route show table main 2>/dev/null || printf '[]')
  ecmp=$(printf '%s' "$routes" | jq -c '[.[] | select(((.nexthops // []) | length) > 1) | {dst: (.dst // "default"), protocol, metric, nexthops: [.nexthops[] | {gateway, dev, weight}]}]' 2>/dev/null || printf '[]')
  peers=$(jq -c '.peers // []' "${NODE_FILE}")
  peer_routes=$(printf '%s' "$peers" | jq -c '[.[] | {name, internalIP, acceleratedIP, podCIDR}]')
  rankings=$(jq -c '.pathRankings // {}' "${NODE_FILE}")
  api_config=$(jq -c '.controlPlaneApi // {enabled:false,eligible:false}' "${NODE_FILE}")
  api_healthy=false
  if [ "$(printf '%s' "$api_config" | jq -r '.enabled and .eligible')" = true ]; then
    api_address=$(printf '%s' "$api_config" | jq -r '.healthCheck.address')
    api_port=$(printf '%s' "$api_config" | jq -r '.port')
    api_path=$(printf '%s' "$api_config" | jq -r '.healthCheck.path')
    api_timeout=$(printf '%s' "$api_config" | jq -r '.healthCheck.timeoutSeconds')
    if host curl --fail --silent --show-error --max-time "$api_timeout" --insecure \
      "https://${api_address}:${api_port}${api_path}" >/dev/null 2>&1; then
      api_healthy=true
    fi
  fi
  status=$(jq -cn \
    --arg node "$NODE_NAME" --arg observedAt "$now" --arg datapath "$datapath" \
    --arg frr "$frr_state" --argjson tunnels "$tunnel_interfaces" \
    --argjson bgp "$bgp" --argjson bfd "$bfd" --argjson ecmp "$ecmp" \
    --argjson bgpRib "$bgp_rib" --argjson peers "$peer_routes" --argjson rankings "$rankings" \
    --argjson controlPlaneApi "$api_config" --argjson controlPlaneApiHealthy "$api_healthy" \
    '{schemaVersion:"networking.re8ch.com/v1alpha1",node:$node,observedAt:$observedAt,
      datapath:{mode:$datapath,tunnelInterfaces:$tunnels},frr:{state:$frr,bgp:$bgp,bfd:$bfd},
      ecmpRoutes:$ecmp,bgpRib:$bgpRib,peerRoutes:$peers,pathRankings:$rankings,
      controlPlaneApi:($controlPlaneApi + {localHealthy:$controlPlaneApiHealthy})}')
  printf '%s\n' "$status" >"${STATUS_FILE}.tmp"
  mv "${STATUS_FILE}.tmp" "${STATUS_FILE}"
}

while [ ! -s "${NODE_FILE}" ]; do sleep 2; done
jq -e '.mode == "observe-only" or .mode == "guarded-apply"' "${NODE_FILE}" >/dev/null

# Runtime safety: the first release observes and validates.  It refuses to
# mutate even when applyEnabled is accidentally set until the controller has
# supplied a non-empty, checksum-bound transaction in a future guarded wave.
if jq -e '.applyEnabled == true' "${NODE_FILE}" >/dev/null; then
  echo "${NODE_NAME}: guarded apply requested but no signed transaction is present" >&2
  exit 1
fi

publish_status
touch "${READY}"
echo "${NODE_NAME}: Advanced Fabric observe-only host validation ready"
while sleep 30; do
  publish_status || true
  touch "${READY}"
done
