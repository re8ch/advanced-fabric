import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "charts/re8ch-advanced-fabric/files/virtual-ingress-controller.py"
spec = importlib.util.spec_from_file_location("virtual_ingress", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def ingress(annotations=None, path_type="Prefix"):
    return {"metadata": {"name": "studio", "namespace": "supabase", "uid": "uid-1",
                         "annotations": annotations or {}},
            "spec": {"ingressClassName": "advanced-fabric", "rules": [{"host": "supabase.example.com",
                      "http": {"paths": [{"path": "/", "pathType": path_type, "backend": {"service": {
                          "name": "kong", "port": {"number": 8000}}}}]}}]}}


class VirtualIngressTest(unittest.TestCase):
    def test_translates_standard_ingress_to_owned_httproute(self):
        route = module.translate(ingress({"traefik.ingress.kubernetes.io/router.tls": "true"}))
        self.assertEqual(route["metadata"]["ownerReferences"][0]["uid"], "uid-1")
        self.assertEqual(route["spec"]["parentRefs"][0]["name"], "re8ch-gateway-canary")
        self.assertEqual(route["spec"]["hostnames"], ["supabase.example.com"])
        self.assertEqual(route["spec"]["rules"][0]["backendRefs"], [{"name": "kong", "port": 8000}])

    def test_rejects_traefik_middleware_instead_of_silently_changing_behavior(self):
        with self.assertRaisesRegex(ValueError, "unsupported Traefik annotations"):
            module.translate(ingress({"traefik.ingress.kubernetes.io/router.middlewares": "auth@kubernetescrd"}))

    def test_rejects_implementation_specific_path(self):
        with self.assertRaisesRegex(ValueError, "ImplementationSpecific"):
            module.translate(ingress(path_type="ImplementationSpecific"))

    def test_route_ready_requires_accepted_and_resolved_refs_for_current_generation(self):
        route = {"metadata": {"generation": 2}, "status": {"parents": [{"conditions": [
            {"type": "Accepted", "status": "True", "observedGeneration": 2},
            {"type": "ResolvedRefs", "status": "True", "observedGeneration": 2}]}]}}
        self.assertTrue(module.route_ready(route))
        route["status"]["parents"][0]["conditions"][1]["status"] = "False"
        self.assertFalse(module.route_ready(route))
