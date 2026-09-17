# ADR 0013: A documented STIX 2.1 subset without attribution or identity merging

- Status: accepted
- Date: 2026-09-17
- Phase: 5

## Context

The PRD asks for a documented STIX 2.1 subset and optional MISP/OpenCTI adapters, with round-trip
tests, explicit unsupported mappings and no claim of full compatibility. Tracehollow's data model is
evidence, observations, entities and reviewed relationships with provenance; it has no calibrated
confidence and must not turn candidate account matches into identities or invent attribution.

## Decision

1. **Subset.** Export and import `domain-name`, `ipv4-addr`, `ipv6-addr`, `url`, `email-addr` and
   `user-account` SCOs, organization `identity` objects and `relationship` objects between them;
   export `observed-data` for records Tracehollow collected or imported and a producer identity.
   Nothing else is produced; nothing else is imported.
2. **Identifiers.** SCO IDs follow section 2.9 (UUIDv5 in the STIX namespace over the canonical
   contributing properties). `user-account` IDs include the platform in a Tracehollow namespace so
   the same login on two platforms never becomes one account (a documented deviation from a
   SHOULD). SDO and SRO IDs are stable UUIDv5 values per case record, and imported IDs are reused on
   re-export through `stix_object_links`.
3. **Provenance extension.** Origin, review status, entity type, record ID and event times travel
   in a declared property extension; evidence travels as external references with SHA-256. No
   `confidence` values.
4. **Import is untrusted input.** Size, object count, depth and string limits; strict JSON (no
   duplicate keys or non-finite numbers); no URL fetching; the bundle stored as authorized-import
   evidence; entities and relationships get the `imported` origin and relationships stay
   unreviewed; accounts match only on the same platform ID and platform; unsupported objects are
   skipped and listed or refuse the bundle; imports are idempotent by hash and STIX ID.
5. **Validation.** Tests parse exports with the OASIS `stix2` library (`allow_custom=False`) and
   test round trips; `stix2-validator` was evaluated but its current wheel lacks its JSON schemas.
6. **MISP and OpenCTI are deferred.** No instances, credentials or authorization were available to
   design and verify adapters; the STIX bundle is the exchange path and no compatibility with those
   platforms is claimed.

## Consequences

- Receiving tools that ignore extensions lose origin and review status; the export report lists
  this and other losses.
- Imported data is visible as imported everywhere (badges, exports, AI provenance) and never counts
  as collected evidence.
- `docs/interoperability/stix.md` holds the support matrix.
