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
  --version 0.6.1 \
  --namespace advanced-fabric --create-namespace
```

Provide deployment-specific nodes and quotas through a private values file.
See [`examples/inventory.example.yaml`](examples/inventory.example.yaml).

RouterOS integrations use `RouterOSNode/v1alpha2`: BGP is constrained to a
dedicated acceleration FIB, while public VIPs retain an R640 LAN fallback. The
controller ships in observe-only/release-gated form so chart installation does
not change the router or the operator workstation's public path.

## Development

```sh
helm lint charts/re8ch-advanced-fabric
helm template test charts/re8ch-advanced-fabric >/dev/null
```
