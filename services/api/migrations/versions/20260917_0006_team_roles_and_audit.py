"""Team roles, case membership roles and the audit trail.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-17

Existing accounts keep what they could do: administrators become ``administrator`` accounts and
every other account becomes an ``analyst`` account. Every existing membership (``owner``, the only
Phase 1-4 role) becomes ``analyst``, so no account loses access to a case it could open.

Downgrading maps administrator accounts back to ``is_admin``, turns every membership (analyst or
viewer) back into ``owner`` (the only role revision 0005 knows, which grants full access to former
viewers) and removes the audit trail.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), server_default="analyst", nullable=False),
    )
    op.execute("UPDATE users SET role = CASE WHEN is_admin THEN 'administrator' ELSE 'analyst' END")
    op.drop_column("users", "is_admin")
    op.create_check_constraint(
        op.f("ck_users_role_valid"), "users", "role IN ('administrator', 'analyst', 'viewer')"
    )

    op.drop_constraint(op.f("ck_case_members_role_valid"), "case_members")
    op.execute("UPDATE case_members SET role = 'analyst' WHERE role = 'owner'")
    op.create_check_constraint(
        op.f("ck_case_members_role_valid"), "case_members", "role IN ('analyst', 'viewer')"
    )
    op.add_column("case_members", sa.Column("added_by_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_case_members_added_by_user_id_users"),
        "case_members",
        "users",
        ["added_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "case_members",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service', 'system')",
            name=op.f("ck_audit_events_actor_type_valid"),
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name=op.f("ck_audit_events_outcome_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_case_occurred", "audit_events", ["case_id", "occurred_at"])
    op.create_index(
        "ix_audit_events_actor_occurred", "audit_events", ["actor_user_id", "occurred_at"]
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_case_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_column("case_members", "updated_at")
    op.drop_constraint(
        op.f("fk_case_members_added_by_user_id_users"), "case_members", type_="foreignkey"
    )
    op.drop_column("case_members", "added_by_user_id")
    op.drop_constraint(op.f("ck_case_members_role_valid"), "case_members")
    op.execute("UPDATE case_members SET role = 'owner'")
    op.create_check_constraint(
        op.f("ck_case_members_role_valid"), "case_members", "role IN ('owner')"
    )

    op.drop_constraint(op.f("ck_users_role_valid"), "users")
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False)
    )
    op.execute("UPDATE users SET is_admin = (role = 'administrator')")
    op.drop_column("users", "role")
