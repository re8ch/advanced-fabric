import ast
import datetime
import json
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


controller = load_functions(ROOT / "charts/re8ch-advanced-fabric/files/controller.py",
                            {"parse_time", "network_quality"}, {"datetime": datetime, "json": json, "time": time})
probe = load_functions(ROOT / "charts/re8ch-advanced-fabric/files/conformance-probe.py",
                       {"percentile", "encode_name", "dns_packet", "dns_rcode", "prometheus_escape",
                        "labels", "parse_observed_time", "prometheus_text"},
                       {"random": __import__("random"), "struct": __import__("struct"),
                        "NODE": "a", "PLANE": "host"})


class NetworkQualityTest(unittest.TestCase):
    def test_13_node_matrix_requires_676_directed_paths(self):
        result = controller["network_quality"]([], 13, {"minimumCoverageRatio": 1})
        self.assertEqual(result["expectedPaths"], 676)
        self.assertFalse(result["networkReady"])
        self.assertFalse(result["dnsReady"])

    def test_complete_fresh_matrix_and_dns_pass(self):
        now = 1_800_000_000
        nodes = ["a", "b"]
        configmaps = []
        for source_node in nodes:
            for source_plane in ("host", "pod"):
                paths = [{"sourceNode": source_node, "sourcePlane": source_plane, "targetNode": target,
                          "targetPlane": target_plane, "lossRatio": 0, "p95Ms": 10}
                         for target in nodes for target_plane in ("host", "pod")]
                dns = [{"protocol": protocol, "failureRatio": 0, "p95Ms": 5} for protocol in ("udp", "tcp")]
                payload = {"sourceNode": source_node, "sourcePlane": source_plane,
                           "observedAt": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
                           "paths": paths, "dns": dns}
                configmaps.append({"data": {"result.json": json.dumps(payload)}})
        result = controller["network_quality"](configmaps, 2, {"freshnessSeconds": 120,
            "minimumCoverageRatio": 1, "maximumLossRatio": 0, "maximumCrossRegionP95Ms": 400,
            "dns": {"maximumFailureRatio": .001, "maximumP95Ms": 50, "requireTcp": True}}, now=now)
        self.assertTrue(result["networkReady"])
        self.assertTrue(result["dnsReady"])
        self.assertTrue(result["dohReady"])
        self.assertEqual(result["observedPaths"], 16)

    def test_enabled_doh_is_a_separate_fail_closed_gate(self):
        now = 1_800_000_000
        payload = {"sourceNode": "a", "sourcePlane": "host",
                   "observedAt": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
                   "paths": [], "dns": [], "doh": [{"failureRatio": 1, "p95Ms": None}]}
        result = controller["network_quality"]([{"data": {"result.json": json.dumps(payload)}}], 1,
            {"freshnessSeconds": 120, "doh": {"enabled": True, "maximumFailureRatio": .001,
                                                "maximumP95Ms": 100}}, now=now)
        self.assertFalse(result["dohReady"])
        self.assertEqual(result["failedDohCount"], 1)

    def test_one_way_loss_fails_instead_of_being_masked(self):
        payload = {"sourceNode": "a", "sourcePlane": "host", "observedAt": "2027-01-15T08:00:00Z",
                   "paths": [{"lossRatio": 1, "p95Ms": None}], "dns": []}
        result = controller["network_quality"]([{"data": {"result.json": json.dumps(payload)}}], 1,
                                               {"freshnessSeconds": 120}, now=1_800_000_000)
        self.assertGreater(result["failedPathCount"], 0)
        self.assertFalse(result["networkReady"])

    def test_dns_wire_format_validates_transaction(self):
        query_id, packet = probe["dns_packet"]("kubernetes.default.svc.cluster.local", query_id=42)
        response = bytearray(packet)
        response[2:4] = b"\x81\x80"
        self.assertEqual(query_id, 42)
        self.assertEqual(probe["dns_rcode"](response, query_id), 0)

    def test_node_exporter_textfile_contract_has_bounded_route_and_dns_labels(self):
        result = {"observedAt": "2027-01-15T08:00:00Z", "paths": [{"sourceNode": "a",
            "sourcePlane": "host", "targetNode": "b", "targetPlane": "pod", "address": "10.42.2.3",
            "attempts": 3, "successes": 2, "lossRatio": .3333, "p50Ms": 10, "p95Ms": 12,
            "selectedSourceAddresses": ["10.181.22.1"]}], "dns": [{"server": "10.43.0.10",
            "protocol": "udp", "name": "kubernetes.default.svc.cluster.local", "attempts": 3,
            "successes": 3, "failureRatio": 0, "p50Ms": 2, "p95Ms": 3, "rcodes": {"0": 3}}],
            "doh": [{"url": "https://advanced-fabric-doh.kube-system.svc.cluster.local/dns-query",
            "name": "kubernetes.default.svc.cluster.local", "attempts": 3, "successes": 3,
            "failureRatio": 0, "p50Ms": 4, "p95Ms": 5, "rcodes": {"0": 3}}]}
        rendered = probe["prometheus_text"](result)
        self.assertIn('re8ch_network_path_loss_ratio{source_node="a",source_plane="host",target_address="10.42.2.3",target_node="b",target_plane="pod"} 0.3333', rendered)
        self.assertIn('re8ch_dns_probe_responses{protocol="udp",query="kubernetes.default.svc.cluster.local",rcode="0",server="10.43.0.10",server_role="stable",source_node="a",source_plane="host"}', rendered)
        self.assertIn("re8ch_doh_probe_latency_p95_milliseconds", rendered)
        self.assertNotIn("result.json", rendered)

    def test_shadow_dns_is_required_before_promotion(self):
        now = 1_800_000_000
        payload = {"sourceNode": "a", "sourcePlane": "host",
                   "observedAt": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
                   "dns": [{"serverRole": "stable", "protocol": protocol, "failureRatio": 0, "p95Ms": 5}
                           for protocol in ("udp", "tcp")]}
        result = controller["network_quality"]([{"data": {"result.json": json.dumps(payload)}}], 1,
            {"freshnessSeconds": 120, "dns": {"shadowEnabled": True, "requireTcp": True,
             "maximumFailureRatio": .001, "maximumP95Ms": 50}}, now=now)
        self.assertFalse(result["dnsReady"])
        self.assertTrue(any(item.get("serverRole") == "shadow" for item in result["failedDns"]))


if __name__ == "__main__":
    unittest.main()
