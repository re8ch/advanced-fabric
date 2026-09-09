#!/usr/bin/env python3
"""Publish a complete, evidence-honest per-node measurement envelope."""

import datetime
import json
import os
import ssl
import time
import urllib.error
import urllib.request

NODE = os.environ["NODE_NAME"]
NAMESPACE = os.environ.get("POD_NAMESPACE", "kube-system")
INTERVAL = int(os.environ.get("MEASUREMENT_INTERVAL_SECONDS", "15"))
METRICS_DIR = os.environ.get("TEXTFILE_DIR", "")
BASE = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"])
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CONTEXT = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
SYMBOLS = ["x_nh", "p_route", "w_ecmp", "m_route", "n_path_change", "d_mode", "a_reach", "l_path",
           "t_rtt", "b_rx", "b_est", "n_peer", "n_adv", "n_recv", "u_bgp", "w_bgp", "t_conv",
           "lambda_flap", "delta_ribfib", "n_nh", "n_if", "n_tun", "n_gw", "n_asn", "g_dep",
           "n_alt", "t_state", "t_persist", "f_switch", "t_recover", "a_osc"]
EXPERIMENT_ONLY = {"t_conv", "t_recover", "t_persist"}


def api(method, path, body=None, content_type="application/json"):
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=payload, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": content_type})
    with urllib.request.urlopen(request, context=CONTEXT, timeout=15) as response:
        return json.loads(response.read() or b"{}")


def safe_name(value):
    return value.lower().replace("_", "-").replace(".", "-")


def flatten_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from flatten_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_objects(child)


def read_probe(plane):
    name = "advanced-fabric-probe-%s-%s" % (safe_name(NODE), plane)
    try:
        obj = api("GET", "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, name))
        return json.loads(obj.get("data", {}).get("result.json", "{}"))
    except (urllib.error.HTTPError, ValueError, json.JSONDecodeError):
        return {}


def observed(value, source, observed_at, scope=None, note=None):
    result = {"state": "observed", "value": value, "source": source, "observedAt": observed_at}
    if scope:
        result["scope"] = scope
    if note:
        result["note"] = note
    return result


def partial(value, source, observed_at, reason):
    return {"state": "partial", "value": value, "source": source, "observedAt": observed_at,
            "missingEvidence": reason}


def unavailable(reason):
    return {"state": "not-observed", "value": None, "missingEvidence": reason}


def build_snapshot(status, probes, now=None):
    now = time.time() if now is None else now
    now_text = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    observed_at = max([status.get("observedAt", "")] + [p.get("observedAt", "") for p in probes if p])
    routes, rib = status.get("routes", []), status.get("bgpRib", [])
    links, peers = status.get("links", []), status.get("peerRoutes", [])
    dynamics = status.get("routeDynamics", {})
    paths = [path for probe in probes for path in probe.get("paths", [])]
    histories = [probe.get("history", {}) for probe in probes if probe.get("history")]
    neighbor_objects = list(flatten_objects(status.get("frr", {}).get("neighbors", {})))
    established = [item for item in flatten_objects(status.get("frr", {}).get("bgp", {}))
                   if str(item.get("state", item.get("peerState", ""))).lower() == "established"]
    candidate_next_hops = sorted({hop for route in rib for path in route.get("paths", [])
                                  for hop in path.get("nextHops", []) if hop and hop != "unknown"})
    route_interfaces = sorted({route.get("dev") for route in routes if route.get("dev")})
    as_paths = sorted({path.get("asPath") for route in rib for path in route.get("paths", []) if path.get("asPath")})
    updates = [{key: value for key, value in item.get("messageStats", {}).items() if "update" in key.lower()}
               for item in neighbor_objects if item.get("messageStats")]
    prefix_counts = [{key: value for key, value in item.items() if "prefix" in key.lower()}
                     for item in neighbor_objects if any("prefix" in key.lower() for key in item)]
    alternatives = [path for path in paths if path.get("pathRole") == "alternative" and
                    path.get("feasible") is True and int(path.get("successes") or 0) > 0]
    attempts = sum(int(path.get("attempts") or 0) for path in paths)
    successes = sum(int(path.get("successes") or 0) for path in paths)
    duration = max(1.0, now - datetime.datetime.fromisoformat(
        str(dynamics.get("startedAt", now_text)).replace("Z", "+00:00")).timestamp())
    values = {
        "x_nh": observed([{"prefix": route.get("prefix"), "nextHops": path.get("nextHops", [])}
                          for route in rib for path in route.get("paths", []) if path.get("best")], "FRR BGP RIB", observed_at),
        "p_route": observed([{"prefix": route.get("prefix"), "paths": route.get("paths", [])} for route in rib], "FRR BGP RIB", observed_at),
        "w_ecmp": observed([{"prefix": route.get("dst"), "width": len(route.get("nexthops", []))}
                            for route in status.get("ecmpRoutes", [])], "Linux main FIB", observed_at),
        "m_route": observed([{"prefix": route.get("prefix"), "paths": len(route.get("paths", []))} for route in rib], "FRR BGP RIB", observed_at),
        "n_path_change": observed(int(dynamics.get("routeChanges") or 0), "Linux route snapshot transitions", observed_at,
                                  note="lower bound; polling can collapse transitions"),
        "d_mode": observed(status.get("datapath", {}), "Cilium and Linux link state", observed_at),
        "a_reach": observed({"attempts": attempts, "successes": successes}, "host/pod conformance probes", observed_at) if attempts else unavailable("no completed path trials"),
        "l_path": observed([{"scope": [p.get("sourcePlane"), p.get("targetNode"), p.get("targetPlane")], "lossRatio": p.get("lossRatio"), "attempts": p.get("attempts")} for p in paths], "host/pod conformance probes", observed_at) if paths else unavailable("no completed path trials"),
        "t_rtt": observed([{"scope": [p.get("sourcePlane"), p.get("targetNode"), p.get("targetPlane")], "p50Ms": p.get("p50Ms"), "p95Ms": p.get("p95Ms")} for p in paths], "TCP conformance probes", observed_at) if paths else unavailable("no successful delay stream"),
        "b_rx": unavailable("requires a registered Hubble/Gateway Service-flow adapter"),
        "b_est": observed({"established": len(established)}, "FRR neighbor summary", observed_at),
        "n_peer": observed({"configured": len(peers), "established": len(established)}, "declared inventory and FRR", observed_at),
        "n_adv": partial(prefix_counts, "FRR neighbor JSON", observed_at, "per-peer advertised/accepted direction is implementation-dependent"),
        "n_recv": partial(prefix_counts, "FRR neighbor JSON", observed_at, "pre-policy received routes may be unavailable"),
        "u_bgp": partial(updates, "FRR message statistics", observed_at, "counter restart epoch and per-prefix event stream unavailable"),
        "w_bgp": unavailable("BMP or structured withdrawal event stream is not configured"),
        "lambda_flap": partial(float(dynamics.get("bgpChanges") or 0) / duration, "FRR summary snapshot transitions", observed_at, "polling provides a lower-bound rate"),
        "delta_ribfib": partial({"bgpChanges": dynamics.get("bgpChanges", 0), "routeChanges": dynamics.get("routeChanges", 0)}, "FRR and Linux snapshot hashes", observed_at, "set-level events are not yet retained"),
        "n_nh": partial(len(candidate_next_hops), "FRR candidate next-hops", observed_at, "candidates are not isolated per-next-hop viability tests"),
        "n_if": partial(route_interfaces, "Linux main FIB", observed_at, "logical interfaces can share physical dependencies"),
        "n_tun": partial(status.get("datapath", {}).get("tunnelInterfaces", []), "Cilium and Linux links", observed_at, "tunnel underlay independence is unverified"),
        "n_gw": partial(sorted({p.get("gateway") for p in peers if p.get("gateway")}), "declared peer inventory", observed_at, "gateway identities require live verification"),
        "n_asn": partial(as_paths, "FRR AS paths", observed_at, "private AS and route-server dependencies can be hidden"),
        "g_dep": partial(peers, "declared dependency inventory", observed_at, "inventory edges are not all observed dependencies"),
        "n_alt": observed(len(alternatives), "same-window alternative path probes", observed_at) if paths else unavailable("no alternative-path trials"),
        "t_state": partial({"collectorStartedAt": dynamics.get("startedAt"), "observedAt": observed_at}, "collector clock and snapshots", observed_at, "individual transition timestamps are not retained"),
        "f_switch": partial(float(dynamics.get("routeChanges") or 0) / duration, "Linux route snapshot transitions", observed_at, "polling provides a lower-bound frequency"),
        "a_osc": observed([{"lossStdDev": h.get("lossStdDev"), "p95StdDevMs": h.get("p95StdDevMs"), "windowSeconds": h.get("windowSeconds")} for h in histories], "conformance probe histories", observed_at) if histories else unavailable("insufficient retained probe history"),
    }
    for symbol in EXPERIMENT_ONLY:
        values[symbol] = unavailable("requires a pre-registered disturbance episode")
    records = [{"symbol": symbol, **values.get(symbol, unavailable("collector has no mapping"))} for symbol in SYMBOLS]
    counts = {state: sum(item["state"] == state for item in records) for state in ("observed", "partial", "not-observed")}
    return {"schemaVersion": "networking.re8ch.com/node-measurement-v1alpha1", "catalogRef": "advanced-fabric-osi-identification",
            "node": NODE, "observedAt": observed_at or now_text, "generatedAt": now_text,
            "envelopeComplete": len(records) == len(SYMBOLS), "coverage": {"total": len(SYMBOLS), **counts},
            "measurements": records}


def publish(snapshot):
    name = "advanced-fabric-measurement-" + safe_name(NODE)
    obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": NAMESPACE,
           "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric", "app.kubernetes.io/component":
           "node-measurement", "networking.re8ch.com/source-node": NODE}},
           "data": {"measurement.json": json.dumps(snapshot, separators=(",", ":"))}}
    path = "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, name)
    try:
        api("PATCH", path, obj, "application/merge-patch+json")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        api("POST", "/api/v1/namespaces/%s/configmaps" % NAMESPACE, obj)


def prometheus_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def publish_metrics(snapshot):
    """Export only observed numeric facts; missing measurements have no value sample."""
    if not METRICS_DIR:
        return
    records = {item["symbol"]: item for item in snapshot["measurements"]}
    epoch = next((item.get("value", {}).get("collectorStartedAt") for item in snapshot["measurements"]
                  if item["symbol"] == "t_state" and isinstance(item.get("value"), dict)), None) or "unknown"
    base = 'node="%s",plane="node",collector_epoch="%s",definition="%s"' % (
        prometheus_escape(NODE), prometheus_escape(epoch), prometheus_escape(snapshot["schemaVersion"]))
    lines = ["# HELP advanced_fabric_measurement_state Evidence state (1 for the current state).",
             "# TYPE advanced_fabric_measurement_state gauge"]
    for symbol, item in records.items():
        lines.append('advanced_fabric_measurement_state{%s,symbol="%s",state="%s"} 1' %
                     (base, symbol, item["state"]))
    values = {}
    for symbol in ("n_path_change", "lambda_flap", "n_nh", "n_alt", "f_switch"):
        item = records[symbol]
        if item["state"] != "not-observed" and isinstance(item.get("value"), (int, float)):
            values[symbol] = item["value"]
    mappings = {
        "w_ecmp": lambda v: max([int(x.get("width") or 0) for x in v] or [0]),
        "m_route": lambda v: max([int(x.get("paths") or 0) for x in v] or [0]),
        "n_if": lambda v: len(v), "n_tun": lambda v: len(v), "n_gw": lambda v: len(v),
        "n_asn": lambda v: len(v),
    }
    for symbol, convert in mappings.items():
        item = records[symbol]
        if item["state"] != "not-observed" and isinstance(item.get("value"), list):
            values[symbol] = convert(item["value"])
    lines.extend(["# HELP advanced_fabric_observable Current raw network observable.",
                  "# TYPE advanced_fabric_observable gauge"])
    for symbol, value in values.items():
        lines.append('advanced_fabric_observable{%s,symbol="%s"} %s' % (base, symbol, value))
    requirements = {"Q": ("a_reach", "l_path", "t_rtt"), "K": ("t_state", "t_conv", "t_recover", "delta_ribfib"),
                    "H": ("p_route", "x_nh", "t_persist", "f_switch", "a_osc"),
                    "C": ("u_bgp", "w_bgp", "lambda_flap", "delta_ribfib", "n_path_change"),
                    "R": ("w_ecmp", "m_route", "n_peer", "n_nh", "n_if", "n_alt"),
                    "D": ("n_tun", "n_gw", "n_asn", "g_dep", "n_alt")}
    lines.extend(["# HELP advanced_fabric_structural_evidence_coverage Fraction of required observables currently observed or partial.",
                  "# TYPE advanced_fabric_structural_evidence_coverage gauge"])
    for latent, required in requirements.items():
        present = sum(records[symbol]["state"] != "not-observed" for symbol in required)
        lines.append('advanced_fabric_structural_evidence_coverage{%s,latent="%s"} %.6f' %
                     (base, latent, present / len(required)))
    temporary = os.path.join(METRICS_DIR, ".advanced_fabric_measurement.prom.tmp")
    target = os.path.join(METRICS_DIR, "advanced_fabric_measurement.prom")
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    os.replace(temporary, target)


while True:
    try:
        with open("/status/status.json", encoding="utf-8") as stream:
            current_status = json.load(stream)
        snapshot = build_snapshot(current_status, [read_probe("host"), read_probe("pod")])
        publish(snapshot)
        publish_metrics(snapshot)
    except Exception as error:
        print(json.dumps({"event": "node-measurement-publish-error", "node": NODE, "error": str(error)}), flush=True)
    time.sleep(INTERVAL)
