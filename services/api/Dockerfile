# syntax=docker/dockerfile:1

# One image serves the API, the Celery workers, the discovery egress gateway and the one-shot
# migration job. The `collector` target adds the Sherlock engine for the collector service. The
# `discovery-runner` target holds only Subfinder and its standard-library job runner; it runs in
# the network sandbox (ADR 0007) and contains no other application code.

FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-default-groups --no-install-project

FROM build AS build-collector
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-default-groups --group collectors --no-install-project

# ProjectDiscovery Subfinder (MIT), pinned by version and verified against the release checksums.
FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS subfinder
ARG TARGETARCH
ARG SUBFINDER_VERSION=2.16.0
ARG SUBFINDER_SHA256_AMD64=1b7f9c608e9a5bd59e609a5e09710d63c5485e92d3d49dc2c16eb4fdbe10cb60
ARG SUBFINDER_SHA256_ARM64=c81d49559c0f630177be9e347e502e7a3d474aacc6ff78291ffcb4964367d63d
RUN python - <<'PY'
import hashlib, io, os, stat, urllib.request, zipfile
arch = os.environ["TARGETARCH"]
expected = {"amd64": os.environ["SUBFINDER_SHA256_AMD64"], "arm64": os.environ["SUBFINDER_SHA256_ARM64"]}[arch]
version = os.environ["SUBFINDER_VERSION"]
url = f"https://github.com/projectdiscovery/subfinder/releases/download/v{version}/subfinder_{version}_linux_{arch}.zip"
data = urllib.request.urlopen(url, timeout=120).read()
digest = hashlib.sha256(data).hexdigest()
if digest != expected:
    raise SystemExit(f"subfinder checksum mismatch: {digest}")
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    for name in ("subfinder", "LICENSE.md"):
        with open(f"/opt/{name}", "wb") as target:
            target.write(archive.read(name))
os.chmod("/opt/subfinder", stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
PY

FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS base
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
RUN groupadd --gid 10001 tracehollow \
    && useradd --uid 10001 --gid tracehollow --home-dir /nonexistent \
       --no-create-home --shell /usr/sbin/nologin tracehollow \
    && mkdir -p /data/evidence \
    && chown tracehollow:tracehollow /data/evidence \
    && chmod 0750 /data/evidence
WORKDIR /app
EXPOSE 8000
CMD ["python", "-m", "app.server"]

FROM base AS collector
COPY --from=repository LICENSE NOTICE.md /usr/local/share/licenses/tracehollow/
COPY --from=build-collector /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root app ./app
USER 10001:10001

# Subfinder in the network sandbox: no virtual environment, no database or broker client, no
# secrets; only the modules the runner imports (standard library only).
FROM base AS discovery-runner
COPY --from=repository LICENSE NOTICE.md /usr/local/share/licenses/tracehollow/
COPY --from=subfinder /opt/subfinder /usr/local/bin/subfinder
COPY --from=subfinder /opt/LICENSE.md /usr/local/share/licenses/subfinder/LICENSE.md
COPY --chown=root:root app/__init__.py app/logging_config.py ./app/
COPY --chown=root:root app/connectors/__init__.py ./app/connectors/
COPY --chown=root:root app/connectors/engines/__init__.py app/connectors/engines/process.py app/connectors/engines/subfinder_runner.py ./app/connectors/engines/
COPY --chown=root:root app/egress/__init__.py app/egress/providers.py ./app/egress/
USER 10001:10001
EXPOSE 8090
CMD ["python", "-m", "app.connectors.engines.subfinder_runner"]

# Default target (last stage): API, worker, dispatcher, ai-worker, discovery-gateway and migrate.
FROM base AS runtime
# Optional OCR engine for scanned PDFs (docs/operations/document-processing.md). Debian packages
# are not version-pinned here; each processing job records the Tesseract version it used.
ARG INSTALL_OCR=false
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      apt-get update \
      && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-tur \
      && rm -rf /var/lib/apt/lists/*; \
    elif [ "$INSTALL_OCR" != "false" ]; then \
      echo "INSTALL_OCR must be true or false" >&2; exit 1; \
    fi
COPY --from=repository LICENSE NOTICE.md /usr/local/share/licenses/tracehollow/
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root app ./app
USER 10001:10001
