#!/usr/bin/env bash
# Release-readiness drills for a candidate (PRD Phase 6 acceptance 1 and 2).
#
# Every drill runs in its own Compose project with its own volumes and ports, from a *clean
# checkout* of this repository (no .env, no secrets/, no node_modules, no existing data), so it
# proves what the documentation alone produces. Nothing touches the default `tracehollow` project.
#
#   install   A clean checkout installed with the documented Quick start: setup.sh, compose up,
#             first-run setup, a case, an import, a synthetic collection, exports and readiness.
#             Records the versions that were actually used.
#   upgrade   The previous recorded release state (TRACEHOLLOW_RELEASE_PREVIOUS, default the last
#             Phase 4 commit) is installed and filled with synthetic records; the same volumes are
#             then served by the candidate, migrations run, and every record, hash, membership,
#             saved query and setting is checked again.
#   restore   A source installation is filled with representative material (members, evidence,
#             derived text, an AI citation, an enabled monitor, audit events), backed up with
#             scripts/backup.sh while writers are stopped, and restored into a second clean
#             installation with scripts/restore.sh, following docs/operations/backup-restore.md.
#             The restored installation is checked for hashes, references, previews, citations,
#             membership limits, paused monitors and the absence of resumed work.
#
# Usage: scripts/verify-release.sh [--keep] [install|upgrade|restore ...]
# Environment: TRACEHOLLOW_RELEASE_PREVIOUS (git revision of the previous release state),
# TRACEHOLLOW_RELEASE_PORT_BASE (default 3170; the drills use base, base+10, base+20, base+25).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
stages=()
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    install | upgrade | restore) stages+=("$argument") ;;
    *) echo "usage: scripts/verify-release.sh [--keep] [install|upgrade|restore ...]" >&2; exit 2 ;;
  esac
done
[ ${#stages[@]} -gt 0 ] || stages=(install upgrade restore)

candidate="$(git rev-parse HEAD)"
previous="${TRACEHOLLOW_RELEASE_PREVIOUS:-0f1350e}"
port_base="${TRACEHOLLOW_RELEASE_PORT_BASE:-3170}"
work_dir="$(mktemp -d)"
chmod 755 "$work_dir"
TRACEHOLLOW_RELEASE_PASSWORD="$(openssl rand -hex 24)"
TRACEHOLLOW_RELEASE_OTHER_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_RELEASE_PASSWORD TRACEHOLLOW_RELEASE_OTHER_PASSWORD
projects=()
project_dirs=()

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
note() { printf '  ..  %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

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

# A drill project must not exist yet, so a run always starts from an empty database.
prepare_project() {
  local name="$1" web="$2" api="$3"
  [ "$name" = "tracehollow" ] && fail "refusing to use the default 'tracehollow' project"
  if docker volume ls -q --filter "label=com.docker.compose.project=${name}" | grep . >/dev/null; then
    fail "project '${name}' already has volumes; remove it or set TRACEHOLLOW_RELEASE_PORT_BASE"
  fi
  for port in "$web" "$api"; do
    port_free "$port" || fail "port $port is already in use; set TRACEHOLLOW_RELEASE_PORT_BASE"
  done
  projects+=("$name")
  project_dirs+=("${4:-$work_dir}")
}

finish() {
  status=$?
  if [ "$keep" = 1 ]; then
    echo
    echo "Kept drill projects: ${projects[*]:-none}"
    echo "Kept checkouts and backups in $work_dir"
  else
    for index in "${!projects[@]}"; do
      name="${projects[$index]}"
      directory="${project_dirs[$index]}"
      [ -n "$name" ] && [ -f "$directory/compose.yaml" ] || continue
      (cd "$directory" && COMPOSE_PROJECT_NAME="$name" docker compose down --volumes --remove-orphans >/dev/null 2>&1) || true
    done
    rm -rf "$work_dir"
  fi
  exit "$status"
}
trap finish EXIT

# A clean checkout of one revision: no working-tree leftovers, no .env, no secrets/.
clone_at() {
  local revision="$1" target="$2"
  git clone --quiet --no-hardlinks "$root" "$target"
  git -C "$target" checkout --quiet "$revision"
  [ -e "$target/.env" ] && fail "the clean checkout unexpectedly contains .env"
  [ -e "$target/secrets" ] && fail "the clean checkout unexpectedly contains secrets/"
  git -C "$target" log -1 --format='  ..  checkout %h %s' | cut -c1-140
}

acceptance() {
  local checkout="$1" web_port="$2" stage="$3"
  (cd "$root" && python3 scripts/release_acceptance.py "$stage" \
    --state "$work_dir/state-${stage%%-*}.json" \
    --web-url "http://localhost:${web_port}" \
    --checkout "$checkout" \
    --timeout 240)
}

compose_in() { # checkout project web api -- args...
  local checkout="$1" project="$2" web="$3" api="$4"; shift 4
  (cd "$checkout" && COMPOSE_PROJECT_NAME="$project" TRACEHOLLOW_WEB_PORT="$web" TRACEHOLLOW_API_PORT="$api" \
    TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture docker compose "$@")
}

record_versions() {
  local checkout="$1" project="$2" web="$3" api="$4"
  {
    echo "Recorded $(date -u +%Y-%m-%dT%H:%M:%SZ) for candidate ${candidate:0:12}"
    echo "host: $(uname -srm)"
    docker version --format 'docker: {{.Server.Version}} (client {{.Client.Version}})'
    docker compose version --short | sed 's/^/compose: /'
    echo "images:"
    compose_in "$checkout" "$project" "$web" "$api" config --images | sort -u | sed 's/^/  /'
    echo "pinned base images (digest from the running containers):"
    docker inspect "$(compose_in "$checkout" "$project" "$web" "$api" ps -q postgres)" --format '  postgres: {{.Config.Image}} -> {{.Image}}'
    docker inspect "$(compose_in "$checkout" "$project" "$web" "$api" ps -q redis)" --format '  redis: {{.Config.Image}} -> {{.Image}}'
    echo "runtime inside the images:"
    compose_in "$checkout" "$project" "$web" "$api" exec -T api python -c "import sys, fastapi, sqlalchemy, celery; print(f'  api: python {sys.version.split()[0]}, fastapi {fastapi.__version__}, sqlalchemy {sqlalchemy.__version__}, celery {celery.__version__}')"
    compose_in "$checkout" "$project" "$web" "$api" exec -T web node -e "console.log('  web: node ' + process.versions.node + ', next ' + require('next/package.json').version)"
    compose_in "$checkout" "$project" "$web" "$api" exec -T api sh -c 'alembic current 2>/dev/null | tail -1' | sed 's/^/  migration head: /'
  } >"$work_dir/versions.txt" 2>&1
  sed 's/^/  /' "$work_dir/versions.txt"
}

# -- install ------------------------------------------------------------------------------------

drill_install() {
  local checkout="$work_dir/install" project="tracehollow-release-install"
  local web=$((port_base)) api=$((port_base + 5000))
  step "Clean install from a clean checkout (documented Quick start)"
  prepare_project "$project" "$web" "$api" "$checkout"
  clone_at "$candidate" "$checkout"
  note "the published clone URL is not populated yet; the drill clones this repository at the candidate commit"
  (cd "$checkout" && scripts/setup.sh >/dev/null)
  [ -s "$checkout/.env" ] || fail "setup.sh did not create .env"
  [ -s "$checkout/secrets/bootstrap_token" ] || fail "setup.sh did not create the bootstrap token"
  ok "scripts/setup.sh created .env and secrets/ (mode $(stat -f '%OLp' "$checkout/secrets" 2>/dev/null || stat -c '%a' "$checkout/secrets"))"
  compose_in "$checkout" "$project" "$web" "$api" config --quiet
  compose_in "$checkout" "$project" "$web" "$api" up --build --detach --wait --quiet-pull
  ok "docker compose up --build --detach --wait finished"
  compose_in "$checkout" "$project" "$web" "$api" ps --format 'table {{.Service}}\t{{.Status}}' | sed 's/^/  /'
  acceptance "$checkout" "$web" install-smoke
  step "Versions used by the install drill"
  record_versions "$checkout" "$project" "$web" "$api"
  step "Restart without data loss (documented down/up)"
  compose_in "$checkout" "$project" "$web" "$api" down >/dev/null
  compose_in "$checkout" "$project" "$web" "$api" up --detach --wait --quiet-pull >/dev/null
  local case_id
  case_id="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['case_id'])" "$work_dir/state-install.json")"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${api}/api/health/ready")"
  [ "$code" = "200" ] || fail "readiness after restart returned HTTP $code"
  ok "the stack restarts healthy and keeps case ${case_id:0:8} in its volumes"
}

# -- upgrade ------------------------------------------------------------------------------------

drill_upgrade() {
  local checkout="$work_dir/upgrade" project="tracehollow-release-upgrade"
  local web=$((port_base + 10)) api=$((port_base + 5010))
  step "Upgrade from the previous recorded release state ($previous)"
  prepare_project "$project" "$web" "$api" "$checkout"
  clone_at "$previous" "$checkout"
  (cd "$checkout" && scripts/setup.sh >/dev/null)
  compose_in "$checkout" "$project" "$web" "$api" up --build --detach --wait --quiet-pull
  local before_head
  before_head="$(compose_in "$checkout" "$project" "$web" "$api" exec -T api sh -c 'alembic current 2>/dev/null | tail -1' | tr -d '\r')"
  ok "previous state running at migration ${before_head}"
  acceptance "$checkout" "$web" upgrade-seed

  step "Upgrading the same volumes to the candidate (${candidate:0:12})"
  git -C "$checkout" checkout --quiet "$candidate"
  compose_in "$checkout" "$project" "$web" "$api" up --build --detach --wait --quiet-pull
  compose_in "$checkout" "$project" "$web" "$api" logs --no-log-prefix migrate | grep -E "Running upgrade 000[5-8]" | sed 's/^/  /' || true
  local after_head
  after_head="$(compose_in "$checkout" "$project" "$web" "$api" exec -T api sh -c 'alembic current 2>/dev/null | tail -1' | tr -d '\r')"
  ok "candidate running at migration ${after_head}"
  acceptance "$checkout" "$web" upgrade-verify
  note "downgrade is not offered: the migrations are reversible in tests, but no downgrade path is verified for released data"
}

# -- backup and restore ---------------------------------------------------------------------------

drill_restore() {
  local source_checkout="$work_dir/restore-source" restored_checkout="$work_dir/restore-target"
  local source_project="tracehollow-release-source" restored_project="tracehollow-release-restored"
  local source_web=$((port_base + 20)) source_api=$((port_base + 5020))
  local restored_web=$((port_base + 25)) restored_api=$((port_base + 5025))
  step "Source installation with representative material"
  prepare_project "$source_project" "$source_web" "$source_api" "$source_checkout"
  prepare_project "$restored_project" "$restored_web" "$restored_api" "$restored_checkout"
  clone_at "$candidate" "$source_checkout"
  (cd "$source_checkout" && scripts/setup.sh >/dev/null)
  compose_in "$source_checkout" "$source_project" "$source_web" "$source_api" up --build --detach --wait --quiet-pull
  acceptance "$source_checkout" "$source_web" restore-seed

  step "Backup with writers stopped (the consistent pair documented in backup-restore.md)"
  compose_in "$source_checkout" "$source_project" "$source_web" "$source_api" stop web api worker ai-worker collector dispatcher >/dev/null
  ok "writers stopped: web, api, worker, ai-worker, collector, dispatcher"
  (cd "$source_checkout" && COMPOSE_PROJECT_NAME="$source_project" scripts/backup.sh "$work_dir/backup" | sed 's/^/  /')
  compose_in "$source_checkout" "$source_project" "$source_web" "$source_api" up --detach --wait --quiet-pull >/dev/null
  ls -la "$work_dir/backup" | sed 's/^/  /'
  grep -E "revision|created|sha256" "$work_dir/backup/manifest.txt" | head -5 | sed 's/^/  /'

  step "Restore onto a clean installation (documented new-machine procedure)"
  clone_at "$candidate" "$restored_checkout"
  cp -R "$source_checkout/secrets" "$restored_checkout/secrets"
  chmod 700 "$restored_checkout/secrets"
  ok "secrets/ copied from protected storage (step 2 of the documented procedure)"
  (cd "$restored_checkout" && scripts/setup.sh >/dev/null)
  compose_in "$restored_checkout" "$restored_project" "$restored_web" "$restored_api" up --build --detach --wait --quiet-pull
  (cd "$restored_checkout" && COMPOSE_PROJECT_NAME="$restored_project" scripts/restore.sh "$work_dir/backup" --verify-only | sed 's/^/  /')
  ok "verify-only drill passed on the clean installation"
  (cd "$restored_checkout" && COMPOSE_PROJECT_NAME="$restored_project" TRACEHOLLOW_WEB_PORT="$restored_web" TRACEHOLLOW_API_PORT="$restored_api" \
    TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture scripts/restore.sh "$work_dir/backup" --yes-overwrite-current-data | sed 's/^/  /')
  compose_in "$restored_checkout" "$restored_project" "$restored_web" "$restored_api" exec -T api python -m app.cli reconcile-evidence | sed 's/^/  /'
  acceptance "$restored_checkout" "$restored_web" restore-verify
  step "Deliveries and collection after the restore"
  local deliveries
  deliveries="$(compose_in "$restored_checkout" "$restored_project" "$restored_web" "$restored_api" logs --no-color collector | grep -c "notification_delivery_finished" || true)"
  [ "$deliveries" = "0" ] || fail "$deliveries notification deliveries ran after the restore"
  ok "no webhook delivery ran in the restored installation (the adapter is off by default)"
}

for stage in "${stages[@]}"; do
  case "$stage" in
    install) drill_install ;;
    upgrade) drill_upgrade ;;
    restore) drill_restore ;;
  esac
done

step "Release drills finished: ${stages[*]}"
