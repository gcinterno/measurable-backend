"""Add fenced execution to Schedule Reports; no dispatch or data backfill.

Revision ID: 20260910_000032
Revises: 20260909_000031
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_000032"
down_revision = "20260909_000031"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("scheduled_report_runs") as batch:
        for name, type_ in (("worker_id", sa.String(120)), ("lease_token", sa.String(36)),
                            ("lease_expires_at", sa.DateTime(timezone=True)), ("heartbeat_at", sa.DateTime(timezone=True)),
                            ("failure_class", sa.String(30)),
                            ("execution_sources_json", sa.JSON().with_variant(JSONB(), "postgresql")),
                            ("quota_json", sa.JSON().with_variant(JSONB(), "postgresql"))):
            batch.add_column(sa.Column(name, type_, nullable=True))
        batch.drop_constraint("uq_scheduled_report_run_occurrence", type_="unique")
        batch.add_column(sa.Column("generation_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_scheduled_report_runs_generation", "report_generations", ["generation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_scheduled_report_runs_generation", "scheduled_report_runs", ["generation_id"])
    # Manual requests have their own identity; intentional same-period regenerations are valid.
    op.create_index("uq_scheduled_report_run_occurrence", "scheduled_report_runs", ["schedule_id", "scheduled_for"],
                    unique=True, postgresql_where=sa.text("trigger_type = 'SCHEDULED'"), sqlite_where=sa.text("trigger_type = 'SCHEDULED'"))
    for name, column, status in (("runnable", "retry_after", "QUEUED"), ("expired", "lease_expires_at", "RUNNING")):
        op.create_index(f"ix_scheduled_report_runs_{name}", "scheduled_report_runs", [column, "id"],
                        postgresql_where=sa.text(f"status = '{status}'"), sqlite_where=sa.text(f"status = '{status}'"))


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("LOCK TABLE scheduled_report_runs IN ACCESS EXCLUSIVE MODE"))
    if op.get_bind().execute(sa.text("SELECT 1 FROM scheduled_report_runs WHERE lease_token IS NOT NULL OR heartbeat_at IS NOT NULL OR execution_sources_json IS NOT NULL OR generation_id IS NOT NULL OR quota_json IS NOT NULL OR failure_class IS NOT NULL OR trigger_type = 'MANUAL' LIMIT 1")).first():
        raise RuntimeError("Execution history exists; roll forward rather than discard execution state.")
    for name in ("uq_scheduled_report_run_occurrence", "ix_scheduled_report_runs_runnable", "ix_scheduled_report_runs_expired", "ix_scheduled_report_runs_generation"):
        op.drop_index(name, table_name="scheduled_report_runs")
    with op.batch_alter_table("scheduled_report_runs") as batch:
        batch.drop_constraint("fk_scheduled_report_runs_generation", type_="foreignkey")
        batch.create_unique_constraint("uq_scheduled_report_run_occurrence", ["schedule_id", "scheduled_for"])
        for name in ("generation_id", "quota_json", "execution_sources_json", "failure_class", "heartbeat_at", "lease_expires_at", "lease_token", "worker_id"):
            batch.drop_column(name)
