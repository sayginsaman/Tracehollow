"""Dependency checks used by readiness and the authenticated system status endpoint."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

API_ROOT = Path(__file__).resolve().parents[2]


class CheckStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    MIGRATIONS_PENDING = "migrations_pending"
    NOT_WRITABLE = "not_writable"


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: CheckStatus
    detail: str | None = None  # safe, non-secret description for authenticated callers

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.OK


def expected_migration_heads() -> frozenset[str]:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def check_database(engine: Engine, expected_heads: frozenset[str]) -> dict[str, CheckResult]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            has_version_table = connection.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar_one()
            current = (
                set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if has_version_table
                else set()
            )
    except SQLAlchemyError as exc:
        logger.warning("readiness_database_unavailable", extra={"error_type": type(exc).__name__})
        return {
            "database": CheckResult(CheckStatus.UNAVAILABLE, "database connection failed"),
            "migrations": CheckResult(CheckStatus.UNAVAILABLE, "database connection failed"),
        }
    migrations = (
        CheckResult(CheckStatus.OK, f"at revision {', '.join(sorted(current))}")
        if current == expected_heads
        else CheckResult(
            CheckStatus.MIGRATIONS_PENDING,
            f"database revision {', '.join(sorted(current)) or 'none'}; "
            f"expected {', '.join(sorted(expected_heads))}",
        )
    )
    return {"database": CheckResult(CheckStatus.OK), "migrations": migrations}


def check_redis(client: redis.Redis) -> CheckResult:
    try:
        client.ping()
    except redis.RedisError as exc:
        logger.warning("readiness_redis_unavailable", extra={"error_type": type(exc).__name__})
        return CheckResult(CheckStatus.UNAVAILABLE, "broker connection failed")
    return CheckResult(CheckStatus.OK)


def check_storage(path: Path) -> CheckResult:
    probe = path / f".readiness-{secrets.token_hex(8)}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        probe.unlink()
    except OSError as exc:
        logger.warning("readiness_storage_not_writable", extra={"error_type": type(exc).__name__})
        return CheckResult(CheckStatus.NOT_WRITABLE, "evidence storage is not writable")
    return CheckResult(CheckStatus.OK)
