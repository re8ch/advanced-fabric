#!/usr/bin/env python3
"""Publish the host collector's atomic JSON snapshot as a node ConfigMap."""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

node = os.environ["NODE_NAME"]
name = "advanced-fabric-node-" + node.lower().replace("_", "-").replace(".", "-")
base = "https://%s:%s" % (os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"])
token = open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8").read().strip()
context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
path = "/api/v1/namespaces/kube-system/configmaps/" + name


def call(method, url, body, content_type):
    request = urllib.request.Request(base + url, data=json.dumps(body).encode(), method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
    with urllib.request.urlopen(request, context=context, timeout=15) as response:
        response.read()


while True:
    try:
        with open("/status/status.json", encoding="utf-8") as stream:
            status = json.load(stream)
        obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name,
            "namespace": "kube-system", "labels": {"app.kubernetes.io/name": "re8ch-advanced-fabric",
            "app.kubernetes.io/component": "node-status", "networking.re8ch.com/node-status": "true"}},
            "data": {"status.json": json.dumps(status, separators=(",", ":"))}}
        try:
            call("PATCH", path, obj, "application/merge-patch+json")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            call("POST", "/api/v1/namespaces/kube-system/configmaps", obj, "application/json")
    except Exception as error:
        print(json.dumps({"event": "status-publish-error", "node": node, "error": str(error)}), flush=True)
    time.sleep(15)
