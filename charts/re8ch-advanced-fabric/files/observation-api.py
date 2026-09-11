#!/usr/bin/env python3
"""Read-only, storage-neutral observation API for Advanced Fabric consumers."""

import datetime
import json
import math
import os
import ssl
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


API_GROUP = os.environ.get("ADVANCED_FABRIC_API_GROUP", "networking.advfab.org")
NAMESPACE = os.environ.get("POD_NAMESPACE", "default")
PORT = int(os.environ.get("OBSERVATION_API_PORT", "8080"))
LABEL_PREFIX = os.environ.get("ADVANCED_FABRIC_LABEL_PREFIX", "networking.advfab.org")
HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
KUBE_PORT = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
BASE = "https://%s:%s" % (HOST, KUBE_PORT)
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

STRUCTURE_SYMBOLS = {
    "Q": ("a_reach", "l_path", "t_rtt", "b_rx"),
    "K": ("t_state", "t_conv", "t_recover", "delta_ribfib"),
    "H": ("p_route", "x_nh", "t_persist", "f_switch", "a_osc"),
    "C": ("u_bgp", "w_bgp", "n_adv", "n_recv", "lambda_flap", "delta_ribfib", "n_path_change"),
    "R": ("b_est", "w_ecmp", "m_route", "n_peer", "n_nh", "n_if", "n_alt"),
    "D": ("d_mode", "n_tun", "n_gw", "n_asn", "g_dep", "n_alt"),
}


def kube_get(path):
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    request = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + token})
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        return json.load(response)


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def bounded_times(times, maximum):
    times = sorted(set(times))
    maximum = max(1, min(int(maximum), 100))
    if len(times) <= maximum:
        return times
    if maximum == 1:
        return [times[-1]]
    return [times[round(index * (len(times) - 1) / (maximum - 1))] for index in range(maximum)]


def relationship_series(history, vertices, start=None, end=None, maximum=7):
    """Produce synchronized coordinates; null remains null and is never coerced to zero."""
    rows = []
    for point in history or []:
        try:
            timestamp = parse_time(point["observedAt"])
        except (KeyError, TypeError, ValueError):
            continue
        if start is not None and timestamp < start or end is not None and timestamp > end:
            continue
        values = point.get("values", {})
        rows.append((timestamp, values))
    selected = set(bounded_times([timestamp for timestamp, _ in rows], maximum))
    ranges = {}
    for vertex in vertices:
        for symbol in STRUCTURE_SYMBOLS.get(vertex, ()):
            numeric = [float(values[symbol]["value"]) for _, values in rows
                       if isinstance(values.get(symbol), dict) and
                       isinstance(values[symbol].get("value"), (int, float)) and
                       math.isfinite(float(values[symbol]["value"]))]
            if numeric:
                ranges[symbol] = (min(numeric), max(numeric))
    samples = []
    for timestamp, values in rows:
        if timestamp not in selected:
            continue
        coordinates, confidence, evidence = {}, {}, {}
        for vertex in vertices:
            components = []
            missing = []
            for symbol in STRUCTURE_SYMBOLS.get(vertex, ()):
                item = values.get(symbol)
                if not isinstance(item, dict) or symbol not in ranges or not isinstance(item.get("value"), (int, float)):
                    missing.append(symbol)
                    continue
                low, high = ranges[symbol]
                components.append(.5 if high == low else (float(item["value"]) - low) / (high - low))
            complete = bool(components) and not missing
            coordinates[vertex] = sum(components) / len(components) if complete else None
            confidence[vertex] = len(components) / max(1, len(STRUCTURE_SYMBOLS.get(vertex, ())))
            evidence[vertex] = {"state": "Observed" if complete else "Partial" if components else "NotObserved",
                                "missingSymbols": missing}
        samples.append({"time": timestamp, "coordinates": coordinates,
                        "confidence": confidence, "evidence": evidence})
    return samples


def configmaps(component):
    selector = urllib.parse.quote("app.kubernetes.io/component=" + component, safe="")
    return kube_get("/api/v1/namespaces/%s/configmaps?labelSelector=%s" % (NAMESPACE, selector)).get("items", [])


def payload(item, key):
    try:
        return json.loads(item.get("data", {}).get(key, "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}


def observations():
    structural = configmaps("structural-observation")
    document = payload(structural[0], "observations.json") if structural else {"nodes": {}}
    nodes = document.setdefault("nodes", {})
    for item in configmaps("node-status"):
        status = payload(item, "status.json")
        if status.get("node"):
            nodes.setdefault(status["node"], {}).update({"networkStatus": status})
    for item in configmaps("node-measurement"):
        measurement = payload(item, "measurement.json")
        if measurement.get("node"):
            nodes.setdefault(measurement["node"], {}).update({"measurement": measurement})
    return document


def relationship_documents():
    path = "/apis/%s/v1alpha1/tradeofftriangles" % API_GROUP
    return kube_get(path).get("items", [])


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, body):
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/healthz":
                return self.send_json(200, {"ready": True, "apiVersion": "v1"})
            data = observations().get("nodes", {})
            if url.path == "/api/v1/subjects":
                return self.send_json(200, {"apiVersion": "v1", "items": [
                    {"kind": "Node", "name": name, "observedAt": value.get("observedAt"),
                     "state": "Ready" if value.get("trackingReady") else "Partial"}
                    for name, value in sorted(data.items())]})
            if url.path.startswith("/api/v1/subjects/") and url.path.endswith("/snapshot"):
                parts = url.path.split("/")
                name = urllib.parse.unquote(parts[5])
                if parts[4].lower() != "node" or name not in data:
                    return self.send_json(404, {"error": "subject not found"})
                return self.send_json(200, {"apiVersion": "v1", "kind": "Node", "name": name, **data[name]})
            if url.path == "/api/v1/relationships":
                return self.send_json(200, {"apiVersion": "v1", "items": [
                    {"id": item["metadata"]["name"], **item.get("spec", {})}
                    for item in relationship_documents()]})
            if url.path.startswith("/api/v1/relationships/") and url.path.endswith("/series"):
                relationship_id = urllib.parse.unquote(url.path.split("/")[4])
                relationship = next((item for item in relationship_documents()
                                     if item.get("metadata", {}).get("name") == relationship_id), None)
                subject = query.get("subject", [""])[0]
                if not relationship or subject not in data:
                    return self.send_json(404, {"error": "relationship or subject not found"})
                vertices = relationship.get("spec", {}).get("vertices", [])
                start = float(query["start"][0]) if "start" in query else None
                end = float(query["end"][0]) if "end" in query else None
                maximum = int(query.get("maxPoints", ["7"])[0])
                return self.send_json(200, {"apiVersion": "v1", "relationshipId": relationship_id,
                    "vertices": vertices, "scale": {vertex: {"minimum": 0, "maximum": 1,
                    "method": "window-min-max-component-mean"} for vertex in vertices},
                    "samples": relationship_series(data[subject].get("history", []), vertices, start, end, maximum)})
            if url.path == "/api/v1/episodes":
                subject = query.get("subject", [""])[0]
                items = kube_get("/apis/%s/v1alpha1/networkobservationepisodes" % API_GROUP).get("items", [])
                return self.send_json(200, {"apiVersion": "v1", "items": [item for item in items
                    if not subject or item.get("spec", {}).get("subjectRef", {}).get("name") == subject]})
            self.send_json(404, {"error": "endpoint not found"})
        except (ValueError, KeyError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(503, {"error": "observation data unavailable", "detail": str(exc)})

    def log_message(self, format, *args):
        print(json.dumps({"event": "observation-api", "message": format % args}), flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
