"""add report shares

Revision ID: 20260523_000026
Revises: 20260522_000025
Create Date: 2026-05-23 00:00:26

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260523_000026"
down_revision = "20260522_000025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_report_shares_report_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_report_shares_workspace_id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_report_shares_created_by_user_id", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_report_shares_report_id", "report_shares", ["report_id"])
    op.create_index("ix_report_shares_workspace_id", "report_shares", ["workspace_id"])
    op.create_index("ix_report_shares_token", "report_shares", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_report_shares_token", table_name="report_shares")
    op.drop_index("ix_report_shares_workspace_id", table_name="report_shares")
    op.drop_index("ix_report_shares_report_id", table_name="report_shares")
    op.drop_table("report_shares")
