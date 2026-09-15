"""Subfinder job runner for the network sandbox (``discovery-runner`` service, ADR 0007).

This process runs Subfinder, a third-party binary whose own network traffic (HTTP with
certificate verification disabled, a direct PostgreSQL connection for crt.sh, DNS) cannot be
controlled from inside the process. The protection is therefore the container's network, not
this code: the container is attached only to the internal ``discovery`` network, where the only
reachable service with outside access is the egress gateway (``app.egress.gateway``). Subfinder
is started with ``-proxy`` pointing at that gateway; traffic that ignores the proxy has no route.

Before every job the runner re-checks the sandbox and refuses to run when it is not in place:

* no IPv4 or IPv6 default route and exactly one network interface;
* the Docker host is not reachable on the network's first address (bridge gateway isolation);
* the egress gateway answers its health check.

The collector calls ``POST /v1/subfinder`` with a validated job and receives newline-delimited
JSON: ``{"stdout": line}`` as results arrive, ``{"heartbeat": true}`` every second (a failed write
means the collector cancelled and the process is stopped), then ``{"stderr": line}`` lines and a
final ``{"exit": code, "stopped": reason}``. Only the standard library and
``app.connectors.engines.process`` are used; the image contains no application secrets.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import struct
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.connectors.engines import process
from app.egress.providers import SUBFINDER_SOURCES
from app.logging_config import configure_logging

logger = logging.getLogger("tracehollow.discovery.runner")

MAX_REQUEST_BYTES = 64 * 1024
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
_RTF_REJECT = 0x0200


@dataclass
class RunnerConfig:
    subfinder: Path
    gateway: str  # host:port of the egress gateway
    peers: tuple[str, ...] = ()  # other containers expected on the sandbox network
    route_file: Path = Path("/proc/net/route")
    ipv6_route_file: Path = Path("/proc/net/ipv6_route")
    max_jobs: int = 2
    check_sandbox: bool = True
    slots: threading.BoundedSemaphore = field(init=False)

    def __post_init__(self) -> None:
        self.slots = threading.BoundedSemaphore(self.max_jobs)


class JobError(ValueError):
    pass


def validate_job(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise JobError("the job must be a JSON object")
    domain = str(raw.get("domain", "")).strip().lower()
    if not _DOMAIN.match(domain):
        raise JobError("domain must be an ASCII (IDNA) domain name")
    with_ip = domain.replace(".", "")
    if with_ip.isdigit():
        raise JobError("domain must not be an IP address")
    sources = raw.get("sources")
    if (
        not isinstance(sources, list)
        or not sources
        or any(source not in SUBFINDER_SOURCES for source in sources)
        or len(set(sources)) != len(sources)
    ):
        raise JobError("sources must be a non-empty list of supported source names")
    keys = raw.get("provider_keys") or {}
    if not isinstance(keys, dict) or any(
        source not in sources
        or not SUBFINDER_SOURCES[source]["key"]
        or not isinstance(value, str)
        or not 0 < len(value) <= 512
        or "\n" in value
        for source, value in keys.items()
    ):
        raise JobError("provider_keys must map selected key-based sources to single-line keys")

    def bounded(name: str, low: int, high: int) -> int:
        value = raw.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise JobError(f"{name} must be an integer between {low} and {high}")
        return value

    return {
        "domain": domain,
        "sources": list(sources),
        "provider_keys": dict(keys),
        "request_timeout_seconds": bounded("request_timeout_seconds", 1, 60),
        "max_time_minutes": bounded("max_time_minutes", 1, 10),
        "max_response_bytes": bounded("max_response_bytes", 1024, 20 * 1024 * 1024),
        "deadline_seconds": bounded("deadline_seconds", 1, 900),
        "max_results": bounded("max_results", 1, 5000),
    }


# -- sandbox checks ----------------------------------------------------------------------------


def _ipv4_routes(path: Path) -> list[tuple[str, int, int]]:
    routes = []
    for line in path.read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8:
            destination = struct.unpack("<I", bytes.fromhex(fields[1]))[0]
            mask = struct.unpack("<I", bytes.fromhex(fields[7]))[0]
            routes.append((fields[0], destination, mask))
    return routes


def _has_ipv6_default_route(path: Path) -> bool:
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        destination, prefix, flags, interface = fields[0], fields[1], int(fields[8], 16), fields[9]
        if (
            destination == "0" * 32
            and prefix == "00"
            and not flags & _RTF_REJECT
            and interface != "lo"
        ):
            return True
    return False


def _resolve_all(name: str) -> set[str]:
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)}
    except OSError:
        return set()


def _answers(address: str, port: int, timeout: float) -> bool:
    """True when something at ``address`` accepts or actively refuses a TCP connection."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def sandbox_problems(config: RunnerConfig) -> list[str]:
    if not config.check_sandbox:
        return []
    problems: list[str] = []
    try:
        routes = _ipv4_routes(config.route_file)
    except (OSError, ValueError):
        return ["the routing table could not be read"]
    if any(destination == 0 and mask == 0 for _, destination, mask in routes):
        problems.append("an IPv4 default route exists (the container can reach other networks)")
    try:
        if _has_ipv6_default_route(config.ipv6_route_file):
            problems.append("an IPv6 default route exists")
    except (OSError, ValueError):
        problems.append("the IPv6 routing table could not be read")
    interfaces = {name for name, _, _ in routes if name != "lo"}
    if len(interfaces) != 1:
        problems.append(f"expected one network interface, found {len(interfaces)}")
    gateway_host, _, gateway_port = config.gateway.rpartition(":")
    known = _resolve_all(socket.gethostname())
    for peer in (gateway_host, *config.peers):
        known |= _resolve_all(peer)
    for _, destination, mask in routes:
        if destination == 0 or mask == 0:
            continue
        network = ipaddress.IPv4Network(
            (ipaddress.IPv4Address(destination), bin(mask).count("1")), strict=False
        )
        first = str(network.network_address + 1)
        # Without gateway isolation the host owns the first address; a container can hold it
        # only when the bridge has no address.
        if first not in known and _answers(first, 9, 0.5):
            problems.append(
                f"the Docker host answers on {first}; the sandbox network needs "
                "com.docker.network.bridge.gateway_mode_ipv4=isolated"
            )
    if not gateway_port.isdigit() or not _gateway_healthy(gateway_host, int(gateway_port)):
        problems.append("the egress gateway is not reachable")
    return problems


def _gateway_healthy(host: str, port: int) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    except OSError:
        return False
    finally:
        connection.close()


# -- jobs --------------------------------------------------------------------------------------


def run_job(
    config: RunnerConfig,
    job: dict[str, Any],
    send: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Run Subfinder for one validated job, streaming output through ``send``.

    ``send`` returns False when the collector is gone; the process is then stopped.
    """
    hosts: set[str] = set()
    gone = False
    last_heartbeat = time.monotonic()

    def on_stdout(line: str) -> None:
        nonlocal gone
        try:
            host = json.loads(line).get("host")
        except (ValueError, AttributeError):
            host = None
        if isinstance(host, str):
            hosts.add(host)
        if not send({"stdout": line}):
            gone = True

    def cancelled() -> bool:
        nonlocal gone, last_heartbeat
        if not gone and time.monotonic() - last_heartbeat >= 1.0:
            last_heartbeat = time.monotonic()
            gone = not send({"heartbeat": True})
        return gone

    with tempfile.TemporaryDirectory(prefix="subfinder-") as home:
        base = Path(home)
        config_file = base / "config.yaml"
        provider_file = base / "provider-config.yaml"
        config_file.write_text("", encoding="utf-8")
        provider_file.touch(mode=0o600)
        provider_file.write_text(
            "".join(
                f"{source}:\n  - {json.dumps(value)}\n"
                for source, value in job["provider_keys"].items()
            )
            or "{}\n",
            encoding="utf-8",
        )
        result = process.run(
            [
                str(config.subfinder),
                "-d",
                job["domain"],
                "-s",
                ",".join(job["sources"]),
                "-oJ",
                "-cs",
                "-duc",
                "-nc",
                "-v",
                "-stats",
                "-timeout",
                str(job["request_timeout_seconds"]),
                "-max-time",
                str(job["max_time_minutes"]),
                "-rsr",
                str(job["max_response_bytes"]),
                "-config",
                str(config_file),
                "-pc",
                str(provider_file),
                "-proxy",
                f"http://{config.gateway}",
            ],
            env=process.minimal_environment(base),
            cwd=base,
            deadline=time.monotonic() + job["deadline_seconds"],
            cancelled=cancelled,
            on_stdout=on_stdout,
            stop_when=lambda: len(hosts) >= job["max_results"],
        )
    stopped = "canceled" if gone else result.stopped
    return {"returncode": result.returncode, "stopped": stopped, "stderr": result.stderr}


class _Handler(BaseHTTPRequestHandler):
    server_version = "tracehollow-discovery-runner"
    config: RunnerConfig

    def log_message(self, format: str, *args: Any) -> None:
        return  # request lines would contain nothing sensitive, but keep logs structured

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(404, {"error": "not_found"})
            return
        problems = sandbox_problems(self.config)
        installed = self.config.subfinder.is_file()
        healthy = not problems and installed
        self._json(
            200 if healthy else 503,
            {
                "status": "ok" if healthy else "unavailable",
                "sandbox_problems": problems,
                "engine_installed": installed,
            },
        )

    def do_POST(self) -> None:
        if self.path != "/v1/subfinder":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(413 if length > MAX_REQUEST_BYTES else 400, {"error": "invalid_request"})
            return
        try:
            job = validate_job(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeDecodeError) as exc:
            detail = str(exc) if isinstance(exc, JobError) else "the body is not valid JSON"
            self._json(400, {"error": "invalid_job", "detail": detail})
            return
        problems = sandbox_problems(self.config)
        if problems:
            logger.warning("discovery_sandbox_unavailable", extra={"problems": problems})
            self._json(503, {"error": "sandbox_unavailable", "problems": problems})
            return
        if not self.config.subfinder.is_file():
            self._json(503, {"error": "engine_not_installed"})
            return
        if not self.config.slots.acquire(blocking=False):
            self._json(429, {"error": "runner_busy"})
            return
        started = time.monotonic()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            lock = threading.Lock()

            def send(payload: dict[str, Any]) -> bool:
                with lock:
                    try:
                        self.wfile.write(json.dumps(payload).encode() + b"\n")
                        self.wfile.flush()
                    except OSError:
                        return False
                return True

            outcome = run_job(self.config, job, send)
            for line in outcome["stderr"]:
                send({"stderr": line})
            send({"exit": outcome["returncode"], "stopped": outcome["stopped"]})
            logger.info(
                "discovery_job_finished",
                extra={
                    "sources": len(job["sources"]),
                    "keys": len(job["provider_keys"]),
                    "stopped": outcome["stopped"],
                    "returncode": outcome["returncode"],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
        finally:
            self.config.slots.release()
            self.close_connection = True


def make_server(config: RunnerConfig, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"config": config})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    configure_logging(os.environ.get("TRACEHOLLOW_LOG_LEVEL", "INFO"))
    config = RunnerConfig(
        subfinder=Path(os.environ.get("TRACEHOLLOW_SUBFINDER_PATH", "/usr/local/bin/subfinder")),
        gateway=os.environ.get("TRACEHOLLOW_DISCOVERY_GATEWAY", "discovery-gateway:3128"),
        peers=tuple(
            peer.strip()
            for peer in os.environ.get("TRACEHOLLOW_DISCOVERY_PEERS", "collector").split(",")
            if peer.strip()
        ),
    )
    port = int(os.environ.get("TRACEHOLLOW_DISCOVERY_RUNNER_PORT", "8090"))
    server = make_server(config, "0.0.0.0", port)  # noqa: S104 - internal sandbox network only
    problems = sandbox_problems(config)
    logger.info("discovery_runner_started", extra={"port": port, "sandbox_problems": problems})
    server.serve_forever()


if __name__ == "__main__":
    main()
