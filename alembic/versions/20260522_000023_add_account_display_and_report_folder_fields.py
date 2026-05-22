"""add account display name and report folder fields

Revision ID: 20260522_000023
Revises: 20260521_000022
Create Date: 2026-05-22 00:00:23

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260522_000023"
down_revision = "20260521_000022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("account_display_name", sa.String(length=255), nullable=True))
    op.add_column("reports", sa.Column("folder_id", sa.String(length=255), nullable=True))
    op.add_column("reports", sa.Column("folder_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "folder_name")
    op.drop_column("reports", "folder_id")
    op.drop_column("workspaces", "account_display_name")
