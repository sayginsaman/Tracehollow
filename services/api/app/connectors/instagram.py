"""Instagram connector: capability-dependent access to professional and public profile data.

Capabilities (see ``CAPABILITIES`` and docs/connectors/instagram.md):

``official_business_discovery``
    Meta's Instagram Graph API Business Discovery (API ``v25.0``): public fields and feed media of
    *professional* (Business or Creator) accounts, requested through a professional account the
    operator administers. Personal and age-gated accounts are not returned by this API. Checked
    against developers.facebook.com (IG User Business Discovery, IG User and IG Media references)
    on 2026-09-16.
``public_profile_page``
    One unauthenticated request for the public profile page on instagram.com, reading only the
    page's metadata tags. Undocumented and unofficial; Instagram often answers with a login wall,
    which is reported as such. Disabled unless an administrator enables it.

Personal-account, private-profile, story, follower-list and message access are not offered.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlsplit

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
    ParameterSpec,
    RetryPolicy,
    VerificationStatus,
    parameter_value,
    selected_capability,
    validate_parameters,
)
from app.connectors.feeds import parse_date
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "instagram.account"
GRAPH_API_VERSION = "v25.0"
PLATFORM = "instagram.com"
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_ACCOUNT_FIELDS = ("id", "username", "biography", "website", "followers_count", "media_count")
_MEDIA_FIELDS = (
    "id", "caption", "media_type", "media_product_type", "permalink", "shortcode", "timestamp",
    "like_count", "comments_count",
)  # fmt: skip
_THROTTLING_CODES = frozenset({4, 17, 32, 613, 80002})
_TRANSIENT_CODES = frozenset({1, 2})

CAPABILITIES = (
    CapabilitySpec(
        name="official_business_discovery",
        label="Official API: professional account discovery",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.OFFICIAL_API,
        provider=f"Meta Instagram Graph API {GRAPH_API_VERSION} (Business Discovery)",
        account_types=("Instagram Business accounts", "Instagram Creator accounts"),
        content_types=("public profile fields", "feed media metadata and captions"),
        returned_fields=(*_ACCOUNT_FIELDS, *(f"media.{name}" for name in _MEDIA_FIELDS)),
        unavailable_fields=(
            "personal (non-professional) accounts",
            "age-gated professional accounts",
            "private content, stories, follower and following lists, direct messages",
            "profile name, profile picture and following count (not public fields)",
            "like counts the owner has hidden",
            "comment text",
        ),
        stable_identifiers=("Instagram user ID (id)", "media ID", "media shortcode"),
        pagination=(
            "Media edge cursors (after) are followed page by page up to the connector page "
            "limit; the account page reports the media count so coverage can be compared."
        ),
        session_requirements=(
            "A Facebook User access token with instagram_basic, instagram_manage_insights and "
            "pages_read_engagement, and the Instagram user ID of a professional account the "
            "operator administers. Both are stored encrypted on the server. No cookies."
        ),
        restrictions=(
            "Only professional accounts that are not age-gated are returned. Meta Platform Terms "
            "and App Review requirements apply. A username the API does not return may still "
            "exist as a personal, restricted or age-gated account."
        ),
        cost_quota=(
            "No per-request charge; Graph API rate limits apply and the usage headers "
            "(X-App-Usage, X-Business-Use-Case-Usage) are recorded on each run."
        ),
        verification_status=VerificationStatus.FIXTURE_TESTED,
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_names=("access_token", "ig_user_id"),
        references=(
            "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/"
            "reference/ig-user/business_discovery (checked 2026-09-16)",
            "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/"
            "reference/ig-media (checked 2026-09-16)",
            "https://developers.facebook.com/docs/graph-api/guides/error-handling "
            "(checked 2026-09-16)",
        ),
    ),
    CapabilitySpec(
        name="public_profile_page",
        label="Unofficial: public profile page metadata",
        status=CapabilityStatus.IMPLEMENTED,
        access_method=AccessMethod.PUBLIC_WEB_UNOFFICIAL,
        provider="instagram.com profile page (undocumented web page)",
        account_types=("accounts whose profile page Instagram serves without login",),
        content_types=("profile summary shown in the page's metadata tags",),
        returned_fields=(
            "og:title",
            "og:description (displayed follower, following and post counts)",
            "og:url",
            "og:image address",
        ),
        unavailable_fields=(
            "posts, comments and media",
            "stable numeric account ID",
            "anything behind Instagram's login wall",
            "private profiles' content",
        ),
        stable_identifiers=("none: the username in the page address can change",),
        pagination="None: one request per run.",
        session_requirements="None. No login, no cookies, no session reuse.",
        restrictions=(
            "Undocumented and fragile. Instagram frequently shows a login wall to unauthenticated "
            "requests; that is reported as a login wall, never as no findings. Instagram's Terms "
            "of Use restrict automated collection: the capability is disabled unless an "
            "administrator sets TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true."
        ),
        cost_quota="No charge; Instagram may throttle or block repeated requests.",
        verification_status=VerificationStatus.FIXTURE_TESTED,
        collection_mode=CollectionMode.PLATFORM_PROBE,
    ),
    CapabilitySpec(
        name="instaloader_session",
        label="Unofficial client with a logged-in session",
        status=CapabilityStatus.NOT_IMPLEMENTED,
        access_method=AccessMethod.UNOFFICIAL_CLIENT,
        provider="Instaloader or similar private-API clients",
        account_types=(),
        content_types=(),
        returned_fields=(),
        unavailable_fields=(),
        stable_identifiers=(),
        pagination="",
        session_requirements="A logged-in Instagram session or reused browser cookies.",
        restrictions="",
        cost_quota="",
        verification_status=None,
        reason=(
            "Not implemented: it needs a logged-in session or cookie reuse (no automatic cookie "
            "extraction is allowed), uses its own HTTP stack outside the connector network "
            "policy, and risks account suspension."
        ),
    ),
    CapabilitySpec(
        name="third_party_provider",
        label="Third-party data provider",
        status=CapabilityStatus.NOT_IMPLEMENTED,
        access_method=AccessMethod.THIRD_PARTY_PROVIDER,
        provider="Commercial Instagram data APIs",
        account_types=(),
        content_types=(),
        returned_fields=(),
        unavailable_fields=(),
        stable_identifiers=(),
        pagination="",
        session_requirements="Provider account and API key.",
        restrictions="",
        cost_quota="Usually paid per request.",
        verification_status=None,
        reason=(
            "Not implemented: no provider has been selected, and a provider would introduce paid "
            "requests and a separate data licence that must be reviewed first."
        ),
    ),
    CapabilitySpec(
        name="private_or_personal_account_access",
        label="Private profiles and unrestricted personal accounts",
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
            "Excluded: private-profile content, direct messages and unrestricted personal-account "
            "collection are outside Tracehollow's access rules and are never offered."
        ),
    ),
)


class InstagramAccountConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="Instagram account",
        synthetic=False,
        description=(
            "Capability-dependent Instagram lookups: the official Business Discovery API for "
            "professional accounts, or (if enabled) the public profile page's metadata. It does "
            "not access private profiles, personal accounts in full, stories or messages."
        ),
        supported_input_types=("username",),
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_requirements=(
            "Official API: a Facebook User access token and the Instagram user ID of a "
            "professional account you administer. Public profile page: none."
        ),
        coverage=(
            "Official API: public fields and feed media of Business and Creator accounts that "
            "are not age-gated. Public profile page: the summary Instagram puts in the page "
            "metadata when it serves the page without login. Nothing else."
        ),
        max_pages=10,
        max_items_per_page=50,
        timeout_seconds=120,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_outcomes=(ConnectorOutcome.UNAVAILABLE, ConnectorOutcome.RATE_LIMITED),
            base_backoff_seconds=5.0,
            max_backoff_seconds=120.0,
        ),
        output_schema="tracehollow.instagram.account/v1",
        cost_model="Official API: no per-request charge, rate limited. Profile page: none.",
        quota_notes=(
            "Graph API usage headers are recorded; one request for the account page and one per "
            "further media page. The profile page capability makes exactly one request."
        ),
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        parameters=(
            social.capability_parameter(CAPABILITIES, "official_business_discovery"),
            ParameterSpec(
                name="include_media",
                kind="boolean",
                label="Include media",
                description="Official API only: also list the account's feed media.",
                default=True,
            ),
        ),
        credentials=(
            CredentialSpec(
                name="access_token",
                label="Facebook User access token",
                description=(
                    "Token with instagram_basic, instagram_manage_insights and "
                    "pages_read_engagement. Sent only in the Authorization header to the "
                    "configured Graph API address."
                ),
                required=False,
            ),
            CredentialSpec(
                name="ig_user_id",
                label="Your professional Instagram user ID",
                description="The Instagram user ID that performs Business Discovery requests.",
                required=False,
            ),
        ),
        cache_policy="No caching: every execution queries the source again.",
        max_concurrent_runs=1,
        min_request_interval_seconds=2.0,
        provider_terms=(
            "Meta Platform Terms and Instagram Terms of Use. Collect only what the investigation "
            "is authorized to collect; do not use results to harass or profile individuals."
        ),
        documentation="docs/connectors/instagram.md",
        capabilities=CAPABILITIES,
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        if not _USERNAME.match(input_value.strip().removeprefix("@")):
            raise ValueError(
                "an Instagram username has 1-30 letters, digits, periods or underscores"
            )
        validate_parameters(self.descriptor, parameters)
        selected_capability(self.descriptor, parameters)

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "username":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only usernames are supported.")
        spec = social.capability_for(self.descriptor, request)
        if spec.name == "public_profile_page":
            return self._profile_page(request, spec)
        return self._business_discovery(request, spec)

    # -- official Business Discovery ------------------------------------------------------------

    def _graph_base(self, request: FetchRequest) -> str:
        settings = request.context.settings
        base = (
            settings.instagram_graph_api_base_url
            if settings is not None
            else "https://graph.facebook.com"
        )
        return f"{str(base).rstrip('/')}/{GRAPH_API_VERSION}"

    def _business_discovery(self, request: FetchRequest, spec: CapabilitySpec) -> ConnectorPage:
        credentials = social.require_credentials(request, spec)
        ig_user_id = credentials["ig_user_id"]
        if not ig_user_id.isdigit():
            raise ConnectorError(
                ConnectorOutcome.AUTHENTICATION_REQUIRED,
                "The configured Instagram user ID must be numeric.",
                code="credential_invalid",
            )
        username = request.input_value.strip().removeprefix("@")
        limit = max(1, min(50, request.max_items_per_page))
        media = ",".join(_MEDIA_FIELDS)
        include_media = bool(parameter_value(self.descriptor, request.parameters, "include_media"))
        if request.page_index == 0:
            inner = ",".join(_ACCOUNT_FIELDS)
            if include_media:
                inner += f",media.limit({limit}){{{media}}}"
        else:
            after = str((request.cursor or {}).get("after") or "")
            if not re.fullmatch(r"[A-Za-z0-9_\-=]{1,512}", after):
                raise ConnectorError(
                    ConnectorOutcome.PARSE_ERROR,
                    "The media pagination cursor is missing or malformed.",
                    code="unexpected_pagination",
                )
            inner = f"id,media.limit({limit}).after({after}){{{media}}}"
        fields = f"business_discovery.username({username}){{{inner}}}"
        url = f"{self._graph_base(request)}/{ig_user_id}?fields={quote(fields, safe='(){},._')}"
        result = http.fetch(
            request,
            url,
            headers={"authorization": f"Bearer {credentials['access_token']}"},
            pacing_key="graph",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=True,
        )
        quota = _graph_quota(result)
        body = social.json_body(result, "Graph API") if result.content else {}
        if not isinstance(body, dict):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The Graph API response is not a JSON object.",
                code="unexpected_schema",
            )
        if "error" in body or result.status_code >= 400:
            _raise_graph_error(request, result, body, quota, username)
        request.context.credential_result("access_token", "accepted")
        account = body.get("business_discovery")
        if not isinstance(account, dict) or not account.get("id"):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The Business Discovery response lacks the account object.",
                code="unexpected_schema",
            )
        evidence = EvidenceDraft(
            key="discovery",
            kind="json",
            content=result.content,
            content_type="application/json",
            title=(
                f"Instagram Business Discovery: {username}"
                + (f" (media page {request.page_index})" if request.page_index else "")
            )[:300],
            source_reference=result.final_url,
            collection_metadata={
                **result.metadata(),
                **social.provenance(spec),
                "api_version": GRAPH_API_VERSION,
            },
            access_category="credentialed",
        )
        raw_media = account.get("media")
        media_edge: dict[str, Any] = raw_media if isinstance(raw_media, dict) else {}
        items = [item for item in media_edge.get("data", []) or [] if isinstance(item, dict)]
        observations: list[ObservationDraft] = []
        entities: list[EntityDraft] = []
        account_id = str(account["id"])
        if request.page_index == 0:
            entities.append(
                EntityDraft(
                    key="account",
                    entity_type="platform_account",
                    display_name=f"{account.get('username') or username} on Instagram",
                    match=IdentifierDraft("platform_id", account_id, PLATFORM),
                    identifiers=(
                        IdentifierDraft("platform_id", account_id, PLATFORM),
                        IdentifierDraft(
                            "username", str(account.get("username") or username), PLATFORM
                        ),
                    ),
                    description=(
                        "Professional Instagram account returned by the official Business "
                        "Discovery API."
                    ),
                    attributes={"platform": PLATFORM, "account_class": "professional"},
                )
            )
            observations.append(
                ObservationDraft(
                    observation_type="instagram_account",
                    evidence_key="discovery",
                    entity_key="account",
                    source_object_id=account_id,
                    payload={
                        **{name: account.get(name) for name in _ACCOUNT_FIELDS},
                        "capability": spec.name,
                        "access_method": str(spec.access_method),
                        "fields_not_available": list(spec.unavailable_fields),
                    },
                    idempotency_suffix="account",
                )
            )
        for item in items:
            observations.append(
                ObservationDraft(
                    observation_type="instagram_media",
                    evidence_key="discovery",
                    source_object_id=str(item.get("id")),
                    payload={
                        **{name: item.get(name) for name in _MEDIA_FIELDS},
                        "like_count_hidden": "like_count" not in item,
                        "account_id": account_id,
                        "capability": spec.name,
                    },
                    event_time=parse_date(_graph_time(item.get("timestamp"))),
                    source_published_at=parse_date(_graph_time(item.get("timestamp"))),
                    idempotency_suffix=f"media:{item.get('id')}",
                    dedupe_across_pages=True,
                )
            )
        paging = media_edge.get("paging")
        cursors = paging.get("cursors") if isinstance(paging, dict) else None
        next_after = cursors.get("after") if isinstance(cursors, dict) else None
        has_more = bool(include_media and items and isinstance(next_after, str) and next_after)
        media_count = account.get("media_count")
        notes = []
        if request.page_index == 0 and not include_media:
            notes.append("Media were not requested for this run.")
        return ConnectorPage(
            page_index=request.page_index,
            has_more=has_more,
            next_cursor={"after": next_after} if has_more else None,
            evidence=[evidence],
            entities=entities,
            observations=observations,
            items=len(observations),
            quota=quota,
            coverage={
                "capability": spec.name,
                "media_count_reported": media_count if request.page_index == 0 else None,
                "media_on_page": len(items),
            },
            notes=notes,
        )

    # -- unofficial public profile page ----------------------------------------------------------

    def _profile_page(self, request: FetchRequest, spec: CapabilitySpec) -> ConnectorPage:
        settings = request.context.settings
        if settings is None or not getattr(settings, "instagram_public_web_enabled", False):
            raise ConnectorError(
                ConnectorOutcome.UNSUPPORTED,
                "The unofficial public profile page capability is disabled. An administrator "
                "must enable it (TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true) after reviewing "
                "Instagram's terms for this use.",
                code="capability_disabled",
            )
        username = request.input_value.strip().removeprefix("@")
        base = str(settings.instagram_web_base_url).rstrip("/")
        result = http.fetch(
            request,
            f"{base}/{quote(username, safe='._')}/",
            headers={"accept": "text/html", "accept-language": "en"},
            pacing_key="web",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=True,
            max_bytes=2 * 1024 * 1024,
        )
        final_path = urlsplit(result.final_url).path
        html_text, encoding = http.decode_text(result)
        evidence = EvidenceDraft(
            key="page",
            kind="html",
            content=result.content,
            content_type=result.headers.get("content-type", "text/html")[:100],
            title=f"Instagram profile page request: {username}",
            source_reference=result.final_url,
            collection_metadata={
                **result.metadata(),
                **social.provenance(spec),
                "decoded_with": encoding,
            },
            indexable=False,
        )
        if result.status_code == 429:
            raise ConnectorError(
                ConnectorOutcome.RATE_LIMITED,
                "Instagram throttled the profile page request (HTTP 429).",
                retry_after_seconds=http.retry_after_seconds(result.headers.get("retry-after"))
                or 600.0,
                code="instagram_throttled",
            )
        if result.status_code == 404:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                evidence=[evidence],
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                coverage={"capability": spec.name, "page_state": "not_available"},
                notes=[
                    "Instagram answered 404 for this profile page at retrieval time. The account "
                    "may not exist, may have been renamed, removed or restricted, or may be "
                    "hidden from unauthenticated visitors in this region."
                ],
            )
        meta = _PageMeta.parse(html_text)
        if final_path.startswith("/accounts/login") or (
            meta.has_login_form and not meta.og.get("og:url")
        ):
            raise ConnectorError(
                ConnectorOutcome.AUTHENTICATION_REQUIRED,
                "Instagram showed a login wall instead of the public profile page. Nothing about "
                "the account can be concluded from this response.",
                code="instagram_login_wall",
            )
        if result.status_code != 200:
            http.raise_for_status(result, "Instagram")
        description = meta.og.get("og:description") or meta.description or ""
        if not meta.og.get("og:title") and not description:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The profile page did not contain the expected metadata tags; the page layout "
                "may have changed.",
                code="unexpected_page_layout",
            )
        counts = _displayed_counts(description)
        private_indicator = "this account is private" in html_text.lower()
        payload = {
            "username_requested": username,
            "og_title": meta.og.get("og:title"),
            "og_description": description[:1000] or None,
            "og_url": meta.og.get("og:url"),
            "og_image": meta.og.get("og:image"),
            "displayed_counts": counts,
            "private_account_indicator": private_indicator,
            "capability": spec.name,
            "access_method": str(spec.access_method),
            "note": (
                "Values are the text Instagram placed in the page metadata for an "
                "unauthenticated visitor; counts are display strings and may be rounded."
            ),
        }
        return ConnectorPage(
            page_index=0,
            has_more=False,
            evidence=[evidence],
            observations=[
                ObservationDraft(
                    observation_type="instagram_public_profile_page",
                    evidence_key="page",
                    source_object_id=f"username:{username.lower()}",
                    payload=payload,
                    idempotency_suffix="profile_page",
                )
            ],
            items=1,
            coverage={"capability": spec.name, "page_state": "metadata_found"},
            incomplete_reason=(
                "The account appears private; only the page summary is visible."
                if private_indicator
                else None
            ),
            notes=[
                "Unofficial capability: no stable account ID is available, so no entity was "
                "created. The same username at another time may be a different account."
            ],
        )


class _PageMeta(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og: dict[str, str] = {}
        self.description: str | None = None
        self.has_login_form = False
        self._inputs: set[str] = set()

    @classmethod
    def parse(cls, html_text: str) -> _PageMeta:
        parser = cls()
        try:
            parser.feed(html_text[:2_000_000])
            parser.close()
        except (AssertionError, ValueError):
            pass
        parser.has_login_form = parser.has_login_form or {"username", "password"} <= parser._inputs
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key.startswith("og:") and content and key not in self.og:
                self.og[key] = content[:2048]
            elif key == "description" and content and self.description is None:
                self.description = content[:2048]
        elif tag == "input" and values.get("name"):
            self._inputs.add(values["name"].lower())
        elif tag == "form" and "/accounts/login" in values.get("action", ""):
            self.has_login_form = True


_COUNT = re.compile(r"([\d.,]+\s*[KkMmBb]?)\s+(Followers|Following|Posts)", re.I)


def _displayed_counts(description: str) -> dict[str, str]:
    return {label.lower(): value.strip() for value, label in _COUNT.findall(description)}


def _graph_time(value: object) -> str | None:
    """Graph API timestamps look like ``2026-09-01T10:00:00+0000``."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(.+[+-]\d{2})(\d{2})", value.strip())
    return f"{match.group(1)}:{match.group(2)}" if match else value


def _graph_quota(result: netguard.FetchResult) -> dict[str, Any] | None:
    usage: dict[str, Any] = {}
    for header in ("x-app-usage", "x-business-use-case-usage", "x-ad-account-usage"):
        raw = result.headers.get(header)
        if raw:
            try:
                usage[header] = json.loads(raw)
            except ValueError:
                usage[header] = raw[:500]
    if not usage:
        return None
    return {"provider": "meta_graph_api", "usage_headers": usage, "cost": "none"}


def _regain_seconds(quota: dict[str, Any] | None) -> float | None:
    """Minutes until access returns, from X-Business-Use-Case-Usage when Meta reports it."""
    headers = (quota or {}).get("usage_headers", {})
    business = headers.get("x-business-use-case-usage")
    if not isinstance(business, dict):
        return None
    minutes = [
        entry.get("estimated_time_to_regain_access")
        for entries in business.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    ]
    values = [m for m in minutes if isinstance(m, int | float) and m > 0]
    return float(max(values)) * 60 if values else None


def _raise_graph_error(
    request: FetchRequest,
    result: netguard.FetchResult,
    body: dict[str, Any],
    quota: dict[str, Any] | None,
    username: str,
) -> None:
    raw_error = body.get("error")
    error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
    code = social.integer(error.get("code"))
    subcode = social.integer(error.get("error_subcode"))
    message = social.text(error.get("message"), 300) or ""
    detail = f"(Graph API code {code}, subcode {subcode}): {message}"
    if code == 190 or code == 102 or result.status_code == 401:
        request.context.credential_result("access_token", "rejected")
        raise ConnectorError(
            ConnectorOutcome.AUTHENTICATION_REQUIRED,
            "Meta rejected the access token (expired, revoked or changed password) "
            + detail
            + ". Replace the token on the Sources page.",
            code="instagram_token_invalid",
            quota=quota,
        )
    if code in _THROTTLING_CODES or result.status_code == 429:
        raise ConnectorError(
            ConnectorOutcome.RATE_LIMITED,
            "Graph API rate limit reached " + detail,
            retry_after_seconds=_regain_seconds(quota) or 3600.0,
            code="instagram_rate_limited",
            quota=quota,
        )
    if code == 110 or subcode == 2207013:
        raise ConnectorError(
            ConnectorOutcome.UNSUPPORTED,
            "Business Discovery did not return this username "
            + detail
            + ". The API only returns professional accounts that are not age-gated; the account "
            "may not exist, or may be personal, restricted or age-gated. This is not evidence "
            "that the account does not exist. (Subcode 2207013 is reported by developers, not "
            "listed in Meta's error reference.)",
            code="instagram_account_not_discoverable",
            quota=quota,
        )
    if code == 10 or (code is not None and 200 <= code <= 299):
        raise ConnectorError(
            ConnectorOutcome.ACCESS_DENIED,
            "The token lacks a permission Business Discovery needs " + detail,
            code="instagram_permission_missing",
            quota=quota,
        )
    if code in (368, 25):
        raise ConnectorError(
            ConnectorOutcome.ACCESS_DENIED,
            "Meta blocked or restricted this request or account " + detail,
            code="instagram_restricted",
            quota=quota,
        )
    if code in _TRANSIENT_CODES or result.status_code >= 500:
        raise ConnectorError(
            ConnectorOutcome.UNAVAILABLE,
            "The Graph API reported a temporary problem " + detail,
            code="instagram_temporary_error",
            quota=quota,
        )
    raise ConnectorError(
        ConnectorOutcome.UNSUPPORTED,
        "The Graph API rejected the request " + detail,
        code=f"graph_error_{code}" if code is not None else f"http_{result.status_code}",
        quota=quota,
    )
