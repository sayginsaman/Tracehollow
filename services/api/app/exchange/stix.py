"""The STIX 2.1 subset Tracehollow exchanges (docs/interoperability/stix.md).

Checked against the OASIS STIX 2.1 specification (docs.oasis-open.org/cti/stix/v2.1/os, read
2026-09-17). Exports are parsed in tests and in scripts/verify-phase5.sh with the OASIS ``stix2``
library 3.0.2 (``allow_custom=False``).

Supported mappings:

==================  ===========================================================================
Tracehollow         STIX 2.1
==================  ===========================================================================
domain entity       ``domain-name`` (SCO, deterministic UUIDv5 over ``value``)
ip entity           ``ipv4-addr`` / ``ipv6-addr`` (SCO, UUIDv5 over ``value``)
url entity          ``url`` (SCO, UUIDv5 over ``value``)
email entity        ``email-addr`` (SCO, UUIDv5 over ``value``)
platform account    ``user-account`` (SCO; ``account_type`` = platform, ``user_id`` = stable
                    platform ID, ``account_login`` = username). Identifier derived from platform
                    *and* identifiers, see ``account_id``
organization        ``identity`` with ``identity_class = organization``
relationship        ``relationship`` (``relationship_type`` = predicate with ``-`` for ``_``)
observations        ``observed-data`` per entity and source: collection times, counts, evidence
                    hashes (only for records Tracehollow collected or imported itself)
provenance          property extension ``extension-definition--{PROVENANCE_EXTENSION_ID}``
==================  ===========================================================================

Never produced: indicators, threat actors, malware, campaigns, attribution, ``confidence``
values (Tracehollow has no calibrated confidence), person identities.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

STIX_SCO_NAMESPACE = uuid.UUID("00abedb4-aa42-466c-9c01-fed23315a9b7")
TRACEHOLLOW_NAMESPACE = uuid.UUID("5d2b8c6e-1f3a-5e4b-9c7d-2a6f0e8b4c31")
PROVENANCE_EXTENSION_ID = "extension-definition--" + str(
    uuid.uuid5(TRACEHOLLOW_NAMESPACE, "tracehollow-provenance-extension-v1")
)
PRODUCER_IDENTITY_ID = "identity--" + str(uuid.uuid5(TRACEHOLLOW_NAMESPACE, "tracehollow-producer"))
FIXED_CREATED = "2026-09-17T00:00:00.000Z"
MEDIA_TYPE = "application/stix+json;version=2.1"

SCO_TYPES = frozenset(
    {"domain-name", "ipv4-addr", "ipv6-addr", "url", "email-addr", "user-account"}
)
SUPPORTED_TYPES = SCO_TYPES | {"identity", "relationship", "observed-data", "extension-definition"}
ENTITY_TO_SCO = {"domain": "domain-name", "url": "url", "email": "email-addr"}
OBJECT_ID = re.compile(
    r"^([a-z0-9][a-z0-9-]{1,248}[a-z0-9])--([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
RELATIONSHIP_TYPE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")


def canonical(value: dict[str, Any]) -> str:
    """RFC 8785 canonical JSON for the string-valued objects used in identifiers."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sco_id(object_type: str, contributing: dict[str, Any]) -> str:
    return f"{object_type}--{uuid.uuid5(STIX_SCO_NAMESPACE, canonical(contributing))}"


def account_id(platform: str | None, user_id: str | None, account_login: str | None) -> str:
    """Identifier of a ``user-account``.

    STIX derives it from ``user_id`` and ``account_login`` only, so accounts with the same login
    on different platforms would share one identifier and a receiving tool would merge them.
    Tracehollow includes the platform (a documented deviation from the SHOULD in section 2.9).
    """
    name = canonical(
        {
            "account_type": platform or "",
            "account_login": account_login or "",
            "user_id": user_id or "",
        }
    )
    return f"user-account--{uuid.uuid5(TRACEHOLLOW_NAMESPACE, name)}"


def record_id(object_type: str, case_id: uuid.UUID, kind: str, value: str) -> str:
    """Stable identifier of an SDO or SRO derived from a case record."""
    return f"{object_type}--{uuid.uuid5(TRACEHOLLOW_NAMESPACE, f'{case_id}:{kind}:{value}')}"


def timestamp(value: datetime | None) -> str:
    moment = (value or datetime.now(UTC)).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not TIMESTAMP.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def predicate_to_type(predicate: str) -> str:
    return predicate.replace("_", "-")


def type_to_predicate(relationship_type: str) -> str | None:
    candidate = relationship_type.replace("-", "_")
    return candidate if re.fullmatch(r"^[a-z][a-z0-9_]{1,63}$", candidate) else None


def producer_identity() -> dict[str, Any]:
    return {
        "type": "identity",
        "spec_version": "2.1",
        "id": PRODUCER_IDENTITY_ID,
        "created": FIXED_CREATED,
        "modified": FIXED_CREATED,
        "name": "Tracehollow export",
        "identity_class": "system",
        "description": "The software that produced this bundle; it does not identify an analyst.",
    }


def provenance_extension_definition() -> dict[str, Any]:
    return {
        "type": "extension-definition",
        "spec_version": "2.1",
        "id": PROVENANCE_EXTENSION_ID,
        "created_by_ref": PRODUCER_IDENTITY_ID,
        "created": FIXED_CREATED,
        "modified": FIXED_CREATED,
        "name": "Tracehollow provenance",
        "description": (
            "Origin, review status, entity type and evidence references of Tracehollow records. "
            "Origins: observed, deterministic_derivation, ai_suggestion, analyst_assertion, "
            "imported. Review states: unreviewed, accepted, rejected, superseded."
        ),
        "schema": (
            "Plain-text definition: Tracehollow repository, docs/interoperability/stix.md, section "
            "'Provenance extension' (version 1.0)."
        ),
        "version": "1.0",
        "extension_types": ["property-extension"],
    }
