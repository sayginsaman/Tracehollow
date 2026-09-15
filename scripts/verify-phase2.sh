#!/usr/bin/env bash
# Reproducible Phase 2 stack verification against an isolated Docker Compose project.
#
# Adds a controlled fixture-source container (compose.verify-sources.yaml) on the collector's
# egress network; no real public source is contacted. Covers:
#   - migration 0004, the collector service with pinned engines, network isolation
#   - public web page: provenance, redirects, derived text, 404, 403, truncation
#   - SSRF: metadata, loopback, internal service names, numeric hosts, blocked redirect hop
#   - RSS pagination with deduplication, broken pagination (partial), malformed feed
#   - GitHub API shape: pagination, quota, verified not found, rate limit, rejected token
#   - username discovery: candidate accounts, 429 and blocked checks never read as absence
#   - Subfinder: key-only source without a key is authentication_required
#   - cancellation during a request, per-connector concurrency slot, non-member access
#   - collected text indexed and cited by the AI assistant (synthetic AI provider)
#   - credentials, secrets and tokens absent from service logs
#
# Usage: scripts/verify-phase2.sh [--keep] [--e2e]
# Environment: TRACEHOLLOW_VERIFY_PROJECT (default tracehollow-verify2), TRACEHOLLOW_VERIFY_WEB_PORT
# (3100) and TRACEHOLLOW_VERIFY_API_PORT (8100). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase2.sh [--keep] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify2}"
export COMPOSE_FILE="compose.yaml:compose.verify-sources.yaml"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3100}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8100}"
export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
web_url="http://localhost:${TRACEHOLLOW_WEB_PORT}"
timeout=150

if [ "$COMPOSE_PROJECT_NAME" = "tracehollow" ]; then
  echo "error: refusing to verify against the default 'tracehollow' project" >&2
  exit 2
fi
if docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep . >/dev/null; then
  echo "error: project '${COMPOSE_PROJECT_NAME}' already has volumes; set TRACEHOLLOW_VERIFY_PROJECT to a new name" >&2
  exit 2
fi

TRACEHOLLOW_ACCEPTANCE_PASSWORD="$(openssl rand -hex 24)"
TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_ACCEPTANCE_PASSWORD TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD
work_dir="$(mktemp -d)"
state="$work_dir/state.json"
acceptance=(python3 scripts/phase2_acceptance.py --state "$state" --web-url "$web_url" --timeout "$timeout")
started=0

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

finish() {
  status=$?
  rm -rf "$work_dir"
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "Verification failed; recent service logs:" >&2
      docker compose logs --tail=80 api collector dispatcher fixture-site migrate >&2 || true
    fi
    if [ "$keep" = 1 ]; then
      echo "Keeping project ${COMPOSE_PROJECT_NAME} (stop with: COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} COMPOSE_FILE=${COMPOSE_FILE} docker compose down)"
    else
      docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  exit "$status"
}
trap finish EXIT

sql() {
  docker compose exec -T postgres psql --no-psqlrc -v ON_ERROR_STOP=1 -U postgres -d tracehollow -At -c "$1"
}
networks_of() {
  docker inspect "$(docker compose ps -q "$1")" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}'
}

step "Configuration and startup"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose up --build --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
docker compose logs --no-log-prefix migrate | grep "Running upgrade 0003 -> 0004" >/dev/null || fail "migration 0004 was not applied"
ok "migrate applied revision 0004"
[ "$(docker compose exec -T collector id -u)" = 10001 ] || fail "collector does not run as the unprivileged user"
docker compose exec -T -e HOME=/tmp collector subfinder -version 2>&1 | grep "Current Version: v2.16.0" >/dev/null || fail "collector lacks the pinned Subfinder"
docker compose exec -T collector python -c "import importlib.metadata as m; assert m.version('sherlock-project') == '0.16.2'" || fail "collector lacks the pinned Sherlock"
ok "collector runs as UID 10001 with Subfinder v2.16.0 and sherlock-project 0.16.2"
if docker compose exec -T api python -c "import sherlock_project" >/dev/null 2>&1; then fail "the api image contains collection engines"; fi
ok "api image does not contain the collection engines"

step "Network isolation"
for service in api worker dispatcher ai-worker web postgres redis; do
  case "$(networks_of "$service")" in *collect-egress*) fail "$service is attached to the collection egress network" ;; esac
done
case "$(networks_of collector)" in *collect-egress*) ;; *) fail "collector is not on the collection egress network" ;; esac
case "$(networks_of fixture-site)" in *_data*) fail "fixture-site must not reach the data network" ;; esac
if docker inspect "$(docker compose ps -q collector)" --format '{{json .NetworkSettings.Ports}}' | grep HostPort >/dev/null; then
  fail "collector publishes a host port"
fi
ok "only collector has collection egress; it publishes no ports; the fixture site cannot reach data services"

step "Seed and source capabilities"
"${acceptance[@]}" seed

step "Public web page collection"
"${acceptance[@]}" web

step "SSRF protections in the running collector"
"${acceptance[@]}" ssrf

step "RSS/Atom feeds"
"${acceptance[@]}" rss

step "GitHub API connector"
"${acceptance[@]}" github

step "Username discovery (Sherlock)"
"${acceptance[@]}" username

step "Passive domain discovery (Subfinder) without credentials"
"${acceptance[@]}" domain

step "Credentials"
"${acceptance[@]}" credentials

step "Cancellation during a request"
"${acceptance[@]}" cancel

step "Per-connector concurrency slot"
"${acceptance[@]}" concurrency

step "Collected evidence in the AI workspace"
"${acceptance[@]}" ai

step "Non-member access"
printf '%s\n' "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" | docker compose exec -T api python -c '
import sys
from app.auth.models import User
from app.auth.security import hash_password, normalize_username
from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory

password = sys.stdin.readline().strip()
factory = create_session_factory(create_db_engine(get_settings()))
with factory() as db:
    db.add(User(username="acceptance-outsider", username_normalized=normalize_username("acceptance-outsider"), password_hash=hash_password(password), is_admin=False))
    db.commit()
'
"${acceptance[@]}" outsider

if [ "$e2e" = 1 ]; then
  step "Browser workflow: sources and a collected page (Playwright)"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "${pnpm[@]}" exec playwright test e2e/phase2-sources.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +1 passed" "$work_dir/e2e.log" >/dev/null || fail "the sources browser workflow did not pass"
  if grep -E "^ +[0-9]+ (failed|skipped)" "$work_dir/e2e.log" >/dev/null; then fail "a browser test failed or was skipped"; fi
  ok "browser sources workflow passed"
fi

step "Where collection ran and what was stored"
claimed_by_worker="$(docker compose logs --no-color worker | grep -c '"query_run_claimed"' || true)"
[ "$claimed_by_worker" = 0 ] || fail "the internal worker executed $claimed_by_worker collection run(s)"
claimed_by_collector="$(docker compose logs --no-color collector | grep -c '"query_run_claimed"' || true)"
[ "$claimed_by_collector" -ge 20 ] || fail "collector claimed only $claimed_by_collector runs"
ok "collector executed $claimed_by_collector collection runs; the internal worker executed none"
blocked="$(sql "SELECT count(*) FROM connector_runs WHERE last_error_code IN ('blocked_address','blocked_host','blocked_port')")"
blocked_evidence="$(sql "SELECT count(*) FROM evidence_objects e JOIN connector_runs c ON c.id = e.connector_run_id WHERE c.last_error_code IN ('blocked_address','blocked_host','blocked_port')")"
[ "$blocked" -ge 9 ] && [ "$blocked_evidence" = 0 ] || fail "blocked runs: $blocked, evidence from blocked runs: $blocked_evidence"
ok "$blocked refused destinations, no evidence stored for any of them"
no_findings="$(sql "SELECT count(*) FROM connector_runs WHERE outcome = 'no_findings'")"
[ "$no_findings" = 4 ] || fail "expected exactly 4 verified no-findings results, found $no_findings"
ok "exactly four runs reported no findings (404 page, API 404, two verified absences); no failure did"
provenance="$(sql "SELECT count(*) FROM evidence_objects WHERE acquisition_method = 'connector_collection' AND (collection_mode IS NULL OR access_category IS NULL OR source_reference IS NULL)")"
[ "$provenance" = 0 ] || fail "$provenance collected evidence record(s) lack provenance"
ok "every collected evidence record has collection mode, access category and source reference"
[ "$(sql "SELECT count(*) FROM integration_credentials")" = 0 ] || fail "the removed credential is still stored"
ok "removed credential deleted from the database"

step "Secrets, tokens and credentials do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token credential_encryption_key; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then fail "the value of secrets/$secret appears in service logs"; fi
done
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" "ghp_synthetic_rejected_token_for_verification_0001"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then fail "a password or token appears in service logs"; fi
done
if grep -F "Alsancak ofisinde" "$work_dir/service.log" >/dev/null; then fail "collected page content appears in service logs"; fi
ok "no secret values, passwords, tokens or collected content in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 2 stack checks passed"
