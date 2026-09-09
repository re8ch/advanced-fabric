# Advanced Fabric

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/advanced-fabric)](https://artifacthub.io/packages/search?repo=advanced-fabric)

Advanced Fabric is a Helm-packaged control and observation layer for Cilium
native routing, FRR spine/leaf fabrics, service VIPs and policy-driven network
economics.

## Theory-first O/S/I research model

The implementation architecture and triangle closure rules are normative in
[`docs/tradeoff-observatory.md`](docs/tradeoff-observatory.md). The default UI
and controller are a Trade-off Observatory; legacy O/S/I score output is a
deprecated compatibility surface and must not drive routing decisions.

Advanced Fabric treats O (optimality), S (stability), and I (independence) as
latent outcomes to be identified, not dashboard numbers to be designed. The
target trade-off matrix is

```text
    [ 1       tau_OS  tau_OI ]
T = [ tau_SO  1       tau_SI ]
    [ tau_IO  tau_IS  1      ]

tau_XY = partial Y / partial X
```

A finite difference `ΔY/ΔX` is only a descriptive response unless X is changed
by a pre-registered intervention, relevant controls are held fixed, measurement
windows are aligned, and interference/carry-over are addressed. No off-diagonal
`tau_XY` is currently identified. In particular, correlation between two
reported values is not a trade-off coefficient.

### Audit of the current implementation

The current controller contains exploratory transforms: fixed loss/RTT weights
for O, fixed variance/burst/churn weights for S, distinct-dependency ratios for
node I, and traffic weighting plus inverse HHI for Service aggregation. These
transforms are engineering hypotheses only. Their constants were not estimated
from interventions, so their output must not be used to claim the sign or
magnitude of any `tau_XY`.

The repository already observes useful snapshots and active probes, but cannot
yet identify the matrix because it lacks a complete event stream for BGP
updates/withdrawals, explicit disturbance and recovery markers, counter-reset
epochs, aligned RIB/FIB/forwarding timelines, verified dependency provenance,
and experiment/treatment/control identifiers. Until those gaps close, new work
must preserve raw values and uncertainty instead of adding another O/S/I scoring
formula.

### Measurement catalog

The normative, machine-readable catalog is
[`docs/osi-measurement-catalog.yaml`](docs/osi-measurement-catalog.yaml). Every
entry specifies `symbol`, semantic meaning, source, unit, sampling method, scope,
time window, validity conditions, and uncertainty. The catalog groups the raw
observables as follows:

| Class | Required raw observables |
| --- | --- |
| Path / forwarding structure | `x_nh` selected next-hop; `p_route` route-preference vector; `w_ecmp` installed ECMP width; `m_route` candidate-route multiplicity; `n_path_change` path-set changes; `d_mode` Cilium/native/tunnel mode; `a_reach` reachability; `l_path` loss; `t_rtt` RTT distribution; `b_rx` Service inflow |
| Control-plane dynamics | `b_est` established state; `n_peer` configured/established peers; `n_adv`, `n_recv` advertised/received prefixes; `u_bgp`, `w_bgp` updates/withdrawals; `t_conv` convergence time; `lambda_flap` flap intensity; `delta_ribfib` RIB/FIB changes |
| Redundancy / independence | `n_nh` viable next-hops; `n_if` interfaces; `n_tun` tunnels; `n_gw` gateways; `n_asn` ASN dependency sets; `g_dep` attributed dependency graph; `n_alt` simultaneously viable alternatives |
| Temporal response | `t_state` transition timestamps; `t_persist` persistence; `f_switch` switching frequency; `t_recover` recovery latency; `a_osc` oscillation amplitude |

Counts are not interchangeable. For example, installed ECMP width is not the
number of viable alternatives, and distinct tunnel names are not independent
failure domains. Unknown dependency edges remain unknown rather than being
counted as diversity.

### Alignment with existing measurement practice

This model reuses established measurement semantics instead of defining a
private meaning for latency, loss, convergence, or telemetry:

- The IETF IPPM framework requires precise metric parameters and separates a
  singleton observation, a sample stream, and statistics derived from that
  sample. Advanced Fabric therefore retains raw outcomes and sampling metadata
  before percentiles or latent inference ([RFC 2330](https://datatracker.ietf.org/doc/rfc2330/)).
- Delay is scoped by packet type and endpoints. One-way delay also requires
  clock synchronization and reported timing uncertainty; an unsuccessful
  packet after the declared waiting threshold is undefined/lost, not a very
  large successful delay ([RFC 7679](https://datatracker.ietf.org/doc/rfc7679/)).
  Delay variation is a separate stream metric, not an alias for a p95 value
  ([RFC 3393](https://datatracker.ietf.org/doc/rfc3393/)).
- Packet size and sampling schedule can change the system being measured.
  Periodic, Poisson, passive, and event-triggered samples are therefore labeled
  and not silently pooled ([RFC 7312](https://datatracker.ietf.org/doc/rfc7312/)).
- BGP session state, control-plane convergence, RIB convergence, FIB
  installation, and actual forwarding convergence are distinct stages
  ([RFC 4098](https://datatracker.ietf.org/doc/rfc4098/)). The convergence
  experiments follow the event-to-forwarding structure and repeated-trial
  reporting of [RFC 7747](https://datatracker.ietf.org/doc/rfc7747/), rather
  than timing a single `Established` snapshot.
- Route updates and withdrawals should come from an event-preserving source.
  BMP carries ongoing Adj-RIB-In monitoring and BGP Update messages, making it
  preferable to inferring all churn from periodic snapshots
  ([RFC 7854](https://datatracker.ietf.org/doc/rfc7854/)). FRR's official JSON
  interfaces expose neighbor message statistics, accepted/sent prefix counts,
  advertised/received routes, best-path state, and flap statistics
  ([FRR BGP documentation](https://docs.frrouting.org/en/latest/bgp.html)).
- For production traffic, passive or hybrid block measurement can complement
  synthetic probes. Alternate Marking defines live-traffic loss/delay/jitter
  measurement ([RFC 9341](https://datatracker.ietf.org/doc/rfc9341/)); IOAM can
  expose node/interface/timestamp/transit-delay fields inside a controlled
  domain ([RFC 9197](https://datatracker.ietf.org/doc/rfc9197/)). Neither is
  presumed available on every Cilium path.
- Hubble provides flow visibility and Service dependency context, but a Hubble
  observation is retained as a flow/event signal and is not automatically
  equivalent to IPPM active-path loss or a physical failure-domain observation
  ([Cilium Hubble documentation](https://docs.cilium.io/en/stable/observability/hubble/)).
- CNCF's observability guidance treats metrics, logs, traces, and other signals
  as complementary and recommends common metadata for correlation. The catalog
  consequently requires shared node/path/service/experiment identity across
  signal types ([CNCF Observability Whitepaper](https://github.com/cncf/tag-observability/blob/main/whitepaper.md)).
  Host interface counters follow explicit counter units and interface identity,
  consistent with the OpenTelemetry system-network conventions
  ([OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/system/system-metrics/)).

These sources standardize observables and experimental procedure; none defines
Advanced Fabric's O/S/I functions or validates an off-diagonal trade-off. The
six latent quantities and every `tau_XY` remain hypotheses to identify.

When `advancedFabric.enabled=true`, each node agent publishes
`advanced-fabric-measurement-<node>` in `kube-system`. Its
`measurement.json` uses `node-measurement-v1alpha1` and contains exactly one
record for every catalog symbol. `envelopeComplete=true` means the schema is
complete; it does **not** mean all evidence was observed. Consumers must inspect
`coverage` and each record's state. `partial` includes the observed value and
the evidence still required for the catalog meaning, while `not-observed`
contains a reason and a null value. This distinction lets any scheduled node
produce a comparable record without manufacturing experimental data.

### Six latent structural quantities

The first model is deliberately limited to six quantities. They remain vectors,
distributions, graphs, or experiment estimands until a scalar mapping is learned:

| Latent quantity | Meaning | Required observables |
| --- | --- | --- |
| `Q` | forwarding/path quality under fixed endpoint and protocol semantics | `a_reach`, `l_path`, `t_rtt` |
| `K` | convergence responsiveness following a declared disturbance | `t_state`, `t_conv`, `t_recover`, `a_reach`, `delta_ribfib` |
| `H` | path hysteresis/inertia under controlled preference sweeps | `p_route`, `x_nh`, `t_state`, `t_persist`, `f_switch`, `a_osc` |
| `C` | control-plane churn as an event process | `u_bgp`, `w_bgp`, `lambda_flap`, `delta_ribfib`, `n_path_change`, `f_switch` |
| `R` | usable route redundancy, not configured route count | `w_ecmp`, `m_route`, `n_peer`, `n_nh`, `n_if`, `n_alt` |
| `D` | evidence-backed failure-domain diversity | `n_tun`, `n_gw`, `n_asn`, `g_dep`, `n_alt` |

The initial structural decomposition is a research hypothesis:

```text
O = f(Q, K)
S = g(H, -C)
I = h(R, D)
```

It does not define `f`, `g`, or `h`, and the minus sign records a direction to
test rather than a universal truth. Candidate hypotheses include higher K
improving O while reducing S, higher H improving S while delaying recovery and
reducing O, higher D improving I while constraining Q and O, and higher R
improving I while increasing C and reducing S. Each relationship may be zero,
non-monotonic, context-dependent, or reversed and must be experimentally
identified.

### Dependency graph and identification path

```text
active probes ── a_reach, l_path, t_rtt ───────────────► Q ─┐
fault markers + RIB/FIB/probes ─ t_conv, t_recover ───► K ─┴► O

route-policy sweeps + selections ─ persistence/switch ─► H ─┐
BGP/rtnetlink event streams ─ updates/withdrawals/flaps ► C ─┴► S

RIB/FIB + per-path viability ─ ECMP/candidates/n_alt ───► R ─┐
verified topology graph ─ interface/tunnel/gateway/ASN ─► D ─┴► I

pre-registered interventions + controls + aligned windows
              O, S, I response surfaces ────────────────► tau_XY
```

The priority experimental derivatives are `∂S/∂K`, `∂O/∂H`, `∂O/∂D`, and
`∂S/∂R`. A valid experiment must record an experiment ID, treatment, baseline,
control vector, subject/path scope, start/end timestamps, washout period,
collector restart epochs, raw observations, missingness, and uncertainty. Safe
candidate interventions include bounded route-preference changes, one-peer
withdrawal drills, controlled impairment of one path, and hysteresis-threshold
sweeps. Production incidents may provide supporting natural experiments but do
not automatically identify causal derivatives.

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

The first repository experiment report, with a versioned summary of the
historical evidence that motivated this model, is
[`docs/experiment-report-2026-09-08.md`](docs/experiment-report-2026-09-08.md).

The self-contained component boundary and provider-neutral API for ingress,
scheduling, failover, DNS, UI and other consumers is documented in
[`docs/component-contract.md`](docs/component-contract.md).

### Service inflow measurement

Advanced Fabric accepts completed service-flow windows from any dataplane
collector (for example Hubble, a Gateway implementation, or a metrics adapter).
The collector publishes a ConfigMap in the observed Service namespace with
`app.kubernetes.io/component=service-traffic` and a `measurement.json` value:

```json
{
  "observedAt": "2026-09-09T06:00:00Z",
  "windowSeconds": 60,
  "collector": "hubble-flow-adapter",
  "samples": [
    {
      "namespace": "example",
      "service": "api",
      "node": "worker-a",
      "receivedBytes": 1048576,
      "receivedPackets": 8192,
      "requests": 1200
    }
  ]
}
```

Counters are concrete values for that non-overlapping completed window, not
cumulative process counters. O and S are the destination-node values weighted
by received bytes (falling back to packets, then requests). I is normalized
inverse HHI over the receiving nodes' declared failure domains. The resulting
Service `NetworkPathAssessment` includes counters, rates, window, sample count,
collector names, confidence, and each formula's numerator and denominator.
Invalid, missing, future work, and stale samples never become zero-valued OSI.

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
  --version 0.20.0 \
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
