"""add report sources

Revision ID: 20260511_000020
Revises: 20260507_000019
Create Date: 2026-05-11 00:00:20

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260511_000020"
down_revision = "20260507_000019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("integration_account_id", sa.Integer(), nullable=True),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_report_sources_report_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_report_sources_workspace_id"),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], name="fk_report_sources_integration_id"),
        sa.ForeignKeyConstraint(
            ["integration_account_id"],
            ["integration_accounts.id"],
            name="fk_report_sources_integration_account_id",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_report_sources_dataset_id"),
        sa.UniqueConstraint("report_id", "position", name="uq_report_sources_report_position"),
    )
    op.create_index("ix_report_sources_report_id", "report_sources", ["report_id"])
    op.create_index("ix_report_sources_workspace_id", "report_sources", ["workspace_id"])
    op.create_index("ix_report_sources_integration_id", "report_sources", ["integration_id"])
    op.create_index("ix_report_sources_dataset_id", "report_sources", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_report_sources_dataset_id", table_name="report_sources")
    op.drop_index("ix_report_sources_integration_id", table_name="report_sources")
    op.drop_index("ix_report_sources_workspace_id", table_name="report_sources")
    op.drop_index("ix_report_sources_report_id", table_name="report_sources")
    op.drop_table("report_sources")
