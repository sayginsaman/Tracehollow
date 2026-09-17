#!/usr/bin/env python3
"""Add clearly labelled synthetic Phase 5 demo data to a disposable verification stack.

For design reviews, screenshots and manual walkthroughs of monitoring, change detection,
notifications, team roles, budgets, STIX exchange and administration. It must run only against a
separate Compose project started by scripts/verify-phase5.sh --keep (the controlled feed and webhook
receiver of compose.verify-phase5.yaml), never against a stack holding real investigations. Case
titles start with "[Synthetic demo]" and carry the tag "synthetic-demo"; account names start with
"demo-". Python standard library only.

Usage (after scripts/verify-phase5.sh --keep, with the env file it printed):
  source <work-dir>/env && TRACEHOLLOW_DEMO_PASSWORD=... python3 scripts/seed_phase5_demo.py \\
      --web-url http://localhost:3160
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase1_acceptance import ADMIN, Session
from phase5_acceptance import FEED, HOOK, change_sets, fixture, multipart, run_now, wait_for, wait_run
from smoke_test import SmokeFailure, expect_status

TAG = "synthetic-demo"
ANALYST = "demo-analyst-p5"
VIEWER = "demo-viewer-p5"
TITLE = "[Synthetic demo] Örnek Lojistik duyuru izleme"
EXCHANGE = "[Synthetic demo] Paylaşılan STIX paketi"


def api(case_id: str, suffix: str = "") -> str:
    return f"/api/v1/cases/{case_id}{suffix}"


def created(response: Any, what: str) -> Any:
    return expect_status(response, 201, what).body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--web-url", default="http://localhost:3160")
    args = parser.parse_args()
    args.origin = args.web_url
    args.timeout = 180
    demo_password = os.environ.get("TRACEHOLLOW_DEMO_PASSWORD", "")
    admin_password = os.environ.get("TRACEHOLLOW_ACCEPTANCE_PASSWORD", "")
    if len(demo_password) < 12 or not admin_password:
        print("Set TRACEHOLLOW_DEMO_PASSWORD (12+ characters) and source the verification env file.", file=sys.stderr)
        return 2
    try:
        admin = Session(args, ADMIN, admin_password)
        for username, role in ((ANALYST, "analyst"), (VIEWER, "viewer")):
            created(admin.send("POST", "/api/v1/admin/accounts", {"username": username, "role": role, "password": demo_password}), f"account {username}")
        analyst = Session(args, ANALYST, demo_password)
        case_id = created(analyst.send("POST", "/api/v1/cases", {"title": TITLE, "purpose": "Synthetic walkthrough of monitoring and change detection.", "scope": "Controlled fixture feed only", "tags": [TAG]}), "demo case")["id"]
        exchange_id = created(analyst.send("POST", "/api/v1/cases", {"title": EXCHANGE, "purpose": "Synthetic STIX import target.", "scope": "Imported bundle only", "tags": [TAG]}), "exchange case")["id"]
        created(analyst.send("POST", api(case_id, "/members"), {"username": VIEWER, "role": "viewer"}), "viewer member")
        state: dict[str, Any] = {"case_id": case_id}

        for body in (
            {"entity_type": "domain", "display_name": "ornek-lojistik.example", "identifiers": [{"identifier_type": "domain", "value": "ornek-lojistik.example"}]},
            {"entity_type": "ip", "display_name": "203.0.113.24", "identifiers": [{"identifier_type": "ip", "value": "203.0.113.24"}]},
            {"entity_type": "organization", "display_name": "Örnek Lojistik A.Ş. (synthetic)"},
        ):
            created(analyst.send("POST", api(case_id, "/entities"), body), "entity")

        fixture(feed="v1", slow=0, webhook_failures=0, reset_deliveries=1)
        query = created(analyst.send("POST", api(case_id, "/saved-queries"), {"name": "Duyuru akışı (fixture)", "input_type": "url", "input_value": FEED, "connector_ids": ["rss.feed"], "limits": {"max_pages": 3, "max_items_per_page": 50}}), "saved query")
        monitor = created(analyst.send("POST", api(case_id, "/monitors"), {
            "name": "Günlük duyuru izleme",
            "saved_query_id": query["id"],
            "schedule": {"kind": "daily", "time": "09:00"},
            "timezone": "Europe/Istanbul",
            "scope": {"max_pages": 3, "max_items_per_page": 50},
            "limits": {"max_requests_per_run": 10, "max_items_per_run": 200, "max_run_seconds": 300},
            "budget": {"period": "day", "max_requests": 40},
        }), "monitor")
        state["monitor_id"] = monitor["id"]
        expect_status(analyst.send("PUT", api(case_id, "/budgets"), {"budgets": [{"metric": "requests", "period": "day", "limit_units": 200}]}), 200, "case budget")

        admin_destination = created(admin.send("POST", "/api/v1/admin/notification-destinations", {"name": "SOC alıcısı (synthetic fixture)", "url": HOOK, "event_types": ["change_detected", "action_required"], "max_per_minute": 30}), "destination")
        path = f"/api/v1/admin/notification-destinations/{admin_destination['id']}"
        expect_status(admin.send("POST", f"{path}/preview"), 200, "preview")
        expect_status(admin.send("POST", f"{path}/enable", {"confirm_host": admin_destination["host"]}), 200, "destination enabled")
        created(analyst.send("POST", api(case_id, f"/monitors/{monitor['id']}/subscriptions"), {"destination_id": admin_destination["id"], "event_types": ["change_detected"]}), "subscription")

        for version in ("v1", "v2", "partial", "v3"):
            fixture(feed=version)
            occurrence = run_now(analyst, state)
            wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
            change_sets(analyst, state, occurrence["query_run_id"], args.timeout)
        fixture(feed="v3")

        second = created(analyst.send("POST", api(case_id, "/monitors"), {
            "name": "Haftalık kontrol (paused)",
            "saved_query_id": query["id"],
            "schedule": {"kind": "weekly", "time": "08:30", "weekdays": [0, 3]},
            "timezone": "Europe/Istanbul",
            "scope": {"max_pages": 1, "max_items_per_page": 20},
        }), "second monitor")
        del second

        status, raw, _ = analyst.raw("GET", api(case_id, "/exports/stix"))
        if status != 200:
            raise SmokeFailure(f"STIX export failed: HTTP {status}")
        created(multipart(analyst, api(exchange_id, "/imports/stix"), raw, "ornek-lojistik.stix.json", import_origin="Synthetic bundle exported from the demo monitoring case"), "STIX import")
        wait_for("webhook deliveries to settle", lambda: all(d["status"] != "pending" for d in admin.get(f"{path}/deliveries").body["items"]), 120, 2)
        print(json.dumps({"case_id": case_id, "exchange_case_id": exchange_id, "monitor_id": monitor["id"], "analyst": ANALYST, "viewer": VIEWER}, indent=2))
    except SmokeFailure as failure:
        print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
