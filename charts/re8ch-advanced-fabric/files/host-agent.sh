#!/bin/sh
set -eu

NODE_FILE="/desired/${NODE_NAME}.json"
READY=/run/advanced-fabric-ready
STATUS_CM="advanced-fabric-node-$(printf '%s' "${NODE_NAME}" | tr '[:upper:]_.' '[:lower:]---')"
TOKEN_FILE=/var/run/secrets/kubernetes.io/serviceaccount/token
CA_FILE=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
API="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT_HTTPS}"
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
  bfd=$(host vtysh -c 'show bfd peers json' 2>/dev/null || printf '[]')
  routes=$(host ip -j route show table main 2>/dev/null || printf '[]')
  ecmp=$(printf '%s' "$routes" | jq -c '[.[] | select(((.nexthops // []) | length) > 1) | {dst: (.dst // "default"), protocol, metric, nexthops: [.nexthops[] | {gateway, dev, weight}]}]' 2>/dev/null || printf '[]')
  peers=$(jq -c '.peers // []' "${NODE_FILE}")
  peer_routes=$(printf '%s' "$peers" | jq -c '[.[] | {name, internalIP, acceleratedIP, podCIDR}]')
  rankings=$(jq -c '.pathRankings // {}' "${NODE_FILE}")
  status=$(jq -cn \
    --arg node "$NODE_NAME" --arg observedAt "$now" --arg datapath "$datapath" \
    --arg frr "$frr_state" --argjson tunnels "$tunnel_interfaces" \
    --argjson bgp "$bgp" --argjson bfd "$bfd" --argjson ecmp "$ecmp" \
    --argjson peers "$peer_routes" --argjson rankings "$rankings" \
    '{schemaVersion:"networking.re8ch.com/v1alpha1",node:$node,observedAt:$observedAt,
      datapath:{mode:$datapath,tunnelInterfaces:$tunnels},frr:{state:$frr,bgp:$bgp,bfd:$bfd},
      ecmpRoutes:$ecmp,peerRoutes:$peers,pathRankings:$rankings}')
  object=$(jq -cn --arg name "$STATUS_CM" --arg status "$status" \
    '{apiVersion:"v1",kind:"ConfigMap",metadata:{name:$name,namespace:"kube-system",labels:{
      "app.kubernetes.io/name":"re8ch-advanced-fabric","app.kubernetes.io/component":"node-status",
      "networking.re8ch.com/node-status":"true"}},data:{"status.json":$status}}')
  code=$(curl -sS --cacert "$CA_FILE" -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
    -H 'Content-Type: application/merge-patch+json' -o /tmp/advanced-fabric-api.out -w '%{http_code}' \
    -X PATCH "${API}/api/v1/namespaces/kube-system/configmaps/${STATUS_CM}" -d "$object" || true)
  if [ "$code" = 404 ]; then
    curl -fsS --cacert "$CA_FILE" -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
      -H 'Content-Type: application/json' -X POST "${API}/api/v1/namespaces/kube-system/configmaps" \
      -d "$object" >/dev/null
  elif [ "$code" != 200 ]; then
    echo "${NODE_NAME}: status publication failed (HTTP ${code})" >&2
    return 1
  fi
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

host systemctl is-active --quiet frr
host vtysh -c 'show bgp ipv4 unicast summary' >/dev/null
publish_status
touch "${READY}"
echo "${NODE_NAME}: Advanced Fabric observe-only host validation ready"
while sleep 30; do
  if host systemctl is-active --quiet frr; then
    publish_status || true
    touch "${READY}"
  else
    rm -f "${READY}"
    publish_status || true
  fi
done
