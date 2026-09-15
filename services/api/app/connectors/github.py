"""GitHub connector: a public account and its public repositories via the official REST API.

API version ``2026-03-10`` (docs.github.com, "API Versions", checked 2026-09-15). Unauthenticated
requests allow 60 per hour per IP address; a personal access token raises this to 5,000 per
hour. Rate-limit headers are recorded as quota usage; a primary or secondary rate limit is
reported as ``rate_limited`` with the documented wait, never as missing data.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from app.connectors import http, netguard
from app.connectors.base import (
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
    RelationshipDraft,
    RetryPolicy,
    VerificationStatus,
    parameter_value,
    validate_parameters,
)
from app.connectors.feeds import parse_date
from app.entities import normalize
from app.queries.models import ConnectorOutcome

CONNECTOR_ID = "github.account"
API_VERSION = "2026-03-10"
PLATFORM = "github.com"
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')
_ACCOUNT_FIELDS = (
    "login", "id", "node_id", "type", "name", "company", "blog", "location", "email", "bio",
    "twitter_username", "public_repos", "public_gists", "followers", "following", "created_at",
    "updated_at", "html_url",
)  # fmt: skip
_REPOSITORY_FIELDS = (
    "id", "full_name", "html_url", "description", "fork", "language", "stargazers_count",
    "forks_count", "open_issues_count", "archived", "homepage", "topics", "created_at",
    "updated_at", "pushed_at",
)  # fmt: skip


class GitHubAccountConnector:
    descriptor = ConnectorDescriptor(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        display_name="GitHub account",
        synthetic=False,
        description=(
            "Looks up a GitHub user or organization and its public repositories through the "
            "official REST API. GitHub sees the request; the account holder is not contacted."
        ),
        supported_input_types=("username",),
        collection_mode=CollectionMode.THIRD_PARTY_API,
        credential_requirements=(
            "Optional personal access token (no scopes needed for public data); without it "
            "GitHub allows 60 requests per hour per IP address."
        ),
        coverage=(
            "Public profile fields and public repositories owned by the account, most recently "
            "updated first, up to the page limit. Private data, contributions to other "
            "repositories and deleted accounts are not covered. Enterprise Managed Users may "
            "appear as not found without suitable authorization."
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
        output_schema="tracehollow.github.account/v1",
        cost_model="Free API; subject to GitHub rate limits.",
        quota_notes=(
            "Primary limit 60 requests/hour unauthenticated or 5,000/hour with a token; "
            "secondary limits apply to bursts. One request for the account plus one per "
            "repository page."
        ),
        last_live_verification=None,
        verification_status=VerificationStatus.FIXTURE_TESTED,
        parameters=(
            ParameterSpec(
                name="include_repositories",
                kind="boolean",
                label="Include repositories",
                description="Also list the account's public repositories (one request per page).",
                default=True,
            ),
        ),
        credentials=(
            CredentialSpec(
                name="token",
                label="Personal access token",
                description=(
                    "Fine-grained or classic token. Public data needs no permissions. Sent only "
                    "to the configured GitHub API address."
                ),
                required=False,
            ),
        ),
        cache_policy="No caching: every execution queries the API again.",
        max_concurrent_runs=2,
        min_request_interval_seconds=1.0,
        provider_terms=(
            "GitHub Terms of Service and API terms; do not use the API for spam or to build "
            "profiles for harassment."
        ),
        documentation="docs/connectors/github.md",
    )

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        if not _LOGIN.match(input_value.strip()):
            raise ValueError(
                "a GitHub account name has 1-39 letters, digits or single hyphens and does not "
                "start or end with a hyphen"
            )
        validate_parameters(self.descriptor, parameters)

    # -- requests ------------------------------------------------------------------------------

    def _base(self, request: FetchRequest) -> str:
        settings = request.context.settings
        base = settings.github_api_base_url if settings is not None else "https://api.github.com"
        return str(base).rstrip("/")

    def _get(self, request: FetchRequest, url: str) -> tuple[netguard.FetchResult, bool]:
        token = request.context.credential("token")
        headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": API_VERSION,
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        result = http.fetch(
            request,
            url,
            headers=headers,
            pacing_key="api",
            interval_seconds=self.descriptor.min_request_interval_seconds,
            same_origin_redirects_only=True,
        )
        quota = _quota(result, authenticated=bool(token))
        code = result.status_code
        if code == 401:
            if token:
                request.context.credential_result("token", "rejected")
            raise ConnectorError(
                ConnectorOutcome.AUTHENTICATION_REQUIRED,
                "GitHub rejected the configured token (HTTP 401). Replace it on the Sources page."
                if token
                else "GitHub requires authentication for this request (HTTP 401).",
                code="github_bad_credentials" if token else "http_401",
                quota=quota,
            )
        if code in (403, 429):
            remaining = result.headers.get("x-ratelimit-remaining")
            message = _message(result)
            if remaining == "0" or code == 429 or "rate limit" in message.lower():
                wait = http.retry_after_seconds(result.headers.get("retry-after"))
                reset = result.headers.get("x-ratelimit-reset", "")
                if wait is None and remaining == "0" and reset.isdigit():
                    wait = max(0.0, int(reset) - datetime.now(UTC).timestamp())
                if wait is None:
                    wait = 60.0
                raise ConnectorError(
                    ConnectorOutcome.RATE_LIMITED,
                    "GitHub rate limit reached"
                    + ("" if token else " for unauthenticated requests; a token raises the limit")
                    + ".",
                    retry_after_seconds=round(wait, 1),
                    code="github_rate_limited",
                    quota=quota,
                )
            raise ConnectorError(
                ConnectorOutcome.ACCESS_DENIED,
                f"GitHub refused access (HTTP 403): {message[:200]}",
                code="http_403",
                quota=quota,
            )
        if code not in (200, 404):
            try:
                http.raise_for_status(result, "The GitHub API")
            except ConnectorError as error:
                error.quota = quota
                raise
        if token and code == 200:
            request.context.credential_result("token", "accepted")
        return result, bool(token)

    # -- pages ---------------------------------------------------------------------------------

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        if request.input_type != "username":
            raise ConnectorError(ConnectorOutcome.UNSUPPORTED, "Only account names are supported.")
        if request.page_index == 0:
            return self._account(request)
        return self._repositories(request)

    def _account(self, request: FetchRequest) -> ConnectorPage:
        login = request.input_value.strip()
        url = f"{self._base(request)}/users/{quote(login, safe='')}"
        result, authenticated = self._get(request, url)
        quota = _quota(result, authenticated=authenticated)
        if result.status_code == 404:
            return ConnectorPage(
                page_index=0,
                has_more=False,
                quota=quota,
                outcome_hint=ConnectorOutcome.NO_FINDINGS,
                evidence=[
                    self._evidence(
                        result,
                        "account",
                        f"GitHub API: no account {login}",
                        authenticated=authenticated,
                    )
                ],
                notes=[
                    "GitHub's API answered 404 Not Found for this account name at retrieval "
                    "time. Accounts can be renamed, deleted, suspended or hidden from "
                    "unauthorized requests (Enterprise Managed Users)."
                ],
            )
        account = _json_object(result)
        missing = [name for name in ("login", "id", "html_url") if name not in account]
        if missing:
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                f"The GitHub account response lacks {', '.join(missing)}.",
                code="unexpected_schema",
            )
        payload = {name: account.get(name) for name in _ACCOUNT_FIELDS}
        account_key = "account"
        entities = [
            EntityDraft(
                key=account_key,
                entity_type="platform_account",
                display_name=f"{account['login']} on GitHub",
                match=IdentifierDraft("platform_id", str(account["id"]), PLATFORM),
                identifiers=(
                    IdentifierDraft("platform_id", str(account["id"]), PLATFORM),
                    IdentifierDraft("username", str(account["login"]), PLATFORM),
                    IdentifierDraft("url", str(account["html_url"]), PLATFORM),
                ),
                description=(
                    f"GitHub {str(account.get('type') or 'account').lower()} observed through "
                    "the official API."
                ),
                attributes={"platform": PLATFORM, "account_type": account.get("type")},
            )
        ]
        observations = [
            ObservationDraft(
                observation_type="github_account",
                evidence_key="account",
                entity_key=account_key,
                source_object_id=str(account["id"]),
                payload=payload,
                event_time=parse_date(_str(account.get("created_at"))),
                idempotency_suffix="account",
            )
        ]
        relationships: list[RelationshipDraft] = []
        blog = _str(account.get("blog"))
        if blog:
            blog_url = blog if "://" in blog else f"https://{blog}"
            try:
                normalize.normalize_url(blog_url)
            except normalize.IdentifierError:
                blog_url = ""
            if blog_url:
                entities.append(
                    EntityDraft(
                        key="blog",
                        entity_type="url",
                        display_name=blog_url[:300],
                        match=IdentifierDraft("url", blog_url),
                        description="Website listed on a GitHub profile.",
                    )
                )
                relationships.append(
                    RelationshipDraft(
                        source_key=account_key,
                        target_key="blog",
                        predicate="links_to",
                        origin="observed",
                        description="Listed as the website on the GitHub profile.",
                        observation_index=0,
                    )
                )
        email = _str(account.get("email"))
        if email:
            try:
                normalize.normalize_email(email)
            except normalize.IdentifierError:
                email = ""
            if email:
                entities.append(
                    EntityDraft(
                        key="email",
                        entity_type="email",
                        display_name=email[:300],
                        match=IdentifierDraft("email", email),
                        description="Public email address listed on a GitHub profile.",
                    )
                )
                relationships.append(
                    RelationshipDraft(
                        source_key=account_key,
                        target_key="email",
                        predicate="lists_email",
                        origin="observed",
                        description=(
                            "Shown as the public email on the GitHub profile. This does not "
                            "establish who controls either."
                        ),
                        observation_index=0,
                    )
                )
        include = bool(parameter_value(self.descriptor, request.parameters, "include_repositories"))
        public_repos = account.get("public_repos")
        has_repos = include and isinstance(public_repos, int) and public_repos > 0
        per_page = max(1, min(100, request.max_items_per_page))
        next_url = (
            f"{self._base(request)}/users/{quote(str(account['login']), safe='')}/repos"
            f"?type=owner&sort=updated&per_page={per_page}&page=1"
            if has_repos
            else None
        )
        return ConnectorPage(
            page_index=0,
            has_more=next_url is not None,
            next_cursor={"url": next_url} if next_url else None,
            evidence=[
                self._evidence(
                    result,
                    "account",
                    f"GitHub account: {account['login']}",
                    authenticated=authenticated,
                )
            ],
            entities=entities,
            observations=observations,
            relationships=relationships,
            items=1,
            quota=quota,
            coverage={"public_repos_reported": public_repos},
        )

    def _repositories(self, request: FetchRequest) -> ConnectorPage:
        cursor = request.cursor or {}
        url = str(cursor.get("url") or "")
        base = urlsplit(self._base(request))
        target = urlsplit(url)
        if not url or (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The next repository page is not on the configured GitHub API address.",
                code="unexpected_pagination",
            )
        result, authenticated = self._get(request, url)
        quota = _quota(result, authenticated=authenticated)
        if result.status_code == 404:
            raise ConnectorError(
                ConnectorOutcome.UNAVAILABLE,
                "The account disappeared while its repositories were being listed (HTTP 404).",
                code="http_404",
                quota=quota,
            )
        try:
            repositories = json.loads(result.content)
        except ValueError:
            repositories = None
        if not isinstance(repositories, list) or not all(isinstance(r, dict) for r in repositories):
            raise ConnectorError(
                ConnectorOutcome.PARSE_ERROR,
                "The repository list is not a JSON array of objects.",
                code="unexpected_schema",
            )
        login = request.input_value.strip()
        observations = [
            ObservationDraft(
                observation_type="github_repository",
                evidence_key="repositories",
                source_object_id=str(repo.get("id")),
                payload={
                    **{name: repo.get(name) for name in _REPOSITORY_FIELDS},
                    "license": (repo.get("license") or {}).get("spdx_id")
                    if isinstance(repo.get("license"), dict)
                    else None,
                    "owner_login": login,
                },
                event_time=parse_date(_str(repo.get("created_at"))),
                idempotency_suffix=f"repo:{repo.get('id')}",
                dedupe_across_pages=True,
            )
            for repo in repositories
            if repo.get("id") is not None
        ]
        next_url = _links(result.headers.get("link", "")).get("next")
        return ConnectorPage(
            page_index=request.page_index,
            has_more=next_url is not None,
            next_cursor={"url": next_url} if next_url else None,
            evidence=[
                self._evidence(
                    result,
                    "repositories",
                    f"GitHub repositories of {login} (page {request.page_index})",
                    authenticated=authenticated,
                )
            ],
            observations=observations,
            items=len(observations),
            quota=quota,
        )

    def _evidence(
        self, result: netguard.FetchResult, key: str, title: str, *, authenticated: bool
    ) -> EvidenceDraft:
        return EvidenceDraft(
            key=key,
            kind="json",
            content=result.content,
            content_type="application/json",
            title=title[:300],
            source_reference=result.final_url,
            collection_metadata={**result.metadata(), "api_version": API_VERSION},
            access_category="credentialed" if authenticated else "public",
            indexable=result.status_code == 200,
        )


def _json_object(result: netguard.FetchResult) -> dict[str, Any]:
    if result.truncated:
        raise ConnectorError(
            ConnectorOutcome.PARSE_ERROR, "The GitHub response was truncated.", code="too_large"
        )
    try:
        value = json.loads(result.content)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ConnectorError(
            ConnectorOutcome.PARSE_ERROR,
            "The GitHub response is not a JSON object.",
            code="unexpected_schema",
        )
    return value


def _message(result: netguard.FetchResult) -> str:
    try:
        value = json.loads(result.content)
    except ValueError:
        return ""
    return str(value.get("message", "")) if isinstance(value, dict) else ""


def _str(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _links(header: str) -> dict[str, str]:
    return {rel: url for url, rel in _LINK.findall(header)}


def _quota(result: netguard.FetchResult, *, authenticated: bool) -> dict[str, Any] | None:
    headers = result.headers
    if "x-ratelimit-limit" not in headers:
        return None
    reset = headers.get("x-ratelimit-reset", "")
    return {
        "provider": "github",
        "authenticated": authenticated,
        "limit": _int(headers.get("x-ratelimit-limit")),
        "remaining": _int(headers.get("x-ratelimit-remaining")),
        "used": _int(headers.get("x-ratelimit-used")),
        "resource": headers.get("x-ratelimit-resource"),
        "reset_at": datetime.fromtimestamp(int(reset), UTC).isoformat()
        if reset.isdigit()
        else None,
        "cost": "none",
    }


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None
