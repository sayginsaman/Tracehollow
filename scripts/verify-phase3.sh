#!/usr/bin/env bash
# Reproducible Phase 3 stack verification against an isolated Docker Compose project.
#
# Covers the Phase 3 checks that need the running stack:
#   - pgvector installed by the db-extensions job; migrations on a fresh database and an
#     existing Phase 1 database (downgrade to 0002 with data, upgrade again)
#   - network isolation: only ai-worker has egress
#   - indexing of imported Turkish text and JSON evidence
#   - grounded answers whose citations open the exact bytes of the original evidence
#   - database-backed counts, insufficient-evidence answers, local-only cloud refusal
#   - hostile evidence cannot trigger collection, writes or secret disclosure
#   - non-member and cross-case access to AI records
#   - AI disabled for the installation keeps the workspace working
#   - rebuild and cancel indexing; summaries and relationship suggestions with review
#   - deletion of cited evidence and of the case removes derived retrieval data
#   - backup and restore drill with vectors; full restore of a revision 0002 backup into the Phase 3
#     installation (database swap, upgrade, re-indexing); secrets absent from logs
#
# Usage: scripts/verify-phase3.sh [--keep] [--model] [--e2e]
#   --keep    leave the verification project running (volumes kept)
#   --model   use the local Ollama models on the host instead of the synthetic fixture provider
#             (needs `ollama pull qwen3:8b qwen3-embedding:0.6b`; slower)
#   --e2e     also run the Playwright AI workflow (apps/web/e2e/phase3-ai.spec.ts)
#
# Environment: TRACEHOLLOW_VERIFY_PROJECT (default tracehollow-verify3), TRACEHOLLOW_VERIFY_WEB_PORT
# (3100) and TRACEHOLLOW_VERIFY_API_PORT (8100). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
model=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --model) model=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase3.sh [--keep] [--model] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify3}"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3100}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8100}"
if [ "$model" = 1 ]; then
  export TRACEHOLLOW_AI_LOCAL_PROVIDER=ollama
  timeout=900
else
  export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
  timeout=120
fi
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
acceptance=(python3 scripts/phase3_acceptance.py --state "$state" --web-url "$web_url" --timeout "$timeout")
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
      docker compose logs --tail=60 api ai-worker dispatcher migrate db-extensions >&2 || true
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
state_value() {
  python3 -c 'import json, sys
print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$state" "$1"
}
non_ai_counts() {
  sql "SELECT string_agg(t || '=' || c, ',' ORDER BY t) FROM (
    SELECT 'cases' t, count(*) c FROM cases UNION ALL SELECT 'entities', count(*) FROM entities
    UNION ALL SELECT 'relationships', count(*) FROM relationships
    UNION ALL SELECT 'relationship_evidence', count(*) FROM relationship_evidence
    UNION ALL SELECT 'evidence_objects', count(*) FROM evidence_objects
    UNION ALL SELECT 'saved_queries', count(*) FROM saved_queries
    UNION ALL SELECT 'query_runs', count(*) FROM query_runs
    UNION ALL SELECT 'analyst_decisions', count(*) FROM analyst_decisions
    UNION ALL SELECT 'notes', count(*) FROM notes
    UNION ALL SELECT 'users', count(*) FROM users
    UNION ALL SELECT 'non_ai_outbox', count(*) FROM dispatch_outbox WHERE aggregate_type IN ('query_run', 'case_deletion')) s"
}

step "Configuration and startup (local provider: ${TRACEHOLLOW_AI_LOCAL_PROVIDER})"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose up --build --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
[ "$(docker inspect "$(docker compose ps -aq db-extensions)" --format '{{.State.ExitCode}}')" = 0 ] || fail "db-extensions did not exit cleanly"
[ "$(sql "SELECT extname FROM pg_extension WHERE extname = 'vector'")" = vector ] || fail "pgvector is not installed"
ok "db-extensions installed pgvector as the superuser"
[ "$(sql "SELECT rolsuper FROM pg_roles WHERE rolname = 'tracehollow_app'")" = f ] || fail "application role is a superuser"
ok "application role remains a non-superuser"
docker compose logs --no-log-prefix migrate | grep "Running upgrade 0002 -> 0003" >/dev/null
ok "migrate applied revision 0003 on a fresh database"

step "Network isolation: only ai-worker has model egress"
for service in api worker dispatcher web postgres redis; do
  networks="$(docker inspect "$(docker compose ps -q "$service")" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}')"
  case "$networks" in *ai-egress*) fail "$service is attached to the AI egress network" ;; esac
done
ai_networks="$(docker inspect "$(docker compose ps -q ai-worker)" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}')"
case "$ai_networks" in *ai-egress*) ;; *) fail "ai-worker is not on the AI egress network" ;; esac
bindings="$(docker inspect "$(docker compose ps -q ai-worker)" --format '{{json .NetworkSettings.Ports}}')"
if printf "%s" "$bindings" | grep HostPort >/dev/null; then fail "ai-worker publishes a host port"; fi
ok "ai-worker alone reaches model endpoints and publishes no ports"

step "Seed cases and imported evidence; wait for indexing"
"${acceptance[@]}" seed
"${acceptance[@]}" wait-index --expect-indexed 3
case_id="$(state_value case_id)"

step "Existing database upgrade: downgrade to 0002 with data, then migrate again"
docker compose stop api ai-worker worker dispatcher web >/dev/null
evidence_before="$(sql "SELECT count(*) FROM evidence_objects")"
docker compose run --rm --no-deps -T migrate alembic downgrade 0002 >/dev/null
[ "$(sql "SELECT count(*) FROM pg_tables WHERE tablename = 'document_chunks'")" = 0 ] || fail "downgrade did not remove AI tables"
[ "$(sql "SELECT count(*) FROM evidence_objects")" = "$evidence_before" ] || fail "downgrade changed evidence rows"
scripts/backup.sh "$work_dir/backup-0002" >/dev/null
grep -qx "alembic_revision=0002" "$work_dir/backup-0002/manifest.txt" || fail "backup at revision 0002 not recorded"
ok "backup taken at revision 0002 for the cross-version restore check"
docker compose run --rm --no-deps -T migrate alembic upgrade head >/dev/null
[ "$(sql "SELECT count(*) FROM evidence_index_states WHERE status = 'pending'")" = "$evidence_before" ] || fail "existing evidence was not queued for indexing"
ok "Phase 1 data kept; $evidence_before existing evidence record(s) queued for indexing after upgrade"
docker compose up --detach --wait >/dev/null
"${acceptance[@]}" wait-index --expect-indexed 3
ok "dispatcher and ai-worker indexed the upgraded database without re-import"

step "Grounded answers, exact passages, database counts, abstention and local-only refusal"
"${acceptance[@]}" qa

step "Hostile evidence cannot trigger collection, writes or secret disclosure"
before="$(non_ai_counts)"
"${acceptance[@]}" hostile
after="$(non_ai_counts)"
[ "$before" = "$after" ] || fail "non-AI rows changed during the hostile question ($before -> $after)"
ok "non-AI table and outbox counts unchanged: $after"

step "Authorization for AI records"
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
"${acceptance[@]}" authz

step "AI disabled for the installation keeps the workspace working"
# Recreating containers discards their logs; keep them for the secret scan at the end.
docker compose logs --no-color >>"$work_dir/service.log" 2>&1
TRACEHOLLOW_AI_ENABLED=false docker compose up --detach --wait api ai-worker dispatcher >/dev/null
"${acceptance[@]}" disabled
[ "$(sql "SELECT count(*) FROM dispatch_outbox WHERE aggregate_type = 'case_index' AND status = 'pending'")" = 0 ] || fail "indexing was scheduled while AI was disabled"
ok "no indexing was scheduled while AI was disabled"
docker compose logs --no-color >>"$work_dir/service.log" 2>&1
docker compose up --detach --wait api ai-worker dispatcher >/dev/null
"${acceptance[@]}" wait-index --expect-indexed 4
ok "re-enabled installation indexed evidence imported while AI was off"

step "Rebuild and cancel indexing"
"${acceptance[@]}" reindex
"${acceptance[@]}" wait-index --expect-indexed 4

step "Summaries and relationship suggestions with analyst review"
"${acceptance[@]}" outputs

if [ "$model" = 1 ]; then
  step "Local model answer through the ai-worker container"
  "${acceptance[@]}" model
fi

if [ "$e2e" = 1 ]; then
  step "Browser AI workflow (Playwright)"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "${pnpm[@]}" exec playwright test e2e/phase3-ai.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +1 passed" "$work_dir/e2e.log" >/dev/null || fail "the AI browser workflow did not pass"
  ok "browser AI workflow passed"
fi

step "Deleting cited evidence removes its retrieval data"
"${acceptance[@]}" delete-evidence
[ "$(sql "SELECT count(*) FROM document_chunks WHERE evidence_id = '$(state_value registry_id)'")" = 0 ] || fail "chunks of deleted evidence remain"
ok "no chunks or vectors remain for the deleted evidence"

step "Backup and restore drill with vectors"
scripts/backup.sh "$work_dir/backup" >/dev/null
scripts/restore.sh "$work_dir/backup" --verify-only

step "Case deletion removes AI records and vectors"
job_id="$(python3 - "$web_url" "$case_id" <<'PY' | tail -n 1
import os, sys
sys.path.insert(0, "scripts")
from argparse import Namespace
from phase1_acceptance import ADMIN, Session
args = Namespace(web_url=sys.argv[1], origin=sys.argv[1])
session = Session(args, ADMIN, os.environ["TRACEHOLLOW_ACCEPTANCE_PASSWORD"])
case = session.get(f"/api/v1/cases/{sys.argv[2]}").body
job = session.send("POST", f"/api/v1/cases/{sys.argv[2]}/deletion", {"confirm_title": case["title"]})
assert job.status == 202, job.status
print(job.body["id"])
PY
)"
deleted() { [ "$(sql "SELECT status FROM case_deletions WHERE id = '$job_id'")" = completed ]; }
for _ in $(seq 1 120); do deleted && break; sleep 1; done
deleted || fail "case deletion did not complete"
remaining="$(sql "SELECT (SELECT count(*) FROM document_chunks WHERE case_id = '$case_id') + (SELECT count(*) FROM chunk_embeddings WHERE case_id = '$case_id') + (SELECT count(*) FROM evidence_index_states WHERE case_id = '$case_id') + (SELECT count(*) FROM ai_conversations WHERE case_id = '$case_id') + (SELECT count(*) FROM ai_runs WHERE case_id = '$case_id') + (SELECT count(*) FROM ai_messages WHERE case_id = '$case_id') + (SELECT count(*) FROM ai_citations WHERE case_id = '$case_id') + (SELECT count(*) FROM dispatch_outbox WHERE case_id = '$case_id')")"
[ "$remaining" = 0 ] || fail "$remaining AI rows remain for the deleted case"
if docker compose exec -T api test -e "/data/evidence/cases/$case_id"; then fail "evidence directory remains"; fi
ok "no chunks, vectors, index states, conversations, runs, messages, citations or files remain"
[ "$(sql "SELECT count(*) FROM document_chunks WHERE case_id = '$(state_value other_case_id)'")" -ge 1 ] || fail "other case lost its index"
ok "the other case's index is untouched"

step "Full restore of the revision 0002 backup into the Phase 3 installation"
scripts/restore.sh "$work_dir/backup-0002" --yes-overwrite-current-data
[ "$(sql "SELECT version_num FROM alembic_version")" = 0003 ] || fail "restored database was not upgraded to 0003"
[ "$(sql "SELECT count(*) FROM evidence_objects")" = "$evidence_before" ] || fail "restored evidence rows differ from the backup"
[ "$(sql "SELECT count(*) FROM pg_database WHERE datname LIKE 'tracehollow\_%restore\_%'")" = 0 ] || fail "temporary restore databases remain"
ok "older backup restored by database swap, upgraded by migrate, temporary databases removed"
reconcile_output="$(docker compose exec -T api python -m app.cli reconcile-evidence)" \
  || fail "reconcile-evidence reported integrity problems after restore: $reconcile_output"
ok "reconcile-evidence clean after restore"
"${acceptance[@]}" wait-index --expect-indexed 3
ok "restored evidence indexed again after the restore"

step "Secrets and passwords do not appear in service logs"
docker compose logs --no-color >>"$work_dir/service.log" 2>&1
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then fail "the value of secrets/$secret appears in service logs"; fi
done
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then fail "an account password appears in service logs"; fi
done
if grep -F "NIGHTJAR" "$work_dir/service.log" >/dev/null || grep -F "tarafından" "$work_dir/service.log" >/dev/null; then
  fail "evidence content appears in service logs"
fi
ok "no secret values, passwords or evidence text in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 3 stack checks passed"
