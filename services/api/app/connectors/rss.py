"""RSS/Atom feed connector: entries with IDs, dates and links; paginated feeds (RFC 5005)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from app.connectors import http
from app.connectors.base import (
    CollectionMode,
    ConnectorDescriptor,
    ConnectorError,
    ConnectorPage,
    EntityDraft,
    EvidenceDraft,
    FetchRequest,
    IdentifierDraft,
    ObservationDraft,
    RetryPolicy,
    VerificationStatus,
    validate_parameters,
)
from app.connectors.feeds import FeedParseError, parse_feed
from app.connectors.web import validate_http_url
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "rss.feed"
_FEED_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/rdf+xml",
        "application/xml",
        "text/xml",
        "application/x-rss+xml",
    }
)


class RssFeedConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="RSS / Atom feed",
        synthetic=False,
        description=(
            "Reads a public RSS or Atom feed: entry IDs, titles, links, publication dates and "
            "summaries, following the feed's own next-page links. The feed's server sees the "
            "requests."
        ),
        supported_input_types=("url",),
        collection_mode=CollectionMode.DIRECT_REQUEST,
        credential_requirements="none",
        coverage=(
            "Only the entries the feed publishes (usually the most recent ones). Older entries "
            "are included only when the feed links to further pages (RFC 5005 'next'); those "
            "links must stay on the feed's origin. Entry pages themselves are not retrieved."
        ),
        max_pages=10,
        max_items_per_page=100,
        timeout_seconds=120,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
            base_backoff_seconds=2.0,
            max_backoff_seconds=20.0,
        ),
        output_schema="tracehollow.feed.entries/v1",
        cost_model="Free (direct request).",
        # Authorized live smoke check: docs/connectors/live-smoke/2026-09-15-results.json
        last_live_verification="2026-09-15",
        verification_status=VerificationStatus.LIVE_VERIFIED,
        cache_policy=(
            "No caching: every execution retrieves the feed again. Entries repeated across "
            "pages of one execution are stored once; each execution keeps its own record."
        ),
        max_concurrent_runs=4,
        min_request_interval_seconds=2.0,
        provider_terms="Respect the publisher's terms of use and applicable law.",
        documentation="docs/connectors/rss-atom-feed.md",
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        validate_http_url(input_value)
        validate_parameters(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "url":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only URL inputs are supported.")
        feed_url = request.input_value.strip()
        url = str(request.cursor["url"]) if request.cursor else feed_url
        origin = urlsplit(feed_url)
        target = urlsplit(url)
        if (target.scheme, target.netloc.lower()) != (origin.scheme, origin.netloc.lower()):
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                "The feed's next-page link leaves the feed's origin and was not followed.",
                code="cross_origin_pagination",
            )
        result = http.fetch(
            request,
            url,
            headers={
                "accept": (
                    "application/rss+xml,application/atom+xml,application/xml;q=0.9,"
                    "text/xml;q=0.9,*/*;q=0.1"
                )
            },
            pacing_key=f"host:{(origin.hostname or '').lower()}",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=request.page_index > 0,
        )
        if result.status_code in (404, 410) and request.page_index == 0:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                coverage={"http_status": result.status_code},
                notes=[
                    f"The server answered HTTP {result.status_code}: no feed at this URL at "
                    "retrieval time."
                ],
                evidence=[
                    EvidenceDraft(
                        key="snapshot",
                        kind="text",
                        content=result.content,
                        content_type=result.headers.get("content-type", "text/plain")[:100],
                        title=f"Feed response HTTP {result.status_code}: {url}"[:300],
                        source_reference=result.final_url,
                        collection_metadata=result.metadata(),
                        indexable=False,
                    )
                ],
            )
        http.raise_for_status(result, "The feed server")
        if result.truncated:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                f"The feed is larger than {len(result.content)} bytes; a truncated feed cannot "
                "be parsed reliably.",
                code="response_too_large",
            )
        if result.content_type and result.content_type not in _FEED_TYPES:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                f"Expected a feed but received content type '{result.content_type}'.",
                code="not_a_feed",
            )
        try:
            feed = parse_feed(result.content, result.final_url)
        except FeedParseError as exc:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR, str(exc), code="invalid_feed"
            ) from None

        entries = feed.entries[: request.max_items_per_page]
        omitted = len(feed.entries) - len(entries)
        normalized = {
            "schema": self.descriptor.output_schema,
            "feed": {
                "format": feed.format,
                "title": feed.title,
                "site_link": feed.site_link,
                "feed_id": feed.feed_id,
                "updated": feed.updated.isoformat() if feed.updated else None,
                "url": result.final_url,
            },
            "page": {"index": request.page_index, "next_url": feed.next_url},
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "id_source": entry.id_source,
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.published.isoformat() if entry.published else None,
                    "published_original": entry.published_original,
                    "updated": entry.updated.isoformat() if entry.updated else None,
                    "author": entry.author,
                    "categories": entry.categories,
                    "summary": entry.summary,
                }
                for entry in entries
            ],
        }
        label = feed.title or (origin.hostname or url)
        page_label = f" page {request.page_index + 1}" if request.page_index else ""
        evidence = [
            EvidenceDraft(
                key="snapshot",
                kind="xml",
                content=result.content,
                content_type=result.headers.get("content-type", "application/xml")[:100],
                title=f"Feed snapshot{page_label}: {label}"[:300],
                source_reference=result.final_url,
                collection_metadata=result.metadata(),
                description="Byte-exact feed document as retrieved.",
            ),
            EvidenceDraft(
                key="entries",
                kind="json",
                content=json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
                title=f"Feed entries{page_label}: {label}"[:300],
                source_reference=result.final_url,
                collection_metadata={"parser": "tracehollow.feeds/1", "format": feed.format},
                derived_from="snapshot",
                description="Entries parsed from the feed snapshot (derived evidence).",
            ),
        ]
        entities: list[EntityDraft] = []
        observations: list[ObservationDraft] = []
        if request.page_index == 0:
            entities.append(
                EntityDraft(
                    key="feed",
                    entity_type="url",
                    display_name=feed_url[:300],
                    match=IdentifierDraft("url", feed_url),
                    description="Collected RSS/Atom feed.",
                )
            )
            observations.append(
                ObservationDraft(
                    observation_type="feed",
                    evidence_key="entries",
                    entity_key="feed",
                    source_object_id=feed.feed_id or result.final_url,
                    payload={
                        "format": feed.format,
                        "title": feed.title,
                        "site_link": feed.site_link,
                        "updated": feed.updated.isoformat() if feed.updated else None,
                        "entries_on_first_page": len(feed.entries),
                    },
                    event_time=feed.updated,
                    idempotency_suffix="feed",
                )
            )
        for entry in entries:
            observations.append(
                ObservationDraft(
                    observation_type="feed_entry",
                    evidence_key="entries",
                    source_object_id=entry.entry_id,
                    payload={
                        "entry_id": entry.entry_id,
                        "id_source": entry.id_source,
                        "title": entry.title,
                        "link": entry.link,
                        "published_original": entry.published_original,
                        "author": entry.author,
                        "feed_url": feed_url,
                    },
                    source_published_at=entry.published,
                    event_time=entry.updated,
                    idempotency_suffix="entry:"
                    + hashlib.sha256(entry.entry_id.encode()).hexdigest()[:40],
                    dedupe_across_pages=True,
                )
            )
        next_url = feed.next_url if feed.next_url and feed.next_url != url else None
        incomplete = (
            f"{omitted} entr{'y' if omitted == 1 else 'ies'} on page {request.page_index + 1} "
            f"exceeded the limit of {request.max_items_per_page} per page and were not stored."
            if omitted
            else None
        )
        return ConnectorPage(
            page_index=request.page_index,
            has_more=next_url is not None,
            next_cursor={"url": next_url} if next_url else None,
            evidence=evidence,
            entities=entities,
            observations=observations,
            items=len(entries),
            coverage={"feed_format": feed.format},
            incomplete_reason=incomplete,
        )
