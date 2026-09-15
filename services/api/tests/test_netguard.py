"""SSRF protection for collection requests (PRD Phase 2 acceptance criterion 4)."""

from __future__ import annotations

import http.server
import ipaddress
import itertools

import httpx2
import pytest

from app.connectors import netguard
from app.connectors.netguard import DestinationBlockedError, FetchError, NetworkPolicy
from tests.collection_helpers import (
    PUBLIC_ADDRESS,
    Router,
    http_server,
    non_loopback_address,
    public_resolver,
    raise_error,
    reachable,
    resolver_map,
    respond,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.10.20.30",
        "0.0.0.0",  # noqa: S104 - the address under test, not a bind
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "100.64.0.1",
        "169.254.169.254",
        "169.254.170.2",
        "224.0.0.251",
        "255.255.255.255",
        "240.0.0.1",
        "198.18.0.1",
        "192.0.2.10",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%eth0",
        "fc00::1",
        "fd00:ec2::254",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "2002:7f00:0001::",
        "2002:a9fe:a9fe::",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "2001:db8::1",
    ],
)
def test_non_public_addresses_are_blocked(address: str) -> None:
    with pytest.raises(DestinationBlockedError):
        NetworkPolicy().check_address(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert NetworkPolicy().check_address(address) == ipaddress.ip_address(address)


def test_operator_allowlist_opens_private_networks_but_never_loopback_or_metadata() -> None:
    policy = NetworkPolicy(allowed_private_networks=netguard.parse_networks(["10.20.0.0/16"]))
    policy.check_address("10.20.30.40")
    with pytest.raises(DestinationBlockedError):
        policy.check_address("10.21.0.1")
    for forbidden in ("127.0.0.0/8", "169.254.169.254/32", "0.0.0.0/0", "::/0"):
        with pytest.raises(ValueError, match="overlaps"):
            netguard.parse_networks([forbidden])


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://example.org/file", "blocked_scheme"),
        ("file:///etc/passwd", "blocked_scheme"),
        ("gopher://example.org/", "blocked_scheme"),
        ("http://user:pass@example.org/", "credentials_in_url"),
        ("http://localhost/", "blocked_host"),
        ("http://metadata.google.internal/computeMetadata/v1/", "blocked_host"),
        ("http://printer.local/", "blocked_host"),
        ("http://example.org:22/", "blocked_port"),
        ("http://127.0.0.1/", "blocked_address"),
        ("http://[::1]:80/", "blocked_address"),
        ("http://[::ffff:169.254.169.254]/latest/meta-data/", "blocked_address"),
        ("http://2130706433/", "blocked_address"),
        ("http://0x7f000001/", "blocked_address"),
    ],
)
def test_urls_are_checked_before_any_request(url: str, code: str) -> None:
    def resolver(host: str, _port: int) -> list[str]:
        # Numeric host forms resolve to loopback like a real resolver would.
        return ["127.0.0.1"] if host in ("2130706433", "0x7f000001") else [PUBLIC_ADDRESS]

    router = Router()
    with pytest.raises(DestinationBlockedError) as caught:
        netguard.fetch(
            url,
            policy=NetworkPolicy(resolver=resolver),
            max_bytes=1024,
            timeout_seconds=5,
            transport=router.transport,
        )
    assert caught.value.code == code
    assert router.requests == []


def test_a_name_with_any_private_answer_is_blocked() -> None:
    policy = NetworkPolicy(resolver=resolver_map({"mixed.example": [PUBLIC_ADDRESS, "10.0.0.7"]}))
    with pytest.raises(DestinationBlockedError):
        netguard.check_url("https://mixed.example/", policy)


def test_redirects_to_prohibited_destinations_are_not_followed() -> None:
    router = Router()
    router.add(
        "https://public.example/start",
        respond(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}),
    )
    router.add("http://169.254.169.254/latest/meta-data/", respond(200, body="secret"))
    with pytest.raises(DestinationBlockedError) as caught:
        netguard.fetch(
            "https://public.example/start",
            policy=NetworkPolicy(resolver=public_resolver),
            max_bytes=1024,
            timeout_seconds=5,
            transport=router.transport,
        )
    assert caught.value.code == "blocked_address"
    assert router.urls() == ["https://public.example/start"]


def test_redirect_to_a_host_resolving_privately_is_blocked_and_relative_redirects_follow() -> None:
    router = Router()
    router.add("https://public.example/a", respond(301, headers={"location": "/b"}))
    router.add(
        "https://public.example/b", respond(302, headers={"location": "https://intra.example/"})
    )
    policy = NetworkPolicy(resolver=resolver_map({"intra.example": ["192.168.0.10"]}))
    with pytest.raises(DestinationBlockedError):
        netguard.fetch(
            "https://public.example/a",
            policy=policy,
            max_bytes=1024,
            timeout_seconds=5,
            transport=router.transport,
        )
    assert router.urls() == ["https://public.example/a", "https://public.example/b"]


def test_redirect_limit_and_same_origin_scope() -> None:
    router = Router()
    for index in range(7):
        router.add(
            f"https://public.example/{index}",
            respond(302, headers={"location": f"https://public.example/{index + 1}"}),
        )
    with pytest.raises(FetchError) as caught:
        netguard.fetch(
            "https://public.example/0",
            policy=NetworkPolicy(resolver=public_resolver),
            max_bytes=1024,
            timeout_seconds=5,
            max_redirects=5,
            transport=router.transport,
        )
    assert caught.value.code == "too_many_redirects"

    scoped = Router()
    scoped.add(
        "https://api.example/x", respond(302, headers={"location": "https://other.example/"})
    )
    with pytest.raises(DestinationBlockedError) as blocked:
        netguard.fetch(
            "https://api.example/x",
            policy=NetworkPolicy(resolver=public_resolver),
            max_bytes=1024,
            timeout_seconds=5,
            transport=scoped.transport,
            same_origin_redirects_only=True,
        )
    assert blocked.value.code == "cross_origin_redirect"


def test_response_size_is_bounded_and_provenance_recorded() -> None:
    router = Router().add(
        "https://public.example/big",
        respond(200, body=b"x" * 5000, headers={"set-cookie": "session=abc", "etag": '"v1"'}),
    )
    result = netguard.fetch(
        "https://public.example/big",
        policy=NetworkPolicy(resolver=public_resolver),
        max_bytes=1000,
        timeout_seconds=5,
        transport=router.transport,
    )
    assert result.truncated
    assert len(result.content) == 1000
    metadata = result.metadata()
    assert metadata["truncated"] is True
    assert metadata["response_headers"]["etag"] == '"v1"'
    assert "set-cookie" not in metadata["response_headers"]


def test_timeouts_and_connection_failures_are_network_errors_not_empty_results() -> None:
    for error, code in (
        (httpx2.ReadTimeout("slow"), "timeout"),
        (httpx2.ConnectError("refused"), "connect_failed"),
    ):
        router = Router().add("https://public.example/", raise_error(error))
        with pytest.raises(FetchError) as caught:
            netguard.fetch(
                "https://public.example/",
                policy=NetworkPolicy(resolver=public_resolver),
                max_bytes=1024,
                timeout_seconds=5,
                transport=router.transport,
            )
        assert caught.value.code == code


def test_dns_rebinding_between_check_and_connect_is_blocked() -> None:
    """The guarded transport resolves again and connects only to the address it checked."""
    answers = itertools.chain([PUBLIC_ADDRESS], itertools.repeat("127.0.0.1"))
    policy = NetworkPolicy(resolver=lambda _host, _port: [next(answers)])
    with pytest.raises(DestinationBlockedError):
        netguard.fetch("http://rebind.example/", policy=policy, max_bytes=1024, timeout_seconds=5)


class _Hello(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        body = b"<title>Local fixture</title><p>merhaba</p>"
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_guarded_transport_connects_to_the_checked_address_on_an_allowed_private_network() -> None:
    address = non_loopback_address()
    if address is None:
        pytest.skip("no non-loopback IPv4 interface")
    with http_server(address, _Hello) as (host, port):
        if not reachable(host, port):
            pytest.skip(f"this machine cannot connect to its own address {host}")
        url = f"http://fixture.example:{port}/page"
        blocked = NetworkPolicy(
            allowed_ports=frozenset({port}), resolver=resolver_map({"fixture.example": [host]})
        )
        if ipaddress.ip_address(host).is_global:
            pytest.skip("interface address is public; the private-network check does not apply")
        with pytest.raises(DestinationBlockedError):
            netguard.fetch(url, policy=blocked, max_bytes=4096, timeout_seconds=5)
        allowed = NetworkPolicy(
            allowed_ports=frozenset({port}),
            allowed_private_networks=netguard.parse_networks([f"{host}/32"]),
            resolver=resolver_map({"fixture.example": [host]}),
        )
        result = netguard.fetch(url, policy=allowed, max_bytes=4096, timeout_seconds=5)
    assert result.status_code == 200
    assert result.remote_address == host
    assert b"merhaba" in result.content
