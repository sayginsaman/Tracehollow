"""Fixture contract tests for the Phase 4 social connectors (PRD Phase 4 acceptance 1 and 2).

Responses are synthetic and shaped after the platforms' documented formats (Graph API, Bot API,
YouTube Data API) or the observed preview/profile page layouts. No test contacts a real source.
"""

# ruff: noqa: E501 - recorded platform responses are kept on one line where they are compared verbatim

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.config import load_settings
from app.connectors import instagram, telegram, youtube
from app.connectors.base import (
    AccessMethod,
    CapabilityStatus,
    ConnectorError,
    effective_collection_mode,
)
from app.connectors.instagram import InstagramAccountConnector
from app.connectors.telegram import TelegramPublicChannelConnector
from app.connectors.youtube import YouTubeDataApiConnector
from app.queries.models import ConnectorOutcome
from tests.collection_helpers import Router, context, request, respond

GRAPH = "https://graph.facebook.com/v25.0"
IG_TOKEN = "EAAsyntheticGraphToken0000000000000000"
IG_USER = "17841400000000001"
BOT_TOKEN = "123456789:AAsyntheticBotToken_000000000000000"
YT_KEY = "AIzaSyntheticKey000000000000000000000"


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "env": "test",
        "database_password": "x" * 16,
        "redis_password": "y" * 16,
        "secret_key": "z" * 32,
    }
    values.update(overrides)
    return load_settings(**values)


def _error(connector: Any, req: Any) -> ConnectorError:
    with pytest.raises(ConnectorError) as caught:
        connector.fetch_page(req)
    return caught.value


def _no_secret(page: Any, *secrets: str) -> None:
    for evidence in page.evidence:
        dumped = json.dumps(evidence.collection_metadata) + evidence.source_reference
        for secret in secrets:
            assert secret.encode() not in evidence.content
            assert secret not in dumped


# -- capability model --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "connector",
    [InstagramAccountConnector(), TelegramPublicChannelConnector(), YouTubeDataApiConnector()],
)
def test_only_implemented_capabilities_are_selectable_and_each_is_fully_described(
    connector: Any,
) -> None:
    descriptor = connector.descriptor
    spec = descriptor.parameter("capability")
    assert spec is not None
    implemented = {c.name for c in descriptor.capabilities if c.status == "implemented"}
    assert set(spec.choices) == implemented
    for capability in descriptor.capabilities:
        if capability.status == CapabilityStatus.IMPLEMENTED:
            assert capability.collection_mode is not None
            assert capability.verification_status == "fixture_tested"
            assert capability.last_live_verification is None
            for text in (
                capability.provider,
                capability.pagination,
                capability.session_requirements,
                capability.restrictions,
                capability.cost_quota,
            ):
                assert text
            assert capability.returned_fields
            assert capability.unavailable_fields
            assert capability.stable_identifiers
        else:
            assert capability.reason
            assert capability.verification_status is None
            with pytest.raises(ValueError, match="capability"):
                connector.validate(
                    descriptor.supported_input_types[0],
                    "synthetic_name",
                    {"capability": capability.name},
                )
    official = [c for c in descriptor.capabilities if c.access_method == AccessMethod.OFFICIAL_API]
    unofficial = [
        c for c in descriptor.capabilities if c.access_method != AccessMethod.OFFICIAL_API
    ]
    assert official
    assert all("Official" in c.label for c in official if c.status == "implemented")
    assert all("Unofficial" in c.label for c in unofficial if c.status == "implemented")


def test_instagram_never_offers_private_or_unrestricted_personal_account_access() -> None:
    descriptor = InstagramAccountConnector.descriptor
    for capability in descriptor.capabilities:
        wording = f"{capability.label} {capability.name}".lower()
        if "private" in wording or "personal" in wording:
            assert capability.status == CapabilityStatus.EXCLUDED
    assert "does not access private profiles" in descriptor.description
    official = descriptor.capability("official_business_discovery")
    assert official is not None
    assert "personal (non-professional) accounts" in official.unavailable_fields


def test_collection_mode_follows_the_selected_capability() -> None:
    descriptor = InstagramAccountConnector.descriptor
    assert effective_collection_mode(descriptor, {}) == "third_party_api"
    assert (
        effective_collection_mode(descriptor, {"capability": "public_profile_page"})
        == "platform_probe"
    )
    telegram_descriptor = TelegramPublicChannelConnector.descriptor
    assert (
        effective_collection_mode(telegram_descriptor, {"capability": "bot_api_chat_info"})
        == "third_party_api"
    )


def test_social_connectors_send_all_traffic_through_the_guarded_fetch() -> None:
    """No SDK, socket, subprocess or other HTTP client can bypass the network policy."""
    allowed = {
        "__future__",
        "dataclasses",
        "datetime",
        "html.parser",
        "json",
        "re",
        "typing",
        "urllib.parse",
        "zoneinfo",
    }
    for module in (instagram, telegram, youtube):
        tree = ast.parse(Path(str(module.__file__)).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
            else:
                continue
            outside = {
                name for name in names if name not in allowed and not name.startswith("app.")
            }
            assert not outside, (module.__name__, outside)
        source = Path(str(module.__file__)).read_text()
        assert "http.fetch(" in source
        for forbidden in ("httpx", "requests", "socket", "subprocess", "telethon", "instaloader"):
            assert f"import {forbidden}" not in source


# -- Instagram official Business Discovery -----------------------------------------------------


def _ig_url(fields: str) -> str:
    from urllib.parse import quote

    return f"{GRAPH}/{IG_USER}?fields={quote(fields, safe='(){},._')}"


MEDIA = "id,caption,media_type,media_product_type,permalink,shortcode,timestamp,like_count,comments_count"
IG_FIRST = _ig_url(
    "business_discovery.username(ornek.magaza){id,username,biography,website,followers_count,"
    f"media_count,media.limit(2){{{MEDIA}}}}}"
)
IG_SECOND = _ig_url(
    f"business_discovery.username(ornek.magaza){{id,media.limit(2).after(QVFIUmN1){{{MEDIA}}}}}"
)
USAGE = {
    "x-app-usage": json.dumps({"call_count": 12, "total_time": 3, "total_cputime": 2}),
    "x-business-use-case-usage": json.dumps(
        {IG_USER: [{"type": "instagram", "call_count": 40, "estimated_time_to_regain_access": 0}]}
    ),
}


def _ig_context(router: Router, **credentials: str) -> Any:
    return context(router, credentials=credentials, settings=_settings())


def test_instagram_business_discovery_with_media_pages_keeps_the_token_out_of_evidence() -> None:
    account = {
        "business_discovery": {
            "id": "17841405555555555",
            "username": "ornek.magaza",
            "biography": "İstanbul'da el yapımı ürünler",
            "website": "https://ornek.example",
            "followers_count": 1520,
            "media_count": 3,
            "media": {
                "data": [
                    {
                        "id": "m1",
                        "caption": "Yeni ürün",
                        "media_type": "IMAGE",
                        "timestamp": "2026-09-01T10:00:00+0000",
                        "like_count": 5,
                        "comments_count": 1,
                    },
                    {
                        "id": "m2",
                        "caption": "Kampanya",
                        "media_type": "VIDEO",
                        "timestamp": "2026-08-20T08:30:00+0000",
                        "comments_count": 0,
                    },
                ],
                "paging": {"cursors": {"before": "QVFIUmN0", "after": "QVFIUmN1"}},
            },
        },
        "id": IG_USER,
    }
    second = {
        "business_discovery": {
            "id": "17841405555555555",
            "media": {"data": [{"id": "m3", "timestamp": "2026-07-01T00:00:00+0000"}]},
        },
        "id": IG_USER,
    }
    router = Router()
    router.add(IG_FIRST, respond(200, json_body=account, headers=USAGE))
    router.add(IG_SECOND, respond(200, json_body=second))
    ctx, record = _ig_context(router, access_token=IG_TOKEN, ig_user_id=IG_USER)
    connector = InstagramAccountConnector()

    first = connector.fetch_page(request("username", "@ornek.magaza", ctx, max_items_per_page=2))
    assert router.requests[0].headers["authorization"] == f"Bearer {IG_TOKEN}"
    assert IG_TOKEN not in str(router.requests[0].url)
    entity = first.entities[0]
    assert (entity.match.identifier_type, entity.match.value) == (
        "platform_id",
        "17841405555555555",
    )
    kinds = [o.observation_type for o in first.observations]
    assert kinds == ["instagram_account", "instagram_media", "instagram_media"]
    assert first.observations[2].payload["like_count_hidden"] is True
    assert first.observations[1].event_time == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert first.next_cursor == {"after": "QVFIUmN1"}
    assert first.quota is not None
    assert first.quota["usage_headers"]["x-app-usage"]["call_count"] == 12
    metadata = first.evidence[0].collection_metadata
    assert metadata["access_method"] == "official_api"
    assert metadata["capability"] == "official_business_discovery"
    assert first.evidence[0].access_category == "credentialed"
    assert record["credential_results"] == {"access_token": "accepted"}
    _no_secret(first, IG_TOKEN)

    page_two = connector.fetch_page(
        request(
            "username",
            "ornek.magaza",
            ctx,
            page_index=1,
            cursor=first.next_cursor,
            max_items_per_page=2,
        )
    )
    assert [o.source_object_id for o in page_two.observations] == ["m3"]
    assert page_two.has_more is False


def test_instagram_missing_credentials_block_collection_without_a_result() -> None:
    router = Router()
    ctx, _ = _ig_context(router, access_token=IG_TOKEN)
    error = _error(InstagramAccountConnector(), request("username", "ornek.magaza", ctx))
    assert error.outcome == ConnectorOutcome.AUTHENTICATION_REQUIRED
    assert error.code == "credential_not_configured"
    assert "ig_user_id" in error.detail
    assert router.requests == []


@pytest.mark.parametrize(
    ("status", "error", "outcome", "code"),
    [
        (
            400,
            {"code": 190, "error_subcode": 463, "message": "Session has expired"},
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            "instagram_token_invalid",
        ),
        (
            400,
            {"code": 4, "message": "Application request limit reached"},
            ConnectorOutcome.RATE_LIMITED,
            "instagram_rate_limited",
        ),
        (
            400,
            {"code": 110, "error_subcode": 2207013, "message": "Cannot find User"},
            ConnectorOutcome.UNSUPPORTED,
            "instagram_account_not_discoverable",
        ),
        (
            403,
            {"code": 10, "message": "Permission denied"},
            ConnectorOutcome.ACCESS_DENIED,
            "instagram_permission_missing",
        ),
        (
            400,
            {"code": 368, "message": "Temporarily blocked"},
            ConnectorOutcome.ACCESS_DENIED,
            "instagram_restricted",
        ),
        (
            500,
            {"code": 2, "message": "Service temporarily unavailable"},
            ConnectorOutcome.UNAVAILABLE,
            "instagram_temporary_error",
        ),
    ],
)
def test_instagram_graph_errors_are_distinguished(
    status: int, error: dict[str, Any], outcome: ConnectorOutcome, code: str
) -> None:
    headers = {
        "x-business-use-case-usage": json.dumps(
            {IG_USER: [{"type": "instagram", "estimated_time_to_regain_access": 12}]}
        )
    }
    router = Router().add(IG_FIRST, respond(status, json_body={"error": error}, headers=headers))
    ctx, record = _ig_context(router, access_token=IG_TOKEN, ig_user_id=IG_USER)
    caught = _error(
        InstagramAccountConnector(), request("username", "ornek.magaza", ctx, max_items_per_page=2)
    )
    assert (caught.outcome, caught.code) == (outcome, code)
    assert IG_TOKEN not in caught.detail
    if outcome == ConnectorOutcome.RATE_LIMITED:
        assert caught.retry_after_seconds == 720.0
    if code == "instagram_token_invalid":
        assert record["credential_results"] == {"access_token": "rejected"}
    if code == "instagram_account_not_discoverable":
        assert "not evidence that the account does not exist" in caught.detail


def test_instagram_malformed_response_is_a_parse_error() -> None:
    router = Router().add(IG_FIRST, respond(200, body="<html>oops</html>"))
    ctx, _ = _ig_context(router, access_token=IG_TOKEN, ig_user_id=IG_USER)
    caught = _error(
        InstagramAccountConnector(), request("username", "ornek.magaza", ctx, max_items_per_page=2)
    )
    assert caught.outcome == ConnectorOutcome.PARSE_ERROR


# -- Instagram public profile page (unofficial) ------------------------------------------------

WEB = "https://www.instagram.com"
PROFILE_HTML = """<!doctype html><html><head><title>Örnek Mağaza (@ornek.magaza)</title>
<meta property="og:title" content="Örnek Mağaza (@ornek.magaza) • Instagram photos and videos">
<meta property="og:description" content="1,520 Followers, 80 Following, 3 Posts - See Instagram photos and videos from Örnek Mağaza (@ornek.magaza)">
<meta property="og:url" content="https://www.instagram.com/ornek.magaza/">
<script>fetch('https://tracker.example/beacon')</script></head><body></body></html>"""
LOGIN_HTML = """<html><head><title>Login • Instagram</title></head><body>
<form action="/accounts/login/ajax/"><input name="username"><input name="password"></form></body></html>"""


def _web_context(router: Router, *, enabled: bool = True) -> Any:
    return context(router, settings=_settings(instagram_public_web_enabled=enabled))


PAGE_PARAMS = {"capability": "public_profile_page"}


def test_instagram_profile_page_is_disabled_until_an_administrator_enables_it() -> None:
    router = Router()
    ctx, _ = _web_context(router, enabled=False)
    caught = _error(
        InstagramAccountConnector(),
        request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS),
    )
    assert (caught.outcome, caught.code) == (ConnectorOutcome.UNSUPPORTED, "capability_disabled")
    assert router.requests == []


def test_instagram_profile_page_metadata_is_read_without_cookies_or_login() -> None:
    router = Router().add(f"{WEB}/ornek.magaza/", respond(200, body=PROFILE_HTML))
    ctx, _ = _web_context(router)
    page = InstagramAccountConnector().fetch_page(
        request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS)
    )
    sent = router.requests[0]
    assert "cookie" not in sent.headers
    assert "authorization" not in sent.headers
    observation = page.observations[0]
    assert observation.observation_type == "instagram_public_profile_page"
    assert observation.payload["displayed_counts"] == {
        "followers": "1,520",
        "following": "80",
        "posts": "3",
    }
    assert observation.payload["access_method"] == "public_web_unofficial"
    assert page.entities == []
    assert page.evidence[0].indexable is False
    assert page.evidence[0].collection_metadata["access_method"] == "public_web_unofficial"


def test_instagram_login_walls_are_not_no_findings() -> None:
    redirect = Router()
    redirect.add(
        f"{WEB}/ornek.magaza/",
        respond(302, headers={"location": "/accounts/login/?next=/ornek.magaza/"}),
    )
    redirect.add(f"{WEB}/accounts/login/?next=/ornek.magaza/", respond(200, body=LOGIN_HTML))
    served = Router().add(f"{WEB}/ornek.magaza/", respond(200, body=LOGIN_HTML))
    for router in (redirect, served):
        ctx, _ = _web_context(router)
        caught = _error(
            InstagramAccountConnector(),
            request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS),
        )
        assert caught.outcome == ConnectorOutcome.AUTHENTICATION_REQUIRED
        assert caught.code == "instagram_login_wall"


def test_instagram_profile_page_other_states() -> None:
    connector = InstagramAccountConnector()
    missing = Router().add(
        f"{WEB}/gone.account/", respond(404, body="Sorry, this page isn't available.")
    )
    ctx, _ = _web_context(missing)
    page = connector.fetch_page(request("username", "gone.account", ctx, parameters=PAGE_PARAMS))
    assert page.outcome_hint == ConnectorOutcome.NO_FINDINGS
    assert "not exist" in page.notes[0]

    throttled = Router().add(f"{WEB}/ornek.magaza/", respond(429, headers={"retry-after": "120"}))
    ctx, _ = _web_context(throttled)
    caught = _error(connector, request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS))
    assert (caught.outcome, caught.retry_after_seconds) == (ConnectorOutcome.RATE_LIMITED, 120.0)

    changed = Router().add(
        f"{WEB}/ornek.magaza/", respond(200, body="<html><body>new app</body></html>")
    )
    ctx, _ = _web_context(changed)
    caught = _error(connector, request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS))
    assert (caught.outcome, caught.code) == (ConnectorOutcome.PARSE_ERROR, "unexpected_page_layout")

    private = PROFILE_HTML.replace("<body>", "<body><h2>This account is private</h2>")
    ctx, _ = _web_context(Router().add(f"{WEB}/ornek.magaza/", respond(200, body=private)))
    page = connector.fetch_page(request("username", "ornek.magaza", ctx, parameters=PAGE_PARAMS))
    assert page.incomplete_reason is not None
    assert page.observations[0].payload["private_account_indicator"] is True


def test_instagram_input_validation() -> None:
    connector = InstagramAccountConnector()
    connector.validate("username", "ornek.magaza", {})
    for bad in ("", "has space", "a" * 31, "<script>"):
        with pytest.raises(ValueError, match="Instagram username"):
            connector.validate("username", bad, {})
    with pytest.raises(ValueError, match="unknown capability"):
        connector.validate("username", "ornek.magaza", {"capability": "everything"})


# -- Telegram public web preview (unofficial) --------------------------------------------------

TME = "https://t.me"
PREVIEW = """<!doctype html><html><head><title>Örnek Haber - Telegram</title>
<script>window.stolen = document.cookie</script></head><body>
<div class="tgme_channel_info">
  <div class="tgme_channel_info_header">
    <div class="tgme_channel_info_header_title"><span dir="auto">Örnek Haber</span></div>
    <div class="tgme_channel_info_header_username"><a href="https://t.me/ornekhaber">@ornekhaber</a></div>
  </div>
  <div class="tgme_channel_info_description">Yerel haberler &amp; duyurular</div>
  <div class="tgme_channel_info_counters">
    <div class="tgme_channel_info_counter"><span class="counter_value">12.4K</span> <span class="counter_type">subscribers</span></div>
    <div class="tgme_channel_info_counter"><span class="counter_value">1.1K</span> <span class="counter_type">photos</span></div>
  </div>
</div>
<section class="tgme_channel_history js-message_history">
 <div class="tgme_widget_message_centered js-messages_more_wrap"><a href="/s/ornekhaber?before=97" class="tme_messages_more js-messages_more" data-before="97"></a></div>
 <div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="ornekhaber/97">
   <div class="tgme_widget_message_bubble">
    <a class="tgme_widget_message_photo_wrap" href="https://t.me/ornekhaber/97"></a>
    <div class="tgme_widget_message_text js-message_text" dir="auto">İzmir'de yeni şube açıldı.<br/>Ayrıntılar: <a href="https://ornek.example/haber">ornek.example/haber</a><script>alert(1)</script></div>
    <div class="tgme_widget_message_footer compact js-message_footer"><div class="tgme_widget_message_info short js-message_info">
     <span class="tgme_widget_message_views">3.2K</span><span class="copyonclick"></span>
     <span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" href="https://t.me/ornekhaber/97"><time datetime="2026-09-10T07:30:00+00:00" class="time">10:30</time></a></span>
    </div></div>
   </div>
  </div>
 </div>
 <div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="ornekhaber/100">
   <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_forwarded_from accent_color">Forwarded from <a class="tgme_widget_message_forwarded_from_name" href="https://t.me/digerkanal/5"><span dir="auto">Diğer Kanal</span></a></div>
    <a class="tgme_widget_message_reply" href="https://t.me/ornekhaber/97"><div class="tgme_widget_message_author">Örnek Haber</div><div class="tgme_widget_message_text js-message_text">İzmir'de yeni şube açıldı.</div></a>
    <div class="tgme_widget_message_text js-message_text" dir="auto">Düzeltme: açılış tarihi 12 Eylül.</div>
    <div class="tgme_widget_message_footer"><div class="tgme_widget_message_info">
     <span class="tgme_widget_message_views">1.9K</span>
     <span class="tgme_widget_message_meta">edited <a class="tgme_widget_message_date" href="https://t.me/ornekhaber/100"><time datetime="2026-09-11T09:00:00+00:00" class="time">12:00</time></a></span>
    </div></div>
   </div>
  </div>
 </div>
</section></body></html>"""


def _tg_context(router: Router, **credentials: str) -> Any:
    return context(router, credentials=credentials, settings=_settings())


def test_telegram_preview_posts_edits_links_and_gaps() -> None:
    router = Router().add(f"{TME}/s/ornekhaber", respond(200, body=PREVIEW))
    ctx, _ = _tg_context(router)
    page = TelegramPublicChannelConnector().fetch_page(request("username", "@ornekhaber", ctx))
    channel, first, second = page.observations
    assert channel.observation_type == "telegram_channel_preview"
    assert channel.payload["title"] == "Örnek Haber"
    assert channel.payload["description"] == "Yerel haberler & duyurular"
    assert channel.payload["displayed_counters"] == {"subscribers": "12.4K", "photos": "1.1K"}
    assert first.source_object_id == "ornekhaber/97"
    assert first.payload["text"] == "İzmir'de yeni şube açıldı.\nAyrıntılar: ornek.example/haber"
    assert first.payload["links"] == ["https://ornek.example/haber"]
    assert first.payload["media"] == ["photo"]
    assert first.payload["edited"] is False
    assert first.payload["views_displayed"] == "3.2K"
    assert first.event_time == datetime(2026, 9, 10, 7, 30, tzinfo=UTC)
    assert second.payload["text"] == "Düzeltme: açılış tarihi 12 Eylül."  # not the quoted reply
    assert second.payload["edited"] is True
    assert second.payload["forwarded_from"] == "Diğer Kanal"
    assert second.payload["forwarded_from_link"] == "https://t.me/digerkanal/5"
    assert "alert(1)" not in json.dumps(first.payload)
    assert "document.cookie" not in json.dumps(channel.payload)
    assert page.coverage["post_numbers_not_visible"] == [98, 99]
    assert "not shown in the preview" in page.notes[0]
    assert page.next_cursor == {"before": "97"}
    html, posts = page.evidence
    assert html.indexable is False
    assert posts.derived_from == "preview"
    assert posts.collection_metadata["access_method"] == "public_web_unofficial"

    older = Router().add(
        f"{TME}/s/ornekhaber?before=97",
        respond(
            200, body=PREVIEW.replace('data-before="97"', "").replace("tme_messages_more", "x")
        ),
    )
    ctx, _ = _tg_context(older)
    next_page = TelegramPublicChannelConnector().fetch_page(
        request("username", "ornekhaber", ctx, page_index=1, cursor=page.next_cursor)
    )
    assert next_page.has_more is False
    assert [o.observation_type for o in next_page.observations] == [
        "telegram_post",
        "telegram_post",
    ]


def test_telegram_preview_unavailable_layout_change_and_throttling_are_distinct() -> None:
    connector = TelegramPublicChannelConnector()
    redirected = Router()
    redirected.add(f"{TME}/s/gizlikanal", respond(302, headers={"location": "/gizlikanal"}))
    redirected.add(f"{TME}/gizlikanal", respond(200, body="<html>Join channel</html>"))
    ctx, _ = _tg_context(redirected)
    caught = _error(connector, request("username", "gizlikanal", ctx))
    assert (caught.outcome, caught.code) == (
        ConnectorOutcome.ACCESS_DENIED,
        "telegram_preview_unavailable",
    )
    assert "not evidence that the source does not exist" in caught.detail

    changed = Router().add(
        f"{TME}/s/ornekhaber", respond(200, body="<html><body>new</body></html>")
    )
    ctx, _ = _tg_context(changed)
    caught = _error(connector, request("username", "ornekhaber", ctx))
    assert (caught.outcome, caught.code) == (ConnectorOutcome.PARSE_ERROR, "unexpected_page_layout")

    throttled = Router().add(f"{TME}/s/ornekhaber", respond(429, headers={"retry-after": "30"}))
    ctx, _ = _tg_context(throttled)
    caught = _error(connector, request("username", "ornekhaber", ctx))
    assert (caught.outcome, caught.retry_after_seconds) == (ConnectorOutcome.RATE_LIMITED, 30.0)

    empty = PREVIEW.split("<section")[0] + "</body></html>"
    ctx, _ = _tg_context(Router().add(f"{TME}/s/ornekhaber", respond(200, body=empty)))
    page = connector.fetch_page(request("username", "ornekhaber", ctx))
    assert [o.observation_type for o in page.observations] == ["telegram_channel_preview"]
    assert page.coverage["posts_on_page"] == 0
    assert page.has_more is False


# -- Telegram Bot API (official) ---------------------------------------------------------------

BOT = f"https://api.telegram.org/bot{BOT_TOKEN}"


def test_telegram_bot_api_chat_info_redacts_the_token() -> None:
    chat = {
        "id": -1001234567890,
        "type": "channel",
        "title": "Örnek Haber",
        "username": "ornekhaber",
        "description": "Yerel haberler",
        "has_protected_content": False,
        "pinned_message": {"message_id": 97, "text": "not stored"},
    }
    router = Router()
    router.add(
        f"{BOT}/getChat?chat_id=@ornekhaber", respond(200, json_body={"ok": True, "result": chat})
    )
    router.add(
        f"{BOT}/getChatMemberCount?chat_id=-1001234567890",
        respond(200, json_body={"ok": True, "result": 12431}),
    )
    ctx, record = _tg_context(router, bot_token=BOT_TOKEN)
    page = TelegramPublicChannelConnector().fetch_page(
        request("username", "ornekhaber", ctx, parameters={"capability": "bot_api_chat_info"})
    )
    entity = page.entities[0]
    assert (entity.match.identifier_type, entity.match.value) == ("platform_id", "-1001234567890")
    payload = page.observations[0].payload
    assert payload["member_count"] == 12431
    assert payload["pinned_message_id"] == 97
    assert "not stored" not in json.dumps(payload)
    assert record["credential_results"] == {"bot_token": "accepted"}
    assert page.evidence[0].source_reference.startswith("https://api.telegram.org/bot[redacted]/")
    _no_secret(page, BOT_TOKEN)


def test_telegram_bot_api_states() -> None:
    connector = TelegramPublicChannelConnector()
    params = {"capability": "bot_api_chat_info"}
    url = f"{BOT}/getChat?chat_id=@ornekhaber"

    ctx, _ = _tg_context(Router())
    caught = _error(connector, request("username", "ornekhaber", ctx, parameters=params))
    assert (caught.outcome, caught.code) == (
        ConnectorOutcome.AUTHENTICATION_REQUIRED,
        "credential_not_configured",
    )

    ctx, _ = _tg_context(Router(), bot_token="not-a-token")
    caught = _error(connector, request("username", "ornekhaber", ctx, parameters=params))
    assert caught.code == "credential_invalid"

    missing = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    ctx, _ = _tg_context(Router().add(url, respond(400, json_body=missing)), bot_token=BOT_TOKEN)
    page = connector.fetch_page(request("username", "ornekhaber", ctx, parameters=params))
    assert page.outcome_hint == ConnectorOutcome.NO_FINDINGS
    _no_secret(page, BOT_TOKEN)

    rejected = {"ok": False, "error_code": 401, "description": "Unauthorized"}
    ctx, record = _tg_context(
        Router().add(url, respond(401, json_body=rejected)), bot_token=BOT_TOKEN
    )
    caught = _error(connector, request("username", "ornekhaber", ctx, parameters=params))
    assert caught.outcome == ConnectorOutcome.AUTHENTICATION_REQUIRED
    assert record["credential_results"] == {"bot_token": "rejected"}
    assert BOT_TOKEN not in caught.detail

    flood = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 17",
        "parameters": {"retry_after": 17},
    }
    ctx, _ = _tg_context(Router().add(url, respond(429, json_body=flood)), bot_token=BOT_TOKEN)
    caught = _error(connector, request("username", "ornekhaber", ctx, parameters=params))
    assert (caught.outcome, caught.retry_after_seconds) == (ConnectorOutcome.RATE_LIMITED, 17.0)

    partial = Router()
    partial.add(
        url, respond(200, json_body={"ok": True, "result": {"id": -100, "type": "channel"}})
    )
    partial.add(f"{BOT}/getChatMemberCount?chat_id=-100", respond(502, body="bad gateway"))
    ctx, _ = _tg_context(partial, bot_token=BOT_TOKEN)
    page = connector.fetch_page(request("username", "ornekhaber", ctx, parameters=params))
    assert page.incomplete_reason is not None
    assert page.observations[0].payload["member_count"] is None


# -- YouTube Data API --------------------------------------------------------------------------

YT = "https://www.googleapis.com/youtube/v3"
CHANNEL_ID = "UCsynthetic000000000000a"
UPLOADS = "UUsynthetic000000000000a"


def _yt_context(router: Router, **credentials: str) -> Any:
    return context(router, credentials=credentials, settings=_settings())


def test_youtube_channel_and_uploads_with_placeholders() -> None:
    channel = {
        "items": [
            {
                "id": CHANNEL_ID,
                "snippet": {
                    "title": "Örnek Kanal",
                    "customUrl": "@ornekkanal",
                    "publishedAt": "2020-01-02T03:04:05Z",
                },
                "contentDetails": {"relatedPlaylists": {"uploads": UPLOADS}},
                "statistics": {
                    "viewCount": "1000",
                    "subscriberCount": "0",
                    "hiddenSubscriberCount": True,
                    "videoCount": "3",
                },
            }
        ]
    }
    uploads = {
        "items": [
            {
                "snippet": {
                    "title": "Tanıtım",
                    "position": 0,
                    "publishedAt": "2026-09-01T00:00:00Z",
                },
                "contentDetails": {
                    "videoId": "abcdefghijk",
                    "videoPublishedAt": "2026-09-01T00:00:00Z",
                },
                "status": {"privacyStatus": "public"},
            },
            {
                "snippet": {"title": "Private video", "position": 1},
                "contentDetails": {"videoId": "privatevid1"},
                "status": {"privacyStatus": "private"},
            },
        ],
        "nextPageToken": "CAIQAA",
        "pageInfo": {"totalResults": 3},
    }
    router = Router()
    router.add(
        f"{YT}/channels?part=snippet%2CcontentDetails%2Cstatistics&forHandle=%40ornekkanal",
        respond(200, json_body=channel),
    )
    router.add(
        f"{YT}/playlistItems?part=snippet%2CcontentDetails%2Cstatus&playlistId={UPLOADS}&maxResults=50",
        respond(200, json_body=uploads),
    )
    ctx, record = _yt_context(router, api_key=YT_KEY)
    connector = YouTubeDataApiConnector()
    first = connector.fetch_page(request("username", "ornekkanal", ctx, max_items_per_page=50))
    assert router.requests[0].headers["x-goog-api-key"] == YT_KEY
    assert YT_KEY not in str(router.requests[0].url)
    assert first.entities[0].match.value == CHANNEL_ID
    assert first.observations[0].payload["subscriber_count_hidden"] is True
    assert first.quota is not None
    assert first.quota["units_this_request"] == 1
    assert first.next_cursor == {"playlist_id": UPLOADS, "page_token": None}
    second = connector.fetch_page(
        request(
            "username",
            "ornekkanal",
            ctx,
            page_index=1,
            cursor=first.next_cursor,
            max_items_per_page=50,
        )
    )
    videos = [o.payload for o in second.observations]
    assert videos[0]["placeholder"] is None
    assert videos[1]["placeholder"] == "private"
    assert second.next_cursor == {"playlist_id": UPLOADS, "page_token": "CAIQAA"}
    assert record["credential_results"] == {"api_key": "accepted"}
    _no_secret(first, YT_KEY)
    _no_secret(second, YT_KEY)


def test_youtube_video_comments_disabled_and_not_found_states() -> None:
    connector = YouTubeDataApiConnector()
    params = {"capability": "video_comments"}
    video_url = f"{YT}/videos?part=snippet%2Cstatistics%2Cstatus&id=abcdefghijk"
    comments_url = (
        f"{YT}/commentThreads?part=snippet&videoId=abcdefghijk&maxResults=100&order=time"
        "&textFormat=plainText"
    )
    video = {
        "items": [
            {
                "id": "abcdefghijk",
                "snippet": {
                    "title": "Tanıtım",
                    "channelId": CHANNEL_ID,
                    "publishedAt": "2026-09-01T00:00:00Z",
                },
                "statistics": {"commentCount": "2"},
            }
        ]
    }
    threads = {
        "items": [
            {
                "id": "Ugthread1",
                "snippet": {
                    "totalReplyCount": 2,
                    "topLevelComment": {
                        "id": "Ugcomment1",
                        "snippet": {
                            "authorDisplayName": "@yorumcu",
                            "authorChannelId": {"value": "UCauthor000000000000000a"},
                            "textOriginal": "Harika <b>video</b>",
                            "likeCount": 4,
                            "publishedAt": "2026-09-02T10:00:00Z",
                            "updatedAt": "2026-09-03T10:00:00Z",
                        },
                    },
                },
            },
        ]
    }
    router = Router()
    router.add(video_url, respond(200, json_body=video))
    router.add(comments_url, respond(200, json_body=threads))
    ctx, _ = _yt_context(router, api_key=YT_KEY)
    first = connector.fetch_page(request("youtube_video_id", "abcdefghijk", ctx, parameters=params))
    assert first.has_more is True
    comments = connector.fetch_page(
        request(
            "youtube_video_id",
            "abcdefghijk",
            ctx,
            parameters=params,
            page_index=1,
            cursor=first.next_cursor,
        )
    )
    comment = comments.observations[0]
    assert comment.payload["text"] == "Harika <b>video</b>"
    assert comment.payload["edited"] is True
    assert comment.payload["author_channel_id"] == "UCauthor000000000000000a"
    assert "not a verified identity" in comment.payload["author_label_note"]
    assert comments.entities == []

    disabled = {
        "error": {
            "code": 403,
            "message": "Comments are disabled.",
            "errors": [{"reason": "commentsDisabled", "domain": "youtube.commentThread"}],
        }
    }
    ctx, _ = _yt_context(
        Router().add(comments_url, respond(403, json_body=disabled)), api_key=YT_KEY
    )
    page = connector.fetch_page(
        request(
            "youtube_video_id",
            "abcdefghijk",
            ctx,
            parameters=params,
            page_index=1,
            cursor={"page_token": None},
        )
    )
    assert page.coverage == {"comments_status": "disabled"}
    assert page.items == 0
    assert "not the same as a video without comments" in page.notes[0]

    gone = {"error": {"code": 404, "errors": [{"reason": "videoNotFound"}]}}
    ctx, _ = _yt_context(Router().add(comments_url, respond(404, json_body=gone)), api_key=YT_KEY)
    caught = _error(
        connector,
        request(
            "youtube_video_id",
            "abcdefghijk",
            ctx,
            parameters=params,
            page_index=1,
            cursor={"page_token": None},
        ),
    )
    assert caught.outcome == ConnectorOutcome.UNAVAILABLE

    ctx, _ = _yt_context(
        Router().add(video_url, respond(200, json_body={"items": []})), api_key=YT_KEY
    )
    page = connector.fetch_page(request("youtube_video_id", "abcdefghijk", ctx, parameters=params))
    assert page.outcome_hint == ConnectorOutcome.NO_FINDINGS


@pytest.mark.parametrize(
    ("status", "reason", "message", "outcome", "code"),
    [
        (403, "quotaExceeded", "quota", ConnectorOutcome.RATE_LIMITED, "youtube_quota_exceeded"),
        (
            403,
            "rateLimitExceeded",
            "slow down",
            ConnectorOutcome.RATE_LIMITED,
            "youtube_rate_limited",
        ),
        (
            400,
            "badRequest",
            "API key not valid. Please pass a valid API key.",
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            "youtube_key_rejected",
        ),
        (
            403,
            "accessNotConfigured",
            "not enabled",
            ConnectorOutcome.ACCESS_DENIED,
            "youtube_accessNotConfigured",
        ),
        (500, "backendError", "oops", ConnectorOutcome.UNAVAILABLE, "youtube_backendError"),
    ],
)
def test_youtube_errors_are_distinguished(
    status: int, reason: str, message: str, outcome: ConnectorOutcome, code: str
) -> None:
    url = f"{YT}/channels?part=snippet%2CcontentDetails%2Cstatistics&id={CHANNEL_ID}"
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    ctx, record = _yt_context(Router().add(url, respond(status, json_body=body)), api_key=YT_KEY)
    caught = _error(YouTubeDataApiConnector(), request("youtube_channel_id", CHANNEL_ID, ctx))
    assert (caught.outcome, caught.code) == (outcome, code)
    if code == "youtube_quota_exceeded":
        assert caught.retry_after_seconds is not None
        assert 60 <= caught.retry_after_seconds <= 25 * 3600
    if code == "youtube_key_rejected":
        assert record["credential_results"] == {"api_key": "rejected"}


def test_youtube_quota_reset_is_midnight_pacific_and_inputs_match_capabilities() -> None:
    noon_utc = datetime(2026, 9, 16, 19, 0, tzinfo=UTC)  # 12:00 in Los Angeles (PDT)
    assert youtube.seconds_until_quota_reset(noon_utc) == 12 * 3600
    connector = YouTubeDataApiConnector()
    connector.validate("username", "@ornekkanal", {})
    connector.validate("youtube_video_id", "abcdefghijk", {"capability": "video_comments"})
    with pytest.raises(ValueError, match="accepts"):
        connector.validate("youtube_video_id", "abcdefghijk", {})
    with pytest.raises(ValueError, match="not a valid"):
        connector.validate("youtube_channel_id", "UCshort", {})
    with pytest.raises(ValueError, match="unknown capability"):
        connector.validate("youtube_video_id", "abcdefghijk", {"capability": "captions_download"})
    ctx, _ = _yt_context(Router())
    caught = _error(connector, request("youtube_channel_id", CHANNEL_ID, ctx))
    assert caught.code == "credential_not_configured"
