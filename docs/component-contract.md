# Advanced Fabric component contract

**Status:** implemented in chart 0.17
**Audience:** platform operators and services that consume network evidence

## Component boundary

Advanced Fabric is a self-contained network evidence component. It owns the
complete loop from observed network reality to post-action validation:

```text
cluster membership + topology declarations + network reality
                         |
                         v
registered Measurement definitions
                         |
                         v
Evidence Planner -> collectors -> timestamped evidence
       ^                              |
       |                              v
post-action validation <- recommendation <- inference -> O / S / I
                                                   |
                                                   v
                                      NetworkPathAssessment API
                                                   |
                                  get / list / watch by any consumer
```

Consumers do not configure FRR, Cilium, tunnels, routes or probes through this
API. They do not parse Advanced Fabric ConfigMaps or reproduce its inference.
Advanced Fabric does not know whether the consumer is an ingress controller,
scheduler, failover manager, DNS system, UI or policy engine.

## Inputs owned by the component

The single installation-level input is the `AdvancedFabric` resource rendered
by the chart. It declares:

- active inventory and topology metadata;
- measurement procedures, cadence, time windows and thresholds;
- observe-only versus separately authorized guarded-apply behavior;
- optional DNS, control-plane VIP and traffic-policy capabilities; and
- the stable component API publication policy.

Kubernetes Node membership is authoritative for active participation. Declared
nodes absent from the cluster are retired from active measurement and inference.
Stale measurements remain audit evidence but never become current state.

```yaml
advancedFabric:
  enabled: true
  mode: observe-only
  componentAPI:
    publishAssessments: true
    validitySeconds: 120
  networkQuality:
    enabled: true
    maximumConcurrency: 24
```

Public defaults remain disabled and contain no organization-specific inventory.

An operator may declare an actually reachable alternative endpoint for the same
destination scope. This is the only input that permits the collector to emit an
`alternative` path for O:

```yaml
nodes:
  - name: node-a
    alternativePaths:
      - name: node-b-via-independent-gateway
        targetNode: node-b
        targetPlane: host
        address: 192.0.2.20
```

The address must route through the declared alternative in the deployment. A
different destination is never accepted as a substitute comparison.

## Internal closure

### Measurement registry

Every collector output names a versioned definition with scope, unit, sampling
procedure, time window, failure semantics, uncertainty and cost. Evidence that
does not match the required definition cannot satisfy an inference dimension.

The initial registry contains:

- `path-quality-v1`;
- `dns-quality-v1`;
- `temporal-stability-v1`; and
- `failure-domain-graph-v1`.

### Evidence Planner and collectors

The inference layer identifies absent, stale or insufficient evidence. The
planner turns those gaps into bounded tasks assigned by node and host/pod plane.
Collectors execute the registered procedures and report task completion with
new evidence. A process being healthy never completes a measurement task by
itself.

### Independent state

O, S and I are independent `[0,1]` variables:

- O compares a current path with measured feasible alternatives;
- S evaluates temporal variance, bursts and BGP/route churn over a window; and
- I derives failure-domain diversity from gateway, ISP, ASN, tunnel and physical
  path dependencies.

They are not sum-normalized. Missing evidence is `null`, not zero. Per-dimension
confidence describes evidence quality and never changes the dimension value.

### Recommendation and validation

Inference produces a diagnosis and bounded recommendation. Network mutation
remains subject to existing guarded-apply authorization. A measurement or
shadow-probe task is created after an action so the same evidence definition can
confirm improvement, detect regression or leave the result inconclusive.

## Stable output: `NetworkPathAssessment`

The component publishes one cluster-scoped assessment per active Node. This is
the supported integration boundary for other services.

```yaml
apiVersion: networking.re8ch.com/v1alpha1
kind: NetworkPathAssessment
metadata:
  name: node-r640
spec:
  subjectRef: {apiVersion: v1, kind: Node, name: r640}
  scope: {plane: host-and-pod, direction: bidirectional, protocol: mixed}
status:
  observedAt: "2026-09-08T06:08:56Z"
  validUntil: "2026-09-08T06:10:56Z"
  state: Partial
  dimensions:
    optimality: null
    stability: 0.71
    independence: null
  confidence:
    optimality: 0
    stability: 0.8
    independence: 0
  evidenceRefs:
    - dns-quality-v1
    - failure-domain-graph-v1
    - path-quality-v1
    - temporal-stability-v1
  diagnosis: dataplane-degradation
  recommendation: compare an independent shadow path
  validation: {state: pending, pendingTaskIds: []}
  conditions:
    - type: EvidenceReady
      status: "True"
      reason: SomeDimensionsUnavailable
```

### State semantics

| State | Meaning |
| --- | --- |
| `Ready` | Fresh formal evidence exists for all O/S/I dimensions |
| `Partial` | Fresh formal state exists, but one or more dimensions are unknown |
| `Unknown` | No formal dimension can currently be evaluated |
| `Stale` | The last formal state exceeded `validUntil` |
| `Contradictory` | Evidence sources disagree beyond an explicit rule |

`Partial` and `Unknown` are not aliases for unhealthy. `Ready` is evidence
readiness, not a universal routing eligibility decision. Consumers choose which
conditions or dimensions matter for their own policy.

### Lifecycle guarantees

- The controller creates and updates assessment spec and status.
- Only snapshots carrying the formal measurement-model version are published;
  provisional legacy scores are ignored.
- `validUntil` is producer-defined, so consumers need no undocumented TTL.
- An assessment is pruned when its Node leaves active cluster membership.
- Controller restart does not convert absent history to healthy state.
- Status contains no credentials, peer keys, packet payloads or raw host output.
- Consumer RBAC should normally be limited to `get`, `list` and `watch`.

## Consumer contract

A consumer discovers the CRD explicitly and watches assessments matching its
subject. It must:

1. validate scope before using a result;
2. honor `validUntil` and state;
3. treat nullable dimensions independently;
4. keep its own application or business eligibility policy; and
5. record the assessment generation/timestamp used for a decision.

A consumer must not infer that Advanced Fabric is installed by checking a
namespace, release name or ConfigMap. Absence of the CRD is a normal compatibility
case. The consuming service decides whether that means disabled, optional or
required evidence.

The output API contains no callback, command or consumer-specific object
reference. This prevents dependency cycles and lets multiple unrelated services
consume the same evidence concurrently.

## Reliability and scale

The CRD is a bounded latest-state API, not a telemetry database. High-frequency
samples and historical polygons stay in the evidence/history pipeline. The
number of assessment objects is linear in active subjects. Unchanged status is
not rewritten, and missing collectors affect only their own evidence scopes.

The Kubernetes API used by the controller must remain independent of any
fabric-managed API VIP or external publication path. Measurement degradation
therefore does not remove the component's ability to describe that degradation.

## Installation verification

```sh
helm template advanced-fabric oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric \
  --version 0.17.1 --set advancedFabric.enabled=true \
  --set advancedFabric.networkQuality.enabled=true >/tmp/advanced-fabric.yaml

kubectl get advancedfabric re8ch
kubectl get networkpathassessments
kubectl get networkpathassessment node-r640 -o yaml
```

A usable component installation has a reconciled `AdvancedFabric`, registered
measurement definitions, an Evidence Planner state, timestamped O/S/I history
and a `NetworkPathAssessment` for each active Node. Individual assessments may
correctly remain `Unknown` or `Partial` while evidence is gathered.

## Compatibility and growth

The v1alpha1 output begins with node subjects because the existing collectors
provide node/plane matrices. Future versions may add destination-scoped
assessments only when the component can measure current and alternative paths to
the same destination under comparable procedures. Different destinations must
not be relabeled as alternative paths to manufacture O.

Revisit the object cardinality model before adding namespace or endpoint-level
subjects. Revisit physical topology attestation before claiming that declared
failure domains prove physical independence. Neither extension should change
the consumer rule: depend on the stable assessment contract, not implementation
internals.
