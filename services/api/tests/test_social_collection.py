"""Social connectors through the API, execution engine and database (Phase 4 acceptance 1, 2, 4)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import EvidenceIndexState
from app.config import Settings
from app.entities.models import Observation
from app.evidence.models import EvidenceObject
from app.integrations.models import IntegrationCredential
from tests.collection_helpers import Router, respond
from tests.conftest import browser_headers, create_case, import_file
from tests.test_collection import _query, _run
from tests.test_social_connectors import PREVIEW, YT_KEY

pytestmark = pytest.mark.integration


def _sources(client: TestClient) -> dict[str, dict[str, Any]]:
    return {c["connector_id"]: c for c in client.get("/api/v1/connectors").json()}


def test_sources_show_every_capability_with_its_blocker(
    client: TestClient, authed: str, settings: Settings
) -> None:
    instagram = _sources(client)["instagram.account"]
    capabilities = {c["name"]: c for c in instagram["capabilities"]}
    assert set(capabilities) == {
        "official_business_discovery",
        "public_profile_page",
        "instaloader_session",
        "third_party_provider",
        "private_or_personal_account_access",
    }
    official = capabilities["official_business_discovery"]
    assert official["status"] == "implemented"
    assert official["access_method"] == "official_api"
    assert official["available"] is False
    assert "access_token, ig_user_id" in official["blocked_reason"]
    assert official["verification_status"] == "fixture_tested"
    assert official["last_live_verification"] is None
    web = capabilities["public_profile_page"]
    assert web["access_method"] == "public_web_unofficial"
    assert "Disabled by configuration" in web["blocked_reason"]
    excluded = capabilities["private_or_personal_account_access"]
    assert (excluded["status"], excluded["available"]) == ("excluded", False)
    assert capabilities["instaloader_session"]["status"] == "not_implemented"
    assert instagram["parameters"][0]["choices"] == {
        "official_business_discovery": "Official API: professional account discovery",
        "public_profile_page": "Unofficial: public profile page metadata",
    }

    for name, value in (("access_token", "EAA" + "s" * 40), ("ig_user_id", "17841400000000001")):
        response = client.post(
            f"/api/v1/connectors/instagram.account/credentials/{name}",
            json={"value": value},
            headers=browser_headers(authed),
        )
        assert response.status_code == 200, response.text
        assert value not in response.text
    capabilities = {c["name"]: c for c in _sources(client)["instagram.account"]["capabilities"]}
    assert capabilities["official_business_discovery"]["available"] is True
    assert capabilities["official_business_discovery"]["blocked_reason"] is None

    telegram = {c["name"]: c for c in _sources(client)["telegram.public_channel"]["capabilities"]}
    assert telegram["public_web_preview"]["available"] is True
    assert "bot_token" in telegram["bot_api_chat_info"]["blocked_reason"]
    assert "MTProto" in telegram["mtproto_user_session"]["label"]
    youtube = {c["name"]: c for c in _sources(client)["youtube.data_api"]["capabilities"]}
    assert youtube["captions_download"]["status"] == "not_implemented"
    assert "OAuth" in youtube["captions_download"]["reason"]


def test_unimplemented_capabilities_cannot_be_saved(
    client: TestClient, authed: str, settings: Settings
) -> None:
    case = create_case(client, authed)
    for connector, capability in (
        ("instagram.account", "instaloader_session"),
        ("instagram.account", "private_or_personal_account_access"),
        ("telegram.public_channel", "mtproto_user_session"),
    ):
        response = client.post(
            f"/api/v1/cases/{case['id']}/saved-queries",
            json={
                "name": "not implemented",
                "input_type": "username",
                "input_value": "ornekhaber",
                "connector_ids": [connector],
                "parameters": {"capability": capability},
            },
            headers=browser_headers(authed),
        )
        assert response.status_code == 422, response.text
        assert "unknown capability" in response.text
    web = _query(
        client,
        authed,
        case["id"],
        "instagram.account",
        "username",
        "ornek.magaza",
        parameters={"capability": "public_profile_page"},
    )
    assert web["collection_mode"] == "platform_probe"


def test_missing_social_api_access_blocks_the_run_without_a_false_result(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    for connector, value, parameters in (
        ("instagram.account", "ornek.magaza", {}),
        ("telegram.public_channel", "ornekhaber", {"capability": "bot_api_chat_info"}),
        ("youtube.data_api", "@ornekkanal", {}),
    ):
        query = _query(
            client, authed, case["id"], connector, "username", value, parameters=parameters
        )
        router = Router()
        run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
        connector_run = run["connector_runs"][0]
        assert connector_run["outcome"] == "authentication_required", connector_run
        assert connector_run["last_error_code"] == "credential_not_configured"
        assert connector_run["items_collected"] == 0
        assert router.requests == []
        assert run["evidence_count"] == 0
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Observation)) == 0


def test_telegram_preview_run_records_capability_provenance_distinct_from_imports(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "telegram.public_channel", "username", "ornekhaber")
    assert query["collection_mode"] == "platform_probe"
    router = Router().add(
        "https://t.me/s/ornekhaber",
        respond(
            200, body=PREVIEW.replace('data-before="97"', "").replace("tme_messages_more", "x")
        ),
    )
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    connector_run = run["connector_runs"][0]
    assert connector_run["outcome"] == "findings"
    assert connector_run["coverage"]["post_numbers_not_visible"] == [98, 99]
    _, imported = import_file(client, authed, case["id"], b"Analyst notes about ornekhaber")

    with db_session_factory() as db:
        rows = {
            row.page_part: row
            for row in db.scalars(
                select(EvidenceObject).where(EvidenceObject.query_run_id == uuid.UUID(run["id"]))
            )
        }
        preview, posts = rows["preview"], rows["posts"]
        for row in (preview, posts):
            assert row.acquisition_method == "connector_collection"
            assert row.collection_mode == "platform_probe"
            assert row.access_category == "public"
            assert row.connector_id == "telegram.public_channel"
            assert row.import_origin is None
            assert row.collection_metadata["access_method"] == "public_web_unofficial"
        assert posts.derived_from_evidence_id == preview.id
        indexed = set(db.scalars(select(EvidenceIndexState.evidence_id)))
        assert posts.id in indexed
        assert preview.id not in indexed
        post_types = [
            o.observation_type
            for o in db.scalars(
                select(Observation).where(Observation.case_id == uuid.UUID(case["id"]))
            )
        ]
        assert sorted(post_types) == ["telegram_channel_preview", "telegram_post", "telegram_post"]
        imported_row = db.get(EvidenceObject, uuid.UUID(imported["evidence"]["id"]))
        assert imported_row is not None
        assert imported_row.acquisition_method == "authorized_import"
        assert imported_row.collection_mode is None
        assert imported_row.connector_id is None
        assert imported_row.import_origin


def test_stored_youtube_key_is_used_in_a_header_and_never_stored_with_evidence(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    response = client.post(
        "/api/v1/connectors/youtube.data_api/credentials/api_key",
        json={"value": YT_KEY},
        headers=browser_headers(authed),
    )
    assert response.status_code == 200, response.text
    case = create_case(client, authed)
    query = _query(client, authed, case["id"], "youtube.data_api", "username", "@ornekkanal")
    router = Router().add(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet%2CcontentDetails%2Cstatistics"
        "&forHandle=%40ornekkanal",
        respond(200, json_body={"items": []}),
    )
    run = _run(client, authed, settings, db_session_factory, case["id"], query["id"], router)
    assert run["connector_runs"][0]["outcome"] == "no_findings"
    assert router.requests[0].headers["x-goog-api-key"] == YT_KEY
    with db_session_factory() as db:
        evidence = db.scalar(
            select(EvidenceObject).where(EvidenceObject.case_id == uuid.UUID(case["id"]))
        )
        assert evidence is not None
        assert YT_KEY not in json.dumps(evidence.collection_metadata)
        assert YT_KEY not in (evidence.source_reference or "")
        credential = db.scalar(select(IntegrationCredential))
        assert credential is not None
        assert credential.last_result == "accepted"
