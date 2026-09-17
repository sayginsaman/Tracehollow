"""Outbound HTTP for public-source connectors, with SSRF protection.

Every URL a connector fetches on behalf of a case (a user-supplied page, a feed and its pagination
links, every redirect hop) goes through :func:`fetch`:

* only ``http``/``https`` on allowed ports, without credentials in the URL;
* the host name is resolved and **every** resolved address must be public. Loopback, private,
  link-local (including cloud metadata), multicast, reserved and unspecified addresses are
  rejected, including their IPv6 forms and IPv4 addresses embedded in IPv6 (mapped, 6to4,
  NAT64). Teredo is rejected outright;
* the TCP connection is made to the address that was checked, never to a second DNS answer, so
  DNS rebinding between check and connect is not possible; TLS still verifies the host name;
* redirects are followed manually and each hop is validated the same way;
* proxies from the environment are ignored; response size, redirects and wall-clock time are
  bounded.

Operators may allow specific private networks for lab or authorized internal targets
(``TRACEHOLLOW_COLLECTION_ALLOWED_PRIVATE_NETWORKS``). Loopback, link-local, unspecified,
multicast and reserved addresses stay blocked even then. Model endpoints and other trusted
infrastructure use their own configuration and never pass through this module.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import typing
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import httpcore2
import httpx2
from httpcore2._backends.base import SOCKET_OPTION
from httpcore2._backends.sync import SyncStream

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_ALLOWED_PORTS = frozenset({80, 443})
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")
# Never reachable, whatever the operator allowlist says.
_ALWAYS_BLOCKED: tuple[IPNetwork, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("224.0.0.0/4"),
    ipaddress.IPv4Network("240.0.0.0/4"),
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),
    # AWS Nitro IPv6 instance metadata endpoint (inside fc00::/7).
    ipaddress.IPv6Network("fd00:ec2::254/128"),
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


class DestinationBlockedError(Exception):
    """The URL or one of its resolved addresses is not a permitted collection destination."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class FetchError(Exception):
    """A network-level failure (DNS, connect, TLS, timeout, protocol) for one request."""

    def __init__(self, code: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError("dns_failure", f"The host name {host!r} could not be resolved.") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64 or address in _NAT64_LOCAL:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


@dataclass(frozen=True)
class NetworkPolicy:
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS
    allowed_private_networks: tuple[IPNetwork, ...] = ()
    resolver: Callable[[str, int], list[str]] = field(default=system_resolver)

    def check_address(self, raw: str) -> IPAddress:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise DestinationBlockedError(
                "invalid_address", "The destination resolved to an invalid address."
            ) from exc
        if isinstance(address, ipaddress.IPv6Address):
            if address.teredo is not None:
                raise DestinationBlockedError(
                    "blocked_address", "Teredo addresses are not permitted destinations."
                )
            embedded = _embedded_ipv4(address)
            if embedded is not None:
                self.check_address(str(embedded))
                return address
        if any(address in network for network in _ALWAYS_BLOCKED):
            raise DestinationBlockedError(
                "blocked_address",
                "The destination is a loopback, link-local, metadata, multicast, reserved or "
                "unspecified address.",
            )
        if address.is_global:
            return address
        if any(address in network for network in self.allowed_private_networks):
            return address
        raise DestinationBlockedError(
            "blocked_address",
            "The destination is a private or otherwise non-public address that is not in the "
            "operator's allowed private networks.",
        )

    def resolve(self, host: str, port: int) -> list[str]:
        """Resolve ``host`` and require every address to be permitted."""
        if port not in self.allowed_ports:
            raise DestinationBlockedError(
                "blocked_port", f"Port {port} is not an allowed collection port."
            )
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        addresses = [str(literal)] if literal is not None else self.resolver(host, port)
        if not addresses:
            raise FetchError("dns_failure", f"The host name {host!r} has no addresses.")
        for address in addresses:
            self.check_address(address)
        return addresses


@dataclass(frozen=True)
class CheckedUrl:
    url: httpx2.URL
    host: str
    port: int


def check_url(raw: str | httpx2.URL, policy: NetworkPolicy, *, resolve: bool = True) -> CheckedUrl:
    """Validate the URL shape and its resolved addresses without contacting it.

    ``resolve=False`` checks only the shape, port and literal IP addresses (host names are then
    checked when the request is made).
    """
    try:
        url = raw if isinstance(raw, httpx2.URL) else httpx2.URL(raw)
    except (httpx2.InvalidURL, ValueError, TypeError) as exc:
        raise DestinationBlockedError("invalid_url", "The URL could not be parsed.") from exc
    if url.scheme not in ("http", "https"):
        raise DestinationBlockedError("blocked_scheme", "Only http and https URLs can be fetched.")
    if url.userinfo:
        raise DestinationBlockedError(
            "credentials_in_url", "URLs with embedded credentials are not fetched."
        )
    host = url.host
    if not host:
        raise DestinationBlockedError("invalid_url", "The URL has no host.")
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(_BLOCKED_HOST_SUFFIXES):
        raise DestinationBlockedError(
            "blocked_host", "Local and internal host names are not permitted destinations."
        )
    port = url.port or (443 if url.scheme == "https" else 80)
    if resolve:
        policy.resolve(host, port)
    else:
        if port not in policy.allowed_ports:
            raise DestinationBlockedError(
                "blocked_port", f"Port {port} is not an allowed collection port."
            )
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            policy.check_address(str(literal))
    return CheckedUrl(url=url, host=host, port=port)


class _GuardedBackend(httpcore2.SyncBackend):
    """Resolve, check and connect to exactly the checked address."""

    def __init__(self, policy: NetworkPolicy, peers: list[str]) -> None:
        self._policy = policy
        self._peers = peers

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore2.NetworkStream:
        addresses = self._policy.resolve(host, port)
        last_error: OSError | None = None
        for address in addresses:
            try:
                sock = socket.create_connection((address, port), timeout)
            except TimeoutError as exc:
                raise httpcore2.ConnectTimeout(str(exc)) from exc
            except OSError as exc:
                last_error = exc
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._peers.append(address)
            return SyncStream(sock)
        raise httpcore2.ConnectError(str(last_error or "connection failed"))


class GuardedTransport(httpx2.HTTPTransport):
    def __init__(self, policy: NetworkPolicy, peers: list[str]) -> None:
        super().__init__(http1=True, http2=False, retries=0)
        self._pool = httpcore2.ConnectionPool(
            ssl_context=httpx2.create_ssl_context(),
            max_connections=4,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_GuardedBackend(policy, peers),
        )


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    truncated: bool
    redirects: list[dict[str, typing.Any]]
    elapsed_ms: int
    remote_address: str | None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def charset(self) -> str | None:
        for part in self.headers.get("content-type", "").split(";")[1:]:
            name, _, value = part.strip().partition("=")
            if name.lower() == "charset" and value:
                return value.strip("\"' ").lower()
        return None

    def metadata(self) -> dict[str, typing.Any]:
        """Provenance recorded with the evidence (no cookies or authorization headers)."""
        kept = {
            key: value
            for key, value in self.headers.items()
            if key
            in {
                "content-type",
                "content-length",
                "last-modified",
                "etag",
                "date",
                "server",
                "retry-after",
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "x-ratelimit-reset",
                "x-ratelimit-resource",
            }
        }
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "http_status": self.status_code,
            "redirects": self.redirects,
            "response_headers": kept,
            "truncated": self.truncated,
            "bytes_read": len(self.content),
            "elapsed_ms": self.elapsed_ms,
            "remote_address": self.remote_address,
        }


_KEPT_HEADER_LIMIT = 4096


def fetch(
    url: str,
    *,
    policy: NetworkPolicy,
    headers: dict[str, str] | None = None,
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int = 5,
    cancelled: Callable[[], bool] | None = None,
    transport: httpx2.BaseTransport | None = None,
    same_origin_redirects_only: bool = False,
) -> FetchResult:
    """GET ``url`` within the policy. Raises DestinationBlockedError or FetchError.

    ``transport`` replaces the guarded transport in tests; URL and address checks still run.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    peers: list[str] = []
    active_transport = transport or GuardedTransport(policy, peers)
    redirects: list[dict[str, typing.Any]] = []
    current = check_url(url, policy)
    origin = (current.url.scheme, current.host.lower(), current.port)
    request_headers = {"accept-encoding": "gzip, deflate", **(headers or {})}
    with httpx2.Client(
        transport=active_transport,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx2.Timeout(min(timeout_seconds, 30.0), connect=min(timeout_seconds, 10.0)),
    ) as client:
        while True:
            if cancelled is not None and cancelled():
                raise FetchError("canceled", "Canceled before the request completed.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("timeout", "The request exceeded its time limit.")
            try:
                with client.stream("GET", current.url, headers=request_headers) as response:
                    if response.is_redirect and "location" in response.headers:
                        if len(redirects) >= max_redirects:
                            raise FetchError(
                                "too_many_redirects",
                                f"More than {max_redirects} redirects.",
                                retryable=False,
                            )
                        target = current.url.join(response.headers["location"])
                        redirects.append({"status": response.status_code, "from": str(current.url)})
                        current = check_url(target, policy)
                        if (
                            same_origin_redirects_only
                            and (
                                current.url.scheme,
                                current.host.lower(),
                                current.port,
                            )
                            != origin
                        ):
                            raise DestinationBlockedError(
                                "cross_origin_redirect",
                                "A redirect left the origin this connector is scoped to.",
                            )
                        continue
                    content, truncated = _read_bounded(response, max_bytes, deadline, cancelled)
                    return FetchResult(
                        requested_url=url,
                        final_url=str(current.url),
                        status_code=response.status_code,
                        headers={
                            key.lower(): value[:_KEPT_HEADER_LIMIT]
                            for key, value in response.headers.items()
                        },
                        content=content,
                        truncated=truncated,
                        redirects=redirects,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        remote_address=peers[-1] if peers else None,
                    )
            except (httpx2.TimeoutException, TimeoutError) as exc:
                raise FetchError("timeout", "The source did not respond in time.") from exc
            except httpx2.ConnectError as exc:
                raise FetchError("connect_failed", "The source could not be reached.") from exc
            except httpx2.RemoteProtocolError as exc:
                raise FetchError("protocol_error", "The source sent an invalid response.") from exc
            except httpx2.DecodingError as exc:
                raise FetchError(
                    "decoding_error", "The response body could not be decoded.", retryable=False
                ) from exc
            except httpx2.TransportError as exc:
                raise FetchError("transport_error", "The request failed in transit.") from exc


def post(
    url: str,
    *,
    policy: NetworkPolicy,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
    max_response_bytes: int = 64 * 1024,
    transport: httpx2.BaseTransport | None = None,
) -> FetchResult:
    """POST ``body`` to ``url`` within the same address policy as collection.

    Used by the optional webhook adapter. Redirects are never followed (a 3xx answer is returned
    as the result), the response body is read only up to ``max_response_bytes``, and environment
    proxies are ignored.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    peers: list[str] = []
    checked = check_url(url, policy)
    with httpx2.Client(
        transport=transport or GuardedTransport(policy, peers),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx2.Timeout(min(timeout_seconds, 30.0), connect=min(timeout_seconds, 10.0)),
    ) as client:
        try:
            with client.stream("POST", checked.url, headers=headers, content=body) as response:
                content, truncated = _read_bounded(response, max_response_bytes, deadline, None)
                return FetchResult(
                    requested_url=url,
                    final_url=str(checked.url),
                    status_code=response.status_code,
                    headers={
                        key.lower(): value[:_KEPT_HEADER_LIMIT]
                        for key, value in response.headers.items()
                    },
                    content=content,
                    truncated=truncated,
                    redirects=[],
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    remote_address=peers[-1] if peers else None,
                )
        except (httpx2.TimeoutException, TimeoutError) as exc:
            raise FetchError("timeout", "The receiver did not respond in time.") from exc
        except httpx2.ConnectError as exc:
            raise FetchError("connect_failed", "The receiver could not be reached.") from exc
        except httpx2.RemoteProtocolError as exc:
            raise FetchError("protocol_error", "The receiver sent an invalid response.") from exc
        except httpx2.TransportError as exc:
            raise FetchError("transport_error", "The request failed in transit.") from exc


def _read_bounded(
    response: httpx2.Response,
    max_bytes: int,
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> tuple[bytes, bool]:
    buffer = bytearray()
    for chunk in response.iter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            return bytes(buffer[:max_bytes]), True
        if time.monotonic() > deadline:
            raise FetchError("timeout", "Reading the response exceeded the time limit.")
        if cancelled is not None and cancelled():
            raise FetchError("canceled", "Canceled while reading the response.")
    return bytes(buffer), False


def parse_networks(values: Sequence[str]) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for value in values:
        network = ipaddress.ip_network(value.strip(), strict=False)
        if any(network.overlaps(blocked) for blocked in _ALWAYS_BLOCKED):
            raise ValueError(
                f"{value} overlaps loopback, link-local, metadata, multicast or reserved space"
            )
        networks.append(network)
    return tuple(networks)
