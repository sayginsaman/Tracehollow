#!/usr/bin/env bash
# Run the backend test suite against ephemeral PostgreSQL and Redis containers.
#
# Usage: scripts/test-backend.sh [pytest arguments...]
# Requires: Docker with Compose v2, uv.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(docker compose --file "$root/compose.test.yaml")

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'
  fi
}

TEST_POSTGRES_PASSWORD="$(random_hex 24)"
TEST_REDIS_PASSWORD="$(random_hex 24)"
export TEST_POSTGRES_PASSWORD TEST_REDIS_PASSWORD

cleanup() {
  # Only the dedicated, tmpfs-backed test project is removed.
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting ephemeral test services..."
"${compose[@]}" up --detach --wait --quiet-pull

pg_port="$("${compose[@]}" port postgres 5432 | awk -F: '{print $NF}')"
redis_port="$("${compose[@]}" port redis 6379 | awk -F: '{print $NF}')"

export TRACEHOLLOW_TEST_POSTGRES_HOST=127.0.0.1
export TRACEHOLLOW_TEST_POSTGRES_PORT="$pg_port"
export TRACEHOLLOW_TEST_POSTGRES_USER=tracehollow_test
export TRACEHOLLOW_TEST_POSTGRES_PASSWORD="$TEST_POSTGRES_PASSWORD"
export TRACEHOLLOW_TEST_REDIS_HOST=127.0.0.1
export TRACEHOLLOW_TEST_REDIS_PORT="$redis_port"
export TRACEHOLLOW_TEST_REDIS_PASSWORD="$TEST_REDIS_PASSWORD"
export TRACEHOLLOW_REQUIRE_INTEGRATION=1

cd "$root/services/api"
uv run pytest "$@"
