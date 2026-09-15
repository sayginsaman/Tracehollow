"""Egress gateway for the discovery sandbox: CONNECT allowlist, address policy, TLS verification.

These tests run the real gateway server on loopback. Name resolution is replaced by a fixed map
and, for the success path only, the final TCP connection to a documentation address is redirected
to a local TLS server, so no real provider or internal service is contacted. The container-level
boundary (no route out of the sandbox network) is exercised by ``scripts/verify-phase2.sh``.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import logging
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.connectors.netguard import NetworkPolicy
from app.egress import gateway
from app.egress.providers import SUBFINDER_SOURCES, provider_hosts

PUBLIC = "203.0.113.5"  # documentation range, treated as public by an explicit test resolver


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(
    tmp_path: Path, host: str, *, trusted: bool
) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """(server context for ``host``, client context trusting the test CA when ``trusted``)."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Test CA"))
        .issuer_name(_name("Test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    issuer_key = ca_key if trusted else key
    issuer = _name("Test CA") if trusted else _name(host)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name(host))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                (ca_key if trusted else key).public_key()
            ),
            critical=False,
        )
        .sign(issuer_key, hashes.SHA256())
    )
    cert_path, key_path, ca_path = tmp_path / "leaf.pem", tmp_path / "leaf.key", tmp_path / "ca.pem"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(ca_path))
    return server, client


def _config(
    resolver: dict[str, list[str]],
    upstream_tls: ssl.SSLContext | None = None,
    open_connection: Callable[..., Awaitable[Any]] | None = None,
    **overrides: Any,
) -> tuple[gateway.GatewayConfig, list[tuple[str, int]]]:
    calls: list[tuple[str, int]] = []

    async def refuse_connections(address: str, port: int, **_: Any) -> Any:
        calls.append((address, port))
        raise AssertionError("the gateway must not connect for a refused destination")

    config = gateway.GatewayConfig(
        allowed_hosts=frozenset({"provider.test", "metadata.test", "mixed.test", "private.test"}),
        policy=NetworkPolicy(
            allowed_ports=frozenset({443}),
            resolver=lambda host, port: resolver[host],
        ),
        upstream_tls=upstream_tls or ssl.create_default_context(),
        client_tls=gateway.throwaway_client_tls(),
        open_connection=open_connection or refuse_connections,
        **overrides,
    )
    return config, calls


async def _serve(config: gateway.GatewayConfig) -> tuple[asyncio.Server, int]:
    server = await gateway.serve(config, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _exchange(port: int, head: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=10)
    writer.close()
    return data


RESOLVER = {
    "provider.test": ["127.0.0.1"],
    "metadata.test": ["169.254.169.254"],
    "mixed.test": [PUBLIC, "10.0.0.1"],
    "private.test": ["192.168.1.20"],
}


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (
            b"CONNECT other.example:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused host_not_allowed",
        ),
        (
            b"CONNECT provider.test:80 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused blocked_port",
        ),
        (
            b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused ip_literal_not_allowed",
        ),
        (
            b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused ip_literal_not_allowed",
        ),
        (
            b"CONNECT provider.test:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused blocked_address",
        ),
        (
            b"CONNECT metadata.test:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused blocked_address",
        ),
        (
            b"CONNECT mixed.test:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused blocked_address",
        ),
        (
            b"CONNECT private.test:443 HTTP/1.1\r\n\r\n",
            b"403 Tracehollow egress refused blocked_address",
        ),
        (
            b"GET http://provider.test/ HTTP/1.1\r\nHost: provider.test\r\n\r\n",
            b"405 Tracehollow egress refused method_not_allowed",
        ),
        (
            b"CONNECT provider.test:443 HTTP/1.1\r\nX: " + b"a" * 9000 + b"\r\n\r\n",
            b"431 Tracehollow egress refused request_head_too_large",
        ),
    ],
)
def test_prohibited_destinations_are_refused_before_any_connection(
    head: bytes, expected: bytes
) -> None:
    async def scenario() -> None:
        config, calls = _config(RESOLVER)
        server, port = await _serve(config)
        async with server:
            response = await _exchange(port, head)
        assert response.startswith(b"HTTP/1.1 " + expected), response[:120]
        assert calls == []

    asyncio.run(scenario())


def test_allowed_provider_is_relayed_over_a_verified_tls_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        server_tls, trusted_client = _certificate(tmp_path, "provider.test", trusted=True)
        seen: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            seen.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\n")
            writer.write(b'{"ok": true}')
            await writer.drain()
            writer.close()

        provider = await asyncio.start_server(upstream, "127.0.0.1", 0, ssl=server_tls)
        provider_port = provider.sockets[0].getsockname()[1]
        connections: list[dict[str, Any]] = []

        async def to_local_provider(address: str, port: int, **kwargs: Any) -> Any:
            connections.append({"address": address, "port": port, **kwargs})
            return await asyncio.open_connection("127.0.0.1", provider_port, **kwargs)

        config, _ = _config(
            {"provider.test": [PUBLIC]},
            upstream_tls=trusted_client,
            open_connection=to_local_provider,
        )
        # The documentation address stands in for a public provider address in this test.
        config.policy = NetworkPolicy(
            allowed_ports=frozenset({443}),
            allowed_private_networks=(ipaddress.ip_network("203.0.113.0/24"),),
            resolver=lambda host, port: [PUBLIC],
        )
        server, port = await _serve(config)
        async with provider, server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"CONNECT provider.test:443 HTTP/1.1\r\nHost: provider.test:443\r\n\r\n")
            await writer.drain()
            status = await reader.readuntil(b"\r\n\r\n")
            assert status.startswith(b"HTTP/1.1 200")
            # Like Subfinder, the client does not verify the certificate it is shown.
            insecure = ssl.create_default_context()
            insecure.check_hostname = False
            insecure.verify_mode = ssl.CERT_NONE
            await writer.start_tls(insecure, server_hostname="provider.test")
            writer.write(b"GET /?q=%25.ornek.example HTTP/1.1\r\nHost: provider.test\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=10)
            writer.close()
        assert response.endswith(b'{"ok": true}')
        assert seen[0].startswith(b"GET /?q=%25.ornek.example HTTP/1.1")
        assert connections[0]["address"] == PUBLIC
        assert connections[0]["port"] == 443
        assert connections[0]["server_hostname"] == "provider.test"
        assert connections[0]["ssl"] is trusted_client

    asyncio.run(scenario())


def test_untrusted_provider_certificate_is_a_visible_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        server_tls, _ = _certificate(tmp_path, "provider.test", trusted=False)
        provider = await asyncio.start_server(
            lambda r, w: w.close(), "127.0.0.1", 0, ssl=server_tls
        )
        provider_port = provider.sockets[0].getsockname()[1]

        async def to_local_provider(address: str, port: int, **kwargs: Any) -> Any:
            return await asyncio.open_connection("127.0.0.1", provider_port, **kwargs)

        config, _ = _config({}, open_connection=to_local_provider)
        config.policy = NetworkPolicy(
            allowed_ports=frozenset({443}),
            allowed_private_networks=(ipaddress.ip_network("203.0.113.0/24"),),
            resolver=lambda host, port: [PUBLIC],
        )
        server, port = await _serve(config)
        async with provider, server:
            response = await _exchange(port, b"CONNECT provider.test:443 HTTP/1.1\r\n\r\n")
        assert response.startswith(
            b"HTTP/1.1 502 Tracehollow egress failed upstream_certificate_invalid"
        )

    caplog.set_level(logging.INFO, logger="tracehollow.egress.gateway")
    asyncio.run(scenario())
    record = next(r for r in caplog.records if r.getMessage() == "egress_tunnel")
    assert (getattr(record, "decision", None), getattr(record, "code", None)) == (
        "refused",
        "upstream_certificate_invalid",
    )


def test_refused_host_names_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        config, _ = _config(RESOLVER)
        server, port = await _serve(config)
        async with server:
            await _exchange(port, b"CONNECT investigated-target.example:443 HTTP/1.1\r\n\r\n")

    caplog.set_level(logging.INFO, logger="tracehollow.egress.gateway")
    asyncio.run(scenario())
    record = next(r for r in caplog.records if r.getMessage() == "egress_tunnel")
    assert getattr(record, "code", None) == "host_not_allowed"
    assert getattr(record, "provider_host", "missing") is None
    assert "investigated-target" not in str(vars(record))


def test_health_reports_policy_without_case_data() -> None:
    async def scenario() -> bytes:
        config, _ = _config(RESOLVER)
        server, port = await _serve(config)
        async with server:
            return await _exchange(port, b"GET /health HTTP/1.1\r\nHost: gateway\r\n\r\n")

    response = asyncio.run(scenario())
    assert response.startswith(b"HTTP/1.1 200")
    assert b'"allowed_ports": [443]' in response


def test_allowlist_matches_the_traced_provider_endpoints() -> None:
    assert provider_hosts() == {
        "crt.sh",
        "certificatedetails.com",
        "anubisdb.com",
        "api.hackertarget.com",
        "rapiddns.io",
        "api.certspotter.com",
        "www.virustotal.com",
        "otx.alienvault.com",
        "api.securitytrails.com",
    }
    assert all(spec["hosts"] for spec in SUBFINDER_SOURCES.values())
    production = gateway.config_from_environment()
    assert production.policy.allowed_ports == frozenset({443})
    assert production.policy.allowed_private_networks == ()
    assert production.upstream_tls.verify_mode == ssl.CERT_REQUIRED
    assert production.upstream_tls.check_hostname
