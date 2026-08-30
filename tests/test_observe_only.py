from pathlib import Path


def test_observe_only_branch_contains_no_mutation_calls():
    script = (Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/host-agent.sh").read_text()
    branch = script.split('if [ "${apply}" != true ]; then', 1)[1].split("else", 1)[0]
    forbidden = ("withdraw_vip", "manage_wireguard", "manage_frr", "manage_forward_rules", "manage_fallback_routes")
    assert not any(command in branch for command in forbidden)


def test_transaction_validation_is_inside_guarded_apply_branch():
    script = (Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/host-agent.sh").read_text()
    before_loop, loop = script.split("while :; do", 1)
    assert "validate_transaction" not in before_loop.split("while [ ! -s", 1)[1]
    guarded = loop.split("else", 1)[1]
    assert guarded.index("validate_transaction") < guarded.index("manage_fallback_routes apply")
