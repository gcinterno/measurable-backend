"""reports and blocks fields

Revision ID: 20260308_000004
Revises: 20260308_000003
Create Date: 2026-03-08 00:00:04

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260308_000004"
down_revision = "20260308_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("dataset_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_reports_dataset_id",
        "reports",
        "datasets",
        ["dataset_id"],
        ["id"],
    )

    op.add_column("report_blocks", sa.Column("type", sa.String(length=50), nullable=True))
    op.add_column("report_blocks", sa.Column("order", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("report_blocks", sa.Column("data_json", sa.Text(), nullable=True))
    op.add_column("report_blocks", sa.Column("editable_fields_json", sa.Text(), nullable=True))

    op.execute(
        'UPDATE report_blocks SET type = block_type, "order" = position, data_json = config_json'
    )

    op.alter_column("report_blocks", "type", nullable=False)
    op.alter_column("report_blocks", "order", nullable=False)

    op.drop_column("report_blocks", "block_type")
    op.drop_column("report_blocks", "position")
    op.drop_column("report_blocks", "config_json")


def downgrade() -> None:
    op.add_column("report_blocks", sa.Column("config_json", sa.Text()))
    op.add_column("report_blocks", sa.Column("position", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("report_blocks", sa.Column("block_type", sa.String(length=50), nullable=True))

    op.execute(
        'UPDATE report_blocks SET block_type = type, position = "order", config_json = data_json'
    )

    op.alter_column("report_blocks", "block_type", nullable=False)
    op.alter_column("report_blocks", "position", nullable=False)

    op.drop_column("report_blocks", "editable_fields_json")
    op.drop_column("report_blocks", "data_json")
    op.drop_column("report_blocks", "order")
    op.drop_column("report_blocks", "type")

    op.drop_constraint("fk_reports_dataset_id", "reports", type_="foreignkey")
    op.drop_column("reports", "dataset_id")
