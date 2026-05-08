"""align report delete foreign keys

Revision ID: 20260507_000019
Revises: 20260505_000018
Create Date: 2026-05-07 00:00:19

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260507_000019"
down_revision = "20260505_000018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_report_versions_report_id", "report_versions", type_="foreignkey")
    op.create_foreign_key(
        "fk_report_versions_report_id",
        "report_versions",
        "reports",
        ["report_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_report_blocks_report_version_id", "report_blocks", type_="foreignkey")
    op.create_foreign_key(
        "fk_report_blocks_report_version_id",
        "report_blocks",
        "report_versions",
        ["report_version_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_exports_report_id", "exports", type_="foreignkey")
    op.create_foreign_key(
        "fk_exports_report_id",
        "exports",
        "reports",
        ["report_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_schedules_report_id", "schedules", type_="foreignkey")
    op.create_foreign_key(
        "fk_schedules_report_id",
        "schedules",
        "reports",
        ["report_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_jobs_schedule_id", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_jobs_schedule_id",
        "jobs",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("fk_jobs_export_id", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_jobs_export_id",
        "jobs",
        "exports",
        ["export_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_export_id", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_jobs_export_id",
        "jobs",
        "exports",
        ["export_id"],
        ["id"],
    )

    op.drop_constraint("fk_jobs_schedule_id", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_jobs_schedule_id",
        "jobs",
        "schedules",
        ["schedule_id"],
        ["id"],
    )

    op.drop_constraint("fk_schedules_report_id", "schedules", type_="foreignkey")
    op.create_foreign_key(
        "fk_schedules_report_id",
        "schedules",
        "reports",
        ["report_id"],
        ["id"],
    )

    op.drop_constraint("fk_exports_report_id", "exports", type_="foreignkey")
    op.create_foreign_key(
        "fk_exports_report_id",
        "exports",
        "reports",
        ["report_id"],
        ["id"],
    )

    op.drop_constraint("fk_report_blocks_report_version_id", "report_blocks", type_="foreignkey")
    op.create_foreign_key(
        "fk_report_blocks_report_version_id",
        "report_blocks",
        "report_versions",
        ["report_version_id"],
        ["id"],
    )

    op.drop_constraint("fk_report_versions_report_id", "report_versions", type_="foreignkey")
    op.create_foreign_key(
        "fk_report_versions_report_id",
        "report_versions",
        "reports",
        ["report_id"],
        ["id"],
    )
