"""add report templates

Revision ID: 20260901_000029
Revises: 20260616_000028
Create Date: 2026-09-01 00:00:29

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260901_000029"
down_revision = "20260616_000028"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="draft"),
        sa.Column("generation_mode", sa.String(length=50), nullable=False, server_default="manual_template"),
        sa.Column("template_type", sa.String(length=100), nullable=False, server_default="custom"),
        sa.Column("datasource_requirements", _jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("active_version_id", sa.Integer(), nullable=True),
        sa.Column("published_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", _jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_report_templates_workspace_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_report_templates_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_report_templates_workspace_slug"),
    )
    op.create_index("ix_report_templates_workspace_id", "report_templates", ["workspace_id"])
    op.create_index("ix_report_templates_status", "report_templates", ["status"])
    op.create_index("ix_report_templates_template_type", "report_templates", ["template_type"])
    op.create_index("ix_report_templates_published_version_id", "report_templates", ["published_version_id"])

    op.create_table(
        "report_template_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_template_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("spec_json", _jsonb(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["report_template_id"],
            ["report_templates.id"],
            name="fk_report_template_versions_report_template_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_report_template_versions_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("report_template_id", "version_number", name="uq_report_template_versions_number"),
    )
    op.create_index(
        "ix_report_template_versions_report_template_id",
        "report_template_versions",
        ["report_template_id"],
    )
    op.create_index("ix_report_template_versions_schema_version", "report_template_versions", ["schema_version"])
    op.create_index("ix_report_template_versions_published_at", "report_template_versions", ["published_at"])

    op.add_column("reports", sa.Column("report_template_id", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("report_template_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_reports_report_template_id",
        "reports",
        "report_templates",
        ["report_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_reports_report_template_version_id",
        "reports",
        "report_template_versions",
        ["report_template_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_reports_report_template_id", "reports", ["report_template_id"])
    op.create_index("ix_reports_report_template_version_id", "reports", ["report_template_version_id"])


def downgrade() -> None:
    op.drop_index("ix_reports_report_template_version_id", table_name="reports")
    op.drop_index("ix_reports_report_template_id", table_name="reports")
    op.drop_constraint("fk_reports_report_template_version_id", "reports", type_="foreignkey")
    op.drop_constraint("fk_reports_report_template_id", "reports", type_="foreignkey")
    op.drop_column("reports", "report_template_version_id")
    op.drop_column("reports", "report_template_id")

    op.drop_index("ix_report_template_versions_published_at", table_name="report_template_versions")
    op.drop_index("ix_report_template_versions_schema_version", table_name="report_template_versions")
    op.drop_index("ix_report_template_versions_report_template_id", table_name="report_template_versions")
    op.drop_table("report_template_versions")

    op.drop_index("ix_report_templates_published_version_id", table_name="report_templates")
    op.drop_index("ix_report_templates_template_type", table_name="report_templates")
    op.drop_index("ix_report_templates_status", table_name="report_templates")
    op.drop_index("ix_report_templates_workspace_id", table_name="report_templates")
    op.drop_table("report_templates")
