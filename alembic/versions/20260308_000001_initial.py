"""initial

Revision ID: 20260308_000001
Revises: 
Create Date: 2026-03-08 00:00:01

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260308_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_workspace_members_workspace_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_workspace_members_user_id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_subscriptions_workspace_id"),
    )
    op.create_index("ix_subscriptions_workspace_id", "subscriptions", ["workspace_id"])

    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_datasets_workspace_id"),
    )
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])

    op.create_table(
        "dataset_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("content_type", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_dataset_files_dataset_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_dataset_files_workspace_id"),
    )
    op.create_index("ix_dataset_files_dataset_id", "dataset_files", ["dataset_id"])
    op.create_index("ix_dataset_files_workspace_id", "dataset_files", ["workspace_id"])

    op.create_table(
        "integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_integrations_workspace_id"),
    )
    op.create_index("ix_integrations_workspace_id", "integrations", ["workspace_id"])

    op.create_table(
        "integration_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["integrations.id"], name="fk_integration_accounts_integration_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_integration_accounts_workspace_id"
        ),
        sa.UniqueConstraint("integration_id", "external_account_id", name="uq_integration_account"),
    )
    op.create_index("ix_integration_accounts_integration_id", "integration_accounts", ["integration_id"])
    op.create_index("ix_integration_accounts_workspace_id", "integration_accounts", ["workspace_id"])

    op.create_table(
        "integration_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("token_type", sa.String(length=50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["integration_accounts.id"], name="fk_integration_tokens_account_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_integration_tokens_workspace_id"
        ),
    )
    op.create_index("ix_integration_tokens_account_id", "integration_tokens", ["account_id"])
    op.create_index("ix_integration_tokens_workspace_id", "integration_tokens", ["workspace_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_reports_workspace_id"),
    )
    op.create_index("ix_reports_workspace_id", "reports", ["workspace_id"])

    op.create_table(
        "report_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_report_versions_report_id"),
        sa.UniqueConstraint("report_id", "version", name="uq_report_version"),
    )
    op.create_index("ix_report_versions_report_id", "report_versions", ["report_id"])

    op.create_table(
        "report_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column("config_json", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["report_version_id"], ["report_versions.id"], name="fk_report_blocks_report_version_id"
        ),
    )
    op.create_index("ix_report_blocks_report_version_id", "report_blocks", ["report_version_id"])

    op.create_table(
        "exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer()),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("output_s3_key", sa.String(length=1024)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_exports_workspace_id"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_exports_report_id"),
    )
    op.create_index("ix_exports_workspace_id", "exports", ["workspace_id"])
    op.create_index("ix_exports_report_id", "exports", ["report_id"])

    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("cron", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_schedules_workspace_id"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_schedules_report_id"),
    )
    op.create_index("ix_schedules_workspace_id", "schedules", ["workspace_id"])
    op.create_index("ix_schedules_report_id", "schedules", ["report_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer()),
        sa.Column("export_id", sa.Integer()),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_jobs_workspace_id"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], name="fk_jobs_schedule_id"),
        sa.ForeignKeyConstraint(["export_id"], ["exports.id"], name="fk_jobs_export_id"),
    )
    op.create_index("ix_jobs_workspace_id", "jobs", ["workspace_id"])
    op.create_index("ix_jobs_schedule_id", "jobs", ["schedule_id"])
    op.create_index("ix_jobs_export_id", "jobs", ["export_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_audit_logs_workspace_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_audit_logs_user_id"),
    )
    op.create_index("ix_audit_logs_workspace_id", "audit_logs", ["workspace_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])



def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_workspace_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_jobs_export_id", table_name="jobs")
    op.drop_index("ix_jobs_schedule_id", table_name="jobs")
    op.drop_index("ix_jobs_workspace_id", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_schedules_report_id", table_name="schedules")
    op.drop_index("ix_schedules_workspace_id", table_name="schedules")
    op.drop_table("schedules")

    op.drop_index("ix_exports_report_id", table_name="exports")
    op.drop_index("ix_exports_workspace_id", table_name="exports")
    op.drop_table("exports")

    op.drop_index("ix_report_blocks_report_version_id", table_name="report_blocks")
    op.drop_table("report_blocks")

    op.drop_index("ix_report_versions_report_id", table_name="report_versions")
    op.drop_table("report_versions")

    op.drop_index("ix_reports_workspace_id", table_name="reports")
    op.drop_table("reports")

    op.drop_index("ix_integration_tokens_workspace_id", table_name="integration_tokens")
    op.drop_index("ix_integration_tokens_account_id", table_name="integration_tokens")
    op.drop_table("integration_tokens")

    op.drop_index("ix_integration_accounts_workspace_id", table_name="integration_accounts")
    op.drop_index("ix_integration_accounts_integration_id", table_name="integration_accounts")
    op.drop_table("integration_accounts")

    op.drop_index("ix_integrations_workspace_id", table_name="integrations")
    op.drop_table("integrations")

    op.drop_index("ix_dataset_files_workspace_id", table_name="dataset_files")
    op.drop_index("ix_dataset_files_dataset_id", table_name="dataset_files")
    op.drop_table("dataset_files")

    op.drop_index("ix_datasets_workspace_id", table_name="datasets")
    op.drop_table("datasets")

    op.drop_index("ix_subscriptions_workspace_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_index("ix_workspace_members_workspace_id", table_name="workspace_members")
    op.drop_table("workspace_members")

    op.drop_table("workspaces")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
