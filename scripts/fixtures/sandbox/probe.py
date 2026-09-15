"""Boundary probe run inside the discovery-runner container by scripts/verify-phase2.sh.

Attempts prohibited connections directly (they must have no route or no name) and through the
egress gateway (they must be refused with a specific code), and reads the routing table. It only
contacts the verification project's own containers and unroutable addresses; the metadata
address is never reachable from this network, so no real metadata service is involved.
Standard library only; prints one JSON object.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import struct


def tcp(host: str, port: int, timeout: float = 3.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "connected"
    except ConnectionRefusedError:
        return "refused"
    except socket.gaierror:
        return "dns_failure"
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        return f"errno_{exc.errno}"


def via_gateway(target: str) -> str:
    with socket.create_connection(("discovery-gateway", 3128), timeout=15) as connection:
        connection.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return connection.recv(512).split(b"\r\n")[0].decode("latin-1")


def addresses(name: str) -> set[str]:
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)}
    except OSError:
        return set()


# With isolated gateway mode Docker may give the first address to one of the sandbox's own
# containers; without it, the first address belongs to the host.
peers = {
    address: name
    for name in ("discovery-gateway", "collector", socket.gethostname())
    for address in addresses(name)
}
routes = [line.split() for line in open("/proc/net/route").read().splitlines()[1:]]
first_addresses = []
for fields in routes:
    destination = struct.unpack("<I", bytes.fromhex(fields[1]))[0]
    mask = struct.unpack("<I", bytes.fromhex(fields[7]))[0]
    if destination and mask:
        network = ipaddress.IPv4Network((destination, bin(mask).count("1")))
        first_addresses.append(str(network.network_address + 1))

print(
    json.dumps(
        {
            "default_route": any(f[1] == "00000000" and f[7] == "00000000" for f in routes),
            "interfaces": sorted({f[0] for f in routes}),
            "direct": {
                f"{host}:{port}": tcp(host, port)
                for host, port in (
                    ("172.31.250.10", 8080),
                    ("172.31.250.10", 443),
                    ("169.254.169.254", 80),
                    ("1.1.1.1", 443),
                    ("fixture-site", 8080),
                    ("postgres", 5432),
                    ("redis", 6379),
                    ("api", 8000),
                    ("crt.sh", 443),
                    ("crt.sh", 5432),
                    ("host.docker.internal", 80),
                )
            },
            "bridge_first_address": {
                address: (
                    {"held_by_sandbox_container": peers[address]}
                    if address in peers
                    else {str(port): tcp(address, port, 1.0) for port in (22, 80, 2375)}
                )
                for address in first_addresses
            },
            "gateway": {
                target: via_gateway(target)
                for target in (
                    "fixture-site:8080",
                    "example.com:443",
                    "certificatedetails.com:443",
                    "anubisdb.com:443",
                    "crt.sh:8443",
                    "172.31.250.10:443",
                    "rapiddns.io:443",
                    "crt.sh:443",
                )
            },
        }
    )
)
