"""Observation comparison rules shared by entity comparison and monitor change detection.

Both features read differences between observations through these helpers, so a field that the
comparison view calls a change is the same field change detection reports, and an item missing
from an incomplete collection is *unknown* in both.
"""

from __future__ import annotations

from typing import Any

from app.queries.models import ConnectorOutcome

COMPLETE_STOP = "complete"
MAX_VALUE_CHARS = 500
# Payload fields compared across sources for conflicting values.
CONFLICT_FIELDS = (
    "name", "full_name", "title", "display_name", "website", "blog", "location", "country",
    "email", "company", "description", "biography", "custom_url",
)  # fmt: skip
# Bookkeeping that is never a change of the observed item.
IGNORED_CHANGE_FIELDS = frozenset(
    {"capability", "access_method", "processing_job_id", "note", "fields_not_available"}
)
# Collection metadata that differs between collections of the same unchanged item (timestamps,
# transfer details, page counts). Excluded from *meaningful* change in monitoring.
VOLATILE_FIELDS = frozenset(
    {
        "bytes",
        "checked_at",
        "collected_at",
        "entries_on_first_page",
        "etag",
        "fetched_at",
        "http_status",
        "last_modified",
        "redirects",
        "retrieved_at",
        "text_characters",
        "truncated",
    }
)
# Engagement and size counters change continuously; monitoring does not treat them as meaningful
# changes (the comparison view still lists them).
COUNTER_FIELDS = frozenset(
    {
        "comment_count",
        "comments_count",
        "followers",
        "followers_count",
        "following",
        "forks_count",
        "like_count",
        "media_count",
        "member_count",
        "open_issues_count",
        "public_gists",
        "public_repos",
        "stargazers_count",
        "subscriber_count",
        "video_count",
        "view_count",
    }
)
MONITORING_EXCLUDED_FIELDS = IGNORED_CHANGE_FIELDS | VOLATILE_FIELDS | COUNTER_FIELDS
COMPLETE_OUTCOMES = (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)


def value_text(value: Any) -> str | None:
    """Comparable, bounded text for a payload value (empty values compare as missing)."""
    if value is None or value == "" or value == []:
        return None
    return str(value)[:MAX_VALUE_CHARS]


def field_differences(
    previous: dict[str, Any], current: dict[str, Any], *, excluded: frozenset[str]
) -> list[tuple[str, str | None, str | None]]:
    """(field, previous, current) for every compared field whose value differs, sorted by field."""
    differences = []
    for field in sorted((set(previous) | set(current)) - excluded):
        before, after = value_text(previous.get(field)), value_text(current.get(field))
        if before != after:
            differences.append((field, before, after))
    return differences


def collection_complete(outcome: str | None, coverage: dict[str, Any] | None) -> bool:
    """A connector run that finished its lookup without stopping early.

    Only such a run can establish that an earlier item is no longer returned, and only within its
    recorded scope. Partial, failed, rate-limited, truncated, budget-stopped and canceled runs
    cannot.
    """
    stopped = (coverage or {}).get("stopped_reason")
    return outcome in COMPLETE_OUTCOMES and stopped == COMPLETE_STOP
