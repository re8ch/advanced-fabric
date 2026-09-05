import importlib.util
import os
import struct
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("KUBERNETES_SERVICE_HOST", "127.0.0.1")
os.environ.setdefault("KUBERNETES_SERVICE_PORT_HTTPS", "443")
spec = importlib.util.spec_from_file_location("advanced_fabric_dns", ROOT / "charts/re8ch-advanced-fabric/files/dns_server.py")
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def query(name, qtype):
    return struct.pack("!HHHHHH", 7, 0x0100, 1, 0, 0, 0) + dns.wire_name(name) + struct.pack("!HH", qtype, 1)


class DNSServerTest(unittest.TestCase):
    def setUp(self):
        dns.INDEX.services = {}
        dns.INDEX.slices = {}
        dns.INDEX.synced = set()
        dns.INDEX.last_sync = {}

    def test_cluster_ip_service_a_record(self):
        dns.INDEX.services[("default", "api")] = {"spec": {"clusterIPs": ["10.43.2.3"]}}
        response = dns.answer(query("api.default.svc.cluster.local", dns.Q_A))
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 1)
        self.assertIn(socket_bytes("10.43.2.3"), response)

    def test_existing_ipv4_service_returns_nodata_for_aaaa(self):
        dns.INDEX.services[("default", "api")] = {"spec": {"clusterIPs": ["10.43.2.3"]}}
        response = dns.answer(query("api.default.svc.cluster.local", dns.Q_AAAA))
        self.assertEqual(response[3] & 0x0F, 0)
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 0)

    def test_missing_service_returns_nxdomain(self):
        response = dns.answer(query("missing.default.svc.cluster.local", dns.Q_A))
        self.assertEqual(response[3] & 0x0F, 3)

    def test_headless_service_and_endpoint_hostname(self):
        dns.INDEX.services[("db", "postgres")] = {"spec": {"clusterIP": "None"}}
        dns.INDEX.slices[("db", "postgres")] = {"slice": {
            "metadata": {"uid": "slice"},
            "ports": [{"name": "sql", "protocol": "TCP", "port": 5432}],
            "endpoints": [{"hostname": "postgres-0", "addresses": ["10.42.1.9"], "conditions": {"ready": True}}],
        }}
        records = dns.INDEX.records("postgres.db.svc.cluster.local.", dns.Q_A)
        self.assertEqual(records, [(dns.Q_A, "10.42.1.9")])
        srv = dns.INDEX.records("_sql._tcp.postgres.db.svc.cluster.local.", dns.Q_SRV)
        self.assertEqual(srv[0][1][2:], (5432, "postgres-0.postgres.db.svc.cluster.local."))

    def test_cluster_ip_srv_targets_service(self):
        dns.INDEX.services[("default", "api")] = {"spec": {"clusterIP": "10.43.2.3",
            "ports": [{"name": "https", "protocol": "TCP", "port": 443}]}}
        records = dns.INDEX.records("_https._tcp.api.default.svc.cluster.local.", dns.Q_SRV)
        self.assertEqual(records, [(dns.Q_SRV, (0, 100, 443, "api.default.svc.cluster.local."))])

    def test_external_name_is_cname(self):
        dns.INDEX.services[("default", "external")] = {"spec": {"externalName": "example.net"}}
        self.assertEqual(dns.INDEX.records("external.default.svc.cluster.local.", dns.Q_A),
                         [(dns.Q_CNAME, "example.net.")])

    def test_unready_endpoint_is_excluded(self):
        dns.INDEX.services[("default", "api")] = {"spec": {"clusterIP": "None"}}
        dns.INDEX.slices[("default", "api")] = {"slice": {"metadata": {"uid": "slice"}, "ports": [],
            "endpoints": [{"addresses": ["10.42.1.10"], "conditions": {"ready": False}}]}}
        self.assertEqual(dns.INDEX.records("api.default.svc.cluster.local.", dns.Q_A), [])

    def test_watch_events_update_and_delete_service(self):
        item = {"metadata": {"namespace": "default", "name": "api"}, "spec": {"clusterIP": "10.43.2.3"}}
        dns.INDEX.apply_event("services", {"type": "ADDED", "object": item})
        self.assertIn(("default", "api"), dns.INDEX.services)
        dns.INDEX.apply_event("services", {"type": "DELETED", "object": item})
        self.assertNotIn(("default", "api"), dns.INDEX.services)

    def test_chart_has_no_legacy_runtime_dependency(self):
        template = (ROOT / "charts/re8ch-advanced-fabric/templates/advanced-fabric-dns.yaml").read_text()
        self.assertNotIn("k3s-coredns", template)
        self.assertNotIn("name: coredns", template)
        self.assertNotIn("Corefile", template)
        self.assertNotIn("NodeHosts", template)
        self.assertIn('ipAddresses: [{{ .Values.advancedFabric.dns.service.clusterIP | quote }}]', template)
        self.assertIn('"before-hook-creation,hook-succeeded"', template)
        probe = (ROOT / "charts/re8ch-advanced-fabric/files/dns_probe.py").read_text()
        self.assertNotIn("advanced-fabric-doh.kube-system.svc", probe)
        self.assertIn('create_default_context(cafile="/tls/ca.crt")', probe)
        self.assertNotIn("_create_unverified_context", probe)
        self.assertIn("advanced-fabric.re8ch.com/chart-version", template)

    def test_conditional_forwarder_uses_longest_matching_zone(self):
        packet = query("api.zt.re8ch.com", dns.Q_A)
        attempted = []
        dns.CONDITIONAL_FORWARDERS = {
            "re8ch.com.": ["192.0.2.1"],
            "zt.re8ch.com.": ["192.0.2.2"],
        }

        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def settimeout(self, _): pass
            def sendto(self, _packet, target): attempted.append(target[0])
            def recv(self, _size): raise OSError("test")

        with mock.patch.object(dns.socket, "socket", return_value=FakeSocket()):
            dns.forward(packet, "api.zt.re8ch.com.")
        self.assertEqual(attempted, ["192.0.2.2"])

    def test_prometheus_metrics_exposes_readiness(self):
        dns.INDEX.synced = {"services", "slices"}
        dns.INDEX.last_sync = {"services": __import__("time").time(), "slices": __import__("time").time()}
        self.assertIn(b"advanced_fabric_dns_ready 1", dns.prometheus_metrics())

    def test_static_authority_mode(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as stream:
            json.dump({"records": {
                "headlamp.zt.re8ch.com": [{"type": "A", "value": "10.181.22.16"}],
                "panel.zt.re8ch.com": [{"type": "CNAME", "value": "headlamp.zt.re8ch.com."}],
            }}, stream)
            stream.flush()
            index = dns.StaticIndex(stream.name)
            self.assertTrue(index.is_ready())
            self.assertEqual(index.records("headlamp.zt.re8ch.com.", dns.Q_A), [(dns.Q_A, "10.181.22.16")])
            self.assertEqual(index.records("panel.zt.re8ch.com.", dns.Q_A),
                             [(dns.Q_CNAME, "headlamp.zt.re8ch.com.")])


def socket_bytes(address):
    import socket
    return socket.inet_pton(socket.AF_INET, address)


if __name__ == "__main__":
    unittest.main()
