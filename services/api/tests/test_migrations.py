from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.health.checks import expected_migration_heads
from tests.conftest import (
    ServiceEndpoints,
    TemporaryDatabase,
    alembic_config,
    create_temporary_database,
)

pytestmark = pytest.mark.integration

PHASE0_TABLES = {"users", "sessions", "worker_checks", "alembic_version"}
PHASE1_TABLES = {
    "analyst_decisions",
    "case_deletions",
    "case_members",
    "cases",
    "connector_runs",
    "dispatch_outbox",
    "entities",
    "entity_evidence",
    "entity_identifiers",
    "evidence_objects",
    "notes",
    "observations",
    "query_runs",
    "relationship_evidence",
    "relationships",
    "saved_queries",
}
ALL_TABLES = PHASE0_TABLES | PHASE1_TABLES


def _tables(url: object) -> set[str]:
    engine = create_engine(url)  # type: ignore[call-overload]
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_fresh_database_upgrade_downgrade_and_reupgrade(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    assert _tables(database.url) == set()

    command.upgrade(config, "head")
    assert _tables(database.url) == ALL_TABLES

    command.downgrade(config, "0001")
    assert _tables(database.url) == PHASE0_TABLES

    command.downgrade(config, "base")
    assert _tables(database.url) == {"alembic_version"}

    command.upgrade(config, "head")
    assert _tables(database.url) == ALL_TABLES


def test_repeated_upgrade_is_a_non_destructive_no_op(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    command.upgrade(config, "head")

    engine = create_engine(database.url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, username_normalized, password_hash) "
                "VALUES (gen_random_uuid(), 'keep', 'keep', 'x')"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        versions = set(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
    engine.dispose()
    assert versions == set(expected_migration_heads())
