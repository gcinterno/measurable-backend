from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .deps import get_db, require_admin_user
from .errors import http_error
from .models import ReportTemplate, ReportTemplateVersion, User, Workspace
from .report_spec import (
    FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC,
    InvalidReportSpecError,
    assert_valid_report_spec,
    validate_report_spec_datasource_requirements,
)


REPORT_TEMPLATE_STATUSES = ("draft", "published", "archived")
REPORT_TEMPLATE_GENERATION_MODES = ("manual_template", "ai_generated", "system")
FACEBOOK_INSTAGRAM_REFERENCE_TEMPLATE_SLUG = "facebook-instagram-10-reference"

router = APIRouter(prefix="/report-templates", tags=["report-templates"])


class ReportTemplateCreateIn(BaseModel):
    workspace_id: int | None = None
    name: str
    description: str | None = None
    slug: str | None = None
    status: str = "draft"
    generation_mode: str = "manual_template"
    template_type: str = "custom"
    datasource_requirements: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    spec_json: dict[str, Any] | None = None
    notes: str | None = None
    change_summary: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Template name is required.")
        return normalized

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in REPORT_TEMPLATE_STATUSES:
            raise ValueError("Invalid template status.")
        return normalized

    @field_validator("generation_mode")
    @classmethod
    def validate_generation_mode(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in REPORT_TEMPLATE_GENERATION_MODES:
            raise ValueError("Invalid template generation_mode.")
        return normalized

    @field_validator("template_type")
    @classmethod
    def validate_template_type(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("template_type is required.")
        return normalized


class ReportTemplateUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    slug: str | None = None
    generation_mode: str | None = None
    template_type: str | None = None
    datasource_requirements: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ReportTemplateVersionCreateIn(BaseModel):
    spec_json: dict[str, Any]
    schema_version: str | None = None
    notes: str | None = None
    change_summary: str | None = None


class ReportTemplatePublishIn(BaseModel):
    version_id: int


class ReportTemplateDuplicateIn(BaseModel):
    name: str | None = None
    slug: str | None = None
    workspace_id: int | None = None


class ReportTemplateVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_template_id: int
    version_number: int
    schema_version: str
    spec_json: dict[str, Any]
    created_by_user_id: int | None = None
    notes: str | None = None
    change_summary: str | None = None
    published_at: datetime | None = None
    created_at: datetime


class ReportTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int | None = None
    name: str
    description: str | None = None
    slug: str
    status: str
    generation_mode: str
    template_type: str
    datasource_requirements: dict[str, Any]
    active_version_id: int | None = None
    published_version_id: int | None = None
    created_by_user_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "report-template"


def _normalize_optional_slug(value: str | None, fallback_name: str) -> str:
    return _slugify(value or fallback_name)


def _workspace_filter(query: Any, workspace_id: int | None) -> Any:
    if workspace_id is None:
        return query.filter(ReportTemplate.workspace_id.is_(None))
    return query.filter(ReportTemplate.workspace_id == workspace_id)


def _find_template_by_slug(db: Session, workspace_id: int | None, slug: str) -> ReportTemplate | None:
    query = db.query(ReportTemplate).filter(ReportTemplate.slug == slug)
    return _workspace_filter(query, workspace_id).first()


def _ensure_unique_slug(db: Session, workspace_id: int | None, slug: str, *, exclude_id: int | None = None) -> None:
    query = db.query(ReportTemplate).filter(ReportTemplate.slug == slug)
    query = _workspace_filter(query, workspace_id)
    if exclude_id is not None:
        query = query.filter(ReportTemplate.id != exclude_id)
    if query.first() is not None:
        raise http_error(409, "template_slug_exists", "A report template with this slug already exists.")


def _next_available_slug(db: Session, workspace_id: int | None, preferred_slug: str) -> str:
    base_slug = _slugify(preferred_slug)
    candidate = base_slug
    suffix = 2
    while _find_template_by_slug(db, workspace_id, candidate) is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _validate_workspace_exists(db: Session, workspace_id: int | None) -> None:
    if workspace_id is not None and db.get(Workspace, workspace_id) is None:
        raise http_error(404, "workspace_not_found", "Workspace not found.")


def _validation_error_message(errors: tuple[Any, ...]) -> str:
    return "; ".join(f"{error.code}: {error.message}" for error in errors)


def validate_template_datasource_requirements(payload: dict[str, Any]) -> dict[str, Any]:
    result = validate_report_spec_datasource_requirements(payload)
    if not result.valid:
        raise http_error(
            400,
            "invalid_datasource_requirements",
            _validation_error_message(result.errors),
        )
    return dict(payload)


def validate_persisted_report_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        spec = assert_valid_report_spec(payload)
    except InvalidReportSpecError as exc:
        raise http_error(
            400,
            "invalid_report_spec",
            _validation_error_message(exc.result.errors),
        ) from exc
    if not spec.template_id:
        raise http_error(400, "invalid_report_spec", "MISSING_TEMPLATE_ID: ReportSpec template_id is required.")
    return spec.as_dict()


def _version_out(version: ReportTemplateVersion) -> ReportTemplateVersionOut:
    return ReportTemplateVersionOut(
        id=version.id,
        report_template_id=version.report_template_id,
        version_number=version.version_number,
        schema_version=version.schema_version,
        spec_json=dict(version.spec_json or {}),
        created_by_user_id=version.created_by_user_id,
        notes=version.notes,
        change_summary=version.change_summary,
        published_at=version.published_at,
        created_at=version.created_at,
    )


def _template_out(template: ReportTemplate) -> ReportTemplateOut:
    return ReportTemplateOut(
        id=template.id,
        workspace_id=template.workspace_id,
        name=template.name,
        description=template.description,
        slug=template.slug,
        status=template.status,
        generation_mode=template.generation_mode,
        template_type=template.template_type,
        datasource_requirements=dict(template.datasource_requirements or {}),
        active_version_id=template.active_version_id,
        published_version_id=template.published_version_id,
        created_by_user_id=template.created_by_user_id,
        metadata=dict(template.metadata_json or {}),
        archived_at=template.archived_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _get_template(db: Session, template_id: int) -> ReportTemplate:
    template = db.get(ReportTemplate, template_id)
    if template is None:
        raise http_error(404, "template_not_found", "Report template not found.")
    return template


def _get_template_version(db: Session, template: ReportTemplate, version_id: int) -> ReportTemplateVersion:
    version = (
        db.query(ReportTemplateVersion)
        .filter(
            ReportTemplateVersion.id == version_id,
            ReportTemplateVersion.report_template_id == template.id,
        )
        .first()
    )
    if version is None:
        raise http_error(404, "template_version_not_found", "Report template version not found.")
    return version


def _create_template_version(
    db: Session,
    template: ReportTemplate,
    payload: ReportTemplateVersionCreateIn,
    created_by_user_id: int | None,
) -> ReportTemplateVersion:
    if template.status == "archived":
        raise http_error(409, "template_archived", "Archived report templates cannot be edited.")
    spec_json = validate_persisted_report_spec_payload(payload.spec_json)
    schema_version = str(spec_json.get("schema_version") or "").strip()
    if payload.schema_version is not None and payload.schema_version != schema_version:
        raise http_error(400, "schema_version_mismatch", "schema_version does not match spec_json.schema_version.")
    latest_version_number = (
        db.query(func.max(ReportTemplateVersion.version_number))
        .filter(ReportTemplateVersion.report_template_id == template.id)
        .scalar()
        or 0
    )
    version = ReportTemplateVersion(
        report_template_id=template.id,
        version_number=int(latest_version_number) + 1,
        schema_version=schema_version,
        spec_json=spec_json,
        created_by_user_id=created_by_user_id,
        notes=payload.notes,
        change_summary=payload.change_summary,
    )
    db.add(version)
    db.flush()
    template.active_version_id = version.id
    if template.published_version_id is not None:
        template.status = "draft"
    db.add(template)
    return version


def seed_facebook_instagram_reference_template(
    db: Session,
    *,
    created_by_user_id: int | None = None,
) -> tuple[ReportTemplate, ReportTemplateVersion]:
    existing = _find_template_by_slug(db, None, FACEBOOK_INSTAGRAM_REFERENCE_TEMPLATE_SLUG)
    if existing is not None and existing.published_version_id is not None:
        return existing, _get_template_version(db, existing, int(existing.published_version_id))

    spec_json = validate_persisted_report_spec_payload(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC.as_dict())
    datasource_requirements = validate_template_datasource_requirements(
        dict(spec_json.get("datasource_requirements") or {})
    )
    template = existing or ReportTemplate(
        workspace_id=None,
        name="Facebook + Instagram Executive",
        description="System reference template for canonical Facebook Pages + Instagram Business reporting.",
        slug=FACEBOOK_INSTAGRAM_REFERENCE_TEMPLATE_SLUG,
        status="draft",
        generation_mode="system",
        template_type="system_reference",
        datasource_requirements=datasource_requirements,
        created_by_user_id=created_by_user_id,
        metadata_json={"reference_reportspec_id": spec_json.get("id")},
    )
    db.add(template)
    db.flush()
    version = ReportTemplateVersion(
        report_template_id=template.id,
        version_number=1,
        schema_version=str(spec_json.get("schema_version")),
        spec_json=spec_json,
        created_by_user_id=created_by_user_id,
        notes="Initial system reference template.",
        change_summary="Seed canonical Facebook + Instagram ReportSpec.",
        published_at=_now(),
    )
    db.add(version)
    db.flush()
    template.status = "published"
    template.active_version_id = version.id
    template.published_version_id = version.id
    db.add(template)
    db.commit()
    db.refresh(template)
    db.refresh(version)
    return template, version


@router.get("", response_model=list[ReportTemplateOut])
def list_report_templates(
    workspace_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[ReportTemplateOut]:
    query = db.query(ReportTemplate)
    if workspace_id is not None:
        query = query.filter(ReportTemplate.workspace_id == workspace_id)
    if status is not None:
        query = query.filter(ReportTemplate.status == status)
    templates = query.order_by(ReportTemplate.updated_at.desc(), ReportTemplate.id.desc()).all()
    return [_template_out(template) for template in templates]


@router.post("", response_model=ReportTemplateOut, status_code=201)
def create_report_template(
    payload: ReportTemplateCreateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    if payload.status != "draft":
        raise http_error(400, "invalid_template_status", "New report templates must start as draft.")
    _validate_workspace_exists(db, payload.workspace_id)
    datasource_requirements = validate_template_datasource_requirements(payload.datasource_requirements)
    slug = _normalize_optional_slug(payload.slug, payload.name)
    _ensure_unique_slug(db, payload.workspace_id, slug)
    template = ReportTemplate(
        workspace_id=payload.workspace_id,
        name=payload.name,
        description=payload.description,
        slug=slug,
        status=payload.status,
        generation_mode=payload.generation_mode,
        template_type=payload.template_type,
        datasource_requirements=datasource_requirements,
        created_by_user_id=current_user.id,
        metadata_json=dict(payload.metadata),
    )
    try:
        db.add(template)
        db.flush()
        if payload.spec_json is not None:
            _create_template_version(
                db,
                template,
                ReportTemplateVersionCreateIn(
                    spec_json=payload.spec_json,
                    notes=payload.notes,
                    change_summary=payload.change_summary,
                ),
                current_user.id,
            )
        db.commit()
        db.refresh(template)
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "template_conflict", "Report template could not be saved.") from exc
    return _template_out(template)


@router.get("/{template_id}", response_model=ReportTemplateOut)
def get_report_template(
    template_id: int,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    return _template_out(_get_template(db, template_id))


@router.patch("/{template_id}", response_model=ReportTemplateOut)
def update_report_template(
    template_id: int,
    payload: ReportTemplateUpdateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    template = _get_template(db, template_id)
    if template.status == "archived":
        raise http_error(409, "template_archived", "Archived report templates cannot be edited.")
    if template.status == "published":
        raise http_error(409, "template_published", "Create a new draft version before editing a published template.")
    if payload.name is not None:
        name = str(payload.name or "").strip()
        if not name:
            raise http_error(400, "invalid_template_name", "Template name is required.")
        template.name = name
    if payload.description is not None:
        template.description = payload.description
    if payload.slug is not None:
        slug = _normalize_optional_slug(payload.slug, template.name)
        _ensure_unique_slug(db, template.workspace_id, slug, exclude_id=template.id)
        template.slug = slug
    if payload.generation_mode is not None:
        generation_mode = str(payload.generation_mode or "").strip()
        if generation_mode not in REPORT_TEMPLATE_GENERATION_MODES:
            raise http_error(400, "invalid_generation_mode", "Invalid template generation_mode.")
        template.generation_mode = generation_mode
    if payload.template_type is not None:
        template_type = str(payload.template_type or "").strip()
        if not template_type:
            raise http_error(400, "invalid_template_type", "template_type is required.")
        template.template_type = template_type
    if payload.datasource_requirements is not None:
        template.datasource_requirements = validate_template_datasource_requirements(payload.datasource_requirements)
    if payload.metadata is not None:
        template.metadata_json = dict(payload.metadata)
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_out(template)


@router.post("/{template_id}/duplicate", response_model=ReportTemplateOut, status_code=201)
def duplicate_report_template(
    template_id: int,
    payload: ReportTemplateDuplicateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    source = _get_template(db, template_id)
    workspace_id = source.workspace_id if payload.workspace_id is None else payload.workspace_id
    _validate_workspace_exists(db, workspace_id)
    name = str(payload.name or f"{source.name} Copy").strip()
    slug = _next_available_slug(db, workspace_id, _normalize_optional_slug(payload.slug, name))
    duplicate = ReportTemplate(
        workspace_id=workspace_id,
        name=name,
        description=source.description,
        slug=slug,
        status="draft",
        generation_mode=source.generation_mode,
        template_type=source.template_type,
        datasource_requirements=dict(source.datasource_requirements or {}),
        created_by_user_id=current_user.id,
        metadata_json=dict(source.metadata_json or {}),
    )
    db.add(duplicate)
    db.flush()
    source_version = (
        db.query(ReportTemplateVersion)
        .filter(ReportTemplateVersion.report_template_id == source.id)
        .order_by(ReportTemplateVersion.version_number.desc())
        .first()
    )
    if source_version is not None:
        version = ReportTemplateVersion(
            report_template_id=duplicate.id,
            version_number=1,
            schema_version=source_version.schema_version,
            spec_json=dict(source_version.spec_json or {}),
            created_by_user_id=current_user.id,
            notes="Duplicated from template.",
            change_summary=f"Duplicated from template {source.id} version {source_version.version_number}.",
        )
        db.add(version)
        db.flush()
        duplicate.active_version_id = version.id
    db.add(duplicate)
    db.commit()
    db.refresh(duplicate)
    return _template_out(duplicate)


@router.get("/{template_id}/versions", response_model=list[ReportTemplateVersionOut])
def list_report_template_versions(
    template_id: int,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[ReportTemplateVersionOut]:
    template = _get_template(db, template_id)
    versions = (
        db.query(ReportTemplateVersion)
        .filter(ReportTemplateVersion.report_template_id == template.id)
        .order_by(ReportTemplateVersion.version_number.asc())
        .all()
    )
    return [_version_out(version) for version in versions]


@router.post("/{template_id}/versions", response_model=ReportTemplateVersionOut, status_code=201)
def create_report_template_version(
    template_id: int,
    payload: ReportTemplateVersionCreateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateVersionOut:
    template = _get_template(db, template_id)
    version = _create_template_version(db, template, payload, current_user.id)
    db.commit()
    db.refresh(version)
    return _version_out(version)


@router.get("/{template_id}/versions/{version_id}", response_model=ReportTemplateVersionOut)
def get_report_template_version(
    template_id: int,
    version_id: int,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateVersionOut:
    template = _get_template(db, template_id)
    return _version_out(_get_template_version(db, template, version_id))


@router.post("/{template_id}/publish", response_model=ReportTemplateOut)
def publish_report_template(
    template_id: int,
    payload: ReportTemplatePublishIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    template = _get_template(db, template_id)
    if template.status == "archived":
        raise http_error(409, "template_archived", "Archived report templates cannot be published.")
    version = _get_template_version(db, template, payload.version_id)
    if version.published_at is None:
        version.published_at = _now()
        db.add(version)
    template.status = "published"
    template.active_version_id = version.id
    template.published_version_id = version.id
    template.archived_at = None
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_out(template)


@router.post("/{template_id}/archive", response_model=ReportTemplateOut)
def archive_report_template(
    template_id: int,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReportTemplateOut:
    template = _get_template(db, template_id)
    if template.status != "archived":
        template.status = "archived"
        template.archived_at = _now()
        db.add(template)
        db.commit()
        db.refresh(template)
    return _template_out(template)


__all__ = [
    "FACEBOOK_INSTAGRAM_REFERENCE_TEMPLATE_SLUG",
    "REPORT_TEMPLATE_GENERATION_MODES",
    "REPORT_TEMPLATE_STATUSES",
    "ReportTemplateCreateIn",
    "ReportTemplateDuplicateIn",
    "ReportTemplateOut",
    "ReportTemplatePublishIn",
    "ReportTemplateUpdateIn",
    "ReportTemplateVersionCreateIn",
    "ReportTemplateVersionOut",
    "router",
    "seed_facebook_instagram_reference_template",
    "validate_persisted_report_spec_payload",
    "validate_template_datasource_requirements",
]
