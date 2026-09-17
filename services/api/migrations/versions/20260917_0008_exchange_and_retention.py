"""STIX exchange links, the imported origin, retention policies, jobs and tombstones.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-17

Adds the ``imported`` origin for entities and relationships created from exchange formats, the
table linking imported STIX identifiers to case records, case retention policies (none exist after
the upgrade, so nothing expires), retention jobs and tombstones, the time an execution's results
expired, and why a cited source no longer exists.

Downgrading removes imported entities and relationships (revision 0007 has no origin for them;
the imported bundles stay as evidence), the retention records and the new columns. Evidence already
removed by retention is not restored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_retention_policies",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("collected_results_max_age_days", sa.Integer(), nullable=True),
        sa.Column("imported_evidence_max_age_days", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True),
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
            "collected_results_max_age_days IS NULL OR collected_results_max_age_days >= 1",
            name=op.f("ck_case_retention_policies_collected_age_positive"),
        ),
        sa.CheckConstraint(
            "imported_evidence_max_age_days IS NULL OR imported_evidence_max_age_days >= 1",
            name=op.f("ck_case_retention_policies_imported_age_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_retention_policies_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_retention_policies_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("case_id", name=op.f("pk_case_retention_policies")),
    )
    op.create_table(
        "retention_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("occurrence_key", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "removed", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "deferred", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("progress_note", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_retention_jobs_status_valid"),
        ),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'manual', 'activation')",
            name=op.f("ck_retention_jobs_trigger_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_retention_jobs_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_retention_jobs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_jobs")),
    )
    op.create_index(
        "ix_retention_jobs_case_created", "retention_jobs", ["case_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_retention_jobs_occurrence", "retention_jobs", ["occurrence_key"], unique=True
    )
    op.create_table(
        "retention_tombstones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("retention_job_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(length=32), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "expired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "record_type IN ('evidence', 'relationship_reference', 'query_run_results')",
            name=op.f("ck_retention_tombstones_record_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_retention_tombstones_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["retention_job_id"],
            ["retention_jobs.id"],
            name=op.f("fk_retention_tombstones_retention_job_id_retention_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_tombstones")),
    )
    op.create_index(
        "ix_retention_tombstones_parent",
        "retention_tombstones",
        ["case_id", "parent_id"],
        unique=False,
    )
    op.create_index(
        "uq_retention_tombstones_record",
        "retention_tombstones",
        ["case_id", "record_type", "record_id"],
        unique=True,
    )
    op.create_table(
        "stix_object_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("stix_id", sa.String(length=128), nullable=False),
        sa.Column("record_type", sa.String(length=16), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("first_evidence_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "record_type IN ('entity', 'relationship')",
            name=op.f("ck_stix_object_links_record_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_stix_object_links_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_stix_object_links_first_evidence_id_evidence_objects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stix_object_links")),
    )
    op.create_index(
        "ix_stix_object_links_record",
        "stix_object_links",
        ["case_id", "record_type", "record_id"],
        unique=False,
    )
    op.create_index(
        "uq_stix_object_links_case_stix", "stix_object_links", ["case_id", "stix_id"], unique=True
    )
    op.add_column(
        "ai_citations", sa.Column("source_removed_reason", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "ai_citations", sa.Column("source_removed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "query_runs", sa.Column("results_expired_at", sa.DateTime(timezone=True), nullable=True)
    )
    for table in ("entities", "relationships"):
        op.drop_constraint(op.f(f"ck_{table}_origin_valid"), table)
        op.create_check_constraint(
            op.f(f"ck_{table}_origin_valid"),
            table,
            "origin IN ('observed', 'deterministic_derivation', 'ai_suggestion', 'analyst_assertion', 'imported')",
        )
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check', 'processing_job', 'change_detection',"
        " 'notification_delivery', 'retention_job')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM dispatch_outbox WHERE aggregate_type = 'retention_job'")
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check', 'processing_job', 'change_detection',"
        " 'notification_delivery')",
    )
    op.execute("DELETE FROM relationships WHERE origin = 'imported'")
    op.execute("DELETE FROM entities WHERE origin = 'imported'")
    for table in ("entities", "relationships"):
        op.drop_constraint(op.f(f"ck_{table}_origin_valid"), table)
        op.create_check_constraint(
            op.f(f"ck_{table}_origin_valid"),
            table,
            "origin IN ('observed', 'deterministic_derivation', 'ai_suggestion', 'analyst_assertion')",
        )
    op.drop_column("query_runs", "results_expired_at")
    op.drop_column("ai_citations", "source_removed_at")
    op.drop_column("ai_citations", "source_removed_reason")
    op.drop_index("uq_stix_object_links_case_stix", table_name="stix_object_links")
    op.drop_index("ix_stix_object_links_record", table_name="stix_object_links")
    op.drop_table("stix_object_links")
    op.drop_index("uq_retention_tombstones_record", table_name="retention_tombstones")
    op.drop_index("ix_retention_tombstones_parent", table_name="retention_tombstones")
    op.drop_table("retention_tombstones")
    op.drop_index("uq_retention_jobs_occurrence", table_name="retention_jobs")
    op.drop_index("ix_retention_jobs_case_created", table_name="retention_jobs")
    op.drop_table("retention_jobs")
    op.drop_table("case_retention_policies")
