#!/usr/bin/env bash
# Reproducible Phase 5 stack verification against an isolated Docker Compose project.
#
# Adds the controlled fixture container (compose.verify-phase5.yaml): a feed that changes on
# command and a local webhook receiver. No real source or notification service is contacted. The
# webhook adapter and a one-minute monitor floor are enabled for this project only. Covers the six
# Phase 5 acceptance criteria and the failure scenarios in docs/STATUS.md:
#   - migrations 0006-0008; roles at API level (viewer, analyst, administrator), incl. AI and exports
#   - repeated collections: controlled change, partial run without deletion claims, real absence
#   - schedules: scheduler-dispatched slots, three competing dispatchers, downtime without a burst
#   - collector killed mid-request: recovery, no duplicate evidence, budget reservations reconciled
#   - concurrent runs against one case budget; cancellation; disabling a monitor during its run
#   - membership revoked while work is queued
#   - webhook: typed-host enablement, signed redacted payloads, retries with a stable event id,
#     destination disabled before a retry
#   - STIX 2.1 subset: validation with the OASIS stix2 library, import, idempotency, round trip,
#     malformed and oversized bundles, URLs never fetched
#   - retention: preview, typed activation waiting for active work, tombstones, expired citations
#   - audit trails; case deletion; secrets and collected content absent from service logs
#
# Usage: scripts/verify-phase5.sh [--keep] [--e2e]
# Environment: TRACEHOLLOW_VERIFY_PROJECT (default tracehollow-verify5), TRACEHOLLOW_VERIFY_WEB_PORT
# (3160) and TRACEHOLLOW_VERIFY_API_PORT (8160). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase5.sh [--keep] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify5}"
export COMPOSE_FILE="compose.yaml:compose.verify-phase5.yaml"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3160}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8160}"
export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
export TRACEHOLLOW_INSTALL_OCR=false
# Verification-only settings: the fixture subnet, a one-minute monitor floor and the webhook adapter.
export TRACEHOLLOW_COLLECTION_ALLOWED_PRIVATE_NETWORKS=172.31.252.0/24
export TRACEHOLLOW_COLLECTION_ALLOWED_PORTS=80,443,8080
export TRACEHOLLOW_MONITOR_MIN_INTERVAL_MINUTES=1
export TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED=true
web_url="http://localhost:${TRACEHOLLOW_WEB_PORT}"
timeout=180

if [ "$COMPOSE_PROJECT_NAME" = "tracehollow" ]; then
  echo "error: refusing to verify against the default 'tracehollow' project" >&2
  exit 2
fi
if docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep . >/dev/null; then
  echo "error: project '${COMPOSE_PROJECT_NAME}' already has volumes; set TRACEHOLLOW_VERIFY_PROJECT to a new name" >&2
  exit 2
fi
port_free() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect((address, port))
    except OSError:
        continue
    sys.exit(1)
sys.exit(0)
PY
}
for port in "$TRACEHOLLOW_WEB_PORT" "$TRACEHOLLOW_API_PORT"; do
  if ! port_free "$port"; then
    echo "error: port $port is already in use by another process; set TRACEHOLLOW_VERIFY_WEB_PORT and TRACEHOLLOW_VERIFY_API_PORT" >&2
    exit 2
  fi
done

TRACEHOLLOW_ACCEPTANCE_PASSWORD="$(openssl rand -hex 24)"
TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_ACCEPTANCE_PASSWORD TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD
work_dir="$(mktemp -d)"
chmod 755 "$work_dir"
state="$work_dir/state.json"
acceptance=(python3 scripts/phase5_acceptance.py --state "$state" --web-url "$web_url" --timeout "$timeout" --work-dir "$work_dir")
started=0

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

finish() {
  status=$?
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "Verification failed; recent service logs:" >&2
      docker compose logs --tail=80 api collector dispatcher worker ai-worker fixture-site migrate >&2 || true
    fi
    if [ "$keep" = 1 ]; then
      echo "Keeping project ${COMPOSE_PROJECT_NAME} (stop with: COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} COMPOSE_FILE=${COMPOSE_FILE} docker compose down)"
      # The generated passwords of this disposable project, to rerun single stages against it.
      umask 077
      {
        printf 'export COMPOSE_PROJECT_NAME=%q COMPOSE_FILE=%q\n' "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"
        # Without these, recreating a service would drop the verification-only settings.
        for name in TRACEHOLLOW_WEB_PORT TRACEHOLLOW_API_PORT TRACEHOLLOW_AI_LOCAL_PROVIDER TRACEHOLLOW_INSTALL_OCR \
          TRACEHOLLOW_COLLECTION_ALLOWED_PRIVATE_NETWORKS TRACEHOLLOW_COLLECTION_ALLOWED_PORTS \
          TRACEHOLLOW_MONITOR_MIN_INTERVAL_MINUTES TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED; do
          printf 'export %s=%q\n' "$name" "${!name}"
        done
        printf 'export TRACEHOLLOW_ACCEPTANCE_PASSWORD=%q TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD=%q\n' "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD"
      } >"$work_dir/env"
      echo "Kept stage state in $work_dir (state.json, env); rerun a stage with:"
      echo "  source $work_dir/env && python3 scripts/phase5_acceptance.py <stage> --state $work_dir/state.json --web-url $web_url --work-dir $work_dir"
    else
      docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  if [ "$keep" != 1 ] || [ "$started" != 1 ]; then rm -rf "$work_dir"; fi
  exit "$status"
}
trap finish EXIT

keep_logs() {
  # Recreated or killed containers lose their logs; keep them for the log checks at the end.
  docker compose logs --no-color "$@" >>"$work_dir/recreated-services.log" 2>&1 || true
}

step "Configuration, build and startup"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose build --quiet
docker compose up --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
for revision in "0005 -> 0006" "0006 -> 0007" "0007 -> 0008"; do
  docker compose logs --no-log-prefix migrate | grep "Running upgrade $revision" >/dev/null || fail "migration $revision was not applied"
done
ok "migrate applied revisions 0006, 0007 and 0008"

step "Accounts, cases and membership"
"${acceptance[@]}" seed

step "Roles at API level (acceptance 4)"
"${acceptance[@]}" roles

step "Repeated collection: controlled change, partial run, real absence (acceptance 1)"
"${acceptance[@]}" monitor

step "Webhook notifications to a local receiver (acceptance 6)"
"${acceptance[@]}" webhook
"${acceptance[@]}" webhook-disabled

step "Scheduling with one dispatcher (acceptance 2)"
"${acceptance[@]}" schedule-start

step "Scheduling with three competing dispatchers"
docker compose up --detach --wait --no-recreate --scale dispatcher=3 dispatcher >/dev/null
"${acceptance[@]}" schedule-concurrent

step "Scheduler downtime and restart"
"${acceptance[@]}" schedule-mark
keep_logs dispatcher
docker compose stop dispatcher >/dev/null
ok "every dispatcher stopped; waiting 150 seconds (at least two one-minute slots)"
sleep 150
docker compose up --detach --wait --scale dispatcher=1 dispatcher >/dev/null
"${acceptance[@]}" schedule-restart

step "Collector killed during a request"
"${acceptance[@]}" crash-start
keep_logs collector
docker compose kill collector >/dev/null
ok "collector killed mid-request"
docker compose up --detach --wait collector >/dev/null
"${acceptance[@]}" crash-check

step "Concurrent budgets and cancellation (acceptance 3)"
"${acceptance[@]}" budget
"${acceptance[@]}" cancel

step "Membership revoked while work is queued"
keep_logs collector
docker compose stop collector >/dev/null
"${acceptance[@]}" revoke-start
docker compose up --detach --wait collector >/dev/null
"${acceptance[@]}" revoke-check

step "STIX 2.1 exchange (acceptance 5)"
"${acceptance[@]}" stix
(cd services/api && uv run --quiet python - "$work_dir/export.json" "$work_dir/round-trip.json" <<'PY'
import json, sys
import stix2
for path in sys.argv[1:]:
    bundle = json.load(open(path, encoding="utf-8"))
    parsed = stix2.parse(bundle, allow_custom=False)
    print(f"  ok  {path.rsplit('/', 1)[-1]}: {len(parsed.objects)} objects parse with the OASIS stix2 library (allow_custom=False)")
PY
)

step "Retention with an AI citation and overlapping work"
"${acceptance[@]}" retention

step "Audit trails"
"${acceptance[@]}" audit

if [ "$e2e" = 1 ]; then
  step "Browser workflow: monitors, notifications, members and administration (Playwright)"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=p5-analyst \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" TRACEHOLLOW_E2E_ADMIN_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_ADMIN_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" TRACEHOLLOW_E2E_VIEWER_USERNAME=p5-viewer \
    "${pnpm[@]}" exec playwright test e2e/phase5-monitoring.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +[0-9]+ passed" "$work_dir/e2e.log" >/dev/null || fail "the Phase 5 browser workflow did not pass"
  if grep -E "^ +[0-9]+ (failed|skipped|flaky)" "$work_dir/e2e.log" >/dev/null; then fail "a browser test failed, was skipped or was flaky"; fi
  ok "Phase 5 browser workflow passed"
fi

step "Case deletion"
deleted_case="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['case_id'])" "$state")"
"${acceptance[@]}" delete
remaining="$(docker compose exec -T worker sh -c "find /data/evidence/cases/$deleted_case -type f 2>/dev/null | wc -l" | tr -d ' ')"
[ "$remaining" = 0 ] || fail "$remaining evidence file(s) of the deleted case remain on the volume"
ok "no evidence files of the deleted case remain on the volume"

step "Secrets and collected content do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
cat "$work_dir/recreated-services.log" >>"$work_dir/service.log" 2>/dev/null || true
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" "İzmir ofisi" "Ankara toplantısı" "Yeni şube" "203.0.113.9 adresine"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then fail "a secret or collected content appears in service logs: ${candidate:0:12}…"; fi
done
signing_secret="$(cat "$work_dir/signing-secret")"
[ -n "$signing_secret" ] || fail "the webhook signing secret was not recorded"
if grep -F "$signing_secret" "$work_dir/service.log" >/dev/null; then fail "the webhook signing secret appears in service logs"; fi
for secret in postgres_app_password redis_password app_secret_key credential_encryption_key; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then fail "the value of secrets/$secret appears in service logs"; fi
done
ok "no passwords, keys, signing secret or collected feed text in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 5 stack checks passed"
