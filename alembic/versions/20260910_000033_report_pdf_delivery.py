"""Private version-pinned PDF exports and independent scheduled email delivery.

No historical export is reinterpreted as PDF. Old revisions remain generate-only.
Stop workers before downgrade. Once snapshots/delivery configurations exist, roll
forward: removing them would discard immutable rendering/delivery history.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_000033"
down_revision = "20260910_000032"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("exports") as batch:
        batch.add_column(sa.Column("artifact_type", sa.String(10), nullable=False, server_default="PPTX"))
        batch.add_column(sa.Column("report_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_exports_report_version", "report_versions", ["report_version_id"], ["id"], ondelete="SET NULL")
        for name, type_ in (
            ("render_snapshot_json", sa.JSON().with_variant(JSONB(), "postgresql")),
            ("snapshot_hash", sa.String(64)), ("renderer_version", sa.String(100)), ("storage_bucket", sa.String(255)),
            ("content_type", sa.String(100)), ("size_bytes", sa.Integer()), ("checksum_sha256", sa.String(64)),
            ("completed_at", sa.DateTime(timezone=True)), ("error_code", sa.String(100)),
            ("lease_token", sa.String(36)), ("lease_expires_at", sa.DateTime(timezone=True)),
        ):
            batch.add_column(sa.Column(name, type_, nullable=True))
    op.create_index("uq_exports_pdf_version", "exports", ["report_version_id"], unique=True,
        postgresql_where=sa.text("artifact_type = 'PDF'"), sqlite_where=sa.text("artifact_type = 'PDF'"))
    op.create_table("scheduled_report_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("scheduled_report_runs.id"), nullable=False, unique=True),
        sa.Column("export_id", sa.Integer(), sa.ForeignKey("exports.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("send_started_at", sa.DateTime(timezone=True)),
        sa.Column("ses_message_id", sa.String(255)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('PENDING','RENDERING_PDF','EMAIL_SENDING','DELIVERED','FAILED','UNKNOWN')", name="ck_scheduled_delivery_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduled_delivery_attempts"),
    )
    op.create_index("ix_scheduled_deliveries_workspace", "scheduled_report_deliveries", ["workspace_id", "id"])
    for name, column, predicate in (
        ("pending", "next_attempt_at", "status = 'PENDING'"),
        ("expired", "lease_expires_at", "status IN ('RENDERING_PDF','EMAIL_SENDING')"),
    ):
        op.create_index(f"ix_scheduled_deliveries_{name}", "scheduled_report_deliveries", [column, "id"],
                        postgresql_where=sa.text(predicate), sqlite_where=sa.text(predicate))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("LOCK TABLE exports, scheduled_report_deliveries, scheduled_report_revisions IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM exports WHERE artifact_type = 'PDF' LIMIT 1")).first() or bind.execute(sa.text("SELECT 1 FROM scheduled_report_deliveries LIMIT 1")).first():
        raise RuntimeError("PDF/delivery history exists; roll forward rather than discard it.")
    revisions = sa.table("scheduled_report_revisions", sa.column("snapshot_json", sa.JSON()))
    for snapshot in bind.execute(sa.select(revisions.c.snapshot_json)).scalars():
        if snapshot.get("delivery", {}).get("mode") == "EMAIL_PDF":
            raise RuntimeError("Delivery configuration exists; roll forward rather than silently disable it.")
    op.drop_table("scheduled_report_deliveries")
    op.drop_index("uq_exports_pdf_version", table_name="exports")
    with op.batch_alter_table("exports") as batch:
        batch.drop_constraint("fk_exports_report_version", type_="foreignkey")
        for name in ("artifact_type", "report_version_id", "render_snapshot_json", "snapshot_hash", "renderer_version",
                     "storage_bucket", "content_type", "size_bytes", "checksum_sha256", "completed_at", "error_code", "lease_token", "lease_expires_at"):
            batch.drop_column(name)
