#!/usr/bin/env python3
"""Fill a disposable demo stack with the English synthetic dataset used in the documentation.

Everything it creates is invented for documentation: Aurora Freight and Northwind Logistics are
fictional companies, aurora-freight.example is a reserved example domain, 203.0.113.42 is a
TEST-NET-3 address, and the pages and feed come from the local fixture container
(scripts/fixtures/public-sources/demo.py). No real source is contacted and no real person appears.

Run it only against an isolated demo project (compose.yaml + compose.demo.yaml), never against an
installation holding real investigations. The case is titled "… (synthetic demo)" and tagged
`synthetic-demo`, monitors are left paused and external notifications stay off.

    COMPOSE_PROJECT_NAME=tracehollow-demo COMPOSE_FILE=compose.yaml:compose.demo.yaml \
    TRACEHOLLOW_WEB_PORT=3200 TRACEHOLLOW_API_PORT=8200 docker compose up --detach --wait
    TRACEHOLLOW_DEMO_PASSWORD='choose at least 12 characters' python3 scripts/seed_demo.py \
        --web-url http://localhost:3200

Options: --skip-ai leaves the AI conversation out (for stacks without a local model);
--reset deletes an existing demo case first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase1_acceptance import Session
from smoke_test import ROOT, Client, SmokeFailure, check, expect_status

ADMIN = "demo.admin"
ANALYST = "demo.analyst"
VIEWER = "demo.viewer"
TAG = "synthetic-demo"
CASE_TITLE = "Aurora Freight infrastructure review (synthetic demo)"
FIXTURE = "http://fixture-site:8080"
PRESS_URL = f"{FIXTURE}/demo/press"
FEED_URL = f"{FIXTURE}/demo/feed"
TERMINAL_RUN = ("completed", "partial", "failed", "canceled")

REGISTRY_NOTE = """Registry extract (synthetic demo data, not a real registry response)

Domain:        aurora-freight.example
Registrant:    Aurora Freight Ltd
Registered:    2024-11-03
Last updated:  2026-08-12
Name servers:  ns1.example-dns.example, ns2.example-dns.example
Address record: aurora-freight.example resolves to 203.0.113.42 (recorded 2026-08-12)
Contact:       press@aurora-freight.example

Note: this extract was written for the Tracehollow documentation. It describes a fictional
company and uses reserved example domains and documentation IP addresses only.
"""

WHOIS_RECORD = {
    "synthetic": True,
    "domain": "aurora-freight.example",
    "registrant": {"organization": "Aurora Freight Ltd", "country": "NL"},
    "created": "2024-11-03",
    "updated": "2026-08-12",
    "name_servers": ["ns1.example-dns.example", "ns2.example-dns.example"],
    "addresses": [{"value": "203.0.113.42", "recorded": "2026-08-12"}],
    "note": "Fictional record written for the Tracehollow documentation demo.",
}

PDF_TEXT = [
    "Aurora Freight Ltd - service level summary (synthetic demo)",
    "Rotterdam hub opened on 14 August 2026.",
    "Northwind Logistics operates the hub under a service agreement.",
    "Status page: status.aurora-freight.example",
]


def fixture_control(**params: Any) -> None:
    """Switch the demo feed version through the container's loopback-only control endpoint."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    code = (
        "import urllib.request, sys; "
        f"sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8080/demo/control/set?{query}', timeout=10).read().decode())"
    )
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "fixture-site", "python", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeFailure(f"fixture control failed: {completed.stderr.strip()[-300:]}")


def wait_for(description: str, predicate: Any, timeout: float, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise SmokeFailure(f"timed out after {timeout:.0f}s waiting for {description}")
        time.sleep(interval)


def api(state: dict[str, Any], suffix: str = "") -> str:
    return f"/api/v1/cases/{state['case_id']}{suffix}"


def created(response: Any, what: str) -> Any:
    return expect_status(response, 201, what).body


def wait_run(session: Session, state: dict[str, Any], run_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = session.get(api(state, f"/runs/{run_id}")).body
        return body if body["status"] in TERMINAL_RUN else None

    result: dict[str, Any] = wait_for(f"run {run_id[:8]}", done, timeout)
    return result


def run_query(session: Session, state: dict[str, Any], query_id: str, timeout: float) -> dict[str, Any]:
    started = expect_status(session.send("POST", api(state, f"/saved-queries/{query_id}/runs")), 202, "run queued").body
    return wait_run(session, state, started["id"], timeout)


def make_pdf() -> bytes:
    """A small text PDF built inside the API image, so no PDF tooling is needed on the host."""
    script = (
        "import base64, io, sys\n"
        "from pypdf import PdfWriter\n"
        "import pypdfium2 as pdfium\n"
        "lines = " + json.dumps(PDF_TEXT) + "\n"
        "objects = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']\n"
        "commands = []\n"
        "for index, line in enumerate(lines):\n"
        "    commands.append(f'BT /F1 16 Tf 60 {720 - index * 40} Td ({line}) Tj ET')\n"
        "content = '\\n'.join(commands).encode()\n"
        "objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>')\n"
        "objects.append(b'<< /Length ' + str(len(content)).encode() + b' >>\\nstream\\n' + content + b'\\nendstream')\n"
        "objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'\n"
        "objects[1] = b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>'\n"
        "out = io.BytesIO(); out.write(b'%PDF-1.7\\n%\\xe2\\xe3\\xcf\\xd3\\n'); offsets = []\n"
        "for number, body in enumerate(objects, start=1):\n"
        "    offsets.append(out.tell()); out.write(f'{number} 0 obj\\n'.encode() + body + b'\\nendobj\\n')\n"
        "xref = out.tell(); out.write(f'xref\\n0 {len(objects) + 1}\\n0000000000 65535 f \\n'.encode())\n"
        "for offset in offsets: out.write(f'{offset:010d} 00000 n \\n'.encode())\n"
        "out.write(f'trailer\\n<< /Size {len(objects) + 1} /Root 1 0 R >>\\nstartxref\\n{xref}\\n%%EOF\\n'.encode())\n"
        "sys.stdout.write(base64.b64encode(out.getvalue()).decode())\n"
    )
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeFailure(f"PDF generation failed: {completed.stderr.strip()[-300:]}")
    import base64

    return base64.b64decode(completed.stdout.strip())


def multipart(session: Session, path: str, content: bytes, filename: str, content_type: str, **fields: str) -> Any:
    import uuid

    boundary = f"tracehollow-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n"
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    from smoke_test import _response

    status, body, headers = session.raw(
        "POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
    )
    return _response(status, body, headers)


def seed(args: argparse.Namespace, password: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    web = Client(args.web_url, args.origin)
    setup = web.request("GET", "/api/v1/setup/status")
    if setup.body.get("setup_required"):
        token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
        created(
            web.request("POST", "/api/v1/setup/admin", {"setup_token": token, "username": ADMIN, "password": password}),
            f"administrator {ADMIN} created",
        )
    admin = Session(args, ADMIN, password)
    existing = {item["username"] for item in admin.get("/api/v1/admin/accounts?limit=100").body["items"]}
    for username, role in ((ANALYST, "analyst"), (VIEWER, "viewer")):
        if username not in existing:
            created(
                admin.send("POST", "/api/v1/admin/accounts", {"username": username, "role": role, "password": password}),
                f"account {username} ({role}) created",
            )
    analyst = Session(args, ANALYST, password)

    if args.reset:
        for item in analyst.get("/api/v1/cases?limit=100").body["items"]:
            if item["title"] == CASE_TITLE:
                expect_status(
                    analyst.send("POST", f"/api/v1/cases/{item['id']}/deletion", {"confirm_title": CASE_TITLE}),
                    202,
                    "existing demo case deletion requested",
                )
                wait_for(
                    "the old demo case to disappear",
                    lambda case_id=item["id"]: analyst.get(f"/api/v1/cases/{case_id}").status == 404,
                    args.timeout,
                    2,
                )

    state["case_id"] = created(
        analyst.send(
            "POST",
            "/api/v1/cases",
            {
                "title": CASE_TITLE,
                "purpose": "Review the public infrastructure of a fictional logistics company and follow its announcements.",
                "scope": "Public pages and the announcement feed of aurora-freight.example, plus documents provided for this demonstration. Synthetic data only.",
                "tags": [TAG, "demo"],
            },
        ),
        "demo case created",
    )["id"]
    created(analyst.send("POST", api(state, "/members"), {"username": VIEWER, "role": "viewer"}), f"{VIEWER} added as a viewer")

    # -- imported material -----------------------------------------------------------------------
    note = analyst.upload(
        state["case_id"],
        REGISTRY_NOTE.encode(),
        "registry-extract.txt",
        kind="text",
        title="Registry extract for aurora-freight.example",
        import_origin="Written for the Tracehollow documentation demo; not a real registry response.",
        source_published_at="2026-08-12T10:00:00+00:00",
    )
    state["note_id"] = created(note, "registry extract imported")["evidence"]["id"]
    whois = analyst.upload(
        state["case_id"],
        json.dumps(WHOIS_RECORD, indent=2).encode(),
        "whois-style-record.json",
        kind="json",
        title="WHOIS-style record (synthetic)",
        import_origin="Synthetic structured record written for the documentation demo.",
    )
    state["whois_id"] = created(whois, "JSON record imported")["evidence"]["id"]
    if not args.skip_pdf:
        document = multipart(
            analyst,
            api(state, "/imports/documents"),
            make_pdf(),
            "service-level-summary.pdf",
            "application/pdf",
            import_origin="Synthetic PDF written for the documentation demo.",
            title="Service level summary (synthetic PDF)",
        )
        job = expect_status(document, 202, "PDF import accepted").body["job"]
        wait_for(
            "the PDF to be processed",
            lambda: analyst.get(api(state, f"/processing-jobs/{job['id']}")).body["status"] in ("completed", "partial", "failed"),
            args.timeout,
            2,
        )
        check(True, "PDF processed into extracted text with a page map")

    # -- entities and relationships --------------------------------------------------------------
    def entity(body: dict[str, Any]) -> str:
        return created(analyst.send("POST", api(state, "/entities"), body), f"entity: {body['display_name']}")["id"]

    ids = {
        "organization": entity(
            {
                "entity_type": "organization",
                "display_name": "Aurora Freight Ltd",
                "description": "Fictional logistics company used in the Tracehollow documentation demo.",
            }
        ),
        "partner": entity({"entity_type": "organization", "display_name": "Northwind Logistics"}),
        "domain": entity(
            {
                "entity_type": "domain",
                "display_name": "aurora-freight.example",
                "identifiers": [{"identifier_type": "domain", "value": "aurora-freight.example"}],
            }
        ),
        "status_domain": entity(
            {
                "entity_type": "domain",
                "display_name": "status.aurora-freight.example",
                "identifiers": [{"identifier_type": "domain", "value": "status.aurora-freight.example"}],
            }
        ),
        "address": entity(
            {
                "entity_type": "ip",
                "display_name": "203.0.113.42",
                "identifiers": [{"identifier_type": "ip", "value": "203.0.113.42"}],
            }
        ),
        "email": entity(
            {
                "entity_type": "email",
                "display_name": "press@aurora-freight.example",
                "identifiers": [{"identifier_type": "email", "value": "press@aurora-freight.example"}],
            }
        ),
        "account": entity(
            {
                "entity_type": "platform_account",
                "display_name": "aurora-ops on forum.example",
                "identifiers": [{"identifier_type": "username", "value": "aurora-ops", "platform": "forum.example"}],
            }
        ),
    }
    state["entity_ids"] = ids
    for source, target, predicate, evidence, description in (
        ("organization", "domain", "owns", [state["note_id"]], "The registry extract names Aurora Freight Ltd as the registrant."),
        ("domain", "address", "resolves_to", [state["note_id"], state["whois_id"]], "Recorded in the registry extract and the structured record."),
        ("email", "organization", "belongs_to", [], "Published as the press contact on the company's press page."),
        ("organization", "partner", "links_to", [], "The partner announcement mentions both companies; not yet reviewed."),
        ("status_domain", "organization", "belongs_to", [], "Linked from the press page as the company's status page."),
    ):
        body = {
            "source_entity_id": ids[source],
            "target_entity_id": ids[target],
            "predicate": predicate,
            "description": description,
        }
        if evidence:
            body["supporting_evidence_ids"] = evidence
        created(analyst.send("POST", api(state, "/relationships"), body), f"relationship: {source} {predicate} {target}")
    created(
        analyst.send(
            "POST",
            api(state, "/notes"),
            {
                "body": "Working note (synthetic): the partner relationship comes from a single announcement and is still unreviewed. "
                "The forum account uses the same name as the company but nothing links it to a person.",
            },
        ),
        "analyst note added",
    )

    # -- collection ------------------------------------------------------------------------------
    fixture_control(feed="v1")
    press_query = created(
        analyst.send(
            "POST",
            api(state, "/saved-queries"),
            {
                "name": "Aurora Freight press room",
                "input_type": "url",
                "input_value": PRESS_URL,
                "connector_ids": ["public_web.page"],
                "limits": {"max_pages": 1, "max_items_per_page": 10},
            },
        ),
        "saved query: press room",
    )
    press_run = run_query(analyst, state, press_query["id"], args.timeout)
    check(press_run["status"] == "completed", f"press page collected ({press_run['status']})")
    state["press_run_id"] = press_run["id"]

    feed_query = created(
        analyst.send(
            "POST",
            api(state, "/saved-queries"),
            {
                "name": "Aurora Freight announcements",
                "input_type": "url",
                "input_value": FEED_URL,
                "connector_ids": ["rss.feed"],
                "limits": {"max_pages": 2, "max_items_per_page": 50},
            },
        ),
        "saved query: announcements",
    )
    state["feed_query_id"] = feed_query["id"]

    # -- monitor with a controlled change ---------------------------------------------------------
    monitor = created(
        analyst.send(
            "POST",
            api(state, "/monitors"),
            {
                "name": "Announcement feed watch",
                "description": "Re-reads the announcement feed every morning and reports what changed.",
                "saved_query_id": feed_query["id"],
                "schedule": {"kind": "daily", "time": "09:00"},
                "timezone": "UTC",
                "scope": {"max_pages": 2, "max_items_per_page": 50},
                "limits": {"max_requests_per_run": 10, "max_items_per_run": 200, "max_run_seconds": 300},
                "budget": {"period": "day", "max_requests": 40},
            },
        ),
        "monitor created (paused)",
    )
    state["monitor_id"] = monitor["id"]

    def monitor_run(label: str) -> dict[str, Any]:
        occurrence = expect_status(analyst.send("POST", api(state, f"/monitors/{monitor['id']}/runs")), 202, f"monitor run: {label}").body
        run = wait_run(analyst, state, occurrence["query_run_id"], args.timeout)
        wait_for(
            f"change detection for the {label} run",
            lambda: analyst.get(api(state, f"/change-sets?query_run_id={run['id']}")).body["total"] >= 1,
            args.timeout,
            2,
        )
        return run

    monitor_run("baseline")
    fixture_control(feed="v2")
    changed = monitor_run("after the controlled change")
    change_set = analyst.get(api(state, f"/change-sets?query_run_id={changed['id']}")).body["items"][0]
    check(change_set["status"] == "changes_detected", f"the monitor reports {change_set['counts']} between the two collections")
    state["change_set_id"] = change_set["id"]
    expect_status(
        analyst.send("PUT", api(state, "/budgets"), {"budgets": [{"metric": "requests", "period": "day", "limit_units": 200}]}),
        200,
        "case collection budget set",
    )

    # -- evidence-grounded answer ------------------------------------------------------------------
    if not args.skip_ai:
        def indexed() -> bool:
            body = analyst.get(api(state, "/ai")).body["index"]
            return body["pending"] == 0 and body["indexing"] == 0 and body["indexed"] >= 4

        wait_for("the case index", indexed, args.ai_timeout, 3)
        conversation = created(analyst.send("POST", api(state, "/ai/conversations"), {}), "conversation created")
        state["conversation_id"] = conversation["id"]
        question = "Which organization registered aurora-freight.example, and which address does it resolve to?"
        asked = expect_status(
            analyst.send("POST", api(state, f"/ai/conversations/{conversation['id']}/questions"), {"question": question, "location": "local"}),
            202,
            "question accepted by the local model",
        ).body
        run = wait_for(
            "the local model to answer",
            lambda: (lambda body: body if body["status"] in ("completed", "failed", "canceled") else None)(
                analyst.get(api(state, f"/ai/runs/{asked['id']}")).body
            ),
            args.ai_timeout,
            3,
        )
        check(run["status"] == "completed", f"answer generated by {run['provider']} {run['model']} ({run['processing_location']})")
        messages = analyst.get(api(state, f"/ai/conversations/{conversation['id']}")).body["messages"]
        answer = next(m for m in messages if m["role"] == "assistant")["answer"]
        citations = [ref for claim in answer["claims"] for ref in claim["citations"]]
        check(bool(citations), f"the answer is {answer['status']} with {len(citations)} citation(s)")

    monitor_state = analyst.get(api(state, f"/monitors/{monitor['id']}")).body
    check(monitor_state["status"] == "paused", "the demo monitor stays paused; nothing collects on a schedule")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--web-url", default="http://localhost:3200")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--ai-timeout", type=float, default=900, help="local models can take minutes on first use")
    parser.add_argument("--skip-ai", action="store_true", help="skip the AI conversation (no local model configured)")
    parser.add_argument("--skip-pdf", action="store_true", help="skip the PDF import")
    parser.add_argument("--reset", action="store_true", help="delete an existing demo case first")
    args = parser.parse_args()
    args.origin = args.origin or args.web_url
    password = os.environ.get("TRACEHOLLOW_DEMO_PASSWORD", "")
    if len(password) < 12:
        print("Set TRACEHOLLOW_DEMO_PASSWORD to at least 12 characters.", file=sys.stderr)
        return 2
    if os.environ.get("COMPOSE_PROJECT_NAME", "") in ("", "tracehollow"):
        print("Set COMPOSE_PROJECT_NAME to a disposable demo project (never 'tracehollow').", file=sys.stderr)
        return 2
    try:
        state = seed(args, password)
    except SmokeFailure as failure:
        print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    print(json.dumps({key: value for key, value in state.items() if key.endswith("_id") or key == "case_id"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
