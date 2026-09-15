#!/usr/bin/env bash
# Verify or restore a backup created by scripts/backup.sh.
#
# Usage:
#   scripts/restore.sh <backup-dir> --verify-only
#       Non-destructive drill: checks checksums, restores the dump into a temporary database,
#       compares row counts with the backup, validates the evidence archive, then drops the
#       temporary database. The live database and volumes are not modified.
#
#   scripts/restore.sh <backup-dir> --yes-overwrite-current-data
#       Replaces the live application database and evidence volume contents with the backup.
#       Stops web, api, worker and dispatcher first and starts the stack again afterwards. Volumes
#       are never deleted. Take a fresh backup before doing this.
#
# Respects COMPOSE_PROJECT_NAME and other Docker Compose environment variables.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -eq 2 ] || usage
backup="$1"
mode="$2"
case "$mode" in
  --verify-only | --yes-overwrite-current-data) ;;
  *) usage ;;
esac

for file in database.dump evidence.tar row-counts.txt manifest.txt; do
  [ -f "$backup/$file" ] || { echo "error: $backup/$file is missing" >&2; exit 1; }
done

compose=(docker compose)
psql_admin() { "${compose[@]}" exec -T postgres psql --no-psqlrc -U postgres -v ON_ERROR_STOP=1 -At "$@"; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

echo "Checking checksums..."
for file in database.dump evidence.tar row-counts.txt; do
  expected="$(grep "^${file}.sha256=" "$backup/manifest.txt" | cut -d= -f2)"
  actual="$(sha256_of "$backup/$file")"
  if [ "$expected" != "$actual" ]; then
    echo "error: checksum mismatch for $file" >&2
    exit 1
  fi
  echo "  ok  $file"
done
tar -tf "$backup/evidence.tar" >/dev/null
echo "  ok  evidence.tar is a readable archive"

counts_for() {
  local database="$1" table
  for table in $(psql_admin -d "$database" -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"); do
    echo "${table}=$(psql_admin -d "$database" -c "SELECT count(*) FROM public.\"${table}\"")"
  done
}

if [ "$mode" = "--verify-only" ]; then
  scratch="tracehollow_restore_check_$(date -u +%Y%m%d%H%M%S)"
  cleanup() { psql_admin -d postgres -c "DROP DATABASE IF EXISTS \"${scratch}\" WITH (FORCE)" >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  echo "Restoring into temporary database ${scratch}..."
  psql_admin -d postgres -c "CREATE DATABASE \"${scratch}\" OWNER tracehollow_app" >/dev/null
  "${compose[@]}" exec -T postgres pg_restore -U postgres -d "$scratch" --no-owner \
    --role=tracehollow_app --exit-on-error <"$backup/database.dump"
  if diff <(counts_for "$scratch") "$backup/row-counts.txt"; then
    echo "  ok  restored row counts match the backup"
  else
    echo "error: restored row counts differ from the backup" >&2
    exit 1
  fi
  echo "Restore drill passed; the temporary database will be dropped."
  exit 0
fi

echo "Stopping web, api, worker and dispatcher..."
"${compose[@]}" stop web api worker dispatcher

echo "Restoring database..."
"${compose[@]}" exec -T postgres pg_restore -U postgres -d tracehollow --clean --if-exists \
  --no-owner --role=tracehollow_app --single-transaction --exit-on-error <"$backup/database.dump"

echo "Restoring evidence volume contents..."
"${compose[@]}" run --rm --no-deps -T api sh -c \
  'find /data/evidence -mindepth 1 -delete && tar -C /data/evidence --no-same-owner -xf -' \
  <"$backup/evidence.tar"

if diff <(counts_for tracehollow) "$backup/row-counts.txt"; then
  echo "  ok  live row counts match the backup"
else
  echo "error: live row counts differ from the backup after restore" >&2
  exit 1
fi

echo "Starting the stack..."
"${compose[@]}" up --detach --wait
echo "Restore complete."
