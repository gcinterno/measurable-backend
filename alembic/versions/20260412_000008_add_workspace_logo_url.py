"""add workspace logo url

Revision ID: 20260412_000008
Revises: 20260330_000007
Create Date: 2026-04-12 00:00:08

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260412_000008"
down_revision = "20260330_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("logo_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "logo_url")
