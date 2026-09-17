#!/usr/bin/env bash
# Reproducible Phase 4 stack verification against an isolated Docker Compose project.
#
# Adds the controlled fixture-source container (compose.verify-phase4.yaml); no real platform is
# contacted. Covers:
#   - migration 0005; imports processed by the worker, which has no internet route
#   - PDF child-process limits enforced in the worker container; OCR engine present only in an
#     image built with --ocr
#   - WhatsApp ZIP export: date-order question, answer, messages citing lines, hostile archive
#     entries skipped, inert attachments, timeline times with their basis
#   - PDFs: extracted text with page map, encrypted and malformed states, OCR unavailable (or real
#     OCR with --ocr), extracted text reaching case-scoped retrieval (synthetic AI provider)
#   - social connectors: missing credentials block runs; Instagram official API, undiscoverable
#     account, rejected token, disabled and enabled unofficial profile page, login wall; Telegram
#     preview with pagination, private channel, Bot API with the token redacted; YouTube uploads,
#     disabled comments, exhausted quota
#   - comparison and timeline; HTML report escaping, redaction and offline citations
#   - non-member access; case deletion of originals, derived records and files
#   - tokens, keys and imported content absent from service logs
#
# Usage: scripts/verify-phase4.sh [--keep] [--ocr] [--e2e]
# Environment: TRACEHOLLOW_VERIFY_PROJECT (default tracehollow-verify4), TRACEHOLLOW_VERIFY_WEB_PORT
# (3140) and TRACEHOLLOW_VERIFY_API_PORT (8140). The project must not already have volumes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

keep=0
ocr=0
e2e=0
for argument in "$@"; do
  case "$argument" in
    --keep) keep=1 ;;
    --ocr) ocr=1 ;;
    --e2e) e2e=1 ;;
    *) echo "usage: scripts/verify-phase4.sh [--keep] [--ocr] [--e2e]" >&2; exit 2 ;;
  esac
done

export COMPOSE_PROJECT_NAME="${TRACEHOLLOW_VERIFY_PROJECT:-tracehollow-verify4}"
export COMPOSE_FILE="compose.yaml:compose.verify-phase4.yaml"
export TRACEHOLLOW_WEB_PORT="${TRACEHOLLOW_VERIFY_WEB_PORT:-3140}"
export TRACEHOLLOW_API_PORT="${TRACEHOLLOW_VERIFY_API_PORT:-8140}"
export TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture
export TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=false
if [ "$ocr" = 1 ]; then export TRACEHOLLOW_INSTALL_OCR=true; else export TRACEHOLLOW_INSTALL_OCR=false; fi
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
ocr_flag=()
if [ "$ocr" = 1 ]; then ocr_flag=(--ocr); fi
acceptance=(python3 scripts/phase4_acceptance.py --state "$state" --web-url "$web_url" --timeout "$timeout" --fixtures "$work_dir/pdfs.json" ${ocr_flag[@]+"${ocr_flag[@]}"})
started=0

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

finish() {
  status=$?
  rm -rf "$work_dir"
  if [ "$started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "Verification failed; recent service logs:" >&2
      docker compose logs --tail=80 api worker collector ai-worker dispatcher fixture-site migrate >&2 || true
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

networks_of() {
  docker inspect "$(docker compose ps -q "$1")" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}'
}

step "Configuration, build and startup (OCR engine: $TRACEHOLLOW_INSTALL_OCR)"
scripts/setup.sh >/dev/null
docker compose config --quiet
started=1
docker compose build --quiet
docker compose up --detach --wait --quiet-pull
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
docker compose logs --no-log-prefix migrate | grep "Running upgrade 0004 -> 0005" >/dev/null || fail "migration 0005 was not applied"
ok "migrate applied revision 0005"

step "Synthetic PDF fixtures (generated offline in the API image)"
docker run --rm -i --network none tracehollow-api:local python - >"$work_dir/pdfs.json" <<'PY'
import base64, io, json, zlib
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

def assemble(objects):
    out = io.BytesIO(); out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"); offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell()); out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell(); out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets: out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()

def stream(data, extra=""):
    return f"<< /Length {len(data)}{extra} >>\nstream\n".encode() + data + b"\nendstream"

def pdf(pages):
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]; kids = []
    for page in pages:
        number = len(objects) + 1; kids.append(f"{number} 0 R"); resources = "/Font << /F1 3 0 R >>"; commands = []
        for index, line in enumerate(page.get("text", [])):
            commands.append(f"BT /F1 24 Tf 72 {700 - index * 40} Td ({line}) Tj ET")
        image = page.get("image")
        if image:
            resources += f" /XObject << /Im1 {number + 2} 0 R >>"; commands.append("q 612 0 0 792 0 0 cm /Im1 Do Q")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << {resources} >> /Contents {number + 1} 0 R >>".encode())
        objects.append(stream("\n".join(commands).encode()))
        if image:
            width, height, pixels = image; data = zlib.compress(pixels)
            objects.append(stream(data, f" /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    return assemble(objects)

text = pdf([{"text": ["Registry summary for ornek.example", "Registrant Synthetic Holding"]}, {"text": ["Hosting moved to 203.0.113.9 in March"]}])
writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf([{"text": ["Protected synthetic page"]}]))))
writer.encrypt("verification-only", algorithm="AES-256"); encrypted = io.BytesIO(); writer.write(encrypted)
source = pdfium.PdfDocument(pdf([{"text": ["SYNTHETIC SCAN 2026", "TRACEHOLLOW OCR CHECK"]}]))
bitmap = source[0].render(scale=200 / 72, grayscale=True)
w, h, stride = bitmap.width, bitmap.height, bitmap.stride; buffer = bytes(bitmap.buffer)
scanned = pdf([{"image": (w, h, b"".join(buffer[y * stride : y * stride + w] for y in range(h)))}])
malformed = b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >>\nnot a pdf body\n%%EOF"
print(json.dumps({name: base64.b64encode(value).decode() for name, value in {"text": text, "encrypted": encrypted.getvalue(), "scanned": scanned, "malformed": malformed}.items()}))
PY
ok "text, encrypted, scanned and malformed PDFs generated"

step "Worker isolation and document-processing limits"
for service in worker api dispatcher; do
  case "$(networks_of "$service")" in *collect-egress*|*ai-egress*) fail "$service has an egress network" ;; esac
done
if docker compose exec -T worker python -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)" >/dev/null 2>&1; then
  fail "the worker reached the internet"
fi
ok "the worker, which parses imports, has no egress network and cannot open an internet connection"
docker compose exec -T worker python - <<'PY' || fail "PDF child-process limits are not enforced"
import sys
from app.imports.documents import ChildFailedError, run_child
try:
    run_child([], b"", timeout=30, memory_mb=256, executable=[sys.executable, "-c", "bytearray(2 * 1024 ** 3)"])
except ChildFailedError as exc:
    print(f"  ok  a 2 GiB allocation in a document child process is stopped ({exc.code})")
else:
    raise SystemExit("allocation succeeded")
try:
    run_child([], b"", timeout=2, memory_mb=256, executable=[sys.executable, "-c", "import time; time.sleep(30)"])
except ChildFailedError as exc:
    assert exc.code == "timeout", exc.code
    print("  ok  a stalled document child process is stopped at its time limit")
PY
if [ "$ocr" = 1 ]; then
  docker compose exec -T worker python -c "from app.config import get_settings; from app.imports.documents import ocr_availability; a = ocr_availability(get_settings()); assert a.available, a; print(f'  ok  Tesseract {a.engine_version} with {\"+\".join(a.languages)}')"
else
  docker compose exec -T worker python -c "from app.config import get_settings; from app.imports.documents import ocr_availability; a = ocr_availability(get_settings()); assert not a.available and 'INSTALL_OCR' in a.action; print('  ok  no OCR engine in the default image; the state explains how to enable it')"
fi

step "Seed and capability matrix"
"${acceptance[@]}" seed

step "WhatsApp export import"
"${acceptance[@]}" whatsapp

step "PDF documents"
"${acceptance[@]}" documents

step "Social connectors without credentials"
"${acceptance[@]}" social-blocked

step "Social connectors against fixture platforms"
"${acceptance[@]}" social

step "Unofficial Instagram profile page after an administrator enables it"
# Recreating the containers discards their logs; keep them for the log checks at the end.
docker compose logs --no-color api collector >>"$work_dir/recreated-services.log" 2>&1
TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=true docker compose up --detach --wait --no-deps api collector >/dev/null
"${acceptance[@]}" web-enabled
docker compose logs --no-color api collector >>"$work_dir/recreated-services.log" 2>&1
TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=false docker compose up --detach --wait --no-deps api collector >/dev/null

step "Comparison and timeline"
"${acceptance[@]}" compare

step "HTML report"
"${acceptance[@]}" reports

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
  step "Browser workflow: imports, timeline, comparison and report (Playwright)"
  if command -v pnpm >/dev/null 2>&1; then pnpm=(pnpm); else pnpm=(npx --yes pnpm@12.4.1); fi
  (cd apps/web && TRACEHOLLOW_E2E_BASE_URL="$web_url" TRACEHOLLOW_E2E_USERNAME=acceptance-admin \
    TRACEHOLLOW_E2E_PASSWORD="$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "${pnpm[@]}" exec playwright test e2e/phase4-workspace.spec.ts e2e/workspace-shell.spec.ts) | tee "$work_dir/e2e.log"
  grep -E "^ +2 passed" "$work_dir/e2e.log" >/dev/null || fail "the Phase 4 and interface browser workflows did not pass"
  if grep -E "^ +[0-9]+ (failed|skipped)" "$work_dir/e2e.log" >/dev/null; then fail "a browser test failed or was skipped"; fi
  ok "Phase 4 and interface browser workflows passed"
fi

step "Where processing ran"
in_worker="$(docker compose logs --no-color worker | grep -c '"processing_job_finished"' || true)"
[ "$in_worker" -ge 6 ] || fail "the worker finished only $in_worker processing jobs"
elsewhere="$(docker compose logs --no-color api collector ai-worker | grep -c '"processing_job_finished"' || true)"
[ "$elsewhere" = 0 ] || fail "$elsewhere processing job(s) ran outside the worker"
ok "the worker finished $in_worker processing jobs; no other service ran one"

step "Case deletion"
deleted_case="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['case_id'])" "$state")"
"${acceptance[@]}" delete
# The browser workflow (--e2e) keeps its own case, so only the deleted case's directory is checked.
remaining="$(docker compose exec -T worker sh -c "find /data/evidence/cases/$deleted_case -type f 2>/dev/null | wc -l" | tr -d ' ')"
[ "$remaining" = 0 ] || fail "$remaining evidence file(s) of the deleted case remain on the volume"
ok "no evidence files of the deleted case remain on the volume"

step "Tokens, keys and imported content do not appear in service logs"
docker compose logs --no-color >"$work_dir/service.log" 2>&1
cat "$work_dir/recreated-services.log" >>"$work_dir/service.log"
for candidate in "$TRACEHOLLOW_ACCEPTANCE_PASSWORD" "$TRACEHOLLOW_ACCEPTANCE_OTHER_PASSWORD" \
  EAAverificationGraphToken000000000000000000 AAverificationBotToken_00000000000000000 \
  AIzaVerificationKey00000000000000000000000 "toplantı saat kaçta" "Hosting moved to 203.0.113.9"; do
  if grep -F "$candidate" "$work_dir/service.log" >/dev/null; then fail "a secret or imported content appears in service logs: ${candidate:0:12}…"; fi
done
for secret in postgres_app_password redis_password app_secret_key credential_encryption_key; do
  value="$(tr -d '\n' <"secrets/$secret")"
  if grep -F "$value" "$work_dir/service.log" >/dev/null; then fail "the value of secrets/$secret appears in service logs"; fi
done
ok "no secrets, tokens or imported content in $(wc -l <"$work_dir/service.log" | tr -d ' ') log lines"

step "All Phase 4 stack checks passed"
