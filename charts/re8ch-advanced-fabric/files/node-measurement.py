#!/usr/bin/env python3
"""Publish a complete, evidence-honest per-node measurement envelope."""

import datetime
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

NODE = os.environ["NODE_NAME"]
NAMESPACE = os.environ.get("POD_NAMESPACE", "kube-system")
INTERVAL = int(os.environ.get("MEASUREMENT_INTERVAL_SECONDS", "15"))
METRICS_DIR = os.environ.get("TEXTFILE_DIR", "")
STATE_FILE = os.environ.get("MEASUREMENT_STATE_FILE", "/status/measurement-state.json")
FRESHNESS_SECONDS = int(os.environ.get("MEASUREMENT_FRESHNESS_SECONDS", "120"))
HORIZON_SECONDS = int(os.environ.get("MEASUREMENT_HORIZON_SECONDS", str(90 * 86400)))
BASE = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"])
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CONTEXT = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
SYMBOLS = ["x_nh", "p_route", "w_ecmp", "m_route", "n_path_change", "d_mode", "a_reach", "l_path",
           "t_rtt", "b_rx", "b_est", "n_peer", "n_adv", "n_recv", "u_bgp", "w_bgp", "t_conv",
           "lambda_flap", "delta_ribfib", "n_nh", "n_if", "n_tun", "n_gw", "n_asn", "g_dep",
           "n_alt", "t_state", "t_persist", "f_switch", "t_recover", "a_osc"]
EPISODE_ONLY = {"t_conv", "t_recover", "t_persist"}


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


def read_service_traffic():
    """Read the registered Service-flow adapter contract without inventing host traffic."""
    try:
        response = api("GET", "/api/v1/namespaces/%s/configmaps?labelSelector=" % NAMESPACE +
                       "app.kubernetes.io%2Fcomponent%3Dservice-traffic")
    except urllib.error.HTTPError:
        return []
    windows = []
    for item in response.get("items", []):
        try:
            payload = json.loads(item.get("data", {}).get("measurement.json") or
                                 item.get("data", {}).get("service-traffic.json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for sample in payload.get("samples", []):
            if sample.get("node") == NODE:
                windows.append({**sample, "observedAt": payload.get("observedAt"),
                                "windowSeconds": payload.get("windowSeconds")})
    if windows:
        return windows
    queries = (
        ('sum(increase(hubble_flow_bytes_total{node="%s",direction="ingress"}[2m]))' % NODE, "Hubble flow bytes"),
        ('sum(increase(envoy_listener_downstream_cx_rx_bytes_total{node="%s"}[2m]))' % NODE, "Gateway Envoy listener receive bytes"),
        ('sum(increase(envoy_downstream_cx_rx_bytes_total{kubernetes_node="%s"}[2m]))' % NODE, "Gateway Envoy receive bytes"),
    )
    for query, source in queries:
        path = "/api/v1/namespaces/observability-system/services/http:vmselect-re8ch-metrics:8481/" \
               "proxy/select/0/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": query})
        try:
            result = api("GET", path).get("data", {}).get("result", [])
            if result and result[0].get("value") and result[0]["value"][1] not in ("NaN", "+Inf", "-Inf"):
                return [{"receivedBytes": float(result[0]["value"][1]), "receivedPackets": 0,
                         "requests": 0, "source": source,
                         "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                         "windowSeconds": 120}]
        except (urllib.error.HTTPError, TypeError, ValueError, KeyError):
            continue
    return []


def fingerprint(status):
    value = {"bgp": status.get("frr", {}).get("bgp", {}), "rib": status.get("bgpRib", []),
             "routes": status.get("routes", [])}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def path_fingerprint(probes):
    value = [{key: path.get(key) for key in ("sourcePlane", "targetNode", "targetPlane", "address",
             "successes", "attempts", "lossRatio")} for probe in probes for path in probe.get("paths", [])]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def probe_loss(probes):
    paths = [path for probe in probes for path in probe.get("paths", [])]
    attempts = sum(int(path.get("attempts") or 0) for path in paths)
    successes = sum(int(path.get("successes") or 0) for path in paths)
    return None if not attempts else round(1 - successes / attempts, 6)


def advance_episode(state, status, probes, now):
    """Track naturally occurring RIB/FIB changes; never creates a disturbance."""
    current = fingerprint(status)
    current_path = path_fingerprint(probes)
    loss = probe_loss(probes)
    previous = state.get("fingerprint")
    previous_path = state.get("pathFingerprint")
    previous_loss = state.get("lossRatio")
    active = state.get("activeEpisode")
    route_changed = previous is not None and previous != current
    path_changed = previous_path is not None and previous_path != current_path
    reachability_changed = previous_loss is not None and loss is not None and abs(previous_loss - loss) > 0.001
    changed = route_changed or path_changed or reachability_changed
    if not active and changed:
        cause = "NaturalRIBFIBChange" if route_changed else "NaturalReachabilityChange" if reachability_changed else "NaturalPathChange"
        active = {"startedEpoch": now, "startedAt": datetime.datetime.fromtimestamp(
            now, datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "baselineFingerprint": previous, "observedFingerprint": current,
            "baselinePathFingerprint": previous_path, "observedPathFingerprint": current_path,
            "baselineLossRatio": previous_loss, "stableSamples": 0, "cause": cause,
            "collectorEpoch": status.get("routeDynamics", {}).get("startedAt")}
    elif active:
        active["stableSamples"] = 0 if changed else int(active.get("stableSamples", 0)) + 1
        active["observedFingerprint"] = current
        active["observedPathFingerprint"] = current_path
        baseline = active.get("baselineLossRatio")
        recovered = loss is not None and (baseline is None or loss <= baseline + 0.001)
        if active["stableSamples"] >= 2 and recovered:
            completed = {**active, "endedEpoch": now, "endedAt": datetime.datetime.fromtimestamp(
                now, datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "state": "Complete", "convergenceSeconds": round(now - active["startedEpoch"], 3),
                "persistenceSeconds": round(now - active["startedEpoch"], 3),
                "recoverySeconds": round(now - active["startedEpoch"], 3),
                "recoveredLossRatio": loss}
            state["lastCompletedEpisode"] = completed
            active = None
    state.update({"fingerprint": current, "pathFingerprint": current_path, "lossRatio": loss, "activeEpisode": active,
                  "updatedEpoch": now})
    return state


def counter_totals(neighbors):
    updates = withdrawals = 0
    supported = False
    for item in flatten_objects(neighbors):
        stats = item.get("messageStats", {})
        prefixes = item.get("prefixStats", {})
        for key, value in stats.items():
            if isinstance(value, (int, float)) and "update" in key.lower():
                updates += value
                supported = True
        for key, value in prefixes.items():
            if isinstance(value, (int, float)) and "withdraw" in key.lower():
                withdrawals += value
                supported = True
    return {"updates": updates, "withdrawals": withdrawals, "supported": supported}


def directional_prefix_counts(neighbors, direction):
    needles = ("pfxsnt", "sent", "advertised") if direction == "advertised" else ("pfxrcd", "recv", "received", "accepted")
    values = []
    for item in flatten_objects(neighbors):
        for key, value in item.items():
            normalized = key.lower().replace("_", "")
            if isinstance(value, (int, float)) and any(needle in normalized for needle in needles):
                values.append({key: value})
    return values


def observed(value, source, observed_at, scope=None, note=None):
    result = {"state": "observed", "observationStatus": "measured", "value": value,
              "source": source, "observedAt": observed_at}
    if scope:
        result["scope"] = scope
    if note:
        result["note"] = note
    return result


def partial(value, source, observed_at, reason):
    return {"state": "partial", "observationStatus": "incomplete", "value": value,
            "source": source, "observedAt": observed_at, "missingEvidence": reason}


def unavailable(reason):
    return {"state": "not-observed", "observationStatus": "unavailable", "value": None,
            "missingEvidence": reason}


def right_censored(source, observed_at, window_started_at, reason):
    """Represent a valid event-conditioned observation without fabricating a duration."""
    return {"state": "observed", "observationStatus": "right-censored", "value": None,
            "source": source, "observedAt": observed_at, "note": reason,
            "censoring": {"kind": "right", "windowStartedAt": window_started_at,
                          "windowEndedAt": observed_at, "eventObserved": False}}


TRACKING_UNITS = {
    "x_nh": "next-hops", "p_route": "routes", "w_ecmp": "paths", "m_route": "paths",
    "n_path_change": "changes", "d_mode": "interfaces", "a_reach": "ratio", "l_path": "ratio",
    "t_rtt": "ms", "b_rx": "bytes", "b_est": "peers", "n_peer": "peers", "n_adv": "prefixes",
    "n_recv": "prefixes", "u_bgp": "updates", "w_bgp": "withdrawals", "t_conv": "seconds",
    "lambda_flap": "changes/second", "delta_ribfib": "changes", "n_nh": "next-hops",
    "n_if": "interfaces", "n_tun": "tunnels", "n_gw": "gateways", "n_asn": "AS paths",
    "g_dep": "dependencies", "n_alt": "paths", "t_state": "seconds", "t_persist": "seconds",
    "f_switch": "changes/second", "t_recover": "seconds", "a_osc": "ms",
}


def tracking_value(symbol, value, now=None):
    """Project an observed fact into one documented scalar without combining indicators."""
    now = time.time() if now is None else now
    if isinstance(value, (int, float)):
        return float(value)
    if symbol in ("x_nh", "p_route", "n_if", "n_tun", "n_gw", "n_asn", "g_dep"):
        return float(len(value or []))
    if symbol in ("w_ecmp", "m_route"):
        field = "width" if symbol == "w_ecmp" else "paths"
        return float(max([int(item.get(field) or 0) for item in value or []] or [0]))
    if symbol == "a_reach":
        attempts = int(value.get("attempts") or 0)
        return None if not attempts else float(value.get("successes") or 0) / attempts
    if symbol == "l_path":
        return float(max([float(item.get("lossRatio") or 0) for item in value or []] or [0]))
    if symbol == "t_rtt":
        return float(max([float(item.get("p95Ms") or 0) for item in value or []] or [0]))
    if symbol == "b_rx": return float(value.get("bytes") or 0)
    if symbol in ("b_est", "n_peer"): return float(value.get("established") or 0)
    if symbol in ("n_adv", "n_recv"):
        needle = "sent" if symbol == "n_adv" else "received"
        candidates = [number for item in value or [] for key, number in item.items()
                      if needle in key.lower() and isinstance(number, (int, float))]
        return float(sum(candidates)) if candidates else None
    if symbol == "u_bgp": return float(value.get("windowUpdates") or 0)
    if symbol == "w_bgp": return float(value.get("windowWithdrawals") or 0)
    if symbol == "delta_ribfib":
        return float(value.get("bgpChanges") or 0) + float(value.get("routeChanges") or 0)
    if symbol == "t_state":
        started = value.get("collectorStartedAt")
        return None if not started else max(0.0, now - datetime.datetime.fromisoformat(
            started.replace("Z", "+00:00")).timestamp())
    if symbol == "a_osc":
        return float(max([float(item.get("p95StdDevMs") or 0) for item in value or []] or [0]))
    return None


def build_snapshot(status, probes, now=None, state=None, service_traffic=None):
    now = time.time() if now is None else now
    now_text = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    observed_at = max([status.get("observedAt", "")] + [p.get("observedAt", "") for p in probes if p])
    routes, rib = status.get("routes", []), status.get("bgpRib", [])
    links, peers = status.get("links", []), status.get("peerRoutes", [])
    dynamics = status.get("routeDynamics", {})
    paths = [path for probe in probes for path in probe.get("paths", [])]
    histories = [probe.get("history", {}) for probe in probes if probe.get("history")]
    neighbors = status.get("frr", {}).get("neighbors", {})
    neighbor_objects = list(flatten_objects(neighbors))
    established = [item for item in flatten_objects(status.get("frr", {}).get("bgp", {}))
                   if str(item.get("state", item.get("peerState", ""))).lower() == "established"]
    candidate_next_hops = sorted({hop for route in rib for path in route.get("paths", [])
                                  for hop in path.get("nextHops", []) if hop and hop != "unknown"})
    route_interfaces = sorted({route.get("dev") for route in routes if route.get("dev")})
    as_paths = sorted({path.get("asPath") for route in rib for path in route.get("paths", []) if path.get("asPath")})
    updates = [{key: value for key, value in item.get("messageStats", {}).items() if "update" in key.lower()}
               for item in neighbor_objects if item.get("messageStats")]
    advertised_prefixes = directional_prefix_counts(neighbors, "advertised")
    received_prefixes = directional_prefix_counts(neighbors, "received")
    alternatives = [path for path in paths if path.get("pathRole") == "alternative" and
                    path.get("feasible") is True and int(path.get("successes") or 0) > 0]
    attempts = sum(int(path.get("attempts") or 0) for path in paths)
    successes = sum(int(path.get("successes") or 0) for path in paths)
    duration = max(1.0, now - datetime.datetime.fromisoformat(
        str(dynamics.get("startedAt", now_text)).replace("Z", "+00:00")).timestamp())
    state = advance_episode(dict(state or {}), status, probes, now)
    state.setdefault("observationStartedEpoch", now)
    observation_started_at = datetime.datetime.fromtimestamp(
        state["observationStartedEpoch"], datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    counters = counter_totals(neighbors)
    previous_counters = state.get("previousCounters")
    counter_epoch = dynamics.get("startedAt")
    counter_reset = previous_counters is None or state.get("counterEpoch") != counter_epoch or any(
        counters[key] < previous_counters.get(key, 0) for key in ("updates", "withdrawals"))
    deltas = {key: 0 if counter_reset else max(0, counters[key] - previous_counters.get(key, counters[key]))
              for key in ("updates", "withdrawals")}
    state["previousCounters"] = counters
    state["counterEpoch"] = counter_epoch
    episode = state.get("lastCompletedEpisode")
    traffic = list(service_traffic or [])
    traffic_fresh = [item for item in traffic if item.get("observedAt") and now - datetime.datetime.fromisoformat(
        item["observedAt"].replace("Z", "+00:00")).timestamp() <= FRESHNESS_SECONDS]
    inventory_complete = bool(peers) and all(item.get("provider") and item.get("failureDomain") and
                                             (item.get("asn") or as_paths) for item in peers)
    event_count = int(dynamics.get("bgpChanges") or 0) + int(dynamics.get("routeChanges") or 0)
    gateway_ids = sorted({value for route in rib for path in route.get("paths", [])
                          for value in path.get("nextHops", []) if value and value not in ("unknown", "0.0.0.0")})
    next_hop_probes = {item.get("address"): item for item in status.get("nextHopProbes", [])}
    verified_next_hops = [hop for hop in candidate_next_hops if next_hop_probes.get(hop, {}).get("reachable") is True and
                          next_hop_probes.get(hop, {}).get("routeDev")]
    next_hop_observation_complete = (
        (not candidate_next_hops and status.get("frr", {}).get("state") == "active" and "bgpRib" in status) or
        (bool(candidate_next_hops) and all(
            hop in next_hop_probes and isinstance(next_hop_probes[hop].get("reachable"), bool)
            for hop in candidate_next_hops)))
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
        "b_rx": observed({"bytes": sum(int(item.get("receivedBytes") or 0) for item in traffic_fresh),
                          "packets": sum(int(item.get("receivedPackets") or 0) for item in traffic_fresh),
                          "requests": sum(int(item.get("requests") or 0) for item in traffic_fresh),
                          "windows": len(traffic_fresh)}, "registered Service-flow adapter", observed_at)
                if traffic_fresh else unavailable("requires a fresh registered Hubble/Gateway Service-flow window"),
        "b_est": observed({"established": len(established)}, "FRR neighbor summary", observed_at),
        "n_peer": observed({"configured": len(peers), "established": len(established)}, "declared inventory and FRR", observed_at),
        "n_adv": observed(advertised_prefixes, "FRR advertised prefix counters", observed_at) if advertised_prefixes else unavailable("FRR advertised prefix counters unavailable"),
        "n_recv": observed(received_prefixes, "FRR received prefix counters", observed_at) if received_prefixes else unavailable("FRR received prefix counters unavailable"),
        "u_bgp": observed({"windowUpdates": deltas["updates"], "totals": updates, "counterReset": counter_reset}, "FRR message counter deltas", observed_at) if counters["supported"] and not counter_reset else unavailable("FRR update counters unavailable or reset in current window"),
        "w_bgp": observed({"windowWithdrawals": deltas["withdrawals"], "counterReset": counter_reset}, "FRR withdrawal counter deltas", observed_at) if counters["supported"] and not counter_reset else unavailable("FRR withdrawal counters unavailable or reset in current window"),
        "lambda_flap": observed(event_count / duration, "retained RIB/FIB fingerprint transitions", observed_at),
        "delta_ribfib": observed({"bgpChanges": dynamics.get("bgpChanges", 0), "routeChanges": dynamics.get("routeChanges", 0), "fingerprint": fingerprint(status)}, "retained FRR and Linux state-set fingerprints", observed_at),
        "n_nh": observed(len(verified_next_hops), "FRR candidates joined to bound route and active reachability probes",
                         observed_at, scope={"candidateCount": len(candidate_next_hops),
                         "probeResults": [next_hop_probes.get(hop) for hop in candidate_next_hops]},
                         note="unreachable candidates are valid measured outcomes, not missing evidence")
                if next_hop_observation_complete else unavailable("not all candidate next-hops have same-window reachability attempts"),
        "n_if": observed(route_interfaces, "Linux FIB and link inventory", observed_at),
        "n_tun": observed(status.get("datapath", {}).get("tunnelInterfaces", []), "effective Cilium mode and Linux links", observed_at),
        "n_gw": observed(gateway_ids, "FRR next-hop and live route inventory", observed_at) if gateway_ids else unavailable("no resolvable live gateway identities"),
        "n_asn": observed(as_paths, "FRR AS paths with declared inventory", observed_at) if as_paths else unavailable("no observed AS paths"),
        "g_dep": observed(peers, "verified declared dependency inventory", observed_at) if inventory_complete else unavailable("dependency inventory is incomplete"),
        "n_alt": observed(len(alternatives), "same-window alternative path probes", observed_at) if paths else unavailable("no alternative-path trials"),
        "t_state": observed({"collectorStartedAt": dynamics.get("startedAt"), "observedAt": observed_at,
                             "lastEpisodeAt": episode.get("startedAt") if episode else None}, "timestamped state snapshots", observed_at),
        "f_switch": observed(float(dynamics.get("routeChanges") or 0) / duration, "retained route fingerprint transitions", observed_at),
        "a_osc": observed([{"lossStdDev": h.get("lossStdDev"), "p95StdDevMs": h.get("p95StdDevMs"), "windowSeconds": h.get("windowSeconds")} for h in histories], "conformance probe histories", observed_at) if histories else unavailable("insufficient retained probe history"),
    }
    episode_values = {"t_conv": "convergenceSeconds", "t_persist": "persistenceSeconds",
                      "t_recover": "recoverySeconds"}
    for symbol, field in episode_values.items():
        values[symbol] = observed(episode[field], "completed natural observation episode",
                                  episode["endedAt"], scope={"episodeStartedAt": episode["startedAt"]}) \
            if episode and now - episode.get("endedEpoch", 0) <= HORIZON_SECONDS else right_censored(
                "natural observation episode detector", now_text, observation_started_at,
                "no completed natural episode in the retained observation window; duration is not assigned")
    records = []
    source_epoch = dynamics.get("startedAt")
    for symbol in SYMBOLS:
        record = {"symbol": symbol, **values.get(symbol, unavailable("collector has no mapping"))}
        record["sourceEpoch"] = source_epoch
        record["evidenceRefs"] = [record["source"]] if record.get("source") else []
        record["historyCoverage"] = {"horizonSeconds": HORIZON_SECONDS,
                                     "observedWindowSeconds": min(HORIZON_SECONDS, max(
                                         0.0, now - state["observationStartedEpoch"])),
                                     "observationStartedAt": observation_started_at,
                                     "episodeEligible": symbol in EPISODE_ONLY}
        sample_time = record.get("observedAt")
        age = None
        try:
            age = max(0.0, now - datetime.datetime.fromisoformat(sample_time.replace("Z", "+00:00")).timestamp())
        except (AttributeError, TypeError, ValueError):
            pass
        fresh = age is not None and (age <= HORIZON_SECONDS if symbol in EPISODE_ONLY else age <= FRESHNESS_SECONDS)
        record["freshness"] = {"ageSeconds": age, "limitSeconds": HORIZON_SECONDS if symbol in EPISODE_ONLY else FRESHNESS_SECONDS,
                               "fresh": fresh}
        if record["state"] == "observed" and not fresh:
            record.update({"state": "partial", "missingEvidence": "latest valid sample is outside its freshness limit"})
        record["validity"] = {"valid": record["state"] == "observed",
                              "condition": ("event-conditioned channel valid; value is right-censored"
                                  if record.get("observationStatus") == "right-censored" else
                                  "catalog validity conditions satisfied") if record["state"] == "observed"
                                  else record.get("missingEvidence")}
        records.append(record)
    tracking_values = {}
    for record in records:
        if record["state"] != "observed":
            continue
        scalar = tracking_value(record["symbol"], record.get("value"), now)
        if scalar is not None:
            tracking_values[record["symbol"]] = {"value": scalar, "unit": TRACKING_UNITS[record["symbol"]]}
    counts = {state: sum(item["state"] == state for item in records) for state in ("observed", "partial", "not-observed")}
    missing = [item["symbol"] for item in records if item["state"] != "observed"]
    return {"schemaVersion": "networking.re8ch.com/node-measurement-v1alpha3", "compatibleSchemaVersions": ["networking.re8ch.com/node-measurement-v1alpha2", "networking.re8ch.com/node-measurement-v1alpha1"], "catalogRef": "advanced-fabric-osi-identification",
            "node": NODE, "observedAt": observed_at or now_text, "generatedAt": now_text,
            "envelopeComplete": len(records) == len(SYMBOLS), "coverage": {"total": len(SYMBOLS), **counts},
            "trackingReady": not missing, "trackingGate": {"required": len(SYMBOLS), "observed": len(SYMBOLS) - len(missing),
            "missingSymbols": missing, "horizonSeconds": HORIZON_SECONDS, "freshnessSeconds": FRESHNESS_SECONDS},
            "latestEpisode": episode, "currentEpisode": state.get("activeEpisode"),
            "trackingValues": tracking_values,
            "measurements": records, "collectorState": state}


def publish(snapshot):
    name = "advanced-fabric-measurement-" + safe_name(NODE)
    obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": NAMESPACE,
           "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric", "app.kubernetes.io/component":
           "node-measurement", "networking.re8ch.com/source-node": NODE}},
           "data": {"measurement.json": json.dumps({key: value for key, value in snapshot.items()
                    if key != "collectorState"}, separators=(",", ":"))}}
    path = "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, name)
    try:
        api("PATCH", path, obj, "application/merge-patch+json")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        api("POST", "/api/v1/namespaces/%s/configmaps" % NAMESPACE, obj)


def publish_episode(episode):
    if not episode:
        return
    digest = hashlib.sha256((NODE + episode["startedAt"]).encode()).hexdigest()[:12]
    name = "natural-" + safe_name(NODE) + "-" + digest
    document = {"apiVersion": "networking.re8ch.com/v1alpha1", "kind": "NetworkObservationEpisode",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "re8ch-advanced-fabric",
        "networking.re8ch.com/subject-node": NODE}}, "spec": {"subjectRef": {"apiVersion": "v1", "kind": "Node", "name": NODE},
        "cause": episode.get("cause", "NaturalRIBFIBChange"), "detectedAt": episode["startedAt"], "collectorEpoch": episode.get("collectorEpoch"),
        "baseline": {"fingerprint": episode.get("baselineFingerprint"), "pathFingerprint": episode.get("baselinePathFingerprint"),
        "lossRatio": episode.get("baselineLossRatio")},
        "change": {"fingerprint": episode.get("observedFingerprint"), "pathFingerprint": episode.get("observedPathFingerprint")},
        "criteria": {"stableSamples": 2, "recoveryLossToleranceRatio": 0.001},
        "windows": {"before": "retained", "change": episode["startedAt"], "after": episode.get("endedAt")}},
        "status": {"state": episode.get("state", "Incomplete"), "startedAt": episode["startedAt"],
        "endedAt": episode.get("endedAt"), "measurements": {key: episode.get(key) for key in
        ("convergenceSeconds", "persistenceSeconds", "recoverySeconds", "recoveredLossRatio", "stableSamples")},
        "evidenceRefs": ["advanced-fabric-measurement-" + safe_name(NODE)],
        "missingEvidence": [] if episode.get("state") == "Complete" else ["stable recovery window incomplete"],
        "confounders": []}}
    path = "/apis/networking.re8ch.com/v1alpha1/networkobservationepisodes/%s" % name
    body = {key: value for key, value in document.items() if key != "status"}
    try:
        api("PATCH", path, body, "application/merge-patch+json")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        api("POST", "/apis/networking.re8ch.com/v1alpha1/networkobservationepisodes", body)
    api("PATCH", path + "/status", {"status": document["status"]}, "application/merge-patch+json")


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_state(state):
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(state, stream, separators=(",", ":"), sort_keys=True)
    os.replace(temporary, STATE_FILE)


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
    values = {symbol: item["value"] for symbol, item in snapshot.get("trackingValues", {}).items()}
    lines.extend(["# HELP advanced_fabric_observable Current raw network observable.",
                  "# TYPE advanced_fabric_observable gauge"])
    for symbol, value in values.items():
        unit = snapshot["trackingValues"][symbol]["unit"]
        lines.append('advanced_fabric_observable{%s,symbol="%s",unit="%s"} %s' %
                     (base, symbol, prometheus_escape(unit), value))
    requirements = {"Q": ("a_reach", "l_path", "t_rtt"), "K": ("t_state", "t_conv", "t_recover", "delta_ribfib"),
                    "H": ("p_route", "x_nh", "t_persist", "f_switch", "a_osc"),
                    "C": ("u_bgp", "w_bgp", "lambda_flap", "delta_ribfib", "n_path_change"),
                    "R": ("w_ecmp", "m_route", "n_peer", "n_nh", "n_if", "n_alt"),
                    "D": ("n_tun", "n_gw", "n_asn", "g_dep", "n_alt")}
    lines.extend(["# HELP advanced_fabric_structural_evidence_coverage Fraction of required observables currently valid.",
                  "# TYPE advanced_fabric_structural_evidence_coverage gauge"])
    for latent, required in requirements.items():
        present = sum(records[symbol]["state"] == "observed" for symbol in required)
        lines.append('advanced_fabric_structural_evidence_coverage{%s,latent="%s"} %.6f' %
                     (base, latent, present / len(required)))
    lines.extend(["# HELP advanced_fabric_tracking_ready Whether all 31 catalog measurements are valid for tracking.",
                  "# TYPE advanced_fabric_tracking_ready gauge",
                  'advanced_fabric_tracking_ready{%s} %d' % (base, 1 if snapshot.get("trackingReady") else 0)])
    temporary = os.path.join(METRICS_DIR, ".advanced_fabric_measurement.prom.tmp")
    target = os.path.join(METRICS_DIR, "advanced_fabric_measurement.prom")
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    os.replace(temporary, target)


while True:
    try:
        with open("/status/status.json", encoding="utf-8") as stream:
            current_status = json.load(stream)
        snapshot = build_snapshot(current_status, [read_probe("host"), read_probe("pod")],
                                  state=read_state(), service_traffic=read_service_traffic())
        publish(snapshot)
        publish_episode(snapshot.get("currentEpisode") or snapshot.get("latestEpisode"))
        publish_metrics(snapshot)
        write_state(snapshot.get("collectorState", {}))
    except Exception as error:
        print(json.dumps({"event": "node-measurement-publish-error", "node": NODE, "error": str(error)}), flush=True)
    time.sleep(INTERVAL)
