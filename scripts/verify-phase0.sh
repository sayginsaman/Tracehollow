#!/usr/bin/env bash
# Reproducible Phase 0 stack verification against an isolated Docker Compose project.
#
# Covers PRD Phase 0 acceptance scenarios that need the running stack: documented startup,
# fresh-database migrations, safe repeated startup, login/logout/unauthenticated access,
# readiness during dependency failure, broker-to-worker connectivity, persistence across
# `docker compose down`/`up` without deleting volumes, published port bindings and a
# backup/restore drill.
#
# Usage: scripts/verify-phase0.sh [--keep]
#   --keep   leave the verification project running (volumes kept) instead of removing it
#
# Environment:
#   TRACEHOLLOW_VERIFY_PROJECT   Compose project name (default: tracehollow-verify)
#   TRACEHOLLOW_VERIFY_WEB_PORT  published web port   (default: 3100)
#   TRACEHOLLOW_VERIFY_API_PORT  published API port   (default: 8100)
#
# The project must not already have volumes, so the run always starts from a fresh database
# and never touches an existing installation. Only volumes created by this run are removed.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
[ "${1:-}" = "--keep" ] && keep=1

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify}"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3100}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8100}"
web_url="http://localhost:${TRACEHOLLOW_WEB_PORT}"
api_url="http://127.0.0.1:${TRACEHOLLOW_API_PORT}"

if [ "$COMPOSE_PROJECT_NAME" = "tracehollow" ]; then
  echo "error: refusing to verify against the default 'tracehollow' project" >&2
  exit 2
fi
if docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep . >/dev/null; then
  echo "error: project '${COMPOSE_PROJECT_NAME}' already has volumes; set TRACEHOLLOW_VERIFY_PROJECT to a new name" >&2
  exit 2
fi

# A process outside this project (on ::1, for example) can hold the same port while Docker binds
# 127.0.0.1, and then the checks would talk to the wrong server.
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

TRACEHOLLOW_SMOKE_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_SMOKE_PASSWORD
smoke=(python3 scripts/smoke_test.py --web-url "$web_url" --api-url "$api_url")
backup_dir="$(mktemp -d)"
started=0

step() { printf '\n==> %s\n' "$*"; }

finish() {
  status=$?
  rm -rf "$backup_dir"
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "Verification failed; recent service logs:" >&2
      docker compose logs --tail=40 >&2 || true
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

count_rows() {
  docker compose exec -T postgres psql --no-psqlrc -U postgres -d tracehollow -At \
    -c "SELECT (SELECT count(*) FROM users) || ',' || (SELECT count(*) FROM worker_checks)"
}

step "Configuration: scripts/setup.sh and docker compose config"
scripts/setup.sh >/dev/null
docker compose config --quiet
echo "  ok  compose configuration is valid"

step "AC1: start core services (no paid APIs, no LLM)"
started=1
docker compose up --build --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

step "AC7: published ports"
for service in postgres redis; do
  bindings="$(docker inspect "$(docker compose ps -q "$service")" --format '{{json .NetworkSettings.Ports}}')"
  if printf "%s" "$bindings" | grep HostPort >/dev/null; then
    echo "error: $service publishes a host port: $bindings" >&2
    exit 1
  fi
  echo "  ok  $service has no host port binding"
done
for service in api web; do
  bindings="$(docker inspect "$(docker compose ps -q "$service")" --format '{{range $p, $b := .NetworkSettings.Ports}}{{range $b}}{{.HostIp}} {{end}}{{end}}')"
  for ip in $bindings; do
    if [ "$ip" != "127.0.0.1" ]; then
      echo "error: $service is bound to $ip" >&2
      exit 1
    fi
  done
  echo "  ok  $service is bound to 127.0.0.1 only"
done

step "AC2: migrations on a fresh database"
docker compose logs --no-log-prefix migrate | grep "Running upgrade  -> 0001" >/dev/null
echo "  ok  migrate service applied revision 0001 to the empty database"

step "AC3 + AC5: setup, authentication, CSRF, worker round trip, logout"
"${smoke[@]}" --mode fresh

step "AC2: repeated startup is safe and non-destructive"
migrate_log_count() { docker compose logs --no-log-prefix migrate | grep -c "$1" || true; }
before="$(count_rows)"
runs_before="$(migrate_log_count "Will assume transactional DDL")"
upgrades_before="$(migrate_log_count "Running upgrade")"
docker compose up --detach --wait
runs_after="$(migrate_log_count "Will assume transactional DDL")"
[ "$runs_after" -gt "$runs_before" ] || { echo "error: migrate did not run again" >&2; exit 1; }
[ "$(migrate_log_count "Running upgrade")" = "$upgrades_before" ] || { echo "error: repeated startup applied migrations again" >&2; exit 1; }
after="$(count_rows)"
[ "$before" = "$after" ] || { echo "error: row counts changed ($before -> $after)" >&2; exit 1; }
echo "  ok  migrate re-ran as a no-op and data was kept (users,worker_checks = $after)"

step "AC4: readiness while Redis is unavailable"
docker compose stop redis
"${smoke[@]}" --mode readiness --expect-check redis=unavailable --expect-check database=ok
docker compose start redis
"${smoke[@]}" --mode readiness

step "AC4: readiness while PostgreSQL is unavailable"
docker compose stop postgres
"${smoke[@]}" --mode readiness --expect-check database=unavailable --expect-check redis=ok
docker compose start postgres
"${smoke[@]}" --mode readiness

step "AC6: persistence across docker compose down/up without deleting volumes"
"${smoke[@]}" --mode existing --min-worker-checks 1 >/dev/null
before="$(count_rows)"
docker compose down
docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep postgres-data >/dev/null
docker compose up --detach --wait
after="$(count_rows)"
[ "$before" = "$after" ] || { echo "error: row counts changed across restart ($before -> $after)" >&2; exit 1; }
echo "  ok  users,worker_checks = $after before and after restart"
"${smoke[@]}" --mode existing --min-worker-checks "${after#*,}"

step "Operator CLI: check-config, create-admin refusal, reset-password"
docker compose exec -T api python -m app.cli check-config
if printf '%s\n' "$(openssl rand -hex 24)" \
  | docker compose exec -T api python -m app.cli create-admin --username second-admin --password-stdin; then
  echo "error: create-admin succeeded although an administrator exists" >&2
  exit 1
fi
echo "  ok  create-admin refuses when an administrator already exists"
previous_password="$TRACEHOLLOW_SMOKE_PASSWORD"
TRACEHOLLOW_SMOKE_PASSWORD="$(openssl rand -hex 24)"
printf '%s\n' "$TRACEHOLLOW_SMOKE_PASSWORD" \
  | docker compose exec -T api python -m app.cli reset-password --username smoke-admin --password-stdin
"${smoke[@]}" --mode existing --min-worker-checks 1 >/dev/null
echo "  ok  new password signs in after reset-password"
if TRACEHOLLOW_SMOKE_PASSWORD="$previous_password" "${smoke[@]}" --mode existing >/dev/null 2>&1; then
  echo "error: the previous password still works after reset-password" >&2
  exit 1
fi
echo "  ok  previous password no longer signs in"

step "Backup and non-destructive restore drill"
scripts/backup.sh "$backup_dir/backup" >/dev/null
scripts/restore.sh "$backup_dir/backup" --verify-only

step "AC7: secrets do not appear in service logs"
docker compose logs --no-color >"$backup_dir/service.log" 2>&1
[ -s "$backup_dir/service.log" ] || { echo "error: no service logs captured" >&2; exit 1; }
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$backup_dir/service.log" >/dev/null; then
    echo "error: the value of secrets/$secret appears in service logs" >&2
    exit 1
  fi
  echo "  ok  secrets/$secret not present in $(wc -l <"$backup_dir/service.log" | tr -d ' ') log lines"
done
for candidate in "$TRACEHOLLOW_SMOKE_PASSWORD" "$previous_password"; do
  if grep -F "$candidate" "$backup_dir/service.log" >/dev/null; then
    echo "error: an administrator password appears in service logs" >&2
    exit 1
  fi
done
echo "  ok  current and previous administrator passwords not present in service logs"
if grep -E "tracehollow_session=[A-Za-z0-9_-]{20,}" "$backup_dir/service.log" >/dev/null; then
  echo "error: a session cookie value appears in service logs" >&2
  exit 1
fi
echo "  ok  no session cookie values in service logs"

step "All Phase 0 stack checks passed"
