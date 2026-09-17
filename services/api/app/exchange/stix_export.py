"""Build a STIX 2.1 bundle from a case (supported subset only; see app.exchange.stix)."""

from __future__ import annotations

import ipaddress
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.models import Case
from app.entities.models import (
    Entity,
    EntityIdentifier,
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
)
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.exchange import stix
from app.exchange.models import StixObjectLink
from app.reports.redaction import Redactor

MAX_OBJECTS = 5000
MAX_REFERENCES_PER_OBJECT = 25


@dataclass
class ExportOptions:
    include_source_urls: bool = False
    include_unreviewed: bool = True
    include_ai_suggestions: bool = False


@dataclass
class ExportReport:
    counts: Counter[str] = field(default_factory=Counter)
    excluded: Counter[str] = field(default_factory=Counter)
    credential_values_removed: int = 0
    truncated: bool = False
    lossy: list[str] = field(
        default_factory=lambda: [
            "Entity types without a STIX 2.1 equivalent (username without platform, phone, "
            "document, event) are not exported.",
            "Analyst notes, AI answers and summaries, evidence contents and comparison results "
            "are not exported.",
            "Event and publication times of observations are kept only inside the Tracehollow "
            "provenance extension; observed-data carries collection times.",
            "Receivers that ignore the provenance extension lose origin and review status.",
        ]
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "excluded": dict(self.excluded),
            "credential_values_removed": self.credential_values_removed,
            "truncated": self.truncated,
            "lossy": self.lossy,
        }


def _extension(**properties: Any) -> dict[str, Any]:
    return {
        stix.PROVENANCE_EXTENSION_ID: {
            "extension_type": "property-extension",
            **{key: value for key, value in properties.items() if value not in (None, [], {})},
        }
    }


def _evidence_reference(evidence: EvidenceObject, options: ExportOptions) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "source_name": "tracehollow-evidence",
        "external_id": str(evidence.id),
        "hashes": {"SHA-256": evidence.sha256},
        "description": (
            f"{evidence.acquisition_method}; collected {stix.timestamp(evidence.collected_at)}"
        ),
    }
    source = evidence.source_reference or ""
    if (
        options.include_source_urls
        and evidence.acquisition_method == AcquisitionMethod.CONNECTOR_COLLECTION
        and urlsplit(source).scheme in ("http", "https")
    ):
        reference["url"] = source
    return reference


def _evidence_summary(evidence: EvidenceObject) -> dict[str, Any]:
    return {
        "evidence_id": str(evidence.id),
        "sha256": evidence.sha256,
        "acquisition_method": evidence.acquisition_method,
        "connector_id": evidence.connector_id,
        "collected_at": stix.timestamp(evidence.collected_at),
    }


@dataclass
class _Context:
    db: Session
    case: Case
    options: ExportOptions
    redact: Redactor
    report: ExportReport
    links: dict[tuple[str, uuid.UUID], str]


def _clean(context: _Context, value: str | None) -> str | None:
    if value is None:
        return None
    return context.redact(value)


def _entity_object(
    context: _Context, entity: Entity, identifiers: list[EntityIdentifier]
) -> dict[str, Any] | None:
    by_type: dict[str, list[EntityIdentifier]] = defaultdict(list)
    for identifier in identifiers:
        by_type[identifier.identifier_type].append(identifier)
    provenance = _extension(
        record_id=str(entity.id),
        entity_type=entity.entity_type,
        origin=entity.origin,
        identifiers=[
            {
                "type": identifier.identifier_type,
                "platform": identifier.platform,
                "value": _clean(context, identifier.original_value),
            }
            for identifier in identifiers[:MAX_REFERENCES_PER_OBJECT]
        ],
    )
    linked = context.links.get(("entity", entity.id))
    kind = entity.entity_type
    if kind in stix.ENTITY_TO_SCO or kind == "ip":
        identifier_type = {
            "domain": IdentifierType.DOMAIN,
            "url": IdentifierType.URL,
            "email": IdentifierType.EMAIL,
            "ip": IdentifierType.IP,
        }[kind]
        values = by_type.get(identifier_type)
        if not values:
            context.report.excluded[f"{kind}_without_identifier"] += 1
            return None
        value = context.redact(values[0].normalized_value)
        if kind == "ip":
            object_type = (
                "ipv6-addr"
                if isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
                else "ipv4-addr"
            )
        else:
            object_type = stix.ENTITY_TO_SCO[kind]
        return {
            "type": object_type,
            "spec_version": "2.1",
            "id": linked or stix.sco_id(object_type, {"value": value}),
            "value": value,
            "extensions": provenance,
        }
    if kind == "platform_account":
        platform_ids = by_type.get(IdentifierType.PLATFORM_ID, [])
        usernames = by_type.get(IdentifierType.USERNAME, [])
        carriers = platform_ids or usernames
        platform_name = carriers[0].platform if carriers else None
        user_id = platform_ids[0].original_value if platform_ids else None
        login = usernames[0].original_value if usernames else None
        if user_id is None and login is None:
            context.report.excluded["platform_account_without_stable_identifier"] += 1
            return None
        account: dict[str, Any] = {
            "type": "user-account",
            "spec_version": "2.1",
            "id": linked or stix.account_id(platform_name, user_id, login),
            "display_name": context.redact(entity.display_name),
            "extensions": provenance,
        }
        if user_id is not None:
            account["user_id"] = context.redact(user_id)
        if login is not None:
            account["account_login"] = context.redact(login)
        if platform_name:
            account["account_type"] = platform_name
        return account
    if kind == "organization":
        identity: dict[str, Any] = {
            "type": "identity",
            "spec_version": "2.1",
            "id": linked or stix.record_id("identity", context.case.id, "entity", str(entity.id)),
            "created": stix.timestamp(entity.created_at),
            "modified": stix.timestamp(entity.updated_at),
            "created_by_ref": stix.PRODUCER_IDENTITY_ID,
            "name": context.redact(entity.display_name),
            "identity_class": "organization",
            "extensions": provenance,
        }
        if entity.description:
            identity["description"] = context.redact(entity.description)[:4000]
        return identity
    context.report.excluded[f"entity_type_{kind}"] += 1
    return None


def build_bundle(
    db: Session, case: Case, options: ExportOptions, *, secret_values: list[str]
) -> tuple[dict[str, Any], ExportReport]:
    report = ExportReport()
    redact = Redactor(terms=[value for value in secret_values if len(value) >= 8])
    links = {
        (row.record_type, row.record_id): row.stix_id
        for row in db.scalars(select(StixObjectLink).where(StixObjectLink.case_id == case.id))
    }
    context = _Context(db=db, case=case, options=options, redact=redact, report=report, links=links)
    objects: list[dict[str, Any]] = [
        stix.producer_identity(),
        stix.provenance_extension_definition(),
    ]
    entity_objects: dict[uuid.UUID, dict[str, Any]] = {}

    identifiers: dict[uuid.UUID, list[EntityIdentifier]] = defaultdict(list)
    for identifier in db.scalars(
        select(EntityIdentifier)
        .where(EntityIdentifier.case_id == case.id)
        .order_by(EntityIdentifier.created_at)
    ):
        identifiers[identifier.entity_id].append(identifier)
    for entity in db.scalars(
        select(Entity).where(Entity.case_id == case.id).order_by(Entity.created_at, Entity.id)
    ):
        if len(objects) >= MAX_OBJECTS:
            report.truncated = True
            break
        stix_object = _entity_object(context, entity, identifiers.get(entity.id, []))
        if stix_object is None:
            continue
        entity_objects[entity.id] = stix_object
        if any(existing["id"] == stix_object["id"] for existing in objects[2:]):
            report.excluded["duplicate_identifier_value"] += 1
            continue
        objects.append(stix_object)
        report.counts[stix_object["type"]] += 1

    evidence_by_id: dict[uuid.UUID, EvidenceObject] = {
        row.id: row
        for row in db.scalars(select(EvidenceObject).where(EvidenceObject.case_id == case.id))
    }
    references: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for relationship_id, evidence_id in db.execute(
        select(
            RelationshipEvidence.relationship_id,
            RelationshipEvidence.evidence_id,
        ).where(RelationshipEvidence.case_id == case.id, RelationshipEvidence.stance == "supports")
    ):
        if evidence_id is not None:
            references[relationship_id].append(evidence_id)
    for relationship in db.scalars(
        select(Relationship)
        .where(Relationship.case_id == case.id)
        .order_by(Relationship.created_at, Relationship.id)
    ):
        if len(objects) >= MAX_OBJECTS:
            report.truncated = True
            break
        if relationship.review_status in (ReviewStatus.REJECTED, ReviewStatus.SUPERSEDED):
            report.excluded[f"relationship_{relationship.review_status}"] += 1
            continue
        if relationship.origin == Origin.AI_SUGGESTION and not (
            options.include_ai_suggestions and relationship.review_status == ReviewStatus.ACCEPTED
        ):
            report.excluded["ai_suggestion_not_accepted_or_not_selected"] += 1
            continue
        if relationship.review_status == ReviewStatus.UNREVIEWED and not options.include_unreviewed:
            report.excluded["relationship_unreviewed"] += 1
            continue
        source = entity_objects.get(relationship.source_entity_id)
        target = entity_objects.get(relationship.target_entity_id)
        if source is None or target is None:
            report.excluded["relationship_endpoint_not_exported"] += 1
            continue
        supporting = [
            evidence_by_id[e]
            for e in dict.fromkeys(references.get(relationship.id, []))
            if e in evidence_by_id
        ]
        stix_relationship: dict[str, Any] = {
            "type": "relationship",
            "spec_version": "2.1",
            "id": links.get(("relationship", relationship.id))
            or stix.record_id("relationship", case.id, "relationship", str(relationship.id)),
            "created": stix.timestamp(relationship.created_at),
            "modified": stix.timestamp(relationship.updated_at),
            "created_by_ref": stix.PRODUCER_IDENTITY_ID,
            "relationship_type": stix.predicate_to_type(relationship.predicate),
            "source_ref": source["id"],
            "target_ref": target["id"],
            "extensions": _extension(
                record_id=str(relationship.id),
                predicate=relationship.predicate,
                origin=relationship.origin,
                review_status=relationship.review_status,
                valid_from=stix.timestamp(relationship.valid_from)
                if relationship.valid_from
                else None,
                valid_to=stix.timestamp(relationship.valid_to) if relationship.valid_to else None,
                evidence=[_evidence_summary(e) for e in supporting[:MAX_REFERENCES_PER_OBJECT]],
            ),
        }
        if relationship.description:
            stix_relationship["description"] = redact(relationship.description)[:4000]
        if supporting:
            stix_relationship["external_references"] = [
                _evidence_reference(e, options) for e in supporting[:MAX_REFERENCES_PER_OBJECT]
            ]
        objects.append(stix_relationship)
        report.counts["relationship"] += 1

    groups: dict[tuple[uuid.UUID, str], list[Observation]] = defaultdict(list)
    for observation in db.scalars(
        select(Observation)
        .where(Observation.case_id == case.id, Observation.entity_id.is_not(None))
        .order_by(Observation.collected_at)
    ):
        assert observation.entity_id is not None
        if observation.observation_type in ("stix_object", "stix_relationship"):
            # What another tool claimed it observed is not re-exported as Tracehollow's observation.
            report.excluded["imported_stix_observation"] += 1
            continue
        evidence = evidence_by_id.get(observation.evidence_id) if observation.evidence_id else None
        source_key = str(observation.connector_run_id or (evidence.id if evidence else "none"))
        groups[(observation.entity_id, source_key)].append(observation)
    for (entity_id, source_key), rows in groups.items():
        target = entity_objects.get(entity_id)
        if target is None or target["type"] not in stix.SCO_TYPES:
            report.excluded["observation_of_entity_without_sco"] += len(rows)
            continue
        if len(objects) >= MAX_OBJECTS:
            report.truncated = True
            break
        evidence_rows = [
            evidence_by_id[o.evidence_id] for o in rows if o.evidence_id in evidence_by_id
        ]
        unique_evidence = list({e.id: e for e in evidence_rows}.values())[
            :MAX_REFERENCES_PER_OBJECT
        ]
        first = min(o.collected_at for o in rows)
        last = max(o.collected_at for o in rows)
        event_times = [o.event_time for o in rows if o.event_time is not None]
        published = [o.source_published_at for o in rows if o.source_published_at is not None]
        observed: dict[str, Any] = {
            "type": "observed-data",
            "spec_version": "2.1",
            "id": stix.record_id(
                "observed-data", case.id, "observations", f"{entity_id}:{source_key}"
            ),
            "created": stix.timestamp(first),
            "modified": stix.timestamp(last),
            "created_by_ref": stix.PRODUCER_IDENTITY_ID,
            "first_observed": stix.timestamp(first),
            "last_observed": stix.timestamp(last),
            "number_observed": len(rows),
            "object_refs": [target["id"]],
            "extensions": _extension(
                observation_types=sorted({o.observation_type for o in rows}),
                connector_id=(unique_evidence[0].connector_id if unique_evidence else None),
                acquisition_method=(
                    unique_evidence[0].acquisition_method if unique_evidence else None
                ),
                event_time_range=[
                    stix.timestamp(min(event_times)),
                    stix.timestamp(max(event_times)),
                ]
                if event_times
                else None,
                published_range=[stix.timestamp(min(published)), stix.timestamp(max(published))]
                if published
                else None,
                evidence=[_evidence_summary(e) for e in unique_evidence],
                note="first_observed and last_observed are Tracehollow collection or import times.",
            ),
        }
        if unique_evidence:
            observed["external_references"] = [
                _evidence_reference(e, options) for e in unique_evidence
            ]
        objects.append(observed)
        report.counts["observed-data"] += 1

    report.credential_values_removed = redact.credentials + redact.redactions
    bundle = {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": objects}
    return bundle, report
