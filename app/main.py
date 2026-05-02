import csv
import io
import json
import logging
import os
from urllib.parse import urlencode
from datetime import date, timedelta
from time import perf_counter
from typing import Any

from fastapi import Body, Depends, FastAPI, File, Form, Query, Request, UploadFile, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from .deps import (
    get_current_user,
    get_current_user_for_report_read,
    get_db,
    load_user_by_email,
    user_logo_column_available,
)
from .errors import http_error
import boto3

from .config import settings
from .integrations.meta_ads import (
    META_ADS_OAUTH_SCOPE,
    META_PAGES_OAUTH_SCOPE,
    debug_ads_permissions,
    debug_token,
    decode_state,
    encode_state,
    exchange_code_for_token,
    fetch_campaign_insights,
    fetch_instagram_insights_metric_with_metadata,
    fetch_page_info,
    fetch_page_info_with_metadata,
    fetch_page_insights,
    fetch_page_insights_timeseries,
    fetch_page_posts,
    fetch_post_metrics,
    exchange_pages_code_for_token,
    get_meta_pages_redirect_uri,
    get_businesses,
    get_owned_ad_accounts,
    list_pages,
    oauth_connect_url,
    oauth_connect_pages_url,
)
from .models import (
    Conversation,
    Dataset,
    DatasetFile,
    Export,
    Integration,
    IntegrationAccount,
    MetaPage,
    IntegrationToken,
    Message,
    Report,
    ReportBlock,
    ReportVersion,
    Schedule,
    User,
    Workspace,
    WorkspaceMember,
)
from .schemas import (
    ChatMessageIn,
    ChatReplyOut,
    ConversationOut,
    DatasetDetailOut,
    DatasetUploadOut,
    InstagramBusinessReportCreateIn,
    LoginIn,
    MeOut,
    MeUpdateIn,
    IntegrationSchema,
    MetaPageOut,
    MetaPagesReportCreateIn,
    MetaPagesReportCreateOut,
    MetaPagesSyncOut,
    MetaSelectAccountIn,
    MetaSelectAccountManualIn,
    MetaSelectPageIn,
    MetaSetTokenManualIn,
    MessageOut,
    PlanLimitsOut,
    RegisterIn,
    RegisterOut,
    ReportBlockOut,
    ReportExportOut,
    ReportListItemOut,
    ReportBlockUpdateIn,
    ReportCreateIn,
    ReportOut,
    ReportVersionOut,
    ScheduleCreateIn,
    ScheduleSchema,
    ScheduleUpdateIn,
    TokenOut,
    WorkspaceUpdateIn,
    WorkspaceCreateIn,
    WorkspaceOut,
)
from .security import create_access_token, create_report_export_token, hash_password, verify_password
from .ai_agents import (
    build_ai_agent_metadata,
    build_ai_agent_plan_context,
    normalize_ai_mode,
    run_ai_agents_pipeline,
)
from .services import (
    build_export_payload,
    build_conversation_title,
    build_meta_pages_ai_payload,
    generate_meta_pages_ai_summary,
    generate_thumbnail_from_export_page,
    build_meta_pages_reach_chart_data,
    build_meta_pages_reach_insight,
    build_meta_pages_recent_posts_summary,
    build_meta_pages_summary,
    count_workspace_storage_bytes,
    enforce_storage_limit,
    enforce_export_capability,
    extract_meta_pages_report_inputs,
    enforce_monthly_report_limit,
    enqueue_job,
    finalize_export_response,
    generate_workspace_ai_reply,
    get_workspace_plan,
    get_plan_limits,
    resolve_report_slide_limits,
    resolve_meta_pages_timeframe,
    register_user_with_default_workspace,
    generate_pdf_from_export_page,
    normalize_report_locale,
    normalize_meta_recent_posts,
    build_report_pdf_export_url,
    resolve_workspace_branding,
    store_report_thumbnail,
    trigger_export_service,
    _generate_download_url,
)

logger = logging.getLogger(__name__)
DEFAULT_GENERATED_REPORT_SLIDE_COUNT = 2


def _workspace_out(db: Session, workspace: Workspace) -> WorkspaceOut:
    plan = get_workspace_plan(db, workspace.id)
    plan_limits = PlanLimitsOut(**get_plan_limits(plan))
    storage_used_bytes = count_workspace_storage_bytes(db, workspace.id)
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        logo_url=workspace.logo_url,
        branding=resolve_workspace_branding(workspace.id),
        plan=plan,
        plan_limits=plan_limits,
        storage_used_bytes=storage_used_bytes,
        storage_limit_bytes=plan_limits.storage_limit_bytes,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _get_conversation_for_workspace(
    db: Session,
    *,
    workspace_id: int,
    conversation_id: int,
) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise http_error(404, "conversation_not_found", "Conversation not found.")
    if conversation.workspace_id != workspace_id:
        raise http_error(403, "forbidden", "Conversation access denied.")
    return conversation


def _sqlalchemy_error_log_payload(exc: Exception, *, stage: str) -> dict[str, object]:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None) if orig is not None else None
    return {
        "stage": stage,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "driver_exception_type": type(orig).__name__ if orig is not None else None,
        "driver_message": str(orig) if orig is not None else None,
        "pgcode": getattr(orig, "pgcode", None),
        "schema_name": getattr(diag, "schema_name", None) if diag is not None else None,
        "table_name": getattr(diag, "table_name", None) if diag is not None else None,
        "column_name": getattr(diag, "column_name", None) if diag is not None else None,
        "constraint_name": getattr(diag, "constraint_name", None) if diag is not None else None,
        "sqlalchemy_exception": exc.__class__.__name__,
    }

def _report_metadata(report: Report) -> dict[str, object]:
    if not report.description:
        return {}
    try:
        payload = json.loads(report.description)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _cors_error_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin and origin in CORS_ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def _report_locale(report: Report) -> str:
    return normalize_report_locale(_report_metadata(report).get("locale"))


def _report_branding(report: Report) -> dict[str, object]:
    metadata = _report_metadata(report)
    branding = metadata.get("branding") if isinstance(metadata.get("branding"), dict) else None
    if branding is not None:
        return {"logo_url": str(branding.get("logo_url")) if branding.get("logo_url") else None}
    return resolve_workspace_branding(report.workspace_id)


def _user_branding(user: User | None) -> dict[str, object]:
    if not user or not user_logo_column_available():
        return {"logo_url": None}
    return {"logo_url": str(user.logo_url) if user.logo_url else None}


def _report_thumbnail_url(report: Report) -> str | None:
    metadata = _report_metadata(report)
    thumbnail_key = metadata.get("thumbnail_s3_key") if isinstance(metadata, dict) else None
    if not thumbnail_key:
        return None
    try:
        return _generate_download_url(str(thumbnail_key))
    except Exception:
        logger.exception("Failed to generate thumbnail URL", extra={"report_id": report.id})
        return None


def _report_timeframe(report: Report) -> dict[str, object] | None:
    metadata = _report_metadata(report)
    timeframe = metadata.get("timeframe") if isinstance(metadata, dict) else None
    return timeframe if isinstance(timeframe, dict) else None


def _timeframe_log_payload(
    report: Report,
    *,
    source: str,
    version_id: int | None = None,
) -> dict[str, object]:
    timeframe = _report_timeframe(report) or {}
    payload: dict[str, object] = {
        "report_id": report.id,
        "source": source,
        "timeframe_key": timeframe.get("key"),
        "since": timeframe.get("since"),
        "until": timeframe.get("until"),
        "label": timeframe.get("label"),
    }
    if version_id is not None:
        payload["version_id"] = version_id
    return payload


def _block_semantic_name(block: ReportBlock | None) -> str | None:
    if not block or not block.data_json:
        return None
    try:
        data = json.loads(block.data_json)
    except json.JSONDecodeError:
        return None
    return str(data.get("semantic_name")) if isinstance(data, dict) and data.get("semantic_name") else None


def _report_version_out(
    db: Session,
    *,
    report: Report,
    report_version: ReportVersion,
) -> ReportVersionOut:
    blocks = (
        db.query(ReportBlock)
        .filter(ReportBlock.report_version_id == report_version.id)
        .order_by(ReportBlock.order.asc())
        .all()
    )
    metadata = _report_metadata(report)
    logger.info(
        "[MetaTimeframeBackend][render.full]",
        extra=_timeframe_log_payload(
            report,
            source="report_version_api",
            version_id=report_version.id,
        ),
    )
    return ReportVersionOut(
        id=report_version.id,
        version_id=report_version.id,
        report_id=report_version.report_id,
        version=report_version.version,
        description=metadata,
        timeframe=_report_timeframe(report),
        locale=_report_locale(report),
        branding=_report_branding(report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report_version.created_at,
        updated_at=report_version.updated_at,
        blocks=blocks,
    )


def _resolve_report_version_for_path(
    db: Session,
    *,
    report_id: int,
    version_value: int,
) -> tuple[ReportVersion | None, str]:
    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id, ReportVersion.version == version_value)
        .first()
    )
    if report_version:
        return report_version, "version_number"

    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id, ReportVersion.id == version_value)
        .first()
    )
    if report_version:
        return report_version, "internal_version_id"

    return None, "not_found"


def _update_report_metadata(db: Session, report: Report, updates: dict[str, object]) -> Report:
    metadata = _report_metadata(report)
    metadata.update(updates)
    report.description = json.dumps(metadata)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def _generate_and_store_report_thumbnail(
    *,
    db: Session,
    report: Report,
    report_version: ReportVersion,
    user_id: int,
    sync_branding_from_user: bool = False,
) -> str | None:
    report_branding = _report_branding(report)
    report_logo_url = str(report_branding.get("logo_url")) if report_branding.get("logo_url") else None
    report_version_logo_url = report_logo_url
    user = db.get(User, user_id)
    user_logo_url = str(user.logo_url) if user and user.logo_url else None
    final_branding_source = "report_metadata"

    if sync_branding_from_user and user_logo_url and not report_logo_url:
        report = _update_report_metadata(db, report, {"branding": {"logo_url": user_logo_url}})
        report_branding = _report_branding(report)
        report_logo_url = (
            str(report_branding.get("logo_url")) if report_branding.get("logo_url") else None
        )
        report_version_logo_url = report_logo_url
        final_branding_source = "user_profile_sync"
    elif not report_logo_url and user_logo_url:
        final_branding_source = "user_profile_available_but_not_persisted"
    elif not report_logo_url:
        final_branding_source = "none"

    locale = _report_locale(report)
    export_url = build_report_pdf_export_url(report, report_version, locale=locale)
    export_token = create_report_export_token(
        str(user_id),
        report_id=report.id,
        version=report_version.version,
    )
    logger.info(
        "[MetaTimeframeBackend][render.thumbnail]",
        extra=_timeframe_log_payload(
            report,
            source="thumbnail_export_page",
            version_id=report_version.id,
        ),
    )
    logger.info(
        "Report thumbnail branding resolved",
        extra={
            "report_id": report.id,
            "thumbnail_target_prefix": f"report-thumbnails/{report.id}/",
            "resolved_report_branding_logo_url": report_logo_url,
            "resolved_report_version_branding_logo_url": report_version_logo_url,
            "resolved_user_logo_url": user_logo_url,
            "final_branding_source_used": final_branding_source,
            "report_version": report_version.version,
        },
    )
    screenshot_bytes, thumbnail_debug = generate_thumbnail_from_export_page(
        export_url=export_url,
        report_id=report.id,
        auth_token=export_token,
    )
    thumbnail_s3_key = store_report_thumbnail(report.id, screenshot_bytes)
    _update_report_metadata(
        db,
        report,
        {
            "thumbnail_s3_key": thumbnail_s3_key,
            "thumbnail_generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(
        "Report thumbnail stored",
        extra={
            "report_id": report.id,
            "thumbnail_target_key": thumbnail_s3_key,
            "slide_selector_used": thumbnail_debug.get("slide_selector_used"),
            "resolved_report_branding_logo_url": report_logo_url,
            "resolved_report_version_branding_logo_url": report_version_logo_url,
            "resolved_user_logo_url": user_logo_url,
            "final_branding_source_used": final_branding_source,
        },
    )
    return thumbnail_s3_key


def _parse_cors_origins(raw: str | None) -> list[str]:
    if not raw:
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


CORS_ALLOWED_ORIGINS = _parse_cors_origins(settings.cors_origins)
CORS_ALLOW_CREDENTIALS = True
EXPECTED_BACKEND_PORT = 8001
META_PAGES_REACH_METRIC_CANDIDATES = [
    "page_impressions_unique",
]
META_PAGES_IMPRESSIONS_METRIC_CANDIDATES = [
    "page_impressions",
]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def log_cors_configuration() -> None:
    logger.info(
        "CORS configuration loaded",
        extra={
            "allowed_origins": CORS_ALLOWED_ORIGINS,
            "expected_port": EXPECTED_BACKEND_PORT,
        },
    )


@app.get("/debug/cors")
def debug_cors() -> dict[str, object]:
    return {
        "allow_origins": CORS_ALLOWED_ORIGINS,
        "allow_credentials": CORS_ALLOW_CREDENTIALS,
    }


def _make_json_safe(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, UploadFile):
        return {
            "filename": value.filename,
            "content_type": value.content_type,
        }
    if isinstance(value, FormData):
        return {key: _make_json_safe(item) for key, item in value.multi_items()}
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "body": _make_json_safe(exc.body),
        },
        headers=_cors_error_headers(request),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled exception during request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "content_type": request.headers.get("content-type"),
            "origin": request.headers.get("origin"),
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_server_error",
                "message": "Internal server error.",
            }
        },
        headers=_cors_error_headers(request),
    )


@app.post("/auth/register", response_model=RegisterOut, status_code=201)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> RegisterOut:
    email = payload.email.strip()
    full_name = payload.full_name.strip() if payload.full_name and payload.full_name.strip() else None

    if not email:
        raise http_error(400, "invalid_email", "Email is required.")
    if not payload.password:
        raise http_error(400, "invalid_password", "Password is required.")
    if len(payload.password) < 8:
        raise http_error(
            400,
            "invalid_password",
            "Password must be at least 8 characters long.",
        )

    try:
        user, workspace, subscription = register_user_with_default_workspace(
            db,
            email=email,
            password_hash=hash_password(payload.password),
            full_name=full_name,
        )
        return RegisterOut(
            user_id=user.id,
            email=user.email,
            workspace_id=workspace.id,
            plan=subscription.plan,
            message="User registered successfully.",
        )
    except IntegrityError as exc:
        db.rollback()
        logger.exception(
            "auth_register_db_error",
            extra=_sqlalchemy_error_log_payload(exc, stage="insert"),
        )
        raise http_error(409, "email_taken", "Email already registered.")
    except OperationalError as exc:
        db.rollback()
        logger.exception(
            "auth_register_db_error",
            extra=_sqlalchemy_error_log_payload(exc, stage="connection"),
        )
        raise http_error(500, "db_unavailable", "Database connection failed.")
    except ProgrammingError as exc:
        db.rollback()
        logger.exception(
            "auth_register_db_error",
            extra=_sqlalchemy_error_log_payload(exc, stage="insert"),
        )
        raise http_error(
            500, "db_schema_mismatch", "Database schema is out of date. Run migrations."
        )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception(
            "auth_register_db_error",
            extra=_sqlalchemy_error_log_payload(exc, stage="sqlalchemy"),
        )
        raise http_error(500, "db_error", "Database error.")

@app.post("/auth/login", response_model=TokenOut)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenOut:
    user = load_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise http_error(401, "invalid_credentials", "Invalid email or password.")
    token = create_access_token(str(user.id))
    return TokenOut(access_token=token)


@app.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user)) -> MeOut:
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        logo_url=(str(current_user.logo_url) if user_logo_column_available() and current_user.logo_url else None),
        branding=_user_branding(current_user),
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@app.put("/me", response_model=MeOut)
def update_me(
    payload: MeUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeOut:
    if "full_name" in payload.model_fields_set:
        current_user.full_name = payload.full_name
    if "logo_url" in payload.model_fields_set and not user_logo_column_available():
        raise http_error(
            500,
            "db_schema_mismatch",
            "Database schema is out of date. Run migrations.",
        )
    if "logo_url" in payload.model_fields_set:
        current_user.logo_url = payload.logo_url
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        logo_url=(str(current_user.logo_url) if user_logo_column_available() and current_user.logo_url else None),
        branding=_user_branding(current_user),
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@app.post("/ai/chat", response_model=ChatReplyOut)
def ai_chat(
    payload: ChatMessageIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatReplyOut:
    message_text = payload.message.strip()
    if not message_text:
        raise http_error(400, "invalid_message", "Message is required.")

    workspace_id = _resolve_workspace_id(db, current_user.id, None)
    if payload.conversation_id is None:
        conversation = Conversation(
            workspace_id=workspace_id,
            title=build_conversation_title(message_text),
        )
        db.add(conversation)
        db.flush()
    else:
        conversation = _get_conversation_for_workspace(
            db,
            workspace_id=workspace_id,
            conversation_id=payload.conversation_id,
        )

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=message_text,
    )
    db.add(user_message)
    db.flush()

    history = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )

    try:
        assistant_reply = generate_workspace_ai_reply(
            db,
            conversation=conversation,
            history=history,
            user_message=message_text,
        )
    except Exception:
        db.rollback()
        raise http_error(500, "ai_generation_failed", "AI assistant failed to generate a reply.")

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=assistant_reply,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(conversation)

    return ChatReplyOut(
        conversation_id=conversation.id,
        reply=assistant_reply,
    )


@app.get("/ai/conversations", response_model=list[ConversationOut])
def list_ai_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    workspace_id = _resolve_workspace_id(db, current_user.id, None)
    return (
        db.query(Conversation)
        .filter(Conversation.workspace_id == workspace_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .all()
    )


@app.get("/ai/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_ai_conversation_messages(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Message]:
    workspace_id = _resolve_workspace_id(db, current_user.id, None)
    conversation = _get_conversation_for_workspace(
        db,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
META_TOKEN_ACCOUNT_PREFIX = "__meta_token__:"
META_PAGE_ACCOUNT_PREFIX = "__meta_page__:"
META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
META_RECORD_TYPE_INSTAGRAM_ACCOUNT = "instagram_account"


def _validate_upload(file: UploadFile) -> int:
    if not file.filename:
        raise http_error(400, "missing_filename", "File name is required.")
    filename_lower = file.filename.lower()
    if not any(filename_lower.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise http_error(400, "invalid_file_type", "Only .csv or .xlsx files are allowed.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        raise http_error(400, "empty_file", "File is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise http_error(413, "file_too_large", "File exceeds size limit.")
    return size


def _enforce_workspace_storage_for_upload(db: Session, workspace_id: int, incoming_bytes: int) -> None:
    if incoming_bytes < 0:
        raise http_error(400, "invalid_file_size", "File size must be zero or greater.")
    enforce_storage_limit(db, workspace_id, incoming_bytes)


def _get_latest_dataset_file(db: Session, dataset_id: int) -> DatasetFile | None:
    return (
        db.query(DatasetFile)
        .filter(DatasetFile.dataset_id == dataset_id)
        .order_by(DatasetFile.created_at.desc(), DatasetFile.id.desc())
        .first()
    )


def _load_dataset_row(dataset_file: DatasetFile) -> dict[str, str | None]:
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        obj = s3.get_object(Bucket=settings.s3_inputs_bucket, Key=dataset_file.s3_key)
    except Exception as exc:
        raise http_error(502, "s3_read_failed", "Failed to read dataset file.") from exc

    content = obj["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    return next(reader, {}) or {}


def _require_workspace_access(db: Session, user_id: int, workspace_id: int) -> None:
    member = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user_id, WorkspaceMember.workspace_id == workspace_id)
        .first()
    )
    if not member:
        raise http_error(403, "forbidden", "Workspace access denied.")


def _resolve_workspace_id(db: Session, user_id: int, workspace_id: int | None) -> int:
    if workspace_id is not None:
        return workspace_id

    memberships = (
        db.query(WorkspaceMember.workspace_id)
        .filter(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.workspace_id.asc())
        .all()
    )
    workspace_ids = [row[0] for row in memberships]

    if not workspace_ids:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")
    if len(workspace_ids) > 1:
        raise http_error(
            400,
            "workspace_id_required",
            "workspace_id is required when the user belongs to multiple workspaces.",
        )
    return int(workspace_ids[0])


def _require_pro_plan(db: Session, workspace_id: int) -> None:
    if get_workspace_plan(db, workspace_id) == "free":
        raise http_error(403, "plan_required", "Pro plan required for schedules.")


def _meta_token_account_external_id(integration_id: int) -> str:
    return f"{META_TOKEN_ACCOUNT_PREFIX}{integration_id}"


def _meta_page_account_external_id(page_id: str) -> str:
    return f"{META_PAGE_ACCOUNT_PREFIX}{page_id}"


def _meta_record_key(record_type: str, page_id: str) -> tuple[str, str]:
    return record_type, page_id


def _filter_meta_records(
    records: list[MetaPage],
    *,
    record_type: str,
) -> list[MetaPage]:
    return [record for record in records if record.record_type == record_type]


def _meta_pages_redirect_uri() -> str | None:
    return get_meta_pages_redirect_uri()


def _get_or_create_meta_integration_for_workspace(db: Session, workspace_id: int) -> Integration:
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "meta")
        .order_by(Integration.id.asc())
        .first()
    )
    if integration:
        return integration

    integration = Integration(
        workspace_id=workspace_id,
        provider="meta",
        name="Meta Pages",
        status="disconnected",
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    logger.warning(
        "Meta integration created workspace_id=%s integration_id=%s provider=%s",
        workspace_id,
        integration.id,
        integration.provider,
    )
    return integration


def _resolve_meta_pages_exchange_redirect_uri(redirect_uri: str | None) -> str:
    configured_redirect_uri = _meta_pages_redirect_uri()
    if redirect_uri is None:
        return configured_redirect_uri

    redirect_uri = redirect_uri.strip()
    if not redirect_uri:
        raise http_error(400, "invalid_redirect_uri", "redirect_uri must be a non-empty string.")

    frontend_redirect_uri = f"{_meta_frontend_base_url()}/integrations/meta/callback"
    allowed_redirect_uris = {
        configured_redirect_uri,
        frontend_redirect_uri,
    }
    if redirect_uri not in allowed_redirect_uris:
        raise http_error(400, "invalid_redirect_uri", "redirect_uri is not allowed for Meta Pages callback.")

    return redirect_uri


def _set_meta_integration_status(
    db: Session,
    integration: Integration,
    *,
    status: str,
) -> Integration:
    integration.status = status
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return integration


def _is_development_env() -> bool:
    env_value = (
        os.getenv("ENV")
        or os.getenv("APP_ENV")
        or os.getenv("FASTAPI_ENV")
        or os.getenv("PYTHON_ENV")
        or ""
    ).strip().lower()
    return env_value in {"dev", "development", "local"}


def _log_meta_pages_debug(
    *,
    integration_id: int,
    source: str,
    pages: list[dict[str, Any]] | list[MetaPage],
    dropdown_count: int | None = None,
) -> None:
    page_names: list[str] = []
    for page in pages:
        if isinstance(page, MetaPage):
            page_name = page.name
        else:
            page_name = str(page.get("name") or "").strip()
        if page_name:
            page_names.append(page_name)

    logger.info(
        "Meta Pages OAuth debug",
        extra={
            "integration_id": integration_id,
            "source": source,
            "meta_accounts_count": len(pages),
            "page_names": page_names,
            "dropdown_count": dropdown_count if dropdown_count is not None else len(pages),
            "dropdown_matches_meta_count": (
                dropdown_count == len(pages) if dropdown_count is not None else True
            ),
        },
    )


def _log_meta_account_summary(
    *,
    integration_id: int,
    user_id: int | None,
    selected_integration_type: str | None,
    facebook_pages: list[dict[str, Any]] | list[MetaPage],
    instagram_accounts: list[dict[str, Any]] | list[MetaPage],
    context: str,
) -> None:
    facebook_page_names = [record.name if isinstance(record, MetaPage) else str(record.get("name") or "").strip() for record in facebook_pages]
    instagram_account_usernames = [
        (
            record.instagram_username
            if isinstance(record, MetaPage)
            else str(record.get("instagram_username") or record.get("name") or "").strip()
        )
        for record in instagram_accounts
    ]
    facebook_page_names = [name for name in facebook_page_names if name]
    instagram_account_usernames = [username for username in instagram_account_usernames if username]
    logger.info(
        "Meta authorized account summary",
        extra={
            "context": context,
            "integration_id": integration_id,
            "user_id": user_id,
            "pages_count": len(facebook_pages),
            "page_names": facebook_page_names,
            "facebook_pages_count": len(facebook_pages),
            "facebook_page_names": facebook_page_names,
            "instagram_accounts_found_count": len(instagram_accounts),
            "instagram_usernames_found": instagram_account_usernames,
            "instagram_accounts_count": len(instagram_accounts),
            "instagram_account_usernames": instagram_account_usernames,
            "selected_integration_type": selected_integration_type,
        },
    )


def _normalize_instagram_business_account_node(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("data"), dict):
        nested = value.get("data")
        return nested if isinstance(nested, dict) else None
    return value


def _log_instagram_business_account_raw(
    *,
    integration_id: int,
    user_id: int | None,
    page_id: str,
    page_name: str,
    has_page_access_token: bool,
    instagram_business_account: dict[str, Any] | None,
) -> None:
    logger.info(
        "Meta page instagram_business_account raw",
        extra={
            "integration_id": integration_id,
            "user_id": user_id,
            "page_id": page_id,
            "page_name": page_name,
            "has_page_access_token": has_page_access_token,
            "instagram_business_account_raw": (
                {
                    "id": instagram_business_account.get("id"),
                    "username": instagram_business_account.get("username"),
                    "name": instagram_business_account.get("name"),
                    "profile_picture_url": instagram_business_account.get("profile_picture_url"),
                }
                if instagram_business_account
                else None
            ),
        },
    )


def _meta_token_preview(access_token: str | None) -> str | None:
    token = str(access_token or "").strip()
    return f"{token[:8]}..." if token else None


def _log_meta_token_context(
    *,
    integration_id: int,
    workspace_id: int,
    user_id: int | None,
    token_account_id: int | None,
    token: IntegrationToken | None,
    context: str,
    selected_integration_type: str | None,
    token_received: bool,
) -> None:
    logger.warning(
        "Meta live refresh token context",
        extra={
            "context": context,
            "integration_id": integration_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "token_account_id": token_account_id,
            "token_id": token.id if token else None,
            "token_updated_at": token.updated_at.isoformat() if token and token.updated_at else None,
            "token_received": token_received,
            "token_preview": _meta_token_preview(token.access_token if token else None),
            "selected_integration_type": selected_integration_type,
        },
    )


def _fetch_instagram_business_account_for_page(
    *,
    page_id: str,
    page_name: str,
    page_access_token: str | None,
    fallback_access_token: str,
    integration_id: int,
    user_id: int | None,
) -> dict[str, Any] | None:
    has_page_access_token = bool(page_access_token)
    logger.info(
        "Meta Instagram fallback lookup start",
        extra={
            "integration_id": integration_id,
            "user_id": user_id,
            "page_id": page_id,
            "page_name": page_name,
            "has_page_access_token": has_page_access_token,
        },
    )
    lookup_token = page_access_token or fallback_access_token
    try:
        page_info = fetch_page_info(
            lookup_token,
            page_id,
            fields="instagram_business_account{id,username,name,profile_picture_url}",
        )
    except HTTPException as exc:
        if not _is_meta_api_error(exc):
            raise
        logger.warning(
            "Meta Instagram fallback lookup failed",
            extra={
                "integration_id": integration_id,
                "user_id": user_id,
                "page_id": page_id,
                "page_name": page_name,
                "has_page_access_token": has_page_access_token,
                "error": str(exc.detail),
            },
        )
        return None

    instagram_account = _normalize_instagram_business_account_node(
        page_info.get("instagram_business_account")
    )
    _log_instagram_business_account_raw(
        integration_id=integration_id,
        user_id=user_id,
        page_id=page_id,
        page_name=page_name,
        has_page_access_token=has_page_access_token,
        instagram_business_account=instagram_account,
    )
    return instagram_account


def _extract_meta_debug_token_target_ids(debug_token_payload: dict[str, Any]) -> list[str]:
    data = debug_token_payload.get("data")
    if not isinstance(data, dict):
        return []
    granular_scopes = data.get("granular_scopes")
    if not isinstance(granular_scopes, list):
        return []

    target_scope_names = {
        "pages_show_list",
        "pages_read_engagement",
        "instagram_basic",
    }
    target_ids: list[str] = []
    seen_target_ids: set[str] = set()
    for granular_scope in granular_scopes:
        if not isinstance(granular_scope, dict):
            continue
        scope_name = str(granular_scope.get("scope") or "").strip()
        if scope_name not in target_scope_names:
            continue
        scope_target_ids = granular_scope.get("target_ids")
        if not isinstance(scope_target_ids, list):
            continue
        for raw_target_id in scope_target_ids:
            target_id = str(raw_target_id or "").strip()
            if not target_id or target_id in seen_target_ids:
                continue
            seen_target_ids.add(target_id)
            target_ids.append(target_id)
    return target_ids


def _fetch_meta_pages_from_granular_target_ids(
    *,
    access_token: str,
    integration_id: int,
    user_id: int | None,
    context: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    debug_token_payload = debug_token(access_token)
    target_ids = _extract_meta_debug_token_target_ids(debug_token_payload)
    logger.warning(
        "Meta fallback target ids context=%s integration_id=%s user_id=%s debug_token_granular_target_ids=%s",
        context,
        integration_id,
        user_id,
        target_ids,
    )
    fallback_records: list[dict[str, Any]] = []
    for target_id in target_ids:
        try:
            page_payload = fetch_page_info_with_metadata(
                access_token,
                target_id,
                fields="id,name,picture{url},instagram_business_account{id,username,name,profile_picture_url}",
            )
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            error_details = _meta_api_error_details(exc)
            logger.warning(
                "Meta fallback page lookup failed context=%s integration_id=%s user_id=%s target_id=%s fallback_page_lookup_status=%s fallback_page_lookup_raw_body=%s error=%s",
                context,
                integration_id,
                user_id,
                target_id,
                error_details["upstream_status_code"],
                error_details["response_body"],
                str(exc.detail),
            )
            try:
                instagram_payload = fetch_page_info_with_metadata(
                    access_token,
                    target_id,
                    fields="id,username,name,profile_picture_url",
                )
            except HTTPException as instagram_exc:
                if not _is_meta_api_error(instagram_exc):
                    raise
                instagram_error_details = _meta_api_error_details(instagram_exc)
                logger.warning(
                    "Meta fallback instagram lookup failed context=%s integration_id=%s user_id=%s target_id=%s fallback_instagram_lookup_status=%s fallback_instagram_lookup_raw_body=%s error=%s",
                    context,
                    integration_id,
                    user_id,
                    target_id,
                    instagram_error_details["upstream_status_code"],
                    instagram_error_details["response_body"],
                    str(instagram_exc.detail),
                )
                continue

            logger.warning(
                "Meta fallback instagram lookup resolved context=%s integration_id=%s user_id=%s target_id=%s fallback_instagram_lookup_status=%s fallback_instagram_lookup_raw_body=%s",
                context,
                integration_id,
                user_id,
                target_id,
                instagram_payload.get("_meta_http_status_code"),
                instagram_payload.get("_meta_raw_body"),
            )
            if str(instagram_payload.get("id") or "").strip() and str(instagram_payload.get("username") or "").strip():
                fallback_records.append(
                    {
                        "record_type": META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                        "page_id": str(instagram_payload.get("id") or "").strip(),
                        "parent_page_id": None,
                        "name": str(
                            instagram_payload.get("name")
                            or instagram_payload.get("username")
                            or instagram_payload.get("id")
                            or ""
                        ).strip(),
                        "instagram_username": str(instagram_payload.get("username") or "").strip() or None,
                        "profile_picture_url": str(instagram_payload.get("profile_picture_url") or "").strip() or None,
                        "page_access_token": None,
                        "tasks": None,
                        "perms": [],
                        "category": None,
                        "business_name": None,
                    }
                )
            continue

        logger.warning(
            "Meta fallback page lookup resolved context=%s integration_id=%s user_id=%s target_id=%s fallback_page_lookup_status=%s fallback_page_lookup_raw_body=%s",
            context,
            integration_id,
            user_id,
            target_id,
            page_payload.get("_meta_http_status_code"),
            page_payload.get("_meta_raw_body"),
        )
        page_id = str(page_payload.get("id") or "").strip()
        page_name = str(page_payload.get("name") or page_id).strip()
        picture_payload = page_payload.get("picture")
        profile_picture_url = None
        if isinstance(picture_payload, dict):
            picture_data = picture_payload.get("data")
            if isinstance(picture_data, dict):
                profile_picture_url = str(picture_data.get("url") or "").strip() or None
            else:
                profile_picture_url = str(picture_payload.get("url") or "").strip() or None

        if page_id and page_name:
            fallback_records.append(
                {
                    "record_type": META_RECORD_TYPE_FACEBOOK_PAGE,
                    "page_id": page_id,
                    "parent_page_id": None,
                    "name": page_name,
                    "instagram_username": None,
                    "profile_picture_url": profile_picture_url,
                    "page_access_token": None,
                    "tasks": None,
                    "perms": [],
                    "category": None,
                    "business_name": None,
                    "instagram_business_account": page_payload.get("instagram_business_account"),
                }
            )

    logger.warning(
        "Meta fallback page lookup completed context=%s integration_id=%s user_id=%s fallback_target_ids_used=%s fallback_pages_found=%s fallback_page_names=%s",
        context,
        integration_id,
        user_id,
        target_ids,
        len(
            [
                record
                for record in fallback_records
                if record.get("record_type") == META_RECORD_TYPE_FACEBOOK_PAGE
            ]
        ),
        [str(record.get('name') or record.get('page_id') or '') for record in fallback_records if record.get("record_type") == META_RECORD_TYPE_FACEBOOK_PAGE],
    )
    return fallback_records, target_ids


def _fetch_instagram_user_details(
    *,
    instagram_account_id: str,
    access_token: str,
    integration_id: int,
    user_id: int | None,
    page_id: str,
    page_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = fetch_page_info(
            access_token,
            instagram_account_id,
            fields="id,username,name,profile_picture_url,followers_count,media_count",
        )
    except HTTPException as exc:
        if not _is_meta_api_error(exc):
            raise
        logger.warning(
            "Meta Instagram user details lookup failed",
            extra={
                "integration_id": integration_id,
                "user_id": user_id,
                "page_id": page_id,
                "page_name": page_name,
                "instagram_account_id": instagram_account_id,
                "token_received": bool(access_token),
                "token_preview": _meta_token_preview(access_token),
                "error": str(exc.detail),
            },
        )
        return None, str(exc.detail)

    logger.info(
        "Meta Instagram user details lookup resolved",
        extra={
            "integration_id": integration_id,
            "user_id": user_id,
            "page_id": page_id,
            "page_name": page_name,
            "instagram_account_id": instagram_account_id,
            "username": payload.get("username"),
            "name": payload.get("name"),
        },
    )
    return payload, None


def _build_instagram_account_record(
    *,
    page_id: str,
    page_name: str,
    page_access_token: str | None,
    tasks: Any,
    perms: Any,
    instagram_account: dict[str, Any],
    instagram_details: dict[str, Any] | None,
) -> dict[str, Any]:
    instagram_account_id = str(instagram_account.get("id") or "").strip()
    resolved_name = str(
        (instagram_details or {}).get("name")
        or instagram_account.get("name")
        or page_name
    )
    resolved_username = str(
        (instagram_details or {}).get("username")
        or instagram_account.get("username")
        or ""
    ) or None
    resolved_profile_picture_url = str(
        (instagram_details or {}).get("profile_picture_url")
        or instagram_account.get("profile_picture_url")
        or ""
    ) or None
    return {
        "record_type": META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        "page_id": instagram_account_id,
        "parent_page_id": page_id,
        "name": resolved_name,
        "instagram_username": resolved_username,
        "profile_picture_url": resolved_profile_picture_url,
        "page_access_token": page_access_token,
        "tasks": tasks if isinstance(tasks, list) else None,
        "perms": perms if isinstance(perms, list) else [],
        "category": None,
        "business_name": page_name,
    }


def _collect_meta_instagram_diagnostics(
    access_token: str,
    integration_id: int,
    *,
    user_id: int | None = None,
    context: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    me_accounts_pages = list_pages(
        access_token,
        context=context,
        integration_id=integration_id,
        user_id=user_id,
        token_received=bool(access_token),
    )
    logger.warning(
        "Meta pages discovery start context=%s integration_id=%s user_id=%s me_accounts_status=%s me_accounts_count=%s",
        context,
        integration_id,
        user_id,
        200,
        len(me_accounts_pages),
    )
    direct_pages = me_accounts_pages
    debug_token_granular_target_ids: list[str] = []
    fallback_target_ids_used: list[str] = []
    prebuilt_fallback_records: list[dict[str, Any]] = []
    if not direct_pages:
        prebuilt_fallback_records, debug_token_granular_target_ids = _fetch_meta_pages_from_granular_target_ids(
            access_token=access_token,
            integration_id=integration_id,
            user_id=user_id,
            context=context,
        )
        fallback_target_ids_used = debug_token_granular_target_ids
        direct_pages = [
            record
            for record in prebuilt_fallback_records
            if record.get("record_type") == META_RECORD_TYPE_FACEBOOK_PAGE
        ]
    authorized_records: dict[tuple[str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []

    for fallback_record in prebuilt_fallback_records:
        record_type = str(fallback_record.get("record_type") or "").strip()
        page_id = str(fallback_record.get("page_id") or "").strip()
        if not record_type or not page_id:
            continue
        authorized_records[_meta_record_key(record_type, page_id)] = {
            key: value
            for key, value in fallback_record.items()
            if not key.startswith("_")
        }

    for page in direct_pages:
        page_id = str(page.get("id") or page.get("page_id") or "")
        if not page_id:
            continue
        page_name = str(page.get("name") or page_id)
        page_access_token = str(page.get("access_token") or "") or None
        has_page_access_token = bool(page_access_token)
        tasks = page.get("tasks")
        perms = page.get("perms")

        authorized_records[_meta_record_key(META_RECORD_TYPE_FACEBOOK_PAGE, page_id)] = {
            "record_type": META_RECORD_TYPE_FACEBOOK_PAGE,
            "page_id": page_id,
            "parent_page_id": None,
            "name": page_name,
            "instagram_username": None,
            "profile_picture_url": None,
            "page_access_token": page_access_token,
            "tasks": tasks if isinstance(tasks, list) else None,
            "perms": perms if isinstance(perms, list) else [],
            "category": str(page.get("category") or "") or None,
            "business_name": (
                str((page.get("business") or {}).get("name") or "") or None
                if isinstance(page.get("business"), dict)
                else str(page.get("business_name") or "") or None
            ),
        }

        me_accounts_instagram = _normalize_instagram_business_account_node(
            page.get("instagram_business_account")
        )
        _log_instagram_business_account_raw(
            integration_id=integration_id,
            user_id=user_id,
            page_id=page_id,
            page_name=page_name,
            has_page_access_token=has_page_access_token,
            instagram_business_account=me_accounts_instagram,
        )

        page_lookup_instagram = me_accounts_instagram
        page_lookup_error: str | None = None
        if page_lookup_instagram is None:
            page_lookup_instagram = _fetch_instagram_business_account_for_page(
                page_id=page_id,
                page_name=page_name,
                page_access_token=page_access_token,
                fallback_access_token=access_token,
                integration_id=integration_id,
                user_id=user_id,
            )
            if page_lookup_instagram is None:
                page_lookup_error = "instagram_business_account_not_returned"

        instagram_details: dict[str, Any] | None = None
        instagram_details_error: str | None = None
        instagram_account_id = str((page_lookup_instagram or {}).get("id") or "").strip()
        if instagram_account_id:
            instagram_details, instagram_details_error = _fetch_instagram_user_details(
                instagram_account_id=instagram_account_id,
                access_token=page_access_token or access_token,
                integration_id=integration_id,
                user_id=user_id,
                page_id=page_id,
                page_name=page_name,
            )
            authorized_records[
                _meta_record_key(META_RECORD_TYPE_INSTAGRAM_ACCOUNT, instagram_account_id)
            ] = _build_instagram_account_record(
                page_id=page_id,
                page_name=page_name,
                page_access_token=page_access_token,
                tasks=tasks,
                perms=perms,
                instagram_account=page_lookup_instagram,
                instagram_details=instagram_details,
            )

        diagnostics.append(
            {
                "page_id": page_id,
                "page_name": page_name,
                "has_page_access_token": has_page_access_token,
                "discovered_via_fallback_target_ids": page_id in fallback_target_ids_used,
                "instagram_business_account_from_me_accounts": me_accounts_instagram,
                "instagram_business_account_from_page_lookup": page_lookup_instagram,
                "instagram_user_details": instagram_details,
                "errors": [
                    error
                    for error in [page_lookup_error, instagram_details_error]
                    if error
                ],
            }
        )

    logger.warning(
        "Meta pages discovery completed context=%s integration_id=%s user_id=%s me_accounts_count=%s debug_token_granular_target_ids=%s fallback_target_ids_used=%s pages_discovered_final=%s",
        context,
        integration_id,
        user_id,
        len(me_accounts_pages),
        debug_token_granular_target_ids,
        fallback_target_ids_used,
        len(
            [
                record
                for record in authorized_records.values()
                if record.get("record_type") == META_RECORD_TYPE_FACEBOOK_PAGE
            ]
        ),
    )
    return list(authorized_records.values()), diagnostics


def _refresh_meta_pages_from_live_graph(
    db: Session,
    integration: Integration,
    *,
    access_token: str,
    user_id: int | None,
    selected_integration_type: str | None,
    context: str,
    return_empty_on_error: bool = False,
) -> tuple[list[MetaPage], list[dict[str, Any]], list[dict[str, Any]]]:
    token_account = _get_meta_token_account(db, integration.id)
    latest_token = _get_latest_integration_token(db, token_account.id) if token_account else None
    _log_meta_token_context(
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        user_id=user_id,
        token_account_id=token_account.id if token_account else None,
        token=latest_token,
        context=context,
        selected_integration_type=selected_integration_type,
        token_received=bool(access_token),
    )

    try:
        authorized_records, diagnostics = _collect_meta_instagram_diagnostics(
            access_token,
            integration.id,
            user_id=user_id,
            context=context,
        )
    except HTTPException as exc:
        if not return_empty_on_error:
            raise
        logger.warning(
            "Meta live refresh failed integration_id=%s workspace_id=%s user_id=%s context=%s error=%s",
            integration.id,
            integration.workspace_id,
            user_id,
            context,
            str(exc.detail),
        )
        cached_pages = _cache_meta_pages(db, integration, user_id, [])
        _clear_selected_meta_page_if_unauthorized(db, integration, set())
        return cached_pages, [], []

    existing_pages = (
        db.query(MetaPage)
        .filter(MetaPage.integration_id == integration.id)
        .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
        .all()
    )
    incoming_record_keys = {
        _meta_record_key(record.get("record_type") or META_RECORD_TYPE_FACEBOOK_PAGE, str(record.get("page_id") or ""))
        for record in authorized_records
        if str(record.get("page_id") or "")
    }
    pages_deleted_as_stale = sum(
        1
        for existing_page in existing_pages
        if _meta_record_key(existing_page.record_type, existing_page.page_id) not in incoming_record_keys
    )
    if not authorized_records and existing_pages:
        existing_facebook_pages = _filter_meta_records(
            existing_pages,
            record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
        )
        existing_instagram_accounts = _filter_meta_records(
            existing_pages,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        )
        logger.warning(
            "Meta live refresh returned empty pages; preserving stored cache integration_id=%s workspace_id=%s user_id=%s context=%s stored_total_pages_count=%s stored_facebook_page_count=%s stored_instagram_account_count=%s",
            integration.id,
            integration.workspace_id,
            user_id,
            context,
            len(existing_pages),
            len(existing_facebook_pages),
            len(existing_instagram_accounts),
        )
        return existing_pages, diagnostics, existing_facebook_pages

    cached_pages = _cache_meta_pages(db, integration, user_id, authorized_records)
    _clear_selected_meta_page_if_unauthorized(
        db,
        integration,
        {
            page.page_id
            for page in cached_pages
            if page.record_type == META_RECORD_TYPE_FACEBOOK_PAGE
        },
    )
    facebook_pages = _filter_meta_records(cached_pages, record_type=META_RECORD_TYPE_FACEBOOK_PAGE)
    instagram_accounts = _filter_meta_records(
        cached_pages,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    logger.warning(
        "Meta live refresh completed integration_id=%s workspace_id=%s user_id=%s context=%s pages_returned_from_graph=%s page_names_returned_from_graph=%s pages_deleted_as_stale=%s pages_saved_final=%s instagram_accounts_saved_final=%s facebook_pages_count=%s instagram_accounts_found_count=%s instagram_usernames_found=%s selected_integration_type=%s",
        integration.id,
        integration.workspace_id,
        user_id,
        context,
        len(diagnostics),
        [item.get("page_name") for item in diagnostics if item.get("page_name")],
        pages_deleted_as_stale,
        len(facebook_pages),
        len(instagram_accounts),
        len(cached_pages),
        len(instagram_accounts),
        [page.instagram_username for page in instagram_accounts if page.instagram_username],
        selected_integration_type,
    )
    return cached_pages, diagnostics, facebook_pages


def _normalize_meta_ad_account_id(ad_account_id: str) -> str:
    return ad_account_id.removeprefix("act_")


def _first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _extract_summary_total(value) -> int | None:
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    if not isinstance(summary, dict):
        return None
    total_count = summary.get("total_count")
    if total_count in (None, "", "null"):
        return None
    try:
        return int(total_count)
    except (TypeError, ValueError):
        return None


def _extract_post_saves(post_metrics: dict) -> int | None:
    activity = post_metrics.get("post_activity_by_action_type_unique")
    if not isinstance(activity, dict):
        return None
    for key in ("post_save", "post_saved", "save", "saved", "saves"):
        value = activity.get(key)
        if value in (None, "", "null"):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _expand_meta_daily_series(
    points: list[dict] | None,
    *,
    since: str,
    until: str,
) -> list[dict[str, int | str | None]]:
    start_date = date.fromisoformat(since)
    end_date = date.fromisoformat(until)
    if end_date < start_date:
        return []

    values_by_date: dict[str, int | None] = {}
    for point in points or []:
        if not isinstance(point, dict):
            continue
        point_date = str(point.get("date") or "").strip()
        if not point_date:
            continue
        raw_value = point.get("value")
        if isinstance(raw_value, (int, float)):
            values_by_date[point_date] = int(raw_value)
        elif raw_value in (None, "", "null"):
            values_by_date[point_date] = None

    expanded: list[dict[str, int | str | None]] = []
    current = start_date
    while current <= end_date:
        date_key = current.isoformat()
        expanded.append(
            {
                "date": date_key,
                "value": values_by_date.get(date_key),
            }
        )
        current += timedelta(days=1)

    last_known: int | None = None
    for point in expanded:
        value = point.get("value")
        if isinstance(value, int):
            last_known = value
            continue
        if last_known is not None:
            point["value"] = last_known

    next_known: int | None = None
    for point in reversed(expanded):
        value = point.get("value")
        if isinstance(value, int):
            next_known = value
            continue
        if next_known is not None:
            point["value"] = next_known

    return expanded


def _sum_meta_daily_series(points: list[dict] | None) -> int | None:
    numeric_values = [
        int(point.get("value"))
        for point in points or []
        if isinstance(point, dict) and isinstance(point.get("value"), (int, float))
    ]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _meta_daily_series_bounds(points: list[dict] | None) -> tuple[str | None, str | None]:
    dated_points = [
        str(point.get("date"))
        for point in points or []
        if isinstance(point, dict) and point.get("date")
    ]
    if not dated_points:
        return None, None
    return dated_points[0], dated_points[-1]


def _log_meta_history_audit(
    *,
    page_id: str,
    page_name: str,
    metric_name: str,
    selected_timeframe: str | None = None,
    since: str | None,
    until: str | None,
    current_since: str | None = None,
    current_until: str | None = None,
    previous_since: str | None = None,
    previous_until: str | None = None,
    points: list[dict] | None,
) -> None:
    normalized_points = _meta_series_points(points or [])
    first_point = normalized_points[0] if normalized_points else {}
    last_point = normalized_points[-1] if normalized_points else {}
    logger.info(
        "[META_HISTORY_AUDIT]",
        extra={
            "page_id": page_id,
            "page_name": page_name,
            "metric_name": metric_name,
            "selected_timeframe": selected_timeframe,
            "requested_since": since,
            "requested_until": until,
            "current_since": current_since,
            "current_until": current_until,
            "previous_since": previous_since,
            "previous_until": previous_until,
            "daily_points_received": len(normalized_points),
            "first_date_received": first_point.get("date"),
            "last_date_received": last_point.get("date"),
            "sample_first_value": first_point.get("value"),
            "sample_last_value": last_point.get("value"),
        },
    )


def _build_meta_report_metric_entry(
    *,
    facebook_ui_target_label: str,
    source_metric_name: str | None,
    total: int | None,
    daily_series: list[dict] | None,
    timeframe_since: str,
    timeframe_until: str,
) -> dict[str, object]:
    return {
        "facebook_ui_target_label": facebook_ui_target_label,
        "ui_label_facebook": facebook_ui_target_label,
        "source_metric_name": source_metric_name,
        "api_metric_used": source_metric_name,
        "total": total,
        "daily_series": daily_series or [],
        "daily_points_count": len(daily_series or []),
        "timeframe_since": timeframe_since,
        "timeframe_until": timeframe_until,
        "timeframe": {
            "since": timeframe_since,
            "until": timeframe_until,
        },
    }


def _normalize_instagram_insight_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        numeric_total = 0
        found_numeric = False
        for item in value.values():
            normalized_item = _normalize_instagram_insight_value(item)
            if normalized_item is None:
                continue
            numeric_total += normalized_item
            found_numeric = True
        return numeric_total if found_numeric else None
    return None


def _normalize_instagram_insight_series(
    data_points: list[dict[str, Any]] | None,
) -> tuple[int | None, int | None, str | None, list[dict[str, int | str | None]], list[Any]]:
    normalized_series: list[dict[str, int | str | None]] = []
    last_value: int | None = None
    last_end_time: str | None = None
    total_value = 0
    has_numeric_value = False
    raw_values: list[Any] = []
    for point in data_points or []:
        if not isinstance(point, dict):
            continue
        end_time = str(point.get("end_time") or "").strip() or None
        raw_value = point.get("value")
        raw_values.append(raw_value)
        normalized_value = _normalize_instagram_insight_value(raw_value)
        normalized_series.append(
            {
                "date": end_time,
                "value": normalized_value,
            }
        )
        if normalized_value is not None:
            total_value += normalized_value
            has_numeric_value = True
            last_value = normalized_value
            last_end_time = end_time
    return (
        total_value if has_numeric_value else None,
        last_value,
        last_end_time,
        normalized_series,
        raw_values,
    )


def _is_total_interactions_metric_type_error(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    message = str(detail.get("message") or "").strip().lower()
    return "total_interactions" in message and "metric_type=total_value" in message


def _build_impressions_slide_payload(
    dataset_row: dict[str, object],
    *,
    locale: str,
) -> dict[str, object]:
    timeframe_data = dataset_row.get("timeframe") if isinstance(dataset_row.get("timeframe"), dict) else {}
    normalized_metrics = (
        dataset_row.get("normalized_report_metrics")
        if isinstance(dataset_row.get("normalized_report_metrics"), dict)
        else {}
    )
    if isinstance(dataset_row.get("impressions_daily"), list):
        impressions_daily_raw = dataset_row.get("impressions_daily")
        daily_source_path = "dataset.data.impressions_daily"
    elif isinstance(normalized_metrics.get("impressions_daily"), list):
        impressions_daily_raw = normalized_metrics.get("impressions_daily")
        daily_source_path = "dataset.data.normalized_report_metrics.impressions_daily"
    else:
        impressions_daily_raw = []
        daily_source_path = "dataset.data.impressions_daily"
    impressions_daily = [
        {
            "date": str(point.get("date") or ""),
            "value": int(point.get("value")),
        }
        for point in impressions_daily_raw
        if isinstance(point, dict)
        and point.get("date")
        and isinstance(point.get("value"), (int, float))
    ]
    total_source_path = "dataset.data.normalized_report_metrics.impressions_total"
    impressions_total_raw = normalized_metrics.get("impressions_total")
    used_total_fallback = False
    impressions_total = (
        int(impressions_total_raw) if isinstance(impressions_total_raw, (int, float)) else None
    )
    if impressions_total is None:
        fallback_total = dataset_row.get("impressions")
        if isinstance(fallback_total, (int, float)):
            impressions_total = int(fallback_total)
            total_source_path = "dataset.data.impressions"
            used_total_fallback = True
    if impressions_total is None and impressions_daily:
        impressions_total = sum(point["value"] for point in impressions_daily)
        total_source_path = "sum(dataset.data.normalized_report_metrics.impressions_daily)"
        used_total_fallback = True

    timeframe_since = (
        str(timeframe_data.get("since") or "")
        if timeframe_data
        else str(normalized_metrics.get("timeframe_since") or "")
    )
    timeframe_until = (
        str(timeframe_data.get("until") or "")
        if timeframe_data
        else str(normalized_metrics.get("timeframe_until") or "")
    )
    timeframe_label = str(timeframe_data.get("label") or "").strip()
    average_daily = (
        (impressions_total / len(impressions_daily))
        if impressions_total is not None and impressions_daily
        else None
    )
    highest_day = max(impressions_daily, key=lambda point: point["value"]) if impressions_daily else None
    lowest_day = min(impressions_daily, key=lambda point: point["value"]) if impressions_daily else None

    viewers_total_raw = normalized_metrics.get("viewers_total")
    viewers_total = int(viewers_total_raw) if isinstance(viewers_total_raw, (int, float)) else None
    frequency = (
        round(impressions_total / viewers_total, 2)
        if impressions_total is not None and viewers_total not in (None, 0)
        else None
    )
    impressions_daily_sum = sum(point["value"] for point in impressions_daily) if impressions_daily else 0
    impressions_daily_all_zero = bool(impressions_daily) and all(
        point["value"] == 0 for point in impressions_daily
    )
    consistency_valid = (
        bool(impressions_daily)
        and impressions_total is not None
        and impressions_daily_sum > 0
        and not impressions_daily_all_zero
        and impressions_daily_sum == impressions_total
    )

    if locale == "es":
        period_label = timeframe_label or "el periodo seleccionado"
        insight_text = (
            "No hubo suficientes datos de impresiones para construir el insight."
            if not consistency_valid
            else (
                f"Las impresiones totalizaron {impressions_total} en {period_label}, con un promedio diario de "
                f"{average_daily:.2f}. El pico ocurrió el {highest_day['date']} con {highest_day['value']} "
                f"y el punto más bajo fue el {lowest_day['date']} con {lowest_day['value']}."
            )
        )
        title = "IMPRESIONES"
        label = (
            f"TOTAL DE IMPRESIONES - {timeframe_label.upper()}"
            if timeframe_label
            else "TOTAL DE IMPRESIONES"
        )
    else:
        period_label = timeframe_label or "the selected period"
        insight_text = (
            "There was not enough impressions data to build the insight."
            if not consistency_valid
            else (
                f"Impressions totaled {impressions_total} in {period_label}, with an average daily value of "
                f"{average_daily:.2f}. The highest day was {highest_day['date']} with {highest_day['value']}, "
                f"and the lowest day was {lowest_day['date']} with {lowest_day['value']}."
            )
        )
        title = "IMPRESSIONS"
        label = f"TOTAL IMPRESSIONS - {timeframe_label.upper()}" if timeframe_label else "TOTAL IMPRESSIONS"

    return {
        "type": "impressions_slide",
        "metric": "impressions",
        "title": title,
        "label": label,
        "timeframe_label": timeframe_label or None,
        "timeframe": {
            "key": str(timeframe_data.get("key") or timeframe_data.get("timeframe") or "") or None,
            "label": timeframe_label or None,
            "preset": str(timeframe_data.get("preset") or "") or None,
            "since": timeframe_since or None,
            "until": timeframe_until or None,
        },
        "source": "normalized_report_metrics",
        "timeframe_since": timeframe_since or None,
        "timeframe_until": timeframe_until or None,
        "impressions_total": impressions_total if consistency_valid else None,
        "impressions_daily": impressions_daily if consistency_valid else [],
        "impressions_daily_count": len(impressions_daily) if consistency_valid else 0,
        "impressions_daily_sum": impressions_daily_sum,
        "impressions_daily_all_zero": impressions_daily_all_zero,
        "average_daily": round(average_daily, 2) if consistency_valid and average_daily is not None else None,
        "highest_day": highest_day if consistency_valid else None,
        "lowest_day": lowest_day if consistency_valid else None,
        "first_impressions_date": impressions_daily[0]["date"] if consistency_valid and impressions_daily else None,
        "last_impressions_date": impressions_daily[-1]["date"] if consistency_valid and impressions_daily else None,
        "source_metric_name": normalized_metrics.get("impressions_source_metric")
        or dataset_row.get("impressions_source_metric"),
        "total_source_path": total_source_path,
        "daily_source_path": daily_source_path,
        "used_total_fallback": used_total_fallback,
        "frequency": frequency if consistency_valid else None,
        "chartable": consistency_valid,
        "consistency_valid": consistency_valid,
        "insight_text": insight_text,
    }


def _build_general_insights_slide_payload(dataset_row: dict[str, object]) -> dict[str, object]:
    normalized_metrics = (
        dataset_row.get("normalized_report_metrics")
        if isinstance(dataset_row.get("normalized_report_metrics"), dict)
        else {}
    )
    integration_type = str(dataset_row.get("integration_type") or "").strip() or None
    is_instagram_business = integration_type == "instagram_business"
    metric_mapping = (
        dataset_row.get("report_metric_mapping")
        if isinstance(dataset_row.get("report_metric_mapping"), dict)
        else {}
    )
    impressions_slide = _build_impressions_slide_payload(dataset_row, locale="es")

    def metric_entry(
        *,
        value: int | float | None,
        source_metric_name: str | None,
        source_field_path: str,
        semantic_valid: bool,
    ) -> dict[str, object]:
        available = semantic_valid and value is not None
        return {
            "value": value if available else None,
            "source_metric_name": source_metric_name,
            "source_field_path": source_field_path,
            "available": available,
            "semantic_valid": semantic_valid,
        }

    reach_metric_name = str(dataset_row.get("reach_source_metric") or "") or None
    reach_value = dataset_row.get("reach")
    reach = metric_entry(
        value=int(reach_value) if isinstance(reach_value, (int, float)) else None,
        source_metric_name=reach_metric_name,
        source_field_path="dataset.data.reach",
        semantic_valid=bool(reach_metric_name and isinstance(reach_value, (int, float))),
    )

    impressions = metric_entry(
        value=impressions_slide.get("impressions_total")
        if isinstance(impressions_slide.get("impressions_total"), (int, float))
        else None,
        source_metric_name=str(impressions_slide.get("source_metric_name") or "") or None,
        source_field_path=str(impressions_slide.get("total_source_path") or "dataset.data.normalized_report_metrics.impressions_total"),
        semantic_valid=bool(impressions_slide.get("consistency_valid")),
    )

    followers_value = dataset_row.get("followers")
    if not isinstance(followers_value, (int, float)):
        followers_value = dataset_row.get("followers_count")
    if not isinstance(followers_value, (int, float)):
        followers_value = normalized_metrics.get("followers_growth_total")
    followers = metric_entry(
        value=int(followers_value) if isinstance(followers_value, (int, float)) else None,
        source_metric_name="followers_count" if is_instagram_business else "followers_count",
        source_field_path="dataset.data.followers_count" if is_instagram_business else "dataset.data.followers",
        semantic_valid=isinstance(followers_value, (int, float)),
    )

    followers_growth_mapping = metric_mapping.get("followers_growth") if isinstance(metric_mapping.get("followers_growth"), dict) else {}
    followers_growth_value = normalized_metrics.get("followers_growth_total")
    followers_growth = metric_entry(
        value=int(followers_growth_value) if isinstance(followers_growth_value, (int, float)) else None,
        source_metric_name=str(followers_growth_mapping.get("source_metric_name") or "") or None,
        source_field_path="dataset.data.normalized_report_metrics.followers_growth_total",
        semantic_valid=bool(
            str(followers_growth_mapping.get("source_metric_name") or "") == "page_fan_adds"
            and isinstance(followers_growth_value, (int, float))
        ),
    )

    interactions_mapping = metric_mapping.get("interactions") if isinstance(metric_mapping.get("interactions"), dict) else {}
    interactions_value = normalized_metrics.get("interactions_total")
    if not isinstance(interactions_value, (int, float)):
        interactions_value = dataset_row.get("engagement")
    if not isinstance(interactions_value, (int, float)):
        interactions_value = dataset_row.get("content_interactions")
    if not isinstance(interactions_value, (int, float)):
        interactions_value = dataset_row.get("total_interactions")
    if not isinstance(interactions_value, (int, float)):
        interactions_value = dataset_row.get("accounts_engaged")
    interactions = metric_entry(
        value=int(interactions_value) if isinstance(interactions_value, (int, float)) else None,
        source_metric_name=str(interactions_mapping.get("source_metric_name") or "") or None,
        source_field_path="dataset.data.normalized_report_metrics.interactions_total",
        semantic_valid=bool(
            (
                str(interactions_mapping.get("source_metric_name") or "") == "page_post_engagements"
                or str(interactions_mapping.get("source_metric_name") or "") in {"total_interactions", "accounts_engaged"}
            )
            and isinstance(interactions_value, (int, float))
        ),
    )

    link_clicks_mapping = metric_mapping.get("link_clicks") if isinstance(metric_mapping.get("link_clicks"), dict) else {}
    link_clicks_value = normalized_metrics.get("link_clicks_total")
    if not isinstance(link_clicks_value, (int, float)):
        link_clicks_value = dataset_row.get("link_clicks")
    if not isinstance(link_clicks_value, (int, float)):
        link_clicks_value = dataset_row.get("website_clicks")
    link_clicks = metric_entry(
        value=int(link_clicks_value) if isinstance(link_clicks_value, (int, float)) else None,
        source_metric_name=str(link_clicks_mapping.get("source_metric_name") or "") or None,
        source_field_path="dataset.data.normalized_report_metrics.link_clicks_total",
        semantic_valid=bool(
            (
                str(link_clicks_mapping.get("source_metric_name") or "") == "website_clicks"
                or str(link_clicks_mapping.get("source_metric_name") or "") == "page_consumptions_by_consumption_type"
            )
            and isinstance(link_clicks_value, (int, float))
        ),
    )

    page_visits_mapping = metric_mapping.get("page_visits") if isinstance(metric_mapping.get("page_visits"), dict) else {}
    page_visits_value = normalized_metrics.get("page_visits_total")
    if not isinstance(page_visits_value, (int, float)):
        page_visits_value = dataset_row.get("profile_visits")
    if not isinstance(page_visits_value, (int, float)):
        page_visits_value = dataset_row.get("profile_views")
    if not isinstance(page_visits_value, (int, float)):
        page_visits_value = normalized_metrics.get("views_total")
    page_visits = metric_entry(
        value=int(page_visits_value) if isinstance(page_visits_value, (int, float)) else None,
        source_metric_name=str(page_visits_mapping.get("source_metric_name") or "") or None,
        source_field_path="dataset.data.normalized_report_metrics.page_visits_total",
        semantic_valid=bool(
            (
                str(page_visits_mapping.get("source_metric_name") or "") == "page_profile_views"
                or str(page_visits_mapping.get("source_metric_name") or "") == "profile_views"
            )
            and isinstance(page_visits_value, (int, float))
        ),
    )

    frequency_value = None
    frequency_valid = False
    if (
        impressions.get("available")
        and reach.get("available")
        and isinstance(impressions.get("value"), (int, float))
        and isinstance(reach.get("value"), (int, float))
        and int(reach["value"]) > 0
    ):
        frequency_value = round(float(impressions["value"]) / float(reach["value"]), 2)
        frequency_valid = True

    frequency = metric_entry(
        value=frequency_value,
        source_metric_name="derived_impressions_over_reach" if frequency_valid else None,
        source_field_path="dataset.data.normalized_report_metrics.impressions_total / dataset.data.reach",
        semantic_valid=frequency_valid,
    )

    return {
        "type": "general_insights_slide",
        "metrics": {
            "reach": reach,
            "impressions": impressions,
            "frequency": frequency,
            "followers": followers,
            "followers_growth": followers_growth,
            "interactions": interactions,
            "link_clicks": link_clicks,
            "page_visits": page_visits,
        },
    }


def _fetch_meta_pages_reach_payload(
    access_token: str,
    page_id: str,
    timeframe_config: dict[str, str],
    integration_id: int,
) -> dict[str, object | None]:
    for metric_name in META_PAGES_REACH_METRIC_CANDIDATES:
        try:
            metric_insight = fetch_page_insights(
                access_token,
                page_id,
                metrics=[metric_name],
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            logger.warning(
                "Meta Pages reach metric rejected",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                    "error": str(exc.detail),
                },
            )
            continue

        metric_value = metric_insight.get(metric_name)
        if metric_value is None:
            logger.info(
                "Meta Pages reach metric returned no value",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                },
            )
            continue

        try:
            reach_daily = fetch_page_insights_timeseries(
                access_token,
                page_id,
                metric_name,
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
            )
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            logger.warning(
                "Meta Pages reach timeseries rejected",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                    "error": str(exc.detail),
                },
            )
            reach_daily = []

        logger.info(
            "Meta Pages reach metric resolved",
            extra={
                "integration_id": integration_id,
                "page_id": page_id,
                "metric_name": metric_name,
                "timeframe": timeframe_config["preset"],
                "reach_value": metric_value,
                "reach_daily_points": len(reach_daily),
            },
        )
        return {
            "metric_name": metric_name,
            "value": metric_value,
            "end_time": metric_insight.get(f"{metric_name}_end_time"),
            "reach_daily": reach_daily,
        }

    return {
        "metric_name": None,
        "value": None,
        "end_time": None,
        "reach_daily": [],
    }


def _fetch_meta_pages_impressions_payload(
    access_token: str,
    page_id: str,
    timeframe_config: dict[str, str],
    integration_id: int,
) -> dict[str, object | None]:
    for metric_name in META_PAGES_IMPRESSIONS_METRIC_CANDIDATES:
        try:
            metric_insight = fetch_page_insights(
                access_token,
                page_id,
                metrics=[metric_name],
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            logger.warning(
                "Meta Pages impressions metric rejected",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                    "error": str(exc.detail),
                },
            )
            continue

        metric_value = metric_insight.get(metric_name)
        if metric_value is None:
            logger.info(
                "Meta Pages impressions metric returned no value",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                },
            )
            continue

        try:
            impressions_daily = fetch_page_insights_timeseries(
                access_token,
                page_id,
                metric_name,
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
            )
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            logger.warning(
                "Meta Pages impressions timeseries rejected",
                extra={
                    "integration_id": integration_id,
                    "page_id": page_id,
                    "metric_name": metric_name,
                    "timeframe": timeframe_config["preset"],
                    "error": str(exc.detail),
                },
            )
            impressions_daily = []

        logger.info(
            "Meta Pages impressions metric resolved",
            extra={
                "integration_id": integration_id,
                "page_id": page_id,
                "metric_name": metric_name,
                "timeframe": timeframe_config["preset"],
                "impressions_value": metric_value,
                "impressions_daily_points": len(impressions_daily),
            },
        )
        return {
            "metric_name": metric_name,
            "value": metric_value,
            "end_time": metric_insight.get(f"{metric_name}_end_time"),
            "impressions_daily": impressions_daily,
        }

    return {
        "metric_name": None,
        "value": None,
        "end_time": None,
        "impressions_daily": [],
    }


def _fetch_meta_pages_metric_payload(
    access_token: str,
    page_id: str,
    timeframe_config: dict[str, str],
    integration_id: int,
    *,
    metric_name: str,
    label: str,
    daily_key: str = "daily_series",
) -> dict[str, object | None]:
    try:
        metric_insight = fetch_page_insights(
            access_token,
            page_id,
            metrics=[metric_name],
            since=timeframe_config["since"],
            until=timeframe_config["until"],
        )
    except HTTPException as exc:
        if not _is_meta_api_error(exc):
            raise
        logger.warning(
            "Meta Pages metric rejected",
            extra={
                "integration_id": integration_id,
                "page_id": page_id,
                "metric_name": metric_name,
                "label": label,
                "timeframe": timeframe_config["preset"],
                "error": str(exc.detail),
            },
        )
        return {
            "metric_name": metric_name,
            "value": None,
            "end_time": None,
            daily_key: [],
        }

    metric_value = metric_insight.get(metric_name)
    try:
        daily_series = fetch_page_insights_timeseries(
            access_token,
            page_id,
            metric_name,
            since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
            until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
        )
    except HTTPException as exc:
        if not _is_meta_api_error(exc):
            raise
        logger.warning(
            "Meta Pages metric timeseries rejected",
            extra={
                "integration_id": integration_id,
                "page_id": page_id,
                "metric_name": metric_name,
                "label": label,
                "timeframe": timeframe_config["preset"],
                "error": str(exc.detail),
            },
        )
        daily_series = []

    logger.info(
        "Meta Pages metric resolved",
        extra={
            "integration_id": integration_id,
            "page_id": page_id,
            "metric_name": metric_name,
            "label": label,
            "timeframe": timeframe_config["preset"],
            "value": metric_value,
            "daily_points_count": len(daily_series),
        },
    )
    return {
        "metric_name": metric_name,
        "value": metric_value if isinstance(metric_value, (int, float)) else None,
        "end_time": metric_insight.get(f"{metric_name}_end_time"),
        daily_key: daily_series,
    }


def _get_meta_integration(
    db: Session, current_user: User, integration_id: int, *, require_access: bool = True
) -> Integration:
    integration = db.get(Integration, integration_id)
    if not integration or integration.provider != "meta":
        raise http_error(404, "integration_not_found", "Integration not found.")
    if require_access:
        _require_workspace_access(db, current_user.id, integration.workspace_id)
    return integration


def _meta_page_out_from_cache(meta_page: MetaPage) -> MetaPageOut:
    is_instagram_record = meta_page.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT
    return MetaPageOut(
        id=meta_page.page_id,
        account_id=meta_page.page_id if is_instagram_record else None,
        page_id=meta_page.page_id,
        type=meta_page.record_type,
        parent_page_id=meta_page.parent_page_id,
        facebook_page_id=meta_page.parent_page_id if is_instagram_record else meta_page.page_id,
        facebook_page_name=meta_page.business_name if is_instagram_record else meta_page.name,
        username=meta_page.instagram_username if is_instagram_record else None,
        name=meta_page.name,
        category=meta_page.category,
        instagram_username=meta_page.instagram_username,
        profile_picture_url=meta_page.profile_picture_url,
        fan_count=None,
        followers_count=None,
        source="business" if meta_page.business_name else "direct",
        business_name=meta_page.business_name,
    )


def _safe_meta_callback_payload(
    *,
    success: bool,
    pages: list[MetaPageOut] | list[dict[str, Any]],
    error: str | None = None,
    integration_id: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": success,
        "pages": [
            page.model_dump() if isinstance(page, MetaPageOut) else page
            for page in pages
        ],
    }
    if integration_id is not None:
        payload["integration_id"] = integration_id
    if success:
        payload["status"] = "connected"
    if error:
        payload["error"] = error
    return payload


def _meta_frontend_base_url() -> str:
    configured_base = str(settings.frontend_base_url or settings.report_export_base_url or "").strip()
    if configured_base:
        return configured_base.rstrip("/")
    return "http://localhost:3000"


def _meta_oauth_frontend_callback_url(
    *,
    status: str,
    source: str | None = None,
    integration_id: int | None = None,
    error: str | None = None,
) -> str:
    params: dict[str, str | int] = {"status": status}
    if integration_id is not None:
        params["integration_id"] = integration_id
    if source:
        params["source"] = source
    if error:
        params["error"] = error
    return f"{_meta_frontend_base_url()}/integrations/meta/callback?{urlencode(params)}"


def _meta_oauth_frontend_redirect_response(
    *,
    status: str,
    source: str | None = None,
    integration_id: int | None = None,
    error: str | None = None,
) -> RedirectResponse:
    target_url = _meta_oauth_frontend_callback_url(
        status=status,
        source=source,
        integration_id=integration_id,
        error=error,
    )
    logger.warning(
        "Meta Pages callback redirecting to frontend status=%s integration_id=%s source=%s error=%s redirect_target=%s",
        status,
        integration_id,
        source,
        error,
        target_url,
    )
    return RedirectResponse(url=target_url, status_code=307)


def _meta_api_error_details(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    meta_error = detail.get("meta_error") if isinstance(detail.get("meta_error"), dict) else {}
    return {
        "upstream_status_code": detail.get("upstream_status_code"),
        "error_type": meta_error.get("type"),
        "error_code": meta_error.get("code"),
        "error_message": meta_error.get("message") or detail.get("message"),
        "response_body": detail.get("response_body"),
    }


def _extract_debug_token_summary(debug_token_payload: dict[str, Any]) -> dict[str, Any]:
    data = debug_token_payload.get("data")
    if not isinstance(data, dict):
        return {
            "is_valid": None,
            "scopes": [],
            "granular_target_ids": [],
        }
    return {
        "is_valid": data.get("is_valid"),
        "scopes": data.get("scopes") if isinstance(data.get("scopes"), list) else [],
        "granular_target_ids": _extract_meta_debug_token_target_ids(debug_token_payload),
    }


def _run_meta_pages_oauth_callback(
    *,
    code: str,
    state: str,
    redirect_uri: str | None,
    db: Session,
    current_user: User | None = None,
    redirect_to_frontend: bool = False,
) -> dict[str, Any] | RedirectResponse:
    try:
        payload = decode_state(state)
    except ValueError:
        logger.warning("Meta Pages OAuth callback received invalid state")
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    try:
        state_user_id = int(payload.get("user_id", 0))
        workspace_id = int(payload.get("workspace_id", 0))
        state_integration_id = int(payload.get("integration_id", 0)) if payload.get("integration_id") else None
        selected_integration_type = (
            str(payload.get("integration_type") or "").strip() or None
        )
        state_source = str(payload.get("source") or "").strip() or None
        state_callback_route = str(payload.get("callback_route") or "").strip() or None
    except (TypeError, ValueError):
        logger.warning("Meta Pages OAuth callback state could not be parsed", extra={"payload": payload})
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    if workspace_id <= 0:
        logger.warning(
            "Meta Pages OAuth callback received invalid workspace id",
            extra={"workspace_id": workspace_id, "payload": payload},
        )
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    effective_user_id = current_user.id if current_user is not None else state_user_id
    if effective_user_id <= 0:
        logger.warning(
            "Meta Pages OAuth callback received invalid user id",
            extra={"state_user_id": state_user_id, "current_user_id": current_user.id if current_user else None},
        )
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    integration_id: int | None = None
    integration: Integration | None = None
    try:
        logger.warning(
            "Meta Pages callback received code_received=%s workspace_id=%s user_id=%s state_integration_id=%s redirect_uri_param=%s selected_integration_type=%s state_callback_route=%s state=%s",
            bool(code),
            workspace_id,
            effective_user_id,
            state_integration_id,
            redirect_uri,
            selected_integration_type,
            state_callback_route,
            payload,
        )
        _require_workspace_access(db, effective_user_id, workspace_id)

        if state_integration_id:
            integration = db.get(Integration, state_integration_id)
            if (
                integration is None
                or integration.provider != "meta"
                or integration.workspace_id != workspace_id
            ):
                logger.warning(
                    "Meta Pages callback state integration mismatch state_integration_id=%s resolved_integration_id=%s workspace_id=%s",
                    state_integration_id,
                    integration.id if integration else None,
                    workspace_id,
                )
                integration = None
        if integration is None:
            integration = _get_or_create_meta_integration_for_workspace(db, workspace_id)
        integration_id = integration.id

        redirect_uri = _meta_pages_redirect_uri()
        logger.warning(
            "Meta Pages callback exchanging code workspace_id=%s user_id=%s state_integration_id=%s callback_exchange_redirect_uri=%s",
            workspace_id,
            effective_user_id,
            state_integration_id,
            redirect_uri,
        )
        try:
            token_data = exchange_pages_code_for_token(code, redirect_uri=redirect_uri)
        except HTTPException as exc:
            if not _is_meta_api_error(exc):
                raise
            error_details = _meta_api_error_details(exc)
            logger.warning(
                "Meta Pages callback token exchange failed workspace_id=%s user_id=%s state_integration_id=%s callback_exchange_redirect_uri=%s code_received=%s token_exchange_success=%s status_code=%s error_type=%s error_code=%s error_message=%s response_body=%s",
                workspace_id,
                effective_user_id,
                state_integration_id,
                redirect_uri,
                bool(code),
                False,
                error_details["upstream_status_code"],
                error_details["error_type"],
                error_details["error_code"],
                error_details["error_message"],
                error_details["response_body"],
            )
            if integration is not None:
                _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_frontend_redirect_response(
                    status="error",
                    source=state_source,
                    integration_id=integration_id,
                    error="token_exchange_failed",
                )
            return _safe_meta_callback_payload(
                success=False,
                pages=[],
                error="token_exchange_failed",
                integration_id=integration_id,
            )
        access_token = str(token_data.get("access_token") or "").strip()
        token_exchange_status_code = token_data.get("_meta_http_status_code")
        token_exchange_raw_body = token_data.get("_meta_raw_body")
        logger.warning(
            "Meta Pages callback token exchange token_received=%s access_token_received=%s access_token_length=%s token_exchange_success=%s token_exchange_status_code=%s token_exchange_raw_body=%s workspace_id=%s user_id=%s state_integration_id=%s token_preview=%s selected_integration_type=%s",
            bool(access_token),
            bool(access_token),
            len(access_token),
            bool(access_token),
            token_exchange_status_code,
            token_exchange_raw_body,
            workspace_id,
            effective_user_id,
            state_integration_id,
            f"{access_token[:8]}..." if access_token else None,
            selected_integration_type,
        )
        if not access_token:
            logger.warning(
                "Meta Pages callback token exchange returned no access token workspace_id=%s user_id=%s state_integration_id=%s redirect_uri=%s token_data_keys=%s",
                workspace_id,
                effective_user_id,
                state_integration_id,
                redirect_uri,
                sorted(token_data.keys()) if isinstance(token_data, dict) else None,
            )
            logger.warning(
                "Meta Pages OAuth callback missing access token after exchange",
                extra={"workspace_id": workspace_id, "user_id": effective_user_id, "token_data": token_data},
            )
            if integration is not None:
                _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_frontend_redirect_response(
                    status="error",
                    source=state_source,
                    integration_id=integration_id,
                    error="token_exchange_failed",
                )
            return _safe_meta_callback_payload(success=False, pages=[], error="token_exchange_failed")
        logger.warning(
            "Meta Pages callback integration resolved workspace_id=%s user_id=%s integration_id=%s state_integration_id=%s",
            workspace_id,
            effective_user_id,
            integration.id,
            state_integration_id,
        )

        token_account = _get_meta_token_account(db, integration.id)
        if not token_account:
            token_account = IntegrationAccount(
                integration_id=integration.id,
                workspace_id=workspace_id,
                external_account_id=_meta_token_account_external_id(integration.id),
                display_name="Meta token store",
            )
            db.add(token_account)
            db.commit()
            db.refresh(token_account)

        saved_token = _replace_integration_token(
            db,
            account_id=token_account.id,
            workspace_id=workspace_id,
            access_token=access_token,
        )
        existing_pages_before = (
            db.query(MetaPage)
            .filter(MetaPage.integration_id == integration.id)
            .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
            .all()
        )
        logger.warning(
            "Meta Pages callback token stored integration_id=%s workspace_id=%s user_id=%s token_account_id=%s saved_token_id=%s",
            integration.id,
            workspace_id,
            effective_user_id,
            token_account.id,
            saved_token.id,
        )
        debug_token_payload = debug_token(access_token)
        debug_token_summary = _extract_debug_token_summary(debug_token_payload)
        logger.warning(
            "Meta Pages callback token debug integration_id=%s workspace_id=%s user_id=%s saved_token_id=%s debug_token_is_valid=%s debug_token_scopes=%s debug_token_granular_target_ids=%s",
            integration.id,
            workspace_id,
            effective_user_id,
            saved_token.id,
            debug_token_summary["is_valid"],
            debug_token_summary["scopes"],
            debug_token_summary["granular_target_ids"],
        )
        if debug_token_summary["is_valid"] is not True:
            _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_frontend_redirect_response(
                    status="error",
                    source=state_source,
                    integration_id=integration.id,
                    error="token_exchange_failed",
                )
            return _safe_meta_callback_payload(
                success=False,
                pages=[],
                error="token_exchange_failed",
                integration_id=integration.id,
            )

        cached_pages, diagnostics, facebook_pages = _refresh_meta_pages_from_live_graph(
            db,
            integration,
            access_token=access_token,
            user_id=effective_user_id,
            selected_integration_type=selected_integration_type,
            context="oauth_callback",
            return_empty_on_error=False,
        )
        instagram_accounts = _filter_meta_records(
            cached_pages,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        )
        page_payloads = [_meta_page_out_from_cache(page) for page in facebook_pages]
        graph_page_names = [item.get("page_name") for item in diagnostics if item.get("page_name")]
        logger.warning(
            "Meta Pages callback account sync integration_id=%s workspace_id=%s user_id=%s state_integration_id=%s resolved_integration_id=%s token_id=%s pages_returned_from_graph=%s page_names_returned_from_graph=%s pages_deleted_as_stale=%s pages_saved_final=%s instagram_accounts_saved_final=%s selected_integration_type=%s",
            integration.id,
            workspace_id,
            effective_user_id,
            state_integration_id,
            integration.id,
            saved_token.id,
            len(graph_page_names),
            graph_page_names,
            max(len(existing_pages_before) - len(cached_pages), 0) if graph_page_names else 0,
            len(page_payloads),
            len(instagram_accounts),
            selected_integration_type,
        )
        _log_meta_account_summary(
            integration_id=integration.id,
            user_id=effective_user_id,
            selected_integration_type=selected_integration_type,
            facebook_pages=cached_pages,
            instagram_accounts=instagram_accounts,
            context="oauth_callback",
        )
        _set_meta_integration_status(db, integration, status="connected")
        logger.warning(
            "Meta Pages callback completed integration_id=%s workspace_id=%s user_id=%s status=%s pages_count=%s page_names=%s instagram_accounts_found_count=%s",
            integration.id,
            workspace_id,
            effective_user_id,
            integration.status,
            len(page_payloads),
            [page.name for page in page_payloads],
            len(instagram_accounts),
        )
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(
                status="connected",
                source=state_source,
                integration_id=integration.id,
            )
        return _safe_meta_callback_payload(
            success=True,
            pages=page_payloads,
            integration_id=integration.id,
        )
    except HTTPException as exc:
        error_code = "meta_fetch_failed"
        if isinstance(exc.detail, dict):
            error_code = str(exc.detail.get("code") or error_code)
        logger.warning(
            "Meta Pages OAuth callback handled HTTP exception",
            extra={
                "workspace_id": workspace_id,
                "user_id": effective_user_id,
                "integration_id": integration_id,
                "error": str(exc.detail),
            },
        )
        if integration is not None:
            _set_meta_integration_status(db, integration, status="disconnected")
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(
                status="error",
                source=state_source,
                integration_id=integration_id,
                error=error_code,
            )
        return _safe_meta_callback_payload(
            success=False,
            pages=[],
            error=error_code,
            integration_id=integration_id,
        )
    except Exception:
        logger.exception(
            "Meta Pages OAuth callback failed unexpectedly",
            extra={"workspace_id": workspace_id, "user_id": effective_user_id, "integration_id": integration_id},
        )
        if integration is not None:
            _set_meta_integration_status(db, integration, status="disconnected")
        if redirect_to_frontend:
            return _meta_oauth_frontend_redirect_response(
                status="error",
                source=state_source,
                integration_id=integration_id,
                error="meta_fetch_failed",
            )
        return _safe_meta_callback_payload(
            success=False,
            pages=[],
            error="meta_fetch_failed",
            integration_id=integration_id,
        )


def _fetch_meta_pages_catalog(
    access_token: str,
    integration_id: int,
    *,
    user_id: int | None = None,
    context: str = "authorized_cache",
    token_received: bool | None = None,
    selected_integration_type: str | None = None,
) -> list[dict[str, Any]]:
    authorized_records, diagnostics = _collect_meta_instagram_diagnostics(
        access_token,
        integration_id,
        user_id=user_id,
        context=context,
    )

    authorized_pages = [
        record
        for record in authorized_records
        if record["record_type"] == META_RECORD_TYPE_FACEBOOK_PAGE
    ]
    instagram_accounts = [
        record
        for record in authorized_records
        if record["record_type"] == META_RECORD_TYPE_INSTAGRAM_ACCOUNT
    ]
    logger.warning(
        "Meta Pages catalog built context=%s integration_id=%s user_id=%s direct_pages_count=%s total_pages_count=%s page_names=%s selected_integration_type=%s",
        context,
        integration_id,
        user_id,
        len(authorized_pages),
        len(authorized_records),
        [page.get("name") for page in authorized_pages if page.get("name")],
        selected_integration_type,
    )
    _log_meta_account_summary(
        integration_id=integration_id,
        user_id=user_id,
        selected_integration_type=selected_integration_type,
        facebook_pages=authorized_pages,
        instagram_accounts=instagram_accounts,
        context=context,
    )
    _log_meta_pages_debug(
        integration_id=integration_id,
        source="meta_me_accounts",
        pages=authorized_pages,
    )
    logger.info(
        "Meta Instagram diagnostics summary",
        extra={
            "integration_id": integration_id,
            "user_id": user_id,
            "instagram_accounts_found_count": len(instagram_accounts),
            "instagram_usernames_found": [
                str(record.get("instagram_username") or "").strip()
                for record in instagram_accounts
                if str(record.get("instagram_username") or "").strip()
            ],
            "diagnostics_pages_checked": len(diagnostics),
        },
    )
    return list(authorized_records)


def _get_stored_meta_records(
    db: Session,
    integration_id: int,
    *,
    record_type: str,
) -> list[MetaPage]:
    return (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration_id,
            MetaPage.record_type == record_type,
        )
        .order_by(MetaPage.name.asc(), MetaPage.page_id.asc())
        .all()
    )


def _cache_meta_pages(
    db: Session,
    integration: Integration,
    user_id: int | None,
    pages: list[dict[str, Any]],
) -> list[MetaPage]:
    def _apply_cache_changes() -> None:
        existing_pages = (
            db.query(MetaPage)
            .filter(MetaPage.integration_id == integration.id)
            .all()
        )
        existing_by_record_key = {
            _meta_record_key(page.record_type, page.page_id): page
            for page in existing_pages
        }
        incoming_record_keys = {
            _meta_record_key(
                str(page.get("record_type") or META_RECORD_TYPE_FACEBOOK_PAGE),
                str(page.get("page_id") or ""),
            )
            for page in pages
            if str(page.get("page_id") or "")
        }

        for existing_page in existing_pages:
            if _meta_record_key(existing_page.record_type, existing_page.page_id) not in incoming_record_keys:
                db.delete(existing_page)

        for page in pages:
            page_id = str(page.get("page_id") or "")
            if not page_id:
                continue
            record_type = str(page.get("record_type") or META_RECORD_TYPE_FACEBOOK_PAGE)
            cached_page = existing_by_record_key.get(_meta_record_key(record_type, page_id))
            if not cached_page:
                cached_page = MetaPage(
                    integration_id=integration.id,
                    user_id=user_id,
                    record_type=record_type,
                    page_id=page_id,
                    parent_page_id=str(page.get("parent_page_id") or "") or None,
                    name=str(page.get("name") or page_id),
                    instagram_username=str(page.get("instagram_username") or "") or None,
                    profile_picture_url=str(page.get("profile_picture_url") or "") or None,
                    page_access_token=str(page.get("page_access_token") or "") or None,
                    tasks=page.get("tasks") if isinstance(page.get("tasks"), list) else None,
                    perms=page.get("perms") if isinstance(page.get("perms"), list) else [],
                    category=page.get("category"),
                    business_name=page.get("business_name"),
                )
                db.add(cached_page)
                continue

            cached_page.user_id = user_id
            cached_page.record_type = record_type
            cached_page.name = str(page.get("name") or page_id)
            cached_page.parent_page_id = str(page.get("parent_page_id") or "") or None
            cached_page.instagram_username = str(page.get("instagram_username") or "") or None
            cached_page.profile_picture_url = str(page.get("profile_picture_url") or "") or None
            cached_page.page_access_token = str(page.get("page_access_token") or "") or None
            cached_page.tasks = page.get("tasks") if isinstance(page.get("tasks"), list) else None
            cached_page.perms = page.get("perms") if isinstance(page.get("perms"), list) else []
            cached_page.category = page.get("category")
            cached_page.business_name = page.get("business_name")

    try:
        _apply_cache_changes()
        db.commit()
    except IntegrityError:
        db.rollback()
        _apply_cache_changes()
        db.commit()
    stored_pages = (
        db.query(MetaPage)
        .filter(MetaPage.integration_id == integration.id)
        .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
        .all()
    )
    _log_meta_account_summary(
        integration_id=integration.id,
        user_id=user_id,
        selected_integration_type=None,
        facebook_pages=_filter_meta_records(stored_pages, record_type=META_RECORD_TYPE_FACEBOOK_PAGE),
        instagram_accounts=_filter_meta_records(stored_pages, record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT),
        context="cache_write",
    )
    return stored_pages


def _clear_selected_meta_page_if_unauthorized(
    db: Session,
    integration: Integration,
    authorized_page_ids: set[str],
) -> None:
    selected_pages = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id.like(f"{META_PAGE_ACCOUNT_PREFIX}%"),
        )
        .all()
    )

    cleared_any = False
    for selected_page in selected_pages:
        if _get_meta_page_id(selected_page) in authorized_page_ids:
            continue
        db.delete(selected_page)
        cleared_any = True

    if cleared_any:
        db.commit()


def _refresh_meta_pages_authorized_cache(
    db: Session,
    integration: Integration,
    access_token: str,
    *,
    user_id: int | None = None,
    selected_integration_type: str | None = None,
    return_empty_on_error: bool = False,
) -> list[MetaPage]:
    cached_pages, _, facebook_pages = _refresh_meta_pages_from_live_graph(
        db,
        integration,
        access_token=access_token,
        user_id=user_id,
        selected_integration_type=selected_integration_type,
        context="authorized_cache",
        return_empty_on_error=return_empty_on_error,
    )
    if cached_pages or not return_empty_on_error:
        return facebook_pages
    return []


def _get_meta_token_account(db: Session, integration_id: int) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id == _meta_token_account_external_id(integration_id),
        )
        .first()
    )


def _get_latest_integration_token(db: Session, account_id: int) -> IntegrationToken | None:
    return (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .first()
    )


def _replace_integration_token(
    db: Session,
    *,
    account_id: int,
    workspace_id: int,
    access_token: str,
) -> IntegrationToken:
    existing_tokens = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .all()
    )
    token = existing_tokens[0] if existing_tokens else None
    if not token:
        token = IntegrationToken(
            account_id=account_id,
            workspace_id=workspace_id,
            token_type="access_token",
            access_token=access_token,
            refresh_token=None,
            expires_at=None,
        )
        db.add(token)
    else:
        token.token_type = "access_token"
        token.access_token = access_token
        token.refresh_token = None
        token.expires_at = None
        db.add(token)
        for stale_token in existing_tokens[1:]:
            db.delete(stale_token)
    db.commit()
    db.refresh(token)
    logger.info(
        "Meta token replaced",
        extra={
            "account_id": account_id,
            "workspace_id": workspace_id,
            "kept_token_id": token.id,
            "deleted_stale_tokens_count": max(len(existing_tokens) - 1, 0),
            "token_updated_at": token.updated_at.isoformat() if token.updated_at else None,
        },
    )
    return token


def _get_meta_access_token(db: Session, integration: Integration) -> str:
    if integration.status != "connected":
        raise http_error(401, "missing_token", "Meta token not found.")
    token_account = _get_meta_token_account(db, integration.id)
    if not token_account:
        raise http_error(401, "missing_token", "Meta token not found.")

    token = _get_latest_integration_token(db, token_account.id)
    if not token:
        raise http_error(401, "missing_token", "Meta token not found.")
    return token.access_token


def _get_selected_meta_account(db: Session, integration_id: int) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id != _meta_token_account_external_id(integration_id),
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )


def _get_selected_meta_page(db: Session, integration_id: int) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id.like(f"{META_PAGE_ACCOUNT_PREFIX}%"),
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )


def _get_meta_page_id(account: IntegrationAccount) -> str:
    return account.external_account_id.removeprefix(META_PAGE_ACCOUNT_PREFIX)


def _save_selected_meta_page(
    db: Session,
    integration: Integration,
    page_id: str,
    display_name: str | None = None,
) -> IntegrationAccount:
    existing_pages = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id.like(f"{META_PAGE_ACCOUNT_PREFIX}%"),
        )
        .all()
    )
    target_external_id = _meta_page_account_external_id(page_id)
    for existing_page in existing_pages:
        if existing_page.external_account_id != target_external_id:
            db.delete(existing_page)

    selected_page = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == target_external_id,
        )
        .first()
    )
    if not selected_page:
        selected_page = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=target_external_id,
            display_name=display_name,
        )
        db.add(selected_page)
    else:
        selected_page.display_name = display_name

    db.commit()
    db.refresh(selected_page)
    return selected_page


def _save_meta_page_token(
    db: Session,
    page_account: IntegrationAccount,
    workspace_id: int,
    access_token: str,
) -> None:
    _replace_integration_token(
        db,
        account_id=page_account.id,
        workspace_id=workspace_id,
        access_token=access_token,
    )


def _get_meta_page_access_token(
    db: Session,
    integration: Integration,
    page_account: IntegrationAccount,
) -> str:
    token = _get_latest_integration_token(db, page_account.id)
    if token and token.access_token:
        return token.access_token
    meta_page = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
            MetaPage.page_id == _get_meta_page_id(page_account),
        )
        .first()
    )
    if meta_page and meta_page.page_access_token:
        return meta_page.page_access_token
    return _get_meta_access_token(db, integration)


def _save_selected_meta_account(
    db: Session,
    integration: Integration,
    ad_account_id: str,
    display_name: str | None = None,
) -> IntegrationAccount:
    existing_accounts = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id != _meta_token_account_external_id(integration.id),
        )
        .all()
    )
    for existing_account in existing_accounts:
        if existing_account.external_account_id != ad_account_id:
            db.delete(existing_account)

    selected_account = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == ad_account_id,
        )
        .first()
    )
    if not selected_account:
        selected_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=ad_account_id,
            display_name=display_name,
        )
        db.add(selected_account)
    else:
        selected_account.display_name = display_name

    db.commit()
    db.refresh(selected_account)
    return selected_account


def _is_meta_ads_permission_error(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if detail.get("code") != "meta_api_error":
        return False

    message = str(detail.get("message", "")).lower()
    permission_markers = (
        "permission",
        "permissions",
        "not authorized",
        "ads_management",
        "ads_read",
        "does not have permission",
    )
    return any(marker in message for marker in permission_markers)


def _is_meta_nonexisting_accounts_field_error(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if detail.get("code") != "meta_api_error":
        return False
    message = str(detail.get("message", "")).lower()
    return "nonexisting field (accounts)" in message


def _is_meta_api_error(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return detail.get("code") == "meta_api_error"


def _meta_report_block(
    block_type: str,
    order: int,
    data: dict,
    editable_fields: list[str] | None = None,
) -> dict:
    return {
        "type": block_type,
        "order": order,
        "data_json": json.dumps(data),
        "editable_fields_json": json.dumps(editable_fields or []),
    }


def _meta_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _meta_format_number(value) -> str:
    numeric = _meta_number(value)
    if numeric is None:
        return "N/A"
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.1f}"


def _meta_point_label(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw[:10])
        return parsed.strftime("%b %-d")
    except Exception:
        try:
            parsed = date.fromisoformat(raw[:10])
            return parsed.strftime("%b %d").replace(" 0", " ")
        except Exception:
            return raw[:10]


def _meta_series_points(points) -> list[dict]:
    if not isinstance(points, list):
        return []
    normalized = []
    for point in points:
        if not isinstance(point, dict):
            continue
        value = _meta_number(point.get("value"))
        if value is None:
            continue
        point_date = point.get("date") or point.get("day") or point.get("label")
        normalized.append(
            {
                "date": point_date,
                "label": point.get("label") or _meta_point_label(point_date),
                "value": value,
            }
        )
    return normalized


def _meta_series_stats(points) -> dict:
    normalized = _meta_series_points(points)
    if not normalized:
        return {
            "points_count": 0,
            "total": None,
            "average": None,
            "highest": None,
            "lowest": None,
            "first": None,
            "last": None,
            "delta": None,
        }
    total = sum(float(point["value"]) for point in normalized)
    first = normalized[0]
    last = normalized[-1]
    return {
        "points_count": len(normalized),
        "total": total,
        "average": total / len(normalized),
        "highest": max(normalized, key=lambda point: float(point["value"])),
        "lowest": min(normalized, key=lambda point: float(point["value"])),
        "first": first,
        "last": last,
        "delta": float(last["value"]) - float(first["value"]),
    }


def _meta_trend_copy(metric: str, stats: dict, period_label: str) -> str:
    if not stats.get("points_count"):
        return f"{metric} daily data is not available for {period_label}."
    highest = stats["highest"]
    lowest = stats["lowest"]
    delta = stats.get("delta")
    direction = "increased" if delta and delta > 0 else "decreased" if delta and delta < 0 else "stayed flat"
    return (
        f"{metric} averaged {_meta_format_number(stats.get('average'))} per day during {period_label}. "
        f"The highest day was {highest.get('date') or 'N/A'} with "
        f"{_meta_format_number(highest.get('value'))}; the lowest day was "
        f"{lowest.get('date') or 'N/A'} with {_meta_format_number(lowest.get('value'))}. "
        f"The series {direction} from {_meta_format_number(stats['first'].get('value'))} "
        f"to {_meta_format_number(stats['last'].get('value'))}."
    )


def _meta_clone_chart(chart_data: dict, *, label: str, metric: str | None = None) -> dict:
    cloned = dict(chart_data) if isinstance(chart_data, dict) else {}
    cloned["label"] = label
    if metric:
        cloned["metric"] = metric
    return cloned


def _meta_text_excerpt(value, *, fallback: str = "N/A", limit: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _meta_direction(delta) -> str:
    numeric = _meta_number(delta)
    if numeric is None or numeric == 0:
        return "stable"
    return "up" if numeric > 0 else "down"


def _meta_recent_posts(context: dict) -> list[dict]:
    report_inputs = context.get("report_inputs") if isinstance(context.get("report_inputs"), dict) else {}
    posts = report_inputs.get("recent_posts")
    if not isinstance(posts, list):
        return []
    return [post for post in posts if isinstance(post, dict)]


def _meta_report_inputs(context: dict) -> dict:
    report_inputs = context.get("report_inputs")
    return report_inputs if isinstance(report_inputs, dict) else {}


def _meta_integration_type(context: dict) -> str | None:
    report_inputs = _meta_report_inputs(context)
    value = report_inputs.get("integration_type") if isinstance(report_inputs, dict) else None
    return str(value).strip() or None if value is not None else None


def _meta_unavailable_metrics(context: dict) -> dict[str, str]:
    report_inputs = _meta_report_inputs(context)
    raw = report_inputs.get("unavailable_metrics") if isinstance(report_inputs, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _meta_metric_unavailable_reason(context: dict, metric: str) -> str | None:
    unavailable = _meta_unavailable_metrics(context)
    candidate_keys = [metric]
    if metric == "engagement":
        candidate_keys.extend(["total_interactions", "content_interactions", "accounts_engaged"])
    elif metric == "followers":
        candidate_keys.extend(["followers_count", "followers"])
    elif metric == "profile_visits":
        candidate_keys.extend(["profile_views"])
    elif metric == "link_clicks":
        candidate_keys.extend(["website_clicks"])
    for key in candidate_keys:
        value = str(unavailable.get(key) or "").strip()
        if value:
            return value
    return None


def _meta_first_series(*values) -> list[dict]:
    for value in values:
        points = _meta_series_points(value)
        if points:
            return points
    return []


def _meta_post_date(post: dict) -> str | None:
    raw = post.get("date") or post.get("created_time") or post.get("published_at") or post.get("timestamp")
    if not raw:
        return None
    return str(raw)[:10]


def _meta_post_score(post: dict) -> float:
    score = 0.0
    for key in (
        "engagement",
        "engagements",
        "interactions",
        "reactions",
        "likes",
        "comments",
        "shares",
        "reach",
        "impressions",
    ):
        value = _meta_number(post.get(key))
        if value is not None:
            score += value
    return score


def _meta_posts_daily_series(context: dict, *, metric: str) -> list[dict]:
    grouped: dict[str, float] = {}
    for post in _meta_recent_posts(context):
        post_date = _meta_post_date(post)
        if not post_date:
            continue
        if metric == "posts":
            value = 1.0
        elif metric == "reach":
            value = _meta_number(post.get("reach")) or 0.0
        elif metric == "impressions":
            value = _meta_number(post.get("impressions")) or 0.0
        else:
            value = _meta_post_score(post)
        grouped[post_date] = grouped.get(post_date, 0.0) + value
    return [
        {"date": day, "label": _meta_point_label(day), "value": value}
        for day, value in sorted(grouped.items())
        if value or metric == "posts"
    ]


def _meta_metric_series(context: dict, metric: str) -> list[dict]:
    report_inputs = _meta_report_inputs(context)
    reach_chart = context.get("reach_chart_data") if isinstance(context.get("reach_chart_data"), dict) else {}
    impressions_payload = (
        context.get("impressions_slide_payload")
        if isinstance(context.get("impressions_slide_payload"), dict)
        else {}
    )
    if metric == "reach":
        return _meta_first_series(reach_chart.get("points"), report_inputs.get("reach_daily"))
    if metric == "impressions":
        return _meta_first_series(
            impressions_payload.get("impressions_daily"),
            report_inputs.get("impressions_daily"),
            _meta_posts_daily_series(context, metric="impressions"),
        )
    if metric == "engagement":
        if _meta_integration_type(context) == "instagram_business":
            return _meta_first_series(
                report_inputs.get("daily_engagement"),
                report_inputs.get("engagement_daily"),
                report_inputs.get("content_interactions_daily"),
                report_inputs.get("interactions_daily"),
            )
        return _meta_first_series(
            report_inputs.get("daily_engagement"),
            report_inputs.get("engagement_daily"),
            report_inputs.get("content_interactions_daily"),
            report_inputs.get("interactions_daily"),
            _meta_posts_daily_series(context, metric="engagement"),
        )
    if metric == "followers":
        return _meta_first_series(
            report_inputs.get("followers_daily"),
            report_inputs.get("fan_count_daily"),
            report_inputs.get("audience_daily"),
        )
    if metric == "posts":
        return _meta_posts_daily_series(context, metric="posts")
    return []


def _meta_metric_total(context: dict, metric: str, points: list[dict]) -> float | int | None:
    if metric == "reach":
        return _meta_number(context.get("reach")) or (sum(point["value"] for point in points) if points else None)
    if metric == "impressions":
        return _meta_number(context.get("impressions")) or (sum(point["value"] for point in points) if points else None)
    if metric == "engagement":
        if _meta_integration_type(context) == "instagram_business":
            return _meta_number(context.get("engagement")) or (
                sum(point["value"] for point in points) if points else None
            )
        return _meta_number(context.get("engagement")) or (sum(point["value"] for point in points) if points else None)
    if metric == "followers":
        return _meta_number(context.get("followers")) or (points[-1]["value"] if points else None)
    if metric == "posts":
        return len(_meta_recent_posts(context)) or (sum(point["value"] for point in points) if points else None)
    return sum(point["value"] for point in points) if points else None


def _meta_timeframe_range(context: dict) -> dict[str, object]:
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    current_since_raw = report_timeframe.get("current_since") or report_timeframe.get("since")
    current_until_raw = report_timeframe.get("current_until") or report_timeframe.get("until")
    previous_since_raw = report_timeframe.get("previous_since")
    previous_until_raw = report_timeframe.get("previous_until")
    requested_since_raw = report_timeframe.get("requested_since") or previous_since_raw or current_since_raw
    requested_until_raw = report_timeframe.get("requested_until") or current_until_raw
    if not current_since_raw or not current_until_raw:
        return {
            "timeframe_key": report_timeframe.get("key"),
            "selected_timeframe": report_timeframe.get("selected_timeframe") or report_timeframe.get("key"),
            "requested_since": None,
            "requested_until": None,
            "current_since": None,
            "current_until": None,
            "previous_since": None,
            "previous_until": None,
            "duration_days": None,
        }
    try:
        current_since = date.fromisoformat(str(current_since_raw)[:10])
        current_until = date.fromisoformat(str(current_until_raw)[:10])
    except ValueError:
        return {
            "timeframe_key": report_timeframe.get("key"),
            "selected_timeframe": report_timeframe.get("selected_timeframe") or report_timeframe.get("key"),
            "requested_since": None,
            "requested_until": None,
            "current_since": None,
            "current_until": None,
            "previous_since": None,
            "previous_until": None,
            "duration_days": None,
        }
    duration_days = (current_until - current_since).days + 1
    if duration_days <= 0:
        return {
            "timeframe_key": report_timeframe.get("key"),
            "selected_timeframe": report_timeframe.get("selected_timeframe") or report_timeframe.get("key"),
            "requested_since": str(requested_since_raw) if requested_since_raw else None,
            "requested_until": str(requested_until_raw) if requested_until_raw else None,
            "current_since": current_since.isoformat(),
            "current_until": current_until.isoformat(),
            "previous_since": None,
            "previous_until": None,
            "duration_days": None,
        }
    previous_since = None
    previous_until = None
    if previous_since_raw and previous_until_raw:
        try:
            previous_since = date.fromisoformat(str(previous_since_raw)[:10])
            previous_until = date.fromisoformat(str(previous_until_raw)[:10])
        except ValueError:
            previous_since = None
            previous_until = None
    if previous_since is None or previous_until is None:
        previous_until = current_since - timedelta(days=1)
        previous_since = current_since - timedelta(days=duration_days)
    return {
        "timeframe_key": report_timeframe.get("key"),
        "selected_timeframe": report_timeframe.get("selected_timeframe") or report_timeframe.get("key"),
        "requested_since": str(requested_since_raw) if requested_since_raw else previous_since.isoformat(),
        "requested_until": str(requested_until_raw) if requested_until_raw else current_until.isoformat(),
        "current_since": current_since.isoformat(),
        "current_until": current_until.isoformat(),
        "previous_since": previous_since.isoformat(),
        "previous_until": previous_until.isoformat(),
        "duration_days": duration_days,
    }


def _meta_points_in_range(points: list[dict], since_iso: str | None, until_iso: str | None) -> list[dict]:
    if not points or not since_iso or not until_iso:
        return []
    try:
        since_date = date.fromisoformat(since_iso[:10])
        until_date = date.fromisoformat(until_iso[:10])
    except ValueError:
        return []
    filtered = []
    for point in _meta_series_points(points):
        point_date_raw = point.get("date")
        if not point_date_raw:
            continue
        try:
            point_date = date.fromisoformat(str(point_date_raw)[:10])
        except ValueError:
            continue
        if since_date <= point_date <= until_date:
            filtered.append(point)
    return filtered


def _meta_current_metric_source(context: dict, metric: str, points: list[dict]) -> str | None:
    source_map = {
        "reach": [("context.reach", context.get("reach"))],
        "impressions": [("context.impressions", context.get("impressions"))],
        "engagement": [("context.engagement", context.get("engagement"))],
        "followers": [("context.followers", context.get("followers"))],
    }
    for source_name, source_value in source_map.get(metric, []):
        if _meta_number(source_value) is not None:
            return source_name
    if points:
        return f"series.{metric}_daily"
    return None


def _meta_metric_period_points(context: dict, metric: str, *, period: str) -> list[dict]:
    timeframe_range = _meta_timeframe_range(context)
    full_points = _meta_metric_series(context, metric)
    if period == "current":
        filtered_points = _meta_points_in_range(
            full_points,
            str(timeframe_range.get("current_since") or ""),
            str(timeframe_range.get("current_until") or ""),
        )
        return filtered_points or full_points
    if period == "previous":
        explicit_previous_points = _meta_previous_metric_series(context, metric)
        filtered_explicit_previous = _meta_points_in_range(
            explicit_previous_points,
            str(timeframe_range.get("previous_since") or ""),
            str(timeframe_range.get("previous_until") or ""),
        )
        if filtered_explicit_previous:
            return filtered_explicit_previous
        if explicit_previous_points:
            return explicit_previous_points
        return _meta_points_in_range(
            full_points,
            str(timeframe_range.get("previous_since") or ""),
            str(timeframe_range.get("previous_until") or ""),
        )
    return full_points


def _meta_previous_metric_series(context: dict, metric: str) -> list[dict]:
    report_inputs = _meta_report_inputs(context)
    previous_period = (
        report_inputs.get("previous_period")
        if isinstance(report_inputs.get("previous_period"), dict)
        else {}
    )
    comparison = (
        report_inputs.get("comparison")
        if isinstance(report_inputs.get("comparison"), dict)
        else {}
    )
    previous_comparison = (
        comparison.get("previous")
        if isinstance(comparison.get("previous"), dict)
        else {}
    )

    candidate_keys = [
        f"previous_{metric}_daily",
        f"{metric}_previous_daily",
        f"{metric}_daily_previous",
        f"{metric}_daily_prior",
        f"prior_{metric}_daily",
    ]
    if metric == "engagement":
        candidate_keys.extend(
            [
                "previous_content_interactions_daily",
                "content_interactions_previous_daily",
                "previous_interactions_daily",
                "interactions_previous_daily",
            ]
        )
    elif metric == "followers":
        candidate_keys.extend(
            [
                "previous_fan_count_daily",
                "fan_count_previous_daily",
                "previous_audience_daily",
                "audience_previous_daily",
            ]
        )

    candidates = []
    candidate_sources = []
    for source in (report_inputs, previous_period, previous_comparison, context):
        if not isinstance(source, dict):
            continue
        for key in candidate_keys:
            candidates.append(source.get(key))
            candidate_sources.append(key)
        daily = source.get("daily") if isinstance(source.get("daily"), dict) else {}
        metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        candidates.extend([daily.get(metric), metrics.get(metric)])
        candidate_sources.extend([f"daily.{metric}", f"metrics.{metric}"])

    for candidate, source_name in zip(candidates, candidate_sources):
        points = _meta_series_points(candidate)
        if points:
            return points
    return []


def _meta_metric_total_for_series(metric: str, points: list[dict]) -> float | int | None:
    if not points:
        return None
    if metric == "followers":
        return points[-1]["value"]
    if metric == "posts":
        return sum(point["value"] for point in points)
    return sum(point["value"] for point in points)


def _meta_previous_metric_total(context: dict, metric: str) -> float | int | None:
    report_inputs = _meta_report_inputs(context)
    previous_period = (
        report_inputs.get("previous_period")
        if isinstance(report_inputs.get("previous_period"), dict)
        else {}
    )
    comparison = (
        report_inputs.get("comparison")
        if isinstance(report_inputs.get("comparison"), dict)
        else {}
    )
    previous_comparison = (
        comparison.get("previous")
        if isinstance(comparison.get("previous"), dict)
        else {}
    )
    candidate_keys = [
        f"previous_{metric}",
        f"{metric}_previous",
        f"prior_{metric}",
        f"{metric}_prior",
    ]
    if metric == "engagement":
        candidate_keys.extend(
            [
                "previous_content_interactions",
                "content_interactions_previous",
                "previous_interactions",
                "interactions_previous",
            ]
        )
    elif metric == "followers":
        candidate_keys.extend(
            [
                "previous_fan_count",
                "fan_count_previous",
                "previous_audience",
                "audience_previous",
            ]
        )
    for source in (report_inputs, previous_period, previous_comparison, context):
        if not isinstance(source, dict):
            continue
        for key in candidate_keys:
            value = _meta_number(source.get(key))
            if value is not None:
                return value
    previous_points = _meta_metric_period_points(context, metric, period="previous")
    return _meta_metric_total_for_series(metric, previous_points)
    


def _meta_previous_metric_source(context: dict, metric: str) -> str | None:
    report_inputs = _meta_report_inputs(context)
    previous_period = (
        report_inputs.get("previous_period")
        if isinstance(report_inputs.get("previous_period"), dict)
        else {}
    )
    comparison = (
        report_inputs.get("comparison")
        if isinstance(report_inputs.get("comparison"), dict)
        else {}
    )
    previous_comparison = (
        comparison.get("previous")
        if isinstance(comparison.get("previous"), dict)
        else {}
    )
    candidate_keys = [
        f"previous_{metric}",
        f"{metric}_previous",
        f"prior_{metric}",
        f"{metric}_prior",
    ]
    if metric == "engagement":
        candidate_keys.extend(
            [
                "previous_content_interactions",
                "content_interactions_previous",
                "previous_interactions",
                "interactions_previous",
            ]
        )
    elif metric == "followers":
        candidate_keys.extend(
            [
                "previous_fan_count",
                "fan_count_previous",
                "previous_audience",
                "audience_previous",
            ]
        )
    for source in (report_inputs, previous_period, previous_comparison, context):
        if not isinstance(source, dict):
            continue
        for key in candidate_keys:
            if _meta_number(source.get(key)) is not None:
                return key
    previous_points = _meta_metric_period_points(context, metric, period="previous")
    if previous_points:
        explicit_previous_points = _meta_previous_metric_series(context, metric)
        if explicit_previous_points:
            return f"series.previous_{metric}_daily"
        return f"series.{metric}_daily.previous_period_slice"
    return None


def _meta_metric_comparison(
    context: dict,
    *,
    metric: str,
    current_value,
    current_points: list[dict],
) -> dict[str, object]:
    current_numeric = _meta_number(current_value)
    if current_numeric is None:
        current_numeric = _meta_metric_total_for_series(metric, current_points)
    current_period_points = current_points or _meta_metric_period_points(context, metric, period="current")
    source_current = _meta_current_metric_source(context, metric, current_period_points)
    previous_points = _meta_metric_period_points(context, metric, period="previous")
    previous_value = _meta_previous_metric_total(context, metric)
    previous_numeric = _meta_number(previous_value)
    source_previous = _meta_previous_metric_source(context, metric)

    if current_numeric is None:
        return {
            "current_value": current_numeric,
            "previous_value": previous_numeric,
            "change_absolute": None,
            "change_percentage": None,
            "trend": None,
            "source_current": source_current,
            "source_previous": source_previous,
            "reason_if_null": "current_value_unavailable",
            "current_points": len(current_period_points),
            "previous_points": len(previous_points),
        }
    if previous_numeric is None:
        return {
            "current_value": current_numeric,
            "previous_value": None,
            "change_absolute": None,
            "change_percentage": None,
            "trend": None,
            "source_current": source_current,
            "source_previous": source_previous,
            "reason_if_null": "previous_value_unavailable",
            "current_points": len(current_period_points),
            "previous_points": len(previous_points),
        }

    change_absolute = current_numeric - previous_numeric
    if previous_numeric > 0:
        change_percentage = (change_absolute / previous_numeric) * 100
        reason_if_null = None
    else:
        change_percentage = None
        reason_if_null = "previous_value_zero"
    if change_absolute > 0:
        trend = "up"
    elif change_absolute < 0:
        trend = "down"
    else:
        trend = "flat"
    return {
        "current_value": current_numeric,
        "previous_value": previous_numeric,
        "change_absolute": change_absolute,
        "change_percentage": round(change_percentage, 2) if change_percentage is not None else None,
        "trend": trend,
        "source_current": source_current,
        "source_previous": source_previous,
        "reason_if_null": reason_if_null,
        "current_points": len(current_period_points),
        "previous_points": len(previous_points),
    }


def _meta_change_payload(
    context: dict,
    *,
    metric: str,
    current_value,
    current_points: list[dict],
) -> dict[str, object]:
    comparison = _meta_metric_comparison(
        context,
        metric=metric,
        current_value=current_value,
        current_points=current_points,
    )
    return {
        "current_value": comparison.get("current_value"),
        "previous_value": comparison.get("previous_value"),
        "change_absolute": comparison.get("change_absolute"),
        "change_percentage": comparison.get("change_percentage"),
        "trend": comparison.get("trend"),
    }


def _meta_chart_payload(context: dict, metric: str, title: str | None = None) -> dict:
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    period_label = str(report_timeframe.get("label") or "Selected period")
    points = _meta_metric_series(context, metric)
    label_metric = title or metric.replace("_", " ").title()
    return {
        "label": f"Daily {label_metric} – {period_label}",
        "metric": metric,
        "points": points,
        "data": points,
        "series": points,
        "timeframe": report_timeframe,
        "is_available": bool(points),
    }


def _meta_data_payload(
    context: dict,
    *,
    semantic_name: str,
    title: str,
    label: str,
    metric: str,
    value=None,
    insight: str | None = None,
    extra: dict | None = None,
) -> dict:
    chart = _meta_chart_payload(context, metric, label)
    points = list(chart["points"])
    total = value if value is not None else _meta_metric_total(context, metric, points)
    fallback_insight = (
        insight
        if insight
        else f"Daily {label.lower()} data is available for this period."
        if points
        else f"Daily {label.lower()} data is not available for this period."
    )
    payload = {
        "semantic_name": semantic_name,
        "title": title,
        "label": label,
        "current_value": total,
        "value": total,
        "total": total,
        "insight": fallback_insight,
        "text": fallback_insight,
        "chart": chart,
        "points": points,
        **_meta_change_payload(
            context,
            metric=metric,
            current_value=total,
            current_points=points,
        ),
    }
    if extra:
        payload.update(extra)
    return payload


def _meta_enrich_existing_block(context: dict, block: dict) -> dict:
    data = block.get("data_json")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    if block.get("type") == "title":
        return block
    semantic_name = str(data.get("semantic_name") or "").strip()
    label = str(data.get("label") or data.get("title") or semantic_name or block.get("type") or "Metric")
    if not semantic_name:
        label_lower = label.lower()
        if "follower" in label_lower or "audience" in label_lower:
            semantic_name = "audience_growth"
        elif "reach" in label_lower:
            semantic_name = "reach_overview"
        elif "impression" in label_lower:
            semantic_name = "impressions_trend"
        elif "engagement" in label_lower:
            semantic_name = "engagement_overview"
        elif block.get("type") == "chart":
            semantic_name = "chart_data"
        else:
            semantic_name = "summary"
    metric = "reach"
    if "impression" in semantic_name or "impression" in label.lower():
        metric = "impressions"
    elif "engagement" in semantic_name or "engagement" in label.lower():
        metric = "engagement"
    elif "audience" in semantic_name or "follower" in semantic_name or "follower" in label.lower():
        metric = "followers"
    elif "post" in semantic_name or "content" in semantic_name:
        metric = "engagement"
    chart = data.get("chart") if isinstance(data.get("chart"), dict) else _meta_chart_payload(context, metric, label)
    if not chart.get("points"):
        chart = _meta_chart_payload(context, metric, label)
    points = list(chart.get("points") or [])
    total = data.get("total", data.get("value"))
    if total is None:
        total = _meta_metric_total(context, metric, points)
    change_payload = _meta_change_payload(
        context,
        metric=metric,
        current_value=total,
        current_points=points,
    )
    if semantic_name == "overview":
        metrics = data.get("metrics") if isinstance(data.get("metrics"), list) else []
        insight = _meta_overview_insight(context, metrics)
    elif semantic_name == "reach_overview":
        insight = _meta_reach_insight(context)
    else:
        insight = data.get("insight") or data.get("text") or (
            f"Daily {label.lower()} data is not available for this period."
            if not points
            else f"Daily {label.lower()} is available for this period."
        )
    data.update(
        {
            "title": data.get("title") or label,
            "semantic_name": semantic_name,
            "label": data.get("label") or label,
            "current_value": change_payload.get("current_value"),
            "value": total,
            "total": total,
            "previous_value": change_payload.get("previous_value"),
            "change_absolute": change_payload.get("change_absolute"),
            "change_percentage": change_payload.get("change_percentage"),
            "trend": change_payload.get("trend"),
            "insight": insight,
            "summary": insight if semantic_name in {"overview", "reach_overview"} else data.get("summary"),
            "content": insight if semantic_name in {"overview", "reach_overview"} else data.get("content"),
            "text": insight,
            "chart": chart,
            "points": points,
        }
    )
    updated = dict(block)
    if semantic_name in {"overview", "reach_overview"}:
        updated["content"] = insight
    updated["data_json"] = json.dumps(data)
    return updated


def _meta_enrich_data_blocks(context: dict, blocks: list[dict]) -> list[dict]:
    return [_meta_enrich_existing_block(context, block) for block in blocks]


def _meta_top_post_text(context: dict, period_label: str) -> str:
    posts = _meta_recent_posts(context)
    if not posts:
        return "No top post data available for this period."
    top_post = max(posts, key=_meta_post_score)
    title = (
        top_post.get("title")
        or top_post.get("message")
        or top_post.get("caption")
        or top_post.get("text")
        or "Top post"
    )
    score = _meta_post_score(top_post)
    date = top_post.get("created_time") or top_post.get("date") or top_post.get("published_at")
    reason = (
        f" It stood out with a combined interaction signal of {_meta_format_number(score)}."
        if score
        else " It is the clearest available post-level highlight in the synced dataset."
    )
    date_text = f" Published on {date}." if date else ""
    return f"{_meta_text_excerpt(title, fallback='Top post')}{date_text}{reason} Period: {period_label}."


def _meta_executive_summary_text(context: dict, period_label: str) -> str:
    page_name = context["page_name"]
    reach = context.get("reach")
    engagement = context.get("engagement")
    followers = context.get("followers")
    if _meta_integration_type(context) == "instagram_business":
        reach_reason = _meta_metric_unavailable_reason(context, "reach")
        engagement_reason = _meta_metric_unavailable_reason(context, "engagement")
        parts = [
            f"During {period_label}, {page_name} closed the period with {_meta_format_number(followers)} followers."
        ]
        profile_views = _meta_number(context.get("profile_visits") or context.get("views"))
        website_clicks = _meta_number(context.get("link_clicks"))
        if reach is not None:
            parts.append(f"Reach totaled {_meta_format_number(reach)}.")
        elif reach_reason:
            parts.append(f"Reach was unavailable because Meta reported: {reach_reason}.")
        else:
            parts.append("Reach was unavailable in the synced Instagram dataset.")
        if engagement is not None:
            parts.append(f"Engagement totaled {_meta_format_number(engagement)}.")
        elif engagement_reason:
            parts.append(f"Engagement was unavailable because Meta reported: {engagement_reason}.")
        else:
            parts.append("Engagement was unavailable in the synced Instagram dataset.")
        if profile_views is not None:
            parts.append(f"Available profile/views metric: {_meta_format_number(profile_views)}.")
        if website_clicks is not None:
            parts.append(f"Available website clicks: {_meta_format_number(website_clicks)}.")
        parts.append("The report does not estimate missing Instagram metrics.")
        return " ".join(parts)
    return (
        f"During {period_label}, {page_name} reached {_meta_format_number(reach)} people, "
        f"generated {_meta_format_number(engagement)} engagements, and closed the period with "
        f"{_meta_format_number(followers)} followers. The report focuses on performance quality, "
        "daily movement, and the clearest opportunities for the next content cycle."
    )


def _meta_executive_insight_cards(context: dict, period_label: str) -> list[dict]:
    reach_points = _meta_metric_series(context, "reach")
    impressions_points = _meta_metric_series(context, "impressions")
    engagement_points = _meta_metric_series(context, "engagement")
    followers_points = _meta_metric_series(context, "followers")
    reach_stats = _meta_series_stats(reach_points)
    impressions_stats = _meta_series_stats(impressions_points)
    engagement_stats = _meta_series_stats(engagement_points)
    top_post_available = bool(_meta_recent_posts(context))
    reach_peak = (reach_stats.get("highest") or {}).get("date")
    impressions_peak = (impressions_stats.get("highest") or {}).get("date")
    engagement_peak = (engagement_stats.get("highest") or {}).get("date")
    return [
        {
            "title": "Reach",
            "value": _meta_format_number(context.get("reach")),
            "insight": (
                f"Peak reach was on {reach_peak} during {period_label}."
                if reach_peak
                else f"Daily reach data is not available for {period_label}."
            ),
        },
        {
            "title": "Engagement",
            "value": _meta_format_number(context.get("engagement")),
            "insight": (
                f"Engagement peaked on {engagement_peak} based on available daily engagement data."
                if engagement_peak
                else "Engagement total is available, but daily engagement series is not available."
            ),
        },
        {
            "title": "Impressions",
            "value": _meta_format_number(context.get("impressions")),
            "insight": (
                f"Impressions peaked on {impressions_peak} during {period_label}."
                if impressions_peak
                else f"Daily impressions data is not available for {period_label}."
            ),
        },
        {
            "title": "Audience",
            "value": _meta_format_number(context.get("followers")),
            "insight": (
                f"Audience trend includes {len(followers_points)} daily points."
                if followers_points
                else "Audience is shown as a current follower snapshot because daily follower history is not available."
            ),
        },
        {
            "title": "Content",
            "value": _meta_format_number(len(_meta_recent_posts(context))),
            "insight": (
                "Post-level data is available; use the top performing post to guide creative iteration."
                if top_post_available
                else "No top post data is available for this period."
            ),
        },
    ]


def _meta_executive_ai_analysis(context: dict, period_label: str) -> str:
    page_name = context["page_name"]
    reach_stats = _meta_series_stats(_meta_metric_series(context, "reach"))
    impressions_stats = _meta_series_stats(_meta_metric_series(context, "impressions"))
    reach_direction = _meta_direction(reach_stats.get("delta"))
    impressions_direction = _meta_direction(impressions_stats.get("delta"))
    top_post_sentence = _meta_top_post_text(context, period_label)
    return (
        f"Análisis ejecutivo: durante {period_label}, {page_name} generó "
        f"{_meta_format_number(context.get('reach'))} de alcance, "
        f"{_meta_format_number(context.get('engagement'))} interacciones y "
        f"{_meta_format_number(context.get('impressions'))} impresiones, con una audiencia de "
        f"{_meta_format_number(context.get('followers'))} seguidores. El alcance cerró con una tendencia "
        f"{reach_direction}, mientras que las impresiones mostraron comportamiento {impressions_direction}. "
        f"Publicaciones destacadas: {top_post_sentence} Recomendación: priorizar los formatos y temas que "
        "coinciden con los días de mayor alcance, reforzar llamados a la interacción y comparar el próximo "
        "periodo contra esta línea base diaria."
    )


def _meta_executive_summary_payload(context: dict, period_label: str) -> dict:
    return {
        "semantic_name": "executive_summary",
        "title": "Insights Summary",
        "text": _meta_executive_summary_text(context, period_label),
        "insight_cards": _meta_executive_insight_cards(context, period_label),
        "ai_analysis": _meta_executive_ai_analysis(context, period_label),
    }


def _meta_key_metrics_overview_value(context: dict) -> str:
    if _meta_number(context.get("reach")) is not None:
        return _meta_format_number(context.get("reach"))
    if _meta_integration_type(context) == "instagram_business" and _meta_number(context.get("followers")) is not None:
        return _meta_format_number(context.get("followers"))
    return "N/A"


def _meta_key_metrics_secondary_text(context: dict) -> str:
    reach = context.get("reach")
    engagement = context.get("engagement")
    followers = context.get("followers")
    if _meta_integration_type(context) != "instagram_business":
        return (
            f"Reach {_meta_format_number(reach)} · "
            f"Engagement {_meta_format_number(engagement)} · "
            f"Followers {_meta_format_number(followers)}"
        )
    reach_text = (
        _meta_format_number(reach)
        if _meta_number(reach) is not None
        else "Unavailable"
    )
    engagement_text = (
        _meta_format_number(engagement)
        if _meta_number(engagement) is not None
        else "Unavailable"
    )
    return (
        f"Reach {reach_text} · "
        f"Engagement {engagement_text} · "
        f"Followers {_meta_format_number(followers)}"
    )


def _meta_engagement_text(context: dict, period_label: str, reach_stats: dict) -> str:
    engagement = context.get("engagement")
    reach = context.get("reach")
    integration_type = _meta_integration_type(context)
    unavailable_reason = _meta_metric_unavailable_reason(context, "engagement")
    reach_numeric = _meta_number(reach)
    engagement_numeric = _meta_number(engagement)
    if engagement_numeric is None:
        if unavailable_reason:
            return (
                f"Engagement is unavailable for {period_label}. Meta reported: {unavailable_reason}."
            )
        if integration_type == "instagram_business":
            return (
                f"Instagram engagement is unavailable for {period_label}. The sync completed, but Meta did not return "
                "accounts engaged or total interactions for this period."
            )
        return f"Engagement is unavailable for {period_label}."
    rate = (
        f"{(engagement_numeric / reach_numeric) * 100:.2f}%"
        if reach_numeric and engagement_numeric is not None
        else "N/A"
    )
    direction = _meta_direction(reach_stats.get("delta"))
    return (
        f"Engagement reached {_meta_format_number(engagement)} during {period_label}. "
        f"Against total reach, the estimated engagement-to-reach rate is {rate}. "
        f"Reach movement was {direction}, so engagement should be read together with daily reach context."
    )


def _meta_engagement_slide_payload(context: dict, period_label: str, reach_stats: dict) -> dict:
    report_inputs = _meta_report_inputs(context)
    engagement_points = _meta_first_series(
        report_inputs.get("daily_engagement"),
        report_inputs.get("engagement_daily"),
        report_inputs.get("content_interactions_daily"),
        report_inputs.get("interactions_daily"),
    )
    engagement_total = (
        _meta_number(report_inputs.get("engagement"))
        or _meta_number(report_inputs.get("total_interactions"))
        or _meta_number(report_inputs.get("accounts_engaged"))
        or _meta_number(report_inputs.get("content_interactions"))
        or _meta_metric_total(context, "engagement", engagement_points)
    )
    engagement_source = (
        str(report_inputs.get("engagement_source_metric") or "").strip()
        or (
            "total_interactions"
            if _meta_number(report_inputs.get("total_interactions")) is not None
            or _meta_number(context.get("total_interactions")) is not None
            else "accounts_engaged"
            if _meta_number(report_inputs.get("accounts_engaged")) is not None
            or _meta_number(context.get("accounts_engaged")) is not None
            else "content_interactions"
            if _meta_number(report_inputs.get("content_interactions")) is not None
            or _meta_number(context.get("content_interactions")) is not None
            else "unavailable"
            if _meta_integration_type(context) == "instagram_business"
            else "fallback.posts_daily_series"
        )
    )
    normalized_points = _meta_series_points(engagement_points)
    chart = _meta_chart_payload(context, "engagement", "Engagement")
    chart["title"] = "Engagement Trend"
    chart["metric"] = "engagement"
    chart["points"] = normalized_points
    chart["data"] = normalized_points
    chart["series"] = normalized_points
    engagement_unavailable_reason = _meta_metric_unavailable_reason(context, "engagement")
    engagement_display_value = engagement_total if engagement_total is not None else "N/A"
    logger.info(
        "[BACKEND_ENGAGEMENT_SLIDE_AUDIT]",
        extra={
            "selected_source_used": engagement_source,
            "raw_points_count": len(engagement_points) if isinstance(engagement_points, list) else 0,
            "normalized_points_count": len(normalized_points),
            "first_point": normalized_points[0] if normalized_points else None,
            "last_point": normalized_points[-1] if normalized_points else None,
        },
    )
    logger.info(
        "instagram_engagement_block_audit",
        extra={
            "engagement_final_value": engagement_total,
            "engagement_source_metric": engagement_source,
            "fallback_used": engagement_source != "total_interactions",
            "engagement_unavailable_reason": engagement_unavailable_reason,
            "chart_points_count": len(normalized_points),
        },
    )
    return {
        "semantic_name": "engagement_overview",
        "title": "Engagement Overview",
        "label": "Engagement",
        "value": engagement_display_value,
        "total": engagement_display_value,
        "summary": engagement_display_value,
        "content": {
            "value": engagement_display_value,
            "label": "Engagement",
            "source": engagement_source,
            "unavailable_reason": engagement_unavailable_reason,
        },
        "text": _meta_engagement_text(context, period_label, reach_stats),
        "insight": _meta_engagement_text(context, period_label, reach_stats),
        "chart": chart,
        "points": normalized_points,
        "source_metric": engagement_source,
    }


def _meta_audience_growth_text(context: dict, period_label: str) -> str:
    followers = context.get("followers")
    report_inputs = context.get("report_inputs") if isinstance(context.get("report_inputs"), dict) else {}
    growth = (
        report_inputs.get("followers_growth")
        or report_inputs.get("fan_count_growth")
        or report_inputs.get("audience_growth")
    )
    if _meta_number(growth) is not None:
        return (
            f"Audience size is {_meta_format_number(followers)} followers with "
            f"{_meta_format_number(growth)} net growth during {period_label}."
        )
    return (
        f"Audience snapshot: {_meta_format_number(followers)} followers. Historical growth was not available "
        f"for {period_label}, so this slide should be read as current audience size rather than net growth."
    )


def _meta_insights_text(context: dict, period_label: str, reach_stats: dict, impressions_stats: dict) -> str:
    top_post_available = bool(_meta_recent_posts(context))
    return "\n".join(
        [
            f"- Reach pattern: {_meta_trend_copy('Reach', reach_stats, period_label)}",
            f"- Engagement: {_meta_format_number(context.get('engagement'))} total engagements in the period.",
            "- Content: top post data is available for review." if top_post_available else "- Content: no top post data was available for this period.",
            f"- Audience: {_meta_audience_growth_text(context, period_label)}",
            f"- Impressions: {_meta_trend_copy('Impressions', impressions_stats, period_label)}",
        ]
    )


def _meta_recommendations_text(context: dict, period_label: str, reach_stats: dict) -> str:
    peak_date = (reach_stats.get("highest") or {}).get("date") or "the strongest reach day"
    return "\n".join(
        [
            f"1. Reuse creative patterns from {peak_date}; that day represents the clearest reach signal in {period_label}.",
            "2. Pair high-reach content with explicit engagement prompts to convert visibility into interactions.",
            "3. Keep a consistent posting cadence and compare the next period against this report's daily trend.",
        ]
    )


def _meta_overview_metric(context: dict, metric_key: str, label: str) -> dict:
    metric_map = {
        "reach": "reach",
        "impressions": "impressions",
        "followers": "followers",
        "engagement": "engagement",
    }
    resolved_metric = metric_map.get(metric_key, metric_key)
    current_value = _meta_metric_total(
        context,
        resolved_metric,
        _meta_metric_series(context, resolved_metric),
    )
    comparison = _meta_metric_comparison(
        context,
        metric=resolved_metric,
        current_value=current_value,
        current_points=_meta_metric_series(context, resolved_metric),
    )
    return {
        "key": metric_key,
        "label": label,
        "value": current_value,
        "total": current_value,
        "current_value": comparison.get("current_value"),
        "previous_value": comparison.get("previous_value"),
        "change_absolute": comparison.get("change_absolute"),
        "change_percentage": comparison.get("change_percentage"),
        "trend": comparison.get("trend"),
    }


def _meta_overview_selected_template(context: dict) -> int:
    page_name = str(context.get("page_name") or "")
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    timeframe_key = str(report_timeframe.get("key") or report_timeframe.get("label") or "")
    seed = f"{page_name}|{timeframe_key}"
    return sum(ord(char) for char in seed) % 5


def _meta_reach_selected_template(context: dict) -> int:
    page_name = str(context.get("page_name") or "")
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    timeframe_key = str(report_timeframe.get("key") or report_timeframe.get("label") or "")
    seed = f"reach|{page_name}|{timeframe_key}"
    return sum(ord(char) for char in seed) % 5


def _meta_reach_insight(context: dict) -> str:
    points = _meta_metric_series(context, "reach")
    stats = _meta_series_stats(points)
    normalized_points = _meta_series_points(points)
    if len(normalized_points) < 2:
        insight = (
            "Daily reach data is limited for this period, so trend interpretation should be reviewed with caution."
        )
        logger.info(
            "[BACKEND_REACH_INSIGHT_AUDIT]",
            extra={
                "points_count": len(normalized_points),
                "max_date": None,
                "max_value": None,
                "min_date": None,
                "min_value": None,
                "average_value": None,
                "selected_template": _meta_reach_selected_template(context),
                "final_insight": insight,
            },
        )
        return insight

    highest = stats.get("highest") or {}
    lowest = stats.get("lowest") or {}
    average_value = _meta_number(stats.get("average"))
    first_value = _meta_number((stats.get("first") or {}).get("value"))
    last_value = _meta_number((stats.get("last") or {}).get("value"))
    highest_value = _meta_number(highest.get("value"))
    lowest_value = _meta_number(lowest.get("value"))
    strongest_vs_average_pct = None
    if highest_value is not None and average_value is not None and average_value > 0:
        strongest_vs_average_pct = ((highest_value - average_value) / average_value) * 100

    range_ratio = None
    if highest_value is not None and lowest_value is not None and average_value is not None and average_value > 0:
        range_ratio = (highest_value - lowest_value) / average_value

    if first_value is not None and last_value is not None and average_value is not None and average_value > 0:
        delta_ratio = (last_value - first_value) / average_value
    else:
        delta_ratio = None

    if range_ratio is not None and range_ratio >= 0.6:
        pattern = "volatile"
    elif delta_ratio is not None and delta_ratio >= 0.2:
        pattern = "rising"
    elif delta_ratio is not None and delta_ratio <= -0.2:
        pattern = "declining"
    else:
        pattern = "stable"

    max_date = highest.get("label") or _meta_point_label(highest.get("date")) or highest.get("date") or "N/A"
    min_date = lowest.get("label") or _meta_point_label(lowest.get("date")) or lowest.get("date") or "N/A"
    average_text = _meta_format_number(average_value)
    max_value_text = _meta_format_number(highest_value)
    min_value_text = _meta_format_number(lowest_value)
    strongest_vs_average_text = (
        f", about {abs(strongest_vs_average_pct):.0f}% above the daily average"
        if strongest_vs_average_pct is not None and strongest_vs_average_pct >= 0
        else ""
    )
    pattern_text = {
        "volatile": "Reach moved unevenly across the period, with noticeable day-to-day swings.",
        "rising": "Reach finished stronger than it started, pointing to improving visibility through the period.",
        "declining": "Reach softened toward the end of the period, suggesting visibility lost momentum after earlier peaks.",
        "stable": "Reach stayed relatively stable across the period, without major day-to-day disruption.",
    }[pattern]

    insight = {
        0: (
            f"The strongest reach day was {max_date} with {max_value_text} people reached{strongest_vs_average_text}. "
            f"Average daily reach was {average_text}, and the weakest point came on {min_date} at {min_value_text}. "
            f"{pattern_text}"
        ),
        1: (
            f"Reach averaged {average_text} per day during the selected period. The highest point landed on {max_date} "
            f"at {max_value_text}, while the lowest point was {min_date} at {min_value_text}. {pattern_text}"
        ),
        2: (
            f"{max_date} was the strongest visibility day with {max_value_text} reached, compared with a daily average of "
            f"{average_text}. The lowest day was {min_date} at {min_value_text}, which makes the overall pattern look {pattern}."
        ),
        3: (
            f"Daily reach peaked on {max_date} at {max_value_text} and bottomed on {min_date} at {min_value_text}. "
            f"With an average of {average_text} per day, the period reads as {pattern} rather than uniformly distributed."
        ),
        4: (
            f"The reach curve was led by a high of {max_value_text} on {max_date}, versus a low of {min_value_text} on {min_date}. "
            f"Daily reach averaged {average_text}, and the pattern appears {pattern} across the selected timeframe."
        ),
    }[_meta_reach_selected_template(context)]

    logger.info(
        "[BACKEND_REACH_INSIGHT_AUDIT]",
        extra={
            "points_count": len(normalized_points),
            "max_date": highest.get("date"),
            "max_value": highest_value,
            "min_date": lowest.get("date"),
            "min_value": lowest_value,
            "average_value": round(average_value, 2) if average_value is not None else None,
            "selected_template": _meta_reach_selected_template(context),
            "final_insight": insight,
        },
    )
    return insight


def _meta_overview_insight(context: dict, metrics: list[dict]) -> str:
    reach_metric = next((metric for metric in metrics if metric.get("key") == "reach"), {})
    followers_metric = next((metric for metric in metrics if metric.get("key") == "followers"), {})
    engagement_metric = next((metric for metric in metrics if metric.get("key") == "engagement"), {})

    reach = _meta_number(reach_metric.get("current_value", reach_metric.get("value")))
    followers = _meta_number(followers_metric.get("current_value", followers_metric.get("value")))
    engagement = _meta_number(engagement_metric.get("current_value", engagement_metric.get("value")))
    integration_type = _meta_integration_type(context)
    unavailable_reason_reach = _meta_metric_unavailable_reason(context, "reach")
    unavailable_reason_engagement = _meta_metric_unavailable_reason(context, "engagement")

    reach_to_followers_ratio = (reach / followers) if reach and followers and followers > 0 else None
    engagement_rate = (engagement / reach) * 100 if engagement is not None and reach and reach > 0 else None

    if integration_type == "instagram_business" and (reach is None or engagement is None):
        available_parts = []
        if followers is not None:
            available_parts.append(
                f"The account currently has {_meta_format_number(followers)} followers."
            )
        if reach is None:
            available_parts.append(
                "Reach was unavailable from Meta."
                + (f" Reason: {unavailable_reason_reach}." if unavailable_reason_reach else "")
            )
        if engagement is None:
            available_parts.append(
                "Engagement was unavailable from Meta."
                + (f" Reason: {unavailable_reason_engagement}." if unavailable_reason_engagement else "")
            )
        available_parts.append(
            "This overview reflects the latest Instagram Business dataset and does not estimate missing metrics."
        )
        return " ".join(available_parts)

    if engagement_rate is None:
        engagement_quality = "limited"
    elif engagement_rate >= 4:
        engagement_quality = "strong"
    elif engagement_rate >= 2:
        engagement_quality = "healthy"
    else:
        engagement_quality = "moderate"

    momentum_parts = []
    for metric in (reach_metric, engagement_metric, followers_metric):
        metric_label = str(metric.get("label") or metric.get("key") or "")
        delta = metric.get("change_percentage")
        if not isinstance(delta, (int, float)):
            continue
        if delta > 0:
            momentum_parts.append(f"{metric_label.lower()} improved")
        elif delta < 0:
            momentum_parts.append(f"{metric_label.lower()} declined")
    momentum_text = None
    if momentum_parts:
        momentum_text = ", ".join(momentum_parts[:2]).capitalize() + " versus the previous period."

    visibility_text = (
        f"Reach was {reach_to_followers_ratio:.1f}x the follower base, which suggests visibility extended beyond the existing audience."
        if reach_to_followers_ratio is not None
        else "Reach and follower data indicate the page generated measurable visibility during the selected period."
        if reach is not None or followers is not None
        else "Visibility data was only partially available for this overview."
    )
    engagement_text = (
        f"Engagement represented {engagement_rate:.1f}% of reach, indicating {engagement_quality} audience response."
        if engagement_rate is not None
        else "Engagement volume was available, but the relationship between interaction and reach could not be fully quantified."
        if engagement is not None
        else "Engagement detail was limited in the synced dataset."
    )
    closing_text = {
        0: "Overall, this period shows solid awareness with room to deepen audience response.",
        1: "Overall, visibility is translating into interaction, although the page can still push for a stronger response from reached users.",
        2: "Overall, the page is generating discoverability and should focus on converting that visibility into more consistent interaction.",
        3: "Overall, audience attention is present; the next step is improving how efficiently reach turns into engagement.",
        4: "Overall, the page is creating awareness and now needs to reinforce the content cues that drive deeper interaction.",
    }[_meta_overview_selected_template(context)]

    parts = [visibility_text, engagement_text]
    if momentum_text:
        parts.append(momentum_text)
    parts.append(closing_text)

    logger.info(
        "[BACKEND_OVERVIEW_INSIGHT_AUDIT]",
        extra={
            "reach": reach,
            "followers": followers,
            "engagement": engagement,
            "reach_to_followers_ratio": round(reach_to_followers_ratio, 2) if reach_to_followers_ratio is not None else None,
            "engagement_rate": round(engagement_rate, 2) if engagement_rate is not None else None,
            "selected_template": _meta_overview_selected_template(context),
        },
    )
    return " ".join(parts)


def _meta_overview_payload(context: dict, period_label: str) -> dict:
    metrics = [
        _meta_overview_metric(context, "reach", "Reach"),
        _meta_overview_metric(context, "impressions", "Impressions"),
        _meta_overview_metric(context, "followers", "Followers"),
        _meta_overview_metric(context, "engagement", "Engagement"),
    ]
    insight_text = _meta_overview_insight(context, metrics)
    return {
        "semantic_name": "overview",
        "title": "Overview",
        "timeframe": context.get("report_timeframe") or {},
        "metrics": metrics,
        "insight": insight_text,
        "summary": insight_text,
        "content": insight_text,
        "text": insight_text,
    }


def _meta_block_title(block: dict) -> str:
    data = block.get("data_json")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    return str(data.get("title") or data.get("label") or data.get("text") or block.get("type") or "")[:80]


def _build_meta_report_block_pool(context: dict) -> list[dict]:
    title = context["title"]
    report_timeframe = context["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    page_name = context["page_name"]
    followers = context.get("followers")
    reach = context.get("reach")
    engagement = context.get("engagement")
    summary = context["summary"]
    reach_chart_data = context["reach_chart_data"]
    reach_insight = context["reach_insight"]
    recent_posts_summary = context["recent_posts_summary"]
    ai_summary = context["ai_summary"]
    general_insights_slide_payload = context.get("general_insights_slide_payload") or {}
    integration_type = _meta_integration_type(context)
    impressions_slide_payload = context.get("impressions_slide_payload") or {}
    reach_points = reach_chart_data.get("points") if isinstance(reach_chart_data, dict) else []
    impressions_points = impressions_slide_payload.get("impressions_daily")
    reach_stats = _meta_series_stats(reach_points)
    impressions_stats = _meta_series_stats(impressions_points)
    reach_unavailable_reason = _meta_metric_unavailable_reason(context, "reach")
    impressions_chart = {
        "label": f"Impressions Trend - {period_label}",
        "metric": "impressions",
        "points": impressions_points if isinstance(impressions_points, list) else [],
        "is_available": bool(impressions_points),
        "timeframe": report_timeframe,
    }
    return [
        _meta_report_block(
            "title",
            1,
            {
                "text": title,
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
            },
            ["text"],
        ),
        _meta_report_block("text", 2, _meta_overview_payload(context, period_label), ["text"]),
        _meta_report_block("stat", 3, {"label": "Reach", "value": reach if reach is not None else "N/A"}),
        _meta_report_block("text", 4, _meta_engagement_slide_payload(context, period_label, reach_stats), ["text"]),
        _meta_report_block("chart", 5, reach_chart_data),
        _meta_report_block(
            "text",
            6,
            {
                "text": reach_insight,
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
                "unavailable_reason": reach_unavailable_reason,
                "integration_type": integration_type,
            },
            ["text"],
        ),
        _meta_report_block("stat", 7, {"label": "Engagement", "value": engagement if engagement is not None else "N/A"}),
        _meta_report_block("chart", 8, impressions_chart),
        _meta_report_block(
            "text",
            9,
            {
                "text": impressions_slide_payload.get("insight_text")
                or _meta_trend_copy("Impressions", impressions_stats, period_label),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
            },
            ["text"],
        ),
        _meta_report_block("text", 10, {"text": recent_posts_summary}, ["text"]),
        _meta_report_block(
            "text",
            11,
            {
                "text": _meta_trend_copy("Reach", reach_stats, period_label),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
            },
            ["text"],
        ),
        _meta_report_block(
            "stat",
            12,
            {"label": "Average Daily Reach", "value": _meta_format_number(reach_stats.get("average"))},
        ),
        _meta_report_block(
            "stat",
            13,
            {
                "label": "Highest Reach Day",
                "value": _meta_format_number((reach_stats.get("highest") or {}).get("value")),
                "date": (reach_stats.get("highest") or {}).get("date"),
            },
        ),
        _meta_report_block(
            "stat",
            14,
            {
                "label": "Lowest Reach Day",
                "value": _meta_format_number((reach_stats.get("lowest") or {}).get("value")),
                "date": (reach_stats.get("lowest") or {}).get("date"),
            },
        ),
        _meta_report_block(
            "text",
            15,
            {"text": ai_summary, "timeframe": report_timeframe, "timeframe_label": period_label},
            ["text"],
        ),
        _meta_report_block(
            "chart",
            16,
            _meta_clone_chart(reach_chart_data, label=f"Reach Daily Distribution - {period_label}"),
        ),
        _meta_report_block(
            "text",
            17,
            {
                "text": (
                    f"{page_name} should use the strongest reach days as creative references and "
                    f"repeat the posting patterns that occurred around {(reach_stats.get('highest') or {}).get('date') or 'the peak day'}."
                )
            },
            ["text"],
        ),
        _meta_report_block(
            "stat",
            18,
            {"label": "Reach Start", "value": _meta_format_number((reach_stats.get("first") or {}).get("value"))},
        ),
        _meta_report_block(
            "stat",
            19,
            {"label": "Reach End", "value": _meta_format_number((reach_stats.get("last") or {}).get("value"))},
        ),
        _meta_report_block(
            "text",
            20,
            {
                "text": (
                    f"Reach changed by {_meta_format_number(reach_stats.get('delta'))} from the first "
                    f"to the last available day in {period_label}."
                )
            },
            ["text"],
        ),
        _meta_report_block(
            "stat",
            21,
            {"label": "Average Daily Impressions", "value": _meta_format_number(impressions_stats.get("average"))},
        ),
        _meta_report_block(
            "stat",
            22,
            {
                "label": "Highest Impressions Day",
                "value": _meta_format_number((impressions_stats.get("highest") or {}).get("value")),
                "date": (impressions_stats.get("highest") or {}).get("date"),
            },
        ),
        _meta_report_block(
            "stat",
            23,
            {
                "label": "Lowest Impressions Day",
                "value": _meta_format_number((impressions_stats.get("lowest") or {}).get("value")),
                "date": (impressions_stats.get("lowest") or {}).get("date"),
            },
        ),
        _meta_report_block(
            "text",
            24,
            {"text": _meta_trend_copy("Impressions", impressions_stats, period_label)},
            ["text"],
        ),
        _meta_report_block(
            "text",
            25,
            {
                "text": (
                    f"Engagement reached {_meta_format_number(engagement)} during {period_label}. "
                    "Use this as the primary response-quality signal alongside reach."
                )
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            26,
            {
                "text": (
                    f"Audience base: {_meta_format_number(followers)} followers. Compare follower base "
                    f"against reach to understand how far content expanded beyond the existing audience."
                )
            },
            ["text"],
        ),
        _meta_report_block(
            "chart",
            27,
            _meta_clone_chart(reach_chart_data, label=f"Reach Momentum - {period_label}"),
        ),
        _meta_report_block(
            "text",
            28,
            {"text": general_insights_slide_payload.get("summary") or "General insights are based on the synced Meta metrics."},
            ["text"],
        ),
        _meta_report_block(
            "text",
            29,
            {
                "text": (
                    "Recommended actions: repeat high-performing creative patterns, protect posting cadence, "
                    "and monitor whether reach gains translate into engagement."
                )
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            30,
            {
                "text": (
                    f"Executive takeaway for {period_label}: {page_name} generated "
                    f"{_meta_format_number(reach)} reach and {_meta_format_number(engagement)} engagements. "
                    "Prioritize the strongest days and keep measuring daily movement."
                )
            },
            ["text"],
        ),
    ]


def _renumber_blocks(blocks: list[dict]) -> list[dict]:
    renumbered = []
    for index, block in enumerate(blocks, start=1):
        updated = dict(block)
        updated["order"] = index
        renumbered.append(updated)
    return renumbered


def build_5_blocks(dataset: dict) -> list[dict]:
    block_pool = _build_meta_report_block_pool(dataset)
    selected_blocks = list(block_pool[:5])
    if len(selected_blocks) >= 5:
        selected_blocks[4] = dict(selected_blocks[0])
    return _meta_enrich_data_blocks(
        dataset,
        _renumber_blocks(selected_blocks),
    )


def build_10_blocks(dataset: dict) -> list[dict]:
    report_timeframe = dataset["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    title = dataset["title"]
    reach_chart_data = dataset.get("reach_chart_data") if isinstance(dataset.get("reach_chart_data"), dict) else {}
    impressions_slide_payload = (
        dataset.get("impressions_slide_payload")
        if isinstance(dataset.get("impressions_slide_payload"), dict)
        else {}
    )
    reach_stats = _meta_series_stats(reach_chart_data.get("points"))
    impressions_points = impressions_slide_payload.get("impressions_daily")
    impressions_stats = _meta_series_stats(impressions_points)
    impressions_chart = {
        "label": f"Impressions Trend — {period_label}",
        "metric": "impressions",
        "points": impressions_points if isinstance(impressions_points, list) else [],
        "is_available": bool(impressions_points),
        "timeframe": report_timeframe,
    }
    blocks = [
        _meta_report_block(
            "title",
            1,
            {
                "text": title,
                "subtitle": f"{dataset['page_name']} performance report · {period_label}",
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
                "branding": dataset.get("branding") or {},
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _meta_report_block(
            "text",
            2,
            _meta_executive_summary_payload(dataset, period_label),
            ["text"],
        ),
        _meta_report_block(
            "stat",
            3,
            {
                "label": "Key Metrics Overview",
                "value": _meta_key_metrics_overview_value(dataset),
                "secondary_text": _meta_key_metrics_secondary_text(dataset),
                "metrics": {
                    "reach": dataset.get("reach"),
                    "engagement": dataset.get("engagement"),
                    "impressions": dataset.get("impressions"),
                    "followers": dataset.get("followers"),
                },
                "semantic_name": "key_metrics_overview",
            },
        ),
        _meta_report_block(
            "chart",
            4,
            {
                **reach_chart_data,
                "label": f"Reach Trend — {period_label}",
                "metric": reach_chart_data.get("metric") or "reach",
                "timeframe": reach_chart_data.get("timeframe") or report_timeframe,
                "semantic_name": "reach_overview",
            },
        ),
        _meta_report_block(
            "text",
            5,
            {
                **_meta_engagement_slide_payload(dataset, period_label, reach_stats),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
            },
            ["text"],
        ),
        _meta_report_block(
            "chart",
            6,
            {**impressions_chart, "semantic_name": "impressions_trend"},
        ),
        _meta_report_block(
            "text",
            7,
            {
                "title": "Top Performing Post",
                "text": _meta_top_post_text(dataset, period_label),
                "semantic_name": "top_performing_post",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            8,
            {
                "title": "Audience Growth",
                "text": _meta_audience_growth_text(dataset, period_label),
                "semantic_name": "audience_growth",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            9,
            {
                "title": "Insights",
                "text": _meta_insights_text(dataset, period_label, reach_stats, impressions_stats),
                "semantic_name": "insights",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            10,
            {
                "title": "Recommendations",
                "text": _meta_recommendations_text(dataset, period_label, reach_stats),
                "semantic_name": "recommendations",
            },
            ["text"],
        ),
    ]
    blocks = _meta_enrich_data_blocks(dataset, _renumber_blocks(blocks))
    logger.info(
        "[ReportBlocks][build.10.start]",
        extra={
            "requested_slides": dataset.get("requested_slides"),
            "blocks_generados": len(blocks),
        },
    )
    for block in blocks:
        data = json.loads(str(block.get("data_json") or "{}"))
        logger.info(
            "[ReportBlocks][build.10.block]",
            extra={
                "order": block.get("order"),
                "type": block.get("type"),
                "semantic_name": data.get("semantic_name"),
            },
        )
    logger.info(
        "[ReportBlocks][build.10.final]",
        extra={
            "total_blocks": len(blocks),
            "block_types": [str(block.get("type")) for block in blocks],
            "block_titles": [_meta_block_title(block) for block in blocks],
        },
    )
    return blocks


def build_15_blocks(dataset: dict) -> list[dict]:
    report_timeframe = dataset["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    title = dataset["title"]
    page_name = dataset["page_name"]
    reach_chart_data = dataset.get("reach_chart_data") if isinstance(dataset.get("reach_chart_data"), dict) else {}
    impressions_slide_payload = (
        dataset.get("impressions_slide_payload")
        if isinstance(dataset.get("impressions_slide_payload"), dict)
        else {}
    )
    reach_stats = _meta_series_stats(reach_chart_data.get("points"))
    impressions_points = impressions_slide_payload.get("impressions_daily")
    impressions_stats = _meta_series_stats(impressions_points)
    reach_numeric = _meta_number(dataset.get("reach"))
    engagement_numeric = _meta_number(dataset.get("engagement"))
    engagement_rate = (
        f"{(engagement_numeric / reach_numeric) * 100:.2f}%"
        if reach_numeric and engagement_numeric is not None
        else "N/A"
    )
    impressions_chart = {
        "label": f"Impressions Trend – {period_label}",
        "metric": "impressions",
        "points": impressions_points if isinstance(impressions_points, list) else [],
        "is_available": bool(impressions_points),
        "timeframe": report_timeframe,
    }
    content_highlights_text = (
        "Content highlights are based on the available post-level data for this period.\n"
        f"- Best available post: {_meta_top_post_text(dataset, period_label)}\n"
        f"- Post coverage: {len(_meta_recent_posts(dataset))} recent posts available for review."
        if _meta_recent_posts(dataset)
        else "No post-level highlights were available for this period. Use reach and engagement trends as the primary content signals."
    )
    closing_text = (
        f"Closing summary: {page_name} delivered {_meta_format_number(dataset.get('reach'))} reach and "
        f"{_meta_format_number(dataset.get('engagement'))} engagements during {period_label}. "
        "Next step: turn the strongest daily and content signals into a focused plan for the next reporting period."
    )
    blocks = [
        _meta_report_block(
            "title",
            1,
            {
                "text": title,
                "subtitle": f"{page_name} performance report · {period_label}",
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
                "branding": dataset.get("branding") or {},
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _meta_report_block(
            "text",
            2,
            _meta_executive_summary_payload(dataset, period_label),
            ["text"],
        ),
        _meta_report_block(
            "stat",
            3,
            {
                "label": "Key Metrics Overview",
                "value": _meta_key_metrics_overview_value(dataset),
                "secondary_text": _meta_key_metrics_secondary_text(dataset),
                "metrics": {
                    "reach": dataset.get("reach"),
                    "engagement": dataset.get("engagement"),
                    "impressions": dataset.get("impressions"),
                    "followers": dataset.get("followers"),
                },
                "semantic_name": "key_metrics_overview",
            },
        ),
        _meta_report_block(
            "chart",
            4,
            {
                **reach_chart_data,
                "label": f"Reach Trend – {period_label}",
                "metric": reach_chart_data.get("metric") or "reach",
                "timeframe": reach_chart_data.get("timeframe") or report_timeframe,
                "semantic_name": "reach_overview",
            },
        ),
        _meta_report_block(
            "text",
            5,
            {
                "title": "Reach Insight",
                "text": build_meta_pages_reach_insight(_meta_report_inputs(dataset), "en"),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
                "semantic_name": "reach_insight",
            },
            ["text"],
        ),
        _meta_report_block(
            "stat",
            6,
            {
                **_meta_engagement_slide_payload(dataset, period_label, reach_stats),
                "label": "Engagement Overview",
                "secondary_text": f"Estimated engagement-to-reach rate: {engagement_rate}",
            },
        ),
        _meta_report_block(
            "text",
            7,
            {
                "title": "Engagement Insight",
                "text": _meta_engagement_text(dataset, period_label, reach_stats),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
                "semantic_name": "engagement_insight",
            },
            ["text"],
        ),
        _meta_report_block(
            "chart",
            8,
            {**impressions_chart, "semantic_name": "impressions_trend"},
        ),
        _meta_report_block(
            "text",
            9,
            {
                "title": "Impressions Insight",
                "text": impressions_slide_payload.get("insight_text")
                or _meta_trend_copy("Impressions", impressions_stats, period_label),
                "timeframe": report_timeframe,
                "timeframe_label": period_label,
                "semantic_name": "impressions_insight",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            10,
            {
                "title": "Audience Growth",
                "text": _meta_audience_growth_text(dataset, period_label),
                "semantic_name": "audience_growth",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            11,
            {
                "title": "Top Performing Post",
                "text": _meta_top_post_text(dataset, period_label),
                "semantic_name": "top_performing_post",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            12,
            {
                "title": "Content Highlights",
                "text": content_highlights_text,
                "semantic_name": "content_highlights",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            13,
            {
                "title": "Insights",
                "text": _meta_insights_text(dataset, period_label, reach_stats, impressions_stats),
                "semantic_name": "insights",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            14,
            {
                "title": "Recommendations",
                "text": _meta_recommendations_text(dataset, period_label, reach_stats),
                "semantic_name": "recommendations",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            15,
            {
                "title": "Closing Summary",
                "text": closing_text,
                "semantic_name": "closing_summary",
            },
            ["text"],
        ),
    ]
    blocks = _meta_enrich_data_blocks(dataset, _renumber_blocks(blocks))
    logger.info(
        "[ReportBlocks][build.15.start]",
        extra={
            "requested_slides": dataset.get("requested_slides"),
            "blocks_generados": len(blocks),
        },
    )
    for block in blocks:
        data = json.loads(str(block.get("data_json") or "{}"))
        logger.info(
            "[ReportBlocks][build.15.block]",
            extra={
                "order": block.get("order"),
                "type": block.get("type"),
                "semantic_name": data.get("semantic_name"),
            },
        )
    logger.info(
        "[ReportBlocks][build.15.final]",
        extra={
            "total_blocks": len(blocks),
            "block_types": [str(block.get("type")) for block in blocks],
            "block_titles": [_meta_block_title(block) for block in blocks],
        },
    )
    return blocks


def build_30_blocks(dataset: dict) -> list[dict]:
    return _meta_enrich_data_blocks(
        dataset,
        _renumber_blocks(_build_meta_report_block_pool(dataset)[:30]),
    )


def build_blocks(requested_slides: int, dataset: dict) -> list[dict]:
    if requested_slides <= 5:
        return build_5_blocks(dataset)
    if requested_slides <= 10:
        return build_10_blocks(dataset)
    if requested_slides <= 15:
        return build_15_blocks(dataset)
    return build_30_blocks(dataset)


@app.post("/datasets/excel", response_model=DatasetUploadOut)
def upload_dataset_excel(
    workspace_id: int | None = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DatasetUploadOut:
    workspace_id = _resolve_workspace_id(db, current_user.id, workspace_id)
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise http_error(404, "workspace_not_found", "Workspace not found.")
    _require_workspace_access(db, current_user.id, workspace_id)
    size_bytes = _validate_upload(file)
    _enforce_workspace_storage_for_upload(db, workspace_id, size_bytes)

    try:
        dataset = Dataset(
            workspace_id=workspace_id,
            name=file.filename,
            description=None,
        )
        db.add(dataset)
        db.commit()
        db.refresh(dataset)
    except IntegrityError:
        db.rollback()
        raise http_error(400, "invalid_workspace", "Workspace does not exist.")

    key = f"workspaces/{workspace_id}/datasets/{dataset.id}/{file.filename}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        file.file.seek(0)
        s3.upload_fileobj(file.file, settings.s3_inputs_bucket, key)
    except Exception:
        db.delete(dataset)
        db.commit()
        raise http_error(502, "s3_upload_failed", "Failed to upload file.")

    dataset_file = DatasetFile(
        dataset_id=dataset.id,
        workspace_id=workspace_id,
        s3_key=key,
        size_bytes=size_bytes,
        content_type=file.content_type,
    )
    db.add(dataset_file)
    db.commit()

    return DatasetUploadOut(dataset_id=dataset.id, status="uploaded")


@app.get("/datasets/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(
    dataset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DatasetDetailOut:
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        raise http_error(404, "dataset_not_found", "Dataset not found.")
    _require_workspace_access(db, current_user.id, dataset.workspace_id)

    dataset_file = _get_latest_dataset_file(db, dataset.id)
    return DatasetDetailOut(
        id=dataset.id,
        workspace_id=dataset.workspace_id,
        name=dataset.name,
        description=dataset.description,
        data=dataset.data,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        file_id=dataset_file.id if dataset_file else None,
        file_key=dataset_file.s3_key if dataset_file else None,
        content_type=dataset_file.content_type if dataset_file else None,
        size_bytes=dataset_file.size_bytes if dataset_file else None,
    )


@app.post("/reports", response_model=ReportOut)
def create_report(
    payload: ReportCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    dataset = db.get(Dataset, payload.dataset_id)
    if not dataset:
        raise http_error(404, "dataset_not_found", "Dataset not found.")
    _require_workspace_access(db, current_user.id, dataset.workspace_id)
    enforce_monthly_report_limit(db, dataset.workspace_id)
    requested_slides = (
        payload.requested_slides
        if payload.requested_slides is not None
        else payload.slide_count
    )
    slide_limits = resolve_report_slide_limits(
        db,
        dataset.workspace_id,
        requested_slides=requested_slides,
        default_slides=DEFAULT_GENERATED_REPORT_SLIDE_COUNT,
    )
    logger.info(
        "[PlanLimits][report.create]",
        extra={
            "plan": slide_limits["plan"],
            "requested_slides": slide_limits["requested_slides"],
            "max_slides": slide_limits["max_slides"],
            "effective_slide_limit": slide_limits["effective_slide_limit"],
        },
    )
    ai_mode = normalize_ai_mode(payload.ai_mode)
    ai_plan_context = build_ai_agent_plan_context(
        plan=slide_limits["plan"],
        effective_slide_limit=slide_limits["effective_slide_limit"],
        dataset_context={"dataset_id": dataset.id},
        report_context={"generation_mode": "standard", "ai_mode": ai_mode},
    )
    logger.info(
        "[AIAgents][plan_context]",
        extra={
            "dataset_id": dataset.id,
            "plan": ai_plan_context["plan"],
            "ai_mode": ai_mode,
            "max_slides": ai_plan_context["max_slides"],
            "allow_ai_agents": ai_plan_context["allow_ai_agents"],
            "effective_slide_limit": ai_plan_context["effective_slide_limit"],
        },
    )
    if ai_mode == "agents" and not ai_plan_context["allow_ai_agents"]:
        raise http_error(
            403,
            "plan_restricted",
            "AI agents are not available for current plan.",
        )
    ai_agent_metadata = build_ai_agent_metadata(
        ai_mode=ai_mode,
        allow_ai_agents=bool(ai_plan_context["allow_ai_agents"]),
    )

    locale = normalize_report_locale(payload.locale)
    report = Report(
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        name=payload.title,
        description=json.dumps(
            {
                "locale": locale,
                "branding": _user_branding(current_user),
                "requested_slides": slide_limits["requested_slides"],
                "effective_slide_limit": slide_limits["effective_slide_limit"],
                "plan_at_generation": slide_limits["plan"],
                "generation_mode": "standard",
                "plan_capabilities": slide_limits["capabilities"],
                **ai_agent_metadata,
            }
        ),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    logger.info(
        "[AIAgents][pipeline.final]",
        extra={
            "report_id": report.id,
            "dataset_id": dataset.id,
            "plan": ai_plan_context["plan"],
            "ai_mode": ai_mode,
            "allow_ai_agents": ai_plan_context["allow_ai_agents"],
            "effective_slide_limit": ai_plan_context["effective_slide_limit"],
            "fallback_used": ai_agent_metadata["ai_agent_fallback_used"],
            "number_of_blocks_final": None,
        },
    )

    enqueue_job(
        db,
        job_type="generate_report",
        payload={
            "dataset_id": dataset.id,
            "report_id": report.id,
            "locale": locale,
            "requested_slides": slide_limits["requested_slides"],
            "effective_slide_limit": slide_limits["effective_slide_limit"],
            "plan_at_generation": slide_limits["plan"],
            "generation_mode": "standard",
            "ai_mode": ai_mode,
            "ai_agent_metadata": ai_agent_metadata,
        },
        workspace_id=dataset.workspace_id,
    )

    return ReportOut(
        id=report.id,
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        title=payload.title,
        description=_report_metadata(report),
        timeframe=_report_timeframe(report),
        locale=locale,
        branding=_report_branding(report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _resolve_instagram_business_report_dataset(
    db: Session,
    current_user: User,
    payload: InstagramBusinessReportCreateIn,
) -> Dataset:
    if payload.dataset_id is not None:
        dataset = db.get(Dataset, int(payload.dataset_id))
        if not dataset:
            raise http_error(404, "dataset_not_found", "Dataset not found.")
        _require_workspace_access(db, current_user.id, dataset.workspace_id)
        dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        if str(dataset_data.get("integration_type") or "").strip() != "instagram_business":
            raise http_error(
                400,
                "invalid_instagram_dataset",
                "Dataset is not an Instagram Business dataset.",
            )
        return dataset

    requested_account_id = str(payload.account_id or payload.page_id or "").strip() or None
    if not requested_account_id:
        raise http_error(
            400,
            "missing_account_id",
            "account_id, page_id, or dataset_id is required.",
        )

    workspace_id: int | None = None
    if payload.integration_id is not None:
        integration = _get_meta_integration(db, current_user, int(payload.integration_id))
        workspace_id = integration.workspace_id
    elif payload.workspace_id is not None:
        workspace_id = int(payload.workspace_id)
        _require_workspace_access(db, current_user.id, workspace_id)

    query = db.query(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc())
    if workspace_id is not None:
        query = query.filter(Dataset.workspace_id == workspace_id)
    else:
        query = (
            query.join(WorkspaceMember, WorkspaceMember.workspace_id == Dataset.workspace_id)
            .filter(WorkspaceMember.user_id == current_user.id)
        )

    expected_filename = f"meta_instagram_{requested_account_id}_insights.csv"
    candidates = query.limit(200).all()
    for dataset in candidates:
        dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        if str(dataset_data.get("integration_type") or "").strip() != "instagram_business":
            continue
        dataset_account_id = str(
            dataset_data.get("account_id") or dataset_data.get("page_id") or ""
        ).strip()
        if dataset_account_id and dataset_account_id == requested_account_id:
            return dataset
        if str(dataset.name or "").strip() == expected_filename:
            return dataset

    raise http_error(
        404,
        "instagram_dataset_not_found",
        "Instagram Business dataset not found for selected account.",
    )


def _create_meta_dataset_report(
    *,
    dataset: Dataset,
    payload: MetaPagesReportCreateIn | InstagramBusinessReportCreateIn,
    current_user: User,
    db: Session,
    report_source: str,
    generation_mode: str,
) -> MetaPagesReportCreateOut:
    locale = normalize_report_locale(payload.locale)
    dataset_file = _get_latest_dataset_file(db, dataset.id)
    if not dataset_file:
        raise http_error(404, "dataset_file_not_found", "Dataset file not found.")
    enforce_monthly_report_limit(db, dataset.workspace_id)
    requested_slides = (
        payload.requested_slides
        if payload.requested_slides is not None
        else payload.slide_count
    )
    slide_limits = resolve_report_slide_limits(
        db,
        dataset.workspace_id,
        requested_slides=requested_slides,
        default_slides=11,
    )
    logger.info(
        "[PlanLimits][report.create]",
        extra={
            "plan": slide_limits["plan"],
            "requested_slides": slide_limits["requested_slides"],
            "max_slides": slide_limits["max_slides"],
            "effective_slide_limit": slide_limits["effective_slide_limit"],
        },
    )
    ai_mode = normalize_ai_mode(payload.ai_mode)

    row = dataset.data or _load_dataset_row(dataset_file)
    dataset_reach_daily = row.get("reach_daily") if isinstance(row.get("reach_daily"), list) else []
    dataset_impressions_daily = (
        row.get("impressions_daily") if isinstance(row.get("impressions_daily"), list) else []
    )
    if str(row.get("integration_type") or "").strip() == "instagram_business":
        logger.warning(
            "instagram_dataset_keys",
            extra={
                "dataset_id": dataset.id,
                "instagram_dataset_keys": sorted(row.keys()),
                "instagram_normalized_metric_keys": sorted(
                    (row.get("normalized_report_metrics") or {}).keys()
                )
                if isinstance(row.get("normalized_report_metrics"), dict)
                else [],
            },
        )
    logger.info(
        "[MetaTimeframeBackend][report.dataset.loaded]",
        extra={
            "dataset_id_loaded": dataset.id,
            "dataset_timeframe": row.get("timeframe") if isinstance(row.get("timeframe"), dict) else None,
            "reach_daily_length": len(dataset_reach_daily),
            "impressions_daily_length": len(dataset_impressions_daily),
        },
    )
    timeframe_source = (
        "dataset.data.timeframe"
        if isinstance(row.get("timeframe"), dict)
        else "legacy_request_fallback"
    )
    report_timeframe = row.get("timeframe") if isinstance(row.get("timeframe"), dict) else None
    if report_timeframe is None:
        report_timeframe = resolve_meta_pages_timeframe(
            payload.timeframe,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    else:
        report_timeframe = {
            "key": str(report_timeframe.get("key") or report_timeframe.get("timeframe") or "") or None,
            "label": str(report_timeframe.get("label") or "") or None,
            "preset": str(report_timeframe.get("preset") or "") or None,
            "since": str(report_timeframe.get("since") or "") or None,
            "until": str(report_timeframe.get("until") or "") or None,
        }
    report_row = dict(row)
    report_row["timeframe"] = report_timeframe
    normalized_metrics = (
        dict(report_row.get("normalized_report_metrics"))
        if isinstance(report_row.get("normalized_report_metrics"), dict)
        else {}
    )
    if report_timeframe.get("since"):
        normalized_metrics["timeframe_since"] = report_timeframe["since"]
        report_row["timeframe_since"] = report_timeframe["since"]
    if report_timeframe.get("until"):
        normalized_metrics["timeframe_until"] = report_timeframe["until"]
        report_row["timeframe_until"] = report_timeframe["until"]
    if report_timeframe.get("preset"):
        report_row["timeframe_preset"] = report_timeframe["preset"]
    if normalized_metrics:
        report_row["normalized_report_metrics"] = normalized_metrics

    report_inputs = extract_meta_pages_report_inputs(report_row)
    if str(report_inputs.get("integration_type") or "").strip() == "instagram_business":
        logger.warning(
            "instagram_normalized_metrics",
            extra={
                "dataset_id": dataset.id,
                "followers": report_inputs.get("followers"),
                "reach": report_inputs.get("reach"),
                "engagement": report_inputs.get("engagement"),
                "profile_visits": report_inputs.get("profile_visits"),
                "link_clicks": report_inputs.get("link_clicks"),
                "content_interactions": report_inputs.get("content_interactions"),
            },
        )
        logger.warning(
            "instagram_unavailable_metrics",
            extra={
                "dataset_id": dataset.id,
                "unavailable_metrics": report_inputs.get("unavailable_metrics"),
            },
        )
    impressions_slide_payload = _build_impressions_slide_payload(report_row, locale=locale)
    general_insights_slide_payload = _build_general_insights_slide_payload(report_row)
    page_name = str(report_inputs["page_name"] or dataset.name or "Meta Page")
    followers = report_inputs.get("followers")
    reach = report_inputs.get("reach")
    engagement = report_inputs.get("engagement")
    impressions = report_inputs.get("impressions")
    reach_chart_data = build_meta_pages_reach_chart_data(report_inputs)
    reach_insight = build_meta_pages_reach_insight(report_inputs, locale)
    reach_chart_first_date, reach_chart_last_date = _meta_daily_series_bounds(
        reach_chart_data.get("points") if isinstance(reach_chart_data.get("points"), list) else []
    )
    impressions_first_date, impressions_last_date = _meta_daily_series_bounds(
        impressions_slide_payload.get("impressions_daily")
        if isinstance(impressions_slide_payload.get("impressions_daily"), list)
        else []
    )
    reach_source = (
        "dataset.data.reach_daily"
        if isinstance(row.get("reach_daily"), list)
        else "legacy_empty_or_csv_fallback"
    )
    impressions_source = (
        "dataset.data.impressions_daily"
        if isinstance(row.get("impressions_daily"), list)
        else "legacy_normalized_report_metrics_fallback"
    )

    title = payload.title or (f"{page_name} Overview" if locale == "en" else f"{page_name} Resumen")
    summary = build_meta_pages_summary(report_inputs, locale)
    recent_posts_summary = build_meta_pages_recent_posts_summary(report_inputs, locale)
    ai_source = {"data": report_row}
    claude_payload = build_meta_pages_ai_payload(ai_source)
    ai_summary = generate_meta_pages_ai_summary(claude_payload, locale)
    ai_plan_context = build_ai_agent_plan_context(
        plan=slide_limits["plan"],
        effective_slide_limit=slide_limits["effective_slide_limit"],
        dataset_context={"dataset_id": dataset.id},
        report_context={"generation_mode": generation_mode, "ai_mode": ai_mode},
    )
    logger.info(
        "[AIAgents][plan_context]",
        extra={
            "dataset_id": dataset.id,
            "plan": ai_plan_context["plan"],
            "ai_mode": ai_mode,
            "max_slides": ai_plan_context["max_slides"],
            "allow_ai_agents": ai_plan_context["allow_ai_agents"],
            "effective_slide_limit": ai_plan_context["effective_slide_limit"],
        },
    )
    if ai_mode == "agents" and not ai_plan_context["allow_ai_agents"]:
        raise http_error(
            403,
            "plan_restricted",
            "AI agents are not available for current plan.",
        )
    report_inputs_for_blocks = dict(report_inputs)
    for daily_key in (
        "engagement_daily",
        "content_interactions_daily",
        "interactions_daily",
        "followers_daily",
        "fan_count_daily",
        "audience_daily",
    ):
        if isinstance(report_row.get(daily_key), list) and daily_key not in report_inputs_for_blocks:
            report_inputs_for_blocks[daily_key] = report_row[daily_key]
    block_build_context = {
        "title": title,
        "report_timeframe": report_timeframe,
        "page_name": page_name,
        "followers": followers,
        "reach": reach,
        "engagement": engagement,
        "impressions": impressions,
        "summary": summary,
        "reach_chart_data": reach_chart_data,
        "reach_insight": reach_insight,
        "recent_posts_summary": recent_posts_summary,
        "ai_summary": ai_summary,
        "general_insights_slide_payload": general_insights_slide_payload,
        "impressions_slide_payload": impressions_slide_payload,
        "report_inputs": report_inputs_for_blocks,
        "branding": _user_branding(current_user),
        "requested_slides": slide_limits["requested_slides"],
    }
    block_specs = build_blocks(int(slide_limits["requested_slides"]), block_build_context)
    logger.warning(
        "report_blocks_metrics_used",
        extra={
            "dataset_id": dataset.id,
            "integration_type": report_inputs.get("integration_type"),
            "reach": report_inputs.get("reach"),
            "followers": report_inputs.get("followers"),
            "engagement": report_inputs.get("engagement"),
            "profile_visits": report_inputs.get("profile_visits"),
            "link_clicks": report_inputs.get("link_clicks"),
            "reach_daily_points": len(report_inputs.get("reach_daily") or []),
            "engagement_daily_points": len(report_inputs.get("engagement_daily") or []),
            "unavailable_metrics": report_inputs.get("unavailable_metrics"),
        },
    )
    if str(report_inputs.get("integration_type") or "").strip() == "instagram_business":
        logger.warning(
            "instagram_report_blocks_metrics_used",
            extra={
                "dataset_id": dataset.id,
                "reach": report_inputs.get("reach"),
                "followers": report_inputs.get("followers"),
                "engagement": report_inputs.get("engagement"),
                "views": report_inputs.get("views"),
                "profile_visits": report_inputs.get("profile_visits"),
                "link_clicks": report_inputs.get("link_clicks"),
                "unavailable_metrics": report_inputs.get("unavailable_metrics"),
            },
        )
    logger.info(
        "[ReportBlocks][build.selected]",
        extra={
            "dataset_id": dataset.id,
            "requested_slides": slide_limits["requested_slides"],
            "effective_slide_limit": slide_limits["effective_slide_limit"],
            "blocks_generados": len(block_specs),
        },
    )
    ai_agent_pipeline_result = None
    if ai_mode == "agents":
        ai_agent_pipeline_result = run_ai_agents_pipeline(
            ai_mode=ai_mode,
            plan_context=ai_plan_context,
            block_specs=block_specs,
            dataset_context={
                "dataset_id": dataset.id,
                "workspace_id": dataset.workspace_id,
                "timeframe": report_timeframe,
                "page_name": page_name,
                "report_inputs": report_inputs,
                "reach_chart_data": reach_chart_data,
                "impressions_slide_payload": impressions_slide_payload,
            },
            report_context={
                "generation_mode": generation_mode,
                "ai_mode": ai_mode,
                "locale": locale,
                "title": title,
                "branding": _user_branding(current_user),
            },
        )
        agent_block_specs = list(ai_agent_pipeline_result.get("blocks") or [])
        if len(agent_block_specs) == int(slide_limits["effective_slide_limit"]):
            block_specs = agent_block_specs
        else:
            ai_agent_pipeline_result = {
                **ai_agent_pipeline_result,
                "used": False,
                "fallback_used": True,
                "errors": list(ai_agent_pipeline_result.get("errors") or [])
                + [
                    "AI agents pipeline returned a block count different from effective_slide_limit."
                ],
            }
            logger.warning(
                "[ReportBlocks][agent_count_fallback]",
                extra={
                    "dataset_id": dataset.id,
                    "requested_slides": slide_limits["requested_slides"],
                    "effective_slide_limit": slide_limits["effective_slide_limit"],
                    "agent_blocks": len(agent_block_specs),
                    "blocks_generados": len(block_specs),
                },
            )
    ai_agent_metadata = build_ai_agent_metadata(
        ai_mode=ai_mode,
        allow_ai_agents=bool(ai_plan_context["allow_ai_agents"]),
        pipeline_result=ai_agent_pipeline_result,
    )
    block_specs = block_specs[: int(slide_limits["effective_slide_limit"])]
    logger.info(
        "[ReportBlocks][build.final]",
        extra={
            "dataset_id": dataset.id,
            "requested_slides": slide_limits["requested_slides"],
            "effective_slide_limit": slide_limits["effective_slide_limit"],
            "blocks_generados": len(block_specs),
        },
    )
    logger.info(
        "[MetaTimeframeBackend] report timeframe resolved",
        extra={
            "dataset_id": dataset.id,
            "dataset_timeframe": row.get("timeframe") if isinstance(row.get("timeframe"), dict) else None,
            "final_timeframe_injected_into_report_row": report_row.get("timeframe"),
            "report_timeframe_key": report_timeframe.get("key"),
            "report_description_timeframe": report_timeframe,
            "report_description_timeframe_key": report_timeframe.get("key"),
            "cover_since": report_timeframe.get("since"),
            "cover_until": report_timeframe.get("until"),
            "reach_chart_label": reach_chart_data.get("label"),
            "reach_chart_first_date": reach_chart_first_date,
            "reach_chart_last_date": reach_chart_last_date,
            "reach_insight_label": report_inputs.get("timeframe_label"),
            "impressions_label": impressions_slide_payload.get("label"),
            "impressions_first_date": impressions_first_date,
            "impressions_last_date": impressions_last_date,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][report.render.cover]",
        extra={
            "period_label": report_timeframe.get("label"),
            "period_since": report_timeframe.get("since"),
            "period_until": report_timeframe.get("until"),
            "source": timeframe_source,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][report.render.reach]",
        extra={
            "label": reach_chart_data.get("label"),
            "timeframe": reach_chart_data.get("timeframe"),
            "points_length": len(reach_chart_data.get("points", [])),
            "first_date": reach_chart_first_date,
            "last_date": reach_chart_last_date,
            "source": reach_source,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][report.render.reach_insight]",
        extra={
            "text": reach_insight,
            "timeframe_label": report_inputs.get("timeframe_label"),
            "source": timeframe_source,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][report.render.impressions]",
        extra={
            "label": impressions_slide_payload.get("label"),
            "timeframe": impressions_slide_payload.get("timeframe"),
            "impressions_daily_count": impressions_slide_payload.get("impressions_daily_count"),
            "first_date": impressions_first_date,
            "last_date": impressions_last_date,
            "source": impressions_source,
        },
    )
    logger.info(
        "Meta Pages report generation started",
        extra={
            "dataset_id": dataset.id,
            "page_name": page_name,
            "locale": locale,
            "reach_present": reach is not None,
            "reach_daily_points": len(reach_chart_data.get("points", [])),
            "reach_source_metric": reach_chart_data.get("source_metric"),
        },
    )

    report = Report(
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        name=title,
        description=json.dumps(
            {
                "source": report_source,
                "locale": locale,
                "timeframe": report_timeframe,
                "claude_payload": claude_payload,
                "branding": _user_branding(current_user),
                "requested_slides": slide_limits["requested_slides"],
                "effective_slide_limit": slide_limits["effective_slide_limit"],
                "plan_at_generation": slide_limits["plan"],
                "generation_mode": generation_mode,
                "plan_capabilities": slide_limits["capabilities"],
                **ai_agent_metadata,
            }
        ),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    logger.info(
        "[MetaTimeframeBackend][report.created]",
        extra={
            "report_id": report.id,
            "report_description_timeframe": _report_metadata(report).get("timeframe"),
        },
    )
    logger.info(
        "[AIAgents][pipeline.final]",
        extra={
            "report_id": report.id,
            "dataset_id": dataset.id,
            "plan": ai_plan_context["plan"],
            "ai_mode": ai_mode,
            "allow_ai_agents": ai_plan_context["allow_ai_agents"],
            "effective_slide_limit": ai_plan_context["effective_slide_limit"],
            "fallback_used": ai_agent_metadata["ai_agent_fallback_used"],
            "number_of_blocks_final": len(block_specs),
        },
    )
    period_comparison_range = _meta_timeframe_range(block_build_context)
    logger.info(
        "[PERIOD_COMPARISON][range]",
        extra={
            "report_id": report.id,
            "dataset_id": dataset.id,
            "timeframe_key": period_comparison_range.get("timeframe_key"),
            "selected_timeframe": period_comparison_range.get("selected_timeframe"),
            "requested_since": period_comparison_range.get("requested_since"),
            "requested_until": period_comparison_range.get("requested_until"),
            "current_since": period_comparison_range.get("current_since"),
            "current_until": period_comparison_range.get("current_until"),
            "previous_since": period_comparison_range.get("previous_since"),
            "previous_until": period_comparison_range.get("previous_until"),
            "duration_days": period_comparison_range.get("duration_days"),
        },
    )
    logger.info(
        "[MetaTimeframeBackend][render.cover]",
        extra={
            "report_id": report.id,
            "timeframe_source": timeframe_source,
            "since": report_timeframe.get("since"),
            "until": report_timeframe.get("until"),
            "label": report_timeframe.get("label"),
        },
    )
    logger.info(
        "[MetaTimeframeBackend][render.reach]",
        extra={
            "report_id": report.id,
            "timeframe_source": timeframe_source,
            "label": reach_chart_data.get("label"),
            "points_count": len(reach_chart_data.get("points", [])),
            "first_date": reach_chart_first_date,
            "last_date": reach_chart_last_date,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][render.impressions]",
        extra={
            "report_id": report.id,
            "timeframe_source": timeframe_source,
            "label": impressions_slide_payload.get("label"),
            "points_count": impressions_slide_payload.get("impressions_daily_count"),
            "first_date": impressions_first_date,
            "last_date": impressions_last_date,
        },
    )

    report_version = ReportVersion(report_id=report.id, version=1)
    db.add(report_version)
    db.commit()
    db.refresh(report_version)

    for block_spec in block_specs:
        raw_data = block_spec.get("data_json")
        if isinstance(raw_data, str):
            try:
                block_data = json.loads(raw_data)
            except json.JSONDecodeError:
                block_data = {}
        elif isinstance(raw_data, dict):
            block_data = raw_data
        else:
            block_data = {}
        chart = block_data.get("chart") if isinstance(block_data.get("chart"), dict) else {}
        chart_points = chart.get("points") if isinstance(chart.get("points"), list) else []
        logger.info(
            "[BACKEND_BLOCK_PAYLOAD_AUDIT]",
            extra={
                "report_id": report.id,
                "dataset_id": dataset.id,
                "order": block_spec.get("order"),
                "type": block_spec.get("type"),
                "semantic_name": block_data.get("semantic_name") or block_data.get("semanticName"),
                "title": block_data.get("title"),
                "data_json_keys": sorted(block_data.keys()) if isinstance(block_data, dict) else [],
                "data_json_value": block_data.get("value"),
                "data_json_total": block_data.get("total"),
                "data_json_current_value": block_data.get("current_value"),
                "data_json_previous_value": block_data.get("previous_value"),
                "data_json_change_percentage": block_data.get("change_percentage"),
                "data_json_trend": block_data.get("trend"),
                "data_json_metrics": block_data.get("metrics"),
                "data_json_stats": block_data.get("stats"),
                "data_json_kpis": block_data.get("kpis"),
                "chart_points_length": len(chart_points),
            },
        )
        semantic_name = block_data.get("semantic_name") or block_data.get("semanticName")
        if semantic_name == "overview":
            logger.info(
                "[BACKEND_OVERVIEW_FINAL_PAYLOAD_AUDIT]",
                extra={
                    "report_id": report.id,
                    "dataset_id": dataset.id,
                    "semantic_name": semantic_name,
                    "data_json_insight": str(block_data.get("insight") or "")[:160],
                    "data_json_text": str(block_data.get("text") or "")[:160],
                    "data_json_summary": str(block_data.get("summary") or "")[:160],
                    "content": str(block_data.get("content") or "")[:160],
                },
            )
        metric_from_semantic = {
            "reach": "reach",
            "reach_overview": "reach",
            "impressions": "impressions",
            "impressions_trend": "impressions",
            "followers": "followers",
            "audience_growth": "followers",
            "engagement": "engagement",
            "engagement_overview": "engagement",
            "key_metrics_overview": "reach",
        }.get(str(semantic_name or "").strip())
        if semantic_name == "overview" and isinstance(block_data.get("metrics"), list):
            for metric_item in block_data.get("metrics") or []:
                if not isinstance(metric_item, dict):
                    continue
                metric_key = str(metric_item.get("key") or "").strip() or "unknown"
                comparison = _meta_metric_comparison(
                    block_build_context,
                    metric=metric_key,
                    current_value=metric_item.get("current_value", metric_item.get("value")),
                    current_points=_meta_metric_series(block_build_context, metric_key),
                )
                logger.info(
                    "[PERIOD_COMPARISON][metric]",
                    extra={
                        "report_id": report.id,
                        "dataset_id": dataset.id,
                        "semantic_name": semantic_name,
                        "metric_key": metric_key,
                        "current_points": comparison.get("current_points"),
                        "previous_points": comparison.get("previous_points"),
                        "current_value": comparison.get("current_value"),
                        "previous_value": comparison.get("previous_value"),
                        "change_percentage": comparison.get("change_percentage"),
                        "trend": comparison.get("trend"),
                        "source_current": comparison.get("source_current"),
                        "source_previous": comparison.get("source_previous"),
                        "reason_if_null": comparison.get("reason_if_null"),
                    },
                )
                logger.info(
                    "[BACKEND_OVERVIEW_COMPARISON_AUDIT]",
                    extra={
                        "report_id": report.id,
                        "dataset_id": dataset.id,
                        "semantic_name": semantic_name,
                        "metric_key": metric_key,
                        "current_value": comparison.get("current_value"),
                        "previous_value": comparison.get("previous_value"),
                        "change_percentage": comparison.get("change_percentage"),
                        "trend": comparison.get("trend"),
                        "source_current": comparison.get("source_current"),
                        "source_previous": comparison.get("source_previous"),
                        "reason_if_null": comparison.get("reason_if_null"),
                    },
                )
        elif metric_from_semantic:
            comparison = _meta_metric_comparison(
                block_build_context,
                metric=metric_from_semantic,
                current_value=block_data.get("current_value", block_data.get("value")),
                current_points=_meta_metric_series(block_build_context, metric_from_semantic),
            )
            logger.info(
                "[PERIOD_COMPARISON][metric]",
                extra={
                    "report_id": report.id,
                    "dataset_id": dataset.id,
                    "semantic_name": semantic_name,
                    "metric_key": metric_from_semantic,
                    "current_points": comparison.get("current_points"),
                    "previous_points": comparison.get("previous_points"),
                    "current_value": comparison.get("current_value"),
                    "previous_value": comparison.get("previous_value"),
                    "change_percentage": comparison.get("change_percentage"),
                    "trend": comparison.get("trend"),
                    "source_current": comparison.get("source_current"),
                    "source_previous": comparison.get("source_previous"),
                    "reason_if_null": comparison.get("reason_if_null"),
                },
            )
        if semantic_name == "engagement_overview":
            chart = block_data.get("chart") if isinstance(block_data.get("chart"), dict) else {}
            chart_points = chart.get("points") if isinstance(chart.get("points"), list) else []
            first_chart_point = chart_points[0] if chart_points else None
            last_chart_point = chart_points[-1] if chart_points else None
            logger.info(
                "[BACKEND_ENGAGEMENT_SLIDE_AUDIT]",
                extra={
                    "report_id": report.id,
                    "dataset_id": dataset.id,
                    "slide_block_index": block_spec.get("order"),
                    "block_title": block_data.get("title"),
                    "metric_value": block_data.get("current_value", block_data.get("value")),
                    "chart_points_count": len(chart_points),
                    "first_chart_point": first_chart_point,
                    "last_chart_point": last_chart_point,
                },
            )
            logger.info(
                "[BACKEND_OVERVIEW_COMPARISON_AUDIT]",
                extra={
                    "report_id": report.id,
                    "dataset_id": dataset.id,
                    "semantic_name": semantic_name,
                    "metric_key": metric_from_semantic,
                    "current_value": comparison.get("current_value"),
                    "previous_value": comparison.get("previous_value"),
                    "change_percentage": comparison.get("change_percentage"),
                    "trend": comparison.get("trend"),
                    "source_current": comparison.get("source_current"),
                    "source_previous": comparison.get("source_previous"),
                    "reason_if_null": comparison.get("reason_if_null"),
                },
            )

    blocks = [
        ReportBlock(
            report_version_id=report_version.id,
            type=str(block_spec["type"]),
            order=int(block_spec["order"]),
            data_json=str(block_spec["data_json"]),
            editable_fields_json=str(block_spec["editable_fields_json"]),
        )
        for block_spec in block_specs
    ]
    for block in blocks:
        db.add(block)
    db.commit()
    try:
        _generate_and_store_report_thumbnail(
            db=db,
            report=report,
            report_version=report_version,
            user_id=current_user.id,
            sync_branding_from_user=False,
        )
    except HTTPException:
        logger.exception("Meta Pages thumbnail generation failed", extra={"report_id": report.id})
    except Exception:
        logger.exception("Unexpected Meta Pages thumbnail generation failure", extra={"report_id": report.id})

    return MetaPagesReportCreateOut(
        report_id=report.id,
        version_id=report_version.id,
        version=report_version.version,
        dataset_id=dataset.id,
        title=title,
        locale=locale,
        status="ready",
    )


@app.post("/reports/meta-pages", response_model=MetaPagesReportCreateOut)
def create_meta_pages_report(
    payload: MetaPagesReportCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPagesReportCreateOut:
    started_at = perf_counter()
    locale = normalize_report_locale(payload.locale)
    logger.info(
        "[MetaTimeframeBackend][report.entry]",
        extra={
            "dataset_id_payload": payload.dataset_id,
            "report_title": payload.title,
            "locale": locale,
        },
    )
    dataset = db.get(Dataset, payload.dataset_id)
    if not dataset:
        raise http_error(404, "dataset_not_found", "Dataset not found.")
    _require_workspace_access(db, current_user.id, dataset.workspace_id)
    return _create_meta_dataset_report(
        dataset=dataset,
        payload=payload,
        current_user=current_user,
        db=db,
        report_source="meta_pages_v2",
        generation_mode="meta_pages",
    )


@app.post("/reports/instagram-business", response_model=MetaPagesReportCreateOut)
def create_instagram_business_report(
    payload: InstagramBusinessReportCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPagesReportCreateOut:
    logger.warning(
        "instagram_report_request",
        extra={
            "workspace_id": payload.workspace_id,
            "integration_id": payload.integration_id,
            "dataset_id": payload.dataset_id,
            "account_id": payload.account_id,
            "page_id": payload.page_id,
            "timeframe": payload.timeframe,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "requested_slides": payload.requested_slides,
            "template": None,
            "ai_mode": payload.ai_mode,
            "locale": payload.locale,
        },
    )
    try:
        dataset = _resolve_instagram_business_report_dataset(db, current_user, payload)
        dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        metric_keys = sorted(
            key
            for key in (
                "followers",
                "followers_count",
                "reach",
                "engagement",
                "content_interactions",
                "profile_visits",
                "link_clicks",
                "unavailable_metrics",
                "normalized_report_metrics",
            )
            if key in dataset_data
        )
        logger.warning(
            "instagram_report_dataset_resolved",
            extra={
                "dataset_id": dataset.id,
                "dataset_name": dataset.name,
                "workspace_id": dataset.workspace_id,
                "integration_type": dataset_data.get("integration_type"),
                "account_id": dataset_data.get("account_id") or payload.account_id or payload.page_id,
                "username": dataset_data.get("username"),
            },
        )
        logger.warning(
            "instagram_report_metrics_keys",
            extra={
                "dataset_id": dataset.id,
                "instagram_report_metrics_keys": metric_keys,
                "normalized_report_metric_keys": sorted(
                    (dataset_data.get("normalized_report_metrics") or {}).keys()
                )
                if isinstance(dataset_data.get("normalized_report_metrics"), dict)
                else [],
            },
        )
        response = _create_meta_dataset_report(
            dataset=dataset,
            payload=payload,
            current_user=current_user,
            db=db,
            report_source="instagram_business_v1",
            generation_mode="instagram_business",
        )
        logger.warning(
            "instagram_report_created",
            extra={
                "dataset_id": dataset.id,
                "report_id": response.report_id,
                "version_id": response.version_id,
                "version": response.version,
            },
        )
        return response
    except HTTPException:
        logger.exception(
            "instagram_report_error",
            extra={
                "workspace_id": payload.workspace_id,
                "integration_id": payload.integration_id,
                "dataset_id": payload.dataset_id,
                "account_id": payload.account_id,
                "page_id": payload.page_id,
            },
        )
        raise
    except Exception:
        logger.exception(
            "instagram_report_error",
            extra={
                "workspace_id": payload.workspace_id,
                "integration_id": payload.integration_id,
                "dataset_id": payload.dataset_id,
                "account_id": payload.account_id,
                "page_id": payload.page_id,
            },
        )
        raise


@app.get("/reports", response_model=list[ReportListItemOut])
def list_reports(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReportListItemOut]:
    started_at = perf_counter()
    query_params = dict(request.query_params)
    workspace_ids = [
        row[0]
        for row in db.query(WorkspaceMember.workspace_id)
        .filter(WorkspaceMember.user_id == current_user.id)
        .order_by(WorkspaceMember.workspace_id.asc())
        .all()
    ]

    logger.info(
        "Reports list requested",
        extra={
            "user_id": current_user.id,
            "workspace_ids": workspace_ids,
            "query_params": query_params,
            "path": request.url.path,
        },
    )

    try:
        reports = (
            db.query(Report)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Report.workspace_id)
            .filter(WorkspaceMember.user_id == current_user.id)
            .order_by(Report.created_at.desc())
            .all()
        )
        if not reports:
            elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
            logger.info(
                "Reports list returned no rows",
                extra={
                    "user_id": current_user.id,
                    "workspace_ids": workspace_ids,
                    "query_params": query_params,
                    "total_reports": 0,
                    "response_time_ms": elapsed_ms,
                },
            )
            return []

        version_counts = dict(
            db.query(ReportVersion.report_id, func.count(ReportVersion.id))
            .filter(ReportVersion.report_id.in_([report.id for report in reports]))
            .group_by(ReportVersion.report_id)
            .all()
        )

        response = [
            ReportListItemOut(
                id=report.id,
                name=report.name,
                status="completed" if version_counts.get(report.id, 0) > 0 else "pending",
                thumbnail_url=_report_thumbnail_url(report),
                created_at=report.created_at,
            )
            for report in reports
        ]
        elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
        logger.info(
            "Reports list returned successfully",
            extra={
                "user_id": current_user.id,
                "workspace_ids": workspace_ids,
                "query_params": query_params,
                "total_reports": len(response),
                "response_time_ms": elapsed_ms,
            },
        )
        return response
    except Exception:
        elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
        logger.exception(
            "Reports list failed",
            extra={
                "user_id": current_user.id,
                "workspace_ids": workspace_ids,
                "query_params": query_params,
                "response_time_ms": elapsed_ms,
            },
        )
        raise


@app.get("/reports/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    current_user: User = Depends(get_current_user_for_report_read),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    latest_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report.id)
        .order_by(ReportVersion.version.desc())
        .first()
    )
    metadata = _report_metadata(report)
    logger.info(
        "[MetaTimeframeBackend] report detail",
        extra={
            "report_id": report.id,
            "description_timeframe": _report_timeframe(report),
        },
    )
    return ReportOut(
        id=report.id,
        workspace_id=report.workspace_id,
        dataset_id=report.dataset_id,
        title=report.name,
        description=metadata,
        timeframe=_report_timeframe(report),
        version_id=latest_version.id if latest_version else None,
        version=latest_version.version if latest_version else None,
        locale=_report_locale(report),
        branding=_report_branding(report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@app.get("/reports/{report_id}/versions", response_model=list[ReportVersionOut])
def list_report_versions(
    report_id: int,
    current_user: User = Depends(get_current_user_for_report_read),
    db: Session = Depends(get_db),
) -> list[ReportVersionOut]:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    report_versions = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .all()
    )
    logger.info(
        "[MetaTimeframeBackend] report versions list",
        extra={
            "report_id": report.id,
            "versions_count": len(report_versions),
        },
    )
    return [
        _report_version_out(
            db,
            report=report,
            report_version=report_version,
        )
        for report_version in report_versions
    ]


@app.get("/reports/{report_id}/versions/{version}", response_model=ReportVersionOut)
def get_report_version(
    report_id: int,
    version: int,
    current_user: User = Depends(get_current_user_for_report_read),
    db: Session = Depends(get_db),
) -> ReportVersionOut:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    report_version, resolution_mode = _resolve_report_version_for_path(
        db,
        report_id=report_id,
        version_value=version,
    )
    logger.info(
        "[ReviewBootstrapBackend][version.lookup]",
        extra={
            "report_id": report.id,
            "requested_version": version,
            "interpretation": resolution_mode,
            "matched_version_id": report_version.id if report_version else None,
            "found_version_id": report_version.id if report_version else None,
            "found_version_number": report_version.version if report_version else None,
        },
    )
    if not report_version:
        raise http_error(404, "report_version_not_found", "Report version number not found.")

    if resolution_mode == "internal_version_id":
        logger.warning(
            "[ReviewBootstrapBackend][version.lookup.compat]",
            extra={
                "report_id": report.id,
                "requested_version": version,
                "matched_version_id": report_version.id,
                "matched_version_number": report_version.version,
                "compatibility_mode": "accepted_internal_version_id",
            },
        )

    return _report_version_out(db, report=report, report_version=report_version)


@app.post("/reports/{report_id}/thumbnail")
def refresh_report_thumbnail(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .first()
    )
    if not report_version:
        raise http_error(404, "report_version_not_found", "Report version not found.")

    thumbnail_s3_key = _generate_and_store_report_thumbnail(
        db=db,
        report=report,
        report_version=report_version,
        user_id=current_user.id,
        sync_branding_from_user=True,
    )
    db.refresh(report)
    return {
        "report_id": report.id,
        "version": report_version.version,
        "thumbnail_s3_key": thumbnail_s3_key,
        "thumbnail_url": _report_thumbnail_url(report),
    }


@app.post("/reports/{report_id}/export", response_model=ReportExportOut)
def export_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    try:
        export_plan_context = enforce_export_capability(db, report.workspace_id, "pptx")
    except HTTPException:
        logger.info(
            "[PPTXExportBackend][blocked]",
            extra={
                "report_id": report_id,
                "workspace_id": report.workspace_id,
                "export_type": "pptx",
                "allowed": False,
            },
        )
        raise

    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .first()
    )
    if not report_version:
        raise http_error(404, "report_version_not_found", "Report version not found.")

    blocks = (
        db.query(ReportBlock)
        .filter(ReportBlock.report_version_id == report_version.id)
        .order_by(ReportBlock.order.asc())
        .all()
    )
    if not blocks:
        logger.warning(
            "[PPTXExportBackend][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "report_version_id": report_version.id,
                "version": report_version.version,
                "plan": export_plan_context["plan"],
                "export_type": "pptx",
                "allowed": True,
                "payload_source": "persisted_report_version_blocks",
                "reason": "no_exportable_blocks",
            },
        )
        raise http_error(
            422,
            "export_content_not_found",
            "Report version has no exportable content.",
        )

    export = Export(workspace_id=report.workspace_id, report_id=report.id, status="processing")
    db.add(export)
    db.commit()
    db.refresh(export)

    payload = build_export_payload(export, report, report_version, blocks)
    payload_summary = {
        "export_id": payload.get("export_id"),
        "format": payload.get("format"),
        "report_id": (payload.get("report") or {}).get("id")
        if isinstance(payload.get("report"), dict)
        else None,
        "report_version_id": (payload.get("report_version") or {}).get("id")
        if isinstance(payload.get("report_version"), dict)
        else None,
        "version": (payload.get("report_version") or {}).get("version")
        if isinstance(payload.get("report_version"), dict)
        else None,
        "blocks_count": len(payload.get("blocks") or []),
        "block_types": [
            str(block.get("type"))
            for block in (payload.get("blocks") or [])
            if isinstance(block, dict)
        ],
        "description_timeframe": ((payload.get("report") or {}).get("description_json") or {}).get(
            "timeframe"
        )
        if isinstance(payload.get("report"), dict)
        and isinstance((payload.get("report") or {}).get("description_json"), dict)
        else None,
    }
    logger.info(
        "[PPTXExportBackend][payload]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "report_version_id": report_version.id,
            "version": report_version.version,
            "plan": export_plan_context["plan"],
            "export_type": "pptx",
            "allowed": True,
            "payload_source": "persisted_report_version_blocks",
            "blocks_count": len(blocks),
            "export_lambda_url_present": bool(settings.export_lambda_url),
            "export_lambda_url": settings.export_lambda_url,
            "payload_summary": payload_summary,
        },
    )
    logger.info(
        "[MetaTimeframeBackend][render.full]",
        extra=_timeframe_log_payload(
            report,
            source="export_payload",
            version_id=report_version.id,
        ),
    )
    try:
        response = trigger_export_service(payload)
    except HTTPException as exc:
        logger.warning(
            "[PPTXExportBackend][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "report_version_id": report_version.id,
                "version": report_version.version,
                "plan": export_plan_context["plan"],
                "export_type": "pptx",
                "allowed": True,
                "payload_source": "persisted_report_version_blocks",
                "reason": "export_service_failed",
                "detail": exc.detail,
            },
        )
        export.status = "failed"
        db.add(export)
        db.commit()
        raise

    try:
        export_result = finalize_export_response(export, report, response)
    except HTTPException as exc:
        logger.warning(
            "[PPTXExportBackend][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "report_version_id": report_version.id,
                "version": report_version.version,
                "plan": export_plan_context["plan"],
                "export_type": "pptx",
                "allowed": True,
                "payload_source": "persisted_report_version_blocks",
                "reason": "invalid_export_service_response",
                "detail": exc.detail,
            },
        )
        export.status = "failed"
        db.add(export)
        db.commit()
        raise

    export.status = str(export_result.get("status") or "done")
    export.output_s3_key = export_result.get("output_s3_key")
    export.download_key = export_result.get("download_key")
    db.add(export)
    db.commit()
    logger.info(
        "[PPTXExportBackend][success]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "report_version_id": report_version.id,
            "version": report_version.version,
            "plan": export_plan_context["plan"],
            "export_type": "pptx",
            "allowed": True,
            "payload_source": "persisted_report_version_blocks",
            "export_id": export.id,
            "status": export.status,
            "output_s3_key": export.output_s3_key,
            "download_key": export.download_key,
        },
    )

    return {
        "status": export_result["status"],
        "download_url": export_result["download_url"],
        "file_name": export_result["file_name"],
    }


@app.get("/reports/{report_id}/download/pdf")
def download_report_pdf(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    enforce_export_capability(db, report.workspace_id, "pdf")

    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .first()
    )
    if not report_version:
        raise http_error(404, "report_version_not_found", "Report version not found.")

    locale = _report_locale(report)
    export_url = build_report_pdf_export_url(report, report_version, locale=locale)
    export_token = create_report_export_token(
        str(current_user.id),
        report_id=report.id,
        version=report_version.version,
    )
    logger.info(
        "[MetaTimeframeBackend][render.pdf]",
        extra=_timeframe_log_payload(
            report,
            source="pdf_export_page",
            version_id=report_version.id,
        ),
    )
    logger.info(
        "PDF download requested",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "version_id": report_version.id,
            "auth_strategy": "authorization_header_report_export_token",
        },
    )
    pdf_bytes, pdf_debug = generate_pdf_from_export_page(
        export_url=export_url,
        report_id=report_id,
        auth_token=export_token,
    )
    logger.info(
        "PDF generated successfully",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "version_id": report_version.id,
            "auth_strategy": pdf_debug.get("auth_strategy"),
            "report_fetch_succeeded": pdf_debug.get("report_fetch_succeeded"),
            "page_count": pdf_debug.get("page_count"),
        },
    )
    file_name = f"measurable-report-{report_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
        },
    )


@app.put("/reports/{report_id}/versions/{version}/blocks/{block_id}", response_model=ReportBlockOut)
def update_report_block(
    report_id: int,
    version: int,
    block_id: int,
    payload: ReportBlockUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportBlock:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    report_version, _ = _resolve_report_version_for_path(
        db,
        report_id=report_id,
        version_value=version,
    )
    if not report_version:
        raise http_error(404, "report_version_not_found", "Report version not found.")

    block = db.get(ReportBlock, block_id)
    if not block or block.report_version_id != report_version.id:
        raise http_error(404, "block_not_found", "Block not found.")

    editable = json.loads(block.editable_fields_json or "[]")
    if not isinstance(editable, list):
        raise http_error(400, "invalid_editable_fields", "Invalid editable fields.")

    data = json.loads(block.data_json or "{}")
    if not isinstance(data, dict):
        raise http_error(400, "invalid_block_data", "Invalid block data.")

    for key, value in payload.data.items():
        if key not in editable:
            raise http_error(403, "field_not_editable", f"Field '{key}' is not editable.")
        data[key] = value

    block.data_json = json.dumps(data)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@app.post("/schedules", response_model=ScheduleSchema)
def create_schedule(
    payload: ScheduleCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    _require_workspace_access(db, current_user.id, payload.workspace_id)
    _require_pro_plan(db, payload.workspace_id)

    integration = db.get(Integration, payload.integration_id)
    if not integration or integration.workspace_id != payload.workspace_id:
        raise http_error(404, "integration_not_found", "Integration not found.")

    if payload.freq not in {"monthly"}:
        raise http_error(400, "invalid_frequency", "Only monthly schedules are supported.")
    if payload.day_of_month is None or not (1 <= payload.day_of_month <= 31):
        raise http_error(400, "invalid_day", "day_of_month must be between 1 and 31.")

    schedule = Schedule(
        workspace_id=payload.workspace_id,
        integration_id=payload.integration_id,
        freq=payload.freq,
        day_of_month=payload.day_of_month,
        timezone=payload.timezone,
        enabled=True,
        next_run_at=None,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@app.patch("/schedules/{schedule_id}", response_model=ScheduleSchema)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise http_error(404, "schedule_not_found", "Schedule not found.")
    _require_workspace_access(db, current_user.id, schedule.workspace_id)

    schedule.enabled = payload.enabled
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@app.get("/schedules", response_model=list[ScheduleSchema])
def list_schedules(
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Schedule]:
    _require_workspace_access(db, current_user.id, workspace_id)
    return (
        db.query(Schedule)
        .filter(Schedule.workspace_id == workspace_id)
        .order_by(Schedule.created_at.desc())
        .all()
    )


@app.post("/workspaces", response_model=WorkspaceOut)
def create_workspace(
    payload: WorkspaceCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = Workspace(name=payload.name, logo_url=payload.logo_url)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(membership)
    db.commit()
    return _workspace_out(db, workspace)


@app.put("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: int,
    payload: WorkspaceUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise http_error(404, "workspace_not_found", "Workspace not found.")
    _require_workspace_access(db, current_user.id, workspace_id)

    if payload.name is not None:
        workspace.name = payload.name
    if "logo_url" in payload.model_fields_set:
        workspace.logo_url = payload.logo_url

    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _workspace_out(db, workspace)


@app.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise http_error(404, "workspace_not_found", "Workspace not found.")
    _require_workspace_access(db, current_user.id, workspace_id)
    return _workspace_out(db, workspace)


@app.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceOut]:
    workspaces = (
        db.query(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == current_user.id)
        .order_by(Workspace.created_at.desc())
        .all()
    )
    return [_workspace_out(db, workspace) for workspace in workspaces]


@app.get("/integrations", response_model=list[IntegrationSchema])
def list_integrations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationSchema]:
    integrations = (
        db.query(Integration)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Integration.workspace_id)
        .filter(WorkspaceMember.user_id == current_user.id)
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )
    return integrations


@app.get("/integrations/meta/connect")
def meta_connect(
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_workspace_access(db, current_user.id, workspace_id)
    state = encode_state({"workspace_id": workspace_id, "user_id": current_user.id})
    url = oauth_connect_url(state)
    return {
        "auth_url": url,
        "scope": META_ADS_OAUTH_SCOPE,
        "status": "manual_token_recommended",
        "message": (
            "Meta Ads OAuth is temporarily disabled for the MVP. "
            "Use POST /integrations/meta/set-token-manual instead."
        ),
    }


@app.get("/integrations/meta/connect-pages")
def meta_connect_pages(
    workspace_id: int,
    integration_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_workspace_access(db, current_user.id, workspace_id)
    integration = _get_or_create_meta_integration_for_workspace(db, workspace_id)
    selected_integration_type = str(integration_type or "").strip() or None
    state = encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "integration_type": selected_integration_type,
            "source": "meta_pages_connect_pages",
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    redirect_uri = _meta_pages_redirect_uri()
    logger.warning(
        "Meta Pages OAuth connect workspace_id=%s user_id=%s integration_id=%s auth_redirect_uri=%s scope=%s selected_integration_type=%s",
        workspace_id,
        current_user.id,
        integration.id,
        redirect_uri,
        META_PAGES_OAUTH_SCOPE,
        selected_integration_type,
    )
    url = oauth_connect_pages_url(state, redirect_uri=redirect_uri)
    return {
        "auth_url": url,
        "integration_id": integration.id,
        "scope": META_PAGES_OAUTH_SCOPE,
        "message": "Connect Meta for Facebook Pages insights.",
    }


@app.get("/integrations/meta/callback")
def meta_callback(
    code: str,
    state: str,
    redirect_uri: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _run_meta_pages_oauth_callback(
        code=code,
        state=state,
        redirect_uri=redirect_uri,
        db=db,
        current_user=current_user,
    )


@app.get("/integrations/meta/callback-pages")
def meta_callback_pages(
    code: str,
    state: str,
    db: Session = Depends(get_db),
) -> Response:
    return _run_meta_pages_oauth_callback(
        code=code,
        state=state,
        redirect_uri=None,
        db=db,
        redirect_to_frontend=True,
    )


@app.get("/integrations/meta/businesses")
def meta_businesses(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, str]]:
    integration = _get_meta_integration(db, current_user, integration_id)
    access_token = _get_meta_access_token(db, integration)
    return get_businesses(access_token)


@app.get("/integrations/meta/debug-token")
def meta_debug_token(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, integration_id)
    token_account = _get_meta_token_account(db, integration.id)
    if not token_account:
        raise http_error(404, "missing_token", "Meta token not found.")

    token = _get_latest_integration_token(db, token_account.id)
    if not token:
        raise http_error(404, "missing_token", "Meta token not found.")

    return {
        "integration_id": integration.id,
        "account_id": token.account_id,
        "access_token": token.access_token,
    }


@app.post("/integrations/meta/set-token-manual")
def meta_set_token_manual(
    payload: MetaSetTokenManualIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, payload.integration_id)
    token_account = _get_meta_token_account(db, integration.id)
    if not token_account:
        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=_meta_token_account_external_id(integration.id),
            display_name="Meta token store",
        )
        db.add(token_account)
        db.commit()
        db.refresh(token_account)

    _replace_integration_token(
        db,
        account_id=token_account.id,
        workspace_id=integration.workspace_id,
        access_token=payload.access_token,
    )
    _refresh_meta_pages_authorized_cache(
        db,
        integration,
        payload.access_token,
        user_id=current_user.id,
        return_empty_on_error=True,
    )
    _set_meta_integration_status(db, integration, status="connected")

    return {
        "status": "ok",
        "integration_id": integration.id,
        "account_id": token_account.id,
    }


@app.get("/integrations/meta/debug-permissions")
def meta_debug_permissions(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, integration_id)
    access_token = _get_meta_access_token(db, integration)
    result = debug_ads_permissions(access_token)
    result["integration_id"] = integration.id
    return result


@app.get("/integrations/meta/pages", response_model=list[MetaPageOut])
def meta_pages(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MetaPageOut]:
    return _get_meta_records_response(
        db,
        current_user,
        integration_id,
        record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
        selected_integration_type="facebook_pages",
        debug_source="dropdown_response",
    )


def _get_meta_records_response(
    db: Session,
    current_user: User,
    integration_id: int,
    *,
    record_type: str,
    selected_integration_type: str,
    debug_source: str,
) -> list[MetaPageOut]:
    integration = _get_meta_integration(db, current_user, integration_id)
    stored_records_before = (
        db.query(MetaPage)
        .filter(MetaPage.integration_id == integration.id)
        .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
        .all()
    )
    stored_pages_before = _get_stored_meta_records(
        db,
        integration.id,
        record_type=record_type,
    )
    stored_facebook_pages_before = _filter_meta_records(
        stored_records_before,
        record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
    )
    stored_instagram_accounts_before = _filter_meta_records(
        stored_records_before,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    logger.warning(
        "Meta records GET start integration_id=%s user_id=%s record_type=%s stored_total_pages_count=%s stored_facebook_page_count=%s stored_instagram_account_count=%s stored_count=%s stored_names=%s",
        integration.id,
        current_user.id,
        record_type,
        len(stored_records_before),
        len(stored_facebook_pages_before),
        len(stored_instagram_accounts_before),
        len(stored_pages_before),
        [page.name for page in stored_pages_before],
    )
    try:
        access_token = _get_meta_access_token(db, integration)
    except HTTPException as exc:
        logger.warning(
            "Meta records GET missing token integration_id=%s user_id=%s record_type=%s stored_count=%s stored_names=%s error=%s",
            integration.id,
            current_user.id,
            record_type,
            len(stored_pages_before),
            [page.name for page in stored_pages_before],
            str(exc.detail),
        )
        _cache_meta_pages(db, integration, current_user.id, [])
        _clear_selected_meta_page_if_unauthorized(db, integration, set())
        _log_meta_pages_debug(
            integration_id=integration.id,
            source="dropdown_response_missing_token",
            pages=[],
            dropdown_count=0,
        )
        return []

    cached_pages, diagnostics, facebook_pages = _refresh_meta_pages_from_live_graph(
        db,
        integration,
        access_token=access_token,
        user_id=current_user.id,
        selected_integration_type=selected_integration_type,
        context=debug_source,
        return_empty_on_error=True,
    )
    instagram_accounts = _filter_meta_records(
        cached_pages,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    returned_records = (
        _filter_meta_records(cached_pages, record_type=record_type)
        if record_type == META_RECORD_TYPE_FACEBOOK_PAGE
        else instagram_accounts
    )
    _log_meta_account_summary(
        integration_id=integration.id,
        user_id=current_user.id,
        selected_integration_type=selected_integration_type,
        facebook_pages=facebook_pages,
        instagram_accounts=instagram_accounts,
        context=debug_source,
    )
    _log_meta_pages_debug(
        integration_id=integration.id,
        source=debug_source,
        pages=returned_records,
        dropdown_count=len(returned_records),
    )
    logger.warning(
        "Meta records GET completed integration_id=%s user_id=%s record_type=%s stored_total_pages_count=%s stored_facebook_page_count=%s stored_instagram_account_count=%s returned_pages_count=%s returned_page_names=%s",
        integration.id,
        current_user.id,
        record_type,
        len(cached_pages),
        len(facebook_pages),
        len(instagram_accounts),
        len(returned_records),
        [page.name for page in returned_records],
    )
    return [_meta_page_out_from_cache(page) for page in returned_records]


@app.get("/integrations/meta/facebook-pages", response_model=list[MetaPageOut])
def meta_facebook_pages(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MetaPageOut]:
    return _get_meta_records_response(
        db,
        current_user,
        integration_id,
        record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
        selected_integration_type="facebook_pages",
        debug_source="facebook_pages_response",
    )


@app.get("/integrations/meta/instagram-accounts", response_model=list[MetaPageOut])
def meta_instagram_accounts(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MetaPageOut]:
    return _get_meta_records_response(
        db,
        current_user,
        integration_id,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        selected_integration_type="instagram_accounts",
        debug_source="instagram_accounts_response",
    )


@app.get("/debug/meta-pages-state")
def debug_meta_pages_state(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not _is_development_env():
        raise http_error(404, "not_found", "Not found.")
    integration = _get_meta_integration(db, current_user, integration_id)
    token_account = _get_meta_token_account(db, integration.id)
    latest_token = _get_latest_integration_token(db, token_account.id) if token_account else None
    stored_pages = (
        db.query(MetaPage)
        .filter(MetaPage.integration_id == integration.id)
        .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
        .all()
    )
    page_names = [page.name for page in stored_pages]
    return {
        "integration": {
            "id": integration.id,
            "workspace_id": integration.workspace_id,
            "provider": integration.provider,
            "name": integration.name,
        },
        "integration_id": integration.id,
        "user_id": current_user.id,
        "has_token": bool(latest_token and latest_token.access_token),
        "token_preview": (
            f"{latest_token.access_token[:8]}..." if latest_token and latest_token.access_token else None
        ),
        "token_updated_at": latest_token.updated_at.isoformat() if latest_token and latest_token.updated_at else None,
        "stored_pages_count": len(stored_pages),
        "stored_page_names": page_names,
        "stored_pages": [
            {
                "integration_id": page.integration_id,
                "record_type": page.record_type,
                "page_id": page.page_id,
                "parent_page_id": page.parent_page_id,
                "name": page.name,
                "instagram_username": page.instagram_username,
                "profile_picture_url": page.profile_picture_url,
                "has_page_access_token": bool(page.page_access_token),
                "tasks": page.tasks,
                "perms": page.perms,
                "category": page.category,
                "business_name": page.business_name,
                "user_id": page.user_id,
                "updated_at": page.updated_at.isoformat() if page.updated_at else None,
            }
            for page in stored_pages
        ],
    }


@app.get("/debug/meta-instagram-diagnostics")
def debug_meta_instagram_diagnostics(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not _is_development_env():
        raise http_error(404, "not_found", "Not found.")

    integration = _get_meta_integration(db, current_user, integration_id)
    access_token = _get_meta_access_token(db, integration)
    cached_pages, diagnostics, _ = _refresh_meta_pages_from_live_graph(
        db,
        integration,
        access_token=access_token,
        user_id=current_user.id,
        selected_integration_type="instagram_accounts",
        context="debug_instagram_diagnostics",
        return_empty_on_error=True,
    )
    instagram_accounts = [
        _meta_page_out_from_cache(record).model_dump()
        for record in _filter_meta_records(cached_pages, record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT)
    ]
    response: dict[str, Any] = {
        "integration_id": integration.id,
        "workspace_id": integration.workspace_id,
        "token_received": bool(access_token),
        "token_preview": _meta_token_preview(access_token),
        "oauth_scope": META_PAGES_OAUTH_SCOPE,
        "diagnostics": diagnostics,
        "pages_count": len(diagnostics),
        "page_names": [item.get("page_name") for item in diagnostics if item.get("page_name")],
        "instagram_accounts_found_count": len(instagram_accounts),
        "instagram_usernames_found": [
            str(account.get("username") or account.get("instagram_username") or "").strip()
            for account in instagram_accounts
            if str(account.get("username") or account.get("instagram_username") or "").strip()
        ],
        "instagram_accounts": instagram_accounts,
    }
    if instagram_accounts:
        return response

    response["error"] = "no_instagram_business_account_found"
    response["explanation"] = (
        "Meta Graph API did not return an Instagram Business Account for any authorized Facebook Page. "
        "The Facebook Page must be linked to an Instagram Business/Creator account and the current user must have admin access."
    )
    return response


@app.get("/debug/meta-instagram-live")
def debug_meta_instagram_live(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not _is_development_env():
        raise http_error(404, "not_found", "Not found.")

    integration = _get_meta_integration(db, current_user, integration_id)
    access_token = _get_meta_access_token(db, integration)
    token_account = _get_meta_token_account(db, integration.id)
    latest_token = _get_latest_integration_token(db, token_account.id) if token_account else None
    cached_pages, diagnostics, _ = _refresh_meta_pages_from_live_graph(
        db,
        integration,
        access_token=access_token,
        user_id=current_user.id,
        selected_integration_type="instagram_accounts",
        context="debug_meta_instagram_live",
        return_empty_on_error=True,
    )
    instagram_accounts = _filter_meta_records(
        cached_pages,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    return {
        "integration_id": integration.id,
        "workspace_id": integration.workspace_id,
        "user_id": current_user.id,
        "scope": META_PAGES_OAUTH_SCOPE,
        "token_received": bool(access_token),
        "token_preview": _meta_token_preview(access_token),
        "token_id": latest_token.id if latest_token else None,
        "token_updated_at": latest_token.updated_at.isoformat() if latest_token and latest_token.updated_at else None,
        "pages_count": len(diagnostics),
        "page_names": [item.get("page_name") for item in diagnostics if item.get("page_name")],
        "instagram_accounts_found_count": len(instagram_accounts),
        "instagram_usernames": [
            account.instagram_username
            for account in instagram_accounts
            if account.instagram_username
        ],
        "pages": diagnostics,
    }


@app.post("/integrations/meta/select-page")
def meta_select_page(
    payload: MetaSelectPageIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    logger.warning(
        "Meta select page start select_page_body_integration_id=%s select_page_body_page_id=%s",
        payload.integration_id,
        payload.page_id,
    )
    integration = _get_meta_integration(db, current_user, payload.integration_id)
    logger.warning(
        "Meta select page integration resolved select_page_resolved_integration_id=%s",
        integration.id,
    )
    access_token = _get_meta_access_token(db, integration)
    logger.warning(
        "Meta select page token resolved select_page_token_found=%s",
        bool(access_token),
    )
    local_record = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.page_id == payload.page_id,
            MetaPage.record_type.in_(
                [META_RECORD_TYPE_FACEBOOK_PAGE, META_RECORD_TYPE_INSTAGRAM_ACCOUNT]
            ),
        )
        .order_by(MetaPage.record_type.asc(), MetaPage.updated_at.desc(), MetaPage.id.desc())
        .first()
    )
    logger.warning(
        "Meta select page local lookup select_page_local_record_found=%s local_record_type=%s",
        bool(local_record),
        local_record.record_type if local_record else None,
    )

    selected_page: dict[str, Any] | None = None
    graph_fallback_used = False
    if local_record is not None:
        selected_page = {
            "id": payload.page_id,
            "name": local_record.business_name if local_record.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT else local_record.name,
            "access_token": local_record.page_access_token or "",
        }
    else:
        graph_fallback_used = True
        try:
            pages = list_pages(
                access_token,
                context="meta_select_page",
                integration_id=integration.id,
                user_id=current_user.id,
                token_received=bool(access_token),
            )
            for page in pages:
                if str(page.get("id") or "") == payload.page_id:
                    selected_page = page
                    break
            if not selected_page:
                raise http_error(400, "page_not_found", "Page not found for this token.")
        except HTTPException as exc:
            if not _is_meta_nonexisting_accounts_field_error(exc):
                raise
            page_info = fetch_page_info(access_token, payload.page_id)
            if str(page_info.get("id") or "") != payload.page_id:
                raise http_error(400, "page_not_found", "Page not found for this token.") from exc
            selected_page = {
                "id": payload.page_id,
                "name": page_info.get("name") or payload.page_id,
                "access_token": "",
            }
    logger.warning(
        "Meta select page graph fallback select_page_graph_fallback_used=%s",
        graph_fallback_used,
    )

    page_account = _save_selected_meta_page(
        db,
        integration,
        payload.page_id,
        str(selected_page.get("name") or payload.page_id),
    )
    page_access_token = str(selected_page.get("access_token") or "")
    if page_access_token:
        _save_meta_page_token(db, page_account, integration.workspace_id, page_access_token)

    logger.warning(
        "Meta select page completed select_page_success=%s integration_id=%s page_id=%s",
        True,
        integration.id,
        payload.page_id,
    )

    return {
        "status": "selected",
        "integration_id": integration.id,
        "page_id": payload.page_id,
        "page_name": page_account.display_name,
        "selected": True,
    }


@app.get("/integrations/meta/ad-accounts")
def meta_ad_accounts(
    integration_id: int,
    business_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, str]]:
    integration = _get_meta_integration(db, current_user, integration_id)
    access_token = _get_meta_access_token(db, integration)
    return get_owned_ad_accounts(access_token, business_id)


@app.post("/integrations/meta/select-account")
def meta_select_account(
    payload: MetaSelectAccountIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, payload.integration_id)
    access_token = _get_meta_access_token(db, integration)
    accounts = get_owned_ad_accounts(access_token, payload.business_id)

    selected_meta_account = None
    requested_id = _normalize_meta_ad_account_id(payload.ad_account_id)
    for account in accounts:
        meta_id = str(account.get("id") or "")
        meta_account_id = str(account.get("account_id") or "")
        if requested_id in {
            _normalize_meta_ad_account_id(meta_id),
            _normalize_meta_ad_account_id(meta_account_id),
        }:
            selected_meta_account = account
            break

    if not selected_meta_account:
        raise http_error(400, "invalid_ad_account", "Ad account not found in business.")
    selected_external_account_id = str(
        selected_meta_account.get("id") or payload.ad_account_id
    )
    selected_account = _save_selected_meta_account(
        db,
        integration,
        selected_external_account_id,
        selected_meta_account.get("name"),
    )

    return {
        "status": "selected",
        "integration_id": integration.id,
        "business_id": payload.business_id,
        "ad_account_id": selected_account.external_account_id,
    }


@app.post("/integrations/meta/select-account-manual")
def meta_select_account_manual(
    payload: MetaSelectAccountManualIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, payload.integration_id)
    selected_account = _save_selected_meta_account(
        db,
        integration,
        payload.ad_account_id,
        payload.ad_account_id,
    )
    return {
        "status": "selected",
        "integration_id": integration.id,
        "ad_account_id": selected_account.external_account_id,
    }


@app.post("/integrations/meta/sync")
def meta_sync(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, integration_id)
    selected_account = _get_selected_meta_account(db, integration.id)
    if not selected_account:
        raise http_error(
            400,
            "meta_account_not_selected",
            "No Meta ad account selected. Call POST /integrations/meta/select-account-manual first.",
        )

    access_token = _get_meta_access_token(db, integration)
    try:
        insights = fetch_campaign_insights(access_token, selected_account.external_account_id)
    except HTTPException as exc:
        if _is_meta_ads_permission_error(exc):
            raise http_error(
                400,
                "meta_ads_permissions_missing",
                "Meta connected, but Ads permissions are missing for this ad account.",
            ) from exc
        raise
    workspace_id = integration.workspace_id

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "date_start",
            "campaign_name",
            "impressions",
            "clicks",
            "spend",
            "ctr",
            "cpc",
            "reach",
        ],
    )
    writer.writeheader()
    for row in insights:
        writer.writerow(
            {
                "date_start": row.get("date_start"),
                "campaign_name": row.get("campaign_name"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "spend": row.get("spend"),
                "ctr": row.get("ctr"),
                "cpc": row.get("cpc"),
                "reach": row.get("reach"),
            }
        )

    csv_bytes = output.getvalue().encode("utf-8")
    filename = "meta_ads_insights.csv"
    _enforce_workspace_storage_for_upload(db, workspace_id, len(csv_bytes))
    dataset = Dataset(
        workspace_id=workspace_id,
        name=filename,
        description="Meta Ads insights",
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    key = f"workspaces/{workspace_id}/datasets/{dataset.id}/{filename}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        s3.put_object(Bucket=settings.s3_inputs_bucket, Key=key, Body=csv_bytes)
    except Exception:
        db.delete(dataset)
        db.commit()
        raise http_error(502, "s3_upload_failed", "Failed to upload file.")

    dataset_file = DatasetFile(
        dataset_id=dataset.id,
        workspace_id=workspace_id,
        s3_key=key,
        size_bytes=len(csv_bytes),
        content_type="text/csv",
    )
    db.add(dataset_file)
    db.commit()

    return {"dataset_id": dataset.id, "status": "uploaded"}


def _sync_meta_instagram_account(
    *,
    db: Session,
    integration: Integration,
    selected_page: IntegrationAccount,
    selected_meta_record: MetaPage,
    timeframe_config: dict[str, Any],
    current_user: User,
) -> MetaPagesSyncOut:
    instagram_user_id = selected_meta_record.page_id
    instagram_username = selected_meta_record.instagram_username or None
    account_name = selected_meta_record.name or instagram_user_id
    logger.warning(
        "Meta Instagram sync selected account",
        extra={
            "integration_id": integration.id,
            "selected_instagram_account_id": instagram_user_id,
            "selected_instagram_username": instagram_username,
            "instagram_scope_used": META_PAGES_OAUTH_SCOPE,
            "timeframe": timeframe_config,
        },
    )

    access_token = _get_meta_page_access_token(db, integration, selected_page)
    try:
        _refresh_meta_pages_authorized_cache(
            db,
            integration,
            access_token,
            user_id=current_user.id,
            return_empty_on_error=True,
        )
    except HTTPException as exc:
        if not _is_meta_api_error(exc):
            raise
        logger.warning(
            "Meta Instagram cache refresh failed during sync",
            extra={"integration_id": integration.id, "error": str(exc.detail)},
        )

    profile_payload = fetch_page_info_with_metadata(
        access_token,
        instagram_user_id,
        fields="id,username,name,profile_picture_url,followers_count",
    )
    logger.warning(
        "instagram_profile_lookup_status",
        extra={
            "integration_id": integration.id,
            "instagram_selected_account_id": instagram_user_id,
            "instagram_profile_lookup_status": profile_payload.get("_meta_http_status_code"),
            "instagram_profile_lookup_raw_body_truncated": profile_payload.get("_meta_raw_body"),
        },
    )
    account_name = str(profile_payload.get("name") or account_name or instagram_user_id)
    instagram_username = str(profile_payload.get("username") or instagram_username or "").strip() or None
    followers_count = _normalize_instagram_insight_value(profile_payload.get("followers_count"))

    requested_metrics = [
        "reach",
        "impressions",
        "views",
        "total_interactions",
        "accounts_engaged",
        "content_interactions",
        "profile_views",
        "website_clicks",
    ]
    logger.warning(
        "Meta Instagram insights request",
        extra={
            "integration_id": integration.id,
            "selected_instagram_account_id": instagram_user_id,
            "selected_instagram_username": instagram_username,
            "instagram_scope_used": META_PAGES_OAUTH_SCOPE,
            "ig_insights_request_metrics": requested_metrics,
        },
    )

    normalized_metrics: dict[str, int | None] = {}
    metric_series: dict[str, list[dict[str, int | str | None]]] = {}
    metric_end_times: dict[str, str | None] = {}
    metric_latest_values: dict[str, int | None] = {}
    metric_fallback_used: dict[str, bool] = {}
    unavailable_metrics: dict[str, str] = {}
    instagram_metric_audit: dict[str, dict[str, Any]] = {}
    for metric_name in requested_metrics:
        metric_type: str | None = None
        insight_payload: dict[str, Any] | None = None
        is_engagement_metric = metric_name in {"total_interactions", "accounts_engaged", "content_interactions"}
        logger.warning(
            "instagram_insights_metric_requested",
            extra={
                "integration_id": integration.id,
                "instagram_selected_account_id": instagram_user_id,
                "metric_name_requested": metric_name,
                "metric_type": metric_type,
                "graph_endpoint": f"/{instagram_user_id}/insights",
                "period": "day",
                "since": timeframe_config["since"],
                "until": timeframe_config["until"],
            },
        )
        if is_engagement_metric:
            logger.warning(
                "engagement_metric_requested",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "graph_endpoint": f"/{instagram_user_id}/insights",
                    "period": "day",
                    "since": timeframe_config["since"],
                    "until": timeframe_config["until"],
                },
            )
            logger.warning(
                "instagram_engagement_metric_requested",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "graph_endpoint": f"/{instagram_user_id}/insights",
                    "period": "day",
                    "since": timeframe_config["since"],
                    "until": timeframe_config["until"],
                },
            )
        try:
            insight_payload = fetch_instagram_insights_metric_with_metadata(
                access_token,
                instagram_user_id,
                metric_name=metric_name,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
                metric_type=metric_type,
            )
        except HTTPException as exc:
            if metric_name == "total_interactions" and _is_total_interactions_metric_type_error(exc):
                metric_type = "total_value"
                metric_fallback_used[metric_name] = True
                logger.warning(
                    "instagram_insights_metric_requested",
                    extra={
                        "integration_id": integration.id,
                        "instagram_selected_account_id": instagram_user_id,
                        "metric_name_requested": metric_name,
                        "metric_type": metric_type,
                        "graph_endpoint": f"/{instagram_user_id}/insights",
                        "period": "day",
                        "since": timeframe_config["since"],
                        "until": timeframe_config["until"],
                        "fallback_used": True,
                    },
                )
                try:
                    insight_payload = fetch_instagram_insights_metric_with_metadata(
                        access_token,
                        instagram_user_id,
                        metric_name=metric_name,
                        since=timeframe_config["since"],
                        until=timeframe_config["until"],
                        metric_type=metric_type,
                    )
                except HTTPException as retry_exc:
                    exc = retry_exc
            else:
                metric_fallback_used[metric_name] = False
            if insight_payload is None:
                if not _is_meta_api_error(exc):
                    raise
                error_details = _meta_api_error_details(exc)
                logger.warning(
                    "instagram_insights_metric_error",
                    extra={
                        "integration_id": integration.id,
                        "instagram_selected_account_id": instagram_user_id,
                        "selected_instagram_username": instagram_username,
                        "metric_name_requested": metric_name,
                        "metric_type": metric_type,
                        "raw_values": [],
                        "sum_value": None,
                        "error": error_details["error_message"],
                        "fallback_used": metric_fallback_used.get(metric_name, False),
                        "instagram_insights_metric_status": error_details["upstream_status_code"],
                        "instagram_insights_metric_error": error_details["error_message"],
                        "ig_insights_raw_body_truncated": error_details["response_body"],
                    },
                )
                if is_engagement_metric:
                    logger.warning(
                        "engagement_status",
                        extra={
                            "integration_id": integration.id,
                            "instagram_selected_account_id": instagram_user_id,
                            "metric_name_requested": metric_name,
                            "metric_type": metric_type,
                            "error": error_details["error_message"],
                            "fallback_used": metric_fallback_used.get(metric_name, False),
                        },
                    )
                    logger.warning(
                        "instagram_engagement_metric_status",
                        extra={
                            "integration_id": integration.id,
                            "instagram_selected_account_id": instagram_user_id,
                            "metric_name_requested": metric_name,
                            "metric_type": metric_type,
                            "status": "error",
                            "error": error_details["error_message"],
                            "fallback_used": metric_fallback_used.get(metric_name, False),
                        },
                    )
                    logger.warning(
                        "instagram_engagement_raw_values",
                        extra={
                            "integration_id": integration.id,
                            "instagram_selected_account_id": instagram_user_id,
                            "metric_name_requested": metric_name,
                            "metric_type": metric_type,
                            "raw_values": [],
                            "sum_value": None,
                            "fallback_used": metric_fallback_used.get(metric_name, False),
                        },
                    )
                    logger.warning(
                        "instagram_engagement_final_value",
                        extra={
                            "integration_id": integration.id,
                            "instagram_selected_account_id": instagram_user_id,
                            "metric_name_requested": metric_name,
                            "metric_type": metric_type,
                            "final_value": None,
                            "fallback_used": metric_fallback_used.get(metric_name, False),
                        },
                    )
                    logger.warning(
                        "instagram_engagement_unavailable_reason",
                        extra={
                            "integration_id": integration.id,
                            "instagram_selected_account_id": instagram_user_id,
                            "metric_name_requested": metric_name,
                            "metric_type": metric_type,
                            "reason": error_details["error_message"],
                            "fallback_used": metric_fallback_used.get(metric_name, False),
                        },
                    )
                unavailable_metrics[metric_name] = str(error_details["error_message"] or "metric_unavailable")
                normalized_metrics[metric_name] = None
                metric_series[metric_name] = []
                metric_end_times[metric_name] = None
                metric_latest_values[metric_name] = None
                instagram_metric_audit[metric_name] = {
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "graph_endpoint": f"/{instagram_user_id}/insights",
                    "period": "day",
                    "since": timeframe_config["since"],
                    "until": timeframe_config["until"],
                    "raw_values": [],
                    "sum_value": None,
                    "latest_value": None,
                    "final_dataset_field": metric_name,
                    "error": str(error_details["error_message"] or "metric_unavailable"),
                    "fallback_used": metric_fallback_used.get(metric_name, False),
                }
                continue

        logger.warning(
            "instagram_insights_metric_status",
            extra={
                "integration_id": integration.id,
                "instagram_selected_account_id": instagram_user_id,
                "selected_instagram_username": instagram_username,
                "metric_name_requested": metric_name,
                "metric_type": metric_type,
                "instagram_insights_metric_status": insight_payload.get("_meta_http_status_code"),
                "ig_insights_raw_body_truncated": insight_payload.get("_meta_raw_body"),
                "fallback_used": metric_fallback_used.get(metric_name, False),
            },
        )
        insight_rows = insight_payload.get("data")
        metric_row = insight_rows[0] if isinstance(insight_rows, list) and insight_rows else {}
        values = metric_row.get("values") if isinstance(metric_row, dict) else []
        (
            metric_total_value,
            metric_latest_value,
            metric_end_time,
            normalized_series,
            raw_values,
        ) = _normalize_instagram_insight_series(
            values if isinstance(values, list) else []
        )
        normalized_metrics[metric_name] = metric_total_value
        metric_series[metric_name] = normalized_series
        metric_end_times[metric_name] = metric_end_time
        metric_latest_values[metric_name] = metric_latest_value
        if metric_total_value is None:
            unavailable_metrics[metric_name] = "empty_response"
        final_dataset_field = {
            "reach": "reach",
            "impressions": "impressions",
            "views": "views",
            "total_interactions": "total_interactions",
            "accounts_engaged": "accounts_engaged",
            "content_interactions": "content_interactions",
            "profile_views": "profile_views",
            "website_clicks": "website_clicks",
        }.get(metric_name, metric_name)
        instagram_metric_audit[metric_name] = {
            "metric_name_requested": metric_name,
            "metric_type": metric_type,
            "graph_endpoint": f"/{instagram_user_id}/insights",
            "period": "day",
            "since": timeframe_config["since"],
            "until": timeframe_config["until"],
            "raw_values": raw_values,
            "sum_value": metric_total_value,
            "latest_value": metric_latest_value,
            "final_dataset_field": final_dataset_field,
            "fallback_used": metric_fallback_used.get(metric_name, False),
        }
        logger.warning(
            "instagram_metric_graph_audit",
            extra={
                "integration_id": integration.id,
                "instagram_selected_account_id": instagram_user_id,
                "metric_name_requested": metric_name,
                "metric_type": metric_type,
                "graph_endpoint": f"/{instagram_user_id}/insights",
                "period": "day",
                "since": timeframe_config["since"],
                "until": timeframe_config["until"],
                "raw_values": raw_values,
                "sum_value": metric_total_value,
                "latest_value": metric_latest_value,
                "final_dataset_field": final_dataset_field,
                "fallback_used": metric_fallback_used.get(metric_name, False),
            },
        )
        if is_engagement_metric:
            logger.warning(
                "engagement_status",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "raw_values": raw_values,
                    "sum_value": metric_total_value,
                    "engagement_final_value": metric_total_value,
                    "engagement_source_metric": metric_name,
                    "fallback_used": metric_fallback_used.get(metric_name, False),
                },
            )
            logger.warning(
                "instagram_engagement_metric_status",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "status": "success",
                    "instagram_insights_metric_status": insight_payload.get("_meta_http_status_code"),
                    "fallback_used": metric_fallback_used.get(metric_name, False),
                },
            )
            logger.warning(
                "instagram_engagement_raw_values",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "raw_values": raw_values,
                    "sum_value": metric_total_value,
                    "fallback_used": metric_fallback_used.get(metric_name, False),
                },
            )
            logger.warning(
                "instagram_engagement_final_value",
                extra={
                    "integration_id": integration.id,
                    "instagram_selected_account_id": instagram_user_id,
                    "metric_name_requested": metric_name,
                    "metric_type": metric_type,
                    "final_value": metric_total_value,
                    "fallback_used": metric_fallback_used.get(metric_name, False),
                },
            )

    normalized_summary = {
        "reach": normalized_metrics.get("reach"),
        "impressions": normalized_metrics.get("impressions"),
        "views": normalized_metrics.get("views"),
        "profile_views": normalized_metrics.get("profile_views"),
        "website_clicks": normalized_metrics.get("website_clicks"),
        "accounts_engaged": normalized_metrics.get("accounts_engaged"),
        "total_interactions": normalized_metrics.get("total_interactions"),
        "content_interactions": normalized_metrics.get("content_interactions"),
        "followers_count": followers_count,
        "unavailable_metrics": unavailable_metrics,
    }
    logger.warning(
        "Meta Instagram sync normalized metrics",
        extra={
            "integration_id": integration.id,
            "selected_instagram_account_id": instagram_user_id,
            "selected_instagram_username": instagram_username,
            "normalized_instagram_metrics": normalized_summary,
        },
    )

    reach_daily = metric_series.get("reach") or []
    impressions_daily = metric_series.get("impressions") or []
    views_daily = metric_series.get("views") or []
    engagement_source_metric = (
        "total_interactions"
        if normalized_metrics.get("total_interactions") is not None
        else "accounts_engaged"
        if normalized_metrics.get("accounts_engaged") is not None
        else "content_interactions"
        if normalized_metrics.get("content_interactions") is not None
        else None
    )
    engagement_raw_total = _first_non_none(
        normalized_metrics.get("total_interactions"),
        normalized_metrics.get("accounts_engaged"),
        normalized_metrics.get("content_interactions"),
    )
    engagement_error = _first_non_none(
        unavailable_metrics.get("total_interactions"),
        unavailable_metrics.get("accounts_engaged"),
        unavailable_metrics.get("content_interactions"),
    )
    interactions_daily = (
        metric_series.get("total_interactions")
        or metric_series.get("accounts_engaged")
        or metric_series.get("content_interactions")
        or []
    )
    daily_engagement = interactions_daily
    profile_views_daily = metric_series.get("profile_views") or []
    website_clicks_daily = metric_series.get("website_clicks") or []
    logger.warning(
        "engagement_finalized",
        extra={
            "integration_id": integration.id,
            "instagram_selected_account_id": instagram_user_id,
            "engagement_source_metric": engagement_source_metric,
            "engagement_raw_total": engagement_raw_total,
            "engagement_error": engagement_error,
            "engagement_final_value": engagement_raw_total,
            "engagement_metric_requested": "total_interactions",
            "engagement_metric_type": "total_value" if metric_fallback_used.get("total_interactions") else None,
            "fallback_used": metric_fallback_used.get("total_interactions", False),
        },
    )
    if engagement_raw_total is None and engagement_error:
        unavailable_metrics.setdefault("engagement", str(engagement_error))
    logger.warning(
        "instagram_engagement_unavailable_reason",
        extra={
            "integration_id": integration.id,
            "instagram_selected_account_id": instagram_user_id,
            "engagement_source_metric": engagement_source_metric,
            "engagement_final_value": engagement_raw_total,
            "engagement_unavailable_reason": engagement_error,
            "fallback_used": metric_fallback_used.get("total_interactions", False),
        },
    )

    csv_output = io.StringIO()
    writer = csv.DictWriter(
        csv_output,
        fieldnames=[
            "account_id",
            "account_name",
            "username",
            "followers",
            "reach",
            "impressions",
            "views",
            "engagement",
            "profile_views",
            "website_clicks",
            "accounts_engaged",
            "total_interactions",
            "content_interactions",
            "daily_engagement",
            "timeframe_preset",
            "timeframe_since",
            "timeframe_until",
            "reach_daily",
            "impressions_daily",
            "views_daily",
            "interactions_daily",
            "profile_views_daily",
            "website_clicks_daily",
            "unavailable_metrics",
        ],
    )
    writer.writeheader()
    writer.writerow(
        {
            "account_id": instagram_user_id,
            "account_name": account_name,
            "username": instagram_username,
            "followers": followers_count,
            "reach": normalized_metrics.get("reach"),
            "impressions": normalized_metrics.get("impressions"),
            "views": normalized_metrics.get("views"),
            "engagement": engagement_raw_total,
            "profile_views": normalized_metrics.get("profile_views"),
            "website_clicks": normalized_metrics.get("website_clicks"),
            "accounts_engaged": normalized_metrics.get("accounts_engaged"),
            "total_interactions": normalized_metrics.get("total_interactions"),
            "content_interactions": normalized_metrics.get("content_interactions"),
            "daily_engagement": json.dumps(daily_engagement),
            "timeframe_preset": timeframe_config["preset"],
            "timeframe_since": timeframe_config["since"],
            "timeframe_until": timeframe_config["until"],
            "reach_daily": json.dumps(reach_daily),
            "impressions_daily": json.dumps(impressions_daily),
            "views_daily": json.dumps(views_daily),
            "interactions_daily": json.dumps(interactions_daily),
            "profile_views_daily": json.dumps(profile_views_daily),
            "website_clicks_daily": json.dumps(website_clicks_daily),
            "unavailable_metrics": json.dumps(unavailable_metrics),
        }
    )

    csv_bytes = csv_output.getvalue().encode("utf-8")
    filename = f"meta_instagram_{instagram_user_id}_insights.csv"
    dataset_data = {
        "integration_type": "instagram_business",
        "page_name": account_name,
        "account_name": account_name,
        "username": instagram_username,
        "followers": followers_count,
        "followers_count": followers_count,
        "reach": normalized_metrics.get("reach"),
        "impressions": normalized_metrics.get("impressions"),
        "views": normalized_metrics.get("views"),
        "engagement": engagement_raw_total,
        "total_interactions": normalized_metrics.get("total_interactions"),
        "accounts_engaged": normalized_metrics.get("accounts_engaged"),
        "content_interactions": normalized_metrics.get("content_interactions"),
        "daily_engagement": daily_engagement,
        "profile_views": normalized_metrics.get("profile_views"),
        "website_clicks": normalized_metrics.get("website_clicks"),
        "profile_visits": normalized_metrics.get("profile_views"),
        "content_interactions": normalized_metrics.get("content_interactions"),
        "link_clicks": normalized_metrics.get("website_clicks"),
        "followers_growth": None,
        "instagram_metric_audit": {
            "reach_source_metric": "reach",
            "reach_raw_total": normalized_metrics.get("reach"),
            "engagement_source_metric": engagement_source_metric,
            "engagement_raw_total": engagement_raw_total,
            "engagement_error": engagement_error,
            "followers_source_metric": "followers_count",
            "unavailable_metrics": unavailable_metrics,
            "metrics": instagram_metric_audit,
        },
        "unavailable_metrics": unavailable_metrics,
        "timeframe": {
            "key": timeframe_config["key"],
            "label": timeframe_config["label"],
            "preset": timeframe_config["preset"],
            "since": timeframe_config["since"],
            "until": timeframe_config["until"],
            "requested_since": timeframe_config.get("requested_since"),
            "requested_until": timeframe_config.get("requested_until"),
            "current_since": timeframe_config.get("current_since"),
            "current_until": timeframe_config.get("current_until"),
            "previous_since": timeframe_config.get("previous_since"),
            "previous_until": timeframe_config.get("previous_until"),
            "selected_timeframe": timeframe_config.get("selected_timeframe"),
        },
        "reach_source_metric": "reach",
        "impressions_source_metric": _first_non_none(
            "impressions" if normalized_metrics.get("impressions") is not None else None,
            "views" if normalized_metrics.get("views") is not None else None,
        ),
        "impressions_daily": impressions_daily,
        "reach_daily": reach_daily,
        "recent_posts": [],
        "report_metric_mapping": {
            "views": _build_meta_report_metric_entry(
                facebook_ui_target_label="Visualizaciones",
                source_metric_name=_first_non_none(
                    "views" if normalized_metrics.get("views") is not None else None,
                    "profile_views" if normalized_metrics.get("profile_views") is not None else None,
                ),
                total=_first_non_none(
                    normalized_metrics.get("views"),
                    normalized_metrics.get("profile_views"),
                ),
                daily_series=views_daily or profile_views_daily,
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
            "viewers": _build_meta_report_metric_entry(
                facebook_ui_target_label="Espectadores",
                source_metric_name="reach",
                total=normalized_metrics.get("reach"),
                daily_series=reach_daily,
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
            "interactions": _build_meta_report_metric_entry(
                facebook_ui_target_label="Interacciones con el contenido",
                source_metric_name=_first_non_none(
                    "total_interactions" if normalized_metrics.get("total_interactions") is not None else None,
                    "accounts_engaged" if normalized_metrics.get("accounts_engaged") is not None else None,
                    "content_interactions" if normalized_metrics.get("content_interactions") is not None else None,
                ),
                total=_first_non_none(
                    normalized_metrics.get("total_interactions"),
                    normalized_metrics.get("accounts_engaged"),
                    normalized_metrics.get("content_interactions"),
                ),
                daily_series=interactions_daily,
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
            "link_clicks": _build_meta_report_metric_entry(
                facebook_ui_target_label="Clics en el enlace",
                source_metric_name="website_clicks",
                total=normalized_metrics.get("website_clicks"),
                daily_series=website_clicks_daily,
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
            "page_visits": _build_meta_report_metric_entry(
                facebook_ui_target_label="Visitas",
                source_metric_name="profile_views",
                total=normalized_metrics.get("profile_views"),
                daily_series=profile_views_daily,
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
            "followers_growth": _build_meta_report_metric_entry(
                facebook_ui_target_label="Seguidores",
                source_metric_name="followers_count",
                total=followers_count,
                daily_series=[],
                timeframe_since=timeframe_config["since"],
                timeframe_until=timeframe_config["until"],
            ),
        },
        "normalized_report_metrics": {
            "impressions_total": normalized_metrics.get("impressions"),
            "impressions_daily": impressions_daily,
            "views_total": _first_non_none(
                normalized_metrics.get("views"),
                normalized_metrics.get("impressions"),
                normalized_metrics.get("profile_views"),
            ),
            "views_daily": views_daily or impressions_daily or profile_views_daily,
            "viewers_total": normalized_metrics.get("reach"),
            "viewers_daily": reach_daily,
            "interactions_total": _first_non_none(
                normalized_metrics.get("total_interactions"),
                normalized_metrics.get("accounts_engaged"),
                normalized_metrics.get("content_interactions"),
            ),
            "interactions_daily": daily_engagement,
            "link_clicks_total": normalized_metrics.get("website_clicks"),
            "link_clicks_daily": website_clicks_daily,
            "page_visits_total": normalized_metrics.get("profile_views"),
            "page_visits_daily": profile_views_daily,
            "followers_growth_total": followers_count,
            "followers_growth_daily": [],
            "requested_since": timeframe_config.get("requested_since"),
            "requested_until": timeframe_config.get("requested_until"),
            "timeframe_since": timeframe_config["since"],
            "timeframe_until": timeframe_config["until"],
            "current_since": timeframe_config.get("current_since"),
            "current_until": timeframe_config.get("current_until"),
            "previous_since": timeframe_config.get("previous_since"),
            "previous_until": timeframe_config.get("previous_until"),
        },
    }
    logger.warning(
        "instagram_dataset_final_keys",
        extra={
            "integration_id": integration.id,
            "instagram_selected_account_id": instagram_user_id,
            "instagram_dataset_final_keys": sorted(dataset_data.keys()),
        },
    )
    logger.warning(
        "instagram_normalized_report_metrics",
        extra={
            "integration_id": integration.id,
            "instagram_selected_account_id": instagram_user_id,
            "instagram_normalized_report_metrics": dataset_data.get("normalized_report_metrics"),
        },
    )

    dataset = Dataset(
        workspace_id=integration.workspace_id,
        name=filename,
        description="Meta Instagram insights",
        data=dataset_data,
    )
    _enforce_workspace_storage_for_upload(db, integration.workspace_id, len(csv_bytes))
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    key = f"workspaces/{integration.workspace_id}/datasets/{dataset.id}/{filename}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        s3.put_object(Bucket=settings.s3_inputs_bucket, Key=key, Body=csv_bytes)
    except Exception:
        db.delete(dataset)
        db.commit()
        raise http_error(502, "s3_upload_failed", "Failed to upload file.")

    dataset_file = DatasetFile(
        dataset_id=dataset.id,
        workspace_id=integration.workspace_id,
        s3_key=key,
        size_bytes=len(csv_bytes),
        content_type="text/csv",
    )
    db.add(dataset_file)
    db.commit()
    db.refresh(dataset_file)

    logger.warning(
        "Meta Instagram sync completed",
        extra={
            "integration_id": integration.id,
            "selected_instagram_account_id": instagram_user_id,
            "selected_instagram_username": instagram_username,
            "dataset_id": dataset.id,
            "dataset_file_id": dataset_file.id,
            "normalized_instagram_metrics": normalized_summary,
        },
    )
    return MetaPagesSyncOut(
        integration_id=integration.id,
        dataset_id=dataset.id,
        dataset_file_id=dataset_file.id,
        page_id=instagram_user_id,
        page_name=account_name,
        status="uploaded",
        timeframe=dataset_data["timeframe"],
    )


@app.post("/integrations/meta/sync-pages", response_model=MetaPagesSyncOut)
def meta_sync_pages(
    request: Request,
    integration_id: int | None = None,
    timeframe: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    payload: dict | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPagesSyncOut:
    raw_body = payload if isinstance(payload, dict) else {}
    raw_query_params = dict(request.query_params)
    body_timeframe_selection = raw_body.get("timeframe_selection")
    if not isinstance(body_timeframe_selection, dict):
        body_timeframe_selection = raw_body.get("timeframeSelection")
    if not isinstance(body_timeframe_selection, dict):
        body_timeframe_selection = {}

    def _body_timeframe_key(value: object) -> object:
        if isinstance(value, dict):
            return (
                value.get("key")
                or value.get("timeframe")
                or value.get("value")
                or value.get("preset")
            )
        return value

    body_integration_id = raw_body.get("integration_id") or raw_body.get("integrationId")
    body_page_id = raw_body.get("page_id") or raw_body.get("pageId")
    body_timeframe = (
        _body_timeframe_key(raw_body.get("timeframe"))
        or _body_timeframe_key(body_timeframe_selection)
    )
    body_start_date = (
        raw_body.get("start_date")
        or raw_body.get("startDate")
        or body_timeframe_selection.get("start_date")
        or body_timeframe_selection.get("startDate")
    )
    body_end_date = (
        raw_body.get("end_date")
        or raw_body.get("endDate")
        or body_timeframe_selection.get("end_date")
        or body_timeframe_selection.get("endDate")
    )
    final_integration_id = body_integration_id if body_integration_id is not None else integration_id
    if final_integration_id is None:
        raise http_error(422, "missing_integration_id", "integration_id is required.")
    try:
        final_integration_id = int(final_integration_id)
    except (TypeError, ValueError):
        raise http_error(422, "invalid_integration_id", "integration_id must be an integer.")

    final_timeframe = str(body_timeframe or timeframe or "last_28_days").strip()
    if not final_timeframe:
        final_timeframe = "last_28_days"
    final_start_date = (
        str(body_start_date)
        if body_start_date is not None
        else start_date
    )
    final_end_date = (
        str(body_end_date)
        if body_end_date is not None
        else end_date
    )
    try:
        integration = _get_meta_integration(db, current_user, final_integration_id)
        selected_page = None
        if body_page_id:
            selected_page = (
                db.query(IntegrationAccount)
                .filter(
                    IntegrationAccount.integration_id == integration.id,
                    IntegrationAccount.external_account_id == _meta_page_account_external_id(str(body_page_id)),
                )
                .first()
            )
            if not selected_page:
                raise http_error(
                    400,
                    "meta_page_not_selected",
                    "Requested Meta page is not selected. Call POST /integrations/meta/select-page first.",
                )
        if not selected_page:
            selected_page = _get_selected_meta_page(db, integration.id)
        if not selected_page:
            raise http_error(
                400,
                "meta_page_not_selected",
                "No Meta page selected. Call POST /integrations/meta/select-page first.",
            )

        page_id = _get_meta_page_id(selected_page)
        page_name = selected_page.display_name or page_id
        selected_meta_record = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration.id,
                MetaPage.page_id == page_id,
            )
            .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
            .first()
        )
        logger.info(
            "[MetaTimeframeBackend][sync.entry]",
            extra={
                "raw_query_params": raw_query_params,
                "raw_body": raw_body,
                "integration_id_final": integration.id,
                "page_id_final": page_id,
                "selected_record_type": selected_meta_record.record_type if selected_meta_record else None,
                "timeframe_final_before_resolve": final_timeframe,
                "start_date_final": final_start_date,
                "end_date_final": final_end_date,
            },
        )
        timeframe_config = resolve_meta_pages_timeframe(
            final_timeframe,
            start_date=final_start_date,
            end_date=final_end_date,
        )
        logger.info(
            "[MetaTimeframeBackend][sync.resolved]",
            extra={
                "integration_id": integration.id,
                "timeframe_key": timeframe_config.get("key"),
                "selected_timeframe": timeframe_config.get("selected_timeframe"),
                "preset": timeframe_config.get("preset"),
                "requested_since": timeframe_config.get("requested_since"),
                "requested_until": timeframe_config.get("requested_until"),
                "current_since": timeframe_config.get("current_since"),
                "current_until": timeframe_config.get("current_until"),
                "previous_since": timeframe_config.get("previous_since"),
                "previous_until": timeframe_config.get("previous_until"),
                "since": timeframe_config.get("since"),
                "until": timeframe_config.get("until"),
            },
        )
        if selected_meta_record and selected_meta_record.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT:
            return _sync_meta_instagram_account(
                db=db,
                integration=integration,
                selected_page=selected_page,
                selected_meta_record=selected_meta_record,
                timeframe_config=timeframe_config,
                current_user=current_user,
            )
        logger.info(
            "Meta Pages sync started",
            extra={
                "integration_id": integration.id,
                "page_id": page_id,
                "timeframe": final_timeframe,
            },
        )
        access_token: str | None = None
        try:
            access_token = _get_meta_page_access_token(db, integration, selected_page)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if not _is_meta_api_error(exc) and detail.get("code") != "missing_token":
                raise
        logger.info(
            "Meta Pages sync token resolved",
            extra={
                "integration_id": integration.id,
                "page_id": page_id,
                "has_page_token": bool(access_token),
            },
        )

        page_counts: dict = {}
        insights: dict = {}
        posts: list[dict] = []
        reach_daily: list[dict] = []
        impressions_daily: list[dict] = []
        views_daily: list[dict] = []
        interactions_daily: list[dict] = []
        link_clicks_daily: list[dict] = []
        page_visits_daily: list[dict] = []
        followers_growth_daily: list[dict] = []
        reach_metric_name: str | None = None
        impressions_metric_name: str | None = None
        views_metric_name: str | None = None
        interactions_metric_name: str | None = None
        link_clicks_metric_name: str | None = None
        page_visits_metric_name: str | None = None
        followers_growth_metric_name: str | None = None

        if access_token:
            try:
                _refresh_meta_pages_authorized_cache(
                    db,
                    integration,
                    access_token,
                    user_id=current_user.id,
                    return_empty_on_error=True,
                )
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise
                logger.warning(
                    "Meta Pages cache refresh failed during sync",
                    extra={"integration_id": integration.id, "error": str(exc.detail)},
                )

            try:
                page_info = fetch_page_info(access_token, page_id, fields="id,name")
                page_name = str(page_info.get("name") or page_name)
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise

            try:
                page_counts = fetch_page_info(
                    access_token,
                    page_id,
                    fields="fan_count,followers_count",
                )
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise

            accepted_metrics: list[str] = []
            rejected_metrics: list[str] = []
            reach_payload = _fetch_meta_pages_reach_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
            )
            reach_metric_name = (
                str(reach_payload["metric_name"]) if reach_payload.get("metric_name") else None
            )
            reach_daily = (
                list(reach_payload["reach_daily"])
                if isinstance(reach_payload.get("reach_daily"), list)
                else []
            )
            reach_daily = _expand_meta_daily_series(
                reach_daily,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=reach_metric_name or "page_reach",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=reach_daily,
            )
            if reach_metric_name:
                accepted_metrics.append(reach_metric_name)
                insights["page_reach"] = reach_payload.get("value")
                insights["page_reach_end_time"] = reach_payload.get("end_time")
            else:
                logger.warning(
                    "Meta Pages reach unavailable after trying all candidates",
                    extra={
                        "integration_id": integration.id,
                        "page_id": page_id,
                        "timeframe": timeframe_config["preset"],
                        "metric_candidates": META_PAGES_REACH_METRIC_CANDIDATES,
                    },
                )
            impressions_payload = _fetch_meta_pages_impressions_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
            )
            impressions_metric_name = (
                str(impressions_payload["metric_name"])
                if impressions_payload.get("metric_name")
                else None
            )
            impressions_daily = (
                list(impressions_payload["impressions_daily"])
                if isinstance(impressions_payload.get("impressions_daily"), list)
                else []
            )
            impressions_daily = _expand_meta_daily_series(
                impressions_daily,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=impressions_metric_name or "page_impressions",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=impressions_daily,
            )
            if impressions_metric_name:
                accepted_metrics.append(impressions_metric_name)
                insights["page_impressions"] = impressions_payload.get("value")
                insights["page_impressions_end_time"] = impressions_payload.get("end_time")
            else:
                logger.warning(
                    "Meta Pages impressions unavailable after trying all candidates",
                    extra={
                        "integration_id": integration.id,
                        "page_id": page_id,
                        "timeframe": timeframe_config["preset"],
                        "metric_candidates": META_PAGES_IMPRESSIONS_METRIC_CANDIDATES,
                    },
                )
            views_payload = _fetch_meta_pages_metric_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
                metric_name="page_views_total",
                label="Visualizaciones",
                daily_key="views_daily",
            )
            views_metric_name = str(views_payload.get("metric_name") or "") or None
            views_daily = _expand_meta_daily_series(
                list(views_payload.get("views_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=views_metric_name or "page_views_total",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=views_daily,
            )
            insights["report_views_total"] = _sum_meta_daily_series(views_daily)
            insights["report_views_end_time"] = views_payload.get("end_time")

            interactions_payload = _fetch_meta_pages_metric_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
                metric_name="page_post_engagements",
                label="Interacciones con el contenido",
                daily_key="interactions_daily",
            )
            interactions_metric_name = str(interactions_payload.get("metric_name") or "") or None
            interactions_daily = _expand_meta_daily_series(
                list(interactions_payload.get("interactions_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=interactions_metric_name or "page_post_engagements",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=interactions_daily,
            )
            insights["report_interactions_total"] = _sum_meta_daily_series(interactions_daily)
            insights["report_interactions_end_time"] = interactions_payload.get("end_time")

            link_clicks_payload = _fetch_meta_pages_metric_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
                metric_name="page_consumptions",
                label="Clics en el enlace",
                daily_key="link_clicks_daily",
            )
            link_clicks_metric_name = str(link_clicks_payload.get("metric_name") or "") or None
            link_clicks_daily = _expand_meta_daily_series(
                list(link_clicks_payload.get("link_clicks_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=link_clicks_metric_name or "page_consumptions",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=link_clicks_daily,
            )
            insights["report_link_clicks_total"] = _sum_meta_daily_series(link_clicks_daily)
            insights["report_link_clicks_end_time"] = link_clicks_payload.get("end_time")

            page_visits_payload = _fetch_meta_pages_metric_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
                metric_name="page_profile_views",
                label="Visitas",
                daily_key="page_visits_daily",
            )
            page_visits_metric_name = str(page_visits_payload.get("metric_name") or "") or None
            page_visits_daily = _expand_meta_daily_series(
                list(page_visits_payload.get("page_visits_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=page_visits_metric_name or "page_profile_views",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=page_visits_daily,
            )
            insights["report_page_visits_total"] = _sum_meta_daily_series(page_visits_daily)
            insights["report_page_visits_end_time"] = page_visits_payload.get("end_time")

            followers_growth_payload = _fetch_meta_pages_metric_payload(
                access_token,
                page_id,
                timeframe_config,
                integration.id,
                metric_name="page_fan_adds",
                label="Seguidores",
                daily_key="followers_growth_daily",
            )
            followers_growth_metric_name = str(followers_growth_payload.get("metric_name") or "") or None
            followers_growth_daily = _expand_meta_daily_series(
                list(followers_growth_payload.get("followers_growth_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=followers_growth_metric_name or "page_fan_adds",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=followers_growth_daily,
            )
            insights["report_followers_growth_total"] = _sum_meta_daily_series(followers_growth_daily)
            insights["report_followers_growth_end_time"] = followers_growth_payload.get("end_time")
            for metric_name in [
                "page_engaged_users",
                "page_profile_views",
                "page_post_engagements",
                "page_fan_adds",
                "page_fans",
                "page_consumptions",
                "page_consumptions_unique",
            ]:
                try:
                    metric_insight = fetch_page_insights(
                        access_token,
                        page_id,
                        metrics=[metric_name],
                        since=timeframe_config["since"],
                        until=timeframe_config["until"],
                    )
                except HTTPException as exc:
                    if not _is_meta_api_error(exc):
                        raise
                    rejected_metrics.append(metric_name)
                    continue

                if metric_name in metric_insight:
                    accepted_metrics.append(metric_name)
                    insights[metric_name] = metric_insight.get(metric_name)
                    insights[f"{metric_name}_end_time"] = metric_insight.get(
                        f"{metric_name}_end_time"
                    )
                else:
                    rejected_metrics.append(metric_name)

            logger.info(
                "Meta Pages insights fetch completed",
                extra={
                    "integration_id": integration.id,
                    "page_id": page_id,
                    "accepted_metrics": accepted_metrics,
                    "rejected_metrics": rejected_metrics,
                    "reach_source_metric": reach_metric_name,
                    "reach_daily_points": len(reach_daily),
                    "impressions_source_metric": impressions_metric_name,
                    "impressions_daily_points": len(impressions_daily),
                    "views_source_metric": views_metric_name,
                    "views_daily_points": len(views_daily),
                    "timeframe_days": (
                        date.fromisoformat(timeframe_config["until"])
                        - date.fromisoformat(timeframe_config["since"])
                    ).days
                    + 1,
                },
            )

            try:
                posts = fetch_page_posts(access_token, page_id, limit=5)
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise

            enriched_posts: list[dict] = []
            posts_found = len(posts)
            for post in posts:
                post_id = str(post.get("id") or "")
                if not post_id:
                    continue
                shares = post.get("shares")
                post_payload = {
                    "id": post_id,
                    "message": post.get("message"),
                    "created_time": post.get("created_time"),
                    "permalink_url": post.get("permalink_url"),
                    "reach": None,
                    "reactions": _extract_summary_total(post.get("reactions")),
                    "comments": _extract_summary_total(post.get("comments")),
                    "shares": shares.get("count") if isinstance(shares, dict) else None,
                    "saves": None,
                }
                try:
                    post_metrics = fetch_post_metrics(access_token, post_id)
                    post_payload["reach"] = post_metrics.get("post_impressions")
                except HTTPException as exc:
                    if not _is_meta_api_error(exc):
                        raise
                enriched_posts.append(post_payload)
            posts = enriched_posts
            logger.info(
                "Meta Pages posts fetch completed",
                extra={
                    "integration_id": integration.id,
                    "page_id": page_id,
                    "posts_found": posts_found,
                    "posts_enriched": len(enriched_posts),
                },
            )

        csv_output = io.StringIO()
        writer = csv.DictWriter(
            csv_output,
            fieldnames=[
                "page_id",
                "page_name",
                "fans",
                "followers",
                "impressions",
                "impressions_date",
                "reach",
                "reach_date",
                "engagement",
                "engagement_date",
                "profile_visits",
                "content_interactions",
                "link_clicks",
                "followers_growth",
                "timeframe_preset",
                "timeframe_since",
                "timeframe_until",
                "reach_source_metric",
                "impressions_source_metric",
                "impressions_daily",
                "reach_daily",
                "recent_posts",
            ],
        )
        writer.writeheader()
        normalized_posts = normalize_meta_recent_posts(posts)
        followers = page_counts.get("followers_count")
        impressions = insights.get("page_impressions")
        reach = insights.get("page_reach")
        engagement = insights.get("page_post_engagements")
        profile_visits = _first_non_none(
            insights.get("page_profile_views"),
            insights.get("page_engaged_users"),
        )
        content_interactions = _first_non_none(
            insights.get("page_engaged_users"),
            insights.get("page_post_engagements"),
        )
        link_clicks = _first_non_none(
            insights.get("page_consumptions"),
            insights.get("page_consumptions_unique"),
            insights.get("page_engaged_users"),
        )
        followers_growth = insights.get("page_fan_adds")
        missing_metrics = [
            metric_name
            for metric_name, metric_value in {
                "impressions": impressions,
                "link_clicks": link_clicks,
                "profile_visits": profile_visits,
                "followers_growth": followers_growth,
            }.items()
            if metric_value is None
        ]
        logger.info(
            "Meta Pages sync normalized metrics",
            extra={
                "integration_id": integration.id,
                "page_id": page_id,
                "missing_metrics": missing_metrics,
            },
        )
        print("REACH:", reach)
        print("IMPRESSIONS:", impressions)
        writer.writerow(
            {
                "page_id": page_id,
                "page_name": page_name,
                "fans": _first_non_none(page_counts.get("fan_count"), insights.get("page_fans")),
                "followers": followers,
                "impressions": impressions,
                "impressions_date": insights.get("page_impressions_end_time"),
                "reach": reach,
                "reach_date": insights.get("page_reach_end_time"),
                "engagement": engagement,
                "engagement_date": insights.get("page_post_engagements_end_time"),
                "profile_visits": profile_visits,
                "content_interactions": content_interactions,
                "link_clicks": link_clicks,
                "followers_growth": followers_growth,
                "timeframe_preset": timeframe_config["preset"],
                "timeframe_since": timeframe_config["since"],
                "timeframe_until": timeframe_config["until"],
                "reach_source_metric": reach_metric_name,
                "impressions_source_metric": impressions_metric_name,
                "impressions_daily": json.dumps(impressions_daily),
                "reach_daily": json.dumps(reach_daily),
                "recent_posts": json.dumps(normalized_posts),
            }
        )

        csv_bytes = csv_output.getvalue().encode("utf-8")
        filename = f"meta_page_{page_id}_insights.csv"
        dataset_data = {
            "page_name": page_name,
            "followers": followers,
            "reach": reach,
            "engagement": engagement,
            "impressions": impressions,
            "profile_visits": profile_visits,
            "content_interactions": content_interactions,
            "link_clicks": link_clicks,
            "followers_growth": followers_growth,
            "timeframe": {
                "key": timeframe_config["key"],
                "label": timeframe_config["label"],
                "preset": timeframe_config["preset"],
                "since": timeframe_config["since"],
                "until": timeframe_config["until"],
                "requested_since": timeframe_config.get("requested_since"),
                "requested_until": timeframe_config.get("requested_until"),
                "current_since": timeframe_config.get("current_since"),
                "current_until": timeframe_config.get("current_until"),
                "previous_since": timeframe_config.get("previous_since"),
                "previous_until": timeframe_config.get("previous_until"),
                "selected_timeframe": timeframe_config.get("selected_timeframe"),
            },
            "reach_source_metric": reach_metric_name,
            "impressions_source_metric": impressions_metric_name,
            "impressions_daily": impressions_daily,
            "reach_daily": reach_daily,
            "report_metric_mapping": {
                "views": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Visualizaciones",
                    source_metric_name=views_metric_name,
                    total=insights.get("report_views_total"),
                    daily_series=views_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "viewers": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Espectadores",
                    source_metric_name=reach_metric_name,
                    total=reach,
                    daily_series=reach_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "interactions": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Interacciones con el contenido",
                    source_metric_name=interactions_metric_name,
                    total=insights.get("report_interactions_total"),
                    daily_series=interactions_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "link_clicks": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Clics en el enlace",
                    source_metric_name=link_clicks_metric_name,
                    total=insights.get("report_link_clicks_total"),
                    daily_series=link_clicks_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "page_visits": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Visitas",
                    source_metric_name=page_visits_metric_name,
                    total=insights.get("report_page_visits_total"),
                    daily_series=page_visits_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "followers_growth": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Seguidores",
                    source_metric_name=followers_growth_metric_name,
                    total=insights.get("report_followers_growth_total"),
                    daily_series=followers_growth_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
            },
            "normalized_report_metrics": {
                "impressions_total": impressions,
                "impressions_daily": impressions_daily,
                "views_total": insights.get("report_views_total"),
                "views_daily": views_daily,
                "viewers_total": reach,
                "viewers_daily": reach_daily,
                "interactions_total": insights.get("report_interactions_total"),
                "interactions_daily": interactions_daily,
                "link_clicks_total": insights.get("report_link_clicks_total"),
                "link_clicks_daily": link_clicks_daily,
                "page_visits_total": insights.get("report_page_visits_total"),
                "page_visits_daily": page_visits_daily,
                "followers_growth_total": insights.get("report_followers_growth_total"),
                "followers_growth_daily": followers_growth_daily,
                "requested_since": timeframe_config.get("requested_since"),
                "requested_until": timeframe_config.get("requested_until"),
                "timeframe_since": timeframe_config["since"],
                "timeframe_until": timeframe_config["until"],
                "current_since": timeframe_config.get("current_since"),
                "current_until": timeframe_config.get("current_until"),
                "previous_since": timeframe_config.get("previous_since"),
                "previous_until": timeframe_config.get("previous_until"),
            },
            "recent_posts": normalized_posts,
        }
        reach_first_date, reach_last_date = _meta_daily_series_bounds(reach_daily)
        impressions_first_date, impressions_last_date = _meta_daily_series_bounds(impressions_daily)
        logger.info(
            "[MetaTimeframeBackend][sync.dataset.before_save]",
            extra={
                "reach_daily_points": len(reach_daily),
                "impressions_daily_points": len(impressions_daily),
                "reach_first_date": reach_first_date,
                "reach_last_date": reach_last_date,
                "impressions_first_date": impressions_first_date,
                "impressions_last_date": impressions_last_date,
                "dataset_timeframe_to_save": dataset_data["timeframe"],
            },
        )
        for metric_name, metric_points in {
            "dataset.reach_daily": reach_daily,
            "dataset.impressions_daily": impressions_daily,
            "dataset.views_daily": views_daily,
            "dataset.interactions_daily": interactions_daily,
            "dataset.link_clicks_daily": link_clicks_daily,
            "dataset.page_visits_daily": page_visits_daily,
            "dataset.followers_growth_daily": followers_growth_daily,
        }.items():
            _log_meta_history_audit(
                page_id=page_id,
                page_name=page_name,
                metric_name=metric_name,
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=metric_points,
            )
        raw_meta_data = {
            "page_id": page_id,
            "timeframe": timeframe_config,
            "reach": {
                "metric": reach_metric_name,
                "total": reach,
                "daily": reach_daily,
            },
            "impressions": {
                "metric": impressions_metric_name,
                "total": impressions,
                "daily": impressions_daily,
            },
        }
        print("RAW META DATA:", raw_meta_data)
        logger.info(
            "[MetaTimeframeBackend] sync metrics resolved",
            extra={
                "page_id": page_id,
                "page_name": page_name,
                "timeframe_resolved_key": timeframe_config["key"],
                "timeframe_resolved_since": timeframe_config["since"],
                "timeframe_resolved_until": timeframe_config["until"],
                "resolved_metrics": [
                    key
                    for key, value in dataset_data.items()
                    if key not in {"recent_posts", "timeframe"} and value is not None
                ],
                "posts_processed": len(normalized_posts),
                "reach_source_metric": reach_metric_name,
                "reach_daily_points": len(reach_daily),
                "reach_daily_first_date": reach_first_date,
                "reach_daily_last_date": reach_last_date,
                "reach_daily_total": sum(
                    int(point.get("value"))
                    for point in reach_daily
                    if isinstance(point, dict) and isinstance(point.get("value"), (int, float))
                ),
                "impressions_source_metric": impressions_metric_name,
                "impressions_daily_points": len(impressions_daily),
                "impressions_daily_first_date": impressions_first_date,
                "impressions_daily_last_date": impressions_last_date,
                "impressions_daily_total": sum(
                    int(point.get("value"))
                    for point in impressions_daily
                    if isinstance(point, dict) and isinstance(point.get("value"), (int, float))
                ),
            },
        )
        dataset = Dataset(
            workspace_id=integration.workspace_id,
            name=filename,
            description="Meta Pages insights",
            data=dataset_data,
        )
        _enforce_workspace_storage_for_upload(db, integration.workspace_id, len(csv_bytes))
        db.add(dataset)
        db.commit()
        db.refresh(dataset)
        saved_dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        saved_dataset_timeframe = (
            saved_dataset_data.get("timeframe")
            if isinstance(saved_dataset_data.get("timeframe"), dict)
            else {}
        )
        saved_reach_daily = (
            saved_dataset_data.get("reach_daily")
            if isinstance(saved_dataset_data.get("reach_daily"), list)
            else []
        )
        saved_impressions_daily = (
            saved_dataset_data.get("impressions_daily")
            if isinstance(saved_dataset_data.get("impressions_daily"), list)
            else []
        )
        logger.info(
            "[MetaTimeframeBackend][sync.dataset.saved]",
            extra={
                "dataset_id": dataset.id,
                "dataset_timeframe_key": saved_dataset_timeframe.get("key"),
                "dataset_timeframe_since": saved_dataset_timeframe.get("since"),
                "dataset_timeframe_until": saved_dataset_timeframe.get("until"),
                "reach_daily_length": len(saved_reach_daily),
                "impressions_daily_length": len(saved_impressions_daily),
            },
        )
        logger.info(
            "[MetaTimeframeBackend] dataset created",
            extra={
                "integration_id": integration.id,
                "page_id": page_id,
                "dataset_id": dataset.id,
                "dataset_timeframe_key": dataset.data.get("timeframe", {}).get("key")
                if isinstance(dataset.data, dict)
                and isinstance(dataset.data.get("timeframe"), dict)
                else None,
                "dataset_timeframe_saved": dataset.data.get("timeframe")
                if isinstance(dataset.data, dict)
                else None,
                "reach_daily_points_saved": len(reach_daily),
                "reach_daily_first_date_saved": reach_first_date,
                "reach_daily_last_date_saved": reach_last_date,
                "impressions_daily_points_saved": len(impressions_daily),
                "impressions_daily_first_date_saved": impressions_first_date,
                "impressions_daily_last_date_saved": impressions_last_date,
            },
        )

        key = f"workspaces/{integration.workspace_id}/datasets/{dataset.id}/{filename}"
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        try:
            s3.put_object(Bucket=settings.s3_inputs_bucket, Key=key, Body=csv_bytes)
        except ClientError as exc:
            error_response = getattr(exc, "response", {}) if hasattr(exc, "response") else {}
            error_details = error_response.get("Error", {}) if isinstance(error_response, dict) else {}
            metadata = error_response.get("ResponseMetadata", {}) if isinstance(error_response, dict) else {}
            logger.error(
                "Meta Pages sync S3 upload failed",
                extra={
                    "bucket": settings.s3_inputs_bucket,
                    "key": key,
                    "aws_region": settings.aws_region,
                    "exception_class": exc.__class__.__name__,
                    "error_code": error_details.get("Code"),
                    "error_message": error_details.get("Message"),
                    "http_status_code": metadata.get("HTTPStatusCode"),
                    "request_id": metadata.get("RequestId"),
                },
            )
            db.delete(dataset)
            db.commit()
            raise http_error(502, "s3_upload_failed", "Failed to upload file.")
        except NoCredentialsError as exc:
            logger.error(
                "Meta Pages sync S3 upload failed: AWS credentials missing",
                extra={
                    "bucket": settings.s3_inputs_bucket,
                    "key": key,
                    "aws_region": settings.aws_region,
                    "exception_class": exc.__class__.__name__,
                },
            )
            db.delete(dataset)
            db.commit()
            raise http_error(502, "s3_upload_failed", "Failed to upload file.")
        except PartialCredentialsError as exc:
            logger.error(
                "Meta Pages sync S3 upload failed: AWS credentials incomplete",
                extra={
                    "bucket": settings.s3_inputs_bucket,
                    "key": key,
                    "aws_region": settings.aws_region,
                    "exception_class": exc.__class__.__name__,
                },
            )
            db.delete(dataset)
            db.commit()
            raise http_error(502, "s3_upload_failed", "Failed to upload file.")
        except Exception as exc:
            logger.error(
                "Meta Pages sync S3 upload failed",
                extra={
                    "bucket": settings.s3_inputs_bucket,
                    "key": key,
                    "aws_region": settings.aws_region,
                    "exception_class": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )
            db.delete(dataset)
            db.commit()
            raise http_error(502, "s3_upload_failed", "Failed to upload file.")

        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            workspace_id=integration.workspace_id,
            s3_key=key,
            size_bytes=len(csv_bytes),
            content_type="text/csv",
        )
        db.add(dataset_file)
        db.commit()
        db.refresh(dataset_file)
        logger.info(
            "[MetaTimeframeBackend] sync completed",
            extra={
                "integration_id": integration.id,
                "page_id": page_id,
                "dataset_id": dataset.id,
                "dataset_file_id": dataset_file.id,
                "dataset_timeframe_key": dataset_data["timeframe"].get("key"),
                "dataset_timeframe_saved": dataset_data["timeframe"],
                "reach_daily_points_saved": len(reach_daily),
                "impressions_daily_points_saved": len(impressions_daily),
            },
        )

        return MetaPagesSyncOut(
            integration_id=integration.id,
            dataset_id=dataset.id,
            dataset_file_id=dataset_file.id,
            page_id=page_id,
            page_name=page_name,
            status="uploaded",
            timeframe=dataset_data["timeframe"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Meta Pages sync failed with unhandled exception",
            extra={
                "integration_id": final_integration_id if "final_integration_id" in locals() else integration_id,
                "timeframe": final_timeframe if "final_timeframe" in locals() else timeframe,
            },
        )
        raise http_error(500, "meta_pages_sync_failed", f"Meta Pages sync failed: {exc}")


@app.get("/debug/meta-raw")
def debug_meta_raw(
    integration_id: int,
    timeframe: str = "last_28_days",
    start_date: str | None = None,
    end_date: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, integration_id)
    timeframe_config = resolve_meta_pages_timeframe(
        timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    selected_page = _get_selected_meta_page(db, integration.id)
    if not selected_page:
        raise http_error(
            400,
            "meta_page_not_selected",
            "No Meta page selected. Call POST /integrations/meta/select-page first.",
        )

    page_id = _get_meta_page_id(selected_page)
    access_token = _get_meta_page_access_token(db, integration, selected_page)
    reach_payload = _fetch_meta_pages_reach_payload(
        access_token,
        page_id,
        timeframe_config,
        integration.id,
    )
    impressions_payload = _fetch_meta_pages_impressions_payload(
        access_token,
        page_id,
        timeframe_config,
        integration.id,
    )
    raw_payload = {
        "integration_id": integration.id,
        "page_id": page_id,
        "timeframe": timeframe_config,
        "reach": reach_payload,
        "impressions": impressions_payload,
    }
    print("RAW META DATA:", raw_payload)
    return raw_payload


@app.get("/debug/meta-report-metrics")
def debug_meta_report_metrics(
    integration_id: int,
    timeframe: str = "last_28_days",
    start_date: str | None = None,
    end_date: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    integration = _get_meta_integration(db, current_user, integration_id)
    timeframe_config = resolve_meta_pages_timeframe(
        timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    selected_page = _get_selected_meta_page(db, integration.id)
    if not selected_page:
        raise http_error(
            400,
            "meta_page_not_selected",
            "No Meta page selected. Call POST /integrations/meta/select-page first.",
        )
    page_id = _get_meta_page_id(selected_page)
    access_token = _get_meta_page_access_token(db, integration, selected_page)
    raw_payload = debug_meta_raw(
        integration_id=integration_id,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        db=db,
    )
    timeframe_config = raw_payload.get("timeframe") if isinstance(raw_payload, dict) else timeframe_config
    reach_payload = raw_payload.get("reach") if isinstance(raw_payload, dict) else {}
    impressions_payload = raw_payload.get("impressions") if isinstance(raw_payload, dict) else {}
    since = str(timeframe_config.get("since") or "")
    until = str(timeframe_config.get("until") or "")
    viewers_daily = _expand_meta_daily_series(
        list(reach_payload.get("reach_daily") or []),
        since=since,
        until=until,
    )
    impressions_daily = _expand_meta_daily_series(
        list(impressions_payload.get("impressions_daily") or []),
        since=since,
        until=until,
    )
    views_payload = _build_meta_report_metric_entry(
        facebook_ui_target_label="Visualizaciones",
        source_metric_name="page_views_total",
        total=None,
        daily_series=[],
        timeframe_since=since,
        timeframe_until=until,
    )
    viewers_payload = _build_meta_report_metric_entry(
        facebook_ui_target_label="Espectadores",
        source_metric_name=str(reach_payload.get("metric_name") or "") or None,
        total=reach_payload.get("value"),
        daily_series=viewers_daily,
        timeframe_since=since,
        timeframe_until=until,
    )
    impressions_debug = _build_meta_report_metric_entry(
        facebook_ui_target_label="Impresiones API",
        source_metric_name=str(impressions_payload.get("metric_name") or "") or None,
        total=impressions_payload.get("value"),
        daily_series=impressions_daily,
        timeframe_since=since,
        timeframe_until=until,
    )
    views_daily = _expand_meta_daily_series(
        list(
            (
                _fetch_meta_pages_metric_payload(
                    access_token=access_token,
                    page_id=page_id,
                    timeframe_config=timeframe_config,
                    integration_id=integration.id,
                    metric_name="page_views_total",
                    label="Visualizaciones",
                    daily_key="views_daily",
                ).get("views_daily")
                or []
            )
        ),
        since=since,
        until=until,
    )
    views_total = _sum_meta_daily_series(views_daily)
    views_payload = _build_meta_report_metric_entry(
        facebook_ui_target_label="Visualizaciones",
        source_metric_name="page_views_total",
        total=views_total,
        daily_series=views_daily,
        timeframe_since=since,
        timeframe_until=until,
    )
    return {
        "views": views_payload,
        "viewers": viewers_payload,
        "impressions_api": impressions_debug,
        "views_debug": {
            "views_total": views_total,
            "views_daily_count": len(views_daily),
            "first_views_date": views_daily[0]["date"] if views_daily else None,
            "last_views_date": views_daily[-1]["date"] if views_daily else None,
            "timeframe_since": since,
            "timeframe_until": until,
            "sample_first_5_points": views_daily[:5],
            "sample_last_5_points": views_daily[-5:] if views_daily else [],
            "series_kind": "daily_dense_calendar_series",
        },
        "timeframe": timeframe_config,
    }


@app.get("/debug/report-render-source")
def debug_report_render_source(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    report_version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .first()
    )
    blocks = []
    if report_version:
        blocks = (
            db.query(ReportBlock)
            .filter(ReportBlock.report_version_id == report_version.id)
            .order_by(ReportBlock.order.asc())
            .all()
        )

    dataset = db.get(Dataset, report.dataset_id)
    dataset_data = dataset.data if dataset and isinstance(dataset.data, dict) else {}
    normalized_metrics = (
        dataset_data.get("normalized_report_metrics")
        if isinstance(dataset_data.get("normalized_report_metrics"), dict)
        else {}
    )
    views_daily = (
        normalized_metrics.get("views_daily")
        if isinstance(normalized_metrics.get("views_daily"), list)
        else []
    )
    timeframe_since = normalized_metrics.get("timeframe_since")
    timeframe_until = normalized_metrics.get("timeframe_until")
    has_views_chart_block = False
    impressions_slide_block_data: dict[str, object] | None = None
    general_insights_slide_block_data: dict[str, object] | None = None
    for block in blocks:
        try:
            block_data = json.loads(block.data_json or "{}")
        except json.JSONDecodeError:
            continue
        if block.type == "chart" and (
            isinstance(block_data, dict)
            and str(block_data.get("metric") or "").lower() == "views"
        ):
            has_views_chart_block = True
        if block.type == "impressions_slide" and isinstance(block_data, dict):
            impressions_slide_block_data = block_data
        if block.type == "general_insights_slide" and isinstance(block_data, dict):
            general_insights_slide_block_data = block_data

    return {
        "report_id": report.id,
        "dataset_id": report.dataset_id,
        "version_id": report_version.id if report_version else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "timeframe_since": timeframe_since,
        "timeframe_until": timeframe_until,
        "views_daily_count": len(views_daily),
        "first_views_date": views_daily[0].get("date") if views_daily else None,
        "last_views_date": views_daily[-1].get("date") if views_daily else None,
        "source_used_for_preview": "stored_report_version_blocks",
        "source_used_for_full_report": "stored_report_version_blocks",
        "report_version_snapshot": {
            "uses_prebuilt_blocks": True,
            "has_views_chart_block": has_views_chart_block,
            "has_impressions_slide_block": impressions_slide_block_data is not None,
            "has_general_insights_slide_block": general_insights_slide_block_data is not None,
            "blocks_count": len(blocks),
        },
        "dataset_snapshot": {
            "has_normalized_report_metrics": bool(normalized_metrics),
            "views_total": normalized_metrics.get("views_total"),
            "sample_first_5_points": views_daily[:5],
            "sample_last_5_points": views_daily[-5:] if views_daily else [],
        },
        "impressions_slide_debug": {
            "impressions_slide_present": impressions_slide_block_data is not None,
            "impressions_total": impressions_slide_block_data.get("impressions_total")
            if isinstance(impressions_slide_block_data, dict)
            else normalized_metrics.get("impressions_total"),
            "impressions_daily_count": impressions_slide_block_data.get("impressions_daily_count")
            if isinstance(impressions_slide_block_data, dict)
            else (
                len(normalized_metrics.get("impressions_daily"))
                if isinstance(normalized_metrics.get("impressions_daily"), list)
                else 0
            ),
            "average_daily": impressions_slide_block_data.get("average_daily")
            if isinstance(impressions_slide_block_data, dict)
            else None,
            "first_impressions_date": impressions_slide_block_data.get("first_impressions_date")
            if isinstance(impressions_slide_block_data, dict)
            else (
                normalized_metrics.get("impressions_daily")[0].get("date")
                if isinstance(normalized_metrics.get("impressions_daily"), list)
                and normalized_metrics.get("impressions_daily")
                else None
            ),
            "last_impressions_date": impressions_slide_block_data.get("last_impressions_date")
            if isinstance(impressions_slide_block_data, dict)
            else (
                normalized_metrics.get("impressions_daily")[-1].get("date")
                if isinstance(normalized_metrics.get("impressions_daily"), list)
                and normalized_metrics.get("impressions_daily")
                else None
            ),
            "source_metric_used": impressions_slide_block_data.get("source_metric_name")
            if isinstance(impressions_slide_block_data, dict)
            else normalized_metrics.get("impressions_source_metric"),
            "impressions_daily_sum": impressions_slide_block_data.get("impressions_daily_sum")
            if isinstance(impressions_slide_block_data, dict)
            else (
                sum(
                    int(point.get("value"))
                    for point in normalized_metrics.get("impressions_daily", [])
                    if isinstance(point, dict) and isinstance(point.get("value"), (int, float))
                )
                if isinstance(normalized_metrics.get("impressions_daily"), list)
                else 0
            ),
            "impressions_daily_all_zero": impressions_slide_block_data.get("impressions_daily_all_zero")
            if isinstance(impressions_slide_block_data, dict)
            else (
                bool(normalized_metrics.get("impressions_daily"))
                and all(
                    point.get("value") in (0, None)
                    for point in normalized_metrics.get("impressions_daily", [])
                    if isinstance(point, dict)
                )
            ),
            "total_source_path": impressions_slide_block_data.get("total_source_path")
            if isinstance(impressions_slide_block_data, dict)
            else "dataset.data.normalized_report_metrics.impressions_total",
            "daily_source_path": impressions_slide_block_data.get("daily_source_path")
            if isinstance(impressions_slide_block_data, dict)
            else "dataset.data.normalized_report_metrics.impressions_daily",
            "used_total_fallback": impressions_slide_block_data.get("used_total_fallback")
            if isinstance(impressions_slide_block_data, dict)
            else False,
            "consistency_valid": impressions_slide_block_data.get("consistency_valid")
            if isinstance(impressions_slide_block_data, dict)
            else False,
        },
        "general_insights_slide_debug": {
            "present": general_insights_slide_block_data is not None,
            "metrics": (
                general_insights_slide_block_data.get("metrics")
                if isinstance(general_insights_slide_block_data, dict)
                else {}
            ),
        },
    }
