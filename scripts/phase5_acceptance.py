#!/usr/bin/env python3
"""Phase 5 acceptance checks against a running stack with controlled fixtures.

Used by scripts/verify-phase5.sh (compose.verify-phase5.yaml). The monitored feed and the webhook
receiver are the fixture-site container; no real source or notification service is contacted.
Python standard library only; record identifiers (never secrets) are shared through --state. The
fixture's control endpoints and a few database adjustments (aging runs for retention) go through
`docker compose exec`, using the project selected by the calling script's environment.

Stages:
  seed              accounts (administrator, two analysts, viewer), cases, members, entities
  roles             viewer, analyst and administrator boundaries at API level, incl. AI and exports
  monitor           repeated collections: baseline, controlled change, partial run, real absence
  webhook           destination preview and typed-host enablement, signed redacted deliveries,
                    retries with a stable event id
  webhook-disabled  a pending delivery is blocked when its destination is disabled
  schedule-start    one-minute schedule; scheduled occurrences dispatched by the scheduler
  schedule-concurrent  several dispatchers: exactly one occurrence and one run per slot
  schedule-restart  after scheduler downtime: one run for the missed period, no burst
  crash-start       a slow collection is running (the calling script then kills the collector)
  crash-check       the run is recovered and finished once; budget reservations reconciled
  budget            concurrent runs against one case budget never exceed it; explicit exhaustion
  cancel            cancellation of one of two concurrent runs; disabling a monitor cancels its run
  revoke-start      runs and a monitor authorized by an analyst, queued while no collector runs,
                    then the analyst is demoted to viewer
  revoke-check      the queued run stops without work; the monitor pauses at its next slot
  stix              export validity, import, idempotent re-import, round trip, refused bundles
  retention         preview, typed activation, waiting for active work, tombstones and 410s
  audit             case and system audit trails: actors, outcomes, correlation, no secrets
  delete            case deletion stops monitors and removes deliveries and occurrences
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from phase1_acceptance import ADMIN, Session, load_state, password, save_state
from smoke_test import ROOT, Client, Response, SmokeFailure, _response, check, expect_status

FIXTURE = "http://fixture-site:8080"
FEED = f"{FIXTURE}/monitor/feed"
HOOK = f"{FIXTURE}/webhook/tracehollow"
ANALYST = "p5-analyst"
ANALYST2 = "p5-analyst-two"
VIEWER = "p5-viewer"
CASE_TITLE = "Phase 5 izleme incelemesi (synthetic)"
EXCHANGE_TITLE = "Phase 5 değişim hedefi (synthetic)"
# Feed text that must never reach notifications, webhooks, audit details or logs.
FEED_TEXT = ("İzmir ofisi", "Ankara toplantısı", "Yeni şube")
TERMINAL_RUN = ("completed", "partial", "failed", "canceled")


# -- plumbing -----------------------------------------------------------------------------------


def other_password() -> str:
    return password("TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD")


def login(args: argparse.Namespace, username: str) -> Session:
    secret = password("TRACEHOLLOW_ACCEPTANCE_PASSWORD") if username == ADMIN else other_password()
    return Session(args, username, secret)


def api(state: dict[str, Any], suffix: str = "", key: str = "case_id") -> str:
    return f"/api/v1/cases/{state[key]}{suffix}"


def wait_for(description: str, predicate: Any, timeout: float, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise SmokeFailure(f"timed out after {timeout:.0f}s waiting for {description}")
        time.sleep(interval)


def compose(*command: str, stdin: str | None = None) -> str:
    completed = subprocess.run(
        ["docker", "compose", *command], input=stdin, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise SmokeFailure(f"docker compose {command[0]} failed: {completed.stderr.strip()[-400:]}")
    return completed.stdout


def fixture(**params: Any) -> dict[str, Any]:
    """Change or read the fixture state through its loopback-only control endpoint."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    path = f"/monitor/control/set?{query}" if params else "/monitor/control/state"
    code = (
        "import sys, urllib.request; "
        f"sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8080{path}', timeout=10).read().decode())"
    )
    state: dict[str, Any] = json.loads(compose("exec", "-T", "fixture-site", "python", "-c", code))
    return state


def sql(statement: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
    """Run one statement in the API container (verification-only adjustments such as aging runs)."""
    script = (
        "import json, sys\n"
        "from sqlalchemy import text\n"
        "from app.config import get_settings\n"
        "from app.db.session import create_db_engine\n"
        "request = json.load(sys.stdin)\n"
        "engine = create_db_engine(get_settings())\n"
        "with engine.begin() as connection:\n"
        "    result = connection.execute(text(request['statement']), request['params'])\n"
        "    rows = [list(row) for row in result] if result.returns_rows else []\n"
        "print(json.dumps(rows, default=str))\n"
    )
    output = compose(
        "exec", "-T", "api", "python", "-c", script, stdin=json.dumps({"statement": statement, "params": params or {}})
    )
    rows: list[list[Any]] = json.loads(output.strip().splitlines()[-1])
    return rows


def run_detail(session: Session, state: dict[str, Any], run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = expect_quiet(session.get(api(state, f"/runs/{run_id}")), 200).body
    return body


def expect_quiet(response: Response, status: int) -> Response:
    if response.status != status:
        raise SmokeFailure(f"HTTP {response.status}, expected {status}: {str(response.body)[:300]}")
    return response


def wait_run(session: Session, state: dict[str, Any], run_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = run_detail(session, state, run_id)
        return body if body["status"] in TERMINAL_RUN else None

    result: dict[str, Any] = wait_for(f"run {run_id[:8]} to finish", done, timeout)
    return result


def change_sets(session: Session, state: dict[str, Any], run_id: str, timeout: float) -> dict[str, Any]:
    """The single change set of a finished run, with its events."""

    def ready() -> dict[str, Any] | None:
        page = session.get(api(state, f"/change-sets?query_run_id={run_id}")).body
        return page if page["total"] >= 1 else None

    page = wait_for(f"change detection for run {run_id[:8]}", ready, timeout)
    check(page["total"] == 1, "one change set per connector run")
    detail: dict[str, Any] = session.get(api(state, f"/change-sets/{page['items'][0]['id']}")).body
    return detail


def events(detail: dict[str, Any]) -> set[tuple[str, str | None, str | None]]:
    return {(e["kind"], e["source_object_id"], e["field"]) for e in detail["events"]["items"]}


def run_now(session: Session, state: dict[str, Any], monitor_key: str = "monitor_id") -> dict[str, Any]:
    response = session.send("POST", api(state, f"/monitors/{state[monitor_key]}/runs"))
    occurrence: dict[str, Any] = expect_quiet(response, 202).body
    return occurrence


def monitor(session: Session, state: dict[str, Any], key: str = "monitor_id") -> dict[str, Any]:
    body: dict[str, Any] = expect_quiet(session.get(api(state, f"/monitors/{state[key]}")), 200).body
    return body


def occurrences(session: Session, state: dict[str, Any], key: str = "monitor_id") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = session.get(api(state, f"/monitors/{state[key]}/occurrences?limit=100")).body["items"]
    return items


def notifications(session: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = session.get("/api/v1/notifications?limit=100").body["items"]
    return items


def multipart(session: Session, path: str, content: bytes, filename: str, **fields: str) -> Response:
    boundary = f"tracehollow-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n"
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/json\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    status, body, headers = session.raw(
        "POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
    )
    return _response(status, body, headers)


def start_run(session: Session, state: dict[str, Any], query_key: str) -> str:
    response = session.send("POST", api(state, f"/saved-queries/{state[query_key]}/runs"))
    run_id: str = expect_quiet(response, 202).body["id"]
    return run_id


# -- seed and roles -----------------------------------------------------------------------------


def entity(session: Session, state: dict[str, Any], body: dict[str, Any]) -> str:
    created = expect_quiet(session.send("POST", api(state, "/entities"), body), 201).body
    entity_id: str = created["id"]
    return entity_id


def stage_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    setup = {"setup_token": token, "username": ADMIN, "password": password("TRACEHOLLOW_ACCEPTANCE_PASSWORD")}
    expect_status(web.request("POST", "/api/v1/setup/admin", setup), 201, "administrator created with the setup token")
    admin = login(args, ADMIN)
    session_info = admin.get("/api/v1/auth/session").body
    check(session_info["user"]["role"] == "administrator" and "accounts.manage" in session_info["permissions"], "the first account is an administrator")
    for username, role in ((ANALYST, "analyst"), (ANALYST2, "analyst"), (VIEWER, "viewer")):
        created = admin.send("POST", "/api/v1/admin/accounts", {"username": username, "role": role, "password": other_password()})
        expect_status(created, 201, f"administrator created local account {username} ({role})")
        state[f"user_{username}"] = created.body["id"]

    analyst = login(args, ANALYST)
    for key, title in (("case_id", CASE_TITLE), ("exchange_case_id", EXCHANGE_TITLE)):
        created = analyst.send("POST", "/api/v1/cases", {"title": title, "purpose": "Phase 5 acceptance", "scope": "Controlled fixtures only"})
        state[key] = expect_status(created, 201, f"analyst created case: {title}").body["id"]
    check(analyst.get(api(state)).body["my_role"] == "analyst", "the case creator is an analyst member")
    expect_status(analyst.send("POST", api(state, "/members"), {"username": ANALYST2, "role": "analyst"}), 201, "second analyst added")
    expect_status(analyst.send("POST", api(state, "/members"), {"username": VIEWER, "role": "viewer"}), 201, "viewer added")
    refused = analyst.send("POST", api(state, "/members"), {"username": VIEWER, "role": "analyst"})
    check(refused.status in (409, 422) and "viewer" in json.dumps(refused.body).lower(), "a viewer account cannot become an analyst member")

    evidence = Session.upload(analyst, state["case_id"], "Kayıt: ornek.example 203.0.113.9 adresine çözümleniyor.".encode(), "kayit.txt", kind="text", title="Kayıt notu", import_origin="Synthetic acceptance note")
    state["evidence_id"] = expect_status(evidence, 201, "evidence imported").body["evidence"]["id"]
    ids = {
        "domain": entity(analyst, state, {"entity_type": "domain", "display_name": "ornek.example", "identifiers": [{"identifier_type": "domain", "value": "ornek.example"}]}),
        "ip": entity(analyst, state, {"entity_type": "ip", "display_name": "203.0.113.9", "identifiers": [{"identifier_type": "ip", "value": "203.0.113.9"}]}),
        "url": entity(analyst, state, {"entity_type": "url", "display_name": "duyuru", "identifiers": [{"identifier_type": "url", "value": "https://ornek.example/duyuru"}]}),
        "email": entity(analyst, state, {"entity_type": "email", "display_name": "basin@ornek.example", "identifiers": [{"identifier_type": "email", "value": "basin@ornek.example"}]}),
        "account": entity(analyst, state, {"entity_type": "platform_account", "display_name": "ornek-dev", "identifiers": [{"identifier_type": "platform_id", "value": "90210001", "platform": "github.com"}, {"identifier_type": "username", "value": "ornek-dev", "platform": "github.com"}]}),
        "organization": entity(analyst, state, {"entity_type": "organization", "display_name": "Örnek A.Ş."}),
        "phone": entity(analyst, state, {"entity_type": "phone", "display_name": "+90 555 000 00 00", "identifiers": [{"identifier_type": "phone", "value": "+905550000000"}]}),
    }
    for source, target, predicate, extra in (
        ("domain", "ip", "resolves_to", {"supporting_evidence_ids": [state["evidence_id"]]}),
        ("account", "domain", "links_to", {}),
        ("organization", "domain", "operates", {}),
        ("phone", "organization", "belongs_to", {}),
    ):
        body = {"source_entity_id": ids[source], "target_entity_id": ids[target], "predicate": predicate, **extra}
        expect_quiet(analyst.send("POST", api(state, "/relationships"), body), 201)
    check(True, "synthetic entities and relationships added for exchange checks")
    state["entity_ids"] = ids


def stage_roles(args: argparse.Namespace, state: dict[str, Any]) -> None:
    viewer = login(args, VIEWER)
    detail = viewer.get(api(state)).body
    check(detail["my_role"] == "viewer" and detail["permissions"] == ["case.read"], "the viewer's effective role and permissions are reported")
    for path in ("", "/entities", "/relationships", "/evidence", f"/evidence/{state['evidence_id']}", "/members", "/monitors", "/change-sets", "/budgets", "/retention", "/ai/search?q=ornek"):
        expect_quiet(viewer.get(api(state, path)), 200)
    status, content, _ = viewer.raw("GET", api(state, f"/evidence/{state['evidence_id']}/content"))
    check(status == 200 and "ornek.example".encode() in content, "a viewer reads the case and downloads a single evidence file; keyword search works")

    refused: list[tuple[str, str, Any]] = [
        ("GET", "/exports/json", None),
        ("GET", "/exports/csv", None),
        ("GET", "/exports/stix", None),
        ("GET", "/exports/stix/report", None),
        ("GET", "/audit-events", None),
        ("POST", "/reports/html/preview", {}),
        ("POST", "/ai/conversations", {}),
        ("POST", "/ai/summaries", {}),
        ("POST", "/ai/relationship-suggestions", {}),
        ("POST", "/ai/index/rebuild", {"scope": "all"}),
        ("POST", "/saved-queries", {"name": "q", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"]}),
        ("POST", "/members", {"username": ANALYST2, "role": "viewer"}),
        ("PUT", "/budgets", {"budgets": []}),
        ("POST", "/retention/preview", {"collected_results_max_age_days": 30}),
        ("POST", "/deletion", {"confirm_title": CASE_TITLE}),
    ]
    for method, path, body in refused:
        response = viewer.send(method, api(state, path), body)
        if response.status != 403 or response.body["detail"]["code"] != "insufficient_case_role" or "ornek" in json.dumps(response.body):
            raise SmokeFailure(f"viewer {method} {path}: HTTP {response.status} {str(response.body)[:200]}")
    stix = multipart(viewer, api(state, "/imports/stix"), b'{"type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000000", "objects": []}', "b.json", import_origin="viewer attempt")
    check(stix.status == 403, f"a viewer is refused {len(refused) + 1} changes, exports, AI requests, imports and deletion (403 insufficient_case_role, no case content)")

    admin = login(args, ADMIN)
    for path in ("", "/evidence", f"/evidence/{state['evidence_id']}/content", "/ai/search?q=ornek", "/exports/json", "/monitors"):
        response = admin.get(api(state, path))
        if response.status != 404:
            raise SmokeFailure(f"administrator (not a member) GET {path}: HTTP {response.status}")
    check(True, "an administrator who is not a member cannot open the case, its evidence, AI search, exports or monitors (404)")
    directory = admin.get("/api/v1/admin/cases?limit=50").body["items"]
    row = next(item for item in directory if item["id"] == state["case_id"])
    check(set(row) == {"id", "title", "status", "created_at", "member_count", "active_analyst_count"} and row["member_count"] == 3, "the administrator case directory shows titles and member counts only")

    analyst = login(args, ANALYST)
    for method, path in (("GET", "/api/v1/admin/accounts"), ("GET", "/api/v1/admin/cases"), ("GET", "/api/v1/admin/audit-events"), ("GET", "/api/v1/admin/notification-destinations")):
        response = analyst.send(method, path)
        if response.status != 403 or response.body["detail"]["code"] != "insufficient_account_role":
            raise SmokeFailure(f"analyst {method} {path}: HTTP {response.status}")
    check(True, "an analyst account cannot reach administration endpoints (403 insufficient_account_role)")
    cross = analyst.get(api(state, f"/evidence/{state['evidence_id']}", key="exchange_case_id"))
    check(cross.status == 404, "evidence is not reachable through another case's path")

    denials = admin.get("/api/v1/admin/audit-events?outcome=denied&limit=100").body
    viewer_denials = [event for event in denials["items"] if event["actor_label"] == VIEWER]
    check(len(viewer_denials) >= len(refused), f"{len(viewer_denials)} denied viewer requests are in the audit log")
    check(all(event["correlation_id"] for event in viewer_denials), "every denial carries the request correlation id")


# -- monitoring: change detection ---------------------------------------------------------------


def stage_monitor(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    fixture(feed="v1", slow=0, webhook_failures=0, reset_deliveries=1)
    query = analyst.send("POST", api(state, "/saved-queries"), {"name": "İzleme akışı", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"], "limits": {"max_pages": 3, "max_items_per_page": 50}})
    state["query_id"] = expect_status(query, 201, "saved query for the controlled feed").body["id"]
    body = {
        "name": "Akış izleme",
        "saved_query_id": state["query_id"],
        "schedule": {"kind": "interval", "every_minutes": 60},
        "timezone": "Europe/Istanbul",
        "scope": {"max_pages": 3, "max_items_per_page": 50},
        "limits": {"max_requests_per_run": 10, "max_items_per_run": 200, "max_run_seconds": 300},
        "budget": {"period": "day", "max_requests": 200},
    }
    unacknowledged = analyst.send("POST", api(state, "/monitors"), {**body, "enable": True})
    check(unacknowledged.status == 422 and "recurring" in json.dumps(unacknowledged.body), "enabling a live monitor without acknowledging recurring collection is refused")
    created = expect_status(analyst.send("POST", api(state, "/monitors"), body), 201, "monitor created").body
    check(created["status"] == "paused" and created["status_reason"] == "created" and created["next_run_at"] is None, "a new monitor starts paused with no next run")
    check(created["collects_live"] is True, "the monitor is labelled as contacting external sources")
    state["monitor_id"] = created["id"]

    def collect(label: str) -> dict[str, Any]:
        occurrence = run_now(analyst, state)
        run = wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
        detail = change_sets(analyst, state, run["id"], args.timeout)
        state[f"run_{label}"] = run["id"]
        return {"run": run, "changes": detail}

    first = collect("v1")
    check(first["run"]["status"] == "completed" and first["changes"]["status"] == "baseline_established", "first collection establishes the baseline")

    fixture(feed="v2")
    second = collect("v2")
    changes = second["changes"]
    check(changes["status"] == "changes_detected", "the controlled feed change is detected")
    check(events(changes) == {("new", "izleme-3", None), ("changed", "izleme-1", "title")}, "exactly one new item and one changed title are reported")
    edit = next(e for e in changes["events"]["items"] if e["kind"] == "changed")
    check(edit["previous_value"] == "Duyuru 1: İzmir ofisi" and edit["current_value"] == "Duyuru 1: İzmir ofisi taşındı", "the change shows the value before and after")
    check(edit["previous_evidence_available"] is True and edit["current_evidence_available"] is True, "both sides link to stored evidence")
    for evidence_id in (edit["previous_evidence_id"], edit["current_evidence_id"]):
        expect_quiet(analyst.get(api(state, f"/evidence/{evidence_id}")), 200)
    check(changes["baseline_query_run_id"] == state["run_v1"], "the change set names its baseline run")

    fixture(feed="partial")
    partial = collect("partial")
    check(partial["run"]["status"] == "partial", "the rate-limited second page makes the run partial")
    outcome = partial["run"]["connector_runs"][0]
    check(outcome["outcome"] == "rate_limited" or outcome["last_error_code"] == "http_429", "the connector outcome records the rate limit")
    check(partial["changes"]["status"] == "unknown" and partial["changes"]["counts"]["not_observed"] == 0, "the partial run reports unknown, never not observed")
    check(events(partial["changes"]) == {("unknown", "izleme-2", None), ("unknown", "izleme-3", None)}, "items the partial run did not reach are unknown")

    fixture(feed="v3")
    third = collect("v3")
    check(third["changes"]["baseline_query_run_id"] == state["run_v2"], "the next complete run is compared with the last complete run, not the partial one")
    check(events(third["changes"]) == {("not_observed", "izleme-2", None)}, "a complete collection reports the genuinely absent item as not observed, with no false new items")
    absent = third["changes"]["events"]["items"][0]
    check("not proof of deletion" in absent["note"], "absence is explained as not proof of deletion")

    time.sleep(3)
    for username in (ANALYST, ANALYST2):
        inbox = notifications(login(args, username))
        changed = [item for item in inbox if item["event_type"] == "change_detected" and item["monitor_id"] == state["monitor_id"]]
        check(len(changed) == 2, f"{username} received one notification per meaningful change set (2)")
        text = json.dumps(changed, ensure_ascii=False)
        check(not any(value in text for value in FEED_TEXT), f"{username}'s notifications contain no collected values")
    viewer_inbox = notifications(login(args, VIEWER))
    check(not [item for item in viewer_inbox if item["monitor_id"] == state["monitor_id"]], "the viewer is not notified (recipients: case analysts)")
    unread = analyst.get("/api/v1/notifications/unread-count").body["unread"]
    first_item = next(item for item in notifications(analyst) if item["event_type"] == "change_detected")
    expect_quiet(analyst.send("POST", f"/api/v1/notifications/{first_item['id']}/read"), 200)
    check(analyst.get("/api/v1/notifications/unread-count").body["unread"] == unread - 1, "marking a notification read updates the unread count")


# -- notifications: webhook ---------------------------------------------------------------------


def deliveries(path: str = "/webhook/tracehollow") -> list[dict[str, Any]]:
    return [item for item in fixture()["deliveries"] if item["path"] == path]


def stage_webhook(args: argparse.Namespace, state: dict[str, Any]) -> None:
    admin = login(args, ADMIN)
    status = admin.get("/api/v1/admin/notification-destinations/status").body
    check(status["adapter_enabled"] is True, "the webhook adapter is switched on for this verification stack only")
    for url, reason in (("http://169.254.169.254/latest/meta-data", "metadata address"), (f"{HOOK}?token=abc", "token in query")):
        refused = admin.send("POST", "/api/v1/admin/notification-destinations", {"name": "Refused", "url": url, "event_types": ["change_detected"]})
        check(refused.status == 422, f"destination refused: {reason}")
    created = admin.send("POST", "/api/v1/admin/notification-destinations", {"name": "Doğrulama alıcısı", "url": HOOK, "event_types": ["change_detected", "action_required"], "max_per_minute": 60})
    destination = expect_status(created, 201, "destination created").body
    check(destination["enabled"] is False, "a new destination is disabled")
    secret = destination["signing_secret"]
    check(isinstance(secret, str) and len(secret) >= 32, "the signing secret is shown once at creation")
    # Kept only in the temporary work directory so the calling script can search the logs for it.
    Path(args.work_dir, "signing-secret").write_text(secret)
    listed = admin.get("/api/v1/admin/notification-destinations").body
    check(secret not in json.dumps(listed) and all("signing_secret" not in item for item in listed), "the secret is not returned again")
    state["destination_id"] = destination["id"]
    path = f"/api/v1/admin/notification-destinations/{destination['id']}"
    preview = expect_status(admin.send("POST", f"{path}/preview"), 200, "payload preview available").body
    check(set(preview["body"]) <= {"schema", "event_id", "event_type", "severity", "occurred_at", "generator", "case_id", "monitor_id", "occurrence_id", "query_run_id", "summary", "link"}, "the previewed payload has only identifiers, counts and reason fields")
    wrong = admin.send("POST", f"{path}/enable", {"confirm_host": "hooks.other.example"})
    check(wrong.status == 422, "enabling with a different host typed is refused")
    check(destination["host"] == "fixture-site:8080", "the destination is identified by its host and port")
    enabled = expect_status(admin.send("POST", f"{path}/enable", {"confirm_host": destination["host"]}), 200, "destination enabled after typing its host").body
    check(enabled["enabled"] is True, "the destination is enabled")

    viewer = login(args, VIEWER)
    check(viewer.get(api(state, "/notification-destinations")).status == 403, "a viewer cannot list destinations to subscribe")
    analyst = login(args, ANALYST)
    available = analyst.get(api(state, "/notification-destinations")).body
    check([item["host"] for item in available] == ["fixture-site:8080"] and "url" not in available[0], "analysts see the destination name and host, not its URL")
    subscription = analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/subscriptions"), {"destination_id": destination["id"], "event_types": ["change_detected"]})
    subscribed = expect_status(subscription, 201, "analyst subscribed the monitor to the destination").body
    state["subscription_id"] = next(item["id"] for item in subscribed if item["destination_id"] == destination["id"])

    fixture(feed="v2", webhook_failures=1, reset_deliveries=1)
    occurrence = run_now(analyst, state)
    wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
    detail = change_sets(analyst, state, occurrence["query_run_id"], args.timeout)
    check(detail["status"] == "changes_detected", "a further controlled change is detected")

    received = wait_for("a successful webhook delivery", lambda: [d for d in deliveries() if d["status"] == 204], args.timeout, 2)
    attempts = deliveries()
    check(len(received) == 1 and len(attempts) == 2, "the receiver failed once and the delivery was retried once")
    event_ids = {item["headers"]["x-tracehollow-event-id"] for item in attempts}
    check(len(event_ids) == 1, "the retry carries the same event id (receivers deduplicate on it)")
    delivery = received[0]
    body_bytes = delivery["body"].encode()
    expected = "sha256=" + hmac.new(secret.encode(), delivery["headers"]["x-tracehollow-timestamp"].encode() + b"." + body_bytes, hashlib.sha256).hexdigest()
    check(hmac.compare_digest(expected, delivery["headers"]["x-tracehollow-signature"]), "the HMAC-SHA256 signature verifies with the signing secret")
    payload = json.loads(delivery["body"])
    check(payload["event_type"] == "change_detected" and payload["case_id"] == state["case_id"] and payload["summary"].get("new", 0) + payload["summary"].get("changed", 0) + payload["summary"].get("not_observed", 0) >= 1, "the payload names the event, case and change counts")
    text = delivery["body"]
    leaked = [value for value in (*FEED_TEXT, CASE_TITLE, "Akış izleme", ANALYST, "ornek.example", FEED) if value in text]
    check(not leaked, "the payload contains no case or monitor names, collected values, usernames or query inputs")
    check(delivery["client"].startswith("172.31.252."), "the delivery came from the collector over the egress network")
    check(delivery["headers"]["user-agent"].startswith("Tracehollow/"), "the delivery identifies Tracehollow")
    listing = admin.get(f"{path}/deliveries").body["items"]
    check(listing[0]["status"] == "delivered" and listing[0]["attempts"] == 2, "the delivery history shows two attempts and success")
    in_app = [item for item in notifications(analyst) if item["event_type"] == "change_detected"]
    check(len(in_app) == 3, "the in-app notification was created once for the change, independent of webhook retries")
    state["destination_path"] = path


def stage_webhook_disabled(args: argparse.Namespace, state: dict[str, Any]) -> None:
    admin = login(args, ADMIN)
    analyst = login(args, ANALYST)
    path = state["destination_path"]
    fixture(feed="v3", webhook_failures=100, reset_deliveries=1)
    occurrence = run_now(analyst, state)
    wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
    change_sets(analyst, state, occurrence["query_run_id"], args.timeout)

    def failing() -> dict[str, Any] | None:
        items = admin.get(f"{path}/deliveries").body["items"]
        latest = items[0] if items else None
        return latest if latest and latest["status"] == "pending" and latest["attempts"] >= 1 and latest["last_response_status"] == 500 else None

    pending = wait_for("a pending delivery after a receiver failure", failing, args.timeout, 1)
    expect_status(admin.send("POST", f"{path}/disable"), 200, "destination disabled while its delivery waits to retry")
    fixture(webhook_failures=0)
    # An attempt already in flight when the destination was disabled may still land (and fail).
    before = len(deliveries()) + 1

    def settled() -> dict[str, Any] | None:
        item = next(d for d in admin.get(f"{path}/deliveries").body["items"] if d["id"] == pending["id"])
        return item if item["status"] != "pending" else None

    blocked = wait_for("the retry to be rechecked", settled, args.timeout, 2)
    check(blocked["status"] == "blocked" and blocked["last_error_code"] == "destination_disabled", "the retry was blocked because the destination is disabled")
    check(len(deliveries()) <= before and not [d for d in deliveries() if d["status"] == 204], "nothing was delivered after the destination was disabled")
    audit = admin.get("/api/v1/admin/audit-events?action=notification.delivery_blocked&limit=10").body["items"]
    check(any(event["details"].get("error_code") == "destination_disabled" for event in audit), "the blocked delivery is audited")


# -- scheduling ---------------------------------------------------------------------------------


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def scheduled(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((item for item in items if item["kind"] == "scheduled"), key=lambda item: item["scheduled_for"])


def monitor_run_count(state: dict[str, Any]) -> int:
    return int(sql("SELECT count(*) FROM query_runs WHERE monitor_id = :id", {"id": state["monitor_id"]})[0][0])


def dispatched_count(state: dict[str, Any]) -> int:
    return int(sql("SELECT count(*) FROM monitor_occurrences WHERE monitor_id = :id AND status = 'dispatched'", {"id": state["monitor_id"]})[0][0])


def check_one_run_per_slot(state: dict[str, Any], label: str) -> None:
    duplicates = sql("SELECT scheduled_for, count(*) FROM monitor_occurrences WHERE monitor_id = :id GROUP BY scheduled_for HAVING count(*) > 1", {"id": state["monitor_id"]})
    check(not duplicates, f"{label}: no slot has more than one occurrence")
    check(monitor_run_count(state) == dispatched_count(state), f"{label}: exactly one execution per dispatched occurrence ({dispatched_count(state)})")
    outbox = sql("SELECT count(*) FROM dispatch_outbox o JOIN query_runs r ON r.id = o.aggregate_id WHERE r.monitor_id = :id AND o.aggregate_type = 'query_run'", {"id": state["monitor_id"]})
    check(int(outbox[0][0]) <= monitor_run_count(state), f"{label}: at most one outbox row per execution")


def stage_schedule_start(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    fixture(feed="v3", webhook_failures=0, reset_deliveries=1)
    expect_quiet(analyst.send("DELETE", api(state, f"/monitors/{state['monitor_id']}/subscriptions/{state['subscription_id']}")), 204)
    updated = analyst.send("PATCH", api(state, f"/monitors/{state['monitor_id']}"), {"schedule": {"kind": "interval", "every_minutes": 1}})
    expect_status(updated, 200, "schedule changed to every minute (the verification stack's floor)")
    unacknowledged = analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/resume"), {})
    check(unacknowledged.status == 422, "resuming a live monitor without the acknowledgement is refused")
    resumed = expect_status(analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/resume"), {"acknowledge_recurring_collection": True}), 200, "monitor resumed").body
    check(resumed["status"] == "enabled" and resumed["next_run_at"] is not None, "the enabled monitor shows its next run")
    state["schedule_started"] = time.time()
    baseline = len(scheduled(occurrences(analyst, state)))
    found = wait_for("two scheduled occurrences", lambda: (lambda items: items if len(items) >= baseline + 2 else None)(scheduled(occurrences(analyst, state))), 200, 5)
    latest = found[-2:]
    check(all(item["status"] == "dispatched" and item["dispatched_by"] != "" for item in latest), "the scheduler dispatched two slots on its own")
    gap = epoch(latest[1]["scheduled_for"]) - epoch(latest[0]["scheduled_for"])
    check(gap == 60, "consecutive slots are one interval apart")
    for item in latest:
        wait_run(analyst, state, item["query_run_id"], args.timeout)
    check_one_run_per_slot(state, "one scheduler")


def stage_schedule_concurrent(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    replicas = compose("ps", "--quiet", "dispatcher").split()
    check(len(replicas) == 3, "three dispatcher processes are running")
    start = len(scheduled(occurrences(analyst, state)))
    found = wait_for("three more scheduled occurrences", lambda: (lambda items: items if len(items) >= start + 3 else None)(scheduled(occurrences(analyst, state))), 260, 5)
    for item in found[start:]:
        if item["query_run_id"]:
            wait_run(analyst, state, item["query_run_id"], args.timeout)
    instances = {item["dispatched_by"] for item in found[start:]}
    print(f"  ..  slots were claimed by {len(instances)} distinct scheduler instance(s)")
    check_one_run_per_slot(state, "three competing schedulers")


def stage_schedule_mark(args: argparse.Namespace, state: dict[str, Any]) -> None:
    state["scheduler_stopped_at"] = time.time()
    state["occurrences_before_stop"] = len(scheduled(occurrences(login(args, ANALYST), state)))
    print("  ..  scheduler downtime starts")


def stage_schedule_restart(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    before = state["occurrences_before_stop"]
    downtime_items = wait_for("the first occurrence after the restart", lambda: (lambda items: items if len(items) > before else None)(scheduled(occurrences(analyst, state))), 180, 3)
    # Occurrences possibly dispatched in the seconds before every scheduler had stopped.
    stopped_at = state["scheduler_stopped_at"]
    after = [item for item in downtime_items[before:] if epoch(item["created_at"]) > stopped_at + 5]
    check(len(after) >= 1, "scheduling resumed after the restart")
    first = after[0]
    check(first["status"] == "dispatched" and first["missed_slots"] >= 1, f"one execution covers the downtime and records {first['missed_slots']} missed slot(s)")
    time.sleep(75)
    items = scheduled(occurrences(analyst, state))
    later_items = [item for item in items if item["scheduled_for"] > first["scheduled_for"]]
    check(len(later_items) <= 2 and all(item["missed_slots"] == 0 for item in later_items), "no catch-up burst: later slots run on the normal cadence")
    for item in [first, *later_items]:
        if item["query_run_id"]:
            wait_run(analyst, state, item["query_run_id"], args.timeout)
    check_one_run_per_slot(state, "after restart")
    paused = expect_quiet(analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/pause")), 200).body
    check(paused["status"] == "paused" and paused["next_run_at"] is None, "pausing stops future runs")
    time.sleep(70)
    check(len(scheduled(occurrences(analyst, state))) == len(items), "no occurrence is created while paused")


# -- crash recovery, budgets and cancellation ---------------------------------------------------


def reservations(run_id: str) -> dict[str, int]:
    rows = sql("SELECT status, count(*) FROM budget_reservations WHERE query_run_id = :id GROUP BY status", {"id": run_id})
    return {str(status): int(count) for status, count in rows}


def stage_crash_start(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    query = analyst.send("POST", api(state, "/saved-queries"), {"name": "Yavaş akış", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"], "limits": {"max_pages": 3, "max_items_per_page": 50, "max_requests": 10}})
    state["slow_query_id"] = expect_quiet(query, 201).body["id"]
    fixture(feed="v2", slow=25)
    requests_before = fixture()["feed_requests"]
    state["crash_run_id"] = start_run(analyst, state, "slow_query_id")
    wait_for("the collector to be inside the slow request", lambda: run_detail(analyst, state, state["crash_run_id"])["status"] == "running" and fixture()["feed_requests"] > requests_before, args.timeout, 1)
    time.sleep(3)
    held = reservations(state["crash_run_id"])
    check(held.get("held", 0) >= 1, "the in-flight request holds a budget reservation")


def stage_crash_check(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    fixture(slow=5)
    run = wait_run(analyst, state, state["crash_run_id"], 420)
    check(run["status"] == "completed", "the execution was recovered after the collector was killed and finished")
    claims = int(sql("SELECT claim_count FROM query_runs WHERE id = :id", {"id": state["crash_run_id"]})[0][0])
    check(claims >= 2, f"it was claimed {claims} times (the lost worker's lease expired)")
    # An uninterrupted collection of the same feed version is the reference (monitor stage, v2).
    reference = run_detail(analyst, state, state["run_v2"])
    evidence = analyst.get(api(state, f"/evidence?query_run_id={state['crash_run_id']}&limit=50")).body
    check(evidence["total"] == run["evidence_count"] == reference["evidence_count"], f"the feed page was stored once ({evidence['total']} records, as in an uninterrupted run), not once per attempt")
    check(len({item["collected_at"] for item in evidence["items"]}) == 1, "all stored records come from the one successful attempt")
    observations = analyst.get(api(state, f"/observations?query_run_id={state['crash_run_id']}&limit=50")).body
    keys = [item["source_object_id"] for item in observations["items"]]
    check(len(keys) == len(set(keys)) == reference["observation_count"], "each feed item was observed once")
    settled = wait_for("the lost reservation to be reconciled", lambda: (lambda value: value if value.get("held", 0) == 0 else None)(reservations(state["crash_run_id"])), 300, 5)
    check(settled.get("expired", 0) >= 1 and settled.get("settled", 0) >= 1, "the lost worker's reservation was counted as estimated use; the completed request as measured")
    ledger = sql("SELECT reserved_units, consumed_units, estimated_units FROM budget_ledgers WHERE scope_type = 'query_run' AND scope_id = :id", {"id": state["crash_run_id"]})
    check(bool(ledger) and ledger[0][0] == 0 and ledger[0][1] >= 1 and ledger[0][2] >= 1, "the run ledger shows measured and estimated requests separately, nothing held")
    fixture(slow=0)


def stage_budget(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    fixture(feed="paged", slow=3)
    usage = analyst.get(api(state, "/budgets")).body
    check(usage["budgets"] == [], "no case budget is set yet")
    # A fresh daily case budget of 5 requests; each run needs 2 (two feed pages).
    expect_status(analyst.send("PUT", api(state, "/budgets"), {"budgets": [{"metric": "requests", "period": "day", "limit_units": 5}]}), 200, "case budget set to 5 requests per day")
    current = analyst.get(api(state, "/budgets")).body["usage"][0]
    limit = current["consumed_units"] + current["estimated_units"] + 5
    if limit != 5:
        expect_quiet(analyst.send("PUT", api(state, "/budgets"), {"budgets": [{"metric": "requests", "period": "day", "limit_units": limit}]}), 200)
    query = analyst.send("POST", api(state, "/saved-queries"), {"name": "Sayfalı akış", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"], "limits": {"max_pages": 2, "max_items_per_page": 50}})
    state["paged_query_id"] = expect_quiet(query, 201).body["id"]
    runs = [start_run(analyst, state, "paged_query_id") for _ in range(4)]
    finished = [wait_run(analyst, state, run_id, args.timeout) for run_id in runs]
    stopped = [run for run in finished if run["error_code"] == "budget_exhausted"]
    complete = [run for run in finished if run["status"] == "completed"]
    check(len(complete) <= 2 and len(stopped) >= 2, f"{len(complete)} concurrent runs completed and {len(stopped)} stopped with budget_exhausted")
    check(all(run["status"] in ("partial", "failed") for run in stopped), "budget-stopped runs are partial or failed, never completed")
    final = analyst.get(api(state, "/budgets")).body["usage"][0]
    used = final["consumed_units"] + final["estimated_units"] + final["reserved_units"]
    check(used <= final["limit_units"] and final["reserved_units"] == 0, f"the case ledger never exceeded its limit ({used} of {final['limit_units']})")
    check(final["exhausted"] is True and final["denied_requests"] >= 1, "the ledger reports exhaustion and refused requests")
    for run in stopped:
        detail = change_sets(analyst, state, run["id"], args.timeout)
        check(detail["counts"]["not_observed"] == 0, "a budget-stopped run claims no absence")

    # The monitor is blocked by the case budget too, with an explicit skipped occurrence.
    blocked = analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/runs"))
    check(blocked.status == 409 and blocked.body["detail"]["code"] == "budget_exhausted", "run now is refused while the case budget is used up")
    skipped = [item for item in occurrences(analyst, state) if item["kind"] == "manual" and item["skip_reason"] == "budget_exhausted"]
    check(len(skipped) == 1 and skipped[0]["status"] == "skipped", "the refused attempt is recorded as a skipped occurrence")
    expect_quiet(analyst.send("PUT", api(state, "/budgets"), {"budgets": []}), 200)
    fixture(slow=0)


def stage_cancel(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    fixture(feed="paged", slow=8)
    first = start_run(analyst, state, "paged_query_id")
    second = start_run(analyst, state, "paged_query_id")
    wait_for("both runs to be running", lambda: all(run_detail(analyst, state, run)["status"] == "running" for run in (first, second)), args.timeout)
    wait_for("the first page of the first run", lambda: run_detail(analyst, state, first)["connector_runs"][0]["pages_completed"] >= 1, args.timeout)
    expect_status(analyst.send("POST", api(state, f"/runs/{first}/cancel")), 200, "cancellation requested for one of two concurrent runs")
    canceled = wait_run(analyst, state, first, args.timeout)
    other = wait_run(analyst, state, second, args.timeout)
    check(canceled["status"] == "canceled" and canceled["evidence_count"] >= 1, "the canceled run stopped and kept the page it had already collected")
    check(other["status"] == "completed", "the other concurrent run completed normally")
    check(reservations(first).get("held", 0) == 0, "the canceled run holds no budget reservation")
    viewer = login(args, VIEWER)
    check(viewer.send("POST", api(state, f"/runs/{second}/cancel")).status == 403, "a viewer cannot cancel executions")

    # Disabling a monitor cancels its active execution; pausing would not.
    fixture(feed="v2", slow=20)
    occurrence = run_now(analyst, state)
    wait_for("the monitor run to start", lambda: run_detail(analyst, state, occurrence["query_run_id"])["status"] == "running", args.timeout)
    disabled = expect_status(analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/disable")), 200, "monitor disabled during its run").body
    check(disabled["status"] == "disabled", "the monitor is disabled")
    run = wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
    check(run["status"] == "canceled", "its active execution was canceled")
    refused = analyst.send("POST", api(state, f"/monitors/{state['monitor_id']}/runs"))
    check(refused.status == 409, "a disabled monitor cannot be run")
    fixture(slow=0)


def stage_revoke_start(args: argparse.Namespace, state: dict[str, Any]) -> None:
    second = login(args, ANALYST2)
    fixture(feed="v2", slow=0)
    query = second.send("POST", api(state, "/saved-queries"), {"name": "İkinci analist sorgusu", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"], "limits": {"max_pages": 1, "max_items_per_page": 50}})
    state["revoked_query_id"] = expect_quiet(query, 201).body["id"]
    state["revoked_run_id"] = start_run(second, state, "revoked_query_id")
    created = second.send("POST", api(state, "/monitors"), {
        "name": "İkinci analist izlemesi",
        "saved_query_id": state["revoked_query_id"],
        "schedule": {"kind": "interval", "every_minutes": 1},
        "scope": {"max_pages": 1, "max_items_per_page": 50},
        "enable": True,
        "acknowledge_recurring_collection": True,
    })
    state["revoked_monitor_id"] = expect_status(created, 201, "second analyst enabled a monitor it authorizes").body["id"]
    check(run_detail(second, state, state["revoked_run_id"])["status"] == "queued", "the second analyst's run is queued (no collector running)")
    state["feed_requests_before_revoke"] = fixture()["feed_requests"]
    analyst = login(args, ANALYST)
    changed = analyst.send("PATCH", api(state, f"/members/{state['user_' + ANALYST2]}"), {"role": "viewer"})
    check(changed.status == 200 and changed.body["effective_role"] == "viewer", "the second analyst was demoted to viewer while its work was queued")


def stage_revoke_check(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    run = wait_run(analyst, state, state["revoked_run_id"], args.timeout)
    check(run["status"] == "canceled" and run["error_code"] == "authorization_revoked", "the queued run stopped with authorization_revoked when a worker picked it up")
    check(run["evidence_count"] == 0, "it collected nothing")

    def paused() -> dict[str, Any] | None:
        body = monitor(analyst, state, "revoked_monitor_id")
        return body if body["status"] == "paused" else None

    stopped = wait_for("the monitor authorized by the demoted analyst to pause", paused, 200, 5)
    check(stopped["status_reason"] == "authorization_lost", "the monitor paused at dispatch because its authorizer lost analyst access")
    items = occurrences(analyst, state, "revoked_monitor_id")
    check(any(item["skip_reason"] == "authorization_lost" for item in items), "the refused slot is recorded as a skipped occurrence")
    for item in items:
        if item["query_run_id"]:
            detail = run_detail(analyst, state, item["query_run_id"])
            check(detail["status"] == "canceled" and detail["evidence_count"] == 0, "any execution dispatched before the demotion stopped without collecting")
    check(fixture()["feed_requests"] == state["feed_requests_before_revoke"], "no request reached the feed on the demoted analyst's behalf")
    inbox = notifications(analyst)
    check(any(item["event_type"] == "action_required" and item["monitor_id"] == state["revoked_monitor_id"] for item in inbox), "remaining analysts are told the monitor needs action")


# -- STIX exchange ------------------------------------------------------------------------------


def stix_import(session: Session, state: dict[str, Any], content: bytes, key: str = "exchange_case_id", **fields: str) -> Response:
    return multipart(session, api(state, "/imports/stix", key=key), content, "bundle.json", import_origin="Synthetic STIX bundle from the Phase 5 verification", **fields)


def stix_view(bundle: dict[str, Any]) -> set[tuple[str, str]]:
    return {(item["type"], item["id"]) for item in bundle["objects"] if item["type"] in ("domain-name", "ipv4-addr", "url", "email-addr", "user-account")}


def stage_stix(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    report = expect_status(analyst.get(api(state, "/exports/stix/report")), 200, "STIX export report").body
    check(report["excluded"].get("entity_type_phone", 0) >= 1 and report["lossy"], "the report lists what cannot be exported and what is lost")
    status, raw, headers = analyst.raw("GET", api(state, "/exports/stix"))
    check(status == 200 and headers["content-type"].startswith("application/stix+json"), "STIX 2.1 bundle downloaded")
    Path(args.work_dir, "export.json").write_bytes(raw)
    bundle = json.loads(raw)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in bundle["objects"]:
        by_type.setdefault(item["type"], []).append(item)
    check(bundle["type"] == "bundle" and all(item.get("spec_version") == "2.1" for item in bundle["objects"]), "every object is STIX 2.1")
    check(len({item["id"] for item in bundle["objects"]}) == len(bundle["objects"]), "object identifiers are unique")
    check("ornek.example" in {d["value"] for d in by_type["domain-name"]} and "203.0.113.9" in {a["value"] for a in by_type["ipv4-addr"]}, "domain and address observables exported")
    accounts = [a for a in by_type["user-account"] if a.get("account_type") == "github.com"]
    check(len(accounts) == 1 and accounts[0]["user_id"] == "90210001", "the platform account keeps its platform")
    check({"resolves-to", "links-to", "operates"} <= {r["relationship_type"] for r in by_type["relationship"]}, "supported relationships exported with STIX-style names")
    check(not any("confidence" in item for item in bundle["objects"]), "no confidence values are invented")
    check(by_type["extension-definition"][0]["id"].startswith("extension-definition--"), "Tracehollow provenance travels in a declared extension")
    text = raw.decode()
    for secret in ("postgres_app_password", "redis_password", "app_secret_key", "credential_encryption_key", "bootstrap_token"):
        value = (ROOT / "secrets" / secret).read_text().strip()
        if value and value in text:
            raise SmokeFailure(f"the value of secrets/{secret} appears in the STIX export")
    check("+905550000000" not in text, "unsupported entity types (phone) are left out, not coerced")

    requests_before = fixture()["feed_requests"]
    imported = stix_import(analyst, state, raw)
    summary = expect_status(imported, 201, "bundle imported into a second case").body
    check(summary["already_imported"] is False and summary["created"].get("domain") == 1 and summary["created"].get("relationship", 0) >= 3, "supported objects created as imported records")
    entities_after_first = analyst.get(api(state, "/entities?limit=100", key="exchange_case_id")).body
    check(all(item["origin"] == "imported" for item in entities_after_first["items"]), "imported entities are labelled imported, not verified evidence")
    relationships = analyst.get(api(state, "/relationships?limit=100", key="exchange_case_id")).body["items"]
    check(relationships and all(item["origin"] == "imported" and item["review_status"] == "unreviewed" for item in relationships), "imported relationships start unreviewed")
    again = expect_status(stix_import(analyst, state, raw), 201, "the same bundle imported again").body
    check(again["already_imported"] is True, "a repeated import is recognized")
    check(analyst.get(api(state, "/entities?limit=100", key="exchange_case_id")).body["total"] == entities_after_first["total"], "a repeated import creates no duplicate entities")
    status, round_raw, _ = analyst.raw("GET", api(state, "/exports/stix", key="exchange_case_id"))
    round_trip = json.loads(round_raw)
    check(status == 200 and stix_view(bundle) <= stix_view(round_trip), "the supported observables round-trip with identical STIX identifiers")
    Path(args.work_dir, "round-trip.json").write_bytes(round_raw)

    probe = {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": [{"type": "url", "spec_version": "2.1", "id": f"url--{uuid.uuid4()}", "value": f"{FEED}?stix-probe=1"}]}
    expect_status(stix_import(analyst, state, json.dumps(probe).encode()), 201, "a bundle with a URL imported")
    check(fixture()["feed_requests"] == requests_before, "imported URLs are recorded, never fetched")

    malware = {"type": "malware", "spec_version": "2.1", "id": f"malware--{uuid.uuid4()}", "created": "2026-09-01T00:00:00.000Z", "modified": "2026-09-01T00:00:00.000Z", "name": "sentetik", "is_family": False}
    unsupported = {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": [malware, {"type": "domain-name", "spec_version": "2.1", "id": "domain-name--" + str(uuid.uuid5(uuid.NAMESPACE_URL, "stix-probe")), "value": "probe.example"}]}
    strict = stix_import(analyst, state, json.dumps(unsupported).encode(), on_unsupported="reject")
    check(strict.status == 422 and any(item.get("type") == "malware" for item in strict.body["detail"].get("objects", [])), "strict import refuses the bundle and lists the unsupported object")
    lenient = expect_status(stix_import(analyst, state, json.dumps(unsupported).encode()), 201, "lenient import of the same bundle").body
    check(sum(lenient["skipped"].values()) >= 1 and any(item["type"] == "malware" for item in lenient["skipped_objects"]), "unsupported objects are skipped and listed")

    deep: Any = {"value": "x"}
    for _ in range(40):
        deep = {"nested": deep}
    refused = {
        "not JSON": b"{not json",
        "duplicate keys": b'{"type": "bundle", "type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000001", "objects": []}',
        "NaN": b'{"type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000002", "objects": [], "x": NaN}',
        "nesting deeper than 32": json.dumps({"type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000003", "objects": [], "x_deep": deep}).encode(),
        "not a bundle": b'{"type": "indicator"}',
    }
    for reason, content in refused.items():
        response = stix_import(analyst, state, content)
        if response.status != 422:
            raise SmokeFailure(f"malformed bundle ({reason}) was not refused: HTTP {response.status}")
    check(True, f"{len(refused)} malformed bundles refused with 422 (invalid JSON, duplicate keys, NaN, excessive nesting, not a bundle)")
    oversized = stix_import(analyst, state, b" " * (5 * 1024 * 1024 + 1024))
    check(oversized.status == 413, "a bundle over 5 MiB is refused with 413")
    audit = analyst.get(api(state, "/audit-events?action=exchange&limit=50", key="exchange_case_id")).body["items"]
    check(any(event["action"] == "exchange.stix_rejected" for event in audit) and any(event["action"] == "exchange.stix_imported" for event in audit), "imports and refusals are audited")


# -- retention ----------------------------------------------------------------------------------


def stage_retention(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)

    def indexed() -> bool:
        detail = analyst.get(api(state, f"/evidence/{state['evidence_id']}")).body
        return bool(detail.get("index") and detail["index"]["status"] == "indexed")

    wait_for("the imported note to be indexed", indexed, args.timeout, 2)
    conversation = expect_quiet(analyst.send("POST", api(state, "/ai/conversations"), {}), 201).body
    asked = expect_quiet(analyst.send("POST", api(state, f"/ai/conversations/{conversation['id']}/questions"), {"question": "203.0.113.9 adresine hangi alan adı çözümleniyor?", "location": "local"}), 202).body
    wait_for("the AI answer", lambda: analyst.get(api(state, f"/ai/runs/{asked['id']}")).body["status"] in ("completed", "failed", "canceled"), args.timeout, 2)
    messages = analyst.get(api(state, f"/ai/conversations/{conversation['id']}")).body["messages"]
    answer = next(m for m in messages if m["role"] == "assistant")
    citations = [ref for claim in (answer.get("answer") or {}).get("claims", []) for ref in claim["citations"] if ref["ref_type"] == "chunk"]
    cited = [ref for ref in citations if analyst.get(api(state, f"/ai/citations/{ref['citation_id']}")).body["passage"]["evidence_id"] == state["evidence_id"]]
    check(bool(cited), "an AI answer cites the imported note")
    state["citation_id"] = cited[0]["citation_id"]

    sql("UPDATE evidence_objects SET collected_at = collected_at - interval '40 days' WHERE id = :id", {"id": state["evidence_id"]})
    sql("UPDATE query_runs SET queued_at = queued_at - interval '40 days', finished_at = finished_at - interval '40 days' WHERE saved_query_id = :query", {"query": state["query_id"]})
    print("  ..  aged the imported note and the monitored query's executions by 40 days (verification-only SQL)")
    rules = {"collected_results_max_age_days": 30, "imported_evidence_max_age_days": 30}
    preview = expect_status(analyst.send("POST", api(state, "/retention/preview"), rules), 200, "retention preview").body
    check(preview["executions"] >= 3 and preview["imported_originals"] == 1 and preview["evidence_records"] >= 4, f"preview: {preview['executions']} executions, {preview['evidence_records']} evidence records ({preview['stored_bytes']} bytes)")
    check(preview["protected_baselines"] >= 1 and preview["ai_citations_affected"] >= 1, "preview shows kept baselines and affected AI citations")
    check(any("Backups" in item for item in preview["not_removed"]), "preview says backups and downloaded copies are not affected")
    mismatch = analyst.send("PUT", api(state, "/retention"), {**rules, "confirm_title": "wrong"})
    check(mismatch.status == 422, "activation without the exact case title is refused")

    # Active work overlapping activation: an indexing lease held on a record that is kept.
    kept = sql("SELECT e.id FROM evidence_objects e JOIN evidence_index_states s ON s.evidence_id = e.id WHERE e.case_id = :case AND e.id <> :note AND (e.query_run_id IS NULL OR e.query_run_id NOT IN (SELECT id FROM query_runs WHERE saved_query_id = :query)) LIMIT 1", {"case": state["case_id"], "note": state["evidence_id"], "query": state["query_id"]})
    sql("UPDATE evidence_index_states SET status = 'indexing', lease_expires_at = now() + interval '70 seconds' WHERE evidence_id = :id", {"id": kept[0][0]})
    activated = expect_status(analyst.send("PUT", api(state, "/retention"), {**rules, "confirm_title": CASE_TITLE}), 200, "retention activated with the typed case title").body
    check(activated["active"] is True and activated["version"] == 1, "policy version 1 is active")

    def job(predicate: Any) -> dict[str, Any] | None:
        items = analyst.get(api(state, "/retention/jobs?limit=5")).body["items"]
        return items[0] if items and predicate(items[0]) else None

    waiting = wait_for("the retention job to wait for active work", lambda: job(lambda item: item["progress_note"] and "Waiting" in item["progress_note"]), args.timeout, 2)
    check(waiting["deferred"].get("indexing") == 1 and waiting["status"] == "queued", "the job waits while indexing holds a lease in the case")
    check(analyst.get(api(state, f"/evidence/{state['evidence_id']}")).status == 200, "nothing is removed while it waits")
    done = wait_for("the retention job to finish", lambda: job(lambda item: item["status"] in ("completed", "failed")), 300, 5)
    check(done["status"] == "completed" and done["removed"].get("evidence_records", 0) >= 4 and done["removed"].get("imported_originals") == 1, f"the job completed after the work finished: {done['removed']}")

    gone = analyst.get(api(state, f"/evidence/{state['evidence_id']}"))
    check(gone.status == 410 and gone.body["detail"]["code"] == "evidence_expired_by_retention" and gone.body["detail"]["sha256"], "removed evidence answers 410 with its tombstone")
    passage = analyst.get(api(state, f"/ai/citations/{state['citation_id']}")).body["passage"]
    check(passage["status"] == "source_expired" and passage.get("removed_at"), "the AI citation says its source expired")
    run = run_detail(analyst, state, state["run_v1"])
    check(run["results_expired_at"] is not None and run["status"] == "completed", "the aged execution keeps its record and shows when its results expired")
    change = change_sets(analyst, state, state["run_v2"], args.timeout)
    check(change["events"]["items"] and all(event["previous_evidence_available"] is False for event in change["events"]["items"] if event["previous_evidence_id"]), "change events show that the baseline's evidence was removed")
    baselines = sql("SELECT count(*) FROM query_runs WHERE saved_query_id = :query AND results_expired_at IS NULL AND status IN ('completed', 'partial')", {"query": state["query_id"]})
    check(int(baselines[0][0]) >= 1, "the latest comparison baselines were kept")
    stored = compose("exec", "-T", "worker", "sh", "-c", f"test -e /data/evidence/cases/{state['case_id']}/evidence/{state['evidence_id']} && echo present || echo absent").strip()
    check(stored == "absent", "the removed file is gone from the evidence volume")
    viewer = login(args, VIEWER)
    check(viewer.send("DELETE", api(state, "/retention")).status == 403, "a viewer cannot change retention")
    expect_quiet(analyst.send("DELETE", api(state, "/retention")), 200)


# -- audit and deletion -------------------------------------------------------------------------


def stage_audit(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    events_page = analyst.get(api(state, "/audit-events?limit=100")).body
    expected = ["case.created", "membership.added", "membership.role_changed", "monitor.created", "monitor.enabled", "monitor.paused", "monitor.disabled", "monitor.dispatched", "export.downloaded", "retention.policy_activated", "retention.applied", "run.cancel_requested"]
    missing = [action for action in expected if analyst.get(api(state, f"/audit-events?action={action}&limit=1")).body["total"] == 0]
    check(not missing, f"the case audit trail records membership, monitor, export, retention and cancellation actions (missing: {missing})")
    check(all(event["outcome"] in ("succeeded", "denied", "failed") and event["actor_label"] and event["occurred_at"] for event in events_page["items"]), "every event has an actor, outcome and time")
    check(any(event["actor_type"] == "service" for event in events_page["items"]), "background work is audited as a service actor")
    admin = login(args, ADMIN)
    system = admin.get("/api/v1/admin/audit-events?limit=100").body["items"]
    system_missing = [action for action in ("account.created", "notification_destination.created", "notification_destination.enabled", "notification_destination.disabled") if admin.get(f"/api/v1/admin/audit-events?action={action}&limit=1").body["total"] == 0]
    check(not system_missing, f"the system audit trail records account and destination changes (missing: {system_missing})")
    text = json.dumps([events_page, system], ensure_ascii=False)
    leaks = [value for value in (*FEED_TEXT, other_password(), password("TRACEHOLLOW_ACCEPTANCE_PASSWORD")) if value in text]
    check(not leaks, "audit details contain no passwords and no collected content")
    viewer = login(args, VIEWER)
    check(viewer.get(api(state, "/audit-events")).status == 403 and viewer.get("/api/v1/admin/audit-events").status == 403, "viewers cannot read audit trails")


def stage_delete(args: argparse.Namespace, state: dict[str, Any]) -> None:
    analyst = login(args, ANALYST)
    expect_status(analyst.send("POST", api(state, "/deletion"), {"confirm_title": CASE_TITLE}), 202, "case deletion requested")
    wait_for("the case to disappear", lambda: analyst.get(api(state)).status == 404, args.timeout, 2)
    remaining = sql(
        "SELECT (SELECT count(*) FROM monitors WHERE case_id = :id) + (SELECT count(*) FROM monitor_occurrences WHERE case_id = :id) "
        "+ (SELECT count(*) FROM notification_deliveries WHERE case_id = :id) + (SELECT count(*) FROM change_sets WHERE case_id = :id) "
        "+ (SELECT count(*) FROM budget_ledgers WHERE case_id = :id) + (SELECT count(*) FROM retention_tombstones WHERE case_id = :id)",
        {"id": state["case_id"]},
    )
    check(int(remaining[0][0]) == 0, "monitors, occurrences, deliveries, change sets, ledgers and tombstones of the case were removed")
    inbox = notifications(analyst)
    check(not [item for item in inbox if item["case_id"] == state["case_id"]], "notifications of the deleted case are no longer listed")


STAGES = {
    "seed": stage_seed,
    "roles": stage_roles,
    "monitor": stage_monitor,
    "webhook": stage_webhook,
    "webhook-disabled": stage_webhook_disabled,
    "schedule-start": stage_schedule_start,
    "schedule-concurrent": stage_schedule_concurrent,
    "schedule-mark": stage_schedule_mark,
    "schedule-restart": stage_schedule_restart,
    "crash-start": stage_crash_start,
    "crash-check": stage_crash_check,
    "budget": stage_budget,
    "cancel": stage_cancel,
    "revoke-start": stage_revoke_start,
    "revoke-check": stage_revoke_check,
    "stix": stage_stix,
    "retention": stage_retention,
    "audit": stage_audit,
    "delete": stage_delete,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--web-url", default="http://localhost:3160")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--work-dir", default=".")
    args = parser.parse_args()
    args.origin = args.origin or args.web_url
    state = load_state(args.state)
    try:
        STAGES[args.stage](args, state)
    except SmokeFailure as failure:
        print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    finally:
        save_state(args.state, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
