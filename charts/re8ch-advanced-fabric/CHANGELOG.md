# Changelog

## 0.9.0

- Add a strict `4 × N²` directed Host/Pod connectivity matrix with independent
  endpoints in both network namespaces.
- Add UDP and TCP DNS synthetics from every Host/Pod source, with freshness,
  coverage, failure-ratio and latency gates.
- Export the NWQ-1/DNSQ-1 metric contract through a per-node Node Exporter
  textfile collector for VictoriaMetrics and Grafana.
- Add an opt-in CoreDNS 1.14.3 RFC 8484 canary and fail-closed DoH quality gate
  without changing the production `kube-dns` client contract.
- Add an independently selected UDP/TCP 53 shadow Service that preserves K3s
  custom zones and NodeHosts, and compare stable/shadow paths before promotion.
- Publish `NetworkConformanceReady` and `DNSQualityReady`; when measurement is
  enabled, both conditions participate in `ApplySafe` and guarded apply.
- Document NWQ-1 and DNSQ-1 metric contracts, diagnostic interpretation,
  operational DNS telemetry and evidence retention.

## 0.8.0

- Add `RouterOSNode/v1alpha2` with role-scoped peers, protected prefixes,
  dedicated acceleration FIBs and explicit public VIP fallback intent.
- Add an observe-first RouterOS controller that publishes a deterministic
  transaction checksum and refuses all mutations behind a release gate.
- Preserve `main`, the physical default route and operator workstation traffic
  as hard invariants of every RouterOS transaction.

## 0.7.0

- Adds an observe-first `controlPlaneApi` contract for an exact accelerated
  `/32`, health-eligible ECMP origins, WireGuard primary transport and
  ZeroTier fallback transport.
- Publishes per-node local API readiness without changing FRR, loopback
  addresses, K3s identity or etcd membership.

## 0.3.0

- Adds the authoritative seven-spine A/B/C inventory and deterministic private ASNs.
- Adds observe-only `AdvancedFabric` and `TrafficPolicy` control loops.
- Standardizes Cilium native routing for `10.42.0.0/16` while keeping
  `auto-direct-node-routes=false` for the multi-WAN underlay.
- Adds quota-aware fastest, balanced and greedy policy models; weighted ECMP
  remains disabled until a separate acceptance gate passes.
- Standardizes the Harbor/OCI endpoint as `https://registry.re8ch.com` on port
  443. No third-party mirror credentials are packaged.
