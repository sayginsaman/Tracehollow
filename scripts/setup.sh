#!/usr/bin/env bash
# Prepare a local Tracehollow installation. Safe to run repeatedly.
#
#   * Creates .env from .env.example if .env does not exist.
#   * Generates any missing secret in secrets/ using the operating system's CSPRNG.
#   * Never overwrites or rotates an existing secret, .env file, database or volume.
#
# Usage: scripts/setup.sh
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

created=()
kept=()

random_hex() {
  local bytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$bytes"
  elif [ -r /dev/urandom ]; then
    od -An -tx1 -N"$bytes" /dev/urandom | tr -d ' \n'
    echo
  else
    echo "error: neither openssl nor /dev/urandom is available" >&2
    exit 1
  fi
}

sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    openssl dgst -sha256 -r | awk '{print $1}'
  fi
}

# Atomically write stdin to a secret file readable by the service containers.
# The secrets/ directory itself is private to the current user (0700).
write_secret_file() {
  local path="$1" tmp
  tmp="$(mktemp "secrets/.tmp.XXXXXX")"
  cat >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$path"
}

ensure_secret() {
  local name="$1" bytes="$2" path="secrets/$1"
  if [ -s "$path" ]; then
    kept+=("$path")
    return
  fi
  random_hex "$bytes" | write_secret_file "$path"
  created+=("$path")
}

if [ -f .env ]; then
  kept+=(".env")
else
  cp .env.example .env
  chmod 0600 .env
  created+=(".env")
fi

mkdir -p secrets
chmod 0700 secrets

ensure_secret postgres_superuser_password 32
ensure_secret postgres_app_password 32
ensure_secret redis_password 32
ensure_secret app_secret_key 32
ensure_secret bootstrap_token 24
ensure_secret credential_encryption_key 32

# Optional cloud AI provider key: created empty (not configured). Paste a key into it only if you
# enable a cloud provider; it is never generated and never overwritten.
if [ -f secrets/cloud_ai_api_key ]; then
  kept+=("secrets/cloud_ai_api_key")
else
  : | write_secret_file secrets/cloud_ai_api_key
  created+=("secrets/cloud_ai_api_key (empty: no cloud AI provider)")
fi

# Derived file: Redis ACL with a SHA-256 digest of the password (never the plaintext).
redis_digest="$(tr -d '\n' <secrets/redis_password | sha256_hex)"
acl_line="user default on #${redis_digest} ~* &* +@all"
if [ ! -f secrets/redis_users.acl ] || [ "$(cat secrets/redis_users.acl)" != "$acl_line" ]; then
  printf '%s\n' "$acl_line" | write_secret_file secrets/redis_users.acl
  created+=("secrets/redis_users.acl")
else
  kept+=("secrets/redis_users.acl")
fi

echo "Tracehollow local setup"
for item in "${created[@]+"${created[@]}"}"; do echo "  created  $item"; done
for item in "${kept[@]+"${kept[@]}"}"; do echo "  kept     $item"; done
cat <<'EOF'

Next steps:
  1. Review .env (ports, bind addresses). Defaults bind to 127.0.0.1 only.
  2. docker compose up --build --detach --wait
  3. Open http://localhost:3000 and complete setup with the token shown by:
       cat secrets/bootstrap_token

Keep secrets/ private and back it up separately from database backups.
EOF
