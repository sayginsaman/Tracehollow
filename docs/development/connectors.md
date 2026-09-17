# Write a connector

A connector turns one saved-query input into bounded pages of evidence. This guide shows what to
implement, what the execution engine does for you, and what a connector is never allowed to do.

Read [ADR 0006](../adr/0006-public-source-collection.md) first for why the contract looks like
this, and [ADR 0009](../adr/0009-social-connector-capabilities.md) if your source is a social
platform with several access methods.

## What the engine does, so you do not

The `collector` service owns execution. It persists each page in one transaction, applies retries
and pacing, records coverage and quota, handles cancellation and writes the run's outcome.

A connector therefore **never**:

- touches the database or the evidence store,
- decides that a run succeeded or failed overall,
- retries by itself, sleeps between pages, or fans out concurrently,
- returns an empty page to mean a failure.

It only validates parameters and returns one page, or raises `ConnectorError`.

## The contract

`services/api/app/connectors/base.py` defines it:

```python
class Connector(Protocol):
    descriptor: ConnectorDescriptor

    def validate(self, input_type: str, input_value: str, parameters: dict[str, Any]) -> None:
        """Raise ValueError for invalid saved-query parameters."""

    def fetch_page(self, request: FetchRequest) -> ConnectorPage:
        """Return one bounded page or raise ConnectorError."""
```

### The descriptor

The descriptor is what analysts see before they run anything, and what the Sources screen
displays. Fill it honestly; it is the only place that says what your connector really does.

| Field | Says |
| --- | --- |
| `connector_id`, `version`, `display_name`, `description` | Identity, shown in saved queries |
| `collection_mode` | `direct_request` (the target sees the request), `third_party_api` (it does not), `platform_probe` (each platform sees a request) or `synthetic_fixture` |
| `supported_input_types` | Which saved-query inputs apply: `username`, `domain`, `email`, and so on |
| `coverage` | What the source can and cannot see. Write the limits, not the hopes |
| `max_pages`, `max_items_per_page`, `timeout_seconds` | Hard bounds the engine enforces |
| `retry_policy` | Which outcomes are retryable, how many attempts, the backoff |
| `credential_requirements`, `credentials` | What must be configured, and what each credential unlocks |
| `cost_model`, `quota_notes` | Paid calls and provider quota, if any |
| `min_request_interval_seconds`, `max_concurrent_runs` | Pacing across all workers |
| `verification_status`, `last_live_verification` | `synthetic`, `fixture_tested` or `live_verified` — see below |
| `parameters` | Typed parameter specs; the engine validates types, choices and bounds |
| `capabilities` | Platform connectors only: every access method considered, implemented or not |

`verification_status` is not yours to set to `live_verified`. A connector starts `fixture_tested`
and is promoted only after an authorized live check is recorded
([../connectors/live-smoke.md](../connectors/live-smoke.md)).

### The page

`ConnectorPage` carries drafts, not database rows:

| Draft | Purpose |
| --- | --- |
| `EvidenceDraft` | The original bytes, their content type, a source reference and the source's own publication time. Keep what the source sent, not your rendering of it |
| `EntityDraft` | An entity the page refers to, with the identifier used to match it. Matching happens only against observed entities of the same type; analyst-created entities are never modified |
| `ObservationDraft` | One source-specific fact, tied to an evidence record and optionally to an entity, with the event time if the source gave one |
| `RelationshipDraft` | A link between two drafts on this page, with an `origin` of `observed` or `deterministic_derivation` and the observation that supports it |

Also set `items` (usable items on the page), `has_more`, `next_cursor` for pagination,
`coverage` counters, `incomplete_reason` when the page is usable but incomplete, and `quota` when
the provider reports it.

### Outcomes

Failures are raised as `ConnectorError` with an outcome from the fixed vocabulary:

`findings`, `no_findings`, `partial`, `authentication_required`, `access_denied`, `rate_limited`,
`unsupported`, `unavailable`, `parse_error`, `canceled`.

Two rules decide most reviews:

- **`no_findings` requires an answer.** Use it only when the source replied and had nothing within
  its coverage. A block, a login wall, a throttle or a timeout is its own outcome.
- **A partially collected page is `partial`, not success.** Set `incomplete_reason` so the run says
  what is missing.

### Making requests

Never use an HTTP client directly. Call `app.connectors.http.fetch`, which reserves the request's
budget before anything is sent, applies pacing, runs the SSRF checks in `netguard` on every address
and redirect, enforces the byte limit and the timeout, and maps network failures to outcomes:

```python
from app.connectors import http

result = http.fetch(
    request,
    url,
    headers={"accept": "application/json"},
    pacing_key=f"host:{host}",
    interval_seconds=self.descriptor.min_request_interval_seconds,
)
http.raise_for_status(result, "The provider")
```

A destination refused by the network policy never leaves the installation, and its budget
reservation is released. Do not retry such a refusal: it is `access_denied` or `unsupported`, not a
transient error.

## Steps

1. **Write a design note first** if the source has several access methods, or if any of them is
   unofficial. The capability model exists so that an analyst can see the difference before
   running anything.
2. **Add the module** under `services/api/app/connectors/`, implementing the protocol.
3. **Register it** in `services/api/app/connectors/registry.py`. Only registered connectors can be
   selected in saved queries.
4. **Write contract tests** in `services/api/tests/test_connector_contracts.py` covering, at
   minimum: a successful page, pagination and its cursor, a verified empty result, each failure
   mapped to its outcome, a truncated response reported as incomplete, and a refused private
   destination that sends no request. Use recorded fixtures; tests never reach the network.
5. **Document it** as `docs/connectors/<name>.md`, following an existing page: mode, coverage,
   inputs, parameters, credentials, outcomes, limits, live-verification status. Add it to the
   table in [../connectors/README.md](../connectors/README.md).
6. **Run the checks**: `scripts/test-backend.sh -k connector`, then the full
   `cd services/api && uv run ruff check . && uv run mypy`.
7. **Verify it in the stack** with a fixture or a target you control, before anyone proposes a live
   check.

## Review checklist

- [ ] The descriptor's coverage says what the source cannot see, not only what it can.
- [ ] Every failure path raises `ConnectorError` with the right outcome; none returns an empty page.
- [ ] `no_findings` is reachable only after the source answered.
- [ ] Pagination is bounded by `max_pages` and stays on the origin it started on.
- [ ] All requests go through `netguard`; redirects are checked, not followed blindly.
- [ ] Credentials are read from the configured store, never logged, never placed in evidence,
      metadata or error messages.
- [ ] Evidence keeps the source's original bytes and its own published timestamp.
- [ ] Entities are drafts matched by identifier; nothing merges identities.
- [ ] `verification_status` is `fixture_tested` unless a recorded live check says otherwise.
- [ ] The connector page in `docs/connectors/` exists and matches the descriptor.

## Sources of truth

| For | Read |
| --- | --- |
| The contract | `services/api/app/connectors/base.py` |
| A direct-request example | `services/api/app/connectors/web.py`, `rss.py` |
| A third-party API example with quota | `services/api/app/connectors/github.py` |
| A capability-based platform example | `services/api/app/connectors/instagram.py` |
| An external engine in a sandbox | `services/api/app/connectors/subfinder.py`, [ADR 0007](../adr/0007-subfinder-network-sandbox.md) |
| Network rules | `services/api/app/connectors/netguard.py` |
| Live verification | [../connectors/live-smoke.md](../connectors/live-smoke.md) |
