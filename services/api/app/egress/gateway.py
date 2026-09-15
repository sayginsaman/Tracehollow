"""Egress gateway: the only way out of the discovery sandbox network (ADR 0007).

The ``discovery-runner`` container, where Subfinder runs, is attached only to the internal
``discovery`` network: it has no route, no external DNS and no host address. This service sits
on that network and on ``collect-egress`` and relays exactly one kind of traffic:

1. ``CONNECT <host>:443`` where ``host`` is a provider host name on the Subfinder allowlist
   (``app.egress.providers``). IP literals, other names and other ports are refused.
2. Every address the name resolves to must pass the collection address policy
   (``app.connectors.netguard.NetworkPolicy``): loopback, private, link-local, metadata and
   reserved ranges are refused unless an operator allowed a private lab network.
3. The gateway connects to a checked address itself and verifies the provider's TLS certificate
   for the host name (Subfinder's own client does not verify certificates). Only then does it
   answer ``200`` and accept the client's TLS session with a throwaway certificate, relaying
   plaintext between the two TLS sessions.

Refusals answer ``403``/``502`` with the reason phrase ``Tracehollow egress refused <code>`` so
Subfinder's error message names the code. Tunnels are bounded in number, duration, idle time and
bytes. Logs record the allowed provider host, decision, code, byte counts and duration; refused
host names, paths and query strings (which contain the investigated domain and API keys) are
never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ipaddress
import json
import logging
import os
import signal
import ssl
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.connectors.netguard import (
    DestinationBlockedError,
    FetchError,
    NetworkPolicy,
    parse_networks,
)
from app.egress.providers import PROVIDER_PORT, provider_hosts
from app.logging_config import configure_logging

logger = logging.getLogger("tracehollow.egress.gateway")

MAX_HEAD_BYTES = 8 * 1024
REFUSED = "Tracehollow egress refused"
FAILED = "Tracehollow egress failed"

OpenConnection = Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


@dataclass
class GatewayConfig:
    allowed_hosts: frozenset[str]
    policy: NetworkPolicy
    upstream_tls: ssl.SSLContext
    client_tls: ssl.SSLContext
    max_tunnels: int = 16
    head_timeout_seconds: float = 10
    connect_timeout_seconds: float = 10
    idle_timeout_seconds: float = 60
    max_tunnel_seconds: float = 300
    max_bytes_per_direction: int = 64 * 1024 * 1024
    open_connection: OpenConnection = field(default=asyncio.open_connection)


@dataclass
class Tunnel:
    host: str | None = None
    decision: str = "refused"
    code: str | None = None
    bytes_up: int = 0
    bytes_down: int = 0


class Refusal(Exception):
    def __init__(self, status: int, code: str, *, failed: bool = False) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.failed = failed


def throwaway_client_tls() -> ssl.SSLContext:
    """Server-side TLS context with a new self-signed certificate (never trusted by anyone)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tracehollow-egress-gateway")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix="egress-tls-") as directory:
        cert_path = Path(directory) / "cert.pem"
        key_path = Path(directory) / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.touch(mode=0o600)
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context.load_cert_chain(cert_path, key_path)
    return context


def verified_upstream_tls() -> ssl.SSLContext:
    """Certificate and host name verification against the system trust store."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _parse_authority(target: str) -> tuple[str, int]:
    host, separator, port_text = target.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise Refusal(400, "bad_request")
    host = host.strip().lower().rstrip(".")
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(host.strip("[]"))
        raise Refusal(403, "ip_literal_not_allowed")
    return host, int(port_text)


async def _read_head(reader: asyncio.StreamReader, config: GatewayConfig) -> list[str]:
    try:
        head = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=config.head_timeout_seconds
        )
    except (asyncio.LimitOverrunError, ValueError) as exc:
        raise Refusal(431, "request_head_too_large") from exc
    except (TimeoutError, asyncio.IncompleteReadError) as exc:
        raise Refusal(400, "bad_request") from exc
    return head.decode("latin-1").split("\r\n")


async def _connect_upstream(
    host: str, port: int, config: GatewayConfig
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        addresses = await asyncio.to_thread(config.policy.resolve, host, port)
    except DestinationBlockedError as exc:
        raise Refusal(403, exc.code) from exc
    except FetchError as exc:
        raise Refusal(502, exc.code, failed=True) from exc
    last: Refusal = Refusal(502, "upstream_unreachable", failed=True)
    for address in addresses:
        try:
            return await asyncio.wait_for(
                config.open_connection(
                    address,
                    port,
                    ssl=config.upstream_tls,
                    server_hostname=host,
                    ssl_handshake_timeout=config.connect_timeout_seconds,
                ),
                timeout=config.connect_timeout_seconds,
            )
        except ssl.SSLCertVerificationError:
            last = Refusal(502, "upstream_certificate_invalid", failed=True)
        except ssl.SSLError:
            last = Refusal(502, "upstream_tls_failed", failed=True)
        except (OSError, TimeoutError):
            last = Refusal(502, "upstream_unreachable", failed=True)
    raise last


async def _pump(
    source: asyncio.StreamReader,
    target: asyncio.StreamWriter,
    tunnel: Tunnel,
    direction: str,
    config: GatewayConfig,
) -> None:
    while True:
        data = await asyncio.wait_for(source.read(65536), timeout=config.idle_timeout_seconds)
        if not data:
            return
        total = getattr(tunnel, direction) + len(data)
        setattr(tunnel, direction, total)
        if total > config.max_bytes_per_direction:
            raise Refusal(502, "tunnel_byte_limit", failed=True)
        target.write(data)
        await target.drain()


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    tunnel: Tunnel,
    config: GatewayConfig,
) -> None:
    up = asyncio.create_task(_pump(client_reader, upstream_writer, tunnel, "bytes_up", config))
    down = asyncio.create_task(_pump(upstream_reader, client_writer, tunnel, "bytes_down", config))
    deadline = time.monotonic() + config.max_tunnel_seconds
    try:
        # The exchange ends when the provider closes its side (Subfinder sends Connection:
        # close), when either side fails, or at a limit. A client that has finished sending
        # still receives the rest of the response.
        pending = {up, down}
        while pending:
            done, pending = await asyncio.wait(
                pending,
                timeout=max(0.0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                tunnel.code = "tunnel_time_limit"
                return
            failed = [task for task in done if task.exception() is not None]
            if failed:
                error = failed[0].exception()
                tunnel.code = getattr(error, "code", None) or (
                    "tunnel_idle_timeout" if isinstance(error, TimeoutError) else "tunnel_closed"
                )
                return
            if down in done:
                return
    finally:
        for task in (up, down):
            task.cancel()
        await asyncio.gather(up, down, return_exceptions=True)
        upstream_writer.close()


async def _respond(
    writer: asyncio.StreamWriter, status: int, reason: str, body: bytes = b""
) -> None:
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode("latin-1")
        + body
    )
    with contextlib.suppress(OSError):
        await writer.drain()


async def handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: GatewayConfig,
    slots: asyncio.Semaphore,
) -> None:
    started = time.monotonic()
    tunnel = Tunnel()
    try:
        lines = await _read_head(reader, config)
        parts = lines[0].split(" ")
        if len(parts) != 3:
            raise Refusal(400, "bad_request")
        method, target, _ = parts
        if method == "GET" and target == "/health":
            body = json.dumps(
                {
                    "status": "ok",
                    "allowed_hosts": sorted(config.allowed_hosts),
                    "allowed_ports": sorted(config.policy.allowed_ports),
                    "lab_networks_configured": bool(config.policy.allowed_private_networks),
                }
            ).encode()
            tunnel.decision, tunnel.code = "health", None
            await _respond(writer, 200, "OK", body)
            return
        if method != "CONNECT":
            raise Refusal(405, "method_not_allowed")
        host, port = _parse_authority(target)
        if host not in config.allowed_hosts:
            raise Refusal(403, "host_not_allowed")
        tunnel.host = host
        if slots.locked():
            raise Refusal(503, "gateway_busy", failed=True)
        async with slots:
            upstream_reader, upstream_writer = await _connect_upstream(host, port, config)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            try:
                await writer.start_tls(
                    config.client_tls, ssl_handshake_timeout=config.connect_timeout_seconds
                )
            except (ssl.SSLError, OSError, TimeoutError):
                upstream_writer.close()
                tunnel.code = "client_tls_failed"
                return
            tunnel.decision = "allowed"
            await _relay(reader, writer, upstream_reader, upstream_writer, tunnel, config)
    except Refusal as refusal:
        tunnel.code = refusal.code
        reason = f"{FAILED if refusal.failed else REFUSED} {refusal.code}"
        await _respond(writer, refusal.status, reason)
    except (OSError, TimeoutError):
        tunnel.code = tunnel.code or "client_disconnected"
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            writer.close()
        if tunnel.decision != "health":
            logger.info(
                "egress_tunnel",
                extra={
                    # Only allowlisted provider names are logged; refused names may be case data.
                    "provider_host": tunnel.host,
                    "decision": tunnel.decision,
                    "code": tunnel.code,
                    "bytes_up": tunnel.bytes_up,
                    "bytes_down": tunnel.bytes_down,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )


async def serve(config: GatewayConfig, host: str, port: int) -> asyncio.Server:
    slots = asyncio.Semaphore(config.max_tunnels)
    return await asyncio.start_server(
        lambda r, w: handle(r, w, config, slots), host, port, limit=MAX_HEAD_BYTES
    )


def config_from_environment() -> GatewayConfig:
    networks = [
        item
        for item in os.environ.get("TRACEHOLLOW_GATEWAY_ALLOWED_PRIVATE_NETWORKS", "").split(",")
        if item.strip()
    ]
    return GatewayConfig(
        allowed_hosts=provider_hosts(),
        policy=NetworkPolicy(
            allowed_ports=frozenset({PROVIDER_PORT}),
            allowed_private_networks=parse_networks(networks),
        ),
        upstream_tls=verified_upstream_tls(),
        client_tls=throwaway_client_tls(),
    )


async def _main() -> None:
    configure_logging(os.environ.get("TRACEHOLLOW_LOG_LEVEL", "INFO"))
    config = config_from_environment()
    port = int(os.environ.get("TRACEHOLLOW_GATEWAY_PORT", "3128"))
    server = await serve(config, "0.0.0.0", port)  # noqa: S104 - reachable only on internal networks
    logger.info(
        "egress_gateway_started",
        extra={
            "port": port,
            "allowed_hosts": len(config.allowed_hosts),
            "lab_networks_configured": bool(config.policy.allowed_private_networks),
        },
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    async with server:
        await stop.wait()


if __name__ == "__main__":
    asyncio.run(_main())
