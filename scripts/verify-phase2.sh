#!/usr/bin/env bash
# Reproducible Phase 2 stack verification against an isolated Docker Compose project.
#
# Adds a controlled fixture-source container (compose.verify-sources.yaml) on the collector's
# egress network; no real public source is contacted. Covers:
#   - migration 0004, the collector service with pinned engines, network isolation
#   - public web page: provenance, redirects, derived text, 404, 403, truncation
#   - SSRF: metadata, loopback, internal service names, numeric hosts, blocked redirect hop
#   - RSS pagination with deduplication, broken pagination (partial), malformed feed
#   - GitHub API shape: pagination, quota, verified not found, rate limit, rejected token
#   - username discovery: candidate accounts, 429 and blocked checks never read as absence
#   - Subfinder network sandbox (ADR 0007): the runner has no route, name or host address beyond
#     its internal network; direct connections to the fixture, metadata, data services and the
#     internet fail; the egress gateway refuses non-provider names, IP literals, other ports,
#     providers resolving to metadata or loopback and untrusted certificates; the real binary
#     returns results only through the gateway; the runner refuses to work outside the sandbox;
#     a stopped runner makes lookups fail visibly
#   - Subfinder: key-only source without a key is authentication_required
#   - cancellation during a request, per-connector concurrency slot, non-member access
#   - collected text indexed and cited by the AI assistant (synthetic AI provider)
#   - credentials, secrets and tokens absent from service logs
#
# Usage: scripts/verify-phase2.sh [--keep] [--e2e]
# Environment: TRACEHOLLOW_VERIFY_PROJECT (default tracehollow-verify2), TRACEHOLLOW_VERIFY_WEB_PORT
# (3100) and TRACEHOLLOW_VERIFY_API_PORT (8100). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase2.sh [--keep] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify2}"
export COMPOSE_FILE="compose.yaml:compose.verify-sources.yaml"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3100}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8100}"
export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
web_url="http://localhost:${TRACEHOLLOW_WEB_PORT}"
timeout=150

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

TRACEHOLLOW_ACCEPTANCE_PASSWORD="$(openssl rand -hex 24)"
TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD="$(openssl rand -hex 24)"
export TRACEHOLLOW_ACCEPTANCE_PASSWORD TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD
work_dir="$(mktemp -d)"
state="$work_dir/state.json"
acceptance=(python3 scripts/phase2_acceptance.py --state "$state" --web-url "$web_url" --timeout "$timeout")
started=0

tls_dir="$work_dir/verify-tls"
mkdir -p "$tls_dir"
chmod 755 "$work_dir" "$tls_dir"
export TRACEHOLLOW_VERIFY_TLS_DIR="$tls_dir"
probe_network=""

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

finish() {
  status=$?
  rm -rf "$work_dir"
  if [ -n "$probe_network" ]; then docker network rm "$probe_network" >/dev/null 2>&1 || true; fi
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "Verification failed; recent service logs:" >&2
      docker compose logs --tail=80 api collector dispatcher fixture-site migrate discovery-runner discovery-gateway >&2 || true
    fi
    if [ "$keep" = 1 ]; then
      echo "Keeping project ${COMPOSE_PROJECT_NAME} (stop with: COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} COMPOSE_FILE=${COMPOSE_FILE} docker compose down)"
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
networks_of() {
  docker inspect "$(docker compose ps -q "$1")" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}'
}

step "Configuration and startup"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose build --quiet
# Verification-only CA and a certificate for the crt.sh stand-in; trusted only by this
# project's egress gateway (SSL_CERT_FILE in compose.verify-sources.yaml).
docker run --rm -i --network none --user "$(id -u):$(id -g)" -v "$tls_dir:/out" tracehollow-api:local python - <<'PY'
import datetime
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

now = datetime.datetime.now(datetime.UTC)
def name(value):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, value)])
ca_key = ec.generate_private_key(ec.SECP256R1())
ca = (x509.CertificateBuilder().subject_name(name("Tracehollow verification CA")).issuer_name(name("Tracehollow verification CA"))
      .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(now - datetime.timedelta(hours=1)).not_valid_after(now + datetime.timedelta(days=1))
      .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
      .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
      .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
      .sign(ca_key, hashes.SHA256()))
key = ec.generate_private_key(ec.SECP256R1())
leaf = (x509.CertificateBuilder().subject_name(name("crt.sh")).issuer_name(ca.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1)).not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("crt.sh")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256()))
out = Path("/out")
(out / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
(out / "crtsh.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
(out / "crtsh.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
for path in out.iterdir():
    path.chmod(0o644)
PY
docker compose up --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
docker compose logs --no-log-prefix migrate | grep "Running upgrade 0003 -> 0004" >/dev/null || fail "migration 0004 was not applied"
ok "migrate applied revision 0004"
[ "$(docker compose exec -T collector id -u)" = 10001 ] || fail "collector does not run as the unprivileged user"
docker compose exec -T collector python -c "import importlib.metadata as m; assert m.version('sherlock-project') == '0.16.2'" || fail "collector lacks the pinned Sherlock"
if docker compose exec -T collector sh -c 'command -v subfinder || test -e /usr/local/bin/subfinder' >/dev/null 2>&1; then fail "the collector contains Subfinder; it must run only in the sandbox"; fi
ok "collector runs as UID 10001 with sherlock-project 0.16.2 and without Subfinder"
[ "$(docker compose exec -T discovery-runner id -u)" = 10001 ] || fail "discovery-runner does not run as the unprivileged user"
docker compose exec -T -e HOME=/tmp discovery-runner subfinder -version 2>&1 | grep "Current Version: v2.16.0" >/dev/null || fail "discovery-runner lacks the pinned Subfinder"
if docker compose exec -T discovery-runner sh -c 'test -e /run/secrets || python -c "import sqlalchemy"' >/dev/null 2>&1; then fail "discovery-runner has secrets or application dependencies"; fi
ok "discovery-runner runs as UID 10001 with Subfinder v2.16.0, no secrets and no database or broker client"
if docker compose exec -T api python -c "import sherlock_project" >/dev/null 2>&1; then fail "the api image contains collection engines"; fi
ok "api image does not contain the collection engines"

step "Network isolation"
for service in api worker dispatcher ai-worker web postgres redis discovery-runner; do
  case "$(networks_of "$service")" in *collect-egress*) fail "$service is attached to the collection egress network" ;; esac
done
for service in api worker dispatcher ai-worker web postgres redis fixture-site; do
  case "$(networks_of "$service")" in *_discovery*) fail "$service is attached to the discovery sandbox network" ;; esac
done
case "$(networks_of collector)" in *collect-egress*) ;; *) fail "collector is not on the collection egress network" ;; esac
[ "$(networks_of discovery-runner)" = "${COMPOSE_PROJECT_NAME}_discovery " ] || fail "discovery-runner must be attached only to the discovery network: $(networks_of discovery-runner)"
case "$(networks_of discovery-gateway)" in *_data*) fail "discovery-gateway must not reach the data network" ;; esac
case "$(networks_of fixture-site)" in *_data*) fail "fixture-site must not reach the data network" ;; esac
if docker inspect "$(docker compose ps -q collector)" --format '{{json .NetworkSettings.Ports}}' | grep HostPort >/dev/null; then
  fail "collector publishes a host port"
fi
ok "only collector and discovery-gateway have collection egress; no ports published; the fixture site cannot reach data services"
docker network inspect "${COMPOSE_PROJECT_NAME}_discovery" --format '{{json .}}' | python3 -c '
import json, sys
network = json.load(sys.stdin)
gateways = [c.get("Gateway") for c in network["IPAM"]["Config"] if c.get("Gateway")]
assert network["Internal"] is True, "not internal"
assert network["Options"].get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated", network["Options"]
assert not gateways, f"host gateway address {gateways}"
members = sorted(c["Name"].split("-")[-2] for c in network["Containers"].values())
assert members == ["collector", "discovery-gateway", "discovery-runner"] or members == ["collector", "gateway", "runner"], members
' || fail "discovery network must be internal, gateway-isolated, without a host gateway address and with only the three sandbox services"
ok "discovery network is internal with isolated gateway mode (no host address on the bridge)"

step "Subfinder network sandbox boundary (probes inside discovery-runner)"
docker compose exec -T discovery-runner python -c "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=15)))" | grep "'sandbox_problems': \[\]" >/dev/null || fail "discovery-runner reports sandbox problems"
ok "runner self-check: no default route, one interface, no host answer, gateway healthy"
docker compose exec -T discovery-runner python - <scripts/fixtures/sandbox/probe.py >"$work_dir/probe.json" || fail "sandbox probe did not run"
python3 - "$work_dir/probe.json" <<'PY' || fail "the sandbox boundary is not as required"
import json, sys
result = json.load(open(sys.argv[1]))
problems = []
if result["default_route"] or len([i for i in result["interfaces"] if i != "lo"]) != 1:
    problems.append(f"routes/interfaces: {result['default_route']} {result['interfaces']}")
for target, outcome in result["direct"].items():
    if outcome in ("connected", "refused"):
        problems.append(f"direct {target} answered ({outcome})")
    else:
        print(f"  ok  direct {target}: {outcome}")
for address, ports in result["bridge_first_address"].items():
    if "held_by_sandbox_container" in ports:
        print(f"  ok  first network address {address} is the {ports['held_by_sandbox_container']} container, not the host")
        continue
    answered = {port: outcome for port, outcome in ports.items() if outcome in ("connected", "refused")}
    if answered:
        problems.append(f"host bridge address {address} answered {answered}")
    else:
        print(f"  ok  first network address {address} does not answer: {ports}")
expected = {
    "fixture-site:8080": "403 Tracehollow egress refused host_not_allowed",
    "example.com:443": "403 Tracehollow egress refused host_not_allowed",
    "certificatedetails.com:443": "403 Tracehollow egress refused blocked_address",
    "anubisdb.com:443": "403 Tracehollow egress refused blocked_address",
    "crt.sh:8443": "403 Tracehollow egress refused blocked_port",
    "172.31.250.10:443": "403 Tracehollow egress refused ip_literal_not_allowed",
    "rapiddns.io:443": "502 Tracehollow egress failed upstream_certificate_invalid",
    "crt.sh:443": "200 Connection established",
}
for target, want in expected.items():
    got = result["gateway"][target]
    if got != f"HTTP/1.1 {want}":
        problems.append(f"gateway {target}: {got!r}, expected {want!r}")
    else:
        print(f"  ok  gateway {target}: {want}")
if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
PY
ok "no direct route or name to the fixture, metadata, data services, internet or host; the gateway admits only a verified allowlisted provider"

probe_network="${COMPOSE_PROJECT_NAME}_probe_internal"
docker network create --internal "$probe_network" >/dev/null
for network in "${COMPOSE_PROJECT_NAME}_collect-egress" "$probe_network"; do
  refusal="$(docker run --rm --network "$network" --read-only --cap-drop ALL tracehollow-discovery-runner:local python -c "
from pathlib import Path
from app.connectors.engines.subfinder_runner import RunnerConfig, sandbox_problems
print('; '.join(sandbox_problems(RunnerConfig(subfinder=Path('/usr/local/bin/subfinder'), gateway='discovery-gateway:3128'))))")"
  case "$network:$refusal" in
    *_collect-egress:*"default route"*) ok "runner outside the sandbox (egress network) refuses: $refusal" ;;
    *_probe_internal:*"Docker host answers"*) ok "runner on an internal network without gateway isolation refuses: $refusal" ;;
    *) fail "runner on $network did not refuse as expected: $refusal" ;;
  esac
done
docker network rm "$probe_network" >/dev/null
probe_network=""

step "Seed and source capabilities"
"${acceptance[@]}" seed

step "Public web page collection"
"${acceptance[@]}" web

step "SSRF protections in the running collector"
"${acceptance[@]}" ssrf

step "RSS/Atom feeds"
"${acceptance[@]}" rss

step "GitHub API connector"
"${acceptance[@]}" github

step "Username discovery (Sherlock)"
"${acceptance[@]}" username

step "Passive domain discovery (Subfinder) through the sandbox"
"${acceptance[@]}" domain
gateway_address="$(docker inspect "$(docker compose ps -q discovery-gateway)" --format "{{(index .NetworkSettings.Networks \"${COMPOSE_PROJECT_NAME}_collect-egress\").IPAddress}}")"
docker compose exec -T fixture-site python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/sandbox/crtsh-requests', timeout=5).read().decode())" >"$work_dir/crtsh.json"
python3 - "$work_dir/crtsh.json" "$gateway_address" <<'PY' || fail "provider requests did not all come from the egress gateway"
import json, sys
requests, gateway = json.load(open(sys.argv[1])), sys.argv[2]
clients = {item["client"] for item in requests}
domains = sorted({item["domain"] for item in requests})
assert requests and clients == {gateway}, (clients, gateway)
assert domains == ["empty-lab.example", "sandbox-lab.example"], domains
print(f"  ok  {len(requests)} crt.sh API request(s) for {domains}, all from the gateway address {gateway}")
PY
ok "the provider saw only the egress gateway, never the sandbox"

step "Stopped discovery runner: lookups fail visibly"
docker compose stop discovery-runner >/dev/null
"${acceptance[@]}" domain-down
docker compose start discovery-runner >/dev/null
docker compose up --detach --wait discovery-runner >/dev/null

step "Credentials"
"${acceptance[@]}" credentials

step "Cancellation during a request"
"${acceptance[@]}" cancel

step "Per-connector concurrency slot"
"${acceptance[@]}" concurrency

step "Collected evidence in the AI workspace"
"${acceptance[@]}" ai

step "Non-member access"
printf '%s\n' "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" | docker compose exec -T api python -c '
import sys
from app.auth.models import User
from app.auth.security import hash_password, normalize_username
from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory

password = sys.stdin.readline().strip()
factory = create_session_factory(create_db_engine(get_settings()))
with factory() as db:
    db.add(User(username="acceptance-outsider", username_normalized=normalize_username("acceptance-outsider"), password_hash=hash_password(password), is_admin=False))
    db.commit()
'
"${acceptance[@]}" outsider

if [ "$e2e" = 1 ]; then
  step "Browser workflow: sources and a collected page (Playwright)"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "${pnpm[@]}" exec playwright test e2e/phase2-sources.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +1 passed" "$work_dir/e2e.log" >/dev/null || fail "the sources browser workflow did not pass"
  if grep -E "^ +[0-9]+ (failed|skipped)" "$work_dir/e2e.log" >/dev/null; then fail "a browser test failed or was skipped"; fi
  ok "browser sources workflow passed"
fi

step "Where collection ran and what was stored"
claimed_by_worker="$(docker compose logs --no-color worker | grep -c '"query_run_claimed"' || true)"
[ "$claimed_by_worker" = 0 ] || fail "the internal worker executed $claimed_by_worker collection run(s)"
claimed_by_collector="$(docker compose logs --no-color collector | grep -c '"query_run_claimed"' || true)"
[ "$claimed_by_collector" -ge 20 ] || fail "collector claimed only $claimed_by_collector runs"
ok "collector executed $claimed_by_collector collection runs; the internal worker executed none"
blocked="$(sql "SELECT count(*) FROM connector_runs WHERE last_error_code IN ('blocked_address','blocked_host','blocked_port')")"
blocked_evidence="$(sql "SELECT count(*) FROM evidence_objects e JOIN connector_runs c ON c.id = e.connector_run_id WHERE c.last_error_code IN ('blocked_address','blocked_host','blocked_port')")"
[ "$blocked" -ge 9 ] && [ "$blocked_evidence" = 0 ] || fail "blocked runs: $blocked, evidence from blocked runs: $blocked_evidence"
ok "$blocked refused destinations, no evidence stored for any of them"
no_findings="$(sql "SELECT count(*) FROM connector_runs WHERE outcome = 'no_findings'")"
[ "$no_findings" = 5 ] || fail "expected exactly 5 verified no-findings results, found $no_findings"
ok "exactly five runs reported no findings (404 page, API 404, two verified absences, one verified empty passive lookup); no failure did"
provenance="$(sql "SELECT count(*) FROM evidence_objects WHERE acquisition_method = 'connector_collection' AND (collection_mode IS NULL OR access_category IS NULL OR source_reference IS NULL)")"
[ "$provenance" = 0 ] || fail "$provenance collected evidence record(s) lack provenance"
ok "every collected evidence record has collection mode, access category and source reference"
[ "$(sql "SELECT count(*) FROM integration_credentials")" = 0 ] || fail "the removed credential is still stored"
ok "removed credential deleted from the database"

step "Secrets, tokens and credentials do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
for secret in postgres_superuser_password postgres_app_password redis_password app_secret_key bootstrap_token credential_encryption_key; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then fail "the value of secrets/$secret appears in service logs"; fi
done
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" "ghp_synthetic_rejected_token_for_verification_0001"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then fail "a password or token appears in service logs"; fi
done
if grep -F "Alsancak ofisinde" "$work_dir/service.log" >/dev/null; then fail "collected page content appears in service logs"; fi
docker compose logs --no-color discovery-gateway discovery-runner >"$work_dir/sandbox.log" 2>&1
if grep -E "sandbox-lab\.example|empty-lab\.example" "$work_dir/sandbox.log" >/dev/null; then fail "an investigated domain appears in sandbox or gateway logs"; fi
grep '"egress_tunnel"' "$work_dir/sandbox.log" >/dev/null || fail "the gateway did not log its tunnel decisions"
ok "no secret values, passwords, tokens or collected content in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 2 stack checks passed"
