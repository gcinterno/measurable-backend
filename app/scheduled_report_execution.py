"""Durable orchestration only. All report construction/quota belongs to generate_report."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import logging
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_

from .models import ReportGeneration, User, WorkspaceMember
from .report_generation import (ExecutableReportConfiguration, GenerateReportCommand, GenerationError,
    GenerationOptions, ReportingPeriod, SourceIdentity, _command_hash, _database_now, _utc, generate_report,
    lock_generation_workspace, renew_generation_reservation, validate_executable_configuration)
from .scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun
from .scheduled_report_recurrence import next_occurrence, occurrence_identity, reporting_period
from .scheduled_report_refresh import private_provider_io, refresh_source
from .services import get_plan_limits, get_workspace_report_quota_status

logger = logging.getLogger(__name__)
RUN_LEASE = timedelta(minutes=2)
HEARTBEAT_SECONDS = 20
MAX_ATTEMPTS = 3
RETRY_DELAYS = (60, 300)


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class RunClaim:
    run_id: int
    workspace_id: int
    token: str


def _write(db):
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def owned_run(db, claim):
    row = db.query(ScheduledReportRun).filter(ScheduledReportRun.id == claim.run_id).with_for_update().populate_existing().one()
    if row.workspace_id != claim.workspace_id or row.status != "RUNNING" or row.lease_token != claim.token or not row.lease_expires_at or _utc(row.lease_expires_at) <= _database_now(db):
        raise LeaseLost("Run execution ownership expired or changed.")
    return row


def _new_run(schedule, revision, instant, trigger, key):
    from .scheduled_reports import _recurrence
    recurrence = _recurrence(revision.snapshot_json)
    period = reporting_period(recurrence, scheduled_for=instant)
    return ScheduledReportRun(schedule_id=schedule.id, workspace_id=schedule.workspace_id,
        configuration_revision=revision.revision, configuration_hash=revision.configuration_hash,
        trigger_type=trigger, scheduled_for=instant, timezone=recurrence.timezone,
        reporting_period_start=period.start, reporting_period_end=period.end,
        reporting_start_date=period.start_date, reporting_end_date=period.end_date,
        idempotency_key=key, status="QUEUED", stage="PENDING", attempt_count=0)


def dispatch_due(factory, *, batch_size=20, now=None):
    from .scheduled_reports import _recurrence
    with factory() as db:
        _write(db)
        now = now or _database_now(db)
        schedules = db.query(ScheduledReport).filter(ScheduledReport.status == "ACTIVE", ScheduledReport.next_run_at <= now).order_by(
            ScheduledReport.next_run_at, ScheduledReport.id).with_for_update(skip_locked=True).limit(batch_size).all()
        ids = []
        for schedule in schedules:
            instant = _utc(schedule.next_run_at)
            revision = db.get(ScheduledReportRevision, (schedule.id, schedule.workspace_id, schedule.configuration_revision))
            run = db.query(ScheduledReportRun).filter(ScheduledReportRun.schedule_id == schedule.id,
                ScheduledReportRun.trigger_type == "SCHEDULED", ScheduledReportRun.scheduled_for == instant).first()
            if run is None:
                run = _new_run(schedule, revision, instant, "SCHEDULED", occurrence_identity(schedule.id, instant))
                db.add(run)
                db.flush()
                ids.append(run.id)
            # Downtime handles at most the stored cursor, never all missed occurrences.
            schedule.next_run_at = next_occurrence(_recurrence(revision.snapshot_json), after=now)
        db.commit()
        return ids


def request_run_now(db, schedule, request_key):
    from .scheduled_reports import _require_paid, _revision, _source_problem
    _require_paid(db, schedule.workspace_id)
    if not request_key or len(request_key) > 200:
        raise GenerationError("invalid_idempotency_key", "Run Now requires a 1-200 character Idempotency-Key.", status_code=422)
    key = f"schedule:{schedule.id}:manual:" + hashlib.sha256(request_key.encode()).hexdigest()
    existing = db.query(ScheduledReportRun).filter(ScheduledReportRun.workspace_id == schedule.workspace_id,
                                                  ScheduledReportRun.idempotency_key == key).first()
    if existing is not None:
        return existing, False
    if schedule.status == "ARCHIVED":
        raise GenerationError("schedule_archived", "Archived schedules cannot run.", status_code=409)
    revision = _revision(db, schedule)
    if schedule.status == "BLOCKED":
        problem = _source_problem(db, revision.snapshot_json["sources"], schedule.workspace_id)
        if problem:
            raise GenerationError(problem, "The schedule requires attention.", status_code=409)
        resolvable = {"source_missing", "source_disconnected", "source_identity_changed", "source_account_changed",
                      "builder_contract_changed", "configuration_not_supported"}
        if schedule.status_reason not in resolvable:
            # A connected flag cannot prove a revoked credential was repaired.
            # Require explicit resume after repair for these action-required blocks.
            raise GenerationError("schedule_blocked", "Repair and resume this schedule before Run Now.", status_code=409)
    validate_snapshot(db, revision)
    run = _new_run(schedule, revision, _database_now(db), "MANUAL", key)
    db.add(run)
    db.flush()
    return run, True


def claim_run(factory, *, worker_id, run_id=None):
    with factory() as db:
        _write(db)
        now = _database_now(db)
        query = db.query(ScheduledReportRun).filter(or_(
            and_(ScheduledReportRun.status == "QUEUED", or_(ScheduledReportRun.retry_after.is_(None), ScheduledReportRun.retry_after <= now)),
            and_(ScheduledReportRun.status == "RUNNING", or_(ScheduledReportRun.lease_expires_at.is_(None), ScheduledReportRun.lease_expires_at <= now))))
        if run_id is not None:
            query = query.filter(ScheduledReportRun.id == run_id)
        row = query.order_by(ScheduledReportRun.created_at, ScheduledReportRun.id).with_for_update(skip_locked=True).first()
        if row is None:
            return None
        row.status, row.worker_id, row.lease_token = "RUNNING", worker_id[:120], str(uuid4())
        row.lease_expires_at, row.heartbeat_at = now + RUN_LEASE, now
        row.started_at = row.started_at or now
        row.attempt_count += 1
        row.retry_after = None
        claim = RunClaim(row.id, row.workspace_id, row.lease_token)
        db.commit()
        return claim


class RunHeartbeat:
    def __init__(self, factory, claim, *, interval=HEARTBEAT_SECONDS):
        self.factory, self.claim, self.interval = factory, claim, interval
        self.stop, self.lost, self.mutex = Event(), Event(), Lock()
        self.reservation = None
        self.thread = Thread(target=self._loop, name=f"schedule-heartbeat-{claim.run_id}", daemon=True)

    def pulse(self):
        with self.mutex, self.factory() as db:
            # Same lock order as canonical finalization: workspace, generation, run.
            lock_generation_workspace(db, self.claim.workspace_id)
            if self.reservation is not None:
                generation = db.get(ReportGeneration, self.reservation.generation_id)
                if generation.state == "reserved":
                    renew_generation_reservation(db, self.reservation)
            row = owned_run(db, self.claim)
            if self.reservation is not None:
                row.generation_id = self.reservation.generation_id
            now = _database_now(db)
            row.lease_expires_at, row.heartbeat_at = now + RUN_LEASE, now
            db.commit()

    def reserved(self, reservation):
        with self.mutex:
            self.reservation = reservation
        self.pulse()

    def guard(self, db):
        if self.lost.is_set():
            raise LeaseLost()
        owned_run(db, self.claim)

    def _loop(self):
        while not self.stop.wait(self.interval):
            try:
                self.pulse()
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.pulse()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def validate_snapshot(db, revision):
    try:
        _validate_snapshot(db, revision)
    except (ValueError, TypeError, KeyError) as exc:
        raise GenerationError("configuration_not_supported", "Schedule configuration is invalid.") from exc


def _validate_snapshot(db, revision):
    from .scheduled_reports import configuration_hash, _validate_source_configuration, SourceInput, OptionsInput
    snapshot = revision.snapshot_json
    if revision.schema_version != 1 or snapshot.get("schema_version") != 1 or configuration_hash(snapshot) != revision.configuration_hash:
        raise GenerationError("configuration_not_supported", "Schedule configuration is invalid.")
    config = ExecutableReportConfiguration(**snapshot["configuration"])
    validate_executable_configuration(config)
    if config.builder not in {"meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"} or config.requested_slides != (10 if config.builder == "multi_source" else 5) or config.template or not config.builder_contract:
        raise GenerationError("configuration_not_supported", "Schedule configuration is not executable.")
    options = dict(snapshot["generation_options"])
    if options.pop("allow_configuration_only", False) is not False:
        raise GenerationError("configuration_not_supported", "Scheduled reports must generate complete reports.")
    OptionsInput.model_validate(options)
    for source in snapshot["sources"]:
        SourceInput.model_validate(source)
    _validate_source_configuration(snapshot["configuration"], snapshot["sources"])


def quota_state(db, workspace_id):
    quota = get_workspace_report_quota_status(db, workspace_id, now=_database_now(db))
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in quota.items()}


def run_output(row):
    fields = ("id", "schedule_id", "workspace_id", "trigger_type", "timezone", "configuration_revision", "configuration_hash",
              "idempotency_key", "status", "stage", "attempt_count", "report_id", "report_version_id", "error_code", "failure_class",
              "reporting_start_date", "reporting_end_date")
    dates = ("scheduled_for", "reporting_period_start", "reporting_period_end", "retry_after", "started_at", "completed_at", "created_at")
    return {**{key: getattr(row, key) for key in fields},
            **{key: _utc(getattr(row, key)) if getattr(row, key) else None for key in dates},
            "quota": row.quota_json, "upgrade_required": row.status == "QUOTA_BLOCKED" or row.error_code == "SCHEDULE_ENTITLEMENT_REQUIRED"}


def _event(row, result):
    logger.info("scheduled_report_execution", extra={"workspace_id": row.workspace_id, "schedule_id": row.schedule_id,
        "scheduled_report_run_id": row.id, "trigger_type": row.trigger_type, "attempt_number": row.attempt_count,
        "stage": row.stage, "result": result})


def finish_run(factory, claim, *, status, code=None, failure_class=None, retryable=False, block=False, report=None):
    with factory() as db:
        _write(db)
        row = owned_run(db, claim)
        now = _database_now(db)
        if report is not None:
            row.report_id, row.report_version_id = report
            row.stage = "COMPLETE"
        row.quota_json = quota_state(db, row.workspace_id)
        if retryable and row.attempt_count < MAX_ATTEMPTS:
            row.status = "QUEUED"
            row.retry_after = now + timedelta(seconds=RETRY_DELAYS[row.attempt_count - 1])
        else:
            row.status, row.completed_at = status, now
        row.error_code, row.failure_class = code, failure_class
        row.error_detail = "Execution requires attention; see error_code." if code else None
        row.lease_token = row.lease_expires_at = row.worker_id = None
        schedule = db.query(ScheduledReport).filter(ScheduledReport.id == row.schedule_id).with_for_update().one()
        schedule.last_run_at = now
        if block and schedule.configuration_revision == row.configuration_revision and schedule.status not in {"ARCHIVED", "PAUSED"}:
            schedule.status, schedule.status_reason, schedule.next_run_at = "BLOCKED", code, None
        _event(row, row.status)
        db.commit()


def _save_stage(factory, claim, stage, sources=None):
    with factory() as db:
        _write(db)
        row = owned_run(db, claim)
        row.stage = stage
        if sources is not None:
            row.execution_sources_json = sources
        _event(row, "RUNNING")
        db.commit()


def _reconcile(factory, claim):
    # A lost worker cannot finalize: the canonical persistence transaction locks and
    # checks this run's fence. Reclaim can safely cancel its unconsumed reservation.
    with factory() as db:
        lock_generation_workspace(db, claim.workspace_id)
        row = db.get(ScheduledReportRun, claim.run_id)
        generation = db.query(ReportGeneration).filter(ReportGeneration.workspace_id == claim.workspace_id,
            ReportGeneration.logical_key == "key:" + row.idempotency_key).with_for_update().first()
        owned_run(db, claim)
        result = None
        if generation is not None:
            if generation.state == "consumed" and row.generation_id == generation.id:
                if generation.report_id is None or generation.report_version_id is None:
                    raise GenerationError("generated_report_deleted", "Original report has been deleted.", status_code=410)
                db.commit()
                return (generation.report_id, generation.report_version_id)
            revision = db.get(ScheduledReportRevision, (row.schedule_id, row.workspace_id, row.configuration_revision))
            snapshot = revision.snapshot_json
            sources = row.execution_sources_json or []
            if len(sources) != len(snapshot["sources"]):
                raise GenerationError("idempotency_conflict", "Run generation identity conflicts with existing inputs.", status_code=409)
            command = GenerateReportCommand(workspace_id=row.workspace_id, actor_user_id=revision.created_by_user_id,
                sources=tuple(SourceIdentity(**source) for source in sources),
                configuration=ExecutableReportConfiguration(**snapshot["configuration"]),
                period=ReportingPeriod("custom", row.reporting_start_date.isoformat(), row.reporting_end_date.isoformat()),
                options=GenerationOptions(**snapshot["generation_options"]), idempotency_key=row.idempotency_key)
            if generation.command_hash != _command_hash(command):
                raise GenerationError("idempotency_conflict", "Run generation identity conflicts with existing inputs.", status_code=409)
            if generation.state == "consumed":
                if generation.report_id is None or generation.report_version_id is None:
                    raise GenerationError("generated_report_deleted", "Original report has been deleted.", status_code=410)
                result = (generation.report_id, generation.report_version_id)
            elif generation.state == "reserved":
                generation.state, generation.attempt_token, generation.lease_expires_at = "failed", None, None
                generation.last_error_code = "scheduled_worker_reclaimed"
        db.commit()
        return result


def execute_run(factory, claim, *, refresher=refresh_source, heartbeat_interval=HEARTBEAT_SECONDS):
    from .scheduled_reports import schedule_availability, _source_problem
    try:
        with RunHeartbeat(factory, claim, interval=heartbeat_interval) as heartbeat:
            existing = _reconcile(factory, claim)
            if existing:
                finish_run(factory, claim, status="SUCCEEDED", report=existing)
                return
            with factory() as db:
                row = owned_run(db, claim)
                revision = db.get(ScheduledReportRevision, (row.schedule_id, row.workspace_id, row.configuration_revision))
                snapshot = deepcopy(revision.snapshot_json)
                actor = revision.created_by_user_id
                period = ReportingPeriod("custom", row.reporting_start_date.isoformat(), row.reporting_end_date.isoformat())
                key, sources = row.idempotency_key, deepcopy(row.execution_sources_json or [])
                availability = schedule_availability(db, row.workspace_id)
                quota = quota_state(db, row.workspace_id)
                attempt = row.attempt_count
                schedule = db.get(ScheduledReport, row.schedule_id)
                if schedule.status == "ARCHIVED" or row.trigger_type == "SCHEDULED" and schedule.status != "ACTIVE":
                    raise GenerationError("SCHEDULE_INACTIVE", "Schedule was paused, blocked, or archived before execution.")
                validate_snapshot(db, revision)
                if not availability["scheduling_enabled"]:
                    raise GenerationError("SCHEDULE_ENTITLEMENT_REQUIRED", "Paid scheduling entitlement required.", status_code=403)
                user = db.get(User, actor) if actor else None
                if user is None or not user.is_active or user.is_deleted or not user.email_verified or not db.query(WorkspaceMember.id).filter_by(workspace_id=row.workspace_id, user_id=actor).first():
                    raise GenerationError("schedule_actor_unavailable", "Schedule actor no longer has workspace access.", status_code=403)
                if snapshot["configuration"]["requested_slides"] > get_plan_limits(availability["plan"])["max_slides"]:
                    raise GenerationError("SCHEDULE_ENTITLEMENT_REQUIRED", "Plan cannot execute this configuration.", status_code=403)
                problem = _source_problem(db, snapshot["sources"], row.workspace_id)
                if problem:
                    raise GenerationError(problem, "Pinned source requires attention.")
                if quota["capacity_remaining"] == 0:
                    raise GenerationError("monthly_report_limit_reached", "Monthly report capacity exhausted.", status_code=403)
            if attempt > MAX_ATTEMPTS:
                raise GenerationError("attempts_exhausted", "Execution attempts exhausted.")
            _save_stage(factory, claim, "REFRESH")
            for original in snapshot["sources"][len(sources):]:
                heartbeat.pulse()
                refreshed = refresher(factory, workspace_id=claim.workspace_id, actor_user_id=actor,
                    source=SourceIdentity(**original), start_date=period.start_date, end_date=period.end_date)
                # Refresh adapters must not change the pinned account while replacing dataset identity.
                expected = {**original, "dataset_id": refreshed.dataset_id}
                actual = asdict(refreshed)
                if any(actual.get(k) != v for k, v in expected.items()) or not refreshed.dataset_id:
                    raise GenerationError("refresh_identity_changed", "Refresh changed a pinned identity.")
                sources.append(actual)
                _save_stage(factory, claim, "REFRESH", sources)
            _save_stage(factory, claim, "GENERATE")
            logger.info("scheduled_report_generation", extra={"workspace_id": claim.workspace_id,
                "scheduled_report_run_id": claim.run_id, "report_type": snapshot["configuration"]["builder"]})
            with factory() as db:
                if not schedule_availability(db, claim.workspace_id)["scheduling_enabled"]:
                    raise GenerationError("SCHEDULE_ENTITLEMENT_REQUIRED", "Paid scheduling entitlement required.", status_code=403)
                problem = _source_problem(db, snapshot["sources"], claim.workspace_id)
                if problem:
                    raise GenerationError(problem, "Pinned source requires attention.")
            command = GenerateReportCommand(workspace_id=claim.workspace_id, actor_user_id=actor,
                sources=tuple(SourceIdentity(**source) for source in sources), period=period, idempotency_key=key,
                configuration=ExecutableReportConfiguration(**snapshot["configuration"]),
                options=GenerationOptions(**snapshot["generation_options"]))
            with factory() as db, private_provider_io():
                result = generate_report(db, command, on_reserved=heartbeat.reserved, ownership_guard=heartbeat.guard)
            _save_stage(factory, claim, "FINALIZE")
            finish_run(factory, claim, status="SUCCEEDED", report=(result.report_id, result.version_id))
    except LeaseLost:
        logger.info("scheduled_report_ownership_lost", extra={"scheduled_report_run_id": claim.run_id})
    except Exception as exc:
        # A Report commit is authoritative even if the following run update failed.
        # Recover it before applying retry limits or failure classification.
        try:
            existing = _reconcile(factory, claim)
            if existing:
                finish_run(factory, claim, status="SUCCEEDED", report=existing)
                return
        except LeaseLost:
            return
        except GenerationError as reconciliation_error:
            exc = reconciliation_error
        code, retryable, block = classify_failure(exc)
        status = "QUOTA_BLOCKED" if code == "monthly_report_limit_reached" else "SKIPPED" if code == "SCHEDULE_ENTITLEMENT_REQUIRED" else "CANCELLED" if code == "SCHEDULE_INACTIVE" else "FAILED"
        try:
            finish_run(factory, claim, status=status, code=code,
                       failure_class="TRANSIENT" if retryable else "ACTION_REQUIRED" if block else "TERMINAL",
                       retryable=retryable, block=block)
        except LeaseLost:
            pass


def classify_failure(exc):
    if isinstance(exc, GenerationError):
        code = exc.code
        if code in {"monthly_report_limit_reached", "SCHEDULE_ENTITLEMENT_REQUIRED", "SCHEDULE_INACTIVE", "attempts_exhausted", "generated_report_deleted"}:
            return code, False, False
        if exc.status_code >= 500 or code in {"generation_in_progress", "generation_reservation_lost"}:
            return "generation_temporarily_unavailable", True, False
        return code, False, True
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        upstream = detail.get("upstream_status_code")
        meta = detail.get("meta_error") if isinstance(detail.get("meta_error"), dict) else {}
        if meta.get("code") in {190, 102}:
            return "source_authorization_required", False, True
        if (exc.status_code == 429 or exc.status_code >= 500 or upstream == 429
                or isinstance(upstream, int) and upstream >= 500 or meta.get("is_transient") is True
                or meta.get("code") in {1, 2, 4, 17, 32, 613}
                or detail.get("code") == "instagram_business_login_insights_failed"):
            return "provider_temporarily_unavailable", True, False
        return "source_authorization_required" if exc.status_code in {401, 403} else "source_unavailable", False, True
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "configuration_not_supported", False, True
    return "execution_temporarily_unavailable", True, False
