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


def test_controller_api_access_does_not_depend_on_accelerated_vip():
    template = (Path(__file__).parents[1] /
                "charts/re8ch-advanced-fabric/templates/advanced-fabric-runtime.yaml").read_text()
    assert 'name: API_HOST' not in template
    assert '10.250.0.1' not in template


def test_host_collector_uses_frr_compatible_neighbor_json_command():
    script = (Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/host-agent.sh").read_text()
    assert "show bgp neighbors json" in script
    assert "show bgp ipv4 unicast neighbors json brief" not in script
