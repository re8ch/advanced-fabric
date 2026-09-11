import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/observation-api.py"
SPEC = importlib.util.spec_from_file_location("observation_api", MODULE_PATH)
observation_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observation_api)


def point(timestamp, values):
    return {"observedAt": timestamp, "values": {
        symbol: {"value": value, "unit": "raw"} for symbol, value in values.items()
    }}


def complete_values(offset):
    symbols = set(observation_api.STRUCTURE_SYMBOLS["R"] +
                  observation_api.STRUCTURE_SYMBOLS["D"] +
                  observation_api.STRUCTURE_SYMBOLS["C"])
    return {symbol: index + offset for index, symbol in enumerate(sorted(symbols))}


def test_relationship_series_is_bounded_and_has_three_producer_coordinates():
    history = [point("2026-09-11T00:0%d:00Z" % index, complete_values(index)) for index in range(6)]
    samples = observation_api.relationship_series(history, ["R", "D", "C"], maximum=3)
    assert len(samples) == 3
    assert [sample["time"] for sample in samples] == sorted(sample["time"] for sample in samples)
    for sample in samples:
        assert set(sample["coordinates"]) == {"R", "D", "C"}
        assert all(0 <= value <= 1 for value in sample["coordinates"].values())


def test_relationship_series_prefers_complete_synchronized_slices():
    history = []
    for index in range(10):
        values = complete_values(index)
        if index < 3:
            values.pop("d_mode")
        history.append(point(f"2026-09-11T00:00:{index:02d}Z", values))

    samples = observation_api.relationship_series(history, ["R", "D", "C"], maximum=7)

    assert len(samples) == 7
    assert all(sample["coordinates"]["D"] is not None for sample in samples)


def test_missing_and_right_censored_values_remain_null_not_zero():
    values = complete_values(0)
    values.pop("n_alt")
    samples = observation_api.relationship_series(
        [point("2026-09-11T00:00:00Z", values)], ["R", "D", "C"]
    )
    assert samples[0]["coordinates"]["R"] is None
    assert samples[0]["coordinates"]["D"] is None
    assert samples[0]["evidence"]["R"]["state"] == "Partial"
    assert "n_alt" in samples[0]["evidence"]["D"]["missingSymbols"]


def test_bounded_times_preserves_endpoints_deterministically():
    assert observation_api.bounded_times(range(10), 4) == [0, 3, 6, 9]
    assert observation_api.bounded_times(range(10), 1) == [9]


def test_public_api_has_no_environment_specific_storage_endpoint():
    source = MODULE_PATH.read_text()
    assert "vmselect" not in source
    assert "vmsingle" not in source
    assert "observability-system" not in source
