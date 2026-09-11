import ast
import datetime
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "charts/re8ch-advanced-fabric/files/node-measurement.py"


def load_builder():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"flatten_objects", "observed", "partial", "unavailable", "right_censored", "service_traffic_value", "fingerprint", "path_fingerprint", "probe_loss",
             "advance_episode", "counter_totals", "directional_prefix_counts", "tracking_value", "build_snapshot"}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "datetime": datetime,
        "time": __import__("time"),
        "NODE": "node-a",
        "SYMBOLS": ["x_nh", "p_route", "w_ecmp", "m_route", "n_path_change", "d_mode", "a_reach",
                    "l_path", "t_rtt", "b_rx", "b_est", "n_peer", "n_adv", "n_recv", "u_bgp", "w_bgp",
                    "t_conv", "lambda_flap", "delta_ribfib", "n_nh", "n_if", "n_tun", "n_gw", "n_asn",
                    "g_dep", "n_alt", "t_state", "t_persist", "f_switch", "t_recover", "a_osc"],
        "hashlib": __import__("hashlib"), "json": __import__("json"),
        "EPISODE_ONLY": {"t_conv", "t_recover", "t_persist"},
        "TRACKING_UNITS": {symbol: "unit" for symbol in ["x_nh", "p_route", "w_ecmp", "m_route",
            "n_path_change", "d_mode", "a_reach", "l_path", "t_rtt", "b_rx", "b_est", "n_peer",
            "n_adv", "n_recv", "u_bgp", "w_bgp", "t_conv", "lambda_flap", "delta_ribfib", "n_nh",
            "n_if", "n_tun", "n_gw", "n_asn", "g_dep", "n_alt", "t_state", "t_persist", "f_switch",
            "t_recover", "a_osc"]},
        "FRESHNESS_SECONDS": 120, "HORIZON_SECONDS": 90 * 86400,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["build_snapshot"]


def load_tracking_value():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and
                node.name == "tracking_value"]
    namespace = {"datetime": datetime, "time": __import__("time")}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["tracking_value"]


def load_gateway_helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"parse_envoy_ingress_counters", "gateway_counter_window"}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"datetime": datetime, "re": re, "NODE": "node-a"}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def test_envoy_ingress_counter_parser_uses_real_downstream_families_only():
    parse = load_gateway_helpers()["parse_envoy_ingress_counters"]
    counters = parse("""
envoy_http_downstream_cx_rx_bytes_total{envoy_http_conn_manager_prefix="http"} 100
envoy_http_downstream_cx_rx_bytes_total{envoy_http_conn_manager_prefix="admin"} 300
envoy_tcp_access_re8ch_com_downstream_cx_rx_bytes_total 25
envoy_http_downstream_cx_rx_bytes_buffered 999
envoy_cluster_upstream_cx_rx_bytes_total{envoy_cluster_name="backend"} 800
envoy_cluster_upstream_cx_tx_bytes_total{envoy_cluster_name="backend"} 900
""")
    assert counters == {
        'envoy_http_downstream_cx_rx_bytes_total{envoy_http_conn_manager_prefix="http"}': 100.0,
        "envoy_tcp_access_re8ch_com_downstream_cx_rx_bytes_total": 25.0,
    }


def test_gateway_counter_window_is_reset_safe_and_accepts_measured_zero():
    window = load_gateway_helpers()["gateway_counter_window"]
    state = {}
    assert window({"envoy_http_downstream_cx_rx_bytes_total": 10}, state, 100) == []
    result = window({"envoy_http_downstream_cx_rx_bytes_total": 10}, state, 115)
    assert result[0]["receivedBytes"] == 0
    assert result[0]["windowSeconds"] == 15
    assert result[0]["availableDimensions"] == ["bytes"]
    assert window({"envoy_http_downstream_cx_rx_bytes_total": 2}, state, 130) == []


def test_service_traffic_adapter_discovery_is_cluster_scoped():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'api("GET", "/api/v1/configmaps?labelSelector="' in source
    assert '"/api/v1/namespaces/%s/configmaps?labelSelector=" % NAMESPACE' not in source


def test_hubble_collector_addresses_host_run_socket_without_symlink_escape():
    script = (ROOT / "charts/re8ch-advanced-fabric/files/service-traffic-window.sh").read_text()
    assert "SOCKET=/host/run/cilium/hubble.sock" in script
    assert "SOCKET=/host/var/run/cilium/hubble.sock" not in script


def test_hubble_only_window_does_not_fabricate_zero_bytes():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "service_traffic_value")
    namespace = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), "exec"), namespace)
    value = namespace["service_traffic_value"]([{
        "receivedFlowEvents": 0, "requests": 0,
        "availableDimensions": ["flowEvents", "requests"],
        "observationPlane": "hubble", "evidenceRef": "local-hubble-window",
    }])
    assert "bytes" not in value
    assert value["flowEvents"] == 0
    assert value["requests"] == 0


def test_complete_envelope_right_censors_quiet_episode_measurements():
    build = load_builder()
    status = {
        "observedAt": "2026-09-09T00:00:00Z",
        "datapath": {"mode": "native", "tunnelInterfaces": []},
        "frr": {"bgp": {"peers": {"p": {"state": "Established"}}}, "neighbors": {}},
        "routes": [{"dst": "default", "dev": "eth0"}],
        "bgpRib": [{"prefix": "0.0.0.0/0", "paths": [{"best": True, "nextHops": ["192.0.2.1"]}]}],
        "peerRoutes": [{"name": "p", "asn": 64512}],
        "ecmpRoutes": [],
        "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z", "bgpChanges": 1, "routeChanges": 2},
    }
    probe = {"observedAt": "2026-09-09T00:00:01Z", "paths": [{"sourcePlane": "host",
             "targetNode": "node-b", "targetPlane": "host", "attempts": 3, "successes": 3,
             "lossRatio": 0, "p50Ms": 2, "p95Ms": 3}],
             "history": {"lossStdDev": 0, "p95StdDevMs": 0.2, "windowSeconds": 60}}
    result = build(status, [probe], datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp() + 2)
    records = {item["symbol"]: item for item in result["measurements"]}
    assert result["envelopeComplete"] is True
    assert result["coverage"]["total"] == 31
    assert len(records) == 31
    assert records["a_reach"]["value"] == {"attempts": 3, "successes": 3}
    assert records["x_nh"]["value"][0]["nextHops"] == ["192.0.2.1"]
    assert records["t_conv"]["state"] == "observed"
    assert records["t_conv"]["observationStatus"] == "right-censored"
    assert records["t_conv"]["value"] is None
    assert records["t_conv"]["censoring"]["eventObserved"] is False
    assert records["b_rx"]["state"] == "not-observed"
    assert result["trackingReady"] is False
    assert {"t_conv", "t_persist", "t_recover"}.isdisjoint(result["trackingGate"]["missingSymbols"])
    assert "b_rx" in result["trackingGate"]["missingSymbols"]


def test_unreachable_next_hops_are_observed_zero_when_every_probe_ran():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {
        "observedAt": "2026-09-09T00:00:00Z",
        "datapath": {"mode": "native"},
        "frr": {"state": "active", "neighbors": {}, "bgp": {}},
        "routes": [],
        "bgpRib": [{"prefix": "192.0.2.0/24", "paths": [
            {"best": True, "nextHops": ["192.0.2.1", "192.0.2.2"]}
        ]}],
        "nextHopProbes": [
            {"address": "192.0.2.1", "reachable": False, "routeDev": "eth0"},
            {"address": "192.0.2.2", "reachable": False, "routeDev": None},
        ],
        "peerRoutes": [],
        "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"},
    }
    result = build(status, [], now)
    record = {item["symbol"]: item for item in result["measurements"]}["n_nh"]
    assert record["state"] == "observed"
    assert record["value"] == 0
    assert record["scope"]["candidateCount"] == 2


def test_on_link_next_hop_sentinels_do_not_require_active_probe():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {
        "observedAt": "2026-09-09T00:00:00Z",
        "datapath": {"mode": "native"},
        "frr": {"state": "active", "neighbors": {}, "bgp": {}},
        "routes": [],
        "bgpRib": [{"prefix": "192.0.2.0/24", "paths": [
            {"best": True, "nextHops": ["0.0.0.0", "::"]}
        ]}],
        "nextHopProbes": [],
        "peerRoutes": [],
        "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"},
    }
    record = {item["symbol"]: item for item in build(status, [], now)["measurements"]}["n_nh"]
    assert record["state"] == "observed"
    assert record["value"] == 0
    assert record["scope"]["candidateCount"] == 0


def test_missing_next_hop_probe_attempt_still_blocks_validity():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {
        "observedAt": "2026-09-09T00:00:00Z",
        "datapath": {"mode": "native"},
        "frr": {"state": "active", "neighbors": {}, "bgp": {}},
        "routes": [],
        "bgpRib": [{"prefix": "192.0.2.0/24", "paths": [
            {"best": True, "nextHops": ["192.0.2.1", "192.0.2.2"]}
        ]}],
        "nextHopProbes": [{"address": "192.0.2.1", "reachable": False, "routeDev": "eth0"}],
        "peerRoutes": [],
        "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"},
    }
    record = {item["symbol"]: item for item in build(status, [], now)["measurements"]}["n_nh"]
    assert record["state"] == "not-observed"


def test_quiet_but_fully_instrumented_node_closes_tracking_gate():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {
        "observedAt": "2026-09-09T00:00:00Z",
        "datapath": {"mode": "native", "tunnelInterfaces": []},
        "frr": {
            "state": "active",
            "bgp": {"peers": {"p": {"state": "Established"}}},
            "neighbors": {"p": {
                "messageStats": {"updatesSent": 12, "updatesRecv": 3},
                "prefixStats": {"pfxSnt": 4, "pfxRcd": 5, "withdrawn": 2},
            }},
        },
        "routes": [{"dst": "default", "dev": "eth0"}],
        "bgpRib": [{"prefix": "192.0.2.0/24", "paths": [{
            "best": True, "nextHops": ["192.0.2.1"], "asPath": "64512",
        }]}],
        "nextHopProbes": [{"address": "192.0.2.1", "reachable": False, "routeDev": "eth0"}],
        "peerRoutes": [{"name": "p", "asn": 64512, "provider": "test", "failureDomain": "fd-a"}],
        "ecmpRoutes": [],
        "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z", "bgpChanges": 0, "routeChanges": 0},
    }
    probe = {
        "observedAt": "2026-09-09T00:00:00Z",
        "paths": [{"sourcePlane": "host", "targetNode": "node-b", "targetPlane": "host",
                   "pathRole": "alternative", "feasible": True, "attempts": 2, "successes": 2,
                   "lossRatio": 0, "p50Ms": 1, "p95Ms": 2}],
        "history": {"lossStdDev": 0, "p95StdDevMs": 0.1, "windowSeconds": 60},
    }
    collector_state = {
        "counterEpoch": "2026-09-08T23:59:00Z",
        "previousCounters": {"updates": 15, "withdrawals": 2},
        "observationStartedEpoch": now - 3600,
    }
    traffic = [{"observedAt": "2026-09-09T00:00:00Z", "receivedBytes": 0,
                "receivedPackets": 0, "requests": 0}]
    result = build(status, [probe], now, state=collector_state, service_traffic=traffic)
    records = {item["symbol"]: item for item in result["measurements"]}
    assert result["trackingReady"] is True
    assert result["trackingGate"]["missingSymbols"] == []
    assert result["trackingGate"]["observed"] == 31
    assert records["n_nh"]["value"] == 0
    assert records["t_conv"]["observationStatus"] == "right-censored"
    assert "t_conv" not in result["trackingValues"]


def test_counter_reset_never_becomes_observed_delta():
    build = load_builder()
    status = {"observedAt": "2026-09-09T00:00:00Z", "datapath": {"mode": "native"},
              "frr": {"neighbors": {"p": {"messageStats": {"updatesSent": 1},
                       "prefixStats": {"withdrawn": 1}}}, "bgp": {}}, "routes": [], "bgpRib": [],
              "peerRoutes": [], "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"}}
    result = build(status, [], datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp(),
                   state={"previousCounters": {"updates": 10, "withdrawals": 4}})
    records = {item["symbol"]: item for item in result["measurements"]}
    assert records["u_bgp"]["state"] == "not-observed"
    assert records["w_bgp"]["state"] == "not-observed"


def test_datapath_mode_is_projected_by_the_producer():
    tracking_value = load_tracking_value()
    assert tracking_value("d_mode", {"mode": "tunnel"}) == 0.0
    assert tracking_value("d_mode", {"mode": "hybrid"}) == 0.5
    assert tracking_value("d_mode", {"mode": "native"}) == 1.0
    assert tracking_value("d_mode", {"mode": "unknown"}) is None
    assert tracking_value("d_mode", {}) is None


def test_counter_delta_requires_same_collector_epoch():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {"observedAt": "2026-09-09T00:00:00Z", "datapath": {"mode": "native"},
              "frr": {"neighbors": {"p": {"messageStats": {"updatesSent": 12},
                       "prefixStats": {"withdrawn": 5}}}, "bgp": {}}, "routes": [], "bgpRib": [],
              "peerRoutes": [], "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"}}
    same = build(status, [], now, state={"counterEpoch": "2026-09-08T23:59:00Z",
                 "previousCounters": {"updates": 10, "withdrawals": 4}})
    records = {item["symbol"]: item for item in same["measurements"]}
    assert records["u_bgp"]["value"]["windowUpdates"] == 2
    assert records["w_bgp"]["value"]["windowWithdrawals"] == 1
    restarted = build(status, [], now, state={"counterEpoch": "2026-09-08T23:58:00Z",
                      "previousCounters": {"updates": 10, "withdrawals": 4}})
    assert {item["symbol"]: item for item in restarted["measurements"]}["u_bgp"]["state"] == "not-observed"


def test_stale_and_unverified_topology_keep_gate_closed():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    status = {"observedAt": "2026-09-08T23:50:00Z", "datapath": {"mode": "native"},
              "frr": {"neighbors": {}, "bgp": {}}, "routes": [], "bgpRib": [], "peerRoutes": [],
              "routeDynamics": {"startedAt": "2026-09-08T23:00:00Z"}}
    result = build(status, [], now)
    records = {item["symbol"]: item for item in result["measurements"]}
    assert records["n_if"]["state"] == "partial"
    assert records["n_nh"]["state"] == "not-observed"
    assert result["trackingReady"] is False


def test_natural_episode_completes_after_stable_recovered_samples():
    build = load_builder()
    now = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()
    base = {"observedAt": "2026-09-09T00:00:00Z", "datapath": {"mode": "native"},
            "frr": {"neighbors": {}, "bgp": {}}, "routes": [{"dst": "default", "dev": "eth0"}],
            "bgpRib": [], "peerRoutes": [], "routeDynamics": {"startedAt": "2026-09-08T23:59:00Z"}}
    probe = {"observedAt": "2026-09-09T00:00:00Z", "paths": [{"attempts": 1, "successes": 1}]}
    first = build(base, [probe], now, state={})
    changed = {**base, "routes": [{"dst": "default", "dev": "eth1"}]}
    second = build(changed, [probe], now + 15, state=first["collectorState"])
    third = build(changed, [probe], now + 30, state=second["collectorState"])
    fourth = build(changed, [probe], now + 45, state=third["collectorState"])
    records = {item["symbol"]: item for item in fourth["measurements"]}
    assert fourth["latestEpisode"]["state"] == "Complete"
    assert records["t_conv"]["state"] == "observed"
    assert records["t_recover"]["value"] == 30
