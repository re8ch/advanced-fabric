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
  --version 0.11.0 \
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

## Network and DNS conformance

Optional network-quality probes build a directed host/pod matrix from every
node to every Cilium health host and endpoint address, and run UDP/TCP DNS
synthetics from both network namespaces. Missing or stale cells fail closed and
publish `NetworkConformanceReady` and `DNSQualityReady` conditions. See the
[cluster network quality standard](../../docs/network-quality-standard.md) for
the metric contract, thresholds, diagnostic interpretation and retention rules.

`advancedFabric.dns.enabled=true` deploys the Advanced Fabric authoritative DNS
provider. It watches Services and EndpointSlices directly and answers ClusterIP,
ExternalName, headless endpoint and named-port SRV records for `cluster.local`.
The same in-memory index serves UDP/TCP 53 and RFC 8484 DoH. It does not mount,
read or require any K3s CoreDNS resource.
Private or delegated zones use the explicit `dns.conditionalForwarders` map;
they are chart values rather than imported legacy server fragments.

### DNS migration and rollback

1. Install with `migration.phase=shadow`, leaving the existing kubelet
   `clusterDNS` unchanged. Run `helm test` and compare UDP, TCP and DoH answers,
   including ClusterIP, headless and SRV fixtures.
2. Reserve the immutable Service IP, set `migration.phase=active`, and change
   every K3s server and agent to `cluster-dns=10.43.65.40`. Restart nodes in
   bounded failure-domain batches only after the new Deployment is Available.
3. Remove the legacy DNS Deployment and configuration objects after all newly
   created Pods contain `nameserver 10.43.65.40` and the conformance gate is
   green. Historical objects are not part of the runtime or rollback contract.
4. To roll back, first restore the previous kubelet `clusterDNS` target, restart
   kubelets in bounded batches, set `migration.phase=rollback`, and then use the
   Helm release rollback. Never delete the fixed-IP Service while clients still
   reference it.

The Deployment uses initial API-sync startup/readiness gates, a 30-second watch
freshness gate, `maxUnavailable=0`, topology spreading and a PDB. This keeps
upgrades and scaling independent of historical DNS objects.

## Development

```sh
helm lint charts/re8ch-advanced-fabric
helm template test charts/re8ch-advanced-fabric >/dev/null
```
