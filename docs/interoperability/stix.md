# STIX 2.1 exchange

Tracehollow exports and imports **a documented subset of STIX 2.1**. It is not a complete STIX
implementation, not a threat intelligence platform, and not tested for compatibility with any
particular receiving tool. Everything outside the subset is either left out of exports (and counted
in the export report) or skipped and listed on import.

Reference: OASIS *STIX Version 2.1*, OASIS Standard, 10 June 2021
(`docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html`, read 2026-09-17). Exports are parsed with
the OASIS `stix2` Python library 3.0.2 with `allow_custom=False` in the test suite and in
`scripts/verify-phase5.sh`. Decision record: [ADR 0013](../adr/0013-stix-exchange-subset.md).
Code: `services/api/app/exchange/`.

## Support matrix

| Tracehollow record | STIX 2.1 object | Export | Import | Notes |
| --- | --- | :---: | :---: | --- |
| Domain entity | `domain-name` (SCO) | yes | yes | ID: UUIDv5 over the canonical `value` (spec section 2.9) |
| IP address entity | `ipv4-addr`, `ipv6-addr` (SCO) | yes | yes | family mismatches are skipped |
| URL entity | `url` (SCO) | yes | yes | never fetched on import |
| Email address entity | `email-addr` (SCO) | yes | yes | |
| Platform account | `user-account` (SCO) | yes | yes | `account_type` = platform, `user_id` = stable platform ID, `account_login` = username. **Deviation:** the ID includes the platform (see below) |
| Organization | `identity`, `identity_class: organization` | yes | yes | |
| Relationship | `relationship` | yes | yes | `relationship_type` is the predicate with `-` for `_` (`resolves_to` ↔ `resolves-to`); both ends must be exported/imported objects |
| Observation of a collected or imported record | `observed-data` | yes | as provenance only | carries collection times, counts and object references; on import it adds provenance to the objects it references and creates nothing else |
| Evidence reference | `external_references` entry `tracehollow-evidence` with SHA-256 | yes | kept on the stored bundle | source URLs only when **Include source URLs** is chosen |
| Tracehollow origin, review status, entity type, record ID, event and publication times | property extension `extension-definition--…` ("tracehollow-provenance") | yes | recorded as claims | an imported claim never makes a record verified or reviewed |
| Producer | `identity`, `identity_class: system` | yes | skipped | |
| Username without platform, phone, document, event, person | — | no (counted as excluded) | — | no faithful STIX equivalent in this subset |
| Person identities (`identity_class: individual`) | — | never | skipped | Tracehollow does not assert who a person is |
| Indicators, malware, threat actors, campaigns, attack patterns, reports, notes, opinions, sightings, grouping, locations, custom objects | — | never produced | skipped and listed, or bundle refused | |
| `confidence` | — | never produced | ignored | Tracehollow has no calibrated confidence to express |
| Marking definitions (TLP) | — | not produced | skipped | receivers must apply their own handling rules |

### Why `user-account` IDs include the platform

STIX derives deterministic `user-account` IDs from `user_id` and `account_login`. Two accounts with
the login `ornek-dev` on different platforms would then share one ID, and a receiving tool would
merge them into a single account. Tracehollow never merges accounts across platforms, so it derives
the ID from platform, login and platform ID in its own UUIDv5 namespace. This is a documented
deviation from a SHOULD in section 2.9; the objects remain valid STIX.

## Export

**Case settings → Export → STIX 2.1 bundle** (analysts), or
`GET /api/v1/cases/{id}/exports/stix` with the media type `application/stix+json;version=2.1`.

Options:

| Option | Default | Effect |
| --- | --- | --- |
| `include_unreviewed` | true | unreviewed relationships are exported and marked unreviewed in the extension |
| `include_source_urls` | false | evidence references include source URLs (which can reveal what was searched) |
| `include_ai_suggestions` | false | AI-suggested relationships are exported only when accepted by an analyst and this is on |

Always excluded: rejected and superseded relationships, analyst notes, AI answers and summaries,
evidence contents, comparison results, credentials. Values matching configured application secrets
or credential patterns are replaced before the bundle leaves the server, and the download is refused
if a secret would still be present. At most 5,000 objects; larger cases produce a truncated bundle
and say so.

`GET …/exports/stix/report` returns what would be exported, what is excluded and why, and what is
lost (below). Exports are audited (`export.downloaded` with format and counts).

### What is lost in STIX

- Entity types without an equivalent (username without platform, phone, document, event).
- Analyst notes, AI output, evidence contents and comparison results.
- Event and publication times of observations exist only inside the Tracehollow extension;
  `observed-data` carries collection times.
- Receivers that ignore the Tracehollow extension lose origin and review status, so an unreviewed
  relationship looks like any other relationship to them.

## Import

**Imports → STIX bundle** (analysts in an active case), or `POST /api/v1/cases/{id}/imports/stix`
(multipart: `file`, `import_origin`, optional `on_unsupported=skip|reject`).

- **Bounded and strict.** At most 5 MiB (`TRACEHOLLOW_STIX_IMPORT_MAX_BYTES`), 2,000 objects, JSON
  nesting depth 32 and strings of 10,000 characters. Invalid UTF-8 or JSON, duplicate keys,
  `NaN`/`Infinity`, and files that are not a bundle are refused with 422 and a reason; oversize with
  413. Refusals are audited (`exchange.stix_rejected`).
- **Nothing is fetched.** URLs in objects, external references and extension definitions are
  recorded as text.
- **The bundle is evidence.** It is stored unchanged as an authorized import with its SHA-256 and the
  origin statement the analyst gave, and indexed like other imports.
- **Imported, not verified.** Entities and relationships get the origin *imported*; relationships
  start *unreviewed*; every created or reused record is linked to the bundle through an
  observation. Extension claims such as "verified" are kept as claims.
- **No identity merging.** An imported account reuses an existing account only when both have the
  same stable platform ID on the same platform. Names, logins, emails and display names never merge
  records.
- **Unsupported content** is skipped and listed (`skipped_objects` with a reason each), or, with
  `on_unsupported=reject`, the whole bundle is refused and nothing is written.
- **Idempotent.** The same bytes are recognized by hash (`already_imported: true`); objects are
  linked by STIX ID, so a later bundle that repeats an object reuses the record instead of duplicating
  it.
- Viewers cannot import; the case must be active.

## Round trips

A case exported, imported into another case and exported again keeps the supported observables
with identical STIX IDs, organizations, supported relationships and their types. Verified in
`services/api/tests/test_stix_exchange.py` and against the running stack. Not preserved: everything
listed under *What is lost*, Tracehollow record IDs (new records are created) and review decisions
(imported relationships are unreviewed again).

## MISP and OpenCTI

**Deferred, not implemented.** The PRD lists MISP and OpenCTI adapters as optional. They would need
live instances, API credentials, a mapping review against each platform's data model (MISP
attributes and objects, OpenCTI's STIX-based schema and connectors) and an authorization to send case
data to them, none of which exist for this phase. The STIX bundle is the supported exchange format;
importing it into those platforms has not been tested and no compatibility is claimed.
