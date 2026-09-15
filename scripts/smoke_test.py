#!/usr/bin/env python3
"""End-to-end smoke test against a running Tracehollow stack (Python standard library only).

Exercises the browser-facing path through the web proxy plus direct API checks:
setup, invalid/valid login, unauthenticated rejection, CSRF and origin enforcement,
broker-to-worker round trip, logout invalidation and persisted records.

The administrator password is taken from TRACEHOLLOW_SMOKE_PASSWORD and never printed.

Examples:
  TRACEHOLLOW_SMOKE_PASSWORD=... scripts/smoke_test.py --mode fresh
  TRACEHOLLOW_SMOKE_PASSWORD=... scripts/smoke_test.py --mode existing --min-worker-checks 1
  scripts/smoke_test.py --mode readiness --expect-check redis=unavailable
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class SmokeFailure(AssertionError):
    pass


@dataclass
class Response:
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


class Client:
    def __init__(self, base_url: str, origin: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.origin = origin
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        browser: bool = True,
    ) -> Response:
        data = None if body is None else json.dumps(body).encode()
        merged = {"Accept": "application/json"}
        if data is not None:
            merged["Content-Type"] = "application/json"
        if browser and self.origin:
            merged["Origin"] = self.origin
            merged["Sec-Fetch-Site"] = "same-origin"
        merged.update(headers or {})
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=merged
        )
        try:
            with self.opener.open(request, timeout=30) as raw:
                return _response(raw.status, raw.read(), raw.headers)
        except urllib.error.HTTPError as error:
            return _response(error.code, error.read(), error.headers)

    def session_cookie(self) -> str | None:
        for cookie in self.cookies:
            if cookie.name == "tracehollow_session":
                return cookie.value
        return None


def _response(status: int, raw: bytes, headers: Any) -> Response:
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw.decode(errors="replace")
    return Response(status, body, {k.lower(): v for k, v in headers.items()})


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)
    print(f"  ok  {message}")


def expect_status(response: Response, status: int, message: str) -> Response:
    check(response.status == status, f"{message} (HTTP {response.status}, expected {status})")
    return response


def login(client: Client, username: str, password: str) -> str:
    response = client.request(
        "POST", "/api/v1/auth/login", {"username": username, "password": password}
    )
    expect_status(response, 200, "valid credentials sign in")
    cookie = response.headers.get("set-cookie", "").lower()
    check("httponly" in cookie and "samesite=strict" in cookie, "session cookie is HttpOnly and SameSite=Strict")
    token: str = response.body["csrf_token"]
    return token


def run_fresh(args: argparse.Namespace, password: str) -> None:
    web = Client(args.web_url, args.origin)
    print("First-run setup")
    status = expect_status(web.request("GET", "/api/v1/setup/status"), 200, "setup status reachable")
    check(status.body == {"setup_required": True}, "fresh installation requires setup")

    token = (ROOT / "secrets" / "bootstrap_token").read_text().strip()
    payload = {"setup_token": "0" * len(token), "username": args.username, "password": password}
    expect_status(web.request("POST", "/api/v1/setup/admin", payload), 403, "wrong setup token is rejected")
    payload["setup_token"] = token
    expect_status(web.request("POST", "/api/v1/setup/admin", payload), 201, "administrator created with setup token")
    payload["username"] = "second-admin"
    expect_status(web.request("POST", "/api/v1/setup/admin", payload), 409, "setup cannot run twice")

    run_auth_and_worker(args, password, web)


def run_auth_and_worker(args: argparse.Namespace, password: str, web: Client | None = None) -> None:
    web = web or Client(args.web_url, args.origin)
    api = Client(args.api_url, None)

    print("Unauthenticated access")
    for method, path in [
        ("GET", "/api/v1/auth/session"),
        ("GET", "/api/v1/system/status"),
        ("GET", "/api/v1/system/worker"),
        ("GET", "/api/v1/system/worker-checks"),
        ("POST", "/api/v1/system/worker-checks"),
    ]:
        expect_status(web.request(method, path), 401, f"{method} {path} rejects anonymous request (web proxy)")
    expect_status(api.request("GET", "/api/v1/system/status"), 401, "GET /api/v1/system/status rejects anonymous request (direct API)")

    print("Login")
    expect_status(
        web.request("POST", "/api/v1/auth/login", {"username": args.username, "password": password + "x"}),
        401,
        "invalid password is rejected",
    )
    expect_status(
        web.request("POST", "/api/v1/auth/login", {"username": "no-such-user", "password": password}),
        401,
        "unknown user is rejected",
    )
    check(web.session_cookie() is None, "failed logins issue no session cookie")
    cross = web.request(
        "POST",
        "/api/v1/auth/login",
        {"username": args.username, "password": password},
        headers={"Origin": "http://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )
    expect_status(cross, 403, "cross-origin login is rejected")
    csrf = login(web, args.username, password)
    session = expect_status(web.request("GET", "/api/v1/auth/session"), 200, "session is active")
    check(session.body["user"]["username"] == args.username, "session belongs to the administrator")

    print("System status")
    system = expect_status(web.request("GET", "/api/v1/system/status"), 200, "authenticated status available")
    check(system.body["ready"] is True, "all API dependencies are ready")

    print("Broker-to-worker round trip")
    before = expect_status(web.request("GET", "/api/v1/system/worker-checks?limit=50"), 200, "worker check history readable")
    check(len(before.body) >= args.min_worker_checks, f"at least {args.min_worker_checks} persisted worker check(s) present")
    expect_status(
        web.request("POST", "/api/v1/system/worker-checks"), 403, "state change without CSRF token is rejected"
    )
    # A worker may need a few seconds to re-subscribe after a broker restart.
    deadline = time.monotonic() + args.worker_wait
    while True:
        worker = web.request("GET", "/api/v1/system/worker")
        if worker.status != 200 or worker.body["status"] == "online" or time.monotonic() > deadline:
            break
        time.sleep(2)
    expect_status(worker, 200, "worker status available")
    check(worker.body["status"] == "online", f"worker answers broker ping ({worker.body['note']})")
    created = web.request("POST", "/api/v1/system/worker-checks", headers={"X-CSRF-Token": csrf})
    expect_status(created, 202, "worker check queued")
    deadline = time.monotonic() + 30
    result = created.body
    while result["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(0.5)
        result = web.request("GET", f"/api/v1/system/worker-checks/{created.body['id']}").body
    check(result["status"] == "completed", f"worker completed the check ({result.get('worker_hostname')})")

    print("Logout")
    stolen = web.session_cookie()
    expect_status(web.request("POST", "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}), 204, "logout succeeds")
    replay = Client(args.web_url, args.origin)
    replay.request("GET", "/api/health/live")
    replay_headers = {"Cookie": f"tracehollow_session={stolen}"}
    expect_status(
        replay.request("GET", "/api/v1/auth/session", headers=replay_headers),
        401,
        "replayed session cookie is invalid after logout",
    )


def run_readiness(args: argparse.Namespace) -> None:
    api = Client(args.api_url, None)
    expected = dict(item.split("=", 1) for item in args.expect_check)
    want_ready = not expected or all(value == "ok" for value in expected.values())
    deadline = time.monotonic() + args.timeout
    while True:
        response = api.request("GET", "/api/health/ready")
        checks = response.body.get("checks", {}) if isinstance(response.body, dict) else {}
        matches = all(checks.get(name) == value for name, value in expected.items())
        status_ok = response.status == (200 if want_ready else 503)
        if (matches and status_ok) or time.monotonic() > deadline:
            break
        time.sleep(1)
    print(f"Readiness: HTTP {response.status} {json.dumps(response.body)}")
    check(status_ok, f"readiness returns HTTP {200 if want_ready else 503}")
    check(matches, f"readiness checks match {expected}")
    live = api.request("GET", "/api/health/live")
    expect_status(live, 200, "liveness stays OK independent of dependencies")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["fresh", "existing", "readiness"], required=True)
    parser.add_argument("--web-url", default=os.environ.get("TRACEHOLLOW_SMOKE_WEB_URL", "http://localhost:3000"))
    parser.add_argument("--api-url", default=os.environ.get("TRACEHOLLOW_SMOKE_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--origin", default=None, help="browser Origin header (defaults to the web URL)")
    parser.add_argument("--username", default=os.environ.get("TRACEHOLLOW_SMOKE_USERNAME", "smoke-admin"))
    parser.add_argument("--min-worker-checks", type=int, default=0)
    parser.add_argument("--expect-check", action="append", default=[], metavar="NAME=STATUS")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--worker-wait", type=float, default=45, help="seconds to wait for a worker")
    args = parser.parse_args()
    args.origin = args.origin or args.web_url.rstrip("/")

    try:
        if args.mode == "readiness":
            run_readiness(args)
        else:
            password = os.environ.get("TRACEHOLLOW_SMOKE_PASSWORD")
            if not password:
                print("TRACEHOLLOW_SMOKE_PASSWORD must be set", file=sys.stderr)
                return 2
            if args.mode == "fresh":
                run_fresh(args, password)
            else:
                run_auth_and_worker(args, password)
    except SmokeFailure as failure:
        print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
