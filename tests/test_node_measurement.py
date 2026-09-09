import ast
import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "charts/re8ch-advanced-fabric/files/node-measurement.py"


def load_builder():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"flatten_objects", "observed", "partial", "unavailable", "fingerprint", "path_fingerprint", "probe_loss",
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


def test_complete_envelope_blocks_tracking_without_service_and_episode_evidence():
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
    assert records["t_conv"]["state"] == "not-observed"
    assert records["t_conv"]["value"] is None
    assert records["b_rx"]["state"] == "not-observed"
    assert result["trackingReady"] is False
    assert {"b_rx", "t_conv", "t_persist", "t_recover"}.issubset(result["trackingGate"]["missingSymbols"])


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
