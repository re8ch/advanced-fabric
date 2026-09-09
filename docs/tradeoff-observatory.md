# Trade-off Observatory

Advanced Fabric is a measurement and identification system. It does not assign
health scores to Optimality, Stability, or Independence. Its job is to retain
network observations, align them around changes, and state whether a trade-off
is identified.

## Model boundary

The six v1 structural quantities are deliberately not normalized scores:

| Quantity | Meaning | Required measurements |
| --- | --- | --- |
| `Q` | forwarding quality | reachability, loss and RTT distributions for a fixed endpoint/protocol |
| `K` | response to a declared disturbance | transition, RIB/FIB, reachability and recovery timestamps |
| `H` | hysteresis/inertia | preference sweep, selected path, persistence, switching and oscillation |
| `C` | control-plane disturbance | BGP updates/withdrawals, flaps, RIB/FIB and path changes |
| `R` | simultaneously usable route redundancy | ECMP, candidate routes, peers, next-hops, interfaces and viable alternatives |
| `D` | verified failure-domain independence | tunnels, gateways, ASNs and attributed dependency graph |

The hypotheses `O=f(Q,K)`, `S=g(H,-C)` and `I=h(R,D)` are research
decompositions, not implemented formulas. The off-diagonal matrix terms
`tau_XY = partial Y / partial X` remain `NotIdentified` until an intervention
provides compatible before/control/after windows. A finite difference from an
uncontrolled change is descriptive evidence, not a causal coefficient.

## Data flow and validity

```text
Cilium/Linux/FRR/BGP/Gateway observations
  -> versioned measurements and Prometheus time series
  -> Q/K/H/C/R/D structural observations
  -> automatically discovered NetworkIntervention candidates
  -> aligned before/control/after windows
  -> NetworkTradeoffEstimate
  -> TradeoffTriangle closure
```

Every observation retains its physical/statistical unit, scope, time window,
collector epoch, definition ID, missingness and uncertainty. `unknown`, stale,
counter reset, node NotReady and unexecuted measurements are never encoded as
zero. Collector restarts, overlapping changes, carry-over and incompatible
scopes mark an intervention `Confounded`.

Automatic discovery is intentionally conservative. Kubernetes generations,
Gateway changes, node transitions, datapath changes, BGP session transitions
and RIB/FIB fingerprints may create intervention candidates. Discovery alone
never makes an estimate `Identified`.

## Initial hypothesis registry

The chart installs three read-only triangle definitions:

1. `redundancy-independence-churn`: `R`, `D`, `C`
2. `responsiveness-inertia-quality`: `K`, `H`, `Q`
3. `redundancy-responsiveness-churn`: `R`, `K`, `C`

A triangle is closed only when all three vertices have valid observations and
all three directed relationships have identified estimates with compatible
scope and windows. Otherwise consumers render the open triangle and list the
missing evidence. Multiple triangles may target different nodes, Services,
Gateways, routes or policy selectors.

## PublicEdge contract

`NetworkPathAssessment/v1alpha2` is an operational eligibility contract, not a
trade-off result. It reports Node readiness, measurement freshness, host/pod
reachability, current-path evidence and viable alternatives. PublicEdge may use
it as a fail-closed publication gate. It must not turn Observatory estimates or
legacy O/S/I dimensions into routing weights.

## Storage

Prometheus-compatible storage is authoritative for time series. Kubernetes CRs
contain discovery metadata and bounded summaries only, with evidence references
back to metric queries and measurement objects. Production targets 90 days of
retention subject to an explicit disk-capacity preflight and alerting.
