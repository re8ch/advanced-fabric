# Cluster network quality standard

This standard answers a binary architecture question: can every scheduled node
participate in the cluster's native-routing and DNS data planes? A healthy
Cilium controller is necessary but is not evidence that the distributed data
plane passes this standard.

## NWQ-1: directed reachability matrix

For `N` inventory nodes the probe set contains `4 × N²` directed paths. Every
node runs one probe in the host network namespace and one in an ordinary pod
network namespace. Each probe provides the same TCP health contract on port
4241. Every source probes both endpoints of every target, including itself:

| Source namespace | Target address | What it proves |
| --- | --- | --- |
| host | target Host IP:4241 | underlay, host route and host health firewall |
| host | target health Pod IP:4241 | host-to-PodCIDR route and return path |
| pod | target Host IP:4241 | Pod egress, host endpoint policy and return path |
| pod | target health Pod IP:4241 | cross-node PodCIDR forwarding and endpoint policy |

The matrix is directional: `A → B` never substitutes for `B → A`. Missing and
stale observations fail coverage instead of being interpreted as success. The
result records source/target node, both planes, address, attempts, successes,
loss ratio, p50/p95 latency and error class. Addresses are observations, not
configuration authority.

The default release gate is:

- 100% fresh path coverage; a 13-node cluster therefore requires 676 paths.
- 0 observed loss in a gate window. Production SLOs may use longer-window error
  budgets, but a rollout conformance run is intentionally strict.
- p95 no greater than 20 ms within a region and 400 ms across regions.
- every source publishes within 120 seconds.
- any single failed direction makes `NetworkConformanceReady=False`.

The first implementation conservatively applies the cross-region ceiling to
all paths. Region-aware classification must come from inventory labels; it must
not infer geography from latency. This avoids hiding a route failure behind a
misclassified RTT.

The chart-owned endpoint avoids coupling the standard to Cilium's internal
health-endpoint representation, which varies by release. `cilium-health status`
remains corroborating evidence but does not define the conformance target set.

To diagnose a failure, compare the four cells for the same source/target pair.
Host→host-only success points toward PodCIDR routing. Pod→pod-only success points
toward the host endpoint firewall. One-way success points toward a return route,
reverse-path filtering, asymmetric policy, or stateful firewall boundary.

## DNSQ-1: DNS architecture quality

Every host and pod source queries the `kube-dns` Service IP over UDP and TCP.
The canonical positive query is `kubernetes.default.svc.cluster.local`. TCP is
mandatory because truncation and fallback are part of the DNS architecture.
For `N` nodes, one interval requires at least `4 × N` measurements.

The release gate is:

- all `2 × N` sources are fresh and both UDP and TCP are present;
- failure ratio no greater than 0.1% over the evaluation window;
- successful-query p95 no greater than 50 ms;
- no timeout, malformed response or transaction-ID mismatch;
- any missing source or protocol makes `DNSQualityReady=False`.

When the private Advanced Fabric DoH canary is enabled, each Host/Pod source
also sends an RFC 8484 `application/dns-message` POST for the canonical query.
`DoHQualityReady` requires all `2 × N` sources, failure ratio no greater than
0.1%, p95 no greater than 100 ms and certificate validation against the mounted
canary certificate. DoH is an additional gate; it never compensates for a
failed UDP/TCP cluster DNS path.

When `dns.shadowEnabled` is true, every Host/Pod source must also measure the
independent `advanced-fabric-dns` Service over UDP and TCP. Metrics distinguish
`server_role="stable"` from `server_role="shadow"`; any missing shadow path
fails closed. Promotion to the stable `10.43.0.10` Service is outside the chart
and is allowed only after the network, DNS and DoH gates remain green for the
reviewed observation window.

The live probe is deliberately low-rate and does not replace CoreDNS telemetry.
The operational DNS SLO must additionally retain, by server and response code:

- request rate and top client/workload sources;
- NOERROR, NXDOMAIN, SERVFAIL and timeout rates;
- cache hit/miss ratio and upstream latency;
- UDP truncation and TCP fallback rate;
- forward-loop/reload errors, process CPU throttling and saturation;
- synthetic in-cluster, external-recursive, NXDOMAIN and TCP queries.

CoreDNS Prometheus metrics do not identify the requesting pod. Workload-level
storm attribution therefore requires a separately governed Hubble DNS metric
or sampled DNS-query log pipeline with source namespace/workload labels. Do not
enable unbounded query-name labels or full query logging as a shortcut; the
default dashboard exposes server-side QPS/type/rcode and the bounded per-node
synthetic source until that attribution pipeline is approved.

Suggested alert gates are SERVFAIL or timeout above 0.1% for 5 minutes, p95 above
50 ms for 5 minutes, or CoreDNS CPU saturation above 80% for 10 minutes. NXDOMAIN
is not intrinsically an error: alert on a baseline-relative surge and identify
the clients and queried suffixes before changing capacity.

## Status and rollout semantics

Probe snapshots are stored as labeled ConfigMaps in `kube-system`; the
controller aggregates them into `AdvancedFabric.status.networkQuality`. The
conditions `NetworkConformanceReady` and `DNSQualityReady` expose independent
gates. When network-quality measurement is enabled, both gates, complete
inventory, eligible spines and the emergency-disable state participate in
`ApplySafe` and effective guarded apply.

The feature defaults to disabled so chart installation remains observe-first.
Enable it with `advancedFabric.networkQuality.enabled=true`. Enabling measurement
does not modify routes, Cilium configuration, firewall rules or CoreDNS.

## Evidence retention

Each host and pod probe atomically writes a separate Prometheus textfile into
`/var/lib/re8ch/advanced-fabric/textfile` on its node. A chart-owned, textfile-
only Node Exporter exposes those files; VictoriaMetrics discovers the headless
exporter Service through Kubernetes endpoints. Grafana reads only the
VictoriaMetrics datasource. ConfigMaps remain a current-state exchange for the
controller gate and are not the historical metrics path.

The stable metric families are:

- `re8ch_network_quality_probe_info` and
  `re8ch_network_quality_observed_timestamp_seconds` for coverage/freshness;
- `re8ch_network_path_attempts`, `re8ch_network_path_successes`,
  `re8ch_network_path_loss_ratio` and p50/p95 latency for every directed cell;
- `re8ch_network_path_selected_source_info` for route/source-address evidence;
- `re8ch_dns_probe_attempts`, `re8ch_dns_probe_successes`, failure ratio,
  p50/p95 latency and response-code counts for UDP/TCP DNS.
- `re8ch_doh_probe_attempts`, `re8ch_doh_probe_successes`, failure ratio,
  p50/p95 latency and response-code counts for the private RFC 8484 canary.

Path series use bounded labels: source/target node, source/target plane and the
observed endpoint address. DNS series use source node/plane, server, protocol
and canonical query. Query names are fixed by the standard; arbitrary DNS names
must never become labels.

Keep at least 30 days of raw samples and 13 months of hourly aggregates so
regressions can be compared across topology and release changes. A conformance
certificate must record the inventory generation, expected/observed matrix
size, thresholds, failed cells and the exact evaluation interval.
