# Monitoring, change detection and notifications

A **monitor** reruns a saved query on a schedule, within limits and a budget, compares every
collection with the previous comparable one and tells case analysts about meaningful changes,
failures that need action and exhausted budgets. This guide describes the behaviour an analyst or
operator can rely on. Budget accounting is in [budgets.md](budgets.md); roles in
[../security/permissions.md](../security/permissions.md); design decisions in
[ADR 0011](../adr/0011-durable-monitoring-budgets-and-change-detection.md) and
[ADR 0012](../adr/0012-notifications-and-webhook-adapter.md).

## Creating a monitor

**Case → Monitors → New monitor** (analysts in an active case). A monitor has:

| Setting | Meaning |
| --- | --- |
| Saved query | What to collect. The monitor pins a fingerprint of the query definition. |
| Sources | The query's connectors, or fewer. |
| Schedule | Every N hours (elapsed time), daily at a time, or weekly on chosen days at a time, in an IANA timezone. |
| After downtime | `run_latest` (default) runs once for the most recent missed time; `skip` records the missed time without running. |
| Scope | Pages and items per page, never above the saved query's own limits. |
| Limits per run | Requests (retries and pages count), items and minutes. |
| Budget | Requests (and optionally documented provider units) per UTC day, ISO week or month for this monitor. |
| Notifications | Changes, failures that need action, budget used up, and optionally every completed run; recipients are the case analysts or all members. |
| Retention | Optionally keep only the last N runs' results, or results younger than N days (applied by the case retention job). |

Monitors are **created paused**. Enabling a monitor whose sources contact anything outside
Tracehollow (every connector except the synthetic fixture) requires ticking an explicit
confirmation that it will contact those sources on the schedule, within its limits and budget; the
API refuses `enable` without `acknowledge_recurring_collection` (422
`recurring_collection_not_acknowledged`). Nothing enables collection automatically, including
restores (see [Restores](#restores)).

The installation sets the shortest interval (`TRACEHOLLOW_MONITOR_MIN_INTERVAL_MINUTES`, default
60). Schedules below it are refused.

## States and actions

| Action | Effect on future runs | Effect on a queued or running run |
| --- | --- | --- |
| **Pause** | none are scheduled | keeps running |
| **Resume / Enable** | scheduled from the next future time; the paused period is never caught up | — |
| **Cancel** (on the run page) | unchanged | stops before the next request; pages already collected are kept |
| **Disable** | none are scheduled; the monitor must be enabled again with the confirmation | cancelled |
| **Delete** (disabled monitors only) | — | — ; runs, evidence and change sets stay in the case |
| **Run now** | unchanged | refused while a run of the monitor is queued or running |

A monitor also pauses itself, with the reason shown and case analysts notified, when:

- its saved query is edited (`query_changed`): resuming requires **Adopt query changes**, which
  starts a new change-detection baseline; queued and running executions keep the snapshot they
  were created with;
- the analyst who last enabled it loses analyst access (`authorization_lost`);
- the case is archived (`case_archived`) or the saved query becomes invalid (`query_invalid`);
- it fails `TRACEHOLLOW_MONITOR_MAX_CONSECUTIVE_FAILURES` times in a row (default 5,
  `repeated_failures`).

A saved query with monitors cannot be deleted until they are deleted. Deleting a case disables its
monitors at once.

## Scheduling semantics

Scheduling state lives in PostgreSQL; Redis only carries task messages.

- **Durable and exactly once per slot.** Every dispatcher process runs the scheduler. A due monitor
  is locked with `SELECT … FOR UPDATE SKIP LOCKED`; the occurrence row is unique per
  `(monitor, scheduled_for)` and is written in the same transaction as the execution and its outbox
  row. Several dispatchers, a crash before commit or a redelivered message cannot create a second
  execution for a slot. Verified with three competing dispatchers in `scripts/verify-phase5.sh`.
- **Overlap.** A slot that arrives while the previous execution is still queued or running is
  recorded as a skipped occurrence (`overlap`); it is not queued behind it.
- **Downtime.** When no scheduler ran for a while, the next scheduler dispatches **one** execution
  for the most recent missed time and records how many earlier times were missed (`missed_slots`),
  or with `skip` records the missed time without running. There is never a catch-up burst. A time
  counts as on time within a grace of `min(TRACEHOLLOW_MONITOR_MISFIRE_GRACE_SECONDS, interval / 2)`
  (default grace 300 seconds).
- **Timezones and daylight saving.**
  - Interval schedules count elapsed time in UTC from the moment the schedule was set;
    daylight-saving changes never move them.
  - Daily and weekly schedules use wall-clock time in the monitor's IANA timezone. A time that does
    not exist because clocks go forward runs that much later (02:30 in a 02:00–03:00 gap runs at
    03:30). A time that occurs twice because clocks go back runs once, at the first instant.
- **Re-checks at dispatch.** Before an execution is created the scheduler re-checks that the case is
  active, the authorizing analyst still has analyst access, the saved query still matches the
  pinned fingerprint and is valid, no run overlaps and the monitor and case budgets have room. A
  failed check is a skipped occurrence with its reason. The execution itself re-checks access before
  every page.
- **Occurrence history** is kept for `TRACEHOLLOW_MONITOR_OCCURRENCE_RETENTION_DAYS` (default 180).

## Change detection

When an execution of a saved query finishes, each connector run is compared with a baseline:

1. **Baseline.** Among earlier connector runs of the same saved query and connector that collected
   at least one page, the most recent one with **complete coverage** and the same fingerprint
   (connector version, input, parameters, scope limits). A partial, failed, rate-limited,
   truncated, budget-stopped or cancelled run is never preferred over a complete one. If the most
   recent usable run has a different fingerprint, the change set is **Not comparable**
   (`baseline_incompatible`), says what differs and compares nothing.
2. **Items** are keyed by observation type and the source's own identifier (for example a feed
   entry ID or a platform ID). Volatile metadata (fetch times, byte counts, request bookkeeping,
   counters such as follower or view counts) is excluded.
3. **Classes:**

   | Class | Meaning |
   | --- | --- |
   | New | returned now, not by a complete baseline |
   | Changed | same item, a different value for a compared field (before and after shown) |
   | No longer observed | in the baseline, not returned by this **complete** collection: absence within the recorded scope, not proof of deletion |
   | Conflicting | the same item returned twice in one collection with different values |
   | Unknown | not returned by an incomplete collection, or missing from an incomplete baseline |
   | No meaningful change | a complete comparison with nothing new, changed, absent or conflicting |

4. **No deletion claims from incomplete runs.** Items a partial, failed, rate-limited, truncated or
   budget-stopped collection did not return are always *unknown*.
5. **Evidence.** Every event links to both executions, both observations and both evidence
   records. If retention later removed a side, the event says so instead of breaking.
6. **Deterministic.** No model is involved; nothing is merged, linked or edited; an account match
   stays a candidate. Detection is idempotent: one change set per connector run.

Change sets appear on the monitor's occurrence list, on the run page (**Changes since the previous
collection**) and at `/cases/{id}/changes/{changeSetId}`.

## In-app notifications

The bell in the header and **Notifications** list events for the signed-in account:

| Event | When | Default |
| --- | --- | --- |
| Changes detected | a comparison found new, changed, no longer observed or conflicting items | on |
| Needs action | a run fails in a new way (a different failure signature from the previous run), or the monitor paused itself (query changed, access lost, repeated failures) | on |
| Budget used up | a scheduled time was skipped or a run stopped because a budget was exhausted, at most once per monitor and budget period | on |
| Run completed | every finished run | off |

- **No repeats.** Each event has a stable ID derived from the case and a deduplication key, and rows
  are unique per account, so retried jobs, redelivered tasks and competing schedulers create one
  notification. Repeated identical failures are not notified again.
- **Membership is checked** when the list is read and when a notification is opened; an account
  that left the case no longer sees its notifications.
- **Content.** Titles name the monitor (a name analysts chose); bodies contain counts and reason
  codes. Collected values, evidence text, AI text, query inputs and credentials are never included.
- Read and unread states are per account. Notifications are kept for
  `TRACEHOLLOW_NOTIFICATION_RETENTION_DAYS` (default 180).

## Webhook notifications (optional)

An optional adapter posts minimal event summaries to receivers an administrator configures.

**Off by default.** It works only when the operator sets `TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED=true`
and restarts the API, dispatcher and collector. While off, no destination can be created or enabled
and pending deliveries are blocked.

**Setting up (administrator, Administration → Notification destinations):**

1. Add a destination: name, receiver URL, the event types it may receive, and a rate limit. The
   URL passes the same network policy as collection: `http` or `https` on an allowed port, no
   loopback, link-local or metadata addresses, private addresses only when explicitly allowed for
   collection, and no credentials, query string or fragment. Use `https` for any receiver outside
   a network you control; the payload is signed but not encrypted. It is created **disabled** with an HMAC signing secret that is shown
   **once** and stored encrypted with the credential key.
2. **Preview the payload** (the exact format with a sample event).
3. **Enable** by typing the receiver's host exactly (the API requires `confirm_host`). This is the
   explicit authorization to send to that destination.

**Subscribing (analyst, monitor page → External notifications):** choose an enabled destination
and the event types. Analysts see the destination's name and host, not its URL.

**Payload** (`schema: tracehollow.notification/v1`):

```json
{
  "schema": "tracehollow.notification/v1",
  "event_id": "5b0c…",
  "event_type": "change_detected",
  "severity": "info",
  "occurred_at": "2026-09-17T08:12:03+00:00",
  "generator": "tracehollow/0.1.0",
  "case_id": "8a21…",
  "monitor_id": "3f9e…",
  "occurrence_id": "c0d1…",
  "query_run_id": "da11…",
  "summary": {"new": 1, "changed": 1, "not_observed": 0, "conflicting": 0, "unknown": 0, "change_sets": 1, "run_status": "completed"},
  "link": "https://tracehollow.example.internal/cases/8a21…/monitors/3f9e…?run=da11…"
}
```

Headers: `x-tracehollow-event-id`, `x-tracehollow-timestamp` and, when signed,
`x-tracehollow-signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<body>">`.

**Never sent:** case or monitor names, notification titles and bodies, query inputs, collected
values, evidence, AI questions or answers, usernames, credentials. Before every attempt the payload
is checked against an allowlist of keys and scanned for configured secrets, and delivery is refused
if either check fails.

**Delivery:**

- Runs in the collector (the service with outbound network access); the receiver's address is
  resolved and checked again at every attempt; redirects are not followed;
  timeout `TRACEHOLLOW_NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` (default 10).
- Before every attempt it re-checks: adapter on, destination enabled, event type allowed for the
  destination and subscription, subscription present, case active or archived, and that the
  analyst who subscribed still has analyst access. A failed check blocks the delivery with a reason.
- 2xx is success. 408, 425, 429, 5xx and network errors are retried with exponential backoff
  (30 seconds doubling, at most one hour, honouring `Retry-After`) up to
  `TRACEHOLLOW_NOTIFICATION_DELIVERY_MAX_ATTEMPTS` (default 5). Other responses fail at once.
- Per-destination pacing enforces the configured rate across workers.
- **Duplicates are possible.** A receiver can get the same event more than once (for example when a
  response is lost after the receiver processed it). Receivers must deduplicate on `event_id`; the
  ID is stable across retries.
- Disabling or deleting a destination, removing a subscription or deleting a case stops pending
  deliveries. **Nothing already delivered can be recalled.**

Delivery history (status, attempts, last response code and error, never the response body) is on
the destination page.

## Restores

A restored database may contain enabled monitors. `scripts/restore.sh` pauses every monitor
(`python -m app.cli pause-monitors --reason restore`) before the stack starts, so restored monitors
never resume collection on their own; analysts review and resume them. Operators can run the same
command at any time (`--reason operator`).

## Verification

- Backend: `tests/test_monitoring_schedule.py` (time rules, DST), `tests/test_monitoring.py`
  (lifecycle, competing schedulers, downtime, overlap, re-checks, operator pause),
  `tests/test_change_detection.py`, `tests/test_notifications.py`, `tests/test_budgets.py`.
- Stack: `scripts/verify-phase5.sh` against a controlled feed and a local webhook receiver
  (see [docs/STATUS.md](../STATUS.md)). No real source or notification service is contacted.

## Configuration reference

Compose passes these to the backend services (set them in `.env`):
`TRACEHOLLOW_MONITOR_MIN_INTERVAL_MINUTES` (60), `TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED`
(false) and `TRACEHOLLOW_AUDIT_RETENTION_DAYS` (400). The other settings mentioned above keep their
defaults unless added to a Compose override: `TRACEHOLLOW_MONITOR_MISFIRE_GRACE_SECONDS` (300),
`TRACEHOLLOW_MONITOR_MAX_CONSECUTIVE_FAILURES` (5),
`TRACEHOLLOW_NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` (10),
`TRACEHOLLOW_NOTIFICATION_DELIVERY_MAX_ATTEMPTS` (5),
`TRACEHOLLOW_NOTIFICATION_RETENTION_DAYS` (180) and
`TRACEHOLLOW_MONITOR_OCCURRENCE_RETENTION_DAYS` (180).
