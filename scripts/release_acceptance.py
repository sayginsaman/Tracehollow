#!/usr/bin/env python3
"""Release-readiness checks against running stacks (Phase 6).

Used by scripts/verify-release.sh, which creates the isolated Compose projects. Python standard
library only; identifiers (never secrets) are shared between stages through --state.

Stages:
  install-smoke   a clean checkout installed from the documentation alone: first-run setup, case,
                  import, synthetic collection, evidence integrity, readiness
  upgrade-seed    fill an installation running the previous release state with synthetic records
  upgrade-verify  after upgrading the same volumes to the candidate: records, hashes, memberships,
                  citations, saved queries and settings survive; new Phase 5 structures exist
  restore-seed    fill a source installation with representative material for the backup drill
  restore-verify  after restoring into a clean installation: hashes, references, previews,
                  citations, memberships, paused monitors, no resumed collection or deliveries
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from phase1_acceptance import Session, load_state, password, save_state
from smoke_test import ROOT, Client, Response, SmokeFailure, check, expect_status

ADMIN = "release-admin"
ANALYST = "release-analyst"
VIEWER = "release-viewer"
CASE_TITLE = "Release drill: synthetic infrastructure review"
NOTE_TEXT = (
    "Registry extract (synthetic): the domain ornek.example is registered to Ornek Holding, "
    "and its published contact address is press@ornek.example.\n"
    "The service moved to 203.0.113.24 during the recorded period."
)
TERMINAL_RUN = ("completed", "partial", "failed", "canceled")


def wait_for(description: str, predicate: Any, timeout: float, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise SmokeFailure(f"timed out after {timeout:.0f}s waiting for {description}")
        time.sleep(interval)


def api(state: dict[str, Any], suffix: str = "", key: str = "case_id") -> str:
    return f"/api/v1/cases/{state[key]}{suffix}"


def admin_password() -> str:
    return password("TRACEHOLLOW_RELEASE_PASSWORD")


def other_password() -> str:
    return password("TRACEHOLLOW_RELEASE_OTHER_PASSWORD")


def sign_in(args: argparse.Namespace, username: str) -> Session:
    return Session(args, username, admin_password() if username == ADMIN else other_password())


def setup_admin(args: argparse.Namespace) -> Session:
    """First-run setup exactly as the documentation describes it."""
    web = Client(args.web_url, args.origin)
    status = expect_status(web.request("GET", "/api/v1/setup/status"), 200, "setup status readable")
    check(status.body == {"setup_required": True}, "a fresh installation asks for setup")
    token = (Path(args.checkout) / "secrets" / "bootstrap_token").read_text().strip()
    created = web.request(
        "POST",
        "/api/v1/setup/admin",
        {"setup_token": token, "username": ADMIN, "password": admin_password()},
    )
    expect_status(created, 201, "administrator created with the token from secrets/bootstrap_token")
    return sign_in(args, ADMIN)


def wait_run(session: Session, state: dict[str, Any], run_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = session.get(api(state, f"/runs/{run_id}")).body
        return body if body["status"] in TERMINAL_RUN else None

    result: dict[str, Any] = wait_for(f"run {run_id[:8]}", done, timeout)
    return result


def collect_fixture(session: Session, state: dict[str, Any], timeout: float, name: str = "Synthetic collection") -> dict[str, Any]:
    query = session.send(
        "POST",
        api(state, "/saved-queries"),
        {
            "name": name,
            "input_type": "username",
            "input_value": "ornek-dev",
            "connector_ids": ["synthetic.fixture"],
        },
    )
    query_id = expect_status(query, 201, f"saved query created: {name}").body["id"]
    started = expect_status(
        session.send("POST", api(state, f"/saved-queries/{query_id}/runs")), 202, "run queued"
    ).body
    run = wait_run(session, state, started["id"], timeout)
    state.setdefault("query_ids", []).append(query_id)
    return run


def evidence_page(session: Session, state: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = session.get(api(state, f"/evidence?limit={limit}")).body["items"]
    return items


def hashes(session: Session, state: dict[str, Any]) -> dict[str, str]:
    return {item["title"]: item["sha256"] for item in evidence_page(session, state)}


# -- install ------------------------------------------------------------------------------------


def stage_install_smoke(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = setup_admin(args)
    info = session.get("/api/v1/auth/session").body
    check(info["user"]["role"] == "administrator", "the first account is an administrator")
    readiness = session.get("/api/v1/system/status").body
    names = {name: value["status"] for name, value in readiness["checks"].items()}
    check(
        {"database", "migrations", "redis", "storage"} <= set(names),
        f"readiness reports every dependency: {json.dumps(names)}",
    )
    check(readiness["ready"] is True and all(value == "ok" for value in names.values()), "every dependency check is ok")
    worker = session.get("/api/v1/system/worker").body
    check(worker["status"] == "online" and worker["workers"], f"{len(worker['workers'])} worker(s) answered the broker ping")

    created = session.send(
        "POST",
        "/api/v1/cases",
        {"title": CASE_TITLE, "purpose": "Clean-install verification", "scope": "Synthetic only"},
    )
    state["case_id"] = expect_status(created, 201, "case created").body["id"]
    imported = session.upload(
        state["case_id"],
        NOTE_TEXT.encode(),
        "registry-extract.txt",
        kind="text",
        title="Registry extract",
        import_origin="Synthetic note written by the release drill",
    )
    state["evidence_id"] = expect_status(imported, 201, "evidence imported").body["evidence"]["id"]
    detail = session.get(api(state, f"/evidence/{state['evidence_id']}")).body
    check(detail["integrity"]["status"] == "verified", "stored evidence passes its integrity check")
    status, content, _ = session.raw("GET", api(state, f"/evidence/{state['evidence_id']}/content"))
    check(status == 200 and content == NOTE_TEXT.encode(), "the original bytes download unchanged")

    run = collect_fixture(session, state, args.timeout)
    check(run["status"] == "completed", f"synthetic collection finished ({run['status']})")
    check(run["evidence_count"] >= 1, f"the run stored {run['evidence_count']} evidence record(s)")
    check(
        session.get(api(state, "/monitors")).status == 200
        and session.get(api(state, "/budgets")).status == 200
        and session.get(api(state, "/retention")).status == 200,
        "Phase 5 case endpoints answer on a fresh installation",
    )
    status, raw, headers = session.raw("GET", api(state, "/exports/json"))
    export = json.loads(raw)
    check(status == 200 and export["manifest"]["record_counts"]["evidence"] >= 2, "the JSON export carries a manifest with record counts and a data hash")
    check(len(export["manifest"]["data_sha256"]) == 64, "the export manifest states the SHA-256 of its data")


# -- upgrade ------------------------------------------------------------------------------------


def stage_upgrade_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    """Fill the previous release state (Phase 4 schema) with records an upgrade must preserve."""
    session = setup_admin(args)
    created = session.send(
        "POST",
        "/api/v1/cases",
        {"title": CASE_TITLE, "purpose": "Upgrade verification", "scope": "Synthetic only", "tags": ["upgrade-drill"]},
    )
    state["case_id"] = expect_status(created, 201, "case created on the previous release").body["id"]
    imported = session.upload(
        state["case_id"],
        NOTE_TEXT.encode(),
        "registry-extract.txt",
        kind="text",
        title="Registry extract",
        import_origin="Synthetic note written before the upgrade",
    )
    state["evidence_id"] = expect_status(imported, 201, "evidence imported").body["evidence"]["id"]
    domain = expect_status(
        session.send(
            "POST",
            api(state, "/entities"),
            {
                "entity_type": "domain",
                "display_name": "ornek.example",
                "identifiers": [{"identifier_type": "domain", "value": "ornek.example"}],
            },
        ),
        201,
        "entity created",
    ).body
    organization = expect_status(
        session.send("POST", api(state, "/entities"), {"entity_type": "organization", "display_name": "Ornek Holding"}),
        201,
        "organization created",
    ).body
    relationship = expect_status(
        session.send(
            "POST",
            api(state, "/relationships"),
            {
                "source_entity_id": organization["id"],
                "target_entity_id": domain["id"],
                "predicate": "owns",
                "supporting_evidence_ids": [state["evidence_id"]],
            },
        ),
        201,
        "relationship created with supporting evidence",
    ).body
    expect_status(
        session.send("POST", api(state, "/notes"), {"body": "Synthetic note recorded before the upgrade."}),
        201,
        "note created",
    )
    run = collect_fixture(session, state, args.timeout, name="Pre-upgrade collection")
    check(run["status"] == "completed", "collection finished before the upgrade")
    state["entity_id"] = domain["id"]
    state["relationship_id"] = relationship["id"]
    state["run_id"] = run["id"]
    state["before"] = {
        "hashes": hashes(session, state),
        "entities": session.get(api(state, "/entities?limit=100")).body["total"],
        "relationships": session.get(api(state, "/relationships?limit=100")).body["total"],
        "runs": session.get(api(state, "/runs?limit=100")).body["total"],
        "notes": session.get(api(state, "/notes?limit=100")).body["total"],
        "queries": session.get(api(state, "/saved-queries?limit=100")).body["total"],
    }
    check(state["before"]["hashes"], f"recorded {len(state['before']['hashes'])} evidence hashes before the upgrade")


def stage_upgrade_verify(args: argparse.Namespace, state: dict[str, Any]) -> None:
    """The same volumes, now served by the candidate."""
    session = sign_in(args, ADMIN)
    info = session.get("/api/v1/auth/session").body
    check(
        info["user"]["role"] == "administrator" and "accounts.manage" in info["permissions"],
        "the pre-upgrade administrator keeps administrator rights (is_admin migrated to a role)",
    )
    detail = session.get(api(state)).body
    check(detail["title"] == CASE_TITLE, "the case survived the upgrade")
    check(detail["my_role"] == "analyst", "the case owner became an analyst member (migration 0006)")
    members = session.get(api(state, "/members")).body
    check(
        len(members) == 1 and members[0]["username"] == ADMIN and members[0]["membership_role"] == "analyst",
        "membership was created for the previous owner",
    )
    after = {
        "hashes": hashes(session, state),
        "entities": session.get(api(state, "/entities?limit=100")).body["total"],
        "relationships": session.get(api(state, "/relationships?limit=100")).body["total"],
        "runs": session.get(api(state, "/runs?limit=100")).body["total"],
        "notes": session.get(api(state, "/notes?limit=100")).body["total"],
        "queries": session.get(api(state, "/saved-queries?limit=100")).body["total"],
    }
    before = state["before"]
    check(after["hashes"] == before["hashes"], f"every evidence hash is unchanged ({len(after['hashes'])} records)")
    for key in ("entities", "relationships", "runs", "notes", "queries"):
        check(after[key] == before[key], f"{key}: {after[key]} record(s), unchanged")
    status, content, _ = session.raw("GET", api(state, f"/evidence/{state['evidence_id']}/content"))
    check(status == 200 and content == NOTE_TEXT.encode(), "evidence bytes still download unchanged")
    integrity = session.get(api(state, f"/evidence/{state['evidence_id']}")).body["integrity"]
    check(integrity["status"] == "verified", "integrity still verifies after the upgrade")
    relationship = session.get(api(state, f"/relationships/{state['relationship_id']}")).body
    check(relationship["predicate"] == "owns", "the relationship and its predicate survived")
    run = session.get(api(state, f"/runs/{state['run_id']}")).body
    check(run["status"] == "completed" and run["parameters_snapshot"], "the execution and its snapshot survived")

    # New Phase 5 structures exist and start empty for an upgraded case.
    monitors = session.get(api(state, "/monitors")).body
    check(monitors["total"] == 0, "the upgraded case has no monitors until one is created")
    budgets = session.get(api(state, "/budgets")).body
    check(budgets["budgets"] == [], "no collection budget is imposed by the upgrade")
    retention = session.get(api(state, "/retention")).body
    check(retention["active"] is False, "retention stays off after the upgrade")
    audit = session.get(api(state, "/audit-events?limit=5")).body
    check(audit["total"] >= 0, "the case audit trail is readable")
    check(session.get("/api/v1/admin/accounts?limit=5").status == 200, "administration endpoints work after the upgrade")


# -- backup and restore -------------------------------------------------------------------------


def stage_restore_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    """Representative material: members, evidence with derived text, AI citation, monitor, audit."""
    admin = setup_admin(args)
    for username, role in ((ANALYST, "analyst"), (VIEWER, "viewer")):
        expect_status(
            admin.send("POST", "/api/v1/admin/accounts", {"username": username, "role": role, "password": other_password()}),
            201,
            f"account created: {username} ({role})",
        )
    analyst = sign_in(args, ANALYST)
    created = analyst.send(
        "POST",
        "/api/v1/cases",
        {"title": CASE_TITLE, "purpose": "Backup and restore drill", "scope": "Synthetic only"},
    )
    state["case_id"] = expect_status(created, 201, "case created by an analyst").body["id"]
    expect_status(analyst.send("POST", api(state, "/members"), {"username": VIEWER, "role": "viewer"}), 201, "viewer added")
    imported = analyst.upload(
        state["case_id"],
        NOTE_TEXT.encode(),
        "registry-extract.txt",
        kind="text",
        title="Registry extract",
        import_origin="Synthetic note for the restore drill",
    )
    state["evidence_id"] = expect_status(imported, 201, "evidence imported").body["evidence"]["id"]
    domain = expect_status(
        analyst.send(
            "POST",
            api(state, "/entities"),
            {"entity_type": "domain", "display_name": "ornek.example", "identifiers": [{"identifier_type": "domain", "value": "ornek.example"}]},
        ),
        201,
        "entity created",
    ).body
    address = expect_status(
        analyst.send(
            "POST",
            api(state, "/entities"),
            {"entity_type": "ip", "display_name": "203.0.113.24", "identifiers": [{"identifier_type": "ip", "value": "203.0.113.24"}]},
        ),
        201,
        "address created",
    ).body
    expect_status(
        analyst.send(
            "POST",
            api(state, "/relationships"),
            {
                "source_entity_id": domain["id"],
                "target_entity_id": address["id"],
                "predicate": "resolves_to",
                "supporting_evidence_ids": [state["evidence_id"]],
            },
        ),
        201,
        "relationship created",
    )
    run = collect_fixture(session=analyst, state=state, timeout=args.timeout, name="Drill collection")
    check(run["status"] == "completed", "collection finished")
    state["run_id"] = run["id"]

    monitor = analyst.send(
        "POST",
        api(state, "/monitors"),
        {
            "name": "Drill monitor",
            "saved_query_id": state["query_ids"][0],
            "schedule": {"kind": "interval", "every_minutes": 60},
            "enable": True,
            "acknowledge_recurring_collection": True,
        },
    )
    state["monitor_id"] = expect_status(monitor, 201, "monitor created and enabled").body["id"]
    check(monitor.body["status"] == "enabled", "the monitor is enabled before the backup")

    def indexed() -> bool:
        body = analyst.get(api(state, f"/evidence/{state['evidence_id']}")).body
        return bool(body.get("index") and body["index"]["status"] == "indexed")

    wait_for("the note to be indexed", indexed, args.timeout, 2)
    conversation = expect_status(analyst.send("POST", api(state, "/ai/conversations"), {}), 201, "conversation created").body
    asked = expect_status(
        analyst.send(
            "POST",
            api(state, f"/ai/conversations/{conversation['id']}/questions"),
            {"question": "Which organization registered ornek.example?", "location": "local"},
        ),
        202,
        "question accepted",
    ).body
    wait_for(
        "the AI answer",
        lambda: analyst.get(api(state, f"/ai/runs/{asked['id']}")).body["status"] in ("completed", "failed", "canceled"),
        args.timeout,
        2,
    )
    messages = analyst.get(api(state, f"/ai/conversations/{conversation['id']}")).body["messages"]
    answer = next(m for m in messages if m["role"] == "assistant")
    citations = [ref for claim in (answer.get("answer") or {}).get("claims", []) for ref in claim["citations"] if ref["ref_type"] == "chunk"]
    check(bool(citations), f"the answer cites {len(citations)} evidence passage(s)")
    state["conversation_id"] = conversation["id"]
    state["citation_id"] = citations[0]["citation_id"]
    state["before"] = {
        "hashes": hashes(analyst, state),
        "entities": analyst.get(api(state, "/entities?limit=100")).body["total"],
        "relationships": analyst.get(api(state, "/relationships?limit=100")).body["total"],
        "runs": analyst.get(api(state, "/runs?limit=100")).body["total"],
        "audit": analyst.get(api(state, "/audit-events?limit=1")).body["total"],
    }
    check(state["before"]["audit"] >= 1, f"{state['before']['audit']} audit events recorded before the backup")


def stage_restore_verify(args: argparse.Namespace, state: dict[str, Any]) -> None:
    """The restored installation: same records, working references, no resumed work."""
    analyst = sign_in(args, ANALYST)
    detail = analyst.get(api(state)).body
    check(detail["title"] == CASE_TITLE, "the case is present after the restore")
    after = {
        "hashes": hashes(analyst, state),
        "entities": analyst.get(api(state, "/entities?limit=100")).body["total"],
        "relationships": analyst.get(api(state, "/relationships?limit=100")).body["total"],
        "runs": analyst.get(api(state, "/runs?limit=100")).body["total"],
        "audit": analyst.get(api(state, "/audit-events?limit=1")).body["total"],
    }
    before = state["before"]
    check(after["hashes"] == before["hashes"], f"every evidence hash matches the source installation ({len(after['hashes'])} records)")
    for key in ("entities", "relationships", "runs"):
        check(after[key] == before[key], f"{key}: {after[key]} record(s), unchanged")
    # The restore pauses monitors and records that in the audit trail, so one event is expected.
    paused_events = analyst.get(api(state, "/audit-events?action=monitor.paused&limit=10")).body["items"]
    restore_pause = [event for event in paused_events if event["details"].get("reason") == "restore"]
    check(
        after["audit"] == before["audit"] + len(restore_pause) and len(restore_pause) == 1,
        f"the audit trail is complete ({before['audit']} events restored plus the audited monitor pause)",
    )
    check(restore_pause[0]["actor_label"] == "operator-cli", "the pause is attributed to the operator command, not to an analyst")
    status, content, _ = analyst.raw("GET", api(state, f"/evidence/{state['evidence_id']}/content"))
    check(status == 200 and content == NOTE_TEXT.encode(), "the original bytes are byte-identical after the restore")
    integrity = analyst.get(api(state, f"/evidence/{state['evidence_id']}")).body["integrity"]
    check(integrity["status"] == "verified", "the restored file matches its recorded hash and size")
    preview = analyst.get(api(state, f"/evidence/{state['evidence_id']}/preview")).body
    check("ornek.example" in preview["text"], "the evidence preview renders from the restored file")
    citation = analyst.get(api(state, f"/ai/citations/{state['citation_id']}")).body["passage"]
    check(citation["status"] == "available", f"the AI citation still resolves ({citation['status']})")
    check(citation["evidence_id"] == state["evidence_id"], "the citation points at the restored evidence record")

    monitor = analyst.get(api(state, f"/monitors/{state['monitor_id']}")).body
    check(monitor["status"] == "paused", f"the monitor is paused after the restore ({monitor['status_reason']})")
    check(monitor["next_run_at"] is None, "no next run is scheduled")
    occurrences_before = analyst.get(api(state, f"/monitors/{state['monitor_id']}/occurrences")).body["total"]
    runs_before = analyst.get(api(state, "/runs?limit=100")).body["total"]
    time.sleep(args.settle)
    check(
        analyst.get(api(state, f"/monitors/{state['monitor_id']}/occurrences")).body["total"] == occurrences_before,
        f"no occurrence was dispatched in {args.settle:.0f}s after the restore",
    )
    check(
        analyst.get(api(state, "/runs?limit=100")).body["total"] == runs_before,
        "no execution started on its own after the restore",
    )

    viewer = sign_in(args, VIEWER)
    check(viewer.get(api(state)).body["my_role"] == "viewer", "the viewer membership survived with its role")
    refused = viewer.send("POST", api(state, "/saved-queries"), {"name": "x", "input_type": "username", "input_value": "y", "connector_ids": ["synthetic.fixture"]})
    check(refused.status == 403, "the viewer is still refused changes after the restore")
    outsider = Client(args.web_url, args.origin)
    check(outsider.request("GET", api(state)).status == 401, "an unauthenticated request is still refused")


STAGES = {
    "install-smoke": stage_install_smoke,
    "upgrade-seed": stage_upgrade_seed,
    "upgrade-verify": stage_upgrade_verify,
    "restore-seed": stage_restore_seed,
    "restore-verify": stage_restore_verify,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--web-url", required=True)
    parser.add_argument("--origin", default=None)
    parser.add_argument("--checkout", default=str(ROOT), help="repository directory holding secrets/")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--settle", type=float, default=20, help="seconds to watch for unwanted background work")
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
