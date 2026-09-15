from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import browser_headers, create_case, import_file

pytestmark = pytest.mark.integration


def _entity(client: TestClient, csrf: str, case_id: object, **body: object) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/cases/{case_id}/entities", json=body, headers=browser_headers(csrf)
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_identifiers_keep_original_and_normalized_values(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    entity = _entity(
        client,
        authed,
        case["id"],
        entity_type="email",
        display_name="Şule Yılmaz contact",
        identifiers=[
            {"identifier_type": "email", "value": "Şule.YILMAZ@Örnek.com.tr"},
            {"identifier_type": "domain", "value": "Örnek.com.tr"},
        ],
    )
    identifiers = {i["identifier_type"]: i for i in entity["identifiers"]}
    assert identifiers["email"]["original_value"] == "Şule.YILMAZ@Örnek.com.tr"
    assert identifiers["email"]["normalized_value"] == "şule.yilmaz@xn--rnek-4qa.com.tr"
    assert identifiers["domain"]["normalized_value"] == "xn--rnek-4qa.com.tr"
    assert entity["origin"] == "analyst_assertion"

    invalid = client.post(
        f"/api/v1/cases/{case['id']}/entities",
        json={
            "entity_type": "ip",
            "display_name": "bad",
            "identifiers": [{"identifier_type": "ip", "value": "999.1.1.1"}],
        },
        headers=browser_headers(authed),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_identifier"


def test_matching_usernames_never_merge_accounts(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    first = _entity(
        client,
        authed,
        case["id"],
        entity_type="platform_account",
        display_name="analyst on platform A",
        identifiers=[
            {"identifier_type": "username", "value": "Analyst", "platform": "platform-a.example"}
        ],
    )
    second = _entity(
        client,
        authed,
        case["id"],
        entity_type="platform_account",
        display_name="analyst on platform B",
        identifiers=[
            {"identifier_type": "username", "value": "analyst", "platform": "platform-a.example"}
        ],
    )
    assert first["id"] != second["id"]
    listing = client.get(f"/api/v1/cases/{case['id']}/entities", params={"q": "analyst"}).json()
    assert listing["total"] == 2

    detail = client.get(f"/api/v1/cases/{case['id']}/entities/{first['id']}").json()
    assert [shared["entity_id"] for shared in detail["shared_identifiers"]] == [second["id"]]
    assert detail["relationships"] == []  # shared values are shown, never turned into a merge


def test_stable_platform_ids_are_unique_per_case(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    identifier = {
        "identifier_type": "platform_id",
        "value": "123456",
        "platform": "platform-a.example",
    }
    first = _entity(
        client,
        authed,
        case["id"],
        entity_type="platform_account",
        display_name="one",
        identifiers=[identifier],
    )
    conflict = client.post(
        f"/api/v1/cases/{case['id']}/entities",
        json={
            "entity_type": "platform_account",
            "display_name": "two",
            "identifiers": [identifier],
        },
        headers=browser_headers(authed),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "platform_id_already_assigned"
    assert conflict.json()["detail"]["entity_id"] == first["id"]

    other_case = create_case(client, authed, title="Other")
    _entity(
        client,
        authed,
        other_case["id"],
        entity_type="platform_account",
        display_name="x",
        identifiers=[identifier],
    )


def test_relationships_record_origin_references_and_review_history(
    client: TestClient, authed: str
) -> None:
    case = create_case(client, authed)
    org = _entity(client, authed, case["id"], entity_type="organization", display_name="Örnek A.Ş.")
    domain = _entity(client, authed, case["id"], entity_type="domain", display_name="ornek.example")
    _s, evidence = import_file(client, authed, case["id"], b"WHOIS-like synthetic record")
    evidence_id = evidence["evidence"]["id"]

    created = client.post(
        f"/api/v1/cases/{case['id']}/relationships",
        json={
            "source_entity_id": org["id"],
            "target_entity_id": domain["id"],
            "predicate": "owns",
            "valid_from": "2026-01-01T00:00:00+03:00",
            "supporting_evidence_ids": [evidence_id],
        },
        headers=browser_headers(authed),
    )
    assert created.status_code == 201, created.text
    relationship = created.json()
    assert relationship["origin"] == "analyst_assertion"
    assert relationship["review_status"] == "unreviewed"
    assert relationship["references"][0]["evidence_id"] == evidence_id
    assert relationship["references"][0]["evidence_acquisition_method"] == "authorized_import"

    reviewed = client.post(
        f"/api/v1/cases/{case['id']}/relationships/{relationship['id']}/review",
        json={"review_status": "accepted", "rationale": "Registry record supports ownership."},
        headers=browser_headers(authed),
    ).json()
    assert reviewed["review_status"] == "accepted"
    assert [(d["previous_value"], d["new_value"]) for d in reviewed["decisions"]] == [
        ("unreviewed", "accepted")
    ]

    contradicting = client.post(
        f"/api/v1/cases/{case['id']}/relationships/{relationship['id']}/references",
        json={"evidence_id": evidence_id, "stance": "contradicts", "note": "Conflicting reading"},
        headers=browser_headers(authed),
    ).json()
    assert {r["stance"] for r in contradicting["references"]} == {"supports", "contradicts"}

    evidence_detail = client.get(f"/api/v1/cases/{case['id']}/evidence/{evidence_id}").json()
    assert {r["relationship_id"] for r in evidence_detail["linked_relationships"]} == {
        relationship["id"]
    }

    self_loop = client.post(
        f"/api/v1/cases/{case['id']}/relationships",
        json={"source_entity_id": org["id"], "target_entity_id": org["id"], "predicate": "owns"},
        headers=browser_headers(authed),
    )
    assert self_loop.status_code == 422


def test_relationship_endpoints_must_belong_to_the_case(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    other = create_case(client, authed, title="Other")
    here = _entity(client, authed, case["id"], entity_type="domain", display_name="a.example")
    there = _entity(client, authed, other["id"], entity_type="domain", display_name="b.example")
    response = client.post(
        f"/api/v1/cases/{case['id']}/relationships",
        json={
            "source_entity_id": here["id"],
            "target_entity_id": there["id"],
            "predicate": "links_to",
        },
        headers=browser_headers(authed),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "entity_not_found"


def test_graph_is_bounded_and_reports_truncation(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    hub = _entity(client, authed, case["id"], entity_type="organization", display_name="Hub")
    for index in range(8):
        leaf = _entity(
            client, authed, case["id"], entity_type="domain", display_name=f"leaf{index}.example"
        )
        client.post(
            f"/api/v1/cases/{case['id']}/relationships",
            json={
                "source_entity_id": hub["id"],
                "target_entity_id": leaf["id"],
                "predicate": "owns",
            },
            headers=browser_headers(authed),
        )

    bounded = client.get(f"/api/v1/cases/{case['id']}/graph", params={"max_nodes": 4}).json()
    assert len(bounded["nodes"]) == 4
    assert bounded["truncated"] is True
    assert bounded["total_entities"] == 9
    assert bounded["nodes"][0]["id"] == hub["id"]  # highest degree first
    node_ids = {node["id"] for node in bounded["nodes"]}
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids for edge in bounded["edges"]
    )

    focused = client.get(
        f"/api/v1/cases/{case['id']}/graph",
        params={"focus_entity_id": hub["id"], "depth": 1, "max_nodes": 150},
    ).json()
    assert len(focused["nodes"]) == 9
    assert len(focused["edges"]) == 8
    assert focused["truncated"] is False
    assert {edge["origin"] for edge in focused["edges"]} == {"analyst_assertion"}

    too_many = client.get(f"/api/v1/cases/{case['id']}/graph", params={"max_nodes": 1000})
    assert too_many.status_code == 422


def test_entity_deletion_rules(client: TestClient, authed: str) -> None:
    case = create_case(client, authed)
    entity = _entity(
        client, authed, case["id"], entity_type="event", display_name="Synthetic event"
    )
    deleted = client.delete(
        f"/api/v1/cases/{case['id']}/entities/{entity['id']}", headers=browser_headers(authed)
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/cases/{case['id']}/entities/{entity['id']}").status_code == 404
