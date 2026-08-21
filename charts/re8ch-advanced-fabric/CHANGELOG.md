# Changelog

## 0.3.0

- Adds the authoritative seven-spine A/B/C inventory and deterministic private ASNs.
- Adds observe-only `AdvancedFabric` and `TrafficPolicy` control loops.
- Standardizes Cilium native routing for `10.42.0.0/16` while keeping
  `auto-direct-node-routes=false` for the multi-WAN underlay.
- Adds quota-aware fastest, balanced and greedy policy models; weighted ECMP
  remains disabled until a separate acceptance gate passes.
- Standardizes the Harbor/OCI endpoint as `https://registry.re8ch.com` on port
  443. No third-party mirror credentials are packaged.

