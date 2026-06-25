import base64
import hashlib
import json
import logging
import os
import re
import secrets
from time import perf_counter
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import HTTPException
import requests
from requests import RequestException
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .errors import http_error
from .security import hash_password, hash_verification_code, verify_password, verify_verification_code
from .models import (
    Conversation,
    EmailVerificationCode,
    Dataset,
    DatasetFile,
    Export,
    Job,
    Message,
    ReferralClick,
    ReferralConversion,
    ReferralPartner,
    Report,
    ReportBlock,
    ReportVersion,
    Schedule,
    Subscription,
    User,
    UserAttribution,
    Workspace,
    WorkspaceMember,
)

logger = logging.getLogger(__name__)

SUPPORTED_REPORT_LOCALES = {"en", "es"}
DEFAULT_WORKSPACE_PLAN = "free"
OPTIONAL_REFERRAL_TABLES = frozenset(
    {
        "user_attributions",
        "referral_conversions",
        "referral_clicks",
        "referral_partners",
    }
)


def _truncate_log_value(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _is_missing_optional_referral_table_error(exc: Exception) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    error_text = str(getattr(exc, "orig", exc)).lower()
    if "undefinedtable" in error_text:
        return True
    return any(
        marker in error_text
        for table_name in OPTIONAL_REFERRAL_TABLES
        for marker in (
            f'relation "{table_name}" does not exist',
            f"no such table: {table_name}",
        )
    )


def _log_optional_referral_table_unavailable(
    exc: Exception,
    *,
    operation: str,
    user_id: int | None = None,
) -> None:
    logger.warning(
        "optional_referral_table_unavailable",
        extra={
            "operation": operation,
            "user_id": user_id,
            "error": _truncate_log_value(getattr(exc, "orig", exc)),
            "tables": sorted(OPTIONAL_REFERRAL_TABLES),
        },
    )

PLAN_ALIASES = {"core": "pro"}
PLAN_ENTITLEMENTS = {
    "free": {
        "plan_code": "free",
        "price_monthly_usd": 0,
        "reports_limit_monthly": 10,
        "reports_limit_is_temporary": True,
        "slides_per_report_limit": 5,
        "platform_report_type": "single_platform",
        "ai_chat_with_data": True,
        "storage_limit_gb": 1,
        "export_pdf": True,
        "export_pptx": False,
        "brand_personalization": False,
        "measurable_watermark": True,
        "scheduled_reports_limit": 0,
        "trial_new_features": False,
        "unlimited_reports": False,
        "unlimited_scheduled_reports": False,
    },
    "starter": {
        "plan_code": "starter",
        "price_monthly_usd": 19,
        "reports_limit_monthly": 10,
        "reports_limit_is_temporary": False,
        "slides_per_report_limit": 10,
        "platform_report_type": "two_to_three_platforms",
        "ai_chat_with_data": True,
        "storage_limit_gb": 3,
        "export_pdf": True,
        "export_pptx": True,
        "brand_personalization": True,
        "measurable_watermark": False,
        "scheduled_reports_limit": 0,
        "trial_new_features": True,
        "unlimited_reports": False,
        "unlimited_scheduled_reports": False,
    },
    "pro": {
        "plan_code": "pro",
        "price_monthly_usd": 39,
        "reports_limit_monthly": 30,
        "reports_limit_is_temporary": False,
        "slides_per_report_limit": 15,
        "platform_report_type": "multi_platform",
        "ai_chat_with_data": True,
        "storage_limit_gb": 5,
        "export_pdf": True,
        "export_pptx": True,
        "brand_personalization": True,
        "measurable_watermark": False,
        "scheduled_reports_limit": 3,
        "trial_new_features": True,
        "unlimited_reports": False,
        "unlimited_scheduled_reports": False,
    },
    "advanced": {
        "plan_code": "advanced",
        "price_monthly_usd": 99,
        "reports_limit_monthly": None,
        "reports_limit_is_temporary": False,
        "slides_per_report_limit": 30,
        "platform_report_type": "multi_platform",
        "ai_chat_with_data": True,
        "storage_limit_gb": 10,
        "export_pdf": True,
        "export_pptx": True,
        "brand_personalization": True,
        "measurable_watermark": False,
        "scheduled_reports_limit": None,
        "trial_new_features": True,
        "unlimited_reports": True,
        "unlimited_scheduled_reports": True,
    },
}

MEASURABLE_BRANDING_NAME = "Measurable"
MEASURABLE_REPORT_BRANDING_NAME = "Measurableapp.com Report Generator"
MEASURABLE_WATERMARK_LABEL = "Created with measurableapp.com"
MEASURABLE_BRANDING_LOGO_URL: str = str(
    os.getenv("MEASURABLE_BRANDING_LOGO_URL") or "http://localhost:3000/brand/measurable-logo.svg"
).strip()
MEASURABLE_WATERMARK_LOGO_LIGHT_URL: str = str(
    os.getenv("MEASURABLE_WATERMARK_LOGO_LIGHT_URL") or "/brand/measurable-logo-black.png"
).strip()
MEASURABLE_WATERMARK_LOGO_DARK_URL: str = str(
    os.getenv("MEASURABLE_WATERMARK_LOGO_DARK_URL") or "/brand/measurable-logo-white.png"
).strip()


def normalize_workspace_plan(plan: Any) -> str:
    normalized = str(plan or DEFAULT_WORKSPACE_PLAN).strip().lower()
    normalized = PLAN_ALIASES.get(normalized, normalized)
    if normalized in PLAN_ENTITLEMENTS:
        return normalized
    return DEFAULT_WORKSPACE_PLAN


def get_plan_entitlements(plan_code: str) -> dict[str, Any]:
    normalized_plan = normalize_workspace_plan(plan_code)
    return dict(PLAN_ENTITLEMENTS[normalized_plan])


def get_plan_limits(plan: str) -> dict[str, Any]:
    entitlements = get_plan_entitlements(plan)
    storage_limit_gb = int(entitlements["storage_limit_gb"])
    reports_limit_monthly = entitlements["reports_limit_monthly"]
    return {
        **entitlements,
        "reports_per_month": reports_limit_monthly,
        "max_slides_per_report": int(entitlements["slides_per_report_limit"]),
        "max_slides": int(entitlements["slides_per_report_limit"]),
        "storage_limit_bytes": storage_limit_gb * 1024 * 1024 * 1024,
        "allow_pdf_export": bool(entitlements["export_pdf"]),
        "allow_pptx_export": bool(entitlements["export_pptx"]),
        "allow_custom_branding": bool(entitlements["brand_personalization"]),
        "allow_ai_agents": normalize_workspace_plan(plan) in {"pro", "advanced"},
    }


def get_stripe_price_plan_mapping() -> dict[str, str]:
    mapping = {
        str(settings.stripe_price_starter_monthly or "").strip(): "starter",
        str(settings.stripe_price_pro_monthly or "").strip(): "pro",
        str(settings.stripe_price_advanced_monthly or "").strip(): "advanced",
    }
    return {price_id: plan_code for price_id, plan_code in mapping.items() if price_id}


def get_plan_code_for_stripe_price(price_id: str | None) -> str | None:
    normalized_price_id = str(price_id or "").strip()
    if not normalized_price_id:
        return None
    return get_stripe_price_plan_mapping().get(normalized_price_id)


def get_workspace_subscription(db: Session, workspace_id: int) -> Subscription | None:
    active_subscription = (
        db.query(Subscription)
        .filter(Subscription.workspace_id == workspace_id, Subscription.status == "active")
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
    )
    if active_subscription is not None:
        return active_subscription
    return (
        db.query(Subscription)
        .filter(Subscription.workspace_id == workspace_id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
    )


def get_workspace_plan(db: Session, workspace_id: int) -> str:
    active_subscription = get_workspace_subscription(db, workspace_id)
    if active_subscription:
        return normalize_workspace_plan(active_subscription.plan)

    return DEFAULT_WORKSPACE_PLAN


def get_plan_capabilities(plan: str) -> dict[str, Any]:
    limits = get_plan_limits(plan)
    return {
        "max_slides": int(limits["max_slides"]),
        "allow_pdf_export": bool(limits["allow_pdf_export"]),
        "allow_pptx_export": bool(limits["allow_pptx_export"]),
        "allow_ai_agents": bool(limits["allow_ai_agents"]),
        "allow_custom_branding": bool(limits["allow_custom_branding"]),
    }


def report_branding_mode_for_plan(plan: str | None) -> str:
    return "custom" if get_plan_capabilities(str(plan or DEFAULT_WORKSPACE_PLAN))["allow_custom_branding"] else "measurable"


def get_workspace_plan_capabilities(db: Session, workspace_id: int) -> dict[str, Any]:
    plan = get_workspace_plan(db, workspace_id)
    return {"plan": plan, "capabilities": get_plan_capabilities(plan)}


def get_workspace_plan_details(db: Session, workspace_id: int) -> dict[str, Any]:
    plan = get_workspace_plan(db, workspace_id)
    return {"plan": plan, "limits": get_plan_limits(plan)}


def apply_plan_entitlements(subscription: Subscription, plan_code: str) -> Subscription:
    normalized_plan = normalize_workspace_plan(plan_code)
    entitlements = get_plan_entitlements(normalized_plan)
    subscription.plan = normalized_plan
    subscription.reports_limit_monthly = entitlements["reports_limit_monthly"]
    subscription.reports_limit_is_temporary = bool(entitlements["reports_limit_is_temporary"])
    subscription.slides_per_report_limit = int(entitlements["slides_per_report_limit"])
    subscription.platform_report_type = str(entitlements["platform_report_type"])
    subscription.ai_chat_with_data = bool(entitlements["ai_chat_with_data"])
    subscription.storage_limit_gb = int(entitlements["storage_limit_gb"])
    subscription.export_pdf = bool(entitlements["export_pdf"])
    subscription.export_pptx = bool(entitlements["export_pptx"])
    subscription.brand_personalization = bool(entitlements["brand_personalization"])
    subscription.measurable_watermark = bool(entitlements["measurable_watermark"])
    subscription.scheduled_reports_limit = entitlements["scheduled_reports_limit"]
    subscription.trial_new_features = bool(entitlements["trial_new_features"])
    return subscription


def get_subscription_entitlements(subscription: Subscription | None) -> dict[str, Any]:
    if subscription is None:
        return get_plan_entitlements(DEFAULT_WORKSPACE_PLAN)
    fallback = get_plan_entitlements(subscription.plan or DEFAULT_WORKSPACE_PLAN)
    return {
        **fallback,
        "plan_code": normalize_workspace_plan(subscription.plan or DEFAULT_WORKSPACE_PLAN),
        "reports_limit_monthly": (
            subscription.reports_limit_monthly
            if subscription.reports_limit_monthly is not None or fallback["reports_limit_monthly"] is None
            else fallback["reports_limit_monthly"]
        ),
        "reports_limit_is_temporary": (
            bool(subscription.reports_limit_is_temporary)
            if subscription.reports_limit_is_temporary is not None
            else bool(fallback["reports_limit_is_temporary"])
        ),
        "slides_per_report_limit": int(subscription.slides_per_report_limit or fallback["slides_per_report_limit"]),
        "platform_report_type": str(subscription.platform_report_type or fallback["platform_report_type"]),
        "ai_chat_with_data": (
            bool(subscription.ai_chat_with_data)
            if subscription.ai_chat_with_data is not None
            else bool(fallback["ai_chat_with_data"])
        ),
        "storage_limit_gb": int(subscription.storage_limit_gb or fallback["storage_limit_gb"]),
        "export_pdf": bool(subscription.export_pdf) if subscription.export_pdf is not None else bool(fallback["export_pdf"]),
        "export_pptx": (
            bool(subscription.export_pptx)
            if subscription.export_pptx is not None
            else bool(fallback["export_pptx"])
        ),
        "brand_personalization": (
            bool(subscription.brand_personalization)
            if subscription.brand_personalization is not None
            else bool(fallback["brand_personalization"])
        ),
        "measurable_watermark": (
            bool(subscription.measurable_watermark)
            if subscription.measurable_watermark is not None
            else bool(fallback["measurable_watermark"])
        ),
        "scheduled_reports_limit": (
            subscription.scheduled_reports_limit
            if subscription.scheduled_reports_limit is not None or fallback["scheduled_reports_limit"] is None
            else fallback["scheduled_reports_limit"]
        ),
        "trial_new_features": (
            bool(subscription.trial_new_features)
            if subscription.trial_new_features is not None
            else bool(fallback["trial_new_features"])
        ),
        "unlimited_reports": bool(fallback["unlimited_reports"]),
        "unlimited_scheduled_reports": bool(fallback["unlimited_scheduled_reports"]),
    }


def get_workspace_storage_limit(db: Session, workspace_id: int) -> int:
    plan_details = get_workspace_plan_details(db, workspace_id)
    return int(plan_details["limits"]["storage_limit_bytes"])


def count_workspace_storage_bytes(db: Session, workspace_id: int) -> int:
    try:
        table_names = set(inspect(engine).get_table_names())
    except SQLAlchemyError:
        return 0
    if "dataset_files" not in table_names:
        return 0
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
    return int(get_workspace_report_quota_status(db, workspace_id, now=now)["reports_used"])


def count_workspace_reports_total(db: Session, workspace_id: int) -> int:
    count = db.query(func.count(Report.id)).filter(Report.workspace_id == workspace_id).scalar()
    return int(count or 0)


def _normalize_quota_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _default_report_quota_period(*, now: datetime | None = None) -> tuple[datetime, datetime]:
    current_time = _normalize_quota_datetime(now) or datetime.now(timezone.utc)
    month_start = current_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)
    return month_start, next_month_start


def resolve_report_quota_period(
    subscription: Subscription | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    period_start = _normalize_quota_datetime(
        subscription.current_period_start if subscription is not None else None
    )
    period_end = _normalize_quota_datetime(
        subscription.current_period_end if subscription is not None else None
    )
    if period_start is not None and period_end is not None and period_end > period_start:
        return period_start, period_end
    return _default_report_quota_period(now=now)


def get_workspace_report_quota_status(
    db: Session,
    workspace_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    subscription = get_workspace_subscription(db, workspace_id)
    plan = normalize_workspace_plan(subscription.plan) if subscription is not None else DEFAULT_WORKSPACE_PLAN
    limits = get_plan_limits(plan)
    report_limit = limits["reports_per_month"]
    period_start, period_end = resolve_report_quota_period(subscription, now=now)

    reports_used = int(
        db.query(func.count(Report.id))
        .filter(Report.workspace_id == workspace_id)
        .filter(Report.created_at >= period_start)
        .filter(Report.created_at < period_end)
        .scalar()
        or 0
    )
    reports_limit = int(report_limit) if report_limit is not None else None
    reports_remaining = None if reports_limit is None else max(reports_limit - reports_used, 0)
    limit_reached = False if reports_limit is None else reports_used >= reports_limit
    return {
        "reports_used": reports_used,
        "reports_limit": reports_limit,
        "reports_remaining": reports_remaining,
        "limit_reached": limit_reached,
        "period_start": period_start,
        "period_end": period_end,
        "plan": plan,
    }


def get_remaining_reports(db: Session, workspace_id: int) -> int | None:
    return get_workspace_report_quota_status(db, workspace_id)["reports_remaining"]


def can_create_report(db: Session, workspace_id: int) -> bool:
    return get_remaining_reports(db, workspace_id) != 0


def can_export_pptx(db: Session, workspace_id: int) -> bool:
    return bool(get_plan_limits(get_workspace_plan(db, workspace_id))["export_pptx"])


def can_use_brand_personalization(db: Session, workspace_id: int) -> bool:
    return bool(get_plan_limits(get_workspace_plan(db, workspace_id))["brand_personalization"])


def can_use_multi_platform_report(db: Session, workspace_id: int) -> bool:
    return str(get_plan_limits(get_workspace_plan(db, workspace_id))["platform_report_type"]) != "single_platform"


def can_schedule_report(db: Session, workspace_id: int) -> bool:
    limits = get_plan_limits(get_workspace_plan(db, workspace_id))
    scheduled_limit = limits["scheduled_reports_limit"]
    if scheduled_limit is None:
        return True
    if int(scheduled_limit) <= 0:
        return False
    existing_count = (
        db.query(func.count(Schedule.id))
        .filter(Schedule.workspace_id == workspace_id, Schedule.enabled.is_(True))
        .scalar()
    )
    return int(existing_count or 0) < int(scheduled_limit)


def enforce_monthly_report_limit(db: Session, workspace_id: int) -> dict[str, Any]:
    plan_details = get_workspace_plan_details(db, workspace_id)
    quota = get_workspace_report_quota_status(db, workspace_id)
    if quota["reports_limit"] is None:
        return plan_details

    if bool(quota["limit_reached"]):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "monthly_report_limit_reached",
                "message": "You have reached your monthly report limit.",
                "reports_used": quota["reports_used"],
                "reports_limit": quota["reports_limit"],
                "reports_remaining": quota["reports_remaining"],
                "period_start": quota["period_start"].isoformat() if quota["period_start"] else None,
                "period_end": quota["period_end"].isoformat() if quota["period_end"] else None,
                "plan": quota["plan"],
            },
        )
    return plan_details


def enforce_report_creation_limit(db: Session, workspace_id: int) -> dict[str, Any]:
    return enforce_monthly_report_limit(db, workspace_id)


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


def resolve_account_display_name(
    workspace: Workspace | dict[str, Any] | None,
    user: User | dict[str, Any] | None = None,
) -> dict[str, str | None]:
    explicit_name: str | None = None
    workspace_name: str | None = None
    user_full_name: str | None = None
    user_email: str | None = None

    if isinstance(workspace, dict):
        explicit_name = str(workspace.get("account_display_name") or "").strip() or None
        workspace_name = str(workspace.get("name") or "").strip() or None
    elif workspace is not None:
        explicit_name = str(getattr(workspace, "account_display_name", "") or "").strip() or None
        workspace_name = str(getattr(workspace, "name", "") or "").strip() or None

    if isinstance(user, dict):
        user_full_name = str(user.get("full_name") or "").strip() or None
        user_email = str(user.get("email") or "").strip() or None
    elif user is not None:
        user_full_name = str(getattr(user, "full_name", "") or "").strip() or None
        user_email = str(getattr(user, "email", "") or "").strip() or None

    effective_name = explicit_name or workspace_name or user_full_name or user_email or "Measurable Account"
    return {
        "account_display_name": explicit_name,
        "account_display_name_effective": effective_name,
    }


def register_user_with_default_workspace(
    db: Session,
    *,
    email: str,
    password_hash: str,
    full_name: str | None,
    email_verified: bool = False,
    auth_provider: str = "email",
    google_sub: str | None = None,
    facebook_sub: str | None = None,
    last_login_at: datetime | None = None,
) -> tuple[User, Workspace, Subscription]:
    user = User(
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        email_verified=email_verified,
        auth_provider=auth_provider,
        google_sub=google_sub,
        facebook_sub=facebook_sub,
        is_admin=False,
        onboarding_completed=False,
        user_type=None,
        goals=[],
        platforms=[],
        is_deleted=False,
        deleted_at=None,
        last_login_at=last_login_at,
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
        billing_status="free",
    )
    apply_plan_entitlements(subscription, DEFAULT_WORKSPACE_PLAN)
    db.add(subscription)
    return user, workspace, subscription


REFERRAL_PAID_CONVERSION_TYPES = {"paid_subscription", "upgrade", "renewal"}


def normalize_referral_code(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def hash_client_ip(ip_address: str | None) -> str | None:
    normalized_ip = str(ip_address or "").strip()
    if not normalized_ip:
        return None
    salt = str(settings.jwt_secret or "measurable").strip()
    return hashlib.sha256(f"{salt}:{normalized_ip}".encode("utf-8")).hexdigest()


def calculate_referral_commission(
    *,
    partner: ReferralPartner | None,
    amount: Any,
) -> Decimal | None:
    if partner is None or partner.commission_type in {None, "", "none"}:
        return None
    commission_type = str(partner.commission_type or "").strip().lower()
    commission_value = partner.commission_value
    if commission_value is None:
        return None
    commission_decimal = Decimal(str(commission_value))
    if commission_type == "fixed":
        return commission_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if commission_type != "percentage" or amount is None:
        return None
    amount_decimal = Decimal(str(amount))
    return ((amount_decimal * commission_decimal) / Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def create_referral_click(
    db: Session,
    *,
    referral_code: str | None,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
    utm_term: str | None,
    utm_content: str | None,
    landing_page: str | None,
    ip_hash: str | None,
    user_agent: str | None,
) -> ReferralClick:
    click = ReferralClick(
        referral_code=normalize_referral_code(referral_code),
        utm_source=str(utm_source or "").strip() or None,
        utm_medium=str(utm_medium or "").strip() or None,
        utm_campaign=str(utm_campaign or "").strip() or None,
        utm_term=str(utm_term or "").strip() or None,
        utm_content=str(utm_content or "").strip() or None,
        landing_page=str(landing_page or "").strip() or None,
        ip_hash=str(ip_hash or "").strip() or None,
        user_agent=str(user_agent or "").strip() or None,
    )
    db.add(click)
    db.commit()
    db.refresh(click)
    return click


def create_or_update_user_attribution(
    db: Session,
    *,
    user_id: int,
    referral_code: str | None,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
    utm_term: str | None,
    utm_content: str | None,
    signup_at: datetime | None = None,
) -> UserAttribution:
    normalized_referral_code = normalize_referral_code(referral_code)
    normalized_utm_source = str(utm_source or "").strip() or None
    normalized_utm_medium = str(utm_medium or "").strip() or None
    normalized_utm_campaign = str(utm_campaign or "").strip() or None
    normalized_utm_term = str(utm_term or "").strip() or None
    normalized_utm_content = str(utm_content or "").strip() or None
    now = signup_at or datetime.now(timezone.utc)

    attribution = (
        db.query(UserAttribution)
        .filter(UserAttribution.user_id == user_id)
        .first()
    )
    if attribution is None:
        attribution = UserAttribution(
            user_id=user_id,
            first_referral_code=normalized_referral_code,
            last_referral_code=normalized_referral_code,
            utm_source=normalized_utm_source,
            utm_medium=normalized_utm_medium,
            utm_campaign=normalized_utm_campaign,
            utm_term=normalized_utm_term,
            utm_content=normalized_utm_content,
            first_touch_at=now if any(
                [
                    normalized_referral_code,
                    normalized_utm_source,
                    normalized_utm_medium,
                    normalized_utm_campaign,
                    normalized_utm_term,
                    normalized_utm_content,
                ]
            ) else None,
            signup_at=signup_at,
        )
        db.add(attribution)
        db.flush()
        return attribution

    if attribution.first_referral_code is None and normalized_referral_code is not None:
        attribution.first_referral_code = normalized_referral_code
    if normalized_referral_code is not None:
        attribution.last_referral_code = normalized_referral_code
    if attribution.utm_source is None and normalized_utm_source is not None:
        attribution.utm_source = normalized_utm_source
    if attribution.utm_medium is None and normalized_utm_medium is not None:
        attribution.utm_medium = normalized_utm_medium
    if attribution.utm_campaign is None and normalized_utm_campaign is not None:
        attribution.utm_campaign = normalized_utm_campaign
    if attribution.utm_term is None and normalized_utm_term is not None:
        attribution.utm_term = normalized_utm_term
    if attribution.utm_content is None and normalized_utm_content is not None:
        attribution.utm_content = normalized_utm_content
    if attribution.first_touch_at is None and any(
        [
            normalized_referral_code,
            normalized_utm_source,
            normalized_utm_medium,
            normalized_utm_campaign,
            normalized_utm_term,
            normalized_utm_content,
        ]
    ):
        attribution.first_touch_at = now
    if attribution.signup_at is None and signup_at is not None:
        attribution.signup_at = signup_at
    db.add(attribution)
    db.flush()
    return attribution


def create_referral_conversion(
    db: Session,
    *,
    user_id: int,
    referral_code: str | None,
    conversion_type: str,
    plan: str | None = None,
    amount: Any = None,
    currency: str | None = "USD",
    commission_amount: Any = None,
    status: str = "pending",
    allow_duplicate: bool = True,
) -> ReferralConversion:
    normalized_referral_code = normalize_referral_code(referral_code)
    normalized_type = str(conversion_type or "").strip()
    if not allow_duplicate:
        existing = (
            db.query(ReferralConversion)
            .filter(
                ReferralConversion.user_id == user_id,
                ReferralConversion.conversion_type == normalized_type,
            )
            .order_by(ReferralConversion.created_at.desc(), ReferralConversion.id.desc())
            .first()
        )
        if existing is not None:
            return existing
    conversion = ReferralConversion(
        user_id=user_id,
        referral_code=normalized_referral_code,
        conversion_type=normalized_type,
        plan=str(plan or "").strip() or None,
        amount=Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount is not None
        else None,
        currency=str(currency or "USD").strip() or "USD",
        commission_amount=Decimal(str(commission_amount)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if commission_amount is not None
        else None,
        status=str(status or "pending").strip() or "pending",
    )
    db.add(conversion)
    db.flush()
    return conversion


def record_signup_attribution(
    db: Session,
    *,
    user: User,
    referral_code: str | None,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
    utm_term: str | None,
    utm_content: str | None,
) -> tuple[UserAttribution | None, ReferralConversion | None]:
    signup_timestamp = datetime.now(timezone.utc)
    try:
        attribution = create_or_update_user_attribution(
            db,
            user_id=user.id,
            referral_code=referral_code,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            signup_at=signup_timestamp,
        )
        conversion = create_referral_conversion(
            db,
            user_id=user.id,
            referral_code=attribution.first_referral_code,
            conversion_type="signup",
            status="approved",
            allow_duplicate=False,
        )
        return attribution, conversion
    except Exception as exc:
        if not _is_missing_optional_referral_table_error(exc):
            raise
        db.rollback()
        _log_optional_referral_table_unavailable(
            exc,
            operation="record_signup_attribution",
            user_id=user.id,
        )
        return None, None


def record_first_report_conversion(
    db: Session,
    *,
    user_id: int,
) -> ReferralConversion | None:
    try:
        attribution = (
            db.query(UserAttribution)
            .filter(UserAttribution.user_id == user_id)
            .first()
        )
        referral_code = attribution.first_referral_code if attribution is not None else None
        if referral_code is None:
            existing = (
                db.query(ReferralConversion)
                .filter(
                    ReferralConversion.user_id == user_id,
                    ReferralConversion.conversion_type == "first_report",
                )
                .first()
            )
            return existing
        return create_referral_conversion(
            db,
            user_id=user_id,
            referral_code=referral_code,
            conversion_type="first_report",
            status="approved",
            allow_duplicate=False,
        )
    except Exception as exc:
        if not _is_missing_optional_referral_table_error(exc):
            raise
        db.rollback()
        _log_optional_referral_table_unavailable(
            exc,
            operation="record_first_report_conversion",
            user_id=user_id,
        )
        return None


def create_manual_referral_conversion(
    db: Session,
    *,
    user_id: int,
    conversion_type: str,
    plan: str | None,
    amount: Any,
    currency: str | None,
) -> ReferralConversion:
    try:
        attribution = (
            db.query(UserAttribution)
            .filter(UserAttribution.user_id == user_id)
            .first()
        )
        referral_code = attribution.first_referral_code if attribution is not None else None
        partner = None
        if referral_code is not None:
            partner = (
                db.query(ReferralPartner)
                .filter(ReferralPartner.code == referral_code)
                .first()
            )
        commission_amount = calculate_referral_commission(partner=partner, amount=amount)
        conversion = create_referral_conversion(
            db,
            user_id=user_id,
            referral_code=referral_code,
            conversion_type=conversion_type,
            plan=plan,
            amount=amount,
            currency=currency,
            commission_amount=commission_amount,
            status="approved",
            allow_duplicate=True,
        )
        db.commit()
        db.refresh(conversion)
        return conversion
    except Exception as exc:
        if not _is_missing_optional_referral_table_error(exc):
            raise
        db.rollback()
        _log_optional_referral_table_unavailable(
            exc,
            operation="create_manual_referral_conversion",
            user_id=user_id,
        )
        raise http_error(
            503,
            "referral_tracking_unavailable",
            "Referral tracking is temporarily unavailable.",
        )


AUTH_CODE_PURPOSE_EMAIL_VERIFICATION = "email_verification"
AUTH_CODE_PURPOSE_PASSWORD_RESET = "password_reset"
AUTH_CODE_TTL_MINUTES = 15
AUTH_CODE_RESEND_COOLDOWN_SECONDS = 60
AUTH_CODE_MAX_ATTEMPTS = 5


def generate_six_digit_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _auth_code_subject(purpose: str) -> str:
    if purpose == AUTH_CODE_PURPOSE_PASSWORD_RESET:
        return "Reset your Measurable password"
    return "Your Measurable verification code"


def _auth_code_action_label(purpose: str) -> str:
    if purpose == AUTH_CODE_PURPOSE_PASSWORD_RESET:
        return "reset your password"
    return "verify your email"


def build_auth_email_html(
    *,
    full_name: str | None,
    code: str,
    purpose: str,
    expires_minutes: int = AUTH_CODE_TTL_MINUTES,
) -> str:
    safe_name = (full_name or "there").strip() or "there"
    subject = _auth_code_subject(purpose)
    is_password_reset = purpose == AUTH_CODE_PURPOSE_PASSWORD_RESET
    title = "Reset your password" if is_password_reset else "Verify your email"
    subtitle = (
        "Use the code below to reset your password"
        if is_password_reset
        else "Use the code below to verify your account"
    )
    footer_hint = (
        "If you didn’t request this, you can ignore this email."
        if not is_password_reset
        else "If you didn’t request a password reset, you can ignore this email."
    )
    return f"""\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{subject}</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#111827;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f7fb;width:100%;border-collapse:collapse;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:480px;width:100%;border-collapse:collapse;">
            <tr>
              <td style="background:#ffffff;border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,0.08);padding:32px;">
                <div style="font-size:14px;line-height:20px;font-weight:600;color:#6b7280;letter-spacing:0.02em;margin:0 0 20px;">
                  Measurable
                </div>

                <h1 style="margin:0 0 12px;font-size:24px;line-height:32px;font-weight:700;color:#111827;">
                  {title}
                </h1>

                <p style="margin:0 0 24px;font-size:15px;line-height:24px;color:#374151;">
                  {subtitle}
                </p>

                <p style="margin:0 0 16px;font-size:14px;line-height:22px;color:#6b7280;">
                  Hi {safe_name},
                </p>

                <div style="background:#f1f3f5;border-radius:8px;padding:16px;text-align:center;margin:0 0 16px;">
                  <div style="font-size:32px;line-height:40px;font-weight:700;letter-spacing:8px;color:#111827;">
                    {code}
                  </div>
                </div>

                <p style="margin:0 0 24px;font-size:14px;line-height:22px;color:#6b7280;text-align:center;">
                  This code expires in {expires_minutes} minutes
                </p>

                <div style="border-top:1px solid #e5e7eb;padding-top:16px;">
                  <p style="margin:0 0 8px;font-size:12px;line-height:18px;color:#9ca3af;">
                    {footer_hint}
                  </p>
                  <p style="margin:0;font-size:12px;line-height:18px;color:#9ca3af;">
                    Measurable — AI Report Generator
                  </p>
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def build_auth_email_text(
    *,
    full_name: str | None,
    code: str,
    purpose: str,
    expires_minutes: int = AUTH_CODE_TTL_MINUTES,
) -> str:
    safe_name = (full_name or "there").strip() or "there"
    action_label = _auth_code_action_label(purpose)
    footer_hint = (
        "If you did not request this email, you can ignore it."
        if purpose != AUTH_CODE_PURPOSE_PASSWORD_RESET
        else "If you did not request a password reset, you can ignore it."
    )
    return (
        f"Hi {safe_name},\n\n"
        f"Use this code to {action_label}: {code}\n\n"
        f"This code expires in {expires_minutes} minutes.\n"
        f"{footer_hint}\n"
    )


def _ses_client() -> Any:
    client_kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id:
        client_kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        client_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.aws_session_token:
        client_kwargs["aws_session_token"] = settings.aws_session_token
    return boto3.client("ses", **client_kwargs)


def _safe_email_delivery_reason(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error") or {}
        code = str(error.get("Code") or "").strip()
        message = str(error.get("Message") or "").strip()
        if code and message:
            return _truncate_log_value(f"{code}: {message}", limit=200) or exc.__class__.__name__
        if code:
            return code[:200]
    return exc.__class__.__name__


def _mask_email(email: str) -> str:
    value = email.strip()
    if "@" not in value:
        return "***"
    local_part, domain = value.split("@", 1)
    if not local_part:
        return f"***@{domain}"
    if len(local_part) == 1:
        return f"{local_part[0]}***@{domain}"
    return f"{local_part[0]}***{local_part[-1]}@{domain}"


def send_auth_email(
    *,
    recipient_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    purpose: str,
) -> str | None:
    from_email = str(settings.ses_from_email or "").strip()
    configuration_set_name = str(settings.ses_configuration_set_name or "").strip() or None
    masked_email = _mask_email(recipient_email)
    if not from_email:
        raise http_error(503, "email_service_unavailable", "Email service is not configured.")

    logger.info(
        "SES_EMAIL_SEND_ATTEMPT",
        extra={
            "purpose": purpose,
            "email": masked_email,
        },
    )

    try:
        ses = _ses_client()
        send_kwargs: dict[str, Any] = {
            "Source": from_email,
            "Destination": {"ToAddresses": [recipient_email]},
            "ReplyToAddresses": ["hello@measurableapp.com"],
            "Tags": [
                {"Name": "purpose", "Value": purpose},
                {"Name": "environment", "Value": "production"},
            ],
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        }
        if configuration_set_name:
            send_kwargs["ConfigurationSetName"] = configuration_set_name
        response = ses.send_email(
            **send_kwargs,
        )
        message_id = str(response.get("MessageId") or "").strip() or None
        logger.info(
            "SES_EMAIL_SENT",
            extra={
                "purpose": purpose,
                "message_id": message_id,
                "configuration_set": configuration_set_name,
            },
        )
        return message_id
    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
        safe_reason = _safe_email_delivery_reason(exc)
        logger.warning(
            "SES_EMAIL_FAILED",
            extra={
                "purpose": purpose,
                "reason": safe_reason,
            },
        )
        logger.exception(
            "auth_email_send_failed",
            extra={
                "recipient_domain": recipient_email.split("@")[-1] if "@" in recipient_email else None,
                "exception_class": exc.__class__.__name__,
                "reply_to": "hello@measurableapp.com",
                "reason": safe_reason,
                "aws_region": settings.aws_region,
                "from_email_configured": bool(from_email),
            },
        )
        raise http_error(503, "email_delivery_failed", "Unable to send verification email.") from exc


def _latest_auth_code(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    include_expired: bool = True,
) -> EmailVerificationCode | None:
    query = (
        db.query(EmailVerificationCode)
        .filter(EmailVerificationCode.user_id == user_id, EmailVerificationCode.purpose == purpose)
        .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
    )
    if not include_expired:
        query = query.filter(EmailVerificationCode.used_at.is_(None))
    return query.first()


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def issue_auth_code(
    db: Session,
    *,
    user: User,
    purpose: str,
    cooldown_seconds: int = AUTH_CODE_RESEND_COOLDOWN_SECONDS,
    ttl_minutes: int = AUTH_CODE_TTL_MINUTES,
) -> str:
    now = datetime.now(timezone.utc)
    latest = _latest_auth_code(db, user_id=user.id, purpose=purpose)
    if latest and latest.used_at is None:
        latest_expires_at = _as_utc_datetime(latest.expires_at)
        latest_created_at = _as_utc_datetime(latest.created_at)
        if latest_expires_at and latest_expires_at > now:
            elapsed_seconds = (now - latest_created_at).total_seconds() if latest_created_at else None
            if elapsed_seconds is not None and elapsed_seconds < cooldown_seconds:
                raise http_error(
                    429,
                    "code_resend_rate_limited",
                    "Please wait before requesting another code.",
                )
        latest.used_at = now
        db.add(latest)

    code = generate_six_digit_code()
    code_row = EmailVerificationCode(
        user_id=user.id,
        purpose=purpose,
        code_hash=hash_verification_code(code),
        expires_at=now + timedelta(minutes=ttl_minutes),
        attempts=0,
    )
    db.add(code_row)
    db.flush()
    logger.info(
        "auth_code_issued",
        extra={
            "user_id": user.id,
            "purpose": purpose,
            "expires_at": code_row.expires_at.isoformat() if code_row.expires_at else None,
        },
    )
    return code


def validate_auth_code(
    db: Session,
    *,
    user: User,
    code: str,
    purpose: str,
) -> EmailVerificationCode:
    now = datetime.now(timezone.utc)
    auth_code = _latest_auth_code(db, user_id=user.id, purpose=purpose)
    expires_at = _as_utc_datetime(auth_code.expires_at) if auth_code else None
    if (
        not auth_code
        or auth_code.used_at is not None
        or expires_at is None
        or expires_at <= now
        or auth_code.attempts >= AUTH_CODE_MAX_ATTEMPTS
    ):
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired verification code.")

    if not verify_verification_code(code, auth_code.code_hash):
        auth_code.attempts += 1
        if auth_code.attempts >= AUTH_CODE_MAX_ATTEMPTS:
            auth_code.used_at = now
        db.add(auth_code)
        db.flush()
        db.commit()
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired verification code.")

    auth_code.used_at = now
    db.add(auth_code)
    db.flush()
    return auth_code


def build_conversation_title(message: str) -> str:
    normalized = " ".join(str(message or "").strip().split())
    if not normalized:
        return "New Conversation"
    return normalized[:80]


def build_workspace_ai_no_data_response() -> str:
    return "No report context is available yet. Open a report and ask me about its metrics."


AI_PRODUCT_REDIRECT_RESPONSE = (
    "Desde este asistente puedo ayudarte a interpretar el reporte abierto y relacionarlo con datos adicionales "
    "que me compartas aquí. Para funciones de carga, generación o configuración de la plataforma, revisa el flujo principal de Measurable."
)

AI_PRODUCT_QUESTION_PATTERNS = (
    re.compile(r"\b(subir|cargar|adjuntar)\b.*\b(excel|csv|archivo)\b", re.IGNORECASE),
    re.compile(r"\b(excel|csv)\b.*\b(reporte|report)\b", re.IGNORECASE),
    re.compile(r"\b(conectar|integrar|integration|integraci[oó]n)\b", re.IGNORECASE),
    re.compile(r"\b(exportar|descargar)\b.*\b(pptx|pdf|powerpoint)\b", re.IGNORECASE),
    re.compile(r"\b(automatizar|programar|schedule|scheduled)\b.*\b(reportes|reporte|reports|report)\b", re.IGNORECASE),
    re.compile(r"\b(onboarding|pricing|planes|plan upgrade|upgrade)\b", re.IGNORECASE),
)


def _is_ai_product_question(user_message: str) -> bool:
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return False
    return any(pattern.search(text) for pattern in AI_PRODUCT_QUESTION_PATTERNS)


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


def _truncate_text_for_ai(value: Any, limit: int = 280) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _summarize_ai_value(value: Any, *, list_limit: int = 5, text_limit: int = 280) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text_for_ai(value, text_limit)
    if isinstance(value, list):
        items = [_summarize_ai_value(item, list_limit=3, text_limit=160) for item in value[:list_limit]]
        if len(value) > list_limit:
            items.append({"truncated_items": len(value) - list_limit})
        return items
    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        for key in list(value.keys())[:12]:
            summary[str(key)] = _summarize_ai_value(value.get(key), list_limit=3, text_limit=160)
        if len(value) > 12:
            summary["_truncated_keys"] = len(value) - 12
        return summary
    return _truncate_text_for_ai(value, text_limit)


def _extract_dataset_sample_rows(dataset_data: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    for key in ("rows", "records", "items", "data"):
        candidate = dataset_data.get(key)
        if isinstance(candidate, list) and candidate and all(isinstance(item, dict) for item in candidate[: min(len(candidate), 3)]):
            return [
                _summarize_ai_value(item, list_limit=3, text_limit=160)
                for item in candidate[:limit]
                if isinstance(item, dict)
            ]
    return []


def _build_dataset_snapshot_for_ai(resolved_dataset: Dataset | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dataset_data = resolved_dataset.data if resolved_dataset and isinstance(resolved_dataset.data, dict) else {}
    report_inputs = extract_meta_pages_report_inputs(dataset_data) if dataset_data else {}
    normalized_metrics = (
        dataset_data.get("normalized_report_metrics")
        if isinstance(dataset_data.get("normalized_report_metrics"), dict)
        else {}
    )
    metric_summary: dict[str, Any] = {}
    for key in (
        "reach",
        "engagement",
        "followers",
        "impressions",
        "profile_visits",
        "link_clicks",
        "interactions_total",
        "impressions_total",
        "followers_growth_total",
        "page_visits_total",
    ):
        value = dataset_data.get(key)
        if value is None:
            value = normalized_metrics.get(key)
        if value is not None:
            metric_summary[key] = value

    dataset_summary: dict[str, Any] = {
        "integration_type": dataset_data.get("integration_type"),
        "page_name": dataset_data.get("page_name"),
        "account_name": dataset_data.get("account_name") or dataset_data.get("name"),
        "username": dataset_data.get("username") or dataset_data.get("instagram_username"),
        "timeframe": _summarize_ai_value(dataset_data.get("timeframe"), list_limit=4, text_limit=120),
        "metrics": metric_summary,
        "sample_rows": _extract_dataset_sample_rows(dataset_data),
    }

    report_inputs_for_ai = _summarize_ai_value(report_inputs, list_limit=6, text_limit=180)
    if not isinstance(report_inputs_for_ai, dict):
        report_inputs_for_ai = {}
    if resolved_dataset is not None:
        report_inputs_for_ai.setdefault("dataset_id", resolved_dataset.id)
        report_inputs_for_ai.setdefault("dataset_name", resolved_dataset.name)
        report_inputs_for_ai.setdefault("dataset_description", _truncate_text_for_ai(resolved_dataset.description, 180))
        report_inputs_for_ai.setdefault("dataset_summary", dataset_summary)

    dataset_snapshot: dict[str, Any] = {
        "id": resolved_dataset.id if resolved_dataset is not None else None,
        "name": resolved_dataset.name if resolved_dataset is not None else None,
        "description": resolved_dataset.description if resolved_dataset is not None else None,
        "summary": dataset_summary,
        "file": {},
    }
    return dataset_snapshot, report_inputs_for_ai, dataset_data


def _build_report_blocks_snapshot_for_ai(report_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summarized_blocks: list[dict[str, Any]] = []
    for block in report_blocks[:20]:
        block_data = block.get("data") if isinstance(block.get("data"), dict) else {}
        summarized_blocks.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "order": block.get("order"),
                "title": _truncate_text_for_ai(block_data.get("title"), 180),
                "subtitle": _truncate_text_for_ai(block_data.get("subtitle"), 220),
                "metric_key": block_data.get("metric_key"),
                "current_value": block_data.get("current_value", block_data.get("value")),
                "insight": _truncate_text_for_ai(
                    block_data.get("insight_short") or block_data.get("insight") or block_data.get("summary"),
                    260,
                ),
            }
        )
    return summarized_blocks


def build_ai_chat_context_snapshot(
    db: Session,
    *,
    workspace_id: int,
    report: Report | None = None,
    dataset: Dataset | None = None,
    current_route: str | None = None,
    page_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace_snapshot = build_workspace_data_snapshot(db, workspace_id)
    resolved_dataset = dataset
    if resolved_dataset is None and report is not None:
        resolved_dataset = db.get(Dataset, report.dataset_id)

    report_description = _load_json(report.description, {}) if report and report.description else {}
    report_version = None
    report_blocks: list[dict[str, Any]] = []
    try:
        available_tables = set(inspect(engine).get_table_names())
    except SQLAlchemyError:
        available_tables = set()
    if report is not None and "report_versions" in available_tables:
        report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
            .first()
        )
        if report_version is not None and "report_blocks" in available_tables:
            blocks = (
                db.query(ReportBlock)
                .filter(ReportBlock.report_version_id == report_version.id)
                .order_by(ReportBlock.order.asc(), ReportBlock.id.asc())
                .all()
            )
            for block in blocks:
                block_data = _load_json(block.data_json, {})
                editable_fields = _load_json(block.editable_fields_json, [])
                report_blocks.append(
                    {
                        "id": block.id,
                        "type": block.type,
                        "order": block.order,
                        "data": block_data if isinstance(block_data, dict) else {},
                        "editable_fields": editable_fields if isinstance(editable_fields, list) else [],
                    }
                )

    dataset_snapshot, report_inputs_for_ai, dataset_data = _build_dataset_snapshot_for_ai(resolved_dataset)
    summarized_report_blocks = _build_report_blocks_snapshot_for_ai(report_blocks)

    report_snapshot: dict[str, Any] = {
        "id": report.id if report is not None else None,
        "title": report.name if report is not None else None,
        "name": report.name if report is not None else None,
        "description": _summarize_ai_value(report_description, list_limit=6, text_limit=220)
        if isinstance(report_description, dict)
        else {},
        "timeframe": report_description.get("timeframe") if isinstance(report_description, dict) else None,
        "latest_version": {
            "id": report_version.id if report_version is not None else None,
            "version": report_version.version if report_version is not None else None,
        },
        "slides": summarized_report_blocks,
        "blocks": summarized_report_blocks,
    }
    if resolved_dataset is not None and "dataset_files" in available_tables:
        latest_file = (
            db.query(DatasetFile)
            .filter(DatasetFile.dataset_id == resolved_dataset.id)
            .order_by(DatasetFile.created_at.desc(), DatasetFile.id.desc())
            .first()
        )
        if latest_file is not None:
            dataset_snapshot["file"] = {
                "id": latest_file.id,
                "s3_key": latest_file.s3_key,
                "size_bytes": latest_file.size_bytes,
                "content_type": latest_file.content_type,
            }

    return {
        "workspace": {
            "id": workspace_id,
            "snapshot": workspace_snapshot,
        },
        "route_context": {
            "current_route": current_route,
            "page_context": _summarize_ai_value(page_context or {}, list_limit=4, text_limit=180),
        },
        "report": report_snapshot,
        "dataset": dataset_snapshot,
        "report_inputs": report_inputs_for_ai,
    }


def _build_ai_system_prompt(chat_context: dict[str, Any]) -> str:
    context_json = json.dumps(chat_context, ensure_ascii=False, default=str, indent=2)
    return (
        "You are Measurable's Report Analysis Assistant.\n"
        "Your only job is to help the user interpret the open report using the report context below and any extra data the user writes in the chat.\n"
        "Focus on report metrics, slides, summaries, trends, comparisons, and analysis.\n"
        "Do not invent product features, onboarding steps, roadmap items, integrations, exports, uploads, or platform capabilities.\n"
        "If the user asks about product/platform functions outside the report, redirect them briefly toward report interpretation instead of answering with feature guidance.\n"
        "Use only the information in the context plus the user's manual inputs.\n"
        "Do not invent metrics, dates, report names, dataset values, costs, or causal claims.\n"
        "If the user provides outside business data manually, use it only for calculations and analysis related to this report.\n"
        "When discussing relationships between report metrics and outside business data, frame it as correlation or directional evidence, not causation.\n"
        "If spend or investment is missing, do not calculate cost metrics; say that investment data is required.\n"
        "If a metric is missing or unavailable, say so clearly.\n"
        "Keep the answer concise, executive, actionable, and grounded in the report.\n\n"
        "Context JSON:\n"
        f"{context_json}"
    )


def generate_workspace_ai_reply(
    db: Session,
    *,
    conversation: Conversation,
    history: list[Message],
    user_message: str,
    chat_context: dict[str, Any] | None = None,
) -> str:
    if _is_ai_product_question(user_message):
        return AI_PRODUCT_REDIRECT_RESPONSE

    if not settings.anthropic_api_key:
        logger.error(
            "ai_chat_provider_error",
            extra={
                "provider": "anthropic",
                "exception_class": "ProviderNotConfigured",
            },
        )
        raise http_error(503, "ai_provider_unavailable", "AI provider is not configured.")

    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error(
            "ai_chat_provider_error",
            extra={
                "provider": "anthropic",
                "exception_class": "ImportError",
            },
        )
        raise http_error(503, "ai_provider_unavailable", "AI provider is not configured.")

    effective_context = chat_context or build_ai_chat_context_snapshot(
        db,
        workspace_id=conversation.workspace_id,
    )
    workspace_snapshot = effective_context.get("workspace") if isinstance(effective_context.get("workspace"), dict) else {}
    report_snapshot = effective_context.get("report") if isinstance(effective_context.get("report"), dict) else {}
    dataset_snapshot = effective_context.get("dataset") if isinstance(effective_context.get("dataset"), dict) else {}
    if (
        not report_snapshot.get("id")
        and not dataset_snapshot.get("id")
        and int(workspace_snapshot.get("snapshot", {}).get("datasets_count") or 0) == 0
    ):
        return build_workspace_ai_no_data_response()

    system_prompt = _build_ai_system_prompt(effective_context)
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
    except Exception as exc:
        logger.exception(
            "ai_chat_provider_error",
            extra={
                "provider": "anthropic",
                "exception_class": exc.__class__.__name__,
            },
        )
        raise

    text_parts = [
        block.text.strip()
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    reply = " ".join(text_parts).strip()
    if not reply:
        return build_workspace_ai_no_data_response()
    logger.info(
        "ai_chat_provider_success",
        extra={
            "provider": "anthropic",
            "workspace_id": conversation.workspace_id,
            "has_report_context": bool(report_snapshot.get("id")),
            "has_dataset_context": bool(dataset_snapshot.get("id")),
            "report_id": report_snapshot.get("id"),
            "dataset_id": dataset_snapshot.get("id"),
            "reply_length": len(reply),
        },
    )
    return reply


@lru_cache(maxsize=1)
def workspace_logo_column_available() -> bool:
    try:
        columns = inspect(engine).get_columns("workspaces")
    except SQLAlchemyError:
        return False
    return any(str(column.get("name")) == "logo_url" for column in columns)


def measurable_branding() -> dict[str, Optional[str]]:
    return {
        "name": MEASURABLE_BRANDING_NAME,
        "display_name": MEASURABLE_BRANDING_NAME,
        "brand_name": MEASURABLE_BRANDING_NAME,
        "logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "brand_logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "fallback_logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "resolved_logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "resolved_brand_name": MEASURABLE_BRANDING_NAME,
        "source": "measurable",
        "watermark_enabled": True,
        "watermark_label": MEASURABLE_WATERMARK_LABEL,
        "watermark_logo_light_url": MEASURABLE_WATERMARK_LOGO_LIGHT_URL,
        "watermark_logo_dark_url": MEASURABLE_WATERMARK_LOGO_DARK_URL,
        "has_custom_branding": False,
    }


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return None


def _branding_uses_measurable_logo(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return "/brand/measurable-logo" in normalized


def _branding_marks_measurable(branding: Any) -> bool:
    if not isinstance(branding, dict):
        return False
    if str(branding.get("source") or "").strip().lower() == "measurable":
        return True
    explicit_watermark = _coerce_optional_bool(branding.get("watermark_enabled"))
    if explicit_watermark is True:
        return True
    brand_name_candidates = {
        str(branding.get("brand_name") or "").strip(),
        str(branding.get("resolved_brand_name") or "").strip(),
        str(branding.get("display_name") or "").strip(),
        str(branding.get("name") or "").strip(),
    }
    if {
        MEASURABLE_BRANDING_NAME,
        MEASURABLE_REPORT_BRANDING_NAME,
    }.intersection({candidate for candidate in brand_name_candidates if candidate}):
        return True
    return any(
        _branding_uses_measurable_logo(branding.get(field_name))
        for field_name in (
            "logo_url",
            "brand_logo_url",
            "resolved_logo_url",
            "fallback_logo_url",
            "watermark_logo_light_url",
            "watermark_logo_dark_url",
        )
    )


def normalize_branding_payload(branding: Any) -> dict[str, Any]:
    if not isinstance(branding, dict):
        return {
            "name": None,
            "display_name": None,
            "brand_name": None,
            "logo_url": None,
            "brand_logo_url": None,
            "fallback_logo_url": None,
            "resolved_logo_url": None,
            "resolved_brand_name": None,
            "source": None,
            "watermark_enabled": None,
            "watermark_label": None,
            "watermark_logo_light_url": None,
            "watermark_logo_dark_url": None,
            "has_custom_branding": False,
        }
    name = str(
        branding.get("resolved_brand_name")
        or branding.get("brand_name")
        or branding.get("display_name")
        or branding.get("name")
        or ""
    ).strip() or None
    logo_url = str(
        branding.get("resolved_logo_url")
        or branding.get("brand_logo_url")
        or branding.get("logo_url")
        or ""
    ).strip() or None
    fallback_logo_url = str(branding.get("fallback_logo_url") or "").strip() or None
    return {
        "name": name,
        "display_name": name,
        "brand_name": name,
        "logo_url": logo_url,
        "brand_logo_url": logo_url,
        "fallback_logo_url": fallback_logo_url,
        "resolved_logo_url": logo_url,
        "resolved_brand_name": name,
        "source": str(branding.get("source") or "").strip().lower() or None,
        "watermark_enabled": _coerce_optional_bool(branding.get("watermark_enabled")),
        "watermark_label": str(branding.get("watermark_label") or "").strip() or None,
        "watermark_logo_light_url": (
            str(branding.get("watermark_logo_light_url") or "").strip() or None
        ),
        "watermark_logo_dark_url": (
            str(branding.get("watermark_logo_dark_url") or "").strip() or None
        ),
        "has_custom_branding": bool(branding.get("has_custom_branding")),
    }


def _branding_asset_base_url() -> str:
    configured_api_base = str(settings.api_base_url or "").strip().rstrip("/")
    if configured_api_base:
        return configured_api_base
    configured_export_base = str(settings.report_export_base_url or "").strip().rstrip("/")
    frontend_base = str(settings.frontend_url or settings.frontend_base_url or "").strip().rstrip("/")
    if configured_export_base and configured_export_base != frontend_base:
        return configured_export_base
    if frontend_base.startswith("http://localhost:3000"):
        return frontend_base.replace("http://localhost:3000", "http://localhost:8001", 1)
    if frontend_base.startswith("http://127.0.0.1:3000"):
        return frontend_base.replace("http://127.0.0.1:3000", "http://127.0.0.1:8001", 1)
    return ""


def _frontend_base_url() -> str:
    return str(settings.frontend_url or settings.frontend_base_url or "").strip().rstrip("/")


def _workspace_brand_logo_path(workspace_id: int, asset_name: str) -> str:
    return f"/workspace/branding/logo/{workspace_id}/{asset_name}"


def _normalize_public_logo_url(value: Any, *, workspace_id: int | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    base_url = _branding_asset_base_url()
    if parsed.path.startswith("/workspace/branding/logo/"):
        raw_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/") if parsed.scheme and parsed.netloc else ""
        frontend_origin = _frontend_base_url()
        if raw_origin and frontend_origin and raw_origin == frontend_origin and base_url:
            return f"{base_url}{parsed.path}"
        if raw_origin:
            return raw
        if base_url:
            return f"{base_url}{parsed.path}"
        return parsed.path
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw
    if raw.startswith("/"):
        return f"{base_url}{raw}" if base_url else raw

    normalized_path = raw
    if raw.startswith("workspace/branding/logo/"):
        normalized_path = f"/{raw}"
        return f"{base_url}{normalized_path}" if base_url else normalized_path

    file_name = os.path.basename(raw)
    if (
        workspace_id
        and "/" not in raw
        and file_name == raw
        and "." in file_name
    ):
        normalized_path = _workspace_brand_logo_path(workspace_id, file_name)
        return f"{base_url}{normalized_path}" if base_url else normalized_path

    return None


def _normalize_workspace_branding_source(workspace: Workspace | dict[str, Any] | None) -> dict[str, Optional[str]]:
    if workspace is None:
        return normalize_branding_payload({})
    if isinstance(workspace, dict):
        workspace_id = workspace.get("id")
        brand_name = workspace.get("brand_name") or workspace.get("name")
        logo_url = workspace.get("brand_logo_url") or workspace.get("logo_url")
    else:
        workspace_id = workspace.id
        brand_name = workspace.name
        logo_url = workspace.logo_url
    return normalize_branding_payload(
        {
            "brand_name": str(brand_name).strip() if brand_name else None,
            "logo_url": _normalize_public_logo_url(logo_url, workspace_id=workspace_id),
        }
    )


def _normalize_user_branding_source(user: User | dict[str, Any] | None) -> dict[str, Optional[str]]:
    if user is None:
        return normalize_branding_payload({})
    if isinstance(user, dict):
        brand_name = user.get("brand_name") or user.get("full_name") or user.get("name")
        logo_url = user.get("brand_logo_url") or user.get("logo_url")
    else:
        brand_name = user.full_name
        logo_url = user.logo_url
    return normalize_branding_payload(
        {
            "brand_name": str(brand_name).strip() if brand_name else None,
            "logo_url": _normalize_public_logo_url(logo_url),
        }
    )


def resolve_report_branding(
    user: User | dict[str, Any] | None,
    workspace: Workspace | dict[str, Any] | None,
    plan: str | None,
    preferred_branding: dict[str, Any] | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback_logo_url = MEASURABLE_BRANDING_LOGO_URL
    fallback_brand_name = MEASURABLE_REPORT_BRANDING_NAME
    normalized_plan = normalize_workspace_plan(plan)
    plan_entitlements = get_plan_entitlements(normalized_plan)
    custom_branding_allowed = bool(get_plan_capabilities(normalized_plan).get("allow_custom_branding"))

    workspace_id = None
    if isinstance(workspace, dict):
        workspace_id = workspace.get("id")
    elif workspace is not None:
        workspace_id = workspace.id

    preferred = normalize_branding_payload(preferred_branding)
    workspace_branding = _normalize_workspace_branding_source(workspace)
    user_branding = _normalize_user_branding_source(user)
    normalized_report_metadata = report_metadata if isinstance(report_metadata, dict) else {}
    metadata_plan_at_generation = normalized_report_metadata.get("plan_at_generation")
    plan_at_generation = (
        normalize_workspace_plan(metadata_plan_at_generation)
        if str(metadata_plan_at_generation or "").strip()
        else None
    )

    raw_brand_name = (
        preferred.get("brand_name")
        or workspace_branding.get("brand_name")
        or user_branding.get("brand_name")
    )
    raw_logo_url = (
        _normalize_public_logo_url(preferred.get("logo_url"), workspace_id=workspace_id)
        or _normalize_public_logo_url(workspace_branding.get("logo_url"), workspace_id=workspace_id)
        or _normalize_public_logo_url(user_branding.get("logo_url"))
    )

    if custom_branding_allowed:
        resolved_brand_name = raw_brand_name or fallback_brand_name
        resolved_logo_url = raw_logo_url or fallback_logo_url
        has_custom_branding = bool(raw_brand_name or raw_logo_url)
    else:
        resolved_brand_name = fallback_brand_name
        resolved_logo_url = fallback_logo_url
        has_custom_branding = False

    explicit_watermark_enabled = preferred.get("watermark_enabled")
    if explicit_watermark_enabled is None and isinstance(normalized_report_metadata.get("branding"), dict):
        explicit_watermark_enabled = _coerce_optional_bool(
            normalized_report_metadata["branding"].get("watermark_enabled")
        )
    metadata_measurable_branding = _branding_marks_measurable(preferred) or _branding_marks_measurable(
        normalized_report_metadata.get("branding")
    )
    created_on_free_plan = (
        bool(get_plan_entitlements(plan_at_generation).get("measurable_watermark"))
        if plan_at_generation
        else False
    )
    if explicit_watermark_enabled is not None:
        watermark_enabled = bool(explicit_watermark_enabled)
    elif metadata_measurable_branding:
        watermark_enabled = True
    elif plan_at_generation is not None:
        watermark_enabled = created_on_free_plan
    else:
        watermark_enabled = bool(plan_entitlements.get("measurable_watermark"))

    branding_source = preferred.get("source")
    if branding_source not in {"user", "measurable"}:
        branding_source = "measurable" if watermark_enabled or not custom_branding_allowed else "user"

    return {
        "name": resolved_brand_name,
        "display_name": resolved_brand_name,
        "brand_name": resolved_brand_name,
        "logo_url": resolved_logo_url,
        "brand_logo_url": resolved_logo_url,
        "fallback_logo_url": fallback_logo_url,
        "resolved_logo_url": resolved_logo_url,
        "resolved_brand_name": resolved_brand_name,
        "source": branding_source,
        "watermark_enabled": watermark_enabled,
        "watermark_label": (
            MEASURABLE_WATERMARK_LABEL if watermark_enabled else None
        ),
        "watermark_logo_light_url": (
            MEASURABLE_WATERMARK_LOGO_LIGHT_URL if watermark_enabled else None
        ),
        "watermark_logo_dark_url": (
            MEASURABLE_WATERMARK_LOGO_DARK_URL if watermark_enabled else None
        ),
        "has_custom_branding": has_custom_branding,
    }


def resolve_workspace_branding(workspace_id: int | None) -> dict[str, Optional[str]]:
    if not workspace_id or not workspace_logo_column_available():
        return normalize_branding_payload({})

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT name, logo_url FROM workspaces WHERE id = :workspace_id"),
                {"workspace_id": int(workspace_id)},
            ).first()
    except SQLAlchemyError:
        return normalize_branding_payload({})

    if not result:
        return normalize_branding_payload({})

    workspace_name, logo_url = result
    return normalize_branding_payload(
        {
            "name": str(workspace_name) if workspace_name else None,
            "logo_url": _normalize_public_logo_url(logo_url, workspace_id=int(workspace_id)),
        }
    )


def workspace_allows_custom_branding(db: Session, workspace_id: int) -> bool:
    plan = get_workspace_plan(db, workspace_id)
    capabilities = get_plan_capabilities(plan)
    return bool(capabilities["allow_custom_branding"])


def resolve_report_branding_for_workspace(
    db: Session,
    workspace_id: int,
    preferred_branding: dict[str, Any] | None = None,
    user: User | dict[str, Any] | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = db.get(Workspace, workspace_id) if workspace_id else None
    plan = get_workspace_plan(db, workspace_id) if workspace_id else DEFAULT_WORKSPACE_PLAN
    return resolve_report_branding(
        user,
        workspace,
        plan,
        preferred_branding=preferred_branding,
        report_metadata=report_metadata,
    )


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
    "last_30_days": 30,
}

META_PAGES_TIMEFRAME_ALIASES = {
    "last_7d": "last_7_days",
    "last_30d": "last_30_days",
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
    selected_timeframe = META_PAGES_TIMEFRAME_ALIASES.get(selected_timeframe, selected_timeframe)

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
        "timeframe must be one of: last_7_days, last_14_days, last_28_days, last_30_days, this_month, last_month, custom.",
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
    is_meta_ads = integration_type == "meta_ads"
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
    meta_ads_daily_reach = normalize_meta_timeseries(normalized_metrics.get("daily_reach"))
    meta_ads_daily_impressions = normalize_meta_timeseries(normalized_metrics.get("daily_impressions"))
    meta_ads_daily_clicks = normalize_meta_timeseries(normalized_metrics.get("daily_clicks"))
    unavailable_metrics_raw = row.get("unavailable_metrics")
    if isinstance(unavailable_metrics_raw, str):
        unavailable_metrics = _load_json(unavailable_metrics_raw, {})
    elif isinstance(unavailable_metrics_raw, dict):
        unavailable_metrics = unavailable_metrics_raw
    else:
        unavailable_metrics = {}
    reach = _to_int(row.get("reach"))
    if reach is None:
        reach = _to_int(row.get("reach_total"))
    if reach is None and is_meta_ads:
        reach = _to_int(row.get("total_reach"))
    if reach is None:
        reach = _to_int(normalized_metrics.get("reach_total"))
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
        impressions = _to_int(row.get("impressions_total"))
    if impressions is None and is_meta_ads:
        impressions = _to_int(row.get("total_impressions"))
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
        followers = _to_int(row.get("followers_total"))
    if followers is None:
        followers = _to_int(row.get("followers_count"))
    if followers is None:
        followers = _to_int(normalized_metrics.get("followers_growth_total"))
    engagement = _to_int(row.get("engagement"))
    if engagement is None:
        engagement = _to_int(row.get("engagement_total"))
    if engagement is None and is_meta_ads:
        engagement = _to_int(row.get("total_clicks"))
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
    if link_clicks is None and is_meta_ads:
        link_clicks = _to_int(row.get("total_clicks"))
    if link_clicks is None:
        link_clicks = _to_int(row.get("website_clicks"))
    if link_clicks is None:
        link_clicks = _to_int(normalized_metrics.get("link_clicks_total"))
    views = _to_int(row.get("views"))
    if views is None:
        views = _to_int(row.get("page_views_total"))
    if views is None and is_meta_ads:
        views = _to_int(row.get("total_impressions"))
    if views is None:
        views = _to_int(normalized_metrics.get("page_views_total"))
    if views is None:
        views = _to_int(normalized_metrics.get("views_total"))
    followers_growth = _to_int(row.get("followers_growth"))
    if followers_growth is None and not is_instagram_business:
        followers_growth = _to_int(normalized_metrics.get("followers_growth_total"))

    if not reach_daily:
        reach_daily = normalize_meta_timeseries(normalized_metrics.get("daily_reach"))
    if not reach_daily:
        reach_daily = normalize_meta_timeseries(normalized_metrics.get("viewers_daily"))
    if not reach_daily and is_meta_ads:
        reach_daily = meta_ads_daily_reach
    if not impressions_daily and is_meta_ads:
        impressions_daily = meta_ads_daily_impressions
    if not impressions_daily:
        impressions_daily = normalize_meta_timeseries(normalized_metrics.get("daily_impressions"))
    if not interactions_daily:
        interactions_daily = normalize_meta_timeseries(normalized_metrics.get("interactions_daily"))
    if not interactions_daily and is_meta_ads:
        interactions_daily = meta_ads_daily_clicks
    if not daily_engagement:
        daily_engagement = normalize_meta_timeseries(normalized_metrics.get("daily_engagement"))
    if not daily_engagement:
        daily_engagement = interactions_daily
    if not link_clicks_daily:
        link_clicks_daily = normalize_meta_timeseries(normalized_metrics.get("link_clicks_daily"))
    if not link_clicks_daily and is_meta_ads:
        link_clicks_daily = meta_ads_daily_clicks
    if not page_visits_daily:
        page_visits_daily = normalize_meta_timeseries(normalized_metrics.get("daily_page_views"))
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
        "spend": row.get("total_spend"),
        "total_spend": row.get("total_spend"),
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
        "page_views_source_metric": str(row.get("page_views_source_metric") or "") or None,
        "impressions_daily": impressions_daily,
        "reach_daily": reach_daily,
        "interactions_daily": interactions_daily,
        "engagement_daily": interactions_daily,
        "daily_engagement": daily_engagement,
        "content_interactions_daily": interactions_daily,
        "page_views_daily": page_visits_daily,
        "link_clicks_daily": link_clicks_daily,
        "page_visits_daily": page_visits_daily,
        "followers_growth_daily": followers_growth_daily,
        "recent_posts": recent_posts,
        "posts_analyzed_count": _to_int(row.get("posts_analyzed_count"))
        if _to_int(row.get("posts_analyzed_count")) is not None
        else _to_int(normalized_metrics.get("posts_analyzed_count")),
        "reactions_total": _to_int(row.get("reactions_total"))
        if _to_int(row.get("reactions_total")) is not None
        else _to_int(normalized_metrics.get("reactions_total")),
        "comments_total": _to_int(row.get("comments_total"))
        if _to_int(row.get("comments_total")) is not None
        else _to_int(normalized_metrics.get("comments_total")),
        "shares_total": _to_int(row.get("shares_total"))
        if _to_int(row.get("shares_total")) is not None
        else _to_int(normalized_metrics.get("shares_total")),
        "top_post_by_engagement": row.get("top_post_by_engagement")
        if isinstance(row.get("top_post_by_engagement"), dict)
        else normalized_metrics.get("top_post_by_engagement")
        if isinstance(normalized_metrics.get("top_post_by_engagement"), dict)
        else None,
        "unavailable_metrics": unavailable_metrics if isinstance(unavailable_metrics, dict) else {},
        "facebook_metric_audit": row.get("facebook_metric_audit")
        if isinstance(row.get("facebook_metric_audit"), dict)
        else {},
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
    if integration_type == "meta_ads":
        spend = report_inputs.get("spend") or report_inputs.get("total_spend")
        impressions = report_inputs.get("impressions")
        clicks = report_inputs.get("link_clicks")
        if locale == "es":
            return (
                f"Meta Ads registró {spend or 'N/A'} de gasto, "
                f"{impressions or 'N/A'} impresiones y {clicks or 'N/A'} clics"
                + (f" en {timeframe_label}." if timeframe_label else ".")
            )
        return (
            f"Meta Ads delivered {spend or 'N/A'} in spend, "
            f"{impressions or 'N/A'} impressions, and {clicks or 'N/A'} clicks"
            + (f" during {timeframe_label}." if timeframe_label else ".")
        )
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


# LEGACY / candidate for removal after frontend/backend contract is stable.
# Recommended source of truth for 5-slide daily series is extractDailyMetricSeries()
# in app/main.py, with this helper retained for compatibility with existing report flows.
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
    db: Session,
    export: Export,
    report: Report,
    report_version: ReportVersion,
    blocks: list[ReportBlock],
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
    branding = resolve_report_branding_for_workspace(
        db,
        report.workspace_id,
        preferred_branding=metadata_branding,
        report_metadata=report_metadata if isinstance(report_metadata, dict) else None,
    )
    report_timeframe = (
        report_metadata.get("timeframe")
        if isinstance(report_metadata, dict) and isinstance(report_metadata.get("timeframe"), dict)
        else None
    )
    logger.info(
        "[ReportBranding][resolved]",
        extra={
            "workspace_id": report.workspace_id,
            "report_id": report.id,
            "plan": get_workspace_plan(db, report.workspace_id),
            "brand_name_original": metadata_branding.get("brand_name")
            if isinstance(metadata_branding, dict)
            else None,
            "brand_logo_url_original": (
                metadata_branding.get("brand_logo_url") or metadata_branding.get("logo_url")
            )
            if isinstance(metadata_branding, dict)
            else None,
            "resolved_brand_name": branding.get("resolved_brand_name"),
            "resolved_logo_url": branding.get("resolved_logo_url"),
            "has_custom_branding": branding.get("has_custom_branding"),
        },
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
                "data": (
                    {
                        **_load_json(block.data_json, {}),
                        "semantic_name": "cover",
                        "branding": branding,
                        "brand_name": branding.get("resolved_brand_name"),
                        "brand_logo_url": branding.get("resolved_logo_url"),
                        "resolved_brand_name": branding.get("resolved_brand_name"),
                        "resolved_logo_url": branding.get("resolved_logo_url"),
                    }
                    if block.type == "title"
                    and (
                        not str(_load_json(block.data_json, {}).get("semantic_name") or "").strip()
                        or str(_load_json(block.data_json, {}).get("semantic_name") or "").strip() == "cover"
                    )
                    else _load_json(block.data_json, {})
                ),
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


def _read_pdf_dom_diagnostics(page: Any) -> dict[str, Any]:
    try:
        diagnostics = page.evaluate(
            """
            () => {
              const body = document.body;
              const text = body ? (body.innerText || body.textContent || "") : "";
              return {
                url: window.location.href,
                title: document.title || "",
                bodyReady: body ? body.getAttribute("data-pdf-ready") : null,
                bodyError: body ? body.getAttribute("data-pdf-error") : null,
                bodyErrorReason: body ? body.getAttribute("data-pdf-error-reason") : null,
                rootReadyExists: Boolean(document.querySelector("[data-pdf-ready='true']")),
                pdfErrorExists: Boolean(document.querySelector("[data-pdf-error='true'], [data-pdf-error]")),
                slideCount: document.querySelectorAll("[data-report-slide='true'], [data-report-slide]").length,
                textExcerpt: String(text || "").trim().slice(0, 1000),
              };
            }
            """
        )
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        body_ready = diagnostics.get("bodyReady")
        body_error = diagnostics.get("bodyError")
        root_ready_exists = bool(diagnostics.get("rootReadyExists"))
        pdf_error_exists = bool(diagnostics.get("pdfErrorExists"))
        slide_count = diagnostics.get("slideCount")
        url = diagnostics.get("url")
        title = diagnostics.get("title")
        return {
            "url": url,
            "title": title,
            "bodyReady": body_ready,
            "bodyError": body_error,
            "bodyErrorReason": diagnostics.get("bodyErrorReason"),
            "rootReadyExists": root_ready_exists,
            "pdfErrorExists": pdf_error_exists,
            "slideCount": slide_count,
            "textExcerpt": diagnostics.get("textExcerpt"),
            "diagnostics_error": None,
            "data_pdf_ready_exists": root_ready_exists or body_ready == "true",
            "data_pdf_error_exists": pdf_error_exists or body_error == "true",
            "page_url": url,
            "page_title": title,
            "slide_count": slide_count,
        }
    except Exception as exc:
        return {
            "diagnostics_error": str(exc)[:1000],
            "data_pdf_ready_exists": False,
            "data_pdf_error_exists": False,
            "slide_count": None,
            "page_url": None,
            "page_title": None,
            "url": None,
            "title": None,
            "bodyReady": None,
            "bodyError": None,
            "bodyErrorReason": None,
            "rootReadyExists": False,
            "pdfErrorExists": False,
            "slideCount": None,
            "textExcerpt": None,
        }


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
    viewport_width = 1600
    viewport_height = 900
    device_scale_factor = float(settings.pdf_export_device_scale_factor)
    auth_strategy = "authorization_header_report_export_token"
    report_fetch_events: list[dict[str, Any]] = []
    slide_selector_used: str | None = None
    ready_selector_timed_out = False
    networkidle_timed_out = False
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
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                networkidle_timed_out = True
                logger.warning(
                    "Report thumbnail generation continued without networkidle",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                    },
                )
            page.evaluate("() => document.fonts.ready")
            if ready_selector:
                try:
                    page.wait_for_selector(ready_selector, timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    ready_selector_timed_out = True
                    logger.warning(
                        "Report thumbnail generation continued without export-ready selector",
                        extra={
                            "report_id": report_id,
                            "export_url": export_url,
                            "auth_strategy": auth_strategy,
                            "ready_selector": ready_selector,
                        },
                    )

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
            "ready_selector_timed_out": ready_selector_timed_out,
            "networkidle_timed_out": networkidle_timed_out,
        },
    )
    return screenshot_bytes, {
        "export_url": export_url,
        "auth_strategy": auth_strategy,
        "report_fetch_events": report_fetch_events,
        "slide_selector_used": slide_selector_used,
        "ready_selector_timed_out": ready_selector_timed_out,
        "networkidle_timed_out": networkidle_timed_out,
    }


def generate_pdf_from_export_page(
    *,
    export_url: str,
    report_id: int,
    auth_token: str | None = None,
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

    configured_timeout_ms = max(int(settings.pdf_export_timeout_ms), 1000)
    timeout_ms = (
        min(configured_timeout_ms, 15000)
        if "localhost" in export_url or "127.0.0.1" in export_url
        else configured_timeout_ms
    )
    ready_selector = (
        str(settings.pdf_export_ready_selector or "").strip()
        or '[data-pdf-ready="true"], body[data-pdf-ready="true"]'
    )
    if ready_selector == '[data-pdf-ready="true"]':
        ready_selector = '[data-pdf-ready="true"], body[data-pdf-ready="true"]'
    viewport_width = int(settings.pdf_export_viewport_width)
    viewport_height = int(settings.pdf_export_viewport_height)
    device_scale_factor = float(settings.pdf_export_device_scale_factor)
    pdf_scale = float(settings.pdf_export_scale)
    pdf_margins = {
        "top": "0px",
        "right": "0px",
        "bottom": "0px",
        "left": "0px",
    }
    pdf_options = {
        "width": "1600px",
        "height": "900px",
        "print_background": True,
        "prefer_css_page_size": False,
        "margin": pdf_margins,
        "scale": pdf_scale,
    }
    page_count: int | None = None
    auth_strategy = "authorization_header_report_export_token" if auth_token else "public_share_url"
    report_fetch_events: list[dict[str, Any]] = []
    browser_console_events: list[dict[str, Any]] = []
    browser_page_errors: list[str] = []
    browser_request_failures: list[dict[str, Any]] = []
    logo_status: int | None = None
    main_response_status: int | None = None
    current_page_url: str | None = None
    page_title: str | None = None
    page_text_excerpt: str | None = None
    body_pdf_ready: str | None = None
    body_pdf_error: str | None = None
    body_pdf_error_reason: str | None = None
    root_ready_exists = False
    data_pdf_ready_exists = False
    data_pdf_error_exists = False
    report_slide_count = 0
    failure_markers = [
        "report export unavailable",
        "the report could not be loaded for export",
        "404",
        "unauthorized",
        "login",
        "no encontramos este reporte compartido",
        "el link de este reporte expiró",
        "share_link_not_found",
        "share_link_expired",
    ]

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
            "prefer_css_page_size": False,
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
                extra_http_headers=(
                    {
                        "Authorization": f"Bearer {auth_token}",
                        "X-Measurable-Export-Auth": "report_export_token",
                    }
                    if auth_token
                    else None
                ),
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

            def _handle_console(message: Any) -> None:
                entry = {
                    "type": str(getattr(message, "type", "")),
                    "text": str(getattr(message, "text", "") or "")[:1000],
                }
                browser_console_events.append(entry)
                logger.info(
                    "[PDFExport][browser.console]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "message_type": entry["type"],
                        "message_text": entry["text"],
                    },
                )

            def _handle_page_error(error: Any) -> None:
                message = str(error or "")[:1000]
                browser_page_errors.append(message)
                logger.warning(
                    "[PDFExport][browser.pageerror]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "event_message": message,
                    },
                )

            def _handle_request_failed(request: Any) -> None:
                failure = getattr(request, "failure", None)
                failure_text = None
                if callable(failure):
                    failure_info = failure()
                    if isinstance(failure_info, dict):
                        failure_text = failure_info.get("errorText")
                elif isinstance(failure, dict):
                    failure_text = failure.get("errorText")
                entry = {
                    "url": str(getattr(request, "url", "") or "")[:1000],
                    "method": str(getattr(request, "method", "") or ""),
                    "resource_type": str(getattr(request, "resource_type", "") or ""),
                    "failure_text": str(failure_text or "")[:500] or None,
                }
                browser_request_failures.append(entry)
                logger.warning(
                    "[PDFExport][browser.requestfailed]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "request_url": entry["url"],
                        "method": entry["method"],
                        "resource_type": entry["resource_type"],
                        "failure_text": entry["failure_text"],
                    },
                )

            page.on("response", _handle_response)
            page.on("console", _handle_console)
            page.on("pageerror", _handle_page_error)
            page.on("requestfailed", _handle_request_failed)
            main_response = page.goto(export_url, wait_until="domcontentloaded", timeout=timeout_ms)
            main_response_status = main_response.status if main_response is not None else None
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.evaluate("() => document.fonts.ready")
            try:
                page.wait_for_selector(
                    "[data-report-slide='true'], [data-report-slide]",
                    timeout=min(timeout_ms, 5000),
                )
            except PlaywrightTimeoutError:
                logger.warning(
                    "[PDFExport][slide.count]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "status": "selector_timeout",
                    },
                )
            page.wait_for_timeout(400)
            diagnostics = _read_pdf_dom_diagnostics(page)
            logger.info(
                "[PDFExport][dom.diagnostics]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "page_url": diagnostics.get("page_url"),
                    "page_title": diagnostics.get("page_title"),
                    "body_ready": diagnostics.get("bodyReady"),
                    "body_error": diagnostics.get("bodyError"),
                    "body_error_reason": diagnostics.get("bodyErrorReason"),
                    "root_ready_exists": diagnostics.get("rootReadyExists"),
                    "data_pdf_ready_exists": diagnostics.get("data_pdf_ready_exists"),
                    "data_pdf_error_exists": diagnostics.get("data_pdf_error_exists"),
                    "slide_count": diagnostics.get("slide_count"),
                    "diagnostics_error": diagnostics.get("diagnostics_error"),
                },
            )
            current_page_url = str(diagnostics.get("url") or page.url or "").strip() or None
            page_title = str(diagnostics.get("title") or page.title() or "").strip() or None
            page_text_excerpt = str(diagnostics.get("textExcerpt") or "").strip() or None
            body_pdf_ready = str(diagnostics.get("bodyReady") or "").strip() or None
            body_pdf_error = str(diagnostics.get("bodyError") or "").strip() or None
            body_pdf_error_reason = str(diagnostics.get("bodyErrorReason") or "").strip() or None
            root_ready_exists = bool(diagnostics.get("rootReadyExists"))
            data_pdf_ready_exists = root_ready_exists or body_pdf_ready == "true"
            data_pdf_error_exists = bool(diagnostics.get("pdfErrorExists")) or body_pdf_error == "true"
            report_slide_count = int(diagnostics.get("slideCount") or 0)
            logger.info(
                "[PDFExport][slide.count]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "slide_count": report_slide_count,
                    "root_ready_exists": root_ready_exists,
                    "data_pdf_ready_exists": data_pdf_ready_exists,
                },
            )
            logger.info(
                "[PDFExport][page.size]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "pdf_width": pdf_options["width"],
                    "pdf_height": pdf_options["height"],
                    "prefer_css_page_size": pdf_options["prefer_css_page_size"],
                },
            )
            if data_pdf_error_exists:
                logger.error(
                    "PDF export page reported a frontend render error",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_events": report_fetch_events,
                        "browser_console_events": browser_console_events[-20:],
                        "browser_page_errors": browser_page_errors[-20:],
                        "browser_request_failures": browser_request_failures[-20:],
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                    },
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_render_frontend_error",
                        "message": "PDF export page reported a frontend render error.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
                        "report_fetch_events": report_fetch_events,
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                        "browser_console_events": browser_console_events[-20:],
                        "browser_page_errors": browser_page_errors[-20:],
                        "browser_request_failures": browser_request_failures[-20:],
                        "failure_reason": "frontend_reported_pdf_error",
                    },
                )
            try:
                if ready_selector:
                    page.wait_for_selector(ready_selector, timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                diagnostics = _read_pdf_dom_diagnostics(page)
                logger.info(
                    "[PDFExport][dom.diagnostics]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "page_url": diagnostics.get("page_url"),
                        "page_title": diagnostics.get("page_title"),
                        "body_ready": diagnostics.get("bodyReady"),
                        "body_error": diagnostics.get("bodyError"),
                        "body_error_reason": diagnostics.get("bodyErrorReason"),
                        "root_ready_exists": diagnostics.get("rootReadyExists"),
                        "data_pdf_ready_exists": diagnostics.get("data_pdf_ready_exists"),
                        "data_pdf_error_exists": diagnostics.get("data_pdf_error_exists"),
                        "slide_count": diagnostics.get("slide_count"),
                        "diagnostics_error": diagnostics.get("diagnostics_error"),
                    },
                )
                current_page_url = str(diagnostics.get("url") or page.url or "").strip() or None
                page_title = str(diagnostics.get("title") or page.title() or "").strip() or None
                page_text_excerpt = str(diagnostics.get("textExcerpt") or "").strip() or None
                body_pdf_ready = str(diagnostics.get("bodyReady") or "").strip() or None
                body_pdf_error = str(diagnostics.get("bodyError") or "").strip() or None
                body_pdf_error_reason = str(diagnostics.get("bodyErrorReason") or "").strip() or None
                root_ready_exists = bool(diagnostics.get("rootReadyExists"))
                data_pdf_ready_exists = root_ready_exists or body_pdf_ready == "true"
                data_pdf_error_exists = bool(diagnostics.get("pdfErrorExists")) or body_pdf_error == "true"
                report_slide_count = int(diagnostics.get("slideCount") or 0)
                auth_failed = any(event.get("status") == 401 for event in report_fetch_events)
                logger.error(
                    "[PDFExport][ready.timeout]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_events": report_fetch_events,
                        "browser_console_events": browser_console_events[-20:],
                        "browser_page_errors": browser_page_errors[-20:],
                        "browser_request_failures": browser_request_failures[-20:],
                        "auth_failed": auth_failed,
                        "viewport_width": viewport_width,
                        "viewport_height": viewport_height,
                        "device_scale_factor": device_scale_factor,
                        "pdf_scale": pdf_scale,
                        "pdf_margins": pdf_margins,
                        "prefer_css_page_size": False,
                        "media_type": "screen",
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "slide_count": report_slide_count,
                        "bodyReady": body_pdf_ready,
                        "bodyError": body_pdf_error,
                        "bodyErrorReason": body_pdf_error_reason,
                        "rootReadyExists": root_ready_exists,
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                    },
                )
                if data_pdf_error_exists:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": "pdf_render_frontend_error",
                            "message": "PDF export page reported a frontend render error.",
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
                            "page_status": main_response_status,
                            "page_url": current_page_url,
                            "page_title": page_title,
                            "page_text_excerpt": page_text_excerpt,
                            "body_pdf_ready": body_pdf_ready,
                            "body_pdf_error": body_pdf_error,
                            "body_pdf_error_reason": body_pdf_error_reason,
                            "root_ready_exists": root_ready_exists,
                            "browser_console_events": browser_console_events[-20:],
                            "browser_page_errors": browser_page_errors[-20:],
                            "browser_request_failures": browser_request_failures[-20:],
                            "data_pdf_ready_exists": data_pdf_ready_exists,
                            "data_pdf_error_exists": data_pdf_error_exists,
                            "report_slide_count": report_slide_count,
                            "failure_reason": "frontend_reported_pdf_error",
                        },
                    ) from exc
                if report_slide_count >= 1:
                    logger.warning(
                        "[PDFExport][ready.fallback.slide_count]",
                        extra={
                            "report_id": report_id,
                            "export_url": export_url,
                            "page_status": main_response_status,
                            "page_url": current_page_url,
                            "page_title": page_title,
                            "page_text_excerpt": page_text_excerpt,
                            "slideCount": report_slide_count,
                            "bodyReady": body_pdf_ready,
                            "bodyError": body_pdf_error,
                            "bodyErrorReason": body_pdf_error_reason,
                            "rootReadyExists": root_ready_exists,
                        },
                    )
                else:
                    logger.error(
                        "[PDFExport][ready.failed.no_slides]",
                        extra={
                            "report_id": report_id,
                            "export_url": export_url,
                            "page_status": main_response_status,
                            "page_url": current_page_url,
                            "page_title": page_title,
                            "page_text_excerpt": page_text_excerpt,
                            "slideCount": report_slide_count,
                            "bodyReady": body_pdf_ready,
                            "bodyError": body_pdf_error,
                            "bodyErrorReason": body_pdf_error_reason,
                            "rootReadyExists": root_ready_exists,
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
                            "page_status": main_response_status,
                            "page_url": current_page_url,
                            "page_title": page_title,
                            "page_text_excerpt": page_text_excerpt,
                            "body_pdf_ready": body_pdf_ready,
                            "body_pdf_error": body_pdf_error,
                            "body_pdf_error_reason": body_pdf_error_reason,
                            "root_ready_exists": root_ready_exists,
                            "browser_console_events": browser_console_events[-20:],
                            "browser_page_errors": browser_page_errors[-20:],
                            "browser_request_failures": browser_request_failures[-20:],
                            "data_pdf_ready_exists": data_pdf_ready_exists,
                            "data_pdf_error_exists": data_pdf_error_exists,
                            "report_slide_count": report_slide_count,
                            "pdf_options": {
                                "scale": pdf_scale,
                                "margin": pdf_margins,
                                "prefer_css_page_size": False,
                                "print_background": True,
                                "media_type": "screen",
                            },
                            "failure_reason": "report_fetch_401"
                            if auth_failed
                            else "ready_selector_timeout",
                        },
                    ) from exc

            combined_text = " ".join(
                part for part in [page_title or "", page_text_excerpt or ""] if part
            ).lower()
            invalid_share_markers = {
                "no encontramos este reporte compartido",
                "el link de este reporte expiró",
                "share_link_not_found",
                "share_link_expired",
            }
            if any(marker in combined_text for marker in invalid_share_markers):
                logger.error(
                    "PDF export page rendered invalid share content",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_events": report_fetch_events,
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "browser_console_events": browser_console_events[-20:],
                        "browser_page_errors": browser_page_errors[-20:],
                        "browser_request_failures": browser_request_failures[-20:],
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                    },
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_render_share_invalid",
                        "message": "PDF render opened an invalid or expired share link.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
                        "report_fetch_events": report_fetch_events,
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "browser_console_events": browser_console_events[-20:],
                        "browser_page_errors": browser_page_errors[-20:],
                        "browser_request_failures": browser_request_failures[-20:],
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                        "failure_reason": "invalid_or_expired_share_link",
                    },
                )
            if any(marker in combined_text for marker in failure_markers):
                logger.error(
                    "PDF export page rendered error content",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_events": report_fetch_events,
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                    },
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_render_failed",
                        "message": "PDF render page did not load the report content.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "auth_strategy": auth_strategy,
                        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
                        "report_fetch_events": report_fetch_events,
                        "page_status": main_response_status,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_ready": body_pdf_ready,
                        "body_pdf_error": body_pdf_error,
                        "body_pdf_error_reason": body_pdf_error_reason,
                        "root_ready_exists": root_ready_exists,
                        "data_pdf_ready_exists": data_pdf_ready_exists,
                        "data_pdf_error_exists": data_pdf_error_exists,
                        "report_slide_count": report_slide_count,
                        "failure_reason": "rendered_error_page",
                    },
                )

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
                "[PDFExport][page.pdf.options]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "device_scale_factor": device_scale_factor,
                    "pdf_scale": pdf_scale,
                    "pdf_margins": pdf_margins,
                    "pdf_width": pdf_options["width"],
                    "pdf_height": pdf_options["height"],
                    "prefer_css_page_size": False,
                    "media_type": "screen",
                },
            )
            pdf_bytes = page.pdf(**pdf_options)
            if len(pdf_bytes) > 2 * 1024 * 1024 and (page_count or 0) <= 5:
                logger.warning(
                    "[PDFExport][size.warning]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "page_count": page_count,
                        "size_bytes": len(pdf_bytes),
                    },
                )
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
                "prefer_css_page_size": False,
                "media_type": "screen",
                "page_status": main_response_status,
                "page_url": current_page_url,
                "page_title": page_title,
                "page_text_excerpt": page_text_excerpt,
                "body_pdf_ready": body_pdf_ready,
                "body_pdf_error": body_pdf_error,
                "body_pdf_error_reason": body_pdf_error_reason,
                "root_ready_exists": root_ready_exists,
                "data_pdf_ready_exists": data_pdf_ready_exists,
                "data_pdf_error_exists": data_pdf_error_exists,
                "report_slide_count": report_slide_count,
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
                "page_status": main_response_status,
                "page_url": current_page_url,
                "page_title": page_title,
                "page_text_excerpt": page_text_excerpt,
                "body_pdf_ready": body_pdf_ready,
                "body_pdf_error": body_pdf_error,
                "body_pdf_error_reason": body_pdf_error_reason,
                "root_ready_exists": root_ready_exists,
                "data_pdf_ready_exists": data_pdf_ready_exists,
                "data_pdf_error_exists": data_pdf_error_exists,
                "report_slide_count": report_slide_count,
                "pdf_options": {
                    "scale": pdf_scale,
                    "margin": pdf_margins,
                    "prefer_css_page_size": False,
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
            "prefer_css_page_size": False,
            "media_type": "screen",
            "page_status": main_response_status,
            "page_url": current_page_url,
            "page_title": page_title,
            "page_text_excerpt": page_text_excerpt,
            "body_pdf_ready": body_pdf_ready,
            "body_pdf_error": body_pdf_error,
            "body_pdf_error_reason": body_pdf_error_reason,
            "root_ready_exists": root_ready_exists,
            "browser_console_events": browser_console_events[-20:],
            "browser_page_errors": browser_page_errors[-20:],
            "browser_request_failures": browser_request_failures[-20:],
            "logo_status": logo_status,
            "data_pdf_ready_exists": data_pdf_ready_exists,
            "data_pdf_error_exists": data_pdf_error_exists,
            "report_slide_count": report_slide_count,
        },
    )
    logger.info(
        "[PDFExport][success.size_bytes]",
        extra={
            "report_id": report_id,
            "export_url": export_url,
            "size_bytes": len(pdf_bytes),
            "page_count": page_count,
        },
    )
    return pdf_bytes, {
        "page_count": page_count,
        "export_url": export_url,
        "auth_strategy": auth_strategy,
        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
        "report_fetch_events": report_fetch_events,
        "page_status": main_response_status,
        "page_url": current_page_url,
        "page_title": page_title,
        "page_text_excerpt": page_text_excerpt,
        "body_pdf_ready": body_pdf_ready,
        "body_pdf_error": body_pdf_error,
        "body_pdf_error_reason": body_pdf_error_reason,
        "root_ready_exists": root_ready_exists,
        "logo_status": logo_status,
        "data_pdf_ready_exists": data_pdf_ready_exists,
        "data_pdf_error_exists": data_pdf_error_exists,
        "report_slide_count": report_slide_count,
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
            "deviceScaleFactor": device_scale_factor,
        },
        "pdf_options": {
            "width": pdf_options["width"],
            "height": pdf_options["height"],
            "scale": pdf_scale,
            "margin": pdf_margins,
            "prefer_css_page_size": False,
            "print_background": True,
            "media_type": "screen",
        },
    }


def generate_pdf_from_export_page(
    *,
    export_url: str,
    report_id: int,
    auth_token: str | None = None,
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

    started_at = perf_counter()
    configured_timeout_ms = max(int(settings.pdf_export_timeout_ms), 1000)
    timeout_ms = (
        min(configured_timeout_ms, 15000)
        if "localhost" in export_url or "127.0.0.1" in export_url
        else configured_timeout_ms
    )
    viewport_width = 1600
    viewport_height = 900
    slide_selector = "[data-report-slide='true'], [data-report-slide]"
    device_scale_factor = float(settings.pdf_export_device_scale_factor or 1.0)
    pdf_margins = {"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"}
    pdf_options = {
        "width": "1600px",
        "height": "900px",
        "print_background": True,
        "prefer_css_page_size": False,
        "margin": pdf_margins,
    }
    auth_strategy = "authorization_header_report_export_token" if auth_token else "public_share_url"
    report_fetch_events: list[dict[str, Any]] = []
    browser_console_events: list[dict[str, Any]] = []
    browser_page_errors: list[str] = []
    browser_request_failures: list[dict[str, Any]] = []
    slide_numbers: list[int] = []
    screenshot_bytes_per_slide: list[int] = []
    logo_status: int | None = None
    main_response_status: int | None = None
    current_page_url: str | None = None
    page_title: str | None = None
    page_text_excerpt: str | None = None
    body_pdf_ready: str | None = None
    body_pdf_error: str | None = None
    body_pdf_error_reason: str | None = None
    root_ready_exists = False
    data_pdf_ready_exists = False
    data_pdf_error_exists = False
    report_slide_count = 0
    page_count: int | None = None
    pdf_bytes: bytes = b""

    logger.info(
        "[PDFExport][screenshot.mode]",
        extra={
            "report_id": report_id,
            "final_render_url": export_url,
            "pdf_strategy": "slide_screenshots",
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
                extra_http_headers=(
                    {
                        "Authorization": f"Bearer {auth_token}",
                        "X-Measurable-Export-Auth": "report_export_token",
                    }
                    if auth_token
                    else None
                ),
            )
            page = context.new_page()
            page.emulate_media(media="screen")

            def _handle_response(response: Any) -> None:
                nonlocal logo_status
                url = response.url
                if "/workspace/branding/logo/" in url:
                    logo_status = response.status
                    logger.info(
                        "[PDFExport][logo.status]",
                        extra={
                            "report_id": report_id,
                            "export_url": export_url,
                            "logo_url": url,
                            "status": logo_status,
                            "ok": response.ok,
                        },
                    )
                if f"/reports/{report_id}" in url:
                    report_fetch_events.append(
                        {
                            "url": url,
                            "status": response.status,
                            "ok": response.ok,
                        }
                    )

            def _handle_console(message: Any) -> None:
                entry = {
                    "type": str(getattr(message, "type", "")),
                    "text": str(getattr(message, "text", "") or "")[:1000],
                }
                browser_console_events.append(entry)
                logger.info(
                    "[PDFExport][browser.console]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "message_type": entry["type"],
                        "message_text": entry["text"],
                    },
                )

            def _handle_page_error(error: Any) -> None:
                message = str(error or "")[:1000]
                browser_page_errors.append(message)
                logger.warning(
                    "[PDFExport][browser.pageerror]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "event_message": message,
                    },
                )

            def _handle_request_failed(request: Any) -> None:
                failure = getattr(request, "failure", None)
                failure_text = None
                if callable(failure):
                    failure_info = failure()
                    if isinstance(failure_info, dict):
                        failure_text = failure_info.get("errorText")
                elif isinstance(failure, dict):
                    failure_text = failure.get("errorText")
                entry = {
                    "url": str(getattr(request, "url", "") or "")[:1000],
                    "method": str(getattr(request, "method", "") or ""),
                    "resource_type": str(getattr(request, "resource_type", "") or ""),
                    "failure_text": str(failure_text or "")[:500] or None,
                }
                browser_request_failures.append(entry)
                logger.warning(
                    "[PDFExport][browser.requestfailed]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "request_url": entry["url"],
                        "method": entry["method"],
                        "resource_type": entry["resource_type"],
                        "failure_text": entry["failure_text"],
                    },
                )

            page.on("response", _handle_response)
            page.on("console", _handle_console)
            page.on("pageerror", _handle_page_error)
            page.on("requestfailed", _handle_request_failed)

            main_response = page.goto(export_url, wait_until="domcontentloaded", timeout=timeout_ms)
            main_response_status = main_response.status if main_response is not None else None
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.evaluate("() => document.fonts.ready")
            try:
                page.wait_for_function(
                    """
                    () => {
                      const ready = document.body?.dataset?.pdfReady === "true"
                        || Boolean(window.__MEASURABLE_EXPORT_READY__)
                        || Boolean(document.querySelector("[data-pdf-ready='true'], body[data-pdf-ready='true']"));
                      const slideCount = document.querySelectorAll("[data-report-slide='true'], [data-report-slide]").length;
                      return ready && slideCount > 0;
                    }
                    """,
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError:
                pass
            try:
                page.wait_for_selector(slide_selector, timeout=min(timeout_ms, 5000))
            except PlaywrightTimeoutError:
                logger.warning(
                    "[PDFExport][slide.count]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "status": "selector_timeout",
                    },
                )
            page.wait_for_timeout(350)

            diagnostics = _read_pdf_dom_diagnostics(page)
            logger.info(
                "[PDFExport][dom.diagnostics]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "page_url": diagnostics.get("page_url"),
                    "page_title": diagnostics.get("page_title"),
                    "body_ready": diagnostics.get("bodyReady"),
                    "body_error": diagnostics.get("bodyError"),
                    "body_error_reason": diagnostics.get("bodyErrorReason"),
                    "root_ready_exists": diagnostics.get("rootReadyExists"),
                    "data_pdf_ready_exists": diagnostics.get("data_pdf_ready_exists"),
                    "data_pdf_error_exists": diagnostics.get("data_pdf_error_exists"),
                    "slide_count": diagnostics.get("slide_count"),
                    "diagnostics_error": diagnostics.get("diagnostics_error"),
                },
            )
            current_page_url = str(diagnostics.get("url") or page.url or "").strip() or None
            page_title = str(diagnostics.get("title") or page.title() or "").strip() or None
            page_text_excerpt = str(diagnostics.get("textExcerpt") or "").strip() or None
            body_pdf_ready = str(diagnostics.get("bodyReady") or "").strip() or None
            body_pdf_error = str(diagnostics.get("bodyError") or "").strip() or None
            body_pdf_error_reason = str(diagnostics.get("bodyErrorReason") or "").strip() or None
            root_ready_exists = bool(diagnostics.get("rootReadyExists"))
            data_pdf_ready_exists = bool(diagnostics.get("data_pdf_ready_exists"))
            data_pdf_error_exists = bool(diagnostics.get("data_pdf_error_exists"))
            report_slide_count = int(diagnostics.get("slideCount") or 0)

            logger.info(
                "[PDFExport][slide.count]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "slide_count": report_slide_count,
                    "root_ready_exists": root_ready_exists,
                    "data_pdf_ready_exists": data_pdf_ready_exists,
                },
            )
            logger.info(
                "[PDFExport][page.size]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "pdf_width": pdf_options["width"],
                    "pdf_height": pdf_options["height"],
                },
            )

            if data_pdf_error_exists:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_render_frontend_error",
                        "message": "PDF export page reported a frontend render error.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "page_url": current_page_url,
                        "page_title": page_title,
                        "page_text_excerpt": page_text_excerpt,
                        "body_pdf_error_reason": body_pdf_error_reason,
                    },
                )
            if report_slide_count < 1:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_export_no_slides",
                        "message": "PDF export page did not render any slides.",
                        "report_id": report_id,
                        "export_url": export_url,
                        "page_url": current_page_url,
                        "page_title": page_title,
                    },
                )

            slide_records = page.evaluate(
                """
                () => Array.from(
                  document.querySelectorAll("[data-report-slide='true'], [data-report-slide]")
                ).map((element, index) => {
                  const rawNumber = element.getAttribute("data-slide-number");
                  const parsed = rawNumber ? Number.parseInt(rawNumber, 10) : Number.NaN;
                  return {
                    index,
                    slideNumber: Number.isFinite(parsed) ? parsed : index + 1,
                  };
                }).sort((a, b) => {
                  if (a.slideNumber !== b.slideNumber) return a.slideNumber - b.slideNumber;
                  return a.index - b.index;
                });
                """
            )
            if not isinstance(slide_records, list) or not slide_records:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "pdf_export_no_slides",
                        "message": "PDF export page did not render any slides.",
                        "report_id": report_id,
                        "export_url": export_url,
                    },
                )

            slide_locator = page.locator(slide_selector)
            slide_images: list[dict[str, Any]] = []
            slide_numbers = [int(record.get("slideNumber") or index + 1) for index, record in enumerate(slide_records)]
            for record in slide_records:
                slide_index = int(record.get("index") or 0)
                slide_number = int(record.get("slideNumber") or slide_index + 1)
                try:
                    screenshot_bytes = slide_locator.nth(slide_index).screenshot(
                        type="jpeg",
                        quality=88,
                        animations="disabled",
                    )
                except Exception as exc:
                    logger.exception(
                        "[PDFExport][failure]",
                        extra={
                            "report_id": report_id,
                            "export_url": export_url,
                            "failed_slide_number": slide_number,
                            "pdf_strategy": "slide_screenshots",
                        },
                    )
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": "pdf_export_slide_screenshot_failed",
                            "message": f"Failed to capture slide {slide_number} for PDF export.",
                            "report_id": report_id,
                            "export_url": export_url,
                        },
                    ) from exc
                screenshot_bytes_per_slide.append(len(screenshot_bytes))
                slide_images.append(
                    {
                        "slide_number": slide_number,
                        "mime_type": "image/jpeg",
                        "base64": base64.b64encode(screenshot_bytes).decode("ascii"),
                    }
                )

            page_count = len(slide_images)
            logger.info(
                "[PDFExport][page.pdf.options]",
                extra={
                    "report_id": report_id,
                    "export_url": export_url,
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "device_scale_factor": device_scale_factor,
                    "pdf_width": pdf_options["width"],
                    "pdf_height": pdf_options["height"],
                    "prefer_css_page_size": False,
                },
            )

            pdf_page = context.new_page()
            pdf_page.set_viewport_size({"width": viewport_width, "height": viewport_height})
            pdf_page.set_content(
                """
                <html>
                  <head>
                    <style>
                      @page { size: 1600px 900px; margin: 0; }
                      html, body { margin: 0; padding: 0; background: #ffffff; }
                      .page { width: 1600px; height: 900px; page-break-after: always; break-after: page; }
                      .page:last-child { page-break-after: auto; break-after: auto; }
                      img { display: block; width: 1600px; height: 900px; }
                    </style>
                  </head>
                  <body>
                """
                + "".join(
                    f'<div class="page"><img alt="Slide {item["slide_number"]}" src="data:{item["mime_type"]};base64,{item["base64"]}" /></div>'
                    for item in slide_images
                )
                + "</body></html>",
                wait_until="load",
            )
            pdf_bytes = pdf_page.pdf(**pdf_options)
            pdf_page.close()
            if len(pdf_bytes) > 2 * 1024 * 1024 and page_count <= 5:
                logger.warning(
                    "[PDFExport][size.warning]",
                    extra={
                        "report_id": report_id,
                        "export_url": export_url,
                        "page_count": page_count,
                        "size_bytes": len(pdf_bytes),
                    },
                )
            context.close()
            browser.close()
    except HTTPException:
        raise
    except PlaywrightError as exc:
        logger.exception(
            "[PDFExport][failure]",
            extra={
                "report_id": report_id,
                "export_url": export_url,
                "page_url": current_page_url,
                "page_title": page_title,
                "slide_numbers": slide_numbers,
                "screenshot_bytes_per_slide": screenshot_bytes_per_slide,
                "pdf_strategy": "slide_screenshots",
            },
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "pdf_export_navigation_failed",
                "message": "Chromium failed to render the report export page.",
                "report_id": report_id,
                "export_url": export_url,
                "page_url": current_page_url,
                "page_title": page_title,
            },
        ) from exc

    render_duration_ms = round((perf_counter() - started_at) * 1000, 2)
    logger.info(
        "[PDF_RENDER_RESPONSE]",
        extra={
            "status": main_response_status,
            "page_url": current_page_url or export_url,
            "page_title": page_title,
            "slide_count": report_slide_count or page_count,
            "pdf_bytes": len(pdf_bytes),
        },
    )
    logger.info(
        "[PDFExport][success.size_bytes]",
        extra={
            "report_id": report_id,
            "final_render_url": export_url,
            "slide_count": page_count,
            "slide_numbers": slide_numbers,
            "screenshot_bytes_per_slide": screenshot_bytes_per_slide,
            "final_pdf_size_bytes": len(pdf_bytes),
            "render_duration_ms": render_duration_ms,
            "pdf_strategy": "slide_screenshots",
        },
    )
    return pdf_bytes, {
        "page_count": page_count,
        "export_url": export_url,
        "auth_strategy": auth_strategy,
        "report_fetch_succeeded": any(event.get("ok") for event in report_fetch_events),
        "report_fetch_events": report_fetch_events,
        "page_status": main_response_status,
        "page_url": current_page_url,
        "page_title": page_title,
        "page_text_excerpt": page_text_excerpt,
        "body_pdf_ready": body_pdf_ready,
        "body_pdf_error": body_pdf_error,
        "body_pdf_error_reason": body_pdf_error_reason,
        "root_ready_exists": root_ready_exists,
        "logo_status": logo_status,
        "data_pdf_ready_exists": data_pdf_ready_exists,
        "data_pdf_error_exists": data_pdf_error_exists,
        "report_slide_count": report_slide_count,
        "browser_console_events": browser_console_events[-20:],
        "browser_page_errors": browser_page_errors[-20:],
        "browser_request_failures": browser_request_failures[-20:],
        "slide_numbers": slide_numbers,
        "screenshot_bytes_per_slide": screenshot_bytes_per_slide,
        "pdf_strategy": "slide_screenshots",
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
            "deviceScaleFactor": device_scale_factor,
        },
        "pdf_options": {
            "width": pdf_options["width"],
            "height": pdf_options["height"],
            "margin": pdf_margins,
            "prefer_css_page_size": False,
            "print_background": True,
        },
        "render_duration_ms": render_duration_ms,
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
