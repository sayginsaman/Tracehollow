#!/usr/bin/env bash
# Back up the PostgreSQL database and the evidence volume of a running Tracehollow stack.
#
# Usage: scripts/backup.sh [destination-directory]
#   Default destination: backups/<UTC timestamp>/
#
# Produces:
#   database.dump     pg_dump custom-format archive of the application database
#   evidence.tar      tar archive of the evidence volume contents
#   row-counts.txt    exact row count per table at backup time (used by restore --verify-only)
#   manifest.txt      creation time, schema revision and SHA-256 checksums
#
# Secrets in secrets/ are deliberately NOT included. Back them up separately to protected
# storage; without secrets/app_secret_key and the database passwords a restored stack
# cannot start with the same configuration.
#
# Respects COMPOSE_PROJECT_NAME and other Docker Compose environment variables.
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

dest="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"
if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  echo "error: destination $dest already exists and is not empty" >&2
  exit 1
fi
mkdir -p "$dest"

compose=(docker compose)
psql_live=("${compose[@]}" exec -T postgres psql --no-psqlrc -U postgres -d tracehollow -v ON_ERROR_STOP=1 -At)

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

if ! "${compose[@]}" exec -T postgres pg_isready -U postgres -d tracehollow >/dev/null; then
  echo "error: the postgres service is not running; start the stack first" >&2
  exit 1
fi

echo "Dumping database..."
"${compose[@]}" exec -T postgres pg_dump -U postgres -d tracehollow --format=custom --no-owner \
  >"$dest/database.dump"

echo "Recording row counts..."
: >"$dest/row-counts.txt"
for table in $("${psql_live[@]}" -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"); do
  count="$("${psql_live[@]}" -c "SELECT count(*) FROM public.\"${table}\"")"
  echo "${table}=${count}" >>"$dest/row-counts.txt"
done

echo "Archiving evidence volume..."
if "${compose[@]}" ps --status running --services | grep -qx api; then
  "${compose[@]}" exec -T api tar -C /data/evidence -cf - . >"$dest/evidence.tar"
else
  "${compose[@]}" run --rm --no-deps -T api tar -C /data/evidence -cf - . >"$dest/evidence.tar"
fi

revision="$("${psql_live[@]}" -c "SELECT string_agg(version_num, ',') FROM alembic_version")"
{
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "compose_project=${COMPOSE_PROJECT_NAME:-tracehollow}"
  echo "alembic_revision=${revision}"
  echo "database.dump.sha256=$(sha256_of "$dest/database.dump")"
  echo "evidence.tar.sha256=$(sha256_of "$dest/evidence.tar")"
  echo "row-counts.txt.sha256=$(sha256_of "$dest/row-counts.txt")"
} >"$dest/manifest.txt"

echo "Backup written to $dest"
cat "$dest/manifest.txt"
