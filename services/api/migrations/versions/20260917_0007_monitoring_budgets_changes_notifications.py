"""Monitors, budgets, change detection and notifications.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-17

Adds scheduled monitors with their occurrences, case and monitor budgets with ledgers and
reservations, change sets and change events, in-app notifications, webhook destinations,
subscriptions and deliveries, and links query runs to the monitor that started them. Existing
records are unchanged: no monitor, budget or destination exists after the upgrade.

Downgrading removes all of these records (and their outbox rows); query runs started by monitors
are kept as ordinary executions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_destinations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="webhook", nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("event_types", sa.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("secret_key_id", sa.String(length=16), nullable=True),
        sa.Column("max_per_minute", sa.Integer(), server_default="30", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
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
            "kind = 'webhook'", name=op.f("ck_notification_destinations_kind_valid")
        ),
        sa.CheckConstraint(
            "max_per_minute BETWEEN 1 AND 600", name=op.f("ck_notification_destinations_rate_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_notification_destinations_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_notification_destinations_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_destinations")),
    )
    op.create_table(
        "budget_ledgers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("overage_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("denied_requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
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
            "metric IN ('requests', 'provider_units')", name=op.f("ck_budget_ledgers_metric_valid")
        ),
        sa.CheckConstraint(
            "period IN ('day', 'week', 'month', 'run')", name=op.f("ck_budget_ledgers_period_valid")
        ),
        sa.CheckConstraint(
            "scope_type IN ('case', 'monitor', 'query_run')",
            name=op.f("ck_budget_ledgers_scope_type_valid"),
        ),
        sa.CheckConstraint(
            "reserved_units >= 0 AND consumed_units >= 0 AND estimated_units >= 0 AND overage_units >= 0 AND limit_units >= 0",
            name=op.f("ck_budget_ledgers_units_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_budget_ledgers_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_ledgers")),
    )
    op.create_index(
        "ix_budget_ledgers_case", "budget_ledgers", ["case_id", "period_start"], unique=False
    )
    op.create_index(
        "uq_budget_ledgers_scope_period",
        "budget_ledgers",
        ["scope_type", "scope_id", "metric", "period_start"],
        unique=True,
    )
    op.create_table(
        "case_budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
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
            "metric IN ('requests', 'provider_units')", name=op.f("ck_case_budgets_metric_valid")
        ),
        sa.CheckConstraint(
            "period IN ('day', 'week', 'month')", name=op.f("ck_case_budgets_period_valid")
        ),
        sa.CheckConstraint("limit_units >= 0", name=op.f("ck_case_budgets_limit_non_negative")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_budgets_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_budgets_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_budgets")),
    )
    op.create_index(
        "uq_case_budgets_case_metric_period",
        "case_budgets",
        ["case_id", "metric", "period"],
        unique=True,
    )
    op.create_table(
        "monitors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("saved_query_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "schedule_anchor",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("missed_run_policy", sa.String(length=16), nullable=False),
        sa.Column("connector_ids", sa.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retention", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notify", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("query_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("config_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("authorized_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
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
            "missed_run_policy IN ('run_latest', 'skip')",
            name=op.f("ck_monitors_missed_run_policy_valid"),
        ),
        sa.CheckConstraint(
            "status = 'enabled' OR next_run_at IS NULL",
            name=op.f("ck_monitors_only_enabled_monitors_are_due"),
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'paused', 'disabled')", name=op.f("ck_monitors_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["authorized_by_user_id"],
            ["users.id"],
            name=op.f("fk_monitors_authorized_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_monitors_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_monitors_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["saved_query_id"],
            ["saved_queries.id"],
            name=op.f("fk_monitors_saved_query_id_saved_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitors")),
    )
    op.create_index("ix_monitors_case_id", "monitors", ["case_id"], unique=False)
    op.create_index("ix_monitors_due", "monitors", ["status", "next_run_at"], unique=False)
    op.create_index("ix_monitors_saved_query_id", "monitors", ["saved_query_id"], unique=False)
    op.create_table(
        "monitor_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("event_types", sa.ARRAY(sa.String(length=32)), nullable=False),
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
            name=op.f("fk_monitor_subscriptions_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_monitor_subscriptions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["notification_destinations.id"],
            name=op.f("fk_monitor_subscriptions_destination_id_notification_destinations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_monitor_subscriptions_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitor_subscriptions")),
    )
    op.create_index(
        "uq_monitor_subscriptions_monitor_destination",
        "monitor_subscriptions",
        ["monitor_id", "destination_id"],
        unique=True,
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("monitor_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('change_detected', 'action_required', 'budget_exhausted', 'run_completed')",
            name=op.f("ck_notifications_event_type_valid"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'action_required')",
            name=op.f("ck_notifications_severity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_notifications_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_notifications_monitor_id_monitors"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index("ix_notifications_case", "notifications", ["case_id"], unique=False)
    op.create_index(
        "ix_notifications_user_created", "notifications", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_notifications_user_dedupe", "notifications", ["user_id", "dedupe_key"], unique=True
    )
    op.create_table(
        "monitor_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("skip_reason", sa.String(length=64), nullable=True),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("missed_slots", sa.Integer(), server_default="0", nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dispatched_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('scheduled', 'manual')", name=op.f("ck_monitor_occurrences_kind_valid")
        ),
        sa.CheckConstraint(
            "status = 'dispatched' OR query_run_id IS NULL",
            name=op.f("ck_monitor_occurrences_skipped_has_no_run"),
        ),
        sa.CheckConstraint(
            "status IN ('dispatched', 'skipped')", name=op.f("ck_monitor_occurrences_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_monitor_occurrences_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_monitor_occurrences_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_monitor_occurrences_query_run_id_query_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitor_occurrences")),
    )
    op.create_index(
        "ix_monitor_occurrences_case_created",
        "monitor_occurrences",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_monitor_occurrences_query_run",
        "monitor_occurrences",
        ["query_run_id"],
        unique=True,
        postgresql_where=sa.text("query_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_monitor_occurrences_slot",
        "monitor_occurrences",
        ["monitor_id", "scheduled_for"],
        unique=True,
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_response_status", sa.Integer(), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('change_detected', 'action_required', 'budget_exhausted', 'run_completed')",
            name=op.f("ck_notification_deliveries_event_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'blocked')",
            name=op.f("ck_notification_deliveries_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_notification_deliveries_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["notification_destinations.id"],
            name=op.f("fk_notification_deliveries_destination_id_notification_destinations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["monitor_subscriptions.id"],
            name=op.f("fk_notification_deliveries_subscription_id_monitor_subscriptions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
    )
    op.create_index(
        "ix_notification_deliveries_case", "notification_deliveries", ["case_id"], unique=False
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "uq_notification_deliveries_event",
        "notification_deliveries",
        ["destination_id", "event_id"],
        unique=True,
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ledger_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("connector_run_id", sa.Uuid(), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("estimated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("settled_units", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('held', 'settled', 'released', 'expired')",
            name=op.f("ck_budget_reservations_status_valid"),
        ),
        sa.CheckConstraint("units >= 0", name=op.f("ck_budget_reservations_units_non_negative")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_budget_reservations_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_run_id"],
            ["connector_runs.id"],
            name=op.f("fk_budget_reservations_connector_run_id_connector_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_id"],
            ["budget_ledgers.id"],
            name=op.f("fk_budget_reservations_ledger_id_budget_ledgers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_budget_reservations_query_run_id_query_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_reservations")),
    )
    op.create_index(
        "ix_budget_reservations_held", "budget_reservations", ["status", "expires_at"], unique=False
    )
    op.create_index(
        "ix_budget_reservations_run", "budget_reservations", ["query_run_id"], unique=False
    )
    op.create_index(
        "ix_budget_reservations_ticket", "budget_reservations", ["ticket_id"], unique=False
    )
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=True),
        sa.Column("occurrence_id", sa.Uuid(), nullable=True),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("connector_run_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("connector_version", sa.String(length=32), nullable=False),
        sa.Column("baseline_query_run_id", sa.Uuid(), nullable=True),
        sa.Column("baseline_connector_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("coverage_complete", sa.Boolean(), nullable=False),
        sa.Column("baseline_coverage_complete", sa.Boolean(), nullable=True),
        sa.Column(
            "counts", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('baseline_established', 'no_meaningful_change', 'changes_detected', 'unknown', 'baseline_incompatible')",
            name=op.f("ck_change_sets_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["baseline_connector_run_id"],
            ["connector_runs.id"],
            name=op.f("fk_change_sets_baseline_connector_run_id_connector_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_change_sets_baseline_query_run_id_query_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_change_sets_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connector_run_id"],
            ["connector_runs.id"],
            name=op.f("fk_change_sets_connector_run_id_connector_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_change_sets_monitor_id_monitors"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["monitor_occurrences.id"],
            name=op.f("fk_change_sets_occurrence_id_monitor_occurrences"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id"],
            ["query_runs.id"],
            name=op.f("fk_change_sets_query_run_id_query_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_sets")),
    )
    op.create_index(
        "ix_change_sets_case_created", "change_sets", ["case_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_change_sets_monitor_created", "change_sets", ["monitor_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_change_sets_connector_run", "change_sets", ["connector_run_id"], unique=True
    )
    op.create_table(
        "change_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("observation_type", sa.String(length=64), nullable=False),
        sa.Column("source_object_id", sa.String(length=512), nullable=True),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("previous_observation_id", sa.Uuid(), nullable=True),
        sa.Column("current_observation_id", sa.Uuid(), nullable=True),
        sa.Column("previous_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("current_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('new', 'changed', 'not_observed', 'conflicting', 'unknown')",
            name=op.f("ck_change_events_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_change_events_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_change_events_change_set_id_change_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_events")),
    )
    op.create_index(
        "ix_change_events_case_created", "change_events", ["case_id", "created_at"], unique=False
    )
    op.create_index("ix_change_events_change_set", "change_events", ["change_set_id"], unique=False)
    op.add_column("query_runs", sa.Column("monitor_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_query_runs_monitor", "query_runs", ["monitor_id", "queued_at"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_query_runs_monitor_id_monitors"),
        "query_runs",
        "monitors",
        ["monitor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check', 'processing_job', 'change_detection',"
        " 'notification_delivery')",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM dispatch_outbox WHERE aggregate_type IN "
        "('change_detection', 'notification_delivery')"
    )
    op.drop_constraint(op.f("ck_dispatch_outbox_aggregate_type_valid"), "dispatch_outbox")
    op.create_check_constraint(
        op.f("ck_dispatch_outbox_aggregate_type_valid"),
        "dispatch_outbox",
        "aggregate_type IN ('query_run', 'case_deletion', 'ai_run', 'case_index',"
        " 'ai_provider_check', 'processing_job')",
    )
    op.drop_constraint(op.f("fk_query_runs_monitor_id_monitors"), "query_runs", type_="foreignkey")
    op.drop_index("ix_query_runs_monitor", table_name="query_runs")
    op.drop_column("query_runs", "monitor_id")
    op.drop_index("ix_change_events_change_set", table_name="change_events")
    op.drop_index("ix_change_events_case_created", table_name="change_events")
    op.drop_table("change_events")
    op.drop_index("uq_change_sets_connector_run", table_name="change_sets")
    op.drop_index("ix_change_sets_monitor_created", table_name="change_sets")
    op.drop_index("ix_change_sets_case_created", table_name="change_sets")
    op.drop_table("change_sets")
    op.drop_index("ix_budget_reservations_ticket", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_run", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_held", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_index("uq_notification_deliveries_event", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_due", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_case", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("uq_monitor_occurrences_slot", table_name="monitor_occurrences")
    op.drop_index(
        "uq_monitor_occurrences_query_run",
        table_name="monitor_occurrences",
        postgresql_where=sa.text("query_run_id IS NOT NULL"),
    )
    op.drop_index("ix_monitor_occurrences_case_created", table_name="monitor_occurrences")
    op.drop_table("monitor_occurrences")
    op.drop_index("uq_notifications_user_dedupe", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_index("ix_notifications_case", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(
        "uq_monitor_subscriptions_monitor_destination", table_name="monitor_subscriptions"
    )
    op.drop_table("monitor_subscriptions")
    op.drop_index("ix_monitors_saved_query_id", table_name="monitors")
    op.drop_index("ix_monitors_due", table_name="monitors")
    op.drop_index("ix_monitors_case_id", table_name="monitors")
    op.drop_table("monitors")
    op.drop_index("uq_case_budgets_case_metric_period", table_name="case_budgets")
    op.drop_table("case_budgets")
    op.drop_index("uq_budget_ledgers_scope_period", table_name="budget_ledgers")
    op.drop_index("ix_budget_ledgers_case", table_name="budget_ledgers")
    op.drop_table("budget_ledgers")
    op.drop_table("notification_destinations")
