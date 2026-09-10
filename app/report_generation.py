from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import event, func
from sqlalchemy.orm import Session

from .models import (
    Dataset, Integration, IntegrationAccount, Report, ReportBlock, ReportGeneration,
    ReportSource, ReportVersion, User, Workspace, WorkspaceMember,
)
from .report_spec import InvalidReportSpecError, assert_valid_report_spec


@dataclass(frozen=True)
class SourceIdentity:
    dataset_id: int | None
    provider: str
    source_type: str
    integration_id: int | None = None
    integration_account_id: int | None = None
    external_account_id: str | None = None
    position: int = 0
    label: str | None = None
    config_json: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReportingPeriod:
    timeframe: str = "last_28_days"
    start_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class ExecutableReportConfiguration:
    builder: str
    requested_slides: int | None = None
    template: str | None = None
    report_spec: Mapping[str, Any] | None = None
    branding: Mapping[str, Any] | None = None
    builder_contract: str | None = None


@dataclass(frozen=True)
class GenerationOptions:
    title: str | None = None
    locale: str = "en"
    ai_mode: str = "standard"
    allow_configuration_only: bool = False


@dataclass(frozen=True)
class GenerateReportCommand:
    workspace_id: int
    actor_user_id: int | None
    sources: tuple[SourceIdentity, ...]
    configuration: ExecutableReportConfiguration
    period: ReportingPeriod = field(default_factory=ReportingPeriod)
    options: GenerationOptions = field(default_factory=GenerationOptions)
    idempotency_key: str | None = None


@dataclass(frozen=True)
class BuiltReport:
    report_id: int
    version_id: int
    outcome: str = "completed"


@dataclass(frozen=True)
class ReportDraft:
    name: str
    metadata: Mapping[str, Any]
    block_specs: tuple[dict[str, Any], ...]
    sources: tuple[SourceIdentity, ...] = ()
    outcome: str = "completed"


@dataclass(frozen=True)
class GenerationReservation:
    generation_id: int
    workspace_id: int
    attempt_token: str


RESERVATION_TTL = timedelta(minutes=15)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    generation_id: int
    report_id: int
    version_id: int
    outcome: str
    replayed: bool = False


class GenerationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


def validate_executable_configuration(configuration: ExecutableReportConfiguration) -> None:
    """Validate canonical configuration without reserving quota or loading datasource values."""
    if configuration.builder not in {"dataset", "meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"}:
        raise GenerationError("unsupported_report_builder", "Unsupported report builder.")
    if configuration.template is not None and configuration.builder != "meta_ads":
        raise GenerationError("template_not_executable", "This builder does not support a template override.")
    if configuration.requested_slides is not None and configuration.requested_slides < 1:
        raise GenerationError("invalid_slide_count", "A report must contain at least one slide.")
    if configuration.report_spec is not None:
        try:
            assert_valid_report_spec(configuration.report_spec)
        except InvalidReportSpecError as exc:
            raise GenerationError("invalid_report_spec", str(exc)) from exc
        # Validation/storage do not imply execution support. Never discard supplied slides/bindings.
        raise GenerationError("report_spec_not_executable", "This ReportSpec does not have a supported generation executor.")
    if configuration.builder_contract is not None:
        from .report_generation_builders import current_builder_contract

        if configuration.builder_contract != current_builder_contract(configuration.builder):
            raise GenerationError("builder_contract_changed", "The pinned builder contract is no longer supported.", status_code=409)
    if configuration.branding is not None:
        from .report_generation_builders import validate_pinned_branding

        validate_pinned_branding(configuration.branding)


def _validate_command(db: Session, command: GenerateReportCommand) -> None:
    validate_executable_configuration(command.configuration)
    if command.options.ai_mode not in {"standard", "agents"}:
        raise GenerationError("invalid_ai_mode", "Unsupported AI generation mode.")
    if not command.sources or command.sources[0].dataset_id is None:
        raise GenerationError("dataset_required", "Generation requires an explicit primary dataset.")
    if command.configuration.builder != "multi_source" and len(command.sources) != 1:
        raise GenerationError("invalid_sources_count", "This builder requires exactly one source.")
    if command.configuration.builder != "multi_source" and command.sources[0].position != 0:
        raise GenerationError("invalid_source_position", "The primary source must use position zero.")
    if command.period.start_date is not None or command.period.end_date is not None:
        try:
            start = date.fromisoformat(command.period.start_date or "")
            end = date.fromisoformat(command.period.end_date or "")
            if start > end:
                raise ValueError("reversed period")
        except ValueError as exc:
            raise GenerationError("invalid_timeframe", "Reporting periods require ordered ISO start and end dates.") from exc
    if command.idempotency_key is not None and not (1 <= len(command.idempotency_key) <= 240):
        raise GenerationError("invalid_idempotency_key", "Idempotency keys must contain between 1 and 240 characters.")
    if command.actor_user_id is not None:
        user = db.get(User, command.actor_user_id)
        membership = db.query(WorkspaceMember.id).filter(
            WorkspaceMember.workspace_id == command.workspace_id,
            WorkspaceMember.user_id == command.actor_user_id,
        ).first()
        if not user or not user.is_active or user.is_deleted or not user.email_verified or not membership:
            raise GenerationError("forbidden", "Workspace access is required.", status_code=403)
    positions: set[int] = set()
    for source in command.sources:
        dataset = None
        account = None
        if command.configuration.builder != "dataset" and source.integration_id is None:
            raise GenerationError("integration_required", "Generation requires a pinned source integration.")
        if source.position in positions:
            raise GenerationError("duplicate_source_position", "Each source position must be unique.")
        positions.add(source.position)
        if source.dataset_id is not None:
            dataset = db.get(Dataset, source.dataset_id)
            if dataset is None or dataset.workspace_id != command.workspace_id:
                raise GenerationError("source_workspace_mismatch", "Dataset does not belong to the workspace.")
            period = (dataset.data or {}).get("timeframe") if isinstance(dataset.data, dict) else None
            if command.configuration.builder != "multi_source" and isinstance(period, dict):
                for field, requested in (("since", command.period.start_date), ("until", command.period.end_date)):
                    if requested and period.get(field) and str(period[field]) != requested:
                        raise GenerationError("dataset_period_mismatch", "Dataset does not cover the requested reporting period.")
        elif not command.options.allow_configuration_only:
            raise GenerationError("dataset_required", "Every generated source requires an explicit dataset.")
        if source.integration_id is not None:
            integration = db.get(Integration, source.integration_id)
            if integration is None or integration.workspace_id != command.workspace_id:
                raise GenerationError("source_workspace_mismatch", "Integration does not belong to the workspace.")
        if source.integration_account_id is not None:
            account = db.get(IntegrationAccount, source.integration_account_id)
            if account is None or account.workspace_id != command.workspace_id or account.integration_id != source.integration_id:
                raise GenerationError("source_account_mismatch", "Account does not belong to the source integration.")
        if dataset is not None:
            from .report_generation_builders import validate_source_dataset_identity

            validate_source_dataset_identity(source, dataset, account)


def _command_hash(command: GenerateReportCommand) -> str:
    payload = asdict(command)
    payload.pop("idempotency_key")
    # Preserve identities already persisted before optional snapshot pinning existed.
    for key in ("branding", "builder_contract"):
        if payload["configuration"][key] is None:
            payload["configuration"].pop(key)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def lock_generation_workspace(db: Session, workspace_id: int) -> None:
    """Serialize only short capacity, identity-normalization, and finalization transactions."""
    if db.get_bind().dialect.name == "sqlite":
        # SQLite has no row locks; this is also used by isolated backend tests.
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).with_for_update().first()
    if workspace is None:
        raise GenerationError("workspace_not_found", "Workspace not found.", status_code=404)


def _validate_generated_state(db: Session, command: GenerateReportCommand, built: BuiltReport) -> None:
    db.flush()
    report = db.get(Report, built.report_id)
    version = db.get(ReportVersion, built.version_id)
    if report is None or version is None or report.workspace_id != command.workspace_id or version.report_id != report.id:
        raise GenerationError("invalid_generated_report", "Generated report identity is invalid.", status_code=500)
    if report.dataset_id != command.sources[0].dataset_id:
        raise GenerationError("invalid_generated_report", "Generated report used a different dataset.", status_code=500)
    if built.outcome == "configured":
        if not command.options.allow_configuration_only:
            raise GenerationError("report_not_generated", "This configuration does not produce a complete report.")
    elif built.outcome == "completed":
        blocks = db.query(ReportBlock).filter(ReportBlock.report_version_id == version.id).order_by(ReportBlock.order).all()
        if not blocks or len({block.order for block in blocks}) != len(blocks):
            raise GenerationError("invalid_generated_blocks", "Generated report blocks are missing or duplicated.", status_code=500)
        for block in blocks:
            if not isinstance(json.loads(block.data_json or "null"), dict):
                raise GenerationError("invalid_generated_blocks", "Generated block payload must be an object.", status_code=500)
    else:
        raise GenerationError("invalid_generation_outcome", "Generation did not complete.", status_code=500)
    sources = db.query(ReportSource).filter(ReportSource.report_id == report.id).all()
    expected = [source for source in command.sources if source.integration_id is not None]
    if len(sources) != len(expected):
        raise GenerationError("invalid_generated_provenance", "Generated report has unexpected source relationships.", status_code=500)
    for source in expected:
        if not any(
            row.position == source.position and row.workspace_id == command.workspace_id
            and row.provider == source.provider and row.source_type == source.source_type
            and row.integration_id == source.integration_id and row.dataset_id == source.dataset_id
            and row.integration_account_id == source.integration_account_id
            for row in sources
        ):
            raise GenerationError("invalid_generated_provenance", "Generated provenance does not match the pinned sources.", status_code=500)
    metadata = json.loads(report.description or "{}")
    metadata["generation_status"] = built.outcome
    if command.configuration.builder != "multi_source":
        metadata["report_status"] = built.outcome
    report.description = json.dumps(metadata)
    db.add(report)
    db.flush()


def _database_now(db: Session) -> datetime:
    # Lease decisions use the database clock on PostgreSQL, not individual worker clocks.
    if db.get_bind().dialect.name == "postgresql":
        return db.query(func.clock_timestamp()).scalar()
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _reserve_generation(db: Session, command: GenerateReportCommand) -> GenerationReservation | GenerationResult:
    from .services import get_workspace_report_quota_status

    try:
        _validate_command(db, command)
        fingerprint = _command_hash(command)
        key = "key:" + command.idempotency_key if command.idempotency_key is not None else "manual:" + str(uuid4())
        db.commit()
        lock_generation_workspace(db, command.workspace_id)
        now = _database_now(db)
        db.query(ReportGeneration).filter(
            ReportGeneration.workspace_id == command.workspace_id,
            ReportGeneration.state == "reserved", ReportGeneration.lease_expires_at <= now,
        ).update({"state": "failed", "last_error_code": "reservation_expired",
                  "attempt_token": None, "lease_expires_at": None}, synchronize_session=False)
        existing = db.query(ReportGeneration).filter(
            ReportGeneration.workspace_id == command.workspace_id, ReportGeneration.logical_key == key,
        ).populate_existing().first()
        if existing is not None:
            if existing.command_hash != fingerprint:
                raise GenerationError("idempotency_conflict", "This generation key was used with different inputs.", status_code=409)
            if existing.state == "consumed":
                if existing.report_id is None or existing.report_version_id is None or db.get(Report, existing.report_id) is None or db.get(ReportVersion, existing.report_version_id) is None:
                    raise GenerationError("generated_report_deleted", "The report for this generation has been deleted.", status_code=410)
                result = GenerationResult(existing.id, existing.report_id, existing.report_version_id, existing.outcome, True)
                db.commit()
                return result
            if existing.state == "reserved":
                raise GenerationError("generation_in_progress", "This logical generation is already running.", status_code=409,
                                      details={"generation_id": existing.id, "retryable": True})

        quota = get_workspace_report_quota_status(db, command.workspace_id, now=now)
        if quota["capacity_remaining"] == 0:
            raise GenerationError("monthly_report_limit_reached", "You have reached your monthly report limit.", status_code=403, details=quota)
        generation = existing or ReportGeneration(
            workspace_id=command.workspace_id, logical_key=key, command_hash=fingerprint, attempt_count=0,
        )
        generation.state = "reserved"
        generation.reserved_at = now
        generation.lease_expires_at = now + RESERVATION_TTL
        generation.attempt_token = str(uuid4())
        generation.attempt_count += 1
        db.add(generation)
        db.flush()
        reservation = GenerationReservation(generation.id, command.workspace_id, generation.attempt_token)
        db.commit()
        return reservation
    except Exception:
        db.rollback()
        raise


def _release_reservation(db: Session, reservation: GenerationReservation, error_code: str) -> None:
    try:
        db.rollback()
        db.query(ReportGeneration).filter(
            ReportGeneration.id == reservation.generation_id, ReportGeneration.state == "reserved",
            ReportGeneration.attempt_token == reservation.attempt_token,
        ).update({"state": "failed", "attempt_token": None, "lease_expires_at": None,
                  "last_error_code": error_code[:100]}, synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        # A database outage cannot make capacity permanently unavailable: the lease is bounded.
        logger.exception("generation_reservation_release_failed", extra={"generation_id": reservation.generation_id})


def _validate_draft(command: GenerateReportCommand, draft: ReportDraft) -> None:
    if draft.outcome == "configured" and command.options.allow_configuration_only:
        return
    if draft.outcome != "completed" or not draft.block_specs:
        raise GenerationError("invalid_generated_blocks", "Generation must produce complete report blocks.", status_code=500)
    orders = set()
    for block in draft.block_specs:
        order = int(block["order"])
        if order in orders or not isinstance(json.loads(block["data_json"]), dict):
            raise GenerationError("invalid_generated_blocks", "Generated blocks are invalid or duplicated.", status_code=500)
        orders.add(order)


def _persist_report_draft(db: Session, command: GenerateReportCommand, draft: ReportDraft) -> BuiltReport:
    from .main import _persist_report_block_specs

    report = Report(workspace_id=command.workspace_id, dataset_id=command.sources[0].dataset_id,
                    name=draft.name, description=json.dumps(dict(draft.metadata)))
    db.add(report)
    db.flush()
    version = ReportVersion(report_id=report.id, version=1)
    db.add(version)
    db.flush()
    for source in draft.sources:
        if source.integration_id is not None:
            db.add(ReportSource(
                report_id=report.id, workspace_id=command.workspace_id, dataset_id=source.dataset_id,
                provider=source.provider, source_type=source.source_type, integration_id=source.integration_id,
                integration_account_id=source.integration_account_id, position=source.position,
                label=source.label, config_json=dict(source.config_json or {}),
            ))
    _persist_report_block_specs(db, report_version=version, block_specs=list(draft.block_specs))
    return BuiltReport(report.id, version.id, draft.outcome)


def _finalize_generation(db: Session, command: GenerateReportCommand, reservation: GenerationReservation, draft: ReportDraft) -> GenerationResult:
    def reject_commit(_session: Session) -> None:
        raise GenerationError("generation_transaction_violation", "Only canonical generation may commit.", status_code=500)

    reports_created: set[int] = set()
    versions_created: set[int] = set()

    def track_created_rows(session: Session, _context: Any) -> None:
        reports_created.update(row.id for row in session.new if isinstance(row, Report))
        versions_created.update(row.id for row in session.new if isinstance(row, ReportVersion))

    guarded = False
    try:
        lock_generation_workspace(db, command.workspace_id)
        generation = db.query(ReportGeneration).filter(ReportGeneration.id == reservation.generation_id).with_for_update().populate_existing().one()
        now = _database_now(db)
        if generation.state != "reserved" or generation.attempt_token != reservation.attempt_token or _utc(generation.lease_expires_at) <= now:
            raise GenerationError("generation_reservation_lost", "Generation reservation expired or was superseded; retry with the same identity.", status_code=409)
        event.listen(db, "before_commit", reject_commit)
        event.listen(db, "after_flush", track_created_rows)
        guarded = True
        transaction = db.get_transaction()
        built = _persist_report_draft(db, command, draft)
        if db.get_transaction() is not transaction:
            raise GenerationError("generation_transaction_violation", "Generation lost its finalization transaction.", status_code=500)
        _validate_generated_state(db, command, built)
        if reports_created != {built.report_id} or versions_created != {built.version_id}:
            raise GenerationError("invalid_generation_cardinality", "One generation must create exactly one report and version.", status_code=500)
        generation.state = "consumed"
        generation.report_id = built.report_id
        generation.report_version_id = built.version_id
        generation.outcome = built.outcome
        # Consumption belongs to the allowance window in which capacity was acquired.
        generation.charged_at = generation.reserved_at
        generation.completed_at = _database_now(db)
        generation.attempt_token = None
        generation.lease_expires_at = None
        db.flush()
        result = GenerationResult(generation.id, built.report_id, built.version_id, built.outcome)
        event.remove(db, "before_commit", reject_commit)
        event.remove(db, "after_flush", track_created_rows)
        guarded = False
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        if guarded:
            event.remove(db, "before_commit", reject_commit)
            event.remove(db, "after_flush", track_created_rows)


def generate_report(db: Session, command: GenerateReportCommand) -> GenerationResult:
    """Reserve briefly, build without a Session, then atomically persist and consume.

    Callers must not have unrelated pending writes. In-flight retries return a retryable
    conflict; completed retries return the original report. Expired attempts are fenced.
    """
    from .report_generation_builders import build_report, prepare_report_inputs

    reservation = _reserve_generation(db, command)
    if isinstance(reservation, GenerationResult):
        return reservation
    try:
        prepared = prepare_report_inputs(db, command)
        db.rollback()
        draft = build_report(command, prepared)
        _validate_draft(command, draft)
        return _finalize_generation(db, command, reservation, draft)
    except Exception as exc:
        _release_reservation(db, reservation, getattr(exc, "code", type(exc).__name__))
        raise
