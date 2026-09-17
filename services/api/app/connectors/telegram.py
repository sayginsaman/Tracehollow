"""Telegram connector: explicitly selected public channels only.

Capabilities (see ``CAPABILITIES`` and docs/connectors/telegram.md):

``public_web_preview``
    The public web preview Telegram serves at ``https://t.me/s/<channel>`` for channels whose
    owners allow it: channel title, description and counters as displayed, and recent posts with
    their post numbers, dates, edit marks, view counts, links and media types. Undocumented HTML;
    older posts are reached with ``?before=<post number>``.
``bot_api_chat_info``
    The official Bot API (``getChat``, ``getChatMemberCount``; Bot API 10.3, core.telegram.org,
    checked 2026-09-16) for a public chat's stable ID and metadata. Bots cannot read the history of
    channels they are not members of; this connector never joins, posts or reads messages.

User-account (MTProto) sessions are not implemented: client libraries open raw MTProto TCP
connections that the connector network policy cannot constrain. Private groups, joining and
sending messages are excluded.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from app.connectors import http, netguard, social
from app.connectors.base import (
    AccessMethod,
    CapabilitySpec,
    CapabilityStatus,
    CollectionMode,
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    CredentialSpec,
    EntityDraft,
    EvidenceDraft,
    FetchRequest,
    IdentifierDraft,
    ObservationDraft,
    RetryPolicy,
    VerificationStatus,
    selected_capability,
    validate_parameters,
)
from app.connectors.feeds import parse_date
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "telegram.public_channel"
BOT_API_VERSION = "10.3"
PLATFORM = "t.me"
_CHANNEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_BOT_TOKEN = re.compile(r"^\d{3,20}:[A-Za-z0-9_-]{30,64}$")
_POST = re.compile(r"^([A-Za-z0-9_]{4,32})/(\d{1,12})$")
_MAX_GAP_REPORT = 200
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
_SKIP = frozenset({"script", "style", "template", "noscript", "svg", "iframe", "object"})
_MEDIA_CLASSES = {
    "tgme_widget_message_photo_wrap": "photo",
    "tgme_widget_message_video_player": "video",
    "tgme_widget_message_roundvideo_player": "round_video",
    "tgme_widget_message_document": "document",
    "tgme_widget_message_voice": "voice",
    "tgme_widget_message_poll": "poll",
    "tgme_widget_message_sticker_wrap": "sticker",
    "tgme_widget_message_location_wrap": "location",
    "message_media_not_supported": "not_supported_in_preview",
}
_CHAT_FIELDS = (
    "id", "type", "title", "username", "active_usernames", "description", "linked_chat_id",
    "has_protected_content", "has_visible_history", "invite_link",
)  # fmt: skip

CAPABILITIES = (
    CapabilitySpec(
        name="public_web_preview",
        label="Unofficial: public channel web preview",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.PUBLIC_WEB_UNOFFICIAL,
        provider="t.me/s public channel preview (undocumented HTML)",
        account_types=("public channels whose owners allow the web preview",),
        content_types=("channel title, description and counters", "recent posts"),
        returned_fields=(
            "channel title",
            "description",
            "displayed counters",
            "post number",
            "post text",
            "post date (time element)",
            "edited mark",
            "displayed view count",
            "links in text",
            "forwarded-from name and link",
            "media types present",
        ),
        unavailable_fields=(
            "numeric channel ID",
            "exact view counts (rounded display strings only)",
            "reactions and comments in most layouts",
            "media files",
            "posts deleted or hidden from the preview",
            "private channels, groups and chats",
            "member lists",
        ),
        stable_identifiers=("channel username (can change)", "post number within the channel"),
        pagination=(
            "Newest posts first; each further page requests posts before the oldest post number "
            "seen, up to the page limit. Gaps in post numbers are reported as not visible, not "
            "as deleted."
        ),
        session_requirements="None. No login, no cookies.",
        restrictions=(
            "Only channels with the web preview enabled. The preview layout is undocumented and "
            "can change; an unexpected layout is a parse error, never an empty result."
        ),
        cost_quota="No charge; Telegram may throttle repeated requests (HTTP 429).",
        verification_status=VerificationStatus.FIXTURE_TESTED,
        collection_mode=CollectionMode.PLATFORM_PROBE,
    ),
    CapabilitySpec(
        name="bot_api_chat_info",
        label="Official Bot API: public chat metadata",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.OFFICIAL_API,
        provider=f"Telegram Bot API {BOT_API_VERSION}",
        account_types=("public channels", "public supergroups"),
        content_types=("chat metadata", "member count"),
        returned_fields=(*_CHAT_FIELDS, "member_count"),
        unavailable_fields=(
            "message history of chats the bot is not a member of",
            "member lists",
            "private chats",
        ),
        stable_identifiers=("chat id (numeric)", "username (can change)"),
        pagination="None: two requests per run (getChat, getChatMemberCount).",
        session_requirements=(
            "A bot token from @BotFather, stored encrypted on the server. The token travels in "
            "the request path and is removed from recorded provenance."
        ),
        restrictions=(
            "The connector only reads chat metadata for the selected public username; it never "
            "joins chats, sends messages or reads updates."
        ),
        cost_quota="Free; Bot API flood limits return HTTP 429 with retry_after.",
        verification_status=VerificationStatus.FIXTURE_TESTED,
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_names=("bot_token",),
        references=("https://core.telegram.org/bots/api (Bot API 10.3, checked 2026-09-16)",),
    ),
    CapabilitySpec(
        name="mtproto_user_session",
        label="User-account session (MTProto client)",
        status=CapabilityStatus.NOT_IMPLEMENTED,
        access_method=AccessMethod.UNOFFICIAL_CLIENT,
        provider="Telethon or other MTProto client libraries",
        account_types=(),
        content_types=(),
        returned_fields=(),
        unavailable_fields=(),
        stable_identifiers=(),
        pagination="",
        session_requirements="A Telegram user account session (phone login).",
        restrictions="",
        cost_quota="",
        verification_status=None,
        reason=(
            "Not implemented: MTProto clients open raw TCP connections that the connector network "
            "policy (SSRF checks, egress limits) does not govern, and a user session can reach "
            "far more than the selected public source. Telethon's repository was also archived "
            "and moved (2026-02); it is in maintenance mode."
        ),
    ),
    CapabilitySpec(
        name="private_groups_and_messaging",
        label="Private groups, joining and messaging",
        status=CapabilityStatus.EXCLUDED,
        access_method=AccessMethod.UNOFFICIAL_CLIENT,
        provider="none",
        account_types=(),
        content_types=(),
        returned_fields=(),
        unavailable_fields=(),
        stable_identifiers=(),
        pagination="",
        session_requirements="",
        restrictions="",
        cost_quota="",
        verification_status=None,
        reason=(
            "Excluded: automatically joining private groups, sending messages or expanding "
            "beyond the selected public source are outside Tracehollow's access rules."
        ),
    ),
)


class TelegramPublicChannelConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Telegram public channel",
        synthetic=False,
        description=(
            "Collects one explicitly selected public Telegram channel: its web preview posts, or "
            "its chat metadata through the official Bot API. Never joins, posts or reads private "
            "chats."
        ),
        supported_input_types=("username",),
        collection_mode=CollectionMode.PLATFORM_PROBE,
        credential_requirements=(
            "Web preview: none. Bot API: a bot token (stored encrypted, never shown again)."
        ),
        coverage=(
            "Web preview: posts Telegram shows in the public preview, newest first, up to the "
            "page limit. Bot API: metadata and member count of a public chat. Private channels, "
            "groups, deleted posts and member lists are not covered."
        ),
        max_pages=10,
        max_items_per_page=50,
        timeout_seconds=120,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
            base_backoff_seconds=3.0,
            max_backoff_seconds=90.0,
        ),
        output_schema="tracehollow.telegram.public_channel/v1",
        cost_model="Free.",
        quota_notes=(
            "Web preview: one request per page. Bot API: two requests per run; flood limits "
            "return retry_after, which is honoured."
        ),
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        parameters=(social.capability_parameter(CAPABILITIES, "public_web_preview"),),
        credentials=(
            CredentialSpec(
                name="bot_token",
                label="Bot token",
                description=(
                    "Token issued by @BotFather. Used only for getChat and getChatMemberCount "
                    "against the configured Bot API address."
                ),
                required=False,
            ),
        ),
        cache_policy="No caching: every execution requests the source again.",
        max_concurrent_runs=2,
        min_request_interval_seconds=2.0,
        provider_terms=(
            "Telegram Terms of Service and Bot API terms. Collect only public sources the "
            "investigation is authorized to review."
        ),
        documentation="docs/connectors/telegram.md",
        capabilities=CAPABILITIES,
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        if not _CHANNEL.match(_channel_name(input_value)):
            raise ValueError(
                "a Telegram public username has 5-32 letters, digits or underscores and starts "
                "with a letter"
            )
        validate_parameters(self.descriptor, parameters)
        selected_capability(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "username":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only usernames are supported.")
        spec = social.capability_for(self.descriptor, request)
        if spec.name == "bot_api_chat_info":
            return self._bot_api(request, spec)
        return self._preview(request, spec)

    # -- public web preview ----------------------------------------------------------------------

    def _preview(self, request: FetchRequest, spec: CapabilitySpec) -> ConnectorPage:
        settings = request.context.settings
        base = str(settings.telegram_web_base_url if settings is not None else "https://t.me")
        base = base.rstrip("/")
        channel = _channel_name(request.input_value)
        url = f"{base}/s/{quote(channel, safe='')}"
        if request.page_index > 0:
            before = str((request.cursor or {}).get("before") or "")
            if not before.isdigit():
                raise ConnectorError(
                    ConnectorOutcome.PARSE_ERROR,
                    "The pagination cursor is missing or malformed.",
                    code="unexpected_pagination",
                )
            url += f"?before={before}"
        result = http.fetch(
            request,
            url,
            headers={"accept": "text/html", "accept-language": "en"},
            pacing_key="web",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=True,
            max_bytes=3 * 1024 * 1024,
        )
        if result.status_code == 429:
            raise ConnectorError(
                ConnectorOutcome.RATE_LIMITED,
                "Telegram throttled the preview request (HTTP 429).",
                retry_after_seconds=http.retry_after_seconds(result.headers.get("retry-after"))
                or 60.0,
                code="telegram_throttled",
            )
        if result.status_code >= 500:
            http.raise_for_status(result, "Telegram")
        if result.status_code == 404 or not urlsplit(result.final_url).path.startswith("/s/"):
            raise ConnectorError(
                ConnectorOutcome.ACCESS_DENIED,
                f"Telegram has no public web preview for {channel}. The name may not exist, or "
                "belong to a user, bot, private channel or a channel with the preview disabled. "
                "This is not evidence that the source does not exist.",
                code="telegram_preview_unavailable",
            )
        if result.status_code != 200:
            http.raise_for_status(result, "Telegram")
        html_text, encoding = http.decode_text(result, "utf-8")
        parsed = _PreviewParser.parse(html_text, result.final_url)
        if not parsed.channel.get("title") and not parsed.messages:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The preview page did not contain the expected channel or post elements; the "
                "layout may have changed.",
                code="unexpected_page_layout",
            )
        page_key = "preview"
        evidence = [
            EvidenceDraft(
                key=page_key,
                kind="html",
                content=result.content,
                content_type=result.headers.get("content-type", "text/html")[:100],
                title=f"Telegram preview: {channel}"
                + (f" (before post {request.cursor.get('before')})" if request.cursor else ""),
                source_reference=result.final_url,
                collection_metadata={
                    **result.metadata(),
                    **social.provenance(spec),
                    "decoded_with": encoding,
                },
                indexable=False,
            )
        ]
        posts = []
        for message in parsed.messages:
            match = _POST.match(message["post"])
            if match is None:
                continue
            posts.append({**message, "channel": match.group(1), "number": int(match.group(2))})
        if posts:
            evidence.append(
                EvidenceDraft(
                    key="posts",
                    kind="json",
                    content=json.dumps(
                        {
                            "channel": channel,
                            "source": result.final_url,
                            "capability": spec.name,
                            "posts": [_post_payload(post) for post in posts],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ).encode(),
                    content_type="application/json",
                    title=f"Telegram posts parsed from the preview of {channel}",
                    source_reference=result.final_url,
                    collection_metadata={**social.provenance(spec), "derived": "preview_posts"},
                    derived_from=page_key,
                )
            )
        observations: list[ObservationDraft] = []
        if request.page_index == 0 and parsed.channel.get("title"):
            observations.append(
                ObservationDraft(
                    observation_type="telegram_channel_preview",
                    evidence_key=page_key,
                    source_object_id=f"username:{channel.lower()}",
                    payload={
                        "channel": channel,
                        "title": parsed.channel.get("title"),
                        "description": parsed.channel.get("description"),
                        "username_shown": parsed.channel.get("username"),
                        "displayed_counters": parsed.counters,
                        "capability": spec.name,
                        "access_method": str(spec.access_method),
                        "note": "Counters are display strings and may be rounded.",
                    },
                    idempotency_suffix="channel",
                )
            )
        for post in posts:
            observations.append(
                ObservationDraft(
                    observation_type="telegram_post",
                    evidence_key="posts",
                    source_object_id=post["post"],
                    payload={**_post_payload(post), "capability": spec.name},
                    event_time=parse_date(post.get("datetime")),
                    source_published_at=parse_date(post.get("datetime")),
                    idempotency_suffix=f"post:{post['post']}",
                    dedupe_across_pages=True,
                )
            )
        numbers = sorted(
            {post["number"] for post in posts if post["channel"].lower() == channel.lower()}
        )
        visible = set(numbers)
        gaps = [n for n in range(numbers[0], numbers[-1]) if n not in visible] if numbers else []
        # Without an older-posts link the preview offers nothing older than this page.
        older = parsed.before if parsed.before and parsed.before.isdigit() else None
        has_more = older is not None and bool(posts)
        return ConnectorPage(
            page_index=request.page_index,
            has_more=has_more,
            next_cursor={"before": older} if has_more else None,
            evidence=evidence,
            observations=observations,
            items=len(observations),
            coverage={
                "capability": spec.name,
                "posts_on_page": len(posts),
                "newest_post_number": numbers[-1] if numbers else None,
                "oldest_post_number": numbers[0] if numbers else None,
                "post_numbers_not_visible": gaps[:_MAX_GAP_REPORT],
                "post_numbers_not_visible_total": len(gaps),
            },
            notes=(
                [
                    f"{len(gaps)} post number(s) between the oldest and newest visible post are "
                    "not shown in the preview. They may be deleted, service messages, or hidden "
                    "from the preview; the preview does not say which."
                ]
                if gaps
                else []
            ),
        )

    # -- official Bot API ------------------------------------------------------------------------

    def _bot_api(self, request: FetchRequest, spec: CapabilitySpec) -> ConnectorPage:
        token = social.require_credentials(request, spec)["bot_token"]
        if not _BOT_TOKEN.match(token):
            raise ConnectorError(
                ConnectorOutcome.AUTHENTICATION_REQUIRED,
                "The stored bot token does not have the format Telegram issues.",
                code="credential_invalid",
            )
        settings = request.context.settings
        base = str(
            settings.telegram_bot_api_base_url
            if settings is not None
            else "https://api.telegram.org"
        ).rstrip("/")
        channel = _channel_name(request.input_value)
        chat, chat_result = self._call(request, base, token, "getChat", f"@{channel}")
        evidence = [
            self._evidence(chat_result, spec, "chat", f"Telegram Bot API getChat: {channel}")
        ]
        if chat is None:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                evidence=evidence,
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                coverage={"capability": spec.name},
                notes=[
                    "The Bot API answered 'chat not found' for this username at retrieval time. "
                    "It may not exist, may belong to a user or a private chat, or may have been "
                    "renamed."
                ],
            )
        if not isinstance(chat, dict) or chat.get("id") is None:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The getChat result lacks the chat ID.",
                code="unexpected_schema",
            )
        request.context.credential_result("bot_token", "accepted")
        incomplete = None
        member_count: int | None = None
        try:
            count, count_result = self._call(
                request, base, token, "getChatMemberCount", str(chat["id"])
            )
            evidence.append(
                self._evidence(
                    count_result, spec, "member_count", f"Telegram member count: {channel}"
                )
            )
            member_count = count if isinstance(count, int) and not isinstance(count, bool) else None
        except ConnectorError as error:
            if error.outcome == ConnectorOutcome.CANCELED:
                raise
            incomplete = f"The member count could not be retrieved ({error.code})."
        chat_id = str(chat["id"])
        pinned = chat.get("pinned_message")
        payload = {
            **{name: chat.get(name) for name in _CHAT_FIELDS},
            "member_count": member_count,
            "pinned_message_id": pinned.get("message_id") if isinstance(pinned, dict) else None,
            "capability": spec.name,
            "access_method": str(spec.access_method),
        }
        usernames = [str(chat.get("username") or channel)]
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=evidence,
            entities=[
                EntityDraft(
                    key="chat",
                    entity_type="platform_account",
                    display_name=f"{chat.get('title') or channel} on Telegram",
                    match=IdentifierDraft("platform_id", chat_id, PLATFORM),
                    identifiers=(
                        IdentifierDraft("platform_id", chat_id, PLATFORM),
                        *(IdentifierDraft("username", name, PLATFORM) for name in usernames),
                    ),
                    description=f"Public Telegram {chat.get('type') or 'chat'} (Bot API getChat).",
                    attributes={"platform": PLATFORM, "chat_type": chat.get("type")},
                )
            ],
            observations=[
                ObservationDraft(
                    observation_type="telegram_chat",
                    evidence_key="chat",
                    entity_key="chat",
                    source_object_id=chat_id,
                    payload=payload,
                    idempotency_suffix="chat",
                )
            ],
            items=1,
            incomplete_reason=incomplete,
            coverage={"capability": spec.name, "member_count_retrieved": member_count is not None},
        )

    def _call(
        self, request: FetchRequest, base: str, token: str, method: str, chat_id: str
    ) -> tuple[Any, netguard.FetchResult]:
        try:
            raw = http.fetch(
                request,
                f"{base}/bot{token}/{method}?chat_id={quote(chat_id, safe='@')}",
                headers={"accept": "application/json"},
                pacing_key="bot_api",
                interval_seconds=1.0,
                same_origin_redirects_only=True,
            )
        except ConnectorError as error:
            error.detail = error.detail.replace(token, social.REDACTED)
            raise
        result = social.redact(raw, token)
        body = social.json_body(result, "Bot API")
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The Bot API response has no 'ok' field.",
                code="unexpected_schema",
            )
        if body["ok"]:
            return body.get("result"), result
        code = social.integer(body.get("error_code")) or result.status_code
        description = (social.text(body.get("description"), 300) or "").replace(
            token, social.REDACTED
        )
        parameters = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
        if code == 401:
            request.context.credential_result("bot_token", "rejected")
            raise ConnectorError(
                ConnectorOutcome.AUTHENTICATION_REQUIRED,
                f"Telegram rejected the bot token: {description}. Replace it on the Sources page.",
                code="telegram_bot_token_rejected",
            )
        if code == 429:
            retry_after = social.integer((parameters or {}).get("retry_after"))
            raise ConnectorError(
                ConnectorOutcome.RATE_LIMITED,
                f"Telegram flood limit: {description}",
                retry_after_seconds=float(retry_after) if retry_after is not None else 30.0,
                code="telegram_flood_limit",
            )
        if code == 400 and "chat not found" in description.lower() and method == "getChat":
            return None, result
        if code == 403:
            raise ConnectorError(
                ConnectorOutcome.ACCESS_DENIED,
                f"Telegram refused access: {description}",
                code="telegram_forbidden",
            )
        if code >= 500:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                f"The Bot API is temporarily unavailable ({code}).",
                code=f"http_{code}",
            )
        raise ConnectorError(
            ConnectorOutcome.UNSUPPORTED,
            f"The Bot API rejected {method} ({code}): {description}",
            code=f"telegram_error_{code}",
        )

    def _evidence(
        self, result: netguard.FetchResult, spec: CapabilitySpec, key: str, title: str
    ) -> EvidenceDraft:
        return EvidenceDraft(
            key=key,
            kind="json",
            content=result.content,
            content_type="application/json",
            title=title[:300],
            source_reference=result.final_url,
            collection_metadata={
                **result.metadata(),
                **social.provenance(spec),
                "bot_api_version": BOT_API_VERSION,
            },
            access_category="credentialed",
        )


def _channel_name(value: str) -> str:
    name = value.strip()
    for prefix in ("https://t.me/s/", "https://t.me/", "t.me/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.removeprefix("@").strip("/")


def _post_payload(post: dict[str, Any]) -> dict[str, Any]:
    return {
        "post": post["post"],
        "post_number": post["number"],
        "channel": post["channel"],
        "text": post.get("text"),
        "datetime": post.get("datetime"),
        "edited": post.get("edited", False),
        "views_displayed": post.get("views"),
        "links": post.get("links", []),
        "forwarded_from": post.get("forwarded_from"),
        "forwarded_from_link": post.get("forwarded_from_link"),
        "media": sorted(post.get("media", [])),
        "service_message": post.get("service", False),
        "permalink": post.get("permalink"),
    }


class _PreviewParser(HTMLParser):
    """Reads channel info and post blocks from preview HTML. Nothing is executed or fetched."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.channel: dict[str, str] = {}
        self.counters: dict[str, str] = {}
        self.messages: list[dict[str, Any]] = []
        self.before: str | None = None
        self._stack: list[set[str]] = []
        self._tags: list[str] = []
        self._message: dict[str, Any] | None = None
        self._message_depth = 0
        self._reply_depth: int | None = None
        # capture name -> (depth, parts)
        self._captures: dict[str, tuple[int, list[str]]] = {}
        self._counter_value: str | None = None
        self._skip_depth = 0

    @classmethod
    def parse(cls, html_text: str, base_url: str) -> _PreviewParser:
        parser = cls(base_url)
        try:
            parser.feed(html_text)
            parser.close()
        except (AssertionError, ValueError):
            pass
        parser._close_message()
        return parser

    def _capture(self, name: str) -> None:
        if name not in self._captures:
            self._captures[name] = (len(self._stack), [])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "br":
            for _depth, parts in self._captures.values():
                parts.append("\n")
            return
        message = self._message
        if "tgme_widget_message" in classes and values.get("data-post"):
            self._close_message()
            message = self._message = {
                "post": values["data-post"][:80],
                "links": [],
                "media": set(),
                "service": "service_message" in classes,
            }
            self._message_depth = len(self._stack)
        if message is not None:
            if "tgme_widget_message_reply" in classes and self._reply_depth is None:
                self._reply_depth = len(self._stack)
            in_reply = self._reply_depth is not None
            if "tgme_widget_message_text" in classes and not in_reply and "text" not in message:
                self._capture("text")
            if "tgme_widget_message_views" in classes:
                self._capture("views")
            if "tgme_widget_message_meta" in classes:
                self._capture("meta")
            if "tgme_widget_message_forwarded_from_name" in classes:
                self._capture("forwarded_from")
                href = values.get("href")
                if href:
                    message["forwarded_from_link"] = urljoin(self.base_url, href)[:2048]
            if "tgme_widget_message_date" in classes and values.get("href"):
                message["permalink"] = urljoin(self.base_url, values["href"])[:2048]
            if tag == "time" and values.get("datetime") and not in_reply:
                message.setdefault("datetime", values["datetime"][:64])
            for css, media in _MEDIA_CLASSES.items():
                if css in classes and not in_reply:
                    message["media"].add(media)
            if tag == "a" and "text" in self._captures and values.get("href"):
                absolute = urljoin(self.base_url, values["href"])
                if urlsplit(absolute).scheme in ("http", "https") and len(message["links"]) < 100:
                    message["links"].append(absolute[:2048])
        else:
            if "tgme_channel_info_header_title" in classes:
                self._capture("channel_title")
            elif "tgme_channel_info_header_username" in classes:
                self._capture("channel_username")
            elif "tgme_channel_info_description" in classes:
                self._capture("channel_description")
            elif "counter_value" in classes:
                self._capture("counter_value")
            elif "counter_type" in classes:
                self._capture("counter_type")
        if "tme_messages_more" in classes and values.get("data-before", "").isdigit():
            self.before = values["data-before"]
        if tag not in _VOID:
            self._stack.append(classes)
            self._tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._tags and self._tags[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag in _VOID or tag not in self._tags:
            return
        while self._tags:
            popped = self._tags.pop()
            self._stack.pop()
            self._close_to(len(self._stack))
            if popped == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        for _depth, parts in self._captures.values():
            parts.append(data)

    def _close_to(self, depth: int) -> None:
        for name in [n for n, (d, _p) in self._captures.items() if d >= depth]:
            _d, parts = self._captures.pop(name)
            self._store(name, "".join(parts))
        if self._reply_depth is not None and self._reply_depth >= depth:
            self._reply_depth = None
        if self._message is not None and self._message_depth >= depth:
            self._close_message()

    def _store(self, name: str, raw: str) -> None:
        value = "\n".join(" ".join(line.split()) for line in raw.split("\n")).strip()
        message = self._message
        if name == "text" and message is not None:
            message["text"] = value[:20_000]
        elif name == "views" and message is not None:
            message["views"] = value[:32] or None
        elif name == "meta" and message is not None:
            message["edited"] = "edited" in value.lower()
        elif name == "forwarded_from" and message is not None:
            message["forwarded_from"] = value[:300] or None
        elif name == "channel_title":
            self.channel["title"] = value[:300]
        elif name == "channel_username":
            self.channel["username"] = value[:64]
        elif name == "channel_description":
            self.channel["description"] = value[:5000]
        elif name == "counter_value":
            self._counter_value = value[:32]
        elif name == "counter_type" and self._counter_value is not None:
            self.counters[value[:32]] = self._counter_value
            self._counter_value = None

    def _close_message(self) -> None:
        if self._message is None:
            return
        for name in [n for n, (d, _p) in self._captures.items() if d > self._message_depth]:
            _d, parts = self._captures.pop(name)
            self._store(name, "".join(parts))
        self._message.setdefault("edited", False)
        self.messages.append(self._message)
        self._message = None
        self._reply_depth = None
