# Advanced Fabric

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/advanced-fabric)](https://artifacthub.io/packages/search?repo=advanced-fabric)

Advanced Fabric is a Helm-packaged control and observation layer for Cilium
native routing, FRR spine/leaf fabrics, service VIPs and policy-driven network
economics.

## Formal network measurement model

The architecture is `Network Reality → Measurements → Evidence → O/S/I`.
Network reality is never scored directly. Collectors register measurements
against versioned definitions; the inference engine accepts only evidence whose
scope and semantics match the dimension being evaluated.

Every Measurement implements this interface:

```yaml
id: string                 # stable, versioned definition id
scope: string              # endpoints, plane, path and failure domain observed
unit: string               # physical/statistical unit; never an unlabeled score
samplingProcedure: string  # protocol, sample count, cadence and selection method
timeWindow: string         # explicit observation/baseline interval
failureSemantics: string   # timeout, loss, stale and partial-result treatment
uncertainty: string        # estimator/error bounds and known blind spots
measurementCost: string    # traffic, time and control-plane load
```

- **O — relative path optimality.** Compare current-path latency, loss and path
  quality only with feasible alternatives measured under the same scope and
  window. Absolute path health, BGP completeness and unmeasured candidates are
  not O.
- **S — temporal invariance.** Evaluate latency variance, loss variance, loss
  bursts, BGP churn and route churn over an explicit window. Snapshot freshness
  is not S.
- **I — failure-domain independence.** Derive diversity from a topology and
  dependency graph covering independent gateway, ISP, ASN, tunnel and physical
  path structure. Peer count is not I.

O, S and I are independent variables in `[0,1]`; they are never normalized
against each other and are never constrained to sum to one. Missing required
evidence produces a partial state with `null`, never zero. Each dimension has a
separate confidence value which describes evidence quality and must not alter
the dimension value. The controller persists timestamped
`(O,S,I,confidenceO,confidenceS,confidenceI)` states for consumers.

Public defaults are safe: runtime components disabled until explicitly enabled,
observe-only mode, no node inventory or credentials, no topology-authority
mutations, and weighted ECMP disabled.

The optional Headlamp plugin presents live per-node datapath mode, tunnel
interfaces, FRR/BGP/BFD health, kernel ECMP routes, candidate decisions and
NWQ-1/DNSQ-1 measurements. Enable it with
`headlampPlugin.enabled=true` when Headlamp uses a shared plugins PVC.

## Install

```sh
helm upgrade --install re8ch-network-fabric \
  oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric \
  --version 0.15.0 \
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
