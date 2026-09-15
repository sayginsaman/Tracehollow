"""Public web page connector: one bounded, SSRF-protected retrieval of a user-supplied URL."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.connectors import http, netguard
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
    RelationshipDraft,
    RetryPolicy,
    VerificationStatus,
    validate_parameters,
)
from app.connectors.feeds import parse_date
from app.connectors.htmltext import extract, sniff_charset
from app.entities import normalize
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "public_web.page"
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_TYPES = frozenset({"text/plain"})
MAX_URL_LENGTH = 2048


def validate_http_url(value: str) -> None:
    if len(value) > MAX_URL_LENGTH:
        raise ValueError(f"the URL must be at most {MAX_URL_LENGTH} characters")
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("the input must be an absolute http or https URL")
    if parts.username or parts.password:
        raise ValueError("URLs with embedded credentials are not accepted")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("the URL port is invalid") from exc


class PublicWebPageConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Public web page",
        synthetic=False,
        description=(
            "Retrieves one public web page, keeps the byte-exact response as evidence and stores "
            "its readable text separately. The page's server sees the request."
        ),
        supported_input_types=("url",),
        collection_mode=CollectionMode.DIRECT_REQUEST,
        credential_requirements="none",
        coverage=(
            "The single URL given (after at most 5 redirects). HTML is not rendered: content "
            "added by JavaScript, pages behind logins or bot challenges, and non-text formats "
            "(PDF, images) are not captured."
        ),
        max_pages=1,
        max_items_per_page=1,
        timeout_seconds=60,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
            base_backoff_seconds=2.0,
            max_backoff_seconds=20.0,
        ),
        output_schema="tracehollow.web.page/v1",
        cost_model="Free (direct request).",
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        max_concurrent_runs=4,
        min_request_interval_seconds=2.0,
        provider_terms="Respect the site's terms of use and applicable law.",
        documentation="docs/connectors/public-web-page.md",
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        validate_http_url(input_value)
        validate_parameters(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "url":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only URL inputs are supported.")
        url = request.input_value.strip()
        host = (urlsplit(url).hostname or "").lower()
        result = http.fetch(
            request,
            url,
            headers={
                "accept": ("text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.1")
            },
            pacing_key=f"host:{host}",
            interval_seconds=self.descriptor.min_request_interval_seconds,
        )
        content_type = result.content_type
        if result.status_code in (404, 410):
            return self._not_found(request, result)
        http.raise_for_status(result, "The web server")
        if content_type not in _HTML_TYPES | _TEXT_TYPES:
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                f"The page has content type '{content_type or 'unknown'}', which this connector "
                "does not store (text and HTML only).",
                code="unsupported_content_type",
            )

        metadata = result.metadata()
        final_host = (urlsplit(result.final_url).hostname or host).lower()
        if content_type in _HTML_TYPES:
            decoded, encoding = http.decode_text(result, sniff_charset(result.content))
            page = extract(decoded, result.final_url)
            text = "\n".join(filter(None, [page.title, page.text]))
            raw_kind = "html"
        else:
            decoded, encoding = http.decode_text(result)
            page = extract("", result.final_url)
            text = decoded
            raw_kind = "text"
        metadata["decoded_with"] = encoding

        label = page.title or final_host
        published = parse_date(page.published)
        evidence = [
            EvidenceDraft(
                key="snapshot",
                kind=raw_kind,  # type: ignore[arg-type]
                content=result.content,
                content_type=result.headers.get("content-type", content_type)[:100],
                title=f"Web page snapshot: {label}",
                source_reference=result.final_url,
                collection_metadata=metadata,
                source_published_at=published,
                source_published_at_original=page.published,
                description=("Byte-exact response body as retrieved. Rendered only as inert text."),
            )
        ]
        needs_text_record = raw_kind == "html" or encoding.replace("-", "") != "utf8"
        text_key = "snapshot"
        if needs_text_record:
            text_key = "text"
            evidence.append(
                EvidenceDraft(
                    key="text",
                    kind="text",
                    content=text.encode("utf-8"),
                    content_type="text/plain; charset=utf-8",
                    title=f"Web page text: {label}",
                    source_reference=result.final_url,
                    collection_metadata={
                        "extraction": "tracehollow.htmltext/1" if raw_kind == "html" else "decode",
                        "decoded_with": encoding,
                    },
                    source_published_at=published,
                    source_published_at_original=page.published,
                    derived_from="snapshot",
                    description="Readable text extracted from the snapshot (derived evidence).",
                )
            )

        entities = [self._url_entity(result.final_url)]
        relationships: list[RelationshipDraft] = []
        domain = _domain_or_none(final_host)
        if domain is not None:
            entities.append(
                EntityDraft(
                    key="domain",
                    entity_type="domain",
                    display_name=domain,
                    match=IdentifierDraft("domain", domain),
                    description="Domain of a collected web page.",
                )
            )
            relationships.append(
                RelationshipDraft(
                    source_key="url",
                    target_key="domain",
                    predicate="hosted_on",
                    origin="deterministic_derivation",
                    description="The page's URL is on this domain.",
                    observation_index=0,
                )
            )
        observation = ObservationDraft(
            observation_type="web_page",
            evidence_key=text_key,
            entity_key="url",
            source_object_id=result.final_url,
            payload={
                "requested_url": url,
                "final_url": result.final_url,
                "http_status": result.status_code,
                "redirects": len(result.redirects),
                "title": page.title,
                "description": page.description,
                "language": page.language,
                "canonical_url": page.canonical_url,
                "published": page.published,
                "content_type": content_type,
                "bytes": len(result.content),
                "truncated": result.truncated,
                "text_characters": len(text),
                "links": page.links[:50],
                "links_total": len(page.links),
            },
            source_published_at=published,
        )
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=evidence,
            entities=entities,
            observations=[observation],
            relationships=relationships,
            items=1,
            coverage={"http_status": result.status_code, "bytes": len(result.content)},
            incomplete_reason=(
                f"The response exceeded {len(result.content)} bytes and was truncated."
                if result.truncated
                else None
            ),
        )

    def _url_entity(self, url: str) -> EntityDraft:
        return EntityDraft(
            key="url",
            entity_type="url",
            display_name=url[:300],
            match=IdentifierDraft("url", url),
            description="Collected web page.",
        )

    def _not_found(self, request: FetchRequest, result: netguard.FetchResult) -> ConnectorPage:
        """The server answered that the page does not exist. Keep the response as evidence."""
        metadata = result.metadata()
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=[
                EvidenceDraft(
                    key="snapshot",
                    kind="html" if result.content_type in _HTML_TYPES else "text",
                    content=result.content,
                    content_type=result.headers.get("content-type", "text/plain")[:100],
                    indexable=False,
                    title=f"Web page response HTTP {result.status_code}: {result.final_url}"[:300],
                    source_reference=result.final_url,
                    collection_metadata=metadata,
                    description="Error response body as retrieved.",
                )
            ],
            items=0,
            coverage={"http_status": result.status_code},
            outcome_hint=ConnectorOutcome.NO_FINDINGS,
            notes=[
                f"The server answered HTTP {result.status_code} for this URL at retrieval time. "
                "This does not show that the content never existed or is unavailable elsewhere."
            ],
        )


def _domain_or_none(host: str) -> str | None:
    try:
        return normalize.normalize_domain(host)
    except normalize.IdentifierError:
        return None
