# Advanced Fabric

Advanced Fabric is a Helm-packaged control and observation layer for Cilium
native routing, FRR spine/leaf fabrics, service VIPs and policy-driven network
economics.

Public defaults are safe: runtime components disabled until explicitly enabled,
observe-only mode, no node inventory or credentials, no topology-authority
mutations, and weighted ECMP disabled.

The optional Headlamp plugin presents live per-node datapath mode, tunnel
interfaces, FRR/BGP/BFD health and kernel ECMP routes. Enable it with
`headlampPlugin.enabled=true` when Headlamp uses a shared plugins PVC.

## Install

```sh
helm upgrade --install re8ch-network-fabric \
  oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric \
  --version 0.7.0 \
  --namespace advanced-fabric --create-namespace
```

Provide deployment-specific nodes and quotas through a private values file.
See [`examples/inventory.example.yaml`](examples/inventory.example.yaml).

## RouterOS eBGP boundary

`RouterOSNode.networking.re8ch.com/v1alpha2` models RouterOS as an acceleration
boundary rather than a default-route authority. Cluster peers import into a
dedicated FIB, public VIPs have an explicit high-distance LAN fallback, and
protected infrastructure prefixes are rejected before a transaction is
accepted. The controller is observe-first and publishes the exact SHA-256
transaction checksum. `GuardedApply` remains release-gated; installing the
chart cannot mutate RouterOS.

The intended rollout creates fallback routes and shadow policy before moving a
peer. Keep the physical WAN default route and operator workstation path outside
the transaction, and abort when either the router-local or external continuity
probe fails.

## Control-plane API observation

`advancedFabric.controlPlaneApi` models a fixed K3s registration `/32` in the
accelerated host/FRR domain. Eligible nodes probe their local kube-apiserver
`/readyz`; the controller publishes eligibility and node readiness in desired
state and CR status. Guarded apply requires a checksum-bound per-node
transaction. `guardedNodes` limits VIP ownership during canary rollout;
`nodeOperations` declares only exact fallback routes, WireGuard interfaces and
FRR export prefix lists. The agent authenticates the local `/readyz` check and
withdraws BGP before removing the loopback address.

Only an exact address inside `10.250.0.0/24` is accepted. Kubernetes Service
VIP space, Node InternalIP space and Pod CIDRs remain under their existing
authorities.

## Development

```sh
helm lint charts/re8ch-advanced-fabric
helm template test charts/re8ch-advanced-fabric >/dev/null
```
