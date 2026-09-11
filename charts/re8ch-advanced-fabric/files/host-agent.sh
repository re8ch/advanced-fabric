#!/bin/sh
set -eu
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "advanced-fabric-agent: node=${NODE_NAME:-unknown} exit=${rc} line=${LINENO:-unknown}" >&2' EXIT

NODE_FILE="/desired/${NODE_NAME}.json"
READY=/run/advanced-fabric-ready
STATUS_FILE=/status/status.json
STATE_FILE=/status/dynamics.json
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
  # FRR 10.x accepts the generic neighbor JSON command, while the AFI-qualified
  # `... neighbors json brief` form prints an error as successful plain text.
  # That text later makes jq reject the otherwise atomic status envelope.
  neighbors=$(host vtysh -c 'show bgp neighbors json' 2>/dev/null || printf '{}')
  rib=$(host vtysh -c 'show bgp ipv4 unicast json' 2>/dev/null || printf '{}')
  bgp_rib=$(printf '%s' "$rib" | jq -c '[((.routes // {}) | to_entries[]) | {prefix:.key,paths:[.value[] | {
    peer:(.peerId // .peerHostname // .nexthops[0].hostname // "unknown"),
    nextHops:[.nexthops[]? | (.ip // .hostname // "unknown")],asPath:(.path // ""),
    metric:(.metric // null),localPreference:(.localPref // null),weight:(.weight // null),
    best:(if (.bestpath | type) == "object" then (.bestpath.overall == true) else (.bestpath == true) end),
    multipath:(.multipath == true)}]}]' 2>/dev/null || printf '[]')
  next_hop_probes=$(printf '%s' "$bgp_rib" | jq -r '[.[].paths[].nextHops[]? | select(. != "unknown" and . != "0.0.0.0")] | unique[]' |
    while IFS= read -r next_hop; do
      route=$(host ip -j route get "$next_hop" 2>/dev/null | jq -c '.[0] // {}' || printf '{}')
      if host ping -n -c 1 -W 1 "$next_hop" >/dev/null 2>&1; then reachable=true; else reachable=false; fi
      jq -cn --arg address "$next_hop" --argjson route "$route" --argjson reachable "$reachable" \
        '{address:$address,reachable:$reachable,routeDev:($route.dev // null),gateway:($route.gateway // null),source:($route.prefsrc // null)}'
    done | jq -sc '.')
  bfd=$(host vtysh -c 'show bfd peers json' 2>/dev/null || printf '[]')
  routes=$(host ip -j route show table main 2>/dev/null || printf '[]')
  nexthops=$(host ip -j nexthop show 2>/dev/null || printf '[]')
  links=$(host ip -j -s link show 2>/dev/null || printf '[]')
  ecmp=$(printf '%s' "$routes" | jq -c '[.[] | select(((.nexthops // []) | length) > 1) | {dst: (.dst // "default"), protocol, metric, nexthops: [.nexthops[] | {gateway, dev, weight}]}]' 2>/dev/null || printf '[]')
  peers=$(jq -c '.peers // []' "${NODE_FILE}")
  peer_routes=$(printf '%s' "$peers" | jq -c '[.[] | {name, internalIP, acceleratedIP, podCIDR,
    provider, region, failureDomain, gateway, tunnel, asn}]')
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
  bgp_fingerprint=$(printf '%s' "$bgp" | jq -Sc '[.. | objects | select(has("state") or has("peerState")) |
    {peer:(.peerId // .hostname // .neighbor // "unknown"),state:(.state // .peerState // "unknown")}]' | sha256sum | awk '{print $1}')
  route_fingerprint=$(printf '%s' "$routes" | jq -Sc '[.[] | {dst:(.dst // "default"),protocol,metric,
    nexthops:(.nexthops // [])}]' | sha256sum | awk '{print $1}')
  if [ -s "${STATE_FILE}" ]; then dynamics=$(cat "${STATE_FILE}"); else
    dynamics=$(jq -cn --arg now "$now" '{startedAt:$now,samples:0,bgpChanges:0,routeChanges:0}'); fi
  dynamics=$(printf '%s' "$dynamics" | jq -c --arg bgp "$bgp_fingerprint" --arg route "$route_fingerprint" '
    .samples += 1 | .bgpChanges += (if .bgpFingerprint and .bgpFingerprint != $bgp then 1 else 0 end) |
    .routeChanges += (if .routeFingerprint and .routeFingerprint != $route then 1 else 0 end) |
    .bgpFingerprint=$bgp | .routeFingerprint=$route')
  printf '%s\n' "$dynamics" >"${STATE_FILE}.tmp"; mv "${STATE_FILE}.tmp" "${STATE_FILE}"
  status=$(jq -cn \
    --arg node "$NODE_NAME" --arg observedAt "$now" --arg datapath "$datapath" \
    --arg frr "$frr_state" --argjson tunnels "$tunnel_interfaces" \
    --argjson bgp "$bgp" --argjson neighbors "$neighbors" --argjson bfd "$bfd" --argjson ecmp "$ecmp" \
    --argjson routes "$routes" --argjson nexthops "$nexthops" --argjson links "$links" --argjson nextHopProbes "$next_hop_probes" \
    --argjson bgpRib "$bgp_rib" --argjson peers "$peer_routes" --argjson rankings "$rankings" \
    --argjson controlPlaneApi "$api_config" --argjson controlPlaneApiHealthy "$api_healthy" --argjson dynamics "$dynamics" \
    '{schemaVersion:"networking.re8ch.com/v1alpha1",node:$node,observedAt:$observedAt,
      datapath:{mode:$datapath,tunnelInterfaces:$tunnels},frr:{state:$frr,bgp:$bgp,neighbors:$neighbors,bfd:$bfd},
      routes:$routes,nexthops:$nexthops,links:$links,nextHopProbes:$nextHopProbes,ecmpRoutes:$ecmp,bgpRib:$bgpRib,peerRoutes:$peers,pathRankings:$rankings,
      controlPlaneApi:($controlPlaneApi + {localHealthy:$controlPlaneApiHealthy}),routeDynamics:$dynamics}')
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
  action=$1
  transaction | jq -r '.wireguardInterfaces[]?' | while read -r iface; do
    peers=$(host wg show "${iface}" peers); [ "$(printf '%s\n' "${peers}" | sed '/^$/d' | wc -l)" -eq 1 ]
    transaction | jq -r '.wireguardAllowedPrefixes[]?' | while read -r allowed; do
      current=$(host wg show "${iface}" allowed-ips | awk -v peer="${peers}" '$1 == peer {$1=""; sub(/^[[:space:]]+/, ""); print}')
      updated=""; found=false
      for prefix in ${current}; do
        [ "${prefix}" = "${allowed}" ] && found=true
        [ "${action}" = remove ] && [ "${prefix}" = "${allowed}" ] && continue
        updated="${updated}${updated:+ }${prefix}"
      done
      [ "${action}" = apply ] && [ "${found}" = false ] && updated="${updated}${updated:+ }${allowed}"
      [ -n "${updated}" ] || continue
      host wg set "${iface}" peer "${peers}" allowed-ips "$(printf '%s' "${updated}" | tr ' ' ',')"
    done
  done
}
manage_wireguard_peer_policies() {
  action=$1
  transaction | jq -c '.wireguardPeerPolicies[]?' | while read -r policy; do
    iface=$(printf '%s' "${policy}" | jq -r '.interface'); peer=$(printf '%s' "${policy}" | jq -r '.peer')
    host wg show "${iface}" peers | grep -Fxq "${peer}"
    printf '%s' "${policy}" | jq -r '.allowedPrefixes[]?' | while read -r allowed; do
      current=$(host wg show "${iface}" allowed-ips | awk -v peer="${peer}" '$1 == peer {$1=""; sub(/^[[:space:]]+/, ""); print}')
      updated=""; found=false
      for prefix in ${current}; do [ "${prefix}" = "${allowed}" ] && found=true; [ "${action}" = remove ] && [ "${prefix}" = "${allowed}" ] && continue; updated="${updated}${updated:+ }${prefix}"; done
      [ "${action}" = apply ] && [ "${found}" = false ] && updated="${updated}${updated:+ }${allowed}"
      [ -n "${updated}" ] || continue
      host wg set "${iface}" peer "${peer}" allowed-ips "$(printf '%s' "${updated}" | tr ' ' ',')"
    done
  done
}
manage_frr_transit_prefixes() {
  action=$1; asn=$(transaction | jq -r '.localAsn')
  transaction | jq -c '.frrPrefixEntries[]?' | while read -r entry; do
    list=$(printf '%s' "${entry}" | jq -r '.list'); sequence=$(printf '%s' "${entry}" | jq -r '.sequence'); prefix=$(printf '%s' "${entry}" | jq -r '.prefix')
    if [ "${action}" = apply ]; then command="ip prefix-list ${list} seq ${sequence} permit ${prefix}"; else command="no ip prefix-list ${list} seq ${sequence}"; fi
    host vtysh -c 'configure terminal' -c "${command}" -c end >/dev/null
  done
  transaction | jq -r '.frrNetworks[]?' | while read -r prefix; do
    if [ "${action}" = apply ]; then command="network ${prefix}"; else command="no network ${prefix}"; fi
    host vtysh -c 'configure terminal' -c "router bgp ${asn}" -c 'address-family ipv4 unicast' -c "${command}" -c end >/dev/null
  done
}
manage_forward_rules() {
  action=$1
  transaction | jq -c '.forwardRules[]?' | while read -r rule; do
    inif=$(printf '%s' "${rule}" | jq -r '.inInterface'); outif=$(printf '%s' "${rule}" | jq -r '.outInterface'); source=$(printf '%s' "${rule}" | jq -r '.source'); destination=$(printf '%s' "${rule}" | jq -r '.destination'); protocol=$(printf '%s' "${rule}" | jq -r '.protocol'); port=$(printf '%s' "${rule}" | jq -r '.port')
    if [ "${action}" = apply ]; then host iptables -C FORWARD -i "${inif}" -o "${outif}" -s "${source}" -d "${destination}" -p "${protocol}" --dport "${port}" -j ACCEPT 2>/dev/null || host iptables -I FORWARD 1 -i "${inif}" -o "${outif}" -s "${source}" -d "${destination}" -p "${protocol}" --dport "${port}" -j ACCEPT; else host iptables -D FORWARD -i "${inif}" -o "${outif}" -s "${source}" -d "${destination}" -p "${protocol}" --dport "${port}" -j ACCEPT 2>/dev/null || true; fi
  done
}
manage_source_identity_rules() {
  action=$1
  transaction | jq -c '.sourceIdentityRules[]?' | while read -r rule; do
    source=$(printf '%s' "${rule}" | jq -r '.source'); destination=$(printf '%s' "${rule}" | jq -r '.destination')
    protocol=$(printf '%s' "${rule}" | jq -r '.protocol'); port=$(printf '%s' "${rule}" | jq -r '.port')
    if [ "${action}" = apply ]; then
      host iptables -t nat -C POSTROUTING -d "${destination}" -p "${protocol}" --dport "${port}" -j SNAT --to-source "${source}" 2>/dev/null || \
        host iptables -t nat -A POSTROUTING -d "${destination}" -p "${protocol}" --dport "${port}" -j SNAT --to-source "${source}"
    else
      host iptables -t nat -D POSTROUTING -d "${destination}" -p "${protocol}" --dport "${port}" -j SNAT --to-source "${source}" 2>/dev/null || true
    fi
  done
}
manage_frr_import_prefixes() {
  vip=$(transaction | jq -r '.vip')
  sequence=$(transaction | jq -r '.frrImportPrefixSequence')
  transaction | jq -r '.frrImportPrefixLists[]?' | while read -r list; do
    host vtysh -c 'show running-config' | grep -Fq "ip prefix-list ${list} seq ${sequence} permit ${vip}" && continue
    host vtysh -c 'configure terminal' -c "ip prefix-list ${list} seq ${sequence} permit ${vip}" -c end >/dev/null
    host vtysh -c 'clear bgp ipv4 unicast * soft in' >/dev/null
  done
}
manage_frr_neighbor_policies() {
  asn=$(transaction | jq -r '.localAsn')
  transaction | jq -c '.frrNeighborPolicies[]?' | while read -r policy; do
    neighbor=$(printf '%s' "${policy}" | jq -r '.neighbor'); maximum=$(printf '%s' "${policy}" | jq -r '.maximumPrefixes')
    map=$(printf '%s' "${policy}" | jq -r '.routeMap // empty'); list=$(printf '%s' "${policy}" | jq -r '.matchPrefixList // empty')
    preference=$(printf '%s' "${policy}" | jq -r '.localPreference // empty')
    running=$(host vtysh -c 'show running-config'); changed=false
    if ! printf '%s\n' "${running}" | grep -Fq "neighbor ${neighbor} maximum-prefix ${maximum} 90 restart 5"; then
      host vtysh -c 'configure terminal' -c "router bgp ${asn}" -c 'address-family ipv4 unicast' -c "no neighbor ${neighbor} maximum-prefix" -c "neighbor ${neighbor} maximum-prefix ${maximum} 90 restart 5" -c end >/dev/null
      changed=true
    fi
    if [ -n "${map}" ] && ! printf '%s\n' "${running}" | grep -Fq "neighbor ${neighbor} route-map ${map} in"; then
      host vtysh -c 'configure terminal' -c "route-map ${map} permit 10" -c "match ip address prefix-list ${list}" -c "set local-preference ${preference}" -c exit -c "router bgp ${asn}" -c 'address-family ipv4 unicast' -c "neighbor ${neighbor} route-map ${map} in" -c end >/dev/null
      changed=true
    fi
    [ "${changed}" = false ] || host vtysh -c "clear bgp ipv4 unicast ${neighbor} soft" >/dev/null
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
vip_announced() {
  vip=$(transaction | jq -r '.vip'); iface=$(transaction | jq -r '.vipInterface'); sequence=$(transaction | jq -r '.frrPrefixSequence')
  host ip -o -4 address show dev "${iface}" | grep -Fq " ${vip} " || return 1
  running=$(host vtysh -c 'show running-config')
  printf '%s\n' "${running}" | grep -Fq "network ${vip}" || return 1
  for list in $(transaction | jq -r '.frrExportPrefixLists[]?'); do
    printf '%s\n' "${running}" | grep -Fq "ip prefix-list ${list} seq ${sequence} permit ${vip}" || return 1
  done
}
api_healthy() {
  timeout_seconds=$(jq -r '.controlPlaneApi.healthCheck.timeoutSeconds' "${NODE_FILE}")
  host timeout "${timeout_seconds}" k3s kubectl --server=https://127.0.0.1:6443 get --raw=/readyz 2>/dev/null | grep -qx ok
}

while [ ! -s "${NODE_FILE}" ]; do sleep 2; done
successes=0; failures=0; announced=false
while :; do
  apply=$(jq -r '.mode == "guarded-apply" and .applyEnabled == true' "${NODE_FILE}"); guarded=$(transaction | jq -r '.guarded')
  if [ "${apply}" != true ]; then
    # Observe-only is strictly read-only. Cleanup/removal is also a mutation and
    # must be performed by a separately reviewed guarded-apply transaction.
    successes=0; failures=0; announced=false
  else
    validate_transaction
    host systemctl is-active --quiet frr
    manage_fallback_routes apply; manage_wireguard_allowed_ips apply; manage_wireguard_peer_policies apply; manage_frr_transit_prefixes apply; manage_forward_rules apply; manage_source_identity_rules apply; manage_frr_import_prefixes; manage_frr_neighbor_policies
    if [ "${guarded}" != true ]; then withdraw_vip; successes=0; failures=0; announced=false
    elif api_healthy; then
      successes=$((successes + 1)); failures=0; threshold=$(jq -r '.controlPlaneApi.healthCheck.successThreshold' "${NODE_FILE}")
      if [ "${announced}" = false ] && [ "${successes}" -ge "${threshold}" ]; then announce_vip; announced=true; fi
      # The PodCIDR reconciler and an operator may legitimately rewrite FRR.
      # Treat loopback/network/prefix-list drift as transaction drift and
      # restore it immediately while the local API remains healthy.
      if [ "${announced}" = true ] && ! vip_announced; then announce_vip; fi
    else
      failures=$((failures + 1)); successes=0; threshold=$(jq -r '.controlPlaneApi.healthCheck.failureThreshold' "${NODE_FILE}")
      if [ "${announced}" = true ] && [ "${failures}" -ge "${threshold}" ]; then withdraw_vip; announced=false; fi
    fi
  fi
  publish_status || true; touch "${READY}"; sleep "$(jq -r '.controlPlaneApi.healthCheck.intervalSeconds' "${NODE_FILE}")"
done
