"""Phase 5 stand-ins: a feed that changes on command and a local webhook receiver.

Used by scripts/verify-phase5.sh. Control endpoints answer only loopback clients, so the
verifier changes the feed through `docker compose exec fixture-site` while the collector, which
reaches this container over the egress network, can only read the feed and post deliveries.
Nothing here is a real source or a real notification service; every value is synthetic.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import parse_qs

BASE = "http://fixture-site:8080"
LOCK = threading.Lock()
STATE: dict[str, Any] = {
    "feed": "v1",
    "feed_requests": 0,
    "slow_seconds": 0.0,
    "webhook_failures_left": 0,
    "deliveries": [],
}

ITEMS = {
    "v1": [
        ("izleme-1", "Duyuru 1: İzmir ofisi", "2026-09-10T09:00:00+03:00"),
        ("izleme-2", "Duyuru 2: Ankara toplantısı", "2026-09-11T10:00:00+03:00"),
    ],
    # izleme-1 changes its title, izleme-3 is new, izleme-2 stays.
    "v2": [
        ("izleme-1", "Duyuru 1: İzmir ofisi taşındı", "2026-09-10T09:00:00+03:00"),
        ("izleme-2", "Duyuru 2: Ankara toplantısı", "2026-09-11T10:00:00+03:00"),
        ("izleme-3", "Duyuru 3: Yeni şube", "2026-09-12T11:00:00+03:00"),
    ],
    # A complete collection in which izleme-2 is genuinely absent.
    "v3": [
        ("izleme-1", "Duyuru 1: İzmir ofisi taşındı", "2026-09-10T09:00:00+03:00"),
        ("izleme-3", "Duyuru 3: Yeni şube", "2026-09-12T11:00:00+03:00"),
    ],
}


def _rss(items: list[tuple[str, str, str]], next_page: str | None = None) -> str:
    entries = "".join(
        f"<item><guid>{guid}</guid><title>{title}</title><link>{BASE}/monitor/haber/{guid}</link>"
        f"<pubDate>{published}</pubDate></item>"
        for guid, title, published in items
    )
    link = f'<atom:link rel="next" href="{next_page}"/>' if next_page else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        f"<channel><title>İzleme akışı (synthetic)</title><link>{BASE}/monitor/feed</link>{link}{entries}</channel></rss>"
    )


def _feed(handler: Any, page: str) -> None:
    with LOCK:
        STATE["feed_requests"] += 1
        version = STATE["feed"]
        slow = float(STATE["slow_seconds"])
    if slow:
        time.sleep(slow)
    if version == "partial":
        # Page 1 is served with a link to page 2, which is rate limited for an hour.
        if page == "2":
            return handler._send(429, "slow down", "text/plain", {"retry-after": "3600"})
        return handler._send(200, _rss(ITEMS["v2"][:1], f"{BASE}/monitor/feed?page=2"), "application/rss+xml; charset=utf-8")
    if version == "down":
        return handler._send(503, "maintenance", "text/plain")
    if version == "paged":
        items = ITEMS["v2"]
        if page == "2":
            return handler._send(200, _rss(items[1:]), "application/rss+xml; charset=utf-8")
        return handler._send(200, _rss(items[:1], f"{BASE}/monitor/feed?page=2"), "application/rss+xml; charset=utf-8")
    return handler._send(200, _rss(ITEMS[version]), "application/rss+xml; charset=utf-8")


def _local(handler: Any) -> bool:
    return handler.client_address[0] in ("127.0.0.1", "::1")


def handle_get(handler: Any, path: str, raw_query: str) -> bool:
    query = parse_qs(raw_query)
    if path == "/monitor/feed":
        _feed(handler, query.get("page", ["1"])[0])
        return True
    if path == "/monitor/control/state" and _local(handler):
        with LOCK:
            handler._json(200, STATE)
        return True
    if path == "/monitor/control/set" and _local(handler):
        with LOCK:
            if "feed" in query:
                STATE["feed"] = query["feed"][0]
            if "slow" in query:
                STATE["slow_seconds"] = float(query["slow"][0])
            if "webhook_failures" in query:
                STATE["webhook_failures_left"] = int(query["webhook_failures"][0])
            if "reset_deliveries" in query:
                STATE["deliveries"] = []
            handler._json(200, STATE)
        return True
    return False


def handle_post(handler: Any, path: str) -> bool:
    if path not in ("/webhook/tracehollow", "/webhook/other"):
        return False
    length = min(int(handler.headers.get("content-length") or 0), 1024 * 1024)
    body = handler.rfile.read(length)
    with LOCK:
        failing = STATE["webhook_failures_left"] > 0
        if failing:
            STATE["webhook_failures_left"] -= 1
        STATE["deliveries"].append(
            {
                "path": path,
                "client": handler.client_address[0],
                "received_at": time.time(),
                "status": 500 if failing else 204,
                "headers": {
                    key.lower(): value
                    for key, value in handler.headers.items()
                    if key.lower().startswith("x-tracehollow-") or key.lower() in ("content-type", "user-agent")
                },
                "body": body.decode("utf-8", "replace"),
            }
        )
    if failing:
        # A short Retry-After keeps the verification quick; the adapter honours it.
        handler._send(500, "receiver failure (fixture)", "text/plain", {"retry-after": "5"})
    else:
        handler._send(204)
    return True


def parse_body(delivery: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(delivery["body"])
    return value
