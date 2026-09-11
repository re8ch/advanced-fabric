import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_function(name):
    path = ROOT / "charts/re8ch-advanced-fabric/files/controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


control_plane_apply_safety = load_function("control_plane_apply_safety")


def api(guarded=("a1", "b1", "r640"), operations=("a1", "b1", "r640")):
    return {"guardedNodes": list(guarded), "nodeOperations": [{"name": name} for name in operations]}


def test_unrelated_unavailable_spine_does_not_block_control_plane_transaction():
    nodes = {name: {"inventoryComplete": True} for name in ("a1", "b1", "r640", "edge")}
    safe, blockers = control_plane_apply_safety(
        api(), nodes, {"a1": True, "b1": True, "r640": True, "edge": False}, True)
    assert safe
    assert blockers == []


def test_participant_must_be_active_inventoried_and_ready():
    nodes = {"a1": {"inventoryComplete": True}, "b1": {"inventoryComplete": False}}
    safe, blockers = control_plane_apply_safety(
        api(), nodes, {"a1": True, "b1": True, "r640": False}, True)
    assert not safe
    assert blockers == ["b1:inventory-incomplete", "r640:inactive"]


def test_enforced_network_quality_failure_blocks_transaction():
    nodes = {name: {"inventoryComplete": True} for name in ("a1", "b1", "r640")}
    safe, blockers = control_plane_apply_safety(
        api(), nodes, {"a1": True, "b1": True, "r640": True}, False)
    assert not safe
    assert blockers == ["network-quality"]
