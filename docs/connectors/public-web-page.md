# Public web page (`public_web.page` 1.0.0)

Retrieves one public web page and keeps the byte-exact response plus its readable text.

| | |
| --- | --- |
| Mode | Direct request: the page's server sees the request |
| Input | Absolute `http`/`https` URL (no embedded credentials, at most 2048 characters) |
| Parameters | none |
| Credentials | none |
| Limits | 1 page, 60 s run timeout, 20 s per request, 5 MiB response, 5 redirects, 4 concurrent runs, 2 s between requests to one host |
| Retries | 3 attempts for `unavailable` and `rate_limited` (2 s backoff, doubling, at most 20 s) |
| Cache | none |
| Output schema | `tracehollow.web.page/v1` |
| Live verification | live verified 2026-09-15 (one authorized page, see [live-smoke.md](live-smoke.md)) |

## What is stored

- **Snapshot** evidence (`html` or `text` kind): the response body exactly as received, with
  requested and final URL, redirect chain, HTTP status, selected response headers (content type,
  length, ETag, Last-Modified, Date, Server), the connected address, truncation flag, timing and
  the character set used for decoding. Shown only as inert text.
- **Text** evidence (`text`, UTF-8), derived from the snapshot: title and visible text; scripts,
  styles, templates and embedded SVG are removed. This record is indexed for AI answers.
- Observation `web_page`: final URL, status, title, description, language, canonical URL,
  published time (from page metadata), content type, size, truncation, text length and up to 50
  links.
- Entities: the final URL (`url`) and, when the host is a domain name, the domain, linked by a
  derived `hosted_on` relationship. Publication time from page metadata is recorded as source
  publication time, separate from collection time.

## Outcomes

| Situation | Outcome |
| --- | --- |
| HTML or plain text retrieved | `findings` |
| Response larger than the limit | `partial` (truncated snapshot kept) |
| HTTP 404 or 410 | `no_findings`, with the error body kept (not indexed) and a note that this does not prove the content never existed |
| HTTP 401 / 403 / 429 | `authentication_required` / `access_denied` / `rate_limited` (with `Retry-After`) |
| HTTP 5xx, 408, timeout, connection or DNS failure | `unavailable` (retried) |
| Other content types (PDF, images, …) | `unsupported` (nothing stored; document processing is Phase 4) |
| Destination or redirect refused by the network policy | `unsupported` with `blocked_address`, `blocked_host`, `blocked_port` or `blocked_scheme` |

## Coverage and limitations

The page is not rendered: content added by JavaScript, pages behind logins or bot challenges, and
cookie consent walls are captured only as served. One URL per run; linked pages are not followed.

## Tests

`tests/test_connector_contracts.py` (success with redirect, legacy Turkish charset, 404, 401, 403,
429, 503, timeout, unsupported type, truncation, private destination, input validation),
`tests/test_collection.py` (persistence, derived text, indexing, preview, routing, single-label
hosts), `tests/test_netguard.py`, and `scripts/verify-phase2.sh` (redirect, 404, 403, truncation,
metadata redirect and eight refused destinations inside the running collector, browser workflow).

## Live verification log

| Date | Version | Target category | Outcome | Reviewer |
| --- | --- | --- | --- | --- |
| 2026-09-15 | 1.0.0 | IANA documentation domain (`https://example.com/`) | `findings`: HTTP 200, no redirects, snapshot and derived text, connected address recorded | implementing assistant; authorized by the repository owner ([record](live-smoke/2026-09-15-results.json)) |

Scope: one HTTPS page without redirects. Redirect chains, legacy charsets and error statuses on live sites are covered by fixtures only.
