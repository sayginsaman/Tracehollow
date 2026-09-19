#!/usr/bin/env python3
"""Phase 2 acceptance checks against a running stack with controlled fixture sources.

Used by scripts/verify-phase2.sh, which adds a fixture-site container on the collector's egress
network (compose.verify-sources.yaml). No real public source is contacted. Python standard
library only; record identifiers (never secrets) are shared through --state.

Stages:
  seed         administrator, case, source capability listing
  web          public page with redirect, verified 404, truncation, blocked redirect to metadata
  ssrf         prohibited destinations are refused before any request and store nothing
  rss          paginated feed with a repeated entry, broken pagination (partial), malformed feed
  github       account and paginated repositories with quota, not found, rate limit
  username     candidate accounts, verified absence, blocked and rate-limited platform checks
  domain       Subfinder in the network sandbox: key-only source without a key, results through the
               egress gateway, verified empty result, refused and untrusted provider destinations
  domain-down  with the discovery runner stopped, a lookup fails visibly instead of running elsewhere
  credentials  write-only admin credentials; a rejected token becomes authentication_required
  cancel       cancelling a run while the source is still answering
  concurrency  the username engine's single slot queues a second run visibly
  ai           collected page text is indexed and cited with the exact passage
  outsider     a non-member cannot reach collected evidence; non-admins cannot set credentials
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from phase1_acceptance import ADMIN, OTHER, Session, load_state, password, save_state
from smoke_test import ROOT, Client, SmokeFailure, check, expect_status

FIXTURE = "http://fixture-site:8080"
CASE_TITLE = "Kontrollü kaynaklar (synthetic Phase 2 acceptance)"
BAD_TOKEN = "ghp_synthetic_rejected_token_for_verification_0001"
TERMINAL = ("completed", "partial", "failed", "canceled")


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


def create_query(session: Session, state: dict[str, Any], connector: str, input_type: str, value: str, **extra: Any) -> str:
    body = {"name": f"{connector}: {value}"[:200], "input_type": input_type, "input_value": value, "connector_ids": [connector], **extra}
    response = session.send("POST", api(state, "/saved-queries"), body)
    expect_status(response, 201, f"saved query for {connector} ({value})")
    return str(response.body["id"])


def start(session: Session, state: dict[str, Any], query_id: str) -> str:
    response = session.send("POST", api(state, f"/saved-queries/{query_id}/runs"))
    expect_status(response, 202, "run queued")
    return str(response.body["id"])


def finish(session: Session, state: dict[str, Any], run_id: str, timeout: float) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        body = session.get(api(state, f"/runs/{run_id}")).body
        return body if body["status"] in TERMINAL else None

    result: dict[str, Any] = wait_for(f"run {run_id[:8]}", done, timeout)
    return result


def run(session: Session, state: dict[str, Any], connector: str, input_type: str, value: str, timeout: float, **extra: Any) -> dict[str, Any]:
    return finish(session, state, start(session, state, create_query(session, state, connector, input_type, value, **extra)), timeout)


def evidence_of(session: Session, state: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = session.get(api(state, f"/evidence?query_run_id={run_id}&limit=50")).body["items"]
    return items


def observations_of(session: Session, state: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = session.get(api(state, f"/observations?query_run_id={run_id}&limit=100")).body["items"]
    return items


def outcome(result: dict[str, Any]) -> tuple[str, str | None, str | None]:
    connector = result["connector_runs"][0]
    return result["status"], connector["outcome"], connector["last_error_code"]


# -- stages ------------------------------------------------------------------------------------


def stage_seed(args: argparse.Namespace, state: dict[str, Any]) -> None:
    web = Client(args.web_url, args.origin)
    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    setup = {"setup_token": token, "username": ADMIN, "password": password("TRACEHOLLOW_ACCEPTANCE_PASSWORD")}
    expect_status(web.request("POST", "/api/v1/setup/admin", setup), 201, "administrator created")
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    created = expect_status(session.send("POST", "/api/v1/cases", {"title": CASE_TITLE, "purpose": "Phase 2 acceptance", "scope": "Controlled fixture sources only"}), 201, "case created")
    state["case_id"] = created.body["id"]
    connectors = {c["connector_id"]: c for c in expect_status(session.get("/api/v1/connectors"), 200, "source capabilities listed").body}
    # Phase 2's own sources must be registered. Later phases add connectors of their own, so this
    # does not demand that nothing else is registered; it names anything missing.
    expected = {"synthetic.fixture", "public_web.page", "rss.feed", "github.account", "username.sherlock", "domain.subfinder"}
    missing = sorted(expected - set(connectors))
    check(
        not missing,
        f"missing Phase 2 connectors: {', '.join(missing)}"
        if missing
        else f"Phase 2 connectors registered, {len(connectors)} in total: {', '.join(sorted(connectors))}",
    )
    modes = {key: c["collection_mode"] for key, c in connectors.items()}
    check(modes["public_web.page"] == "direct_request" and modes["github.account"] == "third_party_api" and modes["username.sherlock"] == "platform_probe", "collection modes distinguish direct requests, third-party lookups and platform probes")
    live = {"public_web.page", "rss.feed", "github.account", "username.sherlock"}
    check(all((c["last_live_verification"] == "2026-09-15") == (k in live) for k, c in connectors.items()), "only connectors with a recorded authorized live check carry a live verification date")
    check(all(c["verification_status"] == ("live_verified" if k in live else "fixture_tested") for k, c in connectors.items() if k != "synthetic.fixture"), "verification labels: live-verified web, feed, GitHub and username connectors; fixture-tested Subfinder")
    for key, descriptor in connectors.items():
        for field in ("coverage", "credential_requirements", "cache_policy", "output_schema", "documentation"):
            check(bool(descriptor[field]), f"{key} declares {field}")


def stage_web(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    result = run(session, state, "public_web.page", "url", f"{FIXTURE}/web/redirect", args.timeout)
    check(outcome(result)[:2] == ("completed", "findings"), f"web page collected: {outcome(result)}")
    evidence = {item["title"].split(":")[0]: item for item in evidence_of(session, state, result["id"])}
    snapshot, text = evidence["Web page snapshot"], evidence["Web page text"]
    check(snapshot["kind"] == "html" and snapshot["acquisition_method"] == "connector_collection", "byte-exact HTML snapshot stored as collected evidence")
    check(snapshot["collection_mode"] == "direct_request" and snapshot["access_category"] == "public", "collection mode and access category recorded")
    metadata = snapshot["collection_metadata"]
    check(metadata["requested_url"] == f"{FIXTURE}/web/redirect" and metadata["final_url"] == f"{FIXTURE}/web/ornek", "requested and final URL recorded")
    check(metadata["http_status"] == 200 and len(metadata["redirects"]) == 1, "HTTP status and redirect chain recorded")
    check(metadata["remote_address"] == "172.31.250.10", "connected address recorded (the checked address)")
    check(snapshot["source_published_at"] is not None, "source publication time kept separately from collection time")
    check(text["derived_from_evidence_id"] == snapshot["id"], "extracted text links to its snapshot")
    preview = session.get(api(state, f"/evidence/{text['id']}/preview")).body
    check("İzmir şubesi 2026-09-10 tarihinde" in preview["text"] and "SYSTEM OVERRIDE" not in preview["text"], "extracted Turkish text without script content")
    raw = session.get(api(state, f"/evidence/{snapshot['id']}/preview")).body
    check("<script>" in raw["text"], "snapshot preview is the original markup shown as inert text")
    observations = observations_of(session, state, result["id"])
    check([o["observation_type"] for o in observations] == ["web_page"] and observations[0]["evidence_id"] == text["id"], "web_page observation references its evidence")
    state["web_text_evidence_id"] = text["id"]

    missing = run(session, state, "public_web.page", "url", f"{FIXTURE}/web/missing", args.timeout)
    check(outcome(missing)[:2] == ("completed", "no_findings"), f"HTTP 404 is an explicit no-findings result: {outcome(missing)}")
    check("does not show that the content never existed" in (missing["connector_runs"][0]["coverage_note"] or ""), "404 note explains what a missing page does not prove")
    forbidden = run(session, state, "public_web.page", "url", f"{FIXTURE}/web/forbidden", args.timeout)
    check(outcome(forbidden)[:2] == ("failed", "access_denied"), f"HTTP 403 is access_denied, not no findings: {outcome(forbidden)}")
    big = run(session, state, "public_web.page", "url", f"{FIXTURE}/web/big", args.timeout)
    check(outcome(big)[:2] == ("partial", "partial"), f"oversized response is partial: {outcome(big)}")
    check("truncated" in (big["connector_runs"][0]["coverage_note"] or ""), "truncation explained")
    metadata_redirect = run(session, state, "public_web.page", "url", f"{FIXTURE}/web/to-metadata", args.timeout)
    check(outcome(metadata_redirect) == ("failed", "unsupported", "blocked_address"), f"redirect to the metadata address refused: {outcome(metadata_redirect)}")
    check(evidence_of(session, state, metadata_redirect["id"]) == [], "nothing stored for the refused redirect")


def stage_ssrf(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    cases = {
        "http://169.254.169.254/latest/meta-data/": "blocked_address",
        "http://[::ffff:169.254.169.254]/latest/meta-data/": "blocked_address",
        "http://127.0.0.1:8080/": "blocked_address",
        "http://localhost:8080/": "blocked_host",
        "http://postgres:5432/": "blocked_port",
        "http://redis:8080/": "blocked_address",
        "http://api:8080/": "blocked_address",
        "http://2130706433:8080/": "blocked_address",
    }
    for url, code in cases.items():
        result = run(session, state, "public_web.page", "url", url, args.timeout)
        check(outcome(result) == ("failed", "unsupported", code), f"{url} refused with {code}")
        check(evidence_of(session, state, result["id"]) == [], f"{url}: no evidence stored")


def stage_rss(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    limits = {"limits": {"max_pages": 5, "max_items_per_page": 50}}
    result = run(session, state, "rss.feed", "url", f"{FIXTURE}/feed/rss", args.timeout, **limits)
    connector = result["connector_runs"][0]
    check(outcome(result)[:2] == ("completed", "findings"), f"paginated feed collected: {outcome(result)}")
    check(connector["pages_completed"] == 2 and connector["items_collected"] == 3, f"two pages, three unique entries ({connector['pages_completed']} pages, {connector['items_collected']} entries)")
    check(connector["coverage"].get("duplicate_observations_skipped") == 1, "the entry repeated on page 2 was stored once")
    entries = [o for o in observations_of(session, state, result["id"]) if o["observation_type"] == "feed_entry"]
    check(sorted(o["source_object_id"] for o in entries) == ["haber-1", "haber-2", "haber-3"], "entry IDs kept from the feed")
    check(any(o["source_published_at"] for o in entries), "entry publication dates recorded")

    broken = run(session, state, "rss.feed", "url", f"{FIXTURE}/feed/paginated-broken", args.timeout, **limits)
    connector = broken["connector_runs"][0]
    check(outcome(broken)[:2] == ("partial", "partial") and connector["pages_completed"] == 1, f"failing next page keeps page 1 and is partial: {outcome(broken)}")
    check(connector["retries"] >= 1, "the failing page was retried before giving up")
    malformed = run(session, state, "rss.feed", "url", f"{FIXTURE}/feed/malformed", args.timeout)
    check(outcome(malformed)[:2] == ("failed", "parse_error"), f"malformed feed is parse_error: {outcome(malformed)}")


def stage_github(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    result = run(session, state, "github.account", "username", "ornek-dev", args.timeout, limits={"max_pages": 5, "max_items_per_page": 2})
    connector = result["connector_runs"][0]
    check(outcome(result)[:2] == ("completed", "findings"), f"GitHub account collected: {outcome(result)}")
    check(connector["pages_completed"] == 3 and connector["items_collected"] == 4, f"account plus two repository pages ({connector['pages_completed']} pages, {connector['items_collected']} items)")
    check(connector["quota_usage"]["remaining"] == 55 and connector["quota_usage"]["cost"] == "none", "rate-limit quota recorded without invented cost")
    types = sorted({o["observation_type"] for o in observations_of(session, state, result["id"])})
    check(types == ["github_account", "github_repository"], f"observation types: {types}")
    relationships = session.get(api(state, "/relationships?limit=50")).body["items"]
    check({r["predicate"] for r in relationships} >= {"links_to", "lists_email"}, "profile website and public email kept as observed relationships")

    missing = run(session, state, "github.account", "username", "missing-dev", args.timeout)
    check(outcome(missing)[:2] == ("completed", "no_findings"), f"API 404 is verified no findings: {outcome(missing)}")
    limited = run(session, state, "github.account", "username", "limited-dev", args.timeout)
    connector = limited["connector_runs"][0]
    check(outcome(limited) == ("failed", "rate_limited", "github_rate_limited"), f"rate limit is rate_limited, never empty: {outcome(limited)}")
    check(connector["retry_after_seconds"] == 3600 and connector["quota_usage"]["remaining"] == 0, "retry information and quota kept")


def stage_username(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    result = run(session, state, "username.sherlock", "username", "ornekdev", args.timeout, parameters={"sites": ["GitHub", "GitLab", "Reddit", "Keybase"], "timeout_seconds": 10})
    connector = result["connector_runs"][0]
    check(outcome(result)[:2] == ("partial", "partial"), f"mixed platform results are partial: {outcome(result)}")
    classes = connector["coverage"]["classifications"]
    check(classes == {"candidate": 2, "rate_limited": 1, "blocked": 1}, f"per-platform classifications {classes}")
    observations = observations_of(session, state, result["id"])
    check(len(observations) == 2 and all(o["observation_type"] == "candidate_account" and o["payload"]["candidate"] for o in observations), "hits are candidate accounts")
    run_detail = session.get(api(state, f"/runs/{result['id']}")).body
    check(run_detail["relationship_count"] == 0, "no relationship links candidate accounts to each other or to a person")
    evidence = evidence_of(session, state, result["id"])[0]
    body = json.loads(session.get(api(state, f"/evidence/{evidence['id']}/preview")).body["text"])
    reddit = next(entry for entry in body["results"] if entry["site"] == "Reddit")
    check(reddit["classification"] == "rate_limited" and reddit["http_status"] == 429, "HTTP 429 recorded as rate limited, not as 'username available'")
    keybase = next(entry for entry in body["results"] if entry["site"] == "Keybase")
    check(keybase["classification"] == "blocked", "the engine's request to a metadata address was refused")

    absent = run(session, state, "username.sherlock", "username", "yokkullanici", args.timeout, parameters={"sites": ["GitHub", "GitLab"]})
    check(outcome(absent)[:2] == ("completed", "no_findings"), f"verified absence on every checked platform is no findings: {outcome(absent)}")


def stage_domain(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    result = run(session, state, "domain.subfinder", "domain", "ornek.example", args.timeout, parameters={"sources": ["virustotal"]})
    connector = result["connector_runs"][0]
    check(outcome(result)[:2] == ("failed", "authentication_required"), f"key-only source without a key is actionable: {outcome(result)}")
    check(connector["coverage"]["sources_skipped_missing_key"] == ["virustotal"], "skipped source named")
    evidence = evidence_of(session, state, result["id"])[0]
    check(evidence["collection_metadata"]["engine_version"] == "v2.16.0", "engine version recorded with the evidence")
    check(evidence["collection_mode"] == "third_party_api", "passive lookup recorded as a third-party lookup")

    # Real Subfinder in the sandbox; crt.sh resolves (in the gateway only) to the fixture's HTTPS
    # stand-in, whose certificate the verification gateway trusts.
    found = run(session, state, "domain.subfinder", "domain", "sandbox-lab.example", args.timeout, parameters={"sources": ["crtsh"]})
    check(outcome(found) == ("completed", "findings", None), f"crt.sh results through the egress gateway: {outcome(found)}")
    hosts = sorted(o["payload"]["host"] for o in observations_of(session, state, found["id"]))
    check(hosts == ["dev.sandbox-lab.example", "mail.sandbox-lab.example", "www.sandbox-lab.example"], f"subdomains recorded: {hosts}")
    connector = found["connector_runs"][0]
    check(connector["coverage"]["sources_answered"] == ["crtsh"], "crt.sh counted as answered from its completed HTTPS request")
    body_evidence = evidence_of(session, state, found["id"])[0]
    check(body_evidence["collection_metadata"]["network_sandbox"] == "discovery-runner", "evidence records that Subfinder ran in the sandbox")
    check("database connection is not available" in (connector["coverage_note"] or ""), "crt.sh's unroutable database path is disclosed, not hidden")
    state["sandbox_found_run"] = found["id"]

    empty = run(session, state, "domain.subfinder", "domain", "empty-lab.example", args.timeout, parameters={"sources": ["crtsh"]})
    check(outcome(empty) == ("completed", "no_findings", None), f"a completed crt.sh request with no names is a verified empty result: {outcome(empty)}")

    refused = run(session, state, "domain.subfinder", "domain", "sandbox-lab.example", args.timeout, parameters={"sources": ["digitorus", "anubis"]})
    check(outcome(refused) == ("failed", "unsupported", "provider_destination_refused"), f"providers resolving to metadata and loopback are refused: {outcome(refused)}")
    refusals = refused["connector_runs"][0]["coverage"]
    body = session.get(api(state, f"/evidence/{evidence_of(session, state, refused['id'])[0]['id']}")).body
    check(refusals["sources_answered"] == [], "no refused provider counted as answered")
    check("blocked_address" in json.dumps(body), "the gateway's refusal code is stored with the evidence")

    untrusted = run(session, state, "domain.subfinder", "domain", "sandbox-lab.example", args.timeout, parameters={"sources": ["rapiddns"]})
    check(outcome(untrusted) == ("failed", "unavailable", "upstream_certificate_invalid"), f"a provider certificate that does not match fails visibly: {outcome(untrusted)}")


def stage_domain_down(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    result = run(session, state, "domain.subfinder", "domain", "sandbox-lab.example", args.timeout, parameters={"sources": ["crtsh"]})
    check(outcome(result) == ("failed", "unavailable", "discovery_runner_unavailable"), f"without the sandbox runner the lookup fails visibly: {outcome(result)}")
    check(evidence_of(session, state, result["id"]) == [], "nothing is stored when the sandbox is unavailable")


def stage_credentials(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    path = "/api/v1/connectors/github.account/credentials/token"
    response = expect_status(session.send("POST", path, {"value": BAD_TOKEN}), 200, "administrator stored a GitHub token")
    check(BAD_TOKEN not in json.dumps(response.body), "the response never contains the token")
    check(BAD_TOKEN not in json.dumps(session.get("/api/v1/connectors").body), "the source listing never contains the token")
    result = run(session, state, "github.account", "username", "token-dev", args.timeout)
    check(outcome(result) == ("failed", "authentication_required", "github_bad_credentials"), f"rejected token is authentication_required: {outcome(result)}")
    github = next(c for c in session.get("/api/v1/connectors").body if c["connector_id"] == "github.account")
    check(github["credentials"][0]["last_result"] == "rejected", "credential marked as rejected for the administrator")
    expect_status(session.send("DELETE", path), 200, "token removed")
    after = run(session, state, "github.account", "username", "token-dev", args.timeout)
    check(outcome(after)[:2] == ("completed", "findings"), "without the token the public lookup works again")


def stage_cancel(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    run_id = start(session, state, create_query(session, state, "public_web.page", "url", f"{FIXTURE}/web/slow"))
    wait_for("the slow run to start", lambda: session.get(api(state, f"/runs/{run_id}")).body["status"] == "running", args.timeout)
    time.sleep(2)
    expect_status(session.send("POST", api(state, f"/runs/{run_id}/cancel")), 200, "cancel requested while the source is answering")
    started = time.monotonic()
    result = finish(session, state, run_id, args.timeout)
    check(result["status"] == "canceled", f"run canceled ({result['status']}) after {time.monotonic() - started:.0f}s")
    check(evidence_of(session, state, run_id) == [], "no partial evidence from the interrupted request")


def stage_concurrency(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    params = {"parameters": {"sites": ["Codeberg"], "timeout_seconds": 20}}
    first = start(session, state, create_query(session, state, "username.sherlock", "username", "ornekdev", **params))
    second = start(session, state, create_query(session, state, "username.sherlock", "username", "baskasi", **params))
    seen_waiting = threading.Event()

    def watch() -> None:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline and not seen_waiting.is_set():
            for run_id in (first, second):
                detail = session.get(api(state, f"/runs/{run_id}")).body
                progress = (detail["connector_runs"][0]["coverage"] or {}).get("progress") or {}
                if progress.get("waiting_for_slot"):
                    seen_waiting.set()
            time.sleep(0.5)

    watch()
    results = [finish(session, state, run_id, args.timeout) for run_id in (first, second)]
    check(seen_waiting.is_set(), "a second username-engine run waited for the single collection slot")
    check([r["status"] for r in results] == ["completed", "completed"], f"both runs finished: {[r['status'] for r in results]}")
    timings = sorted((r["connector_runs"][0]["started_at"], r["connector_runs"][0]["finished_at"]) for r in results)
    check(timings[1][0] >= timings[0][1], "the second run started collecting only after the first finished")


def stage_ai(args: argparse.Namespace, state: dict[str, Any]) -> None:
    session = Session(args, ADMIN, password("TRACEHOLLOW_ACCEPTANCE_PASSWORD"))
    base = api(state, "/ai")

    def indexed() -> bool:
        detail = session.get(api(state, f"/evidence/{state['web_text_evidence_id']}")).body
        return bool(detail.get("index") and detail["index"]["status"] == "indexed")

    wait_for("the collected page text to be indexed", indexed, args.timeout)
    check(True, "collected page text indexed for AI retrieval")
    conversation = expect_status(session.send("POST", f"{base}/conversations", {"title": "Collected sources"}), 201, "conversation created").body
    question = session.send("POST", f"{base}/conversations/{conversation['id']}/questions", {"question": "Örnek A.Ş. İzmir şubesi hangi tarihte açıldı?", "location": "local"})
    expect_status(question, 202, "question about collected evidence accepted")
    wait_for("the AI answer", lambda: session.get(f"{base}/runs/{question.body['id']}").body["status"] in ("completed", "failed"), args.timeout)
    detail = session.get(f"{base}/conversations/{conversation['id']}").body
    answer = next(m for m in detail["messages"] if m["role"] == "assistant")["answer"]
    refs = [ref for claim in answer["claims"] for ref in claim["citations"] if ref["ref_type"] == "chunk"]
    check(bool(refs), f"answer ({answer['status']}) cites collected evidence")
    for ref in refs:
        passage = session.get(f"{base}/citations/{ref['citation_id']}").body["passage"]
        check(passage["status"] == "available", f"citation {ref['label']} opens its exact passage")
        evidence = session.get(api(state, f"/evidence/{passage['evidence_id']}")).body["evidence"]
        check(evidence["acquisition_method"] == "connector_collection", f"citation {ref['label']} points at collected evidence ({evidence['title'][:50]})")


def stage_outsider(args: argparse.Namespace, state: dict[str, Any]) -> None:
    outsider = Session(args, OTHER, password("TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"))
    for path in ("", "/evidence", f"/evidence/{state['web_text_evidence_id']}", f"/evidence/{state['web_text_evidence_id']}/content", "/runs", "/saved-queries"):
        status = outsider.get(api(state, path)).status
        check(status == 404, f"outsider GET case{path or ''} -> {status}")
    denied = outsider.send("POST", "/api/v1/connectors/github.account/credentials/token", {"value": "x" * 12})
    check(denied.status == 403, "a non-administrator cannot store credentials")
    listing = outsider.get("/api/v1/connectors").body
    check(all(c["health"]["last_run_at"] is None for c in listing), "source health shows only runs in cases the user can open")


STAGES = {
    "seed": stage_seed,
    "web": stage_web,
    "ssrf": stage_ssrf,
    "rss": stage_rss,
    "github": stage_github,
    "username": stage_username,
    "domain": stage_domain,
    "domain-down": stage_domain_down,
    "credentials": stage_credentials,
    "cancel": stage_cancel,
    "concurrency": stage_concurrency,
    "ai": stage_ai,
    "outsider": stage_outsider,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--timeout", type=float, default=120)
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
