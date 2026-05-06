"""add account deletion soft delete

Revision ID: 20260502_000016
Revises: 20260502_000015
Create Date: 2026-05-02 00:00:16

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260502_000016"
down_revision = "20260502_000015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.create_table(
        "account_deletion_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=50)),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_account_deletion_feedback_user_id", ondelete="SET NULL"),
    )
    op.create_index(
        "ix_account_deletion_feedback_user_id",
        "account_deletion_feedback",
        ["user_id"],
    )
    op.create_index(
        "ix_account_deletion_feedback_created_at",
        "account_deletion_feedback",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_deletion_feedback_created_at", table_name="account_deletion_feedback")
    op.drop_index("ix_account_deletion_feedback_user_id", table_name="account_deletion_feedback")
    op.drop_table("account_deletion_feedback")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_deleted")
