#!/usr/bin/env python3
"""Observe-first Kubernetes reconciler for Advanced Fabric."""

import json
import hashlib
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import math
import datetime


HOST = os.environ.get("API_HOST", os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"))
PORT = os.environ.get("API_PORT", os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
BASE = f"https://{HOST}:{PORT}"
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
CONTEXT = ssl.create_default_context(cafile=CA)
ADVISOR_URL = os.environ.get("ADVISOR_URL", "http://re8ch-routing-advisor.qianwen-ops.svc.cluster.local:9790")
MEASUREMENT_DEFINITIONS = {
    "path-quality-v1": {"scope": "source node/plane to one selected path endpoint", "unit": "loss ratio and milliseconds",
        "samplingProcedure": "bounded TCP attempts at configured cadence", "timeWindow": "one collector interval",
        "failureSemantics": "timeout/refusal is loss; absent endpoint is missing", "uncertainty": "finite samples and TCP-only reachability",
        "measurementCost": "samples TCP handshakes per path"},
    "dns-quality-v1": {"scope": "source node/plane to one configured resolver endpoint", "unit": "failure ratio and milliseconds",
        "samplingProcedure": "bounded DNS queries at configured cadence", "timeWindow": "one collector interval",
        "failureSemantics": "timeout or invalid response is failure; absent resolver is missing", "uncertainty": "finite samples and resolver-cache effects",
        "measurementCost": "samples DNS queries per resolver"},
    "temporal-stability-v1": {"scope": "one source node across host and pod planes", "unit": "normalized invariance",
        "samplingProcedure": "rolling loss/latency series plus BGP and route counters", "timeWindow": "collector history window",
        "failureSemantics": "insufficient window or missing churn is unknown", "uncertainty": "bounded rolling-window estimator",
        "measurementCost": "retained aggregates; no additional packets"},
    "failure-domain-graph-v1": {"scope": "feasible path dependency graph", "unit": "normalized independent-domain ratio",
        "samplingProcedure": "enumerate gateway/ISP/ASN/tunnel/physical-path dependencies", "timeWindow": "topology observation timestamp",
        "failureSemantics": "missing dependency edge is unknown", "uncertainty": "declared topology may lag physical reality",
        "measurementCost": "metadata and route observation only"},
    "service-traffic-v1": {"scope": "traffic entering one Kubernetes Service, attributed to destination node",
        "unit": "bytes, packets, requests and per-second rates",
        "samplingProcedure": "counter deltas exported by a registered Cilium/Hubble, Gateway or Prometheus collector",
        "timeWindow": "collector-declared bounded interval",
        "failureSemantics": "missing, stale or unattributed traffic is unknown; zero is accepted only from an executed window",
        "uncertainty": "sampling and destination attribution depend on the registered collector",
        "measurementCost": "reads retained dataplane counters; no synthetic service traffic"}}


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, context=CONTEXT, timeout=15) as response:
        return json.load(response)


def selector_matches(selector, labels):
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def policy_key(policy):
    spec, meta = policy.get("spec", {}), policy.get("metadata", {})
    return (-int(spec.get("priority", 100)), meta.get("name", ""))


def condition(kind, status, reason, message):
    return {"type": kind, "status": "True" if status else "False", "reason": reason,
            "message": message, "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def advisor_items(path):
    try:
        with urllib.request.urlopen(ADVISOR_URL + path, timeout=5) as response:
            return json.load(response).get("items", [])
    except Exception:
        return []


def quota_pressure(usage, limit):
    if limit in (None, 0):
        return {"ratio": 0.0, "tier": "unlimited", "penalty": 0.0}
    ratio = max(0.0, float(usage) / float(limit))
    if ratio >= 1: return {"ratio": ratio, "tier": "exhausted", "penalty": 1000.0}
    if ratio >= .95: return {"ratio": ratio, "tier": "critical", "penalty": 400.0}
    if ratio >= .80: return {"ratio": ratio, "tier": "warning", "penalty": 120.0}
    return {"ratio": ratio, "tier": "normal", "penalty": -100.0 * ratio}


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def cluster_inventory(spec_nodes, node_objects):
    """Join declared topology metadata to Kubernetes membership facts."""
    present = {item.get("metadata", {}).get("name") for item in node_objects
               if not item.get("metadata", {}).get("deletionTimestamp")}
    active = [node for node in spec_nodes if node.get("name") in present]
    return active, sorted(node.get("name") for node in spec_nodes if node.get("name") not in present)


def stale_desired_nodes(data, active_names):
    return sorted(key[:-5] for key in (data or {}) if key.endswith(".json") and key[:-5] not in active_names)


def measurement_index(configmaps, active_names, freshness, now):
    indexed = {}
    for item in configmaps:
        try:
            result = json.loads(item.get("data", {}).get("result.json", "{}"))
            key = (result["sourceNode"], result["sourcePlane"])
            if key[0] in active_names:
                duration = max(0, float(result.get("measurementDurationSeconds") or 0))
                validity = max(float(freshness), float(result.get("validitySeconds") or 0), duration * 2 + freshness)
                result["effectiveValiditySeconds"] = round(validity, 3)
                result["fresh"] = now - parse_time(result["observedAt"]) <= validity
                indexed[key] = result
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return indexed


def evidence_plan(spec_nodes, node_objects, configmaps, standard, generation, now=None):
    """Turn explicit O/S/I evidence gaps into collector-executable probe tasks."""
    now = time.time() if now is None else now
    active, retired = cluster_inventory(spec_nodes, node_objects)
    names = {node["name"] for node in active}
    freshness = int(standard.get("freshnessSeconds", 120))
    minimum_history = int(standard.get("minimumHistorySamples", 3))
    evidence = measurement_index(configmaps, names, freshness, now)
    tasks = []
    for node in active:
        for plane in ("host", "pod"):
            result = evidence.get((node["name"], plane))
            missing = []
            if not result or not result.get("fresh"):
                missing.append("freshness")
            paths = [] if not result else result.get("paths", [])
            current_paths = [path for path in paths if path.get("measurementDefinitionId") == "path-quality-v1" and
                             path.get("pathRole") == "current"]
            alternative_paths = [path for path in paths if path.get("measurementDefinitionId") == "path-quality-v1" and
                                 path.get("pathRole") == "alternative" and path.get("feasible", True)]
            if not current_paths:
                missing.append("optimality.current-path")
            if not alternative_paths:
                missing.append("optimality.feasible-alternative")
            if not result or int(result.get("history", {}).get("windowSamples") or 0) < minimum_history:
                missing.append("stability")
            topology_gaps = [key for key in ("gateway", "isp", "asn", "tunnel", "physicalPath")
                             if node.get(key) in (None, "")]
            independent = [peer for peer in active if peer["name"] != node["name"] and all(
                peer.get(key) not in (None, "", node.get(key))
                for key in ("gateway", "isp", "asn", "tunnel", "physicalPath"))]
            missing.extend("independence.%s" % key for key in topology_gaps)
            if not topology_gaps and not independent:
                missing.append("independence.independent-path")
            failed = [] if not result else [path for path in result.get("paths", [])
                                            if float(path.get("lossRatio", 0)) > 0]
            if missing or failed:
                task_id = "%s-%s-%s" % (generation, node["name"], plane)
                tasks.append({"id": task_id, "sourceNode": node["name"], "sourcePlane": plane,
                              "kind": "shadow-path" if failed else "evidence-gap",
                              "missingEvidence": sorted(set(missing)),
                              "targetNodes": sorted(names),
                              "candidateNodes": sorted(peer["name"] for peer in independent),
                              "validateAfterAction": bool(failed)})
    executed = {task_id for result in evidence.values() if result.get("fresh")
                 for task_id in result.get("completedTaskIds", [])}
    return {"schemaVersion": "networking.re8ch.com/evidence-plan-v1alpha1",
            "generation": generation, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "activeNodes": sorted(names), "retiredInventoryNodes": retired,
            "tasks": tasks, "executedTaskIds": sorted(executed),
            "completedTaskIds": sorted(task_id for task_id in executed if task_id not in {task["id"] for task in tasks}),
            "pendingTaskIds": sorted(task["id"] for task in tasks)}


def node_inferences(plan, configmaps, standard, now=None):
    """Recompute bounded diagnoses and recommendations from collected evidence."""
    now = time.time() if now is None else now
    evidence = measurement_index(configmaps, set(plan["activeNodes"]),
                                 int(standard.get("freshnessSeconds", 120)), now)
    pending = set(plan["pendingTaskIds"])
    results = []
    for name in plan["activeNodes"]:
        host, pod = evidence.get((name, "host")), evidence.get((name, "pod"))
        host_dns = sum(float(x.get("failureRatio", 0)) > 0 for x in (host or {}).get("dns", []))
        pod_dns = sum(float(x.get("failureRatio", 0)) > 0 for x in (pod or {}).get("dns", []))
        paths = (host or {}).get("paths", []) + (pod or {}).get("paths", [])
        loss = max([float(x.get("lossRatio", 0)) for x in paths] or [0])
        own_pending = sorted(x for x in pending if ("-%s-" % name) in x)
        if not host or not pod or not host.get("fresh") or not pod.get("fresh"):
            diagnosis, recommendation, action = "evidence-incomplete", "collect missing host/pod evidence", "measure"
        elif host_dns and not pod_dns:
            diagnosis, recommendation, action = "host-pod-dns-divergence", "repair host resolver path", "repair-host-resolver"
        elif loss > 0:
            diagnosis, recommendation, action = "dataplane-degradation", "compare an independent shadow path", "shadow-probe"
        else:
            diagnosis, recommendation, action = "measured-healthy", "retain current path", "hold"
        results.append({"node": name, "diagnosis": diagnosis, "recommendation": recommendation,
                        "action": action, "confidence": "low" if own_pending else "high",
                        "validation": {"state": "pending" if own_pending else "measured",
                                       "pendingTaskIds": own_pending}})
    return results


def osi_snapshot(node, status, measurements):
    """Evaluate independent formal dimensions without proxy substitution."""
    current = [item for key, item in measurements.items() if key[0] == node["name"] and item.get("fresh")]
    paths = [path for item in current for path in item.get("paths", [])]
    optimality = None
    comparable = [path for path in paths if path.get("measurementDefinitionId") == "path-quality-v1" and
                  path.get("feasible", True) and path.get("lossRatio") is not None and path.get("p95Ms") is not None]
    current_paths = [path for path in comparable if path.get("pathRole") == "current"]
    alternatives = [path for path in comparable if path.get("pathRole") == "alternative"]
    comparisons = []
    if current_paths and alternatives:
        quality = lambda path: .7 * (1 - max(0, min(1, float(path["lossRatio"])))) + \
                               .3 * math.exp(-float(path["p95Ms"]) / 200)
        for current_path in current_paths:
            scoped = [path for path in alternatives if path.get("targetNode") == current_path.get("targetNode") and
                      path.get("targetPlane") == current_path.get("targetPlane")]
            if scoped:
                current_quality = quality(current_path)
                best_feasible = max([current_quality] + [quality(path) for path in scoped])
                comparisons.append(1 if best_feasible == 0 else max(0, min(1, current_quality / best_feasible)))
        if comparisons:
            optimality = sum(comparisons) / len(comparisons)
    histories = [item.get("history", {}) for item in current]
    dynamics = status.get("routeDynamics", {})
    stability = None
    if len(histories) == 2 and all(int(item.get("windowSamples") or 0) >= 3 and item.get("windowSeconds") is not None and
                                  item.get("lossBurstRatio") is not None for item in histories) and int(dynamics.get("samples") or 0) >= 3:
        loss_sigma = max(float(item.get("lossStdDev") or 0) for item in histories)
        latency_sigma = max(float(item.get("p95StdDevMs") or 0) for item in histories)
        burst = max(float(item["lossBurstRatio"]) for item in histories)
        churn = (float(dynamics.get("bgpChanges") or 0) + float(dynamics.get("routeChanges") or 0)) / max(1, float(dynamics["samples"]))
        stability = max(0, min(1, 1 - (.35 * min(1, loss_sigma / .25) + .25 * min(1, latency_sigma / 200) +
                                        .2 * min(1, burst) + .2 * min(1, churn))))
    candidates = {entry.get("peer") for items in status.get("pathRankings", {}).values() for entry in items or [] if entry.get("peer")}
    peers = [peer for peer in status.get("peerRoutes", []) if peer.get("name") in candidates]
    dimensions = ("gateway", "isp", "asn", "tunnel", "physicalPath")
    independence = None
    if peers and all(all(peer.get(key) not in (None, "") for key in dimensions) for peer in peers):
        independence = sum(min(1, len({str(peer[key]) for peer in peers}) / len(peers)) for key in dimensions) / len(dimensions)
    observed = max([status.get("observedAt", "")] + [item.get("observedAt", "") for item in current])
    return {"modelVersion": "networking.re8ch.com/measurement-model-v1alpha1", "observedAt": observed,
            "o": None if optimality is None else round(optimality, 3),
            "s": None if stability is None else round(stability, 3),
            "i": None if independence is None else round(independence, 3),
            "confidenceO": round(len(comparisons) / len(current_paths), 3) if current_paths else 0.0,
            "confidenceS": round(min(1, len(histories) / 2) * min(1, float(dynamics.get("samples") or 0) / 10), 3),
            "confidenceI": round(min(1, len(peers) / 3), 3) if independence is not None else 0.0}


def append_osi_history(existing, nodes, statuses, measurements, limit=96):
    history = dict(existing or {})
    for node in nodes:
        snapshot = osi_snapshot(node, statuses.get(node["name"], {}), measurements)
        values = list(history.get(node["name"], []))
        if snapshot["observedAt"] and not any(item.get("observedAt") == snapshot["observedAt"] for item in values):
            values.append(snapshot)
            values.sort(key=lambda item: item["observedAt"])
        history[node["name"]] = values[-limit:]
    return {name: values for name, values in history.items() if name in {node["name"] for node in nodes}}


def service_traffic_index(configmaps, freshness, now=None):
    """Index fresh, collector-executed Service traffic windows without inventing zeroes."""
    now = time.time() if now is None else now
    indexed = {}
    for item in configmaps:
        data = item.get("data", {})
        raw = data.get("measurement.json") or data.get("service-traffic.json") or "{}"
        try:
            payload = json.loads(raw)
            observed = payload["observedAt"]
            window = max(1.0, float(payload["windowSeconds"]))
            validity = max(float(freshness), window * 2)
            if now - parse_time(observed) > validity:
                continue
            collector = payload.get("collector", "registered")
            for sample in payload.get("samples", []):
                namespace, service, node = sample["namespace"], sample["service"], sample["node"]
                values = {key: max(0.0, float(sample.get(key) or 0))
                          for key in ("receivedBytes", "receivedPackets", "requests")}
                key = (namespace, service)
                indexed.setdefault(key, []).append({**values, "node": node, "observedAt": observed,
                    "windowSeconds": window, "collector": collector,
                    "measurementDefinitionId": "service-traffic-v1"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return indexed


def service_osi_snapshot(subject, traffic, node_history, node_index):
    """Aggregate Node O/S by measured inflow and derive I from failure-domain spread."""
    namespace, service = subject
    latest = {node: values[-1] for node, values in node_history.items() if values}
    traffic = [entry for entry in traffic if entry.get("node") in latest]
    observed = max([entry.get("observedAt", "") for entry in traffic] or [""])
    window = max([float(entry.get("windowSeconds") or 0) for entry in traffic] or [0])
    totals = {field: sum(float(entry.get(field) or 0) for entry in traffic)
              for field in ("receivedBytes", "receivedPackets", "requests")}
    weight_field = next((field for field in ("receivedBytes", "receivedPackets", "requests")
                         if totals[field] > 0), None)
    total_weight = totals.get(weight_field, 0) if weight_field else 0

    def weighted(axis):
        known = [(entry, latest[entry["node"]].get(axis)) for entry in traffic]
        known = [(entry, value) for entry, value in known if value is not None]
        denominator = sum(float(entry.get(weight_field) or 0) for entry, _ in known) if weight_field else 0
        numerator = sum(float(entry.get(weight_field) or 0) * float(value) for entry, value in known) if weight_field else 0
        return (numerator / denominator if denominator else None, numerator, denominator)

    optimality, o_num, o_den = weighted("o")
    stability, s_num, s_den = weighted("s")
    domain_weights, attributed = {}, 0.0
    for entry in traffic:
        domain = node_index.get(entry["node"], {}).get("failureDomain")
        weight = float(entry.get(weight_field) or 0) if weight_field else 0
        if domain and weight:
            domain_weights[domain] = domain_weights.get(domain, 0.0) + weight
            attributed += weight
    independence = None
    if attributed:
        shares = [value / attributed for value in domain_weights.values()]
        independence = 0.0 if len(shares) == 1 else (1 - sum(value * value for value in shares)) / (1 - 1 / len(shares))
    rates = {"bytesPerSecond": totals["receivedBytes"] / window if window else None,
             "packetsPerSecond": totals["receivedPackets"] / window if window else None,
             "requestsPerSecond": totals["requests"] / window if window else None}
    return {"modelVersion": "networking.re8ch.com/measurement-model-v1alpha2", "observedAt": observed,
            "o": None if optimality is None else round(optimality, 6),
            "s": None if stability is None else round(stability, 6),
            "i": None if independence is None else round(max(0, min(1, independence)), 6),
            "confidenceO": round(o_den / total_weight, 6) if total_weight else 0.0,
            "confidenceS": round(s_den / total_weight, 6) if total_weight else 0.0,
            "confidenceI": round(attributed / total_weight, 6) if total_weight else 0.0,
            "measurements": {"definitionId": "service-traffic-v1", "windowSeconds": window,
                "sampleCount": len(traffic), "weightUnit": weight_field, **totals, **rates,
                "collectors": sorted({entry["collector"] for entry in traffic}),
                "nodeValues": [{key: entry.get(key) for key in ("node", "receivedBytes", "receivedPackets", "requests")}
                               for entry in sorted(traffic, key=lambda value: value["node"])]},
            "calculation": {
                "optimality": {"method": "traffic-weighted-node-optimality", "numerator": round(o_num, 6),
                               "denominator": round(o_den, 6), "unit": weight_field},
                "stability": {"method": "traffic-weighted-node-stability", "numerator": round(s_num, 6),
                              "denominator": round(s_den, 6), "unit": weight_field},
                "independence": {"method": "normalized-inverse-HHI-by-failure-domain",
                                 "attributedWeight": round(attributed, 6), "totalWeight": round(total_weight, 6),
                                 "unit": weight_field, "domainWeights": domain_weights}}}


def assessment_document(node, snapshots, inference, validity_seconds, now=None):
    """Build the stable consumer API exclusively from formal component state."""
    now = time.time() if now is None else now
    formal = [item for item in snapshots if item.get("modelVersion") ==
              "networking.re8ch.com/measurement-model-v1alpha1"]
    latest = formal[-1] if formal else {}
    observed = latest.get("observedAt", "")
    observed_epoch = parse_time(observed) if observed else None
    valid_until_epoch = observed_epoch + validity_seconds if observed_epoch is not None else None
    dimensions = {"optimality": latest.get("o"), "stability": latest.get("s"),
                  "independence": latest.get("i")}
    confidence = {"optimality": float(latest.get("confidenceO") or 0),
                  "stability": float(latest.get("confidenceS") or 0),
                  "independence": float(latest.get("confidenceI") or 0)}
    known = sum(value is not None for value in dimensions.values())
    diagnosis = (inference or {}).get("diagnosis", "evidence-incomplete")
    if observed_epoch is not None and now > valid_until_epoch:
        state, reason = "Stale", "EvidenceExpired"
    elif "contradict" in diagnosis.lower():
        state, reason = "Contradictory", "ContradictoryEvidence"
    elif known == 3:
        state, reason = "Ready", "AllDimensionsAvailable"
    elif known:
        state, reason = "Partial", "SomeDimensionsUnavailable"
    else:
        state, reason = "Unknown", "RequiredEvidenceUnavailable"
    valid_until = (datetime.datetime.fromtimestamp(valid_until_epoch, datetime.timezone.utc).isoformat()
                   .replace("+00:00", "Z")) if valid_until_epoch is not None else None
    ready = state in ("Ready", "Partial")
    status = {"state": state, "dimensions": dimensions, "confidence": confidence,
              "evidenceRefs": sorted(MEASUREMENT_DEFINITIONS), "diagnosis": diagnosis,
              "recommendation": (inference or {}).get("recommendation", "collect missing evidence"),
              "validation": (inference or {}).get("validation", {"state": "pending"}),
              "conditions": [condition("EvidenceReady", ready, reason,
                                         "%s of 3 O/S/I dimensions available" % known)]}
    if observed:
        status["observedAt"] = observed
    if valid_until:
        status["validUntil"] = valid_until
    name = "node-" + node["name"].lower().replace("_", "-").replace(".", "-")
    return {"apiVersion": "networking.re8ch.com/v1alpha1", "kind": "NetworkPathAssessment",
            "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric",
                          "networking.re8ch.com/subject-kind": "Node"}},
            "spec": {"subjectRef": {"apiVersion": "v1", "kind": "Node", "name": node["name"]},
                     "scope": {"plane": "host-and-pod", "direction": "bidirectional", "protocol": "mixed"}},
            "status": status}


def service_assessment_document(subject, snapshot, validity_seconds, now=None):
    """Build a Service-scoped NPA with numeric traffic and calculation evidence."""
    now = time.time() if now is None else now
    namespace, service = subject
    observed = snapshot.get("observedAt", "")
    observed_epoch = parse_time(observed) if observed else None
    valid_until_epoch = observed_epoch + validity_seconds if observed_epoch is not None else None
    dimensions = {"optimality": snapshot.get("o"), "stability": snapshot.get("s"),
                  "independence": snapshot.get("i")}
    confidence = {"optimality": float(snapshot.get("confidenceO") or 0),
                  "stability": float(snapshot.get("confidenceS") or 0),
                  "independence": float(snapshot.get("confidenceI") or 0)}
    known = sum(value is not None for value in dimensions.values())
    if observed_epoch is not None and now > valid_until_epoch:
        state, reason = "Stale", "EvidenceExpired"
    elif known == 3:
        state, reason = "Ready", "AllDimensionsAvailable"
    elif known:
        state, reason = "Partial", "SomeDimensionsUnavailable"
    else:
        state, reason = "Unknown", "RequiredEvidenceUnavailable"
    safe = lambda value: "".join(char if char.isalnum() or char == "-" else "-" for char in value.lower()).strip("-")
    prefix = "service-%s-%s" % (safe(namespace), safe(service))
    digest = hashlib.sha256((namespace + "/" + service).encode()).hexdigest()[:8]
    name = prefix[:54].rstrip("-") + "-" + digest
    status = {"state": state, "dimensions": dimensions, "confidence": confidence,
              "measurements": snapshot.get("measurements", {}), "calculation": snapshot.get("calculation", {}),
              "evidenceRefs": ["service-traffic-v1", "path-quality-v1", "temporal-stability-v1",
                               "failure-domain-graph-v1"],
              "diagnosis": "service-traffic-assessed" if known else "service-traffic-evidence-incomplete",
              "recommendation": "retain measured distribution" if known == 3 else "collect missing node and traffic evidence",
              "validation": {"state": "measured", "subject": namespace + "/" + service},
              "conditions": [condition("EvidenceReady", state in ("Ready", "Partial"), reason,
                                         "%s of 3 traffic-weighted O/S/I dimensions available" % known)]}
    if observed:
        status["observedAt"] = observed
    if valid_until_epoch is not None:
        status["validUntil"] = datetime.datetime.fromtimestamp(valid_until_epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    return {"apiVersion": "networking.re8ch.com/v1alpha1", "kind": "NetworkPathAssessment",
            "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric",
                          "networking.re8ch.com/subject-kind": "Service",
                          "networking.re8ch.com/subject-namespace": namespace}},
            "spec": {"subjectRef": {"apiVersion": "v1", "kind": "Service", "namespace": namespace, "name": service},
                     "scope": {"plane": "pod", "direction": "forward", "protocol": "mixed"}},
            "status": status}


def publish_assessments(nodes, history, inferences, validity_seconds, service_snapshots=None):
    """Publish and prune the component-owned, consumer-neutral API objects."""
    desired = set()
    inference_index = {item["node"]: item for item in inferences}
    for node in nodes:
        document = assessment_document(node, history.get(node["name"], []),
                                       inference_index.get(node["name"]), validity_seconds)
        name, status = document["metadata"]["name"], document.pop("status")
        desired.add(name)
        path = "/apis/networking.re8ch.com/v1alpha1/networkpathassessments/%s" % name
        try:
            request("PATCH", path, document)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            request("POST", "/apis/networking.re8ch.com/v1alpha1/networkpathassessments", document)
        request("PATCH", path + "/status", {"status": status})
    for subject, snapshot in sorted((service_snapshots or {}).items()):
        document = service_assessment_document(subject, snapshot, validity_seconds)
        name, status = document["metadata"]["name"], document.pop("status")
        desired.add(name)
        path = "/apis/networking.re8ch.com/v1alpha1/networkpathassessments/%s" % name
        try:
            request("PATCH", path, document)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            request("POST", "/apis/networking.re8ch.com/v1alpha1/networkpathassessments", document)
        request("PATCH", path + "/status", {"status": status})
    existing = request("GET", "/apis/networking.re8ch.com/v1alpha1/networkpathassessments?labelSelector="
                       "app.kubernetes.io%2Fmanaged-by%3Dre8ch-advanced-fabric").get("items", [])
    for item in existing:
        name = item.get("metadata", {}).get("name")
        if name and name not in desired:
            request("DELETE", "/apis/networking.re8ch.com/v1alpha1/networkpathassessments/%s" % name,
                    {"propagationPolicy": "Background"})


LATENT_REQUIREMENTS = {
    "Q": ("a_reach", "l_path", "t_rtt"), "K": ("t_state", "t_conv", "t_recover", "delta_ribfib"),
    "H": ("p_route", "x_nh", "t_persist", "f_switch", "a_osc"),
    "C": ("u_bgp", "w_bgp", "lambda_flap", "delta_ribfib", "n_path_change"),
    "R": ("w_ecmp", "m_route", "n_peer", "n_nh", "n_if", "n_alt"),
    "D": ("n_tun", "n_gw", "n_asn", "g_dep", "n_alt"),
}
TRIANGLE_DEFINITIONS = (
    ("redundancy-independence-churn", ("R", "D", "C"), ("R->D", "D->C", "C->R")),
    ("responsiveness-inertia-quality", ("K", "H", "Q"), ("K->H", "H->Q", "Q->K")),
    ("redundancy-responsiveness-churn", ("R", "K", "C"), ("R->K", "K->C", "C->R")),
)


def custom_upsert(resource, document, status=None, version="v1alpha1"):
    name = document["metadata"]["name"]
    path = "/apis/networking.re8ch.com/%s/%s/%s" % (version, resource, name)
    try:
        request("PATCH", path, document)
    except urllib.error.HTTPError as exc:
        if exc.code != 404: raise
        request("POST", "/apis/networking.re8ch.com/%s/%s" % (version, resource), document)
    if status is not None:
        request("PATCH", path + "/status", {"status": status})


def path_evidence_document(node, node_ready, status, measurements, validity_seconds, now=None):
    """Build PublicEdge eligibility evidence without O/S/I scores."""
    now = time.time() if now is None else now
    current = [item for key, item in measurements.items() if key[0] == node["name"] and item.get("fresh")]
    planes = sorted({item.get("sourcePlane") for item in current if item.get("sourcePlane")})
    paths = [path for item in current for path in item.get("paths", [])]
    selected = [path for path in paths if path.get("pathRole", "current") == "current"]
    alternatives = [path for path in paths if path.get("pathRole") == "alternative" and
                    path.get("feasible") is True and float(path.get("lossRatio", 1)) < 1]
    executed_paths = [path for path in selected if path.get("lossRatio") is not None]
    executed = bool(executed_paths)
    successful_planes = {item.get("sourcePlane") for item in current if any(
        path.get("pathRole", "current") == "current" and path.get("lossRatio") is not None and
        float(path.get("lossRatio", 1)) < 1 for path in item.get("paths", []))}
    reachable = set(successful_planes) == {"host", "pod"} if executed else None
    observed = max([status.get("observedAt", "")] + [item.get("observedAt", "") for item in current])
    observed_epoch = parse_time(observed) if observed else None
    valid_until_epoch = observed_epoch + validity_seconds if observed_epoch is not None else None
    missing = []
    if not node_ready: missing.append("node-ready")
    if set(planes) != {"host", "pod"}: missing.append("fresh-host-and-pod")
    if not executed: missing.append("executed-current-path")
    if reachable is not True: missing.append("reachable-current-path")
    stale = valid_until_epoch is not None and now > valid_until_epoch
    eligible = not missing and not stale
    state = "Ready" if eligible else "Stale" if stale else "Partial" if current else "Unknown"
    reason = "PathEvidenceReady" if eligible else "EvidenceExpired" if stale else "RequiredEvidenceUnavailable"
    name = "node-" + node["name"].lower().replace("_", "-").replace(".", "-")
    document = {"apiVersion": "networking.re8ch.com/v1alpha2", "kind": "NetworkPathAssessment",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric",
            "networking.re8ch.com/subject-kind": "Node"}},
        "spec": {"subjectRef": {"apiVersion": "v1", "kind": "Node", "name": node["name"]},
                 "scope": {"plane": "host-and-pod", "direction": "bidirectional", "protocol": "mixed"}}}
    result = {"state": state, "nodeReady": bool(node_ready),
        "collectorEpoch": str(status.get("routeDynamics", {}).get("startedAt") or "unknown"),
        "pathEvidence": {"reachable": reachable, "currentPathMeasured": executed,
                         "viableAlternatives": len(alternatives) if paths else None,
                         "freshPlanes": planes, "missingEvidence": missing},
        "evidenceRefs": ["path-quality-v1", "node-measurement-v1alpha1"],
        "conditions": [condition("EvidenceReady", eligible, reason,
                                  "fresh, executed and reachable path evidence" if eligible else ", ".join(missing) or reason)]}
    if observed: result["observedAt"] = observed
    if valid_until_epoch is not None:
        result["validUntil"] = datetime.datetime.fromtimestamp(valid_until_epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    return document, result


def structural_observations(nodes, measurement_maps, previous=None, history_limit=96,
                            max_payload_bytes=800000):
    output = {}
    previous_nodes = (previous or {}).get("nodes", {})
    for node in nodes:
        payload = measurement_maps.get(node["name"], {})
        records = {item.get("symbol"): item for item in payload.get("measurements", [])}
        latent = {}
        for name, required in LATENT_REQUIREMENTS.items():
            available = [symbol for symbol in required if symbol in records and records[symbol].get("state") == "observed"]
            components = {symbol: records[symbol].get("value") for symbol in available}
            latent[name] = {"state": "Observed" if len(available) == len(required) else "Partial" if available else "NotObserved",
                            "requiredSymbols": list(required), "availableSymbols": available,
                            "missingSymbols": [symbol for symbol in required if symbol not in available],
                            "value": components if len(available) == len(required) else None,
                            "reason": "observation-derived component vector; no scalar mapping"}
        point = {"observedAt": payload.get("observedAt"), "trackingReady": bool(payload.get("trackingReady")),
                 "values": payload.get("trackingValues", {})}
        history = list(previous_nodes.get(node["name"], {}).get("history", []))
        if point["observedAt"] and (not history or history[-1].get("observedAt") != point["observedAt"]):
            history.append(point)
        output[node["name"]] = {"observedAt": payload.get("observedAt"), "latent": latent,
                                "trackingReady": bool(payload.get("trackingReady")),
                                "trackingGate": payload.get("trackingGate", {}),
                                "latestEpisode": payload.get("latestEpisode"), "history": history[-history_limit:],
                                "measurementRef": "advanced-fabric-measurement-" + node["name"].lower().replace("_", "-").replace(".", "-")}
    result = {"schemaVersion": "networking.re8ch.com/structural-observation-v1alpha2",
              "compatibleSchemaVersions": ["networking.re8ch.com/structural-observation-v1alpha1"], "nodes": output}
    # ConfigMaps are limited to 1 MiB and the API request carries additional
    # metadata and JSON escaping. Retain every node's latest point, then evict
    # the oldest points from the longest histories until a safe payload budget
    # is reached. This keeps collection forward-convergent as nodes are added.
    while len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) > max_payload_bytes:
        candidates = [item for item in output.values() if len(item["history"]) > 1]
        if not candidates:
            break
        max(candidates, key=lambda item: len(item["history"]))["history"].pop(0)
    return result


def discovered_interventions(fabric, statuses, previous, now_text):
    generation = str(fabric.get("metadata", {}).get("generation", 0))
    fingerprints = {"advancedfabric/re8ch": hashlib.sha256(("generation:" + generation).encode()).hexdigest()}
    for node, status in statuses.items():
        fingerprints["node/" + node] = hashlib.sha256(json.dumps({"datapath": status.get("datapath"),
            "route": status.get("routeFingerprint"), "bgp": status.get("bgpFingerprint")},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    events = []
    for subject, fingerprint in fingerprints.items():
        if subject in previous and previous[subject] != fingerprint:
            digest = hashlib.sha256((subject + fingerprint + now_text).encode()).hexdigest()[:12]
            kind, name = subject.split("/", 1)
            events.append({"apiVersion": "networking.re8ch.com/v1alpha1", "kind": "NetworkIntervention",
                "metadata": {"name": "auto-" + digest, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric"}},
                "spec": {"subjectRef": {"kind": "AdvancedFabric" if kind == "advancedfabric" else "Node", "name": name},
                         "cause": "GitOpsGeneration" if kind == "advancedfabric" else "RIBFIB", "detectedAt": now_text,
                         "fingerprint": fingerprint, "changedVariables": [], "evidenceRefs": [subject],
                         "windows": {"before": "pending", "control": "pending", "after": "pending"}},
                "status": {"identificationState": "PendingWindows", "confounders": [],
                           "conditions": [condition("Identifiable", False, "PendingAlignedWindows",
                                                    "automatic discovery does not establish causality")]}})
    return fingerprints, events


def triangle_documents():
    for name, vertices, edges in TRIANGLE_DEFINITIONS:
        missing = ["vertex:" + value for value in vertices] + ["edge:" + value for value in edges]
        yield ({"apiVersion": "networking.re8ch.com/v1alpha1", "kind": "TradeoffTriangle",
                "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric"}},
                "spec": {"vertexLabel": "-".join(vertices), "vertices": list(vertices), "edges": list(edges),
                         "subjectSelector": {}, "hypothesis": "experimentally identified trade-off hypothesis"}},
               {"state": "Open", "observedVertices": [], "identifiedEdges": [], "missingEvidence": missing})


def upsert_configmap(name, labels, payload, data_key=None):
    data_key = data_key or ("plan.json" if name.endswith("plan") else "inference.json")
    obj = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": name, "namespace": "kube-system", "labels": labels},
           "data": {data_key: json.dumps(payload, separators=(",", ":"), sort_keys=True)}}
    path = "/api/v1/namespaces/kube-system/configmaps/%s" % name
    try:
        request("PATCH", path, obj)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        request("POST", "/api/v1/namespaces/kube-system/configmaps", obj)


def network_quality(configmaps, inventory_nodes, standard, now=None):
    """Aggregate directed measurements and return deterministic gate evidence."""
    now = time.time() if now is None else now
    node_names = set(inventory_nodes) if not isinstance(inventory_nodes, int) else None
    node_count = inventory_nodes if isinstance(inventory_nodes, int) else len(node_names)
    freshness = int(standard.get("freshnessSeconds", 120))
    paths, dns, doh, sources, stale = [], [], [], set(), []
    for item in configmaps:
        try:
            result = json.loads(item.get("data", {}).get("result.json", "{}"))
            source = (result["sourceNode"], result["sourcePlane"])
            if node_names is not None and source[0] not in node_names:
                continue
            age = now - parse_time(result["observedAt"])
            if age > freshness:
                stale.append("%s/%s" % source)
                continue
            sources.add(source)
            paths.extend(entry for entry in result.get("paths", []) if node_names is None or
                         entry.get("targetNode") in node_names)
            dns.extend(dict(entry, sourceNode=source[0], sourcePlane=source[1]) for entry in result.get("dns", []))
            doh.extend(dict(entry, sourceNode=source[0], sourcePlane=source[1]) for entry in result.get("doh", []))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    expected_paths = 4 * node_count * node_count
    coverage = len(paths) / expected_paths if expected_paths else 0.0
    maximum_loss = float(standard.get("maximumLossRatio", 0.0))
    maximum_latency = float(standard.get("maximumCrossRegionP95Ms", 400))
    failed_paths = [entry for entry in paths if float(entry.get("lossRatio", 1)) > maximum_loss or
                    entry.get("p95Ms") is None or float(entry["p95Ms"]) > maximum_latency]
    dns_standard = standard.get("dns", {})
    maximum_dns_failure = float(dns_standard.get("maximumFailureRatio", .001))
    maximum_dns_latency = float(dns_standard.get("maximumP95Ms", 50))
    failed_dns = [entry for entry in dns if float(entry.get("failureRatio", 1)) > maximum_dns_failure or
                  entry.get("p95Ms") is None or float(entry["p95Ms"]) > maximum_dns_latency]
    network_ready = coverage >= float(standard.get("minimumCoverageRatio", 1.0)) and not failed_paths
    expected_dns_per_source = 4 if dns_standard.get("shadowEnabled") else 2
    required_roles = {"stable", "shadow"} if dns_standard.get("shadowEnabled") else {"stable"}
    missing_dns = [{"sourceNode": node, "sourcePlane": plane, "serverRole": role, "protocol": protocol,
                    "error": "missing"} for node, plane in sources for role in required_roles
                   for protocol in (("udp", "tcp") if dns_standard.get("requireTcp", True) else ("udp",))
                   if not any(entry.get("sourceNode") == node and entry.get("sourcePlane") == plane and
                              entry.get("serverRole", "stable") == role and entry.get("protocol") == protocol
                              for entry in dns)]
    failed_dns.extend(missing_dns)
    dns_ready = (len(sources) == 2 * node_count and len(dns) >= expected_dns_per_source * 2 * node_count
                 and not failed_dns)
    doh_standard = standard.get("doh", {})
    doh_enabled = bool(doh_standard.get("enabled"))
    failed_doh = [entry for entry in doh if float(entry.get("failureRatio", 1)) >
                  float(doh_standard.get("maximumFailureRatio", .001)) or entry.get("p95Ms") is None or
                  float(entry["p95Ms"]) > float(doh_standard.get("maximumP95Ms", 100))]
    doh_ready = not doh_enabled or (len(sources) == 2 * node_count and len(doh) >= 2 * node_count and not failed_doh)
    return {"observedSources": len(sources), "expectedSources": 2 * node_count, "observedPaths": len(paths),
            "expectedPaths": expected_paths, "coverageRatio": round(coverage, 4), "staleSources": sorted(stale),
            "failedPaths": failed_paths[:100], "failedPathCount": len(failed_paths), "dnsMeasurements": len(dns),
            "failedDns": failed_dns[:100], "failedDnsCount": len(failed_dns), "networkReady": network_ready,
            "dnsReady": dns_ready, "dohMeasurements": len(doh), "failedDoh": failed_doh[:100],
            "failedDohCount": len(failed_doh), "dohReady": doh_ready}


def rank_paths(profile, node, edges, costs, ready):
    cost_index = {(item.get("source"), item.get("destination"), item.get("pathType")): item for item in costs}
    ranked = []
    for edge in edges:
        if edge.get("source") != node["name"]:
            continue
        peer = edge.get("target")
        if not ready.get(peer, False) or edge.get("bgpUp") != 1 or edge.get("bfdUp") != 1:
            continue
        loss, rtt = float(edge.get("lossRatio") or 0), float(edge.get("rttMs") or 10000)
        if loss > .05:
            continue
        cost = cost_index.get((node["name"], peer, edge.get("pathType")), {})
        pressure = quota_pressure(cost.get("usageGiB", 0), node.get("monthlyTrafficLimitGiB"))
        capacity = max(.001, float(node.get("uplinkMbps", 1)))
        if profile == "fastest": score = rtt + 4000 * loss - 2 * math.log2(capacity)
        elif profile == "greedy": score = pressure["penalty"] + .25 * rtt + 1000 * loss - math.log2(capacity)
        else: score = .6 * rtt + 2500 * loss + .5 * pressure["penalty"] - 1.5 * math.log2(capacity)
        ranked.append({"peer": peer, "pathType": edge.get("pathType"), "score": round(score, 4),
                       "quotaPressure": pressure, "priceStatus": cost.get("priceStatus", "unknown")})
    return sorted(ranked, key=lambda item: (item["score"], item["peer"]))


def make_api_transaction(node, api, operations, guarded):
    """Build the only host-mutation contract and bind it to a stable digest."""
    spec = {
        "version": 1,
        "node": node,
        "guarded": bool(guarded),
        "vip": api.get("vip", ""),
        "vipInterface": operations.get("vipInterface", "lo"),
        "localAsn": operations.get("localAsn"),
        "frrExportPrefixLists": operations.get("frrExportPrefixLists", []),
        "frrImportPrefixLists": operations.get("frrImportPrefixLists", []),
        "frrImportPrefixSequence": operations.get("frrImportPrefixSequence", 30),
        "frrNeighborPolicies": operations.get("frrNeighborPolicies", []),
        "frrPrefixEntries": operations.get("frrPrefixEntries", []),
        "frrNetworks": operations.get("frrNetworks", []),
        "frrPrefixSequence": operations.get("frrPrefixSequence", 300),
        "wireguardInterfaces": operations.get("wireguardInterfaces", []),
        "wireguardAllowedPrefixes": operations.get("wireguardAllowedPrefixes", [api.get("vip", "")]),
        "wireguardPeerPolicies": operations.get("wireguardPeerPolicies", []),
        "forwardRules": operations.get("forwardRules", []),
        "fallbackRoutes": operations.get("fallbackRoutes", []),
    }
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return {"algorithm": "sha256", "checksum": hashlib.sha256(canonical.encode()).hexdigest(), "spec": spec}


def control_plane_apply_safety(control_plane_api, node_index, ready, quality_gate_ready):
    """Gate API VIP mutations on the nodes participating in that transaction.

    An unrelated spine or non-origin host transaction may be unavailable without
    making a guarded control-plane VIP rollout unsafe. Offline consumers apply
    their transaction when they return; only VIP origins gate publication.
    """
    guarded = set(control_plane_api.get("guardedNodes", []))
    participants = sorted(guarded - {None, ""})
    blockers = []
    for name in participants:
        node = node_index.get(name)
        if node is None:
            blockers.append(name + ":inactive")
        elif not node.get("inventoryComplete", False):
            blockers.append(name + ":inventory-incomplete")
        elif not ready.get(name, False):
            blockers.append(name + ":not-ready")
    if not quality_gate_ready:
        blockers.append("network-quality")
    return not blockers, blockers


def reconcile():
    fabric = request("GET", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch")
    spec = fabric["spec"]
    node_objects = request("GET", "/api/v1/nodes").get("items", [])
    ready = {item["metadata"]["name"]: any(c["type"] == "Ready" and c["status"] == "True"
             for c in item.get("status", {}).get("conditions", [])) for item in node_objects}
    policies = request("GET", "/apis/networking.re8ch.com/v1alpha1/trafficpolicies").get("items", [])
    pods = request("GET", "/api/v1/pods").get("items", [])
    advisor_edges = advisor_items("/api/v1/topology/edges")
    advisor_costs = advisor_items("/api/v1/costs/paths")
    quality_standard = spec.get("networkQuality", {})
    quality_enabled = bool(quality_standard.get("enabled"))
    quality_enforced = quality_enabled and bool(quality_standard.get("enforcementEnabled", False))
    probe_configmaps = []
    if quality_enabled:
        probe_configmaps = request("GET", "/api/v1/namespaces/kube-system/configmaps?labelSelector="
                                   "app.kubernetes.io%2Fcomponent%3Dnetwork-quality").get("items", [])
    status_configmaps = request("GET", "/api/v1/namespaces/kube-system/configmaps?labelSelector="
                                "networking.re8ch.com%2Fnode-status%3Dtrue").get("items", [])
    measurement_configmaps = request("GET", "/api/v1/namespaces/kube-system/configmaps?labelSelector="
                                     "app.kubernetes.io%2Fcomponent%3Dnode-measurement").get("items", [])
    traffic_configmaps = request("GET", "/api/v1/configmaps?labelSelector="
                                 "app.kubernetes.io%2Fcomponent%3Dservice-traffic").get("items", [])
    active_nodes, retired_nodes = cluster_inventory(spec["nodes"], node_objects)
    node_index = {node["name"]: node for node in active_nodes}
    declared_index = {node["name"]: node for node in spec["nodes"]}
    control_plane_api = spec.get("controlPlaneApi", {"enabled": False})
    configured_eligible_api_nodes = set(control_plane_api.get("eligibleNodes", []))
    configured_guarded_api_nodes = set(control_plane_api.get("guardedNodes", []))
    eligible_api_nodes = configured_eligible_api_nodes & set(node_index)
    guarded_api_nodes = configured_guarded_api_nodes & eligible_api_nodes
    api_operations = {item["name"]: item for item in control_plane_api.get("nodeOperations", [])}
    unknown_api_nodes = sorted(configured_eligible_api_nodes - set(declared_index))
    if control_plane_api.get("enabled") and unknown_api_nodes:
        raise ValueError(f"controlPlaneApi references unknown nodes: {','.join(unknown_api_nodes)}")
    unknown_guarded_nodes = sorted(configured_guarded_api_nodes - configured_eligible_api_nodes)
    if unknown_guarded_nodes:
        raise ValueError(f"guardedNodes must be eligible: {','.join(unknown_guarded_nodes)}")
    unknown_operation_nodes = sorted(set(api_operations) - set(declared_index))
    if unknown_operation_nodes:
        raise ValueError(f"nodeOperations references unknown nodes: {','.join(unknown_operation_nodes)}")
    incomplete = [node["name"] for node in active_nodes if not node.get("inventoryComplete")]
    unavailable = [node["name"] for node in active_nodes if node["role"] == "spine" and
                   (not node.get("inventoryComplete") or not ready.get(node["name"], False))]
    quality = network_quality(probe_configmaps, list(node_index), quality_standard) if quality_enabled else {
        "networkReady": True, "dnsReady": True, "dohReady": True, "disabled": True}
    quality_gate_ready = (not quality_enforced or
                          (quality["networkReady"] and quality["dnsReady"] and quality["dohReady"]))
    api_apply_safe, api_apply_blockers = control_plane_apply_safety(
        control_plane_api, node_index, ready, quality_gate_ready)
    effective_apply = (bool(spec.get("applyEnabled")) and not bool(spec.get("emergencyDisable"))
                       and api_apply_safe)
    rankings = {name: {profile: rank_paths(profile, node, advisor_edges, advisor_costs, ready)
                for profile in ("fastest", "balanced", "greedy")} for name, node in node_index.items()}
    resolved = {node["name"]: {"fastest": [], "balanced": [], "greedy": []} for node in spec["nodes"]}
    policy_status = {}
    for policy in sorted(policies, key=policy_key):
        meta, pspec = policy["metadata"], policy["spec"]
        namespace, name = meta["namespace"], meta["name"]
        matched = []
        for pod in pods:
            if pod["metadata"].get("namespace") != namespace or pod.get("spec", {}).get("hostNetwork"):
                continue
            if not selector_matches(pspec.get("podSelector", {}), pod["metadata"].get("labels", {})):
                continue
            ip = pod.get("status", {}).get("podIP")
            node_name = pod.get("spec", {}).get("nodeName")
            if ip and node_name in resolved:
                resolved[node_name][pspec["profile"]].append(ip)
                matched.append({"pod": pod["metadata"]["name"], "node": node_name, "ip": ip})
        policy_status[(namespace, name)] = {"observedGeneration": meta.get("generation", 0),
            "resolvedPods": matched, "conditions": [condition("Resolved", True, "SelectorResolved", f"resolved {len(matched)} pods")],
            "profile": pspec["profile"],
            "selectedNextHops": {item["node"]: rankings.get(item["node"], {}).get(pspec["profile"], [])[:7] for item in matched},
            "lastEvaluationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        existing_desired = request("GET", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-desired")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        existing_desired = {}
    stale_desired = stale_desired_nodes(existing_desired.get("data", {}), set(node_index))
    desired = {"apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "advanced-fabric-desired", "namespace": "kube-system",
                     "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric"}},
        "data": dict({name + ".json": json.dumps({"node": name, "mode": spec["mode"],
                  "applyEnabled": effective_apply,
                  "controlPlaneApi": dict(control_plane_api, eligible=name in eligible_api_nodes),
                  "transaction": make_api_transaction(name, control_plane_api, api_operations.get(name, {}),
                                                       name in guarded_api_nodes),
                  "podProfiles": profiles, "pathRankings": rankings.get(name, {}),
                  "peers": [{key: peer.get(key) for key in ("name", "internalIP", "acceleratedIP", "podCIDR", "role", "class",
                                                                  "provider", "isp", "region", "failureDomain", "gateway", "tunnel",
                                                                  "physicalPath", "asn")}
                            for peer in active_nodes if peer.get("name") != name],
                  "weightedEcmp": bool(spec.get("weightedEcmp", {}).get("enabled"))}, sort_keys=True)
                 for name, profiles in resolved.items()},
                 **{name + ".json": None for name in sorted(set(retired_nodes + stale_desired))})}
    try:
        request("PATCH", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-desired", desired)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        request("POST", "/api/v1/namespaces/kube-system/configmaps", desired)
    plan = evidence_plan(spec["nodes"], node_objects, probe_configmaps, quality_standard,
                         fabric["metadata"].get("generation", 0))
    inferences = node_inferences(plan, probe_configmaps, quality_standard)
    upsert_configmap("advanced-fabric-evidence-plan",
                     {"app.kubernetes.io/name": "re8ch-advanced-fabric",
                      "app.kubernetes.io/component": "evidence-planner"}, plan)
    upsert_configmap("advanced-fabric-inference",
                     {"app.kubernetes.io/name": "re8ch-advanced-fabric",
                      "app.kubernetes.io/component": "inference-engine"},
                     {"generation": plan["generation"], "nodes": inferences})
    statuses = {}
    for item in status_configmaps:
        try:
            parsed = json.loads(item.get("data", {}).get("status.json", "{}"))
            if parsed.get("node") in node_index:
                statuses[parsed["node"]] = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    measurement_state = measurement_index(probe_configmaps, set(node_index),
                                          int(quality_standard.get("freshnessSeconds", 120)), time.time())
    parsed_measurements = {}
    for item in measurement_configmaps:
        try:
            payload = json.loads(item.get("data", {}).get("measurement.json", "{}"))
            if payload.get("node") in node_index: parsed_measurements[payload["node"]] = payload
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    try:
        structural_object = request("GET", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-structural-observations")
        previous_structural = json.loads(structural_object.get("data", {}).get("observations.json", "{}"))
    except (urllib.error.HTTPError, TypeError, ValueError, json.JSONDecodeError):
        previous_structural = {}
    upsert_configmap("advanced-fabric-structural-observations",
                     {"app.kubernetes.io/name": "re8ch-advanced-fabric",
                      "app.kubernetes.io/component": "structural-observation"},
                     structural_observations(active_nodes, parsed_measurements, previous_structural), "observations.json")
    upsert_configmap("advanced-fabric-measurement-model",
                     {"app.kubernetes.io/name": "re8ch-advanced-fabric",
                      "app.kubernetes.io/component": "measurement-model"},
                     {"schemaVersion": "networking.re8ch.com/measurement-model-v1alpha2",
                      "definitions": MEASUREMENT_DEFINITIONS}, "definitions.json")
    component_api = spec.get("componentAPI", {})
    if component_api.get("publishAssessments", True):
        validity = int(component_api.get("validitySeconds", quality_standard.get("freshnessSeconds", 120)))
        for node in active_nodes:
            document, assessment_status = path_evidence_document(
                node, ready.get(node["name"], False), statuses.get(node["name"], {}), measurement_state, validity)
            custom_upsert("networkpathassessments", document, assessment_status, "v1alpha2")
    for document, triangle_status in triangle_documents():
        custom_upsert("tradeofftriangles", document, triangle_status)
    try:
        discovery_object = request("GET", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-intervention-state")
        previous_fingerprints = json.loads(discovery_object.get("data", {}).get("fingerprints.json", "{}"))
    except (urllib.error.HTTPError, TypeError, ValueError, json.JSONDecodeError):
        previous_fingerprints = {}
    now_text = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fingerprints, discovered = discovered_interventions(fabric, statuses, previous_fingerprints, now_text)
    for event in discovered:
        event_status = event.pop("status")
        custom_upsert("networkinterventions", event, event_status)
    upsert_configmap("advanced-fabric-intervention-state",
                     {"app.kubernetes.io/name": "re8ch-advanced-fabric",
                      "app.kubernetes.io/component": "intervention-discovery"},
                     fingerprints, "fingerprints.json")
    for (namespace, name), status in policy_status.items():
        request("PATCH", f"/apis/networking.re8ch.com/v1alpha1/namespaces/{namespace}/trafficpolicies/{name}/status", {"status": status})
    api_ready_nodes = sorted(name for name in eligible_api_nodes if ready.get(name, False))
    status = {"observedGeneration": fabric["metadata"].get("generation", 0),
              "mode": spec["mode"], "applyEnabled": effective_apply,
              "networkQualityEnforced": quality_enforced,
              "activeNodes": sorted(node_index), "retiredInventoryNodes": retired_nodes,
              "evidencePlanner": {"generation": plan["generation"], "tasks": len(plan["tasks"]),
                                  "pendingTasks": len(plan["pendingTaskIds"])},
              "serviceTraffic": {"observedServices": len(service_snapshots),
                                 "measurementWindows": sum(len(items) for items in traffic_state.values())},
              "inventoryIncomplete": incomplete, "ineligibleSpines": unavailable,
              "controlPlaneApi": {"enabled": bool(control_plane_api.get("enabled")),
                                  "vip": control_plane_api.get("vip", ""),
                                  "eligibleNodes": sorted(eligible_api_nodes),
                                  "kubernetesReadyNodes": api_ready_nodes},
              "networkQuality": quality,
              "conditions": [condition("InventoryReady", not incomplete, "InventoryEvaluated", ",".join(incomplete) or "complete"),
                             condition("NetworkConformanceReady", quality["networkReady"], "DirectedMatrixEvaluated",
                                       "%s/%s directed paths; %s failed" % (quality.get("observedPaths", 0), quality.get("expectedPaths", 0), quality.get("failedPathCount", 0))),
                             condition("DNSQualityReady", quality["dnsReady"], "DNSMeasurementsEvaluated",
                                       "%s measurements; %s failed" % (quality.get("dnsMeasurements", 0), quality.get("failedDnsCount", 0))),
                             condition("DoHQualityReady", quality["dohReady"], "DoHMeasurementsEvaluated",
                                       "%s measurements; %s failed" % (quality.get("dohMeasurements", 0), quality.get("failedDohCount", 0))),
                             condition("ApplySafe", api_apply_safe and not spec.get("emergencyDisable"),
                                       "SafetyGatesEvaluated",
                                       ",".join(api_apply_blockers) or
                                       ("measurement-only; quality standard is not enforced" if quality_enabled and not quality_enforced else
                                        "control-plane transaction participants passed"))],
              "lastEvaluationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    request("PATCH", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch/status", {"status": status})


while True:
    try:
        reconcile()
    except Exception as exc:
        print(json.dumps({"event": "advanced-fabric-reconcile-error", "error": str(exc),
                          "url": getattr(exc, "url", None), "code": getattr(exc, "code", None)}), flush=True)
    time.sleep(30)
