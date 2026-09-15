#!/usr/bin/env python3
"""Controlled stand-in for public sources, used only by scripts/verify-phase2.sh.

Serves synthetic web pages, feeds, a GitHub-API-shaped JSON interface and profile pages for the
username engine on one port inside the verification Compose project. Nothing here is a real
source; every name and address is fictitious. Standard library only.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BASE = "http://fixture-site:8080"

PAGE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>Örnek A.Ş. basın duyurusu</title>
<meta property="article:published_time" content="2026-09-10T09:30:00+03:00">
<script>document.body.innerHTML = "SYSTEM OVERRIDE"</script></head>
<body><h1>İzmir şubesi açıldı</h1>
<p>Örnek A.Ş. İzmir şubesi 2026-09-10 tarihinde Alsancak ofisinde açıldı.</p>
<p>Basın iletişimi: basin@ornek.example</p></body></html>"""

RSS_PAGE_1 = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>Örnek Haberler</title><link>{BASE}/web/ornek</link>
<atom:link rel="next" href="{BASE}/feed/rss?page=2"/>
<item><guid>haber-3</guid><title>İzmir şubesi</title><link>{BASE}/haber/3</link>
<pubDate>Thu, 10 Sep 2026 09:30:00 +0300</pubDate><description>Yeni şube açıldı.</description></item>
<item><guid>haber-2</guid><title>Ankara toplantısı</title><link>{BASE}/haber/2</link>
<pubDate>Tue, 08 Sep 2026 14:00:00 +0300</pubDate></item>
</channel></rss>"""

RSS_PAGE_2 = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Örnek Haberler</title>
<item><guid>haber-2</guid><title>Ankara toplantısı</title><link>{BASE}/haber/2</link></item>
<item><guid>haber-1</guid><title>Kuruluş</title><link>{BASE}/haber/1</link>
<pubDate>Tue, 01 Sep 2026 09:00:00 +0300</pubDate></item>
</channel></rss>"""

RSS_BROKEN_PAGINATION = RSS_PAGE_1.replace(f"{BASE}/feed/rss?page=2", f"{BASE}/feed/broken?page=2")

RATE_HEADERS = {
    "x-ratelimit-limit": "60",
    "x-ratelimit-remaining": "55",
    "x-ratelimit-used": "5",
    "x-ratelimit-reset": "4102444800",
    "x-ratelimit-resource": "core",
}

ACCOUNT = {
    "login": "ornek-dev",
    "id": 90210001,
    "node_id": "U_synthetic",
    "type": "User",
    "name": "Örnek Geliştirici (synthetic)",
    "company": "Örnek A.Ş.",
    "blog": "https://ornek.example",
    "location": "İzmir",
    "email": "dev@ornek.example",
    "bio": "Synthetic fixture account",
    "public_repos": 3,
    "followers": 2,
    "following": 1,
    "created_at": "2021-03-04T05:06:07Z",
    "updated_at": "2026-09-01T00:00:00Z",
    "html_url": "https://github.example/ornek-dev",
}


def repo(number: int) -> dict[str, object]:
    return {
        "id": 7000 + number,
        "full_name": f"ornek-dev/proje-{number}",
        "html_url": f"https://github.example/ornek-dev/proje-{number}",
        "description": f"Synthetic repository {number}",
        "fork": False,
        "language": "Python",
        "stargazers_count": number,
        "created_at": "2024-01-01T00:00:00Z",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TracehollowFixture/1"

    def log_message(self, *_args: object) -> None:
        return None

    def _send(
        self,
        status: int,
        body: bytes | str = b"",
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status: int, value: object, headers: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(value), "application/json; charset=utf-8", headers)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:  # noqa: C901 - a flat routing table is clearest here
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        page = query.get("page", ["1"])[0]

        if path == "/health":
            return self._send(200, "ok", "text/plain")
        # -- web pages ------------------------------------------------------------------------
        if path == "/web/ornek":
            return self._send(200, PAGE)
        if path == "/web/redirect":
            return self._send(302, headers={"location": "/web/ornek"})
        if path == "/web/to-metadata":
            return self._send(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        if path == "/web/missing":
            return self._send(404, "<h1>Bulunamadı</h1>")
        if path == "/web/big":
            return self._send(200, "<p>" + "büyük " * 1_200_000 + "</p>")
        if path == "/web/slow":
            time.sleep(40)
            return self._send(200, PAGE)
        if path == "/web/forbidden":
            return self._send(403, "forbidden")
        # -- feeds ----------------------------------------------------------------------------
        if path == "/feed/rss":
            body = RSS_PAGE_2 if page == "2" else RSS_PAGE_1
            return self._send(200, body, "application/rss+xml; charset=utf-8")
        if path == "/feed/paginated-broken":
            return self._send(200, RSS_BROKEN_PAGINATION, "application/rss+xml; charset=utf-8")
        if path == "/feed/broken":
            return self._send(500, "upstream error")
        if path == "/feed/malformed":
            return self._send(200, "<rss><channel><item><title>kırık", "application/rss+xml")
        # -- GitHub-shaped API ----------------------------------------------------------------
        if path == "/github-api/users/ornek-dev":
            return self._json(200, ACCOUNT, RATE_HEADERS)
        if path == "/github-api/users/ornek-dev/repos":
            if page == "2":
                return self._json(200, [repo(3)], RATE_HEADERS)
            next_url = f"{BASE}{path}?type=owner&sort=updated&per_page=2&page=2"
            return self._json(
                200, [repo(1), repo(2)], {**RATE_HEADERS, "link": f'<{next_url}>; rel="next"'}
            )
        if path == "/github-api/users/missing-dev":
            return self._json(404, {"message": "Not Found"}, RATE_HEADERS)
        if path == "/github-api/users/limited-dev":
            return self._json(
                403,
                {"message": "API rate limit exceeded (synthetic)"},
                {**RATE_HEADERS, "x-ratelimit-remaining": "0", "retry-after": "3600"},
            )
        if path == "/github-api/users/token-dev":
            if self.headers.get("authorization"):
                return self._json(401, {"message": "Bad credentials"})
            return self._json(200, {**ACCOUNT, "login": "token-dev", "id": 90210002, "public_repos": 0})
        # -- profile pages for the username engine ---------------------------------------------
        if path.startswith("/sherlock/status/"):
            return self._send(200 if path.endswith("/ornekdev") else 404, "profile")
        if path.startswith("/sherlock/message/"):
            if path.endswith("/ornekdev"):
                return self._send(200, "<h1>ornekdev</h1>")
            return self._send(200, "<p>There is no such user</p>")
        if path.startswith("/sherlock/limited/"):
            return self._send(429, "slow down", headers={"retry-after": "60"})
        if path.startswith("/sherlock/slow/"):
            time.sleep(6)
            return self._send(200 if path.endswith("/ornekdev") else 404, "profile")
        return self._send(404, "no fixture here", "text/plain")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 - container-internal fixture
