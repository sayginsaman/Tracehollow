# ADR 0012: In-app notifications and an optional, redacted webhook adapter

- Status: accepted
- Date: 2026-09-17
- Phase: 5

## Context

Monitors must notify case members about meaningful changes, failures that need action and
exhausted budgets, without repetition, with read state and links, and without secrets or
unnecessary evidence. An external adapter is optional; it must be off until configured, send only
to user-selected destinations with minimal content, protect destination secrets, apply SSRF
controls, timeouts, rate limits and retries, and re-check authorization before sending. Real
external notifications must not be sent without authorization for the destination and payload.

## Decision

1. **In-app first.** Notifications are rows per recipient, unique on `(user_id, dedupe_key)`, with a
   stable `event_id` (UUIDv5 of case and key). Emission happens in the transaction that decides the
   event, so retried jobs and competing schedulers produce one row. Failure notifications fire only
   when the failure signature changes. Lists and opening filter by current membership.
2. **Webhook adapter off by default.** `TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED=false` blocks
   creating, enabling and delivering. Administrators create destinations disabled, with an HMAC
   signing secret shown once and stored with the credential encryption key; the URL must pass the
   collection network policy (and may not carry credentials, a query or a fragment).
3. **Explicit authorization to send.** Enabling requires the administrator to type the receiver's
   host (`confirm_host`) after a payload preview is available; analysts subscribe monitors to
   enabled destinations and event types. The typed host and the subscription are the recorded
   authorization for that destination and payload type.
4. **Minimal payload with an allowlist.** Identifiers, event type, severity, time, change counts and
   reason codes (`tracehollow.notification/v1`). Names, titles, inputs, collected values, evidence,
   AI text and usernames are never included. Every attempt validates the key allowlist and scans for
   configured secrets and the signing secret; a failed check blocks delivery.
5. **Delivery in the collector.** Deliveries are durable rows dispatched through the outbox to the
   collector queue (the only service with outbound network access), posted with `netguard.post`
   (address checked at connect time, no redirects, bounded response). Before each attempt:
   adapter on, destination enabled, event type allowed, subscription present, case active or
   archived, subscriber still an analyst. Pacing per destination, retries for 408/425/429/5xx and
   network errors with exponential backoff honouring `Retry-After`, a maximum attempt count, a
   stable `x-tracehollow-event-id`, and HMAC-SHA256 over `timestamp.body`.
6. **At-least-once.** A lost response can repeat a delivery; receivers deduplicate on the event ID.
   This is documented rather than hidden.

## Consequences

- The verification stack enables the adapter only inside its isolated project and posts to a local
  fixture receiver; no real notification service was contacted.
- Operators who want external notifications must turn on the adapter, which is visible on the
  destinations page, and every destination change is audited.
- Nothing already delivered can be recalled; deletion and retention documentation says so.
