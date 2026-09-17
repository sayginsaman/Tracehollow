"""STIX 2.1 subset: export validity, round trips, idempotent import and refusals (acceptance 5)."""

from __future__ import annotations

import copy
import json
import socket
import uuid
from typing import Any

import pytest
import stix2
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.entities.models import Entity, Relationship
from app.exchange import stix
from tests.conftest import browser_headers, create_case, import_file
from tests.test_team_access import ANALYST, VIEWER, _team, add_member, signed_in

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in export or import may open a network connection (URLs are never fetched)."""

    real = socket.getaddrinfo

    def local_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        # The test database and broker are on loopback; anything else would be a fetch.
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network access attempted: {host}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


def _entity(client: TestClient, csrf: str, case_id: object, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/entities", json=body, headers=browser_headers(csrf)
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _relationship(
    client: TestClient,
    csrf: str,
    case_id: object,
    source: str,
    target: str,
    predicate: str,
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/relationships",
        json={
            "source_entity_id": source,
            "target_entity_id": target,
            "predicate": predicate,
            **extra,
        },
        headers=browser_headers(csrf),
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _populate(client: TestClient, csrf: str, case_id: object) -> dict[str, dict[str, Any]]:
    _, evidence = import_file(
        client, csrf, case_id, b"Kayit: ornek.example, 203.0.113.9", title="Kayıt notu"
    )
    records = {
        "domain": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "domain",
                "display_name": "ornek.example",
                "identifiers": [{"identifier_type": "domain", "value": "Ornek.example"}],
            },
        ),
        "ip": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "ip",
                "display_name": "203.0.113.9",
                "identifiers": [{"identifier_type": "ip", "value": "203.0.113.9"}],
            },
        ),
        "ipv6": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "ip",
                "display_name": "2001:db8::1",
                "identifiers": [{"identifier_type": "ip", "value": "2001:db8::1"}],
            },
        ),
        "url": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "url",
                "display_name": "haber sayfası",
                "identifiers": [
                    {"identifier_type": "url", "value": "https://ornek.example/haber?id=3"}
                ],
            },
        ),
        "email": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "email",
                "display_name": "bilgi@ornek.example",
                "identifiers": [{"identifier_type": "email", "value": "Bilgi@Ornek.example"}],
            },
        ),
        "account": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "platform_account",
                "display_name": "Örnek Geliştirici",
                "identifiers": [
                    {
                        "identifier_type": "platform_id",
                        "value": "90210001",
                        "platform": "github.com",
                    },
                    {"identifier_type": "username", "value": "ornek-dev", "platform": "github.com"},
                ],
            },
        ),
        "same_login_elsewhere": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "platform_account",
                "display_name": "ornek-dev on gitlab",
                "identifiers": [
                    {"identifier_type": "username", "value": "ornek-dev", "platform": "gitlab.com"}
                ],
            },
        ),
        "organization": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "organization",
                "display_name": "Örnek A.Ş.",
                "description": "Sentetik kuruluş",
            },
        ),
        "phone": _entity(
            client,
            csrf,
            case_id,
            {
                "entity_type": "phone",
                "display_name": "+90 555 000 00 00",
                "identifiers": [{"identifier_type": "phone", "value": "+905550000000"}],
            },
        ),
    }
    _relationship(
        client,
        csrf,
        case_id,
        records["domain"]["id"],
        records["ip"]["id"],
        "resolves_to",
        supporting_evidence_ids=[evidence["evidence"]["id"]],
    )
    _relationship(
        client, csrf, case_id, records["account"]["id"], records["domain"]["id"], "links_to"
    )
    _relationship(
        client, csrf, case_id, records["organization"]["id"], records["domain"]["id"], "operates"
    )
    _relationship(
        client, csrf, case_id, records["phone"]["id"], records["organization"]["id"], "belongs_to"
    )
    return records


def _export(client: TestClient, case_id: object, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/cases/{case_id}/exports/stix", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/stix+json")
    bundle: dict[str, Any] = response.json()
    return bundle


def _import(client: TestClient, csrf: str, case_id: object, content: bytes, **fields: str) -> Any:
    return client.post(
        f"/api/v1/cases/{case_id}/imports/stix",
        files={"file": ("bundle.json", content, "application/json")},
        data={"import_origin": "Synthetic STIX bundle written by the test suite", **fields},
        headers=browser_headers(csrf),
    )


def _supported_view(bundle: dict[str, Any]) -> set[tuple[Any, ...]]:
    view: set[tuple[Any, ...]] = set()
    for item in bundle["objects"]:
        kind = item["type"]
        if kind in ("domain-name", "ipv4-addr", "ipv6-addr", "url", "email-addr"):
            view.add((kind, item["id"], item["value"]))
        elif kind == "user-account":
            view.add(
                (
                    kind,
                    item["id"],
                    item.get("user_id"),
                    item.get("account_login"),
                    item.get("account_type"),
                )
            )
        elif kind == "identity" and item["id"] != stix.PRODUCER_IDENTITY_ID:
            view.add((kind, item["id"], item["name"], item["identity_class"]))
        elif kind == "relationship":
            view.add(
                (
                    kind,
                    item["id"],
                    item["relationship_type"],
                    item["source_ref"],
                    item["target_ref"],
                )
            )
    return view


def test_export_is_valid_stix_and_keeps_provenance_without_inventing_confidence(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="STIX export (synthetic)")
    records = _populate(client, authed, case["id"])
    bundle = _export(client, case["id"])
    parsed = stix2.parse(bundle, allow_custom=False)
    assert len(parsed.objects) == len(bundle["objects"])

    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in bundle["objects"]:
        by_type.setdefault(item["type"], []).append(item)
        assert "confidence" not in item
        assert all(
            stix.RELATIONSHIP_TYPE.match(item["relationship_type"])
            for _ in [0]
            if item["type"] == "relationship"
        )
    assert [d["value"] for d in by_type["domain-name"]] == ["ornek.example"]
    domain = by_type["domain-name"][0]
    assert domain["id"] == stix.sco_id("domain-name", {"value": "ornek.example"})
    assert domain["extensions"][stix.PROVENANCE_EXTENSION_ID]["origin"] == "analyst_assertion"
    assert {a["value"] for a in by_type["ipv4-addr"]} == {"203.0.113.9"}
    assert {a["value"] for a in by_type["ipv6-addr"]} == {"2001:db8::1"}
    assert {a["value"] for a in by_type["email-addr"]} == {"bilgi@ornek.example"}
    accounts = {a["account_type"]: a for a in by_type["user-account"]}
    assert accounts["github.com"]["user_id"] == "90210001"
    # The same login on two platforms is two accounts, never one merged identifier.
    assert accounts["github.com"]["id"] != accounts["gitlab.com"]["id"]
    assert [i["name"] for i in by_type["identity"] if i["identity_class"] == "organization"] == [
        "Örnek A.Ş."
    ]
    assert all(i["identity_class"] in ("organization", "system") for i in by_type["identity"])
    relationships = {r["relationship_type"] for r in by_type["relationship"]}
    assert relationships == {
        "resolves-to",
        "links-to",
        "operates",
    }  # the phone relationship has no STIX endpoint
    resolves = next(r for r in by_type["relationship"] if r["relationship_type"] == "resolves-to")
    reference = resolves["external_references"][0]
    assert reference["source_name"] == "tracehollow-evidence"
    assert len(reference["hashes"]["SHA-256"]) == 64
    assert "url" not in reference
    assert by_type["extension-definition"][0]["id"] == stix.PROVENANCE_EXTENSION_ID
    text = json.dumps(bundle)
    for value in settings.secret_values():
        assert value not in text

    report = client.get(f"/api/v1/cases/{case['id']}/exports/stix/report").json()
    assert report["excluded"]["entity_type_phone"] == 1
    assert report["excluded"]["relationship_endpoint_not_exported"] == 1
    # Exports are stable: the same case exports the same object identifiers.
    assert _supported_view(_export(client, case["id"])) == _supported_view(bundle)
    assert records["phone"]["id"] not in text


def test_round_trip_preserves_the_supported_subset_and_repeated_imports_are_idempotent(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    source = create_case(client, authed, title="STIX source")
    _populate(client, authed, source["id"])
    original = _export(client, source["id"])
    content = json.dumps(original).encode()

    target = create_case(client, authed, title="STIX target")
    imported = _import(client, authed, target["id"], content)
    assert imported.status_code == 201, imported.text
    summary = imported.json()
    assert summary["already_imported"] is False
    assert summary["created"] == {
        "domain": 1,
        "ip": 2,
        "url": 1,
        "email": 1,
        "platform_account": 2,
        "organization": 1,
        "relationship": 3,
    }
    assert summary["skipped"].get("extension_definition_recorded_only") == 1
    assert (
        summary["skipped"].get("identity_class_not_imported") == 1
    )  # the producer identity (system)
    evidence = client.get(f"/api/v1/cases/{target['id']}/evidence/{summary['evidence_id']}").json()[
        "evidence"
    ]
    assert evidence["acquisition_method"] == "authorized_import"
    assert evidence["import_origin"] == "Synthetic STIX bundle written by the test suite"

    with db_session_factory() as db:
        origins = set(
            db.scalars(select(Entity.origin).where(Entity.case_id == uuid.UUID(target["id"])))
        )
        statuses = set(
            db.scalars(
                select(Relationship.review_status).where(
                    Relationship.case_id == uuid.UUID(target["id"])
                )
            )
        )
        relationship_origins = set(
            db.scalars(
                select(Relationship.origin).where(Relationship.case_id == uuid.UUID(target["id"]))
            )
        )
    assert origins == {"imported"}
    assert relationship_origins == {"imported"}
    assert statuses == {"unreviewed"}

    round_trip = _export(client, target["id"])
    stix2.parse(round_trip, allow_custom=False)
    assert _supported_view(round_trip) == _supported_view(original)
    domain = next(o for o in round_trip["objects"] if o["type"] == "domain-name")
    assert domain["extensions"][stix.PROVENANCE_EXTENSION_ID]["origin"] == "imported"

    again = _import(client, authed, target["id"], content)
    assert again.status_code == 201
    assert again.json()["already_imported"] is True
    with db_session_factory() as db:
        entities = db.scalar(
            select(func.count())
            .select_from(Entity)
            .where(Entity.case_id == uuid.UUID(target["id"]))
        )
    assert entities == 8

    extended = copy.deepcopy(original)
    extended["id"] = f"bundle--{uuid.uuid4()}"
    extended["objects"].append(
        {
            "type": "domain-name",
            "spec_version": "2.1",
            "id": stix.sco_id("domain-name", {"value": "yeni.example"}),
            "value": "yeni.example",
        }
    )
    second = _import(client, authed, target["id"], json.dumps(extended).encode())
    assert second.status_code == 201
    body = second.json()
    assert body["created"] == {"domain": 1}
    assert body["reused"]["entity"] == 8
    assert body["reused"]["relationship"] == 3
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Entity)
                .where(Entity.case_id == uuid.UUID(target["id"]))
            )
            == 9
        )


def test_unsupported_and_malformed_content_is_explicit(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed, title="STIX refusals")
    domain_id = stix.sco_id("domain-name", {"value": "ornek.example"})
    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "objects": [
            {
                "type": "domain-name",
                "spec_version": "2.1",
                "id": domain_id,
                "value": "ornek.example",
            },
            {
                "type": "indicator",
                "spec_version": "2.1",
                "id": f"indicator--{uuid.uuid4()}",
                "created": "2026-09-01T00:00:00.000Z",
                "modified": "2026-09-01T00:00:00.000Z",
                "pattern": "[domain-name:value = 'ornek.example']",
                "pattern_type": "stix",
                "valid_from": "2026-09-01T00:00:00Z",
            },
            {
                "type": "threat-actor",
                "spec_version": "2.1",
                "id": f"threat-actor--{uuid.uuid4()}",
                "created": "2026-09-01T00:00:00.000Z",
                "modified": "2026-09-01T00:00:00.000Z",
                "name": "Invented attribution",
            },
            {
                "type": "identity",
                "spec_version": "2.1",
                "id": f"identity--{uuid.uuid4()}",
                "created": "2026-09-01T00:00:00.000Z",
                "modified": "2026-09-01T00:00:00.000Z",
                "name": "A person",
                "identity_class": "individual",
            },
            {
                "type": "relationship",
                "spec_version": "2.1",
                "id": f"relationship--{uuid.uuid4()}",
                "created": "2026-09-01T00:00:00.000Z",
                "modified": "2026-09-01T00:00:00.000Z",
                "relationship_type": "attributed-to",
                "source_ref": domain_id,
                "target_ref": f"threat-actor--{uuid.uuid4()}",
            },
            {
                "type": "relationship",
                "spec_version": "2.1",
                "id": f"relationship--{uuid.uuid4()}",
                "created": "2026-09-01T00:00:00.000Z",
                "modified": "2026-09-01T00:00:00.000Z",
                "relationship_type": "Not Valid",
                "source_ref": domain_id,
                "target_ref": domain_id,
            },
            {
                "type": "url",
                "spec_version": "2.1",
                "id": f"domain-name--{uuid.uuid4()}",
                "value": "https://mismatch.example/",
            },
            {
                "type": "x-custom-thing",
                "spec_version": "2.1",
                "id": f"x-custom-thing--{uuid.uuid4()}",
            },
            {
                "type": "domain-name",
                "spec_version": "2.0",
                "id": stix.sco_id("domain-name", {"value": "eski.example"}),
                "value": "eski.example",
            },
            {
                "type": "domain-name",
                "spec_version": "2.1",
                "id": stix.sco_id("domain-name", {"value": "fetch.example"}),
                "value": "fetch.example",
                "external_references": [
                    {"source_name": "x", "url": "http://169.254.169.254/latest/"}
                ],
            },
        ],
    }
    rejected = _import(
        client, authed, case["id"], json.dumps(bundle).encode(), on_unsupported="reject"
    )
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["code"] == "unsupported_content"
    reasons = {(item["type"], item["reason"]) for item in detail["objects"]}
    assert ("indicator", "unsupported_type") in reasons
    assert ("threat-actor", "unsupported_type") in reasons
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Entity)
                .where(Entity.case_id == uuid.UUID(case["id"]))
            )
            == 0
        )

    skipped = _import(client, authed, case["id"], json.dumps(bundle).encode())
    assert skipped.status_code == 201, skipped.text
    body = skipped.json()
    assert body["created"] == {"domain": 2}
    assert body["skipped"] == {
        "unsupported_type": 3,
        "identity_class_not_imported": 1,
        "relationship_reference_not_imported": 1,
        "invalid_relationship_type": 1,
        "identifier_does_not_match_type": 1,
        "unsupported_spec_version": 1,
    }
    with db_session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Relationship)
                .where(Relationship.case_id == uuid.UUID(case["id"]))
            )
            == 0
        )

    malformed = [
        (b"not json", "invalid_json"),
        (
            b'{"type": "bundle", "type": "bundle", '
            b'"id": "bundle--00000000-0000-4000-8000-000000000000", "objects": []}',
            "duplicate_key",
        ),
        (json.dumps({"type": "report", "id": f"report--{uuid.uuid4()}"}).encode(), "not_a_bundle"),
        (
            json.dumps({"type": "bundle", "id": "bundle--nope", "objects": []}).encode(),
            "invalid_identifier",
        ),
        (
            json.dumps(
                {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": [{}] * 2001}
            ).encode(),
            "too_many_objects",
        ),
        (
            json.dumps(
                {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": [], "x": 1}
            ).encode(),
            "invalid_bundle",
        ),
        (
            b'{"type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000000", '
            b'"objects": [NaN]}',
            "invalid_json",
        ),
        (("[" * 40 + "]" * 40).encode(), "json_too_deep"),
        ("Kay\u0131t".encode("utf-16"), "invalid_encoding"),
    ]
    for content, code in malformed:
        response = _import(client, authed, case["id"], content)
        assert response.status_code == 422, (code, response.text)
        assert response.json()["detail"]["code"] == code
    oversized = _import(client, authed, case["id"], b" " * (settings.stix_import_max_bytes + 1))
    assert oversized.status_code == 413


def test_viewers_cannot_exchange_and_exports_can_include_source_urls_on_request(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    _team(client, authed)
    with signed_in(settings, *ANALYST) as (analyst, csrf):
        case = create_case(analyst, csrf, title="STIX roles")
        add_member(analyst, csrf, case["id"], VIEWER[0], "viewer")
        assert analyst.get(f"/api/v1/cases/{case['id']}/exports/stix").status_code == 200
    with signed_in(settings, *VIEWER) as (viewer, csrf):
        assert viewer.get(f"/api/v1/cases/{case['id']}/exports/stix").status_code == 403
        assert viewer.get(f"/api/v1/cases/{case['id']}/exports/stix/report").status_code == 403
        assert _import(viewer, csrf, case["id"], b"{}").status_code == 403
