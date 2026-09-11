from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator, model_serializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .deps import get_current_user, get_db
from .errors import http_error
from .models import Dataset, Integration, IntegrationAccount, ShopifyConnection, User, WorkspaceMember
from .report_generation import ExecutableReportConfiguration, GenerationError, GenerationOptions, SourceIdentity, validate_executable_configuration
from .report_generation_builders import current_builder_contract, validate_source_dataset_identity
from .scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun, ScheduledReportSource
from .scheduled_report_recurrence import Recurrence, next_occurrence
from .services import get_plan_limits, get_workspace_subscription, normalize_workspace_plan, resolve_report_branding_for_workspace


# Legacy /schedules and Schedule/Job remain isolated. Migrate only after their callers and
# incomplete configurations have an explicit conversion policy; never infer a report recipe.
class ScheduleRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_validation(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # The legacy global handler echoes request bodies. Do not echo rejected credentials here.
                return JSONResponse(status_code=422, content={"detail": [
                    {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
                ]})

        return safe_validation


router = APIRouter(prefix="/scheduled-reports", tags=["scheduled-reports"], route_class=ScheduleRoute)
RECURRENCE_FIELDS = ("frequency", "day_of_week", "day_of_month", "local_time", "timezone", "period_policy")


@router.get("/{schedule_id}/runs/{run_id}/pdf")
def download_scheduled_pdf(schedule_id: int, run_id: int, workspace_id: int = Query(gt=0),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from .models import Export
    from .report_artifacts import ArtifactError, download_url, ensure_pdf
    from .scheduled_report_refresh import private_provider_io
    require_workspace_access(db, current_user, workspace_id)
    _schedule(db, schedule_id, workspace_id)
    run = db.query(ScheduledReportRun).filter_by(id=run_id, schedule_id=schedule_id, workspace_id=workspace_id).first()
    if run is None:
        raise http_error(404, "scheduled_run_not_found", "Scheduled run not found.")
    if run.status != "SUCCEEDED" or not run.report_version_id:
        raise http_error(409, "scheduled_pdf_unavailable", "This run has no completed report version.")
    artifact = db.query(Export).filter_by(workspace_id=workspace_id, report_id=run.report_id,
        report_version_id=run.report_version_id, artifact_type="PDF").first()
    if artifact is None:
        raise http_error(409, "render_snapshot_unavailable", "No immutable render snapshot exists for this historical version.")
    export_id = artifact.id
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()  # Renderer/S3 work must not retain the request's read transaction.
    try:
        with private_provider_io():
            result = ensure_pdf(factory, export_id)
            signed_url = download_url(result)
        return JSONResponse({"artifact_id": result.id, "report_id": result.report_id,
            "report_version_id": result.version_id, "download_url": signed_url, "expires_in": 900},
            headers={"Cache-Control": "private, no-store"})
    except ArtifactError as exc:
        raise http_error(409 if exc.code == "artifact_in_progress" else 503, exc.code, "PDF export is unavailable.") from exc
    except Exception as exc:
        raise http_error(503, "pdf_artifact_unavailable", "PDF export is temporarily unavailable.") from exc


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BrandingInput(StrictInput):
    brand_name: str = Field(min_length=1, max_length=255)
    logo_url: str | None = Field(default=None, max_length=2048)

    @field_validator("logo_url")
    @classmethod
    def validate_url_syntax(cls, value: str | None) -> str | None:
        if value:
            urlsplit(value)
        return value


class ConfigurationInput(StrictInput):
    builder: Literal["meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"]
    requested_slides: int = Field(ge=1, le=30, strict=True)
    template: str | None = Field(default=None, max_length=255)
    report_spec: dict[str, Any] | None = None
    template_version_id: int | None = None
    branding: BrandingInput | None = None


class OptionsInput(StrictInput):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    locale: Literal["en", "es"] = "en"
    ai_mode: Literal["standard"] = "standard"


class DeliveryInput(StrictInput):
    mode: Literal["GENERATE_ONLY", "EMAIL_PDF"] = "GENERATE_ONLY"
    recipients: list[EmailStr] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_recipients(self):
        if any(not str(email).isascii() for email in self.recipients):
            raise ValueError("V1 email delivery requires ASCII email addresses.")
        if (self.mode == "EMAIL_PDF") != bool(self.recipients):
            raise ValueError("EMAIL_PDF requires recipients; GENERATE_ONLY must not include recipients.")
        if len({str(email).lower() for email in self.recipients}) != len(self.recipients):
            raise ValueError("Recipients must be distinct.")
        return self


class SourceInput(StrictInput):
    integration_id: int = Field(gt=0, strict=True)
    integration_account_id: int | None = Field(default=None, gt=0, strict=True)
    dataset_id: int | None = Field(default=None, gt=0, strict=True)
    provider: Literal["meta", "instagram_business", "instagram_business_login", "meta_ads", "shopify"]
    source_type: Literal["facebook_pages", "instagram_business", "meta_ads", "shopify"]
    external_account_id: str = Field(min_length=1, max_length=255)
    position: int = Field(ge=0, le=1, strict=True)
    label: str | None = Field(default=None, max_length=255)


class RecurrenceInput(StrictInput):
    frequency: Literal["WEEKLY", "MONTHLY"]
    day_of_week: int | None = Field(default=None, ge=0, le=6, strict=True)
    day_of_month: int | None = Field(default=None, ge=1, le=31, strict=True)
    local_time: time
    timezone: str = Field(min_length=1, max_length=100)
    period_policy: Literal["previous_week", "previous_month"]

    def domain(self) -> Recurrence:
        try:
            return Recurrence(**self.model_dump())
        except ValueError as exc:
            raise http_error(422, "invalid_recurrence", str(exc)) from exc


class ScheduleCreateInput(RecurrenceInput):
    workspace_id: int = Field(gt=0, strict=True)
    name: str = Field(min_length=1, max_length=255)
    configuration: ConfigurationInput
    generation_options: OptionsInput = Field(default_factory=OptionsInput)
    sources: list[SourceInput] = Field(min_length=1, max_length=2)
    delivery: DeliveryInput = Field(default_factory=DeliveryInput)


class ScheduleUpdateInput(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    frequency: Literal["WEEKLY", "MONTHLY"] | None = None
    day_of_week: int | None = Field(default=None, ge=0, le=6, strict=True)
    day_of_month: int | None = Field(default=None, ge=1, le=31, strict=True)
    local_time: time | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    period_policy: Literal["previous_week", "previous_month"] | None = None
    configuration: ConfigurationInput | None = None
    generation_options: OptionsInput | None = None
    sources: list[SourceInput] | None = Field(default=None, min_length=1, max_length=2)
    expected_revision: int | None = Field(default=None, gt=0, strict=True)
    delivery: DeliveryInput | None = None

    @field_validator("name", "frequency", "local_time", "timezone", "period_policy", "configuration", "generation_options", "sources", "expected_revision", "delivery")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("This field cannot be null when supplied.")
        return value


class AvailabilityOutput(BaseModel):
    plan: str
    scheduling_enabled: bool
    reason: str | None
    upgrade_required: bool
    execution_available: bool | None = None
    execution_unavailable_reason: str | None = None


class SnapshotOutput(BaseModel):
    schema_version: Literal[1]
    recurrence: dict[str, Any]
    configuration: ExecutableReportConfiguration
    generation_options: GenerationOptions
    sources: list[SourceInput]
    delivery: DeliveryInput = Field(default_factory=DeliveryInput)

    @model_serializer(mode="wrap")
    def preserve_historical_hash(self, handler):
        result = handler(self)
        if "delivery" not in self.model_fields_set:
            result.pop("delivery", None)
        return result


class ScheduleOutput(BaseModel):
    id: int
    workspace_id: int
    created_by_user_id: int | None
    name: str
    status: Literal["ACTIVE", "PAUSED", "BLOCKED", "ARCHIVED"]
    status_reason: str | None
    frequency: Literal["WEEKLY", "MONTHLY"]
    day_of_week: int | None
    day_of_month: int | None
    local_time: str
    timezone: str
    period_policy: Literal["previous_week", "previous_month"]
    configuration_revision: int
    configuration_hash: str
    configuration_snapshot: SnapshotOutput
    delivery: DeliveryInput
    sources: list[SourceInput]
    availability: AvailabilityOutput
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ScheduleListOutput(BaseModel):
    items: list[ScheduleOutput]
    total: int
    limit: int
    offset: int
    availability: AvailabilityOutput


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    # PostgreSQL returns aware timestamps; SQLite test storage omits the offset.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def require_workspace_access(db: Session, user: User, workspace_id: int) -> None:
    if db.query(WorkspaceMember.id).filter(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id).first() is None:
        raise http_error(403, "forbidden", "Workspace access denied.")


def schedule_availability(db: Session, workspace_id: int) -> dict[str, Any]:
    subscription = get_workspace_subscription(db, workspace_id)
    plan = normalize_workspace_plan(subscription.plan if subscription else "free")
    paid = plan in {"starter", "pro", "advanced"}
    eligible = bool(paid and subscription and subscription.status in {"active", "trialing"}
                    and subscription.billing_status in {None, "active", "trialing"}
                    and (subscription.current_period_end is None or _utc(subscription.current_period_end) > _now()))
    return {"plan": plan, "scheduling_enabled": eligible,
            "reason": None if eligible else "paid_plan_required" if not paid else "subscription_inactive",
            "upgrade_required": not eligible}


def _require_paid(db: Session, workspace_id: int) -> None:
    availability = schedule_availability(db, workspace_id)
    if not availability["scheduling_enabled"]:
        raise http_error(403, availability["reason"], "Schedule Reports requires an eligible paid workspace.")


def _schedule(db: Session, schedule_id: int, workspace_id: int, *, lock: bool = False) -> ScheduledReport:
    query = db.query(ScheduledReport).filter(ScheduledReport.id == schedule_id, ScheduledReport.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update().populate_existing()
    schedule = query.first()
    if schedule is None:
        raise http_error(404, "scheduled_report_not_found", "Scheduled report not found.")
    return schedule


def _revision(db: Session, schedule: ScheduledReport) -> ScheduledReportRevision:
    return db.get(ScheduledReportRevision, (schedule.id, schedule.workspace_id, schedule.configuration_revision))


def _configuration(db: Session, workspace_id: int, payload: ConfigurationInput) -> dict[str, Any]:
    configuration = ExecutableReportConfiguration(
        builder=payload.builder, requested_slides=payload.requested_slides,
        template=payload.template, report_spec=payload.report_spec,
    )
    validate_executable_configuration(configuration)
    if payload.template_version_id is not None or payload.template is not None:
        raise GenerationError("template_not_executable", "Stored templates and render-only template overrides cannot be scheduled.")
    required_slides = 10 if payload.builder == "multi_source" else 5
    if payload.requested_slides != required_slides:
        raise GenerationError("configuration_not_repeatable", "Scheduling currently supports fixed 5-slide provider builders and the 10-slide Facebook/Instagram recipe.")
    plan = schedule_availability(db, workspace_id)["plan"]
    if required_slides > get_plan_limits(plan)["max_slides"]:
        raise GenerationError("plan_restricted", "Current plan cannot execute this configuration.", status_code=403)
    branding = resolve_report_branding_for_workspace(db, workspace_id, preferred_branding=payload.branding.model_dump() if payload.branding else None)
    configuration = ExecutableReportConfiguration(
        builder=payload.builder, requested_slides=required_slides, branding=branding,
        builder_contract=current_builder_contract(payload.builder),
    )
    validate_executable_configuration(configuration)
    return asdict(configuration)


def _sources(db: Session, workspace_id: int, payload: list[SourceInput]) -> list[dict[str, Any]]:
    """dataset_id is a validation anchor, not a future datasource selection rule.

    Execution must refresh each pinned provider/account and pass the resulting explicit
    dataset IDs to canonical generation. Never select a latest dataset from these rows.
    """
    result = []
    seen = set()
    for source in sorted(payload, key=lambda item: item.position):
        integration = db.get(Integration, source.integration_id)
        if integration is None or integration.workspace_id != workspace_id or integration.provider != source.provider:
            raise GenerationError("source_workspace_mismatch", "Source integration does not belong to the workspace/provider.")
        allowed = {"facebook_pages": {"meta"}, "instagram_business": {"meta", "instagram_business", "instagram_business_login"},
                   "meta_ads": {"meta_ads"}, "shopify": {"shopify"}}
        if source.provider not in allowed[source.source_type]:
            raise GenerationError("source_provider_mismatch", "Provider does not support this source type.")
        account = db.get(IntegrationAccount, source.integration_account_id) if source.integration_account_id else None
        if source.integration_account_id is not None:
            if account is None or account.workspace_id != workspace_id or account.integration_id != integration.id or account.external_account_id != source.external_account_id:
                raise GenerationError("source_account_mismatch", "Account does not belong to the pinned integration and workspace.")
        elif source.source_type != "shopify":
            raise GenerationError("source_account_required", "An explicit integration account is required.")
        if source.source_type == "shopify":
            connection = db.query(ShopifyConnection.id).filter(ShopifyConnection.workspace_id == workspace_id,
                ShopifyConnection.integration_id == integration.id, ShopifyConnection.shop_domain == source.external_account_id).first()
            if connection is None:
                raise GenerationError("shopify_source_mismatch", "The pinned Shopify shop does not belong to this workspace.")
        if source.dataset_id is not None:
            dataset = db.get(Dataset, source.dataset_id)
            if dataset is None or dataset.workspace_id != workspace_id:
                raise GenerationError("source_workspace_mismatch", "Dataset does not belong to the workspace.")
            validate_source_dataset_identity(SourceIdentity(**source.model_dump()), dataset, account)
        identity = (source.provider, source.source_type, source.external_account_id)
        if identity in seen or source.position != len(result):
            raise GenerationError("duplicate_source", "Sources must be distinct and positioned consecutively from zero.")
        seen.add(identity)
        result.append(source.model_dump())
    return result


def _validate_source_configuration(configuration: dict, sources: list[dict]) -> None:
    builder = configuration["builder"]
    expected = {"meta_pages": ["facebook_pages"], "instagram_business": ["instagram_business"], "meta_ads": ["meta_ads"],
                "shopify": ["shopify"], "multi_source": ["facebook_pages", "instagram_business"]}[builder]
    if sorted(source["source_type"] for source in sources) != sorted(expected):
        raise GenerationError("configuration_source_mismatch", "These sources cannot execute the selected builder.")


def _recurrence(snapshot: dict) -> Recurrence:
    return RecurrenceInput.model_validate(snapshot["recurrence"]).domain()


def _snapshot(recurrence: Recurrence, configuration: dict, options: dict, sources: list[dict], delivery: dict | None = None) -> dict:
    _validate_source_configuration(configuration, sources)
    recurrence_json = asdict(recurrence)
    recurrence_json["local_time"] = recurrence.local_time.isoformat(timespec="minutes")
    snapshot = {"schema_version": 1, "recurrence": recurrence_json, "configuration": configuration,
            "generation_options": options, "sources": sources}
    # Absence retains the original Phase 2 hash and means generate-only.
    if delivery and delivery.get("mode") != "GENERATE_ONLY":
        snapshot["delivery"] = DeliveryInput.model_validate(delivery).model_dump(mode="json")
    return snapshot


def configuration_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _append_revision(db: Session, schedule: ScheduledReport, snapshot: dict, user_id: int) -> None:
    db.add(ScheduledReportRevision(
        schedule_id=schedule.id, workspace_id=schedule.workspace_id, revision=schedule.configuration_revision,
        schema_version=1, configuration_hash=configuration_hash(snapshot), snapshot_json=snapshot, created_by_user_id=user_id,
    ))
    db.flush()
    for source in snapshot["sources"]:
        db.add(ScheduledReportSource(schedule_id=schedule.id, workspace_id=schedule.workspace_id,
                                     configuration_revision=schedule.configuration_revision, **source))
    db.flush()


def _source_problem(db: Session, sources: list[dict], workspace_id: int) -> str | None:
    for source in sources:
        integration = db.get(Integration, source["integration_id"])
        if integration is None or integration.workspace_id != workspace_id:
            return "source_missing"
        if integration.provider != source["provider"]:
            return "source_identity_changed"
        if integration.status != "connected":
            return "source_disconnected"
        if source["source_type"] == "shopify":
            connection = db.query(ShopifyConnection).filter(ShopifyConnection.workspace_id == workspace_id,
                ShopifyConnection.integration_id == integration.id, ShopifyConnection.shop_domain == source["external_account_id"]).first()
            if connection is None or connection.status != "connected":
                return "source_disconnected"
        if source["integration_account_id"] is not None:
            account = db.get(IntegrationAccount, source["integration_account_id"])
            if account is None or account.workspace_id != workspace_id or account.integration_id != integration.id or account.external_account_id != source["external_account_id"]:
                return "source_account_changed"
    return None


def _apply_recurrence(schedule: ScheduledReport, recurrence: Recurrence, now: datetime) -> None:
    for name, value in asdict(recurrence).items():
        setattr(schedule, name, value)
    schedule.next_run_at = next_occurrence(recurrence, after=now) if schedule.status == "ACTIVE" else None


def _out(db: Session, schedule: ScheduledReport) -> dict[str, Any]:
    revision = _revision(db, schedule)
    availability = schedule_availability(db, schedule.workspace_id)
    reason = availability["reason"] or _source_problem(db, revision.snapshot_json["sources"], schedule.workspace_id)
    try:
        validate_executable_configuration(ExecutableReportConfiguration(**revision.snapshot_json["configuration"]))
    except GenerationError as exc:
        reason = reason or exc.code
    return {
        "id": schedule.id, "workspace_id": schedule.workspace_id, "created_by_user_id": schedule.created_by_user_id,
        "name": schedule.name, "status": schedule.status, "status_reason": schedule.status_reason,
        **{key: getattr(schedule, key).isoformat(timespec="minutes") if key == "local_time" else getattr(schedule, key) for key in RECURRENCE_FIELDS},
        "configuration_revision": schedule.configuration_revision, "configuration_hash": revision.configuration_hash,
        "configuration_snapshot": revision.snapshot_json, "sources": revision.snapshot_json["sources"],
        "delivery": DeliveryInput.model_validate(revision.snapshot_json.get("delivery", {})),
        "availability": {**availability, "execution_available": schedule.status == "ACTIVE" and reason is None,
                         "execution_unavailable_reason": reason or (schedule.status.lower() if schedule.status != "ACTIVE" else None)},
        **{key: _utc(getattr(schedule, key)) for key in ("next_run_at", "last_run_at", "created_at", "updated_at", "archived_at")},
    }


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "schedule_conflict", "The schedule changed concurrently; reload and retry.") from exc


@router.get("", response_model=ScheduleListOutput)
def list_scheduled_reports(workspace_id: int = Query(gt=0), include_archived: bool = False,
                           limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    query = db.query(ScheduledReport).filter(ScheduledReport.workspace_id == workspace_id)
    if not include_archived:
        query = query.filter(ScheduledReport.status != "ARCHIVED")
    total = query.count()
    rows = query.order_by(ScheduledReport.id.desc()).offset(offset).limit(limit).all()
    return {"items": [_out(db, row) for row in rows], "total": total, "limit": limit, "offset": offset,
            "availability": schedule_availability(db, workspace_id)}


@router.get("/{schedule_id}", response_model=ScheduleOutput)
def get_scheduled_report(schedule_id: int, workspace_id: int = Query(gt=0),
                         current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    return _out(db, _schedule(db, schedule_id, workspace_id))


@router.post("", status_code=201, response_model=ScheduleOutput)
def create_scheduled_report(payload: ScheduleCreateInput, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, payload.workspace_id)
    _require_paid(db, payload.workspace_id)
    recurrence = RecurrenceInput.model_validate(payload.model_dump(include=set(RECURRENCE_FIELDS))).domain()
    try:
        sources = _sources(db, payload.workspace_id, payload.sources)
        snapshot = _snapshot(recurrence, _configuration(db, payload.workspace_id, payload.configuration),
                             asdict(GenerationOptions(**payload.generation_options.model_dump())), sources, payload.delivery.model_dump(mode="json"))
        problem = _source_problem(db, sources, payload.workspace_id)
        schedule = ScheduledReport(workspace_id=payload.workspace_id, created_by_user_id=current_user.id,
                                   name=payload.name, status="BLOCKED" if problem else "ACTIVE", status_reason=problem, configuration_revision=1)
        _apply_recurrence(schedule, recurrence, _now())
        db.add(schedule)
        db.flush()
        _append_revision(db, schedule, snapshot, current_user.id)
        _commit(db)
    except GenerationError as exc:
        db.rollback()
        raise http_error(exc.status_code, exc.code, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "schedule_conflict", "The schedule could not be saved atomically.") from exc
    return _out(db, schedule)


@router.patch("/{schedule_id}", response_model=ScheduleOutput)
def update_scheduled_report(schedule_id: int, payload: ScheduleUpdateInput, workspace_id: int = Query(gt=0),
                            current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    _require_paid(db, workspace_id)
    schedule = _schedule(db, schedule_id, workspace_id, lock=True)
    if schedule.status == "ARCHIVED":
        raise http_error(409, "schedule_archived", "Archived schedules cannot be edited or resumed.")
    if payload.expected_revision is not None and payload.expected_revision != schedule.configuration_revision:
        raise http_error(409, "revision_conflict", "Reload the current configuration before editing.")
    old_revision = _revision(db, schedule)
    old = old_revision.snapshot_json
    changes = payload.model_dump(exclude_unset=True)
    try:
        recurrence = RecurrenceInput.model_validate({**old["recurrence"], **{key: value for key, value in changes.items() if key in RECURRENCE_FIELDS}}).domain()
        configuration = _configuration(db, workspace_id, payload.configuration) if payload.configuration is not None else old["configuration"]
        sources = _sources(db, workspace_id, payload.sources) if payload.sources is not None else old["sources"]
        options = asdict(GenerationOptions(**payload.generation_options.model_dump())) if payload.generation_options is not None else old["generation_options"]
        delivery = payload.delivery.model_dump(mode="json") if payload.delivery is not None else old.get("delivery")
        snapshot = _snapshot(recurrence, configuration, options, sources, delivery)
        if configuration_hash(snapshot) != old_revision.configuration_hash:
            schedule.configuration_revision += 1
            _append_revision(db, schedule, snapshot, current_user.id)
            problem = _source_problem(db, sources, workspace_id)
            if schedule.status == "ACTIVE" and problem:
                schedule.status, schedule.status_reason = "BLOCKED", problem
            _apply_recurrence(schedule, recurrence, _now())
        if payload.name is not None:
            schedule.name = payload.name
        if schedule.status == "ACTIVE" and _utc(schedule.next_run_at) <= _now():
            _apply_recurrence(schedule, recurrence, _now())
        schedule.updated_at = _now()
        _commit(db)
    except (GenerationError, ValidationError) as exc:
        db.rollback()
        raise http_error(getattr(exc, "status_code", 422), getattr(exc, "code", "invalid_recurrence"), str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "revision_conflict", "The schedule changed concurrently; reload and retry.") from exc
    return _out(db, schedule)


def _transition(db: Session, schedule: ScheduledReport, status: str) -> dict:
    if schedule.status == "ARCHIVED":
        if status == "ARCHIVED":
            return _out(db, schedule)
        raise http_error(409, "schedule_archived", "Archived schedules cannot be resumed or paused.")
    if status == "ACTIVE":
        _require_paid(db, schedule.workspace_id)
        snapshot = _revision(db, schedule).snapshot_json
        problem = _source_problem(db, snapshot["sources"], schedule.workspace_id)
        if problem:
            raise http_error(409, problem, "The pinned datasource is not available.")
        try:
            validate_executable_configuration(ExecutableReportConfiguration(**snapshot["configuration"]))
        except GenerationError as exc:
            raise http_error(exc.status_code, exc.code, str(exc)) from exc
        if schedule.status == "ACTIVE" and _utc(schedule.next_run_at) > _now():
            return _out(db, schedule)
    schedule.status, schedule.status_reason = status, None
    schedule.archived_at = _now() if status == "ARCHIVED" else None
    _apply_recurrence(schedule, _recurrence(_revision(db, schedule).snapshot_json), _now())
    schedule.updated_at = _now()
    _commit(db)
    return _out(db, schedule)


@router.post("/{schedule_id}/pause", response_model=ScheduleOutput)
def pause_scheduled_report(schedule_id: int, workspace_id: int = Query(gt=0),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    return _transition(db, _schedule(db, schedule_id, workspace_id, lock=True), "PAUSED")


@router.post("/{schedule_id}/resume", response_model=ScheduleOutput)
def resume_scheduled_report(schedule_id: int, workspace_id: int = Query(gt=0),
                            current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    return _transition(db, _schedule(db, schedule_id, workspace_id, lock=True), "ACTIVE")


@router.post("/{schedule_id}/archive", response_model=ScheduleOutput)
def archive_scheduled_report(schedule_id: int, workspace_id: int = Query(gt=0),
                             current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    return _transition(db, _schedule(db, schedule_id, workspace_id, lock=True), "ARCHIVED")


@router.get("/{schedule_id}/runs")
def list_scheduled_report_runs(schedule_id: int, workspace_id: int = Query(gt=0),
                               limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_workspace_access(db, current_user, workspace_id)
    _schedule(db, schedule_id, workspace_id)
    query = db.query(ScheduledReportRun).filter(ScheduledReportRun.schedule_id == schedule_id, ScheduledReportRun.workspace_id == workspace_id)
    rows = query.order_by(ScheduledReportRun.scheduled_for.desc(), ScheduledReportRun.id.desc()).offset(offset).limit(limit).all()
    from .scheduled_report_execution import run_output
    items = [run_output(row) for row in rows]
    return {"items": items, "total": query.count(), "limit": limit, "offset": offset}


@router.post("/{schedule_id}/run-now", status_code=202)
def run_scheduled_report_now(schedule_id: int, response: Response, workspace_id: int = Query(gt=0),
                             idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
                             current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Durably accept execution; the same worker pipeline handles manual and recurring runs."""
    from .scheduled_report_execution import request_run_now, run_output, quota_state
    require_workspace_access(db, current_user, workspace_id)
    schedule = _schedule(db, schedule_id, workspace_id, lock=True)
    try:
        run, created = request_run_now(db, schedule, idempotency_key)
        if created:
            quota = quota_state(db, workspace_id)
            if quota["capacity_remaining"] == 0:
                run.status, run.error_code, run.failure_class = "QUOTA_BLOCKED", "monthly_report_limit_reached", "TERMINAL"
                run.completed_at, run.quota_json = _now(), quota
        _commit(db)
        response.status_code = 403 if run.status == "QUOTA_BLOCKED" else 200 if run.status == "SUCCEEDED" else 202
        return run_output(run)
    except GenerationError as exc:
        db.rollback()
        raise http_error(exc.status_code, exc.code, str(exc)) from exc
