#!/usr/bin/env python3
"""Authorized live smoke checks for the production connectors (docs/connectors/live-smoke.md).

Runs only the checks named in an authorization file, each exactly as defined below: fixed input,
fixed parameters and limits, no credentials. Checks go through the normal application (a saved
query in a dedicated case) so outcomes, evidence and provenance are recorded as in real use. A
failed or unexpected result is reported as a failed live check, never as a success. The results
file keeps outcomes, counts, codes, provenance fields and a few public identifiers, not raw
evidence content; the case is deleted afterwards and scripts/live-smoke.sh removes the project.

Usage (normally through scripts/live-smoke.sh):
  live_smoke.py plan
  live_smoke.py validate --authorization FILE
  live_smoke.py run --authorization FILE --web-url URL --output DIR
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from phase1_acceptance import ADMIN, Session, password
from phase2_acceptance import TERMINAL, evidence_of, observations_of, wait_for
from smoke_test import ROOT, Client, SmokeFailure, expect_status

Verdict = tuple[bool, list[str]]


@dataclass(frozen=True)
class Check:
    check_id: str
    connector: str
    input_type: str
    input_value: str
    parameters: dict[str, Any]
    limits: dict[str, int]
    contacts: str
    target_contacted: bool
    request_bound: str
    expected: str
    evaluate: Callable[[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], Verdict]
    random_suffix: bool = field(default=False)
    # "<connector_id>:<credential name>" when the check cannot run without a key, with the
    # environment variable the value is read from. The authorization file must name it.
    credential: tuple[str, str] | None = field(default=None)


def _connector(result: dict[str, Any]) -> dict[str, Any]:
    return dict(result["connector_runs"][0])


def _web(result: dict[str, Any], evidence: list[dict[str, Any]], _: list[dict[str, Any]]) -> Verdict:
    notes = []
    snapshot = next((e for e in evidence if e["kind"] == "html"), None)
    derived = next((e for e in evidence if e["kind"] == "text" and e.get("derived_from_evidence_id")), None)
    meta = (snapshot or {}).get("collection_metadata") or {}
    ok = (
        _connector(result)["outcome"] == "findings"
        and snapshot is not None
        and derived is not None
        and meta.get("http_status") == 200
        and str(meta.get("final_url", "")).startswith("https://example.com")
        and bool(meta.get("remote_address"))
    )
    notes.append(
        f"HTML snapshot with HTTP {meta.get('http_status')} from {meta.get('final_url')} "
        f"(connected address recorded: {'yes' if meta.get('remote_address') else 'no'}, "
        f"redirects {len(meta.get('redirects') or [])}, truncated {meta.get('truncated')})"
    )
    notes.append(f"derived text evidence: {'yes' if derived else 'no'}")
    return ok, notes


def _rss(result: dict[str, Any], evidence: list[dict[str, Any]], observations: list[dict[str, Any]]) -> Verdict:
    entries = [o for o in observations if o["observation_type"] == "feed_entry"]
    connector = _connector(result)
    ok = connector["outcome"] in ("findings", "partial") and len(entries) > 0 and bool(evidence)
    return ok, [f"{len(entries)} feed entries over {connector['pages_completed']} page(s)", f"outcome {connector['outcome']}"]


def _github(result: dict[str, Any], _: list[dict[str, Any]], observations: list[dict[str, Any]]) -> Verdict:
    accounts = [o for o in observations if o["observation_type"] == "github_account"]
    repositories = [o for o in observations if o["observation_type"] == "github_repository"]
    payload = accounts[0]["payload"] if accounts else {}
    connector = _connector(result)
    quota = connector.get("quota_usage") or {}
    ok = (
        connector["outcome"] in ("findings", "partial")
        and str(payload.get("login", "")).lower() == "octocat"
        and bool(quota)
    )
    return ok, [
        f"account login {payload.get('login')} with platform id {payload.get('id') or payload.get('platform_id')}",
        f"{len(repositories)} repositories on the first repository page",
        f"quota recorded: {sorted(quota)}",
        f"pages {connector['pages_completed']}, outcome {connector['outcome']}",
    ]


def _telegram(
    result: dict[str, Any], evidence: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> Verdict:
    preview = [o for o in observations if o["observation_type"] == "telegram_channel_preview"]
    posts = [o for o in observations if o["observation_type"] == "telegram_post"]
    connector = _connector(result)
    payload = preview[0]["payload"] if preview else {}
    dated = [p for p in posts if p["payload"].get("datetime")]
    ok = (
        connector["outcome"] in ("findings", "partial")
        and bool(preview)
        and len(posts) > 0
        and len(dated) == len(posts)
        and bool(evidence)
    )
    return ok, [
        f"channel {payload.get('channel')!r} titled {payload.get('title')!r}",
        f"{len(posts)} post(s), all carrying the datetime the page showed",
        f"outcome {connector['outcome']}",
    ]


def _youtube_channel(
    result: dict[str, Any], _: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> Verdict:
    channels = [o for o in observations if o["observation_type"] == "youtube_channel"]
    videos = [o for o in observations if o["observation_type"] == "youtube_video"]
    connector = _connector(result)
    payload = channels[0]["payload"] if channels else {}
    quota = connector.get("quota_usage") or {}
    ok = (
        connector["outcome"] in ("findings", "partial")
        and payload.get("channel_id") == YOUTUBE_CHANNEL
        and len(videos) > 0
        and all(v["payload"].get("video_id") for v in videos)
    )
    return ok, [
        f"channel {payload.get('channel_id')} titled {payload.get('title')!r}",
        f"{len(videos)} upload(s), each with a video id",
        f"quota recorded: {bool(quota)}",
        f"outcome {connector['outcome']}",
    ]


def _youtube_comments(
    result: dict[str, Any], _: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> Verdict:
    comments = [o for o in observations if o["observation_type"] == "youtube_comment"]
    connector = _connector(result)
    ok = (
        connector["outcome"] in ("findings", "partial")
        and len(comments) > 0
        and all(c["payload"].get("video_id") == YOUTUBE_VIDEO for c in comments)
    )
    return ok, [
        f"{len(comments)} top-level comment thread(s) on video {YOUTUBE_VIDEO}",
        f"outcome {connector['outcome']}",
    ]


def _sherlock_known(result: dict[str, Any], evidence: list[dict[str, Any]], _: list[dict[str, Any]]) -> Verdict:
    body = _json_body(evidence)
    rows = {row["site"]: row["classification"] for row in body.get("results", [])}
    ok = rows.get("GitHub") == "candidate" and _connector(result)["outcome"] in ("findings", "partial")
    return ok, [f"per-platform classification: {rows}", "GitHub is expected to report a candidate account"]


def _sherlock_absent(result: dict[str, Any], evidence: list[dict[str, Any]], _: list[dict[str, Any]]) -> Verdict:
    body = _json_body(evidence)
    rows = {row["site"]: row["classification"] for row in body.get("results", [])}
    outcome = _connector(result)["outcome"]
    # Absence may only be claimed when every platform answered not_found.
    consistent = (outcome == "no_findings") == (bool(rows) and set(rows.values()) == {"not_found"})
    ok = consistent and "candidate" not in rows.values()
    return ok, [f"per-platform classification: {rows}", f"outcome {outcome}"]


def _subfinder(result: dict[str, Any], evidence: list[dict[str, Any]], observations: list[dict[str, Any]]) -> Verdict:
    body = _json_body(evidence)
    connector = _connector(result)
    sources = body.get("sources", {})
    names = sorted(o["payload"]["host"] for o in observations if o["observation_type"] == "subdomain")
    # Live-verified only when every selected source answered through the sandbox; a source
    # failure is recorded (with its redacted message) and the check fails.
    ok = (
        connector["outcome"] in ("findings", "no_findings")
        and bool(sources.get("selected"))
        and sorted(sources.get("answered", [])) == sorted(sources.get("selected", []))
        and (body.get("egress") or {}).get("sandbox", "").startswith("discovery-runner")
    )
    return ok, [
        f"outcome {connector['outcome']} ({connector.get('last_error_code')})",
        f"sources selected {sources.get('selected')}, answered {sources.get('answered')}, "
        f"refused by gateway {sources.get('refused_by_egress_gateway')}",
        f"source errors (redacted by the connector): {sources.get('errors', {})}",
        f"{len(names)} in-scope names; first: {names[:5]}",
    ]


def _json_body(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return dict(evidence[0].get("_body") or {}) if evidence else {}


YOUTUBE_CHANNEL = "UCBR8-60-B28hp2BmDPdntcQ"  # YouTube's own channel
YOUTUBE_VIDEO = "jNQXAC9IVRw"  # "Me at the zoo", the first video published on YouTube

CHECKS = {
    check.check_id: check
    for check in (
        Check(
            "web.example-com",
            "public_web.page",
            "url",
            "https://example.com/",
            {},
            {"max_pages": 1, "max_items_per_page": 1},
            "example.com (IANA-reserved documentation domain) and any redirect target, after the network policy",
            True,
            "1 GET (plus redirects, at most 5); no retries on success",
            "findings: HTML snapshot with HTTP 200, final URL, connected address, derived text",
            _web,
        ),
        Check(
            "rss.subfinder-releases",
            "rss.feed",
            "url",
            "https://github.com/projectdiscovery/subfinder/releases.atom",
            {},
            {"max_pages": 2, "max_items_per_page": 20},
            "github.com (the feed's own host)",
            True,
            "at most 2 feed pages; retries only for transient failures",
            "findings: feed entries stored once each, with feed evidence",
            _rss,
        ),
        Check(
            "github.octocat",
            "github.account",
            "username",
            "octocat",
            {"include_repositories": True},
            {"max_pages": 2, "max_items_per_page": 20},
            "api.github.com, unauthenticated",
            False,
            "at most 2 API requests (account and one repository page) of the 60/hour anonymous quota",
            "findings: account octocat with its platform id, repositories page, quota recorded",
            _github,
        ),
        Check(
            "telegram.official-channel",
            "telegram.public_channel",
            "username",
            "telegram",
            {"capability": "public_web_preview"},
            {"max_pages": 1, "max_items_per_page": 10},
            "t.me, which serves Telegram's own public announcements channel",
            True,
            "1 GET of the channel's public preview page; retries only for transient failures",
            "findings: channel preview with its title, and posts that each carry an id and a datetime",
            _telegram,
        ),
        Check(
            "youtube.official-channel-uploads",
            "youtube.data_api",
            "youtube_channel_id",
            YOUTUBE_CHANNEL,
            {"capability": "channel_uploads"},
            # Page 0 is the channel itself; uploads arrive on the next page, so one page is not enough.
            {"max_pages": 2, "max_items_per_page": 5},
            "googleapis.com (YouTube Data API v3); the channel itself is not contacted",
            False,
            "2 API requests (channels.list and one playlistItems page), about 2 units of the free 10,000/day quota",
            "findings or partial: the channel with its id and title, and uploads that each carry a video id",
            _youtube_channel,
            credential=("youtube.data_api:api_key", "TRACEHOLLOW_LIVE_YOUTUBE_API_KEY"),
        ),
        Check(
            "youtube.first-video-comments",
            "youtube.data_api",
            "youtube_video_id",
            YOUTUBE_VIDEO,
            {"capability": "video_comments"},
            # Page 0 is the video; comment threads arrive on the next page.
            {"max_pages": 2, "max_items_per_page": 5},
            "googleapis.com (YouTube Data API v3); the video's channel is not contacted",
            False,
            "2 API requests (videos.list and one commentThreads page), about 2 units of the free 10,000/day quota",
            "findings or partial: top-level comment threads, each tied to the requested video id",
            _youtube_comments,
            credential=("youtube.data_api:api_key", "TRACEHOLLOW_LIVE_YOUTUBE_API_KEY"),
        ),
        Check(
            "sherlock.octocat-three-sites",
            "username.sherlock",
            "username",
            "octocat",
            {"sites": ["GitHub", "GitLab", "Codeberg"], "timeout_seconds": 15},
            {"max_pages": 1, "max_items_per_page": 3},
            "github.com, gitlab.com and codeberg.org profile addresses; each learns the name",
            False,
            "3 profile requests, one per platform",
            "GitHub classified as a candidate account; other platforms classified from their responses",
            _sherlock_known,
        ),
        Check(
            "sherlock.unregistered-three-sites",
            "username.sherlock",
            "username",
            "th-smoke-",
            {"sites": ["GitHub", "GitLab", "Codeberg"], "timeout_seconds": 15},
            {"max_pages": 1, "max_items_per_page": 3},
            "github.com, gitlab.com and codeberg.org profile addresses; each learns the random name",
            False,
            "3 profile requests, one per platform",
            "no candidate; no_findings only if every platform answered not_found, otherwise an explicit failure or partial",
            _sherlock_absent,
            random_suffix=True,
        ),
        Check(
            "subfinder.example-com",
            "domain.subfinder",
            "domain",
            "example.com",
            {"sources": ["crtsh", "digitorus"], "max_results": 500},
            {"max_pages": 1, "max_items_per_page": 500},
            "crt.sh and certificatedetails.com through the discovery egress gateway; example.com is not contacted",
            False,
            "one crt.sh API request (its database path cannot route) and one Digitorus request; 300 s run limit",
            "findings or verified no_findings with both sources answering; any provider failure stays explicit",
            _subfinder,
        ),
    )
}


def load_authorization(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for key in ("authorized_by", "authorized_on", "expires_on", "checks", "credentials", "paid_requests"):
        if key not in data:
            problems.append(f"missing {key}")
    if problems:
        raise SystemExit("authorization file invalid: " + ", ".join(problems))
    unknown = [check for check in data["checks"] if check not in CHECKS]
    if unknown:
        raise SystemExit(f"authorization names unknown checks: {unknown}")
    if data["paid_requests"] != 0:
        raise SystemExit("this harness runs only unpaid checks")
    allowed = data["credentials"]
    if allowed != "none" and not (
        isinstance(allowed, list) and all(isinstance(item, str) for item in allowed)
    ):
        raise SystemExit('"credentials" must be "none" or a list of "<connector>:<name>" strings')
    named = set() if allowed == "none" else set(allowed)
    required = {CHECKS[c].credential[0] for c in data["checks"] if CHECKS[c].credential}
    if not required <= named:
        raise SystemExit(
            "these checks need credentials the authorization does not name: "
            + ", ".join(sorted(required - named))
        )
    if named - required:
        raise SystemExit(
            "the authorization names credentials no authorized check uses: "
            + ", ".join(sorted(named - required))
        )
    if date.fromisoformat(data["expires_on"]) < datetime.now(UTC).date():
        raise SystemExit(f"authorization expired on {data['expires_on']}")
    return dict(data)


def plan() -> None:
    for check in CHECKS.values():
        print(f"{check.check_id}: {check.connector} {check.input_type}={check.input_value!r}"
              f"{'<random>' if check.random_suffix else ''}")
        print(f"  contacts: {check.contacts}; investigated target contacted: {check.target_contacted}")
        print(f"  bound: {check.request_bound}; expected: {check.expected}")


def run_checks(args: argparse.Namespace, authorization: dict[str, Any]) -> dict[str, Any]:
    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    admin_password = password("TRACEHOLLOW_LIVE_PASSWORD")
    expect_status(
        web.request("POST", "/api/v1/setup/admin", {"setup_token": token, "username": ADMIN, "password": admin_password}),
        201,
        "administrator created in the isolated live-check project",
    )
    session = Session(args, ADMIN, admin_password)
    connectors = {c["connector_id"]: c for c in expect_status(session.get("/api/v1/connectors"), 200, "sources listed").body}
    expected_credentials = {
        CHECKS[c].credential[0]: CHECKS[c].credential[1]
        for c in authorization["checks"]
        if CHECKS[c].credential
    }
    configured = [
        f"{key}:{c['name']}"
        for key, d in connectors.items()
        for c in d.get("credentials", [])
        if c.get("configured")
    ]
    unexpected = [item for item in configured if item not in expected_credentials]
    if unexpected:
        raise SmokeFailure(
            f"credentials are configured that no authorized check declares ({unexpected})"
        )
    for reference, variable in expected_credentials.items():
        connector_id, name = reference.split(":", 1)
        value = os.environ.get(variable, "").strip()
        if not value:
            raise SmokeFailure(f"{reference} is authorized but {variable} is empty")
        # The value is sent once to the isolated project and never printed or written to results.
        expect_status(
            session.send("POST", f"/api/v1/connectors/{connector_id}/credentials/{name}", {"value": value}),
            200,
            f"{reference} configured in the isolated live-check project",
        )
    title = f"Live smoke checks {datetime.now(UTC).date().isoformat()} (authorized)"
    case = expect_status(
        session.send("POST", "/api/v1/cases", {"title": title, "purpose": "Authorized connector live smoke checks", "scope": "Only the inputs in the authorization file"}),
        201,
        "dedicated case created",
    ).body
    base = f"/api/v1/cases/{case['id']}"
    state = {"case_id": case["id"]}
    results = []
    for check_id in authorization["checks"]:
        check = CHECKS[check_id]
        value = check.input_value + (secrets.token_hex(6) if check.random_suffix else "")
        body = {
            "name": f"live: {check_id}",
            "input_type": check.input_type,
            "input_value": value,
            "connector_ids": [check.connector],
            "parameters": check.parameters,
            "limits": check.limits,
        }
        query = expect_status(session.send("POST", f"{base}/saved-queries", body), 201, f"{check_id}: saved query").body
        started = time.monotonic()
        run_id = expect_status(session.send("POST", f"{base}/saved-queries/{query['id']}/runs"), 202, f"{check_id}: run queued").body["id"]
        result = wait_for(
            f"{check_id} to finish",
            lambda: (lambda r: r if r["status"] in TERMINAL else None)(session.get(f"{base}/runs/{run_id}").body),
            args.timeout,
            interval=2.0,
        )
        evidence = evidence_of(session, state, run_id)
        for item in evidence:
            if item["kind"] == "json":
                status, raw, _ = session.raw("GET", f"{base}/evidence/{item['id']}/content")
                if status == 200:
                    item["_body"] = json.loads(raw)
        observations = observations_of(session, state, run_id)
        passed, notes = check.evaluate(result, evidence, observations)
        connector = _connector(result)
        results.append(
            {
                "check": check_id,
                "connector": check.connector,
                "input": value if not check.random_suffix else "th-smoke-<random 12 hex>",
                "contacts": check.contacts,
                "investigated_target_contacted": check.target_contacted,
                "request_bound": check.request_bound,
                "expected": check.expected,
                "run_status": result["status"],
                "outcome": connector["outcome"],
                "error_code": connector.get("last_error_code"),
                "coverage_note": connector.get("coverage_note"),
                "items": connector.get("items_collected"),
                "pages": connector.get("pages_completed"),
                "evidence": [
                    {
                        "kind": e["kind"],
                        "collection_mode": e.get("collection_mode"),
                        "access_category": e.get("access_category"),
                        "source_reference": e.get("source_reference"),
                        "sha256": e.get("sha256"),
                        "size_bytes": e.get("size_bytes"),
                    }
                    for e in evidence
                ],
                # Measured by polling every 2 seconds, so short runs show the polling interval.
                "duration_seconds": round(time.monotonic() - started, 1),
                "verdict": "live_verified" if passed else "live_check_failed",
                "notes": notes,
            }
        )
        print(f"  {'ok  ' if passed else 'FAIL'} {check_id}: {connector['outcome']} ({connector.get('last_error_code')}) — {'; '.join(notes)}")
    deletion = session.send("POST", f"{base}/deletion", {"confirm_title": title})
    expect_status(deletion, 202, "live-check case deletion queued")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "authorization": {k: authorization[k] for k in ("authorized_by", "authorized_on", "expires_on", "checks")},
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "validate", "run"])
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--web-url", default="http://localhost:3200")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=420)
    args = parser.parse_args()
    args.origin = args.origin or args.web_url
    if args.command == "plan":
        plan()
        return 0
    if args.authorization is None:
        parser.error("--authorization is required")
    authorization = load_authorization(args.authorization)
    if args.command == "validate":
        print(f"authorized checks: {', '.join(authorization['checks'])} (expires {authorization['expires_on']})")
        return 0
    if args.output is None:
        parser.error("--output is required")
    summary = run_checks(args, authorization)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [r["check"] for r in summary["results"] if r["verdict"] != "live_verified"]
    print(f"{len(summary['results']) - len(failed)} of {len(summary['results'])} live checks met their expected behaviour" + (f"; failed: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
