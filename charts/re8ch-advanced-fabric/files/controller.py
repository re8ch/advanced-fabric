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
    probe_configmaps = []
    if quality_enabled:
        probe_configmaps = request("GET", "/api/v1/namespaces/kube-system/configmaps?labelSelector="
                                   "app.kubernetes.io%2Fcomponent%3Dnetwork-quality").get("items", [])
    node_index = {node["name"]: node for node in spec["nodes"]}
    control_plane_api = spec.get("controlPlaneApi", {"enabled": False})
    eligible_api_nodes = set(control_plane_api.get("eligibleNodes", []))
    guarded_api_nodes = set(control_plane_api.get("guardedNodes", []))
    api_operations = {item["name"]: item for item in control_plane_api.get("nodeOperations", [])}
    unknown_api_nodes = sorted(eligible_api_nodes - set(node_index))
    if control_plane_api.get("enabled") and unknown_api_nodes:
        raise ValueError(f"controlPlaneApi references unknown nodes: {','.join(unknown_api_nodes)}")
    unknown_guarded_nodes = sorted(guarded_api_nodes - eligible_api_nodes)
    if unknown_guarded_nodes:
        raise ValueError(f"guardedNodes must be eligible: {','.join(unknown_guarded_nodes)}")
    incomplete = [node["name"] for node in spec["nodes"] if not node.get("inventoryComplete")]
    unavailable = [node["name"] for node in spec["nodes"] if node["role"] == "spine" and
                   (not node.get("inventoryComplete") or not ready.get(node["name"], False))]
    quality = network_quality(probe_configmaps, [node["name"] for node in spec["nodes"]], quality_standard) if quality_enabled else {
        "networkReady": True, "dnsReady": True, "dohReady": True, "disabled": True}
    api_apply_safe = (all(ready.get(name, False) and node_index[name].get("inventoryComplete", False)
                          for name in guarded_api_nodes) and not incomplete and not unavailable and
                      quality["networkReady"] and quality["dnsReady"] and quality["dohReady"])
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
    desired = {"apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "advanced-fabric-desired", "namespace": "kube-system",
                     "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric"}},
        "data": {name + ".json": json.dumps({"node": name, "mode": spec["mode"],
                  "applyEnabled": effective_apply,
                  "controlPlaneApi": dict(control_plane_api, eligible=name in eligible_api_nodes),
                  "transaction": make_api_transaction(name, control_plane_api, api_operations.get(name, {}),
                                                       name in guarded_api_nodes),
                  "podProfiles": profiles, "pathRankings": rankings.get(name, {}),
                  "peers": [{key: peer.get(key) for key in ("name", "internalIP", "acceleratedIP", "podCIDR", "role", "class")}
                            for peer in spec["nodes"] if peer.get("name") != name],
                  "weightedEcmp": bool(spec.get("weightedEcmp", {}).get("enabled"))}, sort_keys=True)
                 for name, profiles in resolved.items()}}
    try:
        request("PATCH", "/api/v1/namespaces/kube-system/configmaps/advanced-fabric-desired", desired)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        request("POST", "/api/v1/namespaces/kube-system/configmaps", desired)
    for (namespace, name), status in policy_status.items():
        request("PATCH", f"/apis/networking.re8ch.com/v1alpha1/namespaces/{namespace}/trafficpolicies/{name}/status", {"status": status})
    api_ready_nodes = sorted(name for name in eligible_api_nodes if ready.get(name, False))
    status = {"observedGeneration": fabric["metadata"].get("generation", 0),
              "mode": spec["mode"], "applyEnabled": effective_apply,
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
                             condition("ApplySafe", not incomplete and not unavailable and not spec.get("emergencyDisable") and
                                       quality["networkReady"] and quality["dnsReady"] and quality["dohReady"], "SafetyGatesEvaluated",
                                       ",".join(sorted(set(incomplete + unavailable))) or ("all gates passed" if quality["networkReady"] and quality["dnsReady"] and quality["dohReady"] else "network quality gate failed"))],
              "lastEvaluationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    request("PATCH", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch/status", {"status": status})


while True:
    try:
        reconcile()
    except Exception as exc:
        print(json.dumps({"event": "advanced-fabric-reconcile-error", "error": str(exc),
                          "url": getattr(exc, "url", None), "code": getattr(exc, "code", None)}), flush=True)
    time.sleep(30)
