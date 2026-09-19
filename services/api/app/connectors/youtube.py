"""YouTube connector: public channel, video and comment metadata via the YouTube Data API v3.

Endpoints and quota costs were checked against developers.google.com/youtube/v3 on 2026-09-16:
``channels.list``, ``playlistItems.list``, ``videos.list`` and ``commentThreads.list`` cost 1 unit
each; projects get 10,000 units per day by default, reset at midnight Pacific Time. The API key is
sent in the ``X-Goog-Api-Key`` header, never in the URL, so it does not reach recorded provenance.

Transcripts are not collected: ``captions.download`` requires OAuth authorization by the video's
owner (and 200 units), and no other supported, authorized transcript source exists.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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

CONNECTOR_ID = "youtube.data_api"
PLATFORM = "youtube.com"
DAILY_DEFAULT_UNITS = 10_000
_HANDLE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PAGE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_NOT_FOUND = frozenset(
    {"videoNotFound", "channelNotFound", "playlistNotFound", "commentThreadNotFound"}
)
_PLACEHOLDER_TITLES = {"Private video": "private", "Deleted video": "deleted"}
_INPUTS = {
    "channel_uploads": ("username", "youtube_channel_id"),
    "video_comments": ("youtube_video_id",),
}
_REFERENCES = (
    "https://developers.google.com/youtube/v3/determine_quota_cost (checked 2026-09-16)",
    "https://developers.google.com/youtube/v3/docs/commentThreads/list (checked 2026-09-16)",
    "https://developers.google.com/youtube/v3/docs/core_errors (checked 2026-09-16)",
)

_EMPTY: dict[str, Any] = {
    "account_types": (),
    "content_types": (),
    "returned_fields": (),
    "unavailable_fields": (),
    "stable_identifiers": (),
    "pagination": "",
    "restrictions": "",
    "verification_status": None,
}

CAPABILITIES = (
    CapabilitySpec(
        name="channel_uploads",
        label="Official API: channel and uploaded videos",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.OFFICIAL_API,
        provider="YouTube Data API v3",
        account_types=("public YouTube channels",),
        content_types=("channel metadata and statistics", "uploaded videos (public listing)"),
        returned_fields=(
            "channel id, title, description, custom URL, country, published date",
            "subscriber, view and video counts (subscriber count may be hidden)",
            "per upload: video id, title, description, publish date, playlist position, "
            "privacy status",
        ),
        unavailable_fields=(
            "private and deleted videos' details (placeholders are flagged)",
            "members-only content",
            "watch history, subscribers list",
            "transcripts",
        ),
        stable_identifiers=("channel ID (UC…)", "video ID"),
        pagination=(
            "Uploads playlist pages of 50 via nextPageToken, up to the connector page limit; the "
            "channel's reported video count is recorded for coverage comparison."
        ),
        session_requirements="A YouTube Data API key (stored encrypted). No OAuth, no cookies.",
        restrictions="YouTube API Services Terms and Developer Policies apply.",
        cost_quota="1 quota unit per request; 10,000 units per day by default.",
        verification_status=VerificationStatus.LIVE_VERIFIED,
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_names=("api_key",),
        references=_REFERENCES,
    ),
    CapabilitySpec(
        name="video_comments",
        label="Official API: video and public comments",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.OFFICIAL_API,
        provider="YouTube Data API v3",
        account_types=("public videos",),
        content_types=("video metadata and statistics", "top-level public comments"),
        returned_fields=(
            "video id, title, description, channel id, publish date, privacy status, counts",
            "per comment: thread id, comment id, author display name and channel id, text, "
            "published and updated times, like count, reply count",
        ),
        unavailable_fields=(
            "replies beyond the reply count",
            "comments held for review or removed",
            "comments when the owner disabled them (reported as disabled)",
            "transcripts",
        ),
        stable_identifiers=("video ID", "comment thread ID", "comment ID", "author channel ID"),
        pagination="Comment pages of 100 (newest first) via nextPageToken, up to the page limit.",
        session_requirements="A YouTube Data API key (stored encrypted). No OAuth, no cookies.",
        restrictions=(
            "Author names are labels shown by YouTube, not verified identities. YouTube API "
            "Services Terms and Developer Policies apply."
        ),
        cost_quota="1 quota unit per request; 10,000 units per day by default.",
        verification_status=VerificationStatus.LIVE_VERIFIED,
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_names=("api_key",),
        references=_REFERENCES,
    ),
    CapabilitySpec(
        name="captions_download",
        label="Official API: caption tracks (transcripts)",
        status=CapabilityStatus.NOT_IMPLEMENTED,
        access_method=AccessMethod.OFFICIAL_API,
        provider="YouTube Data API v3 captions.download",
        session_requirements="OAuth 2.0 authorization with permission to edit the video.",
        cost_quota="captions.list 50 units; captions.download 200 units.",
        reason=(
            "Not implemented: downloading captions requires OAuth authorization by someone who "
            "can edit the video, which an investigator normally is not. Transcripts are never "
            "promised."
        ),
        **_EMPTY,
    ),
    CapabilitySpec(
        name="unofficial_transcripts",
        label="Unofficial transcript endpoints",
        status=CapabilityStatus.NOT_IMPLEMENTED,
        access_method=AccessMethod.PUBLIC_WEB_UNOFFICIAL,
        provider="Undocumented YouTube web endpoints or transcript libraries",
        session_requirements="None or browser cookies, depending on the tool.",
        cost_quota="",
        reason=(
            "Not implemented: no supported, authorized transcript source; undocumented endpoints "
            "would be unlabelled in origin and fragile."
        ),
        **_EMPTY,
    ),
)


class YouTubeDataApiConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="YouTube (Data API)",
        synthetic=False,
        description=(
            "Public YouTube channel, upload, video and comment metadata through the official "
            "YouTube Data API v3. YouTube sees the request; channel owners are not contacted."
        ),
        supported_input_types=("username", "youtube_channel_id", "youtube_video_id"),
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_requirements="A YouTube Data API key (Google Cloud project).",
        coverage=(
            "Channel uploads: channel metadata and the public uploads list. Video comments: video "
            "metadata and top-level public comments. Transcripts are not collected."
        ),
        max_pages=10,
        max_items_per_page=100,
        timeout_seconds=120,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
            base_backoff_seconds=2.0,
            max_backoff_seconds=60.0,
        ),
        output_schema="tracehollow.youtube.data_api/v1",
        cost_model="No charge; quota units (1 per request here).",
        quota_notes=(
            "Default 10,000 units per day per Google Cloud project, reset at midnight Pacific "
            "Time. Each page costs 1 unit; exhausted quota is reported as rate_limited with the "
            "time until reset."
        ),
        last_live_verification="2026-09-19",
        verification_status=VerificationStatus.LIVE_VERIFIED,
        parameters=(social.capability_parameter(CAPABILITIES, "channel_uploads"),),
        credentials=(
            CredentialSpec(
                name="api_key",
                label="API key",
                description=(
                    "Google Cloud API key with the YouTube Data API v3 enabled. Sent only in the "
                    "X-Goog-Api-Key header to the configured API address."
                ),
                required=False,
            ),
        ),
        cache_policy="No caching: every execution queries the API again.",
        max_concurrent_runs=2,
        min_request_interval_seconds=0.5,
        provider_terms="YouTube API Services Terms of Service and Developer Policies.",
        documentation="docs/connectors/youtube.md",
        capabilities=CAPABILITIES,
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        validate_parameters(self.descriptor, parameters)
        spec = selected_capability(self.descriptor, parameters)
        assert spec is not None
        if input_type not in _INPUTS[spec.name]:
            raise ValueError(
                f"the {spec.label} capability accepts {', '.join(_INPUTS[spec.name])} inputs"
            )
        value = input_value.strip()
        pattern = {"username": _HANDLE, "youtube_channel_id": _CHANNEL_ID}.get(
            input_type, _VIDEO_ID
        )
        if not pattern.match(value):
            raise ValueError(f"'{value[:40]}' is not a valid {input_type.replace('_', ' ')}")

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        spec = social.capability_for(self.descriptor, request)
        if request.input_type not in _INPUTS[spec.name]:
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                f"{spec.label} does not accept {request.input_type} inputs.",
                code="unsupported_input",
            )
        api_key = social.require_credentials(request, spec)["api_key"]
        if spec.name == "video_comments":
            if request.page_index == 0:
                return self._video(request, spec, api_key)
            return self._comments(request, spec, api_key)
        if request.page_index == 0:
            return self._channel(request, spec, api_key)
        return self._uploads(request, spec, api_key)

    # -- requests --------------------------------------------------------------------------------

    def _get(
        self, request: FetchRequest, api_key: str, resource: str, query: dict[str, str]
    ) -> tuple[dict[str, Any] | None, netguard.FetchResult, dict[str, Any]]:
        """Returns (body, result, quota); body is None for a documented not-found reason."""
        settings = request.context.settings
        base = str(
            settings.youtube_api_base_url
            if settings is not None
            else "https://www.googleapis.com/youtube/v3"
        ).rstrip("/")
        result = http.fetch(
            request,
            f"{base}/{resource}?{urlencode(query)}",
            headers={"x-goog-api-key": api_key, "accept": "application/json"},
            pacing_key="api",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=True,
            # Documented cost of a list request (developers.google.com/youtube/v3).
            provider_units=1,
        )
        quota = {
            "provider": "youtube_data_api_v3",
            "method": f"{resource}.list",
            "units_this_request": 1,
            "default_daily_units": DAILY_DEFAULT_UNITS,
            "cost": "none (quota units)",
        }
        body = social.json_body(result, "YouTube API")
        if not isinstance(body, dict):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The YouTube API response is not a JSON object.",
                code="unexpected_schema",
                quota=quota,
            )
        if "error" not in body and result.status_code < 400:
            request.context.credential_result("api_key", "accepted")
            return body, result, quota
        # Raises for failures; returns the reason for documented states (not found, disabled).
        reason = _raise_api_error(request, result, body, quota)
        return {"__reason": reason}, result, quota

    def _evidence(
        self, result: netguard.FetchResult, spec: CapabilitySpec, key: str, title: str
    ) -> EvidenceDraft:
        ok = 200 <= result.status_code < 300
        return EvidenceDraft(
            key=key,
            kind="json",
            content=result.content,
            content_type="application/json",
            title=title[:300],
            source_reference=result.final_url,
            collection_metadata={**result.metadata(), **social.provenance(spec)},
            access_category="credentialed",
            indexable=ok,
        )

    # -- channel uploads -------------------------------------------------------------------------

    def _channel(self, request: FetchRequest, spec: CapabilitySpec, api_key: str) -> ConnectorPage:
        value = request.input_value.strip()
        query = {"part": "snippet,contentDetails,statistics"}
        if request.input_type == "username":
            query["forHandle"] = value if value.startswith("@") else f"@{value}"
        else:
            query["id"] = value
        body, result, quota = self._get(request, api_key, "channels", query)
        evidence = self._evidence(result, spec, "channel", f"YouTube channel lookup: {value}")
        items = [item for item in (body or {}).get("items", []) or [] if isinstance(item, dict)]
        if not items:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                evidence=[evidence],
                quota=quota,
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                coverage={"capability": spec.name},
                notes=[
                    "The API returned no channel for this handle or ID at retrieval time. The "
                    "channel may not exist, may have been renamed, terminated or hidden."
                ],
            )
        channel = items[0]
        channel_id = social.text(channel.get("id"), 64)
        snippet = _dict(channel.get("snippet"))
        statistics = _dict(channel.get("statistics"))
        uploads = social.text(
            _dict(_dict(channel.get("contentDetails")).get("relatedPlaylists")).get("uploads"), 64
        )
        if channel_id is None:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The channel response lacks an ID.",
                code="unexpected_schema",
                quota=quota,
            )
        payload = {
            "channel_id": channel_id,
            "title": social.text(snippet.get("title"), 300),
            "description": social.text(snippet.get("description")),
            "custom_url": social.text(snippet.get("customUrl"), 100),
            "country": social.text(snippet.get("country"), 8),
            "published_at": social.text(snippet.get("publishedAt"), 40),
            "subscriber_count": social.integer(statistics.get("subscriberCount")),
            "subscriber_count_hidden": bool(statistics.get("hiddenSubscriberCount")),
            "view_count": social.integer(statistics.get("viewCount")),
            "video_count": social.integer(statistics.get("videoCount")),
            "uploads_playlist_id": uploads,
            "capability": spec.name,
        }
        identifiers = [IdentifierDraft("platform_id", channel_id, PLATFORM)]
        if payload["custom_url"]:
            identifiers.append(IdentifierDraft("username", str(payload["custom_url"]), PLATFORM))
        has_more = uploads is not None
        return ConnectorPage(
            page_index=0,
            has_more=has_more,
            next_cursor={"playlist_id": uploads, "page_token": None} if has_more else None,
            evidence=[evidence],
            entities=[
                EntityDraft(
                    key="channel",
                    entity_type="platform_account",
                    display_name=f"{payload['title'] or channel_id} on YouTube",
                    match=IdentifierDraft("platform_id", channel_id, PLATFORM),
                    identifiers=tuple(identifiers),
                    description="YouTube channel observed through the Data API.",
                    attributes={"platform": PLATFORM},
                )
            ],
            observations=[
                ObservationDraft(
                    observation_type="youtube_channel",
                    evidence_key="channel",
                    entity_key="channel",
                    source_object_id=channel_id,
                    payload=payload,
                    event_time=parse_date(social.text(snippet.get("publishedAt"), 40)),
                    idempotency_suffix="channel",
                )
            ],
            items=1,
            quota=quota,
            coverage={"capability": spec.name, "video_count_reported": payload["video_count"]},
        )

    def _uploads(self, request: FetchRequest, spec: CapabilitySpec, api_key: str) -> ConnectorPage:
        cursor = request.cursor or {}
        playlist = str(cursor.get("playlist_id") or "")
        token = cursor.get("page_token")
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", playlist) or (
            token is not None and not _PAGE_TOKEN.match(str(token))
        ):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The uploads pagination cursor is malformed.",
                code="unexpected_pagination",
            )
        query = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist,
            "maxResults": str(max(1, min(50, request.max_items_per_page))),
        }
        if token:
            query["pageToken"] = str(token)
        body, result, quota = self._get(request, api_key, "playlistItems", query)
        evidence = self._evidence(
            result, spec, "uploads", f"YouTube uploads page {request.page_index}"
        )
        if body is not None and body.get("__reason") in _NOT_FOUND:
            return ConnectorPage(
                page_index=request.page_index,
                has_more=False,
                evidence=[evidence],
                quota=quota,
                coverage={"uploads_status": "playlist_not_found"},
                notes=["The uploads playlist was not returned; the channel may have no uploads."],
            )
        items = [item for item in (body or {}).get("items", []) or [] if isinstance(item, dict)]
        observations = []
        for item in items:
            snippet = _dict(item.get("snippet"))
            details = _dict(item.get("contentDetails"))
            video_id = social.text(details.get("videoId"), 32)
            if video_id is None:
                continue
            title = social.text(snippet.get("title"), 300)
            observations.append(
                ObservationDraft(
                    observation_type="youtube_video",
                    evidence_key="uploads",
                    source_object_id=video_id,
                    payload={
                        "video_id": video_id,
                        "title": title,
                        "description": social.text(snippet.get("description")),
                        "channel_id": social.text(snippet.get("channelId"), 64),
                        "added_to_uploads_at": social.text(snippet.get("publishedAt"), 40),
                        "video_published_at": social.text(details.get("videoPublishedAt"), 40),
                        "position": social.integer(snippet.get("position")),
                        "privacy_status": social.text(
                            _dict(item.get("status")).get("privacyStatus"), 32
                        ),
                        "placeholder": _PLACEHOLDER_TITLES.get(title or ""),
                        "capability": spec.name,
                    },
                    event_time=parse_date(social.text(details.get("videoPublishedAt"), 40)),
                    source_published_at=parse_date(
                        social.text(details.get("videoPublishedAt"), 40)
                    ),
                    idempotency_suffix=f"video:{video_id}",
                    dedupe_across_pages=True,
                )
            )
        next_token = social.text((body or {}).get("nextPageToken"), 512)
        return ConnectorPage(
            page_index=request.page_index,
            has_more=next_token is not None,
            next_cursor={"playlist_id": playlist, "page_token": next_token} if next_token else None,
            evidence=[evidence],
            observations=observations,
            items=len(observations),
            quota=quota,
            coverage={
                "total_results_reported": social.integer(
                    _dict((body or {}).get("pageInfo")).get("totalResults")
                )
            },
        )

    # -- video comments --------------------------------------------------------------------------

    def _video(self, request: FetchRequest, spec: CapabilitySpec, api_key: str) -> ConnectorPage:
        video_id = request.input_value.strip()
        body, result, quota = self._get(
            request, api_key, "videos", {"part": "snippet,statistics,status", "id": video_id}
        )
        evidence = self._evidence(result, spec, "video", f"YouTube video: {video_id}")
        items = [item for item in (body or {}).get("items", []) or [] if isinstance(item, dict)]
        if not items:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                evidence=[evidence],
                quota=quota,
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                coverage={"capability": spec.name},
                notes=[
                    "The API returned no video for this ID at retrieval time. It may not exist, "
                    "or be private or removed."
                ],
            )
        video = items[0]
        snippet = _dict(video.get("snippet"))
        statistics = _dict(video.get("statistics"))
        comment_count = social.integer(statistics.get("commentCount"))
        payload = {
            "video_id": video_id,
            "title": social.text(snippet.get("title"), 300),
            "description": social.text(snippet.get("description")),
            "channel_id": social.text(snippet.get("channelId"), 64),
            "channel_title": social.text(snippet.get("channelTitle"), 300),
            "published_at": social.text(snippet.get("publishedAt"), 40),
            "live_broadcast_content": social.text(snippet.get("liveBroadcastContent"), 16),
            "privacy_status": social.text(_dict(video.get("status")).get("privacyStatus"), 32),
            "view_count": social.integer(statistics.get("viewCount")),
            "like_count": social.integer(statistics.get("likeCount")),
            "comment_count": comment_count,
            "comment_count_absent": "commentCount" not in statistics,
            "capability": spec.name,
        }
        has_more = comment_count != 0
        return ConnectorPage(
            page_index=0,
            has_more=has_more,
            next_cursor={"page_token": None} if has_more else None,
            evidence=[evidence],
            observations=[
                ObservationDraft(
                    observation_type="youtube_video",
                    evidence_key="video",
                    source_object_id=video_id,
                    payload=payload,
                    event_time=parse_date(social.text(snippet.get("publishedAt"), 40)),
                    source_published_at=parse_date(social.text(snippet.get("publishedAt"), 40)),
                    idempotency_suffix=f"video:{video_id}",
                )
            ],
            items=1,
            quota=quota,
            coverage={"capability": spec.name, "comment_count_reported": comment_count},
            notes=["The video reports no comments."] if comment_count == 0 else [],
        )

    def _comments(self, request: FetchRequest, spec: CapabilitySpec, api_key: str) -> ConnectorPage:
        video_id = request.input_value.strip()
        token = (request.cursor or {}).get("page_token")
        if token is not None and not _PAGE_TOKEN.match(str(token)):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The comment pagination token is malformed.",
                code="unexpected_pagination",
            )
        query = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": str(max(1, min(100, request.max_items_per_page))),
            "order": "time",
            "textFormat": "plainText",
        }
        if token:
            query["pageToken"] = str(token)
        body, result, quota = self._get(request, api_key, "commentThreads", query)
        evidence = self._evidence(
            result, spec, "comments", f"YouTube comments on {video_id} (page {request.page_index})"
        )
        reason = (body or {}).get("__reason")
        if reason == "commentsDisabled":
            return ConnectorPage(
                page_index=request.page_index,
                has_more=False,
                evidence=[evidence],
                quota=quota,
                coverage={"comments_status": "disabled"},
                notes=[
                    "Comments are disabled on this video; none can be collected. This is not the "
                    "same as a video without comments."
                ],
            )
        if reason in _NOT_FOUND:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The video was no longer available when its comments were requested.",
                code="youtube_video_not_found",
                quota=quota,
            )
        observations = []
        for thread in (body or {}).get("items", []) or []:
            if not isinstance(thread, dict):
                continue
            thread_snippet = _dict(thread.get("snippet"))
            top = _dict(thread_snippet.get("topLevelComment"))
            comment = _dict(top.get("snippet"))
            thread_id = social.text(thread.get("id"), 128)
            if thread_id is None:
                continue
            published = social.text(comment.get("publishedAt"), 40)
            updated = social.text(comment.get("updatedAt"), 40)
            observations.append(
                ObservationDraft(
                    observation_type="youtube_comment",
                    evidence_key="comments",
                    source_object_id=thread_id,
                    payload={
                        "thread_id": thread_id,
                        "comment_id": social.text(top.get("id"), 128),
                        "video_id": video_id,
                        "author_display_name": social.text(comment.get("authorDisplayName"), 300),
                        "author_channel_id": social.text(
                            _dict(comment.get("authorChannelId")).get("value"), 64
                        ),
                        "author_label_note": (
                            "Display name shown by YouTube; not a verified identity."
                        ),
                        "text": social.text(
                            comment.get("textOriginal") or comment.get("textDisplay"), 10_000
                        ),
                        "published_at": published,
                        "updated_at": updated,
                        "edited": bool(published and updated and published != updated),
                        "like_count": social.integer(comment.get("likeCount")),
                        "total_reply_count": social.integer(thread_snippet.get("totalReplyCount")),
                        "replies_collected": 0,
                        "capability": spec.name,
                    },
                    event_time=parse_date(published),
                    source_published_at=parse_date(published),
                    idempotency_suffix=f"comment:{thread_id}",
                    dedupe_across_pages=True,
                )
            )
        next_token = social.text((body or {}).get("nextPageToken"), 512)
        return ConnectorPage(
            page_index=request.page_index,
            has_more=next_token is not None,
            next_cursor={"page_token": next_token} if next_token else None,
            evidence=[evidence],
            observations=observations,
            items=len(observations),
            quota=quota,
            coverage={"comments_status": "enabled", "threads_on_page": len(observations)},
        )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def seconds_until_quota_reset(now: datetime | None = None) -> float:
    """YouTube daily quotas reset at midnight Pacific Time."""
    pacific = ZoneInfo("America/Los_Angeles")
    current = (now or datetime.now(UTC)).astimezone(pacific)
    midnight = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), pacific)
    return max(60.0, (midnight - current).total_seconds())


def _raise_api_error(
    request: FetchRequest,
    result: netguard.FetchResult,
    body: dict[str, Any],
    quota: dict[str, Any],
) -> str:
    """Raise the outcome for an API error; return the reason when it is a documented state."""
    error = _dict(body.get("error"))
    reasons = [
        str(item.get("reason"))
        for item in error.get("errors", []) or []
        if isinstance(item, dict) and item.get("reason")
    ]
    reason = reasons[0] if reasons else ""
    message = social.text(error.get("message"), 300) or ""
    code = social.integer(error.get("code")) or result.status_code
    if reason in _NOT_FOUND or reason == "commentsDisabled":
        return reason
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        raise ConnectorError(
            ConnectorOutcome.RATE_LIMITED,
            "The YouTube Data API daily quota is exhausted; it resets at midnight Pacific Time.",
            retry_after_seconds=round(seconds_until_quota_reset(), 1),
            code="youtube_quota_exceeded",
            quota={**quota, "exhausted": True},
        )
    if reason in ("rateLimitExceeded", "userRateLimitExceeded") or code == 429:
        raise ConnectorError(
            ConnectorOutcome.RATE_LIMITED,
            f"YouTube rate limit reached: {message}",
            retry_after_seconds=http.retry_after_seconds(result.headers.get("retry-after")) or 60.0,
            code="youtube_rate_limited",
            quota=quota,
        )
    if (
        reason in ("keyInvalid", "keyExpired", "unauthorized", "authError")
        or code == 401
        or "api key not valid" in message.lower()
        or "api key expired" in message.lower()
    ):
        request.context.credential_result("api_key", "rejected")
        raise ConnectorError(
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            f"YouTube rejected the API key: {message}. Replace it on the Sources page.",
            code="youtube_key_rejected",
            quota=quota,
        )
    if reason in ("accessNotConfigured", "forbidden", "ipRefererBlocked") or code == 403:
        raise ConnectorError(
            ConnectorOutcome.ACCESS_DENIED,
            f"YouTube refused access ({reason or code}): {message}",
            code=f"youtube_{reason or 'forbidden'}",
            quota=quota,
        )
    if reason == "processingFailure" or code >= 500:
        raise ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            f"YouTube could not process the request ({reason or code}).",
            code=f"youtube_{reason or code}",
            quota=quota,
        )
    raise ConnectorError(
        ConnectorOutcome.UNSUPPORTED,
        f"YouTube rejected the request ({reason or code}): {message}",
        code=f"youtube_{reason or code}",
        quota=quota,
    )
