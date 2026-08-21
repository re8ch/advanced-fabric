#!/bin/sh
set -eu

NODE_FILE="/desired/${NODE_NAME}.json"
READY=/run/advanced-fabric-ready
rm -f "${READY}"

host() { nsenter -t 1 -n chroot /host "$@"; }

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
touch "${READY}"
echo "${NODE_NAME}: Advanced Fabric observe-only host validation ready"
while sleep 30; do
  host systemctl is-active --quiet frr || { rm -f "${READY}"; exit 1; }
done
