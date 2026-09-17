# ADR 0009: Capability-based social platform connectors

- Status: accepted
- Date: 2026-09-16
- Phase: 4

## Context

Instagram, Telegram and YouTube offer very different access: official APIs with narrow coverage,
undocumented public pages, unofficial clients that need logged-in sessions, and paid third-party
providers. The PRD forbids advertising unrestricted personal-account or private-profile access,
hidden paid requests, automatic cookie extraction, placeholder success and joining private groups.
It requires the existing network policy to apply to all real traffic, including SDKs and
subprocesses.

## Decision

1. **Capability model.** `ConnectorDescriptor.capabilities` lists every access method considered
   for a platform as a `CapabilitySpec`: status (`implemented`, `not_implemented`, `excluded`),
   access method (`official_api`, `public_web_unofficial`, `unofficial_client`,
   `third_party_provider`), provider, account and content types, returned and never-returned
   fields, stable identifiers, pagination and coverage, session requirements, restrictions, cost or
   quota, verification status, credential names and the reason when not implemented.
2. **Only implemented capabilities are selectable.** A connector with capabilities takes a
   `capability` choice parameter whose choices are the implemented ones; saving a query with any
   other value fails validation. The collection mode (and therefore the evidence label and the
   mixing rule) follows the selected capability.
3. **Availability is reported, not assumed.** The Sources API marks each capability as available
   or blocked with a reason (missing credentials, disabled by configuration, no adapter). Missing
   credentials at run time end as `authentication_required` with `credential_not_configured` and
   store nothing, so missing API access blocks live verification without faking a status.
4. **HTTP only, through the guarded fetch.** The social connectors use `app.connectors.http.fetch`
   and no platform SDK, client library or subprocess. A contract test fails if their modules import
   another network client.
5. **Implemented capabilities:**
   - Instagram: official Graph API Business Discovery (professional accounts; token in the
     `Authorization` header); unofficial public profile page metadata, disabled unless
     `TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true`, with login walls reported as
     `authentication_required` / `instagram_login_wall`.
   - Telegram: public channel web preview (`t.me/s/…`) with pagination and gap reporting; Bot API
     `getChat` and `getChatMemberCount` with the token removed from recorded provenance.
   - YouTube: Data API v3 channel uploads, and video plus top-level comments, with quota tracking.
6. **Not implemented or excluded, with reasons shown to analysts:** Instagram session-based
   clients (cookie reuse, own HTTP stack) and third-party providers (paid, licence review);
   private or unrestricted personal-account access (excluded); Telegram MTProto user sessions (raw
   TCP outside the network policy; a user session exceeds the selected source); joining private
   groups or messaging (excluded); YouTube caption downloads (OAuth by the video's editor) and
   unofficial transcript endpoints.
7. **Uncertain states stay distinct:** an Instagram username the API does not return is
   `unsupported` / `instagram_account_not_discoverable`, not `no_findings`; a Telegram channel
   without a preview is `access_denied`; missing post numbers are "not visible", not deleted;
   disabled YouTube comments are a coverage state, not an error or absence; exhausted YouTube
   quota is `rate_limited` until midnight Pacific Time.

## Consequences

- All three connectors are `fixture_tested`; none is live-verified. Live verification needs
  credentials and explicit authorization (docs/connectors/live-smoke.md).
- Undocumented page layouts can change; the parsers then report `parse_error` instead of returning
  empty results.
- Instagram's Business Discovery error subcode `2207013` is taken from developer reports, not from
  Meta's error reference; the adapter says so in the error detail.

## Alternatives considered

- *Instaloader / Telethon:* both bypass the HTTP egress controls and need logged-in sessions;
  rejected for the default build (documented as not implemented).
- *A generic "Instagram connector" with a free-form mode:* rejected; it would hide which access
  method produced a result.
