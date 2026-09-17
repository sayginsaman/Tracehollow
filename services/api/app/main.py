from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.activity.router import router as activity_router
from app.ai.router import router as ai_router
from app.ai.router import status_router as ai_status_router
from app.auth.router import auth_router, setup_router
from app.cases.router import deletions_router
from app.cases.router import router as cases_router
from app.config import Settings, get_settings
from app.connectors.router import router as connectors_router
from app.db.session import create_db_engine, create_session_factory
from app.entities.router import router as entities_router
from app.evidence.router import IMPORT_PATH_PATTERN
from app.evidence.router import router as evidence_router
from app.evidence.storage import EvidenceStorage
from app.exports.router import router as exports_router
from app.health.checks import expected_migration_heads
from app.health.router import router as health_router
from app.imports.router import DOCUMENT_IMPORT_PATH_PATTERN, WHATSAPP_IMPORT_PATH_PATTERN
from app.imports.router import router as imports_router
from app.queries.router import router as queries_router
from app.reports.router import router as reports_router
from app.security_middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
)
from app.system.router import router as system_router
from app.tasks.celery_app import create_celery_app

logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 64 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024


async def database_unavailable_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Report a lost database connection as a dependency failure instead of a generic 500."""
    logger.warning("database_unavailable", extra={"error_type": type(exc).__name__})
    return JSONResponse(
        status_code=503,
        content={"detail": "database_unavailable"},
        headers={"Retry-After": "5"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("api_starting", extra={"version": __version__, "environment": settings.env})
        yield
        app.state.redis.close()
        app.state.engine.dispose()

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="Tracehollow API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )

    engine = create_db_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    app.state.celery = create_celery_app(settings)
    app.state.expected_migration_heads = expected_migration_heads()
    app.state.evidence_storage = EvidenceStorage(settings.evidence_storage_path)

    app.include_router(health_router)
    app.include_router(setup_router)
    app.include_router(auth_router)
    app.include_router(system_router)
    app.include_router(cases_router)
    app.include_router(deletions_router)
    app.include_router(activity_router)
    app.include_router(entities_router)
    app.include_router(evidence_router)
    app.include_router(imports_router)
    app.include_router(queries_router)
    app.include_router(connectors_router)
    app.include_router(exports_router)
    app.include_router(reports_router)
    app.include_router(ai_status_router)
    app.include_router(ai_router)
    app.add_exception_handler(OperationalError, database_unavailable_handler)
    app.add_exception_handler(InterfaceError, database_unavailable_handler)

    # Middleware added last runs first.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=MAX_REQUEST_BODY_BYTES,
        overrides=[
            (IMPORT_PATH_PATTERN, settings.evidence_max_import_bytes + MULTIPART_OVERHEAD_BYTES),
            (
                WHATSAPP_IMPORT_PATH_PATTERN,
                settings.import_max_archive_bytes + MULTIPART_OVERHEAD_BYTES,
            ),
            (
                DOCUMENT_IMPORT_PATH_PATTERN,
                settings.import_max_document_bytes + MULTIPART_OVERHEAD_BYTES,
            ),
        ],
    )
    app.add_middleware(OriginCheckMiddleware, trusted_origins=settings.trusted_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    return app
