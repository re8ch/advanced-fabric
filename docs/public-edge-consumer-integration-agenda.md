# PublicEdge consumer integration agenda

**Status:** agenda and architecture proposal; no runtime behavior is changed  
**Primary reference consumer:** Public Edge Manager  
**Scope:** a provider-neutral contract between network evidence producers and
edge, ingress, DNS, scheduling or failover consumers

## Executive decision

Advanced Fabric should serve Public Edge Manager deeply through evidence, but
must not absorb PublicEdge's DNS, hostname, Gateway or publication authority.
The integration boundary is a versioned, read-only assessment API derived from
Advanced Fabric measurements. PublicEdge remains responsible for application
health, candidate selection and public publication. Advanced Fabric remains
responsible for network reality, measurements, evidence quality and guarded
network actions.

The intended relationship is:

```text
Network reality
  Cilium / FRR / BGP / WireGuard / RouterOS / underlay / Service VIP
        |
        v
Advanced Fabric measurements and evidence
        |
        v
NetworkPathAssessment (stable consumer contract)
        |
        +--> PublicEdge candidate eligibility and scoring
        +--> schedulers, failover controllers and human diagnostics

PublicEdge application probes + NetworkPathAssessment
        |
        v
PublicEdgeSelection
        |
        +--> authoritative public DNS
        +--> ExternalDNS / cloud-DNS adapters
        +--> Gateway and publication status
```

This proposal explicitly rejects a shared controller, shared Helm release,
shared leader election, direct ConfigMap parsing and any model in which
Advanced Fabric mutates public DNS or PublicEdge configures the fabric.

## Why this agenda exists

Edge selection can be correct according to DNS and HTTP probes while the
selected data path is unusable. Prior deployments exposed several classes of
false confidence:

- a Service VIP and kernel route existed while WireGuard rejected packets
  because the selected peer did not authorize the destination prefix;
- a Gateway object was accepted by the Kubernetes API while the Cilium operator
  responsible for programming it was unavailable;
- a public origin was reachable in the forward direction while the backend or
  return path depended on an unhealthy transit;
- BGP was active or mostly established while end-to-end host and pod
  measurements still showed material loss and latency;
- node identity, transport identity, public endpoint and provider-owned NAT
  addresses were treated as interchangeable even though they belong to
  different authority domains;
- stale shared-Gateway publication metadata could overwrite a service-specific
  public origin.

These are not reasons to place routing code in PublicEdge. They are reasons for
Advanced Fabric to publish stronger, scoped and freshness-bounded evidence that
an edge controller can consume without understanding FRR, AllowedIPs, Cilium
maps, RouterOS or private topology.

## Goals

1. Give consumers a stable answer to: "Can traffic from this ingress candidate
   reach this destination class and return, with what evidence and confidence?"
2. Preserve the formal `Network Reality -> Measurements -> Evidence -> O/S/I`
   model rather than introducing a second ad hoc health score.
3. Separate hard eligibility gates from optional ranking signals.
4. Represent unknown, stale, partial and contradictory evidence explicitly.
5. Support heterogeneous implementations without requiring Advanced Fabric to
   own every router, tunnel, Gateway or load balancer.
6. Permit PublicEdge to operate without Advanced Fabric through an explicit
   compatibility policy.
7. Make every future guarded network action measurable before and after it is
   proposed or applied.
8. Keep public chart defaults disabled, empty and organization-neutral.

## Non-goals

- Advanced Fabric does not select public hostnames or DNS answers.
- Advanced Fabric does not patch HTTPRoute, TLSRoute, Ingress or ExternalDNS
  publication objects.
- PublicEdge does not configure BGP, FRR, WireGuard, Cilium, RouterOS, NAT,
  firewall rules, kernel routes or Service VIP pools.
- A successful control-plane query is not treated as dataplane proof.
- Peer count, process readiness or route presence is not collapsed into a
  universal network-health boolean.
- This contract does not promise physical-path independence when the dependency
  graph is incomplete.
- The integration does not require both projects to share a CRD API group.
- The first release does not automate route changes based on PublicEdge demand.

## Ownership boundaries

| Concern | Advanced Fabric | PublicEdge | Publication adapter |
| --- | --- | --- | --- |
| Network inventory and transport identity | authority/evidence | opaque reference | none |
| BGP, FRR, tunnels and kernel routes | observe; guarded action | none | none |
| Forward and return-path measurements | authority/evidence | consume | none |
| Gateway programmed evidence | collect/normalize | consume | none |
| Application TLS/HTTP health | optional transport evidence only | authority | none |
| Candidate eligibility | provides network facts | authority | none |
| Edge ranking | provides O/S/I and measurements | authority | none |
| Public hostname ownership | none | authority | execute declared result |
| DNS provider credentials | none | none | authority |
| DNS record mutation | none | decision only | authority |
| Remediation | propose/guard/remeasure | drain or avoid candidate | provider-specific |

No component may infer authority merely because it can observe another
component's resource.

## Consumer requirements

### Functional requirements

A consumer needs to identify:

- the source edge or ingress candidate;
- the destination Gateway, Service, endpoint class or externally defined
  destination;
- the traffic plane and protocol being assessed;
- forward reachability and return reachability;
- whether the relevant Gateway or Service VIP has data-plane evidence;
- observation freshness and coverage;
- O, S and I independently, including `null` when unknown;
- evidence provenance and measurement definition versions;
- failure-domain and path dependencies when known;
- whether the assessment is informational, eligible for gating or eligible for
  guarded automation.

### Non-functional requirements

- Status updates must be monotonic in observed generation and timestamped.
- Consumers must not need access to node Secrets or host namespaces.
- Assessment reads must remain available during a dataplane degradation.
- A controller restart must not turn missing history into a healthy result.
- Cardinality must be bounded; the API is not a per-packet telemetry store.
- The contract must work with namespaced and cluster-scoped consumers.
- The assessment API must not create a dependency on public DNS.
- A stale assessment must never remain silently eligible forever.

## Proposed API: `NetworkPathAssessment`

The working name is intentionally consumer-neutral. `PublicEdgePath` would
encode one product's use case into the fabric API and should not be used.

```yaml
apiVersion: fabric.networking.example.io/v1alpha1
kind: NetworkPathAssessment
metadata:
  name: edge-a-to-regional-web
  labels:
    fabric.networking.example.io/consumer-class: public-edge
spec:
  sourceRef:
    apiGroup: networking.example.io
    kind: EdgeCandidate
    name: edge-a
  destinationRef:
    apiGroup: gateway.networking.k8s.io
    kind: Gateway
    namespace: ingress-system
    name: regional-web
  scope:
    plane: pod
    direction: bidirectional
    protocol: tcp
    ports: [80, 443]
    serviceClass: web
  policyRef:
    name: public-ingress-default
status:
  observedGeneration: 4
  observedAt: "2026-09-08T10:00:00Z"
  validUntil: "2026-09-08T10:01:30Z"
  conditions:
    - type: EvidenceReady
      status: "True"
      reason: RequiredMeasurementsFresh
    - type: ForwardReachable
      status: "True"
      reason: EndToEndProbeSucceeded
    - type: ReturnReachable
      status: "True"
      reason: BidirectionalEvidenceSucceeded
    - type: DestinationProgrammed
      status: "True"
      reason: GatewayDataplaneObserved
  measurements:
    latencyP95Ms: 18.4
    lossRatio: 0.001
    availableCapacityMbps: 740
    sampleCount: 60
    timeWindow: 5m
  assessment:
    optimality: 0.82
    stability: 0.93
    independence: null
    confidence:
      optimality: 0.88
      stability: 0.91
      independence: 0.21
  evidenceRefs:
    - id: tcp-path-v2
      version: "2.0.0"
      digest: sha256:example
  dependencies:
    gateways: [gateway-a]
    providers: [isp-a]
    transports: [wireguard-a]
  recommendation:
    disposition: Eligible
    reason: RequiredEvidenceSatisfied
```

The concrete API group must be chosen during the API review. Examples use a
neutral placeholder so this agenda does not prematurely reserve ownership.

## Required semantics

### References, not copied topology

`sourceRef` and `destinationRef` identify objects at the integration boundary.
The assessment must not copy public IPs, private next hops, peer keys or router
credentials into consumer-facing status. Human-readable dependency identifiers
may be published only when they are non-secret and stable.

### Scope is part of the result

An assessment for host-plane TCP/443 must not satisfy a pod-plane UDP check.
An assessment for one destination Gateway must not establish reachability to an
unrelated Service. Protocol, ports, plane, direction and destination identity
are semantic keys rather than display metadata.

### Unknown is not unhealthy

The following states are distinct:

| State | Meaning | Default PublicEdge treatment |
| --- | --- | --- |
| `Eligible` | required evidence is fresh and gates pass | candidate may be selected |
| `Ineligible` | fresh evidence proves a required gate failed | candidate excluded |
| `Unknown` | required evidence was never obtained | compatibility policy decides |
| `Stale` | evidence existed but exceeded `validUntil` | exclude for strict policy |
| `Partial` | some dimensions are valid, others absent | gate on required dimensions only |
| `Contradictory` | sources disagree beyond declared tolerance | exclude and alert |

O, S and I remain nullable independent values. They are ranking inputs, not
implicit health gates. A consumer policy may explicitly require a minimum
confidence or dimension value, but Advanced Fabric must not invent one global
threshold for all consumers.

### Forward and return paths are separate evidence

A TCP request completing is useful end-to-end evidence, but it may not explain
which direction failed or cover non-TCP use cases. The data model preserves
forward and return conditions separately and may additionally publish an
end-to-end condition.

### Freshness is bounded

Every assessment includes `observedAt` and `validUntil`. Consumers compare
against `validUntil`; they do not derive an undocumented TTL. Advanced Fabric
must set `EvidenceReady=False` before or when required evidence becomes stale.

### Destination programmed is not API acceptance

For Gateway API integrations, `Accepted=True` and `ResolvedRefs=True` are
necessary metadata but are insufficient to set `DestinationProgrammed=True`.
The latter requires implementation-specific data-plane evidence normalized by
a collector.

## PublicEdge consumption contract

PublicEdge combines independent signals:

```text
candidate configured and enabled
AND candidate application probe ready
AND destination route/reference accepted
AND network policy permits the assessment state
= candidate eligible
```

Suggested consumer policy:

```yaml
fabricEvidence:
  mode: Optional       # Disabled | Optional | Required
  assessmentSelector:
    matchLabels:
      fabric.networking.example.io/consumer-class: public-edge
  freshness:
    failClosedAfter: 90s
    lastKnownGood: 30s
  requiredConditions:
    EvidenceReady: "True"
    ForwardReachable: "True"
    ReturnReachable: "True"
    DestinationProgrammed: "True"
  ranking:
    optimalityWeight: 10
    stabilityWeight: 20
    independenceWeight: 5
    minimumConfidence: 0.6
  unknownPolicy: Exclude
```

`Optional` is the migration default: a matching assessment is enforced when
present, while installations without Advanced Fabric retain current probe-only
behavior. `Required` is an operator decision made only after evidence coverage
and freshness SLOs are demonstrated.

PublicEdge should record the assessment generation and disposition used in each
selection. It must not copy the entire evidence record into its status.

## Failure behavior

### Advanced Fabric API unavailable

- Existing cached assessments remain usable only until `validUntil` plus an
  explicitly configured last-known-good grace period.
- Strict consumers exclude affected candidates after the grace period.
- Optional consumers revert only if their declared policy permits probe-only
  operation; this transition must be visible in status and metrics.
- PublicEdge must not attempt to repair routes or restart fabric components.

### Measurement collector unavailable

- The affected evidence becomes stale.
- Unrelated scopes and destinations remain valid.
- Missing collector data must not zero O/S/I.
- No route withdrawal occurs solely because a telemetry process restarted,
  unless a separately reviewed safety policy explicitly defines that behavior.

### PublicEdge unavailable

- Advanced Fabric continues measuring and publishing assessments.
- No DNS or Gateway mutation is performed on PublicEdge's behalf.
- Assessment cardinality is controlled by declared requests/policies, not by
  whether the consumer process is currently online.

### Contradictory evidence

- Set `EvidenceReady=False` with reason `ContradictoryEvidence`.
- Preserve references to both evidence sources.
- Suppress automatic remediation.
- Require a new measurement or operator-reviewed reconciliation plan.

### Clock skew

- Producers use Kubernetes API/server time where practical.
- Consumers reject `observedAt` materially in the future.
- Time synchronization health is evidence metadata, not silently ignored.

## Avoiding dependency cycles

This integration must pass a dependency-cycle review. In particular:

1. Advanced Fabric reconciliation uses the Kubernetes-injected API endpoint,
   not a PublicEdge-selected endpoint or fabric-managed API VIP.
2. Advanced Fabric internal DNS may serve `cluster.local`; it must not require
   PublicEdge public DNS to become Ready.
3. PublicEdge may consume Advanced Fabric assessments, but Advanced Fabric
   readiness must not consume PublicEdge selection status.
4. Publication adapters may consume PublicEdge selections, but neither
   PublicEdge nor Advanced Fabric waits for every cloud DNS provider before
   maintaining its own control loop.
5. Observability storage failure must not invalidate fresh in-memory or CRD
   status solely because historical dashboards are unavailable.

The dependency graph is therefore acyclic:

```text
Kubernetes API
  -> Advanced Fabric evidence
      -> PublicEdge selection
          -> publication adapters

External measurement targets -> Advanced Fabric evidence
Application targets ----------> PublicEdge health
```

## Security and tenancy

- Assessment status is sanitized and contains no credentials, peer public keys,
  router configuration or unrestricted host command output.
- Advanced Fabric writes only its owned assessment resources and status.
- PublicEdge receives get/list/watch on assessments, not patch permission.
- Namespaced requests must not cause a tenant to probe arbitrary Internet or
  RFC1918 targets.
- Assessment creation is limited to reviewed controllers or constrained claim
  resources; status remains controller-owned.
- Consumers cannot request host-network execution or choose arbitrary source
  nodes through an untrusted object.
- Dependency identifiers are reviewed for infrastructure-information exposure.
- Every guarded action retains separate authorization, preview, audit and
  post-action measurement.

## Observability contract

Minimum metrics:

```text
advanced_fabric_path_assessment_info{assessment,source_kind,destination_kind,disposition}
advanced_fabric_path_condition{assessment,type,status,reason}
advanced_fabric_path_evidence_age_seconds{assessment}
advanced_fabric_path_latency_p95_seconds{assessment}
advanced_fabric_path_loss_ratio{assessment}
advanced_fabric_path_available_capacity_bits_per_second{assessment}
advanced_fabric_path_dimension{assessment,dimension}
advanced_fabric_path_confidence{assessment,dimension}
advanced_fabric_path_transitions_total{assessment,from,to,reason}
```

Labels must not include IP addresses, hostnames with unbounded cardinality,
measurement digests or error strings. Detailed evidence belongs in status and
bounded logs.

PublicEdge should expose complementary metrics:

```text
public_edge_candidate_eligibility{candidate,service,network_disposition}
public_edge_selection_info{service,candidate,policy}
public_edge_selection_changes_total{service,reason}
public_edge_fabric_evidence_age_seconds{candidate,service}
```

Alerts should distinguish application-probe failure, network-evidence failure,
staleness, contradiction and consumer API failure.

## Scale and cardinality model

The initial object model should be per source candidate and destination class,
not per Pod endpoint. For `E` edges, `D` destination classes and `P` planes,
expected assessment count is `E * D * P`. The first production target should be
bounded to hundreds, not tens of thousands, of objects.

Endpoint-level samples remain evidence behind an assessment. They do not each
become CRDs. High-frequency measurements belong in the existing history and
metrics pipeline; the CRD is the latest decision-grade summary.

Controllers must use watches, indexed caches and rate-limited status updates.
Unchanged measurements should not produce status writes. Material changes,
freshness transitions and observed-generation changes do.

## Compatibility and portability

The provider contract must allow several backends:

- Advanced Fabric native measurements;
- another controller implementing the same assessment semantics;
- a static assessment for controlled test environments;
- no provider, with PublicEdge operating in probe-only mode;
- externally measured networks where no Advanced Fabric agent runs on the edge.

PublicEdge must discover support through API availability and explicit values,
not by checking for an `advanced-fabric` namespace or RE8CH-specific labels.

Advanced Fabric examples may demonstrate PublicEdge, but default values must not
ship public IPs, private CIDRs, node names, domain names, providers or mutable
network policy.

## Phased delivery plan

### Phase 0: decision record and vocabulary

Deliverables:

- accept, amend or reject this ownership model;
- choose the API group and resource names;
- define source, destination, plane and direction vocabulary;
- identify the initial PublicEdge service classes;
- document compatibility and strict-mode policies;
- record the dependency-cycle analysis.

Exit criteria:

- both repositories agree that assessments are evidence, not commands;
- DNS and network mutation ownership is unambiguous;
- no RE8CH-specific topology is required by the public schema.

### Phase 1: API types and static fixtures

Deliverables:

- structural CRD schema;
- condition and reason taxonomy;
- example objects for eligible, ineligible, unknown, stale, partial and
  contradictory states;
- schema validation and compatibility tests;
- status-size and cardinality limits.

Exit criteria:

- server-side dry-run accepts valid fixtures and rejects ambiguous scopes;
- unknown fields and null dimensions behave as documented;
- no status field contains credentials or unbounded raw output.

### Phase 2: observe-only assessment controller

Deliverables:

- generate assessments from existing registered evidence;
- no route, tunnel, DNS or Gateway mutation;
- freshness controller and `validUntil` transitions;
- Prometheus metrics and Headlamp read-only views;
- event reasons tied to measurement-definition versions.

Exit criteria:

- controller restart preserves correct unknown/stale semantics;
- an unavailable measurement source affects only its scopes;
- API and assessment publication remain available during dataplane degradation.

### Phase 3: PublicEdge shadow consumption

Deliverables:

- PublicEdge reads assessments but does not change selection;
- compare current selection with evidence-gated selection;
- record counterfactual exclusions and ranking changes;
- build dashboards for false-positive and false-negative analysis.

Exit criteria:

- at least 30 days of representative evidence, or an explicitly approved
  shorter campaign with controlled failures;
- no unexplained selection divergence;
- stale and contradictory evidence paths have been exercised.

### Phase 4: optional eligibility gates

Deliverables:

- enable `Optional` consumption for selected service classes;
- maintain probe-only compatibility for deployments without a provider;
- expose selection provenance and last-known-good transitions;
- document rollback as a values-only consumer policy change.

Exit criteria:

- controlled forward-path, return-path and Gateway-programming failures exclude
  the intended candidate within the declared SLO;
- unrelated candidates and services remain eligible;
- rollback does not change the fabric or public DNS ownership model.

### Phase 5: strict gates for reviewed services

Deliverables:

- opt selected production services into `Required` mode;
- define per-class evidence freshness and confidence policies;
- enforce admission checks preventing strict mode without adequate coverage;
- publish an operational runbook.

Exit criteria:

- evidence availability meets its SLO;
- failover tests demonstrate bounded recovery without oscillation;
- operators can distinguish application, fabric and publication failures.

### Phase 6: guarded recommendations

Deliverables:

- produce remediation recommendations from evidence gaps or degradations;
- preview candidate drain, measurement expansion or fabric action separately;
- require existing authorization and release gates for every network mutation;
- remeasure using the same definition after any action.

Exit criteria:

- no recommendation bypasses native authority;
- before/after evidence is comparable and retained;
- automatic action remains disabled until a separate decision explicitly
  authorizes a bounded class of changes.

## Test strategy

### Schema and unit tests

- every condition/reason combination is validated;
- O/S/I accept null and reject out-of-range values;
- `validUntil` precedes no `observedAt`;
- scope mismatches are rejected by consumers;
- duplicate or conflicting source/destination identities fail closed;
- future timestamps and stale generations are rejected;
- status updates do not overwrite a newer observation.

### Integration tests

- Service VIP exists but no usable WireGuard AllowedIPs;
- BGP route exists but end-to-end dataplane probe fails;
- forward path passes and return path fails;
- Gateway API status is accepted while implementation programming is absent;
- host plane passes while pod plane fails, and the inverse;
- application probe fails while fabric assessment stays eligible;
- fabric assessment fails while application probe succeeds;
- assessment API becomes unavailable before and after `validUntil`;
- controller restarts with insufficient stability history;
- one destination degrades without contaminating unrelated scopes.

### Failure-domain experiments

- withdraw one BGP path;
- disable one tunnel while a nominally independent path remains;
- remove a route-server session;
- degrade a provider-owned NAT boundary without changing node readiness;
- make the dependency graph incomplete and verify `I=null`;
- restore the path and confirm hysteresis prevents oscillation.

### Portability tests

- render Advanced Fabric with no PublicEdge resources present;
- run PublicEdge with the assessment API absent;
- run against a neutral example inventory with no RE8CH names or ranges;
- use a non-Cilium destination implementation;
- use a non-ExternalDNS publication adapter;
- verify that neither chart needs the other's namespace or release name.

## Rollout safety and rollback

Every phase through shadow consumption is observe-only. Enabling a consumer gate
changes candidate eligibility but does not modify network infrastructure.

Rollback order:

1. Set PublicEdge consumption from `Required` to `Optional` or `Disabled`.
2. Confirm candidate selection returns to the prior probe-only policy.
3. Retain assessment objects and evidence for diagnosis.
4. Roll back the Advanced Fabric chart only after consumers no longer require
   its API.
5. Never delete the CRD before checking for active consumers and stored status.

Rollback must not alter public IPs, routes, BGP sessions, Gateway listeners,
DNAT, certificates or DNS records merely to remove the integration.

## Open design questions

1. Should assessment requests be explicit CRDs, inferred from consumer objects,
   or generated from a constrained policy selector?
2. Which component owns mapping a Gateway or Service reference to a measurement
   destination without leaking implementation detail?
3. Is `DestinationProgrammed` generic enough, or should implementation-specific
   conditions remain evidence inputs beneath an end-to-end gate?
4. What is the minimum defensible return-path measurement for provider-owned NAT
   where direct reverse probing is unavailable?
5. Should `validUntil` be producer-defined exclusively, or bounded by an
   administrator policy?
6. How should two evidence providers for the same scope be combined without
   hiding contradiction?
7. Which O/S/I dimensions should PublicEdge use only for ranking, and which, if
   any, may become eligibility gates?
8. How much dependency detail can be exposed safely in a cluster-scoped status?
9. Should assessments be namespaced with the destination, or cluster-scoped
   because source edges and network paths cross namespaces?
10. What admission policy prevents an untrusted tenant from causing arbitrary
    active probes?
11. Which status fields are stable API and which remain alpha diagnostics?
12. What evidence-availability SLO is required before any service adopts strict
    mode?

## Decisions deliberately deferred

- automatic path switching by Advanced Fabric;
- automatic PublicEdge drain based solely on an O/S/I threshold;
- a shared cross-project API group;
- physical topology attestation;
- active probing from arbitrary tenant-selected nodes;
- merging internal Kubernetes DNS with public authoritative DNS;
- replacing Gateway API status with a proprietary route API;
- provider-specific RouterOS, Cilium or cloud-DNS fields in the public contract.

## Review checklist

Reviewers should explicitly answer:

- Does the ownership table preserve each project's authority?
- Can a non-Advanced-Fabric PublicEdge installation still work?
- Can a non-PublicEdge consumer use the assessment contract?
- Are unknown, stale and contradictory states operationally distinct?
- Is every eligibility condition backed by scoped dataplane evidence?
- Are dependency cycles impossible by construction?
- Can the integration be disabled without a network or DNS migration?
- Are security and cardinality boundaries enforceable?
- Does the phased plan keep mutation out of early releases?
- Are the proposed experiments sufficient to falsify false-health assumptions?

## Definition of success

This agenda succeeds when a PublicEdge operator can explain why a candidate was
eligible or excluded without reading FRR, WireGuard, Cilium or RouterOS internals;
an Advanced Fabric operator can improve evidence collection without changing
DNS selection semantics; and failure of either controller degrades according to
an explicit, bounded policy rather than a hidden dependency.

The long-term product promise is not "Advanced Fabric makes PublicEdge green."
It is: "Advanced Fabric publishes scoped, versioned and uncertainty-aware
network evidence that any authorized consumer can evaluate safely."
