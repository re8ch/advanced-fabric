# Advanced Fabric: evidence-driven network reality experiment

**Date:** 2026-09-08  
**Environment:** heterogeneous 12-node K3S fabric across on-premises, WSL and public cloud  
**Release:** Advanced Fabric 0.15.0  
**Evidence:** [`experiments/2026-09-08-measurement-summary.json`](experiments/2026-09-08-measurement-summary.json)

## Executive result

This experiment shows why network infrastructure must measure the dataplane
instead of equating process health with service health. All observed nodes could
report FRR active while end-to-end loss, latency and DNS behavior still differed
materially by node and between host and pod planes.

After a K3S restart, `r640` reported FRR active and 8/10 BGP peers established,
yet its fresh host measurement had loss on 11/24 paths and a worst successful
p95 of 291.245 ms. Its preceding pod sample had loss on 12/24 paths and a worst
successful p95 of 336.279 ms. The restart restored control-plane participation;
it did not establish dataplane health.

The experiment falsified two common shortcuts:

1. **BGP completeness is not dataplane health.** `b1-wsl-zt` had incomplete BGP
   while host and pod worst p95 stayed near 49 ms with fewer lossy paths.
2. **Freshness is not stability.** Stability requires variance, bursts and
   routing churn over an explicit interval. A restarted collector is fresh but
   initially has insufficient history.

These results motivated the formal 0.15 pipeline:
`Network Reality → Measurements → Evidence → O/S/I`.

## Hypotheses and method

- Active FRR and mostly established BGP peers are insufficient to prove
  dataplane health.
- Host and pod measurements can expose resolver or datapath divergence.
- Freshness and peer count cannot substitute for stability and independence.
- A formal model can preserve incomplete knowledge without creating false zero
  or false healthy states.

Collectors sampled TCP reachability, loss and latency from host and pod planes
to the active node matrix. DNS was measured separately for each plane. Rolling
aggregates retained loss and p95 means/variance over explicit windows. FRR/BGP
and kernel routes were observed separately from dataplane probes.

The active inventory contained 12 Kubernetes members. Migrated `qwen-1/2/3`
and powered-off `overseas-edge-50` were excluded from active inference. Retained
old measurements were marked stale rather than treated as current reality.

Version 0.15 evaluates independent axes:

- **O — relative path optimality:** current quality versus feasible alternatives
  measured under comparable scope and procedure.
- **S — temporal invariance:** loss variance, latency variance, loss bursts,
  BGP churn and route churn over an explicit window.
- **I — failure-domain independence:** dependency-graph diversity across gateway,
  ISP, ASN, tunnel and physical path.

O, S and I are independent `[0,1]` values and never sum-normalized. Each has
separate confidence. Missing required evidence produces `null`, never zero. See
[`network-quality-standard.md`](network-quality-standard.md) for measurement
contracts.

## Observations

| Node / plane | UTC | Lossy paths | Worst p95 | DNS failures | Interpretation |
|---|---:|---:|---:|---:|---|
| r640 / host | 06:01:10 | 11/24 | 291.245 ms | 2/2 | Degradation remains after restart |
| r640 / pod | 05:57:53 | 12/24 | 336.279 ms | 0/2 | Severe dataplane degradation; DNS differs |
| b1-wsl-zt / host | 06:09:13 | 5/24 | 48.319 ms | 0/2 | Better dataplane despite incomplete BGP |
| b1-wsl-zt / pod | 06:08:21 | 7/24 | 48.540 ms | 0/2 | Similar host/pod latency |
| domain-ex / host | 05:53:38 | 11/24 | 125.095 ms | 2/2 | Host resolver failure |
| domain-ex / pod | 06:03:46 | 12/24 | 58.852 ms | 0/2 | Pod DNS succeeds: plane divergence |
| muaya-ecs / host | 05:58:17 | 11/24 | 130.821 ms | 2/2 | Host resolver failure |
| muaya-ecs / pod | 05:56:09 | 11/24 | 346.300 ms | 0/2 | DNS succeeds while latency is poor |

R640's rolling evidence strengthens the snapshot result. Over 240 seconds, host
mean loss was 0.4583 and p95 standard deviation was 65.039 ms. The preceding
480-second pod window had mean loss 0.5077 and p95 standard deviation 171.067
ms. This is sustained loss with strong latency dispersion, not one slow request.

At capture, nine active nodes had the configured 96 retained snapshots,
`overseas-la` had 70, and two nodes had one. Uneven history is evidence coverage,
not a network score.

## Supported claims and limitations

The evidence supports saying that Advanced Fabric:

- detects control-plane/dataplane disagreement;
- distinguishes host and pod planes and detects DNS divergence;
- preserves freshness, history and measurement coverage;
- plans missing measurements and recomputes inference after evidence updates;
- represents partial O/S/I state without silently converting unknown to bad.

It does not yet prove globally optimal routing, long-term SLA attainment or full
physical-path independence. Formal O was unknown because comparable feasible
alternative measurements were unavailable. I was unknown because the complete
dependency graph was not declared. Restarted collectors initially lacked enough
history for S. These nulls are correct experimental outcomes.

Pre-0.15 snapshots used provisional scoring and overall confidence. They remain
for audit continuity but must not be joined quantitatively with formal 0.15
states. Historical visualization should segment the model-version boundary.

## Product significance

Advanced Fabric is not another green control-plane dashboard. Its differentiator
is a closed evidence loop: inference finds missing or contradictory evidence;
the planner requests bounded measurement or shadow probes; registered collectors
publish evidence; independent timestamped O/S/I is recomputed; recommendations
remain conditional on confidence; and every action is followed by remeasurement.

This makes uncertainty visible and testable. Historical radar geometry can show
drift, collapse, recovery and trade-offs without confusing topology metadata
with measured behavior.

## Reproduction and long-term baseline

Every future experiment should record release, active membership and schema;
retain host/pod path, DNS and churn evidence; version feasible alternatives and
the dependency graph; capture O/S/I with per-dimension confidence before action;
then repeat the same procedure after a shadow probe or controlled path change.

Recommended campaigns are a 30-day steady-state baseline, controlled
gateway/ISP/tunnel failure injection, and paired pre/post path-switch trials.
External claims should always quote sample size, time window and confidence and
link to a versioned evidence fixture such as this report's JSON companion.
