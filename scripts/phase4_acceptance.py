#!/usr/bin/env python3
"""Phase 4 acceptance checks against a running stack with controlled fixtures.

Used by scripts/verify-phase4.sh (compose.verify-phase4.yaml). No real platform is contacted.
Python standard library only; record identifiers (never secrets) are shared through --state.

Stages:
  seed          administrator, case, connector capability matrix and blockers
  whatsapp      ZIP export with hostile entries: date-order question, answer, messages with
                locations, inert attachments, timeline sections
  documents     text PDF with page references, encrypted and malformed PDFs, image-only PDF (OCR
                unavailable, or real OCR text with --ocr), AI retrieval of extracted text
  social-blocked  social runs without credentials are authentication_required, nothing stored
  social        Instagram official API, undiscoverable account, disabled/enabled profile page and
                login wall; Telegram preview, private channel and Bot API; YouTube uploads,
                disabled comments and exhausted quota; tokens absent from evidence
  web-enabled   Instagram public profile page after an administrator enabled it
  compare       comparison and timeline over collected and imported records
  reports       HTML report preview and download: escaping, redaction, offline citations
  outsider      a non-member cannot reach imports, jobs, timeline, comparison or reports
  delete        case deletion removes originals, derived records and their files
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from phase1_acceptance import ADMIN, OTHER, Session, load_state, password, save_state
from smoke_test import ROOT, Client, Response, SmokeFailure, _response, check, expect_status

FIXTURE = "http://fixture-site:8080"
CASE_TITLE = "Phase 4 kabul <script>alert('case')</script> (synthetic)"
GRAPH_TOKEN = "EAAverificationGraphToken000000000000000000"
GRAPH_REJECTED = "EAAverificationRejectedToken00000000000000"
IG_USER_ID = "17841400000000001"
BOT_TOKEN = "123456789:AAverificationBotToken_00000000000000000"
YOUTUBE_KEY = "AIzaVerificationKey00000000000000000000000"
SECRETS = (GRAPH_TOKEN, GRAPH_REJECTED, BOT_TOKEN, YOUTUBE_KEY)
TERMINAL_RUN = ("completed", "partial", "failed", "canceled")
TERMINAL_JOB = ("completed", "partial", "failed", "canceled", "needs_input")

CHAT = (
    "03/04/2024, 09:15 - Messages and calls are end-to-end encrypted.\n"
    "03/04/2024, 09:16 - Ayşe Yılmaz: Merhaba, toplantı saat kaçta?\n"
    "İkinci satır: çğıöşü ÇĞİÖŞÜ\n"
    "03/04/2024, 09:17 - +1 202-555-0143: IMG-20240403-WA0001.jpg (file attached)\n"
    "03/04/2024, 09:18 - Can: rapor.pdf (file attached)\n"
    "05/04/2024, 21:05 - Can: <Media omitted>\n"
)


def api(state: dict[str, Any], suffix: str) -> str:
    return f"/api/v1/cases/{state['case_id']}{suffix}"


def wait_for(description: str, predicate: Any, timeout: float, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise SmokeFailure(f"timed out after {timeout:.0f}s waiting for {description}")
        time.sleep(interval)


def multipart(session: Session, path: str, content: bytes, filename: str, **fields: str) -> Response:
    boundary = f"tracehollow-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n"
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename.replace(chr(34), "%22")}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    status, body, headers = session.raw(
        "POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
    )
    return _response(status, body, headers)


def job(session: Session, state: dict[str, Any], job_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = session.get(api(state, f"/processing-jobs/{job_id}")).body
        return body if body["status"] in TERMINAL_JOB else None

    result: dict[str, Any] = wait_for(f"processing job {job_id[:8]}", done, timeout)
    return result


def run(session: Session, state: dict[str, Any], connector: str, input_type: str, value: str, timeout: float, **extra: Any) -> dict[str, Any]:
    body = {"name": f"{connector}: {value}"[:200], "input_type": input_type, "input_value": value, "connector_ids": [connector], **extra}
    query = expect_status(session.send("POST", api(state, "/saved-queries"), body), 201, f"saved query {connector} {value} {extra.get('parameters', {})}").body
    started = expect_status(session.send("POST", api(state, f"/saved-queries/{query['id']}/runs")), 202, "run queued").body

    def done() -> dict[str, Any] | None:
        result = session.get(api(state, f"/runs/{started['id']}")).body
        return result if result["status"] in TERMINAL_RUN else None

    result: dict[str, Any] = wait_for(f"run {started['id'][:8]}", done, timeout)
    return result


def outcome(result: dict[str, Any]) -> tuple[str | None, str | None]:
    connector = result["connector_runs"][0]
    return connector["outcome"], connector["last_error_code"]


def admin(args: argparse.Namespace) -> Session:
    return Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))


def fixtures(args: argparse.Namespace) -> dict[str, bytes]:
    data = json.loads(Path(args.fixtures).read_text())
    return {name: base64.b64decode(value) for name, value in data.items()}


# -- stages ------------------------------------------------------------------------------------


def stage_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    setup = {"setup_token": token, "username": ADMIN, "password": password("TRACEHOLLOW_ACCEPTANCE_PASSWORD")}
    expect_status(web.request("POST", "/api/v1/setup/admin", setup), 201, "administrator created")
    session = admin(args)
    created = session.send("POST", "/api/v1/cases", {"title": CASE_TITLE, "purpose": "Phase 4 <b>acceptance</b>", "scope": "Controlled fixtures only"})
    state["case_id"] = expect_status(created, 201, "case created").body["id"]
    connectors = {c["connector_id"]: c for c in session.get("/api/v1/connectors").body}
    # Telegram's web preview and YouTube's Data API capabilities earned live checks on 2026-09-19
    # (docs/connectors/live-smoke.md); Instagram has none, so it must still read fixture-tested.
    live_dates = {"telegram.public_channel": "2026-09-19", "youtube.data_api": "2026-09-19"}
    for key in ("instagram.account", "telegram.public_channel", "youtube.data_api"):
        check(key in connectors, f"{key} registered")
        expected_date = live_dates.get(key)
        expected_label = "live_verified" if expected_date else "fixture_tested"
        check(
            connectors[key]["verification_status"] == expected_label
            and connectors[key]["last_live_verification"] == expected_date,
            f"{key} is labelled {expected_label}"
            + (f" with its recorded date {expected_date}" if expected_date else ", with no live date"),
        )
    capabilities = {c["name"]: c for c in connectors["instagram.account"]["capabilities"]}
    check(capabilities["private_or_personal_account_access"]["status"] == "excluded", "private and unrestricted personal-account access is excluded")
    check(capabilities["instaloader_session"]["status"] == "not_implemented", "session-based unofficial client is not implemented")
    check("access_token, ig_user_id" in (capabilities["official_business_discovery"]["blocked_reason"] or ""), "official Instagram API is blocked until credentials exist")
    check("Disabled by configuration" in (capabilities["public_profile_page"]["blocked_reason"] or ""), "unofficial profile page is disabled by default")
    choices = connectors["instagram.account"]["parameters"][0]["choices"]
    check(set(choices) == {"official_business_discovery", "public_profile_page"}, "only implemented Instagram capabilities are selectable")
    telegram = {c["name"]: c for c in connectors["telegram.public_channel"]["capabilities"]}
    check(telegram["mtproto_user_session"]["status"] == "not_implemented", "Telegram user sessions are not implemented")
    youtube = {c["name"]: c for c in connectors["youtube.data_api"]["capabilities"]}
    check(youtube["captions_download"]["status"] == "not_implemented", "YouTube transcripts are not promised")


def stage_whatsapp(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("WhatsApp Chat with Synthetic.txt", CHAT)
        archive.writestr("IMG-20240403-WA0001.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        archive.writestr("../../escape.txt", b"outside")
        link = zipfile.ZipInfo("link")
        link.external_attr = (0o120777 << 16) | 0x20
        archive.writestr(link, "/etc/passwd")
        archive.writestr("note<svg onload=alert(1)>.html", "<script>alert(document.cookie)</script>")
    response = multipart(session, api(state, "/imports/whatsapp"), buffer.getvalue(), "../export<b>.zip", import_origin="Synthetic export for acceptance", timezone="Europe/Istanbul", date_order="auto")
    accepted = expect_status(response, 202, "WhatsApp export accepted").body
    check(accepted["evidence"]["kind"] == "archive" and accepted["evidence"]["acquisition_method"] == "authorized_import", "original stored as an authorized-import archive")
    check(accepted["evidence"]["original_filename"] == "export<b>.zip", "path components removed from the filename")
    waiting = job(session, state, accepted["job"]["id"], args.timeout)
    check(waiting["status"] == "needs_input" and waiting["needs_input"]["basis"] == "ambiguous", "ambiguous dates stop the job and ask for the date order")
    check(waiting["observation_count"] == 0, "nothing derived before the question is answered")
    expect_status(session.send("POST", api(state, f"/processing-jobs/{waiting['id']}/input"), {"date_order": "day_first"}), 200, "date order answered")
    finished = job(session, state, waiting["id"], args.timeout)
    result = finished["result"]
    check(finished["status"] == "partial", f"missing attachment and skipped entries make the result partial ({finished['status']})")
    check(result["messages"] == 4 and result["system_events"] == 1, "four messages and one system event")
    skipped = {item["name"]: item["reason"] for item in result["skipped_archive_entries"]}
    check(skipped.get("../../escape.txt") == "unsafe_path" and skipped.get("link") == "symbolic_link", "traversal and symlink entries skipped and reported")
    check(result["attachments"] == {**result["attachments"], "present": 1, "missing": 1, "omitted_by_export": 1}, "attachments resolved as present, missing and omitted")
    parts = {item["page_part"]: item for item in finished["derived_evidence"]}
    hostile = next(item for item in finished["derived_evidence"] if item["title"].startswith("Attachment: note"))
    check(hostile["content_type"] == "application/octet-stream", "HTML attachment stored as an inert binary")
    preview = session.get(api(state, f"/evidence/{hostile['id']}/preview")).body
    check(preview["previewable"] is False and preview["text"] == "", "binary attachment is never decoded for preview")
    status, _body, headers = session.raw("GET", api(state, f"/evidence/{hostile['id']}/content"))
    check(status == 200 and headers.get("content-type") == "application/octet-stream" and headers.get("content-disposition", "").startswith("attachment"), "attachment downloads as an inert file")
    state["chat_evidence_id"] = parts["chat_text"]["id"]
    state["whatsapp_original_id"] = accepted["evidence"]["id"]
    timeline = session.get(api(state, "/timeline?limit=100")).body
    messages = [item for item in timeline["items"] if item["observation_type"] == "whatsapp_message"]
    first = next(item for item in messages if item["source_label"] == "Ayşe Yılmaz")
    check(first["time"].startswith("2024-04-03T06:16:00") and first["timestamp_text"] == "03/04/2024, 09:16", "UTC time from the chosen timezone with the timestamp as written")
    check(first["location"]["line_start"] == 2 and first["acquisition_method"] == "authorized_import", "message cites its line in the imported chat text")
    entities = session.get(api(state, "/entities?limit=100")).body
    check(entities["total"] == 0, "sender labels created no entities or phone numbers")


def stage_documents(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    files = fixtures(args)

    def upload(name: str, ocr: str = "if_needed") -> dict[str, Any]:
        response = multipart(session, api(state, "/imports/documents"), files[name], f"{name}.pdf", import_origin="Synthetic PDF for acceptance", ocr=ocr)
        accepted = expect_status(response, 202, f"{name} PDF accepted").body
        return job(session, state, accepted["job"]["id"], args.timeout)

    text = upload("text")
    check(text["status"] == "completed" and text["result"]["document_state"] == "text_extracted", "text PDF extracted in the worker")
    layer = text["derived_evidence"][0]
    detail = session.get(api(state, f"/evidence/{layer['id']}")).body["evidence"]
    check(detail["collection_metadata"]["text_origin"] == "embedded_text_layer" and len(detail["collection_metadata"]["page_map"]) == 2, "extracted text carries its origin and page map")
    state["pdf_text_id"] = layer["id"]
    encrypted = upload("encrypted")
    check(encrypted["status"] == "failed" and encrypted["error_code"] == "pdf_encrypted" and not encrypted["derived_evidence"], "encrypted PDF is reported, not guessed")
    malformed = upload("malformed")
    check(malformed["status"] == "failed" and malformed["error_code"] == "pdf_malformed", "malformed PDF is reported")
    scanned = upload("scanned")
    if args.ocr:
        parts = {item["page_part"]: item for item in scanned["derived_evidence"]}
        check(scanned["status"] == "completed" and "ocr_text" in parts, f"scanned page recognised by Tesseract ({scanned['status']})")
        ocr_detail = session.get(api(state, f"/evidence/{parts['ocr_text']['id']}")).body["evidence"]
        engine = ocr_detail["collection_metadata"]["engine"]
        check(engine["name"] == "tesseract" and bool(engine["version"]), f"OCR record names its engine and version ({engine['version']})")
        preview = session.get(api(state, f"/evidence/{parts['ocr_text']['id']}/preview")).body
        check("SYNTHETIC" in preview["text"].upper(), "OCR text contains the rendered words")
    else:
        check(scanned["status"] == "partial" and scanned["error_code"] == "ocr_unavailable", "missing OCR engine gives an actionable partial state")
        check("INSTALL_OCR=true" in (scanned["error_detail"] or ""), "the state says how to enable OCR")
    refused = multipart(session, api(state, "/imports/documents"), b"<html><script>alert(1)</script></html>", "fake.pdf", import_origin="Synthetic")
    check(refused.status == 422, "a non-PDF upload is refused")

    def indexed() -> bool:
        states = session.get(api(state, "/ai/index?limit=100")).body
        items = {item["evidence_id"]: item["status"] for item in states.get("items", [])}
        return items.get(layer["id"]) == "indexed"

    wait_for("extracted text indexed", indexed, args.timeout, 2)
    hits = session.get(api(state, "/ai/search?q=Hosting%20moved%20203.0.113.9")).body["hits"]
    check(any(hit["evidence_id"] == layer["id"] for hit in hits), "extracted PDF text reaches case-scoped retrieval")


def stage_social_blocked(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    for connector, value, parameters in (
        ("instagram.account", "ornek.magaza", {}),
        ("telegram.public_channel", "ornekhaber", {"capability": "bot_api_chat_info"}),
        ("youtube.data_api", "@ornekkanal", {}),
    ):
        result = run(session, state, connector, "username", value, args.timeout, parameters=parameters)
        check(outcome(result) == ("authentication_required", "credential_not_configured") and result["evidence_count"] == 0, f"{connector} without credentials is blocked with nothing stored")


def stage_social(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    for connector, name, value in (
        ("instagram.account", "access_token", GRAPH_TOKEN),
        ("instagram.account", "ig_user_id", IG_USER_ID),
        ("telegram.public_channel", "bot_token", BOT_TOKEN),
        ("youtube.data_api", "api_key", YOUTUBE_KEY),
    ):
        response = session.send("POST", f"/api/v1/connectors/{connector}/credentials/{name}", {"value": value})
        check(response.status == 200 and value not in json.dumps(response.body), f"{connector} {name} stored write-only")

    official = run(session, state, "instagram.account", "username", "ornek.magaza", args.timeout, limits={"max_pages": 3, "max_items_per_page": 2})
    check(outcome(official)[0] == "findings" and official["connector_runs"][0]["pages_completed"] == 2, f"official Business Discovery with a media page ({outcome(official)})")
    state["instagram_run_id"] = official["id"]
    undiscoverable = run(session, state, "instagram.account", "username", "kisisel.hesap", args.timeout)
    check(outcome(undiscoverable) == ("unsupported", "instagram_account_not_discoverable"), "an account the API does not return is not reported as absent")
    disabled = run(session, state, "instagram.account", "username", "acik.profil", args.timeout, parameters={"capability": "public_profile_page"})
    check(outcome(disabled) == ("unsupported", "capability_disabled"), "unofficial profile page refuses to run while disabled")

    preview = run(session, state, "telegram.public_channel", "username", "ornekhaber", args.timeout, limits={"max_pages": 3, "max_items_per_page": 50})
    connector_run = preview["connector_runs"][0]
    check(outcome(preview)[0] == "findings" and connector_run["pages_completed"] == 2, f"Telegram preview with an older page ({outcome(preview)})")
    check(connector_run["coverage"].get("post_numbers_not_visible") in ([98, 99], [], None), "gaps between visible posts reported as not visible")
    evidence = session.get(api(state, f"/evidence?query_run_id={preview['id']}&limit=50")).body["items"]
    check(all(item["collection_mode"] == "platform_probe" and item["collection_metadata"].get("access_method") == "public_web_unofficial" for item in evidence), "preview evidence labelled as an unofficial public web capability")
    private = run(session, state, "telegram.public_channel", "username", "gizlikanal", args.timeout)
    check(outcome(private) == ("access_denied", "telegram_preview_unavailable"), "a channel without a preview is inaccessible, not empty")
    bot = run(session, state, "telegram.public_channel", "username", "ornekhaber", args.timeout, parameters={"capability": "bot_api_chat_info"})
    check(outcome(bot)[0] == "findings", f"Bot API chat metadata ({outcome(bot)})")
    bot_evidence = session.get(api(state, f"/evidence?query_run_id={bot['id']}&limit=10")).body["items"]
    check(all("bot[redacted]" in item["source_reference"] for item in bot_evidence), "bot token removed from recorded provenance")

    uploads = run(session, state, "youtube.data_api", "username", "@ornekkanal", args.timeout)
    check(outcome(uploads)[0] == "findings", f"YouTube channel uploads ({outcome(uploads)})")
    disabled_comments = run(session, state, "youtube.data_api", "youtube_video_id", "yorumkapali", args.timeout, parameters={"capability": "video_comments"})
    check(outcome(disabled_comments)[0] == "findings" and disabled_comments["connector_runs"][0]["coverage"].get("comments_status") == "disabled", "disabled comments are a stated condition, not an error or absence")
    quota = run(session, state, "youtube.data_api", "username", "@kotadolu", args.timeout)
    check(outcome(quota) == ("rate_limited", "youtube_quota_exceeded"), "exhausted quota is rate_limited")

    all_evidence = session.get(api(state, "/evidence?acquisition_method=connector_collection&limit=100")).body["items"]
    dumped = json.dumps(all_evidence)
    check(not any(secret in dumped for secret in SECRETS), "no token, bot token or API key in collected evidence metadata")

    session.send("POST", "/api/v1/connectors/instagram.account/credentials/access_token", {"value": GRAPH_REJECTED})
    rejected = run(session, state, "instagram.account", "username", "ornek.magaza", args.timeout)
    check(outcome(rejected) == ("authentication_required", "instagram_token_invalid"), "an expired token is authentication_required")
    listing = {c["connector_id"]: c for c in session.get("/api/v1/connectors").body}
    token = next(c for c in listing["instagram.account"]["credentials"] if c["name"] == "access_token")
    check(token["last_result"] == "rejected", "the rejected token is marked on the Sources page")
    session.send("POST", "/api/v1/connectors/instagram.account/credentials/access_token", {"value": GRAPH_TOKEN})


def stage_web_enabled(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    listing = {c["connector_id"]: c for c in session.get("/api/v1/connectors").body}
    capability = next(c for c in listing["instagram.account"]["capabilities"] if c["name"] == "public_profile_page")
    check(capability["available"] is True, "profile page capability available after the administrator enabled it")
    page = run(session, state, "instagram.account", "username", "acik.profil", args.timeout, parameters={"capability": "public_profile_page"})
    check(outcome(page)[0] == "findings", f"public profile metadata read without login ({outcome(page)})")
    wall = run(session, state, "instagram.account", "username", "ornek.magaza", args.timeout, parameters={"capability": "public_profile_page"})
    check(outcome(wall) == ("authentication_required", "instagram_login_wall"), "a login wall is reported as a login wall")


def stage_compare(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    entities = session.get(api(state, "/entities?limit=100")).body["items"]
    check(len(entities) >= 3, f"collected entities available ({len(entities)})")
    manual = session.send("POST", api(state, "/entities"), {"entity_type": "platform_account", "display_name": "ornek.magaza elsewhere", "identifiers": [{"identifier_type": "username", "value": "ornek.magaza", "platform": "instagram.com"}]})
    expect_status(manual, 201, "analyst entity sharing a username")
    instagram = next(e for e in entities if e["display_name"].endswith("on Instagram"))
    params = f"entity_id={instagram['id']}&entity_id={manual.body['id']}"
    comparison = expect_status(session.get(api(state, f"/entity-comparison?{params}")), 200, "comparison built").body
    check(any(i["kind"] == "shared" for i in comparison["identifiers"]), "shared username shown")
    check(any("not proof that they are the same" in item for item in comparison["unresolved"]), "shared identifier left unresolved, not merged")
    check(len(session.get(api(state, "/entities?limit=100")).body["items"]) == len(entities) + 1, "comparison created or merged nothing")
    state["entity_ids"] = [instagram["id"], manual.body["id"]]
    timeline = session.get(api(state, "/timeline?limit=10")).body
    check(timeline["sections"]["dated"] > 0 and timeline["sections"]["undated"] > 0, "timeline separates dated and collection-only items")


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)
            if name == "href" and value:
                self.hrefs.append(value)


def stage_reports(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    selection = {
        "entity_ids": state["entity_ids"],
        "comparison_entity_ids": state["entity_ids"],
        "evidence_ids": [state["chat_evidence_id"], state["pdf_text_id"]],
        "include_timeline": True,
        "redact_terms": ["Ayşe Yılmaz"],
    }
    preview = expect_status(session.send("POST", api(state, "/reports/html/preview"), selection), 200, "report preview").body
    markup = preview["html"]
    audit = _Audit()
    audit.feed(markup)
    check("<script" not in markup.lower() and not re.search(r"<[^>]+\son[a-z]+\s*=", markup, re.I), "no scripts or event handlers in the report")
    check(not audit.tags & {"img", "iframe", "link", "object", "embed", "svg", "form", "input"}, "no elements that load or run content")
    check(all(h.startswith(("#", "http://", "https://")) for h in audit.hrefs), "only in-file anchors and plain http(s) links")
    check(all(h[1:] in audit.ids for h in audit.hrefs if h.startswith("#")), "every citation anchor resolves inside the file")
    check("default-src 'none'" in markup, "report forbids loading anything")
    check("ayşe yılmaz" not in markup.lower() and preview["redactions_applied"] > 0, "analyst redaction applied")
    check("&lt;script&gt;alert(&#x27;case&#x27;)&lt;/script&gt;" in markup, "hostile case title rendered as text")
    status, body, headers = session.raw("POST", api(state, "/reports/html"), json.dumps(selection).encode(), {"Content-Type": "application/json", "Accept": "text/html"})
    check(status == 200 and headers.get("content-disposition", "").startswith("attachment; filename=\"tracehollow-report-"), "report downloads as an attachment")
    check(b"<script" not in body.lower(), "downloaded report contains no scripts")


def stage_outsider(args: argparse.Namespace, state: dict[str, Any]) -> None:
    other = Session(args, OTHER, password("TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"))
    for method, path, body in (
        ("GET", "/processing-jobs", None),
        ("GET", f"/evidence/{state['chat_evidence_id']}/content", None),
        ("GET", "/timeline", None),
        ("GET", f"/entity-comparison?entity_id={state['entity_ids'][0]}&entity_id={state['entity_ids'][1]}", None),
        ("GET", "/reports/selectable", None),
        ("POST", "/reports/html", {}),
    ):
        response = other.get(api(state, path)) if method == "GET" else other.send(method, api(state, path), body)
        check(response.status == 404, f"non-member {method} {path.split('?')[0]} is not found")
    upload = multipart(other, api(state, "/imports/whatsapp"), CHAT.encode(), "chat.txt", import_origin="Synthetic", timezone="UTC")
    check(upload.status == 404, "non-member cannot import into the case")


def stage_delete(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = admin(args)
    response = session.send("POST", f"/api/v1/cases/{state['case_id']}/deletion", {"confirm_title": CASE_TITLE})
    deletion = expect_status(response, 202, "case deletion requested").body

    def done() -> dict[str, Any] | None:
        body = session.get(f"/api/v1/case-deletions/{deletion['id']}").body
        return body if body["status"] in ("completed", "failed") else None

    result = wait_for("case deletion", done, args.timeout)
    check(result["status"] == "completed", f"case deletion completed ({result['status']})")
    check(result["removed_counts"].get("processing_jobs", 0) > 0 and result["removed_counts"].get("evidence_files", 0) > 0, "processing jobs and evidence files counted as removed")


STAGES = {
    "seed": stage_seed,
    "whatsapp": stage_whatsapp,
    "documents": stage_documents,
    "social-blocked": stage_social_blocked,
    "social": stage_social,
    "web-enabled": stage_web_enabled,
    "compare": stage_compare,
    "reports": stage_reports,
    "outsider": stage_outsider,
    "delete": stage_delete,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--timeout", type=float, default=150)
    parser.add_argument("--fixtures", default="")
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    args.origin = args.origin or args.web_url.rstrip("/")
    state = load_state(args.state)
    print(f"Stage: {args.stage}")
    try:
        STAGES[args.stage](args, state)
    except SmokeFailure as failure:
        print(f"FAIL {failure}", file=sys.stderr)
        return 1
    finally:
        save_state(args.state, state)
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
