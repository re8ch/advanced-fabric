#!/bin/sh
set -eu

WINDOW_SECONDS=${SERVICE_TRAFFIC_WINDOW_SECONDS:-15}
# /var/run is an absolute host symlink to /run. Inside the host-root mount,
# address the real path so the symlink cannot escape back into the container.
SOCKET=/host/run/cilium/hubble.sock
TARGET=/status/service-traffic.json
SOURCE_EPOCH=$(date -u +%Y-%m-%dT%H:%M:%SZ)

while :; do
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  started_epoch=$(date +%s)
  raw="${TARGET}.ndjson.tmp"
  : >"${raw}"
  rc=0
  timeout "${WINDOW_SECONDS}" hubble observe --server "unix://${SOCKET}" --follow \
    --output jsonpb --all-namespaces --traffic-direction ingress --verdict FORWARDED >"${raw}" 2>/dev/null || rc=$?
  ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  ended_epoch=$(date +%s)
  duration=$((ended_epoch - started_epoch))
  # timeout(1) exit 124 means a healthy, deliberately completed follow window.
  if [ "${rc}" -eq 124 ] && [ "${duration}" -gt 0 ]; then
    flow_events=$(jq -s '[.[] | select(.flow != null)] | length' "${raw}" 2>/dev/null || printf '')
    requests=$(jq -s '[.[] | select(.flow.l7.http != null)] | length' "${raw}" 2>/dev/null || printf '')
    if [ -n "${flow_events}" ] && [ -n "${requests}" ]; then
      jq -cn --arg node "${NODE_NAME}" --arg observedAt "${ended}" --arg sourceEpoch "${SOURCE_EPOCH}" \
        --argjson windowSeconds "${duration}" --argjson flowEvents "${flow_events}" --argjson requests "${requests}" \
        '{schemaVersion:"networking.re8ch.com/service-traffic-window-v1alpha1",observedAt:$observedAt,
          windowSeconds:$windowSeconds,sourceEpoch:$sourceEpoch,collector:"node-local Hubble Unix socket",
          samples:[{node:$node,receivedFlowEvents:$flowEvents,requests:$requests,observationPlane:"hubble",
            availableDimensions:["flowEvents","requests"]}]}' >"${TARGET}.tmp"
      mv "${TARGET}.tmp" "${TARGET}"
    fi
  fi
  rm -f "${raw}"
  [ "${rc}" -eq 124 ] || sleep "${WINDOW_SECONDS}"
done
