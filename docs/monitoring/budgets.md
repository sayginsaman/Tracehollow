# Budgets: reservations, measurement and cancellation

Budgets cap how much work Tracehollow asks of external sources. They apply to every execution in a
case (manual runs and monitor runs alike) and are enforced by PostgreSQL, so concurrent workers,
retries and restarts cannot overspend them. Implementation: `services/api/app/budgets/service.py`;
decision record: [ADR 0011](../adr/0011-durable-monitoring-budgets-and-change-detection.md).

**Budgets are request counts and documented quota units, not money.** Tracehollow does not know
what a provider bills and makes no financial promise. A provider's own quota or bill can differ.

## Which limits apply

| Scope | Set where | Metrics | Period |
| --- | --- | --- | --- |
| Case | Case settings → Collection budget (analysts) | requests, provider units | UTC day, ISO week (Monday), calendar month; up to six limits |
| Monitor | Monitor → Budget | requests, optional provider units | UTC day, ISO week or month |
| Execution | saved query limit `max_requests`, or the monitor's *requests per run* | requests | the execution |

A request is sent only if **every** applicable limit has room. The case budget is shared by all
monitors and manual runs in the case.

## What is counted

| Work | Counted as | How |
| --- | --- | --- |
| HTTP request sent by a connector through Tracehollow's fetch client, including retries, pagination and failed requests | 1 request, **measured** | reserved before sending, settled after the response or failure |
| Request refused by the network policy before sending | nothing | the reservation is released |
| Documented quota cost (YouTube Data API: 1 unit per list call) | provider units, **estimated** from the documentation | reserved and settled with the request |
| Username discovery (Sherlock) page | one request per selected platform, **estimated** | the engine runs in its own process; Tracehollow cannot count its requests |
| Domain discovery (Subfinder) run | five requests per selected source, **estimated** | runs in the network sandbox; providers that paginate may use more |
| Request whose worker crashed | the reserved units, **estimated** | the dispatcher expires the reservation after the connector timeout plus 60 seconds; the request may have reached the source |

Usage shows *consumed* (measured), *estimated*, *in flight* (reserved), *remaining* and the number
of requests refused in the period. The interface says "estimated" wherever a number is not a
measurement.

## Atomic reservation

Each ledger row (scope, metric, period) is changed only by

```sql
UPDATE budget_ledgers
   SET reserved_units = reserved_units + :units
 WHERE id = :ledger
   AND reserved_units + consumed_units + estimated_units + :units <= limit_units
```

for all applicable ledgers in one transaction. If any update matches no row, the transaction rolls
back, the refusal is counted and the request is not sent. Two workers competing for the last unit
serialize on the row; one gets it. `scripts/verify-phase5.sh` runs four executions concurrently
against a five-request case budget and checks the ledger never exceeds its limit.

## When a budget is used up

- **Running executions** stop before their next request with `error_code = budget_exhausted`. They
  finish as *partial* (something was collected) or *failed* (nothing was). Collected pages stay.
  Change detection treats them as incomplete: items not reached are *unknown*, never *no longer
  observed*.
- **Scheduled times** are skipped with the reason `budget_exhausted`; **Run now** is refused with 409.
- Monitor notifications: **Budget used up** at most once per monitor and budget period.
- Nothing retries automatically in the same period; the next period starts at midnight UTC (day),
  Monday 00:00 UTC (week) or the first of the month.
- Raising a limit takes effect for the next request. Lowering it below current use refuses new
  requests until the next period; nothing already consumed is undone.

## Cancellation and the in-flight boundary

Cancelling an execution (or disabling its monitor) sets a cancellation flag that the worker checks
before every request and between pages:

- **No new request** starts after the flag is seen.
- **A request already sent** completes or times out (at most the connector's request timeout); its
  units are settled as measured. Whether that last response is stored as a page depends on whether
  the worker saw the flag before writing it; either way the execution records what it kept.
- **Pages collected before cancellation** are kept with their provenance; the execution ends as
  *canceled* and its reservations are settled or released.
- A worker that dies mid-request loses its lease; the execution is claimed again (or failed after
  `TRACEHOLLOW_RUN_MAX_CLAIMS` attempts), and the lost reservation is counted as estimated use.

Pause is not cancellation: pausing a monitor stops future runs only.

## Crash recovery

- Reservations carry an expiry. The dispatcher expires held reservations whose worker disappeared
  and moves their units to *estimated*; nothing stays "in flight" forever.
- A recovered execution re-requests the page it had not finished. The retry is a new request and is
  counted again, which is why interrupted runs can use more than an uninterrupted one.
- Evidence and observations are written idempotently, so recovery does not duplicate them
  (verified by killing the collector mid-request in `scripts/verify-phase5.sh`).
