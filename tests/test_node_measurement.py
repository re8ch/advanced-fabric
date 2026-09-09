import ast
import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "charts/re8ch-advanced-fabric/files/node-measurement.py"


def load_builder():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"flatten_objects", "observed", "partial", "unavailable", "build_snapshot"}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "datetime": datetime,
        "time": __import__("time"),
        "NODE": "node-a",
        "SYMBOLS": ["x_nh", "p_route", "w_ecmp", "m_route", "n_path_change", "d_mode", "a_reach",
                    "l_path", "t_rtt", "b_rx", "b_est", "n_peer", "n_adv", "n_recv", "u_bgp", "w_bgp",
                    "t_conv", "lambda_flap", "delta_ribfib", "n_nh", "n_if", "n_tun", "n_gw", "n_asn",
                    "g_dep", "n_alt", "t_state", "t_persist", "f_switch", "t_recover", "a_osc"],
        "EXPERIMENT_ONLY": {"t_conv", "t_recover", "t_persist"},
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["build_snapshot"]


def test_complete_envelope_preserves_missing_experiment_evidence():
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
