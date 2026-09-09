# Changelog

## 0.21.1

- Use the FRR 10.x-compatible `show bgp neighbors json` command. The previous
  AFI-qualified `brief` form returned successful plain text, causing `jq` to
  reject the node status envelope before measurement publication.

## 0.21.0

- Publish one `node-measurement-v1alpha1` ConfigMap per scheduled node with all
  31 catalog symbols and explicit `observed`, `partial`, or `not-observed`
  evidence state.
- Combine Linux FIB/nexthop/interface state, FRR neighbor/RIB state and host/pod
  conformance results into a common timestamped node envelope.
- Report coverage and missing-evidence reasons; disturbance-only measurements
  remain absent until a pre-registered experiment supplies them.

## 0.20.0

- Register `service-traffic-v1`, a provider-neutral input contract for traffic
  entering Kubernetes Services, attributed to the destination node.
- Calculate Service optimality and stability as traffic-weighted node values and
  failure independence as normalized inverse HHI across observed failure domains.
- Publish the input window, samples, bytes, packets, requests, rates, confidence,
  and formula operands on Service-scoped `NetworkPathAssessment` resources.
- Keep missing and stale evidence unknown; no traffic or OSI value is synthesized.

## 0.19.0

- Add opt-in two-phase adoption from a legacy IngressClass: render and validate
  the Gateway API shadow route first, then change the source Ingress class.
- Resolve named Service ports and translate literal `ImplementationSpecific`
  paths while continuing to reject regex-like paths and unsupported middleware.
- Select every matching HTTP/HTTPS Gateway listener and support explicit
  namespace/name exclusions for exceptional applications.

## 0.18.0

- Add the opt-in `advanced-fabric` IngressClass and a fail-closed compatibility
  controller that translates standard host/path/Service backends to HTTPRoute.
- Keep the request data plane entirely on the selected Cilium Gateway; unsupported
  Traefik annotations and implementation-specific paths are explicitly rejected.

## 0.17.1

- Pin all external runtime images by immutable multi-platform OCI digest.
- Synchronize the Artifact Hub image inventory with controller, agent, metrics, DNS, Headlamp installer, and optional mihomo workloads.
- Replace the unavailable `registry.k8s.io/busybox:1.36.1` Headlamp installer default with the digest-pinned Docker Official Image.

## 0.17.0

- Bound freshness to the actual sampling duration and collector cadence.
- Probe path endpoints concurrently so degraded targets cannot serialize an
  entire node matrix beyond its own freshness window.
- Keep planner tasks pending until their formal evidence gap is actually closed.
- Report topology gaps by dependency dimension.
- Measure explicitly declared same-destination alternatives for O; unrelated
  target nodes remain non-comparable.

## 0.16.0

- Close the component loop with a controller-owned `NetworkPathAssessment` API.
- Publish independent formal O/S/I values, per-dimension confidence, evidence
  provenance, diagnosis, recommendation, validation and explicit validity.
- Prune assessments for nodes that leave active Kubernetes membership.
- Replace consumer-specific integration planning with a provider-neutral
  component contract.

## Unreleased

- Persist the latest 96 normalized O/S/I snapshots per active node in a shared
  ConfigMap, retaining null for dimensions whose evidence is unavailable.
- Prune legacy desired-state keys by comparing the existing ConfigMap with
  active Kubernetes membership, including nodes no longer present in CR spec.
- Use the Kubernetes-injected API Service endpoint for controller reconciliation
  instead of the accelerated fabric VIP, so dataplane degradation cannot stall
  evidence planning and status publication.
- Add a controller-side Evidence Planner that turns missing optimality,
  stability, independence, or freshness evidence into generation-bound probe
  tasks. Host and pod collectors acknowledge completed task IDs so inference,
  recommendation, and post-action validation can be recomputed continuously.
- Derive active Advanced Fabric membership from Kubernetes Node objects joined
  with declared topology metadata. Retired inventory entries no longer count
  toward measurement coverage, peer selection, desired agent state, or API
  eligibility, and stale desired-state keys are removed automatically.

## 0.12.0

- Replace snapshot freshness and candidate-peer count proxies with an explicit
  Measurement → Evidence → Inference → Recommendation → Validation pipeline.
- Add rolling loss/p95 variance, BGP and route churn, host/pod DNS divergence,
  evidence freshness and confidence-aware rule diagnosis.
- Evaluate failure independence from provider, ASN, failure domain, gateway and
  tunnel metadata, and require remeasurement after shadow probes or route changes.
- Put O/S/I and diagnosis on the Headlamp landing view and move raw snapshots
  below the decision layer.

## 0.11.7

- Return authoritative NODATA for existing Kubernetes Service names that do not
  have the requested address family, preserving valid IPv4 resolution for
  musl clients that query A and AAAA together.

## 0.11.6

- Allow one authoritative DNS replica to become unavailable during rolling
  updates, avoiding a surge/anti-affinity deadlock on an exact three-node pool.
- Support an ordered list of direct Kubernetes API endpoints, avoiding a
  circular dependency on the Service dataplane and failing over by IP.

## 0.11.5

- Expose authoritative DNS node selector and affinity so operators can exclude
  unstable workstation/WSL nodes and pin replicas to reviewed failure domains.

## 0.11.4

- Add a reloadable static-authority mode for bootstrap and private management
  zones that must remain independent of the Kubernetes API.
- Allow binding DNS/health/metrics to one management IP and disabling DoH when
  no host certificate is provisioned.

## 0.11.3

- Roll authoritative DNS pods on every chart revision so renewed certificates
  are loaded instead of remaining resident in an old TLS listener.
- Hot-reload projected TLS keypairs and let the Helm hook retry verified DoH
  during the bounded cert-manager projection window.

## 0.11.2

- Make the Helm health hook bootstrap-safe by testing DoH directly through the
  fixed DNS Service IP and include that IP in the certificate SANs.
- Correct the hook cleanup annotation and retain the pre-0.11 ServiceAccount so
  an in-flight rollback can complete during migration.

## 0.11.1

- Preserve explicit private-zone conditional forwarding with longest-suffix matching.
- Expose native Advanced Fabric DNS query, failure, forwarding, and readiness metrics on port 9153.

## 0.11.0

- Make Advanced Fabric DNS authoritative for Kubernetes Service discovery over UDP/TCP 53 and DoH.
- Watch Services and EndpointSlices directly for ClusterIP, ExternalName, headless endpoint and SRV records.
- Remove all runtime dependencies on the K3s CoreDNS Deployment, ConfigMap, Corefile and NodeHosts.
- Add fixed service-IP, migration-phase, HA rollout and readiness controls.

## 0.10.1

- Make observe-only host agents strictly read-only: route, WireGuard and FRR
  removal is a mutation and no longer runs outside guarded apply.
- Validate mutation transactions and FRR availability only when guarded apply
  is actually enabled, so observation remains available on heterogeneous nodes.

## 0.10.0

- Separate continuous measurement from quality enforcement; enforcement remains
  disabled until a reviewed long-term baseline exists.
- Publish a VMServiceScrape when the VictoriaMetrics Operator is installed.
- Add per-node network-intelligence triangle evidence to the Headlamp view.
- Document observe-first intelligent routing and idempotent hedge constraints.

## 0.9.1

- Remove quadratic `uniqueItems` validation from RouterOSNode CRD arrays so Kubernetes can replace the CRD during Helm upgrades.

## 0.9.0

- Add a strict `4 × N²` directed Host/Pod connectivity matrix with independent
  endpoints in both network namespaces.
- Add UDP and TCP DNS synthetics from every Host/Pod source, with freshness,
  coverage, failure-ratio and latency gates.
- Export the NWQ-1/DNSQ-1 metric contract through a per-node Node Exporter
  textfile collector for VictoriaMetrics and Grafana.
- Add an opt-in CoreDNS 1.14.3 RFC 8484 canary and fail-closed DoH quality gate
  without changing the production `kube-dns` client contract.
- Add an independently selected UDP/TCP 53 shadow Service that preserves K3s
  custom zones and NodeHosts, and compare stable/shadow paths before promotion.
- Publish `NetworkConformanceReady` and `DNSQualityReady`; when measurement is
  enabled, both conditions participate in `ApplySafe` and guarded apply.
- Document NWQ-1 and DNSQ-1 metric contracts, diagnostic interpretation,
  operational DNS telemetry and evidence retention.

## 0.8.0

- Add `RouterOSNode/v1alpha2` with role-scoped peers, protected prefixes,
  dedicated acceleration FIBs and explicit public VIP fallback intent.
- Add an observe-first RouterOS controller that publishes a deterministic
  transaction checksum and refuses all mutations behind a release gate.
- Preserve `main`, the physical default route and operator workstation traffic
  as hard invariants of every RouterOS transaction.

## 0.7.0

- Adds an observe-first `controlPlaneApi` contract for an exact accelerated
  `/32`, health-eligible ECMP origins, WireGuard primary transport and
  ZeroTier fallback transport.
- Publishes per-node local API readiness without changing FRR, loopback
  addresses, K3s identity or etcd membership.

## 0.3.0

- Adds the authoritative seven-spine A/B/C inventory and deterministic private ASNs.
- Adds observe-only `AdvancedFabric` and `TrafficPolicy` control loops.
- Standardizes Cilium native routing for `10.42.0.0/16` while keeping
  `auto-direct-node-routes=false` for the multi-WAN underlay.
- Adds quota-aware fastest, balanced and greedy policy models; weighted ECMP
  remains disabled until a separate acceptance gate passes.
- Standardizes the Harbor/OCI endpoint as `https://registry.re8ch.com` on port
  443. No third-party mirror credentials are packaged.
## 0.11.8

- Flatten in-cluster ExternalName address lookups so Go clients resolve Kubernetes service aliases reliably.
