#!/usr/bin/env bash
# Run the versioned AI evaluation set against a disposable database.
#
# Usage: scripts/ai-eval.sh [--providers configured|fixture] [--output DIR] [--only q01,q13]
#
#   --providers configured  use the local Ollama models from the environment (default)
#   --providers fixture     use the deterministic synthetic provider (no model needed)
#   --output DIR            where results.json, worksheet.csv and summary.md are written
#                           (default: evaluation-output/<UTC timestamp>, git-ignored)
#
# Environment (configured providers):
#   TRACEHOLLOW_AI_OLLAMA_BASE_URL    default http://127.0.0.1:11434 (Ollama on this host)
#   TRACEHOLLOW_AI_GENERATION_MODEL   default qwen3:8b
#   TRACEHOLLOW_AI_EMBEDDING_MODEL    default qwen3-embedding:0.6b
#
# The evaluation writes only synthetic data into an ephemeral PostgreSQL started from
# compose.test.yaml, then removes it. It never contacts a cloud provider: any cloud provider is
# replaced by a recording transport and the report states how many requests it received (0).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
providers=configured
output=""
only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --providers) providers="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --only) only="$2"; shift 2 ;;
    *) echo "usage: scripts/ai-eval.sh [--providers configured|fixture] [--output DIR]" >&2; exit 2 ;;
  esac
done
output="${output:-$root/evaluation-output/$(date -u +%Y%m%dT%H%M%SZ)-$providers}"
compose=(docker compose --file "$root/compose.test.yaml" --project-name tracehollow-ai-eval)

random_hex() { openssl rand -hex "$1" 2>/dev/null || od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'; }
TEST_POSTGRES_PASSWORD="$(random_hex 24)"
TEST_REDIS_PASSWORD="$(random_hex 24)"
export TEST_POSTGRES_PASSWORD TEST_REDIS_PASSWORD
storage="$(mktemp -d)"

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$storage"
}
trap cleanup EXIT

echo "Starting a disposable PostgreSQL and Redis..."
"${compose[@]}" up --detach --wait --quiet-pull postgres redis >/dev/null
pg_port="$("${compose[@]}" port postgres 5432 | awk -F: '{print $NF}')"
redis_port="$("${compose[@]}" port redis 6379 | awk -F: '{print $NF}')"

export TRACEHOLLOW_ENV=test
export TRACEHOLLOW_DATABASE_HOST=127.0.0.1 TRACEHOLLOW_DATABASE_PORT="$pg_port"
export TRACEHOLLOW_DATABASE_USER=tracehollow_test TRACEHOLLOW_DATABASE_NAME=tracehollow_test
export TRACEHOLLOW_DATABASE_PASSWORD="$TEST_POSTGRES_PASSWORD"
export TRACEHOLLOW_REDIS_HOST=127.0.0.1 TRACEHOLLOW_REDIS_PORT="$redis_port" TRACEHOLLOW_REDIS_PASSWORD="$TEST_REDIS_PASSWORD"
TRACEHOLLOW_SECRET_KEY="$(random_hex 32)"
export TRACEHOLLOW_SECRET_KEY
export TRACEHOLLOW_EVIDENCE_STORAGE_PATH="$storage"
export TRACEHOLLOW_FIXTURE_PAGE_DELAY_SECONDS=0 TRACEHOLLOW_FIXTURE_RETRY_BACKOFF_SECONDS=0
export TRACEHOLLOW_AI_OLLAMA_BASE_URL="${TRACEHOLLOW_AI_OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export TRACEHOLLOW_AI_GENERATION_MODEL="${TRACEHOLLOW_AI_GENERATION_MODEL:-qwen3:8b}"
export TRACEHOLLOW_AI_EMBEDDING_MODEL="${TRACEHOLLOW_AI_EMBEDDING_MODEL:-qwen3-embedding:0.6b}"
if [ "$providers" = fixture ]; then
  export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
fi

cd "$root/services/api"
echo "Migrating the evaluation database..."
uv run --quiet alembic upgrade head >/dev/null
echo "Running the evaluation with $providers providers (this can take a while with a local model)..."
uv run --quiet python -m app.ai.evaluation --providers "$providers" --output "$output" --only "$only"
echo "Results written to $output"
