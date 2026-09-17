# ADR 0011: Durable monitoring, shared budgets and evidence-based change detection

- Status: accepted
- Date: 2026-09-17
- Phase: 5

## Context

Phase 5 adds scheduled reruns of saved queries with bounded budgets and change detection. The
existing execution pipeline (PostgreSQL state, transactional outbox, dispatcher relay, leases,
retries, cancellation, immutable parameter snapshots) must stay the only way work runs. Schedules
must survive restarts, multiple schedulers and redelivery without duplicate dispatch or catch-up
bursts; budgets must hold across concurrent workers and crashes; a partial collection must never be
reported as a deletion.

## Decision

1. **Scheduling in PostgreSQL, run by the dispatcher.** No separate scheduler service or Celery
   beat. Each dispatcher cycle selects due monitors and handles each under
   `SELECT … FOR UPDATE SKIP LOCKED`. `monitor_occurrences` is unique on `(monitor_id,
   scheduled_for)`; the occurrence, the execution (`queries.create_run` with the monitor's scope
   and limits in the snapshot) and its outbox row commit in one transaction. The dispatcher
   publishes it like any other execution.
2. **Explicit time rules.** Interval schedules are elapsed UTC time from an anchor; daily and weekly
   schedules are wall-clock times in an IANA zone (a skipped local time runs that much later, a
   repeated one runs at its first instant). A floor (`monitor_min_interval_minutes`) bounds the
   frequency.
3. **Downtime and overlap.** After downtime one execution runs for the latest missed time with the
   number of missed times recorded (`run_latest`), or the time is recorded as missed (`skip`). A
   slot that overlaps a queued or running execution is a skipped occurrence. Resuming schedules from
   the next future time.
4. **States.** Created paused; enabling live collection requires an explicit acknowledgement.
   Pause stops future occurrences; disable also cancels the active execution; cancel acts on one
   execution. Editing the saved query pauses its monitors (`query_changed`) because the pinned query
   fingerprint no longer matches; resuming adopts the change and starts a new baseline. Dispatch
   re-checks the case, the authorizing analyst, the fingerprint, the definition, overlap and budgets.
5. **Budgets as ledgers with reservations.** Ledgers per scope (case, monitor, execution), metric
   (requests, documented provider units) and UTC period. Every outbound request reserves its units on
   all applicable ledgers with one conditional `UPDATE … WHERE reserved + consumed + estimated + n
   <= limit` per ledger in one transaction and is settled after the response (measured) or released
   when not sent. Engines that run in their own process reserve documented estimates. Reservations
   expire after the connector timeout plus a margin and are then counted as estimated use. A refused
   reservation stops the execution with `budget_exhausted`.
6. **Change detection is deterministic and evidence-linked.** A `change_detection` outbox job
   compares each connector run with its baseline: the latest earlier complete run with the same
   fingerprint (connector version, input, parameters, scope), falling back to the latest usable run.
   Items are keyed by observation type and source object ID; volatile and counter fields are
   excluded using the comparison helpers shared with entity comparison. Classes: new, changed, not
   observed (only after a complete collection), conflicting, unknown, no meaningful change;
   incompatible baselines are reported, not compared. Events reference both runs, observations and
   evidence. No model, no merging.

## Alternatives considered

- **Celery beat or a cron container:** state outside PostgreSQL, duplicate dispatch across
  replicas, and no transactional link between the schedule and the execution. Rejected.
- **Counting budgets in Redis:** fast, but not authoritative and lost on restart. Rejected.
- **Comparing with the immediately previous run:** simple, but a partial run would become the
  reference and make existing items look new. Replaced by the complete-baseline rule during
  verification.

## Consequences

- Monitoring works with any number of dispatchers; scheduling precision is the dispatcher poll
  interval (2 seconds by default) plus queueing.
- A monitor can use at most its per-run limits per slot and its budget per period, and the case
  budget caps all collection in the case.
- Interrupted requests can make an execution consume more than an uninterrupted one; the extra use
  is shown as estimated.
- Semantics are documented in `docs/monitoring/README.md` and `docs/monitoring/budgets.md`.
