"""English documentation demo: a fictional company's public pages and announcement feed.

Served by the fixture container of compose.demo.yaml and used by scripts/seed_demo.py to build the
dataset shown in the README and the guides. Everything here is invented: Aurora Freight is not a
real company, aurora-freight.example is a reserved example domain and 203.0.113.42 is a TEST-NET-3
address. Nothing in this file is collected from a real source.

The feed has two versions so a monitor can find a controlled change between two collections:
`v1` is the baseline, `v2` renames one announcement and adds another. The control endpoint answers
loopback requests only, so the seeding script switches versions through
`docker compose exec fixture-site`, while the collector can only read the pages.
"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import parse_qs

BASE = "http://fixture-site:8080"
LOCK = threading.Lock()
STATE: dict[str, Any] = {"feed": "v1", "requests": 0}

PRESS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Aurora Freight press room (synthetic)</title>
<meta property="article:published_time" content="2026-08-14T09:00:00+00:00"></head>
<body>
<h1>Aurora Freight opens its Rotterdam hub</h1>
<p>Aurora Freight Ltd announced on 14 August 2026 that its Rotterdam distribution hub is open.
The company says the hub serves northern Europe and is operated with its partner Northwind
Logistics.</p>
<p>Press contact: press@aurora-freight.example. Registered office: Aurora Freight Ltd,
1 Example Quay, Rotterdam.</p>
<p>Service status updates are published at https://status.aurora-freight.example.</p>
</body></html>"""

ANNOUNCEMENTS = {
    "v1": [
        ("aurora-2026-08-14", "Rotterdam hub opens", "Mon, 14 Aug 2026 09:00:00 +0000"),
        ("aurora-2026-08-21", "Summer service schedule", "Mon, 21 Aug 2026 08:30:00 +0000"),
    ],
    # The controlled change: the schedule notice is renamed and a new announcement appears.
    "v2": [
        ("aurora-2026-08-14", "Rotterdam hub opens", "Mon, 14 Aug 2026 09:00:00 +0000"),
        ("aurora-2026-08-21", "Summer service schedule (revised)", "Mon, 21 Aug 2026 08:30:00 +0000"),
        ("aurora-2026-09-02", "New partner: Northwind Logistics", "Wed, 02 Sep 2026 07:45:00 +0000"),
    ],
}


def _feed(version: str) -> str:
    items = "".join(
        f"<item><guid>{guid}</guid><title>{title}</title>"
        f"<link>{BASE}/demo/announcement/{guid}</link><pubDate>{published}</pubDate>"
        f"<description>Synthetic announcement published by the Tracehollow demo fixture.</description></item>"
        for guid, title, published in ANNOUNCEMENTS[version]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
        f"<channel><title>Aurora Freight announcements (synthetic)</title>"
        f"<link>{BASE}/demo/press</link>"
        "<description>Fictional announcements for documentation screenshots.</description>"
        f"{items}</channel></rss>"
    )


def handle_get(handler: Any, path: str, raw_query: str) -> bool:
    if path == "/demo/press":
        handler._send(200, PRESS_PAGE)
        return True
    if path == "/demo/feed":
        with LOCK:
            STATE["requests"] += 1
            version = STATE["feed"]
        handler._send(200, _feed(version), "application/rss+xml; charset=utf-8")
        return True
    if path == "/demo/control/set" and handler.client_address[0] in ("127.0.0.1", "::1"):
        query = parse_qs(raw_query)
        with LOCK:
            if "feed" in query:
                STATE["feed"] = query["feed"][0]
            handler._json(200, STATE)
        return True
    return False
