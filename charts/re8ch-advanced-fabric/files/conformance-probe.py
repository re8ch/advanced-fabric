#!/usr/bin/env python3
"""Directed host/pod-path and DNS conformance probe for Advanced Fabric."""

import json
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request


NODE = os.environ["NODE_NAME"]
PLANE = os.environ["SOURCE_PLANE"]
INTERVAL = int(os.environ.get("PROBE_INTERVAL_SECONDS", "30"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT_SECONDS", "2"))
SAMPLES = int(os.environ.get("PROBE_SAMPLES", "3"))
PORT = int(os.environ.get("PROBE_PORT", "4241"))
NAMESPACE = os.environ.get("PUBLISH_NAMESPACE", "kube-system")
TEXTFILE_DIR = os.environ.get("TEXTFILE_DIR", "/metrics")
DOH_URL = os.environ.get("DOH_URL", "")
DOH_CA_FILE = os.environ.get("DOH_CA_FILE", "/doh-ca/tls.crt")
SHADOW_DNS_SERVICE = os.environ.get("SHADOW_DNS_SERVICE", "")
BASE = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"])
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
CONTEXT = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + TOKEN, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, context=CONTEXT, timeout=15) as response:
        return json.load(response) if response.length != 0 else {}


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * quantile))], 3)


def tcp_probe(address, port=PORT):
    started = time.monotonic()
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT) as connection:
            source_address = connection.getsockname()[0]
            return True, round((time.monotonic() - started) * 1000, 3), "", source_address
    except OSError as error:
        return False, round((time.monotonic() - started) * 1000, 3), error.__class__.__name__, None


def measure(address):
    attempts = [tcp_probe(address) for _ in range(SAMPLES)]
    latencies = [latency for ok, latency, _, _ in attempts if ok]
    successes = len(latencies)
    return {"address": address, "port": PORT, "attempts": len(attempts), "successes": successes,
            "lossRatio": round(1 - successes / len(attempts), 4), "p50Ms": percentile(latencies, .5),
            "p95Ms": percentile(latencies, .95), "selectedSourceAddresses": sorted({source for ok, _, _, source
            in attempts if ok and source}), "errors": sorted({error for ok, _, error, _ in attempts if not ok})}


def encode_name(name):
    parts = name.rstrip(".").split(".")
    return b"".join(bytes([len(part)]) + part.encode() for part in parts) + b"\0"


def dns_packet(name, qtype=1, query_id=None):
    query_id = random.randint(0, 65535) if query_id is None else query_id
    return query_id, struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + encode_name(name) + struct.pack("!HH", qtype, 1)


def dns_rcode(payload, query_id):
    if len(payload) < 12:
        raise ValueError("short DNS response")
    response_id, flags = struct.unpack("!HH", payload[:4])
    if response_id != query_id:
        raise ValueError("DNS transaction mismatch")
    return flags & 0xF


def dns_query(server, name, protocol="udp"):
    query_id, packet = dns_packet(name)
    started = time.monotonic()
    try:
        socktype = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
        with socket.socket(socket.AF_INET, socktype) as client:
            client.settimeout(TIMEOUT)
            client.connect((server, 53))
            if protocol == "tcp":
                client.sendall(struct.pack("!H", len(packet)) + packet)
                size = struct.unpack("!H", client.recv(2))[0]
                response = b""
                while len(response) < size:
                    response += client.recv(size - len(response))
            else:
                client.send(packet)
                response = client.recv(4096)
        rcode = dns_rcode(response, query_id)
        return {"ok": rcode == 0, "rcode": rcode, "latencyMs": round((time.monotonic() - started) * 1000, 3)}
    except (OSError, ValueError) as error:
        return {"ok": False, "rcode": None, "latencyMs": round((time.monotonic() - started) * 1000, 3),
                "error": error.__class__.__name__}


def dns_measure(server, protocol, name="kubernetes.default.svc.cluster.local", role="stable"):
    attempts = [dns_query(server, name, protocol) for _ in range(SAMPLES)]
    latencies = [item["latencyMs"] for item in attempts if item["ok"]]
    successes = len(latencies)
    return {"server": server, "serverRole": role, "protocol": protocol, "name": name, "attempts": len(attempts),
            "successes": successes, "failureRatio": round(1 - successes / len(attempts), 4),
            "p50Ms": percentile(latencies, .5), "p95Ms": percentile(latencies, .95),
            "rcodes": {str(code): sum(1 for item in attempts if item.get("rcode") == code)
                       for code in sorted({item.get("rcode") for item in attempts if item.get("rcode") is not None})}}


def doh_query(url, name):
    query_id, packet = dns_packet(name)
    started = time.monotonic()
    try:
        context = ssl.create_default_context(cafile=DOH_CA_FILE)
        request = urllib.request.Request(url, data=packet, method="POST", headers={
            "Accept": "application/dns-message", "Content-Type": "application/dns-message"})
        with urllib.request.urlopen(request, context=context, timeout=TIMEOUT) as response:
            payload = response.read(65536)
            content_type = response.headers.get_content_type()
        if content_type != "application/dns-message":
            raise ValueError("unexpected DoH content type")
        rcode = dns_rcode(payload, query_id)
        return {"ok": rcode == 0, "rcode": rcode, "latencyMs": round((time.monotonic() - started) * 1000, 3)}
    except (OSError, ValueError, urllib.error.URLError) as error:
        return {"ok": False, "rcode": None, "latencyMs": round((time.monotonic() - started) * 1000, 3),
                "error": error.__class__.__name__}


def doh_measure(url, name="kubernetes.default.svc.cluster.local"):
    attempts = [doh_query(url, name) for _ in range(SAMPLES)]
    latencies = [item["latencyMs"] for item in attempts if item["ok"]]
    successes = len(latencies)
    return {"url": url, "name": name, "attempts": len(attempts), "successes": successes,
            "failureRatio": round(1 - successes / len(attempts), 4), "p50Ms": percentile(latencies, .5),
            "p95Ms": percentile(latencies, .95), "rcodes": {str(code): sum(1 for item in attempts
            if item.get("rcode") == code) for code in sorted({item.get("rcode") for item in attempts
            if item.get("rcode") is not None})}}


def discover():
    fabric = api("GET", "/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch")
    inventory_nodes = {item.get("name") for item in fabric.get("spec", {}).get("nodes", [])}
    nodes = api("GET", "/api/v1/nodes").get("items", [])
    pods = api("GET", "/api/v1/namespaces/kube-system/pods?labelSelector="
               "app.kubernetes.io%2Fname%3Dre8ch-advanced-fabric-pod-conformance").get("items", [])
    pod_ips = {pod.get("spec", {}).get("nodeName"): pod.get("status", {}).get("podIP") for pod in pods
               if pod.get("status", {}).get("phase") == "Running"}
    targets = []
    for node in nodes:
        name = node.get("metadata", {}).get("name")
        addresses = node.get("status", {}).get("addresses", [])
        host_ip = next((item.get("address") for item in addresses if item.get("type") == "InternalIP"), None)
        if name in inventory_nodes:
            targets.append({"node": name, "hostIP": host_ip, "podIP": pod_ips.get(name)})
    service = api("GET", "/api/v1/namespaces/kube-system/services/kube-dns")
    dns_servers = [("stable", service.get("spec", {}).get("clusterIP"))]
    if SHADOW_DNS_SERVICE:
        try:
            shadow = api("GET", "/api/v1/namespaces/kube-system/services/%s" % SHADOW_DNS_SERVICE)
            dns_servers.append(("shadow", shadow.get("spec", {}).get("clusterIP")))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    return sorted(targets, key=lambda item: item["node"]), dns_servers


def serve():
    """Provide the same TCP health contract in the host and pod namespaces."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", PORT))
    listener.listen(64)
    while True:
        connection, _ = listener.accept()
        connection.close()


def snapshot():
    targets, dns_servers = discover()
    paths = []
    for target in targets:
        for destination_plane, key in (("host", "hostIP"), ("pod", "podIP")):
            if target.get(key):
                paths.append({"sourceNode": NODE, "sourcePlane": PLANE, "targetNode": target["node"],
                              "targetPlane": destination_plane, **measure(target[key])})
    dns = []
    for role, dns_server in dns_servers:
        if dns_server and dns_server != "None":
            dns.extend(dns_measure(dns_server, protocol, role=role) for protocol in ("udp", "tcp"))
    doh = [doh_measure(DOH_URL)] if DOH_URL else []
    return {"schemaVersion": "networking.re8ch.com/network-quality-v1alpha1", "observedAt":
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sourceNode": NODE, "sourcePlane": PLANE,
            "targetsDiscovered": len(targets), "paths": paths, "dns": dns, "doh": doh}


def prometheus_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def labels(values):
    return "{" + ",".join('%s="%s"' % (key, prometheus_escape(value)) for key, value in sorted(values.items())) + "}"


def parse_observed_time(value):
    import datetime
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def prometheus_text(result):
    """Render the stable NWQ-1/DNSQ-1 Node Exporter textfile contract."""
    lines = [
        "# HELP re8ch_network_quality_probe_info Static identity of a conformance probe source.",
        "# TYPE re8ch_network_quality_probe_info gauge",
        "re8ch_network_quality_probe_info%s 1" % labels({"source_node": NODE, "source_plane": PLANE,
                                                         "standard": "NWQ-1_DNSQ-1"}),
        "# HELP re8ch_network_quality_observed_timestamp_seconds Unix time of the completed measurement.",
        "# TYPE re8ch_network_quality_observed_timestamp_seconds gauge",
        "re8ch_network_quality_observed_timestamp_seconds%s %d" % (labels({"source_node": NODE,
            "source_plane": PLANE}), int(parse_observed_time(result["observedAt"]))),
    ]
    path_metrics = (("attempts", "re8ch_network_path_attempts"), ("successes", "re8ch_network_path_successes"),
                    ("lossRatio", "re8ch_network_path_loss_ratio"), ("p50Ms", "re8ch_network_path_latency_p50_milliseconds"),
                    ("p95Ms", "re8ch_network_path_latency_p95_milliseconds"))
    for entry in result.get("paths", []):
        common = {"source_node": entry["sourceNode"], "source_plane": entry["sourcePlane"],
                  "target_node": entry["targetNode"], "target_plane": entry["targetPlane"],
                  "target_address": entry["address"]}
        for field, metric in path_metrics:
            if entry.get(field) is not None:
                lines.append("%s%s %s" % (metric, labels(common), entry[field]))
        for source_address in entry.get("selectedSourceAddresses", []):
            lines.append("re8ch_network_path_selected_source_info%s 1" % labels(dict(common,
                         selected_source_address=source_address)))
    dns_metrics = (("attempts", "re8ch_dns_probe_attempts"), ("successes", "re8ch_dns_probe_successes"),
                   ("failureRatio", "re8ch_dns_probe_failure_ratio"), ("p50Ms", "re8ch_dns_probe_latency_p50_milliseconds"),
                   ("p95Ms", "re8ch_dns_probe_latency_p95_milliseconds"))
    for entry in result.get("dns", []):
        common = {"source_node": NODE, "source_plane": PLANE, "server": entry["server"],
                  "server_role": entry.get("serverRole", "stable"),
                  "protocol": entry["protocol"], "query": entry["name"]}
        for field, metric in dns_metrics:
            if entry.get(field) is not None:
                lines.append("%s%s %s" % (metric, labels(common), entry[field]))
        for rcode, count in entry.get("rcodes", {}).items():
            lines.append("re8ch_dns_probe_responses%s %s" % (labels(dict(common, rcode=rcode)), count))
    doh_metrics = (("attempts", "re8ch_doh_probe_attempts"), ("successes", "re8ch_doh_probe_successes"),
                   ("failureRatio", "re8ch_doh_probe_failure_ratio"), ("p50Ms", "re8ch_doh_probe_latency_p50_milliseconds"),
                   ("p95Ms", "re8ch_doh_probe_latency_p95_milliseconds"))
    for entry in result.get("doh", []):
        common = {"source_node": NODE, "source_plane": PLANE, "endpoint": entry["url"], "query": entry["name"]}
        for field, metric in doh_metrics:
            if entry.get(field) is not None:
                lines.append("%s%s %s" % (metric, labels(common), entry[field]))
        for rcode, count in entry.get("rcodes", {}).items():
            lines.append("re8ch_doh_probe_responses%s %s" % (labels(dict(common, rcode=rcode)), count))
    return "\n".join(lines) + "\n"


def write_textfile(result):
    os.makedirs(TEXTFILE_DIR, exist_ok=True)
    path = os.path.join(TEXTFILE_DIR, "advanced_fabric_%s.prom" % PLANE)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(prometheus_text(result))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def publish(result):
    safe_node = NODE.lower().replace("_", "-").replace(".", "-")
    name = "advanced-fabric-probe-%s-%s" % (safe_node, PLANE)
    obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": NAMESPACE,
           "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric", "app.kubernetes.io/component":
           "network-quality", "networking.re8ch.com/source-node": NODE, "networking.re8ch.com/source-plane": PLANE}},
           "data": {"result.json": json.dumps(result, separators=(",", ":"))}}
    path = "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, name)
    try:
        api("PATCH", path, obj)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        api("POST", "/api/v1/namespaces/%s/configmaps" % NAMESPACE, obj)


if __name__ == "__main__":
    threading.Thread(target=serve, name="conformance-health", daemon=True).start()
    while True:
        try:
            result = snapshot()
            write_textfile(result)
            publish(result)
        except Exception as error:
            print(json.dumps({"event": "network-quality-probe-error", "node": NODE, "plane": PLANE,
                              "error": str(error)}), flush=True)
        time.sleep(INTERVAL)
