import base64
import json
import logging
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import HTTPException
import requests
from requests import RequestException
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .errors import http_error
from .models import (
    Conversation,
    Dataset,
    DatasetFile,
    Export,
    Job,
    Message,
    Report,
    ReportBlock,
    ReportVersion,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)

logger = logging.getLogger(__name__)

SUPPORTED_REPORT_LOCALES = {"en", "es"}
DEFAULT_WORKSPACE_PLAN = "free"


def _truncate_log_value(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
PLAN_ALIASES = {"pro": "advanced"}
PLAN_LIMITS = {
    "free": {
        "reports_per_month": 5,
        "max_slides_per_report": 5,
        "max_slides": 5,
        "storage_limit_bytes": 1 * 1024 * 1024 * 1024,
        "allow_pdf_export": True,
        "allow_pptx_export": False,
        "allow_ai_agents": False,
    },
    "starter": {
        "reports_per_month": 10,
        "max_slides_per_report": 10,
        "max_slides": 10,
        "storage_limit_bytes": 5 * 1024 * 1024 * 1024,
        "allow_pdf_export": True,
        "allow_pptx_export": False,
        "allow_ai_agents": False,
    },
    "core": {
        "reports_per_month": 30,
        "max_slides_per_report": 15,
        "max_slides": 15,
        "storage_limit_bytes": 15 * 1024 * 1024 * 1024,
        "allow_pdf_export": True,
        "allow_pptx_export": True,
        "allow_ai_agents": True,
    },
    "advanced": {
        "reports_per_month": None,
        "max_slides_per_report": 30,
        "max_slides": 30,
        "storage_limit_bytes": 30 * 1024 * 1024 * 1024,
        "allow_pdf_export": True,
        "allow_pptx_export": True,
        "allow_ai_agents": True,
    },
}


def normalize_workspace_plan(plan: Any) -> str:
    normalized = str(plan or DEFAULT_WORKSPACE_PLAN).strip().lower()
    normalized = PLAN_ALIASES.get(normalized, normalized)
    if normalized in PLAN_LIMITS:
        return normalized
    return DEFAULT_WORKSPACE_PLAN


def get_workspace_plan(db: Session, workspace_id: int) -> str:
    active_subscription = (
        db.query(Subscription)
        .filter(Subscription.workspace_id == workspace_id, Subscription.status == "active")
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
    )
    if active_subscription:
        return normalize_workspace_plan(active_subscription.plan)

    latest_subscription = (
        db.query(Subscription)
        .filter(Subscription.workspace_id == workspace_id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
    )
    if latest_subscription:
        return normalize_workspace_plan(latest_subscription.plan)

    return DEFAULT_WORKSPACE_PLAN


def get_plan_limits(plan: str) -> dict[str, Any]:
    normalized_plan = normalize_workspace_plan(plan)
    return dict(PLAN_LIMITS[normalized_plan])


def get_plan_capabilities(plan: str) -> dict[str, Any]:
    limits = get_plan_limits(plan)
    return {
        "max_slides": int(limits["max_slides"]),
        "allow_pdf_export": bool(limits["allow_pdf_export"]),
        "allow_pptx_export": bool(limits["allow_pptx_export"]),
        "allow_ai_agents": bool(limits["allow_ai_agents"]),
    }


def get_workspace_plan_capabilities(db: Session, workspace_id: int) -> dict[str, Any]:
    plan = get_workspace_plan(db, workspace_id)
    return {"plan": plan, "capabilities": get_plan_capabilities(plan)}


def get_workspace_plan_details(db: Session, workspace_id: int) -> dict[str, Any]:
    plan = get_workspace_plan(db, workspace_id)
    return {"plan": plan, "limits": get_plan_limits(plan)}


def get_workspace_storage_limit(db: Session, workspace_id: int) -> int:
    plan_details = get_workspace_plan_details(db, workspace_id)
    return int(plan_details["limits"]["storage_limit_bytes"])


def count_workspace_storage_bytes(db: Session, workspace_id: int) -> int:
    total_size = (
        db.query(func.coalesce(func.sum(DatasetFile.size_bytes), 0))
        .filter(DatasetFile.workspace_id == workspace_id)
        .scalar()
    )
    return int(total_size or 0)


def count_workspace_reports_this_month(
    db: Session,
    workspace_id: int,
    *,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now().astimezone()
    month_start = current_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    count = (
        db.query(func.count(Report.id))
        .filter(
            Report.workspace_id == workspace_id,
            Report.created_at >= month_start,
            Report.created_at < next_month_start,
        )
        .scalar()
    )
    return int(count or 0)


def enforce_monthly_report_limit(db: Session, workspace_id: int) -> dict[str, Any]:
    plan_details = get_workspace_plan_details(db, workspace_id)
    report_limit = plan_details["limits"]["reports_per_month"]
    if report_limit is None:
        return plan_details

    reports_this_month = count_workspace_reports_this_month(db, workspace_id)
    if reports_this_month >= report_limit:
        raise http_error(
            403,
            "monthly_report_limit_reached",
            "Monthly report limit reached for current plan.",
        )
    return plan_details


def enforce_slide_limit(db: Session, workspace_id: int, slide_count: int) -> dict[str, Any]:
    plan_details = get_workspace_plan_details(db, workspace_id)
    max_slides = plan_details["limits"]["max_slides"]
    if slide_count > max_slides:
        raise http_error(
            403,
            "slide_limit_exceeded",
            "Slide limit exceeded for current plan.",
        )
    return plan_details


def resolve_report_slide_limits(
    db: Session,
    workspace_id: int,
    *,
    requested_slides: int | None,
    default_slides: int,
) -> dict[str, Any]:
    plan_details = get_workspace_plan_details(db, workspace_id)
    plan = str(plan_details["plan"])
    max_slides = int(plan_details["limits"]["max_slides"])
    has_explicit_request = requested_slides is not None
    requested = int(requested_slides if has_explicit_request else default_slides)
    effective = min(requested, max_slides)
    if has_explicit_request and requested > max_slides:
        raise http_error(
            403,
            "slide_limit_exceeded",
            "Slide limit exceeded for current plan.",
        )
    return {
        "plan": plan,
        "requested_slides": requested,
        "max_slides": max_slides,
        "effective_slide_limit": effective,
        "capabilities": get_plan_capabilities(plan),
    }


def enforce_export_capability(db: Session, workspace_id: int, export_type: str) -> dict[str, Any]:
    plan_context = get_workspace_plan_capabilities(db, workspace_id)
    plan = str(plan_context["plan"])
    capabilities = dict(plan_context["capabilities"])
    export_key = str(export_type).strip().lower()
    allowed = (
        bool(capabilities["allow_pdf_export"])
        if export_key == "pdf"
        else bool(capabilities["allow_pptx_export"])
        if export_key == "pptx"
        else False
    )
    logger.info(
        "[PlanLimits][export]",
        extra={
            "plan": plan,
            "export_type": export_key,
            "allowed": allowed,
        },
    )
    if not allowed:
        raise http_error(
            403,
            "plan_restricted",
            f"{export_key.upper()} export is not available for current plan.",
        )
    return {"plan": plan, "export_type": export_key, "allowed": allowed, "capabilities": capabilities}


def enforce_storage_limit(db: Session, workspace_id: int, incoming_bytes: int) -> dict[str, Any]:
    plan_details = get_workspace_plan_details(db, workspace_id)
    storage_limit = int(plan_details["limits"]["storage_limit_bytes"])
    storage_used = count_workspace_storage_bytes(db, workspace_id)
    if storage_used + incoming_bytes > storage_limit:
        raise http_error(
            403,
            "storage_limit_reached",
            "Storage limit reached for current plan.",
        )
    return plan_details


def build_default_workspace_name(full_name: str | None) -> str:
    normalized_name = str(full_name or "").strip()
    if normalized_name:
        return f"Workspace de {normalized_name}"
    return "My Workspace"


def register_user_with_default_workspace(
    db: Session,
    *,
    email: str,
    password_hash: str,
    full_name: str | None,
) -> tuple[User, Workspace, Subscription]:
    user = User(
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        is_active=True,
    )
    db.add(user)
    db.flush()

    workspace = Workspace(name=build_default_workspace_name(full_name))
    db.add(workspace)
    db.flush()

    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)

    subscription = Subscription(
        workspace_id=workspace.id,
        plan=DEFAULT_WORKSPACE_PLAN,
        status="active",
    )
    db.add(subscription)
    db.commit()
    db.refresh(user)
    db.refresh(workspace)
    db.refresh(subscription)
    return user, workspace, subscription


def build_conversation_title(message: str) -> str:
    normalized = " ".join(str(message or "").strip().split())
    if not normalized:
        return "New Conversation"
    return normalized[:80]


def build_workspace_ai_demo_response() -> str:
    return "This is a demo AI response based on your workspace data."


def _extract_workspace_metric_value(data: dict[str, Any], key: str) -> Any:
    if key in data and data.get(key) is not None:
        return data.get(key)
    normalized_metrics = (
        data.get("normalized_report_metrics")
        if isinstance(data.get("normalized_report_metrics"), dict)
        else {}
    )
    metric_aliases = {
        "reach": ["reach", "viewers_total"],
        "engagement": ["engagement", "interactions_total"],
        "followers": ["followers", "followers_growth_total"],
        "impressions": ["impressions", "impressions_total"],
    }
    for metric_key in metric_aliases.get(key, [key]):
        if normalized_metrics.get(metric_key) is not None:
            return normalized_metrics.get(metric_key)
    return None


def build_workspace_data_snapshot(db: Session, workspace_id: int) -> dict[str, Any]:
    recent_datasets = (
        db.query(Dataset)
        .filter(Dataset.workspace_id == workspace_id)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        .limit(3)
        .all()
    )

    metrics: dict[str, Any] = {}
    latest_dataset_summary: str | None = None
    for index, dataset in enumerate(recent_datasets):
        dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        if index == 0:
            summary_parts = [f"name={dataset.name}"]
            for metric_name in ("reach", "engagement", "followers", "impressions"):
                metric_value = _extract_workspace_metric_value(dataset_data, metric_name)
                if metric_value is not None:
                    summary_parts.append(f"{metric_name}={metric_value}")
                    metrics.setdefault(metric_name, metric_value)
            latest_dataset_summary = ", ".join(summary_parts)
        else:
            for metric_name in ("reach", "engagement", "followers", "impressions"):
                if metric_name in metrics:
                    continue
                metric_value = _extract_workspace_metric_value(dataset_data, metric_name)
                if metric_value is not None:
                    metrics[metric_name] = metric_value

    return {
        "datasets_count": int(
            db.query(func.count(Dataset.id)).filter(Dataset.workspace_id == workspace_id).scalar() or 0
        ),
        "latest_dataset_summary": latest_dataset_summary,
        "metrics": metrics,
    }


def generate_workspace_ai_reply(
    db: Session,
    *,
    conversation: Conversation,
    history: list[Message],
    user_message: str,
) -> str:
    if not settings.anthropic_api_key:
        return build_workspace_ai_demo_response()

    try:
        from anthropic import Anthropic
    except ImportError:
        return build_workspace_ai_demo_response()

    workspace_snapshot = build_workspace_data_snapshot(db, conversation.workspace_id)
    workspace_data_lines = [
        f"- Total datasets: {workspace_snapshot['datasets_count']}",
    ]
    if workspace_snapshot.get("latest_dataset_summary"):
        workspace_data_lines.append(
            f"- Latest insights: {workspace_snapshot['latest_dataset_summary']}"
        )
    metrics = workspace_snapshot.get("metrics") if isinstance(workspace_snapshot.get("metrics"), dict) else {}
    if metrics:
        metrics_text = ", ".join(f"{key}={value}" for key, value in metrics.items())
        workspace_data_lines.append(f"- Key metrics: {metrics_text}")
    else:
        workspace_data_lines.append("- Key metrics: unavailable")

    system_prompt = (
        "You are an AI assistant inside Measurable, a platform that generates "
        "marketing reports from data.\n\n"
        "You help users:\n"
        "- understand their reports\n"
        "- explain metrics (reach, engagement, sales)\n"
        "- suggest improvements\n"
        "- summarize insights\n\n"
        "Keep answers:\n"
        "- concise\n"
        "- actionable\n"
        "- business-focused\n\n"
        "You MUST use the workspace data when available.\n"
        "If data is missing, say so clearly.\n\n"
        "Workspace data:\n"
        + "\n".join(workspace_data_lines)
    )
    anthropic_messages: list[dict[str, str]] = []
    for message in history[-14:]:
        if message.role not in {"user", "assistant"}:
            continue
        content = str(message.content or "").strip()
        if not content:
            continue
        anthropic_messages.append({"role": message.role, "content": content})

    if not anthropic_messages:
        anthropic_messages = [{"role": "user", "content": user_message}]

    try:
        client = Anthropic(api_key=settings.anthropic_api_key, timeout=10.0)
        response = client.messages.create(
            model=settings.anthropic_model or "claude-3-haiku-20240307",
            max_tokens=500,
            temperature=0.2,
            system=system_prompt,
            messages=anthropic_messages,
        )
    except Exception:
        return build_workspace_ai_demo_response()

    text_parts = [
        block.text.strip()
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    reply = " ".join(text_parts).strip()
    if not reply:
        return build_workspace_ai_demo_response()
    return reply


@lru_cache(maxsize=1)
def workspace_logo_column_available() -> bool:
    try:
        columns = inspect(engine).get_columns("workspaces")
    except SQLAlchemyError:
        return False
    return any(str(column.get("name")) == "logo_url" for column in columns)


def resolve_workspace_branding(workspace_id: int | None) -> dict[str, Optional[str]]:
    if not workspace_id or not workspace_logo_column_available():
        return {"logo_url": None}

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT logo_url FROM workspaces WHERE id = :workspace_id"),
                {"workspace_id": int(workspace_id)},
            ).first()
    except SQLAlchemyError:
        return {"logo_url": None}

    if not result:
        return {"logo_url": None}

    logo_url = result[0]
    return {"logo_url": str(logo_url) if logo_url else None}


def _load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


META_PAGES_TIMEFRAME_PRESETS = {
    "last_7_days": 7,
    "last_14_days": 14,
    "last_28_days": 28,
}


def resolve_meta_pages_timeframe(
    timeframe: str | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    today: date | None = None,
) -> dict[str, str | None]:
    current_day = today or date.today()
    selected_timeframe = str(timeframe or "last_28_days").strip() or "last_28_days"

    def _build_timeframe_payload(
        *,
        key: str,
        label: str,
        preset: str | None,
        current_since: date,
        current_until: date,
        previous_since: date | None,
        previous_until: date | None,
    ) -> dict[str, str | None]:
        requested_since = previous_since or current_since
        requested_until = current_until
        duration_days = (current_until - current_since).days + 1
        return {
            "key": key,
            "label": label,
            "preset": preset,
            "since": current_since.isoformat(),
            "until": current_until.isoformat(),
            "timeframe": key,
            "requested_since": requested_since.isoformat(),
            "requested_until": requested_until.isoformat(),
            "current_since": current_since.isoformat(),
            "current_until": current_until.isoformat(),
            "previous_since": previous_since.isoformat() if previous_since else None,
            "previous_until": previous_until.isoformat() if previous_until else None,
            "selected_timeframe": key,
            "duration_days": str(duration_days),
        }

    if selected_timeframe in META_PAGES_TIMEFRAME_PRESETS:
        days = META_PAGES_TIMEFRAME_PRESETS[selected_timeframe]
        until = current_day
        since = until - timedelta(days=days - 1)
        previous_until = since - timedelta(days=1)
        previous_since = since - timedelta(days=days)
        return _build_timeframe_payload(
            key=selected_timeframe,
            label=selected_timeframe.replace("_", " ").title(),
            preset=selected_timeframe,
            current_since=since,
            current_until=until,
            previous_since=previous_since,
            previous_until=previous_until,
        )

    if selected_timeframe == "this_month":
        current_since = current_day.replace(day=1)
        current_until = current_day
        previous_month_end = current_since - timedelta(days=1)
        previous_since = previous_month_end.replace(day=1)
        candidate_previous_until = previous_since + timedelta(days=current_day.day - 1)
        previous_until = min(candidate_previous_until, previous_month_end)
        return _build_timeframe_payload(
            key=selected_timeframe,
            label="This Month",
            preset=None,
            current_since=current_since,
            current_until=current_until,
            previous_since=previous_since,
            previous_until=previous_until,
        )

    if selected_timeframe == "last_month":
        current_month_start = current_day.replace(day=1)
        current_until = current_month_start - timedelta(days=1)
        current_since = current_until.replace(day=1)
        previous_until = current_since - timedelta(days=1)
        previous_since = previous_until.replace(day=1)
        return _build_timeframe_payload(
            key=selected_timeframe,
            label="Last Month",
            preset=None,
            current_since=current_since,
            current_until=current_until,
            previous_since=previous_since,
            previous_until=previous_until,
        )

    if selected_timeframe == "custom":
        if not start_date or not end_date:
            raise http_error(
                422,
                "invalid_timeframe",
                "custom timeframe requires start_date and end_date.",
            )
        try:
            since = date.fromisoformat(start_date)
            until = date.fromisoformat(end_date)
        except ValueError:
            raise http_error(
                422,
                "invalid_timeframe",
                "start_date and end_date must use YYYY-MM-DD format.",
            )
        if since > until:
            raise http_error(
                422,
                "invalid_timeframe",
                "start_date must be before or equal to end_date.",
            )
        duration_days = (until - since).days + 1
        previous_until = since - timedelta(days=1)
        previous_since = since - timedelta(days=duration_days)
        return _build_timeframe_payload(
            key=selected_timeframe,
            label=f"Custom ({since.isoformat()} to {until.isoformat()})",
            preset=None,
            current_since=since,
            current_until=until,
            previous_since=previous_since,
            previous_until=previous_until,
        )

    raise http_error(
        400,
        "invalid_timeframe",
        "timeframe must be one of: last_7_days, last_14_days, last_28_days, this_month, last_month, custom.",
    )


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value in (None, "", "null"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_report_locale(locale: Any) -> str:
    normalized = str(locale or "en").strip().lower()
    if normalized in SUPPORTED_REPORT_LOCALES:
        return normalized
    return "en"


def normalize_meta_recent_posts(posts: Any) -> list[dict[str, Any]]:
    if not isinstance(posts, list):
        return []

    normalized_posts: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        normalized_posts.append(
            {
                "id": str(post.get("id")) if post.get("id") is not None else None,
                "message": str(post.get("message")) if post.get("message") is not None else None,
                "created_time": str(post.get("created_time"))
                if post.get("created_time") is not None
                else None,
                "permalink_url": str(post.get("permalink_url"))
                if post.get("permalink_url") is not None
                else None,
                "reach": _to_int(post.get("reach")),
                "reactions": _to_int(post.get("reactions")),
                "comments": _to_int(post.get("comments")),
                "shares": _to_int(post.get("shares")),
                "saves": _to_int(post.get("saves")),
            }
        )
    return normalized_posts


def normalize_meta_timeseries(points: Any) -> list[dict[str, Optional[int | str]]]:
    if not isinstance(points, list):
        return []

    normalized_points: list[dict[str, Optional[int | str]]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        raw_date = point.get("date")
        normalized_points.append(
            {
                "date": str(raw_date) if raw_date is not None else None,
                "value": _to_int(point.get("value")),
            }
        )
    return normalized_points


def extract_meta_pages_report_inputs(row: dict[str, Any]) -> dict[str, Any]:
    timeframe = row.get("timeframe") if isinstance(row.get("timeframe"), dict) else {}
    normalized_metrics = (
        row.get("normalized_report_metrics")
        if isinstance(row.get("normalized_report_metrics"), dict)
        else {}
    )
    integration_type = str(row.get("integration_type") or "").strip() or None
    is_instagram_business = integration_type == "instagram_business"
    has_dataset_timeframe = bool(timeframe)
    timeframe_preset = (
        str(timeframe.get("preset") or "") or None
        if has_dataset_timeframe
        else str(row.get("timeframe_preset") or "") or None
    )
    timeframe_since = (
        str(timeframe.get("since") or "") or None
        if has_dataset_timeframe
        else str(row.get("timeframe_since") or "") or None
    )
    timeframe_until = (
        str(timeframe.get("until") or "") or None
        if has_dataset_timeframe
        else str(row.get("timeframe_until") or "") or None
    )
    recent_posts_raw = row.get("recent_posts")
    if isinstance(recent_posts_raw, str):
        recent_posts = normalize_meta_recent_posts(_load_json(recent_posts_raw, []))
    else:
        recent_posts = normalize_meta_recent_posts(recent_posts_raw)

    reach_daily_raw = row.get("reach_daily")
    if isinstance(reach_daily_raw, str):
        reach_daily = normalize_meta_timeseries(_load_json(reach_daily_raw, []))
    else:
        reach_daily = normalize_meta_timeseries(reach_daily_raw)
    impressions_daily_raw = row.get("impressions_daily")
    if isinstance(impressions_daily_raw, str):
        impressions_daily = normalize_meta_timeseries(_load_json(impressions_daily_raw, []))
    else:
        impressions_daily = normalize_meta_timeseries(impressions_daily_raw)
    interactions_daily_raw = normalized_metrics.get("interactions_daily")
    interactions_daily = normalize_meta_timeseries(interactions_daily_raw)
    daily_engagement_raw = row.get("daily_engagement")
    if isinstance(daily_engagement_raw, str):
        daily_engagement = normalize_meta_timeseries(_load_json(daily_engagement_raw, []))
    else:
        daily_engagement = normalize_meta_timeseries(daily_engagement_raw)
    if not daily_engagement:
        daily_engagement = interactions_daily
    link_clicks_daily_raw = normalized_metrics.get("link_clicks_daily")
    link_clicks_daily = normalize_meta_timeseries(link_clicks_daily_raw)
    page_visits_daily_raw = normalized_metrics.get("page_visits_daily")
    page_visits_daily = normalize_meta_timeseries(page_visits_daily_raw)
    followers_growth_daily_raw = normalized_metrics.get("followers_growth_daily")
    followers_growth_daily = normalize_meta_timeseries(followers_growth_daily_raw)
    unavailable_metrics_raw = row.get("unavailable_metrics")
    if isinstance(unavailable_metrics_raw, str):
        unavailable_metrics = _load_json(unavailable_metrics_raw, {})
    elif isinstance(unavailable_metrics_raw, dict):
        unavailable_metrics = unavailable_metrics_raw
    else:
        unavailable_metrics = {}
    reach = _to_int(row.get("reach"))
    if reach is None:
        reach = _to_int(normalized_metrics.get("viewers_total"))
    if reach is None:
        reach_values = [
            int(point["value"])
            for point in reach_daily
            if isinstance(point, dict) and point.get("value") is not None
        ]
        if reach_values:
            reach = sum(reach_values)
    impressions = _to_int(row.get("impressions"))
    if impressions is None:
        impressions = _to_int(normalized_metrics.get("impressions_total"))
    if impressions is None:
        impression_values = [
            int(point["value"])
            for point in impressions_daily
            if isinstance(point, dict) and point.get("value") is not None
        ]
        if impression_values:
            impressions = sum(impression_values)
    followers = _to_int(row.get("followers"))
    if followers is None:
        followers = _to_int(row.get("followers_count"))
    if followers is None:
        followers = _to_int(normalized_metrics.get("followers_growth_total"))
    engagement = _to_int(row.get("engagement"))
    if engagement is None:
        engagement = _to_int(row.get("total_interactions"))
    if engagement is None:
        engagement = _to_int(row.get("accounts_engaged"))
    if engagement is None:
        engagement = _to_int(row.get("content_interactions"))
    profile_visits = _to_int(row.get("profile_visits"))
    if profile_visits is None:
        profile_visits = _to_int(row.get("profile_views"))
    if profile_visits is None:
        profile_visits = _to_int(normalized_metrics.get("page_visits_total"))
    if profile_visits is None:
        profile_visits = _to_int(normalized_metrics.get("views_total"))
    content_interactions = _to_int(row.get("content_interactions"))
    if content_interactions is None:
        content_interactions = _to_int(normalized_metrics.get("content_interactions"))
    link_clicks = _to_int(row.get("link_clicks"))
    if link_clicks is None:
        link_clicks = _to_int(row.get("website_clicks"))
    if link_clicks is None:
        link_clicks = _to_int(normalized_metrics.get("link_clicks_total"))
    views = _to_int(row.get("views"))
    if views is None:
        views = _to_int(normalized_metrics.get("views_total"))
    followers_growth = _to_int(row.get("followers_growth"))
    if followers_growth is None and not is_instagram_business:
        followers_growth = _to_int(normalized_metrics.get("followers_growth_total"))

    if not reach_daily:
        reach_daily = normalize_meta_timeseries(normalized_metrics.get("viewers_daily"))
    if not interactions_daily:
        interactions_daily = normalize_meta_timeseries(normalized_metrics.get("interactions_daily"))
    if not daily_engagement:
        daily_engagement = interactions_daily
    if not link_clicks_daily:
        link_clicks_daily = normalize_meta_timeseries(normalized_metrics.get("link_clicks_daily"))
    if not page_visits_daily:
        page_visits_daily = normalize_meta_timeseries(
            normalized_metrics.get("page_visits_daily") or normalized_metrics.get("views_daily")
        )

    logger.info(
        "instagram_dataset_keys" if is_instagram_business else "meta_dataset_keys",
        extra={
            "integration_type": integration_type,
            "dataset_keys": sorted(row.keys()),
            "normalized_metric_keys": sorted(normalized_metrics.keys()),
        },
    )
    if is_instagram_business:
        logger.info(
            "instagram_normalized_metrics",
            extra={
                "followers": followers,
                "reach": reach,
                "engagement": engagement,
                "engagement_source_metric": row.get("engagement_source_metric"),
                "content_interactions": content_interactions,
                "profile_visits": profile_visits,
                "views": views,
                "link_clicks": link_clicks,
                "impressions": impressions,
                "daily_engagement_count": len(daily_engagement),
            },
        )
        logger.info(
            "instagram_unavailable_metrics",
            extra={"unavailable_metrics": unavailable_metrics if isinstance(unavailable_metrics, dict) else {}},
        )

    return {
        "integration_type": integration_type,
        "page_name": str(row.get("page_name") or row.get("account_name") or "Meta Page"),
        "account_name": str(row.get("account_name") or row.get("page_name") or "Meta Page"),
        "username": str(row.get("username") or "") or None,
        "followers": followers,
        "reach": reach,
        "engagement": engagement,
        "total_interactions": _to_int(row.get("total_interactions")),
        "accounts_engaged": _to_int(row.get("accounts_engaged")),
        "impressions": impressions,
        "profile_visits": profile_visits,
        "views": views,
        "content_interactions": content_interactions,
        "link_clicks": link_clicks,
        "followers_growth": followers_growth,
        "timeframe_preset": timeframe_preset,
        "timeframe_since": timeframe_since,
        "timeframe_until": timeframe_until,
        "timeframe_key": str(timeframe.get("key") or timeframe.get("timeframe") or "") or None,
        "timeframe_label": str(timeframe.get("label") or "") or None,
        "reach_source_metric": str(row.get("reach_source_metric") or "") or None,
        "engagement_source_metric": str(row.get("engagement_source_metric") or "") or None,
        "impressions_source_metric": str(row.get("impressions_source_metric") or "") or None,
        "impressions_daily": impressions_daily,
        "reach_daily": reach_daily,
        "interactions_daily": interactions_daily,
        "engagement_daily": interactions_daily,
        "daily_engagement": daily_engagement,
        "content_interactions_daily": interactions_daily,
        "link_clicks_daily": link_clicks_daily,
        "page_visits_daily": page_visits_daily,
        "followers_growth_daily": followers_growth_daily,
        "recent_posts": recent_posts,
        "unavailable_metrics": unavailable_metrics if isinstance(unavailable_metrics, dict) else {},
    }


def build_meta_pages_recent_posts_summary(report_inputs: dict[str, Any], locale: str = "en") -> str:
    locale = normalize_report_locale(locale)
    recent_posts = normalize_meta_recent_posts(report_inputs.get("recent_posts"))
    if not recent_posts:
        if locale == "es":
            return "No hay publicaciones recientes disponibles en el último dataset sincronizado."
        return "No recent posts are available in the latest synced dataset."

    snippets: list[str] = []
    for post in recent_posts[:3]:
        message = (post.get("message") or "").strip()
        if message:
            snippets.append(message[:80])
        else:
            snippets.append("Publicación sin texto" if locale == "es" else "Post without message text")

    snippet_text = "; ".join(snippets)
    if locale == "es":
        return f"Hay {len(recent_posts)} publicaciones recientes disponibles. Destacados: {snippet_text}."
    return f"{len(recent_posts)} recent posts are available. Highlights: {snippet_text}."


def build_meta_pages_summary(report_inputs: dict[str, Any], locale: str = "en") -> str:
    locale = normalize_report_locale(locale)
    timeframe_label = str(report_inputs.get("timeframe_label") or "").strip()
    integration_type = str(report_inputs.get("integration_type") or "").strip()
    is_instagram_business = integration_type == "instagram_business"
    unavailable_metrics = (
        report_inputs.get("unavailable_metrics")
        if isinstance(report_inputs.get("unavailable_metrics"), dict)
        else {}
    )
    metrics = {
        "followers": report_inputs.get("followers"),
        "reach": report_inputs.get("reach"),
        "engagement": report_inputs.get("engagement"),
    }
    if any(value is None for value in metrics.values()):
        if is_instagram_business:
            missing_labels = []
            if metrics["reach"] is None:
                missing_labels.append("reach")
            if metrics["engagement"] is None:
                missing_labels.append("engagement")
            if metrics["followers"] is None:
                missing_labels.append("followers")
            reason = None
            for key in ("reach", "total_interactions", "content_interactions", "accounts_engaged", "followers_count"):
                if unavailable_metrics.get(key):
                    reason = str(unavailable_metrics.get(key))
                    break
            missing_text = ", ".join(missing_labels) if missing_labels else "some Instagram metrics"
            if locale == "es":
                if reason:
                    available_parts = []
                    if report_inputs.get("followers") is not None:
                        available_parts.append(
                            f"Seguidores disponibles: {report_inputs['followers']}."
                        )
                    if report_inputs.get("profile_visits") is not None:
                        available_parts.append(
                            f"Visitas de perfil disponibles: {report_inputs['profile_visits']}."
                        )
                    if report_inputs.get("link_clicks") is not None:
                        available_parts.append(
                            f"Clics al sitio disponibles: {report_inputs['link_clicks']}."
                        )
                    if report_inputs.get("views") is not None:
                        available_parts.append(
                            f"Views disponibles: {report_inputs['views']}."
                        )
                    return (
                        f"Instagram Business sincronizó la cuenta, pero Meta no devolvió {missing_text} "
                        f"para este periodo. Motivo reportado por Meta: {reason}. "
                        + " ".join(available_parts)
                    )
                return (
                    f"Instagram Business sincronizó la cuenta, pero Meta no devolvió {missing_text} "
                    "para este periodo."
                )
            if reason:
                available_parts = []
                if report_inputs.get("followers") is not None:
                    available_parts.append(
                        f"Available followers: {report_inputs['followers']}."
                    )
                if report_inputs.get("profile_visits") is not None:
                    available_parts.append(
                        f"Available profile views: {report_inputs['profile_visits']}."
                    )
                if report_inputs.get("link_clicks") is not None:
                    available_parts.append(
                        f"Available website clicks: {report_inputs['link_clicks']}."
                    )
                if report_inputs.get("views") is not None:
                    available_parts.append(
                        f"Available views: {report_inputs['views']}."
                    )
                return (
                    f"Instagram Business synced successfully, but Meta did not return {missing_text} "
                    f"for this period. Reported reason: {reason}. "
                    + " ".join(available_parts)
                )
            return (
                f"Instagram Business synced successfully, but Meta did not return {missing_text} "
                "for this period."
            )
        if locale == "es":
            return "Algunas métricas de la página de Meta no estuvieron disponibles en el último dataset sincronizado."
        return "Some Meta Page metrics were not available in the latest synced dataset."

    subject_name = report_inputs["account_name"] if is_instagram_business else report_inputs["page_name"]
    if locale == "es":
        timeframe_suffix = f" para {timeframe_label}" if timeframe_label else ""
        return (
            f"{subject_name} actualmente registra "
            f"{report_inputs['followers']} seguidores y {report_inputs['reach']} de alcance "
            f"con {report_inputs['engagement']} de interacción{timeframe_suffix} en el último dataset sincronizado."
        )
    timeframe_suffix = f" for {timeframe_label}" if timeframe_label else ""
    return (
        f"{subject_name} currently reports "
        f"{report_inputs['followers']} followers and {report_inputs['reach']} reach "
        f"with {report_inputs['engagement']} engagement{timeframe_suffix} in the latest synced dataset."
    )


def build_meta_pages_reach_chart_data(report_inputs: dict[str, Any]) -> dict[str, Any]:
    points = normalize_meta_timeseries(report_inputs.get("reach_daily"))
    has_points = any(point.get("value") is not None for point in points)
    timeframe_label = str(report_inputs.get("timeframe_label") or "").strip()
    chart_label = (
        f"Reach Trend - {timeframe_label}" if timeframe_label else "Reach Daily Trend"
    )
    return {
        "label": chart_label,
        "metric": "reach",
        "points": points if has_points else [],
        "is_available": has_points,
        "source_metric": report_inputs.get("reach_source_metric") or "page_reach",
        "timeframe": {
            "key": report_inputs.get("timeframe_key"),
            "label": report_inputs.get("timeframe_label"),
            "preset": report_inputs.get("timeframe_preset"),
            "since": report_inputs.get("timeframe_since"),
            "until": report_inputs.get("timeframe_until"),
        },
    }


def build_meta_pages_reach_insight(report_inputs: dict[str, Any], locale: str = "en") -> str:
    locale = normalize_report_locale(locale)
    total_reach = report_inputs.get("reach")
    timeframe_label = str(report_inputs.get("timeframe_label") or "").strip()
    unavailable_metrics = (
        report_inputs.get("unavailable_metrics")
        if isinstance(report_inputs.get("unavailable_metrics"), dict)
        else {}
    )
    points = [
        point
        for point in normalize_meta_timeseries(report_inputs.get("reach_daily"))
        if point.get("date") and point.get("value") is not None
    ]
    if total_reach is None:
        explicit_reason = str(unavailable_metrics.get("reach") or "").strip() or None
        if explicit_reason:
            if locale == "es":
                return (
                    "Reach no estuvo disponible en el origen para el periodo seleccionado. "
                    f"Meta reportó: {explicit_reason}."
                )
            return (
                "Reach was not available from the source for the selected period. "
                f"Meta reported: {explicit_reason}."
            )
        if locale == "es":
            return "Reach no estuvo disponible en el origen para el periodo seleccionado."
        return "Reach was not available from the source for the selected period."
    if not points:
        if locale == "es":
            return (
                f"El Reach total del periodo fue {total_reach}, pero no se recibió serie diaria "
                "desde el origen para describir su comportamiento."
            )
        return (
            f"Total reach for the period was {total_reach}, but no daily time series was returned "
            "from the source to describe its behavior."
        )

    peak_point = max(points, key=lambda point: int(point["value"]))
    first_value = int(points[0]["value"])
    last_value = int(points[-1]["value"])
    average_value = round(sum(int(point["value"]) for point in points) / len(points))

    if locale == "es":
        if last_value > first_value:
            trend = "cerró por encima del inicio del periodo"
        elif last_value < first_value:
            trend = "cerró por debajo del inicio del periodo"
        else:
            trend = "cerró en línea con el inicio del periodo"
        timeframe_prefix = f"Para {timeframe_label}, " if timeframe_label else ""
        return (
            f"{timeframe_prefix}el Reach total del periodo fue {total_reach}. El promedio diario fue {average_value} y "
            f"el pico ocurrió el {peak_point['date']} con {peak_point['value']}. "
            f"En la comparación entre el inicio y el cierre del periodo, el Reach {trend}."
        )

    if last_value > first_value:
        trend = "closed above the start of the period"
    elif last_value < first_value:
        trend = "closed below the start of the period"
    else:
        trend = "closed in line with the start of the period"
    timeframe_prefix = f"For {timeframe_label}, " if timeframe_label else ""

    return (
        f"{timeframe_prefix}total reach for the period was {total_reach}. The daily average was {average_value}, "
        f"and the highest peak occurred on {peak_point['date']} with {peak_point['value']}. "
        f"Compared with the start of the period, reach {trend}."
    )


def build_meta_pages_ai_payload(dataset: dict[str, Any] | Any) -> dict[str, Any]:
    # This payload will be used for AI-generated insights (Claude) in a future step.
    data = None
    if isinstance(dataset, dict) and isinstance(dataset.get("data"), dict):
        data = dataset.get("data")
    elif isinstance(getattr(dataset, "data", None), dict):
        data = getattr(dataset, "data")
    source = data if data is not None else dataset
    recent_posts = source.get("recent_posts") if isinstance(source, dict) else None

    return {
        "page_name": source.get("page_name") if isinstance(source, dict) else None,
        "followers": source.get("followers") if isinstance(source, dict) else None,
        "reach": source.get("reach") if isinstance(source, dict) else None,
        "engagement": source.get("engagement") if isinstance(source, dict) else None,
        "impressions": source.get("impressions") if isinstance(source, dict) else None,
        "profile_visits": source.get("profile_visits") if isinstance(source, dict) else None,
        "content_interactions": source.get("content_interactions") if isinstance(source, dict) else None,
        "link_clicks": source.get("link_clicks") if isinstance(source, dict) else None,
        "followers_growth": source.get("followers_growth") if isinstance(source, dict) else None,
        "timeframe": source.get("timeframe") if isinstance(source, dict) else None,
        "impressions_daily": normalize_meta_timeseries(source.get("impressions_daily")) if isinstance(source, dict) else [],
        "reach_daily": normalize_meta_timeseries(source.get("reach_daily")) if isinstance(source, dict) else [],
        "recent_posts": recent_posts if isinstance(recent_posts, list) else [],
    }


def build_meta_pages_ai_fallback_summary(payload: dict[str, Any], locale: str = "en") -> str:
    locale = normalize_report_locale(locale)
    has_metric = any(payload.get(key) is not None for key in ("followers", "reach", "engagement"))
    has_posts = bool(payload.get("recent_posts"))
    if has_metric or has_posts:
        if locale == "en":
            return (
                "The page shows overall performance based on the available data. "
                "Review recent posts to identify formats or messages that can strengthen reach and engagement."
            )
        return (
            "La página presenta un desempeño general con base en los datos disponibles. "
            "Se recomienda revisar las publicaciones recientes para detectar formatos o mensajes que puedan reforzar alcance e interacción."
        )
    if locale == "en":
        return (
            "It was not possible to generate the executive summary with AI at this time. "
            "The report shows the available metrics for manual review."
        )
    return (
        "No fue posible generar el resumen ejecutivo con IA en este momento. "
        "El reporte muestra las métricas disponibles para análisis manual."
    )


def generate_meta_pages_ai_summary(payload: dict[str, Any], locale: str = "en") -> str:
    locale = normalize_report_locale(locale)
    if not settings.anthropic_api_key:
        return build_meta_pages_ai_fallback_summary(payload, locale)

    try:
        from anthropic import Anthropic
    except ImportError:
        return build_meta_pages_ai_fallback_summary(payload, locale)

    recent_posts_json = json.dumps(payload.get("recent_posts", []), ensure_ascii=False)
    timeframe = payload.get("timeframe") if isinstance(payload.get("timeframe"), dict) else {}
    timeframe_label = timeframe.get("label")
    timeframe_since = timeframe.get("since")
    timeframe_until = timeframe.get("until")

    try:
        client = Anthropic(api_key=settings.anthropic_api_key, timeout=10.0)
        if locale == "es":
            system_prompt = (
                "Eres un analista senior de marketing digital.\n"
                "Tu trabajo es redactar un resumen ejecutivo breve en español a partir de métricas de redes sociales.\n\n"
                "Reglas:\n"
                "- Responde únicamente en español.\n"
                "- Sé claro, concreto y útil.\n"
                "- Máximo 90 palabras.\n"
                "- No inventes datos.\n"
                "- Si algún dato viene como null, trátalo como no disponible y no lo menciones como si existiera.\n"
                "- Enfócate en lectura ejecutiva para negocio y marketing.\n"
                "- No uses formato markdown.\n"
                "- No uses viñetas.\n"
                "- Devuelve solo el texto final."
            )
            user_prompt = (
                "Genera un resumen ejecutivo corto para esta página de Meta usando únicamente estos datos:\n\n"
                f"page_name: {payload.get('page_name')}\n"
                f"followers: {payload.get('followers')}\n"
                f"reach: {payload.get('reach')}\n"
                f"engagement: {payload.get('engagement')}\n"
                f"timeframe_label: {timeframe_label}\n"
                f"timeframe_since: {timeframe_since}\n"
                f"timeframe_until: {timeframe_until}\n"
                f"recent_posts: {recent_posts_json}\n\n"
                "El resumen debe mencionar desempeño general, incluir una oportunidad o lectura accionable y sonar profesional y breve."
            )
        else:
            system_prompt = (
                "You are a senior digital marketing analyst.\n"
                "Your job is to write a short executive summary in English based on social media metrics.\n\n"
                "Rules:\n"
                "- Respond only in English.\n"
                "- Be clear, concrete, and useful.\n"
                "- Maximum 90 words.\n"
                "- Do not invent data.\n"
                "- If any value is null, treat it as unavailable and do not mention it as if it existed.\n"
                "- Focus on an executive business and marketing readout.\n"
                "- Do not use markdown.\n"
                "- Do not use bullet points.\n"
                "- Return only the final text."
            )
            user_prompt = (
                "Generate a short executive summary for this Meta page using only these data points:\n\n"
                f"page_name: {payload.get('page_name')}\n"
                f"followers: {payload.get('followers')}\n"
                f"reach: {payload.get('reach')}\n"
                f"engagement: {payload.get('engagement')}\n"
                f"timeframe_label: {timeframe_label}\n"
                f"timeframe_since: {timeframe_since}\n"
                f"timeframe_until: {timeframe_until}\n"
                f"recent_posts: {recent_posts_json}\n\n"
                "The summary should mention overall performance, include one actionable opportunity or business readout, and sound professional and concise."
            )
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=220,
            temperature=0.2,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        )
    except Exception:
        return build_meta_pages_ai_fallback_summary(payload, locale)

    text_parts = [
        block.text.strip()
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    summary = " ".join(text_parts).strip()
    if not summary:
        return build_meta_pages_ai_fallback_summary(payload, locale)
    return summary


def build_meta_pages_claude_payload(report_inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_name": report_inputs["page_name"],
        "followers": report_inputs["followers"],
        "reach": report_inputs["reach"],
        "engagement": report_inputs["engagement"],
        "recent_posts": normalize_meta_recent_posts(report_inputs.get("recent_posts")),
    }


def build_export_payload(
    export: Export, report: Report, report_version: ReportVersion, blocks: list[ReportBlock]
) -> dict[str, Any]:
    report_metadata = _load_json(report.description, {}) if report.description else {}
    report_locale = normalize_report_locale(
        report_metadata.get("locale") if isinstance(report_metadata, dict) else None
    )
    metadata_branding = (
        report_metadata.get("branding")
        if isinstance(report_metadata, dict) and isinstance(report_metadata.get("branding"), dict)
        else None
    )
    branding = (
        {"logo_url": str(metadata_branding.get("logo_url")) if metadata_branding.get("logo_url") else None}
        if metadata_branding is not None
        else resolve_workspace_branding(report.workspace_id)
    )
    report_timeframe = (
        report_metadata.get("timeframe")
        if isinstance(report_metadata, dict) and isinstance(report_metadata.get("timeframe"), dict)
        else None
    )
    return {
        "export_id": export.id,
        "format": "pptx",
        "report": {
            "id": report.id,
            "workspace_id": report.workspace_id,
            "dataset_id": report.dataset_id,
            "title": report.name,
            "locale": report_locale,
            "branding": branding,
            "description": report.description,
            "description_json": report_metadata if isinstance(report_metadata, dict) else {},
            "timeframe": report_timeframe,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "updated_at": report.updated_at.isoformat() if report.updated_at else None,
        },
        "report_version": {
            "id": report_version.id,
            "report_id": report_version.report_id,
            "version": report_version.version,
            "locale": report_locale,
            "branding": branding,
            "description": report_metadata if isinstance(report_metadata, dict) else {},
            "timeframe": report_timeframe,
            "created_at": report_version.created_at.isoformat()
            if report_version.created_at
            else None,
            "updated_at": report_version.updated_at.isoformat()
            if report_version.updated_at
            else None,
        },
        "blocks": [
            {
                "id": block.id,
                "type": block.type,
                "order": block.order,
                "data": _load_json(block.data_json, {}),
                "editable_fields": _load_json(block.editable_fields_json, []),
            }
            for block in blocks
        ],
    }


def trigger_export_service(payload: dict[str, Any]) -> Any:
    headers = {"Content-Type": "application/json"}
    if settings.export_api_key:
        headers["x-api-key"] = settings.export_api_key

    # Debug export contract before calling the external service.
    print(
        "EXPORT_SERVICE_PAYLOAD_SUMMARY="
        + json.dumps(
            {
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
            },
            ensure_ascii=True,
        )
    )

    try:
        resp = requests.post(
            settings.export_lambda_url,
            json=payload,
            headers=headers,
            timeout=60,
        )
    except RequestException as exc:
        logger.exception(
            "[PPTXExportBackend][service.request_failed]",
            extra={
                "export_lambda_url_present": bool(settings.export_lambda_url),
                "export_lambda_url": settings.export_lambda_url,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise http_error(502, "export_service_unavailable", "Export service is unavailable.") from exc

    response_text = resp.text
    response_content_type = resp.headers.get("Content-Type")
    logger.info(
        "[PPTXExportBackend][service.response]",
        extra={
            "status_code": resp.status_code,
            "content_type": response_content_type,
            "headers": dict(resp.headers),
            "raw_response_body": _truncate_log_value(response_text),
            "response_bytes": len(resp.content or b""),
        },
    )

    if resp.status_code < 200 or resp.status_code >= 300:
        logger.warning(
            "[PPTXExportBackend][service.non_2xx]",
            extra={
                "status_code": resp.status_code,
                "content_type": response_content_type,
                "raw_response_body": _truncate_log_value(response_text),
            },
        )
        raise http_error(502, "export_service_error", resp.text or "Export service failed.")

    print(
        "EXPORT_SERVICE_RESPONSE="
        + json.dumps(
            {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "text": _truncate_log_value(resp.text),
            },
            ensure_ascii=True,
            default=str,
        )
    )

    try:
        parsed_response = resp.json()
    except ValueError:
        return {
            "status_code": resp.status_code,
            "body": resp.text,
            "_binary_content": resp.content,
            "_content_type": resp.headers.get("Content-Type"),
            "_response_headers": dict(resp.headers),
        }

    if isinstance(parsed_response, dict):
        raw_body = parsed_response.get("body")
        if isinstance(raw_body, str):
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed_body = raw_body
        else:
            parsed_body = raw_body

        if "body" in parsed_response or "isBase64Encoded" in parsed_response:
            print(
                "EXPORT_SERVICE_API_GATEWAY_RESPONSE="
                + json.dumps(
                    {
                        "isBase64Encoded": parsed_response.get("isBase64Encoded"),
                        "headers": parsed_response.get("headers"),
                        "body": parsed_body,
                    },
                    ensure_ascii=True,
                    default=str,
                )
            )

    return parsed_response


def _build_export_file_name(report: Report, response: dict[str, Any] | None) -> str:
    if response and response.get("file_name"):
        return str(response["file_name"])
    sanitized_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in report.name)
    sanitized_name = sanitized_name.strip("_") or f"report-{report.id}"
    return f"{sanitized_name}.pptx"


def _generate_download_url(key: str) -> str:
    s3 = boto3.client("s3", region_name=settings.aws_region)
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_outputs_bucket, "Key": key},
        ExpiresIn=3600,
    )


def _is_pptx_binary(content: bytes | None, content_type: str | None) -> bool:
    if content and content.startswith(b"PK"):
        return True
    if not content_type:
        return False

    normalized_content_type = content_type.lower()
    binary_content_types = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/octet-stream",
        "application/zip",
    )
    return any(value in normalized_content_type for value in binary_content_types) or "zip" in normalized_content_type


def _decode_base64_file(value: str) -> bytes | None:
    compact_value = "".join(value.split())
    if not compact_value:
        return None
    try:
        return base64.b64decode(compact_value, validate=True)
    except Exception:
        return None


def _normalize_export_service_response(response: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    normalized = dict(response)
    raw_body = normalized.get("body")

    if isinstance(raw_body, dict):
        for key, value in raw_body.items():
            normalized.setdefault(key, value)
        return normalized, None

    if not isinstance(raw_body, str):
        return normalized, None

    stripped_body = raw_body.strip()
    if not stripped_body:
        return normalized, raw_body

    try:
        parsed_body = json.loads(stripped_body)
    except json.JSONDecodeError:
        return normalized, raw_body

    if isinstance(parsed_body, dict):
        for key, value in parsed_body.items():
            normalized.setdefault(key, value)
        return normalized, None

    return normalized, raw_body


def _store_export_file(export: Export, file_name: str, file_bytes: bytes, status: str) -> dict[str, Any]:
    s3_key = f"exports/{export.id}/{file_name}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        s3.put_object(
            Bucket=settings.s3_outputs_bucket,
            Key=s3_key,
            Body=file_bytes,
            ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except Exception as exc:
        raise http_error(502, "export_storage_failed", "Failed to store exported file.") from exc

    return {
        "status": status,
        "download_url": _generate_download_url(s3_key),
        "file_name": file_name,
        "output_s3_key": s3_key,
        "download_key": s3_key,
    }


def store_report_thumbnail(report_id: int, image_bytes: bytes) -> str:
    version_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    s3_key = f"report-thumbnails/{report_id}/cover-{version_suffix}.png"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        s3.put_object(
            Bucket=settings.s3_outputs_bucket,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/png",
            CacheControl="public, max-age=300",
        )
    except Exception as exc:
        raise http_error(
            502,
            "thumbnail_storage_failed",
            "Failed to store report thumbnail.",
        ) from exc
    return s3_key


def build_report_pdf_export_url(
    report: Report,
    report_version: ReportVersion,
    *,
    locale: str | None = None,
) -> str:
    if not settings.report_export_base_url:
        raise http_error(
            503,
            "pdf_export_not_configured",
            "REPORT_EXPORT_BASE_URL is required for Chromium PDF export.",
        )

    base_url = settings.report_export_base_url.rstrip("/")
    path = settings.report_export_path_template.format(
        report_id=report.id,
        version=report_version.version,
    )
    query: dict[str, str] = {}
    normalized_locale = normalize_report_locale(locale)
    if normalized_locale:
        query["locale"] = normalized_locale

    full_url = f"{base_url}{path}"
    if query:
        full_url = f"{full_url}?{urlencode(query)}"
    return full_url


def generate_thumbnail_from_export_page(
    *,
    export_url: str,
    report_id: int,
    auth_token: str,
) -> tuple[bytes, dict[str, Any]]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise http_error(
            503,
            "thumbnail_dependency_missing",
            "Playwright is not installed on the backend.",
        ) from exc

    timeout_ms = max(int(settings.pdf_export_timeout_ms), 1000)
    ready_selector = settings.pdf_export_ready_selector
    viewport_width = int(settings.pdf_export_viewport_width)
    viewport_height = int(settings.pdf_export_viewport_height)
    device_scale_factor = float(settings.pdf_export_device_scale_factor)
    auth_strategy = "authorization_header_report_export_token"
    report_fetch_events: list[dict[str, Any]] = []
    slide_selector_used: str | None = None
    slide_selectors = [
        "[data-report-slide]",
        "[data-report-page]",
        "[data-slide]",
        ".report-slide",
        ".pdf-page",
    ]

    logger.info(
        "Report thumbnail generation started",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "auth_strategy": auth_strategy,
            "viewport_width": viewport_width,
            "viewport_height": viewport_height,
            "device_scale_factor": device_scale_factor,
        },
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=settings.chromium_executable_path or None,
            )
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                device_scale_factor=device_scale_factor,
                extra_http_headers={
                    "Authorization": f"Bearer {auth_token}",
                    "X-Measurable-Export-Auth": "report_export_token",
                },
            )
            page = context.new_page()
            page.emulate_media(media="screen")

            def _handle_response(response: Any) -> None:
                url = response.url
                if f"/reports/{report_id}" not in url:
                    return
                report_fetch_events.append(
                    {
                        "url": url,
                        "status": response.status,
                        "ok": response.ok,
                    }
                )

            page.on("response", _handle_response)
            page.goto(export_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.evaluate("() => document.fonts.ready")
            if ready_selector:
                page.wait_for_selector(ready_selector, timeout=timeout_ms)

            screenshot_bytes: bytes | None = None
            for selector in slide_selectors:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    slide_selector_used = selector
                    screenshot_bytes = locator.screenshot(
                        type="png",
                        animations="disabled",
                    )
                    break

            if screenshot_bytes is None:
                screenshot_bytes = page.screenshot(
                    type="png",
                    full_page=False,
                    animations="disabled",
                )
            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        auth_failed = any(event.get("status") == 401 for event in report_fetch_events)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "thumbnail_auth_failed" if auth_failed else "thumbnail_ready_timeout",
                "message": "Thumbnail page failed to load authenticated report data."
                if auth_failed
                else "Thumbnail page did not reach the ready state before timeout.",
                "report_id": report_id,
                "export_url": export_url,
                "auth_strategy": auth_strategy,
                "report_fetch_events": report_fetch_events,
            },
        ) from exc
    except PlaywrightError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "thumbnail_generation_failed",
                "message": "Chromium failed to render the report thumbnail.",
                "report_id": report_id,
                "export_url": export_url,
                "auth_strategy": auth_strategy,
                "report_fetch_events": report_fetch_events,
            },
        ) from exc

    logger.info(
        "Report thumbnail generation completed",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "auth_strategy": auth_strategy,
            "thumbnail_bytes": len(screenshot_bytes),
            "slide_selector_used": slide_selector_used,
            "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
        },
    )
    return screenshot_bytes, {
        "export_url": export_url,
        "auth_strategy": auth_strategy,
        "report_fetch_events": report_fetch_events,
        "slide_selector_used": slide_selector_used,
    }


def generate_pdf_from_export_page(
    *,
    export_url: str,
    report_id: int,
    auth_token: str,
) -> tuple[bytes, dict[str, Any]]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise http_error(
            503,
            "pdf_export_dependency_missing",
            "Playwright is not installed on the backend.",
        ) from exc

    timeout_ms = max(int(settings.pdf_export_timeout_ms), 1000)
    ready_selector = settings.pdf_export_ready_selector
    viewport_width = int(settings.pdf_export_viewport_width)
    viewport_height = int(settings.pdf_export_viewport_height)
    device_scale_factor = float(settings.pdf_export_device_scale_factor)
    pdf_scale = float(settings.pdf_export_scale)
    pdf_margins = {
        "top": settings.pdf_export_margin_top,
        "right": settings.pdf_export_margin_right,
        "bottom": settings.pdf_export_margin_bottom,
        "left": settings.pdf_export_margin_left,
    }
    pdf_options = {
        "print_background": True,
        "prefer_css_page_size": True,
        "margin": pdf_margins,
        "scale": pdf_scale,
    }
    page_count: int | None = None
    auth_strategy = "authorization_header_report_export_token"
    report_fetch_events: list[dict[str, Any]] = []

    logger.info(
        "Chromium PDF export started",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "auth_strategy": auth_strategy,
            "viewport_width": viewport_width,
            "viewport_height": viewport_height,
            "device_scale_factor": device_scale_factor,
            "pdf_scale": pdf_scale,
            "pdf_margins": pdf_margins,
            "prefer_css_page_size": True,
            "media_type": "screen",
        },
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=settings.chromium_executable_path or None,
            )
            logger.info(
                "Chromium launched for PDF export",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "auth_strategy": auth_strategy,
                },
            )
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                device_scale_factor=device_scale_factor,
                extra_http_headers={
                    "Authorization": f"Bearer {auth_token}",
                    "X-Measurable-Export-Auth": "report_export_token",
                }
            )
            page = context.new_page()
            page.emulate_media(media="screen")

            def _handle_response(response: Any) -> None:
                url = response.url
                if f"/reports/{report_id}" not in url:
                    return
                report_fetch_events.append(
                    {
                        "url": url,
                        "status": response.status,
                        "ok": response.ok,
                    }
                )

            page.on("response", _handle_response)
            page.goto(export_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.evaluate("() => document.fonts.ready")
            try:
                if ready_selector:
                    page.wait_for_selector(ready_selector, timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                auth_failed = any(event.get("status") == 401 for event in report_fetch_events)
                logger.error(
                    "PDF export page did not reach ready state",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_events": report_fetch_events,
                        "auth_failed": auth_failed,
                        "viewport_width": viewport_width,
                        "viewport_height": viewport_height,
                        "device_scale_factor": device_scale_factor,
                        "pdf_scale": pdf_scale,
                        "pdf_margins": pdf_margins,
                        "prefer_css_page_size": True,
                        "media_type": "screen",
                    },
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_export_auth_failed"
                        if auth_failed
                        else "pdf_export_ready_timeout",
                        "message": "Export page failed to load authenticated report data."
                        if auth_failed
                        else "Export page did not reach the ready state before timeout.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_succeeded": any(
                            event.get("ok") for event in report_fetch_events
                        ),
                        "report_fetch_events": report_fetch_events,
                        "viewport": {
                            "width": viewport_width,
                            "height": viewport_height,
                            "deviceScaleFactor": device_scale_factor,
                        },
                        "pdf_options": {
                            "scale": pdf_scale,
                            "margin": pdf_margins,
                            "prefer_css_page_size": True,
                            "print_background": True,
                            "media_type": "screen",
                        },
                        "failure_reason": "report_fetch_401"
                        if auth_failed
                        else "ready_selector_timeout",
                    },
                ) from exc

            page_count = page.evaluate(
                """
                () => {
                  const selectors = [
                    '[data-report-page]',
                    '[data-report-slide]',
                    '[data-slide]',
                    '.report-slide',
                    '.pdf-page'
                  ];
                  for (const selector of selectors) {
                    const count = document.querySelectorAll(selector).length;
                    if (count > 0) {
                      return count;
                    }
                  }
                  return null;
                }
                """
            )

            logger.info(
                "Chromium PDF print config",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "device_scale_factor": device_scale_factor,
                    "pdf_scale": pdf_scale,
                    "pdf_margins": pdf_margins,
                    "prefer_css_page_size": True,
                    "media_type": "screen",
                },
            )
            pdf_bytes = page.pdf(**pdf_options)
            context.close()
            browser.close()
    except HTTPException:
        raise
    except PlaywrightError as exc:
        logger.exception(
            "Chromium PDF export failed",
            extra={
                "report_id": report_id,
                "export_url": export_url,
                "auth_strategy": auth_strategy,
                "report_fetch_events": report_fetch_events,
                "viewport_width": viewport_width,
                "viewport_height": viewport_height,
                "device_scale_factor": device_scale_factor,
                "pdf_scale": pdf_scale,
                "pdf_margins": pdf_margins,
                "prefer_css_page_size": True,
                "media_type": "screen",
            },
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "pdf_export_navigation_failed",
                "message": "Chromium failed to render the report export page.",
                "report_id": report_id,
                "export_url": export_url,
                "auth_strategy": auth_strategy,
                "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
                "report_fetch_events": report_fetch_events,
                "viewport": {
                    "width": viewport_width,
                    "height": viewport_height,
                    "deviceScaleFactor": device_scale_factor,
                },
                "pdf_options": {
                    "scale": pdf_scale,
                    "margin": pdf_margins,
                    "prefer_css_page_size": True,
                    "print_background": True,
                    "media_type": "screen",
                },
                "failure_reason": "chromium_launch_or_navigation_failure",
            },
        ) from exc

    logger.info(
        "Chromium PDF export completed",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "auth_strategy": auth_strategy,
            "page_count": page_count,
            "pdf_bytes": len(pdf_bytes),
            "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
            "viewport_width": viewport_width,
            "viewport_height": viewport_height,
            "device_scale_factor": device_scale_factor,
            "pdf_scale": pdf_scale,
            "pdf_margins": pdf_margins,
            "prefer_css_page_size": True,
            "media_type": "screen",
        },
    )
    return pdf_bytes, {
        "page_count": page_count,
        "export_url": export_url,
        "auth_strategy": auth_strategy,
        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
        "report_fetch_events": report_fetch_events,
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
            "deviceScaleFactor": device_scale_factor,
        },
        "pdf_options": {
            "scale": pdf_scale,
            "margin": pdf_margins,
            "prefer_css_page_size": True,
            "print_background": True,
            "media_type": "screen",
        },
    }


def finalize_export_response(export: Export, report: Report, response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        logger.warning(
            "[PPTXExportBackend][finalize.branch]",
            extra={
                "branch": "invalid_response_type",
                "response_type": type(response).__name__,
            },
        )
        raise http_error(502, "export_service_error", "Export service returned an invalid response.")

    normalized_response, raw_body = _normalize_export_service_response(response)

    file_name = _build_export_file_name(report, normalized_response)
    status = str(normalized_response.get("status") or "ok")
    download_url = normalized_response.get("download_url")
    output_s3_key = normalized_response.get("output_s3_key") or normalized_response.get("s3_key")
    download_key = normalized_response.get("download_key")
    raw_binary_content = response.get("_binary_content")
    raw_binary_content_type = response.get("_content_type")

    if download_url:
        logger.info(
            "[PPTXExportBackend][finalize.branch]",
            extra={
                "branch": "download_url",
                "status": status,
                "file_name": file_name,
                "download_url_present": True,
                "output_s3_key_present": bool(output_s3_key),
                "download_key_present": bool(download_key),
            },
        )
        return {
            "status": status,
            "download_url": str(download_url),
            "file_name": file_name,
            "output_s3_key": output_s3_key,
            "download_key": download_key,
        }

    if download_key:
        resolved_key = str(download_key)
        logger.info(
            "[PPTXExportBackend][finalize.branch]",
            extra={
                "branch": "download_key",
                "status": status,
                "file_name": file_name,
                "download_key": resolved_key,
            },
        )
        return {
            "status": status,
            "download_url": _generate_download_url(resolved_key),
            "file_name": file_name,
            "output_s3_key": output_s3_key or resolved_key,
            "download_key": resolved_key,
        }

    if output_s3_key:
        resolved_key = str(output_s3_key)
        logger.info(
            "[PPTXExportBackend][finalize.branch]",
            extra={
                "branch": "output_s3_key",
                "status": status,
                "file_name": file_name,
                "output_s3_key": resolved_key,
            },
        )
        return {
            "status": status,
            "download_url": _generate_download_url(resolved_key),
            "file_name": file_name,
            "output_s3_key": resolved_key,
            "download_key": resolved_key,
        }

    encoded_file = None
    if normalized_response.get("isBase64Encoded") and isinstance(raw_body, str):
        encoded_file = raw_body
    elif isinstance(raw_body, str) and len(raw_body) > 1024:
        encoded_file = raw_body
    else:
        for key in ("pptx_base64", "file_base64", "base64", "content_base64"):
            value = normalized_response.get(key)
            if isinstance(value, str):
                encoded_file = value
                break

    if encoded_file:
        file_bytes = _decode_base64_file(encoded_file)
        if file_bytes:
            logger.info(
                "[PPTXExportBackend][finalize.branch]",
                extra={
                    "branch": "base64_file",
                    "status": status,
                    "file_name": file_name,
                    "file_bytes": len(file_bytes),
                },
            )
            return _store_export_file(export, file_name, file_bytes, status)

    if isinstance(raw_binary_content, bytes) and _is_pptx_binary(raw_binary_content, raw_binary_content_type):
        logger.info(
            "[PPTXExportBackend][finalize.branch]",
            extra={
                "branch": "binary_pptx",
                "status": status,
                "file_name": file_name,
                "file_bytes": len(raw_binary_content),
                "content_type": raw_binary_content_type,
            },
        )
        return _store_export_file(export, file_name, raw_binary_content, status)

    logger.warning(
        "[PPTXExportBackend][finalize.branch]",
        extra={
            "branch": "unsupported_contract",
            "normalized_keys": sorted(normalized_response.keys()),
            "raw_body_type": type(raw_body).__name__,
            "raw_body_preview": _truncate_log_value(raw_body),
            "raw_binary_content_type": raw_binary_content_type,
            "raw_binary_content_bytes": len(raw_binary_content)
            if isinstance(raw_binary_content, bytes)
            else None,
        },
    )
    raise http_error(
        502,
        "export_service_error",
        "Export service did not return a download URL, storage key, base64 file, or valid PPTX binary.",
    )


def _run_local_job(db: Session, job: Job) -> None:
    if job.type == "generate_report":
        data = json.loads(job.payload_json or "{}")
        report_id = data.get("report_id")
        dataset_id = data.get("dataset_id")
        title = data.get("title") or data.get("name") or f"Report {job.id}"

        if report_id:
            report = db.get(Report, int(report_id))
        else:
            report = None

        if not report:
            if not dataset_id:
                raise ValueError("missing dataset_id for report creation")
            report = Report(
                workspace_id=job.workspace_id,
                dataset_id=int(dataset_id),
                name=title,
                description="dummy",
            )
            db.add(report)
            db.commit()
            db.refresh(report)

        version = ReportVersion(report_id=report.id, version=1)
        db.add(version)
        db.commit()
        db.refresh(version)

        blocks = [
            ReportBlock(
                report_version_id=version.id,
                type="title",
                order=0,
                data_json=json.dumps({"title": title}),
                editable_fields_json=json.dumps(["title"]),
            ),
            ReportBlock(
                report_version_id=version.id,
                type="chart",
                order=1,
                data_json=json.dumps({"series": []}),
                editable_fields_json=json.dumps(["series"]),
            ),
        ]
        db.add_all(blocks)
        db.commit()


def enqueue_job(db: Session, job_type: str, payload: dict, workspace_id: int) -> Job:
    job = Job(
        workspace_id=workspace_id,
        type=job_type,
        payload_json=json.dumps(payload),
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        sqs = boto3.client("sqs", region_name=settings.aws_region)
        queue_url = sqs.get_queue_url(QueueName="measurable-jobs")["QueueUrl"]
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"job_id": job.id}))
        return job
    except (NoCredentialsError, BotoCoreError, ClientError):
        # Local/dev fallback: run inline and mark done.
        job.status = "processing"
        db.add(job)
        db.commit()
        _run_local_job(db, job)
        job.status = "done"
        db.add(job)
        db.commit()
        return job
