"""add user logo url

Revision ID: 20260413_000009
Revises: 20260412_000008
Create Date: 2026-04-13 00:00:09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260413_000009"
down_revision = "20260412_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("logo_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "logo_url")
