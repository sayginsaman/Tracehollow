"""Fixture-based contract tests for the Phase 2 connectors (PRD Phase 2 acceptance 2-4, 6).

Every connector is exercised for success, verified no findings, 401/403, 429, timeout,
malformed content and partial pagination where the source has these states. No test contacts
a real source.
"""

# ruff: noqa: E501, PT011 - recorded engine output is kept verbatim; validation loops check rejection only

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.config import load_settings
from app.connectors import sherlock as sherlock_module
from app.connectors.base import ConnectorError, ConnectorPage, VerificationStatus
from app.connectors.engines import process as engine_process
from app.connectors.engines import subfinder_runner
from app.connectors.engines.process import ProcessResult
from app.connectors.github import GitHubAccountConnector
from app.connectors.rss import RssFeedConnector
from app.connectors.sherlock import SherlockUsernameConnector, classify
from app.connectors.subfinder import (
    SubfinderDomainConnector,
    in_scope,
    is_crtsh_database_path,
    parse_stderr,
)
from app.connectors.web import PublicWebPageConnector
from app.queries.models import ConnectorOutcome
from tests.collection_helpers import Router, context, raise_error, request, resolver_map, respond

API = "https://api.github.com"


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "env": "test",
        "database_password": "x" * 16,
        "redis_password": "y" * 16,
        "secret_key": "z" * 32,
    }
    values.update(overrides)
    return load_settings(**values)


def _outcome(connector: Any, req: Any) -> ConnectorError:
    with pytest.raises(ConnectorError) as caught:
        connector.fetch_page(req)
    return caught.value


# -- public web page ---------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>Örnek A.Ş. — Hakkımızda</title>
<meta name="description" content="Kurumsal tanıtım">
<meta property="article:published_time" content="2026-09-01T10:00:00+03:00">
<script>document.write('<p>injected</p>')</script><style>p{color:red}</style></head>
<body><h1>İstanbul ofisi</h1><p>İletişim: bilgi@ornek.example</p>
<a href="/iletisim">İletişim</a><a href="https://other.example/x">Dış</a></body></html>"""


def test_web_page_success_keeps_snapshot_and_derived_text() -> None:
    router = Router()
    router.add("https://ornek.example/", respond(301, headers={"location": "/hakkimizda"}))
    router.add("https://ornek.example/hakkimizda", respond(200, body=PAGE))
    ctx, record = context(router)
    page = PublicWebPageConnector().fetch_page(request("url", "https://ornek.example/", ctx))

    snapshot, text = page.evidence
    assert snapshot.kind == "html"
    assert snapshot.content == PAGE.encode()
    assert snapshot.source_reference == "https://ornek.example/hakkimizda"
    assert snapshot.collection_metadata["redirects"][0]["status"] == 301
    assert snapshot.collection_metadata["http_status"] == 200
    assert snapshot.source_published_at == datetime(2026, 9, 1, 7, tzinfo=UTC)
    assert text.derived_from == "snapshot"
    body = text.content.decode()
    assert body.splitlines()[0] == "Örnek A.Ş. — Hakkımızda"
    assert "İstanbul ofisi" in body
    assert "injected" not in body
    assert "color:red" not in body
    observation = page.observations[0]
    assert observation.observation_type == "web_page"
    assert observation.evidence_key == "text"
    assert observation.payload["links"] == [
        "https://ornek.example/iletisim",
        "https://other.example/x",
    ]
    assert {entity.entity_type for entity in page.entities} == {"url", "domain"}
    assert page.relationships[0].predicate == "hosted_on"
    assert page.items == 1
    assert page.incomplete_reason is None
    assert record["paced"] == [("host:ornek.example", 2.0)]


def test_web_page_decodes_legacy_turkish_charset_but_stores_original_bytes() -> None:
    raw = "<title>Şirket</title><p>Güncel duyuru</p>".encode("windows-1254")
    router = Router().add(
        "https://eski.example/",
        respond(200, body=raw, content_type="text/html; charset=windows-1254"),
    )
    ctx, _ = context(router)
    page = PublicWebPageConnector().fetch_page(request("url", "https://eski.example/", ctx))
    assert page.evidence[0].content == raw
    assert page.evidence[0].collection_metadata["decoded_with"] == "windows-1254"
    assert "Güncel duyuru" in page.evidence[1].content.decode()


def test_web_page_verified_not_found_is_explicit() -> None:
    router = Router().add("https://ornek.example/yok", respond(404, body="Not found"))
    ctx, _ = context(router)
    page = PublicWebPageConnector().fetch_page(request("url", "https://ornek.example/yok", ctx))
    assert page.outcome_hint == ConnectorOutcome.NO_FINDINGS
    assert page.items == 0
    assert page.evidence[0].indexable is False
    assert "does not show that the content never existed" in page.notes[0]


@pytest.mark.parametrize(
    ("handler", "outcome", "retry_after"),
    [
        (respond(401), ConnectorOutcome.AUTHENTICATION_REQUIRED, None),
        (respond(403), ConnectorOutcome.ACCESS_DENIED, None),
        (respond(429, headers={"retry-after": "42"}), ConnectorOutcome.RATE_LIMITED, 42.0),
        (respond(503), ConnectorOutcome.UNAVAILABLE, None),
        (raise_error(httpx2.ReadTimeout("slow")), ConnectorOutcome.UNAVAILABLE, None),
        (
            respond(200, body=b"%PDF-1.7", content_type="application/pdf"),
            ConnectorOutcome.UNSUPPORTED,
            None,
        ),
    ],
)
def test_web_page_failures_map_to_prd_outcomes(
    handler: Any, outcome: ConnectorOutcome, retry_after: float | None
) -> None:
    router = Router().add("https://ornek.example/", handler)
    ctx, _ = context(router)
    error = _outcome(PublicWebPageConnector(), request("url", "https://ornek.example/", ctx))
    assert error.outcome == outcome
    assert error.retry_after_seconds == retry_after


def test_web_page_truncation_is_incomplete_not_complete() -> None:
    router = Router().add("https://ornek.example/", respond(200, body="<p>" + "a" * 5000))
    ctx, _ = context(router, max_response_bytes=1024)
    page = PublicWebPageConnector().fetch_page(request("url", "https://ornek.example/", ctx))
    assert page.incomplete_reason is not None
    assert page.evidence[0].collection_metadata["truncated"] is True


def test_web_page_refuses_private_destinations_without_sending_requests() -> None:
    router = Router()
    ctx, _ = context(router, resolver=resolver_map({"intranet.example": ["10.1.2.3"]}))
    error = _outcome(PublicWebPageConnector(), request("url", "http://intranet.example/", ctx))
    assert error.outcome == ConnectorOutcome.UNSUPPORTED
    assert error.code == "blocked_address"
    assert router.requests == []


def test_web_page_input_validation() -> None:
    connector = PublicWebPageConnector()
    for bad in ("ornek.example", "ftp://ornek.example/", "https://user:pw@ornek.example/"):
        with pytest.raises(ValueError):
            connector.validate("url", bad, {})
    with pytest.raises(ValueError, match="unsupported parameters"):
        connector.validate("url", "https://ornek.example/", {"render": True})


# -- RSS / Atom --------------------------------------------------------------------------------

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>Örnek Haberler</title><link>https://ornek.example/</link>
<atom:link rel="next" href="https://ornek.example/feed?page=2"/>
<item><title>Yeni ofis</title><link>https://ornek.example/haber/1</link>
<guid isPermaLink="false">haber-1</guid><pubDate>Tue, 01 Sep 2026 09:00:00 +0300</pubDate>
<description>&lt;p&gt;İzmir &lt;b&gt;şubesi&lt;/b&gt; açıldı&lt;/p&gt;</description></item>
<item><title>Duyuru</title><link>https://ornek.example/haber/2</link>
<pubDate>not a date</pubDate></item>
</channel></rss>"""

RSS_PAGE_2 = """<?xml version="1.0"?><rss version="2.0"><channel><title>Örnek Haberler</title>
<item><title>Yeni ofis</title><guid>haber-1</guid></item>
<item><title>Eski haber</title><guid>haber-0</guid></item></channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Kestrel log</title><id>urn:kestrel</id>
<updated>2026-09-10T12:00:00Z</updated>
<entry><id>urn:kestrel:1</id><title>Release</title><link href="/r/1"/>
<published>2026-09-09T08:00:00Z</published><updated>2026-09-10T08:00:00Z</updated>
<author><name>Ops</name></author><summary>First</summary></entry></feed>"""


def test_rss_entries_ids_dates_and_pagination_cursor() -> None:
    router = Router().add(
        "https://ornek.example/feed", respond(200, body=RSS, content_type="application/rss+xml")
    )
    ctx, _ = context(router)
    page = RssFeedConnector().fetch_page(request("url", "https://ornek.example/feed", ctx))
    snapshot, entries = page.evidence
    assert snapshot.kind == "xml"
    assert snapshot.content == RSS.encode()
    parsed = json.loads(entries.content)
    first, second = parsed["entries"]
    assert first["entry_id"] == "haber-1"
    assert first["id_source"] == "guid"
    assert first["published"] == "2026-09-01T06:00:00+00:00"
    assert first["summary"] == "İzmir şubesi açıldı"
    assert second["entry_id"] == "https://ornek.example/haber/2"
    assert second["published"] is None
    assert second["published_original"] == "not a date"
    assert page.has_more is True
    assert page.next_cursor == {"url": "https://ornek.example/feed?page=2"}
    entry_observations = [o for o in page.observations if o.observation_type == "feed_entry"]
    assert all(o.dedupe_across_pages for o in entry_observations)
    assert page.items == 2


def test_rss_second_page_repeats_share_idempotency_suffix() -> None:
    router = Router()
    router.add(
        "https://ornek.example/feed", respond(200, body=RSS, content_type="application/rss+xml")
    )
    router.add(
        "https://ornek.example/feed?page=2", respond(200, body=RSS_PAGE_2, content_type="text/xml")
    )
    ctx, _ = context(router)
    connector = RssFeedConnector()
    first = connector.fetch_page(request("url", "https://ornek.example/feed", ctx))
    second = connector.fetch_page(
        request("url", "https://ornek.example/feed", ctx, page_index=1, cursor=first.next_cursor)
    )
    suffixes_first = {o.idempotency_suffix for o in first.observations if o.dedupe_across_pages}
    suffixes_second = {o.idempotency_suffix for o in second.observations}
    assert len(suffixes_first & suffixes_second) == 1
    assert second.has_more is False


def test_atom_feed_and_verified_empty_feed() -> None:
    router = Router().add(
        "https://kestrel.example/atom", respond(200, body=ATOM, content_type="application/atom+xml")
    )
    ctx, _ = context(router)
    page = RssFeedConnector().fetch_page(request("url", "https://kestrel.example/atom", ctx))
    entry = json.loads(page.evidence[1].content)["entries"][0]
    assert entry["link"] == "https://kestrel.example/r/1"
    assert entry["published"] == "2026-09-09T08:00:00+00:00"

    empty = Router().add(
        "https://kestrel.example/empty",
        respond(
            200,
            body="<rss version='2.0'><channel><title>x</title></channel></rss>",
            content_type="application/rss+xml",
        ),
    )
    ctx, _ = context(empty)
    page = RssFeedConnector().fetch_page(request("url", "https://kestrel.example/empty", ctx))
    assert page.items == 0
    assert page.has_more is False
    assert page.outcome_hint is None


@pytest.mark.parametrize(
    ("body", "content_type", "code"),
    [
        ("<rss><channel><item><title>broken", "application/rss+xml", "invalid_feed"),
        (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
            "<rss><channel><title>&lol2;</title></channel></rss>",
            "application/rss+xml",
            "invalid_feed",
        ),
        (
            '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            "<rss><channel><title>&x;</title></channel></rss>",
            "application/xml",
            "invalid_feed",
        ),
        ("<html><body>Not a feed</body></html>", "text/html", "not_a_feed"),
        ("<svg xmlns='http://www.w3.org/2000/svg'/>", "application/xml", "invalid_feed"),
    ],
)
def test_malformed_and_hostile_feeds_are_parse_errors(
    body: str, content_type: str, code: str
) -> None:
    router = Router().add(
        "https://ornek.example/feed", respond(200, body=body, content_type=content_type)
    )
    ctx, _ = context(router)
    error = _outcome(RssFeedConnector(), request("url", "https://ornek.example/feed", ctx))
    assert error.outcome == ConnectorOutcome.PARSE_ERROR
    assert error.code == code


def test_rss_not_found_rate_limit_and_cross_origin_pagination() -> None:
    connector = RssFeedConnector()
    router = Router().add("https://ornek.example/feed", respond(404))
    ctx, _ = context(router)
    assert connector.fetch_page(request("url", "https://ornek.example/feed", ctx)).outcome_hint == (
        ConnectorOutcome.NO_FINDINGS
    )

    limited = Router().add("https://ornek.example/feed", respond(429, headers={"retry-after": "5"}))
    ctx, _ = context(limited)
    error = _outcome(connector, request("url", "https://ornek.example/feed", ctx))
    assert error.outcome == ConnectorOutcome.RATE_LIMITED
    assert error.retry_after_seconds == 5.0

    ctx, _ = context(Router())
    error = _outcome(
        connector,
        request(
            "url",
            "https://ornek.example/feed",
            ctx,
            page_index=1,
            cursor={"url": "https://elsewhere.example/feed?page=2"},
        ),
    )
    assert error.code == "cross_origin_pagination"


# -- GitHub ------------------------------------------------------------------------------------

ACCOUNT = {
    "login": "ornek-dev",
    "id": 424242,
    "node_id": "U_x",
    "type": "User",
    "name": "Örnek Geliştirici",
    "company": "Örnek A.Ş.",
    "blog": "ornek.example",
    "location": "İstanbul",
    "email": "dev@ornek.example",
    "bio": None,
    "public_repos": 3,
    "followers": 1,
    "following": 0,
    "created_at": "2020-01-02T03:04:05Z",
    "updated_at": "2026-09-01T00:00:00Z",
    "html_url": "https://github.com/ornek-dev",
}
RATE = {
    "x-ratelimit-limit": "60",
    "x-ratelimit-remaining": "57",
    "x-ratelimit-used": "3",
    "x-ratelimit-reset": "1789500000",
    "x-ratelimit-resource": "core",
}


def _github(router: Router, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    return context(router, settings=_settings(), **kwargs)


def test_github_account_and_paginated_repositories() -> None:
    repos_1 = f"{API}/users/ornek-dev/repos?type=owner&sort=updated&per_page=2&page=1"
    repos_2 = f"{API}/user/424242/repos?type=owner&sort=updated&per_page=2&page=2"
    router = Router()
    router.add(f"{API}/users/ornek-dev", respond(200, json_body=ACCOUNT, headers=RATE))
    router.add(
        repos_1,
        respond(
            200,
            json_body=[
                {"id": 1, "full_name": "ornek-dev/a"},
                {"id": 2, "full_name": "ornek-dev/b"},
            ],
            headers={**RATE, "link": f'<{repos_2}>; rel="next", <{repos_2}>; rel="last"'},
        ),
    )
    router.add(
        repos_2, respond(200, json_body=[{"id": 3, "full_name": "ornek-dev/c"}], headers=RATE)
    )
    ctx, record = _github(router, credentials={"token": "ghp_testtoken"})
    connector = GitHubAccountConnector()

    first = connector.fetch_page(request("username", "ornek-dev", ctx, max_items_per_page=2))
    assert first.items == 1
    account = first.entities[0]
    assert account.match.identifier_type == "platform_id"
    assert account.match.value == "424242"
    assert {r.predicate for r in first.relationships} == {"links_to", "lists_email"}
    assert first.evidence[0].access_category == "credentialed"
    assert first.quota is not None
    assert first.quota["remaining"] == 57
    assert first.quota["authenticated"] is True
    assert router.requests[0].headers["authorization"] == "Bearer ghp_testtoken"
    assert router.requests[0].headers["x-github-api-version"] == "2026-03-10"
    assert record["credential_results"] == {"token": "accepted"}

    second = connector.fetch_page(
        request(
            "username",
            "ornek-dev",
            ctx,
            page_index=1,
            cursor=first.next_cursor,
            max_items_per_page=2,
        )
    )
    assert [o.payload["full_name"] for o in second.observations] == ["ornek-dev/a", "ornek-dev/b"]
    assert second.next_cursor == {"url": repos_2}
    third = connector.fetch_page(
        request(
            "username",
            "ornek-dev",
            ctx,
            page_index=2,
            cursor=second.next_cursor,
            max_items_per_page=2,
        )
    )
    assert third.has_more is False
    assert third.items == 1
    # The token never appears in stored evidence.
    for page in (first, second, third):
        for evidence in page.evidence:
            assert b"ghp_testtoken" not in evidence.content
            assert "ghp_testtoken" not in json.dumps(evidence.collection_metadata)


def test_github_not_found_is_verified_no_findings_without_a_token() -> None:
    router = Router().add(
        f"{API}/users/missing-user", respond(404, json_body={"message": "Not Found"}, headers=RATE)
    )
    ctx, _ = _github(router)
    page = GitHubAccountConnector().fetch_page(request("username", "missing-user", ctx))
    assert page.outcome_hint == ConnectorOutcome.NO_FINDINGS
    assert page.evidence[0].access_category == "public"
    assert "authorization" not in router.requests[0].headers


def test_github_bad_token_rate_limits_forbidden_and_malformed() -> None:
    connector = GitHubAccountConnector()

    router = Router().add(
        f"{API}/users/ornek-dev", respond(401, json_body={"message": "Bad credentials"})
    )
    ctx, record = _github(router, credentials={"token": "ghp_wrong"})
    error = _outcome(connector, request("username", "ornek-dev", ctx))
    assert error.outcome == ConnectorOutcome.AUTHENTICATION_REQUIRED
    assert error.code == "github_bad_credentials"
    assert record["credential_results"] == {"token": "rejected"}

    reset = int(datetime.now(UTC).timestamp()) + 1200
    primary = Router().add(
        f"{API}/users/ornek-dev",
        respond(
            403,
            json_body={"message": "API rate limit exceeded"},
            headers={**RATE, "x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
        ),
    )
    ctx, _ = _github(primary)
    error = _outcome(connector, request("username", "ornek-dev", ctx))
    assert error.outcome == ConnectorOutcome.RATE_LIMITED
    assert error.retry_after_seconds is not None
    assert 1100 < error.retry_after_seconds <= 1200
    assert error.quota is not None
    assert error.quota["remaining"] == 0

    secondary = Router().add(
        f"{API}/users/ornek-dev",
        respond(
            429,
            json_body={"message": "You have exceeded a secondary rate limit"},
            headers={"retry-after": "30"},
        ),
    )
    ctx, _ = _github(secondary)
    error = _outcome(connector, request("username", "ornek-dev", ctx))
    assert (error.outcome, error.retry_after_seconds) == (ConnectorOutcome.RATE_LIMITED, 30.0)

    forbidden = Router().add(
        f"{API}/users/ornek-dev", respond(403, json_body={"message": "Forbidden"}, headers=RATE)
    )
    ctx, _ = _github(forbidden)
    assert (
        _outcome(connector, request("username", "ornek-dev", ctx)).outcome
        == ConnectorOutcome.ACCESS_DENIED
    )

    malformed = Router().add(
        f"{API}/users/ornek-dev", respond(200, body="<html>", content_type="text/html")
    )
    ctx, _ = _github(malformed)
    assert (
        _outcome(connector, request("username", "ornek-dev", ctx)).outcome
        == ConnectorOutcome.PARSE_ERROR
    )

    outage = Router().add(f"{API}/users/ornek-dev", raise_error(httpx2.ConnectTimeout("down")))
    ctx, _ = _github(outage)
    assert (
        _outcome(connector, request("username", "ornek-dev", ctx)).outcome
        == ConnectorOutcome.UNAVAILABLE
    )


def test_github_pagination_must_stay_on_the_api_origin_and_login_is_validated() -> None:
    ctx, _ = _github(Router())
    error = _outcome(
        GitHubAccountConnector(),
        request(
            "username", "ornek-dev", ctx, page_index=1, cursor={"url": "https://evil.example/repos"}
        ),
    )
    assert error.code == "unexpected_pagination"
    for bad in ("-dash", "has space", "a" * 40, "../etc"):
        with pytest.raises(ValueError):
            GitHubAccountConnector().validate("username", bad, {})


# -- Sherlock username discovery ---------------------------------------------------------------

STATUS_SITE = {"errorType": "status_code", "url": "https://s.example/{}"}
MESSAGE_SITE = {"errorType": "message", "errorMsg": "not found", "url": "https://m.example/{}"}
REDIRECT_SITE = {"errorType": "response_url", "url": "https://r.example/{}"}


@pytest.mark.parametrize(
    ("site", "status", "http_status", "context_text", "expected"),
    [
        (STATUS_SITE, "Claimed", 200, None, "candidate"),
        (STATUS_SITE, "Available", 404, None, "not_found"),
        # Sherlock's own reading of these would be "Available" (not found):
        (STATUS_SITE, "Available", 403, None, "access_denied"),
        (STATUS_SITE, "Available", 429, None, "rate_limited"),
        (STATUS_SITE, "Available", 503, None, "unavailable"),
        (STATUS_SITE, "Available", 302, None, "inconclusive"),
        (MESSAGE_SITE, "Available", 200, None, "not_found"),
        # A login wall or rate-limit page without the error text is not a candidate:
        (MESSAGE_SITE, "Claimed", 429, None, "rate_limited"),
        (MESSAGE_SITE, "Claimed", 401, None, "authentication_required"),
        (REDIRECT_SITE, "Available", 301, None, "not_found"),
        (STATUS_SITE, "Unknown", None, "Error Connecting", "unavailable"),
        (STATUS_SITE, "Unknown", None, "Timeout Error", "unavailable"),
        (STATUS_SITE, "WAF", 403, None, "access_denied"),
        (STATUS_SITE, "Illegal", None, None, "unsupported"),
    ],
)
def test_sherlock_results_are_reinterpreted_truthfully(
    site: dict[str, Any],
    status: str,
    http_status: int | None,
    context_text: str | None,
    expected: str,
) -> None:
    assert classify(site, status, http_status, context_text)[0] == expected


def _sherlock_process(results: list[dict[str, Any]], *, stopped: str | None = None) -> Any:
    lines = [json.dumps({"type": "result", **item}) for item in results]
    if stopped is None:
        lines.append(json.dumps({"type": "done"}))

    def fake_run(*_args: Any, **kwargs: Any) -> ProcessResult:
        on_stdout = kwargs.get("on_stdout")
        for line in lines:
            if on_stdout is not None:
                on_stdout(line)
        return ProcessResult(
            returncode=0 if stopped is None else -15, stopped=stopped, stdout=lines
        )

    return fake_run


def test_sherlock_candidates_are_candidates_and_mixed_results_are_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        {
            "site": "GitHub",
            "status": "Claimed",
            "http_status": 200,
            "url": "https://www.github.com/ornekdev",
        },
        {
            "site": "GitLab",
            "status": "Available",
            "http_status": 200,
            "url": "https://gitlab.com/ornekdev",
        },
        {
            "site": "Reddit",
            "status": "Available",
            "http_status": 429,
            "url": "https://www.reddit.com/user/ornekdev",
        },
    ]
    monkeypatch.setattr(engine_process, "run", _sherlock_process(results))
    ctx, _ = context()
    page = SherlockUsernameConnector().fetch_page(
        request("username", "ornekdev", ctx, parameters={"sites": ["GitHub", "GitLab", "Reddit"]})
    )
    assert page.items == 1
    assert page.entities[0].entity_type == "platform_account"
    assert page.entities[0].attributes["candidate"] is True
    assert "not an identity assertion" in page.entities[0].description
    assert page.observations[0].observation_type == "candidate_account"
    assert page.relationships == []
    body = json.loads(page.evidence[0].content)
    by_site = {entry["site"]: entry["classification"] for entry in body["results"]}
    assert by_site == {"GitHub": "candidate", "GitLab": "not_found", "Reddit": "rate_limited"}
    assert page.incomplete_reason is not None
    assert page.outcome_hint is None


def test_sherlock_verified_absence_blocked_checks_and_engine_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = SherlockUsernameConnector()
    ctx, _ = context()
    params = {"sites": ["GitHub", "GitLab"]}

    monkeypatch.setattr(
        engine_process,
        "run",
        _sherlock_process(
            [
                {"site": "GitHub", "status": "Available", "http_status": 404, "url": "u"},
                {"site": "GitLab", "status": "Available", "http_status": 200, "url": "u"},
            ]
        ),
    )
    page = connector.fetch_page(request("username", "nobody", ctx, parameters=params))
    assert (page.items, page.outcome_hint, page.incomplete_reason) == (0, None, None)

    monkeypatch.setattr(
        engine_process,
        "run",
        _sherlock_process(
            [
                {"site": "GitHub", "status": "Available", "http_status": 429, "url": "u"},
                {"site": "GitLab", "status": "WAF", "http_status": 403, "url": "u"},
            ]
        ),
    )
    page = connector.fetch_page(request("username", "nobody", ctx, parameters=params))
    assert page.items == 0
    assert page.outcome_hint in (ConnectorOutcome.RATE_LIMITED, ConnectorOutcome.ACCESS_DENIED)

    def crashed(*_args: Any, **_kwargs: Any) -> ProcessResult:
        return ProcessResult(returncode=1, stopped=None, stderr=["Traceback ...", "boom"])

    monkeypatch.setattr(engine_process, "run", crashed)
    assert _outcome(connector, request("username", "nobody", ctx, parameters=params)).outcome == (
        ConnectorOutcome.UNAVAILABLE
    )

    for bad in ("with space", "{?}", "a/b"):
        with pytest.raises(ValueError):
            connector.validate("username", bad, {})
    with pytest.raises(ValueError, match="unknown platforms"):
        connector.validate("username", "ok", {"sites": ["Instagram"]})


def test_sherlock_runner_refuses_prohibited_destinations() -> None:
    """The real engine, isolated runner and network guard: loopback profile URLs are refused."""
    pytest.importorskip("sherlock_project")
    site = {
        "errorType": "status_code",
        "url": "http://127.0.0.1:9/{}",
        "urlMain": "http://127.0.0.1:9/",
    }
    ctx, _ = context()
    connector = SherlockUsernameConnector()
    original = sherlock_module._MANIFEST["sites"]
    try:
        sherlock_module._MANIFEST["sites"] = {"GitHub": site}
        page = connector.fetch_page(
            request(
                "username", "ornekdev", ctx, parameters={"sites": ["GitHub"], "timeout_seconds": 5}
            )
        )
    finally:
        sherlock_module._MANIFEST["sites"] = original
    body = json.loads(page.evidence[0].content)
    assert body["results"][0]["classification"] == "blocked"
    assert page.items == 0
    assert page.outcome_hint == ConnectorOutcome.UNAVAILABLE


# -- Subfinder passive domain discovery --------------------------------------------------------

# Recorded from subfinder v2.16.0 run without network access (2026-09-15).
OFFLINE_STDERR = """[INF] Loading provider config from /tmp/pc.yaml
[DBG] Selected source(s) for this search: alienvault, anubis, crtsh, hackertarget
[INF] Enumerating subdomains for ornek.example
[DBG] Cannot use the alienvault source because there was no API key/secret defined for it.
[WRN] Encountered an error with source hackertarget: Get "https://api.hackertarget.com/hostsearch/?q=ornek.example": dial tcp: lookup api.hackertarget.com on 192.168.65.7:53: dial udp 192.168.65.7:53: connect: network is unreachable
[WRN] Encountered an error with source anubis: Get "https://anubisdb.com/anubis/subdomains/ornek.example": dial tcp: lookup anubisdb.com: network is unreachable
[WRN] Encountered an error with source crtsh: dial tcp: lookup crt.sh on 192.168.65.7:53: connect: network is unreachable
[INF] Found 0 subdomains for ornek.example in 1 millisecond 560 microseconds"""


def test_subfinder_stderr_parsing_matches_recorded_output() -> None:
    selected, errors, skipped, fatal = parse_stderr(OFFLINE_STDERR.splitlines())
    assert selected == ["alienvault", "anubis", "crtsh", "hackertarget"]
    assert sorted(errors) == ["anubis", "crtsh", "hackertarget"]
    assert skipped == ["alienvault"]
    assert fatal is None
    # crt.sh's database path (no https:// URL) is recognised; its HTTPS API errors are not.
    assert is_crtsh_database_path("crtsh", errors["crtsh"][0])
    assert not is_crtsh_database_path("crtsh", 'Get "https://crt.sh/?q=%25.x": EOF')
    assert not is_crtsh_database_path("anubis", errors["anubis"][0])
    assert in_scope("*.Mail.Ornek.example.", "ornek.example") == "mail.ornek.example"
    assert in_scope("ornek.example.evil.example", "ornek.example") is None
    assert in_scope("notornek.example", "ornek.example") is None


def _stats(**sources: tuple[int, int, int]) -> str:
    """The ``-stats`` table Subfinder v2.16.0 prints to stderr (results, requests, errors)."""
    rows = "\n".join(
        f" {name:<20} {'1.2s':<10} {r:>10} {q:>10} {e:>10}" for name, (r, q, e) in sources.items()
    )
    return (
        "[INF] Printing source statistics for ornek.example\n\n"
        " Source               Duration      Results   Requests     Errors\n"
        + "─" * 68
        + "\n"
        + rows
    )


def _fake_subfinder(
    tmp_path: Path, stdout: list[str], stderr: str, exit_code: int = 0, sleep: float = 0
) -> Path:
    data = tmp_path / "fake.json"
    data.write_text(
        json.dumps({"stdout": stdout, "stderr": stderr, "exit": exit_code, "sleep": sleep})
    )
    script = tmp_path / "subfinder"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, sys, time
            spec = json.load(open({str(data)!r}))
            args = sys.argv[1:]
            open({str(tmp_path / "args.json")!r}, "w").write(json.dumps(args))
            if "-pc" in args:
                open({str(tmp_path / "provider.yaml")!r}, "w").write(open(args[args.index("-pc") + 1]).read())
            sys.stderr.write(spec["stderr"] + "\\n")
            sys.stderr.flush()
            for line in spec["stdout"]:
                print(line, flush=True)
                time.sleep(spec["sleep"])
            open({str(tmp_path / "finished")!r}, "w").write("yes")
            sys.exit(spec["exit"])
            """
        )
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def runner() -> Any:
    """Start real discovery runners (sandbox checks off: this host is not the sandbox)."""
    servers: list[Any] = []

    def start(script: Path, **config: Any) -> str:
        options = {"check_sandbox": False, **config}
        server = subfinder_runner.make_server(
            subfinder_runner.RunnerConfig(
                subfinder=script, gateway="discovery-gateway.invalid:3128", **options
            ),
            "127.0.0.1",
            0,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _subfinder_request(runner_url: str, parameters: dict[str, Any], **ctx_kwargs: Any) -> Any:
    ctx, record = context(settings=_settings(discovery_runner_url=runner_url), **ctx_kwargs)
    return request("domain", "Ornek.example", ctx, parameters=parameters), record


def test_subfinder_runs_only_through_the_sandbox_runner_with_the_gateway_proxy(
    tmp_path: Path, runner: Any
) -> None:
    stdout = [
        json.dumps({"host": "mail.ornek.example", "input": "ornek.example", "sources": ["crtsh"]}),
        json.dumps(
            {"host": "*.dev.ornek.example", "input": "ornek.example", "sources": ["digitorus"]}
        ),
        json.dumps({"host": "ornek.example.attacker.example", "sources": ["crtsh"]}),
        "not json",
    ]
    stderr = "[DBG] Selected source(s) for this search: crtsh, digitorus\n" + _stats(
        crtsh=(1, 1, 0), digitorus=(1, 1, 0)
    )
    script = _fake_subfinder(tmp_path, stdout, stderr)
    req, record = _subfinder_request(runner(script), {})
    page = SubfinderDomainConnector().fetch_page(req)
    hosts = sorted(o.payload["host"] for o in page.observations)
    assert hosts == ["dev.ornek.example", "mail.ornek.example"]
    assert page.coverage["out_of_scope_discarded"] == 1
    assert page.outcome_hint is None
    assert page.incomplete_reason is None
    assert {r.predicate for r in page.relationships} == {"subdomain_of"}
    assert all(r.origin == "deterministic_derivation" for r in page.relationships)
    args = json.loads((tmp_path / "args.json").read_text())
    assert "-duc" in args
    assert args[args.index("-d") + 1] == "ornek.example"
    assert args[args.index("-s") + 1] == "crtsh,digitorus"
    assert args[args.index("-proxy") + 1] == "http://discovery-gateway.invalid:3128"
    assert not {"-active", "-nW", "-up", "-update"} & set(args)
    assert record["progress"]
    assert page.evidence[0].collection_metadata["network_sandbox"] == "discovery-runner"


def test_subfinder_is_never_run_without_the_sandbox(tmp_path: Path, runner: Any) -> None:
    connector = SubfinderDomainConnector()
    script = _fake_subfinder(tmp_path, [], "[DBG] Selected source(s) for this search: crtsh")

    ctx, _ = context(settings=_settings(discovery_runner_url="http://127.0.0.1:9"))
    error = _outcome(connector, request("domain", "ornek.example", ctx))
    assert (error.outcome, error.code) == (
        ConnectorOutcome.UNAVAILABLE,
        "discovery_runner_unavailable",
    )

    # A runner whose container has a default route refuses the job before starting Subfinder.
    routes = tmp_path / "route"
    routes.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    exposed = runner(
        script, check_sandbox=True, route_file=routes, ipv6_route_file=tmp_path / "none"
    )
    req, _ = _subfinder_request(exposed, {})
    error = _outcome(connector, req)
    assert (error.outcome, error.code) == (
        ConnectorOutcome.UNAVAILABLE,
        "egress_sandbox_unavailable",
    )
    assert "default route" in error.detail
    assert not (tmp_path / "args.json").exists()

    missing = runner(tmp_path / "absent")
    req, _ = _subfinder_request(missing, {})
    assert _outcome(connector, req).code == "engine_not_installed"


def test_subfinder_all_sources_failing_is_unavailable_not_no_findings(
    tmp_path: Path, runner: Any
) -> None:
    script = _fake_subfinder(tmp_path, [], OFFLINE_STDERR)
    req, _ = _subfinder_request(
        runner(script), {"sources": ["alienvault", "anubis", "crtsh", "hackertarget"]}
    )
    page = SubfinderDomainConnector().fetch_page(req)
    assert page.items == 0
    assert page.outcome_hint == ConnectorOutcome.UNAVAILABLE
    body = json.loads(page.evidence[0].content)
    assert body["sources"]["skipped_missing_key"] == ["alienvault"]
    # crt.sh failed only on its database path, which says nothing about its HTTPS API.
    assert "crtsh" not in body["sources"]["errors"]
    assert body["egress"]["crtsh_database_path_unavailable"] is True
    # Query strings (which can carry API keys) are removed from stored error messages.
    assert "?q=" not in json.dumps(body)


def test_subfinder_crtsh_database_failure_with_api_results_is_complete(
    tmp_path: Path, runner: Any
) -> None:
    script = _fake_subfinder(
        tmp_path,
        [json.dumps({"host": "www.ornek.example", "sources": ["crtsh"]})],
        "[DBG] Selected source(s) for this search: crtsh\n"
        "[WRN] Encountered an error with source crtsh: dial tcp: lookup crt.sh on 127.0.0.11:53: server misbehaving\n"
        + _stats(crtsh=(1, 1, 1)),
    )
    req, _ = _subfinder_request(runner(script), {"sources": ["crtsh"]})
    page = SubfinderDomainConnector().fetch_page(req)
    assert page.items == 1
    assert page.incomplete_reason is None
    assert any("HTTPS API was used" in note for note in page.notes)

    # Only the database-path error and no completed HTTPS request: not a verified empty result.
    silent_dir = tmp_path / "silent"
    silent_dir.mkdir()
    silent = _fake_subfinder(
        silent_dir,
        [],
        "[DBG] Selected source(s) for this search: crtsh\n"
        "[WRN] Encountered an error with source crtsh: dial tcp: lookup crt.sh on 127.0.0.11:53: server misbehaving",
    )
    req, _ = _subfinder_request(runner(silent), {"sources": ["crtsh"]})
    page = SubfinderDomainConnector().fetch_page(req)
    assert (page.outcome_hint, page.outcome_code) == (
        ConnectorOutcome.UNAVAILABLE,
        "sources_not_completed",
    )
    verified_dir = tmp_path / "verified"
    verified_dir.mkdir()
    verified = _fake_subfinder(
        verified_dir,
        [],
        "[DBG] Selected source(s) for this search: crtsh\n"
        "[WRN] Encountered an error with source crtsh: dial tcp: lookup crt.sh on 127.0.0.11:53: server misbehaving\n"
        + _stats(crtsh=(0, 1, 1)),
    )
    req, _ = _subfinder_request(runner(verified), {"sources": ["crtsh"]})
    page = SubfinderDomainConnector().fetch_page(req)
    assert (page.items, page.outcome_hint, page.incomplete_reason) == (0, None, None)


def test_subfinder_gateway_refusals_are_explicit_outcomes(tmp_path: Path, runner: Any) -> None:
    refused = _fake_subfinder(
        tmp_path,
        [],
        "[DBG] Selected source(s) for this search: crtsh, digitorus\n"
        "[WRN] Encountered an error with source crtsh: dial tcp: lookup crt.sh on 127.0.0.11:53: server misbehaving\n"
        '[WRN] Encountered an error with source crtsh: Get "https://crt.sh/?q=%25.ornek.example&output=json": Tracehollow egress refused blocked_address\n'
        '[WRN] Encountered an error with source digitorus: Get "https://certificatedetails.com/ornek.example": Tracehollow egress refused host_not_allowed',
    )
    req, _ = _subfinder_request(runner(refused), {})
    page = SubfinderDomainConnector().fetch_page(req)
    assert page.items == 0
    assert (page.outcome_hint, page.outcome_code) == (
        ConnectorOutcome.UNSUPPORTED,
        "provider_destination_refused",
    )
    body = json.loads(page.evidence[0].content)
    assert body["sources"]["refused_by_egress_gateway"] == {
        "crtsh": "blocked_address",
        "digitorus": "host_not_allowed",
    }

    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    untrusted = _fake_subfinder(
        tls_dir,
        [],
        "[DBG] Selected source(s) for this search: crtsh\n"
        '[WRN] Encountered an error with source crtsh: Get "https://crt.sh/?q=%25.ornek.example&output=json": Tracehollow egress failed upstream_certificate_invalid',
    )
    req, _ = _subfinder_request(runner(untrusted), {"sources": ["crtsh"]})
    page = SubfinderDomainConnector().fetch_page(req)
    assert (page.outcome_hint, page.outcome_code) == (
        ConnectorOutcome.UNAVAILABLE,
        "upstream_certificate_invalid",
    )


def test_subfinder_verified_empty_missing_keys_rate_limits_and_partial(
    tmp_path: Path, runner: Any
) -> None:
    connector = SubfinderDomainConnector()

    ok = _fake_subfinder(
        tmp_path,
        [],
        "[DBG] Selected source(s) for this search: crtsh, digitorus\n"
        + _stats(crtsh=(0, 1, 0), digitorus=(0, 1, 0)),
    )
    req, _ = _subfinder_request(runner(ok), {})
    page = connector.fetch_page(req)
    assert (page.items, page.outcome_hint, page.incomplete_reason) == (0, None, None)

    keyless = tmp_path / "keyless"
    keyless.mkdir()
    missing_key = _fake_subfinder(
        keyless,
        [],
        "[DBG] Selected source(s) for this search: virustotal\n"
        "[DBG] Cannot use the virustotal source because there was no API key/secret defined for it.",
    )
    req, _ = _subfinder_request(runner(missing_key), {"sources": ["virustotal"]})
    page = connector.fetch_page(req)
    assert (page.outcome_hint, page.outcome_code) == (
        ConnectorOutcome.AUTHENTICATION_REQUIRED,
        "api_key_missing",
    )

    limited_dir = tmp_path / "limited"
    limited_dir.mkdir()
    limited = _fake_subfinder(
        limited_dir,
        [],
        "[DBG] Selected source(s) for this search: virustotal\n"
        "[WRN] Encountered an error with source virustotal: virustotal quota exhausted (HTTP 429); "
        "some subdomains for ornek.example may be missing",
    )
    req, _ = _subfinder_request(
        runner(limited),
        {"sources": ["virustotal"]},
        credentials={"virustotal": "vt-secret-key"},
    )
    page = connector.fetch_page(req)
    assert page.outcome_hint == ConnectorOutcome.RATE_LIMITED
    # The key reaches only the runner's private provider configuration.
    assert "vt-secret-key" in (limited_dir / "provider.yaml").read_text()
    assert page.evidence[0].access_category == "credentialed"

    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    partial = _fake_subfinder(
        partial_dir,
        [json.dumps({"host": "a.ornek.example", "sources": ["crtsh"]})],
        "[DBG] Selected source(s) for this search: crtsh, anubis\n"
        '[WRN] Encountered an error with source anubis: Get "https://anubisdb.com/x?key=vt-secret-key": timeout vt-secret-key\n'
        + _stats(crtsh=(1, 1, 0), anubis=(0, 1, 1)),
    )
    req, _ = _subfinder_request(
        runner(partial),
        {"sources": ["crtsh", "anubis"]},
        credentials={"virustotal": "vt-secret-key"},
    )
    page = connector.fetch_page(req)
    assert page.items == 1
    assert page.incomplete_reason is not None
    assert "anubis" in page.incomplete_reason
    assert b"vt-secret-key" not in page.evidence[0].content
    # Keys of sources that were not selected are never sent to the runner.
    assert (partial_dir / "provider.yaml").read_text() == "{}\n"


def test_subfinder_limits_fatal_errors_and_cancellation(tmp_path: Path, runner: Any) -> None:
    connector = SubfinderDomainConnector()
    many = [json.dumps({"host": f"h{i}.ornek.example", "sources": ["crtsh"]}) for i in range(50)]
    script = _fake_subfinder(
        tmp_path, many, "[DBG] Selected source(s) for this search: crtsh", sleep=0.02
    )
    req, _ = _subfinder_request(runner(script), {"sources": ["crtsh"], "max_results": 5})
    page = connector.fetch_page(req)
    assert page.items == 5
    assert page.incomplete_reason is not None
    assert "limit of 5" in page.incomplete_reason

    fatal_dir = tmp_path / "fatal"
    fatal_dir.mkdir()
    fatal = _fake_subfinder(
        fatal_dir, [], "[FTL] Could not read config: permission denied", exit_code=1
    )
    req, _ = _subfinder_request(runner(fatal), {})
    assert _outcome(connector, req).code == "engine_failed"

    slow_dir = tmp_path / "slow"
    slow_dir.mkdir()
    slow = _fake_subfinder(slow_dir, many, "", sleep=0.2)
    calls = {"n": 0}

    def cancel_after_a_moment() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    req, _ = _subfinder_request(
        runner(slow), {"sources": ["crtsh"]}, cancelled=cancel_after_a_moment
    )
    assert _outcome(connector, req).outcome == ConnectorOutcome.CANCELED
    # The runner notices the closed stream at its next heartbeat and stops the process.
    time.sleep(3)
    assert not (slow_dir / "finished").exists()
    for bad in ("*.ornek.example", "not a domain", "192.0.2.1"):
        with pytest.raises(ValueError):
            connector.validate("domain", bad, {})


def test_discovery_runner_validates_jobs() -> None:
    job = {
        "domain": "ornek.example",
        "sources": ["crtsh", "virustotal"],
        "provider_keys": {"virustotal": "key"},
        "request_timeout_seconds": 20,
        "max_time_minutes": 4,
        "max_response_bytes": 5_000_000,
        "deadline_seconds": 290,
        "max_results": 500,
    }
    assert subfinder_runner.validate_job(job)["domain"] == "ornek.example"
    bad_cases: list[dict[str, Any]] = [
        {"domain": "192.0.2.1"},
        {"domain": "ornek.example -active"},
        {"domain": "localhost"},
        {"sources": ["crtsh", "shodan"]},
        {"sources": []},
        {"provider_keys": {"crtsh": "key"}},
        {"provider_keys": {"certspotter": "key"}},
        {"provider_keys": {"virustotal": "line\nbreak"}},
        {"max_results": 0},
        {"deadline_seconds": True},
    ]
    for override in bad_cases:
        with pytest.raises(subfinder_runner.JobError):
            subfinder_runner.validate_job({**job, **override})


def test_discovery_runner_sandbox_checks(tmp_path: Path) -> None:
    header = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    subnet = "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    routes = tmp_path / "route"
    ipv6 = tmp_path / "ipv6_route"
    reject_default = (
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200       lo\n"
    )
    config = subfinder_runner.RunnerConfig(
        subfinder=tmp_path / "subfinder",
        gateway="127.0.0.1:9",
        route_file=routes,
        ipv6_route_file=ipv6,
    )

    def problems(route_text: str, ipv6_text: str) -> list[str]:
        routes.write_text(header + route_text)
        ipv6.write_text(ipv6_text)
        found = subfinder_runner.sandbox_problems(config)
        # Neither the host-address probe nor the gateway check can pass on a test host.
        return [p for p in found if "gateway" not in p and "Docker host" not in p]

    assert problems(subnet, reject_default) == []
    assert any(
        "IPv4 default route" in p
        for p in problems(
            "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n" + subnet, ""
        )
    )
    assert any(
        "IPv6 default route" in p
        for p in problems(subnet, reject_default.replace("00200200       lo", "00000003     eth0"))
    )
    assert any(
        "one network interface" in p
        for p in problems(subnet + subnet.replace("eth0\t000011AC", "eth1\t000012AC"), "")
    )
    assert "the egress gateway is not reachable" in subfinder_runner.sandbox_problems(config)


def test_every_connector_declares_the_contract() -> None:
    from app.connectors.registry import all_connectors

    for connector in all_connectors():
        d = connector.descriptor
        for value in (d.connector_id, d.version, d.output_schema, d.documentation):
            assert value
        for value in (d.coverage, d.credential_requirements, d.cache_policy):
            assert value
        assert d.supported_input_types
        assert min(d.timeout_seconds, d.max_pages, d.max_concurrent_runs) > 0
        # A live-verified badge needs a recorded live check; fixture tests alone never earn it.
        if d.verification_status == VerificationStatus.LIVE_VERIFIED:
            assert d.last_live_verification == "2026-09-15"
        else:
            assert d.last_live_verification is None
        assert isinstance(ConnectorPage(page_index=0, has_more=False).items, int)
    live = {
        c.descriptor.connector_id for c in all_connectors() if c.descriptor.last_live_verification
    }
    assert live == {"public_web.page", "rss.feed", "github.account", "username.sherlock"}
