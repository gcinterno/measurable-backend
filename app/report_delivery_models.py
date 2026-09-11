from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class ScheduledReportDelivery(Base):
    """Generation success is independent of this bounded delivery retry domain."""
    __tablename__ = "scheduled_report_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','RENDERING_PDF','EMAIL_SENDING','DELIVERED','FAILED','UNKNOWN')", name="ck_scheduled_delivery_status"),
        CheckConstraint("attempt_count >= 0", name="ck_scheduled_delivery_attempts"),
        Index("ix_scheduled_deliveries_workspace", "workspace_id", "id"),
        Index("ix_scheduled_deliveries_pending", "next_attempt_at", "id", postgresql_where=text("status = 'PENDING'"), sqlite_where=text("status = 'PENDING'")),
        Index("ix_scheduled_deliveries_expired", "lease_expires_at", "id", postgresql_where=text("status IN ('RENDERING_PDF','EMAIL_SENDING')"), sqlite_where=text("status IN ('RENDERING_PDF','EMAIL_SENDING')")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("scheduled_report_runs.id"), nullable=False, unique=True)
    export_id: Mapped[int | None] = mapped_column(ForeignKey("exports.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ses_message_id: Mapped[str | None] = mapped_column(String(255))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
