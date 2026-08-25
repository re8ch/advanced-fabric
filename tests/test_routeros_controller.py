import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import mock_open, patch


CONTROLLER = Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/routeros-controller.py"


def load_controller():
    spec = importlib.util.spec_from_file_location("routeros_controller", CONTROLLER)
    module = importlib.util.module_from_spec(spec)
    with patch("builtins.open", mock_open(read_data="token")), patch("ssl.create_default_context"):
        # Do not execute the controller loop when importing helpers.
        source = CONTROLLER.read_text().split("while True:", 1)[0]
        exec(compile(source, str(CONTROLLER), "exec"), module.__dict__)
    return module


class RouterOSControllerTest(TestCase):
    def setUp(self):
        self.module = load_controller()
        self.spec = {
            "nodeRef": "r640",
            "routerAddress": "192.168.88.1",
            "routerASN": 4200064001,
            "mode": "Observe",
            "continuity": {"operatorWitness": "macmini", "mainDefaultGateway": "223.159.210.221",
                           "mainDefaultInterface": "ether4", "requireExternalProbe": True},
            "fallbackNextHop": "192.168.88.251",
            "protectedPrefixes": ["0.0.0.0/0", "192.168.88.0/24", "10.181.22.0/24"],
            "routeDomains": [{"name": "re8ch-service-fast", "fib": True,
                              "prefixes": ["10.42.0.0/16", "10.251.0.0/24"], "fallbackTable": "main"}],
            "publicVIPs": [{"cidr": "10.251.0.4/32", "owner": "r640", "fallbackDistance": 250,
                            "healthAuthority": "GatewayServiceProbe"}],
            "peers": [
                {"name": "r640", "role": "r640-public-origin", "address": "192.168.88.251/32",
                 "remoteASN": 4200000128, "localAddress": "192.168.88.1",
                 "acceptedPrefixes": ["10.42.6.0/24", "10.251.0.4/32"], "maxPrefixes": 64,
                 "routeDomain": "re8ch-service-fast", "failurePolicy": "WithdrawPeerOnly", "useBFD": True},
                {"name": "b1", "role": "cluster-worker", "address": "10.181.22.125/32",
                 "remoteASN": 4200010125, "localAddress": "10.181.22.128",
                 "acceptedPrefixes": ["10.42.13.0/24"], "maxPrefixes": 64,
                 "routeDomain": "re8ch-service-fast", "failurePolicy": "WithdrawPeerOnly"},
            ],
        }

    def test_valid_intent_generates_fast_table_and_main_fallback(self):
        self.assertEqual([], self.module.validate(self.spec))
        transaction, checksum = self.module.desired(self.spec)
        self.assertEqual(64, len(checksum))
        self.assertEqual("re8ch-service-fast", transaction["connections"][0]["routing-table"])
        self.assertEqual("main", transaction["fallbackRoutes"][0]["routing-table"])
        self.assertEqual("192.168.88.251", transaction["fallbackRoutes"][0]["gateway"])

    def test_non_origin_cannot_originate_public_vip(self):
        self.spec["peers"][1]["acceptedPrefixes"].append("10.251.0.4/32")
        errors = self.module.validate(self.spec)
        self.assertTrue(any("may not originate" in item for item in errors))

    def test_protected_prefix_is_rejected(self):
        self.spec["peers"][0]["acceptedPrefixes"].append("192.168.88.0/24")
        errors = self.module.validate(self.spec)
        self.assertTrue(any("protected prefix" in item for item in errors))

    def test_checksum_is_stable(self):
        first = self.module.desired(self.spec)[1]
        second = self.module.desired(self.spec)[1]
        self.assertEqual(first, second)

    def test_observe_reports_main_bgp_and_doh_without_mutating(self):
        transaction = self.module.desired(self.spec)[0]

        class FakeRouter:
            def request(self, path):
                return {
                    "/routing/table": [{"name": "main"}],
                    "/routing/rule": [],
                    "/routing/filter/rule": [],
                    "/routing/bgp/connection": [],
                    "/ip/route": [
                        {"dst-address": "0.0.0.0/0", "routing-table": "main", "active": "true",
                         "gateway": "223.159.210.221", "immediate-gw": "223.159.210.221%ether4"},
                        {"dst-address": "10.42.6.0/24", "routing-table": "main", "dynamic": "true", "bgp": "true"},
                    ],
                    "/ip/dns": {"use-doh-server": "https://example.invalid/dns-query", "servers": "1.1.1.1"},
                    "/ip/firewall/nat": [],
                    "/ip/firewall/mangle": [],
                }[path]

        result = self.module.observe(FakeRouter(), transaction)
        self.assertFalse(result["invariants"]["mainBgpForbidden"])
        self.assertTrue(result["invariants"]["defaultRoutePresent"])
        self.assertTrue(result["invariants"]["defaultRouteUnchanged"])
        self.assertTrue(result["invariants"]["dohSoleResolver"])
