# Timeline, entity comparison and HTML reports

These views read the existing records (observations, entities, identifiers, relationships,
evidence and collection runs). They do not create a separate data model and never change records.

## Timeline

**Case → Timeline** or `GET /api/v1/cases/{case}/timeline?section=…` (filters: `entity_id`,
`evidence_id`, `observation_type`, `from`/`to` with a timezone offset, `limit`, `offset`).

| Section | Contains | Ordered by |
| --- | --- | --- |
| On the UTC timeline (`dated`) | observations with an event time, or a publication time the source reported | that instant |
| Local time only (`local_time_only`) | wall-clock times without a timezone (for example a chat imported with an unknown timezone) | the local time |
| Collection time only (`undated`) | only the retrieval or import time is known | collection time |

Every item states its **time basis** (event time, published per source, local time with unknown
timezone, collected), the timestamp as written when available, the evidence and its acquisition
method, the source location (for chats, the line) and notes such as "publication time does not
establish when the event happened" or daylight-saving ambiguity.

## Entity comparison

**Case → Compare** or `GET /api/v1/cases/{case}/entity-comparison?entity_id=…&entity_id=…`
(2-4 entities). The response and view show:

- **Identifiers:** shared by several entities (a lead to review), held by one only, and different
  stable platform IDs on the same platform (different accounts even when names match).
- **Relationships** between the compared entities and neighbours they share, with origin and
  review status.
- **Source coverage** per entity: which connectors and imports produced its observations, with
  run outcomes and stop reasons; observation date spans (event, publication, collection).
- **Changes over time:** fields whose value differs between successive collections of the same
  source object. The change happened at an unknown time between those collections; collection and
  publication dates do not date it.
- **Items not seen in a later collection:** reported as *not observed in a later complete
  collection* (not proof of deletion) or, when the later collection failed, was partial or stopped
  early, as **unknown**.
- **Conflicts:** different values for the same field from different sources (neither is preferred),
  contradicting evidence on relationships, and different stable IDs.
- **Unresolved questions:** unreviewed relationships and shared identifiers without an accepted
  relationship.

Entities are never merged automatically. An analyst records a conclusion as a reviewed relationship.

## HTML reports

**Case → Reports**, or `POST /api/v1/cases/{case}/reports/html/preview` (JSON with the markup, counts,
redactions and warnings) and `POST …/reports/html` (download). `GET …/reports/selectable` lists
candidates.

1. **Select** what to include: case purpose and scope, entities, relationships (with their
   references), evidence excerpts, an entity comparison, AI-generated answers with their citations,
   analyst notes, the timeline and collection coverage. Nothing else is included.
2. **Redact:** terms (case-insensitive) and every known value of chosen identifier types (for
   example phone numbers). Strings shaped like API keys, bearer tokens, bot tokens and secret query
   parameters are always removed. Reports never read stored credentials or sessions.
3. **Preview** the exact file in a sandboxed frame (scripts disabled), then **download**.

The file is self-contained and inert:

- All case text is escaped; evidence markup, filenames, notes and model output are shown as text.
- No scripts, event handlers, external stylesheets, fonts or images; a Content-Security-Policy
  forbids loading anything, so opening the file makes no network request.
- **Citations work offline:** they link to bundled evidence excerpts inside the file (with evidence
  ID, SHA-256, acquisition, collection and publication dates). External originals appear as plain
  links labelled *not bundled; may have changed or be unavailable*. No localhost or session URL is
  used as a citation.
- **Uncertainty is kept:** observed / analyst assertion / AI-generated labels, review status, AI
  claim kinds, limitations and removed claims, comparison conflicts, changes, absences, unresolved
  questions, OCR warnings and a table of failed, partial or blocked collections and processing jobs.

Reports are generated on demand and not stored or published. PDF export is not implemented; the
file prints cleanly from a browser.

Tests: `tests/test_timeline_comparison.py`, `tests/test_reports.py`,
`apps/web/src/components/cases/phase4-views.test.tsx`, `scripts/verify-phase4.sh`.
