# syntax=docker/dockerfile:1

# One image serves the API, the Celery worker and the one-shot migration job.

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
    uv sync --locked --no-dev --no-install-project

FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime
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
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root app ./app
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "app.server"]
