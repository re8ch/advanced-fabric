import ast
import datetime
import hashlib
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
                            {"parse_time", "condition", "network_quality", "cluster_inventory", "stale_desired_nodes",
                             "measurement_index", "evidence_plan", "node_inferences", "osi_snapshot",
                             "append_osi_history", "assessment_document", "service_traffic_index",
                             "service_osi_snapshot", "service_assessment_document"},
                            {"datetime": datetime, "json": json, "time": time, "math": __import__("math"),
                             "hashlib": hashlib,
                             "MEASUREMENT_DEFINITIONS": {"path-quality-v1": {}, "temporal-stability-v1": {},
                                                         "failure-domain-graph-v1": {}}})
probe = load_functions(ROOT / "charts/re8ch-advanced-fabric/files/conformance-probe.py",
                       {"percentile", "history_summary", "encode_name", "dns_packet", "dns_rcode", "prometheus_escape",
                        "labels", "parse_observed_time", "prometheus_text"},
                       {"random": __import__("random"), "struct": __import__("struct"),
                        "statistics": __import__("statistics"), "INTERVAL": 30, "NODE": "a", "PLANE": "host"})


class NetworkQualityTest(unittest.TestCase):
    def test_service_osi_uses_measured_inflow_and_exposes_numeric_calculation(self):
        now = 1_800_000_000
        observed = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
        payload = {"observedAt": observed, "windowSeconds": 60, "collector": "hubble",
                   "samples": [
                       {"namespace": "headlamp", "service": "headlamp", "node": "a",
                        "receivedBytes": 750, "receivedPackets": 75, "requests": 30},
                       {"namespace": "headlamp", "service": "headlamp", "node": "b",
                        "receivedBytes": 250, "receivedPackets": 25, "requests": 10}]}
        indexed = controller["service_traffic_index"](
            [{"data": {"measurement.json": json.dumps(payload)}}], 120, now)
        history = {"a": [{"o": .8, "s": .6, "i": .4}],
                   "b": [{"o": .4, "s": 1.0, "i": .8}]}
        nodes = {"a": {"failureDomain": "zone-a"}, "b": {"failureDomain": "zone-b"}}
        snapshot = controller["service_osi_snapshot"](
            ("headlamp", "headlamp"), indexed[("headlamp", "headlamp")], history, nodes)
        self.assertEqual(snapshot["o"], .7)
        self.assertEqual(snapshot["s"], .7)
        self.assertEqual(snapshot["i"], .75)
        self.assertEqual(snapshot["measurements"]["receivedBytes"], 1000)
        self.assertEqual(snapshot["measurements"]["bytesPerSecond"], 1000 / 60)
        self.assertEqual(snapshot["calculation"]["optimality"]["numerator"], 700)
        document = controller["service_assessment_document"](
            ("headlamp", "headlamp"), snapshot, 120, now)
        self.assertEqual(document["spec"]["subjectRef"]["namespace"], "headlamp")
        self.assertEqual(document["status"]["state"], "Ready")

    def test_service_traffic_does_not_turn_missing_window_into_zero(self):
        self.assertEqual(controller["service_traffic_index"]([], 120, 1_800_000_000), {})

    def test_component_assessment_uses_only_formal_state(self):
        legacy = {"observedAt": "2027-01-15T08:00:00Z", "o": .9, "s": .8, "i": .7}
        formal = {"modelVersion": "networking.re8ch.com/measurement-model-v1alpha1",
                  "observedAt": "2027-01-15T08:00:10Z", "o": .8, "s": None, "i": None,
                  "confidenceO": .7, "confidenceS": 0, "confidenceI": 0}
        result = controller["assessment_document"]({"name": "node-a"}, [legacy, formal],
            {"diagnosis": "evidence-incomplete", "recommendation": "measure"}, 120, 1_800_000_020)
        self.assertEqual(result["status"]["state"], "Partial")
        self.assertEqual(result["status"]["dimensions"]["optimality"], .8)
        self.assertIsNone(result["status"]["dimensions"]["stability"])
        self.assertEqual(result["status"]["confidence"]["optimality"], .7)

    def test_component_assessment_marks_expired_state_stale(self):
        formal = {"modelVersion": "networking.re8ch.com/measurement-model-v1alpha1",
                  "observedAt": "2027-01-15T08:00:00Z", "o": 1, "s": 1, "i": 1}
        result = controller["assessment_document"]({"name": "node-a"}, [formal], {}, 30, 1_800_000_100)
        self.assertEqual(result["status"]["state"], "Stale")

    def test_osi_history_preserves_unknown_dimensions(self):
        node = {"name": "r640"}
        result = controller["append_osi_history"]({}, [node], {"r640": {"observedAt": "2026-09-08T00:00:00Z"}}, {})
        self.assertIsNone(result["r640"][0]["o"])
        self.assertIsNone(result["r640"][0]["s"])
        self.assertIsNone(result["r640"][0]["i"])
        self.assertEqual(result["r640"][0]["confidenceO"], 0)

    def test_optimality_is_relative_to_measured_feasible_alternative(self):
        measurements = {("a", "host"): {"fresh": True, "observedAt": "2026-09-08T00:00:00Z", "paths": [
            {"measurementDefinitionId": "path-quality-v1", "pathRole": "current", "lossRatio": 0, "p95Ms": 20},
            {"measurementDefinitionId": "path-quality-v1", "pathRole": "alternative", "lossRatio": 0, "p95Ms": 10}]}}
        snapshot = controller["osi_snapshot"]({"name": "a"}, {}, measurements)
        self.assertIsNotNone(snapshot["o"])
        self.assertLess(snapshot["o"], 1)
        self.assertIsNone(snapshot["s"])
        self.assertIsNone(snapshot["i"])

    def test_stale_desired_nodes_includes_legacy_entries_absent_from_spec(self):
        stale = controller["stale_desired_nodes"](
            {"r640.json": "{}", "qwen-1.json": "{}", "qwen-2.json": "{}"}, {"r640"})
        self.assertEqual(stale, ["qwen-1", "qwen-2"])

    def test_cluster_membership_filters_retired_static_inventory(self):
        declared = [{"name": name} for name in ("r640", "qwen-1", "overseas-edge-50")]
        actual = [{"metadata": {"name": "r640"}}]
        active, retired = controller["cluster_inventory"](declared, actual)
        self.assertEqual([item["name"] for item in active], ["r640"])
        self.assertEqual(retired, ["overseas-edge-50", "qwen-1"])

    def test_deleting_node_object_removes_it_from_expected_quality_matrix(self):
        declared = [{"name": "r640"}, {"name": "qwen-1"}]
        active, _ = controller["cluster_inventory"](declared, [{"metadata": {"name": "r640"}}])
        result = controller["network_quality"]([], [item["name"] for item in active],
                                               {"minimumCoverageRatio": 1})
        self.assertEqual(result["expectedSources"], 2)
        self.assertEqual(result["expectedPaths"], 4)

    def test_evidence_planner_closes_task_after_collector_reports_it(self):
        now = 1_800_000_000
        nodes = [{"name": "a", "isp": "p1", "asn": 1, "gateway": "g1", "tunnel": "t1", "physicalPath": "fiber-a"},
                 {"name": "b", "isp": "p2", "asn": 2, "gateway": "g2", "tunnel": "t2", "physicalPath": "fiber-b"}]
        actual = [{"metadata": {"name": name}} for name in ("a", "b")]
        first = controller["evidence_plan"](nodes, actual, [], {"freshnessSeconds": 120}, 7, now)
        task = next(item for item in first["tasks"] if item["sourceNode"] == "a" and item["sourcePlane"] == "host")
        payload = {"sourceNode": "a", "sourcePlane": "host", "observedAt":
                   datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
                   "history": {"windowSamples": 3}, "paths": [
                       {"measurementDefinitionId": "path-quality-v1", "pathRole": "current",
                        "targetNode": "b", "targetPlane": "host", "lossRatio": 0, "p95Ms": 10},
                       {"measurementDefinitionId": "path-quality-v1", "pathRole": "alternative", "feasible": True,
                        "targetNode": "b", "targetPlane": "host", "lossRatio": 0, "p95Ms": 9}],
                   "completedTaskIds": [task["id"]]}
        second = controller["evidence_plan"](nodes, actual,
            [{"data": {"result.json": json.dumps(payload)}}], {"freshnessSeconds": 120}, 7, now)
        self.assertIn(task["id"], second["completedTaskIds"])
        self.assertNotIn(task["id"], second["pendingTaskIds"])

    def test_measurement_freshness_tolerates_sampling_duration(self):
        now = 1_800_000_000
        payload = {"sourceNode": "a", "sourcePlane": "host", "observedAt":
                   datetime.datetime.fromtimestamp(now - 240, datetime.timezone.utc).isoformat(),
                   "measurementDurationSeconds": 180, "validitySeconds": 390}
        result = controller["measurement_index"](
            [{"data": {"result.json": json.dumps(payload)}}], {"a"}, 120, now)
        self.assertTrue(result[("a", "host")]["fresh"])
        self.assertEqual(result[("a", "host")]["effectiveValiditySeconds"], 480)

    def test_optimality_does_not_compare_different_destinations(self):
        measurements = {("a", "host"): {"fresh": True, "observedAt": "2026-09-08T00:00:00Z", "paths": [
            {"measurementDefinitionId": "path-quality-v1", "pathRole": "current", "targetNode": "b",
             "targetPlane": "host", "lossRatio": 0, "p95Ms": 20},
            {"measurementDefinitionId": "path-quality-v1", "pathRole": "alternative", "targetNode": "c",
             "targetPlane": "host", "lossRatio": 0, "p95Ms": 10}]}}
        snapshot = controller["osi_snapshot"]({"name": "a"}, {}, measurements)
        self.assertIsNone(snapshot["o"])
        self.assertEqual(snapshot["confidenceO"], 0)

    def test_history_summary_reports_variance_not_freshness(self):
        result = probe["history_summary"]([{"lossRatio": 0, "p95Ms": 10},
                                            {"lossRatio": .5, "p95Ms": 30},
                                            {"lossRatio": 1, "p95Ms": None}])
        self.assertEqual(result["windowSamples"], 3)
        self.assertEqual(result["lossMean"], .5)
        self.assertGreater(result["lossStdDev"], 0)
        self.assertEqual(result["p95StdDevMs"], 10)

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
