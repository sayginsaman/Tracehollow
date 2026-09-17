#!/usr/bin/env python3
"""Fill an isolated, disposable Tracehollow stack with clearly labelled synthetic demo data.

Used for design reviews, screenshots and manual walkthroughs. It must run only against a
separate Compose project started with compose.verify-phase4.yaml (the fixture platforms) and the
synthetic AI provider, never against a stack holding real investigations. Every case title starts
with "[Synthetic demo]" and carries the tag "synthetic-demo". Python standard library only.

Usage:
  TRACEHOLLOW_DEMO_PASSWORD=... python3 scripts/seed_demo_workspace.py \\
      --web-url http://localhost:3150 --pdfs /path/to/pdfs.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase1_acceptance import Session
from phase4_acceptance import (
    BOT_TOKEN,
    GRAPH_TOKEN,
    IG_USER_ID,
    YOUTUBE_KEY,
    multipart,
    wait_for,
)
from smoke_test import ROOT, Client, SmokeFailure, expect_status

USERNAME = "demo-analyst"
TAG = "synthetic-demo"
TERMINAL_RUN = ("completed", "partial", "failed", "canceled")

REGISTRY = (
    "Kayıt özeti (synthetic): ornek-lojistik.example alan adı Örnek Lojistik A.Ş. tarafından "
    "2026-03-12 tarihinde tescil edildi.\n\n"
    "Teknik iletişim adresi altyapi@ornek-lojistik.example olarak görünüyor. Sunucu 203.0.113.24 "
    "adresinde barındırılıyor ve 2026-08-01 tarihinde barındırma sağlayıcısı değişti.\n"
)
WHOIS = {
    "domain": "ornek-lojistik.example",
    "registrant": {"organization": "Örnek Lojistik A.Ş.", "country": "TR"},
    "created": "2026-03-12",
    "name_servers": ["ns1.dns-ornegi.example", "ns2.dns-ornegi.example"],
    "note": "Synthetic record for demonstration",
}
CHAT = (
    "12/08/2026, 09:15 - Messages and calls are end-to-end encrypted.\n"
    "12/08/2026, 09:16 - Ayşe Yılmaz: Günaydın, yeni sunucu adresini paylaşıyorum.\n"
    "Adres: 203.0.113.24 (synthetic)\n"
    "12/08/2026, 09:18 - Can Demir: IMG-20260812-WA0001.jpg (file attached)\n"
    "12/08/2026, 09:19 - Can Demir: fatura-agustos.pdf (file attached)\n"
    "13/08/2026, 17:42 - Ayşe Yılmaz: <Media omitted>\n"
    "14/08/2026, 08:05 - Can Demir: Barındırma değişikliği tamamlandı.\n"
)
AMBIGUOUS_CHAT = (
    "03/04/2026, 10:00 - Selin Kaya: Toplantı notları ektedir.\n"
    "05/04/2026, 11:30 - Emre Aksoy: Teşekkürler, inceliyorum.\n"
)


def create(session: Session, path: str, body: dict[str, Any], what: str) -> dict[str, Any]:
    response = session.send("POST", path, body)
    if response.status not in (200, 201, 202):
        raise SmokeFailure(f"{what}: HTTP {response.status} {response.body}")
    result: dict[str, Any] = response.body
    return result


def run_query(
    session: Session,
    case_id: str,
    name: str,
    connector: str,
    input_type: str,
    value: str,
    timeout: float,
    **extra: Any,
) -> dict[str, Any]:
    query = create(
        session,
        f"/api/v1/cases/{case_id}/saved-queries",
        {
            "name": name,
            "input_type": input_type,
            "input_value": value,
            "connector_ids": [connector],
            **extra,
        },
        f"saved query {name}",
    )
    started = create(
        session, f"/api/v1/cases/{case_id}/saved-queries/{query['id']}/runs", {}, "run"
    )

    def done() -> dict[str, Any] | None:
        body = session.get(f"/api/v1/cases/{case_id}/runs/{started['id']}").body
        return body if body["status"] in TERMINAL_RUN else None

    result: dict[str, Any] = wait_for(f"run {name}", done, timeout)
    print(f"  run {name}: {result['status']} / {result['connector_runs'][0]['outcome']}")
    return result


def wait_job(session: Session, case_id: str, job_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = session.get(f"/api/v1/cases/{case_id}/processing-jobs/{job_id}").body
        return (
            body
            if body["status"] in ("completed", "partial", "failed", "canceled", "needs_input")
            else None
        )

    result: dict[str, Any] = wait_for(f"job {job_id[:8]}", done, timeout)
    return result


def entity(
    session: Session,
    case_id: str,
    entity_type: str,
    name: str,
    identifiers: list[dict[str, str]],
    description: str = "",
) -> dict[str, Any]:
    return create(
        session,
        f"/api/v1/cases/{case_id}/entities",
        {
            "entity_type": entity_type,
            "display_name": name,
            "description": description,
            "identifiers": identifiers,
        },
        f"entity {name}",
    )


def import_text(
    session: Session,
    case_id: str,
    content: bytes,
    filename: str,
    kind: str,
    title: str,
    origin: str,
) -> dict[str, Any]:
    response = session.upload(
        case_id, content, filename, kind=kind, title=title, import_origin=origin
    )
    expect_status(response, 201, f"import {title}")
    result: dict[str, Any] = response.body["evidence"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--web-url", default="http://localhost:3150")
    parser.add_argument(
        "--pdfs", required=True, type=Path, help="JSON with base64 PDFs (text, scanned, encrypted)"
    )
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    args.origin = args.web_url.rstrip("/")
    password = os.environ.get("TRACEHOLLOW_DEMO_PASSWORD", "")
    if len(password) < 12:
        print("Set TRACEHOLLOW_DEMO_PASSWORD (at least 12 characters).", file=sys.stderr)
        return 2
    pdfs = {
        name: base64.b64decode(value) for name, value in json.loads(args.pdfs.read_text()).items()
    }

    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    setup = web.request(
        "POST",
        "/api/v1/setup/admin",
        {"setup_token": token, "username": USERNAME, "password": password},
    )
    if setup.status not in (201, 409):
        raise SmokeFailure(f"setup failed: HTTP {setup.status}")
    session = Session(args, USERNAME, password)

    # Credentials for the fixture platforms only (the verification fixture container).
    for connector, name, value in (
        ("instagram.account", "access_token", GRAPH_TOKEN),
        ("instagram.account", "ig_user_id", IG_USER_ID),
        ("telegram.public_channel", "bot_token", BOT_TOKEN),
        ("youtube.data_api", "api_key", YOUTUBE_KEY),
    ):
        create(
            session,
            f"/api/v1/connectors/{connector}/credentials/{name}",
            {"value": value},
            f"credential {name}",
        )

    print("Case 1: infrastructure review")
    case = create(
        session,
        "/api/v1/cases",
        {
            "title": "[Synthetic demo] Örnek Lojistik altyapı incelemesi",
            "purpose": "Synthetic demonstration: map the public infrastructure of a fictitious logistics company and record how its hosting changed.",
            "scope": "Fictitious organization, documentation IP ranges (203.0.113.0/24) and fixture sources only. No real people or companies.",
            "tags": [TAG, "infrastructure"],
        },
        "case 1",
    )
    cid = case["id"]
    org = entity(
        session,
        cid,
        "organization",
        "Örnek Lojistik A.Ş.",
        [{"identifier_type": "name", "value": "Örnek Lojistik A.Ş."}],
        "Fictitious company used for the demo.",
    )
    domain = entity(
        session,
        cid,
        "domain",
        "ornek-lojistik.example",
        [{"identifier_type": "domain", "value": "ornek-lojistik.example"}],
    )
    ip = entity(
        session, cid, "ip", "203.0.113.24", [{"identifier_type": "ip", "value": "203.0.113.24"}]
    )
    entity(
        session,
        cid,
        "platform_account",
        "ornek.magaza elsewhere",
        [{"identifier_type": "username", "value": "ornek.magaza", "platform": "instagram.com"}],
        "Analyst-recorded account with the same username as a collected account. Not established as the same account.",
    )
    registry = import_text(
        session,
        cid,
        REGISTRY.encode(),
        "kayit-ozeti.txt",
        "text",
        "Kayıt özeti (synthetic registry note)",
        "Synthetic registry excerpt written for the demo",
    )
    whois = import_text(
        session,
        cid,
        json.dumps(WHOIS, ensure_ascii=False, indent=2).encode(),
        "whois.json",
        "json",
        "WHOIS record (synthetic)",
        "Synthetic WHOIS record written for the demo",
    )
    create(
        session,
        f"/api/v1/cases/{cid}/relationships",
        {
            "source_entity_id": org["id"],
            "target_entity_id": domain["id"],
            "predicate": "owns",
            "description": "Registry note names the company as registrant.",
            "supporting_evidence_ids": [registry["id"], whois["id"]],
        },
        "relationship owns",
    )
    create(
        session,
        f"/api/v1/cases/{cid}/relationships",
        {
            "source_entity_id": domain["id"],
            "target_entity_id": ip["id"],
            "predicate": "resolves_to",
            "description": "Hosting address named in the registry note.",
            "supporting_evidence_ids": [registry["id"]],
        },
        "relationship resolves_to",
    )
    create(
        session,
        f"/api/v1/cases/{cid}/notes",
        {
            "body": "Hosting provider change on 2026-08-01 needs a second source before it goes into the report.",
            "entity_id": domain["id"],
        },
        "note",
    )

    for name, scenario in (
        ("Hesap adayları (fixture)", "findings"),
        ("Kısmi kapsam (fixture)", "partial"),
        ("Erişim gerekli (fixture)", "authentication_required"),
    ):
        run_query(
            session,
            cid,
            name,
            "synthetic.fixture",
            "username",
            "ornekdev",
            args.timeout,
            parameters={"scenario": scenario},
        )
    run_query(
        session,
        cid,
        "Basın duyurusu sayfası",
        "public_web.page",
        "url",
        "http://fixture-site:8080/web/ornek",
        args.timeout,
    )
    run_query(
        session,
        cid,
        "Instagram işletme hesabı",
        "instagram.account",
        "username",
        "ornek.magaza",
        args.timeout,
        limits={"max_pages": 3, "max_items_per_page": 2},
    )
    run_query(
        session,
        cid,
        "Telegram kanal önizlemesi",
        "telegram.public_channel",
        "username",
        "ornekhaber",
        args.timeout,
        limits={"max_pages": 3, "max_items_per_page": 50},
    )
    run_query(
        session,
        cid,
        "YouTube kanal yüklemeleri",
        "youtube.data_api",
        "username",
        "@ornekkanal",
        args.timeout,
    )
    run_query(
        session,
        cid,
        "YouTube kota testi",
        "youtube.data_api",
        "username",
        "@kotadolu",
        args.timeout,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("WhatsApp Chat with Operasyon Ekibi.txt", CHAT)
        archive.writestr("IMG-20260812-WA0001.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    accepted = expect_status(
        multipart(
            session,
            f"/api/v1/cases/{cid}/imports/whatsapp",
            buffer.getvalue(),
            "WhatsApp Chat - Operasyon Ekibi.zip",
            import_origin="Synthetic chat export written for the demo",
            timezone="Europe/Istanbul",
            date_order="auto",
        ),
        202,
        "WhatsApp export",
    ).body
    print(
        f"  WhatsApp export: {wait_job(session, cid, accepted['job']['id'], args.timeout)['status']}"
    )
    ambiguous = expect_status(
        multipart(
            session,
            f"/api/v1/cases/{cid}/imports/whatsapp",
            AMBIGUOUS_CHAT.encode(),
            "WhatsApp Chat with Toplantı.txt",
            import_origin="Synthetic chat export with ambiguous dates",
            timezone="unknown",
            date_order="auto",
        ),
        202,
        "ambiguous export",
    ).body
    print(
        f"  ambiguous export: {wait_job(session, cid, ambiguous['job']['id'], args.timeout)['status']} (left waiting for the analyst)"
    )
    for name, title in (
        ("text", "Yıllık faaliyet raporu (synthetic PDF)"),
        ("scanned", "Taranmış sözleşme sayfası (synthetic PDF)"),
        ("encrypted", "Parola korumalı ek (synthetic PDF)"),
    ):
        doc = expect_status(
            multipart(
                session,
                f"/api/v1/cases/{cid}/imports/documents",
                pdfs[name],
                f"{name}.pdf",
                import_origin="Synthetic PDF generated for the demo",
                title=title,
            ),
            202,
            f"PDF {name}",
        ).body
        print(f"  PDF {name}: {wait_job(session, cid, doc['job']['id'], args.timeout)['status']}")

    def indexed() -> bool:
        items = session.get(f"/api/v1/cases/{cid}/ai/index?limit=100").body.get("items", [])
        return bool(items) and all(
            item["status"] in ("indexed", "failed", "canceled") for item in items
        )

    wait_for("case index", indexed, args.timeout, 2)
    conversation = create(session, f"/api/v1/cases/{cid}/ai/conversations", {}, "conversation")
    question = create(
        session,
        f"/api/v1/cases/{cid}/ai/conversations/{conversation['id']}/questions",
        {"question": "ornek-lojistik.example alan adı hangi tarihte kim tarafından tescil edildi?"},
        "question",
    )

    def answered() -> bool:
        return session.get(f"/api/v1/cases/{cid}/ai/runs/{question['id']}").body["status"] in (
            "completed",
            "failed",
            "canceled",
        )

    wait_for("AI answer", answered, args.timeout, 2)
    print("  AI answer recorded")

    print("Case 2: footprint review")
    second = create(
        session,
        "/api/v1/cases",
        {
            "title": "[Synthetic demo] Dijital ayak izi gözden geçirmesi",
            "purpose": "Synthetic demonstration of a self-review of public accounts.",
            "scope": "Fixture sources only.",
            "tags": [TAG, "footprint"],
        },
        "case 2",
    )
    run_query(
        session,
        second["id"],
        "Kullanıcı adı adayları",
        "synthetic.fixture",
        "username",
        "selin.kaya",
        args.timeout,
        parameters={"scenario": "no_findings"},
    )

    print("Case 3: archived")
    third = create(
        session,
        "/api/v1/cases",
        {
            "title": "[Synthetic demo] Arşivlenmiş örnek vaka",
            "purpose": "Synthetic archived case.",
            "scope": "None.",
            "tags": [TAG],
        },
        "case 3",
    )
    create(session, f"/api/v1/cases/{third['id']}/archive", {}, "archive case 3")
    print(f"Demo workspace ready at {args.web_url} (user {USERNAME}).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        print(f"FAIL {failure}", file=sys.stderr)
        sys.exit(1)
