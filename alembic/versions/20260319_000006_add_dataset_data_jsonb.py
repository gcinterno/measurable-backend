"""add dataset data jsonb

Revision ID: 20260319_000006
Revises: 20260308_000005
Create Date: 2026-03-19 00:00:06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260319_000006"
down_revision = "20260308_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "data")
