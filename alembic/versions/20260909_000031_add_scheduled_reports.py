"""Persist versioned Schedule Reports without dispatching or altering legacy schedules.

Revision ID: 20260909_000031
Revises: 20260909_000030
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260909_000031"
down_revision = "20260909_000030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    current_revision = sa.ForeignKeyConstraint(
        ["id", "workspace_id", "configuration_revision"],
        ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision"],
        name="fk_scheduled_reports_current_revision", deferrable=True, initially="DEFERRED",
    )
    op.create_table(
        "scheduled_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement="ignore_fk"),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(100)),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("day_of_week", sa.Integer()), sa.Column("day_of_month", sa.Integer()),
        sa.Column("local_time", sa.Time(timezone=False), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("period_policy", sa.String(20), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)), sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "workspace_id", name="uq_scheduled_reports_workspace_identity"),
        sa.CheckConstraint("status IN ('ACTIVE', 'PAUSED', 'BLOCKED', 'ARCHIVED')", name="ck_scheduled_reports_status"),
        sa.CheckConstraint("(status = 'ACTIVE' AND next_run_at IS NOT NULL) OR (status != 'ACTIVE' AND next_run_at IS NULL)", name="ck_scheduled_reports_next_run"),
        sa.CheckConstraint("(status = 'ARCHIVED' AND archived_at IS NOT NULL) OR (status != 'ARCHIVED' AND archived_at IS NULL)", name="ck_scheduled_reports_archived"),
        sa.CheckConstraint("(frequency = 'WEEKLY' AND day_of_week IS NOT NULL AND day_of_week BETWEEN 0 AND 6 AND day_of_month IS NULL AND period_policy = 'previous_week') OR (frequency = 'MONTHLY' AND day_of_month IS NOT NULL AND day_of_month BETWEEN 1 AND 31 AND day_of_week IS NULL AND period_policy = 'previous_month')", name="ck_scheduled_reports_recurrence"),
        *([current_revision] if op.get_bind().dialect.name == "sqlite" else []),
    )
    op.create_table(
        "scheduled_report_revisions",
        sa.Column("schedule_id", sa.Integer(), primary_key=True), sa.Column("workspace_id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schedule_id", "workspace_id"], ["scheduled_reports.id", "scheduled_reports.workspace_id"], name="fk_scheduled_report_revisions_schedule"),
        sa.UniqueConstraint("schedule_id", "workspace_id", "revision", "configuration_hash", name="uq_scheduled_report_revision_hash"),
        sa.CheckConstraint("revision > 0 AND schema_version = 1", name="ck_scheduled_report_revision_version"),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key("fk_scheduled_reports_current_revision", "scheduled_reports", "scheduled_report_revisions",
                              ["id", "workspace_id", "configuration_revision"], ["schedule_id", "workspace_id", "revision"],
                              deferrable=True, initially="DEFERRED")
    op.create_table(
        "scheduled_report_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), nullable=False), sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False), sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integrations.id", ondelete="SET NULL")),
        sa.Column("integration_account_id", sa.Integer(), sa.ForeignKey("integration_accounts.id", ondelete="SET NULL")),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id", ondelete="SET NULL")),
        sa.Column("external_account_id", sa.String(255), nullable=False), sa.Column("label", sa.String(255)),
        sa.ForeignKeyConstraint(["schedule_id", "workspace_id", "configuration_revision"],
                                ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision"], name="fk_scheduled_report_sources_revision"),
        sa.UniqueConstraint("schedule_id", "configuration_revision", "position", name="uq_scheduled_report_source_position"),
        sa.UniqueConstraint("schedule_id", "configuration_revision", "provider", "source_type", "external_account_id", name="uq_scheduled_report_source_identity"),
        sa.CheckConstraint("position >= 0", name="ck_scheduled_report_source_position"),
    )
    op.create_table(
        "scheduled_report_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), nullable=False), sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False), sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("trigger_type", sa.String(20), nullable=False), sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reporting_period_start", sa.DateTime(timezone=True), nullable=False), sa.Column("reporting_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reporting_start_date", sa.Date(), nullable=False), sa.Column("reporting_end_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False), sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="SET NULL")),
        sa.Column("report_version_id", sa.Integer(), sa.ForeignKey("report_versions.id", ondelete="SET NULL")),
        sa.Column("error_code", sa.String(100)), sa.Column("error_detail", sa.Text()),
        sa.Column("retry_after", sa.DateTime(timezone=True)), sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schedule_id", "workspace_id", "configuration_revision", "configuration_hash"],
                                ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision", "scheduled_report_revisions.configuration_hash"], name="fk_scheduled_report_runs_revision"),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_scheduled_report_run_occurrence"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_scheduled_report_run_identity"),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'QUOTA_BLOCKED', 'SKIPPED', 'CANCELLED')", name="ck_scheduled_report_run_status"),
        sa.CheckConstraint("trigger_type IN ('SCHEDULED', 'MANUAL')", name="ck_scheduled_report_run_trigger"),
        sa.CheckConstraint("stage IN ('PENDING', 'REFRESH', 'GENERATE', 'FINALIZE', 'COMPLETE')", name="ck_scheduled_report_run_stage"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduled_report_run_attempts"),
        sa.CheckConstraint("reporting_period_start < reporting_period_end AND reporting_start_date <= reporting_end_date", name="ck_scheduled_report_run_period"),
    )
    indexes = {
        "scheduled_reports": [("workspace_status", ["workspace_id", "status", "id"])],
        "scheduled_report_revisions": [("workspace", ["workspace_id", "schedule_id"])],
        "scheduled_report_sources": [("workspace", ["workspace_id", "schedule_id"]), ("integration", ["integration_id"]), ("account", ["integration_account_id"]), ("dataset", ["dataset_id"])],
        "scheduled_report_runs": [("history", ["workspace_id", "schedule_id", "scheduled_for", "id"]), ("status", ["workspace_id", "status"]), ("report", ["report_id"]), ("version", ["report_version_id"])],
    }
    for table, specs in indexes.items():
        for suffix, columns in specs:
            op.create_index(f"ix_{table}_{suffix}", table, columns)
    op.create_index("ix_scheduled_reports_due", "scheduled_reports", ["next_run_at", "id"],
                    postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))


def downgrade() -> None:
    # Configuration/history is user data. Downgrade is safe before feature use only.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("LOCK TABLE scheduled_reports, scheduled_report_revisions, scheduled_report_sources, scheduled_report_runs IN ACCESS EXCLUSIVE MODE"))
    for table in ("scheduled_reports", "scheduled_report_revisions", "scheduled_report_sources", "scheduled_report_runs"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot discard Schedule Reports configuration/history; roll forward instead.")
    op.drop_table("scheduled_report_runs")
    op.drop_table("scheduled_report_sources")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_scheduled_reports_current_revision", "scheduled_reports", type_="foreignkey")
    op.drop_table("scheduled_report_revisions")
    op.drop_table("scheduled_reports")
