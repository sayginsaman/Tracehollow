"""Authorized imports with processing: binary evidence kinds and durable processing jobs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-16

Existing evidence is unchanged. Downgrading removes binary evidence rows (PDF, archive and
attachment originals; their files stay on the volume and are reported by ``reconcile-evidence``)
because revision 0004 cannot represent them, removes processing jobs and their outbox rows, and
keeps text derived by those jobs as ordinary imported evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_TYPES = "job_type IN ('whatsapp_export', 'document_text')"
_STATUSES = (
    "status IN ('queued', 'running', 'needs_input', 'completed', 'partial', 'failed', 'canceled')"
)


def upgrade() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("needs_input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_name", sa.String(length=255), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_JOB_TYPES, name=op.f("ck_processing_jobs_job_type_valid")),
        sa.CheckConstraint(_STATUSES, name=op.f("ck_processing_jobs_status_valid")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_processing_jobs_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_processing_jobs_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_objects.id"],
            name=op.f("fk_processing_jobs_evidence_id_evidence_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_processing_jobs_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
    )
    op.create_index(
        "uq_processing_jobs_active",
        "processing_jobs",
        ["evidence_id", "job_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'needs_input')"),
    )
    op.create_index(
        "ix_processing_jobs_case_created", "processing_jobs", ["case_id", "created_at"]
    )
    op.create_index(
        "ix_processing_jobs_status_lease", "processing_jobs", ["status", "lease_expires_at"]
    )

    op.add_column("evidence_objects", sa.Column("processing_job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_evidence_objects_processing_job_id_processing_jobs"),
        "evidence_objects",
        "processing_jobs",
        ["processing_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_evidence_objects_processing_part",
        "evidence_objects",
        ["processing_job_id", "page_part"],
        unique=True,
        postgresql_where=sa.text("processing_job_id IS NOT NULL"),
    )
    op.drop_constraint(op.f("ck_evidence_objects_kind_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_kind_valid"),
        "evidence_objects",
        "kind IN ('text', 'json', 'html', 'xml', 'pdf', 'archive', 'binary')",
    )

    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check', 'processing_job')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM dispatch_outbox WHERE aggregate_type = 'processing_job'")
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check')",
    )
    # Revision 0004 cannot represent binary evidence.
    op.execute("DELETE FROM evidence_objects WHERE kind IN ('pdf', 'archive', 'binary')")
    op.drop_constraint(op.f("ck_evidence_objects_kind_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_kind_valid"),
        "evidence_objects",
        "kind IN ('text', 'json', 'html', 'xml')",
    )
    op.drop_index(
        "uq_evidence_objects_processing_part",
        table_name="evidence_objects",
        postgresql_where=sa.text("processing_job_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_evidence_objects_processing_job_id_processing_jobs"),
        "evidence_objects",
        type_="foreignkey",
    )
    op.drop_column("evidence_objects", "processing_job_id")
    op.drop_index("ix_processing_jobs_status_lease", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_case_created", table_name="processing_jobs")
    op.drop_index(
        "uq_processing_jobs_active",
        table_name="processing_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running', 'needs_input')"),
    )
    op.drop_table("processing_jobs")
