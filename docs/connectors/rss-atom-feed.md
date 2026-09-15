# RSS / Atom feed (`rss.feed` 1.0.0)

Reads a public RSS 2.0, RSS 1.0 (RDF) or Atom 1.0 feed.

| | |
| --- | --- |
| Mode | Direct request: the feed's server sees the requests |
| Input | Absolute `http`/`https` URL of the feed |
| Parameters | none (saved-query limits set pages and entries per page) |
| Credentials | none |
| Limits | up to 10 pages, 100 entries per page, 120 s run timeout, 5 MiB per document, 4 concurrent runs, 2 s between requests to one host |
| Retries | 3 attempts for `unavailable` and `rate_limited` |
| Cache | none; entries repeated on later pages of one execution are stored once |
| Output schema | `tracehollow.feed.entries/v1` |
| Live verification | not performed |

## What is stored

- **Snapshot** evidence (`xml`): each feed document byte-exact, with HTTP provenance.
- **Entries** evidence (`json`), derived: feed title, site link, feed ID, updated time and for every
  entry its ID, where the ID came from (`guid`, `atom_id`, `rdf_about`, `link` or a content hash
  when the feed gives none), title, link, publication time (UTC) and the original date text,
  updated time, author, categories and summary text (HTML reduced to text). Indexed for AI answers.
- Observations: one `feed` (with the feed URL entity) and one `feed_entry` per entry, with
  publication time stored as source publication time. Entry pages are not retrieved.

## Pagination

Feeds that publish RFC 5005 `next` links (`atom:link rel="next"`) are followed page by page up to
the page limit. Next-page links and redirects must stay on the feed's scheme, host and port;
otherwise the run stops (`partial` if pages were already collected). Reaching the page limit while
a next link exists is `partial`.

## Outcomes

| Situation | Outcome |
| --- | --- |
| Feed parsed with entries | `findings` |
| Feed parsed, no entries, no further pages | `no_findings` |
| HTTP 404/410 for the feed URL | `no_findings` (no feed at that address at retrieval time) |
| Later page fails after retries | `partial` (earlier pages kept) |
| More entries on a page than the per-page limit | `partial` with the number not stored |
| Malformed XML, entity declarations or external entities, non-feed document, truncated feed | `parse_error` |
| HTTP 401 / 403 / 429 / 5xx, timeouts | as for web pages |
| Destination refused | `unsupported` with the network-policy code |

Dates without a time-zone offset are kept as text only (not placed on the UTC timeline).

## Tests

`tests/test_connector_contracts.py` (RSS 2.0 with pagination cursor and unparseable dates, Atom,
repeated entries across pages, empty feed, malformed XML, entity-expansion and external-entity
documents, HTML instead of a feed, 404, 429, cross-origin pagination), `tests/test_collection.py`
(deduplication and a failing third page), `scripts/verify-phase2.sh`.

## Live verification log

None.
