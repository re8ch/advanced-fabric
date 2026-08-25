#!/usr/bin/env python3
"""Observe-first RouterOSNode v1alpha2 reconciler.

The controller owns only objects bearing the ``re8ch-v2:`` comment prefix. It
never deletes unowned RouterOS configuration. GuardedApply requires the exact
SHA-256 transaction checksum published by Observe mode.
"""

import base64
import hashlib
import ipaddress
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

API_HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
API_PORT = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
KUBE_API = f"https://{API_HOST}:{API_PORT}"
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
KUBE_CONTEXT = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
ROS_CONTEXT = ssl.create_default_context()
ROS_CONTEXT.check_hostname = False
ROS_CONTEXT.verify_mode = ssl.CERT_NONE
MANAGED = "re8ch-v2:"


def kube(path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        KUBE_API + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/merge-patch+json"},
    )
    return json.load(urllib.request.urlopen(request, context=KUBE_CONTEXT, timeout=15))


def credentials(reference):
    payload = kube(f"/api/v1/namespaces/{reference['namespace']}/secrets/{reference['name']}")
    return {key: base64.b64decode(value).decode() for key, value in payload.get("data", {}).items()}


class RouterOS:
    def __init__(self, address, secret):
        raw = f"{secret['username']}:{secret['password']}".encode()
        self.base = f"http://{address}/rest"
        self.auth = base64.b64encode(raw).decode()

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Authorization": "Basic " + self.auth, "Content-Type": "application/json"},
        )
        response = urllib.request.urlopen(request, context=ROS_CONTEXT, timeout=15)
        raw = response.read()
        return json.loads(raw) if raw else {}


def network(value):
    return ipaddress.ip_network(value, strict=True)


def validate(spec):
    errors = []
    protected = [network(value) for value in spec.get("protectedPrefixes", [])]
    domains = {item["name"]: item for item in spec.get("routeDomains", [])}
    if "main" in domains:
        errors.append("routeDomains must not contain main")
    if not any(net == network("0.0.0.0/0") for net in protected):
        errors.append("protectedPrefixes must contain 0.0.0.0/0")
    public_vips = {str(network(item["cidr"])) for item in spec.get("publicVIPs", [])}
    continuity = spec.get("continuity", {})
    if not continuity.get("operatorWitness") or not continuity.get("requireExternalProbe"):
        errors.append("continuity requires an operator witness and external probe")
    if public_vips and not spec.get("fallbackNextHop"):
        errors.append("fallbackNextHop is required for publicVIPs")
    names = set()
    for peer in spec.get("peers", []):
        if peer["name"] in names:
            errors.append(f"duplicate peer {peer['name']}")
        names.add(peer["name"])
        if peer["routeDomain"] not in domains:
            errors.append(f"peer {peer['name']} references unknown routeDomain")
        accepted = [network(value) for value in peer.get("acceptedPrefixes", [])]
        if len(accepted) > int(peer.get("maxPrefixes", 0)):
            errors.append(f"peer {peer['name']} acceptedPrefixes exceeds maxPrefixes")
        for prefix in accepted:
            if prefix.prefixlen == 0 or any(prefix.subnet_of(item) for item in protected if item.prefixlen > 0):
                errors.append(f"peer {peer['name']} accepts protected prefix {prefix}")
        if peer["role"] != "r640-public-origin" and any(str(item) in public_vips for item in accepted):
            errors.append(f"{peer['role']} {peer['name']} may not originate a public VIP")
        if peer.get("failurePolicy") != "WithdrawPeerOnly":
            errors.append(f"peer {peer['name']} must use WithdrawPeerOnly")
    origins = [peer for peer in spec.get("peers", []) if peer["role"] == "r640-public-origin"]
    for vip in public_vips:
        if sum(vip in peer.get("acceptedPrefixes", []) for peer in origins) != 1:
            errors.append(f"public VIP {vip} must have exactly one r640-public-origin")
    return errors


def desired(spec):
    fast_domains = []
    rules = []
    for domain in sorted(spec["routeDomains"], key=lambda item: item["name"]):
        fast_domains.append({"name": domain["name"], "fib": "yes" if domain.get("fib") else "no",
                             "comment": MANAGED + "route-domain"})
        for prefix in sorted(domain["prefixes"]):
            rules.append({"dst-address": str(network(prefix)), "action": "lookup", "table": domain["name"],
                          "comment": MANAGED + "accelerated-prefix:" + str(network(prefix))})
    filters, connections = [], []
    for peer in sorted(spec["peers"], key=lambda item: item["name"]):
        chain = "re8ch-v2-" + peer["name"] + "-in"
        for sequence, prefix in enumerate(sorted(peer["acceptedPrefixes"]), start=10):
            filters.append({"chain": chain, "rule": f"if (dst=={network(prefix)}) {{ accept }}",
                            "comment": f"{MANAGED}{peer['name']}:accept:{sequence}"})
        filters.append({"chain": chain, "rule": "reject", "comment": f"{MANAGED}{peer['name']}:reject-all"})
        connections.append({
            "name": peer["name"], "as": str(spec["routerASN"]), "local.address": peer["localAddress"],
            "local.role": "ebgp", "remote.address": peer["address"], "remote.as": str(peer["remoteASN"]),
            "routing-table": peer["routeDomain"], "input.filter": chain,
            "use-bfd": "yes" if peer.get("useBFD") else "no", "keepalive-time": peer.get("keepaliveTime", "10s"),
            "hold-time": peer.get("holdTime", "30s"),
            "input.limit-process-routes-ipv4": str(peer["maxPrefixes"]),
            "comment": MANAGED + "peer:" + peer["role"],
        })
    fallbacks = [{"dst-address": str(network(item["cidr"])), "gateway": spec["fallbackNextHop"],
                  "routing-table": "main", "distance": str(item["fallbackDistance"]),
                  "comment": MANAGED + "public-vip-fallback"} for item in spec["publicVIPs"]]
    transaction = {"version": 2, "tables": fast_domains, "rules": rules, "filters": filters,
                   "connections": connections, "fallbackRoutes": fallbacks,
                   "protectedPrefixes": sorted(str(network(value)) for value in spec["protectedPrefixes"]),
                   "continuity": spec["continuity"],
                   "invariants": {"mainBgpForbidden": True, "defaultRouteImmutable": True,
                                  "failurePolicy": "WithdrawPeerOnly"}}
    canonical = json.dumps(transaction, sort_keys=True, separators=(",", ":"))
    return transaction, hashlib.sha256(canonical.encode()).hexdigest()


def index(rows, key):
    return {row.get(key): row for row in rows if row.get(key)}


def observe(router, transaction):
    tables = router.request("/routing/table")
    rules = router.request("/routing/rule")
    filters = router.request("/routing/filter/rule")
    connections = router.request("/routing/bgp/connection")
    routes = router.request("/ip/route")
    dns = router.request("/ip/dns")
    nat = router.request("/ip/firewall/nat")
    mangle = router.request("/ip/firewall/mangle")
    defaults = [row for row in routes if row.get("dst-address") == "0.0.0.0/0" and row.get("routing-table", "main") == "main"]
    continuity = transaction["continuity"]
    expected_immediate = continuity["mainDefaultGateway"] + "%" + continuity["mainDefaultInterface"]
    default_unchanged = any(row.get("active") == "true" and row.get("gateway") == continuity["mainDefaultGateway"]
                            and row.get("immediate-gw", "").startswith(expected_immediate) for row in defaults)
    main_bgp = [row.get("dst-address") for row in routes if row.get("dynamic") == "true" and row.get("bgp") == "true"
                and row.get("routing-table", "main") == "main"]
    protected = [network(value) for value in transaction["protectedPrefixes"]]
    leaked = []
    for value in main_bgp:
        try:
            candidate = network(value)
        except ValueError:
            continue
        if any(candidate == item or (item.prefixlen > 0 and candidate.subnet_of(item)) for item in protected):
            leaked.append(value)
    def duplicates(rows):
        seen, repeated = set(), []
        for row in rows:
            if row.get("disabled") == "true":
                continue
            key = tuple((name, row.get(name, "")) for name in
                        ("chain", "action", "src-address", "dst-address", "in-interface", "out-interface", "protocol", "dst-port", "to-addresses", "to-ports", "new-routing-mark"))
            if key in seen:
                repeated.append(row.get("comment", "<unowned>"))
            seen.add(key)
        return sorted(repeated)
    present = {
        "tables": sorted(row.get("name") for row in tables),
        "rules": sorted(row.get("comment") for row in rules if row.get("comment", "").startswith(MANAGED)),
        "filters": sorted(row.get("comment") for row in filters if row.get("comment", "").startswith(MANAGED)),
        "connections": {name: {"routingTable": row.get("routing-table", "main"), "disabled": row.get("disabled")}
                        for name, row in index(connections, "name").items() if name in {item["name"] for item in transaction["connections"]}},
        "fallbackRoutes": sorted(row.get("dst-address") for row in routes if row.get("comment") == MANAGED + "public-vip-fallback"),
    }
    expected = {
        "tables": sorted(item["name"] for item in transaction["tables"]),
        "rules": sorted(item["comment"] for item in transaction["rules"]),
        "filters": sorted(item["comment"] for item in transaction["filters"]),
        "connections": {item["name"]: {"routingTable": item["routing-table"], "disabled": "false"}
                        for item in transaction["connections"]},
        "fallbackRoutes": sorted(item["dst-address"] for item in transaction["fallbackRoutes"]),
    }
    result = {"expected": expected, "observed": present, "converged": expected == present,
              "invariants": {"mainBgpRoutes": main_bgp, "mainDefaultRoutes": defaults,
                             "protectedPrefixLeaks": sorted(leaked),
                             "mainBgpForbidden": not main_bgp,
                             "defaultRoutePresent": any(row.get("active") == "true" for row in defaults),
                             "defaultRouteUnchanged": default_unchanged,
                             "operatorWitness": continuity["operatorWitness"],
                             "externalProbeRequired": continuity["requireExternalProbe"]}}
    result["invariants"].update({
        "dohSoleResolver": bool(dns.get("use-doh-server")),
        "dnsServers": dns.get("servers", ""),
        "duplicateNatRules": duplicates(nat),
        "duplicateMangleRules": duplicates(mangle),
    })
    return result


def patch_status(name, status):
    kube(f"/apis/networking.re8ch.com/v1alpha2/routerosnodes/{name}/status", "PATCH", {"status": status})


def reconcile(obj):
    name, spec = obj["metadata"]["name"], obj["spec"]
    errors = validate(spec)
    transaction, checksum = desired(spec) if not errors else ({}, "")
    status = {"observedGeneration": obj["metadata"].get("generation", 0), "safety": "Rejected" if errors else "Accepted",
              "transactionChecksum": checksum, "errors": errors,
              "lastObservedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not errors:
        router = RouterOS(spec["routerAddress"], credentials(spec["credentialsSecretRef"]))
        status["diff"] = observe(router, transaction)
        apply = spec.get("apply", {})
        if spec["mode"] == "GuardedApply" and apply.get("enabled"):
            if apply.get("checksum") != checksum:
                status["errors"] = ["GuardedApply checksum does not match observed transaction"]
                status["safety"] = "Rejected"
            else:
                status["errors"] = ["GuardedApply is staged but mutation remains release-gated"]
                status["safety"] = "ReleaseGated"
    status["conditions"] = [{"type": "Ready", "status": "True" if not status["errors"] else "False",
                             "reason": status["safety"], "message": "; ".join(status["errors"]) or "observe transaction accepted"}]
    patch_status(name, status)


while True:
    try:
        for item in kube("/apis/networking.re8ch.com/v1alpha2/routerosnodes").get("items", []):
            try:
                reconcile(item)
            except Exception as error:
                print(json.dumps({"event": "routeros-v2-reconcile-error", "name": item["metadata"]["name"], "error": repr(error)}), flush=True)
    except Exception as error:
        print(json.dumps({"event": "routeros-v2-list-error", "error": repr(error)}), flush=True)
    time.sleep(30)
