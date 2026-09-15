"""Create Phase 1 case, evidence and query lifecycle tables.

Cases and membership, entities with original/normalized identifiers, observations,
relationships with origin/review state and supporting references, notes, analyst decisions,
evidence metadata, saved queries, immutable run snapshots, connector outcomes, the dispatch
outbox and observable case deletion jobs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15 13:43:39.845353+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dispatch_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "aggregate_type IN ('query_run', 'case_deletion')",
            name=op.f("ck_dispatch_outbox_aggregate_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'done')",
            name=op.f("ck_dispatch_outbox_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dispatch_outbox")),
    )
    op.create_index("ix_dispatch_outbox_case_id", "dispatch_outbox", ["case_id"], unique=False)
    op.create_index(
        "ix_dispatch_outbox_status_available",
        "dispatch_outbox",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "uq_dispatch_outbox_aggregate",
        "dispatch_outbox",
        ["aggregate_type", "aggregate_id"],
        unique=True,
    )
    op.create_table(
        "case_deletions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_note", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "removed_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_case_deletions_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_deletions_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_deletions")),
    )
    op.create_index("ix_case_deletions_case_id", "case_deletions", ["case_id"], unique=False)
    op.create_index(
        "ix_case_deletions_requested_by_user_id",
        "case_deletions",
        ["requested_by_user_id"],
        unique=False,
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.Text(), server_default="", nullable=False),
        sa.Column("scope", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", sa.ARRAY(sa.String(length=64)), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleting', 'deletion_failed')",
            name=op.f("ck_cases_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_cases_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    op.create_index("ix_cases_status", "cases", ["status"], unique=False)
    op.create_table(
        "case_members",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('owner')", name=op.f("ck_case_members_role_valid")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_members_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_case_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_id", "user_id", name=op.f("pk_case_members")),
    )
    op.create_index("ix_case_members_user_id", "case_members", ["user_id"], unique=False)
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("input_type", sa.String(length=32), nullable=False),
        sa.Column("input_value", sa.String(length=1000), nullable=False),
        sa.Column("connector_ids", sa.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("collection_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "limits", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("run_counter", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_saved_queries_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_saved_queries_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_queries")),
    )
    op.create_index("ix_saved_queries_case_id", "saved_queries", ["case_id"], unique=False)
    op.create_table(
        "query_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("saved_query_id", sa.Uuid(), nullable=True),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("parameters_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', 'canceled')",
            name=op.f("ck_query_runs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["cancel_requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_query_runs_cancel_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_query_runs_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_query_runs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["saved_query_id"],
            ["saved_queries.id"],
            name=op.f("fk_query_runs_saved_query_id_saved_queries"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_runs")),
    )
    op.create_index(
        "ix_query_runs_case_queued", "query_runs", ["case_id", "queued_at"], unique=False
    )
    op.create_index(
        "ix_query_runs_saved_query", "query_runs", ["saved_query_id", "run_number"], unique=False
    )
    op.create_index("ix_query_runs_status", "query_runs", ["status"], unique=False)
    op.create_table(
        "connector_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("connector_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("pages_completed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_collected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetch_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retries", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "page_attempts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.String(length=500), nullable=True),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.Column(
            "coverage", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("coverage_note", sa.Text(), nullable=True),
        sa.Column("quota_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('findings', 'no_findings', 'partial', 'authentication_required', 'access_denied', 'rate_limited', 'unsupported', 'unavailable', 'parse_error', 'canceled')",
            name=op.f("ck_connector_runs_outcome_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', 'canceled')",
            name=op.f("ck_connector_runs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_connector_runs_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_connector_runs_query_run_id_query_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_runs")),
    )
    op.create_index(
        "uq_connector_runs_run_connector",
        "connector_runs",
        ["query_run_id", "connector_id"],
        unique=True,
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_query_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('organization', 'domain', 'ip', 'url', 'username', 'platform_account', 'email', 'phone', 'document', 'event')",
            name=op.f("ck_entities_entity_type_valid"),
        ),
        sa.CheckConstraint(
            "origin IN ('observed', 'deterministic_derivation', 'ai_suggestion', 'analyst_assertion')",
            name=op.f("ck_entities_origin_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_entities_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_entities_created_by_query_run_id_query_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_entities_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
    )
    op.create_index(
        "ix_entities_case_id_entity_type", "entities", ["case_id", "entity_type"], unique=False
    )
    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("identifier_type", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=100), nullable=True),
        sa.Column("original_value", sa.String(length=2048), nullable=False),
        sa.Column("normalized_value", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "identifier_type <> 'platform_id' OR platform IS NOT NULL",
            name=op.f("ck_entity_identifiers_platform_id_requires_platform"),
        ),
        sa.CheckConstraint(
            "identifier_type IN ('domain', 'email', 'username', 'platform_id', 'url', 'ip', 'phone', 'name', 'other')",
            name=op.f("ck_entity_identifiers_identifier_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_entity_identifiers_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_identifiers_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_identifiers")),
    )
    op.create_index(
        "ix_entity_identifiers_entity_id", "entity_identifiers", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_entity_identifiers_lookup",
        "entity_identifiers",
        ["case_id", "identifier_type", "normalized_value"],
        unique=False,
    )
    op.create_index(
        "uq_entity_identifiers_entity_value",
        "entity_identifiers",
        ["entity_id", "identifier_type", "platform", "normalized_value"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_entity_identifiers_platform_id",
        "entity_identifiers",
        ["case_id", "platform", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'platform_id'"),
    )
    op.create_table(
        "evidence_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("acquisition_method", sa.String(length=32), nullable=False),
        sa.Column("import_origin", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(length=2048), nullable=True),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_published_at_original", sa.String(length=64), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("connector_id", sa.String(length=100), nullable=True),
        sa.Column("connector_version", sa.String(length=32), nullable=True),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("connector_run_id", sa.Uuid(), nullable=True),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "acquisition_method <> 'authorized_import' OR import_origin IS NOT NULL",
            name=op.f("ck_evidence_objects_import_requires_origin"),
        ),
        sa.CheckConstraint(
            "acquisition_method IN ('authorized_import', 'synthetic_fixture')",
            name=op.f("ck_evidence_objects_acquisition_method_valid"),
        ),
        sa.CheckConstraint("kind IN ('text', 'json')", name=op.f("ck_evidence_objects_kind_valid")),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_evidence_objects_sha256_hex")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_evidence_objects_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_evidence_objects_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_run_id"],
            ["connector_runs.id"],
            name=op.f("fk_evidence_objects_connector_run_id_connector_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name=op.f("fk_evidence_objects_imported_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_evidence_objects_query_run_id_query_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_objects")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_evidence_objects_storage_key")),
    )
    op.create_index(
        "ix_evidence_objects_case_collected",
        "evidence_objects",
        ["case_id", "collected_at"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_objects_case_sha256", "evidence_objects", ["case_id", "sha256"], unique=False
    )
    op.create_index(
        "ix_evidence_objects_query_run_id", "evidence_objects", ["query_run_id"], unique=False
    )
    op.create_index(
        "uq_evidence_objects_connector_page",
        "evidence_objects",
        ["connector_run_id", "page_index"],
        unique=True,
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source_entity_id", sa.Uuid(), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=False),
        sa.Column("predicate", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_query_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin IN ('observed', 'deterministic_derivation', 'ai_suggestion', 'analyst_assertion')",
            name=op.f("ck_relationships_origin_valid"),
        ),
        sa.CheckConstraint(
            "predicate ~ '^[a-z][a-z0-9_]{1,63}$'", name=op.f("ck_relationships_predicate_format")
        ),
        sa.CheckConstraint(
            "review_status IN ('unreviewed', 'accepted', 'rejected', 'superseded')",
            name=op.f("ck_relationships_review_status_valid"),
        ),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id", name=op.f("ck_relationships_distinct_endpoints")
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name=op.f("ck_relationships_valid_period"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_relationships_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_relationships_created_by_query_run_id_query_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_relationships_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_entity_id"],
            ["entities.id"],
            name=op.f("fk_relationships_source_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id"],
            ["entities.id"],
            name=op.f("fk_relationships_target_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
    )
    op.create_index(
        "ix_relationships_source", "relationships", ["case_id", "source_entity_id"], unique=False
    )
    op.create_index(
        "ix_relationships_target", "relationships", ["case_id", "target_entity_id"], unique=False
    )
    op.create_index(
        "uq_relationships_observed_edge",
        "relationships",
        ["case_id", "source_entity_id", "target_entity_id", "predicate"],
        unique=True,
        postgresql_where=sa.text("origin = 'observed'"),
    )
    op.create_table(
        "analyst_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("previous_value", sa.String(length=32), nullable=False),
        sa.Column("new_value", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_analyst_decisions_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_analyst_decisions_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.id"],
            name=op.f("fk_analyst_decisions_relationship_id_relationships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyst_decisions")),
    )
    op.create_index(
        "ix_analyst_decisions_relationship_id",
        "analyst_decisions",
        ["relationship_id"],
        unique=False,
    )
    op.create_table(
        "entity_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_entity_evidence_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_entity_evidence_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_evidence_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_entity_evidence_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_evidence")),
    )
    op.create_index(
        "ix_entity_evidence_evidence_id", "entity_evidence", ["evidence_id"], unique=False
    )
    op.create_index(
        "uq_entity_evidence_link", "entity_evidence", ["entity_id", "evidence_id"], unique=True
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("relationship_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "num_nonnulls(entity_id, relationship_id, evidence_id) <= 1",
            name=op.f("ck_notes_single_subject"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_notes_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_notes_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_notes_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_notes_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.id"],
            name=op.f("fk_notes_relationship_id_relationships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    op.create_index("ix_notes_case_id", "notes", ["case_id"], unique=False)
    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("connector_run_id", sa.Uuid(), nullable=True),
        sa.Column("observation_type", sa.String(length=64), nullable=False),
        sa.Column("source_object_id", sa.String(length=512), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_observations_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_run_id"],
            ["connector_runs.id"],
            name=op.f("fk_observations_connector_run_id_connector_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_observations_entity_id_entities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_observations_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_observations_query_run_id_query_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
    )
    op.create_index("ix_observations_entity_id", "observations", ["entity_id"], unique=False)
    op.create_index("ix_observations_query_run_id", "observations", ["query_run_id"], unique=False)
    op.create_index(
        "uq_observations_case_idempotency",
        "observations",
        ["case_id", "idempotency_key"],
        unique=True,
    )
    op.create_table(
        "relationship_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.Column("stance", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stance IN ('supports', 'contradicts')",
            name=op.f("ck_relationship_evidence_stance_valid"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(evidence_id, observation_id) >= 1",
            name=op.f("ck_relationship_evidence_has_reference"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_relationship_evidence_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_relationship_evidence_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_relationship_evidence_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=op.f("fk_relationship_evidence_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.id"],
            name=op.f("fk_relationship_evidence_relationship_id_relationships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationship_evidence")),
    )
    op.create_index(
        "uq_relationship_evidence_reference",
        "relationship_evidence",
        ["relationship_id", "evidence_id", "observation_id", "stance"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_relationship_evidence_reference",
        table_name="relationship_evidence",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_table("relationship_evidence")
    op.drop_index("uq_observations_case_idempotency", table_name="observations")
    op.drop_index("ix_observations_query_run_id", table_name="observations")
    op.drop_index("ix_observations_entity_id", table_name="observations")
    op.drop_table("observations")
    op.drop_index("ix_notes_case_id", table_name="notes")
    op.drop_table("notes")
    op.drop_index("uq_entity_evidence_link", table_name="entity_evidence")
    op.drop_index("ix_entity_evidence_evidence_id", table_name="entity_evidence")
    op.drop_table("entity_evidence")
    op.drop_index("ix_analyst_decisions_relationship_id", table_name="analyst_decisions")
    op.drop_table("analyst_decisions")
    op.drop_index(
        "uq_relationships_observed_edge",
        table_name="relationships",
        postgresql_where=sa.text("origin = 'observed'"),
    )
    op.drop_index("ix_relationships_target", table_name="relationships")
    op.drop_index("ix_relationships_source", table_name="relationships")
    op.drop_table("relationships")
    op.drop_index(
        "uq_evidence_objects_connector_page",
        table_name="evidence_objects",
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.drop_index("ix_evidence_objects_query_run_id", table_name="evidence_objects")
    op.drop_index("ix_evidence_objects_case_sha256", table_name="evidence_objects")
    op.drop_index("ix_evidence_objects_case_collected", table_name="evidence_objects")
    op.drop_table("evidence_objects")
    op.drop_index(
        "uq_entity_identifiers_platform_id",
        table_name="entity_identifiers",
        postgresql_where=sa.text("identifier_type = 'platform_id'"),
    )
    op.drop_index(
        "uq_entity_identifiers_entity_value",
        table_name="entity_identifiers",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_entity_identifiers_lookup", table_name="entity_identifiers")
    op.drop_index("ix_entity_identifiers_entity_id", table_name="entity_identifiers")
    op.drop_table("entity_identifiers")
    op.drop_index("ix_entities_case_id_entity_type", table_name="entities")
    op.drop_table("entities")
    op.drop_index("uq_connector_runs_run_connector", table_name="connector_runs")
    op.drop_table("connector_runs")
    op.drop_index("ix_query_runs_status", table_name="query_runs")
    op.drop_index("ix_query_runs_saved_query", table_name="query_runs")
    op.drop_index("ix_query_runs_case_queued", table_name="query_runs")
    op.drop_table("query_runs")
    op.drop_index("ix_saved_queries_case_id", table_name="saved_queries")
    op.drop_table("saved_queries")
    op.drop_index("ix_case_members_user_id", table_name="case_members")
    op.drop_table("case_members")
    op.drop_index("ix_cases_status", table_name="cases")
    op.drop_table("cases")
    op.drop_index("ix_case_deletions_requested_by_user_id", table_name="case_deletions")
    op.drop_index("ix_case_deletions_case_id", table_name="case_deletions")
    op.drop_table("case_deletions")
    op.drop_index("uq_dispatch_outbox_aggregate", table_name="dispatch_outbox")
    op.drop_index("ix_dispatch_outbox_status_available", table_name="dispatch_outbox")
    op.drop_index("ix_dispatch_outbox_case_id", table_name="dispatch_outbox")
    op.drop_table("dispatch_outbox")
