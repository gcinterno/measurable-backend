"""Track canonical report generation identity and consumed allowance.

Revision ID: 20260909_000030
Revises: 20260901_000029
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_000030"
down_revision = "20260901_000029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("logical_key", sa.String(255), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="SET NULL")),
        sa.Column("report_version_id", sa.Integer(), sa.ForeignKey("report_versions.id", ondelete="SET NULL")),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20)),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_token", sa.String(36)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("charged_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "logical_key", name="uq_report_generations_identity"),
        sa.UniqueConstraint("report_id", name="uq_report_generations_report"),
        sa.CheckConstraint("outcome IN ('completed', 'configured', 'legacy')", name="ck_report_generations_outcome"),
        sa.CheckConstraint("state IN ('reserved', 'consumed', 'failed')", name="ck_report_generations_state"),
        sa.CheckConstraint("(state = 'consumed' AND charged_at IS NOT NULL AND completed_at IS NOT NULL AND outcome IS NOT NULL) OR (state IN ('reserved', 'failed') AND charged_at IS NULL AND report_id IS NULL AND report_version_id IS NULL)", name="ck_report_generations_consumption"),
        sa.CheckConstraint("state != 'reserved' OR (attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_report_generations_reservation"),
    )
    op.create_index("ix_report_generations_workspace_charged_at", "report_generations", ["workspace_id", "charged_at"])
    op.create_index("ix_report_generations_capacity", "report_generations", ["workspace_id", "state", "reserved_at", "lease_expires_at"])
    op.create_index("ix_report_generations_version", "report_generations", ["report_version_id"])
    # Preserve pre-cutover usage exactly; do not reinterpret historical report statuses.
    op.execute(sa.text("""
        INSERT INTO report_generations
            (workspace_id, logical_key, command_hash, report_id, state, outcome, reserved_at, charged_at, completed_at)
        SELECT workspace_id, 'legacy:' || CAST(id AS VARCHAR), '', id, 'consumed', 'legacy', created_at, created_at, created_at
        FROM reports
    """))


def downgrade() -> None:
    # Dropping post-cutover identities would refund deleted reports and permit duplicate retries.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("LOCK TABLE report_generations IN ACCESS EXCLUSIVE MODE"))
    if op.get_bind().execute(sa.text("SELECT 1 FROM report_generations WHERE outcome IS NULL OR outcome != 'legacy' OR report_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade after canonical generation has reserved capacity; retain the ledger and roll forward.")
    op.drop_index("ix_report_generations_version", table_name="report_generations")
    op.drop_index("ix_report_generations_capacity", table_name="report_generations")
    op.drop_index("ix_report_generations_workspace_charged_at", table_name="report_generations")
    op.drop_table("report_generations")
