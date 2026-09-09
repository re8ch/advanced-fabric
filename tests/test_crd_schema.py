from pathlib import Path

import yaml


CRDS = Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/crds/advanced-fabric.yaml"


def test_list_map_keys_are_declared_item_properties():
    for document in yaml.safe_load_all(CRDS.read_text()):
        if not document:
            continue
        for version in document.get("spec", {}).get("versions", []):
            _assert_list_map_keys(version.get("schema", {}).get("openAPIV3Schema", {}))


def _assert_list_map_keys(schema):
    if not isinstance(schema, dict):
        return
    if schema.get("x-kubernetes-list-type") == "map":
        item_properties = schema.get("items", {}).get("properties", {})
        for key in schema.get("x-kubernetes-list-map-keys", []):
            assert key in item_properties
    for value in schema.values():
        if isinstance(value, dict):
            _assert_list_map_keys(value)
        elif isinstance(value, list):
            for item in value:
                _assert_list_map_keys(item)
