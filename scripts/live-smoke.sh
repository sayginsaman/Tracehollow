#!/usr/bin/env bash
# Authorized live smoke checks for the production connectors (docs/connectors/live-smoke.md).
#
# Starts an isolated Compose project WITHOUT the fixture overlay, so the collector and the
# discovery egress gateway reach real public sources, and runs only the checks named in the
# authorization file through the application (a saved query per check in a dedicated case).
# AI is disabled for the project. Results (outcomes, codes, counts, provenance fields, no raw
# evidence content) are written to results.json; the case is deleted and the project and its
# volumes are removed afterwards.
#
# Usage: scripts/live-smoke.sh --plan
#        scripts/live-smoke.sh --authorization FILE [--output DIR] [--keep]
# Environment: TRACEHOLLOW_LIVE_PROJECT (default tracehollow-live), TRACEHOLLOW_LIVE_WEB_PORT
# (3200), TRACEHOLLOW_LIVE_API_PORT (8200). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

authorization=""
output=""
keep=0
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) exec python3 scripts/live_smoke.py plan ;;
    --authorization) authorization="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --keep) keep=1; shift ;;
    *) echo "usage: scripts/live-smoke.sh --plan | --authorization FILE [--output DIR] [--keep]" >&2; exit 2 ;;
  esac
done
[ -n "$authorization" ] || { echo "error: --authorization FILE is required (see docs/connectors/live-smoke.md)" >&2; exit 2; }
python3 scripts/live_smoke.py validate --authorization "$authorization"

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_LIVE_PROJECT:-tracehollow-live}"
export COMPOSE_FILE="compose.yaml"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_LIVE_WEB_PORT:-3200}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_LIVE_API_PORT:-8200}"
export TRACEHOLLOW_AI_ENABLED=false
output="${output:-$root/evaluation-output/live-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"

if [ "$COMPOSE_PROJECT_NAME" = "tracehollow" ]; then
  echo "error: refusing to run live checks in the default 'tracehollow' project" >&2
  exit 2
fi
if docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" | grep . >/dev/null; then
  echo "error: project '${COMPOSE_PROJECT_NAME}' already has volumes; set TRACEHOLLOW_LIVE_PROJECT to a new name" >&2
  exit 2
fi

TRACEHOLLOW_LIVE_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_LIVE_PASSWORD
work_dir="$(mktemp -d)"
started=0
finish() {
  status=$?
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      docker compose logs --tail=60 collector discovery-runner discovery-gateway >&2 || true
    fi
    if [ "$keep" = 1 ]; then
      echo "Keeping project ${COMPOSE_PROJECT_NAME}"
    else
      docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$work_dir"
  exit "$status"
}
trap finish EXIT

echo "==> Isolated project ${COMPOSE_PROJECT_NAME} with real outbound access (no fixture overlay)"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose up --build --detach --wait --quiet-pull

echo "==> Authorized live checks"
python3 scripts/live_smoke.py run --authorization "$authorization" --web-url "http://localhost:${TRACEHOLLOW_WEB_PORT}" --output "$output"

echo "==> Secrets and passwords do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token credential_encryption_key; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then echo "error: secrets/$secret appears in service logs" >&2; exit 1; fi
done
if grep -F "$TRACEHOLLOW_LIVE_PASSWORD" "$work_dir/service.log" >/dev/null; then echo "error: the password appears in service logs" >&2; exit 1; fi
grep '"egress_tunnel"' "$work_dir/service.log" | sed 's/^[^{]*//' >"$output/gateway-decisions.jsonl" || true
echo "  ok  no secret values in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines; gateway decisions saved"
echo "Results: $output/results.json"
