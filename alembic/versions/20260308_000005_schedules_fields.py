"""schedules fields

Revision ID: 20260308_000005
Revises: 20260308_000004
Create Date: 2026-03-08 00:00:05

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260308_000005"
down_revision = "20260308_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("integration_id", sa.Integer()))
    op.add_column("schedules", sa.Column("freq", sa.String(length=50), nullable=False, server_default="monthly"))
    op.add_column("schedules", sa.Column("day_of_month", sa.Integer()))
    op.add_column("schedules", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"))
    op.add_column("schedules", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))

    op.create_index("ix_schedules_integration_id", "schedules", ["integration_id"])
    op.create_foreign_key(
        "fk_schedules_integration_id",
        "schedules",
        "integrations",
        ["integration_id"],
        ["id"],
    )

    op.alter_column("schedules", "report_id", nullable=True)
    op.drop_column("schedules", "cron")
    op.drop_column("schedules", "is_active")


def downgrade() -> None:
    op.add_column("schedules", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("schedules", sa.Column("cron", sa.String(length=100), nullable=False, server_default=""))
    op.alter_column("schedules", "report_id", nullable=False)

    op.drop_constraint("fk_schedules_integration_id", "schedules", type_="foreignkey")
    op.drop_index("ix_schedules_integration_id", table_name="schedules")

    op.drop_column("schedules", "enabled")
    op.drop_column("schedules", "timezone")
    op.drop_column("schedules", "day_of_month")
    op.drop_column("schedules", "freq")
    op.drop_column("schedules", "integration_id")
