# Changelog

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
