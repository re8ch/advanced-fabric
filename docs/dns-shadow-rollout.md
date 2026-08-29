# Advanced Fabric DNS shadow rollout

The first rollout is deliberately non-disruptive. It creates
`kube-system/advanced-fabric-dns` on UDP/TCP 53 and
`kube-system/advanced-fabric-doh` on HTTPS 443. Neither Service selects the K3s
CoreDNS Pods and neither changes `kube-system/kube-dns`.

## Required values

```yaml
advancedFabric:
  enabled: true
  networkQuality:
    enabled: true
    samples: 20
    dns:
      shadowEnabled: true
      shadowServiceName: advanced-fabric-dns
      maximumFailureRatio: 0.001
      maximumP95Ms: 50
      requireTcp: true
    doh:
      enabled: true
      maximumFailureRatio: 0.001
      maximumP95Ms: 100
```

## Upgrade order

Helm does not upgrade files in a chart's `crds/` directory. Before upgrading an
existing release, apply the reviewed `crds/advanced-fabric.yaml` revision, wait
for the CRD Established condition, and only then upgrade the release. A
server-side dry-run must accept `spec.networkQuality` before rollout proceeds.

## Observation and promotion

Keep the stable and shadow Services separate for at least 24 hours. Promotion
requires all 676 directed Host/Pod paths, all 104 stable/shadow UDP/TCP DNS
measurements, and all 26 DoH measurements to be fresh and passing. VictoriaMetrics
must show DNS and DoH failure at or below 0.1%, cluster DNS p95 at or below 50 ms,
DoH p95 at or below 100 ms, and no sustained SERVFAIL, timeout or abnormal
NXDOMAIN increase.

Promotion is not part of this chart revision. It requires a separate reviewed
change that disables the K3s packaged CoreDNS authority on every server and
transfers the existing `kube-dns` Service contract, including ClusterIP
`10.43.0.10`, to the self-managed Deployment. Never run both controllers against
the same `kube-dns` object.

## Rollback

Before promotion, set `dns.shadowEnabled=false` and `doh.enabled=false`; the
stable resolver path is untouched. After promotion, restore K3s CoreDNS ownership
and its prior Service selector before removing the self-managed endpoints.
