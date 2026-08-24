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
    api_timeout=$(printf '%s' "$api_config" | jq -r '.healthCheck.timeoutSeconds')
    if host timeout "$api_timeout" k3s kubectl --server=https://127.0.0.1:6443 \
      get --raw=/readyz 2>/dev/null | grep -qx ok; then
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

transaction() { jq -c '.transaction.spec' "${NODE_FILE}"; }
validate_transaction() {
  [ "$(jq -r '.transaction.algorithm' "${NODE_FILE}")" = sha256 ]
  expected=$(jq -r '.transaction.checksum' "${NODE_FILE}")
  actual=$(printf '%s' "$(transaction)" | sha256sum | awk '{print $1}')
  [ "${actual}" = "${expected}" ]
  transaction | jq -e --arg node "${NODE_NAME}" '.version == 1 and .node == $node and
    (.vip | test("^10\\.250\\.0\\.[0-9]{1,3}/32$")) and .vipInterface == "lo" and
    (.frrPrefixSequence >= 1 and .frrPrefixSequence <= 999)' >/dev/null
}
manage_fallback_routes() {
  action=$1
  transaction | jq -c '.fallbackRoutes[]?' | while read -r route; do
    destination=$(printf '%s' "${route}" | jq -r '.destination'); via=$(printf '%s' "${route}" | jq -r '.via // empty')
    dev=$(printf '%s' "${route}" | jq -r '.dev'); src=$(printf '%s' "${route}" | jq -r '.src // empty'); metric=$(printf '%s' "${route}" | jq -r '.metric')
    if [ "${action}" = apply ]; then
      if [ -n "${via}" ] && [ -n "${src}" ]; then host ip route replace "${destination}" via "${via}" dev "${dev}" src "${src}" metric "${metric}"
      elif [ -n "${via}" ]; then host ip route replace "${destination}" via "${via}" dev "${dev}" metric "${metric}"
      else host ip route replace "${destination}" dev "${dev}" metric "${metric}"; fi
    else host ip route del "${destination}" dev "${dev}" metric "${metric}" 2>/dev/null || true; fi
  done
}
manage_wireguard_allowed_ips() {
  action=$1; vip=$(transaction | jq -r '.vip')
  transaction | jq -r '.wireguardInterfaces[]?' | while read -r iface; do
    peers=$(host wg show "${iface}" peers); [ "$(printf '%s\n' "${peers}" | sed '/^$/d' | wc -l)" -eq 1 ]
    prefix=+${vip}; [ "${action}" = apply ] || prefix=-${vip}; host wg set "${iface}" peer "${peers}" allowed-ips "${prefix}"
  done
}
frr_vip() {
  action=$1; vip=$(transaction | jq -r '.vip'); asn=$(transaction | jq -r '.localAsn'); sequence=$(transaction | jq -r '.frrPrefixSequence')
  [ "${asn}" != null ] || return 0
  if [ "${action}" = apply ]; then network="network ${vip}"; else network="no network ${vip}"; fi
  host vtysh -c 'configure terminal' -c "router bgp ${asn}" -c 'address-family ipv4 unicast' -c "${network}" -c end >/dev/null
  transaction | jq -r '.frrExportPrefixLists[]?' | while read -r list; do
    if [ "${action}" = apply ]; then command="ip prefix-list ${list} seq ${sequence} permit ${vip}"; else command="no ip prefix-list ${list} seq ${sequence}"; fi
    host vtysh -c 'configure terminal' -c "${command}" -c end >/dev/null
  done
}
withdraw_vip() {
  frr_vip remove || true; delay=$(jq -r '.controlPlaneApi.healthCheck.withdrawDelaySeconds' "${NODE_FILE}"); [ "${delay}" -eq 0 ] || sleep "${delay}"
  host ip address del "$(transaction | jq -r '.vip')" dev "$(transaction | jq -r '.vipInterface')" 2>/dev/null || true
}
announce_vip() { host ip address replace "$(transaction | jq -r '.vip')" dev "$(transaction | jq -r '.vipInterface')"; frr_vip apply; }
api_healthy() {
  timeout_seconds=$(jq -r '.controlPlaneApi.healthCheck.timeoutSeconds' "${NODE_FILE}")
  host timeout "${timeout_seconds}" k3s kubectl --server=https://127.0.0.1:6443 get --raw=/readyz 2>/dev/null | grep -qx ok
}

while [ ! -s "${NODE_FILE}" ]; do sleep 2; done
validate_transaction; host systemctl is-active --quiet frr
successes=0; failures=0; announced=false
while :; do
  apply=$(jq -r '.mode == "guarded-apply" and .applyEnabled == true' "${NODE_FILE}"); guarded=$(transaction | jq -r '.guarded')
  if [ "${apply}" != true ]; then
    withdraw_vip; manage_wireguard_allowed_ips remove; manage_fallback_routes remove; successes=0; failures=0; announced=false
  else
    manage_fallback_routes apply; manage_wireguard_allowed_ips apply
    if [ "${guarded}" != true ]; then withdraw_vip; successes=0; failures=0; announced=false
    elif api_healthy; then
      successes=$((successes + 1)); failures=0; threshold=$(jq -r '.controlPlaneApi.healthCheck.successThreshold' "${NODE_FILE}")
      if [ "${announced}" = false ] && [ "${successes}" -ge "${threshold}" ]; then announce_vip; announced=true; fi
    else
      failures=$((failures + 1)); successes=0; threshold=$(jq -r '.controlPlaneApi.healthCheck.failureThreshold' "${NODE_FILE}")
      if [ "${announced}" = true ] && [ "${failures}" -ge "${threshold}" ]; then withdraw_vip; announced=false; fi
    fi
  fi
  publish_status || true; touch "${READY}"; sleep "$(jq -r '.controlPlaneApi.healthCheck.intervalSeconds' "${NODE_FILE}")"
done
