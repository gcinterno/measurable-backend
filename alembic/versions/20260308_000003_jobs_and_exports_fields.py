"""jobs and exports fields

Revision ID: 20260308_000003
Revises: 20260308_000002
Create Date: 2026-03-08 00:00:03

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260308_000003"
down_revision = "20260308_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exports", sa.Column("download_key", sa.String(length=1024)))

    op.add_column(
        "jobs",
        sa.Column("type", sa.String(length=50), nullable=False, server_default="sync_integration"),
    )
    op.add_column("jobs", sa.Column("payload_json", sa.Text()))
    op.create_index("ix_jobs_type", "jobs", ["type"])


def downgrade() -> None:
    op.drop_index("ix_jobs_type", table_name="jobs")
    op.drop_column("jobs", "payload_json")
    op.drop_column("jobs", "type")

    op.drop_column("exports", "download_key")
