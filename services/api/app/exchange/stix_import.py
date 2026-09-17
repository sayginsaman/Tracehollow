"""Import the supported STIX 2.1 subset into a case (bounded, strict, idempotent).

* The bundle is stored unchanged as authorized-import evidence (JSON) with its origin, and every
  record created from it points to that evidence through an observation. Nothing becomes
  locally collected or verified: entities and relationships get the ``imported`` origin and
  relationships stay unreviewed.
* Limits: size (checked by the route), object count, JSON depth, string lengths, and a
  refusal of duplicate keys and non-finite numbers.
* Unsupported objects (indicators, malware, reports, person identities, custom objects, ...) are
  skipped and listed, or the whole bundle is refused with ``on_unsupported=reject``.
* Referenced URLs (external references, extension schemas) are recorded, never fetched.
* Idempotent: the same bytes are recognised by their SHA-256; objects are linked by STIX
  identifier, so a later bundle repeating an object reuses the record and adds provenance.
* An imported account matches an existing account only by the same stable platform ID on the
  same platform (the collection rule); nothing is merged on names, logins or other values.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai import indexing
from app.audit.service import Actor, record
from app.auth.models import User
from app.config import Settings
from app.db.base import utcnow
from app.entities import normalize
from app.entities.models import (
    Entity,
    EntityEvidence,
    EntityIdentifier,
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
    Stance,
)
from app.evidence import importing
from app.evidence.models import AcquisitionMethod, EvidenceKind, EvidenceObject
from app.evidence.service import find_duplicates, lock_case_for_write, to_out
from app.evidence.storage import EvidenceStorage
from app.exchange import stix
from app.exchange.models import StixObjectLink

logger = logging.getLogger(__name__)

MAX_OBJECTS = 2000
MAX_DEPTH = 32
MAX_STRING = 10_000
MAX_LISTED_SKIPS = 100
IMPORT_FORMAT = "stix_bundle"


class StixRejectedError(ValueError):
    def __init__(
        self, code: str, message: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass
class ImportSummary:
    evidence_id: uuid.UUID | None = None
    already_imported: bool = False
    bundle_id: str | None = None
    objects: int = 0
    created: Counter[str] = field(default_factory=Counter)
    reused: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    skipped_objects: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def skip(self, stix_id: str, object_type: str, reason: str) -> None:
        self.skipped[reason] += 1
        if len(self.skipped_objects) < MAX_LISTED_SKIPS:
            self.skipped_objects.append(
                {"id": stix_id[:128], "type": object_type[:64], "reason": reason}
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
            "already_imported": self.already_imported,
            "bundle_id": self.bundle_id,
            "objects": self.objects,
            "created": dict(self.created),
            "reused": dict(self.reused),
            "skipped": dict(self.skipped),
            "skipped_objects": self.skipped_objects,
            "warnings": self.warnings,
        }


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StixRejectedError("duplicate_key", f"The JSON repeats the key '{key[:64]}'.")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise StixRejectedError("invalid_json", f"The JSON contains the non-standard number {value}.")


def _check(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise StixRejectedError("json_too_deep", f"JSON nesting exceeds {MAX_DEPTH} levels.")
    if isinstance(value, str) and len(value) > MAX_STRING:
        raise StixRejectedError("string_too_long", f"A string exceeds {MAX_STRING} characters.")
    if isinstance(value, dict):
        for item in value.values():
            _check(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check(item, depth + 1)


def parse_bundle(content: bytes) -> dict[str, Any]:
    if not content:
        raise StixRejectedError("empty_content", "The uploaded file is empty.")
    try:
        text = content.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError:
        raise StixRejectedError("invalid_encoding", "A STIX bundle must be UTF-8 JSON.") from None
    try:
        document = json.loads(
            text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
    except RecursionError:
        raise StixRejectedError(
            "json_too_deep", f"JSON nesting exceeds {MAX_DEPTH} levels."
        ) from None
    except json.JSONDecodeError as exc:
        raise StixRejectedError(
            "invalid_json", f"Not valid JSON (line {exc.lineno}, column {exc.colno})."
        ) from None
    _check(document)
    if not isinstance(document, dict) or document.get("type") != "bundle":
        raise StixRejectedError("not_a_bundle", "The file must contain a STIX bundle object.")
    bundle_id = document.get("id")
    match = stix.OBJECT_ID.match(bundle_id) if isinstance(bundle_id, str) else None
    if match is None or match.group(1) != "bundle":
        raise StixRejectedError(
            "invalid_identifier", "The bundle identifier is not bundle--<UUID>."
        )
    objects = document.get("objects", [])
    if not isinstance(objects, list):
        raise StixRejectedError("invalid_bundle", "The bundle's objects must be a list.")
    if len(objects) > MAX_OBJECTS:
        raise StixRejectedError(
            "too_many_objects", f"A bundle may contain at most {MAX_OBJECTS} objects."
        )
    unexpected = sorted(set(document) - {"type", "id", "objects", "spec_version"})
    if unexpected:
        raise StixRejectedError(
            "invalid_bundle", f"Unexpected bundle properties: {', '.join(unexpected)}."
        )
    return document


@dataclass
class _Candidate:
    stix_id: str
    object_type: str
    body: dict[str, Any]


def _validate_object(raw: Any, index: int) -> _Candidate | tuple[str, str, str]:
    """A candidate, or (id, type, reason) when the object cannot be used at all."""
    if not isinstance(raw, dict):
        return (f"#{index}", "unknown", "not_an_object")
    object_type = raw.get("type")
    object_id = raw.get("id")
    if not isinstance(object_type, str) or not isinstance(object_id, str):
        return (str(object_id)[:128], str(object_type)[:64], "missing_type_or_id")
    match = stix.OBJECT_ID.match(object_id)
    if match is None or match.group(1) != object_type:
        return (object_id, object_type, "identifier_does_not_match_type")
    version = raw.get("spec_version", "2.1" if object_type in stix.SCO_TYPES else None)
    if version != "2.1":
        return (object_id, object_type, "unsupported_spec_version")
    if object_type not in stix.SUPPORTED_TYPES:
        return (object_id, object_type, "unsupported_type")
    if object_type not in stix.SCO_TYPES:
        for key in ("created", "modified"):
            if stix.parse_timestamp(raw.get(key)) is None:
                return (object_id, object_type, f"invalid_{key}")
    return _Candidate(stix_id=object_id, object_type=object_type, body=raw)


def _entity_draft(
    candidate: _Candidate,
) -> tuple[str, str, list[tuple[str, str | None, str]]] | str:
    """(entity type, display name, identifiers) or a skip reason."""
    body = candidate.body
    kind = candidate.object_type
    try:
        if kind in ("domain-name", "url", "email-addr", "ipv4-addr", "ipv6-addr"):
            value = body.get("value")
            if not isinstance(value, str) or not value.strip():
                return "missing_value"
            if kind == "domain-name":
                return "domain", normalize.normalize_domain(value), [("domain", None, value)]
            if kind == "url":
                return "url", value.strip()[:300], [("url", None, value)]
            if kind == "email-addr":
                return "email", normalize.normalize_email(value), [("email", None, value)]
            address = ipaddress.ip_address(value.strip())
            if (kind == "ipv4-addr") != isinstance(address, ipaddress.IPv4Address):
                return "address_family_mismatch"
            return "ip", address.compressed, [("ip", None, value)]
        if kind == "user-account":
            user_id = body.get("user_id")
            login = body.get("account_login")
            if not isinstance(user_id, str) and not isinstance(login, str):
                return "account_without_stable_identifier"
            platform = (
                body.get("account_type") if isinstance(body.get("account_type"), str) else None
            )
            identifiers: list[tuple[str, str | None, str]] = []
            if isinstance(user_id, str) and user_id.strip():
                identifiers.append(("platform_id" if platform else "other", platform, user_id))
            if isinstance(login, str) and login.strip():
                identifiers.append(("username", platform, login))
            name = body.get("display_name") if isinstance(body.get("display_name"), str) else None
            label = name or login or user_id
            return (
                "platform_account",
                f"{label} ({platform or 'unspecified platform'})"[:300],
                identifiers,
            )
        if kind == "identity":
            if body.get("identity_class") != "organization":
                return "identity_class_not_imported"
            name = body.get("name")
            if not isinstance(name, str) or not name.strip():
                return "missing_name"
            return "organization", name.strip()[:300], [("name", None, name.strip())]
    except normalize.IdentifierError:
        return "invalid_value"
    return "unsupported_type"


def _extension_claims(body: dict[str, Any]) -> dict[str, Any] | None:
    extensions = body.get("extensions")
    if not isinstance(extensions, dict):
        return None
    ours = extensions.get(stix.PROVENANCE_EXTENSION_ID)
    if not isinstance(ours, dict):
        return None
    return {
        key: ours[key]
        for key in ("origin", "review_status", "entity_type", "record_id")
        if key in ours
    }


def _visible_properties(body: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key in (
        "value",
        "user_id",
        "account_login",
        "account_type",
        "display_name",
        "name",
        "identity_class",
        "description",
        "relationship_type",
        "source_ref",
        "target_ref",
        "created",
        "modified",
        "first_observed",
        "last_observed",
        "number_observed",
        "start_time",
        "stop_time",
    ):
        if key in body and isinstance(body[key], str | int | float | bool):
            kept[key] = body[key]
    references = body.get("external_references")
    if isinstance(references, list):
        kept["external_references"] = [
            {
                k: v
                for k, v in ref.items()
                if k in ("source_name", "external_id", "url", "description") and isinstance(v, str)
            }
            for ref in references[:20]
            if isinstance(ref, dict)
        ]
    return kept


def import_bundle(
    db: Session,
    storage: EvidenceStorage,
    settings: Settings,
    *,
    case_id: uuid.UUID,
    user: User,
    actor: Actor,
    content: bytes,
    filename: str | None,
    import_origin: str,
    on_unsupported: Literal["skip", "reject"],
) -> ImportSummary:
    document = parse_bundle(content)
    summary = ImportSummary(bundle_id=str(document["id"]), objects=len(document["objects"]))
    candidates: list[_Candidate] = []
    seen: set[str] = set()
    for index, raw in enumerate(document["objects"]):
        checked = _validate_object(raw, index)
        if isinstance(checked, tuple):
            summary.skip(*checked)
            continue
        if checked.stix_id in seen:
            summary.skip(checked.stix_id, checked.object_type, "duplicate_identifier_in_bundle")
            continue
        seen.add(checked.stix_id)
        candidates.append(checked)
    blocking = {"unsupported_type", "unsupported_spec_version", "identity_class_not_imported"}
    if on_unsupported == "reject" and any(reason in blocking for reason in summary.skipped):
        raise StixRejectedError(
            "unsupported_content",
            "The bundle contains objects outside the supported subset; nothing was imported.",
            summary.skipped_objects,
        )
    if any(reason not in blocking for reason in summary.skipped):
        summary.warnings.append(
            "Some objects were malformed and were skipped; see skipped_objects."
        )

    case = lock_case_for_write(db, case_id)
    existing = db.scalar(
        select(EvidenceObject).where(
            EvidenceObject.case_id == case_id,
            EvidenceObject.sha256 == hashlib.sha256(content).hexdigest(),
            EvidenceObject.acquisition_method == AcquisitionMethod.AUTHORIZED_IMPORT,
            EvidenceObject.collection_metadata["import_format"].astext == IMPORT_FORMAT,
        )
    )
    if existing is not None:
        summary.already_imported = True
        summary.evidence_id = existing.id
        summary.warnings.append("This exact bundle was imported before; nothing new was created.")
        return summary

    sanitized = importing.sanitize_filename(filename)
    evidence_id = uuid.uuid4()
    key = EvidenceStorage.key_for(case_id, evidence_id)
    staged = storage.store(key, content)
    try:
        now = utcnow()
        evidence = EvidenceObject(
            id=evidence_id,
            case_id=case_id,
            kind=EvidenceKind.JSON,
            title=f"STIX bundle {document['id']}"[:300],
            original_filename=sanitized.value,
            content_type="application/stix+json",
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            storage_key=key,
            acquisition_method=AcquisitionMethod.AUTHORIZED_IMPORT,
            import_origin=import_origin,
            collected_at=now,
            imported_by_user_id=user.id,
            description=(
                "Imported STIX 2.1 bundle. Objects were created as imported records; they are "
                "claims of the producing tool and were not verified by Tracehollow."
            ),
            collection_metadata={
                "import_format": IMPORT_FORMAT,
                "bundle_id": str(document["id"])[:128],
                "objects": len(document["objects"]),
            },
        )
        db.add(evidence)
        db.flush()
        summary.evidence_id = evidence_id
        _create_records(db, case_id, evidence, candidates, summary, now)
        find_duplicates(db, case_id, staged.sha256, evidence_id)
        indexing.mark_evidence_for_indexing(
            db, settings, case_id=case_id, evidence_id=evidence_id, ai_mode=case.ai_mode
        )
        record(
            db,
            actor,
            "exchange.stix_imported",
            case_id=case_id,
            target_type="evidence",
            target_id=evidence_id,
            details={
                "bundle_id": str(document["id"]),
                "objects": len(document["objects"]),
                "created": dict(summary.created),
                "reused": dict(summary.reused),
                "skipped": dict(summary.skipped),
            },
        )
        db.commit()
    except BaseException:
        db.rollback()
        storage.remove_key(key)
        raise
    logger.info(
        "stix_bundle_imported", extra={"case_ref": str(case_id)[:8], "objects": summary.objects}
    )
    summary.warnings.append(
        "Imported records keep the 'imported' origin; relationships are unreviewed. Referenced "
        "URLs were recorded, not fetched."
    )
    return summary


def _link(db: Session, case_id: uuid.UUID, stix_id: str, record_type: str) -> uuid.UUID | None:
    return db.scalar(
        select(StixObjectLink.record_id).where(
            StixObjectLink.case_id == case_id,
            StixObjectLink.stix_id == stix_id,
            StixObjectLink.record_type == record_type,
        )
    )


def _observe(
    db: Session,
    case_id: uuid.UUID,
    evidence: EvidenceObject,
    candidate: _Candidate,
    entity_id: uuid.UUID | None,
    observation_type: str,
    now: Any,
) -> uuid.UUID | None:
    payload: dict[str, Any] = {
        "stix_type": candidate.object_type,
        "stix_id": candidate.stix_id,
        "properties": _visible_properties(candidate.body),
        "note": "Claimed by the producer of the imported STIX bundle; not verified by Tracehollow.",
    }
    claims = _extension_claims(candidate.body)
    if claims:
        payload["declared_by_producer"] = claims
    return db.scalar(
        insert(Observation)
        .values(
            id=uuid.uuid4(),
            case_id=case_id,
            entity_id=entity_id,
            evidence_id=evidence.id,
            observation_type=observation_type,
            source_object_id=candidate.stix_id,
            payload=payload,
            collected_at=now,
            idempotency_key=f"stix:{evidence.id}:{candidate.stix_id}"[:200],
        )
        .on_conflict_do_nothing(index_elements=["case_id", "idempotency_key"])
        .returning(Observation.id)
    )


def _create_records(
    db: Session,
    case_id: uuid.UUID,
    evidence: EvidenceObject,
    candidates: list[_Candidate],
    summary: ImportSummary,
    now: Any,
) -> None:
    stix_to_entity: dict[str, uuid.UUID] = {}
    for candidate in candidates:
        if candidate.object_type in ("relationship", "observed-data", "extension-definition"):
            continue
        linked = _link(db, case_id, candidate.stix_id, "entity")
        if linked is not None and db.get(Entity, linked) is not None:
            stix_to_entity[candidate.stix_id] = linked
            summary.reused["entity"] += 1
            _observe(db, case_id, evidence, candidate, linked, "stix_object", now)
            _link_evidence(db, case_id, linked, evidence.id)
            continue
        draft = _entity_draft(candidate)
        if isinstance(draft, str):
            summary.skip(candidate.stix_id, candidate.object_type, draft)
            continue
        entity_type, display_name, identifiers = draft
        entity_id = (
            _existing_account(db, case_id, identifiers)
            if entity_type == "platform_account"
            else None
        )
        if entity_id is not None:
            summary.reused["entity_by_platform_id"] += 1
        else:
            entity_id = uuid.uuid4()
            description = (
                candidate.body.get("description") if candidate.object_type == "identity" else None
            )
            db.add(
                Entity(
                    id=entity_id,
                    case_id=case_id,
                    entity_type=entity_type,
                    display_name=display_name,
                    description=(description if isinstance(description, str) else "")[:10_000],
                    attributes={"stix_id": candidate.stix_id, "imported": True},
                    origin=Origin.IMPORTED,
                )
            )
            db.flush()
            added: set[tuple[str, str | None, str]] = set()
            for identifier_type, platform, value in identifiers:
                try:
                    normalized = normalize.normalize_identifier(
                        IdentifierType(identifier_type), value
                    )
                except normalize.IdentifierError:
                    continue
                platform_key = normalize.normalize_platform(platform)
                if (identifier_type, platform_key, normalized) in added:
                    continue
                added.add((identifier_type, platform_key, normalized))
                db.add(
                    EntityIdentifier(
                        case_id=case_id,
                        entity_id=entity_id,
                        identifier_type=identifier_type,
                        platform=platform_key,
                        original_value=value[:2048],
                        normalized_value=normalized,
                    )
                )
            summary.created[entity_type] += 1
        db.add(
            StixObjectLink(
                case_id=case_id,
                stix_id=candidate.stix_id,
                record_type="entity",
                record_id=entity_id,
                first_evidence_id=evidence.id,
            )
        )
        db.flush()
        stix_to_entity[candidate.stix_id] = entity_id
        _observe(db, case_id, evidence, candidate, entity_id, "stix_object", now)
        _link_evidence(db, case_id, entity_id, evidence.id)

    for candidate in candidates:
        if candidate.object_type == "observed-data":
            refs = candidate.body.get("object_refs")
            if not isinstance(refs, list) or not refs:
                summary.skip(candidate.stix_id, candidate.object_type, "missing_object_refs")
                continue
            targets = [stix_to_entity.get(ref) for ref in refs if isinstance(ref, str)]
            if not any(targets):
                summary.skip(candidate.stix_id, candidate.object_type, "references_not_imported")
                continue
            for referenced in {t for t in targets if t}:
                _observe(db, case_id, evidence, candidate, referenced, "stix_object", now)
            summary.created["observed_data_claim"] += 1
        elif candidate.object_type == "extension-definition":
            summary.skip(
                candidate.stix_id, candidate.object_type, "extension_definition_recorded_only"
            )

    for candidate in candidates:
        if candidate.object_type != "relationship":
            continue
        body = candidate.body
        relationship_type = body.get("relationship_type")
        predicate = (
            stix.type_to_predicate(relationship_type)
            if isinstance(relationship_type, str)
            and stix.RELATIONSHIP_TYPE.match(relationship_type)
            else None
        )
        if predicate is None:
            summary.skip(candidate.stix_id, candidate.object_type, "invalid_relationship_type")
            continue
        source = stix_to_entity.get(str(body.get("source_ref")))
        target = stix_to_entity.get(str(body.get("target_ref")))
        if source is None or target is None:
            summary.skip(
                candidate.stix_id, candidate.object_type, "relationship_reference_not_imported"
            )
            continue
        if source == target:
            summary.skip(candidate.stix_id, candidate.object_type, "relationship_to_itself")
            continue
        relationship_id = _link(db, case_id, candidate.stix_id, "relationship")
        if relationship_id is not None and db.get(Relationship, relationship_id) is not None:
            summary.reused["relationship"] += 1
        else:
            relationship_id = uuid.uuid4()
            description = body.get("description")
            db.add(
                Relationship(
                    id=relationship_id,
                    case_id=case_id,
                    source_entity_id=source,
                    target_entity_id=target,
                    predicate=predicate,
                    origin=Origin.IMPORTED,
                    review_status=ReviewStatus.UNREVIEWED,
                    description=(description if isinstance(description, str) else "")[:10_000],
                    valid_from=stix.parse_timestamp(body.get("start_time")),
                    valid_to=stix.parse_timestamp(body.get("stop_time")),
                )
            )
            db.add(
                StixObjectLink(
                    case_id=case_id,
                    stix_id=candidate.stix_id,
                    record_type="relationship",
                    record_id=relationship_id,
                    first_evidence_id=evidence.id,
                )
            )
            db.flush()
            summary.created["relationship"] += 1
        observation_id = _observe(db, case_id, evidence, candidate, None, "stix_relationship", now)
        db.execute(
            insert(RelationshipEvidence)
            .values(
                id=uuid.uuid4(),
                case_id=case_id,
                relationship_id=relationship_id,
                evidence_id=evidence.id,
                observation_id=observation_id,
                stance=Stance.SUPPORTS,
                note="Stated in an imported STIX bundle (not verified).",
            )
            .on_conflict_do_nothing()
        )


def _link_evidence(
    db: Session, case_id: uuid.UUID, entity_id: uuid.UUID, evidence_id: uuid.UUID
) -> None:
    db.execute(
        insert(EntityEvidence)
        .values(id=uuid.uuid4(), case_id=case_id, entity_id=entity_id, evidence_id=evidence_id)
        .on_conflict_do_nothing(index_elements=["entity_id", "evidence_id"])
    )


def _existing_account(
    db: Session, case_id: uuid.UUID, identifiers: list[tuple[str, str | None, str]]
) -> uuid.UUID | None:
    """The account with the same stable platform ID on the same platform, if the case has one."""
    for identifier_type, platform, value in identifiers:
        if identifier_type != "platform_id" or not platform:
            continue
        try:
            normalized = normalize.normalize_identifier(IdentifierType.PLATFORM_ID, value)
        except normalize.IdentifierError:
            return None
        return db.scalar(
            select(EntityIdentifier.entity_id).where(
                EntityIdentifier.case_id == case_id,
                EntityIdentifier.identifier_type == IdentifierType.PLATFORM_ID,
                EntityIdentifier.platform == normalize.normalize_platform(platform),
                EntityIdentifier.normalized_value == normalized,
            )
        )
    return None


def import_result(summary: ImportSummary, db: Session) -> dict[str, Any]:
    result = summary.as_dict()
    if summary.evidence_id is not None:
        evidence = db.get(EvidenceObject, summary.evidence_id)
        if evidence is not None:
            result["evidence"] = to_out(evidence).model_dump(mode="json")
    return result
