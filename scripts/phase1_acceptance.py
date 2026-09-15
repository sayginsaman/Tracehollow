#!/usr/bin/env python3
"""Phase 1 acceptance checks against a running Tracehollow stack (Python standard library only).

Each stage is a separate invocation so scripts/verify-phase1.sh can restart, stop or break
services between stages. Stages share record identifiers (never secrets) through --state.

Stages:
  seed        first-run setup, case, entities, evidence imports, links, hostile imports
  reopen      the seeded records are intact after a restart (including evidence hashes)
  lifecycle   saved query executed twice, parameter snapshots, fixture outcomes, cancellation
  start-run   create a run for a new saved query and record its id under --key
  await-run   wait for the run under --key to finish and check it has no duplicate results
  authz       a non-member account cannot read the case, its evidence, runs or exports
  export      JSON and CSV exports: manifest, hashes, provenance, formula neutralization
  delete      typed-title deletion job completes and the case disappears from the API

Passwords come from TRACEHOLLOW_ACCEPTANCE_PASSWORD and TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD
and are never printed. All data is synthetic; the fixture connector contacts no network service.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

from smoke_test import ROOT, Client, Response, SmokeFailure, _response, check, expect_status

ADMIN = "acceptance-admin"
OTHER = "acceptance-outsider"
CASE_TITLE = "Örnek altyapı incelemesi (synthetic acceptance)"
FORMULA = '=HYPERLINK("http://attacker.example","click")'
MAX_IMPORT_BYTES = 5 * 1024 * 1024


class Session:
    """A signed-in browser-like client that talks to the API through the web proxy."""

    def __init__(self, args: argparse.Namespace, username: str, password: str) -> None:
        self.client = Client(args.web_url, args.origin)
        response = self.client.request(
            "POST", "/api/v1/auth/login", {"username": username, "password": password}
        )
        expect_status(response, 200, f"{username} signs in")
        self.csrf: str = response.body["csrf_token"]

    def get(self, path: str) -> Response:
        return self.client.request("GET", path)

    def send(self, method: str, path: str, body: Any = None) -> Response:
        return self.client.request(method, path, body, headers={"X-CSRF-Token": self.csrf})

    def raw(
        self, method: str, path: str, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, bytes, dict[str, str]]:
        merged = {"Origin": self.client.origin or "", "Sec-Fetch-Site": "same-origin"}
        if method != "GET":
            merged["X-CSRF-Token"] = self.csrf
        merged.update(headers or {})
        request = urllib.request.Request(
            self.client.base_url + path, data=data, method=method, headers=merged
        )
        try:
            with self.client.opener.open(request, timeout=60) as raw:
                return raw.status, raw.read(), {k.lower(): v for k, v in raw.headers.items()}
        except urllib.error.HTTPError as error:
            return error.code, error.read(), {k.lower(): v for k, v in error.headers.items()}

    def upload(self, case_id: str, content: bytes, filename: str, **fields: str) -> Response:
        boundary = f"tracehollow-{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                + value.encode()
                + b"\r\n"
            )
        quoted = filename.replace('"', "%22")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{quoted}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + content
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        status, body, headers = self.raw(
            "POST",
            f"/api/v1/cases/{case_id}/evidence/imports",
            b"".join(parts),
            {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
        )
        return _response(status, body, headers)

    def declare_upload(self, case_id: str, size: int) -> Response:
        """Send only the headers of an import declaring ``size`` body bytes and read the reply.

        The proxy rejects oversized requests from Content-Length and closes the connection
        without reading the body, so a client streaming the body would see a broken pipe.
        """
        parts = urllib.parse.urlsplit(self.client.base_url)
        connection = http.client.HTTPConnection(parts.hostname or "localhost", parts.port, timeout=30)
        try:
            connection.putrequest("POST", f"/api/v1/cases/{case_id}/evidence/imports")
            for name, value in {
                "Content-Type": "multipart/form-data; boundary=tracehollow-declared",
                "Content-Length": str(size),
                "Accept": "application/json",
                "Origin": self.client.origin or "",
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": self.csrf,
                "Cookie": "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.client.cookies),
            }.items():
                connection.putheader(name, value)
            connection.endheaders()
            reply = connection.getresponse()
            return _response(reply.status, reply.read(), reply.headers)
        finally:
            connection.close()


# -- helpers ------------------------------------------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2))


def password(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SmokeFailure(f"{name} must be set")
    return value


def case_path(state: dict[str, Any], suffix: str = "") -> str:
    return f"/api/v1/cases/{state['case_id']}{suffix}"


def error_code(response: Response) -> str | None:
    detail = response.body.get("detail") if isinstance(response.body, dict) else None
    if isinstance(detail, dict):
        return detail.get("code")
    return detail if isinstance(detail, str) else None


def saved_query(session: Session, state: dict[str, Any], name: str, scenario: str, **extra: Any) -> dict[str, Any]:
    body = {
        "name": name,
        "input_type": "username",
        "input_value": extra.pop("input_value", "sule.yilmaz"),
        "connector_ids": ["synthetic.fixture"],
        "parameters": {"scenario": scenario},
        **extra,
    }
    response = expect_status(session.send("POST", case_path(state, "/saved-queries"), body), 201, f"saved query '{name}' created")
    return response.body


def start_run(session: Session, state: dict[str, Any], query_id: str) -> dict[str, Any]:
    response = session.send("POST", case_path(state, f"/saved-queries/{query_id}/runs"))
    expect_status(response, 202, "execution accepted")
    return response.body


def wait_run(session: Session, state: dict[str, Any], run_id: str, timeout: float = 60, until: Any = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = session.get(case_path(state, f"/runs/{run_id}"))
        if response.status != 200:
            raise SmokeFailure(f"run {run_id} unreadable (HTTP {response.status})")
        run = response.body
        done = until(run) if until else run["status"] not in ("queued", "running")
        if done:
            return run
        if time.monotonic() > deadline:
            raise SmokeFailure(f"run {run_id} did not reach the expected state in {timeout:.0f}s (status {run['status']})")
        time.sleep(0.5)


def run_evidence(session: Session, state: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    response = expect_status(session.get(case_path(state, f"/evidence?query_run_id={run_id}&limit=100")), 200, "run evidence listed")
    items: list[dict[str, Any]] = response.body["items"]
    return items


def assert_unique_pages(session: Session, state: dict[str, Any], run: dict[str, Any]) -> None:
    evidence = run_evidence(session, state, run["id"])
    keys = [(item["connector_run_id"], item["page_index"]) for item in evidence]
    check(len(keys) == len(set(keys)), f"run #{run['run_number']}: no page was stored twice ({len(keys)} evidence records)")
    pages = sum(item["pages_completed"] for item in run["connector_runs"])
    check(len(keys) == pages == run["evidence_count"], f"run #{run['run_number']}: evidence records match completed pages ({pages})")
    observations = expect_status(session.get(case_path(state, f"/observations?query_run_id={run['id']}&limit=100")), 200, "run observations listed").body
    ids = [item["source_object_id"] for item in observations["items"]]
    check(len(ids) == len(set(ids)) == run["observation_count"], f"run #{run['run_number']}: no observation was stored twice ({len(ids)})")


# -- stages -------------------------------------------------------------------------------------


def stage_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    setup = {"setup_token": token, "username": ADMIN, "password": password("TRACEHOLLOW_ACCEPTANCE_PASSWORD")}
    expect_status(web.request("POST", "/api/v1/setup/admin", setup), 201, "administrator created")
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))

    print("Case")
    expect_status(session.get("/api/v1/cases"), 200, "case list readable")
    created = session.send("POST", "/api/v1/cases", {"title": CASE_TITLE, "purpose": "Phase 1 acceptance", "scope": "Synthetic data only", "tags": ["acceptance", "ACCEPTANCE", "synthetic"]})
    expect_status(created, 201, "case created")
    check(created.body["tags"] == ["acceptance", "synthetic"], "duplicate tags are folded")
    state["case_id"] = created.body["id"]

    print("Entities and identifiers")
    org = expect_status(session.send("POST", case_path(state, "/entities"), {"entity_type": "organization", "display_name": "Örnek A.Ş.", "identifiers": [{"identifier_type": "name", "value": "Örnek Anonim Şirketi"}]}), 201, "organization entity created").body
    domain = expect_status(session.send("POST", case_path(state, "/entities"), {"entity_type": "domain", "display_name": "ornek.example", "identifiers": [{"identifier_type": "domain", "value": "ORNEK.example"}]}), 201, "domain entity created").body
    handle = expect_status(session.send("POST", case_path(state, "/entities"), {"entity_type": "username", "display_name": FORMULA, "identifiers": [{"identifier_type": "username", "value": "İlkay"}]}), 201, "username entity with a spreadsheet formula name created").body
    account = expect_status(session.send("POST", case_path(state, "/entities"), {"entity_type": "platform_account", "display_name": "ilkay on synthetic-social.example", "identifiers": [{"identifier_type": "username", "value": "ilkay"}]}), 201, "platform account entity created").body
    check(domain["identifiers"][0]["normalized_value"] == "ornek.example", "domain identifier keeps original and normalized values")
    detail = expect_status(session.get(case_path(state, f"/entities/{handle['id']}")), 200, "username entity detail readable").body
    shared = [item["entity_id"] for item in detail["shared_identifiers"]]
    check(shared == [account["id"]], "Turkish-aware username match (İlkay / ilkay) shown as a hint, not merged")
    entities = expect_status(session.get(case_path(state, "/entities?limit=100")), 200, "entity list readable").body
    check(entities["total"] == 4, "platform account and username remain separate entities")

    print("Evidence imports")
    text = "Kayıt: ornek.example, Örnek A.Ş. tarafından tescil edildi. <script>alert(1)</script>\n".encode()
    imported = session.upload(state["case_id"], text, "registry-extract.txt", kind="text", title="Registry extract", import_origin="Analyst copy of a synthetic registry page", source_reference="https://registry.example/ornek", source_published_at="2026-09-01T10:00:00+03:00")
    expect_status(imported, 201, "text evidence imported")
    evidence = imported.body["evidence"]
    check(evidence["sha256"] == hashlib.sha256(text).hexdigest(), "stored SHA-256 matches the uploaded bytes")
    check(evidence["acquisition_method"] == "authorized_import" and evidence["import_origin"], "import provenance recorded")
    check(evidence["source_published_at"].startswith("2026-09-01T07:00:00") and evidence["source_published_at_original"] == "2026-09-01T10:00:00+03:00", "source date stored in UTC with the original value")
    state["evidence_id"] = evidence["id"]
    state["evidence_sha256"] = evidence["sha256"]

    payload = json.dumps({"domain": "ornek.example", "registrant": "Örnek A.Ş."}, ensure_ascii=False).encode()
    unsafe = session.upload(state["case_id"], payload, "../../etc/passwd‮txt.json", kind="json", import_origin="Synthetic WHOIS-style record")
    expect_status(unsafe, 201, "JSON evidence with an unsafe filename imported")
    name = unsafe.body["evidence"]["original_filename"]
    check(unsafe.body["filename_sanitized"] and "/" not in name and ".." not in name and "‮" not in name, f"unsafe filename reduced to a safe display name ({name!r})")
    state["json_evidence_id"] = unsafe.body["evidence"]["id"]
    again = session.upload(state["case_id"], text, "copy.txt", kind="text", import_origin="Same bytes imported again")
    expect_status(again, 201, "identical bytes imported as a separate record")
    check(again.body["duplicate_of"] == [evidence["id"]] and again.body["evidence"]["id"] != evidence["id"], "duplicate content is reported, never silently overwritten")

    print("Hostile and malformed imports")
    for content, kind, code, label in [
        (b"{not json", "json", "invalid_json", "malformed JSON"),
        (b"GIF89a\x01\x00\x00\x00;", "text", "binary_content", "binary content (NUL bytes)"),
        (b"\xff\xfe\xfa", "text", "invalid_encoding", "invalid UTF-8"),
        (b"", "text", "empty_content", "empty file"),
        (b"[" * 200 + b"]" * 200, "json", "json_too_deep", "deeply nested JSON"),
    ]:
        rejected = session.upload(state["case_id"], content, "bad.bin", kind=kind, import_origin="Hostile acceptance input")
        expect_status(rejected, 422, f"{label} rejected")
        check(error_code(rejected) == code, f"{label} reports '{code}'")
    missing = session.upload(state["case_id"], text, "no-origin.txt", kind="text")
    expect_status(missing, 422, "import without an import origin rejected")
    oversize = session.upload(state["case_id"], b"a" * (MAX_IMPORT_BYTES + 1), "big.txt", kind="text", import_origin="Oversized acceptance input")
    expect_status(oversize, 413, "file one byte above the 5 MiB import limit rejected")
    check(error_code(oversize) == "evidence_too_large", "oversized file reports 'evidence_too_large'")
    flood = session.declare_upload(state["case_id"], 8 * 1024 * 1024)
    expect_status(flood, 413, "request declaring an 8 MiB body rejected by the web proxy before the body is sent")
    check(error_code(flood) == "request_body_too_large", "oversized request body reports 'request_body_too_large'")
    listing = expect_status(session.get(case_path(state, "/evidence?limit=100")), 200, "evidence list readable").body
    check(listing["total"] == 3, "rejected imports left no evidence records")

    print("Links, relationship and notes")
    expect_status(session.send("POST", case_path(state, f"/entities/{domain['id']}/evidence-links"), {"evidence_id": state["evidence_id"], "note": "Registry names the domain"}), 201, "evidence linked to the domain entity")
    relationship = session.send("POST", case_path(state, "/relationships"), {"source_entity_id": org["id"], "target_entity_id": domain["id"], "predicate": "owns", "supporting_evidence_ids": [state["evidence_id"]]})
    expect_status(relationship, 201, "typed relationship created with supporting evidence")
    check(relationship.body["origin"] == "analyst_assertion" and relationship.body["review_status"] == "unreviewed", "manual relationship is an unreviewed analyst assertion")
    review = session.send("POST", case_path(state, f"/relationships/{relationship.body['id']}/review"), {"review_status": "accepted", "rationale": "Registry extract names the registrant."})
    expect_status(review, 200, "review decision recorded")
    note = session.send("POST", case_path(state, "/notes"), {"body": "Registrant confirmed from the extract.", "entity_id": domain["id"]})
    expect_status(note, 201, "note attached to the domain entity")
    state.update(org_id=org["id"], domain_id=domain["id"], relationship_id=relationship.body["id"], note_id=note.body["id"])


def stage_reopen(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    case = expect_status(session.get(case_path(state)), 200, "case reopened after restart").body
    check(case["title"] == CASE_TITLE, "case title intact (Turkish characters preserved)")
    check(case["counts"]["entities"] == 4 and case["counts"]["evidence"] == 3 and case["counts"]["relationships"] == 1, f"record counts intact {case['counts']}")
    evidence = expect_status(session.get(case_path(state, f"/evidence/{state['evidence_id']}")), 200, "evidence detail reopened").body
    check(evidence["integrity"]["status"] == "verified", "stored evidence file re-hashed and verified after restart")
    check([item["entity_id"] for item in evidence["linked_entities"]] == [state["domain_id"]], "evidence-to-entity link intact")
    check([item["relationship_id"] for item in evidence["linked_relationships"]] == [state["relationship_id"]], "evidence-to-relationship reference intact")
    status, content, headers = session.raw("GET", case_path(state, f"/evidence/{state['evidence_id']}/content"))
    check(status == 200 and hashlib.sha256(content).hexdigest() == state["evidence_sha256"] == headers.get("x-evidence-sha256"), "original bytes downloadable through the web proxy with matching hash")
    check(headers.get("content-disposition", "").startswith("attachment;"), "download is an attachment, never rendered inline")
    preview = expect_status(session.get(case_path(state, f"/evidence/{state['evidence_id']}/preview")), 200, "preview readable").body
    check("<script>" in preview["text"], "preview returns the text verbatim for plain-text rendering")
    relationship = expect_status(session.get(case_path(state, f"/relationships/{state['relationship_id']}")), 200, "relationship reopened").body
    check(relationship["review_status"] == "accepted" and relationship["references"][0]["evidence_id"] == state["evidence_id"], "review status and supporting evidence intact")
    check(relationship["decisions"][0]["rationale"] == "Registry extract names the registrant.", "analyst decision history intact")
    notes = expect_status(session.get(case_path(state, "/notes?limit=10")), 200, "notes readable").body
    check([item["id"] for item in notes["items"]] == [state["note_id"]], "note intact")


def stage_lifecycle(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))

    print("One saved query executed twice")
    query = saved_query(session, state, "Username candidates", "findings", limits={"max_pages": 3, "max_items_per_page": 2})
    first = wait_run(session, state, start_run(session, state, query["id"])["id"])
    check(first["status"] == "completed" and first["run_number"] == 1, "first execution completed")
    edited = session.send("PATCH", case_path(state, f"/saved-queries/{query['id']}"), {"input_value": "ilkay"})
    expect_status(edited, 200, "saved query edited between executions")
    second = wait_run(session, state, start_run(session, state, query["id"])["id"])
    check(second["status"] == "completed" and second["run_number"] == 2 and second["id"] != first["id"], "second execution is a separate run")
    first_again = expect_status(session.get(case_path(state, f"/runs/{first['id']}")), 200, "first run still readable").body
    check(first_again["parameters_snapshot"]["input_value"] == "sule.yilmaz", "first run keeps its original parameter snapshot")
    check(second["parameters_snapshot"]["input_value"] == "ilkay", "second run captured the edited parameters")
    check(first_again["evidence_count"] == first["evidence_count"] == 3 and first_again["finished_at"] == first["finished_at"], "rerun did not change the first run's results")
    first_ids = {item["id"] for item in run_evidence(session, state, first["id"])}
    second_ids = {item["id"] for item in run_evidence(session, state, second["id"])}
    check(len(first_ids) == len(second_ids) == 3 and not first_ids & second_ids, "each run has its own evidence records")
    for run in (first_again, second):
        assert_unique_pages(session, state, run)
    history = expect_status(session.get(case_path(state, f"/runs?saved_query_id={query['id']}")), 200, "execution history listed").body
    check(history["total"] == 2, "execution history shows both runs")

    print("Fixture outcomes are reported truthfully")
    expectations = [
        ("no_findings", "completed", "no_findings", 0, 1),
        ("partial", "partial", "partial", 2, 1),
        ("failure", "failed", "unavailable", 2, 0),
        ("flaky", "completed", "findings", 1, 3),
        ("rate_limited", "completed", "findings", 1, 3),
        ("authentication_required", "failed", "authentication_required", 0, 0),
        ("parse_error", "failed", "parse_error", 0, 0),
    ]
    for scenario, status, outcome, retries, evidence_count in expectations:
        query = saved_query(session, state, f"Scenario {scenario}", scenario)
        run = wait_run(session, state, start_run(session, state, query["id"])["id"], timeout=90)
        connector = run["connector_runs"][0]
        check(
            (run["status"], connector["outcome"], connector["retries"], run["evidence_count"]) == (status, outcome, retries, evidence_count),
            f"{scenario}: status={run['status']} outcome={connector['outcome']} retries={connector['retries']} evidence={run['evidence_count']}",
        )
        if status != "completed":
            check(bool(connector["coverage_note"] and connector["last_error_code"]), f"{scenario}: coverage note and error code explain the gap")

    print("Cancellation keeps collected evidence")
    query = saved_query(session, state, "Slow run", "slow", limits={"max_pages": 10, "max_items_per_page": 1})
    run = start_run(session, state, query["id"])
    wait_run(session, state, run["id"], until=lambda r: r["connector_runs"] and r["connector_runs"][0]["pages_completed"] >= 2, timeout=60)
    canceled = expect_status(session.send("POST", case_path(state, f"/runs/{run['id']}/cancel")), 200, "cancellation requested")
    check(canceled.body["cancel_requested_at"] is not None, "cancellation request recorded in PostgreSQL")
    final = wait_run(session, state, run["id"], timeout=60)
    connector = final["connector_runs"][0]
    check(final["status"] == "canceled" and connector["outcome"] == "canceled", "run and connector report 'canceled'")
    check(2 <= connector["pages_completed"] < 10 and final["evidence_count"] == connector["pages_completed"], f"{connector['pages_completed']} page(s) collected before cancellation kept")
    assert_unique_pages(session, state, final)
    kept = run_evidence(session, state, final["id"])[0]
    status, content, _ = session.raw("GET", case_path(state, f"/evidence/{kept['id']}/content"))
    check(status == 200 and hashlib.sha256(content).hexdigest() == kept["sha256"], "evidence from the canceled run is downloadable and intact")
    check(b"SYNTHETIC FIXTURE DATA" in content, "fixture evidence is labelled synthetic in its content")
    again = session.send("POST", case_path(state, f"/runs/{run['id']}/cancel"))
    check(again.status in (200, 409), f"cancelling a finished run is harmless (HTTP {again.status})")
    state["canceled_run_id"] = final["id"]


def stage_start_run(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    query = saved_query(session, state, f"Recovery {args.key}", args.scenario, limits={"max_pages": args.max_pages, "max_items_per_page": 1 if args.scenario == "slow" else 2})
    run = start_run(session, state, query["id"])
    check(run["status"] == "queued", "run is queued in PostgreSQL")
    if args.expect_dispatch:
        check(run["dispatch_status"] == args.expect_dispatch, f"dispatch status is '{args.expect_dispatch}'")
    state.setdefault("runs", {})[args.key] = run["id"]
    if args.wait_pages:
        wait_run(session, state, run["id"], until=lambda r: r["connector_runs"] and r["connector_runs"][0]["pages_completed"] >= args.wait_pages, timeout=60)
        print(f"  ok  run reached {args.wait_pages} completed page(s)")


def stage_await_run(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    run_id = state["runs"][args.key]
    run = wait_run(session, state, run_id, timeout=args.timeout)
    check(run["status"] == args.expect_status, f"{args.key}: run finished with status '{run['status']}'")
    if args.expect_evidence is not None:
        check(run["evidence_count"] == args.expect_evidence, f"{args.key}: {run['evidence_count']} evidence record(s), expected {args.expect_evidence}")
    check(run["dispatch_status"] == "done", f"{args.key}: outbox row marked done")
    assert_unique_pages(session, state, run)


def stage_authz(args: argparse.Namespace, state: dict[str, Any]) -> None:
    outsider = Session(args, OTHER, password("TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"))
    listing = expect_status(outsider.get("/api/v1/cases"), 200, "outsider can list their own cases")
    check(listing.body["total"] == 0, "outsider sees no cases they are not a member of")
    run_id = state["canceled_run_id"]
    for method, path in [
        ("GET", case_path(state)),
        ("GET", case_path(state, "/entities")),
        ("GET", case_path(state, f"/entities/{state['domain_id']}")),
        ("GET", case_path(state, f"/relationships/{state['relationship_id']}")),
        ("GET", case_path(state, "/evidence")),
        ("GET", case_path(state, f"/evidence/{state['evidence_id']}")),
        ("GET", case_path(state, f"/evidence/{state['evidence_id']}/preview")),
        ("GET", case_path(state, f"/evidence/{state['evidence_id']}/content")),
        ("GET", case_path(state, "/runs")),
        ("GET", case_path(state, f"/runs/{run_id}")),
        ("GET", case_path(state, "/graph")),
        ("GET", case_path(state, "/notes")),
        ("GET", case_path(state, "/exports/json")),
        ("GET", case_path(state, "/exports/csv")),
        ("POST", case_path(state, f"/runs/{run_id}/cancel")),
        ("POST", case_path(state, "/entities")),
    ]:
        if method == "GET":
            status, body, _ = outsider.raw("GET", path, headers={"Accept": "application/json"})
        else:
            response = outsider.send(method, path, {"entity_type": "organization", "display_name": "x"} if path.endswith("/entities") else None)
            status, body = response.status, json.dumps(response.body).encode()
        check(status == 404 and CASE_TITLE.encode() not in body and state["evidence_sha256"].encode() not in body, f"outsider {method} {path.split(state['case_id'])[-1] or '/'} -> 404 without case data")
    upload = outsider.upload(state["case_id"], b"intrusion", "x.txt", kind="text", import_origin="Outsider attempt")
    expect_status(upload, 404, "outsider cannot import evidence into the case")
    deletion = outsider.send("POST", case_path(state, "/deletion"), {"confirm_title": CASE_TITLE})
    expect_status(deletion, 404, "outsider cannot delete the case")
    anonymous = Client(args.web_url, args.origin)
    expect_status(anonymous.request("GET", case_path(state, f"/evidence/{state['evidence_id']}/content")), 401, "anonymous evidence download rejected")
    expect_status(anonymous.request("GET", case_path(state, "/exports/json")), 401, "anonymous export rejected")
    owner = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    foreign = owner.send("POST", "/api/v1/cases", {"title": "Other case"})
    expect_status(foreign, 201, "second case created by the owner")
    cross = owner.get(f"/api/v1/cases/{foreign.body['id']}/evidence/{state['evidence_id']}")
    expect_status(cross, 404, "evidence id cannot be read through another case")
    cross_run = owner.get(f"/api/v1/cases/{foreign.body['id']}/runs/{run_id}")
    expect_status(cross_run, 404, "run id cannot be read through another case")
    state["other_case_id"] = foreign.body["id"]


def canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stage_export(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    out = Path(args.artifacts)
    out.mkdir(parents=True, exist_ok=True)

    print("JSON export")
    status, body, headers = session.raw("GET", case_path(state, "/exports/json"))
    check(status == 200 and "attachment" in headers.get("content-disposition", ""), "JSON export downloaded as an attachment")
    (out / "export.json").write_bytes(body)
    document = json.loads(body)
    manifest, data = document["manifest"], document["data"]
    check(manifest["case_id"] == state["case_id"] and manifest["format_version"], "manifest identifies the case and format version")
    check(hashlib.sha256(canonical(data)).hexdigest() == manifest["data_sha256"], "manifest data_sha256 matches the exported data")
    check(all(manifest["record_counts"][name] == len(records) for name, records in data.items()), "manifest record counts match the data")
    check(manifest["synthetic_data_present"] is True and manifest["acquisition_methods"].get("authorized_import") == 3, f"acquisition methods reported {manifest['acquisition_methods']}")
    outcomes = {gap["outcome"] for gap in manifest["coverage_gaps"]}
    check({"partial", "unavailable", "canceled", "authentication_required"} <= outcomes, "coverage gaps list failed, partial and canceled executions")
    check(manifest["source_dates"]["earliest_source_published_at"] is not None, "source dates summarized")
    evidence = {item["id"]: item for item in data["evidence"]}
    imported = evidence[state["evidence_id"]]
    check(imported["sha256"] == state["evidence_sha256"] and imported["import_origin"], "evidence provenance and hash exported")
    check(all("storage_key" not in item and "path" not in item for item in data["evidence"]), "internal storage paths not exported")
    relationship = next(item for item in data["relationships"] if item["id"] == state["relationship_id"])
    check(relationship["origin"] == "analyst_assertion" and relationship["review_status"] == "accepted", "relationship origin and review status exported")
    check(any(ref["evidence_id"] == state["evidence_id"] for ref in data["relationship_references"]), "supporting evidence references exported")
    check(all(item["collection_mode"] == "synthetic_fixture" for item in data["saved_queries"]), "saved queries labelled as synthetic fixture collection")
    lowered = body.lower()
    for marker in (b"password_hash", b"$argon2", b"csrf", b"session_token", b"lease_token", b"/data/evidence"):
        check(marker not in lowered, f"JSON export contains no '{marker.decode()}'")

    print("CSV export")
    status, body, headers = session.raw("GET", case_path(state, "/exports/csv"))
    check(status == 200 and headers.get("content-type", "").startswith("application/zip"), "CSV export downloaded as a ZIP archive")
    (out / "export.zip").write_bytes(body)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        for name, meta in manifest["files"].items():
            content = archive.read(name)
            check(hashlib.sha256(content).hexdigest() == meta["sha256"], f"{name}: hash matches manifest ({meta['records']} records)")
        check(names == {"manifest.json", *manifest["files"]}, "archive contains only the manifest and listed files")
        entities = archive.read("entities.csv")
        check(entities.startswith(b"\xef\xbb\xbf"), "CSV is UTF-8 with a byte order mark")
        rows = list(csv.DictReader(io.StringIO(entities.decode("utf-8-sig"))))
        names_out = [row["display_name"] for row in rows]
        check("'" + FORMULA in names_out and FORMULA not in names_out, "formula-like cell neutralized with a leading quote")
        check("Örnek A.Ş." in names_out, "Turkish text preserved in CSV")
        combined = b"".join(archive.read(name) for name in names).lower()
        for marker in (b"password_hash", b"$argon2", b"lease_token", b"/data/evidence"):
            check(marker not in combined, f"CSV export contains no '{marker.decode()}'")


def stage_delete(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    wrong = session.send("POST", case_path(state, "/deletion"), {"confirm_title": "wrong title"})
    expect_status(wrong, 422, "deletion with a mismatched confirmation is refused")
    check(error_code(wrong) == "confirmation_mismatch", "refusal explains the confirmation mismatch")
    expect_status(session.get(case_path(state)), 200, "case untouched after refused deletion")
    job = expect_status(session.send("POST", case_path(state, "/deletion"), {"confirm_title": CASE_TITLE}), 202, "deletion job queued").body
    blocked = session.send("POST", case_path(state, "/entities"), {"entity_type": "organization", "display_name": "late write"})
    check(blocked.status in (404, 409), f"writes are refused while deletion runs (HTTP {blocked.status})")
    deadline = time.monotonic() + 120
    while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
        time.sleep(1)
        progress = session.get(f"/api/v1/case-deletions/{job['id']}")
        if progress.status != 200:
            raise SmokeFailure(f"deletion progress unreadable (HTTP {progress.status})")
        job = progress.body
    check(job["status"] == "completed", f"deletion job completed ({job.get('progress_note')})")
    removed = job["removed_counts"]
    check(removed.get("evidence_files", 0) >= 3 and removed.get("evidence_objects", 0) >= 3, f"deletion job recorded removed counts {removed}")
    for path in (case_path(state), case_path(state, f"/evidence/{state['evidence_id']}/content"), case_path(state, "/exports/json")):
        expect_status(session.get(path), 404, f"GET {path.split(state['case_id'])[-1] or '/'} returns 404 after deletion")
    check(session.get(f"/api/v1/cases/{state['other_case_id']}").status == 200, "other cases are not affected")
    state["deletion_id"] = job["id"]


STAGES = {
    "seed": stage_seed,
    "reopen": stage_reopen,
    "lifecycle": stage_lifecycle,
    "start-run": stage_start_run,
    "await-run": stage_await_run,
    "authz": stage_authz,
    "export": stage_export,
    "delete": stage_delete,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--key", default="run")
    parser.add_argument("--scenario", default="findings")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--expect-dispatch", default=None)
    parser.add_argument("--wait-pages", type=int, default=0)
    parser.add_argument("--expect-status", default="completed")
    parser.add_argument("--expect-evidence", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--artifacts", default=".")
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
