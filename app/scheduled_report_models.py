from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, Time, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_scheduled_reports_workspace_identity"),
        ForeignKeyConstraint(["id", "workspace_id", "configuration_revision"],
                             ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision"],
                             name="fk_scheduled_reports_current_revision", deferrable=True, initially="DEFERRED"),
        CheckConstraint("status IN ('ACTIVE', 'PAUSED', 'BLOCKED', 'ARCHIVED')", name="ck_scheduled_reports_status"),
        CheckConstraint("(status = 'ACTIVE' AND next_run_at IS NOT NULL) OR (status != 'ACTIVE' AND next_run_at IS NULL)", name="ck_scheduled_reports_next_run"),
        CheckConstraint("(status = 'ARCHIVED' AND archived_at IS NOT NULL) OR (status != 'ARCHIVED' AND archived_at IS NULL)", name="ck_scheduled_reports_archived"),
        CheckConstraint("(frequency = 'WEEKLY' AND day_of_week IS NOT NULL AND day_of_week BETWEEN 0 AND 6 AND day_of_month IS NULL AND period_policy = 'previous_week') OR (frequency = 'MONTHLY' AND day_of_month IS NOT NULL AND day_of_month BETWEEN 1 AND 31 AND day_of_week IS NULL AND period_policy = 'previous_month')", name="ck_scheduled_reports_recurrence"),
        Index("ix_scheduled_reports_workspace_status", "workspace_id", "status", "id"),
        Index("ix_scheduled_reports_due", "next_run_at", "id", postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement="ignore_fk")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(100))
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer)
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    period_policy: Mapped[str] = mapped_column(String(20), nullable=False)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ScheduledReportRevision(Base):
    __tablename__ = "scheduled_report_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["schedule_id", "workspace_id"], ["scheduled_reports.id", "scheduled_reports.workspace_id"], name="fk_scheduled_report_revisions_schedule"),
        UniqueConstraint("schedule_id", "workspace_id", "revision", "configuration_hash", name="uq_scheduled_report_revision_hash"),
        CheckConstraint("revision > 0 AND schema_version = 1", name="ck_scheduled_report_revision_version"),
        Index("ix_scheduled_report_revisions_workspace", "workspace_id", "schedule_id"),
    )

    schedule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScheduledReportSource(Base):
    __tablename__ = "scheduled_report_sources"
    __table_args__ = (
        ForeignKeyConstraint(["schedule_id", "workspace_id", "configuration_revision"],
                             ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision"], name="fk_scheduled_report_sources_revision"),
        UniqueConstraint("schedule_id", "configuration_revision", "position", name="uq_scheduled_report_source_position"),
        UniqueConstraint("schedule_id", "configuration_revision", "provider", "source_type", "external_account_id", name="uq_scheduled_report_source_identity"),
        CheckConstraint("position >= 0", name="ck_scheduled_report_source_position"),
        Index("ix_scheduled_report_sources_workspace", "workspace_id", "schedule_id"),
        Index("ix_scheduled_report_sources_integration", "integration_id"),
        Index("ix_scheduled_report_sources_account", "integration_account_id"),
        Index("ix_scheduled_report_sources_dataset", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Nullable live links allow disconnect cleanup without destroying the immutable original identities in the revision.
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"))
    integration_account_id: Mapped[int | None] = mapped_column(ForeignKey("integration_accounts.id", ondelete="SET NULL"))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))


class ScheduledReportRun(Base):
    __tablename__ = "scheduled_report_runs"
    __table_args__ = (
        ForeignKeyConstraint(["schedule_id", "workspace_id", "configuration_revision", "configuration_hash"],
                             ["scheduled_report_revisions.schedule_id", "scheduled_report_revisions.workspace_id", "scheduled_report_revisions.revision", "scheduled_report_revisions.configuration_hash"], name="fk_scheduled_report_runs_revision"),
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_scheduled_report_run_occurrence"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_scheduled_report_run_identity"),
        CheckConstraint("status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'QUOTA_BLOCKED', 'SKIPPED', 'CANCELLED')", name="ck_scheduled_report_run_status"),
        CheckConstraint("trigger_type IN ('SCHEDULED', 'MANUAL')", name="ck_scheduled_report_run_trigger"),
        CheckConstraint("stage IN ('PENDING', 'REFRESH', 'GENERATE', 'FINALIZE', 'COMPLETE')", name="ck_scheduled_report_run_stage"),
        CheckConstraint("attempt_count >= 0", name="ck_scheduled_report_run_attempts"),
        CheckConstraint("reporting_period_start < reporting_period_end AND reporting_start_date <= reporting_end_date", name="ck_scheduled_report_run_period"),
        Index("ix_scheduled_report_runs_history", "workspace_id", "schedule_id", "scheduled_for", "id"),
        Index("ix_scheduled_report_runs_status", "workspace_id", "status"),
        Index("ix_scheduled_report_runs_report", "report_id"),
        Index("ix_scheduled_report_runs_version", "report_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reporting_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reporting_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reporting_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    reporting_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))
    report_version_id: Mapped[int | None] = mapped_column(ForeignKey("report_versions.id", ondelete="SET NULL"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
