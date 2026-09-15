from __future__ import annotations

import secrets

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

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
PHASE3_TABLES = {
    "ai_citations",
    "ai_conversations",
    "ai_messages",
    "ai_provider_status",
    "ai_runs",
    "chunk_embeddings",
    "document_chunks",
    "embedding_profiles",
    "evidence_index_states",
}
ALL_TABLES = PHASE0_TABLES | PHASE1_TABLES | PHASE3_TABLES


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

    command.downgrade(config, "0002")
    assert _tables(database.url) == PHASE0_TABLES | PHASE1_TABLES

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


def test_upgrade_keeps_phase1_data_and_queues_existing_evidence(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    command.upgrade(config, "0002")
    engine = create_engine(database.url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cases (id, title, status) VALUES "
                "('11111111-1111-4111-8111-111111111111', 'Örnek vaka', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, import_origin, collected_at) VALUES"
                " ('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111',"
                " 'text', 'Kayıt', 'text/plain', 3, :sha, 'cases/x/evidence/y',"
                " 'authorized_import', 'test', now())"
            ),
            {"sha": "a" * 64},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        case = connection.execute(text("SELECT title, ai_mode, ai_policy_version FROM cases")).one()
        states = connection.execute(
            text("SELECT evidence_id::text, status FROM evidence_index_states")
        ).all()
        vector = connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    engine.dispose()
    assert tuple(case) == ("Örnek vaka", "local_only", 1)
    assert [tuple(row) for row in states] == [("22222222-2222-4222-8222-222222222222", "pending")]
    assert vector == "vector"


def test_non_superuser_migration_explains_missing_pgvector(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    """The production role is not a superuser: the migration must stop with instructions and
    succeed once an operator (the db-extensions service) has installed the extension."""
    role = f"tracehollow_role_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    admin = create_engine(services.pg_url("tracehollow_test"), isolation_level="AUTOCOMMIT")
    name = f"tracehollow_t_{secrets.token_hex(6)}"
    with admin.connect() as connection:
        connection.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER PASSWORD '{password}'"))
        connection.execute(text(f'CREATE DATABASE "{name}" OWNER {role}'))
    database_factory.append(TemporaryDatabase(services, name))
    role_url = URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=services.pg_host,
        port=services.pg_port,
        database=name,
    )
    config = alembic_config(role_url)
    try:
        with pytest.raises(RuntimeError, match="db-extensions"):
            command.upgrade(config, "head")
        assert _tables(role_url) >= PHASE0_TABLES | PHASE1_TABLES
        assert not (_tables(role_url) & PHASE3_TABLES)

        superuser = create_engine(services.pg_url(name), isolation_level="AUTOCOMMIT")
        with superuser.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        superuser.dispose()

        command.upgrade(config, "head")
        assert _tables(role_url) == ALL_TABLES
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
