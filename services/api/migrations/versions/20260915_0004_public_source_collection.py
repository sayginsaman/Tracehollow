"""Public-source collection: collection provenance, encrypted credentials, source limits.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15

Existing evidence keeps its values: imports and synthetic fixture pages get no collection mode,
and every existing collected page becomes part ``page`` of its page. Downgrading removes
collected evidence rows created by public-source connectors (their files stay on the volume
and are reported by ``reconcile-evidence``) because revision 0003 cannot represent them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence_objects", sa.Column("page_part", sa.String(length=32), nullable=True))
    op.add_column(
        "evidence_objects", sa.Column("collection_mode", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "evidence_objects", sa.Column("access_category", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "evidence_objects", sa.Column("derived_from_evidence_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "evidence_objects",
        sa.Column(
            "collection_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_evidence_objects_derived_from_evidence_id_evidence_objects"),
        "evidence_objects",
        "evidence_objects",
        ["derived_from_evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("UPDATE evidence_objects SET page_part = 'page' WHERE connector_run_id IS NOT NULL")
    op.drop_index(
        "uq_evidence_objects_connector_page",
        table_name="evidence_objects",
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_evidence_objects_connector_page_part",
        "evidence_objects",
        ["connector_run_id", "page_index", "page_part"],
        unique=True,
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_evidence_objects_derived_from", "evidence_objects", ["derived_from_evidence_id"]
    )
    op.drop_constraint(op.f("ck_evidence_objects_kind_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_kind_valid"),
        "evidence_objects",
        "kind IN ('text', 'json', 'html', 'xml')",
    )
    op.drop_constraint(op.f("ck_evidence_objects_acquisition_method_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_acquisition_method_valid"),
        "evidence_objects",
        "acquisition_method IN ('authorized_import', 'synthetic_fixture', 'connector_collection')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_objects_collection_mode_valid"),
        "evidence_objects",
        "collection_mode IS NULL OR collection_mode IN "
        "('direct_request', 'third_party_api', 'platform_probe')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_objects_access_category_valid"),
        "evidence_objects",
        "access_category IS NULL OR access_category IN ('public', 'credentialed')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_objects_collection_requires_provenance"),
        "evidence_objects",
        "acquisition_method <> 'connector_collection' OR "
        "(collection_mode IS NOT NULL AND access_category IS NOT NULL "
        "AND connector_id IS NOT NULL)",
    )

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=32), nullable=True),
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
            "octet_length(nonce) = 12", name=op.f("ck_integration_credentials_nonce_length")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_integration_credentials_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_credentials")),
    )
    op.create_index(
        "uq_integration_credentials_connector_name",
        "integration_credentials",
        ["connector_id", "name"],
        unique=True,
    )
    op.create_table(
        "source_slots",
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("holder", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("connector_id", "slot", name=op.f("pk_source_slots")),
    )
    op.create_table(
        "source_pacing",
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_source_pacing")),
    )


def downgrade() -> None:
    op.drop_table("source_pacing")
    op.drop_table("source_slots")
    op.drop_index("uq_integration_credentials_connector_name", table_name="integration_credentials")
    op.drop_table("integration_credentials")
    # Revision 0003 cannot represent evidence collected by public-source connectors.
    op.execute("DELETE FROM evidence_objects WHERE acquisition_method = 'connector_collection'")
    op.drop_constraint(
        op.f("ck_evidence_objects_collection_requires_provenance"), "evidence_objects"
    )
    op.drop_constraint(op.f("ck_evidence_objects_access_category_valid"), "evidence_objects")
    op.drop_constraint(op.f("ck_evidence_objects_collection_mode_valid"), "evidence_objects")
    op.drop_constraint(op.f("ck_evidence_objects_acquisition_method_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_acquisition_method_valid"),
        "evidence_objects",
        "acquisition_method IN ('authorized_import', 'synthetic_fixture')",
    )
    op.drop_constraint(op.f("ck_evidence_objects_kind_valid"), "evidence_objects")
    op.create_check_constraint(
        op.f("ck_evidence_objects_kind_valid"), "evidence_objects", "kind IN ('text', 'json')"
    )
    op.drop_index("ix_evidence_objects_derived_from", table_name="evidence_objects")
    op.drop_index(
        "uq_evidence_objects_connector_page_part",
        table_name="evidence_objects",
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_evidence_objects_connector_page",
        "evidence_objects",
        ["connector_run_id", "page_index"],
        unique=True,
        postgresql_where=sa.text("connector_run_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_evidence_objects_derived_from_evidence_id_evidence_objects"),
        "evidence_objects",
        type_="foreignkey",
    )
    op.drop_column("evidence_objects", "collection_metadata")
    op.drop_column("evidence_objects", "derived_from_evidence_id")
    op.drop_column("evidence_objects", "access_category")
    op.drop_column("evidence_objects", "collection_mode")
    op.drop_column("evidence_objects", "page_part")
