"""Isolated Sherlock runner: ``python -m app.connectors.engines.sherlock_runner`` < config JSON.

Runs in its own process (started by the ``username.sherlock`` connector) and uses Sherlock's
library API with the curated site definitions it is given, so the Sherlock command line's update
check and remote manifest downloads never happen. Every TCP connection, including redirect hops,
is resolved and checked by the Tracehollow network policy before connecting. Output is one JSON
object per line:

* ``{"type": "progress", "site": ...}`` as each site finishes;
* ``{"type": "blocked", "host": ..., "code": ...}`` when a destination was refused;
* ``{"type": "result", "site": ..., "status": ..., "http_status": ..., ...}`` per site at the end;
* ``{"type": "done"}``.
"""

from __future__ import annotations

import json
import socket
import sys
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _install_network_guard(config: dict[str, Any]) -> None:
    import urllib3.util.connection as urllib3_connection

    from app.connectors.netguard import (
        DestinationBlockedError,
        FetchError,
        NetworkPolicy,
        parse_networks,
    )

    policy = NetworkPolicy(
        allowed_ports=frozenset(int(port) for port in config["allowed_ports"]),
        allowed_private_networks=parse_networks(config["allowed_private_networks"]),
    )
    original = urllib3_connection.create_connection

    def guarded(
        address: tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
        source_address: tuple[str, int] | None = None,
        socket_options: Any = None,
    ) -> socket.socket:
        host, port = address
        try:
            addresses = policy.resolve(host, port)
        except DestinationBlockedError as exc:
            _emit({"type": "blocked", "host": host, "code": exc.code})
            raise OSError(f"destination blocked: {exc.code}") from None
        except FetchError as exc:
            raise OSError(exc.detail) from None
        last: OSError | None = None
        for candidate in addresses:
            try:
                return original((candidate, port), timeout, source_address, socket_options)
            except OSError as exc:
                last = exc
        raise last or OSError("connection failed")

    urllib3_connection.create_connection = guarded


def main() -> int:
    config = json.loads(sys.stdin.read())
    _install_network_guard(config)

    from sherlock_project.notify import QueryNotify
    from sherlock_project.sherlock import sherlock

    class Progress(QueryNotify):  # type: ignore[misc]
        def update(self, result: Any) -> None:
            _emit({"type": "progress", "site": result.site_name})

    results = sherlock(
        config["username"], config["sites"], Progress(), timeout=int(config["timeout"])
    )
    for site, entry in results.items():
        status = entry["status"]
        http_status = entry.get("http_status")
        _emit(
            {
                "type": "result",
                "site": site,
                "status": str(status.status),
                "http_status": http_status if isinstance(http_status, int) else None,
                "url": entry.get("url_user") or None,
                "context": status.context,
                "query_time": status.query_time,
            }
        )
    _emit({"type": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
