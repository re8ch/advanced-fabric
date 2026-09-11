#!/usr/bin/env python3
"""Translate explicitly opted-in legacy Ingress objects to Gateway API HTTPRoutes."""

import json
import copy
import os
import ssl
import time
import urllib.error
import urllib.request


CLASS = os.environ.get("INGRESS_CLASS", "advanced-fabric")
GATEWAY_NAME = os.environ.get("GATEWAY_NAME", "re8ch-gateway-canary")
GATEWAY_NAMESPACE = os.environ.get("GATEWAY_NAMESPACE", "kube-system")
DEFAULT_SECTION = os.environ.get("GATEWAY_SECTION", "http")
PUBLIC_ADDRESS = os.environ.get("PUBLIC_ADDRESS", "")
INTERVAL = int(os.environ.get("RECONCILE_INTERVAL_SECONDS", "15"))
ADOPT_SOURCE_CLASS = os.environ.get("ADOPT_SOURCE_CLASS", "")
ADOPT_ENABLED = os.environ.get("ADOPT_ENABLED", "false").lower() == "true"
EXCLUSIONS = set(filter(None, os.environ.get("ADOPT_EXCLUSIONS", "").split(",")))
HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
PORT = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
BASE = f"https://{HOST}:{PORT}"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
MANAGED_LABEL = "networking.advfab.org/virtual-ingress"
SOURCE_ANNOTATION = "networking.advfab.org/source-ingress-uid"
STATUS_ANNOTATION = "networking.advfab.org/virtual-ingress-status"


def request(method, path, body=None):
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, context=ssl.create_default_context(cafile=CA_PATH), timeout=15) as response:
        if response.status == 204:
            return {}
        return json.load(response)


def route_name(ingress):
    return (ingress["metadata"]["name"] + "-advfab")[:63].rstrip("-")


def normalize_named_ports(ingress):
    normalized = copy.deepcopy(ingress)
    namespace = normalized["metadata"]["namespace"]
    cache = {}
    for rule in normalized.get("spec", {}).get("rules", []):
        for path in rule.get("http", {}).get("paths", []):
            service = path.get("backend", {}).get("service", {})
            port = service.get("port", {})
            if "name" not in port:
                continue
            name = service.get("name")
            if name not in cache:
                cache[name] = request("GET", f"/api/v1/namespaces/{namespace}/services/{name}")
            matches = [item["port"] for item in cache[name].get("spec", {}).get("ports", [])
                       if item.get("name") == port["name"]]
            if len(matches) != 1:
                raise ValueError(f"Service {name} does not expose one port named {port['name']}")
            service["port"] = {"number": matches[0]}
    return normalized


def listener_matches(listener_host, route_host):
    if not listener_host:
        return True
    if listener_host.startswith("*."):
        return route_host.endswith(listener_host[1:]) and route_host.count(".") == listener_host.count(".")
    return listener_host == route_host


def gateway_parent_refs(gateway, hostnames, explicit_section=""):
    sections = []
    for listener in gateway.get("spec", {}).get("listeners", []):
        name = listener.get("name")
        if explicit_section and name != explicit_section:
            continue
        if listener.get("protocol") not in {"HTTP", "HTTPS"}:
            continue
        if any(listener_matches(listener.get("hostname"), host) for host in hostnames):
            sections.append(name)
    if explicit_section and not sections:
        raise ValueError(f"Gateway listener {explicit_section} does not match any Ingress hostname")
    if not sections:
        raise ValueError("no HTTP/HTTPS Gateway listener matches the Ingress hostnames")
    return [{"name": GATEWAY_NAME, "namespace": GATEWAY_NAMESPACE, "sectionName": name}
            for name in sorted(set(sections))]


def translate(ingress):
    """Return an owned HTTPRoute or reject semantics we cannot preserve."""
    meta, spec = ingress.get("metadata", {}), ingress.get("spec", {})
    annotations = meta.get("annotations", {})
    unsupported = sorted(key for key in annotations if key.startswith("traefik.ingress.kubernetes.io/") and
                         key not in {"traefik.ingress.kubernetes.io/router.entrypoints",
                                     "traefik.ingress.kubernetes.io/router.tls"})
    if unsupported:
        raise ValueError("unsupported Traefik annotations: " + ",".join(unsupported))
    if spec.get("defaultBackend"):
        raise ValueError("defaultBackend is not supported")
    rules, hostnames = [], []
    for ingress_rule in spec.get("rules", []):
        host = ingress_rule.get("host")
        if not host:
            raise ValueError("hostless rules are not supported")
        hostnames.append(host)
        for path in ingress_rule.get("http", {}).get("paths", []):
            backend = path.get("backend", {}).get("service", {})
            port = backend.get("port", {})
            if not backend.get("name") or "number" not in port:
                raise ValueError("every backend must reference a Service and numeric port")
            path_type = path.get("pathType", "Prefix")
            value = path.get("path") or "/"
            if path_type == "ImplementationSpecific":
                if not value.startswith("/") or any(char in value for char in "()[]{}*+?|"):
                    raise ValueError("ImplementationSpecific path is not a literal prefix")
                path_type = "Prefix"
            if path_type not in {"Prefix", "Exact"}:
                raise ValueError(f"unsupported pathType {path_type}")
            match_type = "PathPrefix" if path_type == "Prefix" else "Exact"
            rules.append({"matches": [{"path": {"type": match_type, "value": value}}],
                          "backendRefs": [{"name": backend["name"], "port": port.get("number", port.get("name"))}]})
    if not rules:
        raise ValueError("at least one HTTP path is required")
    section = annotations.get("networking.advfab.org/gateway-section", DEFAULT_SECTION)
    return {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute",
            "metadata": {"name": route_name(ingress), "namespace": meta["namespace"],
                         "labels": {MANAGED_LABEL: "true"},
                         "annotations": {SOURCE_ANNOTATION: meta["uid"]},
                         "ownerReferences": [{"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
                                              "name": meta["name"], "uid": meta["uid"],
                                              "controller": True, "blockOwnerDeletion": True}]},
            "spec": {"parentRefs": [{"name": GATEWAY_NAME, "namespace": GATEWAY_NAMESPACE,
                                       "sectionName": section}],
                     "hostnames": sorted(set(hostnames)), "rules": rules}}


def set_ingress_state(ingress, state, message):
    namespace, name = ingress["metadata"]["namespace"], ingress["metadata"]["name"]
    patch = {"metadata": {"annotations": {STATUS_ANNOTATION: f"{state}: {message[:512]}"}}}
    request("PATCH", f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses/{name}", patch)
    if state == "Ready" and PUBLIC_ADDRESS:
        request("PATCH", f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses/{name}/status",
                {"status": {"loadBalancer": {"ingress": [{"ip": PUBLIC_ADDRESS}]}}})


def route_ready(route):
    generation = route.get("metadata", {}).get("generation")
    parents = route.get("status", {}).get("parents", [])
    for parent in parents:
        conditions = {item.get("type"): item for item in parent.get("conditions", [])
                      if item.get("observedGeneration") in (None, generation)}
        if (conditions.get("Accepted", {}).get("status") == "True" and
                conditions.get("ResolvedRefs", {}).get("status") == "True"):
            return True
    return False


def upsert_route(route):
    namespace, name = route["metadata"]["namespace"], route["metadata"]["name"]
    path = f"/apis/gateway.networking.k8s.io/v1/namespaces/{namespace}/httproutes/{name}"
    try:
        existing = request("GET", path)
        if existing.get("metadata", {}).get("labels", {}).get(MANAGED_LABEL) != "true":
            raise ValueError(f"HTTPRoute {namespace}/{name} exists and is not owned by this controller")
        return request("PATCH", path, route)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return request("POST", f"/apis/gateway.networking.k8s.io/v1/namespaces/{namespace}/httproutes", route)


def reconcile():
    ingresses = request("GET", "/apis/networking.k8s.io/v1/ingresses").get("items", [])
    gateway = request("GET", f"/apis/gateway.networking.k8s.io/v1/namespaces/{GATEWAY_NAMESPACE}/gateways/{GATEWAY_NAME}")
    selected = [item for item in ingresses if item.get("spec", {}).get("ingressClassName") == CLASS or
                (ADOPT_ENABLED and item.get("spec", {}).get("ingressClassName") == ADOPT_SOURCE_CLASS)]
    for ingress in selected:
        identity = f"{ingress['metadata']['namespace']}/{ingress['metadata']['name']}"
        if identity in EXCLUSIONS:
            continue
        try:
            route = translate(normalize_named_ports(ingress))
            explicit = ingress.get("metadata", {}).get("annotations", {}).get("networking.advfab.org/gateway-section", "")
            route["spec"]["parentRefs"] = gateway_parent_refs(gateway, route["spec"]["hostnames"], explicit)
            observed = upsert_route(route)
            if route_ready(observed):
                if ingress.get("spec", {}).get("ingressClassName") != CLASS:
                    request("PATCH", f"/apis/networking.k8s.io/v1/namespaces/{ingress['metadata']['namespace']}/ingresses/{ingress['metadata']['name']}",
                            {"metadata": {"annotations": {"networking.advfab.org/adopted-from-class": ADOPT_SOURCE_CLASS}},
                             "spec": {"ingressClassName": CLASS}})
                set_ingress_state(ingress, "Ready", f"HTTPRoute {route['metadata']['name']} accepted")
            else:
                set_ingress_state(ingress, "Pending", f"HTTPRoute {route['metadata']['name']} awaits Gateway acceptance")
        except Exception as exc:
            set_ingress_state(ingress, "Rejected", str(exc))
            print(json.dumps({"event": "virtual-ingress-rejected", "namespace": ingress["metadata"]["namespace"],
                              "name": ingress["metadata"]["name"], "error": str(exc)}), flush=True)


if __name__ == "__main__":
    while True:
        try:
            reconcile()
        except Exception as exc:
            print(json.dumps({"event": "virtual-ingress-reconcile-error", "error": str(exc),
                              "url": getattr(exc, "url", None), "code": getattr(exc, "code", None)}), flush=True)
        time.sleep(INTERVAL)
