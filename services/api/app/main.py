from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.auth.router import auth_router, setup_router
from app.config import Settings, get_settings
from app.db.session import create_db_engine, create_session_factory
from app.health.checks import expected_migration_heads
from app.health.router import router as health_router
from app.security_middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
)
from app.system.router import router as system_router
from app.tasks.celery_app import create_celery_app

logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 64 * 1024


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

    app.include_router(health_router)
    app.include_router(setup_router)
    app.include_router(auth_router)
    app.include_router(system_router)

    # Middleware added last runs first.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    app.add_middleware(OriginCheckMiddleware, trusted_origins=settings.trusted_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    return app
