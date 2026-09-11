import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_make_api_transaction():
    path = ROOT / "charts/re8ch-advanced-fabric/files/controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "make_api_transaction")
    namespace = {"hashlib": hashlib, "json": json}
    exec(compile(ast.Module(body=[selected], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["make_api_transaction"]


def test_source_identity_rules_are_checksum_bound_to_host_transaction():
    rule = {"source": "10.250.0.147", "destination": "10.250.0.0/24", "protocol": "tcp", "port": 2380}
    transaction = load_make_api_transaction()(
        "a1-wsl-zt", {"vip": "10.250.0.1/32"}, {"sourceIdentityRules": [rule]}, True
    )
    assert transaction["spec"]["sourceIdentityRules"] == [rule]
    canonical = json.dumps(transaction["spec"], sort_keys=True, separators=(",", ":"))
    assert transaction["checksum"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_host_agent_applies_source_identity_after_routes_and_before_vip_health():
    script = (ROOT / "charts/re8ch-advanced-fabric/files/host-agent.sh").read_text(encoding="utf-8")
    assert "iptables -t nat -C POSTROUTING" in script
    assert "--to-source" in script
    apply = script.split('if [ "${apply}" != true ]; then', 1)[1].split("if [ \"${guarded}\"", 1)[0]
    assert apply.index("manage_fallback_routes apply") < apply.index("manage_source_identity_rules apply")
