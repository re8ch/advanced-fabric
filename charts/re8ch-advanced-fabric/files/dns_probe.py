#!/usr/bin/env python3
import base64
import os
import socket
import ssl
import struct
import urllib.request

server = os.getenv("DNS_SERVER", "10.43.65.40")
name = os.getenv("DNS_QUERY", "kubernetes.default.svc.cluster.local")
query = struct.pack("!HHHHHH", 42, 0x0100, 1, 0, 0, 0)
for label in name.split("."):
    query += bytes([len(label)]) + label.encode()
query += b"\0" + struct.pack("!HH", 1, 1)


def valid(response):
    if len(response) < 12 or response[:2] != b"\0*" or response[3] & 0x0F:
        raise RuntimeError("DNS query failed")


with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    udp.settimeout(5)
    udp.sendto(query, (server, 53))
    valid(udp.recv(65535))

with socket.create_connection((server, 53), timeout=5) as tcp:
    tcp.sendall(struct.pack("!H", len(query)) + query)
    size = struct.unpack("!H", tcp.recv(2))[0]
    valid(tcp.recv(size))

encoded = base64.urlsafe_b64encode(query).rstrip(b"=").decode()
context = ssl.create_default_context(cafile="/tls/ca.crt")
with urllib.request.urlopen("https://%s/dns-query?dns=%s" % (server, encoded),
                            context=context, timeout=5) as response:
    valid(response.read())
print("advanced-fabric-dns UDP/TCP/DoH health check passed")
