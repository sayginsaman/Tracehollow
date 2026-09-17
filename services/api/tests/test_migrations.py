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
PHASE2_TABLES = {"integration_credentials", "source_pacing", "source_slots"}
PHASE4_TABLES = {"processing_jobs"}
PHASE5_TEAM_TABLES = {"audit_events"}
PHASE5_TABLES = PHASE5_TEAM_TABLES
ALL_TABLES = (
    PHASE0_TABLES | PHASE1_TABLES | PHASE3_TABLES | PHASE2_TABLES | PHASE4_TABLES | PHASE5_TABLES
)


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

    command.downgrade(config, "0005")
    assert _tables(database.url) == ALL_TABLES - PHASE5_TABLES

    command.downgrade(config, "0004")
    assert _tables(database.url) == ALL_TABLES - PHASE5_TABLES - PHASE4_TABLES

    command.downgrade(config, "0003")
    assert _tables(database.url) == PHASE0_TABLES | PHASE1_TABLES | PHASE3_TABLES

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


def test_collection_migration_keeps_existing_pages_and_downgrades_cleanly(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    command.upgrade(config, "0003")
    engine = create_engine(database.url)
    ids = {
        "case": "33333333-3333-4333-8333-333333333333",
        "run": "44444444-4444-4444-8444-444444444444",
        "connector_run": "55555555-5555-4555-8555-555555555555",
        "page": "66666666-6666-4666-8666-666666666666",
        "web": "77777777-7777-4777-8777-777777777777",
    }
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO cases (id, title, status) VALUES (:case, 'Vaka', 'active')"), ids
        )
        connection.execute(
            text(
                "INSERT INTO query_runs (id, case_id, run_number, parameters_snapshot, status)"
                " VALUES (:run, :case, 1, '{}', 'completed')"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO connector_runs (id, case_id, query_run_id, position, connector_id,"
                " connector_version, status) VALUES (:connector_run, :case, :run, 0,"
                " 'synthetic.fixture', '1.0.0', 'completed')"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, collected_at, connector_id,"
                " connector_version, query_run_id, connector_run_id, page_index) VALUES"
                " (:page, :case, 'json', 'Page 1', 'application/json', 2, :sha,"
                " 'cases/x/evidence/page', 'synthetic_fixture', now(), 'synthetic.fixture',"
                " '1.0.0', :run, :connector_run, 0)"
            ),
            {**ids, "sha": "b" * 64},
        )

    command.upgrade(config, "head")
    with engine.begin() as connection:
        part = connection.execute(
            text("SELECT page_part, collection_mode FROM evidence_objects")
        ).one()
        assert tuple(part) == ("page", None)
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, collected_at, connector_id,"
                " connector_version, query_run_id, connector_run_id, page_index, page_part,"
                " collection_mode, access_category) VALUES (:web, :case, 'html', 'Snapshot',"
                " 'text/html', 2, :sha, 'cases/x/evidence/web', 'connector_collection', now(),"
                " 'public_web.page', '1.0.0', :run, :connector_run, 0, 'snapshot',"
                " 'direct_request', 'public')"
            ),
            {**ids, "sha": "c" * 64},
        )
    command.downgrade(config, "0003")
    with engine.connect() as connection:
        kinds = connection.execute(text("SELECT acquisition_method FROM evidence_objects")).all()
    engine.dispose()
    assert [row[0] for row in kinds] == ["synthetic_fixture"]


def test_models_and_migrations_describe_the_same_schema(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    """Alembic autogenerate finds nothing to change after upgrading to head."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.models import Base

    database = create_temporary_database(services, database_factory)
    command.upgrade(alembic_config(database.url), "head")
    engine = create_engine(database.url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": False}
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert differences == []


def test_processing_migration_keeps_evidence_and_downgrades_binary_records(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    command.upgrade(config, "0004")
    engine = create_engine(database.url)
    ids = {
        "case": "81111111-1111-4111-8111-111111111111",
        "text": "82222222-2222-4222-8222-222222222222",
        "pdf": "83333333-3333-4333-8333-333333333333",
        "derived": "84444444-4444-4444-8444-444444444444",
        "job": "85555555-5555-4555-8555-555555555555",
    }
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO cases (id, title, status) VALUES (:case, 'Belge', 'active')"), ids
        )
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, import_origin, collected_at) VALUES"
                " (:text, :case, 'text', 'Not', 'text/plain', 2, :sha, 'cases/x/evidence/t',"
                " 'authorized_import', 'test', now())"
            ),
            {**ids, "sha": "d" * 64},
        )

    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM evidence_objects")).scalar_one() == 1
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, import_origin, collected_at) VALUES"
                " (:pdf, :case, 'pdf', 'Rapor', 'application/pdf', 5, :sha,"
                " 'cases/x/evidence/p', 'authorized_import', 'test', now())"
            ),
            {**ids, "sha": "e" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO processing_jobs (id, case_id, evidence_id, job_type, status)"
                " VALUES (:job, :case, :pdf, 'document_text', 'completed')"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO evidence_objects (id, case_id, kind, title, content_type, size_bytes,"
                " sha256, storage_key, acquisition_method, import_origin, collected_at,"
                " derived_from_evidence_id, processing_job_id, page_part) VALUES"
                " (:derived, :case, 'text', 'Metin', 'text/plain', 5, :sha,"
                " 'cases/x/evidence/d', 'authorized_import', 'test', now(), :pdf, :job,"
                " 'text_layer')"
            ),
            {**ids, "sha": "f" * 64},
        )
        # A second active job for the same original and type is refused.
        with (
            pytest.raises(Exception, match="uq_processing_jobs_active"),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO processing_jobs (id, case_id, evidence_id, job_type, status)"
                    " VALUES (gen_random_uuid(), :case, :pdf, 'document_text', 'queued'),"
                    " (gen_random_uuid(), :case, :pdf, 'document_text', 'running')"
                ),
                ids,
            )

    command.downgrade(config, "0004")
    with engine.connect() as connection:
        kinds = sorted(
            connection.execute(text("SELECT kind FROM evidence_objects ORDER BY kind")).scalars()
        )
    engine.dispose()
    assert _tables(database.url) == ALL_TABLES - PHASE5_TABLES - PHASE4_TABLES
    # The binary original cannot be represented; imported and derived text stays.
    assert kinds == ["text", "text"]
    command.upgrade(config, "head")
    assert _tables(database.url) == ALL_TABLES


def test_team_roles_migration_keeps_every_account_and_membership_able_to_work(
    services: ServiceEndpoints, database_factory: list[TemporaryDatabase]
) -> None:
    database = create_temporary_database(services, database_factory)
    config = alembic_config(database.url)
    command.upgrade(config, "0005")
    engine = create_engine(database.url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, username_normalized, password_hash, is_admin) "
                "VALUES ('00000000-0000-4000-8000-000000000001', 'admin', 'admin', 'x', true), "
                "('00000000-0000-4000-8000-000000000002', 'analyst', 'analyst', 'x', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO cases (id, title, status) VALUES "
                "('00000000-0000-4000-8000-00000000c001', 'Existing', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO case_members (case_id, user_id, role) VALUES "
                "('00000000-0000-4000-8000-00000000c001', "
                "'00000000-0000-4000-8000-000000000002', 'owner')"
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        roles = {
            row[0]: row[1] for row in connection.execute(text("SELECT username, role FROM users"))
        }
        membership = connection.execute(text("SELECT role FROM case_members")).scalar_one()
    assert roles == {"admin": "administrator", "analyst": "analyst"}
    assert membership == "analyst"

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, username_normalized, password_hash, role) "
                "VALUES ('00000000-0000-4000-8000-000000000003', 'viewer', 'viewer', 'x', 'viewer')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO case_members (case_id, user_id, role) VALUES "
                "('00000000-0000-4000-8000-00000000c001', "
                "'00000000-0000-4000-8000-000000000003', 'viewer')"
            )
        )
    command.downgrade(config, "0005")
    with engine.connect() as connection:
        admins = {
            row[0]: row[1]
            for row in connection.execute(text("SELECT username, is_admin FROM users"))
        }
        memberships = set(connection.execute(text("SELECT role FROM case_members")).scalars())
    engine.dispose()
    assert admins == {"admin": True, "analyst": False, "viewer": False}
    assert memberships == {"owner"}
