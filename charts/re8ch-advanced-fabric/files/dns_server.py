#!/usr/bin/env python3
"""Advanced Fabric authoritative Kubernetes DNS and DNS-over-HTTPS server."""

import base64
import http.server
import ipaddress
import json
import os
import signal
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request

CLUSTER_DOMAIN = os.getenv("CLUSTER_DOMAIN", "cluster.local").strip(".")
UPSTREAMS = [x for x in os.getenv("UPSTREAMS", "223.5.5.5,119.29.29.29,1.1.1.1").split(",") if x]
CONDITIONAL_FORWARDERS = {zone.rstrip(".").lower() + ".": servers for zone, servers in
                          json.loads(os.getenv("CONDITIONAL_FORWARDERS", "{}")).items()}
API = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"])
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
Q_A, Q_NS, Q_CNAME, Q_SOA, Q_PTR, Q_AAAA, Q_SRV = 1, 2, 5, 6, 12, 28, 33
CLASS_IN, TTL = 1, int(os.getenv("TTL", "30"))
METRICS_LOCK = threading.Lock()
METRICS = {"queries": 0, "failures": 0, "forwarded": 0}


def metric(name, amount=1):
    with METRICS_LOCK:
        METRICS[name] += amount


def prometheus_metrics():
    with METRICS_LOCK:
        values = dict(METRICS)
    values["ready"] = int(INDEX.is_ready())
    lines = [
        "# HELP advanced_fabric_dns_queries_total DNS requests received.",
        "# TYPE advanced_fabric_dns_queries_total counter",
        "advanced_fabric_dns_queries_total %d" % values["queries"],
        "# HELP advanced_fabric_dns_failures_total Requests returning SERVFAIL or malformed input.",
        "# TYPE advanced_fabric_dns_failures_total counter",
        "advanced_fabric_dns_failures_total %d" % values["failures"],
        "# HELP advanced_fabric_dns_forwarded_total Requests sent to an upstream resolver.",
        "# TYPE advanced_fabric_dns_forwarded_total counter",
        "advanced_fabric_dns_forwarded_total %d" % values["forwarded"],
        "# HELP advanced_fabric_dns_ready Whether both Kubernetes watches are current.",
        "# TYPE advanced_fabric_dns_ready gauge",
        "advanced_fabric_dns_ready %d" % values["ready"],
    ]
    return ("\n".join(lines) + "\n").encode()


def read_name(packet, offset):
    labels, jumped, end, seen = [], False, offset, set()
    while True:
        if offset >= len(packet) or offset in seen:
            raise ValueError("invalid DNS name")
        seen.add(offset)
        size = packet[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated DNS pointer")
            if not jumped:
                end = offset + 2
            offset = ((size & 0x3F) << 8) | packet[offset + 1]
            jumped = True
            continue
        offset += 1
        if size == 0:
            return ".".join(labels).lower() + ".", end if jumped else offset
        if offset + size > len(packet):
            raise ValueError("truncated DNS label")
        labels.append(packet[offset:offset + size].decode("ascii"))
        offset += size


def wire_name(name):
    name = name.rstrip(".")
    return b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split(".")) + b"\0"


def question(packet):
    if len(packet) < 12:
        raise ValueError("short DNS packet")
    qd = struct.unpack("!H", packet[4:6])[0]
    if qd != 1:
        raise ValueError("exactly one question is required")
    name, end = read_name(packet, 12)
    if end + 4 > len(packet):
        raise ValueError("truncated DNS question")
    qtype, qclass = struct.unpack("!HH", packet[end:end + 4])
    return name, qtype, qclass, packet[12:end + 4]


def rr(name, qtype, value, ttl=TTL):
    if qtype == Q_A:
        data = socket.inet_pton(socket.AF_INET, value)
    elif qtype == Q_AAAA:
        data = socket.inet_pton(socket.AF_INET6, value)
    elif qtype in (Q_CNAME, Q_PTR, Q_NS):
        data = wire_name(value)
    elif qtype == Q_SRV:
        priority, weight, port, target = value
        data = struct.pack("!HHH", priority, weight, port) + wire_name(target)
    else:
        raise ValueError("unsupported record")
    return wire_name(name) + struct.pack("!HHIH", qtype, CLASS_IN, ttl, len(data)) + data


class KubernetesIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.services, self.slices, self.synced, self.last_sync = {}, {}, set(), {}

    @staticmethod
    def key(item):
        meta = item.get("metadata", {})
        return meta.get("namespace", "default"), meta.get("name", "")

    def _request(self, path, timeout=310):
        token = open(TOKEN_PATH, encoding="utf-8").read().strip()
        req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token})
        context = ssl.create_default_context(cafile=CA_PATH)
        return urllib.request.urlopen(req, context=context, timeout=timeout)

    @staticmethod
    def slice_key(item):
        meta = item.get("metadata", {})
        return meta.get("namespace", "default"), meta.get("labels", {}).get("kubernetes.io/service-name", "")

    def replace(self, kind, payload):
        items = payload.get("items", [])
        if kind == "services":
            value = {self.key(x): x for x in items}
        else:
            value = {}
            for item in items:
                value.setdefault(self.slice_key(item), {})[item["metadata"]["uid"]] = item
        with self.lock:
            setattr(self, kind, value)
            self.synced.add(kind)
            self.last_sync[kind] = time.time()

    def apply_event(self, kind, event):
        item, action = event.get("object", {}), event.get("type")
        if action == "BOOKMARK":
            with self.lock:
                self.last_sync[kind] = time.time()
            return
        if action not in ("ADDED", "MODIFIED", "DELETED") or not item.get("metadata"):
            return
        with self.lock:
            if kind == "services":
                key = self.key(item)
                if action == "DELETED":
                    self.services.pop(key, None)
                else:
                    self.services[key] = item
            else:
                key, uid = self.slice_key(item), item["metadata"]["uid"]
                if action == "DELETED":
                    self.slices.get(key, {}).pop(uid, None)
                else:
                    self.slices.setdefault(key, {})[uid] = item
            self.last_sync[kind] = time.time()

    def run_resource(self, kind, path):
        while True:
            try:
                with self._request(path, timeout=10) as response:
                    payload = json.load(response)
                self.replace(kind, payload)
                rv = payload.get("metadata", {}).get("resourceVersion", "")
                query = urllib.parse.urlencode({"watch": "true", "allowWatchBookmarks": "true", "timeoutSeconds": "300", "resourceVersion": rv})
                with self._request(path + "?" + query) as response:
                    for raw in response:
                        if raw.strip():
                            self.apply_event(kind, json.loads(raw))
            except Exception as exc:
                print("kubernetes %s watch failed: %s" % (kind, exc), flush=True)
                time.sleep(2)

    def run(self):
        resources = (("services", "/api/v1/services"), ("slices", "/apis/discovery.k8s.io/v1/endpointslices"))
        for kind, path in resources:
            threading.Thread(target=self.run_resource, args=(kind, path), daemon=True).start()
        while True:
            time.sleep(5)

    def is_ready(self):
        with self.lock:
            return self.synced == {"services", "slices"} and all(
                time.time() - self.last_sync.get(kind, 0) < 330 for kind in self.synced)

    def records(self, name, qtype):
        suffix = ".svc.%s." % CLUSTER_DOMAIN
        if not name.endswith(suffix):
            return None
        labels = name[:-len(suffix)].strip(".").split(".")
        if len(labels) < 2:
            return []
        port_name = protocol = None
        if labels[0].startswith("_") and len(labels) == 4:
            port_name, protocol, service, namespace = labels
            port_name, protocol = port_name[1:], protocol[1:].upper()
        elif len(labels) >= 3:
            hostname, service, namespace = labels[-3:]
        else:
            hostname, service, namespace = None, labels[0], labels[1]
        with self.lock:
            svc = self.services.get((namespace, service))
            slices = list(self.slices.get((namespace, service), {}).values())
        if not svc:
            return []
        spec = svc.get("spec", {})
        external = spec.get("externalName")
        if external:
            return [(Q_CNAME, external.rstrip(".") + ".")]
        cluster_ips = [x for x in spec.get("clusterIPs", [spec.get("clusterIP")]) if x and x != "None"]
        if cluster_ips and qtype == Q_SRV:
            target = "%s.%s.svc.%s." % (service, namespace, CLUSTER_DOMAIN)
            return [(Q_SRV, (0, 100, int(port["port"]), target)) for port in spec.get("ports", [])
                    if port.get("port") and (not port_name or port.get("name") == port_name)
                    and (not protocol or port.get("protocol", "TCP") == protocol)]
        if cluster_ips and hostname is None and qtype != Q_SRV:
            return [(Q_AAAA if ":" in ip else Q_A, ip) for ip in cluster_ips if qtype in (Q_A, Q_AAAA, 255) and (qtype == 255 or (":" in ip) == (qtype == Q_AAAA))]
        answers = []
        publish_unready = bool(spec.get("publishNotReadyAddresses"))
        ports = []
        for slc in slices:
            for port in slc.get("ports", []):
                if port.get("port") and (not port_name or port.get("name") == port_name) and (not protocol or port.get("protocol", "TCP") == protocol):
                    ports.append(port)
            for endpoint in slc.get("endpoints", []):
                if not publish_unready and endpoint.get("conditions", {}).get("ready") is False:
                    continue
                ep_host = endpoint.get("hostname")
                for address in endpoint.get("addresses", []):
                    target_label = ep_host or address.replace(":", "-").replace(".", "-")
                    target = "%s.%s.%s.svc.%s." % (target_label, service, namespace, CLUSTER_DOMAIN)
                    if qtype == Q_SRV:
                        for port in ports:
                            answers.append((Q_SRV, (0, 100, int(port["port"]), target)))
                    elif hostname is None or hostname == target_label:
                        rtype = Q_AAAA if ":" in address else Q_A
                        if qtype in (rtype, 255):
                            answers.append((rtype, address))
        return answers


INDEX = KubernetesIndex()


def forward(packet, name=""):
    matching = [(zone, servers) for zone, servers in CONDITIONAL_FORWARDERS.items() if name.endswith(zone)]
    upstreams = max(matching, key=lambda item: len(item[0]))[1] if matching else UPSTREAMS
    for upstream in upstreams:
        metric("forwarded")
        family = socket.AF_INET6 if ":" in upstream else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(packet, (upstream, 53))
                response = sock.recv(65535)
            if response[2] & 0x02:
                with socket.create_connection((upstream, 53), timeout=2) as tcp:
                    tcp.sendall(struct.pack("!H", len(packet)) + packet)
                    size = struct.unpack("!H", tcp.recv(2))[0]
                    response = b""
                    while len(response) < size:
                        response += tcp.recv(size - len(response))
            return response
        except OSError:
            continue
    return None


def answer(packet):
    metric("queries")
    try:
        name, qtype, qclass, raw_question = question(packet)
        if qclass != CLASS_IN:
            raise ValueError("unsupported class")
        records = INDEX.records(name, qtype)
        if records is None:
            response = forward(packet, name)
            if response is not None:
                return response
            metric("failures")
            return packet[:2] + b"\x81\x82" + packet[4:6] + b"\0\0\0\0\0\0" + raw_question
        payload = b"".join(rr(name, kind, value) for kind, value in records)
        flags = b"\x85\x80" if records else b"\x85\x83"
        return packet[:2] + flags + struct.pack("!HHHH", 1, len(records), 0, 0) + raw_question + payload
    except (ValueError, UnicodeError):
        metric("failures")
        return packet[:2].ljust(2, b"\0") + b"\x81\x81\0\0\0\0\0\0\0\0"


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(answer(data), self.client_address)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        raw = self.request.recv(2)
        if len(raw) != 2:
            return
        size = struct.unpack("!H", raw)[0]
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                return
            data += chunk
        response = answer(data)
        self.request.sendall(struct.pack("!H", len(response)) + response)


class HTTPHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, data=b"", content_type="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, b"ok\n")
        if parsed.path == "/ready":
            ready = INDEX.is_ready()
            return self._send(200 if ready else 503, b"ready\n" if ready else b"not ready\n")
        if parsed.path == "/metrics":
            return self._send(200, prometheus_metrics(), "text/plain; version=0.0.4")
        if parsed.path != "/dns-query":
            return self._send(404)
        encoded = urllib.parse.parse_qs(parsed.query).get("dns", [""])[0]
        try:
            packet = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except ValueError:
            return self._send(400)
        self._send(200, answer(packet), "application/dns-message")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/dns-query" or self.headers.get("Content-Type", "").split(";")[0] != "application/dns-message":
            return self._send(400)
        self._send(200, answer(self.rfile.read(int(self.headers.get("Content-Length", "0")))), "application/dns-message")

    def log_message(self, *_):
        return


class ThreadingUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def serve():
    threading.Thread(target=INDEX.run, daemon=True).start()
    servers = [ThreadingUDPServer(("0.0.0.0", 53), UDPHandler), ThreadingTCPServer(("0.0.0.0", 53), TCPHandler)]
    health = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), HTTPHandler)
    metrics = http.server.ThreadingHTTPServer(("0.0.0.0", 9153), HTTPHandler)
    doh = http.server.ThreadingHTTPServer(("0.0.0.0", 8443), HTTPHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    doh.socket = context.wrap_socket(doh.socket, server_side=True)
    servers.extend([health, metrics, doh])
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    signal.pause()


if __name__ == "__main__":
    serve()
