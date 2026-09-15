"""Evidence-grounded AI: chunks, embeddings, index state, conversations, runs and citations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16

pgvector is not a trusted extension, so a non-superuser application role cannot create it.
Compose installs it with the ``db-extensions`` one-shot service before migrations run; this
migration creates it only when the connected role is allowed to, and otherwise stops with an
actionable message instead of a partial schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _Vector(sa.types.UserDefinedType[list[float]]):
    """pgvector column without a fixed dimension (a check constraint ties it to ``dimensions``)."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return "vector"


EXTENSION_HELP = (
    "The pgvector extension is not installed in this database and the migration role cannot "
    "create it. Run `docker compose run --rm db-extensions` (or, as a PostgreSQL superuser, "
    "`CREATE EXTENSION IF NOT EXISTS vector;` in the application database), then migrate again."
)


def _ensure_vector_extension() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
        return
    is_superuser = bind.execute(
        sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    ).scalar()
    if not is_superuser:
        raise RuntimeError(EXTENSION_HELP)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    _ensure_vector_extension()

    op.add_column(
        "cases",
        sa.Column("ai_mode", sa.String(length=16), server_default="local_only", nullable=False),
    )
    op.add_column(
        "cases", sa.Column("ai_policy_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_cases_ai_mode_valid"),
        "cases",
        "ai_mode IN ('disabled', 'local_only', 'cloud_allowed')",
    )

    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check')",
    )

    op.create_table(
        "embedding_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("model_digest", sa.String(length=128), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("chunking_version", sa.Integer(), nullable=False),
        sa.Column("indexing_version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "dimensions > 0 AND dimensions <= 16000",
            name=op.f("ck_embedding_profiles_dimensions_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_profiles")),
        sa.UniqueConstraint("profile_key", name=op.f("uq_embedding_profiles_profile_key")),
    )
    op.create_index(
        "uq_embedding_profiles_single_active",
        "embedding_profiles",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunking_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("json_locations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "identifiers",
            postgresql.ARRAY(sa.String(length=512)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint("kind IN ('text', 'json')", name=op.f("ck_document_chunks_kind_valid")),
        sa.CheckConstraint(
            "(kind = 'text' AND char_start IS NOT NULL AND char_end > char_start)"
            " OR (kind = 'json' AND json_locations IS NOT NULL)",
            name=op.f("ck_document_chunks_location_present"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_document_chunks_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_document_chunks_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
    )
    op.create_index(
        "uq_document_chunks_evidence_version_index",
        "document_chunks",
        ["evidence_id", "chunking_version", "chunk_index"],
        unique=True,
    )
    op.create_index("ix_document_chunks_case_id", "document_chunks", ["case_id"])
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_chunks_identifiers",
        "document_chunks",
        ["identifiers"],
        postgresql_using="gin",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name=op.f("ck_chunk_embeddings_dimensions_match"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_chunk_embeddings_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["embedding_profiles.id"],
            name=op.f("fk_chunk_embeddings_profile_id_embedding_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "profile_id", name=op.f("pk_chunk_embeddings")),
    )
    op.create_index(
        "ix_chunk_embeddings_case_profile", "chunk_embeddings", ["case_id", "profile_id"]
    )

    op.create_table(
        "evidence_index_states",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("chunking_version", sa.Integer(), nullable=True),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=300), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'indexing', 'indexed', 'failed', 'canceled')",
            name=op.f("ck_evidence_index_states_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_evidence_index_states_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_evidence_index_states_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["embedding_profiles.id"],
            name=op.f("fk_evidence_index_states_profile_id_embedding_profiles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name=op.f("pk_evidence_index_states")),
    )
    op.create_index(
        "ix_evidence_index_states_case_status", "evidence_index_states", ["case_id", "status"]
    )

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_ai_conversations_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_conversations_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_conversations")),
    )
    op.create_index(
        "ix_ai_conversations_case_updated", "ai_conversations", ["case_id", "updated_at"]
    )

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("requested_location", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("processing_location", sa.String(length=16), nullable=True),
        sa.Column("prompt_template_version", sa.String(length=32), nullable=True),
        sa.Column(
            "retrieval",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "coverage", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "validation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "usage", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=300), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "run_type IN ('answer', 'summary', 'relationship_suggestions')",
            name=op.f("ck_ai_runs_run_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name=op.f("ck_ai_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "requested_location IN ('local', 'cloud', 'fixture')",
            name=op.f("ck_ai_runs_requested_valid"),
        ),
        sa.CheckConstraint(
            "processing_location IS NULL OR processing_location IN ('local', 'cloud', 'fixture')",
            name=op.f("ck_ai_runs_processing_location_valid"),
        ),
        sa.CheckConstraint(
            "run_type <> 'answer' OR (question IS NOT NULL AND conversation_id IS NOT NULL)",
            name=op.f("ck_ai_runs_answer_has_question"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_ai_runs_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name=op.f("fk_ai_runs_conversation_id_ai_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_runs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
    )
    op.create_index("ix_ai_runs_case_queued", "ai_runs", ["case_id", "queued_at"])
    op.create_index("ix_ai_runs_conversation", "ai_runs", ["conversation_id"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_ai_messages_role_valid")),
        sa.CheckConstraint(
            "kind IN ('question', 'answer', 'summary', 'suggestions')",
            name=op.f("ck_ai_messages_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_ai_messages_ai_run_id_ai_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_ai_messages_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name=op.f("fk_ai_messages_conversation_id_ai_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_messages")),
    )
    op.create_index(
        "ix_ai_messages_conversation_created", "ai_messages", ["conversation_id", "created_at"]
    )
    op.create_index("ix_ai_messages_run", "ai_messages", ["ai_run_id"])

    op.create_table(
        "ai_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("ai_run_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("claim_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("ref_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("source_char_start", sa.Integer(), nullable=True),
        sa.Column("source_char_end", sa.Integer(), nullable=True),
        sa.Column("json_pointer", sa.String(length=2048), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted', 'rejected_unknown_reference', 'rejected_quote_not_found')",
            name=op.f("ck_ai_citations_status_valid"),
        ),
        sa.CheckConstraint(
            "ref_type IN ('chunk', 'tool')", name=op.f("ck_ai_citations_ref_type_valid")
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_ai_citations_ai_run_id_ai_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_ai_citations_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_ai_citations_chunk_id_document_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_ai_citations_evidence_id_evidence_objects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ai_messages.id"],
            name=op.f("fk_ai_citations_message_id_ai_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_citations")),
    )
    op.create_index("ix_ai_citations_run", "ai_citations", ["ai_run_id"])
    op.create_index("ix_ai_citations_evidence", "ai_citations", ["evidence_id"])

    op.create_table(
        "ai_provider_status",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=16), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=True),
        sa.Column("generation_model", sa.String(length=200), nullable=True),
        sa.Column("generation_model_available", sa.Boolean(), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_model_available", sa.Boolean(), nullable=True),
        sa.Column("embedding_model_digest", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "location IN ('local', 'cloud', 'fixture')",
            name=op.f("ck_ai_provider_status_location_valid"),
        ),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_ai_provider_status")),
    )

    op.add_column("relationships", sa.Column("created_by_ai_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_relationships_created_by_ai_run_id_ai_runs"),
        "relationships",
        "ai_runs",
        ["created_by_ai_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing evidence becomes indexable; the dispatcher schedules it when AI is enabled.
    op.execute(
        "INSERT INTO evidence_index_states (evidence_id, case_id, status) "
        "SELECT id, case_id, 'pending' FROM evidence_objects"
    )


def downgrade() -> None:
    # The pgvector extension is left installed: it is owned by a superuser and harmless.
    op.drop_constraint(
        op.f("fk_relationships_created_by_ai_run_id_ai_runs"), "relationships", type_="foreignkey"
    )
    op.drop_column("relationships", "created_by_ai_run_id")
    op.drop_table("ai_provider_status")
    op.drop_table("ai_citations")
    op.drop_table("ai_messages")
    op.drop_table("ai_runs")
    op.drop_table("ai_conversations")
    op.drop_table("evidence_index_states")
    op.drop_table("chunk_embeddings")
    op.drop_table("document_chunks")
    op.drop_table("embedding_profiles")
    op.execute(
        "DELETE FROM dispatch_outbox WHERE aggregate_type IN ('ai_run', 'case_index',"
        " 'ai_provider_check')"
    )
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion')",
    )
    op.drop_constraint(op.f("ck_cases_ai_mode_valid"), "cases")
    op.drop_column("cases", "ai_policy_version")
    op.drop_column("cases", "ai_mode")
