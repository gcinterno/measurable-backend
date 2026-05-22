"""add user suggestions

Revision ID: 20260521_000022
Revises: 20260516_000021
Create Date: 2026-05-21 00:00:22

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260521_000022"
down_revision = "20260516_000021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="new"),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="floating_suggestion_button"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_suggestions_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_user_suggestions_workspace_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], name="fk_user_suggestions_reviewed_by", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_user_suggestions_user_id", "user_suggestions", ["user_id"])
    op.create_index("ix_user_suggestions_workspace_id", "user_suggestions", ["workspace_id"])
    op.create_index("ix_user_suggestions_created_at", "user_suggestions", ["created_at"])
    op.create_index("ix_user_suggestions_status", "user_suggestions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_user_suggestions_status", table_name="user_suggestions")
    op.drop_index("ix_user_suggestions_created_at", table_name="user_suggestions")
    op.drop_index("ix_user_suggestions_workspace_id", table_name="user_suggestions")
    op.drop_index("ix_user_suggestions_user_id", table_name="user_suggestions")
    op.drop_table("user_suggestions")
