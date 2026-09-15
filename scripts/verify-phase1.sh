#!/usr/bin/env bash
# Reproducible Phase 1 stack verification against an isolated Docker Compose project.
#
# Covers the PRD Phase 1 acceptance scenarios that need the running stack:
#   1. case, evidence and links survive `docker compose down`/`up` (hashes re-verified)
#   2. one saved query executed twice with independent results and parameter snapshots
#   3. recovery without duplicate results: broker down at creation, lost broker message,
#      duplicate delivery and a worker killed mid-run
#   4. cancellation keeps evidence collected before the cancel
#   5. a non-member cannot reach case records, evidence downloads, runs or exports
#   6. JSON/CSV exports with manifest, hashes and provenance, free of secrets
#   7. case deletion removes database rows and evidence files
#   8. malformed and oversized imports, unsafe filenames, source failures and partial outcomes
#
# Usage: scripts/verify-phase1.sh [--keep] [--e2e]
#   --keep   leave the verification project running (volumes kept) instead of removing it
#   --e2e    also run the Playwright browser workflow (apps/web/e2e); needs pnpm dependencies and
#            a Chromium build (`pnpm exec playwright install chromium` or
#            TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE)
#
# Environment:
#   TRACEHOLLOW_VERIFY_PROJECT   Compose project name (default: tracehollow-verify)
#   TRACEHOLLOW_VERIFY_WEB_PORT  published web port   (default: 3100)
#   TRACEHOLLOW_VERIFY_API_PORT  published API port   (default: 8100)
#
# The project must not already have volumes, so the run starts from a fresh database and never
# touches an existing installation. Only volumes created by this run are removed. All data is
# synthetic; the fixture connector contacts no network service.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase1.sh [--keep] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify}"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3100}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8100}"
web_url="http://localhost:${TRACEHOLLOW_WEB_PORT}"

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
acceptance=(python3 scripts/phase1_acceptance.py --state "$state" --web-url "$web_url")
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
      docker compose logs --tail=60 api worker dispatcher web >&2 || true
    fi
    if [ "$keep" = 1 ]; then
      echo "Keeping project ${COMPOSE_PROJECT_NAME} (stop with: COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} docker compose down)"
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
redis_cli() {
  # The password is read inside the container and passed through the environment, never argv.
  docker compose exec -T redis sh -c 'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" exec redis-cli "$@"' redis-cli "$@"
}
state_value() {
  python3 -c 'import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    value = value[key]
print(value)' "$state" "$@"
}
wait_for() {
  # wait_for <seconds> <description> <command...>
  local seconds="$1" description="$2"
  shift 2
  local deadline=$((SECONDS + seconds))
  until "$@"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "timed out after ${seconds}s waiting for: $description"
    sleep 1
  done
}
evidence_files() {
  docker compose exec -T api sh -c 'find /data/evidence/cases -type f 2>/dev/null | wc -l' | tr -d ' \r'
}

step "Configuration: scripts/setup.sh and docker compose config"
scripts/setup.sh >/dev/null
docker compose config --quiet
ok "compose configuration is valid"

step "Start the stack (build, migrate, wait for health)"
started=1
docker compose up --build --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
for service in dispatcher worker postgres redis; do
  bindings="$(docker inspect "$(docker compose ps -q "$service")" --format '{{json .NetworkSettings.Ports}}')"
  if printf "%s" "$bindings" | grep HostPort >/dev/null; then
    fail "$service publishes a host port: $bindings"
  fi
done
ok "dispatcher, worker, postgres and redis publish no host ports"
docker compose logs --no-log-prefix migrate | grep "Running upgrade 0001 -> 0002" >/dev/null
ok "migrate applied revision 0002 (cases, evidence and query lifecycle)"

step "Scenarios 1 + 8: case, entities, evidence imports, links and hostile imports"
"${acceptance[@]}" seed
case_id="$(state_value case_id)"

step "Scenario 1: restart the stack without deleting volumes and reopen the records"
counts_query="SELECT (SELECT count(*) FROM cases) || ',' || (SELECT count(*) FROM entities) || ',' || (SELECT count(*) FROM evidence_objects) || ',' || (SELECT count(*) FROM relationship_evidence) || ',' || (SELECT count(*) FROM entity_evidence)"
before="$(sql "$counts_query")"
files_before="$(evidence_files)"
[ "$files_before" = 3 ] || fail "expected 3 evidence files on the volume, found $files_before"
docker compose down
docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep evidence-data >/dev/null
docker compose up --detach --wait
after="$(sql "$counts_query")"
[ "$before" = "$after" ] || fail "row counts changed across restart ($before -> $after)"
[ "$(evidence_files)" = "$files_before" ] || fail "evidence file count changed across restart"
ok "cases,entities,evidence,relationship_evidence,entity_evidence = $after and $files_before evidence files kept"
"${acceptance[@]}" reopen

step "Scenarios 2, 4 + 8: repeated execution, fixture outcomes and cancellation"
"${acceptance[@]}" lifecycle

step "Scenario 3a: broker unavailable when the run is created"
docker compose stop redis
"${acceptance[@]}" start-run --key broker-down --expect-dispatch pending
run_id="$(state_value runs broker-down)"
[ "$(sql "SELECT status || ',' || coalesce(last_error_code, '') FROM dispatch_outbox WHERE aggregate_id = '$run_id'")" = "pending,broker_unavailable" ] \
  || fail "outbox row was not left pending after the broker failure"
ok "run committed in PostgreSQL with a pending outbox row while Redis was down"
docker compose up --detach --wait redis
"${acceptance[@]}" await-run --key broker-down --expect-evidence 3 --timeout 300
ok "dispatcher published the pending row after Redis returned"

step "Scenario 3b: broker message lost before a worker received it"
docker compose stop worker
"${acceptance[@]}" start-run --key lost-message --expect-dispatch dispatched
run_id="$(state_value runs lost-message)"
[ "$(redis_cli LLEN tracehollow | tr -d '\r')" = 1 ] || fail "expected exactly one queued broker message"
redis_cli DEL tracehollow >/dev/null
[ "$(redis_cli LLEN tracehollow | tr -d '\r')" = 0 ] || fail "broker queue was not cleared"
ok "queued broker message deleted from Redis (run still queued in PostgreSQL)"
docker compose start worker
"${acceptance[@]}" await-run --key lost-message --expect-evidence 3 --timeout 300
attempts="$(sql "SELECT attempts FROM dispatch_outbox WHERE aggregate_id = '$run_id'")"
[ "$attempts" -ge 2 ] || fail "run completed without a redelivery (attempts=$attempts)"
ok "dispatcher re-published the lost message (outbox attempts=$attempts)"

step "Scenario 3c: the same run delivered twice"
docker compose stop worker
"${acceptance[@]}" start-run --key duplicate --expect-dispatch dispatched
run_id="$(state_value runs duplicate)"
sql "UPDATE dispatch_outbox SET status = 'pending', available_at = now() WHERE aggregate_id = '$run_id'" >/dev/null
queued_twice() { [ "$(redis_cli LLEN tracehollow | tr -d '\r')" = 2 ]; }
wait_for 60 "a second broker message for the same run" queued_twice
ok "two broker messages queued for one run"
docker compose start worker
"${acceptance[@]}" await-run --key duplicate --expect-evidence 3 --timeout 120
queue_drained() { [ "$(redis_cli LLEN tracehollow | tr -d '\r')" = 0 ]; }
wait_for 60 "both messages to be consumed" queue_drained
claims="$(sql "SELECT claim_count FROM query_runs WHERE id = '$run_id'")"
[ "$claims" = 1 ] || fail "duplicate delivery claimed the run $claims times"
ok "both messages consumed; the run was claimed once (claim_count=1)"

step "Scenario 3d: worker killed while a run is in progress"
"${acceptance[@]}" start-run --key crash --scenario slow --max-pages 10 --wait-pages 2
run_id="$(state_value runs crash)"
docker compose kill worker
[ "$(sql "SELECT status FROM query_runs WHERE id = '$run_id'")" = running ] || fail "run is not marked running after the worker was killed"
ok "worker killed; PostgreSQL still records the run as running with a lease"
docker compose start worker
"${acceptance[@]}" await-run --key crash --expect-evidence 10 --timeout 300
claims="$(sql "SELECT claim_count FROM query_runs WHERE id = '$run_id'")"
[ "$claims" -ge 2 ] || fail "run finished without being reclaimed (claim_count=$claims)"
duplicates="$(sql "SELECT count(*) FROM (SELECT connector_run_id, page_index FROM evidence_objects WHERE query_run_id = '$run_id' GROUP BY 1, 2 HAVING count(*) > 1) d")"
[ "$duplicates" = 0 ] || fail "$duplicates page(s) stored twice after recovery"
ok "expired lease reclaimed (claim_count=$claims); run resumed without duplicate pages"

if [ "$e2e" = 1 ]; then
  step "Browser workflow (Playwright): case, evidence, review, reruns, cancel, graph, export, deletion"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "${pnpm[@]}" exec playwright test e2e/phase1-workflow.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +1 passed" "$work_dir/e2e.log" >/dev/null || fail "the browser workflow did not pass (skipped or failed)"
  if grep -E "^ +[0-9]+ (failed|skipped)" "$work_dir/e2e.log" >/dev/null; then fail "a browser test failed or was skipped"; fi
  ok "browser workflow passed"
fi

step "Scenario 5: a non-member account cannot reach the case"
# Phase 1 has no team management, so the second account is created directly for this check.
printf '%s\n' "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" | docker compose exec -T api python -c '
import sys
from app.auth.models import User
from app.auth.security import hash_password, normalize_username
from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory

password = sys.stdin.readline().rstrip("\n")
factory = create_session_factory(create_db_engine(get_settings()))
with factory() as db:
    db.add(User(username="acceptance-outsider", username_normalized=normalize_username("acceptance-outsider"), password_hash=hash_password(password), is_admin=False))
    db.commit()
'
ok "non-administrator account created without case membership"
"${acceptance[@]}" authz

step "Scenario 6: exports with manifest and provenance, free of secrets"
"${acceptance[@]}" export --artifacts "$work_dir/exports"
python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$work_dir/exports/export.zip" "$work_dir/exports/csv"
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -rF "$value" "$work_dir/exports" >/dev/null; then
    fail "the value of secrets/$secret appears in an export"
  fi
done
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"; do
  if grep -rF "$candidate" "$work_dir/exports" >/dev/null; then
    fail "an account password appears in an export"
  fi
done
ok "no secret file values or account passwords in the JSON export or extracted CSV files"

step "Scenario 7: delete the case and verify records and files are gone"
case_files="$(docker compose exec -T api sh -c "find /data/evidence/cases/$case_id -type f | wc -l" | tr -d ' \r')"
[ "$case_files" -gt 3 ] || fail "expected imported and fixture evidence files before deletion, found $case_files"
ok "$case_files evidence files on the volume before deletion"
reconcile_output="$(docker compose exec -T api python -m app.cli reconcile-evidence)" \
  || fail "reconcile-evidence reported integrity problems: $reconcile_output"
printf '%s\n' "$reconcile_output" | grep -E "records checked: +$case_files$" >/dev/null \
  || fail "reconcile-evidence did not check every evidence record: $reconcile_output"
ok "reconcile-evidence re-hashed $case_files records: no missing files, mismatches or orphans"
"${acceptance[@]}" delete
remaining="$(sql "SELECT (SELECT count(*) FROM cases WHERE id = '$case_id') + (SELECT count(*) FROM case_members WHERE case_id = '$case_id') + (SELECT count(*) FROM notes WHERE case_id = '$case_id') + (SELECT count(*) FROM entities WHERE case_id = '$case_id') + (SELECT count(*) FROM entity_identifiers WHERE case_id = '$case_id') + (SELECT count(*) FROM entity_evidence WHERE case_id = '$case_id') + (SELECT count(*) FROM observations WHERE case_id = '$case_id') + (SELECT count(*) FROM relationships WHERE case_id = '$case_id') + (SELECT count(*) FROM relationship_evidence WHERE case_id = '$case_id') + (SELECT count(*) FROM analyst_decisions WHERE case_id = '$case_id') + (SELECT count(*) FROM evidence_objects WHERE case_id = '$case_id') + (SELECT count(*) FROM saved_queries WHERE case_id = '$case_id') + (SELECT count(*) FROM query_runs WHERE case_id = '$case_id') + (SELECT count(*) FROM connector_runs WHERE case_id = '$case_id') + (SELECT count(*) FROM dispatch_outbox WHERE case_id = '$case_id')")"
[ "$remaining" = 0 ] || fail "$remaining database rows still reference the deleted case"
ok "no database rows reference the deleted case"
if docker compose exec -T api test -e "/data/evidence/cases/$case_id"; then
  fail "the deleted case still has an evidence directory"
fi
ok "the case evidence directory was removed from the volume"
[ "$(sql "SELECT status FROM case_deletions WHERE case_id = '$case_id'")" = completed ] || fail "deletion job not recorded as completed"
ok "deletion job kept as an audit record with status 'completed'"
docker compose exec -T api python -m app.cli reconcile-evidence >/dev/null || fail "reconcile-evidence reported problems after deletion"
ok "reconcile-evidence finds no missing or orphaned files after deletion"

step "Secrets and passwords do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
[ -s "$work_dir/service.log" ] || fail "no service logs captured"
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then
    fail "the value of secrets/$secret appears in service logs"
  fi
done
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then
    fail "an account password appears in service logs"
  fi
done
if grep -E "tracehollow_session=[A-Za-z0-9_-]{20,}" "$work_dir/service.log" >/dev/null; then
  fail "a session cookie value appears in service logs"
fi
ok "no secret values, passwords or session cookies in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 1 stack checks passed"
