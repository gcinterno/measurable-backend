import csv
import io
import importlib
import json
import logging
import os
import re
import secrets
import sys
import requests
from uuid import uuid4
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from datetime import date, timedelta, datetime, timezone, time
from time import perf_counter
from functools import lru_cache
from typing import Any, Literal, cast

from fastapi import Body, Depends, FastAPI, File, Form, Query, Request, UploadFile, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
from sqlalchemy import and_, case, func, inspect, literal, or_
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from .deps import (
    get_current_user,
    get_current_user_for_report_read,
    get_optional_current_user,
    get_db,
    load_current_user,
    load_user_by_email,
    load_user_by_google_sub,
    require_admin_user,
    user_logo_column_available,
    user_onboarding_columns_available,
)
from .errors import http_error
import boto3

from .config import settings
from .crypto import decrypt_secret, encrypt_secret
from .db import SessionLocal, engine
from .integrations.meta_ads import (
    FACEBOOK_PAGES_OAUTH_SCOPE,
    FACEBOOK_PAGES_SCOPES,
    INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN,
    INSTAGRAM_BUSINESS_SCOPES_LEGACY_FACEBOOK_LOGIN,
    META_ADS_OAUTH_SCOPE,
    META_ADS_SCOPES,
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
    get_meta_ads_redirect_uri,
    get_meta_pages_redirect_uri,
    get_businesses,
    get_owned_ad_accounts,
    list_ad_accounts,
    list_pages,
    meta_oauth_scope_string_for_integration_type,
    meta_oauth_scopes_for_integration_type,
    normalize_meta_oauth_integration_type,
    oauth_connect_url,
    oauth_connect_pages_url,
)
from .integrations.tiktok_ads import (
    build_authorization_url as build_tiktok_authorization_url,
    decode_state as decode_tiktok_state,
    encode_state as encode_tiktok_state,
    exchange_auth_code_for_token,
    fetch_daily_advertiser_report,
    get_authorized_advertisers,
    normalize_tiktok_report_to_dataset_payload,
    tiktok_missing_env_flags,
)
from .integrations.shopify import (
    SHOPIFY_COMPLIANCE_TOPICS,
    SHOPIFY_OAUTH_STATE_PURPOSE,
    SHOPIFY_PROVIDER,
    SHOPIFY_STATUS_CONNECTED,
    SHOPIFY_STATUS_DISCONNECTED,
    SHOPIFY_STATUS_ERROR,
    exchange_code_for_access_token as exchange_shopify_code_for_access_token,
    fetch_orders_metrics,
    fetch_shop_details,
    normalize_shop_domain,
    resolve_shopify_timeframe,
    shopify_authorize_url,
    shopify_callback_hmac_valid,
    shopify_missing_config,
    shopify_webhook_hmac_valid,
)
from .integrations.meta_capi import send_meta_capi_event
from .integrations.instagram_business import (
    INSTAGRAM_BUSINESS_CALLBACK_PATH,
    INSTAGRAM_BUSINESS_OAUTH_SCOPE,
    INSTAGRAM_BUSINESS_SCOPES,
    build_instagram_business_auth_url,
    decode_instagram_business_state,
    encode_instagram_business_state,
    exchange_instagram_business_code_for_token,
    fetch_instagram_business_profile,
    get_missing_instagram_business_config_fields,
    get_instagram_business_redirect_uri,
)
from .models import (
    AccountDeletionFeedback,
    AuditLog,
    Conversation,
    Dataset,
    DatasetFile,
    EmailVerificationCode,
    Export,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    Job,
    Message,
    MetaAdAccount,
    MetaAdsInsightDaily,
    MetaPage,
    ReferralClick,
    ReferralConversion,
    ReferralPartner,
    Report,
    ReportBlock,
    ReportShare,
    ReportSource,
    ReportVersion,
    Schedule,
    ShopifyConnection,
    ShopifyOAuthState,
    ShopifySnapshot,
    Subscription,
    User,
    UserAttribution,
    UserSuggestion,
    WishlistLead,
    Workspace,
    WorkspaceMember,
)
from .schemas import (
    ChatMessageIn,
    ChatReplyOut,
    AuthMessageOut,
    AdminDeletionFeedbackOut,
    AdminDeletionInsightsOut,
    AdminDeletionReasonCountsOut,
    AdminGoalCountsOut,
    AdminInsightsOut,
    AdminFunnelOut,
    AdminFunnelStepOut,
    AdminCohortOut,
    AdminCohortAveragesOut,
    AdminCohortRetentionOut,
    AdminCohortsOut,
    AdminProductMetricsOut,
    AdminMetricsOut,
    AdminOnboardingCountsOut,
    AdminOnboardingInsightsOut,
    AdminPlatformCountsOut,
    AdminSuggestionOut,
    AdminWishlistLeadOut,
    AdminUserOut,
    AdminUsersOut,
    AccountSummaryOut,
    BillingCheckoutSessionIn,
    BillingCheckoutSessionOut,
    BillingMeOut,
    BillingPlanChangePreviewOut,
    BillingPlanSnapshotOut,
    BillingPortalSessionOut,
    DeleteAccountIn,
    DeleteAccountOut,
    ConversationOut,
    DatasetDetailOut,
    DatasetUploadOut,
    InstagramBusinessReportCreateIn,
    InstagramBusinessSyncIn,
    InstagramBusinessSyncOut,
    OnboardingCompleteOut,
    OnboardingStateOut,
    OnboardingUpdate,
    LoginIn,
    MeOut,
    MeUpdateIn,
    IntegrationSchema,
    MetaPageOut,
    MetaPageCatalogOut,
    MetaDisconnectIn,
    MetaDisconnectClearedOut,
    MetaDisconnectOut,
    MetaPagesReportCreateIn,
    MetaPagesRefreshIn,
    MetaPagesRefreshOut,
    MetaPagesReportCreateOut,
    MetaSyncAllIn,
    MetaSyncAllOut,
    MetaSyncAllResultsOut,
    MetaSyncSourceResultOut,
    MetaTrackingEventIn,
    MetaTrackingEventOut,
    MetaPagesSyncOut,
    MetaSelectAccountIn,
    MetaSelectAccountManualIn,
    MetaAdsAccountOut,
    MetaAdsConnectOut,
    MetaAdsDisconnectOut,
    MetaAdsSelectAccountIn,
    MetaAdsStatusOut,
    MetaAdsSyncIn,
    MetaAdsSyncOut,
    MetaSelectPageIn,
    MetaSetTokenManualIn,
    MessageOut,
    PlanLimitsOut,
    MultiSourceReportCreateRequest,
    RegisterIn,
    RegisterOut,
    ReferralClickIn,
    ReferralClickOut,
    ReferralConversionOut,
    ReferralManualConversionIn,
    ReferralPartnerCreateIn,
    ReferralPartnerOut,
    ReferralSummaryOut,
    ResendVerificationCodeIn,
    ForgotPasswordIn,
    ResetPasswordIn,
    VerifyEmailIn,
    VerifyEmailOut,
    PublicReportOut,
    PublicSharedReportOut,
    PublicSharedReportVersionOut,
    ReportBlockOut,
    ReportExportOut,
    ReportListItemOut,
    ReportBlockUpdateIn,
    ReportCreateIn,
    ReportDeleteOut,
    ReportFolderUpdateIn,
    ReportFolderUpdateOut,
    ReportIntegrationMetadataOut,
    ReportOut,
    ReportShareCreateOut,
    ReportShareRevokeOut,
    ReportSourceRead,
    ReportVersionOut,
    ScheduleCreateIn,
    ScheduleSchema,
    ScheduleUpdateIn,
    ShopifyDisconnectOut,
    ShopifyReportCreateIn,
    ShopifyStatusOut,
    ShopifySyncIn,
    ShopifySyncOut,
    SuggestionCreateIn,
    SuggestionCreateOut,
    SuggestionStatusUpdateIn,
    TikTokAdvertiserAccountOut,
    TikTokAdvertiserAccountsOut,
    TikTokCallbackCompleteIn,
    TikTokCallbackCompleteOut,
    TikTokConnectOut,
    TikTokDisconnectOut,
    TikTokSelectAccountIn,
    TikTokStatusOut,
    TikTokSyncIn,
    TikTokSyncOut,
    TokenOut,
    UserSuggestionOut,
    WishlistCreateIn,
    WishlistCreateOut,
    WishlistLeadOut,
    WorkspaceBrandingLogoUploadOut,
    WorkspaceBrandingUpdateIn,
    WorkspaceUpdateIn,
    WorkspaceCreateIn,
    WorkspaceOut,
)
from .security import (
    TokenError,
    create_access_token,
    create_report_export_token,
    create_oauth_state,
    decode_oauth_state,
    hash_password,
    verify_password,
)
from .ai_agents import (
    build_ai_agent_metadata,
    build_ai_agent_plan_context,
    normalize_ai_mode,
    run_ai_agents_pipeline,
)
from .services import (
    _mask_email,
    _safe_email_delivery_reason,
    build_export_payload,
    build_conversation_title,
    build_meta_pages_ai_payload,
    generate_meta_pages_ai_summary,
    generate_thumbnail_from_export_page,
    build_meta_pages_reach_chart_data,
    build_meta_pages_reach_insight,
    build_meta_pages_recent_posts_summary,
    build_meta_pages_summary,
    build_ai_chat_context_snapshot,
    AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
    AUTH_CODE_PURPOSE_PASSWORD_RESET,
    build_default_workspace_name,
    build_auth_email_html,
    build_auth_email_text,
    create_manual_referral_conversion,
    create_referral_click,
    issue_auth_code,
    send_auth_email,
    validate_auth_code,
    count_workspace_reports_total,
    count_workspace_storage_bytes,
    enforce_report_creation_limit,
    enforce_storage_limit,
    enforce_export_capability,
    extract_meta_pages_report_inputs,
    enqueue_job,
    finalize_export_response,
    generate_workspace_ai_reply,
    get_plan_code_for_stripe_price,
    get_plan_entitlements,
    get_workspace_plan,
    get_workspace_report_quota_status,
    get_workspace_subscription,
    get_plan_limits,
    get_stripe_price_plan_mapping,
    get_subscription_entitlements,
    hash_client_ip,
    normalize_workspace_plan,
    report_branding_mode_for_plan,
    resolve_account_display_name,
    resolve_report_slide_limits,
    resolve_report_branding_for_workspace,
    resolve_meta_pages_timeframe,
    record_first_report_conversion,
    record_signup_attribution,
    register_user_with_default_workspace,
    generate_pdf_from_export_page,
    normalize_report_locale,
    normalize_meta_recent_posts,
    apply_plan_entitlements,
    build_report_pdf_export_url,
    can_schedule_report,
    can_use_multi_platform_report,
    resolve_report_branding,
    resolve_workspace_branding,
    store_report_thumbnail,
    trigger_export_service,
    _generate_download_url,
)

logger = logging.getLogger(__name__)
DEFAULT_GENERATED_REPORT_SLIDE_COUNT = 2
INTEGRATIONS_TOTAL_AVAILABLE = 5
META_PAGES_CACHE_TTL = timedelta(hours=6)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _SafeAuthEmailFormatter(logging.Formatter):
    _SAFE_KEYS = ("email", "message_id", "reason", "purpose", "configuration_set")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        safe_fields: list[str] = []
        for key in self._SAFE_KEYS:
            value = getattr(record, key, None)
            if value is None or value == "":
                continue
            safe_fields.append(f"{key}={value}")
        if not safe_fields:
            return base
        return f"{base} {' '.join(safe_fields)}"


def _resolve_log_level(value: str | None) -> int:
    normalized = str(value or "").strip().upper()
    return getattr(logging, normalized, logging.INFO)


def _configure_application_logging() -> None:
    formatter = _SafeAuthEmailFormatter("%(levelname)s %(name)s %(message)s")
    root_logger = logging.getLogger()
    log_level = _resolve_log_level(settings.log_level)

    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    root_logger.setLevel(log_level)
    logging.getLogger("app").setLevel(log_level)
    logger.setLevel(log_level)


_configure_application_logging()


def _workspace_account_display_payload(workspace: Workspace | None, user: User | None) -> dict[str, str | None]:
    return resolve_account_display_name(workspace, user)


def _enforce_report_creation_limit_or_response(
    db: Session,
    workspace_id: int,
) -> JSONResponse | None:
    try:
        enforce_report_creation_limit(db, workspace_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code == 403 and str(detail.get("code") or "").strip() == "monthly_report_limit_reached":
            message = str(detail.get("message") or "You have reached your monthly report limit.")
            return JSONResponse(
                status_code=403,
                content={
                    "code": "monthly_report_limit_reached",
                    "detail": message,
                    "message": message,
                    "reports_used": detail.get("reports_used"),
                    "reports_limit": detail.get("reports_limit"),
                    "reports_remaining": detail.get("reports_remaining"),
                    "period_start": detail.get("period_start"),
                    "period_end": detail.get("period_end"),
                    "plan": detail.get("plan"),
                },
            )
        raise
    return None


def _wishlist_lead_out(lead: WishlistLead) -> WishlistLeadOut:
    return WishlistLeadOut(
        id=lead.id,
        user_id=lead.user_id,
        workspace_id=lead.workspace_id,
        name=lead.name,
        email=lead.email,
        company=lead.company,
        message=lead.message,
        source=lead.source,
        created_at=lead.created_at,
    )


def _frontend_url() -> str:
    return str(settings.frontend_url or settings.frontend_base_url or "").strip().rstrip("/")


def _pdf_render_base_url() -> str:
    configured = str(
        settings.report_export_base_url or settings.frontend_url or settings.frontend_base_url or ""
    ).strip().rstrip("/")
    if configured:
        return configured
    raise http_error(
        500,
        "frontend_url_not_configured",
        "FRONTEND_URL or REPORT_EXPORT_BASE_URL is required for PDF export.",
    )


def _billing_portal_return_url() -> str:
    configured = str(settings.stripe_billing_portal_return_url or "").strip()
    if configured:
        return configured
    frontend_url = _frontend_url()
    return frontend_url or "http://localhost:3000"


def _stripe_checkout_success_url() -> str:
    frontend_url = _frontend_url()
    if not frontend_url:
        raise http_error(500, "frontend_url_not_configured", "FRONTEND_URL is required for Stripe checkout.")
    return f"{frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"


def _stripe_checkout_cancel_url() -> str:
    frontend_url = _frontend_url()
    if not frontend_url:
        raise http_error(500, "frontend_url_not_configured", "FRONTEND_URL is required for Stripe checkout.")
    return f"{frontend_url}/billing/cancel"


def _get_stripe_module():
    try:
        return importlib.import_module("stripe")
    except ImportError as exc:
        raise http_error(500, "stripe_unavailable", "Stripe dependency is not installed.") from exc


def _require_stripe_configuration() -> None:
    required_values = {
        "STRIPE_SECRET_KEY": settings.stripe_secret_key,
        "STRIPE_WEBHOOK_SECRET": settings.stripe_webhook_secret,
        "STRIPE_PRICE_STARTER_MONTHLY": settings.stripe_price_starter_monthly,
        "STRIPE_PRICE_PRO_MONTHLY": settings.stripe_price_pro_monthly,
        "STRIPE_PRICE_ADVANCED_MONTHLY": settings.stripe_price_advanced_monthly,
    }
    missing = [key for key, value in required_values.items() if not str(value or "").strip()]
    if missing:
        raise http_error(
            500,
            "stripe_not_configured",
            f"Missing Stripe configuration: {', '.join(missing)}.",
        )


def _configure_stripe():
    _require_stripe_configuration()
    stripe = _get_stripe_module()
    stripe.api_key = str(settings.stripe_secret_key or "").strip()
    return stripe


def _billing_status_for_subscription(subscription: Subscription | None) -> str:
    if subscription is None:
        return "free"
    explicit = str(subscription.billing_status or "").strip()
    if explicit:
        return explicit
    if normalize_workspace_plan(subscription.plan or "free") == "free":
        return "free"
    return str(subscription.status or "active").strip() or "active"


def _billing_me_out(db: Session, subscription: Subscription | None, workspace_id: int) -> BillingMeOut:
    entitlements = get_subscription_entitlements(subscription)
    quota = get_workspace_report_quota_status(db, workspace_id)
    return BillingMeOut(
        plan_code=str(entitlements["plan_code"]),
        plan_name=_plan_display_name(str(entitlements["plan_code"])),
        billing_status=_billing_status_for_subscription(subscription),
        current_period_end=subscription.current_period_end if subscription is not None else None,
        price_monthly_usd=int(entitlements["price_monthly_usd"]),
        cancel_at_period_end=bool(subscription.cancel_at_period_end) if subscription is not None else False,
        reports_limit_monthly=entitlements["reports_limit_monthly"],
        reports_used_current_month=int(quota["reports_used"]),
        slides_per_report_limit=int(entitlements["slides_per_report_limit"]),
        platform_report_type=str(entitlements["platform_report_type"]),
        ai_chat_with_data=bool(entitlements["ai_chat_with_data"]),
        storage_limit_gb=int(entitlements["storage_limit_gb"]),
        export_pdf=bool(entitlements["export_pdf"]),
        export_pptx=bool(entitlements["export_pptx"]),
        brand_personalization=bool(entitlements["brand_personalization"]),
        measurable_watermark=bool(entitlements["measurable_watermark"]),
        scheduled_reports_limit=entitlements["scheduled_reports_limit"],
        trial_new_features=bool(entitlements["trial_new_features"]),
    )


def _resolve_workspace_subscription_for_user(db: Session, current_user: User) -> tuple[Workspace, Subscription]:
    workspace, subscription = _ensure_user_workspace_and_subscription(db, user=current_user)
    apply_plan_entitlements(subscription, subscription.plan or "free")
    return workspace, subscription


def _plan_display_name(plan_code: str | None) -> str:
    normalized = normalize_workspace_plan(plan_code or "free")
    if normalized == "free":
        return "Free"
    if normalized == "starter":
        return "Starter"
    if normalized == "pro":
        return "Pro"
    if normalized == "advanced":
        return "Advanced"
    return normalized.replace("_", " ").title()


def _billing_plan_snapshot(plan_code: str) -> BillingPlanSnapshotOut:
    entitlements = get_plan_entitlements(plan_code)
    return BillingPlanSnapshotOut(
        plan_code=str(entitlements["plan_code"]),
        plan_name=_plan_display_name(str(entitlements["plan_code"])),
        price_monthly_usd=int(entitlements["price_monthly_usd"]),
        reports_limit_monthly=entitlements["reports_limit_monthly"],
        slides_per_report_limit=int(entitlements["slides_per_report_limit"]),
        export_pdf=bool(entitlements["export_pdf"]),
        export_pptx=bool(entitlements["export_pptx"]),
        brand_personalization=bool(entitlements["brand_personalization"]),
        measurable_watermark=bool(entitlements["measurable_watermark"]),
        scheduled_reports_limit=entitlements["scheduled_reports_limit"],
    )


def _find_subscription_by_stripe_customer(db: Session, customer_id: str | None) -> Subscription | None:
    normalized_customer_id = str(customer_id or "").strip()
    if not normalized_customer_id:
        return None
    return (
        db.query(Subscription)
        .filter(Subscription.stripe_customer_id == normalized_customer_id)
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .first()
    )


def _find_subscription_by_stripe_subscription(db: Session, stripe_subscription_id: str | None) -> Subscription | None:
    normalized_subscription_id = str(stripe_subscription_id or "").strip()
    if not normalized_subscription_id:
        return None
    return (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == normalized_subscription_id)
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .first()
    )


def _find_subscription_for_stripe_event(
    db: Session,
    *,
    stripe_subscription_id: str | None,
    stripe_customer_id: str | None,
) -> Subscription | None:
    return _find_subscription_by_stripe_subscription(db, stripe_subscription_id) or _find_subscription_by_stripe_customer(
        db, stripe_customer_id
    )


ACTIVE_STRIPE_BILLING_STATUSES = {"active", "trialing", "past_due"}


def _stripe_object_get(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return None
    return current


def _stripe_subscription_status(value: Any) -> str | None:
    status = str(_stripe_object_get(value, "status") or "").strip().lower()
    return status or None


def _stripe_subscription_price_id(value: Any) -> str | None:
    items = _stripe_object_get(value, "items", "data")
    if not isinstance(items, list) or not items:
        return None
    return str(_stripe_object_get(items[0], "price", "id") or "").strip() or None


def _stripe_subscription_item_id(value: Any) -> str | None:
    items = _stripe_object_get(value, "items", "data")
    if not isinstance(items, list) or not items:
        return None
    return str(_stripe_object_get(items[0], "id") or "").strip() or None


def _is_reusable_stripe_subscription(value: Any) -> bool:
    return str(_stripe_subscription_status(value) or "") in ACTIVE_STRIPE_BILLING_STATUSES


def _is_missing_stripe_subscription_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "").strip().lower()
    if code == "resource_missing":
        return True
    message = str(exc).lower()
    return "no such subscription" in message or "resource_missing" in message


def _retrieve_existing_stripe_subscription(
    stripe: Any,
    *,
    stripe_subscription_id: str | None,
) -> Any | None:
    normalized_subscription_id = str(stripe_subscription_id or "").strip()
    if not normalized_subscription_id:
        return None
    try:
        return stripe.Subscription.retrieve(normalized_subscription_id)
    except Exception as exc:
        if _is_missing_stripe_subscription_error(exc):
            return None
        raise http_error(502, "stripe_subscription_retrieve_failed", "Stripe subscription could not be retrieved.") from exc


def _list_customer_stripe_subscriptions(
    stripe: Any,
    *,
    customer_id: str | None,
) -> list[Any]:
    normalized_customer_id = str(customer_id or "").strip()
    if not normalized_customer_id:
        return []
    try:
        response = stripe.Subscription.list(customer=normalized_customer_id, status="all", limit=10)
    except AttributeError:
        return []
    except Exception as exc:
        raise http_error(502, "stripe_subscription_list_failed", "Stripe subscriptions could not be listed.") from exc
    items = _stripe_object_get(response, "data")
    return items if isinstance(items, list) else []


def _find_reusable_stripe_subscription(
    stripe: Any,
    *,
    subscription: Subscription,
) -> Any | None:
    retrieved = _retrieve_existing_stripe_subscription(
        stripe,
        stripe_subscription_id=subscription.stripe_subscription_id,
    )
    if retrieved is not None:
        if _is_reusable_stripe_subscription(retrieved):
            return retrieved
        return None

    candidates = _list_customer_stripe_subscriptions(
        stripe,
        customer_id=subscription.stripe_customer_id,
    )
    for candidate in candidates:
        if _is_reusable_stripe_subscription(candidate):
            return candidate
    return None


def _sync_local_subscription_from_stripe_object(
    db: Session,
    *,
    subscription: Subscription,
    stripe_subscription: Any,
) -> Subscription:
    price_id = _stripe_subscription_price_id(stripe_subscription)
    plan_code = get_plan_code_for_stripe_price(price_id) or subscription.plan or "free"
    _apply_stripe_subscription_state(
        subscription,
        plan_code=plan_code,
        billing_status=_stripe_subscription_status(stripe_subscription) or "active",
        stripe_customer_id=_stripe_object_get(stripe_subscription, "customer"),
        stripe_subscription_id=_stripe_object_get(stripe_subscription, "id"),
        stripe_price_id=price_id,
        current_period_start=_stripe_object_get(stripe_subscription, "current_period_start"),
        current_period_end=_stripe_object_get(stripe_subscription, "current_period_end"),
        cancel_at_period_end=_stripe_object_get(stripe_subscription, "cancel_at_period_end"),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def _set_subscription_period_dates(
    subscription: Subscription,
    *,
    current_period_start: Any,
    current_period_end: Any,
) -> None:
    subscription.current_period_start = (
        datetime.fromtimestamp(int(current_period_start), tz=timezone.utc)
        if current_period_start is not None
        else None
    )
    subscription.current_period_end = (
        datetime.fromtimestamp(int(current_period_end), tz=timezone.utc)
        if current_period_end is not None
        else None
    )


def _downgrade_subscription_to_free(subscription: Subscription) -> Subscription:
    apply_plan_entitlements(subscription, "free")
    subscription.status = "active"
    subscription.billing_status = "free"
    subscription.stripe_subscription_id = None
    subscription.stripe_price_id = None
    subscription.current_period_start = None
    subscription.current_period_end = None
    subscription.cancel_at_period_end = False
    return subscription


def _apply_stripe_subscription_state(
    subscription: Subscription,
    *,
    plan_code: str,
    billing_status: str | None,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    stripe_price_id: str | None,
    current_period_start: Any,
    current_period_end: Any,
    cancel_at_period_end: Any,
) -> Subscription:
    apply_plan_entitlements(subscription, plan_code)
    subscription.status = "active"
    subscription.billing_status = str(billing_status or "active").strip() or "active"
    subscription.stripe_customer_id = str(stripe_customer_id or "").strip() or subscription.stripe_customer_id
    subscription.stripe_subscription_id = str(stripe_subscription_id or "").strip() or None
    subscription.stripe_price_id = str(stripe_price_id or "").strip() or None
    subscription.cancel_at_period_end = bool(cancel_at_period_end)
    _set_subscription_period_dates(
        subscription,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )
    return subscription


def _latest_report_version(db: Session, report_id: int) -> ReportVersion | None:
    return (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
        .first()
    )


def _clean_pdf_file_name(report_name: str | None) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(report_name or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    return normalized or "Report"


def _frontend_share_base_url(request: Request) -> str:
    frontend_url = _frontend_url()
    if frontend_url:
        return frontend_url
    return str(request.base_url).rstrip("/")


def _public_pdf_frontend_base_url() -> str:
    return _pdf_render_base_url()


def _active_report_share(db: Session, report_id: int) -> ReportShare | None:
    now = datetime.now(timezone.utc)
    return (
        db.query(ReportShare)
        .filter(
            ReportShare.report_id == report_id,
            ReportShare.is_active.is_(True),
            ReportShare.revoked_at.is_(None),
            or_(ReportShare.expires_at.is_(None), ReportShare.expires_at > now),
        )
        .order_by(ReportShare.created_at.desc(), ReportShare.id.desc())
        .first()
    )


def _latest_report_share(db: Session, report_id: int) -> ReportShare | None:
    return (
        db.query(ReportShare)
        .filter(ReportShare.report_id == report_id)
        .order_by(ReportShare.created_at.desc(), ReportShare.id.desc())
        .first()
    )


def _valid_report_share_by_token(db: Session, share_token: str) -> ReportShare | None:
    now = datetime.now(timezone.utc)
    return (
        db.query(ReportShare)
        .filter(
            ReportShare.token == str(share_token or "").strip(),
            ReportShare.is_active.is_(True),
            ReportShare.revoked_at.is_(None),
            or_(ReportShare.expires_at.is_(None), ReportShare.expires_at > now),
        )
        .order_by(ReportShare.created_at.desc(), ReportShare.id.desc())
        .first()
    )


def _resolve_shared_report_context(
    db: Session,
    share_token: str,
) -> tuple[ReportShare, Report, ReportVersion]:
    share = _valid_report_share_by_token(db, share_token)
    if share is None:
        raise http_error(404, "share_link_not_found", "Share link not found.")

    report = db.get(Report, share.report_id)
    if report is None:
        raise http_error(404, "share_link_not_found", "Share link not found.")
    report_version = _latest_report_version(db, report.id)
    if report_version is None:
        raise http_error(404, "share_link_not_found", "Share link not found.")
    return share, report, report_version


def _normalize_report_template_value(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized):
        return None
    return normalized


def _stored_report_template(report: Report) -> str | None:
    return _normalize_report_template_value(_report_metadata(report).get("template"))


def _effective_report_template(report: Report, incoming_template: Any = None) -> tuple[str | None, str]:
    requested = _normalize_report_template_value(incoming_template)
    if requested:
        return requested, "query"
    stored = _stored_report_template(report)
    if stored:
        return stored, "report"
    return "executive", "default"


def _pdf_export_timestamp(incoming_ts: Any = None) -> str:
    normalized = str(incoming_ts or "").strip()
    if normalized:
        return normalized
    return str(int(datetime.now(timezone.utc).timestamp()))


def _pdf_cache_key(
    *,
    report_id: int,
    version_id: int,
    effective_template: str | None,
    report_updated_at: datetime | None,
    version_updated_at: datetime | None,
) -> str:
    return "|".join(
        [
            f"report_id={report_id}",
            f"version_id={version_id}",
            f"effective_template={effective_template or 'default'}",
            "report_updated_at="
            + (report_updated_at.isoformat() if isinstance(report_updated_at, datetime) else "none"),
            "version_updated_at="
            + (version_updated_at.isoformat() if isinstance(version_updated_at, datetime) else "none"),
        ]
    )


def _public_report_out(
    db: Session,
    report: Report,
    report_version: ReportVersion,
    *,
    template_override: Any = None,
) -> PublicReportOut:
    metadata = _report_metadata(report)
    timeframe = _report_timeframe(report)
    report_branding = _report_branding(db, report)
    integration_metadata = derive_report_integration_metadata(db, report)
    effective_template, _template_source = _effective_report_template(report, template_override)
    period_start = (
        str(
            (timeframe or {}).get("since")
            or (timeframe or {}).get("current_since")
            or (timeframe or {}).get("start_date")
            or (timeframe or {}).get("start")
            or ""
        )[:10]
        or None
    )
    period_end = (
        str(
            (timeframe or {}).get("until")
            or (timeframe or {}).get("current_until")
            or (timeframe or {}).get("end_date")
            or (timeframe or {}).get("end")
            or ""
        )[:10]
        or None
    )
    blocks = (
        db.query(ReportBlock)
        .filter(ReportBlock.report_version_id == report_version.id)
        .order_by(ReportBlock.order.asc(), ReportBlock.id.asc())
        .all()
    )
    logger.info(
        "[PUBLIC_REPORT_PAYLOAD_AUDIT]",
        extra={
            "share_token": None,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "has_blocks": bool(blocks),
            "blocks_count": len(blocks),
            "report_title": report.name,
            "integration_type": integration_metadata.integration_type,
            "integration_label": integration_metadata.integration_display_name,
            "source_name": integration_metadata.source_name,
            "brand_name": report_branding.get("resolved_brand_name"),
            "logo_url": report_branding.get("resolved_logo_url"),
            "period_start": period_start,
            "period_end": period_end,
            "template": effective_template,
        },
    )
    return PublicReportOut(
        report=PublicSharedReportOut(
            id=report.id,
            workspace_id=report.workspace_id,
            title=report.name,
            integration_type=integration_metadata.integration_type,
            integration_label=integration_metadata.integration_display_name,
            source_name=integration_metadata.source_name,
            channel=integration_metadata.channel,
            brand_name=str(report_branding.get("resolved_brand_name") or "").strip() or None,
            logo_url=str(report_branding.get("resolved_logo_url") or "").strip() or None,
            period_start=period_start,
            period_end=period_end,
            template=effective_template,
            description=metadata,
            timeframe=timeframe,
            locale=_report_locale(report),
            branding=report_branding,
            thumbnail_url=_report_thumbnail_url(report),
            created_at=report.created_at,
            updated_at=report.updated_at,
        ),
        version=PublicSharedReportVersionOut(
            id=report_version.id,
            report_id=report.id,
            version=report_version.version,
            created_at=report_version.created_at,
            updated_at=report_version.updated_at,
        ),
        blocks=[_report_block_out(block, report_branding) for block in blocks],
        is_public_share=True,
    )


def _workspace_plan_snapshot(db: Session, workspace_id: int) -> dict[str, object]:
    plan = get_workspace_plan(db, workspace_id)
    plan_limits = get_plan_limits(plan)
    can_use_custom_branding = bool(plan_limits.get("allow_custom_branding"))
    return {
        "plan": plan,
        "plan_limits": plan_limits,
        "is_free_plan": plan == "free",
        "can_use_custom_branding": can_use_custom_branding,
        "report_branding_mode": report_branding_mode_for_plan(plan),
    }


def _workspace_summary_out(db: Session, workspace: Workspace, user: User | None) -> AccountSummaryOut:
    plan_snapshot = _workspace_plan_snapshot(db, workspace.id)
    quota = get_workspace_report_quota_status(db, workspace.id)
    reports_created_count = count_workspace_reports_total(db, workspace.id)
    reports_remaining_this_month = quota["reports_remaining"]
    reports_available_count = quota["reports_remaining"]
    integrations_connected_count = int(
        db.query(func.count(Integration.id))
        .filter(Integration.workspace_id == workspace.id, func.lower(Integration.status) == "connected")
        .scalar()
        or 0
    )
    account_display = _workspace_account_display_payload(workspace, user)
    return AccountSummaryOut(
        reports_created_count=reports_created_count,
        reports_available_count=reports_available_count,
        reports_remaining_this_month=reports_remaining_this_month,
        reports_limit_this_month=quota["reports_limit"],
        integrations_connected_count=integrations_connected_count,
        integrations_total_available=INTEGRATIONS_TOTAL_AVAILABLE,
        current_plan_name=str(plan_snapshot["plan"]),
        current_plan_code=str(plan_snapshot["plan"]),
        is_free_plan=bool(plan_snapshot["is_free_plan"]),
        can_use_custom_branding=bool(plan_snapshot["can_use_custom_branding"]),
        report_branding_mode=str(plan_snapshot["report_branding_mode"]),
        account_display_name=account_display["account_display_name"],
        account_display_name_effective=str(account_display["account_display_name_effective"]),
    )


def _workspace_out(db: Session, workspace: Workspace) -> WorkspaceOut:
    plan_snapshot = _workspace_plan_snapshot(db, workspace.id)
    plan = str(plan_snapshot["plan"])
    plan_limits = PlanLimitsOut(**dict(plan_snapshot["plan_limits"]))
    storage_used_bytes = count_workspace_storage_bytes(db, workspace.id)
    branding = resolve_report_branding(
        None,
        workspace,
        plan,
    )
    account_display = _workspace_account_display_payload(workspace, None)
    workspace_branding = resolve_workspace_branding(workspace.id)
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        account_display_name=account_display["account_display_name"],
        account_display_name_effective=str(account_display["account_display_name_effective"]),
        logo_url=workspace_branding.get("logo_url"),
        brand_name=workspace.name or None,
        brand_logo_url=workspace_branding.get("logo_url"),
        branding=branding,
        plan=plan,
        plan_limits=plan_limits,
        storage_used_bytes=storage_used_bytes,
        storage_limit_bytes=plan_limits.storage_limit_bytes,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _update_workspace_from_payload(workspace: Workspace, payload: WorkspaceUpdateIn) -> tuple[bool, bool]:
    branding_changed = False
    account_changed = False
    if "account_display_name" in payload.model_fields_set:
        workspace.account_display_name = str(payload.account_display_name or "").strip() or None
        account_changed = True
    if payload.name is not None:
        workspace.name = payload.name
        branding_changed = True
    if "brand_name" in payload.model_fields_set:
        workspace.name = payload.brand_name or ""
        branding_changed = True
    if "logo_url" in payload.model_fields_set:
        workspace.logo_url = payload.logo_url
        branding_changed = True
    if "brand_logo_url" in payload.model_fields_set:
        workspace.logo_url = payload.brand_logo_url
        branding_changed = True
    return branding_changed, account_changed


def _resolve_workspace_branding_update_payload(
    payload: WorkspaceBrandingUpdateIn,
) -> tuple[bool, str | None, bool, str | None]:
    brand_name_provided = False
    resolved_brand_name: str | None = None
    for field_name in ("brand_name", "brandName", "name"):
        if field_name in payload.model_fields_set:
            brand_name_provided = True
            value = getattr(payload, field_name, None)
            resolved_brand_name = str(value or "").strip() or ""
            break

    logo_provided = False
    resolved_logo_url: str | None = None
    remove_logo = bool(payload.remove_logo) if "remove_logo" in payload.model_fields_set else False
    if remove_logo:
        logo_provided = True
        resolved_logo_url = None
    else:
        for field_name in ("brand_logo_url", "logo_url", "logoUrl"):
            if field_name in payload.model_fields_set:
                logo_provided = True
                value = getattr(payload, field_name, None)
                if value is None:
                    resolved_logo_url = None
                else:
                    cleaned = str(value).strip()
                    resolved_logo_url = cleaned or None
                break

    return brand_name_provided, resolved_brand_name, logo_provided, resolved_logo_url


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
        "safe_message": _safe_exception_message(exc),
        "driver_exception_type": type(orig).__name__ if orig is not None else None,
        "driver_message": str(orig) if orig is not None else None,
        "pgcode": getattr(orig, "pgcode", None),
        "schema_name": getattr(diag, "schema_name", None) if diag is not None else None,
        "table_name": getattr(diag, "table_name", None) if diag is not None else None,
        "column_name": getattr(diag, "column_name", None) if diag is not None else None,
        "constraint_name": getattr(diag, "constraint_name", None) if diag is not None else None,
        "sqlalchemy_exception": exc.__class__.__name__,
    }


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message[:200]


def _tracking_client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded_for = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client is not None else None


def _tracking_event_source_url(request: Request | None, fallback_path: str | None = None) -> str | None:
    if request is not None:
        referer = str(request.headers.get("referer") or "").strip()
        if referer:
            return referer
    base_url = str(settings.frontend_base_url or settings.frontend_url or "").strip().rstrip("/")
    if not base_url or not fallback_path:
        return None
    normalized_path = fallback_path if fallback_path.startswith("/") else f"/{fallback_path}"
    return f"{base_url}{normalized_path}"


def _track_meta_event(
    *,
    event_name: str,
    user: User | None,
    request: Request | None = None,
    event_id: str | None = None,
    event_source_url: str | None = None,
    fbp: str | None = None,
    fbc: str | None = None,
    custom_data: dict[str, Any] | None = None,
    action_source: str = "website",
) -> bool:
    try:
        request_fbp = request.cookies.get("_fbp") if request is not None else None
        request_fbc = request.cookies.get("_fbc") if request is not None else None
        return send_meta_capi_event(
            event_name=event_name,
            event_id=event_id or str(uuid4()),
            event_source_url=event_source_url,
            user_email=user.email if user is not None else None,
            user_id=str(user.id) if user is not None else None,
            client_ip_address=_tracking_client_ip(request),
            client_user_agent=str(request.headers.get("user-agent") or "").strip() if request is not None else None,
            fbp=fbp or request_fbp,
            fbc=fbc or request_fbc,
            custom_data=custom_data,
            action_source=action_source,
        )
    except Exception as exc:
        logger.warning(
            "meta_capi_event_dispatch_failed",
            extra={
                "event_name": event_name,
                "reason": _safe_exception_message(exc),
            },
        )
        return False


def _safe_register_email_failure_reason(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "").strip()
    if code == "email_service_unavailable":
        if not str(settings.ses_from_email or "").strip():
            return "SES_FROM_EMAIL missing"
        if not str(settings.aws_region or "").strip():
            return "AWS_REGION missing"
        return "email service unavailable"
    if code == "email_delivery_failed" and exc.__cause__ is not None:
        return _safe_email_delivery_reason(cast(Exception, exc.__cause__))
    message = str(detail.get("message") or "").strip()
    return message[:200] if message else "email send failed"


def _send_verification_email_or_raise(
    *,
    user: User,
    code: str,
    masked_email: str,
    send_purpose: str,
    attempt_log: str,
    sent_log: str,
    failed_log: str,
    error_message: str,
) -> None:
    logger.info(attempt_log, extra={"email": masked_email})
    try:
        message_id = send_auth_email(
            recipient_email=user.email,
            subject="Verify your Measurable email",
            html_body=build_auth_email_html(
                full_name=user.full_name,
                code=code,
                purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
            ),
            text_body=build_auth_email_text(
                full_name=user.full_name,
                code=code,
                purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
            ),
            purpose=send_purpose,
        )
    except HTTPException as exc:
        logger.warning(
            failed_log,
            extra={"reason": _safe_register_email_failure_reason(exc)},
        )
        raise http_error(
            503,
            "email_delivery_failed",
            error_message,
        ) from exc
    logger.info(sent_log, extra={"message_id": message_id})


def _decimal_to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _create_referral_partner(
    db: Session,
    *,
    name: str,
    code: str,
    partner_type: str | None,
    commission_type: str | None,
    commission_value: float | None,
    status: str,
) -> ReferralPartner:
    normalized_code = str(code or "").strip()
    if not normalized_code:
        raise http_error(400, "invalid_referral_code", "code is required.")
    existing = (
        db.query(ReferralPartner)
        .filter(func.lower(ReferralPartner.code) == normalized_code.lower())
        .first()
    )
    if existing is not None:
        raise http_error(409, "referral_partner_exists", "Referral partner code already exists.")
    partner = ReferralPartner(
        name=str(name or "").strip() or normalized_code,
        code=normalized_code,
        type=str(partner_type or "").strip() or None,
        commission_type=str(commission_type or "").strip() or None,
        commission_value=commission_value,
        status=str(status or "active").strip() or "active",
    )
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return partner


def _build_referral_summary_rows(db: Session) -> list[ReferralSummaryOut]:
    partner_by_code = {
        str(partner.code): partner
        for partner in db.query(ReferralPartner).order_by(ReferralPartner.code.asc()).all()
    }

    summary_by_code: dict[str | None, dict[str, Any]] = {}

    def ensure_row(referral_code: str | None) -> dict[str, Any]:
        key = str(referral_code).strip() if referral_code is not None else None
        if key == "":
            key = None
        if key not in summary_by_code:
            partner = partner_by_code.get(key) if key is not None else None
            summary_by_code[key] = {
                "referral_code": key,
                "partner_name": partner.name if partner is not None else None,
                "clicks": 0,
                "signups": 0,
                "first_reports": 0,
                "paid_conversions": 0,
                "revenue": 0.0,
                "estimated_commission": 0.0,
            }
        return summary_by_code[key]

    for partner_code in partner_by_code:
        ensure_row(partner_code)

    click_rows = (
        db.query(
            ReferralClick.referral_code,
            func.count(ReferralClick.id),
        )
        .group_by(ReferralClick.referral_code)
        .all()
    )
    for referral_code, clicks in click_rows:
        row = ensure_row(referral_code)
        row["clicks"] = int(clicks or 0)

    conversion_rows = (
        db.query(
            ReferralConversion.referral_code,
            ReferralConversion.conversion_type,
            func.count(ReferralConversion.id),
            func.coalesce(func.sum(ReferralConversion.amount), 0),
            func.coalesce(func.sum(ReferralConversion.commission_amount), 0),
        )
        .group_by(
            ReferralConversion.referral_code,
            ReferralConversion.conversion_type,
        )
        .all()
    )
    for referral_code, conversion_type, count_value, revenue_value, commission_value in conversion_rows:
        row = ensure_row(referral_code)
        normalized_type = str(conversion_type or "").strip()
        if normalized_type == "signup":
            row["signups"] += int(count_value or 0)
        elif normalized_type == "first_report":
            row["first_reports"] += int(count_value or 0)
        if normalized_type in {"paid_subscription", "upgrade", "renewal"}:
            row["paid_conversions"] += int(count_value or 0)
            row["revenue"] += _decimal_to_float(revenue_value)
            row["estimated_commission"] += _decimal_to_float(commission_value)

    rows = [
        ReferralSummaryOut(
            referral_code=summary["referral_code"],
            partner_name=summary["partner_name"],
            clicks=int(summary["clicks"]),
            signups=int(summary["signups"]),
            first_reports=int(summary["first_reports"]),
            paid_conversions=int(summary["paid_conversions"]),
            revenue=round(float(summary["revenue"]), 2),
            estimated_commission=round(float(summary["estimated_commission"]), 2),
        )
        for summary in summary_by_code.values()
    ]
    rows.sort(key=lambda item: ((item.referral_code or "~").lower(), item.partner_name or ""))
    return rows


def _jwt_secret_configured() -> bool:
    return bool((settings.jwt_secret or "").strip())


@lru_cache(maxsize=1)
def _table_names() -> set[str]:
    try:
        return set(inspect(engine).get_table_names())
    except SQLAlchemyError:
        return set()


def _table_available(table_name: str) -> bool:
    return table_name in _table_names()


def _safe_int_scalar(db: Session, query, *, default: int = 0) -> int:
    try:
        value = query.scalar()
    except SQLAlchemyError:
        return default
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def _previous_month_bounds(now: datetime) -> tuple[date, date]:
    current_first = now.date().replace(day=1)
    previous_last = current_first - timedelta(days=1)
    previous_first = previous_last.replace(day=1)
    return previous_first, previous_last


def _resolve_admin_metrics_timeframe(
    timeframe: str | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    selected = str(timeframe or "all").strip().lower() or "all"
    if selected not in {"all", "this_month", "last_month", "custom"}:
        logger.warning(
            "ADMIN_METRICS_INVALID_TIMEFRAME",
            extra={"timeframe": selected},
        )
        raise http_error(
            400,
            "invalid_timeframe",
            'Invalid timeframe. Use "all", "this_month", "last_month", or "custom".',
        )

    if selected == "all":
        return {
            "timeframe": "all",
            "start_date": None,
            "end_date": None,
            "start_dt": None,
            "end_dt": None,
        }

    if selected == "this_month":
        start = now.date().replace(day=1)
        end = now.date()
    elif selected == "last_month":
        start, end = _previous_month_bounds(now)
    else:
        if start_date is None or end_date is None:
            logger.warning(
                "ADMIN_METRICS_CUSTOM_DATE_MISSING",
                extra={"timeframe": selected, "start_date": start_date, "end_date": end_date},
            )
            raise http_error(
                400,
                "missing_timeframe_dates",
                "start_date and end_date are required when timeframe=custom.",
            )
        if start_date > end_date:
            logger.warning(
                "ADMIN_METRICS_CUSTOM_DATE_ORDER_INVALID",
                extra={"timeframe": selected, "start_date": start_date, "end_date": end_date},
            )
            raise http_error(
                400,
                "invalid_timeframe_dates",
                "start_date must be on or before end_date.",
            )
        start, end = start_date, end_date

    return {
        "timeframe": selected,
        "start_date": start,
        "end_date": end,
        "start_dt": _day_start(start),
        "end_dt": _day_end(end),
    }


def _count_users_in_range(
    db: Session,
    *,
    start_dt: datetime | None,
    end_dt: datetime | None,
    column,
    extra_filters: list[Any] | None = None,
) -> int:
    query = db.query(func.count(User.id)).filter(User.is_deleted.is_(False))
    if start_dt is not None:
        query = query.filter(column >= start_dt)
    if end_dt is not None:
        query = query.filter(column <= end_dt)
    for extra_filter in extra_filters or []:
        query = query.filter(extra_filter)
    return _safe_int_scalar(db, query)


def _count_rows_in_range(
    db: Session,
    *,
    model,
    column,
    start_dt: datetime | None,
    end_dt: datetime | None,
    extra_filters: list[Any] | None = None,
) -> int:
    query = db.query(func.count(model.id))
    if start_dt is not None:
        query = query.filter(column >= start_dt)
    if end_dt is not None:
        query = query.filter(column <= end_dt)
    for extra_filter in extra_filters or []:
        query = query.filter(extra_filter)
    return _safe_int_scalar(db, query)


def _date_range(start_date: date, end_date: date) -> list[date]:
    if start_date > end_date:
        return []
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _previous_equivalent_range(start_date: date, end_date: date) -> tuple[date | None, date | None]:
    if start_date > end_date:
        return None, None
    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    return previous_start, previous_end


def _percent_change(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100.0, 2)


def _daily_counts(
    db: Session,
    *,
    model,
    column,
    start_dt: datetime,
    end_dt: datetime,
    extra_filters: list[Any] | None = None,
) -> dict[date, int]:
    query = db.query(column)
    query = query.filter(column >= start_dt).filter(column <= end_dt)
    for extra_filter in extra_filters or []:
        query = query.filter(extra_filter)
    counts: dict[date, int] = {}
    for (value,) in query.all():
        if not value:
            continue
        day = value.date()
        counts[day] = counts.get(day, 0) + 1
    return counts


def _build_daily_points(
    counts: dict[date, int],
    dates: list[date],
    *,
    value_key: str,
) -> list[dict[str, Any]]:
    return [{"date": current_date, value_key: counts.get(current_date, 0)} for current_date in dates]


def _build_admin_metric_insights(
    *,
    timeframe: str,
    users_in_period: int,
    previous_users_in_period: int | None,
    reports_in_period: int,
    previous_reports_in_period: int | None,
    active_users_in_period: int,
    previous_active_users_in_period: int | None,
    onboarding_completion_rate: float,
    paid_users_in_scope: int | None,
    previous_paid_users_in_scope: int | None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []

    def _append_unique(insight_type: str, message: str, severity: str) -> None:
        if any(item["type"] == insight_type for item in insights):
            return
        insights.append({"type": insight_type, "message": message, "severity": severity})

    if timeframe != "all" and previous_users_in_period is not None:
        if users_in_period > previous_users_in_period:
            _append_unique(
                "growth",
                "User growth increased in the selected period.",
                "positive",
            )
        elif users_in_period < previous_users_in_period:
            _append_unique(
                "growth",
                "User growth slowed compared to the previous period.",
                "neutral",
            )
        else:
            _append_unique(
                "growth",
                "User growth stayed flat compared to the previous period.",
                "neutral",
            )

    if users_in_period > 0:
        _append_unique(
            "growth",
            "New users were added in this timeframe.",
            "positive",
        )

    if onboarding_completion_rate < 10:
        _append_unique(
            "onboarding",
            "Onboarding completion is low. Review the onboarding flow to reduce activation drop-off.",
            "critical",
        )
    elif onboarding_completion_rate < 30:
        _append_unique(
            "onboarding",
            "Onboarding completion is low. Review the onboarding flow to reduce activation drop-off.",
            "warning",
        )
    else:
        _append_unique(
            "onboarding",
            "Onboarding completion is healthy.",
            "positive",
        )

    if reports_in_period >= 5 and onboarding_completion_rate < 30:
        _append_unique(
            "activation",
            "Report activity exists, but most users have not completed onboarding.",
            "warning",
        )

    if paid_users_in_scope is not None:
        if paid_users_in_scope == 0:
            _append_unique(
                "monetization",
                "Users are signing up, but no paid users are active yet.",
                "neutral",
            )
        elif previous_paid_users_in_scope is not None:
            if paid_users_in_scope > previous_paid_users_in_scope:
                _append_unique(
                    "monetization",
                    "You already have paid users. Start tracking conversion and MRR growth.",
                    "positive",
                )
            elif paid_users_in_scope < previous_paid_users_in_scope:
                _append_unique(
                    "monetization",
                    "Paid users slowed compared to the previous period.",
                    "neutral",
                )
        else:
            _append_unique(
                "monetization",
                "You already have paid users. Start tracking conversion and MRR growth.",
                "positive",
            )

    return insights[:5]


def _user_onboarding_started(user: User) -> bool:
    onboarding_started_value = getattr(user, "onboarding_started", None)
    if onboarding_started_value is not None:
        return bool(onboarding_started_value)
    return bool(
        user.onboarding_completed
        or (user.user_type and str(user.user_type).strip())
        or _user_list_value(user.goals)
        or _user_list_value(user.platforms)
    )


def _report_dates_by_user(
    db: Session,
    *,
    user_ids: list[int],
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> dict[int, set[date]]:
    if not user_ids or not _table_available("reports"):
        return {}
    query = (
        db.query(WorkspaceMember.user_id, Report.created_at)
        .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
        .filter(WorkspaceMember.user_id.in_(user_ids))
    )
    if start_dt is not None:
        query = query.filter(Report.created_at >= start_dt)
    if end_dt is not None:
        query = query.filter(Report.created_at <= end_dt)
    report_dates_by_user: dict[int, set[date]] = {}
    for user_id, created_at in query.all():
        if user_id is None or created_at is None:
            continue
        report_dates_by_user.setdefault(int(user_id), set()).add(created_at.date())
    return report_dates_by_user


def _admin_reports_count_subquery(db: Session):
    if not _table_available("reports"):
        return None
    return (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            func.count(func.distinct(Report.id)).label("reports_count"),
        )
        .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
        .group_by(WorkspaceMember.user_id)
        .subquery()
    )


def _admin_reports_activity_subquery(db: Session):
    if not _table_available("reports"):
        return None
    recent_cutoff_7 = datetime.now(timezone.utc) - timedelta(days=7)
    return (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            func.max(Report.created_at).label("last_report_created_at"),
            func.sum(
                case(
                    (Report.created_at >= recent_cutoff_7, 1),
                    else_=0,
                )
            ).label("reports_last_7_days"),
        )
        .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
        .group_by(WorkspaceMember.user_id)
        .subquery()
    )


def _admin_ai_usage_subquery(db: Session):
    if not (_table_available("conversations") and _table_available("messages")):
        return None
    return (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            func.count(func.distinct(Message.id)).label("ai_messages_count"),
        )
        .join(Conversation, Conversation.workspace_id == WorkspaceMember.workspace_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .group_by(WorkspaceMember.user_id)
        .subquery()
    )


def _admin_integrations_count_subquery(db: Session):
    if not _table_available("integrations"):
        return None
    return (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            func.count(func.distinct(Integration.id)).label("integrations_count"),
        )
        .join(Integration, Integration.workspace_id == WorkspaceMember.workspace_id)
        .group_by(WorkspaceMember.user_id)
        .subquery()
    )


def _admin_latest_plan_subquery(db: Session):
    if not _table_available("subscriptions"):
        return None
    latest_subscription = (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            func.max(Subscription.created_at).label("latest_created_at"),
        )
        .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
        .filter(Subscription.status == "active")
        .group_by(WorkspaceMember.user_id)
        .subquery()
    )
    return (
        db.query(
            WorkspaceMember.user_id.label("user_id"),
            Subscription.plan.label("plan"),
        )
        .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
        .join(
            latest_subscription,
            and_(
                latest_subscription.c.user_id == WorkspaceMember.user_id,
                latest_subscription.c.latest_created_at == Subscription.created_at,
            ),
        )
        .filter(Subscription.status == "active")
        .subquery()
    )


def _admin_health_score_expression(
    *,
    cutoff_7: datetime,
    reports_count_expr,
    reports_last_7_days_expr,
    integrations_count_expr,
    plan_expr,
):
    return (
        case((User.email_verified.is_(True), 20), else_=0)
        + case((User.onboarding_completed.is_(True), 20), else_=0)
        + case((reports_count_expr > 0, 20), else_=0)
        + case((reports_last_7_days_expr > 0, 15), else_=0)
        + case((User.last_login_at.is_not(None) & (User.last_login_at >= cutoff_7), 10), else_=0)
        + case((integrations_count_expr > 0, 10), else_=0)
        + case((func.lower(plan_expr) != "free", 15), else_=0)
    )


def _admin_health_status_from_score(score: int) -> str:
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "active"
    if score >= 25:
        return "at_risk"
    return "dormant"


def _admin_health_reasons(
    *,
    email_verified: bool,
    onboarding_completed: bool,
    reports_count: int,
    reports_last_7_days: int,
    last_login_at: datetime | None,
    integrations_count: int,
    plan_value: str | None,
) -> list[str]:
    cutoff_7 = datetime.now(timezone.utc) - timedelta(days=7)
    last_login_aware = last_login_at
    if last_login_aware is not None and last_login_aware.tzinfo is None:
        last_login_aware = last_login_aware.replace(tzinfo=timezone.utc)
    reasons: list[str] = []
    reasons.append("Email verified" if email_verified else "Email not verified")
    reasons.append("Onboarding completed" if onboarding_completed else "Onboarding pending")
    reasons.append("Generated reports" if reports_count > 0 else "No reports yet")
    reasons.append("Recent report activity" if reports_last_7_days > 0 else "No reports in last 7 days")
    reasons.append("Recent login" if last_login_aware and last_login_aware >= cutoff_7 else "No recent login")
    reasons.append("Integrations connected" if integrations_count > 0 else "No integrations connected")
    reasons.append("Paid plan" if plan_value and plan_value.lower() != "free" else "Free plan")
    return reasons

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


# LEGACY / candidate for removal after frontend/backend contract is stable.
# Recommended source of truth for report branding is resolve_report_branding()
# via resolve_report_branding_for_workspace() in app/services.py.
def _report_branding(db: Session, report: Report) -> dict[str, object]:
    metadata = _report_metadata(report)
    branding = metadata.get("branding") if isinstance(metadata.get("branding"), dict) else None
    return resolve_report_branding_for_workspace(
        db,
        report.workspace_id,
        preferred_branding=branding,
        report_metadata=metadata,
    )


# LEGACY / candidate for removal after frontend/backend contract is stable.
# Recommended source of truth for report branding is resolve_report_branding() in app/services.py.
def _user_branding(user: User | None) -> dict[str, object]:
    if not user or not user_logo_column_available():
        return {"logo_url": None}
    return {"logo_url": str(user.logo_url) if user.logo_url else None}


# LEGACY / candidate for removal after frontend/backend contract is stable.
# Recommended source of truth for cover branding is build_5_blocks() cover payload
# backed by resolve_report_branding().
def _inject_cover_branding_payload(
    *,
    block_type: str,
    order: int,
    data: dict[str, Any],
    branding: dict[str, Any],
) -> dict[str, Any]:
    if block_type != "title":
        return data
    semantic_name = str(data.get("semantic_name") or "").strip()
    if semantic_name and semantic_name != "cover":
        return data
    updated = dict(data)
    updated["semantic_name"] = "cover"
    updated["branding"] = branding
    updated["brand_name"] = branding.get("resolved_brand_name")
    updated["brand_logo_url"] = branding.get("resolved_logo_url")
    updated["resolved_brand_name"] = branding.get("resolved_brand_name")
    updated["resolved_logo_url"] = branding.get("resolved_logo_url")
    if order == 1 and not updated.get("cover_branding"):
        updated["cover_branding"] = {
            "resolved_brand_name": branding.get("resolved_brand_name"),
            "resolved_logo_url": branding.get("resolved_logo_url"),
        }
    return updated


def _report_block_out(block: ReportBlock, branding: dict[str, Any]) -> ReportBlockOut:
    try:
        data = json.loads(block.data_json or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data = _inject_cover_branding_payload(
        block_type=str(block.type),
        order=int(block.order),
        data=data,
        branding=branding,
    )
    return ReportBlockOut(
        id=block.id,
        report_version_id=block.report_version_id,
        type=block.type,
        order=block.order,
        data_json=json.dumps(data),
        editable_fields_json=block.editable_fields_json,
        created_at=block.created_at,
        updated_at=block.updated_at,
    )


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


def _report_status(report: Report) -> str | None:
    metadata = _report_metadata(report)
    status = metadata.get("report_status") if isinstance(metadata, dict) else None
    return str(status) if isinstance(status, str) and status.strip() else None


def _canonical_report_integration_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"shopify", "shopify_store"}:
        return "shopify"
    if normalized in {"meta_ads", "meta-ad", "metaads", "meta_ads_account"}:
        return "meta_ads"
    if normalized in {"instagram", "instagram_business", "instagram_account"}:
        return "instagram"
    if normalized in {"facebook", "facebook_page", "facebook_pages", "meta_pages", "meta_pages_v1", "meta_pages_v2"}:
        return "facebook"
    if normalized in {"meta", "meta_ads"}:
        return "meta"
    if normalized in {"tiktok", "tiktok_ads", "tiktok_ads_v1"}:
        return "tiktok_ads"
    if normalized in {"csv", "csv_upload"}:
        return "csv"
    if normalized in {"upload", "file_upload", "manual_upload"}:
        return "upload"
    if normalized in {"legacy", "manual", "manual_report"}:
        return "legacy"
    return normalized or "legacy"


def _report_integration_display_name(integration_type: str) -> str:
    mapping = {
        "shopify": "Shopify",
        "instagram": "Instagram Business",
        "facebook": "Facebook Pages",
        "meta_ads": "Meta Ads",
        "meta": "Meta",
        "tiktok_ads": "TikTok Ads",
        "csv": "CSV Upload",
        "upload": "File Upload",
        "legacy": "Manual / Legacy report",
    }
    return mapping.get(integration_type, "Unknown integration")


def _report_source_handle(payload: dict[str, Any]) -> str | None:
    for key in ("source_handle", "handle", "username", "instagram_username"):
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        return value if value.startswith("@") else f"@{value}" if key in {"username", "instagram_username"} else value
    return None


def _report_integration_type_from_payload(payload: dict[str, Any]) -> Any:
    for key in (
        "integration_type",
        "integration",
        "platform",
        "channel",
        "social_network",
        "source_type",
        "report_type",
        "generation_mode",
        "source",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    if payload.get("facebook_page_id") or payload.get("facebook_pages"):
        return "facebook_pages"
    if payload.get("instagram_business_account_id") or payload.get("instagram_username"):
        return "instagram_business"
    if payload.get("shop_domain") or payload.get("top_products"):
        return "shopify"
    if payload.get("ad_account_id") or payload.get("top_campaigns"):
        return "meta_ads"
    return None


def _infer_report_integration_from_text(*values: Any) -> dict[str, str | None] | None:
    text_parts = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not text_parts:
        return None
    haystack = " ".join(text_parts).lower()
    has_facebook = any(token in haystack for token in ("facebook pages report", "facebook pages", "facebook_page", "facebook"))
    has_instagram = any(token in haystack for token in ("instagram business", "instagram report", "instagram_business", "instagram"))
    has_shopify = "shopify" in haystack
    has_meta_ads = any(token in haystack for token in ("meta ads", "paid media", "meta_ads"))
    has_tiktok = any(token in haystack for token in ("tiktok ads", "tiktok_ads", "tiktok"))
    if has_facebook and has_instagram:
        return {
            "integration_type": "meta",
            "integration_display_name": "Multi-source Report",
            "social_network": None,
            "channel": "meta",
        }
    if has_facebook:
        return {
            "integration_type": "facebook",
            "integration_display_name": "Facebook Pages",
            "social_network": "facebook",
            "channel": "facebook",
        }
    if has_instagram:
        return {
            "integration_type": "instagram",
            "integration_display_name": "Instagram Business",
            "social_network": "instagram",
            "channel": "instagram",
        }
    if has_shopify:
        return {
            "integration_type": "shopify",
            "integration_display_name": "Shopify",
            "social_network": None,
            "channel": "shopify",
        }
    if has_meta_ads:
        return {
            "integration_type": "meta_ads",
            "integration_display_name": "Meta Ads",
            "social_network": None,
            "channel": "meta_ads",
        }
    if has_tiktok:
        return {
            "integration_type": "tiktok_ads",
            "integration_display_name": "TikTok Ads",
            "social_network": "tiktok",
            "channel": "tiktok_ads",
        }
    if any(token in haystack for token in ("csv upload", ".csv", "spreadsheet", "upload")):
        return {
            "integration_type": "csv",
            "integration_display_name": "CSV Upload",
            "social_network": None,
            "channel": "csv",
        }
    return None


def derive_report_integration_metadata(
    db: Session,
    report: Report,
    dataset: Dataset | None = None,
) -> ReportIntegrationMetadataOut:
    metadata = _report_metadata(report)
    if "report_sources" in report.__dict__:
        report_sources = list(report.report_sources)
    else:
        try:
            report_sources = (
                db.query(ReportSource)
                .filter(ReportSource.report_id == report.id)
                .order_by(ReportSource.position.asc(), ReportSource.id.asc())
                .all()
            )
        except SQLAlchemyError:
            report_sources = []
    resolved_dataset = dataset or db.get(Dataset, report.dataset_id)
    dataset_data = resolved_dataset.data if resolved_dataset is not None and isinstance(resolved_dataset.data, dict) else {}

    payload_candidates: list[dict[str, Any]] = []
    if report_sources:
        primary_source = report_sources[0]
        config_json = dict(primary_source.config_json) if isinstance(primary_source.config_json, dict) else {}
        source_payload = {
            "integration_type": primary_source.source_type or primary_source.provider,
            "integration_display_name": _report_integration_display_name(
                _canonical_report_integration_type(primary_source.source_type or primary_source.provider)
            ),
            "source_name": (
                primary_source.label
                or config_json.get("account_name")
                or config_json.get("page_name")
                or (
                    primary_source.integration_account.display_name
                    if primary_source.integration_account is not None
                    else None
                )
            ),
            "source_handle": _report_source_handle(config_json),
            "social_network": config_json.get("social_network") or config_json.get("channel") or primary_source.provider,
            "channel": config_json.get("channel") or primary_source.source_type,
        }
        payload_candidates.append(source_payload)

    report_metadata_payload = metadata.get("integration_metadata") if isinstance(metadata.get("integration_metadata"), dict) else {}
    if report_metadata_payload:
        payload_candidates.append(dict(report_metadata_payload))
    payload_candidates.append(
        {
            "integration_type": metadata.get("integration_type"),
            "integration_display_name": metadata.get("integration_display_name"),
            "source_name": metadata.get("source_name") or metadata.get("integration_account_name"),
            "source_handle": metadata.get("source_handle"),
            "social_network": metadata.get("social_network"),
            "channel": metadata.get("channel"),
        }
    )
    if dataset_data:
        payload_candidates.append(
            {
                "integration_type": _report_integration_type_from_payload(dataset_data),
                "integration_display_name": dataset_data.get("integration_display_name"),
                "source_name": (
                    dataset_data.get("page_name")
                    or dataset_data.get("account_name")
                    or dataset_data.get("name")
                    or dataset_data.get("file_name")
                    or dataset_data.get("filename")
                ),
                "source_handle": _report_source_handle(dataset_data),
                "social_network": dataset_data.get("social_network"),
                "channel": dataset_data.get("channel") or dataset_data.get("integration_type"),
            }
        )
    payload_candidates.append(
        {
            "integration_type": _report_integration_type_from_payload(metadata) or _report_integration_type_from_payload(dataset_data),
            "integration_display_name": metadata.get("integration_display_name"),
            "source_name": (
                metadata.get("sourceSummary")
                or metadata.get("page_name")
                or metadata.get("account_name")
                or (
                    metadata.get("claude_payload", {}).get("page_name")
                    if isinstance(metadata.get("claude_payload"), dict)
                    else None
                )
                or dataset_data.get("page_name")
                or dataset_data.get("account_name")
            ),
            "source_handle": _report_source_handle(metadata),
            "social_network": metadata.get("social_network"),
            "channel": metadata.get("channel"),
        }
    )

    text_hint_values: list[Any] = [
        report.name,
        metadata.get("sourceSummary"),
        metadata.get("subtitle"),
        metadata.get("report_type"),
        metadata.get("integration_display_name"),
        metadata.get("integration_type"),
    ]
    if dataset_data:
        text_hint_values.extend(
            [
                dataset_data.get("subtitle"),
                dataset_data.get("sourceSummary"),
                dataset_data.get("integration_display_name"),
                dataset_data.get("integration_type"),
                dataset_data.get("platform"),
                dataset_data.get("integration"),
            ]
        )

    try:
        latest_report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
            .first()
        )
    except SQLAlchemyError:
        latest_report_version = None
    if latest_report_version is not None:
        try:
            cover_block = (
                db.query(ReportBlock)
                .filter(ReportBlock.report_version_id == latest_report_version.id)
                .order_by(ReportBlock.order.asc(), ReportBlock.id.asc())
                .first()
            )
        except SQLAlchemyError:
            cover_block = None
        if cover_block is not None and cover_block.data_json:
            try:
                cover_data = json.loads(cover_block.data_json)
            except json.JSONDecodeError:
                cover_data = {}
            if isinstance(cover_data, dict):
                text_hint_values.extend(
                    [
                        cover_data.get("subtitle"),
                        cover_data.get("title"),
                        cover_data.get("text"),
                    ]
                )

    text_inferred = _infer_report_integration_from_text(*text_hint_values)
    if text_inferred:
        payload_candidates.append(
            {
                **text_inferred,
                "source_name": (
                    metadata.get("page_name")
                    or metadata.get("sourceSummary")
                    or dataset_data.get("page_name")
                    or dataset_data.get("account_name")
                ),
            }
        )

    chosen: dict[str, Any] | None = None
    legacy_fallback: dict[str, Any] | None = None
    for candidate in payload_candidates:
        integration_type = _canonical_report_integration_type(candidate.get("integration_type"))
        has_context = any(candidate.get(key) for key in ("source_name", "source_handle", "channel"))
        if integration_type != "legacy":
            chosen = dict(candidate)
            chosen["integration_type"] = integration_type
            break
        if has_context and legacy_fallback is None:
            legacy_fallback = dict(candidate)
            legacy_fallback["integration_type"] = integration_type

    if chosen is None:
        chosen = legacy_fallback or {"integration_type": "legacy"}

    integration_type = _canonical_report_integration_type(chosen.get("integration_type"))
    if integration_type == "meta":
        channel = str(chosen.get("channel") or "").strip().lower()
        if channel in {"facebook", "facebook_pages", "meta_pages"}:
            integration_type = "facebook"
        elif channel in {"instagram", "instagram_business"}:
            integration_type = "instagram"

    source_name = str(chosen.get("source_name") or "").strip() or None
    if source_name is None and integration_type == "legacy":
        source_name = "Unknown source"

    source_handle = _report_source_handle(chosen)
    social_network = str(chosen.get("social_network") or "").strip().lower() or None
    if social_network in {"facebook_pages", "meta_pages"}:
        social_network = "facebook"
    if social_network == "instagram_business":
        social_network = "instagram"

    channel = str(chosen.get("channel") or "").strip().lower() or None
    canonical_channel = _canonical_report_integration_type(channel or integration_type)
    if canonical_channel == "legacy":
        canonical_channel = None if integration_type == "legacy" else integration_type

    return ReportIntegrationMetadataOut(
        integration_type=integration_type,
        integration_display_name=(
            str(chosen.get("integration_display_name") or "").strip()
            or _report_integration_display_name(integration_type)
        ),
        source_name=source_name,
        source_handle=source_handle,
        social_network=social_network or (integration_type if integration_type in {"facebook", "instagram"} else None),
        channel=canonical_channel,
    )


def _report_sources_out(db: Session, *, report_id: int) -> list[ReportSourceRead]:
    report_sources = (
        db.query(ReportSource)
        .filter(ReportSource.report_id == report_id)
        .order_by(ReportSource.position.asc(), ReportSource.id.asc())
        .all()
    )
    return [ReportSourceRead.model_validate(source) for source in report_sources]


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
    report_branding = _report_branding(db, report)
    metadata = _report_metadata(report)
    logger.info(
        "[MetaTimeframeBackend][render.full]",
        extra=_timeframe_log_payload(
            report,
            source="report_version_api",
            version_id=report_version.id,
        ),
    )
    integration_metadata = derive_report_integration_metadata(db, report)
    return ReportVersionOut(
        id=report_version.id,
        version_id=report_version.id,
        report_id=report_version.report_id,
        version=report_version.version,
        folder_id=report.folder_id,
        folder_name=report.folder_name,
        report_sources=_report_sources_out(db, report_id=report.id),
        integration_metadata=integration_metadata,
        description=metadata,
        timeframe=_report_timeframe(report),
        locale=_report_locale(report),
        branding=report_branding,
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report_version.created_at,
        updated_at=report_version.updated_at,
        blocks=[_report_block_out(block, report_branding) for block in blocks],
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


def _sync_workspace_branding_to_reports(
    db: Session,
    *,
    workspace_id: int,
    resolved_branding: dict[str, Any],
) -> int:
    reports = (
        db.query(Report)
        .filter(Report.workspace_id == workspace_id)
        .order_by(Report.id.asc())
        .all()
    )
    updated_count = 0
    for report in reports:
        metadata = _report_metadata(report)
        metadata["branding"] = dict(resolved_branding)
        metadata.pop("thumbnail_s3_key", None)
        metadata.pop("thumbnail_generated_at", None)
        report.description = json.dumps(metadata)
        db.add(report)
        updated_count += 1
    if updated_count:
        db.commit()
    return updated_count


def _generate_and_store_report_thumbnail(
    *,
    db: Session,
    report: Report,
    report_version: ReportVersion,
    user_id: int,
    sync_branding_from_user: bool = False,
) -> str | None:
    report_metadata = _report_metadata(report)
    persisted_branding = (
        report_metadata.get("branding")
        if isinstance(report_metadata, dict) and isinstance(report_metadata.get("branding"), dict)
        else None
    )
    report_branding = _report_branding(db, report)
    report_logo_url = str(report_branding.get("logo_url")) if report_branding.get("logo_url") else None
    report_version_logo_url = report_logo_url
    user = db.get(User, user_id)
    user_logo_url = str(user.logo_url) if user and user.logo_url else None
    final_branding_source = "report_metadata"
    workspace_branding = resolve_workspace_branding(report.workspace_id)
    workspace_logo_url = str(workspace_branding.get("logo_url")) if workspace_branding.get("logo_url") else None
    custom_branding_allowed = bool(
        get_plan_limits(get_workspace_plan(db, report.workspace_id)).get("allow_custom_branding")
    )

    if sync_branding_from_user and custom_branding_allowed and user_logo_url and not persisted_branding:
        synced_branding = resolve_report_branding_for_workspace(
            db,
            report.workspace_id,
            preferred_branding=_user_branding(user),
        )
        report = _update_report_metadata(db, report, {"branding": synced_branding})
        report_branding = _report_branding(db, report)
        report_logo_url = (
            str(report_branding.get("logo_url")) if report_branding.get("logo_url") else None
        )
        report_version_logo_url = report_logo_url
        final_branding_source = "user_profile_sync"
    elif not custom_branding_allowed:
        final_branding_source = "measurable_default"
    elif persisted_branding:
        final_branding_source = "report_metadata"
    elif workspace_logo_url:
        final_branding_source = "workspace_fallback"
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
            "resolved_workspace_logo_url": workspace_logo_url,
            "final_branding_source_used": final_branding_source,
            "report_version": report_version.version,
        },
    )
    try:
        screenshot_bytes, thumbnail_debug = generate_thumbnail_from_export_page(
            export_url=export_url,
            report_id=report.id,
            auth_token=export_token,
        )
    except HTTPException:
        logger.exception(
            "Report thumbnail generation failed but report creation will continue",
            extra={
                "report_id": report.id,
                "report_version": report_version.version,
                "export_url": export_url,
            },
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected report thumbnail generation failure but report creation will continue",
            extra={
                "report_id": report.id,
                "report_version": report_version.version,
                "export_url": export_url,
            },
        )
        return None
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
            "resolved_workspace_logo_url": workspace_logo_url,
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
META_PAGES_ORGANIC_IMPRESSIONS_METRIC_CANDIDATES = [
    "page_posts_impressions_organic",
]
META_PAGES_IMPRESSIONS_METRIC_CANDIDATES = META_PAGES_ORGANIC_IMPRESSIONS_METRIC_CANDIDATES

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
    if isinstance(value, BaseException):
        return str(value)
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
            "detail": _make_json_safe(exc.errors()),
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


GOOGLE_OAUTH_STATE_PURPOSE = "google_oauth"
GOOGLE_OAUTH_SCOPE = "openid email profile"


def _google_client_id() -> str:
    client_id = str(settings.google_client_id or "").strip()
    if not client_id:
        raise http_error(503, "google_oauth_config_missing", "Google OAuth is not configured.")
    return client_id


def _google_client_secret() -> str:
    client_secret = str(settings.google_client_secret or "").strip()
    if not client_secret:
        raise http_error(503, "google_oauth_config_missing", "Google OAuth is not configured.")
    return client_secret


def _google_redirect_uri() -> str:
    redirect_uri = str(settings.google_redirect_uri or "").strip()
    if not redirect_uri:
        raise http_error(503, "google_oauth_config_missing", "Google OAuth is not configured.")
    return redirect_uri


def _google_frontend_base_url() -> str:
    configured_base = str(settings.frontend_base_url or settings.report_export_base_url or "").strip()
    if not configured_base:
        raise http_error(503, "google_oauth_config_missing", "Google OAuth is not configured.")
    return configured_base.rstrip("/")


def _auth_cookie_secure() -> bool:
    configured_base = str(settings.frontend_base_url or settings.report_export_base_url or "").strip()
    return configured_base.startswith("https://")


def _build_google_oauth_url(state: str) -> str:
    params = {
        "client_id": _google_client_id(),
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_OAUTH_SCOPE,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def _exchange_google_code_for_tokens(code: str) -> dict[str, Any]:
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": _google_client_id(),
            "client_secret": _google_client_secret(),
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if response.status_code != 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text}
        raise http_error(400, "google_token_exchange_failed", "Google OAuth token exchange failed.")
    return response.json()


def _verify_google_id_token(id_token_value: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        payload = google_id_token.verify_oauth2_token(
            id_token_value,
            google_requests.Request(),
            _google_client_id(),
        )
    except Exception as exc:
        raise http_error(400, "google_id_token_invalid", "Google ID token is invalid.") from exc
    if not isinstance(payload, dict):
        raise http_error(400, "google_id_token_invalid", "Google ID token is invalid.")
    return payload


def _google_auth_redirect_response(access_token: str) -> RedirectResponse:
    target_url = f"{_google_frontend_base_url()}/login#{urlencode({'access_token': access_token, 'token_type': 'bearer'})}"
    response = RedirectResponse(url=target_url, status_code=302)
    _set_auth_cookie(response, access_token)
    return response


def _find_user_workspace_and_subscription(
    db: Session,
    *,
    user_id: int,
) -> tuple[Workspace | None, Subscription | None]:
    workspace = (
        db.query(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at.asc(), Workspace.id.asc())
        .first()
    )
    subscription = (
        db.query(Subscription)
        .filter(Subscription.workspace_id == workspace.id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
        if workspace is not None
        else None
    )
    return workspace, subscription


def _ensure_user_workspace_and_subscription(
    db: Session,
    *,
    user: User,
) -> tuple[Workspace, Subscription]:
    workspace, subscription = _find_user_workspace_and_subscription(db, user_id=user.id)
    if workspace is None:
        workspace = Workspace(name=build_default_workspace_name(user.full_name))
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    if subscription is None:
        subscription = Subscription(
            workspace_id=workspace.id,
            plan="free",
            status="active",
            billing_status="free",
        )
        apply_plan_entitlements(subscription, "free")
        db.add(subscription)
        db.flush()
    else:
        apply_plan_entitlements(subscription, subscription.plan or "free")
    return workspace, subscription


def _set_auth_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=_auth_cookie_secure(),
        samesite="lax",
        path="/",
        max_age=3600,
    )


def _clear_access_token_cookie(response: Response) -> Response:
    secure_cookie = _auth_cookie_secure()
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    return response


@app.post("/auth/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    _clear_access_token_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "auth_logout_completed",
        extra={
            "cookie_cleared": True,
            "cookie_secure": _auth_cookie_secure(),
            "cookie_path": "/",
            "cookie_same_site": "lax",
        },
    )
    return response


@app.post("/referrals/click", response_model=ReferralClickOut, status_code=201)
def create_public_referral_click(
    payload: ReferralClickIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ReferralClickOut:
    client_ip = request.client.host if request.client is not None else None
    click = create_referral_click(
        db,
        referral_code=payload.referral_code,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
        utm_term=payload.utm_term,
        utm_content=payload.utm_content,
        landing_page=payload.landing_page,
        ip_hash=hash_client_ip(client_ip),
        user_agent=request.headers.get("user-agent"),
    )
    return ReferralClickOut(
        id=click.id,
        referral_code=click.referral_code,
        created_at=click.created_at,
    )


@app.delete("/account/delete", response_model=DeleteAccountOut)
def delete_account(
    payload: DeleteAccountIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteAccountOut:
    confirmation = str(payload.confirmation or "").strip()
    if confirmation != "Eliminar":
        raise http_error(400, "invalid_confirmation", 'Type "Eliminar" to confirm account deletion.')

    allowed_reasons = {
        "too_expensive",
        "missing_features",
        "hard_to_use",
        "no_longer_needed",
        "switching_tool",
        "privacy_concerns",
        "other",
    }
    reason = str(payload.reason).strip() if payload.reason is not None and str(payload.reason).strip() else None
    if reason is not None and reason not in allowed_reasons:
        raise http_error(400, "invalid_reason", "Please select a valid reason.")

    original_email = current_user.email
    current_time = datetime.now(timezone.utc)

    feedback = AccountDeletionFeedback(
        user_id=current_user.id,
        email=original_email,
        reason=reason,
        details=str(payload.details).strip() if payload.details is not None and str(payload.details).strip() else None,
    )
    db.add(feedback)

    db.query(EmailVerificationCode).filter(EmailVerificationCode.user_id == current_user.id).delete(
        synchronize_session=False
    )
    db.query(WorkspaceMember).filter(WorkspaceMember.user_id == current_user.id).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.user_id == current_user.id).update(
        {AuditLog.user_id: None}, synchronize_session=False
    )
    db.query(MetaPage).filter(MetaPage.user_id == current_user.id).update(
        {MetaPage.user_id: None}, synchronize_session=False
    )

    current_user.email = f"deleted_{current_user.id}@deleted.measurable.local"
    current_user.password_hash = hash_password(secrets.token_urlsafe(32))
    current_user.full_name = None
    current_user.logo_url = None
    current_user.email_verified = False
    current_user.auth_provider = "deleted"
    current_user.google_sub = None
    current_user.facebook_sub = None
    current_user.onboarding_completed = False
    current_user.user_type = None
    current_user.goals = []
    current_user.platforms = []
    current_user.last_login_at = None
    current_user.is_active = False
    current_user.is_deleted = True
    current_user.deleted_at = current_time
    db.add(current_user)
    db.commit()
    logger.info(
        "account_deleted",
        extra={
            "user_id": current_user.id,
            "email": original_email,
            "reason": reason,
        },
    )

    response = JSONResponse({"ok": True})
    _clear_access_token_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


def _is_deleted_user(user: User | None) -> bool:
    return bool(user and (getattr(user, "is_deleted", False) or not getattr(user, "is_active", False)))


@app.post("/auth/register", response_model=RegisterOut, status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> RegisterOut:
    email = payload.email.strip()
    masked_email = _mask_email(email)
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
        existing_user = load_user_by_email(db, email)
        if existing_user and existing_user.email_verified:
            db.rollback()
            return RegisterOut(
                message="If the email can be registered, verification instructions will be sent.",
                verification_required=True,
            )

        if existing_user is None:
            user, workspace, subscription = register_user_with_default_workspace(
                db,
                email=email,
                password_hash=hash_password(payload.password),
                full_name=full_name,
                email_verified=False,
                auth_provider="email",
                last_login_at=None,
            )
            record_signup_attribution(
                db,
                user=user,
                referral_code=payload.referral_code,
                utm_source=payload.utm_source,
                utm_medium=payload.utm_medium,
                utm_campaign=payload.utm_campaign,
                utm_term=payload.utm_term,
                utm_content=payload.utm_content,
            )
        else:
            user = existing_user
            user.password_hash = hash_password(payload.password)
            user.full_name = full_name or user.full_name
            user.email_verified = False
            user.auth_provider = "email"
            db.add(user)
            workspace = (
                db.query(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .filter(WorkspaceMember.user_id == user.id)
                .order_by(Workspace.created_at.asc(), Workspace.id.asc())
                .first()
            )
            subscription = (
                db.query(Subscription)
                .filter(Subscription.workspace_id == workspace.id)
                .order_by(Subscription.created_at.desc(), Subscription.id.desc())
                .first()
                if workspace is not None
                else None
            )
            record_signup_attribution(
                db,
                user=user,
                referral_code=payload.referral_code,
                utm_source=payload.utm_source,
                utm_medium=payload.utm_medium,
                utm_campaign=payload.utm_campaign,
                utm_term=payload.utm_term,
                utm_content=payload.utm_content,
            )

        verification_code = issue_auth_code(
            db,
            user=user,
            purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
        )
        _send_verification_email_or_raise(
            user=user,
            code=verification_code,
            masked_email=masked_email,
            send_purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
            attempt_log="REGISTER_EMAIL_ATTEMPT",
            sent_log="REGISTER_EMAIL_SENT",
            failed_log="REGISTER_EMAIL_FAILED",
            error_message="We could not send your verification email. Please try again in a moment.",
        )
        db.commit()
        logger.info(
            "auth_register_completed",
            extra={
                "user_id": user.id,
                "workspace_id": workspace.id if workspace else None,
                "has_existing_user": existing_user is not None,
            },
        )
        _track_meta_event(
            event_name="CompleteRegistration",
            user=user,
            request=request,
            event_source_url=_tracking_event_source_url(request, "/register"),
            custom_data={
                "status": "verification_required",
                "workspace_id": workspace.id if workspace else None,
            },
        )
        return RegisterOut(
            message="If the email can be registered, verification instructions will be sent.",
            verification_required=True,
            user_id=user.id if existing_user is None else None,
            email=user.email if existing_user is None else None,
            workspace_id=workspace.id if existing_user is None else None,
            plan=subscription.plan if existing_user is None and subscription else None,
        )
    except HTTPException as exc:
        db.rollback()
        raise
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
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenOut:
    email = form_data.username.strip()
    masked_email = _mask_email(email) if email else None
    logger.info("auth_login_start", extra={"email": masked_email})

    try:
        user = load_user_by_email(db, email)
        logger.info(
            "auth_login_db_lookup_ok",
            extra={"email": masked_email, "user_found": bool(user)},
        )
        if not user or _is_deleted_user(user) or not user.email_verified:
            logger.info("auth_login_user_not_found", extra={"email": masked_email})
            raise http_error(401, "invalid_credentials", "Invalid email or password.")

        try:
            password_ok = verify_password(form_data.password, user.password_hash)
        except Exception as exc:
            logger.exception(
                "auth_login_error",
                extra={
                    "email": masked_email,
                    "exception_class": exc.__class__.__name__,
                    "safe_message": _safe_exception_message(exc),
                    "stage": "password_verify",
                },
            )
            raise http_error(500, "invalid_configuration", "Authentication configuration is invalid.")

        if not password_ok:
            logger.info(
                "auth_login_password_verify_failed",
                extra={"email": masked_email, "user_id": user.id},
            )
            raise http_error(401, "invalid_credentials", "Invalid email or password.")

        if not _jwt_secret_configured():
            logger.error(
                "auth_login_error",
                extra={
                    "email": masked_email,
                    "exception_class": "InvalidConfiguration",
                    "safe_message": "JWT secret is missing.",
                    "stage": "token_create",
                },
            )
            raise http_error(500, "invalid_configuration", "Authentication configuration is invalid.")

        user.last_login_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id))
        logger.info("auth_login_token_created", extra={"email": masked_email, "user_id": user.id})
        _track_meta_event(
            event_name="Login",
            user=user,
            request=request,
            event_source_url=_tracking_event_source_url(request, "/login"),
            custom_data={"auth_provider": user.auth_provider},
        )
        return TokenOut(access_token=token)
    except HTTPException:
        db.rollback()
        raise
    except (OperationalError, ProgrammingError, SQLAlchemyError) as exc:
        db.rollback()
        logger.exception(
            "auth_login_error",
            extra={
                **_sqlalchemy_error_log_payload(exc, stage="login"),
                "email": masked_email,
                "exception_class": exc.__class__.__name__,
                "safe_message": "Database unavailable during login.",
            },
        )
        raise http_error(500, "db_unavailable", "Database temporarily unavailable.")
    except Exception as exc:
        db.rollback()
        logger.exception(
            "auth_login_error",
            extra={
                "email": email,
                "exception_class": exc.__class__.__name__,
                "safe_message": _safe_exception_message(exc),
                "stage": "unexpected",
            },
        )
        raise http_error(500, "internal_error", "Internal server error.")


@app.get("/auth/google/start")
def google_start() -> RedirectResponse:
    state = create_oauth_state(purpose=GOOGLE_OAUTH_STATE_PURPOSE)
    url = _build_google_oauth_url(state)
    logger.info(
        "google_oauth_start",
        extra={
            "scope": GOOGLE_OAUTH_SCOPE,
            "redirect_uri": _google_redirect_uri(),
        },
    )
    return RedirectResponse(url=url, status_code=302)


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    email: str | None = None
    try:
        logger.info(
            "google_oauth_callback_received",
            extra={"code_received": bool(code), "state_received": bool(state)},
        )
        if not code or not state:
            raise http_error(400, "invalid_state", "Invalid Google OAuth state.")

        state_payload = decode_oauth_state(state)
        if str(state_payload.get("purpose") or "") != GOOGLE_OAUTH_STATE_PURPOSE:
            raise http_error(400, "invalid_state", "Invalid Google OAuth state.")

        token_payload = _exchange_google_code_for_tokens(code)
        id_token_value = str(token_payload.get("id_token") or "").strip()
        if not id_token_value:
            raise http_error(400, "google_id_token_missing", "Google ID token was not returned.")

        google_profile = _verify_google_id_token(id_token_value)
        email = str(google_profile.get("email") or "").strip().lower()
        full_name = str(google_profile.get("name") or "").strip() or None
        google_sub = str(google_profile.get("sub") or "").strip()
        picture = str(google_profile.get("picture") or "").strip() or None
        email_verified = bool(google_profile.get("email_verified"))

        if not email or not google_sub:
            raise http_error(400, "google_profile_invalid", "Google profile is incomplete.")
        if not email_verified:
            raise http_error(400, "google_email_unverified", "Google email is not verified.")

        user = load_user_by_google_sub(db, google_sub)
        if user is None:
            user = load_user_by_email(db, email)
        logger.info(
            "google_oauth_user_loaded",
            extra={
                "email": email,
                "user_found": bool(user),
                "matched_by_google_sub": bool(user and getattr(user, "google_sub", None) == google_sub),
            },
        )
        if _is_deleted_user(user):
            raise http_error(403, "account_unavailable", "User account is unavailable.")
        is_new_user = user is None
        if user is None:
            user, workspace, subscription = register_user_with_default_workspace(
                db,
                email=email,
                password_hash=hash_password(secrets.token_urlsafe(32)),
                full_name=full_name,
                email_verified=True,
                auth_provider="google",
                google_sub=google_sub,
                last_login_at=datetime.now(timezone.utc),
            )
            if picture and not user.logo_url:
                user.logo_url = picture
                db.add(user)
        else:
            user.google_sub = google_sub
            if email_verified:
                user.email_verified = True
            if user.auth_provider in {"email", "google"}:
                user.auth_provider = "google"
            if full_name:
                user.full_name = full_name
            if picture and not user.logo_url:
                user.logo_url = picture
            user.last_login_at = datetime.now(timezone.utc)
            db.add(user)
            workspace, subscription = _ensure_user_workspace_and_subscription(db, user=user)
        logger.info(
            "google_oauth_user_created_or_loaded",
            extra={
                "user_id": user.id,
                "is_new_user": is_new_user,
                "auth_provider": user.auth_provider,
                "workspace_id": workspace.id if workspace else None,
                "has_subscription": bool(subscription),
            },
        )

        if not _jwt_secret_configured():
            raise http_error(500, "invalid_configuration", "Authentication configuration is invalid.")
        db.commit()
        access_token = create_access_token(str(user.id))
        logger.info(
            "google_oauth_token_created",
            extra={"user_id": user.id, "token_type": "bearer"},
        )
        _track_meta_event(
            event_name="Login",
            user=user,
            request=request,
            event_source_url=_tracking_event_source_url(request, "/login"),
            custom_data={"auth_provider": "google", "is_new_user": is_new_user},
        )
        response = _google_auth_redirect_response(access_token)
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            "google_oauth_redirect_success",
            extra={
                "user_id": user.id,
                "frontend_base_url": _google_frontend_base_url(),
                "redirect_path": "/login",
            },
        )
        return response
    except HTTPException:
        db.rollback()
        raise
    except TokenError:
        db.rollback()
        logger.warning(
            "google_oauth_error",
            extra={
                "email": email,
                "exception_class": "TokenError",
                "safe_message": "Invalid Google OAuth state.",
            },
        )
        raise http_error(400, "invalid_state", "Invalid Google OAuth state.")
    except Exception as exc:
        db.rollback()
        logger.exception(
            "google_oauth_error",
            extra={
                "email": email,
                "exception_class": exc.__class__.__name__,
                "safe_message": _safe_exception_message(exc),
            },
        )
        raise


@app.post("/auth/verify-email", response_model=VerifyEmailOut)
def verify_email(payload: VerifyEmailIn, db: Session = Depends(get_db)) -> JSONResponse:
    email = payload.email.strip()
    code = payload.code.strip()
    if not email or not code:
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired verification code.")

    user = load_user_by_email(db, email)
    if not user:
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired verification code.")
    if user.email_verified:
        return AuthMessageOut(message="Email already verified.")

    try:
        validate_auth_code(
            db,
            user=user,
            code=code,
            purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
        )
        user.email_verified = True
        user.last_login_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        access_token = create_access_token(str(user.id))
        response = JSONResponse(
            {
                "ok": True,
                "access_token": access_token,
                "token_type": "bearer",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "email_verified": user.email_verified,
                    "onboarding_completed": bool(getattr(user, "onboarding_completed", False)),
                },
            }
        )
        _set_auth_cookie(response, access_token)
        logger.info(
            "auth_email_verified",
            extra={
                "user_id": user.id,
                "token_length": len(access_token),
                "cookie_set": True,
            },
        )
        return response
    except HTTPException:
        db.rollback()
        raise


@app.post("/auth/resend-verification-code", response_model=AuthMessageOut)
def resend_verification_code(
    payload: ResendVerificationCodeIn,
    db: Session = Depends(get_db),
) -> AuthMessageOut:
    email = payload.email.strip()
    masked_email = _mask_email(email) if email else None
    if not email:
        return AuthMessageOut(message="If the email can be used, verification instructions will be sent.")

    user = load_user_by_email(db, email)
    if not user or user.email_verified:
        return AuthMessageOut(message="If the email can be used, verification instructions will be sent.")

    try:
        verification_code = issue_auth_code(
            db,
            user=user,
            purpose=AUTH_CODE_PURPOSE_EMAIL_VERIFICATION,
        )
        _send_verification_email_or_raise(
            user=user,
            code=verification_code,
            masked_email=masked_email or _mask_email(user.email),
            send_purpose="resend_verification",
            attempt_log="RESEND_EMAIL_ATTEMPT",
            sent_log="RESEND_EMAIL_SENT",
            failed_log="RESEND_EMAIL_FAILED",
            error_message="We could not send the verification email. Please try again.",
        )
        db.commit()
        logger.info("auth_verification_code_resent", extra={"user_id": user.id})
    except HTTPException:
        db.rollback()
        raise
    return AuthMessageOut(message="If the email can be used, verification instructions will be sent.")


@app.post("/auth/forgot-password", response_model=AuthMessageOut)
def forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)) -> AuthMessageOut:
    email = payload.email.strip()
    if not email:
        return AuthMessageOut(message="If the email is registered, password reset instructions will be sent.")

    user = load_user_by_email(db, email)
    if not user or not user.email_verified:
        return AuthMessageOut(message="If the email is registered, password reset instructions will be sent.")

    try:
        reset_code = issue_auth_code(
            db,
            user=user,
            purpose=AUTH_CODE_PURPOSE_PASSWORD_RESET,
        )
        send_auth_email(
            recipient_email=user.email,
            subject="Reset your Measurable password",
            html_body=build_auth_email_html(
                full_name=user.full_name,
                code=reset_code,
                purpose=AUTH_CODE_PURPOSE_PASSWORD_RESET,
            ),
            text_body=build_auth_email_text(
                full_name=user.full_name,
                code=reset_code,
                purpose=AUTH_CODE_PURPOSE_PASSWORD_RESET,
            ),
            purpose=AUTH_CODE_PURPOSE_PASSWORD_RESET,
        )
        db.commit()
        logger.info("auth_password_reset_code_sent", extra={"user_id": user.id})
    except HTTPException:
        db.rollback()
        raise
    return AuthMessageOut(message="If the email is registered, password reset instructions will be sent.")


@app.post("/auth/reset-password", response_model=AuthMessageOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)) -> AuthMessageOut:
    email = payload.email.strip()
    code = payload.code.strip()
    new_password = payload.new_password
    if not email or not code:
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired reset code.")
    if not new_password or len(new_password) < 8:
        raise http_error(400, "invalid_password", "Password must be at least 8 characters long.")

    user = load_user_by_email(db, email)
    if not user or not user.email_verified:
        raise http_error(400, "invalid_or_expired_code", "Invalid or expired reset code.")

    try:
        validate_auth_code(
            db,
            user=user,
            code=code,
            purpose=AUTH_CODE_PURPOSE_PASSWORD_RESET,
        )
        user.password_hash = hash_password(new_password)
        user.last_login_at = None
        db.add(user)
        db.commit()
        logger.info("auth_password_reset_completed", extra={"user_id": user.id})
        return AuthMessageOut(message="Password updated successfully.")
    except HTTPException:
        db.rollback()
        raise


def _user_list_value(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _me_out(db: Session, current_user: User) -> MeOut:
    workspace, _subscription = _find_user_workspace_and_subscription(db, user_id=current_user.id)
    workspace_id = workspace.id if workspace is not None else None
    plan_snapshot = (
        _workspace_plan_snapshot(db, workspace.id)
        if workspace is not None
        else {
            "plan": None,
            "is_free_plan": True,
            "can_use_custom_branding": False,
            "report_branding_mode": "measurable",
        }
    )
    account_display = _workspace_account_display_payload(workspace, current_user)
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        workspace_id=workspace_id,
        account_display_name=account_display["account_display_name"],
        account_display_name_effective=str(account_display["account_display_name_effective"]),
        email_verified=current_user.email_verified,
        auth_provider=current_user.auth_provider,
        is_admin=bool(getattr(current_user, "is_admin", False)),
        last_login_at=current_user.last_login_at,
        logo_url=(str(current_user.logo_url) if user_logo_column_available() and current_user.logo_url else None),
        branding=_user_branding(current_user),
        current_plan_name=str(plan_snapshot["plan"]) if plan_snapshot.get("plan") else None,
        current_plan_code=str(plan_snapshot["plan"]) if plan_snapshot.get("plan") else None,
        is_free_plan=bool(plan_snapshot["is_free_plan"]),
        can_use_custom_branding=bool(plan_snapshot["can_use_custom_branding"]),
        report_branding_mode=str(plan_snapshot["report_branding_mode"]),
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@app.get("/onboarding/me", response_model=OnboardingStateOut)
def onboarding_me(current_user: User = Depends(get_current_user)) -> OnboardingStateOut:
    if not user_onboarding_columns_available():
        raise http_error(500, "db_schema_mismatch", "Database schema is out of date. Run migrations.")
    return OnboardingStateOut(
        onboarding_completed=bool(current_user.onboarding_completed),
        user_type=str(current_user.user_type).strip() if current_user.user_type else None,
        goals=_user_list_value(current_user.goals),
        platforms=_user_list_value(current_user.platforms),
    )


@app.post("/onboarding/complete", response_model=OnboardingCompleteOut)
def complete_onboarding(
    payload: OnboardingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OnboardingCompleteOut:
    if not user_onboarding_columns_available():
        raise http_error(500, "db_schema_mismatch", "Database schema is out of date. Run migrations.")
    current_user.user_type = payload.user_type
    current_user.goals = list(payload.goals)
    current_user.platforms = list(payload.platforms)
    current_user.onboarding_completed = True
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    logger.info(
        "onboarding_completed",
        extra={
            "user_id": current_user.id,
            "user_type": current_user.user_type,
            "goals_count": len(current_user.goals or []),
            "platforms_count": len(current_user.platforms or []),
        },
    )
    return OnboardingCompleteOut(ok=True, onboarding_completed=True)


@app.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeOut:
    logger.info(
        "AUTH_ME_DEBUG",
        extra={
            "email": current_user.email,
            "is_admin": bool(getattr(current_user, "is_admin", False)),
        },
    )
    return _me_out(db, current_user)


@app.get("/auth/me", response_model=MeOut)
def auth_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeOut:
    return _me_out(db, current_user)


@app.post("/tracking/meta/event", response_model=MetaTrackingEventOut)
def track_meta_event(
    payload: MetaTrackingEventIn,
    request: Request,
    current_user: User | None = Depends(get_optional_current_user),
) -> MetaTrackingEventOut:
    sent = _track_meta_event(
        event_name=payload.event_name,
        user=current_user,
        request=request,
        event_id=str(payload.event_id or "").strip() or None,
        event_source_url=payload.event_source_url,
        fbp=payload.fbp,
        fbc=payload.fbc,
        custom_data=payload.custom_data,
    )
    return MetaTrackingEventOut(ok=True, sent=sent)


@app.get("/account/summary", response_model=AccountSummaryOut)
def account_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountSummaryOut:
    workspace, _subscription = _find_user_workspace_and_subscription(db, user_id=current_user.id)
    if workspace is None:
        account_display = _workspace_account_display_payload(None, current_user)
        return AccountSummaryOut(
            integrations_total_available=INTEGRATIONS_TOTAL_AVAILABLE,
            account_display_name=account_display["account_display_name"],
            account_display_name_effective=str(account_display["account_display_name_effective"]),
        )
    return _workspace_summary_out(db, workspace, current_user)


@app.get("/billing/me", response_model=BillingMeOut)
def billing_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingMeOut:
    workspace, subscription = _resolve_workspace_subscription_for_user(db, current_user)
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return _billing_me_out(db, subscription, workspace.id)


@app.post("/billing/plan-change-preview", response_model=BillingPlanChangePreviewOut)
def billing_plan_change_preview(
    payload: BillingCheckoutSessionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingPlanChangePreviewOut:
    workspace, subscription = _resolve_workspace_subscription_for_user(db, current_user)
    current_plan_code = normalize_workspace_plan(subscription.plan or "free")
    target_plan_code = normalize_workspace_plan(payload.plan_code)
    if target_plan_code == "free":
        raise http_error(400, "invalid_plan_code", "Free plan does not require checkout.")

    action_mode: str = "checkout"
    requires_confirmation = False
    if current_plan_code != "free":
        stripe = _configure_stripe()
        reusable_stripe_subscription = _find_reusable_stripe_subscription(
            stripe,
            subscription=subscription,
        )
        if reusable_stripe_subscription is not None:
            current_price_id = _stripe_subscription_price_id(reusable_stripe_subscription)
            reusable_plan_code = normalize_workspace_plan(
                get_plan_code_for_stripe_price(current_price_id) or current_plan_code
            )
            cancel_at_period_end = bool(_stripe_object_get(reusable_stripe_subscription, "cancel_at_period_end"))
            if reusable_plan_code == target_plan_code and not cancel_at_period_end:
                action_mode = "already_on_plan"
            else:
                action_mode = "updated"
            requires_confirmation = True

    return BillingPlanChangePreviewOut(
        action_mode=cast(Literal["checkout", "updated", "already_on_plan"], action_mode),
        requires_confirmation=requires_confirmation,
        billing_status=_billing_status_for_subscription(subscription),
        current_period_end=subscription.current_period_end,
        billing_note=(
            "Your subscription will be updated in Stripe. Any prorated adjustment will be handled automatically by Stripe."
        ),
        current_plan=_billing_plan_snapshot(current_plan_code),
        new_plan=_billing_plan_snapshot(target_plan_code),
    )


@app.post("/billing/create-checkout-session", response_model=BillingCheckoutSessionOut)
def create_billing_checkout_session(
    payload: BillingCheckoutSessionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingCheckoutSessionOut:
    stripe = _configure_stripe()
    workspace, subscription = _resolve_workspace_subscription_for_user(db, current_user)
    plan_code = normalize_workspace_plan(payload.plan_code)
    if plan_code == "free":
        raise http_error(400, "invalid_plan_code", "Free plan does not require checkout.")

    entitlements = get_plan_entitlements(plan_code)
    if int(entitlements["price_monthly_usd"]) <= 0:
        raise http_error(400, "invalid_plan_code", "Selected plan is not billable.")

    price_mapping = get_stripe_price_plan_mapping()
    reverse_mapping = {plan: price for price, plan in price_mapping.items()}
    price_id = reverse_mapping.get(plan_code)
    if not price_id:
        raise http_error(500, "stripe_price_not_configured", f"Missing Stripe price for plan {plan_code}.")

    reusable_stripe_subscription = _find_reusable_stripe_subscription(
        stripe,
        subscription=subscription,
    )
    if reusable_stripe_subscription is not None:
        current_price_id = _stripe_subscription_price_id(reusable_stripe_subscription)
        current_plan_code = get_plan_code_for_stripe_price(current_price_id)
        cancel_at_period_end = bool(_stripe_object_get(reusable_stripe_subscription, "cancel_at_period_end"))
        if current_plan_code == plan_code and not cancel_at_period_end:
            return BillingCheckoutSessionOut(
                mode="already_on_plan",
                plan_code=plan_code,
                billing_status=_stripe_subscription_status(reusable_stripe_subscription) or "active",
                plan_name=_plan_display_name(plan_code),
                price_monthly_usd=int(entitlements["price_monthly_usd"]),
                current_period_end=subscription.current_period_end,
            )

        subscription_item_id = _stripe_subscription_item_id(reusable_stripe_subscription)
        if not subscription_item_id:
            raise http_error(502, "stripe_subscription_invalid", "Stripe subscription items could not be resolved.")
        try:
            updated_subscription = stripe.Subscription.modify(
                str(_stripe_object_get(reusable_stripe_subscription, "id") or "").strip(),
                items=[{"id": subscription_item_id, "price": price_id}],
                cancel_at_period_end=False,
                proration_behavior="create_prorations",
            )
        except Exception as exc:
            raise http_error(502, "stripe_subscription_update_failed", "Stripe subscription could not be updated.") from exc
        _sync_local_subscription_from_stripe_object(
            db,
            subscription=subscription,
            stripe_subscription=updated_subscription,
        )
        db.refresh(subscription)
        return BillingCheckoutSessionOut(
            mode="updated",
            plan_code=plan_code,
            billing_status=_stripe_subscription_status(updated_subscription) or "active",
            plan_name=_plan_display_name(plan_code),
            price_monthly_usd=int(entitlements["price_monthly_usd"]),
            current_period_end=subscription.current_period_end,
        )

    customer_id = str(subscription.stripe_customer_id or "").strip()
    if not customer_id:
        customer = stripe.Customer.create(
            email=current_user.email,
            name=current_user.full_name,
            metadata={
                "user_id": str(current_user.id),
                "workspace_id": str(workspace.id),
            },
        )
        customer_id = str(customer.get("id"))
        subscription.stripe_customer_id = customer_id
        db.add(subscription)
        db.commit()
        db.refresh(subscription)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        metadata={
            "user_id": str(current_user.id),
            "workspace_id": str(workspace.id),
            "plan_code": plan_code,
        },
        success_url=_stripe_checkout_success_url(),
        cancel_url=_stripe_checkout_cancel_url(),
    )
    checkout_url = str(session.get("url") or "").strip()
    if not checkout_url:
        raise http_error(502, "stripe_checkout_failed", "Stripe did not return a checkout URL.")
    return BillingCheckoutSessionOut(
        mode="checkout",
        checkout_url=checkout_url,
        plan_code=plan_code,
        plan_name=_plan_display_name(plan_code),
        price_monthly_usd=int(entitlements["price_monthly_usd"]),
    )


@app.post("/billing/create-portal-session", response_model=BillingPortalSessionOut)
def create_billing_portal_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingPortalSessionOut:
    stripe = _configure_stripe()
    workspace, subscription = _resolve_workspace_subscription_for_user(db, current_user)
    customer_id = str(subscription.stripe_customer_id or "").strip()
    if not customer_id:
        raise http_error(400, "stripe_customer_not_found", "No Stripe customer found for this account.")
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=_billing_portal_return_url(),
    )
    portal_url = str(session.get("url") or "").strip()
    if not portal_url:
        raise http_error(502, "stripe_portal_failed", "Stripe did not return a portal URL.")
    return BillingPortalSessionOut(portal_url=portal_url)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    stripe = _configure_stripe()
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise http_error(400, "missing_stripe_signature", "Missing Stripe signature.")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=str(settings.stripe_webhook_secret or "").strip(),
        )
    except Exception as exc:
        raise http_error(400, "invalid_stripe_signature", "Invalid Stripe signature.") from exc

    event_type = str(event.get("type") or "").strip()
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        metadata = data_object.get("metadata") or {}
        user_id_raw = str(metadata.get("user_id") or "").strip()
        if user_id_raw.isdigit():
            user = db.get(User, int(user_id_raw))
            if user is not None:
                workspace, subscription = _resolve_workspace_subscription_for_user(db, user)
                subscription.stripe_customer_id = str(data_object.get("customer") or "").strip() or subscription.stripe_customer_id
                subscription.stripe_subscription_id = str(data_object.get("subscription") or "").strip() or subscription.stripe_subscription_id
                db.add(subscription)
                db.commit()
        return {"received": True}

    if event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        price_id = (
            data_object.get("items", {})
            .get("data", [{}])[0]
            .get("price", {})
            .get("id")
        )
        plan_code = get_plan_code_for_stripe_price(price_id)
        if plan_code:
            subscription = _find_subscription_for_stripe_event(
                db,
                stripe_subscription_id=data_object.get("id"),
                stripe_customer_id=data_object.get("customer"),
            )
            if subscription is not None:
                _apply_stripe_subscription_state(
                    subscription,
                    plan_code=plan_code,
                    billing_status=str(data_object.get("status") or "active"),
                    stripe_customer_id=data_object.get("customer"),
                    stripe_subscription_id=data_object.get("id"),
                    stripe_price_id=price_id,
                    current_period_start=data_object.get("current_period_start"),
                    current_period_end=data_object.get("current_period_end"),
                    cancel_at_period_end=data_object.get("cancel_at_period_end"),
                )
                db.add(subscription)
                db.commit()
        return {"received": True}

    if event_type == "customer.subscription.deleted":
        subscription = _find_subscription_for_stripe_event(
            db,
            stripe_subscription_id=data_object.get("id"),
            stripe_customer_id=data_object.get("customer"),
        )
        if subscription is not None:
            _downgrade_subscription_to_free(subscription)
            db.add(subscription)
            db.commit()
        return {"received": True}

    if event_type == "invoice.paid":
        subscription = _find_subscription_for_stripe_event(
            db,
            stripe_subscription_id=data_object.get("subscription"),
            stripe_customer_id=data_object.get("customer"),
        )
        if subscription is not None and normalize_workspace_plan(subscription.plan or "free") != "free":
            subscription.billing_status = "active"
            subscription.status = "active"
            db.add(subscription)
            db.commit()
        return {"received": True}

    if event_type == "invoice.payment_failed":
        subscription = _find_subscription_for_stripe_event(
            db,
            stripe_subscription_id=data_object.get("subscription"),
            stripe_customer_id=data_object.get("customer"),
        )
        if subscription is not None:
            subscription.billing_status = "past_due"
            db.add(subscription)
            db.commit()
        return {"received": True}

    return {"received": True}


def _suggestion_out(suggestion: UserSuggestion) -> UserSuggestionOut:
    return UserSuggestionOut(
        id=suggestion.id,
        user_id=suggestion.user_id,
        workspace_id=suggestion.workspace_id,
        message=suggestion.message,
        status=suggestion.status,
        source=suggestion.source,
        reviewed_at=suggestion.reviewed_at,
        reviewed_by=suggestion.reviewed_by,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
    )


def _validate_wishlist_payload(payload: WishlistCreateIn) -> dict[str, str | None]:
    name = str(payload.name or "").strip()
    email = str(payload.email or "").strip().lower()
    company = str(payload.company or "").strip() or None
    message = str(payload.message or "").strip()
    source = str(payload.source or "").strip() or "upgrade_page"

    if not name:
        raise http_error(400, "invalid_name", "Name is required.")
    if len(name) > 255:
        raise http_error(400, "name_too_long", "Name must be 255 characters or fewer.")
    if not email or not EMAIL_PATTERN.match(email):
        raise http_error(400, "invalid_email", "Valid email is required.")
    if company and len(company) > 255:
        raise http_error(400, "company_too_long", "Company must be 255 characters or fewer.")
    if not message:
        raise http_error(400, "invalid_message", "Message is required.")
    if len(message) > 2000:
        raise http_error(400, "message_too_long", "Message must be 2000 characters or fewer.")
    if len(source) > 100:
        raise http_error(400, "source_too_long", "Source must be 100 characters or fewer.")

    return {
        "name": name,
        "email": email,
        "company": company,
        "message": message,
        "source": source,
    }


@app.post("/suggestions", response_model=SuggestionCreateOut, status_code=201)
def create_suggestion(
    payload: SuggestionCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuggestionCreateOut:
    if not payload.message.strip():
        raise http_error(400, "invalid_message", "Suggestion message is required.")
    if len(payload.message) > 1000:
        raise http_error(400, "message_too_long", "Suggestion message must be 1000 characters or fewer.")

    workspace_ids = _workspace_ids_for_user(db, current_user.id)
    suggestion = UserSuggestion(
        user_id=current_user.id,
        workspace_id=workspace_ids[0] if workspace_ids else None,
        message=payload.message,
        status="new",
        source="floating_suggestion_button",
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return SuggestionCreateOut(success=True, suggestion=_suggestion_out(suggestion))


@app.post("/wishlist", response_model=WishlistCreateOut, status_code=201)
def create_wishlist_lead(
    payload: WishlistCreateIn,
    current_user: User | None = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
) -> WishlistCreateOut:
    normalized = _validate_wishlist_payload(payload)
    workspace_ids = _workspace_ids_for_user(db, current_user.id) if current_user is not None else []
    lead = WishlistLead(
        user_id=current_user.id if current_user is not None else None,
        workspace_id=workspace_ids[0] if workspace_ids else None,
        name=str(normalized["name"]),
        email=str(normalized["email"]),
        company=str(normalized["company"]) if normalized["company"] is not None else None,
        message=str(normalized["message"]),
        source=str(normalized["source"]),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return WishlistCreateOut(success=True, lead=_wishlist_lead_out(lead))


@app.get("/admin/suggestions", response_model=list[AdminSuggestionOut])
def admin_list_suggestions(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[AdminSuggestionOut]:
    rows = (
        db.query(UserSuggestion, User.email, User.full_name, Workspace.name)
        .join(User, User.id == UserSuggestion.user_id)
        .outerjoin(Workspace, Workspace.id == UserSuggestion.workspace_id)
        .order_by(UserSuggestion.created_at.desc(), UserSuggestion.id.desc())
        .all()
    )
    return [
        AdminSuggestionOut(
            id=suggestion.id,
            user_id=suggestion.user_id,
            workspace_id=suggestion.workspace_id,
            message=suggestion.message,
            status=suggestion.status,
            source=suggestion.source,
            reviewed_at=suggestion.reviewed_at,
            reviewed_by=suggestion.reviewed_by,
            created_at=suggestion.created_at,
            updated_at=suggestion.updated_at,
            user_email=user_email,
            user_name=user_name,
            workspace_name=workspace_name,
        )
        for suggestion, user_email, user_name, workspace_name in rows
    ]


@app.get("/admin/wishlist", response_model=list[AdminWishlistLeadOut])
def admin_list_wishlist_leads(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[AdminWishlistLeadOut]:
    rows = (
        db.query(WishlistLead, User.email, User.full_name, Workspace.name)
        .outerjoin(User, User.id == WishlistLead.user_id)
        .outerjoin(Workspace, Workspace.id == WishlistLead.workspace_id)
        .order_by(WishlistLead.created_at.desc(), WishlistLead.id.desc())
        .all()
    )
    return [
        AdminWishlistLeadOut(
            id=lead.id,
            user_id=lead.user_id,
            workspace_id=lead.workspace_id,
            name=lead.name,
            email=lead.email,
            company=lead.company,
            message=lead.message,
            source=lead.source,
            created_at=lead.created_at,
            user_email=user_email,
            user_name=user_name,
            workspace_name=workspace_name,
        )
        for lead, user_email, user_name, workspace_name in rows
    ]


@app.patch("/admin/suggestions/{suggestion_id}", response_model=AdminSuggestionOut)
def admin_update_suggestion_status(
    suggestion_id: int,
    payload: SuggestionStatusUpdateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> AdminSuggestionOut:
    suggestion = db.get(UserSuggestion, suggestion_id)
    if suggestion is None:
        raise http_error(404, "suggestion_not_found", "Suggestion not found.")

    suggestion.status = payload.status
    if payload.status in {"reviewed", "archived"}:
        suggestion.reviewed_at = datetime.now(timezone.utc)
        suggestion.reviewed_by = current_user.id
    elif payload.status == "new":
        suggestion.reviewed_at = None
        suggestion.reviewed_by = None
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)

    user = db.get(User, suggestion.user_id)
    workspace = db.get(Workspace, suggestion.workspace_id) if suggestion.workspace_id is not None else None
    return AdminSuggestionOut(
        id=suggestion.id,
        user_id=suggestion.user_id,
        workspace_id=suggestion.workspace_id,
        message=suggestion.message,
        status=suggestion.status,
        source=suggestion.source,
        reviewed_at=suggestion.reviewed_at,
        reviewed_by=suggestion.reviewed_by,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        user_email=user.email if user else None,
        user_name=user.full_name if user else None,
        workspace_name=workspace.name if workspace else None,
    )


@app.get("/admin/metrics", response_model=AdminMetricsOut)
def admin_metrics(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
    timeframe: str = Query(default="all"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> AdminMetricsOut:
    resolved_timeframe = _resolve_admin_metrics_timeframe(timeframe, start_date, end_date)
    selected_timeframe = str(resolved_timeframe["timeframe"])
    period_start = resolved_timeframe["start_dt"]
    period_end = resolved_timeframe["end_dt"]

    total_users = _safe_int_scalar(
        db,
        db.query(func.count(User.id)).filter(User.is_deleted.is_(False)),
    )

    legacy_cutoff_7 = datetime.now(timezone.utc) - timedelta(days=7)
    users_last_7_days = _safe_int_scalar(
        db,
        db.query(func.count(User.id))
        .filter(User.is_deleted.is_(False))
        .filter(User.created_at >= legacy_cutoff_7),
    )
    active_users_last_7_days = _safe_int_scalar(
        db,
        db.query(func.count(User.id))
        .filter(User.is_deleted.is_(False))
        .filter(User.last_login_at >= legacy_cutoff_7),
    )
    onboarding_completed_legacy = _safe_int_scalar(
        db,
        db.query(func.count(User.id))
        .filter(User.is_deleted.is_(False))
        .filter(User.onboarding_completed.is_(True)),
    )
    onboarding_pending = max(total_users - onboarding_completed_legacy, 0)

    users_in_period = _count_users_in_range(
        db,
        start_dt=period_start,
        end_dt=period_end,
        column=User.created_at,
    ) if period_start and period_end else total_users
    active_users_in_period = _count_users_in_range(
        db,
        start_dt=period_start,
        end_dt=period_end,
        column=User.last_login_at,
    ) if period_start and period_end else _safe_int_scalar(
        db,
        db.query(func.count(User.id))
        .filter(User.is_deleted.is_(False))
        .filter(User.last_login_at.is_not(None)),
    )
    onboarding_completed_in_period = _count_users_in_range(
        db,
        start_dt=period_start,
        end_dt=period_end,
        column=User.updated_at,
        extra_filters=[User.onboarding_completed.is_(True)],
    ) if period_start and period_end else onboarding_completed_legacy
    onboarding_completion_rate = (
        round((onboarding_completed_in_period / users_in_period) * 100.0, 2) if users_in_period else 0.0
    )

    users_growth_percent: float | None = None
    reports_growth_percent: float | None = None
    active_users_growth_percent: float | None = None
    previous_users_in_period: int | None = None
    previous_reports_in_period: int | None = None
    previous_active_users_in_period: int | None = None
    paid_users_in_scope: int | None = None
    previous_paid_users_in_scope: int | None = None
    daily_users_points: list[dict[str, Any]] = []
    daily_reports_points: list[dict[str, Any]] = []
    cumulative_users_points: list[dict[str, Any]] = []

    if selected_timeframe == "all":
        user_rows = []
        report_rows = []
        if _table_available("users"):
            user_rows = [
                value.date()
                for (value,) in db.query(User.created_at)
                .filter(User.is_deleted.is_(False))
                .filter(User.created_at.is_not(None))
                .all()
                if value
            ]
        if _table_available("reports"):
            report_rows = [
                value.date()
                for (value,) in db.query(Report.created_at)
                .filter(Report.created_at.is_not(None))
                .all()
                if value
            ]
        all_dates = sorted(set(user_rows + report_rows))
        if all_dates:
            series_start = all_dates[0]
            series_end = all_dates[-1]
            series_dates = _date_range(series_start, series_end)
            user_daily_counts = dict.fromkeys(series_dates, 0)
            report_daily_counts = dict.fromkeys(series_dates, 0)
            for day in user_rows:
                if day in user_daily_counts:
                    user_daily_counts[day] += 1
            for day in report_rows:
                if day in report_daily_counts:
                    report_daily_counts[day] += 1
            daily_users_points = [
                {"date": current_day, "users": user_daily_counts.get(current_day, 0)} for current_day in series_dates
            ]
            daily_reports_points = [
                {"date": current_day, "reports": report_daily_counts.get(current_day, 0)}
                for current_day in series_dates
            ]
            running_total = 0
            for current_day in series_dates:
                running_total += user_daily_counts.get(current_day, 0)
                cumulative_users_points.append({"date": current_day, "total_users": running_total})
    else:
        if period_start and period_end:
            selected_start_date = resolved_timeframe["start_date"]
            selected_end_date = resolved_timeframe["end_date"]
            if isinstance(selected_start_date, date) and isinstance(selected_end_date, date):
                selected_dates = _date_range(selected_start_date, selected_end_date)
                user_daily_counts = _daily_counts(
                    db,
                    model=User,
                    column=User.created_at,
                    start_dt=period_start,
                    end_dt=period_end,
                    extra_filters=[User.is_deleted.is_(False)],
                )
                report_daily_counts = (
                    _daily_counts(
                        db,
                        model=Report,
                        column=Report.created_at,
                        start_dt=period_start,
                        end_dt=period_end,
                    )
                    if _table_available("reports")
                    else {}
                )
                daily_users_points = [
                    {"date": current_day, "users": user_daily_counts.get(current_day, 0)} for current_day in selected_dates
                ]
                daily_reports_points = [
                    {"date": current_day, "reports": report_daily_counts.get(current_day, 0)}
                    for current_day in selected_dates
                ]
                running_total = 0
                for current_day in selected_dates:
                    running_total += user_daily_counts.get(current_day, 0)
                    cumulative_users_points.append({"date": current_day, "total_users": running_total})
        else:
            daily_users_points = []
            daily_reports_points = []
            cumulative_users_points = []

    total_reports = 0
    reports_last_7_days = 0
    reports_in_period = 0
    if _table_available("reports"):
        total_reports = _safe_int_scalar(db, db.query(func.count(Report.id)))
        reports_last_7_days = _safe_int_scalar(
            db,
            db.query(func.count(Report.id)).filter(Report.created_at >= legacy_cutoff_7),
        )
        reports_in_period = (
            _safe_int_scalar(
                db,
                db.query(func.count(Report.id))
                .filter(Report.created_at >= period_start)
                .filter(Report.created_at <= period_end),
            )
            if period_start and period_end
            else total_reports
        )
    else:
        reports_in_period = 0

    if selected_timeframe != "all" and period_start and period_end:
        selected_start_date = resolved_timeframe["start_date"]
        selected_end_date = resolved_timeframe["end_date"]
        if isinstance(selected_start_date, date) and isinstance(selected_end_date, date):
            previous_start, previous_end = _previous_equivalent_range(selected_start_date, selected_end_date)
            if previous_start is not None and previous_end is not None:
                previous_start_dt = _day_start(previous_start)
                previous_end_dt = _day_end(previous_end)
                previous_users = _count_rows_in_range(
                    db,
                    model=User,
                    column=User.created_at,
                    start_dt=previous_start_dt,
                    end_dt=previous_end_dt,
                    extra_filters=[User.is_deleted.is_(False)],
                )
                previous_active_users = _count_rows_in_range(
                    db,
                    model=User,
                    column=User.last_login_at,
                    start_dt=previous_start_dt,
                    end_dt=previous_end_dt,
                    extra_filters=[User.is_deleted.is_(False), User.last_login_at.is_not(None)],
                )
                previous_reports = (
                    _count_rows_in_range(
                        db,
                        model=Report,
                        column=Report.created_at,
                        start_dt=previous_start_dt,
                        end_dt=previous_end_dt,
                    )
                    if _table_available("reports")
                    else 0
                )
                users_growth_percent = _percent_change(users_in_period, previous_users)
                reports_growth_percent = _percent_change(reports_in_period, previous_reports)
                active_users_growth_percent = _percent_change(active_users_in_period, previous_active_users)
                previous_users_in_period = previous_users
                previous_reports_in_period = previous_reports
                previous_active_users_in_period = previous_active_users

    deletions_in_period = 0
    if _table_available("account_deletion_feedback"):
        deletions_query = db.query(func.count(AccountDeletionFeedback.id))
        if period_start and period_end:
            deletions_query = (
                deletions_query.filter(AccountDeletionFeedback.created_at >= period_start)
                .filter(AccountDeletionFeedback.created_at <= period_end)
            )
        deletions_in_period = _safe_int_scalar(db, deletions_query)

    paid_users = 0
    if _table_available("subscriptions"):
        try:
            paid_users = _safe_int_scalar(
                db,
                db.query(func.count(func.distinct(WorkspaceMember.user_id)))
                .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
                .filter(Subscription.status == "active")
                .filter(func.lower(Subscription.plan) != "free"),
            )
        except SQLAlchemyError:
            paid_users = 0
    free_users = max(total_users - paid_users, 0) if paid_users else total_users

    if selected_timeframe == "all":
        paid_users_in_scope = paid_users
        previous_paid_users_in_scope = None
    elif period_start and period_end and _table_available("subscriptions"):
        current_paid_query = (
            db.query(func.count(func.distinct(WorkspaceMember.user_id)))
            .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
            .filter(Subscription.status == "active")
            .filter(func.lower(Subscription.plan) != "free")
            .filter(Subscription.created_at >= period_start)
            .filter(Subscription.created_at <= period_end)
        )
        paid_users_in_scope = _safe_int_scalar(db, current_paid_query)
        if previous_users_in_period is not None:
            previous_start_date, previous_end_date = _previous_equivalent_range(
                resolved_timeframe["start_date"],
                resolved_timeframe["end_date"],
            )
            if previous_start_date is not None and previous_end_date is not None:
                previous_paid_users_in_scope = _safe_int_scalar(
                    db,
                    db.query(func.count(func.distinct(WorkspaceMember.user_id)))
                    .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
                    .filter(Subscription.status == "active")
                    .filter(func.lower(Subscription.plan) != "free")
                    .filter(Subscription.created_at >= _day_start(previous_start_date))
                    .filter(Subscription.created_at <= _day_end(previous_end_date)),
                )

    return AdminMetricsOut(
        timeframe=selected_timeframe,
        start_date=resolved_timeframe["start_date"],
        end_date=resolved_timeframe["end_date"],
        total_users=total_users,
        users_in_period=users_in_period,
        active_users_in_period=active_users_in_period,
        reports_in_period=reports_in_period,
        onboarding_completed_in_period=onboarding_completed_in_period,
        onboarding_completion_rate=onboarding_completion_rate,
        deletions_in_period=deletions_in_period,
        users_last_7_days=users_last_7_days,
        active_users_last_7_days=active_users_last_7_days,
        onboarding_completed=onboarding_completed_legacy,
        onboarding_pending=onboarding_pending,
        total_reports=total_reports,
        reports_last_7_days=reports_last_7_days,
        paid_users=paid_users,
        free_users=free_users,
        mrr=0.0,
        daily_users=daily_users_points,
        daily_reports=daily_reports_points,
        cumulative_users=cumulative_users_points,
        users_growth_percent=users_growth_percent,
        reports_growth_percent=reports_growth_percent,
        active_users_growth_percent=active_users_growth_percent,
        insights=_build_admin_metric_insights(
            timeframe=selected_timeframe,
            users_in_period=users_in_period,
            previous_users_in_period=previous_users_in_period,
            reports_in_period=reports_in_period,
            previous_reports_in_period=previous_reports_in_period,
            active_users_in_period=active_users_in_period,
            previous_active_users_in_period=previous_active_users_in_period,
            onboarding_completion_rate=onboarding_completion_rate,
            paid_users_in_scope=paid_users_in_scope,
            previous_paid_users_in_scope=previous_paid_users_in_scope,
        ),
    )


@app.get("/admin/users", response_model=AdminUsersOut)
def admin_users(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
    search: str | None = Query(default=None),
    auth_provider: str | None = Query(default=None),
    onboarding_completed: bool | None = Query(default=None),
    plan: str | None = Query(default=None),
    is_deleted: bool | None = Query(default=None),
    health_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> AdminUsersOut:
    report_counts_subq = _admin_reports_count_subquery(db)
    report_activity_subq = _admin_reports_activity_subquery(db)
    integrations_count_subq = _admin_integrations_count_subquery(db)
    plan_subq = _admin_latest_plan_subquery(db)

    base_query = db.query(User)
    if report_counts_subq is not None:
        base_query = base_query.outerjoin(report_counts_subq, report_counts_subq.c.user_id == User.id)
    if report_activity_subq is not None:
        base_query = base_query.outerjoin(report_activity_subq, report_activity_subq.c.user_id == User.id)
    if integrations_count_subq is not None:
        base_query = base_query.outerjoin(integrations_count_subq, integrations_count_subq.c.user_id == User.id)
    if plan_subq is not None:
        base_query = base_query.outerjoin(plan_subq, plan_subq.c.user_id == User.id)

    if search:
        search_term = f"%{search.strip()}%"
        base_query = base_query.filter(or_(User.email.ilike(search_term), User.full_name.ilike(search_term)))
    if auth_provider:
        base_query = base_query.filter(func.lower(User.auth_provider) == auth_provider.strip().lower())
    if onboarding_completed is not None:
        base_query = base_query.filter(User.onboarding_completed.is_(onboarding_completed))
    if is_deleted is not None:
        base_query = base_query.filter(User.is_deleted.is_(is_deleted))

    plan_expr = func.coalesce(plan_subq.c.plan, literal("free")) if plan_subq is not None else literal("free")
    if plan:
        plan_filter = plan.strip().lower()
        if plan_subq is None:
            if plan_filter != "free":
                return AdminUsersOut(items=[], total=0, page=page, page_size=page_size)
        else:
            base_query = base_query.filter(func.lower(plan_expr) == plan_filter)

    reports_count_expr = (
        func.coalesce(report_counts_subq.c.reports_count, 0)
        if report_counts_subq is not None
        else literal(0)
    )
    last_report_created_at_expr = (
        report_activity_subq.c.last_report_created_at
        if report_activity_subq is not None
        else literal(None)
    )
    reports_last_7_days_expr = (
        func.coalesce(report_activity_subq.c.reports_last_7_days, 0)
        if report_activity_subq is not None
        else literal(0)
    )
    integrations_count_expr = (
        func.coalesce(integrations_count_subq.c.integrations_count, 0)
        if integrations_count_subq is not None
        else literal(0)
    )
    health_score_expr = _admin_health_score_expression(
        cutoff_7=datetime.now(timezone.utc) - timedelta(days=7),
        reports_count_expr=reports_count_expr,
        reports_last_7_days_expr=reports_last_7_days_expr,
        integrations_count_expr=integrations_count_expr,
        plan_expr=plan_expr,
    )
    health_status_expr = case(
        (health_score_expr >= 80, literal("healthy")),
        (health_score_expr >= 50, literal("active")),
        (health_score_expr >= 25, literal("at_risk")),
        else_=literal("dormant"),
    )
    health_status_filter = str(health_status or "").strip().lower()
    if health_status_filter:
        if health_status_filter not in {"healthy", "active", "at_risk", "dormant"}:
            raise http_error(
                400,
                "invalid_health_status",
                'Invalid health_status. Use "healthy", "active", "at_risk", or "dormant".',
            )
        base_query = base_query.filter(func.lower(health_status_expr) == health_status_filter)

    total = _safe_int_scalar(
        db,
        base_query.with_entities(func.count(func.distinct(User.id))),
    )
    rows = (
        base_query.add_columns(
            reports_count_expr.label("reports_count"),
            last_report_created_at_expr.label("last_report_created_at"),
            reports_last_7_days_expr.label("reports_last_7_days"),
            plan_expr.label("plan"),
            integrations_count_expr.label("integrations_count"),
            health_score_expr.label("health_score"),
            health_status_expr.label("health_status"),
        )
        .order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminUserOut(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            auth_provider=user.auth_provider,
            email_verified=bool(user.email_verified),
            onboarding_completed=bool(getattr(user, "onboarding_completed", False)),
            user_type=str(user.user_type).strip() if getattr(user, "user_type", None) else None,
            plan=str(plan_value) if plan_value else "free",
            reports_count=int(reports_count or 0),
            last_report_created_at=last_report_created_at,
            last_report_at=last_report_created_at,
            last_report_created=last_report_created_at,
            reports_last_7_days=int(reports_last_7_days or 0),
            health_score=int(health_score or 0),
            health_status=str(health_status_value) if health_status_value else _admin_health_status_from_score(
                int(health_score or 0)
            ),
            health_reasons=_admin_health_reasons(
                email_verified=bool(user.email_verified),
                onboarding_completed=bool(getattr(user, "onboarding_completed", False)),
                reports_count=int(reports_count or 0),
                reports_last_7_days=int(reports_last_7_days or 0),
                last_login_at=user.last_login_at,
                integrations_count=int(integrations_count or 0),
                plan_value=str(plan_value) if plan_value else "free",
            ),
            last_login_at=user.last_login_at,
            last_login=user.last_login_at,
            created_at=user.created_at,
            is_active=bool(user.is_active),
            is_deleted=bool(getattr(user, "is_deleted", False)),
        )
        for (
            user,
            reports_count,
            last_report_created_at,
            reports_last_7_days,
            plan_value,
            integrations_count,
            health_score,
            health_status_value,
        ) in rows
    ]
    return AdminUsersOut(items=items, total=total, page=page, page_size=page_size)


@app.get("/admin/funnel", response_model=AdminFunnelOut)
def admin_funnel(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
    timeframe: str = Query(default="all"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> AdminFunnelOut:
    resolved_timeframe = _resolve_admin_metrics_timeframe(timeframe, start_date, end_date)
    period_start = resolved_timeframe["start_dt"]
    period_end = resolved_timeframe["end_dt"]

    cohort_query = db.query(User).filter(User.is_deleted.is_(False))
    if period_start and period_end:
        cohort_query = cohort_query.filter(User.created_at >= period_start).filter(User.created_at <= period_end)
    cohort_users = cohort_query.all()
    cohort_user_ids = [user.id for user in cohort_users]

    signup_count = len(cohort_users)
    onboarding_completed_count = sum(1 for user in cohort_users if user.onboarding_completed)

    report_counts_subq = _admin_reports_count_subquery(db)
    report_activity_subq = _admin_reports_activity_subquery(db)
    ai_usage_subq = _admin_ai_usage_subquery(db)
    plan_subq = _admin_latest_plan_subquery(db)

    report_user_ids: set[int] = set()
    if cohort_user_ids and _table_available("reports"):
        report_user_ids_query = (
            db.query(func.distinct(WorkspaceMember.user_id))
            .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id.in_(cohort_user_ids))
        )
        if period_start and period_end:
            report_user_ids_query = report_user_ids_query.filter(Report.created_at >= period_start).filter(
                Report.created_at <= period_end
            )
        report_user_ids = {
                int(user_id)
                for (user_id,) in report_user_ids_query.all()
                if user_id is not None
            }

    ai_user_ids: set[int] = set()
    if cohort_user_ids and ai_usage_subq is not None:
        ai_user_ids_query = (
            db.query(func.distinct(WorkspaceMember.user_id))
            .join(Conversation, Conversation.workspace_id == WorkspaceMember.workspace_id)
            .join(Message, Message.conversation_id == Conversation.id)
            .filter(WorkspaceMember.user_id.in_(cohort_user_ids))
        )
        if period_start and period_end:
            ai_user_ids_query = ai_user_ids_query.filter(Message.created_at >= period_start).filter(
                Message.created_at <= period_end
            )
        ai_user_ids = {
            int(user_id)
            for (user_id,) in ai_user_ids_query.all()
            if user_id is not None
        }

    paid_user_ids: set[int] = set()
    if cohort_user_ids and _table_available("subscriptions"):
        paid_user_ids_query = (
            db.query(func.distinct(WorkspaceMember.user_id))
            .join(Subscription, Subscription.workspace_id == WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id.in_(cohort_user_ids))
            .filter(Subscription.status == "active")
            .filter(func.lower(Subscription.plan) != "free")
        )
        if period_start and period_end:
            paid_user_ids_query = paid_user_ids_query.filter(Subscription.created_at >= period_start).filter(
                Subscription.created_at <= period_end
            )
        paid_user_ids = {
            int(user_id)
            for (user_id,) in paid_user_ids_query.all()
            if user_id is not None
        }

    activated_user_ids: set[int] = set()
    if cohort_user_ids and report_activity_subq is not None:
        activated_user_ids_query = (
            db.query(report_activity_subq.c.user_id)
            .filter(report_activity_subq.c.user_id.in_(cohort_user_ids))
            .filter(report_activity_subq.c.reports_last_7_days > 0)
        )
        activated_user_ids = {
            int(user_id)
            for (user_id,) in activated_user_ids_query.all()
            if user_id is not None
        }

    step_counts = [
        ("Signups", signup_count),
        ("Onboarding", onboarding_completed_count),
        ("Reports created", len(report_user_ids)),
        ("AI Assistant used", len(ai_user_ids)),
        ("Activated users", len(activated_user_ids)),
        ("Paid Users", len(paid_user_ids)),
    ]
    steps: list[AdminFunnelStepOut] = []
    previous_count = 0
    strongest_step = step_counts[0][0] if step_counts else ""
    strongest_count = step_counts[0][1] if step_counts else 0
    biggest_dropoff_stage = step_counts[0][0] if step_counts else ""
    biggest_dropoff_value = 0

    for index, (name, count) in enumerate(step_counts):
        conversion_from_start = round((count / signup_count) * 100.0, 2) if signup_count else 0.0
        conversion_from_previous = round((count / previous_count) * 100.0, 2) if previous_count else 0.0
        dropoff = max(previous_count - count, 0) if index > 0 else 0
        steps.append(
            AdminFunnelStepOut(
                name=name,
                count=count,
                conversion_from_previous=conversion_from_previous,
                conversion_from_start=conversion_from_start,
                dropoff=dropoff,
            )
        )
        if index > 0 and dropoff > biggest_dropoff_value:
            biggest_dropoff_value = dropoff
            biggest_dropoff_stage = name
        if count > strongest_count:
            strongest_count = count
            strongest_step = name
        previous_count = count

    total_conversion = round((steps[-1].count / signup_count) * 100.0, 2) if signup_count else 0.0

    return AdminFunnelOut(
        steps=steps,
        summary={
            "total_conversion": total_conversion,
            "biggest_dropoff_stage": biggest_dropoff_stage,
            "strongest_step": strongest_step,
        },
    )


@app.get("/admin/product-metrics", response_model=AdminProductMetricsOut)
def admin_product_metrics(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
    timeframe: str = Query(default="all"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> AdminProductMetricsOut:
    resolved_timeframe = _resolve_admin_metrics_timeframe(timeframe, start_date, end_date)
    period_start = resolved_timeframe["start_dt"]
    period_end = resolved_timeframe["end_dt"]

    cohort_query = db.query(User).filter(User.is_deleted.is_(False))
    if period_start and period_end:
        cohort_query = cohort_query.filter(User.created_at >= period_start).filter(User.created_at <= period_end)
    cohort_users = cohort_query.all()
    user_ids = [user.id for user in cohort_users if user.id is not None]
    total_users = len(user_ids)

    report_counts_by_user: dict[int, int] = {}
    first_report_created_at_by_user: dict[int, datetime] = {}
    if user_ids and _table_available("reports"):
        report_query = (
            db.query(
                WorkspaceMember.user_id.label("user_id"),
                func.count(func.distinct(Report.id)).label("reports_count"),
            )
            .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id.in_(user_ids))
        )
        if period_start and period_end:
            report_query = report_query.filter(Report.created_at >= period_start).filter(Report.created_at <= period_end)
        for user_id, reports_count in report_query.group_by(WorkspaceMember.user_id).all():
            if user_id is None:
                continue
            report_counts_by_user[int(user_id)] = int(reports_count or 0)

        first_report_rows = (
            db.query(
                WorkspaceMember.user_id.label("user_id"),
                User.created_at.label("user_created_at"),
                Report.created_at.label("report_created_at"),
            )
            .join(User, User.id == WorkspaceMember.user_id)
            .join(Report, Report.workspace_id == WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id.in_(user_ids))
        )
        if period_start and period_end:
            first_report_rows = first_report_rows.filter(Report.created_at >= period_start).filter(
                Report.created_at <= period_end
            )
        for user_id, user_created_at, report_created_at in first_report_rows.all():
            if user_id is None or user_created_at is None or report_created_at is None:
                continue
            user_id_int = int(user_id)
            signup_at = user_created_at if user_created_at.tzinfo is not None else user_created_at.replace(
                tzinfo=timezone.utc
            )
            report_at = report_created_at if report_created_at.tzinfo is not None else report_created_at.replace(
                tzinfo=timezone.utc
            )
            if report_at < signup_at:
                continue
            current_first = first_report_created_at_by_user.get(user_id_int)
            if current_first is None or report_at < current_first:
                first_report_created_at_by_user[user_id_int] = report_at

    ai_messages_by_user: dict[int, int] = {}
    if user_ids and _table_available("conversations") and _table_available("messages"):
        ai_query = (
            db.query(
                WorkspaceMember.user_id.label("user_id"),
                func.count(func.distinct(Message.id)).label("ai_messages_count"),
            )
            .join(Conversation, Conversation.workspace_id == WorkspaceMember.workspace_id)
            .join(Message, Message.conversation_id == Conversation.id)
            .filter(WorkspaceMember.user_id.in_(user_ids))
        )
        if period_start and period_end:
            ai_query = ai_query.filter(Message.created_at >= period_start).filter(Message.created_at <= period_end)
        for user_id, ai_messages_count in ai_query.group_by(WorkspaceMember.user_id).all():
            if user_id is None:
                continue
            ai_messages_by_user[int(user_id)] = int(ai_messages_count or 0)

    total_reports = sum(report_counts_by_user.values())
    users_with_reports = sum(1 for count in report_counts_by_user.values() if count > 0)
    users_with_2_reports = sum(1 for count in report_counts_by_user.values() if count >= 2)
    users_used_ai = sum(1 for count in ai_messages_by_user.values() if count > 0)

    time_deltas_hours: list[float] = []
    for user in cohort_users:
        if user.id is None or user.created_at is None:
            continue
        first_report_created_at = first_report_created_at_by_user.get(int(user.id))
        if first_report_created_at is None:
            continue
        signup_at = user.created_at
        if signup_at.tzinfo is None:
            signup_at = signup_at.replace(tzinfo=timezone.utc)
        first_report_at = first_report_created_at
        if first_report_at.tzinfo is None:
            first_report_at = first_report_at.replace(tzinfo=timezone.utc)
        delta_hours = max((first_report_at - signup_at).total_seconds() / 3600.0, 0.0)
        time_deltas_hours.append(delta_hours)

    avg_time_to_first_report = round(sum(time_deltas_hours) / len(time_deltas_hours), 2) if time_deltas_hours else 0.0
    reports_per_user = round(total_reports / total_users, 2) if total_users else 0.0
    ai_usage_rate = round((users_used_ai / total_users) * 100.0, 2) if total_users else 0.0
    repeat_usage_rate = round((users_with_2_reports / users_with_reports) * 100.0, 2) if users_with_reports else 0.0

    return AdminProductMetricsOut(
        avg_time_to_first_report=avg_time_to_first_report,
        time_to_first_report_unit="hours",
        reports_per_user=reports_per_user,
        ai_usage_rate=ai_usage_rate,
        repeat_usage_rate=repeat_usage_rate,
        total_users=total_users,
        users_with_reports=users_with_reports,
        users_with_2_reports=users_with_2_reports,
        users_used_ai=users_used_ai,
    )


@app.get("/admin/referrals/partners", response_model=list[ReferralPartnerOut])
def admin_list_referral_partners(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[ReferralPartnerOut]:
    partners = (
        db.query(ReferralPartner)
        .order_by(ReferralPartner.created_at.desc(), ReferralPartner.id.desc())
        .all()
    )
    return [
        ReferralPartnerOut(
            id=partner.id,
            name=partner.name,
            code=partner.code,
            type=partner.type,
            commission_type=partner.commission_type,
            commission_value=_decimal_to_float(partner.commission_value)
            if partner.commission_value is not None
            else None,
            status=partner.status,
            created_at=partner.created_at,
            updated_at=partner.updated_at,
        )
        for partner in partners
    ]


@app.post("/admin/referrals/partners", response_model=ReferralPartnerOut, status_code=201)
def admin_create_referral_partner(
    payload: ReferralPartnerCreateIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReferralPartnerOut:
    partner = _create_referral_partner(
        db,
        name=payload.name,
        code=payload.code,
        partner_type=payload.type,
        commission_type=payload.commission_type,
        commission_value=payload.commission_value,
        status=payload.status,
    )
    return ReferralPartnerOut(
        id=partner.id,
        name=partner.name,
        code=partner.code,
        type=partner.type,
        commission_type=partner.commission_type,
        commission_value=_decimal_to_float(partner.commission_value)
        if partner.commission_value is not None
        else None,
        status=partner.status,
        created_at=partner.created_at,
        updated_at=partner.updated_at,
    )


@app.get("/admin/referrals/summary", response_model=list[ReferralSummaryOut])
def admin_referrals_summary(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[ReferralSummaryOut]:
    return _build_referral_summary_rows(db)


@app.post("/admin/referrals/manual-conversion", response_model=ReferralConversionOut, status_code=201)
def admin_create_manual_referral_conversion_endpoint(
    payload: ReferralManualConversionIn,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> ReferralConversionOut:
    user = db.get(User, int(payload.user_id))
    if user is None:
        raise http_error(404, "user_not_found", "User not found.")
    conversion = create_manual_referral_conversion(
        db,
        user_id=user.id,
        conversion_type=payload.conversion_type,
        plan=payload.plan,
        amount=payload.amount,
        currency=payload.currency,
    )
    return ReferralConversionOut(
        id=conversion.id,
        user_id=conversion.user_id,
        referral_code=conversion.referral_code,
        conversion_type=conversion.conversion_type,
        plan=conversion.plan,
        amount=_decimal_to_float(conversion.amount) if conversion.amount is not None else None,
        currency=conversion.currency,
        commission_amount=_decimal_to_float(conversion.commission_amount)
        if conversion.commission_amount is not None
        else None,
        status=conversion.status,
        created_at=conversion.created_at,
    )


@app.get("/admin/cohorts", response_model=AdminCohortsOut)
def admin_cohorts(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
    timeframe: str = Query(default="all"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> AdminCohortsOut:
    resolved_timeframe = _resolve_admin_metrics_timeframe(timeframe, start_date, end_date)
    period_start = resolved_timeframe["start_dt"]
    period_end = resolved_timeframe["end_dt"]

    cohort_query = db.query(User).filter(User.is_deleted.is_(False))
    if period_start and period_end:
        cohort_query = cohort_query.filter(User.created_at >= period_start).filter(User.created_at <= period_end)
    cohort_users = cohort_query.all()

    cohort_dates_all = sorted({user.created_at.date() for user in cohort_users if user.created_at})
    cohort_dates = cohort_dates_all[-10:]
    users_by_signup_date: dict[date, list[User]] = {}
    for user in cohort_users:
        if not user.created_at:
            continue
        users_by_signup_date.setdefault(user.created_at.date(), []).append(user)

    cohort_user_ids = [
        user.id
        for signup_date in cohort_dates
        for user in users_by_signup_date.get(signup_date, [])
        if user.id is not None
    ]
    report_dates_by_user = _report_dates_by_user(
        db,
        user_ids=cohort_user_ids,
        start_dt=period_start,
        end_dt=period_end,
    )

    offsets = [0, 1, 3, 7, 14, 30]
    cohorts: list[AdminCohortOut] = []
    for signup_date in cohort_dates:
        users = users_by_signup_date.get(signup_date, [])
        cohort_size = len(users)
        retention = AdminCohortRetentionOut()
        if cohort_size <= 0:
            cohorts.append(
                AdminCohortOut(
                    date=signup_date,
                    size=0,
                    retention=retention,
                )
            )
            continue

        retention.day_0 = 100.0
        for offset in offsets[1:]:
            target_date = signup_date + timedelta(days=offset)
            retained_users = sum(
                1
                for user in users
                if target_date in report_dates_by_user.get(user.id, set())
            )
            setattr(retention, f"day_{offset}", round((retained_users / cohort_size) * 100.0, 2))

        cohorts.append(
            AdminCohortOut(
                date=signup_date,
                size=cohort_size,
                retention=retention,
            )
        )

    if cohorts:
        averages = AdminCohortAveragesOut(
            day_1=round(sum(item.retention.day_1 for item in cohorts) / len(cohorts), 2),
            day_3=round(sum(item.retention.day_3 for item in cohorts) / len(cohorts), 2),
            day_7=round(sum(item.retention.day_7 for item in cohorts) / len(cohorts), 2),
            day_14=round(sum(item.retention.day_14 for item in cohorts) / len(cohorts), 2),
            day_30=round(sum(item.retention.day_30 for item in cohorts) / len(cohorts), 2),
        )
    else:
        averages = AdminCohortAveragesOut()

    return AdminCohortsOut(cohorts=cohorts, averages=averages)


@app.get("/admin/insights", response_model=AdminInsightsOut)
def admin_insights(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> AdminInsightsOut:
    cutoff_7 = datetime.now(timezone.utc) - timedelta(days=7)
    onboarding_user_types = {"freelancer": 0, "agency": 0, "business": 0, "team": 0}
    onboarding_goals = {
        "track_growth": 0,
        "client_reports": 0,
        "fast_insights": 0,
        "improve_performance": 0,
        "understand_data": 0,
        "export_reports": 0,
        "automate_reports": 0,
    }
    onboarding_platforms = {
        "facebook": 0,
        "instagram": 0,
        "tiktok": 0,
        "google_analytics": 0,
        "shopify": 0,
        "meta_ads": 0,
        "google_ads": 0,
        "other": 0,
    }
    completed = 0
    pending = 0

    if user_onboarding_columns_available():
        try:
            users = db.query(User).filter(User.is_deleted.is_(False)).all()
            for user in users:
                if bool(getattr(user, "onboarding_completed", False)):
                    completed += 1
                else:
                    pending += 1
                user_type = str(getattr(user, "user_type", "") or "").strip()
                if user_type in onboarding_user_types:
                    onboarding_user_types[user_type] += 1
                for goal in _user_list_value(getattr(user, "goals", [])):
                    if goal in onboarding_goals:
                        onboarding_goals[goal] += 1
                for platform in _user_list_value(getattr(user, "platforms", [])):
                    if platform in onboarding_platforms:
                        onboarding_platforms[platform] += 1
        except SQLAlchemyError:
            completed = 0
            pending = 0

    completion_rate = round((completed / (completed + pending)) * 100.0, 2) if (completed + pending) else 0.0

    deletion_total = 0
    deletion_last_7_days = 0
    deletion_reasons = {
        "too_expensive": 0,
        "missing_features": 0,
        "hard_to_use": 0,
        "no_longer_needed": 0,
        "switching_tool": 0,
        "privacy_concerns": 0,
        "other": 0,
    }
    recent_feedback: list[AdminDeletionFeedbackOut] = []
    if _table_available("account_deletion_feedback"):
        try:
            feedback_rows = (
                db.query(AccountDeletionFeedback)
                .order_by(AccountDeletionFeedback.created_at.desc(), AccountDeletionFeedback.id.desc())
                .all()
            )
            deletion_total = len(feedback_rows)
            for feedback in feedback_rows:
                created_at = feedback.created_at
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at and created_at >= cutoff_7:
                    deletion_last_7_days += 1
                reason = str(feedback.reason or "other").strip()
                if reason not in deletion_reasons:
                    reason = "other"
                deletion_reasons[reason] += 1
            recent_feedback = [
                AdminDeletionFeedbackOut(
                    email=row.email,
                    reason=row.reason,
                    details=row.details,
                    created_at=row.created_at,
                )
                for row in feedback_rows[:10]
            ]
        except SQLAlchemyError:
            deletion_total = 0
            deletion_last_7_days = 0

    return AdminInsightsOut(
        onboarding=AdminOnboardingInsightsOut(
            user_types=AdminOnboardingCountsOut(**onboarding_user_types),
            goals=AdminGoalCountsOut(**onboarding_goals),
            platforms=AdminPlatformCountsOut(**onboarding_platforms),
            completed=completed,
            pending=pending,
            completion_rate=completion_rate,
        ),
        deletions=AdminDeletionInsightsOut(
            total=deletion_total,
            last_7_days=deletion_last_7_days,
            reasons=AdminDeletionReasonCountsOut(**deletion_reasons),
            recent_feedback=recent_feedback,
        ),
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
    return _me_out(db, current_user)


@app.patch("/me/workspace", response_model=WorkspaceOut)
def update_my_workspace_account_display_name(
    payload: WorkspaceUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace, _subscription = _find_user_workspace_and_subscription(db, user_id=current_user.id)
    if workspace is None:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")
    _update_workspace_from_payload(workspace, payload)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _workspace_out(db, workspace)


@app.patch("/workspace", response_model=WorkspaceOut)
def patch_current_workspace(
    payload: WorkspaceUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    return update_my_workspace_account_display_name(payload=payload, current_user=current_user, db=db)


@app.patch("/workspace/branding", response_model=WorkspaceOut)
def patch_current_workspace_branding(
    payload: WorkspaceBrandingUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace, _subscription = _find_user_workspace_and_subscription(db, user_id=current_user.id)
    if workspace is None:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")
    return update_workspace_branding(
        workspace_id=workspace.id,
        payload=payload,
        current_user=current_user,
        db=db,
    )


@app.post("/workspace/branding/logo", response_model=WorkspaceBrandingLogoUploadOut)
def upload_workspace_branding_logo(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceBrandingLogoUploadOut:
    workspace, _subscription = _find_user_workspace_and_subscription(db, user_id=current_user.id)
    if workspace is None:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")
    _require_workspace_access(db, current_user.id, workspace.id)

    size_bytes, extension, content_type = _validate_brand_logo_upload(file)
    storage_key, asset_name = _workspace_brand_logo_storage_key(workspace.id, extension)
    s3 = boto3.client("s3", region_name=settings.aws_region)

    try:
        file.file.seek(0)
        payload = file.file.read()
        s3.put_object(
            Bucket=settings.s3_outputs_bucket,
            Key=storage_key,
            Body=payload,
            ContentType=content_type,
            CacheControl="public, max-age=300",
        )
    except Exception as exc:
        logger.exception(
            "[BrandAssets][logo.upload_failed]",
            extra={
                "endpoint": "/workspace/branding/logo",
                "workspace_id": workspace.id,
                "upload_filename": file.filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
            },
        )
        raise http_error(502, "brand_logo_upload_failed", "Failed to upload brand logo.") from exc

    logo_url = _workspace_brand_logo_public_url(
        workspace.id,
        asset_name,
        base_url_override=str(request.base_url).rstrip("/"),
    )
    logger.info(
        "[BrandAssets][logo.uploaded]",
        extra={
            "endpoint": "/workspace/branding/logo",
            "workspace_id": workspace.id,
            "upload_filename": file.filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage_key": storage_key,
            "logo_url": logo_url,
        },
    )
    return WorkspaceBrandingLogoUploadOut(logo_url=logo_url)


@app.get("/workspace/branding/logo/{workspace_id}/{asset_name}")
def get_workspace_branding_logo(
    workspace_id: int,
    asset_name: str,
) -> Response:
    sanitized_asset_name = os.path.basename(str(asset_name or "").strip())
    if not sanitized_asset_name:
        raise http_error(404, "brand_logo_not_found", "Brand logo not found.")

    storage_key = f"brand-assets/{workspace_id}/{sanitized_asset_name}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        result = s3.get_object(Bucket=settings.s3_outputs_bucket, Key=storage_key)
    except (ClientError, BotoCoreError, NoCredentialsError, PartialCredentialsError) as exc:
        logger.warning(
            "[BrandAssets][logo.fetch_failed]",
            extra={
                "endpoint": "/workspace/branding/logo/{workspace_id}/{asset_name}",
                "workspace_id": workspace_id,
                "asset_name": sanitized_asset_name,
                "storage_key": storage_key,
            },
        )
        raise http_error(404, "brand_logo_not_found", "Brand logo not found.") from exc

    body = _read_s3_object_bytes(result.get("Body"))
    media_type = str(result.get("ContentType") or "application/octet-stream")
    headers = {}
    cache_control = result.get("CacheControl")
    if cache_control:
        headers["Cache-Control"] = str(cache_control)
    return Response(content=body, media_type=media_type, headers=headers)


@app.post("/ai/chat", response_model=ChatReplyOut)
def ai_chat(
    payload: ChatMessageIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatReplyOut:
    message_text = payload.message.strip()
    if not message_text:
        raise http_error(400, "invalid_message", "Message is required.")

    logger.info(
        "ai_chat_request_received",
        extra={
            "user_id": current_user.id,
            "workspace_id": payload.workspace_id,
            "report_id": payload.report_id,
            "dataset_id": payload.dataset_id,
            "conversation_id": payload.conversation_id,
            "current_route": payload.current_route,
            "message_length": len(message_text),
        },
    )

    workspace_id: int | None = None
    report: Report | None = None
    dataset: Dataset | None = None
    existing_conversation: Conversation | None = None

    if payload.conversation_id is not None:
        candidate_conversation = db.get(Conversation, int(payload.conversation_id))
        if candidate_conversation is None:
            raise http_error(404, "conversation_not_found", "Conversation not found.")
        _require_workspace_access(db, current_user.id, candidate_conversation.workspace_id)
        existing_conversation = candidate_conversation
        workspace_id = candidate_conversation.workspace_id

    if payload.report_id is not None:
        report = db.get(Report, int(payload.report_id))
        if report is None:
            raise http_error(404, "report_not_found", "Report not found.")
        _require_workspace_access(db, current_user.id, report.workspace_id)
        if workspace_id is not None and workspace_id != report.workspace_id:
            raise http_error(403, "invalid_workspace_context", "report_id does not belong to conversation workspace.")
        workspace_id = report.workspace_id
        if payload.workspace_id is not None and int(payload.workspace_id) != workspace_id:
            raise http_error(403, "invalid_workspace_context", "report_id does not belong to workspace_id.")
        dataset = db.get(Dataset, report.dataset_id)
        if dataset is None:
            raise http_error(404, "dataset_not_found", "No dataset found for this report.")
        logger.info(
            "ai_chat_report_context_resolved",
            extra={
                "user_id": current_user.id,
                "payload_report_id": payload.report_id,
                "payload_dataset_id": payload.dataset_id,
                "resolved_report_id": report.id,
                "resolved_report_dataset_id": report.dataset_id,
                "resolved_dataset_id": dataset.id,
                "workspace_id": workspace_id,
            },
        )
    elif payload.dataset_id is not None:
        dataset = db.get(Dataset, int(payload.dataset_id))
        if dataset is None:
            raise http_error(404, "dataset_not_found", "Dataset not found.")
        _require_workspace_access(db, current_user.id, dataset.workspace_id)
        if workspace_id is not None and workspace_id != dataset.workspace_id:
            raise http_error(403, "invalid_workspace_context", "dataset_id does not belong to conversation workspace.")
        workspace_id = dataset.workspace_id
        if payload.workspace_id is not None and int(payload.workspace_id) != workspace_id:
            raise http_error(403, "invalid_workspace_context", "dataset_id does not belong to workspace_id.")
        report = (
            db.query(Report)
            .filter(Report.workspace_id == workspace_id, Report.dataset_id == dataset.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .first()
        )
    elif workspace_id is None:
        accessible_workspace_ids = [
            row[0]
            for row in (
                db.query(WorkspaceMember.workspace_id)
                .filter(WorkspaceMember.user_id == current_user.id)
                .order_by(WorkspaceMember.workspace_id.asc())
                .all()
            )
        ]
        if not accessible_workspace_ids:
            raise http_error(404, "workspace_not_found", "No workspace found for current user.")

        workspace_id = None
        if payload.workspace_id is None:
            report = (
                db.query(Report)
                .filter(Report.workspace_id.in_(accessible_workspace_ids))
                .order_by(Report.created_at.desc(), Report.id.desc())
                .first()
            )
            if report is not None:
                workspace_id = report.workspace_id
                dataset = db.get(Dataset, report.dataset_id)
            else:
                dataset = (
                    db.query(Dataset)
                    .filter(Dataset.workspace_id.in_(accessible_workspace_ids))
                    .order_by(Dataset.created_at.desc(), Dataset.id.desc())
                    .first()
                )
                if dataset is not None:
                    workspace_id = dataset.workspace_id
                else:
                    workspace_id = int(accessible_workspace_ids[0])
        else:
            workspace_id = _resolve_workspace_id(db, current_user.id, payload.workspace_id)
            _require_workspace_access(db, current_user.id, workspace_id)
            report = (
                db.query(Report)
                .filter(Report.workspace_id == workspace_id)
                .order_by(Report.created_at.desc(), Report.id.desc())
                .first()
            )
            if report is not None:
                dataset = db.get(Dataset, report.dataset_id)
            else:
                dataset = (
                    db.query(Dataset)
                    .filter(Dataset.workspace_id == workspace_id)
                    .order_by(Dataset.created_at.desc(), Dataset.id.desc())
                    .first()
                )

    if workspace_id is None:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")

    if existing_conversation is not None and report is None and dataset is None:
        report = (
            db.query(Report)
            .filter(Report.workspace_id == workspace_id)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .first()
        )
        if report is not None:
            dataset = db.get(Dataset, report.dataset_id)
        else:
            dataset = (
                db.query(Dataset)
                .filter(Dataset.workspace_id == workspace_id)
                .order_by(Dataset.created_at.desc(), Dataset.id.desc())
                .first()
            )

    chat_context = build_ai_chat_context_snapshot(
        db,
        workspace_id=workspace_id,
        report=report,
        dataset=dataset,
        current_route=payload.current_route,
        page_context=payload.page_context,
    )
    logger.info(
        "ai_chat_context_loaded",
        extra={
            "workspace_id": workspace_id,
            "conversation_workspace_id": existing_conversation.workspace_id if existing_conversation else None,
            "report_id": report.id if report else None,
            "dataset_id": dataset.id if dataset else None,
            "has_report": bool(report),
            "has_dataset": bool(dataset),
            "report_blocks_count": len(chat_context["report"]["blocks"]) if isinstance(chat_context.get("report"), dict) else 0,
            "datasets_count": chat_context.get("workspace", {}).get("snapshot", {}).get("datasets_count")
            if isinstance(chat_context.get("workspace"), dict)
            else None,
        },
    )

    if payload.conversation_id is None:
        conversation = Conversation(
            workspace_id=workspace_id,
            title=build_conversation_title(message_text),
        )
        db.add(conversation)
        db.flush()
    else:
        conversation = existing_conversation if existing_conversation is not None else _get_conversation_for_workspace(
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
            chat_context=chat_context,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception(
            "ai_chat_generation_failed",
            extra={
                "user_id": current_user.id,
                "workspace_id": workspace_id,
                "conversation_id": conversation.id if 'conversation' in locals() else payload.conversation_id,
                "report_id": report.id if report else None,
                "dataset_id": dataset.id if dataset else None,
                "exception_type": exc.__class__.__name__,
            },
        )
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
MAX_BRAND_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_BRAND_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
ALLOWED_BRAND_LOGO_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
    "image/svg",
}
META_TOKEN_ACCOUNT_PREFIX = "__meta_token__:"
META_PAGE_ACCOUNT_PREFIX = "__meta_page__:"
META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
META_RECORD_TYPE_INSTAGRAM_ACCOUNT = "instagram_account"
TIKTOK_TOKEN_ACCOUNT_PREFIX = "__tiktok_token__:"
TIKTOK_SELECTED_ADVERTISER_PREFIX = "__tiktok_selected__:"
TIKTOK_OAUTH_STATE_SOURCE = "tiktok_ads_connect"


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


def _backend_public_base_url() -> str:
    configured = str(settings.report_export_base_url or "").strip().rstrip("/")
    return configured


def _workspace_brand_logo_public_path(workspace_id: int, asset_name: str) -> str:
    return f"/workspace/branding/logo/{workspace_id}/{asset_name}"


def _workspace_brand_logo_public_url(
    workspace_id: int,
    asset_name: str,
    *,
    base_url_override: str | None = None,
) -> str:
    path = _workspace_brand_logo_public_path(workspace_id, asset_name)
    base_url = str(base_url_override or "").strip().rstrip("/") or _backend_public_base_url()
    if not base_url:
        return path
    return f"{base_url}{path}"


def _validate_brand_logo_upload(file: UploadFile) -> tuple[int, str, str]:
    if not file.filename:
        raise http_error(400, "missing_filename", "File name is required.")
    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ALLOWED_BRAND_LOGO_EXTENSIONS:
        raise http_error(
            400,
            "invalid_brand_logo_type",
            "Only PNG, JPG, JPEG, WEBP, or SVG files are allowed.",
        )
    content_type = str(file.content_type or "").strip().lower()
    if content_type and content_type not in ALLOWED_BRAND_LOGO_CONTENT_TYPES:
        raise http_error(
            400,
            "invalid_brand_logo_type",
            "Only PNG, JPG, JPEG, WEBP, or SVG files are allowed.",
        )
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        raise http_error(400, "empty_file", "File is empty.")
    if size > MAX_BRAND_LOGO_UPLOAD_BYTES:
        raise http_error(413, "brand_logo_too_large", "Brand logo exceeds 5 MB limit.")
    normalized_content_type = content_type or {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }[extension]
    return size, extension, normalized_content_type


def _workspace_brand_logo_storage_key(workspace_id: int, extension: str) -> tuple[str, str]:
    asset_name = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-"
        f"{secrets.token_hex(8)}{extension}"
    )
    return f"brand-assets/{workspace_id}/{asset_name}", asset_name


def _read_s3_object_bytes(body: Any) -> bytes:
    if hasattr(body, "read"):
        return body.read()
    if isinstance(body, bytes):
        return body
    return bytes(body)


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


def _require_workspace_owner(db: Session, user_id: int, workspace_id: int) -> WorkspaceMember:
    membership = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user_id, WorkspaceMember.workspace_id == workspace_id)
        .first()
    )
    if not membership or str(membership.role or "").strip().lower() != "owner":
        raise http_error(403, "forbidden", "Only workspace owners can delete reports.")
    return membership


def _workspace_ids_for_user(db: Session, user_id: int) -> list[int]:
    memberships = (
        db.query(WorkspaceMember.workspace_id)
        .filter(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.workspace_id.asc())
        .all()
    )
    return [int(row[0]) for row in memberships]


def _resolve_workspace_id(db: Session, user_id: int, workspace_id: int | None) -> int:
    if workspace_id is not None:
        return workspace_id

    workspace_ids = _workspace_ids_for_user(db, user_id)

    if not workspace_ids:
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")
    if len(workspace_ids) > 1:
        raise http_error(
            400,
            "workspace_id_required",
            "workspace_id is required when the user belongs to multiple workspaces.",
        )
    return int(workspace_ids[0])


def _report_delete_asset_keys(report: Report, exports: list[Export]) -> list[str]:
    metadata = _report_metadata(report)
    keys: list[str] = []
    thumbnail_key = metadata.get("thumbnail_s3_key") if isinstance(metadata, dict) else None
    if thumbnail_key:
        keys.append(str(thumbnail_key))
    for export in exports:
        for candidate in (export.output_s3_key, export.download_key):
            if candidate:
                keys.append(str(candidate))
    return list(dict.fromkeys(keys))


def _cleanup_report_assets(report_id: int, asset_keys: list[str]) -> None:
    if not asset_keys:
        return
    s3 = boto3.client("s3", region_name=settings.aws_region)
    for key in asset_keys:
        try:
            s3.delete_object(Bucket=settings.s3_outputs_bucket, Key=key)
            logger.info(
                "report_delete_asset_removed",
                extra={"report_id": report_id, "bucket": settings.s3_outputs_bucket, "s3_key": key},
            )
        except (NoCredentialsError, PartialCredentialsError, BotoCoreError, ClientError):
            logger.exception(
                "report_delete_asset_cleanup_failed",
                extra={"report_id": report_id, "bucket": settings.s3_outputs_bucket, "s3_key": key},
            )


def _resolve_meta_connect_workspace_id(
    db: Session,
    *,
    user_id: int,
    requested_workspace_id: int | None,
) -> int:
    workspace_ids = _workspace_ids_for_user(db, user_id)

    if not workspace_ids:
        logger.warning(
            "meta_connect_pages_workspace_access_denied",
            extra={
                "user_id": user_id,
                "workspace_id_received": requested_workspace_id,
                "workspace_ids_available": workspace_ids,
                "reason": "user_has_no_workspaces",
            },
        )
        raise http_error(404, "workspace_not_found", "No workspace found for current user.")

    if requested_workspace_id is None:
        if len(workspace_ids) == 1:
            return workspace_ids[0]
        logger.warning(
            "meta_connect_pages_workspace_access_denied",
            extra={
                "user_id": user_id,
                "workspace_id_received": requested_workspace_id,
                "workspace_ids_available": workspace_ids,
                "reason": "workspace_id_missing_with_multiple_workspaces",
            },
        )
        raise http_error(
            403,
            "workspace_access_denied",
            "workspace_id is required when the user belongs to multiple workspaces.",
        )

    if requested_workspace_id in workspace_ids:
        return requested_workspace_id

    if len(workspace_ids) == 1:
        logger.info(
            "meta_connect_pages_workspace_fallback",
            extra={
                "user_id": user_id,
                "workspace_id_received": requested_workspace_id,
                "workspace_ids_available": workspace_ids,
                "reason": "requested_workspace_not_accessible_using_single_available_workspace",
                "resolved_workspace_id": workspace_ids[0],
            },
        )
        return workspace_ids[0]

    logger.warning(
        "meta_connect_pages_workspace_access_denied",
        extra={
            "user_id": user_id,
            "workspace_id_received": requested_workspace_id,
            "workspace_ids_available": workspace_ids,
            "reason": "requested_workspace_not_accessible",
        },
    )
    raise http_error(
        403,
        "workspace_access_denied",
        "Requested workspace does not belong to the authenticated user.",
    )


def _tiktok_token_account_external_id(integration_id: int) -> str:
    return f"{TIKTOK_TOKEN_ACCOUNT_PREFIX}{integration_id}"


def _tiktok_selected_advertiser_external_id(advertiser_id: str) -> str:
    return f"{TIKTOK_SELECTED_ADVERTISER_PREFIX}{advertiser_id}"


def _parse_iso_date_or_400(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise http_error(422, "invalid_date", f"{field_name} must be in YYYY-MM-DD format.") from exc


def _resolve_tiktok_workspace_id(
    db: Session,
    *,
    user_id: int,
    workspace_id: int | None,
) -> int:
    return _resolve_meta_connect_workspace_id(
        db,
        user_id=user_id,
        requested_workspace_id=workspace_id,
    )


def _get_or_create_tiktok_integration_for_workspace(db: Session, workspace_id: int) -> Integration:
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "tiktok_ads")
        .order_by(Integration.id.asc())
        .first()
    )
    if integration is not None:
        return integration

    integration = Integration(
        workspace_id=workspace_id,
        provider="tiktok_ads",
        name="TikTok Ads",
        status="disconnected",
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    logger.info(
        "TikTok integration created",
        extra={"workspace_id": workspace_id, "integration_id": integration.id},
    )
    return integration


def _resolve_tiktok_integration(
    db: Session,
    *,
    current_user: User,
    integration_id: int | None,
    workspace_id: int | None,
) -> Integration:
    if integration_id is not None:
        integration = db.get(Integration, int(integration_id))
        if integration is None or integration.provider != "tiktok_ads":
            raise http_error(404, "integration_not_found", "TikTok integration not found.")
        _require_workspace_access(db, current_user.id, integration.workspace_id)
        return integration

    resolved_workspace_id = _resolve_tiktok_workspace_id(
        db,
        user_id=current_user.id,
        workspace_id=workspace_id,
    )
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    return _get_or_create_tiktok_integration_for_workspace(db, resolved_workspace_id)


def _get_tiktok_token_account(db: Session, integration_id: int) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id == _tiktok_token_account_external_id(integration_id),
        )
        .first()
    )


def _get_or_create_tiktok_token_account(db: Session, integration: Integration) -> IntegrationAccount:
    token_account = _get_tiktok_token_account(db, integration.id)
    if token_account is not None:
        return token_account

    token_account = IntegrationAccount(
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        external_account_id=_tiktok_token_account_external_id(integration.id),
        display_name="TikTok token store",
    )
    db.add(token_account)
    db.commit()
    db.refresh(token_account)
    return token_account


def _replace_integration_token_with_refresh(
    db: Session,
    *,
    account_id: int,
    workspace_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> IntegrationToken:
    existing_tokens = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .all()
    )
    token = existing_tokens[0] if existing_tokens else None
    if token is None:
        token = IntegrationToken(
            account_id=account_id,
            workspace_id=workspace_id,
            token_type="access_token",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        db.add(token)
    else:
        token.token_type = "access_token"
        token.access_token = access_token
        token.refresh_token = refresh_token
        token.expires_at = expires_at
        db.add(token)
        for stale_token in existing_tokens[1:]:
            db.delete(stale_token)
    db.commit()
    db.refresh(token)
    return token


def _get_tiktok_access_token(db: Session, integration: Integration) -> str:
    if str(integration.status or "").strip().lower() != "connected":
        raise http_error(401, "missing_token", "TikTok token not found.")
    token_account = _get_tiktok_token_account(db, integration.id)
    if token_account is None:
        raise http_error(401, "missing_token", "TikTok token not found.")
    token = _get_latest_integration_token(db, token_account.id)
    if token is None or not str(token.access_token or "").strip():
        raise http_error(401, "missing_token", "TikTok token not found.")
    return str(token.access_token).strip()


def _store_tiktok_advertisers(
    db: Session,
    *,
    integration: Integration,
    advertisers: list[dict[str, Any]],
) -> list[IntegrationAccount]:
    existing_accounts = (
        db.query(IntegrationAccount)
        .filter(IntegrationAccount.integration_id == integration.id)
        .all()
    )
    helper_external_ids = {
        _tiktok_token_account_external_id(integration.id),
    }
    selected_account = next(
        (
            account for account in existing_accounts
            if str(account.external_account_id).startswith(TIKTOK_SELECTED_ADVERTISER_PREFIX)
        ),
        None,
    )
    if selected_account is not None:
        helper_external_ids.add(selected_account.external_account_id)

    advertiser_ids = {
        str(item.get("advertiser_id") or "").strip()
        for item in advertisers
        if str(item.get("advertiser_id") or "").strip()
    }
    for account in existing_accounts:
        if account.external_account_id in helper_external_ids:
            continue
        if account.external_account_id not in advertiser_ids:
            db.delete(account)

    stored_accounts: list[IntegrationAccount] = []
    for advertiser in advertisers:
        advertiser_id = str(advertiser.get("advertiser_id") or "").strip()
        if not advertiser_id:
            continue
        advertiser_name = str(advertiser.get("advertiser_name") or advertiser_id).strip()
        account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id == advertiser_id,
            )
            .first()
        )
        if account is None:
            account = IntegrationAccount(
                integration_id=integration.id,
                workspace_id=integration.workspace_id,
                external_account_id=advertiser_id,
                display_name=advertiser_name,
            )
            db.add(account)
        else:
            account.display_name = advertiser_name
            db.add(account)
        stored_accounts.append(account)

    db.commit()
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id.notlike(f"{TIKTOK_TOKEN_ACCOUNT_PREFIX}%"),
            IntegrationAccount.external_account_id.notlike(f"{TIKTOK_SELECTED_ADVERTISER_PREFIX}%"),
        )
        .order_by(IntegrationAccount.display_name.asc(), IntegrationAccount.external_account_id.asc())
        .all()
    )


def _get_tiktok_selected_advertiser_marker(
    db: Session,
    integration_id: int,
) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id.like(f"{TIKTOK_SELECTED_ADVERTISER_PREFIX}%"),
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )


def _list_tiktok_advertiser_accounts(db: Session, integration_id: int) -> list[IntegrationAccount]:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id.notlike(f"{TIKTOK_TOKEN_ACCOUNT_PREFIX}%"),
            IntegrationAccount.external_account_id.notlike(f"{TIKTOK_SELECTED_ADVERTISER_PREFIX}%"),
        )
        .order_by(IntegrationAccount.display_name.asc(), IntegrationAccount.external_account_id.asc())
        .all()
    )


def _select_tiktok_advertiser_account(
    db: Session,
    *,
    integration: Integration,
    advertiser_id: str,
) -> IntegrationAccount:
    normalized_advertiser_id = str(advertiser_id or "").strip()
    if not normalized_advertiser_id:
        raise http_error(400, "missing_advertiser_id", "advertiser_id is required.")

    advertiser_account = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == normalized_advertiser_id,
        )
        .first()
    )
    if advertiser_account is None:
        raise http_error(404, "advertiser_not_found", "TikTok advertiser account not found.")

    existing_marker = _get_tiktok_selected_advertiser_marker(db, integration.id)
    target_external_id = _tiktok_selected_advertiser_external_id(normalized_advertiser_id)
    if existing_marker is None:
        existing_marker = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=target_external_id,
            display_name=advertiser_account.display_name,
        )
        db.add(existing_marker)
    else:
        existing_marker.external_account_id = target_external_id
        existing_marker.display_name = advertiser_account.display_name
        db.add(existing_marker)
    db.commit()
    return advertiser_account


def _get_selected_tiktok_advertiser_account(
    db: Session,
    integration: Integration,
) -> IntegrationAccount | None:
    marker = _get_tiktok_selected_advertiser_marker(db, integration.id)
    if marker is None:
        return None
    advertiser_id = marker.external_account_id.removeprefix(TIKTOK_SELECTED_ADVERTISER_PREFIX)
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == advertiser_id,
        )
        .first()
    )


def _tiktok_account_last_synced_at(
    db: Session,
    *,
    workspace_id: int,
    advertiser_id: str | None,
) -> datetime | None:
    try:
        datasets = (
            db.query(Dataset)
            .filter(Dataset.workspace_id == workspace_id)
            .order_by(Dataset.updated_at.desc(), Dataset.id.desc())
            .limit(200)
            .all()
        )
    except SQLAlchemyError:
        return None
    for dataset in datasets:
        dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
        if str(dataset_data.get("integration_type") or "").strip() != "tiktok_ads":
            continue
        dataset_account_id = str(dataset_data.get("account_id") or dataset_data.get("page_id") or "").strip()
        if advertiser_id is not None and dataset_account_id != advertiser_id:
            continue
        return dataset.updated_at
    return None


def _tiktok_advertiser_out(
    db: Session,
    *,
    integration: Integration,
    account: IntegrationAccount,
    selected_advertiser_id: str | None,
) -> TikTokAdvertiserAccountOut:
    advertiser_id = str(account.external_account_id or "").strip()
    return TikTokAdvertiserAccountOut(
        advertiser_id=advertiser_id,
        advertiser_name=str(account.display_name or advertiser_id),
        selected=advertiser_id == selected_advertiser_id,
        last_synced_at=_tiktok_account_last_synced_at(
            db,
            workspace_id=integration.workspace_id,
            advertiser_id=advertiser_id,
        ),
    )


def _disconnect_tiktok_integration(db: Session, integration: Integration) -> TikTokDisconnectOut:
    integration_accounts = (
        db.query(IntegrationAccount)
        .filter(IntegrationAccount.integration_id == integration.id)
        .all()
    )
    token_account_ids = [
        account.id
        for account in integration_accounts
        if account.external_account_id == _tiktok_token_account_external_id(integration.id)
    ]
    selected_account_cleared = any(
        account.external_account_id.startswith(TIKTOK_SELECTED_ADVERTISER_PREFIX)
        for account in integration_accounts
    )
    tokens = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id.in_(token_account_ids))
        .all()
        if token_account_ids
        else []
    )
    for token in tokens:
        db.delete(token)
    for account in integration_accounts:
        if account.external_account_id.startswith(TIKTOK_SELECTED_ADVERTISER_PREFIX):
            db.delete(account)
    integration.status = "disconnected"
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return TikTokDisconnectOut(
        advertisers_cleared=0,
        selected_account_cleared=selected_account_cleared,
        tokens_cleared=bool(tokens),
    )


def _require_pro_plan(db: Session, workspace_id: int) -> None:
    if not can_schedule_report(db, workspace_id):
        raise http_error(403, "plan_required", "Current plan does not allow more scheduled reports.")


def _meta_token_account_external_id(integration_id: int) -> str:
    return f"{META_TOKEN_ACCOUNT_PREFIX}{integration_id}"


def _instagram_business_token_account_external_id(integration_id: int) -> str:
    return f"instagram_business_token_{integration_id}"


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


def _get_or_create_meta_ads_integration_for_workspace(db: Session, workspace_id: int) -> Integration:
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "meta_ads")
        .order_by(Integration.id.asc())
        .first()
    )
    if integration:
        return integration

    integration = Integration(
        workspace_id=workspace_id,
        provider="meta_ads",
        name="Meta Ads",
        status="disconnected",
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return integration


def _get_or_create_instagram_business_integration_for_workspace(db: Session, workspace_id: int) -> Integration:
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "instagram_business")
        .order_by(Integration.id.asc())
        .first()
    )
    if integration:
        return integration

    integration = Integration(
        workspace_id=workspace_id,
        provider="instagram_business",
        name="Instagram Business",
        status="disconnected",
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return integration


def _get_meta_ads_integration(
    db: Session,
    current_user: User,
    integration_id: int,
    *,
    require_access: bool = True,
) -> Integration:
    integration = db.get(Integration, integration_id)
    if not integration or integration.provider != "meta_ads":
        raise http_error(404, "integration_not_found", "Meta Ads integration not found.")
    if require_access:
        _require_workspace_access(db, current_user.id, integration.workspace_id)
    return integration


def _get_meta_ads_token_account(db: Session, integration_id: int) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id == _meta_token_account_external_id(integration_id),
        )
        .first()
    )


def _ensure_meta_ads_token_account(db: Session, integration: Integration) -> IntegrationAccount:
    token_account = _get_meta_ads_token_account(db, integration.id)
    if token_account is not None:
        return token_account

    token_account = IntegrationAccount(
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        external_account_id=_meta_token_account_external_id(integration.id),
        display_name="Meta Ads token store",
    )
    db.add(token_account)
    db.commit()
    db.refresh(token_account)
    return token_account


def _get_meta_ads_access_token(db: Session, integration: Integration) -> str:
    token_account = _get_meta_ads_token_account(db, integration.id)
    if token_account is None:
        raise http_error(401, "missing_token", "Meta Ads token not found.")
    latest_token = _get_latest_integration_token(db, token_account.id)
    if latest_token is None or not str(latest_token.access_token or "").strip():
        raise http_error(401, "missing_token", "Meta Ads token not found.")
    return str(latest_token.access_token)


def _meta_ads_account_out(record: MetaAdAccount, *, source: str | None = None) -> MetaAdsAccountOut:
    return MetaAdsAccountOut(
        id=record.id,
        account_id=record.account_id,
        name=record.account_name,
        currency=record.currency,
        timezone_name=record.timezone_name,
        account_status=record.account_status,
        business_id=record.business_id,
        business_name=record.business_name,
        is_selected=record.is_selected,
        last_synced_at=record.last_synced_at,
        source=source,
    )


def _upsert_meta_ads_account(
    db: Session,
    *,
    integration: Integration,
    account_payload: dict[str, Any],
    is_selected: bool | None = None,
) -> MetaAdAccount:
    raw_account_id = str(account_payload.get("account_id") or account_payload.get("id") or "").strip()
    normalized_account_id = _normalize_meta_ad_account_id(raw_account_id)
    if not normalized_account_id:
        raise http_error(400, "invalid_ad_account", "Meta Ads account id is required.")

    business_payload = account_payload.get("business") if isinstance(account_payload.get("business"), dict) else {}
    record = (
        db.query(MetaAdAccount)
        .filter(
            MetaAdAccount.integration_id == integration.id,
            MetaAdAccount.account_id == normalized_account_id,
        )
        .first()
    )
    if record is None:
        record = MetaAdAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            account_id=normalized_account_id,
            account_name=str(account_payload.get("name") or normalized_account_id),
        )
        db.add(record)

    record.account_name = str(account_payload.get("name") or record.account_name or normalized_account_id)
    record.currency = str(account_payload.get("currency") or "").strip() or None
    record.timezone_name = str(account_payload.get("timezone_name") or "").strip() or None
    account_status = account_payload.get("account_status")
    record.account_status = str(account_status).strip() if account_status not in (None, "") else None
    record.business_id = str(business_payload.get("id") or "").strip() or None
    record.business_name = str(business_payload.get("name") or "").strip() or None
    if is_selected is not None:
        record.is_selected = bool(is_selected)
    db.add(record)
    db.flush()
    return record


def _save_meta_ads_selected_account(
    db: Session,
    *,
    integration: Integration,
    account_payload: dict[str, Any],
) -> MetaAdAccount:
    existing_accounts = (
        db.query(MetaAdAccount)
        .filter(MetaAdAccount.integration_id == integration.id)
        .all()
    )
    for account in existing_accounts:
        account.is_selected = False
        db.add(account)
    record = _upsert_meta_ads_account(
        db,
        integration=integration,
        account_payload=account_payload,
        is_selected=True,
    )
    db.commit()
    db.refresh(record)
    return record


def _get_selected_meta_ads_account(db: Session, integration_id: int) -> MetaAdAccount | None:
    return (
        db.query(MetaAdAccount)
        .filter(
            MetaAdAccount.integration_id == integration_id,
            MetaAdAccount.is_selected.is_(True),
        )
        .order_by(MetaAdAccount.updated_at.desc(), MetaAdAccount.id.desc())
        .first()
    )


def _meta_ads_find_account_payload(accounts: list[dict[str, Any]], ad_account_id: str) -> dict[str, Any] | None:
    requested_id = _normalize_meta_ad_account_id(ad_account_id)
    for account in accounts:
        account_ids = {
            _normalize_meta_ad_account_id(str(account.get("id") or "")),
            _normalize_meta_ad_account_id(str(account.get("account_id") or "")),
        }
        if requested_id in account_ids:
            return account
    return None


def _meta_ads_status_message(*, status: str, connected: bool, accounts_count: int) -> str | None:
    if not connected:
        return "Connect Meta Ads to load ad accounts and sync reporting data."
    if status == "reauthorization_required":
        return "Reconnect Meta Ads to refresh permissions or access."
    if accounts_count == 0:
        return "No ad accounts found for this Meta Ads connection."
    return None


def _disconnect_meta_ads_integration(
    db: Session,
    integration: Integration,
    *,
    revoke_permissions: bool = True,
) -> MetaAdsDisconnectOut:
    token_account = _get_meta_ads_token_account(db, integration.id)
    token = _get_latest_integration_token(db, token_account.id) if token_account is not None else None
    accounts = db.query(MetaAdAccount).filter(MetaAdAccount.integration_id == integration.id).all()
    account_ids = [account.id for account in accounts]
    rows = (
        db.query(MetaAdsInsightDaily)
        .filter(MetaAdsInsightDaily.integration_id == integration.id)
        .all()
    )
    cleared_accounts = len(accounts)
    cleared_rows = len(rows)

    token_revoked = False
    if revoke_permissions and token is not None and str(token.access_token or "").strip():
        token_revoked = _revoke_meta_permissions(token.access_token) == "success"

    for row in rows:
        db.delete(row)
    for account in accounts:
        db.delete(account)
    if token_account is not None:
        db.delete(token_account)
    integration.status = "disconnected"
    db.add(integration)
    db.commit()
    db.refresh(integration)

    return MetaAdsDisconnectOut(
        cleared_accounts=cleared_accounts,
        cleared_rows=cleared_rows,
        token_revoked=token_revoked,
    )


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


def _revoke_meta_permissions(access_token: str) -> str:
    normalized_token = str(access_token or "").strip()
    if not normalized_token:
        return "skipped"
    revoke_url = f"https://graph.facebook.com/{settings.meta_api_version}/me/permissions"
    try:
        response = requests.delete(
            revoke_url,
            params={"access_token": normalized_token},
            timeout=15,
        )
        if response.ok:
            return "success"
        logger.warning(
            "[META_DISCONNECT_REVOKE_FAILED]",
            extra={
                "status_code": response.status_code,
                "response_body": str(response.text or "")[:1000] or None,
            },
        )
    except requests.RequestException as exc:
        logger.warning(
            "[META_DISCONNECT_REVOKE_FAILED]",
            extra={"error": str(exc)},
        )
    return "failed"


def _resolve_meta_disconnect_integration(
    db: Session,
    current_user: User,
    *,
    integration_id: int | None,
    workspace_id: int | None,
) -> Integration:
    if integration_id is not None:
        return _get_meta_integration(db, current_user, int(integration_id))

    resolved_workspace_id = _resolve_meta_connect_workspace_id(
        db,
        user_id=current_user.id,
        requested_workspace_id=workspace_id,
    )
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    return _get_or_create_meta_integration_for_workspace(db, resolved_workspace_id)


def _disconnect_meta_integration(
    db: Session,
    integration: Integration,
    *,
    revoke_permissions: bool = True,
) -> MetaDisconnectOut:
    token_accounts = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == _meta_token_account_external_id(integration.id),
        )
        .all()
    )
    integration_accounts = (
        db.query(IntegrationAccount)
        .filter(IntegrationAccount.integration_id == integration.id)
        .all()
    )
    token_account_ids = [account.id for account in token_accounts]
    all_integration_account_ids = [account.id for account in integration_accounts]
    tokens = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id.in_(all_integration_account_ids))
        .all()
        if all_integration_account_ids
        else []
    )
    stored_records = (
        db.query(MetaPage)
        .filter(MetaPage.integration_id == integration.id)
        .all()
    )
    facebook_pages_count = len(
        [record for record in stored_records if record.record_type == META_RECORD_TYPE_FACEBOOK_PAGE]
    )
    instagram_accounts_count = len(
        [record for record in stored_records if record.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT]
    )

    meta_revoke_status = "skipped"
    if revoke_permissions and token_account_ids:
        latest_token = _get_latest_integration_token(db, token_account_ids[0])
        if latest_token and latest_token.access_token:
            meta_revoke_status = _revoke_meta_permissions(latest_token.access_token)

    for record in stored_records:
        db.delete(record)
    for account in integration_accounts:
        db.delete(account)
    integration.status = "disconnected"
    db.add(integration)
    db.commit()
    db.refresh(integration)

    cleared = MetaDisconnectClearedOut(
        tokens=bool(tokens),
        facebook_pages=facebook_pages_count,
        instagram_accounts=instagram_accounts_count,
        integration_accounts=len(integration_accounts),
    )
    logger.info(
        "[META_DISCONNECT_AUDIT]",
        extra={
            "integration_id": integration.id,
            "workspace_id": integration.workspace_id,
            "provider": integration.provider,
            "status": integration.status,
            "token_accounts_count": len(token_accounts),
            "integration_accounts_count": len(integration_accounts),
            "tokens_cleared": cleared.tokens,
            "facebook_pages_cleared": cleared.facebook_pages,
            "instagram_accounts_cleared": cleared.instagram_accounts,
            "meta_revoke_status": meta_revoke_status,
        },
    )
    return MetaDisconnectOut(
        cleared=cleared,
        meta_revoke_status=meta_revoke_status,
    )


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
        "pages_read_user_content",
        "instagram_basic",
        "instagram_manage_insights",
        "ads_read",
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
            "entity_name": payload.get("name"),
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
    preserve_existing_on_empty: bool = True,
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
    if preserve_existing_on_empty and not authorized_records and existing_pages:
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
    # LEGACY / candidate for removal after frontend/backend contract is stable.
    # Recommended source of truth for 5-slide daily series is extractDailyMetricSeries()
    # backed by _extract_daily_metric_series_details().
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


def _sum_meta_daily_series(points: list[dict] | None) -> int | float | None:
    numeric_values: list[float] = []
    for point in points or []:
        if not isinstance(point, dict):
            continue
        numeric = _sum_nested_numeric_values(point.get("value"))
        if numeric is not None:
            numeric_values.append(float(numeric))
    if not numeric_values:
        return None
    total = sum(numeric_values)
    return int(total) if total.is_integer() else total


def _facebook_metric_unavailable_reason(
    *,
    value: Any,
    points: list[dict] | None,
    source_metric: str | None,
) -> str | None:
    if isinstance(value, (int, float)) or _sum_meta_daily_series(points) is not None:
        return None
    if source_metric:
        return "Meta did not return this metric for the selected period."
    return "Meta did not return this metric for the selected period."


def _facebook_metric_audit_entry(
    *,
    source_metric: str | None,
    raw_value: Any,
    points: list[dict] | None,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    normalized_points = _meta_series_points(points or [])
    return {
        "source_metric": source_metric,
        "raw_value": raw_value,
        "sum_value": _sum_meta_daily_series(normalized_points),
        "points_count": len(normalized_points),
        "unavailable_reason": unavailable_reason,
    }


def _log_facebook_pages_metric_event(
    event_name: str,
    *,
    report_id: int | None = None,
    dataset_id: int | None = None,
    page_id: str | None = None,
    page_name: str | None = None,
    metric_name: str,
    source_metric: str | None = None,
    raw_value: Any = None,
    points: list[dict] | None = None,
    unavailable_reason: str | None = None,
    formatted_total: str | None = None,
) -> None:
    normalized_points = _meta_series_points(points or [])
    logger.info(
        event_name,
        extra={
            "report_id": report_id,
            "dataset_id": dataset_id,
            "page_id": page_id,
            "page_name": page_name,
            "metric_name": metric_name,
            "source_metric": source_metric,
            "raw_value": raw_value,
            "formatted_total": formatted_total or _format_metric_summary_value(raw_value),
            "sum_value": _sum_meta_daily_series(normalized_points),
            "points_count": len(normalized_points),
            "unavailable_reason": unavailable_reason,
        },
    )


def _log_json_event(event_name: str, payload: dict[str, Any]) -> None:
    logger.info(
        "%s %s",
        event_name,
        json.dumps(payload, ensure_ascii=False, default=str),
        extra=payload,
    )


def _log_facebook_graph_request(
    *,
    page_id: str,
    page_name: str,
    since: str | None,
    until: str | None,
    period: str,
    metric_requested: str,
    endpoint: str | None = None,
) -> None:
    payload = {
        "page_id": page_id,
        "page_name": page_name,
        "since": since,
        "until": until,
        "period": period,
        "metric_requested": metric_requested,
        "endpoint": endpoint,
        "metric_returned": None,
        "status_code": None,
        "raw_response": None,
        "raw_values": [],
        "raw_sum": None,
        "points_count": 0,
        "normalized_field": None,
        "normalized_value": None,
        "unavailable_reason": None,
    }
    _log_json_event("FACEBOOK_GRAPH_PAGE_INSIGHTS_REQUEST", payload)


def _log_facebook_graph_raw_response(
    *,
    page_id: str,
    page_name: str,
    since: str | None,
    until: str | None,
    period: str,
    metric_requested: str,
    metric_returned: str | None,
    endpoint: str | None,
    status_code: int | None,
    raw_body: Any,
    raw_values: list[Any],
    raw_sum: Any,
    points_count: int,
    normalized_field: str,
    normalized_value: Any,
    unavailable_reason: str | None,
) -> None:
    payload = {
        "page_id": page_id,
        "page_name": page_name,
        "since": since,
        "until": until,
        "period": period,
        "metric_requested": metric_requested,
        "metric_returned": metric_returned,
        "endpoint": endpoint,
        "status_code": status_code,
        "raw_response": raw_body,
        "raw_values": raw_values,
        "raw_sum": raw_sum,
        "points_count": points_count,
        "normalized_field": normalized_field,
        "normalized_value": normalized_value,
        "unavailable_reason": unavailable_reason,
    }
    _log_json_event("FACEBOOK_GRAPH_PAGE_INSIGHTS_RAW_RESPONSE", payload)


def _log_facebook_metric_raw_values(
    *,
    page_id: str,
    page_name: str,
    since: str | None,
    until: str | None,
    period: str,
    metric_requested: str,
    metric_returned: str | None,
    endpoint: str | None,
    status_code: int | None,
    raw_values: list[Any],
    raw_sum: Any,
    points_count: int,
    normalized_field: str,
    normalized_value: Any,
    unavailable_reason: str | None,
) -> None:
    payload = {
        "page_id": page_id,
        "page_name": page_name,
        "since": since,
        "until": until,
        "period": period,
        "metric_requested": metric_requested,
        "metric_returned": metric_returned,
        "endpoint": endpoint,
        "status_code": status_code,
        "raw_response": None,
        "raw_values": raw_values,
        "raw_sum": raw_sum,
        "points_count": points_count,
        "normalized_field": normalized_field,
        "normalized_value": normalized_value,
        "unavailable_reason": unavailable_reason,
    }
    _log_json_event("FACEBOOK_METRIC_RAW_VALUES", payload)


def _log_facebook_metric_normalized(
    *,
    page_id: str,
    page_name: str,
    since: str | None,
    until: str | None,
    period: str,
    metric_requested: str,
    metric_returned: str | None,
    endpoint: str | None,
    status_code: int | None,
    raw_values: list[Any],
    raw_sum: Any,
    points_count: int,
    normalized_field: str,
    normalized_value: Any,
    unavailable_reason: str | None,
) -> None:
    payload = {
        "page_id": page_id,
        "page_name": page_name,
        "since": since,
        "until": until,
        "period": period,
        "metric_requested": metric_requested,
        "metric_returned": metric_returned,
        "endpoint": endpoint,
        "status_code": status_code,
        "raw_response": None,
        "raw_values": raw_values,
        "raw_sum": raw_sum,
        "points_count": points_count,
        "normalized_field": normalized_field,
        "normalized_value": normalized_value,
        "unavailable_reason": unavailable_reason,
    }
    _log_json_event("FACEBOOK_METRIC_NORMALIZED", payload)


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
    page_name: str,
    timeframe_config: dict[str, str],
    integration_id: int,
) -> dict[str, object | None]:
    endpoint = f"/{page_id}/insights"
    for metric_name in META_PAGES_REACH_METRIC_CANDIDATES:
        _log_facebook_graph_request(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            endpoint=endpoint,
        )
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
            unavailable_reason = "Meta did not return this metric for the selected period."
            _log_facebook_graph_raw_response(
                page_id=page_id,
                page_name=page_name,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
                period="day",
                metric_requested=metric_name,
                metric_returned=metric_name,
                endpoint=endpoint,
                status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
                raw_body=metric_insight.get("_meta_raw_body"),
                raw_values=[],
                raw_sum=None,
                points_count=0,
                normalized_field="reach_total",
                normalized_value=None,
                unavailable_reason=unavailable_reason,
            )
            _log_facebook_metric_normalized(
                page_id=page_id,
                page_name=page_name,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
                period="day",
                metric_requested=metric_name,
                metric_returned=metric_name,
                endpoint=endpoint,
                status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
                raw_values=[],
                raw_sum=None,
                points_count=0,
                normalized_field="reach_total",
                normalized_value=None,
                unavailable_reason=unavailable_reason,
            )
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

        raw_values = [point.get("value") for point in reach_daily if isinstance(point, dict)]
        raw_sum = _sum_meta_daily_series(reach_daily)
        _log_facebook_graph_raw_response(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_body=metric_insight.get("_meta_raw_body"),
            raw_values=raw_values,
            raw_sum=raw_sum,
            points_count=len(reach_daily),
            normalized_field="reach_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
        _log_facebook_metric_raw_values(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_values=raw_values,
            raw_sum=raw_sum,
            points_count=len(reach_daily),
            normalized_field="reach_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
        _log_facebook_metric_normalized(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_values=[metric_value],
            raw_sum=metric_value,
            points_count=len(reach_daily),
            normalized_field="reach_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
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
    page_name: str,
    timeframe_config: dict[str, str],
    integration_id: int,
) -> dict[str, object | None]:
    endpoint = f"/{page_id}/insights"
    for metric_name in META_PAGES_IMPRESSIONS_METRIC_CANDIDATES:
        _log_facebook_graph_request(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            endpoint=endpoint,
        )
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
            unavailable_reason = "Meta did not return this metric for the selected period."
            _log_facebook_graph_raw_response(
                page_id=page_id,
                page_name=page_name,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
                period="day",
                metric_requested=metric_name,
                metric_returned=metric_name,
                endpoint=endpoint,
                status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
                raw_body=metric_insight.get("_meta_raw_body"),
                raw_values=[],
                raw_sum=None,
                points_count=0,
                normalized_field="impressions_total",
                normalized_value=None,
                unavailable_reason=unavailable_reason,
            )
            _log_facebook_metric_normalized(
                page_id=page_id,
                page_name=page_name,
                since=timeframe_config["since"],
                until=timeframe_config["until"],
                period="day",
                metric_requested=metric_name,
                metric_returned=metric_name,
                endpoint=endpoint,
                status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
                raw_values=[],
                raw_sum=None,
                points_count=0,
                normalized_field="impressions_total",
                normalized_value=None,
                unavailable_reason=unavailable_reason,
            )
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

        raw_values = [point.get("value") for point in impressions_daily if isinstance(point, dict)]
        raw_sum = _sum_meta_daily_series(impressions_daily)
        _log_facebook_graph_raw_response(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_body=metric_insight.get("_meta_raw_body"),
            raw_values=raw_values,
            raw_sum=raw_sum,
            points_count=len(impressions_daily),
            normalized_field="impressions_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
        _log_facebook_metric_raw_values(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_values=raw_values,
            raw_sum=raw_sum,
            points_count=len(impressions_daily),
            normalized_field="impressions_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
        _log_facebook_metric_normalized(
            page_id=page_id,
            page_name=page_name,
            since=timeframe_config["since"],
            until=timeframe_config["until"],
            period="day",
            metric_requested=metric_name,
            metric_returned=metric_name,
            endpoint=endpoint,
            status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
            raw_values=[metric_value],
            raw_sum=metric_value,
            points_count=len(impressions_daily),
            normalized_field="impressions_total",
            normalized_value=metric_value,
            unavailable_reason=None,
        )
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
    page_name: str,
    timeframe_config: dict[str, str],
    integration_id: int,
    *,
    metric_name: str,
    label: str,
    daily_key: str = "daily_series",
) -> dict[str, object | None]:
    endpoint = f"/{page_id}/insights"
    _log_facebook_graph_request(
        page_id=page_id,
        page_name=page_name,
        since=timeframe_config["since"],
        until=timeframe_config["until"],
        period="day",
        metric_requested=metric_name,
        endpoint=endpoint,
    )
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

    raw_values = [point.get("value") for point in daily_series if isinstance(point, dict)]
    raw_sum = _sum_meta_daily_series(daily_series)
    _log_facebook_graph_raw_response(
        page_id=page_id,
        page_name=page_name,
        since=timeframe_config["since"],
        until=timeframe_config["until"],
        period="day",
        metric_requested=metric_name,
        metric_returned=metric_name,
        endpoint=endpoint,
        status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
        raw_body=metric_insight.get("_meta_raw_body"),
        raw_values=raw_values,
        raw_sum=raw_sum,
        points_count=len(daily_series),
        normalized_field=daily_key,
        normalized_value=metric_value,
        unavailable_reason=None,
    )
    _log_facebook_metric_raw_values(
        page_id=page_id,
        page_name=page_name,
        since=timeframe_config["since"],
        until=timeframe_config["until"],
        period="day",
        metric_requested=metric_name,
        metric_returned=metric_name,
        endpoint=endpoint,
        status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
        raw_values=raw_values,
        raw_sum=raw_sum,
        points_count=len(daily_series),
        normalized_field=daily_key,
        normalized_value=metric_value,
        unavailable_reason=None,
    )
    _log_facebook_metric_normalized(
        page_id=page_id,
        page_name=page_name,
        since=timeframe_config["since"],
        until=timeframe_config["until"],
        period="day",
        metric_requested=metric_name,
        metric_returned=metric_name,
        endpoint=endpoint,
        status_code=metric_insight.get("_meta_http_status_code") if isinstance(metric_insight.get("_meta_http_status_code"), int) else None,
        raw_values=[metric_value],
        raw_sum=metric_value,
        points_count=len(daily_series),
        normalized_field=daily_key,
        normalized_value=metric_value,
        unavailable_reason=None,
    )
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
    display_label = (
        f"@{meta_page.instagram_username}"
        if is_instagram_record and str(meta_page.instagram_username or "").strip()
        else meta_page.name
    )
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
        display_label=display_label,
        category=meta_page.category,
        instagram_username=meta_page.instagram_username,
        profile_picture_url=meta_page.profile_picture_url,
        fan_count=None,
        followers_count=None,
        source="business" if meta_page.business_name else "direct",
        business_name=meta_page.business_name,
        last_synced_at=meta_page.updated_at,
    )


def _resolve_instagram_account_record_for_sync(
    db: Session,
    *,
    integration: Integration,
    current_user: User,
    instagram_account_id: str,
) -> MetaPage:
    requested_account_id = str(instagram_account_id or "").strip()
    if not requested_account_id:
        raise http_error(
            400,
            "missing_instagram_account_id",
            "instagram_account_id is required.",
        )

    stored_record = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            MetaPage.page_id == requested_account_id,
        )
        .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
        .first()
    )
    if stored_record:
        return stored_record

    access_token = _get_meta_access_token(db, integration)
    cached_pages, diagnostics, _ = _refresh_meta_pages_from_live_graph(
        db,
        integration,
        access_token=access_token,
        user_id=current_user.id,
        selected_integration_type="instagram_accounts",
        context="sync_instagram_business",
        return_empty_on_error=True,
    )
    instagram_records = _filter_meta_records(
        cached_pages,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    for record in instagram_records:
        if str(record.page_id or "").strip() == requested_account_id:
            return record

    logger.warning(
        "Meta Instagram sync requested account not found",
        extra={
            "integration_id": integration.id,
            "requested_instagram_account_id": requested_account_id,
            "instagram_accounts_found_count": len(instagram_records),
            "instagram_usernames_found": [
                record.instagram_username for record in instagram_records if record.instagram_username
            ],
            "diagnostics_pages_checked": len(diagnostics),
        },
    )
    raise http_error(
        404,
        "instagram_account_not_found",
        "Instagram Business account not found for this integration.",
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
    configured_base = str(settings.frontend_url or settings.frontend_base_url or "").strip()
    if configured_base:
        return configured_base.rstrip("/")
    return "http://localhost:3000"


def _meta_frontend_origin() -> str:
    frontend_base_url = _meta_frontend_base_url()
    parsed = urlsplit(frontend_base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return frontend_base_url


def _meta_oauth_frontend_callback_url(
    *,
    status: str,
    source: str | None = None,
    integration_id: int | None = None,
    error: str | None = None,
    callback_path: str = "/integrations/meta/callback",
) -> str:
    params: dict[str, str | int] = {"status": status}
    if integration_id is not None:
        params["integration_id"] = integration_id
    if source:
        params["source"] = source
    if error:
        params["error"] = error
    return f"{_meta_frontend_base_url()}{callback_path}?{urlencode(params)}"


def _meta_oauth_frontend_redirect_response(
    *,
    status: str,
    source: str | None = None,
    integration_id: int | None = None,
    error: str | None = None,
    callback_path: str = "/integrations/meta/callback",
) -> RedirectResponse:
    target_url = _meta_oauth_frontend_callback_url(
        status=status,
        source=source,
        integration_id=integration_id,
        error=error,
        callback_path=callback_path,
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


def _meta_oauth_popup_response(
    *,
    status: str,
    source: str | None = None,
    integration_id: int | None = None,
    error: str | None = None,
    message: str | None = None,
    callback_path: str = "/integrations/meta/callback",
    provider: str = "meta",
) -> HTMLResponse:
    target_url = _meta_oauth_frontend_callback_url(
        status=status,
        source=source,
        integration_id=integration_id,
        error=error,
        callback_path=callback_path,
    )
    frontend_origin = _meta_frontend_origin()
    event_type = "MEASURABLE_META_CONNECT_SUCCESS" if status == "connected" else "MEASURABLE_META_CONNECT_ERROR"
    fallback_message = (
        "Connection completed. You can close this window."
        if status == "connected"
        else "We could not complete the connection. You can close this window and try again."
    )
    payload: dict[str, Any] = {
        "type": event_type,
        "provider": provider,
        "status": status,
    }
    if integration_id is not None:
        payload["integrationId"] = integration_id
    if source:
        payload["source"] = source
    if error:
        payload["error"] = error
        payload["message"] = message or "We could not complete the Meta connection."
    elif message:
        payload["message"] = message

    html = f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Meta connection</title>
  </head>
  <body>
    <main style="font-family: Arial, sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1rem; line-height: 1.5;">
      <h1 style="font-size: 1.25rem;">{fallback_message}</h1>
      <p>{message or "If this window does not close automatically, you can return to Measurable."}</p>
      <p><a href="{target_url}">Volver a Measurable</a></p>
    </main>
    <script>
      (function () {{
        const payload = {json.dumps(payload)};
        const targetOrigin = {json.dumps(frontend_origin)};
        const fallbackUrl = {json.dumps(target_url)};
        try {{
          if (window.opener && !window.opener.closed) {{
            window.opener.postMessage(payload, targetOrigin);
            window.setTimeout(function () {{
              window.close();
            }}, 600);
            window.setTimeout(function () {{
              document.body.setAttribute("data-close-fallback", "true");
            }}, 900);
            return;
          }}
        }} catch (error) {{
          console.error("Meta popup callback failed", error);
        }}
        window.location.replace(fallbackUrl);
      }})();
    </script>
  </body>
</html>"""
    logger.warning(
        "Meta Pages callback returning popup close page status=%s integration_id=%s source=%s error=%s redirect_target=%s frontend_origin=%s",
        status,
        integration_id,
        source,
        error,
        target_url,
        frontend_origin,
    )
    return HTMLResponse(content=html, status_code=200)


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


def _meta_oauth_log(event: str, **payload: Any) -> None:
    logger.info("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))


def _meta_oauth_expected_scopes(integration_type: str | None) -> list[str]:
    return meta_oauth_scopes_for_integration_type(normalize_meta_oauth_integration_type(integration_type))


def _meta_oauth_expected_scope_string(integration_type: str | None) -> str:
    return meta_oauth_scope_string_for_integration_type(normalize_meta_oauth_integration_type(integration_type))


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
            return _meta_oauth_popup_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    try:
        state_user_id = int(payload.get("user_id", 0))
        workspace_id = int(payload.get("workspace_id", 0))
        state_integration_id = int(payload.get("integration_id", 0)) if payload.get("integration_id") else None
        selected_integration_type = normalize_meta_oauth_integration_type(payload.get("integration_type"))
        reconnect_requested = bool(payload.get("reconnect"))
        state_source = str(payload.get("source") or "").strip() or None
        state_callback_route = str(payload.get("callback_route") or "").strip() or None
    except (TypeError, ValueError):
        logger.warning("Meta Pages OAuth callback state could not be parsed", extra={"payload": payload})
        if redirect_to_frontend:
            return _meta_oauth_popup_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    if workspace_id <= 0:
        logger.warning(
            "Meta Pages OAuth callback received invalid workspace id",
            extra={"workspace_id": workspace_id, "payload": payload},
        )
        if redirect_to_frontend:
            return _meta_oauth_popup_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    effective_user_id = current_user.id if current_user is not None else state_user_id
    if effective_user_id <= 0:
        logger.warning(
            "Meta Pages OAuth callback received invalid user id",
            extra={"state_user_id": state_user_id, "current_user_id": current_user.id if current_user else None},
        )
        if redirect_to_frontend:
            return _meta_oauth_popup_response(status="error", error="invalid_state")
        return _safe_meta_callback_payload(success=False, pages=[], error="invalid_state")

    integration_id: int | None = None
    integration: Integration | None = None
    try:
        _meta_oauth_log(
            "META_OAUTH_CALLBACK_RECEIVED",
            provider="meta_pages",
            code_received=bool(code),
            workspace_id=workspace_id,
            user_id=effective_user_id,
            state_integration_id=state_integration_id,
            redirect_uri_param=redirect_uri,
            integration_type=selected_integration_type,
            reconnect_requested=reconnect_requested,
            state_callback_route=state_callback_route,
            state_payload=payload,
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
            if integration is not None and not reconnect_requested:
                _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_popup_response(
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
            if integration is not None and not reconnect_requested:
                _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_popup_response(
                    status="error",
                    source=state_source,
                    integration_id=integration_id,
                    error="token_exchange_failed",
                    message="Meta did not return an access token. Please close this window and try again.",
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

        existing_pages_before = (
            db.query(MetaPage)
            .filter(MetaPage.integration_id == integration.id)
            .order_by(MetaPage.record_type.asc(), MetaPage.name.asc(), MetaPage.page_id.asc())
            .all()
        )
        debug_token_payload = debug_token(access_token)
        debug_token_summary = _extract_debug_token_summary(debug_token_payload)
        requested_scopes = _meta_oauth_expected_scopes(selected_integration_type)
        received_scopes = [
            str(scope).strip()
            for scope in debug_token_summary["scopes"]
            if str(scope).strip()
        ]
        _meta_oauth_log(
            "META_OAUTH_TOKEN_SCOPES_RECEIVED",
            provider="meta_pages",
            integration_id=integration.id,
            workspace_id=workspace_id,
            user_id=effective_user_id,
            token_account_id=token_account.id,
            integration_type=selected_integration_type,
            token_valid=debug_token_summary["is_valid"],
            scopes_received=received_scopes,
            requested_scopes=requested_scopes,
            granular_target_ids=debug_token_summary["granular_target_ids"],
            reconnect_requested=reconnect_requested,
        )
        missing_scopes = [scope for scope in requested_scopes if scope not in received_scopes]
        if missing_scopes:
            _meta_oauth_log(
                "META_PERMISSION_MISSING",
                provider="meta_pages",
                integration_id=integration.id,
                workspace_id=workspace_id,
                user_id=effective_user_id,
                integration_type=selected_integration_type,
                missing_scopes=missing_scopes,
                scopes_received=received_scopes,
            )
        if debug_token_summary["is_valid"] is not True:
            if not reconnect_requested:
                _set_meta_integration_status(db, integration, status="disconnected")
            if redirect_to_frontend:
                return _meta_oauth_popup_response(
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
            preserve_existing_on_empty=False,
        )
        saved_token = _replace_integration_token(
            db,
            account_id=token_account.id,
            workspace_id=workspace_id,
            access_token=access_token,
        )
        logger.warning(
            "Meta Pages callback token stored integration_id=%s workspace_id=%s user_id=%s token_account_id=%s saved_token_id=%s reconnect_requested=%s",
            integration.id,
            workspace_id,
            effective_user_id,
            token_account.id,
            saved_token.id,
            reconnect_requested,
        )
        instagram_accounts = _filter_meta_records(
            cached_pages,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        )
        page_payloads = [_meta_page_out_from_cache(page) for page in facebook_pages]
        connected_assets = [
            {
                "record_type": page.record_type,
                "page_id": page.page_id,
                "name": page.name,
                "instagram_username": page.instagram_username,
            }
            for page in cached_pages
        ]
        _meta_oauth_log(
            "META_CONNECTED_ASSETS_DISCOVERED",
            provider="meta_pages",
            integration_id=integration.id,
            workspace_id=workspace_id,
            user_id=effective_user_id,
            integration_type=selected_integration_type,
            assets_count=len(connected_assets),
            facebook_pages_count=len(page_payloads),
            instagram_accounts_count=len(instagram_accounts),
            assets=connected_assets,
        )
        required_assets = instagram_accounts if selected_integration_type == "instagram_business" else page_payloads
        unavailable_reason = (
            "Meta connected, but no authorized Instagram Business accounts were returned."
            if selected_integration_type == "instagram_business"
            else "Meta connected, but no authorized Facebook Pages were returned."
        )
        if not required_assets:
            if not reconnect_requested:
                _set_meta_integration_status(db, integration, status="disconnected")
            return (
                _meta_oauth_popup_response(
                    status="error",
                    source=state_source,
                    integration_id=integration.id,
                    error="no_authorized_assets",
                    message=unavailable_reason,
                )
                if redirect_to_frontend
                else _safe_meta_callback_payload(
                    success=False,
                    pages=[],
                    error="no_authorized_assets",
                    integration_id=integration.id,
                )
            )
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
        callback_user = load_current_user(db, effective_user_id)
        _track_meta_event(
            event_name="MetaConnected",
            user=callback_user,
            event_source_url=_tracking_event_source_url(None, "/integrations"),
            custom_data={
                "integration_id": integration.id,
                "workspace_id": workspace_id,
                "facebook_pages_count": len(page_payloads),
                "instagram_accounts_count": len(instagram_accounts),
            },
        )
        if redirect_to_frontend:
            return _meta_oauth_popup_response(
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
        if integration is not None and not reconnect_requested:
            _set_meta_integration_status(db, integration, status="disconnected")
        if redirect_to_frontend:
            return _meta_oauth_popup_response(
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
        if integration is not None and not reconnect_requested:
            _set_meta_integration_status(db, integration, status="disconnected")
        if redirect_to_frontend:
            return _meta_oauth_popup_response(
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


def _meta_cache_last_synced_at(records: list[MetaPage]) -> datetime | None:
    timestamps = [record.updated_at for record in records if record.updated_at is not None]
    return max(timestamps) if timestamps else None


def _meta_cache_status(records: list[MetaPage]) -> str:
    if not records:
        return "empty_cache"
    last_synced_at = _meta_cache_last_synced_at(records)
    if last_synced_at is None:
        return "cached_stale"
    if last_synced_at.tzinfo is None:
        last_synced_at = last_synced_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last_synced_at <= META_PAGES_CACHE_TTL:
        return "cached"
    return "cached_stale"


def _apply_meta_records_search(records: list[MetaPage], search: str | None) -> list[MetaPage]:
    normalized_search = str(search or "").strip().lower()
    if not normalized_search:
        return records
    return [
        record
        for record in records
        if normalized_search in str(record.name or "").lower()
        or normalized_search in str(record.page_id or "").lower()
        or normalized_search in str(record.instagram_username or "").lower()
        or normalized_search in str(record.business_name or "").lower()
    ]


def _paginate_meta_records(records: list[MetaPage], *, limit: int, offset: int) -> list[MetaPage]:
    safe_offset = max(offset, 0)
    safe_limit = max(min(limit, 100), 1)
    return records[safe_offset : safe_offset + safe_limit]


def _meta_page_out_with_cache_status(meta_page: MetaPage, *, cache_status: str) -> MetaPageOut:
    payload = _meta_page_out_from_cache(meta_page)
    payload.source = "cached"
    payload.cache_status = cache_status
    return payload


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


def _replace_integration_token_encrypted(
    db: Session,
    *,
    account_id: int,
    workspace_id: int,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
) -> IntegrationToken:
    existing_tokens = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .all()
    )
    encrypted_access_token = encrypt_secret(access_token)
    encrypted_refresh_token = encrypt_secret(refresh_token) if refresh_token else None
    token = existing_tokens[0] if existing_tokens else None
    if not token:
        token = IntegrationToken(
            account_id=account_id,
            workspace_id=workspace_id,
            token_type="access_token",
            access_token=encrypted_access_token,
            refresh_token=encrypted_refresh_token,
            expires_at=expires_at,
        )
        db.add(token)
    else:
        token.token_type = "access_token"
        token.access_token = encrypted_access_token
        token.refresh_token = encrypted_refresh_token
        token.expires_at = expires_at
        db.add(token)
        for stale_token in existing_tokens[1:]:
            db.delete(stale_token)
    db.commit()
    db.refresh(token)
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


METRIC_ALIASES: dict[str, list[str]] = {
    "organic_impressions": [
        "organic_impressions",
        "organic_impressions_total",
        "page_posts_impressions_organic",
        "daily_organic_impressions",
    ],
    "reach": [
        "reach",
        "page_impressions_unique",
        "impressions_unique",
        "unique_reach",
        "page_reach",
        "account_reach",
        "profile_reach",
        "viewers",
    ],
    "impressions": [
        "impressions",
        "page_impressions",
        "profile_impressions",
        "account_impressions",
        "views",
        "content_views",
        "profile_views",
    ],
    "engagement": [
        "engagement",
        "engagements",
        "interactions",
        "total_interactions",
        "post_engagements",
        "content_interactions",
        "likes",
        "comments",
        "shares",
        "saves",
        "reactions",
        "link_clicks",
        "profile_activity",
    ],
    "page_views": [
        "page_views",
        "page_views_total",
        "page_visits",
        "page_visits_total",
        "views",
        "views_total",
        "profile_views",
        "profile_visits",
        "page_views_login",
        "page_views_logout",
        "profile_activity",
    ],
    "followers": [
        "followers",
        "followers_count",
        "follower_count",
        "fans",
        "fan_count",
        "page_fans",
    ],
    "fans": [
        "fans",
        "fans_total",
        "fan_count",
    ],
    "reactions": [
        "reactions",
        "reactions_total",
        "page_actions_post_reactions_total",
    ],
}

METRIC_LABELS: dict[str, str] = {
    "organic_impressions": "Organic Impressions",
    "reach": "Reach",
    "impressions": "Impressions",
    "engagement": "Engagement",
    "page_views": "Page Views",
    "followers": "Followers",
    "fans": "Fans",
    "reactions": "Reactions",
}

METRIC_LABELS_ES: dict[str, str] = {
    "organic_impressions": "Impresiones orgánicas",
    "reach": "Alcance",
    "impressions": "Impresiones",
    "engagement": "Engagement",
    "page_views": "Visitas a la página",
    "followers": "Seguidores",
    "fans": "Fans",
    "reactions": "Reacciones",
}

METRIC_UNAVAILABLE_MESSAGE = "Meta did not return this metric for the selected period."
LEGACY_METRIC_UNAVAILABLE_MESSAGE = "Dato no disponible en este momento con los permisos actuales de Meta."


def normalizeMetricValue(value) -> float | int | None:
    numeric = _meta_number(value)
    if numeric is None:
        return None
    if float(numeric).is_integer():
        return int(numeric)
    return float(numeric)


def _sum_nested_numeric_values(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, dict):
        total = 0.0
        found = False
        for item in value.values():
            numeric = _sum_nested_numeric_values(item)
            if numeric is not None:
                total += float(numeric)
                found = True
        if not found:
            return None
        return int(total) if total.is_integer() else total
    if isinstance(value, list):
        total = 0.0
        found = False
        for item in value:
            numeric = _sum_nested_numeric_values(item)
            if numeric is not None:
                total += float(numeric)
                found = True
        if not found:
            return None
        return int(total) if total.is_integer() else total
    return None


def _meta_full_date_label(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw[:10])
        return parsed.strftime("%A, %B %d, %Y").replace(" 0", " ")
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


def _meta_ads_decimal(value: Any) -> float:
    numeric = _meta_number(value)
    return float(numeric) if numeric is not None else 0.0


def _meta_ads_int(value: Any) -> int:
    numeric = _meta_number(value)
    return int(round(numeric)) if numeric is not None else 0


def _meta_ads_primary_result(actions: Any) -> dict[str, Any] | None:
    if not isinstance(actions, list):
        return None
    preferred_order = (
        "purchase",
        "omni_purchase",
        "offsite_conversion.purchase",
        "lead",
        "onsite_web_lead",
        "omni_lead",
        "complete_registration",
    )
    by_type: dict[str, dict[str, Any]] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type") or "").strip()
        if action_type:
            by_type[action_type] = action
    for action_type in preferred_order:
        if action_type in by_type:
            return by_type[action_type]
    return next(iter(by_type.values()), None) if by_type else None


def _meta_ads_primary_cost(costs: Any, primary_action_type: str | None) -> float | None:
    if not isinstance(costs, list):
        return None
    if primary_action_type:
        for item in costs:
            if not isinstance(item, dict):
                continue
            if str(item.get("action_type") or "").strip() == primary_action_type:
                numeric = _meta_number(item.get("value"))
                return float(numeric) if numeric is not None else None
    for item in costs:
        if not isinstance(item, dict):
            continue
        numeric = _meta_number(item.get("value"))
        if numeric is not None:
            return float(numeric)
    return None


def _build_meta_ads_dataset_data(
    *,
    account: MetaAdAccount,
    timeframe_config: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    total_spend = 0.0
    total_impressions = 0
    total_reach = 0
    total_clicks = 0
    total_inline_link_clicks = 0
    top_campaigns: dict[str, dict[str, Any]] = {}
    daily_trend_map: dict[str, dict[str, Any]] = {}
    total_results = 0.0
    weighted_ctr_numerator = 0.0
    weighted_cpc_numerator = 0.0
    weighted_cpm_numerator = 0.0
    primary_action_type: str | None = None
    last_primary_cost: float | None = None

    for row in rows:
        spend = _meta_ads_decimal(row.get("spend"))
        impressions = _meta_ads_int(row.get("impressions"))
        reach = _meta_ads_int(row.get("reach"))
        clicks = _meta_ads_int(row.get("clicks"))
        inline_link_clicks = _meta_ads_int(row.get("inline_link_clicks"))
        date_key = str(row.get("date_start") or "").strip()
        campaign_id = str(row.get("campaign_id") or "").strip() or "unknown_campaign"
        campaign_name = str(row.get("campaign_name") or campaign_id).strip() or campaign_id
        primary_action = _meta_ads_primary_result(row.get("actions"))
        result_value = _meta_number(primary_action.get("value")) if isinstance(primary_action, dict) else None
        result_count = float(result_value) if result_value is not None else 0.0
        action_type = (
            str(primary_action.get("action_type") or "").strip()
            if isinstance(primary_action, dict)
            else None
        ) or None
        if primary_action_type is None and action_type:
            primary_action_type = action_type
        if action_type:
            last_primary_cost = _meta_ads_primary_cost(row.get("cost_per_action_type"), action_type)

        total_spend += spend
        total_impressions += impressions
        total_reach += reach
        total_clicks += clicks
        total_inline_link_clicks += inline_link_clicks
        total_results += result_count
        ctr_value = _meta_number(row.get("ctr"))
        if ctr_value is not None and impressions > 0:
            weighted_ctr_numerator += float(ctr_value) * impressions
        cpc_value = _meta_number(row.get("cpc"))
        if cpc_value is not None and clicks > 0:
            weighted_cpc_numerator += float(cpc_value) * clicks
        cpm_value = _meta_number(row.get("cpm"))
        if cpm_value is not None and impressions > 0:
            weighted_cpm_numerator += float(cpm_value) * impressions

        top_campaign = top_campaigns.setdefault(
            campaign_id,
            {
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "spend": 0.0,
                "clicks": 0,
                "results": 0.0,
            },
        )
        top_campaign["spend"] += spend
        top_campaign["clicks"] += clicks
        top_campaign["results"] += result_count

        daily_point = daily_trend_map.setdefault(
            date_key,
            {
                "date": date_key,
                "spend": 0.0,
                "impressions": 0,
                "reach": 0,
                "clicks": 0,
                "results": 0.0,
            },
        )
        daily_point["spend"] += spend
        daily_point["impressions"] += impressions
        daily_point["reach"] += reach
        daily_point["clicks"] += clicks
        daily_point["results"] += result_count

    average_ctr = (weighted_ctr_numerator / total_impressions) if total_impressions > 0 else None
    average_cpc = (weighted_cpc_numerator / total_clicks) if total_clicks > 0 else None
    average_cpm = (weighted_cpm_numerator / total_impressions) if total_impressions > 0 else None
    cost_per_result = (total_spend / total_results) if total_results > 0 else last_primary_cost
    top_campaign_rows = sorted(
        top_campaigns.values(),
        key=lambda item: (-float(item["spend"]), -float(item["results"]), -int(item["clicks"])),
    )[:5]
    daily_trend = [daily_trend_map[key] for key in sorted(daily_trend_map.keys())]

    return {
        "integration_type": "meta_ads",
        "integration_display_name": "Meta Ads",
        "provider": "meta_ads",
        "channel": "meta_ads",
        "social_network": "meta_ads",
        "account_id": account.account_id,
        "account_name": account.account_name,
        "currency": account.currency,
        "timezone_name": account.timezone_name,
        "account_status": account.account_status,
        "business_id": account.business_id,
        "business_name": account.business_name,
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
        "total_spend": round(total_spend, 2),
        "total_impressions": total_impressions,
        "total_reach": total_reach,
        "total_clicks": total_clicks,
        "inline_link_clicks": total_inline_link_clicks,
        "average_ctr": round(average_ctr, 4) if average_ctr is not None else None,
        "average_cpc": round(average_cpc, 4) if average_cpc is not None else None,
        "average_cpm": round(average_cpm, 4) if average_cpm is not None else None,
        "total_results": round(total_results, 4) if total_results else None,
        "primary_result_type": primary_action_type,
        "cost_per_result": round(cost_per_result, 4) if cost_per_result is not None else None,
        "top_campaigns": [
            {
                **item,
                "spend": round(float(item["spend"]), 2),
                "results": round(float(item["results"]), 4) if item["results"] else 0,
            }
            for item in top_campaign_rows
        ],
        "daily_trend": [
            {
                **point,
                "spend": round(float(point["spend"]), 2),
                "results": round(float(point["results"]), 4) if point["results"] else 0,
            }
            for point in daily_trend
        ],
        "normalized_report_metrics": {
            "total_spend": round(total_spend, 2),
            "total_impressions": total_impressions,
            "total_reach": total_reach,
            "total_clicks": total_clicks,
            "average_ctr": round(average_ctr, 4) if average_ctr is not None else None,
            "average_cpc": round(average_cpc, 4) if average_cpc is not None else None,
            "average_cpm": round(average_cpm, 4) if average_cpm is not None else None,
            "total_results": round(total_results, 4) if total_results else None,
            "cost_per_result": round(cost_per_result, 4) if cost_per_result is not None else None,
            "daily_spend": [
                {"date": point["date"], "label": _meta_point_label(point["date"]), "value": point["spend"]}
                for point in daily_trend
            ],
            "daily_impressions": [
                {"date": point["date"], "label": _meta_point_label(point["date"]), "value": point["impressions"]}
                for point in daily_trend
            ],
            "daily_reach": [
                {"date": point["date"], "label": _meta_point_label(point["date"]), "value": point["reach"]}
                for point in daily_trend
            ],
            "daily_clicks": [
                {"date": point["date"], "label": _meta_point_label(point["date"]), "value": point["clicks"]}
                for point in daily_trend
            ],
            "daily_results": [
                {"date": point["date"], "label": _meta_point_label(point["date"]), "value": point["results"]}
                for point in daily_trend
            ],
        },
        "insights_rows": rows,
    }


def _multi_source_source_label(source_type: str) -> str:
    normalized = str(source_type or "").strip().lower()
    return {
        "facebook_pages": "Facebook",
        "instagram_business": "Instagram",
        "shopify": "Shopify",
        "meta_ads": "Meta Ads",
        "tiktok_ads": "TikTok Ads",
    }.get(normalized, normalized.replace("_", " ").title() or "Source")


def _multi_source_total(points: Any) -> int:
    normalized_points = _meta_series_points(points)
    total = 0
    for point in normalized_points:
        value = _meta_number(point.get("value"))
        if value is None:
            continue
        total += int(round(value))
    return total


def _multi_source_merge_series(sources: list[dict[str, Any]], metric: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregate: dict[str, dict[str, Any]] = {}
    series_payload: list[dict[str, Any]] = []
    for source in sources:
        raw_points = source.get("timeseries", {}).get(metric)
        points = _meta_series_points(raw_points)
        series_payload.append(
            {
                "source_type": source.get("source_type"),
                "provider": source.get("provider"),
                "label": source.get("label"),
                "account_name": source.get("account_name"),
                "metric": metric,
                "points": points,
            }
        )
        for point in points:
            point_date = str(point.get("date") or point.get("label") or "").strip()
            if not point_date:
                continue
            item = aggregate.setdefault(
                point_date,
                {
                    "date": point_date,
                    "label": point.get("label") or _meta_point_label(point_date),
                    "value": 0,
                },
            )
            point_value = _meta_number(point.get("value"))
            if point_value is not None:
                item["value"] = int(item["value"]) + int(round(point_value))
    aggregate_points = [aggregate[key] for key in sorted(aggregate.keys())]
    return aggregate_points, series_payload


def _multi_source_metric_sum(sources: list[dict[str, Any]], metric: str) -> int:
    total = 0
    for source in sources:
        value = _meta_number(source.get("metrics", {}).get(metric))
        if value is not None:
            total += int(round(value))
    return total


def _multi_source_engagement_rate(source: dict[str, Any]) -> float | None:
    reach_value = _meta_number(source.get("metrics", {}).get("reach"))
    engagement_value = _meta_number(source.get("metrics", {}).get("engagement"))
    if reach_value is None or reach_value <= 0 or engagement_value is None:
        return None
    return (engagement_value / reach_value) * 100.0


def _multi_source_format_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _multi_source_account_name(source: dict[str, Any], report_inputs: dict[str, Any]) -> str:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    return str(
        report_inputs.get("account_name")
        or report_inputs.get("page_name")
        or config_json.get("account_name")
        or source.get("label")
        or _multi_source_source_label(str(source.get("source_type") or ""))
    )


def _multi_source_normalize_source(source: dict[str, Any], *, dataset: Dataset, locale: str) -> dict[str, Any]:
    dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
    report_inputs = extract_meta_pages_report_inputs(dataset_data)
    source_type = str(source.get("source_type") or "").strip()
    default_label = _multi_source_source_label(source_type)
    account_name = _multi_source_account_name(source, report_inputs)
    label = str(source.get("label") or "").strip() or account_name or default_label
    metrics = {
        "followers": report_inputs.get("followers"),
        "reach": report_inputs.get("reach"),
        "impressions": report_inputs.get("impressions"),
        "engagement": report_inputs.get("engagement"),
        "profile_visits": report_inputs.get("profile_visits"),
        "page_visits": report_inputs.get("profile_visits"),
        "link_clicks": report_inputs.get("link_clicks"),
        "views": report_inputs.get("views"),
        "content_interactions": report_inputs.get("content_interactions"),
    }
    timeseries = {
        "followers_growth": report_inputs.get("followers_growth_daily") or [],
        "followers": report_inputs.get("followers_daily") or report_inputs.get("fan_count_daily") or report_inputs.get("audience_daily") or [],
        "reach": report_inputs.get("reach_daily") or [],
        "impressions": report_inputs.get("impressions_daily") or [],
        "engagement": report_inputs.get("daily_engagement") or report_inputs.get("engagement_daily") or [],
        "page_visits": report_inputs.get("page_visits_daily") or report_inputs.get("page_views_daily") or [],
    }
    posts = normalize_meta_recent_posts(report_inputs.get("recent_posts"))
    raw_summary_parts = [build_meta_pages_summary(report_inputs, locale)]
    posts_summary = build_meta_pages_recent_posts_summary(report_inputs, locale)
    if posts_summary:
        raw_summary_parts.append(posts_summary)
    return {
        "dataset_id": dataset.id,
        "source_type": source_type,
        "provider": str(source.get("provider") or "").strip(),
        "label": label,
        "account_name": account_name,
        "metrics": metrics,
        "timeseries": timeseries,
        "content": posts,
        "raw_summary": " ".join(part.strip() for part in raw_summary_parts if str(part or "").strip()),
        "report_inputs": report_inputs,
        "report_timeframe": dataset_data.get("timeframe") if isinstance(dataset_data.get("timeframe"), dict) else {},
    }


def _multi_source_top_content(sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for source in sources:
        for post in source.get("content") or []:
            if not isinstance(post, dict):
                continue
            candidate = dict(post)
            candidate["_source_label"] = source.get("label")
            candidate["_account_name"] = source.get("account_name")
            candidates.append(candidate)
    if not candidates:
        return None
    return max(candidates, key=_meta_post_score)


def _multi_source_build_context(
    *,
    title: str,
    locale: str,
    timeframe: dict[str, Any],
    branding: dict[str, Any],
    normalized_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    strongest_source = None
    strongest_sort_key: tuple[float, float, float] | None = None
    engagement_rates: list[float] = []
    for source in normalized_sources:
        rate = _multi_source_engagement_rate(source)
        if rate is not None:
            engagement_rates.append(rate)
        sort_key = (
            rate if rate is not None else -1.0,
            _meta_number(source.get("metrics", {}).get("engagement")) or -1.0,
            _meta_number(source.get("metrics", {}).get("reach")) or -1.0,
        )
        if strongest_sort_key is None or sort_key > strongest_sort_key:
            strongest_sort_key = sort_key
            strongest_source = source
    top_content = _multi_source_top_content(normalized_sources)
    total_reach = _multi_source_metric_sum(normalized_sources, "reach")
    total_impressions = _multi_source_metric_sum(normalized_sources, "impressions")
    total_engagement = _multi_source_metric_sum(normalized_sources, "engagement")
    average_engagement_rate = sum(engagement_rates) / len(engagement_rates) if engagement_rates else None
    key_insights = [
        f"Combined reach reached {_meta_format_number(total_reach)} across {len(normalized_sources)} sources.",
        (
            f"{strongest_source.get('label')} led performance with an engagement rate of "
            f"{_multi_source_format_rate(_multi_source_engagement_rate(strongest_source))}."
            if strongest_source is not None
            else "No strongest source could be identified from the available metrics."
        ),
        (
            f"Top content came from {top_content.get('_source_label')} and generated "
            f"{_meta_format_number(_meta_post_score(top_content))} combined engagement signals."
            if top_content is not None
            else "No cross-platform post-level content was available for this period."
        ),
    ]
    return {
        "report_kind": "multi_source",
        "title": title,
        "locale": locale,
        "report_timeframe": timeframe,
        "branding": branding,
        "sources": normalized_sources,
        "combined": {
            "total_reach": total_reach,
            "total_impressions": total_impressions,
            "total_engagement": total_engagement,
            "average_engagement_rate": average_engagement_rate,
            "strongest_source": {
                "label": strongest_source.get("label"),
                "account_name": strongest_source.get("account_name"),
                "source_type": strongest_source.get("source_type"),
                "metrics": strongest_source.get("metrics"),
                "engagement_rate": _multi_source_engagement_rate(strongest_source),
            }
            if strongest_source is not None
            else None,
            "top_content": top_content,
            "key_insights": key_insights,
        },
    }


def _multi_source_block_text_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if str(item or "").strip())


def _top_content_title(post: dict[str, Any] | None) -> str:
    if not isinstance(post, dict):
        return "Untitled content"
    return (
        str(
            post.get("title")
            or post.get("message")
            or post.get("caption")
            or post.get("text")
            or "Untitled content"
        ).strip()
        or "Untitled content"
    )


def _top_content_items(posts: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for post in sorted(posts, key=_meta_post_score, reverse=True)[:limit]:
        reach_value = _meta_number(post.get("reach"))
        engagement_value = _meta_post_score(post)
        items.append(
            {
                "id": post.get("id"),
                "title": _top_content_title(post),
                "date": post.get("created_time") or post.get("published_at") or post.get("date"),
                "source": post.get("_source_label") or post.get("_account_name"),
                "reach": reach_value,
                "impressions": _meta_number(post.get("impressions")),
                "engagement": engagement_value,
                "engagement_rate": round((engagement_value / reach_value) * 100, 2)
                if reach_value not in (None, 0)
                else None,
                "reactions": _meta_number(post.get("reactions")),
                "comments": _meta_number(post.get("comments")),
                "shares": _meta_number(post.get("shares")),
                "saves": _meta_number(post.get("saves")),
            }
        )
    return items


def _posts_chart_payload(posts: list[dict[str, Any]], *, timeframe: dict[str, Any], title: str) -> dict[str, Any]:
    grouped: dict[str, int] = {}
    for post in posts:
        post_date = _meta_post_date(post)
        if not post_date:
            continue
        grouped[post_date] = grouped.get(post_date, 0) + 1
    points = [
        {"date": day, "label": _meta_point_label(day), "value": value}
        for day, value in sorted(grouped.items())
    ]
    return {
        "label": title,
        "metric": "posts",
        "points": points,
        "data": points,
        "series": points,
        "timeframe": timeframe,
        "is_available": bool(points),
    }


def _multi_source_build_10_blocks(context: dict[str, Any]) -> list[dict[str, Any]]:
    sources = list(context.get("sources") or [])
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    combined = context.get("combined") if isinstance(context.get("combined"), dict) else {}
    period_label = str(report_timeframe.get("label") or "Selected period")
    source_labels = [str(source.get("label") or source.get("account_name") or "Source") for source in sources[:2]]
    subtitle = f"{' + '.join(source_labels)} performance report · {period_label}"
    reach_points, reach_series = _multi_source_merge_series(sources, "reach")
    impressions_points, impressions_series = _multi_source_merge_series(sources, "impressions")
    engagement_points, engagement_series = _multi_source_merge_series(sources, "engagement")
    page_visits_points, page_visits_series = _multi_source_merge_series(sources, "page_visits")
    followers_points, followers_series = _multi_source_merge_series(sources, "followers")
    posts = [
        {**post, "_source_label": source.get("label"), "_account_name": source.get("account_name")}
        for source in sources
        for post in list(source.get("content") or [])
        if isinstance(post, dict)
    ]
    top_posts = _top_content_items(posts)
    top_post = top_posts[0] if top_posts else None
    content_chart = _posts_chart_payload(posts, timeframe=report_timeframe, title=f"Posting Rhythm - {period_label}")

    def _aggregate_growth(metric: str, current_value) -> dict[str, object]:
        previous_total = 0.0
        has_previous = False
        for source in sources:
            source_context = {
                "report_inputs": source.get("report_inputs"),
                "report_timeframe": source.get("report_timeframe") or report_timeframe,
            }
            previous_value = _meta_previous_metric_total(
                source_context,
                "page_views" if metric == "page_visits" else metric,
            )
            if previous_value is None:
                continue
            has_previous = True
            previous_total += float(previous_value)
        return _growth_metadata_from_values(current_value, previous_total if has_previous else None)

    reach_growth = _aggregate_growth("reach", combined.get("total_reach"))
    impressions_growth = _aggregate_growth("impressions", combined.get("total_impressions"))
    engagement_growth = _aggregate_growth("engagement", combined.get("total_engagement"))
    page_visits_total = _multi_source_metric_sum(sources, "profile_visits")
    page_visits_growth = _aggregate_growth("page_visits", page_visits_total)
    followers_total = _multi_source_metric_sum(sources, "followers")
    followers_growth = _aggregate_growth("followers", followers_total)
    post_count = len(posts)
    strongest_source = combined.get("strongest_source") if isinstance(combined.get("strongest_source"), dict) else None
    weakest_source = (
        min(sources, key=lambda source: _meta_number(source.get("metrics", {}).get("engagement")) or float("inf"))
        if sources
        else None
    )
    executive_lines = [
        (
            f"Strongest platform: {strongest_source.get('label')} with "
            f"{_multi_source_format_rate(_meta_number(strongest_source.get('engagement_rate')))} engagement rate."
        )
        if strongest_source
        else "Strongest platform could not be identified from the selected data.",
        (
            f"Weakest platform: {weakest_source.get('label')} based on current engagement volume."
            if weakest_source
            else "Weakest platform could not be identified from the selected data."
        ),
        f"Combined reach was {_meta_format_number(combined.get('total_reach'))} and combined engagement was {_meta_format_number(combined.get('total_engagement'))}.",
        (
            f"Top content came from {top_post.get('source')} and delivered {_meta_format_number(top_post.get('engagement'))} engagement signals."
            if top_post
            else "No post-level content was available, so content pattern analysis is limited."
        ),
    ]
    recommendations = [
        (
            f"Scale the creative pattern from {strongest_source.get('label')} into the weaker platform during the next cycle."
            if strongest_source and weakest_source and strongest_source.get("label") != weakest_source.get("label")
            else "Keep the strongest creative theme consistent across both sources in the next reporting window."
        ),
        "Use the next report with the same platform mix so cross-platform movement can be compared period over period.",
        (
            f"Build the next content batch around the theme surfaced by \"{top_post.get('title')}\"."
            if top_post
            else "Improve post-level tracking so the next report can identify which content pattern wins by platform."
        ),
    ]
    return [
        _meta_report_block(
            "title",
            1,
            {
                "text": context.get("title") or "Multi-source report",
                "subtitle": subtitle,
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
                "branding": context.get("branding") or {},
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _meta_report_block(
            "stat",
            2,
            {
                "title": "Reach",
                "label": "Total Reach",
                "value": combined.get("total_reach"),
                "current_value": reach_growth.get("current_value"),
                "previous_value": reach_growth.get("previous_value"),
                "growth": reach_growth,
                "growth_percent": reach_growth.get("growth_percent"),
                "growth_label": reach_growth.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": {
                    "label": f"Reach - {period_label}",
                    "metric": "reach",
                    "points": reach_points,
                    "data": reach_points,
                    "series": reach_series,
                    "timeframe": report_timeframe,
                    "is_available": bool(reach_points),
                },
                "points": reach_points,
                "metrics": {
                    "main": reach_growth,
                    "sources": [
                        {"label": source.get("label"), "value": source.get("metrics", {}).get("reach")}
                        for source in sources
                    ],
                },
                "text": f"Reach totaled {_meta_format_number(combined.get('total_reach'))} across the selected platforms during {period_label}.",
                "semantic_name": "reach",
            },
        ),
        _meta_report_block(
            "stat",
            3,
            {
                "title": "Impressions",
                "label": "Total Impressions",
                "value": combined.get("total_impressions"),
                "current_value": impressions_growth.get("current_value"),
                "previous_value": impressions_growth.get("previous_value"),
                "growth": impressions_growth,
                "growth_percent": impressions_growth.get("growth_percent"),
                "growth_label": impressions_growth.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": {
                    "label": f"Impressions - {period_label}",
                    "metric": "impressions",
                    "points": impressions_points,
                    "data": impressions_points,
                    "series": impressions_series,
                    "timeframe": report_timeframe,
                    "is_available": bool(impressions_points),
                },
                "points": impressions_points,
                "metrics": {
                    "main": impressions_growth,
                    "sources": [
                        {"label": source.get("label"), "value": source.get("metrics", {}).get("impressions")}
                        for source in sources
                    ],
                },
                "text": f"Impressions reached {_meta_format_number(combined.get('total_impressions'))} across the selected platforms during {period_label}.",
                "semantic_name": "impressions",
            },
        ),
        _meta_report_block(
            "stat",
            4,
            {
                "title": "Engagement",
                "label": "Total Engagement",
                "value": combined.get("total_engagement"),
                "current_value": engagement_growth.get("current_value"),
                "previous_value": engagement_growth.get("previous_value"),
                "growth": engagement_growth,
                "growth_percent": engagement_growth.get("growth_percent"),
                "growth_label": engagement_growth.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": {
                    "label": f"Engagement - {period_label}",
                    "metric": "engagement",
                    "points": engagement_points,
                    "data": engagement_points,
                    "series": engagement_series,
                    "timeframe": report_timeframe,
                    "is_available": bool(engagement_points),
                },
                "points": engagement_points,
                "metrics": {
                    "main": engagement_growth,
                    "engagement_rate": {
                        "value": combined.get("average_engagement_rate"),
                        "label": _multi_source_format_rate(combined.get("average_engagement_rate")),
                    },
                    "sources": [
                        {
                            "label": source.get("label"),
                            "engagement": source.get("metrics", {}).get("engagement"),
                            "engagement_rate": _multi_source_engagement_rate(source),
                        }
                        for source in sources
                    ],
                },
                "text": f"Average engagement rate across the selected sources was {_multi_source_format_rate(combined.get('average_engagement_rate'))}.",
                "semantic_name": "engagement",
            },
        ),
        _meta_report_block(
            "stat",
            5,
            {
                "title": "Page Visits",
                "label": "Page/Profile Visits",
                "value": page_visits_total,
                "current_value": page_visits_growth.get("current_value"),
                "previous_value": page_visits_growth.get("previous_value"),
                "growth": page_visits_growth,
                "growth_percent": page_visits_growth.get("growth_percent"),
                "growth_label": page_visits_growth.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": {
                    "label": f"Page Visits - {period_label}",
                    "metric": "page_visits",
                    "points": page_visits_points,
                    "data": page_visits_points,
                    "series": page_visits_series,
                    "timeframe": report_timeframe,
                    "is_available": bool(page_visits_points),
                },
                "points": page_visits_points,
                "metrics": {
                    "main": page_visits_growth,
                    "sources": [
                        {
                            "label": source.get("label"),
                            "value": source.get("metrics", {}).get("profile_visits"),
                        }
                        for source in sources
                    ],
                },
                "text": (
                    "Daily page-visit history is available for comparison."
                    if page_visits_points
                    else "Page visits are available as a total, but daily visit history was not available."
                ),
                "semantic_name": "page_visits",
            },
        ),
        _meta_report_block(
            "stat",
            6,
            {
                "title": "Audience Growth",
                "label": "Followers / Audience",
                "value": followers_total,
                "current_value": followers_growth.get("current_value"),
                "previous_value": followers_growth.get("previous_value"),
                "growth": followers_growth,
                "growth_percent": followers_growth.get("growth_percent"),
                "growth_label": followers_growth.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": {
                    "label": f"Audience Trend - {period_label}",
                    "metric": "followers",
                    "points": followers_points,
                    "data": followers_points,
                    "series": followers_series,
                    "timeframe": report_timeframe,
                    "is_available": bool(followers_points),
                },
                "points": followers_points,
                "metrics": {
                    "main": followers_growth,
                    "sources": [
                        {
                            "label": source.get("label"),
                            "followers": source.get("metrics", {}).get("followers"),
                            "net_follower_change": _multi_source_total(source.get("timeseries", {}).get("followers_growth") or []),
                        }
                        for source in sources
                    ],
                },
                "text": "Audience movement reflects the combined follower base and any source-level follower growth signals available in the synced datasets.",
                "semantic_name": "audience_growth",
            },
        ),
        _meta_report_block(
            "stat",
            7,
            {
                "title": "Content Activity",
                "label": "Published Content",
                "value": post_count,
                "current_value": post_count,
                "previous_value": None,
                "growth": _growth_metadata_from_values(post_count, None),
                "growth_percent": None,
                "growth_label": "N/A",
                "comparison_period": "previous_period",
                "chart": content_chart,
                "points": list(content_chart.get("points") or []),
                "metrics": {
                    "main": _growth_metadata_from_values(post_count, None),
                    "average_reach_per_post": round((combined.get("total_reach") or 0) / post_count, 2) if post_count else None,
                    "average_engagement_per_post": round((combined.get("total_engagement") or 0) / post_count, 2) if post_count else None,
                },
                "text": (
                    f"{post_count} tracked content pieces were available across the selected platforms."
                    if post_count
                    else "No post-level content was available, so publishing rhythm could not be evaluated."
                ),
                "semantic_name": "content_activity",
            },
        ),
        _meta_report_block(
            "text",
            8,
            {
                "title": "Top Performing Content",
                "text": (
                    f"{top_post.get('source')} led with \"{top_post.get('title')}\" and generated {_meta_format_number(top_post.get('engagement'))} engagement signals."
                    if top_post
                    else "No post-level content exists for the selected sources, so this slide is an empty state."
                ),
                "top_posts": top_posts,
                "main_metric": top_post,
                "empty_state": top_post is None,
                "semantic_name": "top_performing_content",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            9,
            {
                "title": "Executive Insights",
                "text": _multi_source_block_text_lines(executive_lines),
                "insights": executive_lines,
                "metrics": {
                    "strongest_source": strongest_source,
                    "weakest_source": weakest_source.get("label") if weakest_source else None,
                },
                "semantic_name": "executive_insights",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            10,
            {
                "title": "Recommendations / Next Steps",
                "text": _multi_source_block_text_lines(recommendations),
                "recommendations": recommendations,
                "semantic_name": "recommendations",
            },
            ["text"],
        ),
    ]


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
    if metric == "organic_impressions":
        candidate_keys.extend(["organic_impressions_total", "page_posts_impressions_organic"])
    elif metric == "engagement":
        candidate_keys.extend(["total_interactions", "content_interactions", "accounts_engaged"])
    elif metric == "followers":
        candidate_keys.extend(["followers_count", "followers"])
    elif metric == "fans":
        candidate_keys.extend(["fan_count", "fans_total"])
    elif metric == "reactions":
        candidate_keys.extend(["reactions_total", "page_actions_post_reactions_total"])
    elif metric == "page_views":
        candidate_keys.extend(["page_views", "page_visits", "profile_views", "views"])
    elif metric == "profile_visits":
        candidate_keys.extend(["profile_views"])
    elif metric == "link_clicks":
        candidate_keys.extend(["website_clicks"])
    for key in candidate_keys:
        value = str(unavailable.get(key) or "").strip()
        if value:
            return value
    return None


def _metric_unavailable_message_for_context(context: dict) -> str:
    if _meta_integration_type(context) in {"facebook_pages", "meta_pages"}:
        return METRIC_UNAVAILABLE_MESSAGE
    return LEGACY_METRIC_UNAVAILABLE_MESSAGE


def _meta_first_series(*values) -> list[dict]:
    for value in values:
        points = _meta_series_points(value)
        if points:
            return points
    return []


def _metric_aliases(metric_key: str) -> list[str]:
    aliases = METRIC_ALIASES.get(metric_key, [metric_key])
    return list(dict.fromkeys([metric_key, *aliases]))


def _metric_summary_description(metric_key: str) -> str:
    descriptions = {
        "organic_impressions": "Organic post impressions",
        "reach": "Total reach",
        "impressions": "Total impressions",
        "engagement": "Total engagement",
        "followers": "Current audience size",
        "fans": "Current page fans",
        "reactions": "Total reactions",
        "page_views": "Total page views",
    }
    return descriptions.get(str(metric_key or "").strip().lower(), "Total metric value")


def _format_metric_summary_value(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, str):
        return value
    numeric = normalizeMetricValue(value)
    if numeric is None:
        return str(value)
    return _meta_format_number(numeric)


def _build_summary_metric_card(metric_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    is_available = bool(payload.get("is_available"))
    raw_value = payload.get("total") if is_available else None
    simple_value: Any
    if isinstance(raw_value, (dict, list)):
        simple_value = None
    else:
        simple_value = raw_value if raw_value not in (None, "") else None
    effective_metric_key = str(payload.get("metric_key") or metric_key).strip().lower() or metric_key
    return {
        "label": payload.get("metric_label_en") or payload.get("metric_label") or metric_key.title(),
        "value": simple_value,
        "formatted_value": _format_metric_summary_value(simple_value),
        "is_available": is_available,
        "description": (
            _metric_summary_description(effective_metric_key)
            if is_available
            else str(payload.get("unavailable_message") or payload.get("unavailable_reason") or METRIC_UNAVAILABLE_MESSAGE)
        ),
    }


def _metric_direct_total_only(context: dict[str, Any], metric_key: str) -> tuple[float | int | None, str]:
    for value in _metric_direct_value_candidates(context, metric_key):
        normalized = normalizeMetricValue(value)
        if normalized is not None:
            return normalized, "direct_meta_metric"
    return None, "not_available"


def _facebook_metric_audit_reason(context: dict[str, Any], metric_key: str) -> str | None:
    report_inputs = _meta_report_inputs(context)
    audit = report_inputs.get("facebook_metric_audit") if isinstance(report_inputs.get("facebook_metric_audit"), dict) else {}
    metric_audit = audit.get(metric_key) if isinstance(audit.get(metric_key), dict) else {}
    reason = str(metric_audit.get("unavailable_reason") or "").strip()
    if reason:
        return reason
    fallback = str(_meta_metric_unavailable_reason(context, metric_key) or "").strip()
    return fallback or None


def _facebook_metric_slide_unavailable_message(metric_key: str) -> str:
    messages = {
        "organic_impressions": "Meta did not return organic post impressions for the selected period.",
        "reach": "Meta did not return unique reach for the selected period.",
        "impressions": "Meta did not return impressions for the selected period.",
        "page_views": "Meta did not return page views for the selected period.",
        "engagement": "Meta did not return engagement for the selected period.",
        "followers": "Meta did not return followers for the selected period.",
        "fans": "Meta did not return fans for the selected period.",
        "reactions": "Meta did not return reactions for the selected period.",
    }
    return messages.get(metric_key, "Meta did not return this metric for the selected period.")


def _facebook_pages_metric_details(context: dict[str, Any], metric_key: str) -> dict[str, Any]:
    report_inputs = _meta_report_inputs(context)
    normalized_metrics = (
        report_inputs.get("normalized_report_metrics")
        if isinstance(report_inputs.get("normalized_report_metrics"), dict)
        else {}
    )
    audit = (
        report_inputs.get("facebook_metric_audit")
        if isinstance(report_inputs.get("facebook_metric_audit"), dict)
        else {}
    )
    audit_entry = audit.get(metric_key) if isinstance(audit.get(metric_key), dict) else {}

    def _candidate_total(values: list[Any]) -> float | int | None:
        for value in values:
            normalized = normalizeMetricValue(value)
            if normalized is not None:
                return normalized
        return None

    if metric_key == "organic_impressions":
        daily_candidates = [
            report_inputs.get("daily_organic_impressions"),
            report_inputs.get("organic_impressions_daily"),
            normalized_metrics.get("daily_organic_impressions"),
            normalized_metrics.get("organic_impressions_daily"),
            context.get("daily_organic_impressions"),
        ]
        total_candidates = [
            report_inputs.get("organic_impressions_total"),
            normalized_metrics.get("organic_impressions_total"),
            context.get("organic_impressions_total"),
            report_inputs.get("organic_impressions"),
            context.get("organic_impressions"),
        ]
        source_metric = (
            str(
                (audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None)
                or report_inputs.get("organic_impressions_source_metric")
                or ""
            ).strip()
            or None
        )
    elif metric_key == "reach":
        daily_candidates = [
            report_inputs.get("reach_daily"),
            normalized_metrics.get("daily_reach"),
            normalized_metrics.get("viewers_daily"),
            context.get("daily_reach"),
        ]
        total_candidates = [
            report_inputs.get("reach_total"),
            normalized_metrics.get("reach_total"),
            normalized_metrics.get("viewers_total"),
            context.get("reach_total"),
            report_inputs.get("reach"),
            context.get("reach"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or report_inputs.get("reach_source_metric") or "").strip()
            or None
        )
    elif metric_key == "impressions":
        daily_candidates = [
            report_inputs.get("impressions_daily"),
            normalized_metrics.get("daily_impressions"),
            normalized_metrics.get("impressions_daily"),
            context.get("daily_impressions"),
        ]
        total_candidates = [
            report_inputs.get("impressions_total"),
            normalized_metrics.get("impressions_total"),
            context.get("impressions_total"),
            report_inputs.get("impressions"),
            context.get("impressions"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or report_inputs.get("impressions_source_metric") or "").strip()
            or None
        )
    elif metric_key == "engagement":
        daily_candidates = [
            report_inputs.get("daily_engagement"),
            report_inputs.get("engagement_daily"),
            normalized_metrics.get("daily_engagement"),
            normalized_metrics.get("interactions_daily"),
            context.get("daily_engagement"),
        ]
        total_candidates = [
            report_inputs.get("engagement_total"),
            normalized_metrics.get("engagement_total"),
            normalized_metrics.get("interactions_total"),
            context.get("engagement_total"),
            report_inputs.get("interactions_total"),
            report_inputs.get("engagement"),
            context.get("engagement"),
            report_inputs.get("total_interactions"),
            report_inputs.get("accounts_engaged"),
            report_inputs.get("content_interactions"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or report_inputs.get("engagement_source_metric") or "").strip()
            or None
        )
    elif metric_key == "page_views":
        daily_candidates = [
            report_inputs.get("page_views_daily"),
            normalized_metrics.get("daily_page_views"),
            normalized_metrics.get("page_visits_daily"),
            normalized_metrics.get("views_daily"),
            report_inputs.get("daily_page_views"),
            report_inputs.get("page_visits_daily"),
            context.get("daily_page_views"),
        ]
        total_candidates = [
            report_inputs.get("page_views_total"),
            normalized_metrics.get("page_views_total"),
            normalized_metrics.get("views_total"),
            context.get("page_views_total"),
            report_inputs.get("views"),
            report_inputs.get("profile_visits"),
            context.get("views"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or report_inputs.get("page_views_source_metric") or "").strip()
            or None
        )
    elif metric_key == "fans":
        daily_candidates = []
        total_candidates = [
            report_inputs.get("fans_total"),
            normalized_metrics.get("fans_total"),
            context.get("fans_total"),
            report_inputs.get("fans"),
            report_inputs.get("fan_count"),
            context.get("fans"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or "fan_count").strip()
            or None
        )
    elif metric_key == "reactions":
        daily_candidates = [
            report_inputs.get("daily_reactions"),
            normalized_metrics.get("daily_reactions"),
            context.get("daily_reactions"),
        ]
        total_candidates = [
            report_inputs.get("reactions_total"),
            normalized_metrics.get("reactions_total"),
            context.get("reactions_total"),
            report_inputs.get("reactions"),
            context.get("reactions"),
        ]
        source_metric = (
            str(
                (audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None)
                or "page_actions_post_reactions_total"
            ).strip()
            or None
        )
    else:
        daily_candidates = [report_inputs.get("followers_daily"), context.get("followers_daily")]
        total_candidates = [
            report_inputs.get("followers_total"),
            normalized_metrics.get("followers_total"),
            context.get("followers_total"),
            report_inputs.get("followers"),
            context.get("followers"),
        ]
        source_metric = (
            str((audit_entry.get("source_metric") if isinstance(audit_entry, dict) else None) or "followers_count").strip()
            or None
        )

    daily_series: list[dict[str, Any]] = []
    for candidate in daily_candidates:
        points = _extract_series_candidate(candidate)
        if points:
            daily_series = _normalize_daily_series_result(points)
            if daily_series:
                break

    total = _candidate_total(total_candidates)
    if total is None and metric_key == "reactions":
        total = _sum_meta_daily_series(daily_series)
    if total is None and daily_series and metric_key in {"organic_impressions", "reach", "impressions", "engagement", "page_views"}:
        total = _meta_metric_total_for_series(metric_key, daily_series)

    unavailable_reason = _facebook_metric_audit_reason(context, metric_key)
    unavailable_message = _facebook_metric_slide_unavailable_message(metric_key) if total is None else None
    return {
        "total": total,
        "daily_series": daily_series,
        "source_metric": source_metric,
        "unavailable_reason": unavailable_reason,
        "unavailable_message": unavailable_message,
    }


def _log_facebook_pages_report_metric_payload(context: dict[str, Any], payload: dict[str, Any]) -> None:
    if _meta_integration_type(context) not in {"facebook_pages", "meta_pages"}:
        return
    metric_key = str(payload.get("metric_key") or "").strip().lower()
    event_map = {
        "organic_impressions": "FACEBOOK_METRIC_RESOLVED_ORGANIC_IMPRESSIONS",
        "reach": "FACEBOOK_METRIC_RESOLVED_REACH",
        "impressions": "FACEBOOK_METRIC_RESOLVED_IMPRESSIONS",
        "engagement": "FACEBOOK_METRIC_RESOLVED_ENGAGEMENT",
        "page_views": "FACEBOOK_METRIC_RESOLVED_PAGE_VIEWS",
        "followers": "FACEBOOK_METRIC_RESOLVED_FOLLOWERS",
        "fans": "FACEBOOK_METRIC_RESOLVED_FANS",
        "reactions": "FACEBOOK_METRIC_RESOLVED_REACTIONS",
    }
    event_name = event_map.get(metric_key)
    if not event_name:
        return
    _log_facebook_pages_metric_event(
        event_name,
        report_id=int(context.get("report_id")) if isinstance(context.get("report_id"), int) else None,
        dataset_id=int(context.get("dataset_id")) if isinstance(context.get("dataset_id"), int) else None,
        page_name=str(context.get("page_name") or "") or None,
        metric_name=metric_key,
        source_metric=str(payload.get("metric_source") or "") or None,
        raw_value=payload.get("total"),
        points=payload.get("daily_series") if isinstance(payload.get("daily_series"), list) else [],
        unavailable_reason=str(payload.get("unavailable_reason") or payload.get("unavailable_message") or "") or None,
        formatted_total=str(payload.get("formatted_total") or "") or None,
    )


def _build_facebook_pages_metric_slide_payload(
    context: dict[str, Any],
    *,
    metric_key: str,
    title: str,
    label: str,
    semantic_name: str,
) -> dict[str, Any]:
    payload = buildMetricSlidePayload(context, metric_key=metric_key, metric_label=title)
    strict = _facebook_pages_metric_details(context, metric_key)
    daily_series = strict["daily_series"] if isinstance(strict.get("daily_series"), list) else []
    total = strict.get("total")
    is_available = total is not None or bool(daily_series)
    customized = dict(payload)
    customized.update(
        {
            "metric_key": metric_key,
            "metric_label": title,
            "metric_label_en": title,
            "metric_label_es": METRIC_LABELS_ES.get(metric_key, title),
            "title": title,
            "label": label,
            "semantic_name": semantic_name,
            "primary_metric_label": label.upper(),
            "secondary_metric": None,
            "value": total if total is not None else None,
            "total": total if total is not None else None,
            "formatted_total": _format_metric_summary_value(total if total is not None else None),
            "is_available": is_available,
            "metric_source": strict.get("source_metric") if is_available else "not_available",
            "unavailable_reason": strict.get("unavailable_reason") if not is_available else None,
            "unavailable_message": strict.get("unavailable_message") if not is_available else None,
            "daily_series": daily_series,
            "highest_day": getHighestDay(daily_series) if daily_series else None,
            "lowest_day": getLowestDay(daily_series) if daily_series else None,
            "daily_series_reason": "" if daily_series else "daily_series_unavailable_from_source",
        }
    )
    chart = customized.get("chart") if isinstance(customized.get("chart"), dict) else {}
    timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    customized["chart"] = {
        **chart,
        "label": label,
        "metric": metric_key,
        "points": daily_series,
        "data": daily_series,
        "series": daily_series,
        "timeframe": timeframe,
        "is_available": bool(daily_series),
    }
    insight_payload = build_metric_ai_insight(customized, context)
    customized.update(insight_payload)
    customized["insight_full"] = customized["insight"]
    if not daily_series:
        customized["current_value"] = normalizeMetricValue(total)
    else:
        customized["current_value"] = normalizeMetricValue(total)
    _log_facebook_pages_report_metric_payload(context, customized)
    return customized


def _build_context_metric_summary_card(
    context: dict[str, Any],
    *,
    metric_key: str,
    metric_label: str | None = None,
) -> dict[str, Any]:
    points = extractDailyMetricSeries(context, metric_key)
    total, _metric_source = _resolve_metric_total_details(context, metric_key, points)
    is_available = total is not None or bool(points)
    if total is None and points:
        total = _meta_metric_total_for_series(metric_key, points)
        is_available = total is not None
    payload = {
        "metric_label_en": metric_label or METRIC_LABELS.get(metric_key, metric_key.replace("_", " ").title()),
        "metric_label": metric_label or METRIC_LABELS.get(metric_key, metric_key.replace("_", " ").title()),
        "total": total if is_available else None,
        "is_available": is_available,
    }
    return _build_summary_metric_card(metric_key, payload)


def _daily_series_candidate_debug(context: dict, metric_key: str) -> dict[str, Any]:
    report_inputs = _meta_report_inputs(context)
    aliases = _metric_aliases(metric_key)
    container_keys = [
        "daily",
        "daily_metrics",
        "daily_series",
        "time_series",
        "insights",
        "metric_values",
        "values",
        "data",
        "breakdowns",
        "metrics",
        "normalized_report_metrics",
        "report_metric_mapping",
        "chart_data",
    ]

    def _available_nested_keys(source: dict) -> dict[str, list[str]]:
        nested: dict[str, list[str]] = {}
        for container_key in container_keys:
            container = source.get(container_key)
            if isinstance(container, dict):
                nested[container_key] = sorted(str(key) for key in container.keys())
        return nested

    def _matching_paths(source: dict, prefix: str) -> list[str]:
        matches: list[str] = []
        for alias in aliases:
            for key in (
                alias,
                f"{alias}_daily",
                f"daily_{alias}",
                f"{alias}_daily_series",
                f"daily_series_{alias}",
                f"{alias}_series",
            ):
                if key in source:
                    matches.append(f"{prefix}.{key}")
            for container_key in container_keys:
                container = source.get(container_key)
                if isinstance(container, dict) and alias in container:
                    matches.append(f"{prefix}.{container_key}.{alias}")
        return matches

    return {
        "metric_key": metric_key,
        "aliases": aliases,
        "context_keys": sorted(str(key) for key in context.keys()),
        "report_inputs_keys": sorted(str(key) for key in report_inputs.keys()),
        "context_nested_keys": _available_nested_keys(context if isinstance(context, dict) else {}),
        "report_inputs_nested_keys": _available_nested_keys(report_inputs),
        "context_matching_paths": _matching_paths(context if isinstance(context, dict) else {}, "context"),
        "report_inputs_matching_paths": _matching_paths(report_inputs, "report_inputs"),
    }


def _metric_unavailable_reason(context: dict, metric_key: str) -> str:
    explicit_reason = str(_meta_metric_unavailable_reason(context, metric_key) or "").strip().lower()
    if "permission" in explicit_reason or "scope" in explicit_reason:
        return "missing_permission"
    if "unsupported" in explicit_reason or "not_supported" in explicit_reason:
        return "not_supported_for_source"
    if "empty" in explicit_reason:
        return "empty_response"
    return "not_returned_by_meta"


def _metric_period_bounds(context: dict) -> tuple[str | None, str | None]:
    report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
    period_start = str(report_timeframe.get("since") or report_timeframe.get("current_since") or "")[:10] or None
    period_end = str(report_timeframe.get("until") or report_timeframe.get("current_until") or "")[:10] or None
    return period_start, period_end


def _series_first_last_dates(points: list[dict]) -> tuple[str | None, str | None]:
    dates = [str(point.get("date") or "")[:10] for point in points if point.get("date")]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _metric_direct_value_candidates(context: dict, metric_key: str) -> list[Any]:
    report_inputs = _meta_report_inputs(context)
    aliases = _metric_aliases(metric_key)
    direct_values: list[Any] = []
    if metric_key == "impressions":
        impressions_payload = (
            context.get("impressions_slide_payload")
            if isinstance(context.get("impressions_slide_payload"), dict)
            else {}
        )
        direct_values.extend(
            [
                impressions_payload.get("impressions_total"),
                impressions_payload.get("total"),
                impressions_payload.get("value"),
            ]
        )
    for source in (context, report_inputs):
        if not isinstance(source, dict):
            continue
        for alias in aliases:
            metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
            normalized = (
                source.get("normalized_report_metrics")
                if isinstance(source.get("normalized_report_metrics"), dict)
                else {}
            )
            direct_values.extend(
                [
                    source.get(alias),
                    source.get(f"{alias}_total"),
                    source.get(f"total_{alias}"),
                    metrics.get(alias),
                    metrics.get(f"{alias}_total"),
                    normalized.get(alias),
                    normalized.get(f"{alias}_total"),
                ]
            )
    return direct_values


def _engagement_component_values(context: dict) -> list[Any]:
    report_inputs = _meta_report_inputs(context)
    component_keys = ("reactions", "likes", "comments", "shares", "saves", "link_clicks")
    return [report_inputs.get(key) for key in component_keys]


def _engagement_direct_value_candidates(context: dict) -> list[Any]:
    report_inputs = _meta_report_inputs(context)
    direct_keys = ("engagement", "engagements", "interactions", "total_interactions", "post_engagements", "content_interactions")
    direct_values: list[Any] = []
    for source in (context, report_inputs):
        if not isinstance(source, dict):
            continue
        metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        normalized = (
            source.get("normalized_report_metrics")
            if isinstance(source.get("normalized_report_metrics"), dict)
            else {}
        )
        for key in direct_keys:
            direct_values.extend(
                [
                    source.get(key),
                    source.get(f"{key}_total"),
                    metrics.get(key),
                    metrics.get(f"{key}_total"),
                    normalized.get(key),
                    normalized.get(f"{key}_total"),
                ]
            )
    return direct_values


def _page_views_direct_value_candidates(context: dict) -> list[Any]:
    report_inputs = _meta_report_inputs(context)
    direct_keys = (
        "page_views",
        "page_views_total",
        "views",
        "views_total",
        "page_visits",
        "page_visits_total",
        "profile_views",
        "profile_visits",
        "page_views_login",
        "page_views_logout",
        "profile_activity",
    )
    direct_values: list[Any] = []
    for source in (context, report_inputs):
        if not isinstance(source, dict):
            continue
        metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        normalized = (
            source.get("normalized_report_metrics")
            if isinstance(source.get("normalized_report_metrics"), dict)
            else {}
        )
        report_metric_mapping = (
            source.get("report_metric_mapping")
            if isinstance(source.get("report_metric_mapping"), dict)
            else {}
        )
        page_visits_mapping = (
            report_metric_mapping.get("page_visits")
            if isinstance(report_metric_mapping.get("page_visits"), dict)
            else {}
        )
        views_mapping = (
            report_metric_mapping.get("views")
            if isinstance(report_metric_mapping.get("views"), dict)
            else {}
        )
        direct_values.extend(
            [
                page_visits_mapping.get("total"),
                views_mapping.get("total"),
                normalized.get("page_visits_total"),
                normalized.get("views_total"),
            ]
        )
        for key in direct_keys:
            direct_values.extend(
                [
                    source.get(key),
                    source.get(f"{key}_total"),
                    metrics.get(key),
                    metrics.get(f"{key}_total"),
                    normalized.get(key),
                    normalized.get(f"{key}_total"),
                ]
            )
    return direct_values


def _resolve_metric_total_details(context: dict, metric_key: str, points: list[dict]) -> tuple[float | int | None, str]:
    if metric_key == "engagement":
        for value in _engagement_direct_value_candidates(context):
            normalized = normalizeMetricValue(value)
            if normalized is not None:
                return normalized, "direct_meta_metric"
        component_values = [normalizeMetricValue(value) for value in _engagement_component_values(context)]
        if any(value is not None for value in component_values):
            return sum(value or 0 for value in component_values), "calculated_from_components"
        if points:
            return _meta_metric_total_for_series(metric_key, points), "direct_meta_metric"
        return None, "not_available"
    if metric_key == "page_views":
        for value in _page_views_direct_value_candidates(context):
            normalized = normalizeMetricValue(value)
            if normalized is not None:
                return normalized, "direct_meta_metric"
        if points:
            return _meta_metric_total_for_series(metric_key, points), "direct_meta_metric"
        return None, "not_available"

    for value in _metric_direct_value_candidates(context, metric_key):
        normalized = normalizeMetricValue(value)
        if normalized is not None:
            return normalized, "direct_meta_metric"
    if points:
        return _meta_metric_total_for_series(metric_key, points), "direct_meta_metric"
    return None, "not_available"


def _extract_series_candidate(candidate: Any) -> list[dict]:
    points = _meta_series_points(candidate)
    if points:
        return points
    if isinstance(candidate, dict):
        for key in (
            "points",
            "daily_series",
            "daily",
            "daily_metrics",
            "time_series",
            "metric_values",
            "values",
            "data",
            "breakdowns",
            "series",
        ):
            nested = candidate.get(key)
            nested_points = _extract_series_candidate(nested)
            if nested_points:
                return nested_points
    return []


def _normalize_daily_series_result(points: list[dict]) -> list[dict]:
    collapsed: dict[str, dict[str, Any]] = {}
    for point in _meta_series_points(points):
        point_date = str(point.get("date") or "").strip()
        if not point_date:
            continue
        normalized_value = normalizeMetricValue(point.get("value"))
        if normalized_value is None:
            continue
        existing = collapsed.get(point_date)
        if existing is None:
            collapsed[point_date] = {
                "date": point_date,
                "label": point.get("label") or _meta_point_label(point_date),
                "value": normalized_value,
            }
        else:
            existing["value"] = normalizeMetricValue(
                (normalizeMetricValue(existing.get("value")) or 0) + normalized_value
            )
    return [collapsed[key] for key in sorted(collapsed.keys())]


# Source of truth for 5-slide daily series resolution.
# Use this through extractDailyMetricSeries()/extractDailyMetricsSeries() instead
# of adding new per-platform readers.
def _extract_daily_metric_series_details(dataset: dict, metric_key: str) -> tuple[list[dict], str | None, str | None]:
    report_inputs = _meta_report_inputs(dataset)
    reach_chart = dataset.get("reach_chart_data") if isinstance(dataset.get("reach_chart_data"), dict) else {}
    impressions_payload = (
        dataset.get("impressions_slide_payload")
        if isinstance(dataset.get("impressions_slide_payload"), dict)
        else {}
    )
    aliases = _metric_aliases(metric_key)
    candidate_pairs: list[tuple[Any, str | None, str | None]] = []
    if metric_key == "reach":
        candidate_pairs.append((reach_chart.get("points"), "context.reach_chart_data.points", "reach"))
    if metric_key == "impressions":
        candidate_pairs.extend(
            [
                (impressions_payload.get("daily_series"), "context.impressions_slide_payload.daily_series", "impressions"),
                (impressions_payload.get("impressions_daily"), "context.impressions_slide_payload.impressions_daily", "impressions"),
                (
                    impressions_payload.get("chart", {}).get("points")
                    if isinstance(impressions_payload.get("chart"), dict)
                    else None,
                    "context.impressions_slide_payload.chart.points",
                    "impressions",
                ),
            ]
        )
    if metric_key == "engagement":
        candidate_pairs.extend(
            [
                (report_inputs.get("daily_engagement"), "report_inputs.daily_engagement", "daily_engagement"),
                (report_inputs.get("engagement_daily"), "report_inputs.engagement_daily", "engagement"),
                (report_inputs.get("content_interactions_daily"), "report_inputs.content_interactions_daily", "content_interactions"),
                (report_inputs.get("interactions_daily"), "report_inputs.interactions_daily", "interactions"),
            ]
        )
    if metric_key == "page_views":
        candidate_pairs.extend(
            [
                (report_inputs.get("page_views_daily"), "report_inputs.page_views_daily", "page_views"),
                (report_inputs.get("page_visits_daily"), "report_inputs.page_visits_daily", "page_visits"),
                (report_inputs.get("profile_views_daily"), "report_inputs.profile_views_daily", "profile_views"),
                (report_inputs.get("views_daily"), "report_inputs.views_daily", "views"),
            ]
        )
    for source in (dataset, report_inputs):
        if not isinstance(source, dict):
            continue
        for alias in aliases:
            source_prefix = "context" if source is dataset else "report_inputs"
            candidate_pairs.extend(
                [
                    (source.get(f"{alias}_daily"), f"{source_prefix}.{alias}_daily", alias),
                    (source.get(f"daily_{alias}"), f"{source_prefix}.daily_{alias}", alias),
                    (source.get(f"{alias}_daily_series"), f"{source_prefix}.{alias}_daily_series", alias),
                    (source.get(f"daily_series_{alias}"), f"{source_prefix}.daily_series_{alias}", alias),
                    (source.get(f"{alias}_series"), f"{source_prefix}.{alias}_series", alias),
                    (source.get(alias), f"{source_prefix}.{alias}", alias),
                ]
            )
            for container_key in (
                "daily",
                "daily_metrics",
                "daily_series",
                "time_series",
                "insights",
                "metric_values",
                "values",
                "data",
                "breakdowns",
                "metrics",
                "normalized_report_metrics",
                "report_metric_mapping",
            ):
                container = source.get(container_key)
                if isinstance(container, dict):
                    candidate_pairs.extend(
                        [
                            (
                                container.get(alias),
                                f"{source_prefix}.{container_key}.{alias}",
                                alias,
                            ),
                            (
                                container.get(f"{alias}_daily"),
                                f"{source_prefix}.{container_key}.{alias}_daily",
                                alias,
                            ),
                            (
                                container.get(f"daily_{alias}"),
                                f"{source_prefix}.{container_key}.daily_{alias}",
                                alias,
                            ),
                            (
                                container.get(f"{alias}_daily_series"),
                                f"{source_prefix}.{container_key}.{alias}_daily_series",
                                alias,
                            ),
                            (
                                container.get(f"daily_series_{alias}"),
                                f"{source_prefix}.{container_key}.daily_series_{alias}",
                                alias,
                            ),
                        ]
                    )
    blocks = dataset.get("blocks") if isinstance(dataset.get("blocks"), list) else []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        candidate_pairs.extend(
            [
                (block.get("daily_series"), f"context.blocks[{index}].daily_series", metric_key),
                (block.get("chart_data"), f"context.blocks[{index}].chart_data", metric_key),
            ]
        )
    first_any: tuple[list[dict], str | None, str | None] | None = None
    for candidate, source_path, matched_key in candidate_pairs:
        points = _extract_series_candidate(candidate)
        if points:
            normalized_points = _normalize_daily_series_result(points)
            if not normalized_points:
                continue
            if first_any is None:
                first_any = (normalized_points, source_path, matched_key)
            if any(normalizeMetricValue(point.get("value")) not in (None, 0) for point in normalized_points):
                return normalized_points, source_path, matched_key
    if first_any is not None:
        return first_any
    return [], None, None


def extractDailyMetricSeries(dataset: dict, metric_key: str) -> list[dict]:
    points, _source_path, _matched_key = _extract_daily_metric_series_details(dataset, metric_key)
    return points


def extractDailyMetricsSeries(dataset: dict, metric_key: str) -> list[dict]:
    return extractDailyMetricSeries(dataset, metric_key)


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


def _resolve_metric_total(context: dict, metric_key: str, points: list[dict]) -> float | int | None:
    total, _metric_source = _resolve_metric_total_details(context, metric_key, points)
    return total


def getHighestDay(points: list[dict]) -> dict[str, Any]:
    normalized = _meta_series_points(points)
    if not normalized:
        return {}
    highest = max(normalized, key=lambda point: float(point["value"]))
    return {
        "date": highest.get("date"),
        "label": _meta_full_date_label(highest.get("date")) or highest.get("label"),
        "value": normalizeMetricValue(highest.get("value")),
    }


def getLowestDay(points: list[dict]) -> dict[str, Any]:
    normalized = _meta_series_points(points)
    if not normalized:
        return {}
    lowest = min(normalized, key=lambda point: float(point["value"]))
    return {
        "date": lowest.get("date"),
        "label": _meta_full_date_label(lowest.get("date")) or lowest.get("label"),
        "value": normalizeMetricValue(lowest.get("value")),
    }


def truncateInsight(text: Any, max_chars: int) -> str:
    full_text = " ".join(str(text or "").split())
    if not full_text:
        return ""
    if len(full_text) <= max_chars:
        return full_text
    clipped = full_text[:max_chars].rstrip()
    sentence_break = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    if sentence_break >= max(80, int(max_chars * 0.45)):
        return clipped[: sentence_break + 1].rstrip()
    word_break = clipped.rfind(" ")
    return (clipped[:word_break] if word_break > 0 else clipped).rstrip(" ,;:-") + "..."


def truncateInsightForSlide(text: Any, *, limit: int = 280) -> tuple[str, str]:
    full_text = " ".join(str(text or "").split())
    if not full_text:
        return "", ""
    short_text = truncateInsight(full_text, limit)
    return short_text, full_text


def _metric_day_label(day: Any) -> str | None:
    if not isinstance(day, dict):
        return None
    return str(day.get("label") or day.get("date") or "").strip() or None


def _metric_trend_sentence(metric_label: str, daily_series: list[dict]) -> str:
    points = _meta_series_points(daily_series)
    if len(points) < 2:
        return ""
    first_value = normalizeMetricValue(points[0].get("value"))
    last_value = normalizeMetricValue(points[-1].get("value"))
    if first_value is None or last_value is None:
        return ""
    if last_value > first_value:
        direction = "cerró por encima del inicio"
    elif last_value < first_value:
        direction = "cerró por debajo del inicio"
    else:
        direction = "se mantuvo estable entre el inicio y el cierre"
    return f"La serie diaria de {metric_label.lower()} {direction}, lo que ayuda a ubicar el momento con mayor tracción."


def build_metric_ai_insight(metric_slide: dict[str, Any], context: dict[str, Any]) -> dict[str, str | int]:
    metric_key = str(metric_slide.get("metric_key") or "").strip().lower()
    label = str(metric_slide.get("metric_label") or metric_slide.get("metric_label_en") or metric_key.title())
    total = metric_slide.get("formatted_total") or _format_metric_summary_value(metric_slide.get("total"))
    daily_series = metric_slide.get("daily_series") if isinstance(metric_slide.get("daily_series"), list) else []
    highest_label = _metric_day_label(metric_slide.get("highest_day"))
    lowest_label = _metric_day_label(metric_slide.get("lowest_day"))
    unavailable_message = str(
        metric_slide.get("unavailable_message") or _metric_unavailable_message_for_context(context)
    )

    if not metric_slide.get("is_available"):
        full = unavailable_message or (
            "Meta did not return this metric for the selected period. "
            "The report keeps interpreting the metrics that are available and will update this section if Meta returns the metric later."
        )
        return {
            "insight_short": truncateInsight(full, 260),
            "insight": truncateInsight(full, 420),
            "insight_tone": "executive_ai",
            "insight_max_chars": 260,
        }

    trend = _metric_trend_sentence(label, daily_series)
    peak = f" El pico más alto aparece en {highest_label}." if highest_label else ""
    low = f" El punto más bajo fue {lowest_label}." if lowest_label and lowest_label != highest_label else ""

    if metric_key == "organic_impressions":
        full = (
            f"Las impresiones orgánicas de publicaciones llegaron a {total}.{peak}{low} "
            f"{trend or 'Meta devolvió esta métrica como señal de visibilidad orgánica de posts durante el periodo.'} "
            "Léela como visibilidad orgánica de contenido, no como unique reach."
        )
    elif metric_key == "reach":
        full = (
            f"El alcance acumuló {total} personas durante el periodo.{peak} "
            f"{trend or 'La distribución diaria ayuda a identificar qué piezas empujaron más visibilidad.'} "
            "Conviene reforzar los formatos que generaron mayor exposición."
        )
    elif metric_key == "engagement":
        source = str(metric_slide.get("metric_source") or "")
        source_text = " calculado desde interacciones disponibles" if source == "calculated_from_components" else ""
        full = (
            f"El engagement alcanzó {total}{source_text}.{peak}{low} "
            f"{trend or 'La lectura diaria permite detectar qué contenido concentró más respuesta.'} "
            "Analiza formato, copy y llamada a la acción del mejor día."
        )
    elif metric_key == "page_views":
        if daily_series:
            full = (
                f"Las visitas a la página llegaron a {total}.{peak}{low} "
                f"{trend or 'La evolución diaria ayuda a entender cuándo la audiencia mostró mayor intención de visitar la página.'} "
                "Conecta esos picos con publicaciones, llamadas a la acción o campañas que impulsaron interés."
            )
        else:
            full = (
                f"Las visitas a la página llegaron a {total}. "
                "Daily series not available for this metric. "
                "Usa el total como referencia ejecutiva y confirma en el siguiente sync si Meta entrega el desglose diario."
            )
    elif metric_key == "followers":
        full = (
            f"La comunidad cerró el periodo con {total} seguidores. "
            "Este dato funciona como referencia del tamaño actual de audiencia para leer el resto de métricas disponibles."
        )
    elif metric_key == "fans":
        full = (
            f"La página cerró el periodo con {total} fans. "
            "Este dato se presenta como snapshot actual del tamaño de la base de fans devuelta por Meta."
        )
    elif metric_key == "reactions":
        full = (
            f"Las reacciones totalizaron {total}.{peak}{low} "
            f"{trend or 'La serie disponible ayuda a identificar los días con mayor respuesta emocional al contenido.'} "
            "Úsalo como señal complementaria al engagement total."
        )
    else:
        full = (
            f"{label} cerró en {total}.{peak}{low} "
            f"{trend or 'La serie diaria disponible ayuda a ubicar el momento de mayor tracción.'} "
            "Toma los días pico como referencia para el siguiente periodo."
        )

    return {
        "insight_short": truncateInsight(full, 260),
        "insight": truncateInsight(full, 420),
        "insight_tone": "executive_ai",
        "insight_max_chars": 260,
    }


def _summary_card_from_value(
    *,
    label: str,
    value: Any,
    description: str,
    is_available: bool | None = None,
) -> dict[str, Any]:
    available = bool(is_available) if is_available is not None else value not in (None, "", [])
    return {
        "label": label,
        "value": value,
        "formatted_value": _format_metric_summary_value(value),
        "is_available": available,
        "description": description if available else METRIC_UNAVAILABLE_MESSAGE,
    }


def _facebook_pages_post_summary_cards(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    posts = _meta_recent_posts(context)
    if not posts:
        return {}
    reactions_total = sum(int(_meta_number(post.get("reactions")) or 0) for post in posts)
    comments_total = sum(int(_meta_number(post.get("comments")) or 0) for post in posts)
    shares_total = sum(int(_meta_number(post.get("shares")) or 0) for post in posts)
    top_post = max(posts, key=_meta_post_score) if posts else None
    top_post_label = None
    if isinstance(top_post, dict):
        top_post_label = _meta_text_excerpt(
            top_post.get("message") or top_post.get("title") or top_post.get("caption") or top_post.get("text"),
            fallback="Top post",
            limit=80,
        )
    cards: dict[str, dict[str, Any]] = {
        "posts_analyzed": _summary_card_from_value(
            label="Posts Analyzed",
            value=len(posts),
            description="Recent posts analyzed from the synced dataset.",
            is_available=True,
        ),
    }
    if reactions_total > 0:
        cards["reactions"] = _summary_card_from_value(
            label="Reactions",
            value=reactions_total,
            description="Total reactions across recent synced posts.",
            is_available=True,
        )
    if comments_total > 0:
        cards["comments"] = _summary_card_from_value(
            label="Comments",
            value=comments_total,
            description="Total comments across recent synced posts.",
            is_available=True,
        )
    if shares_total > 0:
        cards["shares"] = _summary_card_from_value(
            label="Shares",
            value=shares_total,
            description="Total shares across recent synced posts.",
            is_available=True,
        )
    if top_post_label:
        cards["top_post"] = _summary_card_from_value(
            label="Top Post",
            value=top_post_label,
            description="Post with the strongest combined post-level signal in the synced dataset.",
            is_available=True,
        )
    return cards


def build_final_ai_summary(slides: dict[str, dict[str, Any]], context: dict[str, Any]) -> dict[str, str]:
    period_label = str((context.get("report_timeframe") or {}).get("label") or "el periodo").strip()
    available = [
        payload
        for payload in slides.values()
        if isinstance(payload, dict) and payload.get("is_available")
    ]
    unavailable = [
        str(payload.get("metric_label_en") or payload.get("metric_label") or key.title())
        for key, payload in slides.items()
        if isinstance(payload, dict) and not payload.get("is_available")
    ]
    organic_impressions = slides.get("organic_impressions", {})
    engagement = slides.get("engagement", {})
    followers = slides.get("followers", {})
    fans = slides.get("fans", {})
    reactions = slides.get("reactions", {})
    page_views = slides.get("page_views", {})
    posts = _meta_recent_posts(context)

    if available:
        parts = []
        if organic_impressions.get("total") is not None:
            parts.append(f"Organic Impressions registró {organic_impressions.get('formatted_total')}")
        if _facebook_metric_audit_reason(context, "reach"):
            parts.append("Meta did not return unique reach for the selected period")
        if engagement.get("is_available"):
            parts.append(f"Engagement registró {engagement.get('formatted_total')}")
        if followers.get("is_available"):
            parts.append(f"Followers cerró en {followers.get('formatted_total')}")
        if fans.get("is_available"):
            parts.append(f"Fans cerró en {fans.get('formatted_total')}")
        if reactions.get("is_available"):
            parts.append(f"Reactions registró {reactions.get('formatted_total')}")
        if page_views.get("total") is not None:
            parts.append(f"Page Views alcanzó {page_views.get('formatted_total')}")
        if posts:
            parts.append(f"Posts analyzed llegó a {len(posts)}")
        summary = (
            f"Durante {period_label}, " + ", ".join(parts) + ". "
            "La lectura ejecutiva apunta a reforzar los días y formatos que concentran visibilidad, interacción e intención de visita."
        )
    else:
        summary = (
            f"Durante {period_label}, Meta did not return the main metrics for the selected period. "
            "El reporte conserva la estructura y se actualizará cuando la fuente entregue más información."
        )
    if unavailable:
        summary += f" Métricas no disponibles por ahora: {', '.join(unavailable)}."

    best_metric = None
    for payload in (engagement, organic_impressions, page_views, reactions):
        if payload.get("is_available"):
            best_metric = payload
            break
    if best_metric and best_metric.get("highest_day"):
        day_label = _metric_day_label(best_metric.get("highest_day"))
        recommendation = (
            f"Revisa qué publicación o formato impulsó el pico de {best_metric.get('metric_label_en')} en {day_label} "
            "y úsalo como referencia para el siguiente periodo."
        )
    elif posts:
        recommendation = (
            "Usa el top post y los días con mayor engagement e impressions para repetir formatos, ajustar el copy "
            "y reforzar llamadas a la acción que impulsen visitas y respuesta."
        )
    elif unavailable and len(unavailable) == len(slides):
        recommendation = "Confirm that Meta returned the selected-period metrics before comparing performance across periods."
    else:
        recommendation = "Prioriza las métricas disponibles y evita interpretar como cero cualquier dato marcado como N/A."

    return {
        "ai_summary": truncateInsight(summary, 520),
        "recommendation": truncateInsight(recommendation, 220),
    }


def buildMetricSlidePayload(
    context: dict,
    *,
    metric_key: str,
    metric_label: str | None = None,
    insight: str | None = None,
) -> dict[str, Any]:
    normalized_metric_key = str(metric_key or "").strip().lower()
    label_en = metric_label or METRIC_LABELS.get(normalized_metric_key, normalized_metric_key.replace("_", " ").title())
    label = METRIC_LABELS_ES.get(normalized_metric_key, label_en)
    debug_metadata = _daily_series_candidate_debug(context, normalized_metric_key)
    daily_series, source_path, matched_key = _extract_daily_metric_series_details(context, normalized_metric_key)
    total, metric_source = _resolve_metric_total_details(context, normalized_metric_key, daily_series)
    all_daily_values_zero = bool(daily_series) and all(
        normalizeMetricValue(point.get("value")) == 0 for point in daily_series
    )
    numeric_total = normalizeMetricValue(total)
    if normalized_metric_key in {"reach", "impressions"} and all_daily_values_zero and (numeric_total or 0) > 0:
        logger.warning(
            "[FiveSlideMetric][daily_series.inconsistent_with_total]",
            extra={
                "report_id": context.get("report_id"),
                "integration": _meta_integration_type(context),
                "metric_key": normalized_metric_key,
                "total": total,
                "daily_series_length": len(daily_series),
                "daily_series_values": daily_series,
                "daily_series_source_path": source_path,
                "daily_series_source_metric_key": matched_key,
                "raw_candidate_keys": debug_metadata,
            },
        )
        daily_series = []
        source_path = None
        matched_key = None
    is_available = total is not None or bool(daily_series)
    if total is None and daily_series:
        total = _meta_metric_total_for_series(normalized_metric_key, daily_series)
        metric_source = "direct_meta_metric"
        is_available = total is not None
    unavailable_reason = None if is_available else _metric_unavailable_reason(context, normalized_metric_key)
    unavailable_message = None if is_available else _metric_unavailable_message_for_context(context)
    total_value: Any = total if is_available else None
    value = total_value
    formatted_total = _format_metric_summary_value(total_value)
    frequency = None
    if normalized_metric_key == "impressions":
        viewers_total = (
            normalizeMetricValue((context.get("impressions_slide_payload") or {}).get("viewers_total"))
            if isinstance(context.get("impressions_slide_payload"), dict)
            else None
        )
        if viewers_total in (None, 0):
            viewers_total = normalizeMetricValue(context.get("profile_views") or context.get("views"))
        numeric_total = normalizeMetricValue(total) if total is not None else None
        if numeric_total is not None and viewers_total not in (None, 0):
            frequency = round(float(numeric_total) / float(viewers_total), 2)
    highest_day = getHighestDay(daily_series)
    lowest_day = getLowestDay(daily_series)
    if not is_available:
        highest_day = None
        lowest_day = None
    period_start, period_end = _metric_period_bounds(context)
    first_date, last_date = _series_first_last_dates(daily_series)
    branding = context.get("branding") if isinstance(context.get("branding"), dict) else {}
    payload = {
        "metric_key": normalized_metric_key,
        "metric_label": label_en,
        "metric_label_es": label,
        "metric_label_en": label_en,
        "value": value,
        "total": total_value,
        "formatted_total": formatted_total,
        "is_available": is_available,
        "unavailable_reason": unavailable_reason,
        "unavailable_message": unavailable_message,
        "metric_source": metric_source if is_available else "not_available",
        "branding": branding,
        "daily_series": daily_series,
        "highest_day": highest_day,
        "lowest_day": lowest_day,
        "frequency": frequency,
        "daily_series_reason": "" if daily_series else "daily_series_unavailable_from_source",
        "daily_series_source_path": source_path,
        "daily_series_source_metric_key": matched_key,
        "chart": {
            "label": label_en,
            "metric": normalized_metric_key,
            "points": daily_series,
            "data": daily_series,
            "series": daily_series,
            "is_available": is_available and bool(daily_series),
            "timeframe": context.get("report_timeframe") or {},
        },
    }
    insight_payload = build_metric_ai_insight(payload, context)
    payload.update(insight_payload)
    payload["insight_full"] = payload["insight"]
    if period_end and last_date and last_date < period_end:
        logger.info(
            "[FiveSlideMetric][daily_series.missing_period_end]",
            extra={
                "integration": _meta_integration_type(context),
                "report_id": context.get("report_id"),
                "metric_key": normalized_metric_key,
                "period_start": period_start,
                "period_end": period_end,
                "first_date": first_date,
                "last_date": last_date,
                "daily_series_source_path": source_path,
            },
        )
    logger.info(
            "[FiveSlideMetric][resolved]",
            extra={
                "report_id": context.get("report_id"),
                "integration": _meta_integration_type(context),
            "metric_key": normalized_metric_key,
            "total": total_value,
            "formatted_total": formatted_total,
            "is_available": is_available,
            "unavailable_reason": unavailable_reason,
            "metric_source": payload["metric_source"],
            "daily_series_length": len(daily_series),
            "first_date": first_date,
            "last_date": last_date,
            "period_start": period_start,
            "period_end": period_end,
            "daily_series_values": daily_series,
            "daily_series_source_path": source_path,
            "daily_series_source_metric_key": matched_key,
            "raw_candidate_keys": debug_metadata,
            "highest_day": highest_day,
            "lowest_day": lowest_day,
        },
    )
    return payload


def _build_five_slide_summary_payload(
    context: dict,
    *,
    period_label: str,
    organic_impressions_payload: dict[str, Any],
    engagement_payload: dict[str, Any],
    page_views_payload: dict[str, Any],
) -> dict[str, Any]:
    followers_card = _build_context_metric_summary_card(
        context,
        metric_key="followers",
        metric_label="Followers",
    )
    fans_card = _build_context_metric_summary_card(
        context,
        metric_key="fans",
        metric_label="Fans",
    )
    reactions_card = _build_context_metric_summary_card(
        context,
        metric_key="reactions",
        metric_label="Reactions",
    )
    final_insights = build_final_ai_summary(
        {
            "organic_impressions": organic_impressions_payload,
            "engagement": engagement_payload,
            "followers": {
                "metric_label_en": followers_card["label"],
                "metric_label": followers_card["label"],
                "formatted_total": followers_card["formatted_value"],
                "total": followers_card["value"],
                "is_available": followers_card["is_available"],
            },
            "fans": {
                "metric_label_en": fans_card["label"],
                "metric_label": fans_card["label"],
                "formatted_total": fans_card["formatted_value"],
                "total": fans_card["value"],
                "is_available": fans_card["is_available"],
            },
            "reactions": {
                "metric_label_en": reactions_card["label"],
                "metric_label": reactions_card["label"],
                "formatted_total": reactions_card["formatted_value"],
                "total": reactions_card["value"],
                "is_available": reactions_card["is_available"],
            },
            "page_views": page_views_payload,
        },
        context,
    )
    ai_summary = final_insights["ai_summary"]
    recommendation = final_insights["recommendation"]
    metrics_summary = {
        "organic_impressions": _build_summary_metric_card("organic_impressions", organic_impressions_payload),
        "engagement": _build_summary_metric_card("engagement", engagement_payload),
        "page_views": _build_summary_metric_card("page_views", page_views_payload),
        "followers": followers_card,
        "fans": fans_card,
        "reactions": reactions_card,
    }
    _log_json_event(
        "FACEBOOK_SUMMARY_METRICS_BUILT",
        {
            "report_id": context.get("report_id"),
            "dataset_id": context.get("dataset_id"),
            "page_name": context.get("page_name"),
            "metric_name": "summary_metrics",
            "source_metric": "summary",
            "raw_value": len(metrics_summary),
            "formatted_total": str(len(metrics_summary)),
            "points_count": len(metrics_summary),
            "unavailable_reason": None,
            "metrics_summary": metrics_summary,
        },
    )
    return {
        "slide_number": 5,
        "slide_type": "summary",
        "title": "Resumen final",
        "title_en": "Final Summary",
        "branding": context.get("branding") if isinstance(context.get("branding"), dict) else {},
        "metrics_summary": metrics_summary,
        "ai_summary": ai_summary,
        "recommendation": recommendation,
        "text": ai_summary,
        "insight": ai_summary,
        "insight_short": ai_summary,
        "semantic_name": "executive_summary",
        "timeframe": context.get("report_timeframe") or {},
    }


def _meta_metric_series(context: dict, metric: str) -> list[dict]:
    # LEGACY / candidate for removal after frontend/backend contract is stable.
    # Recommended source of truth for 5-slide daily series is extractDailyMetricSeries()
    # backed by _extract_daily_metric_series_details().
    if metric in {"reach", "impressions", "engagement", "page_views"}:
        return extractDailyMetricSeries(context, metric)
    if metric == "followers":
        report_inputs = _meta_report_inputs(context)
        return _meta_first_series(
            report_inputs.get("followers_daily"),
            report_inputs.get("fan_count_daily"),
            report_inputs.get("audience_daily"),
        )
    if metric == "posts":
        return _meta_posts_daily_series(context, metric="posts")
    return []


def _meta_metric_total(context: dict, metric: str, points: list[dict]) -> float | int | None:
    if metric in {"reach", "impressions", "engagement", "page_views"}:
        return _resolve_metric_total(context, metric, points)
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


def _format_growth_label(growth_percent: float | None) -> str:
    if growth_percent is None:
        return "N/A"
    rounded = round(float(growth_percent), 2)
    if abs(rounded - int(rounded)) < 0.01:
        return f"{int(rounded):+d}%"
    return f"{rounded:+.2f}".rstrip("0").rstrip(".") + "%"


def _growth_metadata_from_values(current_value, previous_value) -> dict[str, object]:
    current_numeric = _meta_number(current_value)
    previous_numeric = _meta_number(previous_value)
    growth_percent: float | None = None
    if previous_numeric is None:
        growth_label = "N/A"
    elif previous_numeric == 0:
        growth_label = "New activity" if (current_numeric or 0) > 0 else "No change"
    elif current_numeric is None:
        growth_label = "N/A"
    else:
        growth_percent = round(((current_numeric - previous_numeric) / previous_numeric) * 100, 2)
        growth_label = _format_growth_label(growth_percent)
    return {
        "current_value": current_numeric,
        "previous_value": previous_numeric,
        "growth_percent": growth_percent,
        "growth_label": growth_label,
        "comparison_period": "previous_period",
    }


def _comparison_growth_payload(
    context: dict,
    *,
    metric: str,
    current_value,
    current_points: list[dict] | None = None,
) -> dict[str, object]:
    comparison = _meta_metric_comparison(
        context,
        metric=metric,
        current_value=current_value,
        current_points=current_points or _meta_metric_series(context, metric),
    )
    growth = _growth_metadata_from_values(
        comparison.get("current_value"),
        comparison.get("previous_value"),
    )
    return {
        **comparison,
        "growth": growth,
        "growth_percent": growth.get("growth_percent"),
        "growth_label": growth.get("growth_label"),
        "comparison_period": "previous_period",
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
        branding = data.get("branding") if isinstance(data.get("branding"), dict) else {}
        if not branding:
            branding = context.get("branding") if isinstance(context.get("branding"), dict) else {}
        updated = dict(block)
        updated["data_json"] = json.dumps(
            _inject_cover_branding_payload(
                block_type=str(block.get("type") or ""),
                order=int(block.get("order") or 0),
                data=data,
                branding=branding,
            )
        )
        return updated
    semantic_name = str(data.get("semantic_name") or "").strip()
    label = str(data.get("label") or data.get("title") or semantic_name or block.get("type") or "Metric")
    if not semantic_name:
        label_lower = label.lower()
        if "follower" in label_lower or "audience" in label_lower:
            semantic_name = "audience_growth"
        elif "page visit" in label_lower or "profile visit" in label_lower:
            semantic_name = "page_visits"
        elif "organic impression" in label_lower or "organic_impression" in label_lower:
            semantic_name = "organic_impressions_overview"
        elif "reach" in label_lower:
            semantic_name = "reach_overview"
        elif "page view" in label_lower or "page_view" in label_lower or "visitas" in label_lower:
            semantic_name = "page_views_overview"
        elif "impression" in label_lower:
            semantic_name = "impressions_trend"
        elif "engagement" in label_lower:
            semantic_name = "engagement_overview"
        elif block.get("type") == "chart":
            semantic_name = "chart_data"
        else:
            semantic_name = "summary"
    metric = "reach"
    if semantic_name == "organic_impressions_overview" or "organic impression" in label.lower():
        metric = "organic_impressions"
    elif semantic_name == "impressions" or "impression" in semantic_name or "impression" in label.lower():
        metric = "impressions"
    elif semantic_name == "engagement" or "engagement" in semantic_name or "engagement" in label.lower():
        metric = "engagement"
    elif semantic_name == "page_visits" or "page_views" in semantic_name or "page view" in label.lower() or "page visit" in label.lower() or "visitas" in label.lower():
        metric = "page_views"
    elif "audience" in semantic_name or "follower" in semantic_name or "follower" in label.lower():
        metric = "followers"
    elif semantic_name == "content_activity":
        metric = "posts"
    elif "post" in semantic_name or "content" in semantic_name:
        metric = "engagement"
    if semantic_name in {"reach", "impressions", "engagement", "page_visits", "reach_overview", "organic_impressions_overview", "impressions_overview", "impressions_trend", "engagement_overview", "page_views_overview"}:
        report_timeframe = context.get("report_timeframe") if isinstance(context.get("report_timeframe"), dict) else {}
        period_label = str(report_timeframe.get("label") or "Selected period")
        reach_stats = _meta_series_stats(extractDailyMetricSeries(context, "reach"))
        if semantic_name in {"organic_impressions_overview"}:
            metric_payload = _build_facebook_pages_metric_slide_payload(
                context,
                metric_key="organic_impressions",
                title="ORGANIC IMPRESSIONS",
                label="TOTAL ORGANIC IMPRESSIONS",
                semantic_name=semantic_name,
            )
        elif semantic_name in {"reach_overview", "reach"}:
            metric_payload = _build_facebook_pages_metric_slide_payload(
                context,
                metric_key="reach",
                title="Reach",
                label="Total Reach",
                semantic_name=semantic_name,
            )
        elif semantic_name in {"impressions_overview", "impressions"}:
            metric_payload = _build_facebook_pages_metric_slide_payload(
                context,
                metric_key="impressions",
                title="Impressions",
                label="Total Impressions",
                semantic_name=semantic_name,
            )
        else:
            insight_source = (
                str((context.get("impressions_slide_payload") or {}).get("insight_text") or "").strip()
                or _meta_trend_copy("Impressions", _meta_series_stats(extractDailyMetricSeries(context, "impressions")), period_label)
                if semantic_name == "impressions_trend"
                else _meta_engagement_text(context, period_label, reach_stats)
                if semantic_name == "engagement_overview" or semantic_name == "engagement"
                else ""
            )
            metric_payload = buildMetricSlidePayload(
                context,
                metric_key=metric,
                metric_label=str(data.get("label") or label),
                insight=insight_source,
            )
        data.update(metric_payload)
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
    if semantic_name in {"reach", "impressions", "engagement", "page_visits", "reach_overview", "organic_impressions_overview", "impressions_overview", "impressions_trend", "engagement_overview", "page_views_overview"}:
        insight_short_value = str(data.get("insight_short") or data.get("insight") or insight)
        insight_full_value = str(data.get("insight_full") or data.get("insight") or insight_short_value)
    else:
        insight_short_value = str(data.get("insight_short") or insight)
        insight_full_value = str(data.get("insight_full") or data.get("insight") or insight)
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
            "insight": insight_full_value,
            "insight_short": insight_short_value,
            "insight_full": insight_full_value,
            "summary": insight if semantic_name in {"overview", "reach_overview"} else data.get("summary"),
            "content": insight if semantic_name in {"overview", "reach_overview"} else data.get("content"),
            "text": insight_short_value,
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
    engagement_points = extractDailyMetricSeries(context, "engagement")
    engagement_total = _resolve_metric_total(context, "engagement", engagement_points)
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
    engagement_display_value = engagement_total if engagement_total is not None else 0
    insight_text = _meta_engagement_text(context, period_label, reach_stats)
    insight_short, insight_full = truncateInsightForSlide(insight_text)
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
        "text": insight_short,
        "insight": insight_short,
        "insight_short": insight_short,
        "insight_full": insight_full,
        "metric_key": "engagement",
        "metric_label": "Engagement",
        "chart": chart,
        "points": normalized_points,
        "daily_series": normalized_points,
        "highest_day": getHighestDay(normalized_points),
        "lowest_day": getLowestDay(normalized_points),
        "daily_series_reason": "" if normalized_points else "daily_series_unavailable_from_source",
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
    # LEGACY / candidate for removal after frontend/backend contract is stable.
    # Recommended source of truth for social 5-slide reports is build_5_blocks().
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
    # Source of truth for official social 5-slide report structure:
    # cover, organic impressions, engagement, page views, summary.
    report_timeframe = dataset["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    resolved_branding = resolve_report_branding(
        None,
        None,
        str(dataset.get("plan") or ""),
        preferred_branding=dataset.get("branding") if isinstance(dataset.get("branding"), dict) else None,
    )
    metric_context = {**dataset, "branding": resolved_branding}
    organic_impressions_payload = _build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="organic_impressions",
        title="ORGANIC IMPRESSIONS",
        label="TOTAL ORGANIC IMPRESSIONS",
        semantic_name="organic_impressions_overview",
    )
    engagement_payload = _build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="engagement",
        title="ENGAGEMENT",
        label="TOTAL ENGAGEMENT",
        semantic_name="engagement_overview",
    )
    page_views_payload = _build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="page_views",
        title="PAGE VIEWS",
        label="TOTAL PAGE VIEWS",
        semantic_name="page_views_overview",
    )
    followers_details = _facebook_pages_metric_details(metric_context, "followers")
    _log_facebook_pages_report_metric_payload(
        metric_context,
        {
            "metric_key": "followers",
            "metric_source": "followers_count",
            "total": followers_details.get("total"),
            "formatted_total": _format_metric_summary_value(followers_details.get("total")),
            "daily_series": [],
            "unavailable_reason": _facebook_metric_audit_reason(metric_context, "followers"),
        },
    )
    if _meta_integration_type(metric_context) in {"facebook_pages", "meta_pages"}:
        report_inputs = _meta_report_inputs(metric_context)
        logger.info(
            "[FiveSlideReport][facebook.debug]",
            extra={
                "integration": _meta_integration_type(metric_context),
                "dataset_keys_available": sorted(str(key) for key in metric_context.keys()),
                "report_inputs_keys_available": sorted(str(key) for key in report_inputs.keys()),
                "insights_keys": sorted(str(key) for key in (report_inputs.get("insights") or {}).keys())
                if isinstance(report_inputs.get("insights"), dict)
                else [],
                "daily_keys": sorted(str(key) for key in (report_inputs.get("daily") or {}).keys())
                if isinstance(report_inputs.get("daily"), dict)
                else [],
                "values_keys": sorted(str(key) for key in (report_inputs.get("values") or {}).keys())
                if isinstance(report_inputs.get("values"), dict)
                else [],
                "metric_values_keys": sorted(str(key) for key in (report_inputs.get("metric_values") or {}).keys())
                if isinstance(report_inputs.get("metric_values"), dict)
                else [],
                "chart_data_keys": sorted(str(key) for key in (metric_context.get("chart_data") or {}).keys())
                if isinstance(metric_context.get("chart_data"), dict)
                else [],
                "organic_impressions_daily_source_path": organic_impressions_payload.get("daily_series_source_path"),
                "organic_impressions_daily_source_metric_key": organic_impressions_payload.get("daily_series_source_metric_key"),
                "engagement_daily_source_path": engagement_payload.get("daily_series_source_path"),
                "engagement_daily_source_metric_key": engagement_payload.get("daily_series_source_metric_key"),
                "page_views_daily_source_path": page_views_payload.get("daily_series_source_path"),
                "page_views_daily_source_metric_key": page_views_payload.get("daily_series_source_metric_key"),
            },
        )
    blocks = [
        _meta_report_block(
            "title",
            1,
            {
                "slide_number": 1,
                "slide_type": "cover",
                "text": "Facebook Pages Report - Summary & Insights",
                "subtitle": dataset["page_name"],
                "page_name": dataset["page_name"],
                "platform": "Facebook Pages",
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
                "branding": resolved_branding,
                "brand_name": resolved_branding.get("resolved_brand_name"),
                "brand_logo_url": resolved_branding.get("resolved_logo_url"),
                "resolved_brand_name": resolved_branding.get("resolved_brand_name"),
                "resolved_logo_url": resolved_branding.get("resolved_logo_url"),
                "cover_branding": {
                    "resolved_brand_name": resolved_branding.get("resolved_brand_name"),
                    "resolved_logo_url": resolved_branding.get("resolved_logo_url"),
                },
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _meta_report_block(
            "stat",
            2,
            {
                "slide_number": 2,
                "slide_type": "metric",
                **organic_impressions_payload,
            },
        ),
        _meta_report_block(
            "stat",
            3,
            {
                "slide_number": 3,
                "slide_type": "metric",
                **engagement_payload,
            },
        ),
        _meta_report_block(
            "stat",
            4,
            {
                "slide_number": 4,
                "slide_type": "metric",
                **page_views_payload,
            },
        ),
        _meta_report_block(
            "text",
            5,
            _build_five_slide_summary_payload(
                metric_context,
                period_label=period_label,
                organic_impressions_payload=organic_impressions_payload,
                engagement_payload=engagement_payload,
                page_views_payload=page_views_payload,
            ),
            ["text"],
        ),
    ]
    final_blocks = _meta_enrich_data_blocks(metric_context, _renumber_blocks(blocks[:5]))
    _log_json_event(
        "FACEBOOK_REPORT_BLOCKS_CREATED",
        {
            "report_id": metric_context.get("report_id"),
            "dataset_id": metric_context.get("dataset_id"),
            "page_id": metric_context.get("page_id"),
            "page_name": metric_context.get("page_name"),
            "metric_name": "report_blocks",
            "source_metric": None,
            "raw_value": len(final_blocks),
            "sum_value": len(final_blocks),
            "points_count": len(final_blocks),
            "unavailable_reason": None,
            "block_mapping": [
                {
                    "order": block.get("order"),
                    "type": block.get("type"),
                    "semantic_name": (json.loads(block.get("data_json")) if isinstance(block.get("data_json"), str) else {}).get("semantic_name")
                    if block.get("data_json")
                    else None,
                }
                for block in final_blocks
            ],
        },
    )
    return final_blocks


def build_10_blocks(dataset: dict) -> list[dict]:
    report_timeframe = dataset["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    resolved_branding = resolve_report_branding(
        None,
        None,
        str(dataset.get("plan") or ""),
        preferred_branding=dataset.get("branding") if isinstance(dataset.get("branding"), dict) else None,
    )
    metric_context = {**dataset, "branding": resolved_branding}
    reach_payload = _build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="reach",
        title="Reach",
        label="Total Reach",
        semantic_name="reach",
    )
    impressions_payload = _build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="impressions",
        title="Impressions",
        label="Total Impressions",
        semantic_name="impressions",
    )
    reach_stats = _meta_series_stats(reach_payload.get("daily_series"))
    impressions_stats = _meta_series_stats(impressions_payload.get("daily_series"))
    engagement_payload = _meta_engagement_slide_payload(metric_context, period_label, reach_stats)
    engagement_payload.update(
        {
            "title": "Engagement",
            "label": "Total Engagement",
            "semantic_name": "engagement",
        }
    )
    reach_numeric = _meta_number(metric_context.get("reach"))
    engagement_numeric = _meta_number(metric_context.get("engagement"))
    if reach_numeric not in (None, 0) and engagement_numeric is not None:
        engagement_payload["secondary_metric"] = {
            "label": "Engagement Rate",
            "value": round((engagement_numeric / reach_numeric) * 100, 2),
            "formatted_value": _format_growth_label((engagement_numeric / reach_numeric) * 100).lstrip("+"),
        }
    page_visits_payload = buildMetricSlidePayload(
        metric_context,
        metric_key="page_views",
        metric_label="Page Visits",
    )
    page_visits_payload.update(
        {
            "title": "Page Visits",
            "label": "Page/Profile Visits",
            "semantic_name": "page_visits",
        }
    )
    followers_chart_points = _meta_metric_series(metric_context, "followers")
    if not followers_chart_points:
        followers_chart_points = _meta_series_points(
            (_meta_report_inputs(metric_context).get("followers_growth_daily") or [])
        )
    followers_chart = {
        "label": f"Audience Trend - {period_label}",
        "metric": "followers",
        "points": followers_chart_points,
        "data": followers_chart_points,
        "series": followers_chart_points,
        "timeframe": report_timeframe,
        "is_available": bool(followers_chart_points),
    }
    audience_growth_comparison = _comparison_growth_payload(
        metric_context,
        metric="followers",
        current_value=metric_context.get("followers"),
        current_points=followers_chart_points,
    )
    report_inputs = _meta_report_inputs(metric_context)
    net_follower_change = _meta_number(report_inputs.get("followers_growth"))
    if net_follower_change is None:
        net_follower_change = _meta_metric_total_for_series(
            "followers",
            _meta_series_points(report_inputs.get("followers_growth_daily") or []),
        )
    posts = _meta_recent_posts(metric_context)
    post_count = len(posts)
    posts_chart = _posts_chart_payload(posts, timeframe=report_timeframe, title=f"Posting Rhythm - {period_label}")
    content_activity_comparison = _comparison_growth_payload(
        metric_context,
        metric="posts",
        current_value=post_count,
        current_points=list(posts_chart.get("points") or []),
    )
    avg_reach_per_post = round((reach_numeric or 0) / post_count, 2) if post_count and reach_numeric is not None else None
    avg_engagement_per_post = (
        round((engagement_numeric or 0) / post_count, 2)
        if post_count and engagement_numeric is not None
        else None
    )
    top_posts = _top_content_items(posts)
    top_post = top_posts[0] if top_posts else None
    metric_summaries = [
        {"name": "reach", "label": "Reach", "value": _meta_number(metric_context.get("reach")), "growth": reach_payload.get("growth")},
        {"name": "impressions", "label": "Impressions", "value": _meta_number(metric_context.get("impressions")), "growth": impressions_payload.get("growth")},
        {"name": "engagement", "label": "Engagement", "value": _meta_number(metric_context.get("engagement")), "growth": engagement_payload.get("growth")},
        {"name": "page_visits", "label": "Page Visits", "value": _meta_number(page_visits_payload.get("total")), "growth": page_visits_payload.get("growth")},
    ]
    available_metric_summaries = [item for item in metric_summaries if item.get("value") is not None]
    strongest_metric = max(available_metric_summaries, key=lambda item: item.get("value") or 0) if available_metric_summaries else None
    weakest_metric = min(available_metric_summaries, key=lambda item: item.get("value") or 0) if available_metric_summaries else None
    trend_candidates = [item for item in available_metric_summaries if isinstance((item.get("growth") or {}).get("growth_percent"), (int, float, float))]
    trend_direction = (
        "improving"
        if trend_candidates and max((item.get("growth") or {}).get("growth_percent") or 0 for item in trend_candidates) > 0
        else "declining"
        if trend_candidates and max((item.get("growth") or {}).get("growth_percent") or 0 for item in trend_candidates) < 0
        else "mixed"
    )
    recommendations = [
        (
            f"Repeat the content pattern behind \"{top_post.get('title')}\" and adapt it into the next publishing cycle."
            if top_post
            else "Improve post-level tracking so the next report can identify which content pattern is actually winning."
        ),
        (
            "Protect a steady posting rhythm because content volume is supporting visibility."
            if post_count >= 3
            else "Increase publishing cadence during the next period so reach and engagement can build from a larger content base."
        ),
        (
            f"Address the weakest metric first: {weakest_metric.get('label')} needs a more specific optimization plan in the next cycle."
            if weakest_metric
            else "Review data availability before setting performance targets for the next cycle."
        ),
    ]
    if any((item.get("growth") or {}).get("growth_label") == "N/A" for item in metric_summaries):
        recommendations.append("Improve previous-period tracking coverage so growth can be measured instead of inferred as unavailable.")
    blocks = [
        _meta_report_block(
            "title",
            1,
            {
                "text": dataset["title"],
                "subtitle": f"{dataset['page_name']} performance report · {period_label}",
                "timeframe": report_timeframe,
                "period_label": report_timeframe.get("label"),
                "period_since": report_timeframe.get("since"),
                "period_until": report_timeframe.get("until"),
                "branding": resolved_branding,
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _meta_report_block(
            "stat",
            2,
            {
                **reach_payload,
                **_comparison_growth_payload(
                    metric_context,
                    metric="reach",
                    current_value=reach_payload.get("total"),
                    current_points=reach_payload.get("daily_series"),
                ),
                "semantic_name": "reach",
            },
        ),
        _meta_report_block(
            "stat",
            3,
            {
                **impressions_payload,
                **_comparison_growth_payload(
                    metric_context,
                    metric="impressions",
                    current_value=impressions_payload.get("total"),
                    current_points=impressions_payload.get("daily_series"),
                ),
                "semantic_name": "impressions",
            },
        ),
        _meta_report_block(
            "stat",
            4,
            {
                **engagement_payload,
                **_comparison_growth_payload(
                    metric_context,
                    metric="engagement",
                    current_value=engagement_payload.get("total"),
                    current_points=engagement_payload.get("daily_series"),
                ),
                "semantic_name": "engagement",
            },
        ),
        _meta_report_block(
            "stat",
            5,
            {
                **page_visits_payload,
                **_comparison_growth_payload(
                    metric_context,
                    metric="page_views",
                    current_value=page_visits_payload.get("total"),
                    current_points=page_visits_payload.get("daily_series"),
                ),
                "semantic_name": "page_visits",
            },
        ),
        _meta_report_block(
            "stat",
            6,
            {
                "title": "Audience Growth",
                "label": "Followers / Audience",
                "value": metric_context.get("followers"),
                "current_value": audience_growth_comparison.get("current_value"),
                "previous_value": audience_growth_comparison.get("previous_value"),
                "growth": audience_growth_comparison.get("growth"),
                "growth_percent": audience_growth_comparison.get("growth_percent"),
                "growth_label": audience_growth_comparison.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": followers_chart,
                "points": followers_chart_points,
                "metrics": {
                    "main": audience_growth_comparison.get("growth"),
                    "net_follower_change": net_follower_change,
                    "new_followers": net_follower_change if net_follower_change and net_follower_change > 0 else None,
                },
                "text": _meta_audience_growth_text(metric_context, period_label),
                "semantic_name": "audience_growth",
            },
        ),
        _meta_report_block(
            "stat",
            7,
            {
                "title": "Content Activity",
                "label": "Published Content",
                "value": post_count,
                "current_value": content_activity_comparison.get("current_value"),
                "previous_value": content_activity_comparison.get("previous_value"),
                "growth": content_activity_comparison.get("growth"),
                "growth_percent": content_activity_comparison.get("growth_percent"),
                "growth_label": content_activity_comparison.get("growth_label"),
                "comparison_period": "previous_period",
                "chart": posts_chart,
                "points": list(posts_chart.get("points") or []),
                "metrics": {
                    "main": content_activity_comparison.get("growth"),
                    "average_reach_per_post": avg_reach_per_post,
                    "average_engagement_per_post": avg_engagement_per_post,
                },
                "text": (
                    f"{post_count} content pieces were published in the selected period."
                    if post_count
                    else "No post-level content was available for the selected period."
                ),
                "semantic_name": "content_activity",
            },
        ),
        _meta_report_block(
            "text",
            8,
            {
                "title": "Top Performing Content",
                "text": (
                    f"\"{top_post.get('title')}\" generated the strongest available performance pattern in {period_label}."
                    if top_post
                    else "No post-level content exists for this period, so this slide is an empty state."
                ),
                "top_posts": top_posts,
                "main_metric": top_post,
                "empty_state": top_post is None,
                "semantic_name": "top_performing_content",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            9,
            {
                "title": "Executive Insights",
                "text": _multi_source_block_text_lines(
                    [
                        (
                            f"Strongest metric: {strongest_metric.get('label')} at {_meta_format_number(strongest_metric.get('value'))}."
                            if strongest_metric
                            else "Strongest metric could not be identified from the available data."
                        ),
                        (
                            f"Weakest metric: {weakest_metric.get('label')} at {_meta_format_number(weakest_metric.get('value'))}."
                            if weakest_metric
                            else "Weakest metric could not be identified from the available data."
                        ),
                        f"Trend direction: {trend_direction}.",
                        f"Business interpretation: {dataset['page_name']} generated visibility through reach and impressions while content response should be judged primarily through engagement and visit behavior.",
                    ]
                ),
                "metrics": {
                    "strongest_metric": strongest_metric,
                    "weakest_metric": weakest_metric,
                    "trend_direction": trend_direction,
                },
                "semantic_name": "executive_insights",
            },
            ["text"],
        ),
        _meta_report_block(
            "text",
            10,
            {
                "title": "Recommendations / Next Steps",
                "text": _multi_source_block_text_lines(recommendations[:5]),
                "recommendations": recommendations[:5],
                "semantic_name": "recommendations",
            },
            ["text"],
        ),
    ]
    blocks = _meta_enrich_data_blocks(metric_context, _renumber_blocks(blocks))
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
    # LEGACY / candidate for removal after frontend/backend contract is stable.
    # Recommended source of truth for official social 5-slide reports is build_5_blocks().
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
    # LEGACY / candidate for removal after frontend/backend contract is stable.
    # Recommended source of truth for official social 5-slide reports is build_5_blocks().
    return _meta_enrich_data_blocks(
        dataset,
        _renumber_blocks(_build_meta_report_block_pool(dataset)[:30]),
    )


def build_blocks(requested_slides: int, dataset: dict) -> list[dict]:
    # Official source of truth for social 5-slide reports.
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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    dataset = db.get(Dataset, payload.dataset_id)
    if not dataset:
        raise http_error(404, "dataset_not_found", "Dataset not found.")
    _require_workspace_access(db, current_user.id, dataset.workspace_id)
    report_limit_response = _enforce_report_creation_limit_or_response(db, dataset.workspace_id)
    if report_limit_response is not None:
        return report_limit_response
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
    report_branding = resolve_report_branding_for_workspace(
        db,
        dataset.workspace_id,
    )
    report = Report(
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        name=payload.title,
        description=json.dumps(
            {
                "locale": locale,
                "branding": report_branding,
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
        "[ReportBranding][resolved]",
        extra={
            "workspace_id": dataset.workspace_id,
            "report_id": report.id,
            "plan": slide_limits["plan"],
            "brand_name_original": str(current_user.full_name).strip() if current_user.full_name else None,
            "brand_logo_url_original": (
                str(current_user.logo_url).strip()
                if user_logo_column_available() and current_user.logo_url
                else None
            ),
            "resolved_brand_name": report_branding.get("resolved_brand_name"),
            "resolved_logo_url": report_branding.get("resolved_logo_url"),
            "has_custom_branding": report_branding.get("has_custom_branding"),
        },
    )
    if int(slide_limits["requested_slides"]) == 5:
        metric_payloads: dict[str, dict[str, Any]] = {}
        ai_summary_length = 0
        for block_spec in block_specs:
            raw_block_data = block_spec.get("data_json")
            if isinstance(raw_block_data, str):
                try:
                    block_data = json.loads(raw_block_data)
                except json.JSONDecodeError:
                    block_data = {}
            elif isinstance(raw_block_data, dict):
                block_data = raw_block_data
            else:
                block_data = {}
            semantic_name = str(block_data.get("semantic_name") or "").strip()
            if semantic_name in {"organic_impressions_overview", "engagement_overview", "page_views_overview"}:
                metric_payloads[semantic_name] = block_data
            elif semantic_name == "executive_summary":
                ai_summary_length = len(str(block_data.get("ai_summary") or block_data.get("text") or ""))
        logger.info(
            "[FiveSlideReport][structure]",
            extra={
                "report_id": report.id,
                "integration": report_inputs.get("integration_type"),
                "template": "executive_5_slide",
                "slide_count": int(slide_limits["requested_slides"]),
                "dataset_keys_available": sorted(report_row.keys()),
                "slide_2_metric_key": metric_payloads.get("organic_impressions_overview", {}).get("metric_key"),
                "slide_2_total": metric_payloads.get("organic_impressions_overview", {}).get("total"),
                "slide_2_daily_series_length": len(metric_payloads.get("organic_impressions_overview", {}).get("daily_series") or []),
                "slide_2_daily_series_source_path": metric_payloads.get("organic_impressions_overview", {}).get("daily_series_source_path"),
                "slide_2_daily_series_source_metric_key": metric_payloads.get("organic_impressions_overview", {}).get("daily_series_source_metric_key"),
                "slide_3_metric_key": metric_payloads.get("engagement_overview", {}).get("metric_key"),
                "slide_3_total": metric_payloads.get("engagement_overview", {}).get("total"),
                "slide_3_daily_series_length": len(metric_payloads.get("engagement_overview", {}).get("daily_series") or []),
                "slide_3_daily_series_source_path": metric_payloads.get("engagement_overview", {}).get("daily_series_source_path"),
                "slide_3_daily_series_source_metric_key": metric_payloads.get("engagement_overview", {}).get("daily_series_source_metric_key"),
                "slide_4_metric_key": metric_payloads.get("page_views_overview", {}).get("metric_key"),
                "slide_4_total": metric_payloads.get("page_views_overview", {}).get("total"),
                "slide_4_daily_series_length": len(metric_payloads.get("page_views_overview", {}).get("daily_series") or []),
                "slide_4_daily_series_source_path": metric_payloads.get("page_views_overview", {}).get("daily_series_source_path"),
                "slide_4_daily_series_source_metric_key": metric_payloads.get("page_views_overview", {}).get("daily_series_source_metric_key"),
                "slide_5_ai_summary_length": ai_summary_length,
            },
        )
    record_first_report_conversion(db, user_id=current_user.id)
    db.commit()
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
    _track_meta_event(
        event_name="ReportCreated",
        user=current_user,
        request=request,
        event_source_url=_tracking_event_source_url(request, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": dataset.workspace_id,
            "dataset_id": dataset.id,
            "generation_mode": "standard",
        },
    )

    integration_metadata = derive_report_integration_metadata(db, report, dataset=dataset)
    return ReportOut(
        id=report.id,
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        title=payload.title,
        status=None,
        folder_id=report.folder_id,
        folder_name=report.folder_name,
        description=_report_metadata(report),
        timeframe=_report_timeframe(report),
        report_sources=_report_sources_out(db, report_id=report.id),
        integration_metadata=integration_metadata,
        locale=locale,
        branding=_report_branding(db, report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _create_shopify_report(
    *,
    dataset: Dataset,
    payload: ShopifyReportCreateIn,
    current_user: User,
    request: Request,
    db: Session,
) -> MetaPagesReportCreateOut:
    dataset_data = dataset.data if isinstance(dataset.data, dict) else {}
    if str(dataset_data.get("integration_type") or "").strip().lower() != "shopify":
        raise http_error(400, "invalid_shopify_dataset", "Dataset is not a Shopify dataset.")
    dataset_file = _get_latest_dataset_file(db, dataset.id)
    if dataset_file is None:
        raise http_error(404, "dataset_file_not_found", "Dataset file not found.")
    requested_slides = payload.requested_slides if payload.requested_slides is not None else payload.slide_count
    if requested_slides not in (None, 5):
        raise http_error(400, "invalid_shopify_slide_count", "Shopify MVP currently supports only 5-slide reports.")
    report_limit_response = _enforce_report_creation_limit_or_response(db, dataset.workspace_id)
    if report_limit_response is not None:
        return report_limit_response
    slide_limits = resolve_report_slide_limits(
        db,
        dataset.workspace_id,
        requested_slides=5,
        default_slides=5,
    )
    locale = normalize_report_locale(payload.locale)
    ai_mode = normalize_ai_mode(payload.ai_mode)
    ai_plan_context = build_ai_agent_plan_context(
        plan=slide_limits["plan"],
        effective_slide_limit=slide_limits["effective_slide_limit"],
        dataset_context={"dataset_id": dataset.id},
        report_context={"generation_mode": "shopify", "ai_mode": ai_mode},
    )
    if ai_mode == "agents" and not ai_plan_context["allow_ai_agents"]:
        raise http_error(403, "plan_restricted", "AI agents are not available for current plan.")
    ai_agent_metadata = build_ai_agent_metadata(
        ai_mode=ai_mode,
        allow_ai_agents=bool(ai_plan_context["allow_ai_agents"]),
    )
    report_branding = resolve_report_branding_for_workspace(db, dataset.workspace_id)
    title = payload.title or f"{dataset_data.get('shop_name') or dataset_data.get('shop_domain') or 'Shopify'} Overview"
    timeframe = dataset_data.get("timeframe") if isinstance(dataset_data.get("timeframe"), dict) else {}
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == dataset.workspace_id, Integration.provider == SHOPIFY_PROVIDER)
        .order_by(Integration.id.asc())
        .first()
    )
    if integration is None:
        integration = _get_or_create_shopify_integration_for_workspace(db, dataset.workspace_id)
    connection = _shopify_connection_for_workspace(db, workspace_id=dataset.workspace_id)
    block_specs = _build_shopify_report_blocks(dataset_data, title=title, branding=report_branding)
    report = Report(
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        name=title,
        description=json.dumps(
            {
                "source": "shopify_v1",
                "locale": locale,
                "timeframe": timeframe,
                "branding": report_branding,
                "requested_slides": 5,
                "effective_slide_limit": slide_limits["effective_slide_limit"],
                "plan_at_generation": slide_limits["plan"],
                "generation_mode": "shopify",
                "plan_capabilities": slide_limits["capabilities"],
                **ai_agent_metadata,
            }
        ),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    record_first_report_conversion(db, user_id=current_user.id)
    db.commit()

    report_source = ReportSource(
        report_id=report.id,
        workspace_id=report.workspace_id,
        provider=SHOPIFY_PROVIDER,
        source_type=SHOPIFY_PROVIDER,
        integration_id=integration.id,
        integration_account_id=None,
        dataset_id=dataset.id,
        position=0,
        label=str(dataset_data.get("shop_name") or dataset_data.get("shop_domain") or "Shopify"),
        config_json={
            "shop_domain": dataset_data.get("shop_domain"),
            "shop_name": dataset_data.get("shop_name"),
            "channel": "shopify",
            "account_name": dataset_data.get("shop_name") or dataset_data.get("shop_domain"),
            "source_type": "shopify",
        },
    )
    db.add(report_source)
    db.commit()

    report_version = ReportVersion(report_id=report.id, version=1)
    db.add(report_version)
    db.commit()
    db.refresh(report_version)

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
        logger.exception("Shopify thumbnail generation failed", extra={"report_id": report.id})
    except Exception:
        logger.exception("Unexpected Shopify thumbnail generation failure", extra={"report_id": report.id})

    _track_meta_event(
        event_name="ReportCreated",
        user=current_user,
        request=request,
        event_source_url=_tracking_event_source_url(request, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": dataset.workspace_id,
            "dataset_id": dataset.id,
            "generation_mode": "shopify",
        },
    )
    integration_metadata = derive_report_integration_metadata(db, report, dataset=dataset)
    return MetaPagesReportCreateOut(
        report_id=report.id,
        version_id=report_version.id,
        version=report_version.version,
        dataset_id=dataset.id,
        title=title,
        locale=locale,
        status="ready",
        selected_integration_metadata=integration_metadata,
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


@app.post("/reports/shopify", response_model=MetaPagesReportCreateOut)
def create_shopify_report(
    payload: ShopifyReportCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPagesReportCreateOut:
    dataset = db.get(Dataset, payload.dataset_id)
    if dataset is None:
        raise http_error(404, "dataset_not_found", "Dataset not found.")
    _require_workspace_access(db, current_user.id, dataset.workspace_id)
    return _create_shopify_report(
        dataset=dataset,
        payload=payload,
        current_user=current_user,
        request=request,
        db=db,
    )


def _resolve_report_source_integration_account(
    db: Session,
    *,
    integration: Integration,
    source_workspace_id: int,
    raw_integration_account_id: int | str | None,
    config_json: dict[str, Any] | None,
) -> IntegrationAccount | None:
    if raw_integration_account_id is None and not isinstance(config_json, dict):
        return None

    external_candidates: list[str] = []
    internal_candidate: int | None = None

    if raw_integration_account_id is not None:
        raw_value = str(raw_integration_account_id).strip()
        if raw_value:
            if raw_value.isdigit():
                internal_candidate = int(raw_value)
            external_candidates.append(raw_value)

    if isinstance(config_json, dict):
        for key in ("external_account_id", "account_id"):
            candidate = str(config_json.get(key) or "").strip()
            if candidate:
                external_candidates.append(candidate)
        internal_from_config = config_json.get("integration_account_id")
        if internal_candidate is None and internal_from_config is not None:
            internal_raw = str(internal_from_config).strip()
            if internal_raw.isdigit():
                internal_candidate = int(internal_raw)

    integration_account: IntegrationAccount | None = None
    if internal_candidate is not None:
        candidate = db.get(IntegrationAccount, internal_candidate)
        if candidate and candidate.integration_id == integration.id:
            integration_account = candidate

    if integration_account is None:
        deduped_external_candidates = [
            candidate for candidate in dict.fromkeys(external_candidates) if candidate
        ]
        if deduped_external_candidates:
            integration_account = (
                db.query(IntegrationAccount)
                .filter(
                    IntegrationAccount.integration_id == integration.id,
                    IntegrationAccount.external_account_id.in_(deduped_external_candidates),
                )
                .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
                .first()
            )

    if integration_account is None:
        return None
    if integration_account.workspace_id != source_workspace_id:
        raise http_error(
            400,
            "source_workspace_mismatch",
            "Source integration account must belong to the same workspace.",
        )
    return integration_account


@app.post("/reports/multi-source", response_model=ReportOut)
def create_multi_source_report(
    payload: MultiSourceReportCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    if not (1 <= len(payload.sources) <= 2):
        raise http_error(
            400,
            "invalid_sources_count",
            "sources length must be between 1 and 2.",
        )
    requested_slides = (
        payload.requested_slides if payload.requested_slides is not None else payload.slide_count
    )
    if len(payload.sources) >= 2 and requested_slides != 10:
        raise http_error(
            400,
            "invalid_multi_source_slide_count",
            "Multi-source reports require the 10-slide format.",
        )

    seen_positions: set[int] = set()
    resolved_workspace_id: int | None = None
    first_dataset: Dataset | None = None
    resolved_sources: list[dict[str, Any]] = []
    multi_source_normalized_sources: list[dict[str, Any]] = []

    for index, source in enumerate(payload.sources):
        if not str(source.provider or "").strip():
            raise http_error(400, "invalid_source_provider", "provider is required for each source.")
        if not str(source.source_type or "").strip():
            raise http_error(400, "invalid_source_type", "source_type is required for each source.")
        if source.integration_id is None:
            raise http_error(400, "invalid_source_integration", "integration_id is required for each source.")
        if source.dataset_id is None and source.integration_account_id is None:
            raise http_error(
                400,
                "invalid_source_reference",
                "Each source must include dataset_id or integration_account_id.",
            )
        if source.position in seen_positions:
            raise http_error(400, "duplicate_source_position", "Each source position must be unique.")
        seen_positions.add(int(source.position))

        integration = db.get(Integration, int(source.integration_id))
        if not integration:
            raise http_error(404, "integration_not_found", "Integration not found.")
        _require_workspace_access(db, current_user.id, integration.workspace_id)

        source_workspace_id = integration.workspace_id
        dataset: Dataset | None = None
        if source.dataset_id is not None:
            dataset = db.get(Dataset, int(source.dataset_id))
            if not dataset:
                raise http_error(404, "dataset_not_found", "Dataset not found.")
            _require_workspace_access(db, current_user.id, dataset.workspace_id)
            if dataset.workspace_id != integration.workspace_id:
                raise http_error(
                    400,
                    "source_workspace_mismatch",
                    "Source dataset and integration must belong to the same workspace.",
                )
            source_workspace_id = dataset.workspace_id

        integration_account = _resolve_report_source_integration_account(
            db,
            integration=integration,
            source_workspace_id=source_workspace_id,
            raw_integration_account_id=source.integration_account_id,
            config_json=source.config_json,
        )
        if dataset is None and integration_account is None:
            raise http_error(404, "integration_account_not_found", "Integration account not found.")

        if resolved_workspace_id is None:
            resolved_workspace_id = source_workspace_id
        elif resolved_workspace_id != source_workspace_id:
            raise http_error(
                400,
                "source_workspace_mismatch",
                "All sources must belong to the same workspace.",
            )

        if index == 0:
            first_dataset = dataset
        source_config_json = dict(source.config_json) if isinstance(source.config_json, dict) else {}
        source_config_json.setdefault("provider", str(source.provider).strip())
        source_config_json.setdefault("source_type", str(source.source_type).strip())
        if integration_account is not None:
            source_config_json.setdefault("external_account_id", integration_account.external_account_id)
            source_config_json.setdefault("account_name", integration_account.display_name)
        if dataset is not None and isinstance(dataset.data, dict):
            dataset_account_name = (
                dataset.data.get("account_name")
                or dataset.data.get("page_name")
                or dataset.data.get("username")
            )
            dataset_external_account_id = dataset.data.get("account_id") or dataset.data.get("page_id")
            if dataset_external_account_id:
                source_config_json.setdefault("external_account_id", str(dataset_external_account_id))
            if dataset_account_name:
                source_config_json.setdefault("account_name", str(dataset_account_name))
        resolved_sources.append(
            {
                "provider": str(source.provider).strip(),
                "source_type": str(source.source_type).strip(),
                "integration_id": integration.id,
                "integration_account_id": integration_account.id if integration_account is not None else None,
                "dataset_id": dataset.id if dataset is not None else None,
                "position": int(source.position),
                "label": str(source.label).strip() if source.label else None,
                "config_json": source_config_json or None,
                "dataset": dataset,
            }
        )

    if first_dataset is None:
        raise http_error(
            400,
            "first_source_dataset_required",
            "The first source must include dataset_id for backwards-compatible report creation.",
        )
    if len(resolved_sources) >= 2 and not can_use_multi_platform_report(db, first_dataset.workspace_id):
        raise http_error(
            403,
            "plan_restricted",
            "Current plan does not allow multi-platform reports.",
        )
    report_limit_response = _enforce_report_creation_limit_or_response(db, first_dataset.workspace_id)
    if report_limit_response is not None:
        return report_limit_response

    locale = normalize_report_locale(payload.locale)
    ai_mode = normalize_ai_mode(payload.ai_mode)
    report_title = payload.title or first_dataset.name or "Multi-source report"
    timeframe = resolve_meta_pages_timeframe(
        payload.timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    branding = resolve_report_branding_for_workspace(
        db,
        first_dataset.workspace_id,
    )
    generate_multi_source_blocks = (
        len(resolved_sources) == 2
        and requested_slides == 10
        and all(isinstance(source.get("dataset"), Dataset) for source in resolved_sources)
    )
    if generate_multi_source_blocks:
        multi_source_normalized_sources = [
            _multi_source_normalize_source(source, dataset=source["dataset"], locale=locale)
            for source in resolved_sources
            if isinstance(source.get("dataset"), Dataset)
        ]
    metadata = {
        "source": "multi_source_v1",
        "kind": "multi_source",
        "locale": locale,
        "timeframe": timeframe,
        "branding": branding,
        "requested_slides": requested_slides,
        "generation_mode": "multi_source_visual_v1" if generate_multi_source_blocks else "multi_source_config_only",
        "ai_mode": ai_mode,
        "sources_count": len(resolved_sources),
        "report_status": "sources_configured",
        "visual_generation_pending": not generate_multi_source_blocks,
    }
    try:
        report = Report(
            workspace_id=first_dataset.workspace_id,
            dataset_id=first_dataset.id,
            name=report_title,
            description=json.dumps(metadata),
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        logger.info(
            "[ReportBranding][resolved]",
            extra={
                "workspace_id": first_dataset.workspace_id,
                "report_id": report.id,
                "plan": get_workspace_plan(db, first_dataset.workspace_id),
                "brand_name_original": str(current_user.full_name).strip() if current_user.full_name else None,
                "brand_logo_url_original": (
                    str(current_user.logo_url).strip()
                    if user_logo_column_available() and current_user.logo_url
                    else None
                ),
                "resolved_brand_name": branding.get("resolved_brand_name"),
                "resolved_logo_url": branding.get("resolved_logo_url"),
                "has_custom_branding": branding.get("has_custom_branding"),
            },
        )
        record_first_report_conversion(db, user_id=current_user.id)
        db.commit()

        report_sources = [
            ReportSource(
                report_id=report.id,
                workspace_id=report.workspace_id,
                provider=source["provider"],
                source_type=source["source_type"],
                integration_id=source["integration_id"],
                integration_account_id=source["integration_account_id"],
                dataset_id=source["dataset_id"],
                position=source["position"],
                label=source["label"],
                config_json=source["config_json"],
            )
            for source in resolved_sources
        ]
        db.add_all(report_sources)
        db.commit()

        report_version = ReportVersion(report_id=report.id, version=1)
        db.add(report_version)
        db.commit()
        db.refresh(report_version)

        if generate_multi_source_blocks and len(multi_source_normalized_sources) == 2:
            block_context = _multi_source_build_context(
                title=report_title,
                locale=locale,
                timeframe=timeframe,
                branding=branding,
                normalized_sources=multi_source_normalized_sources,
            )
            block_specs = _multi_source_build_10_blocks(block_context)
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
    except Exception:
        db.rollback()
        logger.exception(
            "multi_source_report_create_failed",
            extra={
                "workspace_id": first_dataset.workspace_id if first_dataset else None,
                "integration_ids": [source["integration_id"] for source in resolved_sources],
                "dataset_ids": [source["dataset_id"] for source in resolved_sources],
                "sources_count": len(resolved_sources),
            },
        )
        raise

    _track_meta_event(
        event_name="ReportCreated",
        user=current_user,
        request=request,
        event_source_url=_tracking_event_source_url(request, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "dataset_id": report.dataset_id,
            "generation_mode": "multi_source",
            "sources_count": len(resolved_sources),
        },
    )
    integration_metadata = derive_report_integration_metadata(db, report, dataset=first_dataset)
    return ReportOut(
        id=report.id,
        workspace_id=report.workspace_id,
        dataset_id=report.dataset_id,
        title=report.name,
        status="sources_configured",
        folder_id=report.folder_id,
        folder_name=report.folder_name,
        description=_report_metadata(report),
        timeframe=_report_timeframe(report),
        report_sources=_report_sources_out(db, report_id=report.id),
        integration_metadata=integration_metadata,
        version_id=report_version.id,
        version=report_version.version,
        locale=_report_locale(report),
        branding=_report_branding(db, report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _create_meta_dataset_report(
    *,
    dataset: Dataset,
    payload: MetaPagesReportCreateIn | InstagramBusinessReportCreateIn,
    current_user: User,
    request: Request | None,
    db: Session,
    report_source: str,
    generation_mode: str,
) -> MetaPagesReportCreateOut:
    locale = normalize_report_locale(payload.locale)
    dataset_file = _get_latest_dataset_file(db, dataset.id)
    if not dataset_file:
        raise http_error(404, "dataset_file_not_found", "Dataset file not found.")
    report_limit_response = _enforce_report_creation_limit_or_response(db, dataset.workspace_id)
    if report_limit_response is not None:
        return report_limit_response
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
    report_branding = resolve_report_branding_for_workspace(
        db,
        dataset.workspace_id,
    )
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
        "plan": slide_limits["plan"],
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
        "branding": report_branding,
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
                "branding": report_branding,
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
                "branding": report_branding,
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
        "[ReportBranding][resolved]",
        extra={
            "workspace_id": dataset.workspace_id,
            "report_id": report.id,
            "plan": slide_limits["plan"],
            "brand_name_original": str(current_user.full_name).strip() if current_user.full_name else None,
            "brand_logo_url_original": (
                str(current_user.logo_url).strip()
                if user_logo_column_available() and current_user.logo_url
                else None
            ),
            "resolved_brand_name": report_branding.get("resolved_brand_name"),
            "resolved_logo_url": report_branding.get("resolved_logo_url"),
            "has_custom_branding": report_branding.get("has_custom_branding"),
        },
    )
    record_first_report_conversion(db, user_id=current_user.id)
    db.commit()
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
            "organic_impressions_overview": "organic_impressions",
            "reach": "reach",
            "reach_overview": "reach",
            "impressions": "impressions",
            "impressions_overview": "impressions",
            "impressions_trend": "impressions",
            "followers": "followers",
            "audience_growth": "followers",
            "engagement": "engagement",
            "engagement_overview": "engagement",
            "page_visits": "page_views",
            "page_views": "page_views",
            "page_views_overview": "page_views",
            "content_activity": "posts",
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
        if semantic_name in {"organic_impressions_overview", "engagement_overview", "page_views_overview"}:
            chart = block_data.get("chart") if isinstance(block_data.get("chart"), dict) else {}
            chart_points = chart.get("points") if isinstance(chart.get("points"), list) else []
            first_chart_point = chart_points[0] if chart_points else None
            last_chart_point = chart_points[-1] if chart_points else None
            logger.info(
                "[BACKEND_METRIC_SLIDE_AUDIT]",
                extra={
                    "report_id": report.id,
                    "dataset_id": dataset.id,
                    "slide_block_index": block_spec.get("order"),
                    "block_title": block_data.get("title"),
                    "semantic_name": semantic_name,
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
        if semantic_name in {"organic_impressions_overview", "engagement_overview", "page_views_overview"}:
            logger.info(
                "[MetricSlidePayload][resolved]",
                extra={
                    "report_id": report.id,
                    "integration": report_inputs.get("integration_type"),
                    "metric_key": block_data.get("metric_key"),
                    "total": block_data.get("total"),
                    "daily_series_length": len(block_data.get("daily_series") or []),
                    "highest_day": block_data.get("highest_day"),
                    "lowest_day": block_data.get("lowest_day"),
                    "insight_length": len(str(block_data.get("insight_short") or block_data.get("insight") or "")),
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

    _track_meta_event(
        event_name="ReportCreated",
        user=current_user,
        request=request,
        event_source_url=_tracking_event_source_url(request, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": dataset.workspace_id,
            "dataset_id": dataset.id,
            "generation_mode": generation_mode,
            "report_source": report_source,
        },
    )
    integration_metadata = derive_report_integration_metadata(db, report, dataset=dataset)
    return MetaPagesReportCreateOut(
        report_id=report.id,
        version_id=report_version.id,
        version=report_version.version,
        dataset_id=dataset.id,
        title=title,
        locale=locale,
        status="ready",
        selected_integration_metadata=integration_metadata,
    )


@app.post("/reports/meta-pages", response_model=MetaPagesReportCreateOut)
def create_meta_pages_report(
    payload: MetaPagesReportCreateIn,
    request: Request,
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
        request=request,
        db=db,
        report_source="meta_pages_v2",
        generation_mode="meta_pages",
    )


@app.post("/reports/instagram-business", response_model=MetaPagesReportCreateOut)
def create_instagram_business_report(
    payload: InstagramBusinessReportCreateIn,
    request: Request,
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
            request=request,
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
    integration_type: str | None = Query(default=None),
    channel: str | None = Query(default=None),
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

        datasets_by_id = {
            dataset.id: dataset
            for dataset in db.query(Dataset).filter(Dataset.id.in_([report.dataset_id for report in reports])).all()
        }
        integration_filter = _canonical_report_integration_type(integration_type) if integration_type else None
        channel_filter = _canonical_report_integration_type(channel) if channel else None
        response: list[ReportListItemOut] = []
        for report in reports:
            integration_metadata = derive_report_integration_metadata(
                db,
                report,
                dataset=datasets_by_id.get(report.dataset_id),
            )
            metadata_integration = _canonical_report_integration_type(integration_metadata.integration_type)
            metadata_channel = _canonical_report_integration_type(integration_metadata.channel)
            if integration_filter and metadata_integration != integration_filter:
                continue
            if channel_filter and metadata_channel != channel_filter:
                continue
            response.append(
                ReportListItemOut(
                    id=report.id,
                    name=report.name,
                    status="completed" if version_counts.get(report.id, 0) > 0 else "pending",
                    folder_id=report.folder_id,
                    folder_name=report.folder_name,
                    integration_metadata=integration_metadata,
                    thumbnail_url=_report_thumbnail_url(report),
                    created_at=report.created_at,
                )
            )
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
    latest_version = _latest_report_version(db, report.id)
    metadata = _report_metadata(report)
    logger.info(
        "[MetaTimeframeBackend] report detail",
        extra={
            "report_id": report.id,
            "description_timeframe": _report_timeframe(report),
        },
    )
    integration_metadata = derive_report_integration_metadata(db, report)
    return ReportOut(
        id=report.id,
        workspace_id=report.workspace_id,
        dataset_id=report.dataset_id,
        title=report.name,
        status=_report_status(report),
        folder_id=report.folder_id,
        folder_name=report.folder_name,
        description=metadata,
        timeframe=_report_timeframe(report),
        report_sources=_report_sources_out(db, report_id=report.id),
        integration_metadata=integration_metadata,
        version_id=latest_version.id if latest_version else None,
        version=latest_version.version if latest_version else None,
        locale=_report_locale(report),
        branding=_report_branding(db, report),
        thumbnail_url=_report_thumbnail_url(report),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@app.post("/reports/{report_id}/share", response_model=ReportShareCreateOut)
def create_report_share(
    report_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportShareCreateOut:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    latest_share = _latest_report_share(db, report.id)
    share = _active_report_share(db, report.id)
    existing_share_found = latest_share is not None
    existing_share_expired = bool(
        latest_share is not None
        and latest_share.is_active
        and latest_share.revoked_at is None
        and latest_share.expires_at is not None
        and latest_share.expires_at <= datetime.now(timezone.utc)
    )
    new_share_created = False
    if share is None:
        share = ReportShare(
            report_id=report.id,
            workspace_id=report.workspace_id,
            token=secrets.token_urlsafe(32),
            is_active=True,
            created_by_user_id=current_user.id,
        )
        db.add(share)
        db.commit()
        db.refresh(share)

    share_url = f"{_frontend_share_base_url(request)}/share/reports/{share.token}"
    return ReportShareCreateOut(
        report_id=report.id,
        share_token=share.token,
        share_url=share_url,
    )


@app.delete("/reports/{report_id}/share", response_model=ReportShareRevokeOut)
def revoke_report_share(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportShareRevokeOut:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)

    share = _active_report_share(db, report.id)
    if share is not None:
        share.is_active = False
        share.revoked_at = datetime.now(timezone.utc)
        db.add(share)
        db.commit()

    return ReportShareRevokeOut(report_id=report.id, revoked=True)


@app.get("/public/reports/{share_token}", response_model=PublicReportOut)
def get_public_report(
    share_token: str,
    template: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PublicReportOut:
    share, report, report_version = _resolve_shared_report_context(db, share_token)
    effective_template, template_source = _effective_report_template(report, template)
    payload = _public_report_out(db, report, report_version, template_override=effective_template)
    logger.info(
        "[PUBLIC_REPORT_PAYLOAD_AUDIT]",
        extra={
            "share_token": share.token,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "has_blocks": bool(payload.blocks),
            "blocks_count": len(payload.blocks),
            "report_title": payload.report.title,
            "integration_type": payload.report.integration_type,
            "integration_label": payload.report.integration_label,
            "source_name": payload.report.source_name,
            "brand_name": payload.report.brand_name,
            "logo_url": payload.report.logo_url,
            "period_start": payload.report.period_start,
            "period_end": payload.report.period_end,
            "template": payload.report.template,
        },
    )
    logger.info(
        "[PDF_TEMPLATE_AUDIT]",
        extra={
            "report_id": report.id,
            "incoming_template": template,
            "stored_template": _stored_report_template(report),
            "effective_template": effective_template,
            "source": template_source,
        },
    )
    return payload


@app.get("/public/reports/{share_token}/download/pdf")
def download_public_report_pdf(
    share_token: str,
    request: Request,
    template: str | None = Query(default=None),
    request_ts: str | None = Query(default=None, alias="_ts"),
    db: Session = Depends(get_db),
) -> Response:
    normalized_share_token = str(share_token or "").strip()
    logger.info(
        "[PublicPDFExport][request]",
        extra={"share_token": normalized_share_token},
    )
    try:
        share, report, report_version = _resolve_shared_report_context(db, share_token)
    except HTTPException:
        logger.warning(
            "[PublicPDFExport][failure]",
            extra={
                "share_token": normalized_share_token,
                "reason": "share_link_not_found",
            },
        )
        raise

    logger.info(
        "[PublicPDFExport][share.lookup]",
        extra={
            "share_token": share.token,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
        },
    )

    frontend_url_env = _frontend_url()
    request_base_url = str(request.base_url).rstrip("/")
    try:
        frontend_url_used = _public_pdf_frontend_base_url()
    except HTTPException:
        logger.error(
            "[PublicPDFExport][config]",
            extra={
                "share_token": share.token,
                "frontend_url_env": frontend_url_env or None,
                "request_base_url": request_base_url,
                "final_render_url": None,
            },
        )
        raise
    effective_template, _template_source = _effective_report_template(report, template)
    timestamp = _pdf_export_timestamp(request_ts)
    export_query = {
        "export": "pdf",
        "_ts": timestamp,
    }
    if effective_template:
        export_query["template"] = effective_template
    export_url = f"{frontend_url_used}/share/reports/{share.token}?{urlencode(export_query)}"
    cache_key = _pdf_cache_key(
        report_id=report.id,
        version_id=report_version.id,
        effective_template=effective_template,
        report_updated_at=getattr(report, "updated_at", None),
        version_updated_at=getattr(report_version, "updated_at", None),
    )
    logger.info(
        "[PDF_CACHE_AUDIT]",
        extra={
            "report_id": report.id,
            "version_id": report_version.id,
            "template": effective_template,
            "incoming_template": template,
            "effective_template": effective_template,
            "cache_enabled": False,
            "cache_hit": False,
            "cache_key": cache_key,
            "regenerated": True,
            "final_render_url": export_url,
            "report_updated_at": report.updated_at.isoformat() if getattr(report, "updated_at", None) else None,
            "version_updated_at": report_version.updated_at.isoformat() if getattr(report_version, "updated_at", None) else None,
        },
    )
    logger.info(
        "[PDF_TEMPLATE_AUDIT]",
        extra={
            "report_id": report.id,
            "incoming_template": template,
            "stored_template": _stored_report_template(report),
            "effective_template": effective_template,
            "final_render_url": export_url,
        },
    )
    logger.info(
        "[PublicPDFExport][config]",
        extra={
            "share_token": share.token,
            "frontend_url_env": frontend_url_env or None,
            "request_base_url": request_base_url,
            "final_render_url": export_url,
        },
    )
    logger.info(
        "[PublicPDFExport][render.url]",
        extra={
            "share_token": share.token,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
            "render_url": export_url,
            "frontend_url_used": frontend_url_used,
            "status_http": None,
            "page_title": None,
            "page_text_excerpt": None,
        },
    )

    try:
        pdf_bytes, pdf_debug = generate_pdf_from_export_page(
            export_url=export_url,
            report_id=report.id,
            auth_token=None,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        logger.info(
            "[PDF_RENDER_RESPONSE]",
            extra={
                "status": detail.get("page_status"),
                "page_url": detail.get("page_url") or detail.get("export_url") or export_url,
                "page_title": detail.get("page_title"),
                "slide_count": detail.get("report_slide_count"),
                "pdf_bytes": None,
            },
        )
        logger.warning(
            "[PublicPDFExport][render.url]",
            extra={
                "share_token": share.token,
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "version_id": report_version.id,
                "version": report_version.version,
                "render_url": export_url,
                "frontend_url_used": frontend_url_used,
                "status_http": detail.get("page_status"),
                "page_title": detail.get("page_title"),
                "page_text_excerpt": detail.get("page_text_excerpt"),
            },
        )
        logger.warning(
            "[PublicPDFExport][render.response]",
            extra={
                "share_token": share.token,
                "status": detail.get("page_status"),
                "url": detail.get("page_url") or detail.get("export_url") or export_url,
                "title": detail.get("page_title"),
                "text_excerpt": detail.get("page_text_excerpt"),
                "data_pdf_ready_exists": detail.get("data_pdf_ready_exists"),
                "data_pdf_error_exists": detail.get("data_pdf_error_exists"),
                "report_slide_count": detail.get("report_slide_count"),
            },
        )
        if detail.get("code") == "pdf_render_failed":
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "public_pdf_render_failed",
                    "message": "PDF render page did not load the shared report content.",
                },
            )
        logger.warning(
            "[PublicPDFExport][failure]",
            extra={
                "share_token": share.token,
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "version_id": report_version.id,
                "version": report_version.version,
                "reason": "generate_pdf_failed",
            },
        )
        raise
    except Exception:
        logger.exception(
            "[PublicPDFExport][failure]",
            extra={
                "share_token": share.token,
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "version_id": report_version.id,
                "version": report_version.version,
                "reason": "unexpected_generate_pdf_failure",
            },
        )
        raise

    file_name = f"{_clean_pdf_file_name(report.name)}.pdf"
    logger.info(
        "[PDF_RENDER_RESPONSE]",
        extra={
            "status": pdf_debug.get("page_status"),
            "page_url": pdf_debug.get("page_url") or pdf_debug.get("export_url") or export_url,
            "page_title": pdf_debug.get("page_title"),
            "slide_count": pdf_debug.get("report_slide_count") or pdf_debug.get("page_count"),
            "pdf_bytes": len(pdf_bytes),
        },
    )
    logger.info(
        "[PublicPDFExport][render.url]",
        extra={
            "share_token": share.token,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
            "render_url": export_url,
            "frontend_url_used": frontend_url_used,
            "status_http": pdf_debug.get("page_status"),
            "page_title": pdf_debug.get("page_title"),
            "page_text_excerpt": pdf_debug.get("page_text_excerpt"),
        },
    )
    logger.info(
        "[PublicPDFExport][render.response]",
        extra={
            "share_token": share.token,
            "status": pdf_debug.get("page_status"),
            "url": pdf_debug.get("page_url") or pdf_debug.get("export_url") or export_url,
            "title": pdf_debug.get("page_title"),
            "text_excerpt": pdf_debug.get("page_text_excerpt"),
            "data_pdf_ready_exists": pdf_debug.get("data_pdf_ready_exists"),
            "data_pdf_error_exists": pdf_debug.get("data_pdf_error_exists"),
            "report_slide_count": pdf_debug.get("report_slide_count"),
        },
    )
    logger.info(
        "[PublicPDFExport][success]",
        extra={
            "share_token": share.token,
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
            "auth_strategy": pdf_debug.get("auth_strategy"),
            "report_fetch_succeeded": pdf_debug.get("report_fetch_succeeded"),
            "page_count": pdf_debug.get("page_count"),
            "file_name": file_name,
        },
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": f'attachment; filename="{file_name}"',
        },
    )


@app.patch("/reports/{report_id}/folder", response_model=ReportFolderUpdateOut)
def update_report_folder(
    report_id: int,
    payload: ReportFolderUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportFolderUpdateOut:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    report.folder_id = str(payload.folder_id or "").strip() or None
    report.folder_name = str(payload.folder_name or "").strip() or None
    db.add(report)
    db.commit()
    db.refresh(report)
    return ReportFolderUpdateOut(
        report_id=report.id,
        folder_id=report.folder_id,
        folder_name=report.folder_name,
        updated=True,
    )


@app.delete("/reports/{report_id}", response_model=ReportDeleteOut)
def delete_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDeleteOut:
    report = db.get(Report, report_id)
    if not report:
        logger.warning("report_delete_not_found", extra={"report_id": report_id, "user_id": current_user.id})
        raise http_error(404, "report_not_found", "Report not found.")

    try:
        _require_workspace_owner(db, current_user.id, report.workspace_id)
    except HTTPException:
        logger.warning(
            "report_delete_forbidden",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
            },
        )
        raise

    exports = (
        db.query(Export)
        .filter(Export.report_id == report.id)
        .order_by(Export.id.asc())
        .all()
    )
    schedules = (
        db.query(Schedule)
        .filter(Schedule.report_id == report.id)
        .order_by(Schedule.id.asc())
        .all()
    )
    export_ids = [export.id for export in exports]
    schedule_ids = [schedule.id for schedule in schedules]
    asset_keys = _report_delete_asset_keys(report, exports)

    logger.info(
        "report_delete_started",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "user_id": current_user.id,
            "report_version_count": len(report.versions),
            "export_count": len(export_ids),
            "schedule_count": len(schedule_ids),
            "asset_key_count": len(asset_keys),
        },
    )

    try:
        db.query(ReportSource).filter(ReportSource.report_id == report.id).delete(synchronize_session=False)
        if schedule_ids:
            db.query(Job).filter(Job.schedule_id.in_(schedule_ids)).update(
                {Job.schedule_id: None},
                synchronize_session=False,
            )
            db.query(Schedule).filter(Schedule.id.in_(schedule_ids)).delete(synchronize_session=False)
        if export_ids:
            db.query(Job).filter(Job.export_id.in_(export_ids)).update(
                {Job.export_id: None},
                synchronize_session=False,
            )
            db.query(Export).filter(Export.id.in_(export_ids)).delete(synchronize_session=False)
        db.delete(report)
        db.flush()
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception(
            "report_delete_database_failed",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "export_ids": export_ids,
                "schedule_ids": schedule_ids,
                **_sqlalchemy_error_log_payload(exc, stage="report_delete"),
            },
        )
        raise http_error(
            500,
            "report_delete_failed",
            "We could not delete the report right now. Please try again.",
        )
    except Exception:
        db.rollback()
        logger.exception(
            "report_delete_unexpected_failed",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "export_ids": export_ids,
                "schedule_ids": schedule_ids,
            },
        )
        raise http_error(
            500,
            "report_delete_failed",
            "We could not delete the report right now. Please try again.",
        )

    _cleanup_report_assets(report.id, asset_keys)
    logger.info(
        "report_delete_succeeded",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "user_id": current_user.id,
            "deleted_export_count": len(export_ids),
            "deleted_schedule_count": len(schedule_ids),
            "deleted_asset_key_count": len(asset_keys),
        },
    )
    return ReportDeleteOut(success=True)


@app.post("/reports/{report_id}/delete", response_model=ReportDeleteOut)
def delete_report_compat(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDeleteOut:
    return delete_report(report_id=report_id, current_user=current_user, db=db)


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

    payload = build_export_payload(db, export, report, report_version, blocks)
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
    _track_meta_event(
        event_name="ExportPPTX",
        user=current_user,
        event_source_url=_tracking_event_source_url(None, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "export_id": export.id,
            "format": "pptx",
        },
    )

    return {
        "status": export_result["status"],
        "download_url": export_result["download_url"],
        "file_name": export_result["file_name"],
    }


@app.get("/reports/{report_id}/download/pdf")
def download_report_pdf(
    request: Request,
    report_id: int,
    template: str | None = Query(default=None),
    request_ts: str | None = Query(default=None, alias="_ts"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    report = db.get(Report, report_id)
    if not report:
        raise http_error(404, "report_not_found", "Report not found.")
    _require_workspace_access(db, current_user.id, report.workspace_id)
    logger.info(
        "[PDFExport][request]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "user_id": current_user.id,
        },
    )
    try:
        enforce_export_capability(db, report.workspace_id, "pdf")
    except HTTPException:
        logger.warning(
            "[PDFExport][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "reason": "plan_restricted",
            },
        )
        raise

    report_version = _latest_report_version(db, report_id)
    if not report_version:
        logger.warning(
            "[PDFExport][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "reason": "report_version_not_found",
            },
        )
        raise http_error(404, "report_version_not_found", "Report version not found.")
    logger.info(
        "[PDFExport][version]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "user_id": current_user.id,
            "report_version_id": report_version.id,
            "version": report_version.version,
        },
    )

    latest_share = _latest_report_share(db, report.id)
    share = _active_report_share(db, report.id)
    existing_share_found = latest_share is not None
    existing_share_expired = bool(
        latest_share is not None
        and latest_share.is_active
        and latest_share.revoked_at is None
        and latest_share.expires_at is not None
        and latest_share.expires_at <= datetime.now(timezone.utc)
    )
    new_share_created = False
    if share is None:
        share = ReportShare(
            report_id=report.id,
            workspace_id=report.workspace_id,
            token=secrets.token_urlsafe(32),
            is_active=True,
            created_by_user_id=current_user.id,
        )
        db.add(share)
        db.commit()
        db.refresh(share)
        new_share_created = True
    frontend_url_env = str(settings.frontend_url or "").strip() or None
    frontend_base_url_env = str(settings.frontend_base_url or "").strip() or None
    report_export_base_url_env = str(settings.report_export_base_url or "").strip() or None
    report_export_path_template = str(settings.report_export_path_template or "").strip() or None
    export_lambda_url = str(settings.export_lambda_url or "").strip() or None
    try:
        frontend_url_used = _pdf_render_base_url()
    except HTTPException:
        logger.error(
            "[PDFExport][env.audit]",
            extra={
                "FRONTEND_URL": frontend_url_env,
                "FRONTEND_BASE_URL": frontend_base_url_env,
                "REPORT_EXPORT_BASE_URL": report_export_base_url_env,
                "REPORT_EXPORT_PATH_TEMPLATE": report_export_path_template,
                "EXPORT_LAMBDA_URL": export_lambda_url,
                "final_render_url": None,
                "report_id": report.id,
                "share_token": share.token,
                "version_id": report_version.id,
                "version": report_version.version,
                "existing_share_found": existing_share_found,
                "existing_share_expired": existing_share_expired,
                "new_share_created": new_share_created,
                "share_expires_at": share.expires_at.isoformat() if share.expires_at else None,
            },
        )
        raise
    effective_template, _template_source = _effective_report_template(report, template)
    timestamp = _pdf_export_timestamp(request_ts)
    export_query = {
        "export": "pdf",
        "_ts": timestamp,
    }
    if effective_template:
        export_query["template"] = effective_template
    export_url = f"{frontend_url_used}/share/reports/{share.token}?{urlencode(export_query)}"
    cache_key = _pdf_cache_key(
        report_id=report.id,
        version_id=report_version.id,
        effective_template=effective_template,
        report_updated_at=getattr(report, "updated_at", None),
        version_updated_at=getattr(report_version, "updated_at", None),
    )
    logger.info(
        "[PDF_CACHE_AUDIT]",
        extra={
            "report_id": report.id,
            "version_id": report_version.id,
            "template": effective_template,
            "incoming_template": template,
            "effective_template": effective_template,
            "cache_enabled": False,
            "cache_hit": False,
            "cache_key": cache_key,
            "regenerated": True,
            "final_render_url": export_url,
            "report_updated_at": report.updated_at.isoformat() if getattr(report, "updated_at", None) else None,
            "version_updated_at": report_version.updated_at.isoformat() if getattr(report_version, "updated_at", None) else None,
        },
    )
    logger.info(
        "[PDF_TEMPLATE_AUDIT]",
        extra={
            "report_id": report.id,
            "incoming_template": template,
            "stored_template": _stored_report_template(report),
            "effective_template": effective_template,
            "final_render_url": export_url,
        },
    )
    logger.info(
        "[PDFExport][env.audit]",
        extra={
            "FRONTEND_URL": frontend_url_env,
            "FRONTEND_BASE_URL": frontend_base_url_env,
            "REPORT_EXPORT_BASE_URL": report_export_base_url_env,
            "REPORT_EXPORT_PATH_TEMPLATE": report_export_path_template,
            "EXPORT_LAMBDA_URL": export_lambda_url,
            "final_render_url": export_url,
            "report_id": report.id,
            "share_token": share.token,
            "version_id": report_version.id,
            "version": report_version.version,
            "existing_share_found": existing_share_found,
            "existing_share_expired": existing_share_expired,
            "new_share_created": new_share_created,
            "share_expires_at": share.expires_at.isoformat() if share.expires_at else None,
        },
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
        "[PDFExport][render.url]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
            "render_url": export_url,
            "frontend_url_used": frontend_url_used,
            "status_http": None,
            "page_title": None,
            "page_text_excerpt": None,
        },
    )
    try:
        pdf_bytes, pdf_debug = generate_pdf_from_export_page(
            export_url=export_url,
            report_id=report_id,
            auth_token=None,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        logger.info(
            "[PDF_RENDER_RESPONSE]",
            extra={
                "status": detail.get("page_status"),
                "page_url": detail.get("page_url") or detail.get("export_url") or export_url,
                "page_title": detail.get("page_title"),
                "slide_count": detail.get("report_slide_count"),
                "pdf_bytes": None,
            },
        )
        logger.warning(
            "[PDFExport][render.url]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "version_id": report_version.id,
                "version": report_version.version,
                "render_url": export_url,
                "frontend_url_used": frontend_url_used,
                "status_http": detail.get("page_status"),
                "page_title": detail.get("page_title"),
                "page_text_excerpt": detail.get("page_text_excerpt"),
            },
        )
        logger.warning(
            "[PDFExport][render.response]",
            extra={
                "status": detail.get("page_status"),
                "url": detail.get("page_url") or detail.get("export_url") or export_url,
                "title": detail.get("page_title"),
                "text_excerpt": detail.get("page_text_excerpt"),
                "data_pdf_ready_exists": detail.get("data_pdf_ready_exists"),
                "data_pdf_error_exists": detail.get("data_pdf_error_exists"),
                "report_slide_count": detail.get("report_slide_count"),
            },
        )
        logger.warning(
            "[PDFExport][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "report_version_id": report_version.id,
                "version": report_version.version,
                "reason": "generate_pdf_failed",
            },
        )
        raise
    except Exception:
        logger.exception(
            "[PDFExport][failure]",
            extra={
                "report_id": report.id,
                "workspace_id": report.workspace_id,
                "user_id": current_user.id,
                "report_version_id": report_version.id,
                "version": report_version.version,
                "reason": "unexpected_generate_pdf_failure",
            },
        )
        raise

    file_name = f"{_clean_pdf_file_name(report.name)}.pdf"
    logger.info(
        "[PDF_RENDER_RESPONSE]",
        extra={
            "status": pdf_debug.get("page_status"),
            "page_url": pdf_debug.get("page_url") or pdf_debug.get("export_url") or export_url,
            "page_title": pdf_debug.get("page_title"),
            "slide_count": pdf_debug.get("report_slide_count") or pdf_debug.get("page_count"),
            "pdf_bytes": len(pdf_bytes),
        },
    )
    logger.info(
        "[PDFExport][render.url]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "version_id": report_version.id,
            "version": report_version.version,
            "render_url": export_url,
            "frontend_url_used": frontend_url_used,
            "status_http": pdf_debug.get("page_status"),
            "page_title": pdf_debug.get("page_title"),
            "page_text_excerpt": pdf_debug.get("page_text_excerpt"),
        },
    )
    logger.info(
        "[PDFExport][render.response]",
        extra={
            "status": pdf_debug.get("page_status"),
            "url": pdf_debug.get("page_url") or pdf_debug.get("export_url") or export_url,
            "title": pdf_debug.get("page_title"),
            "text_excerpt": pdf_debug.get("page_text_excerpt"),
            "data_pdf_ready_exists": pdf_debug.get("data_pdf_ready_exists"),
            "data_pdf_error_exists": pdf_debug.get("data_pdf_error_exists"),
            "report_slide_count": pdf_debug.get("report_slide_count"),
        },
    )
    logger.info(
        "[PDFExport][success]",
        extra={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "user_id": current_user.id,
            "report_version_id": report_version.id,
            "version": report_version.version,
            "auth_strategy": pdf_debug.get("auth_strategy"),
            "report_fetch_succeeded": pdf_debug.get("report_fetch_succeeded"),
            "page_count": pdf_debug.get("page_count"),
            "file_name": file_name,
        },
    )
    _track_meta_event(
        event_name="ExportPDF",
        user=current_user,
        request=request,
        event_source_url=_tracking_event_source_url(request, f"/reports/{report.id}"),
        custom_data={
            "report_id": report.id,
            "workspace_id": report.workspace_id,
            "format": "pdf",
        },
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Type": "application/pdf",
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
    workspace = Workspace(
        name=payload.brand_name if payload.brand_name is not None else payload.name,
        logo_url=payload.brand_logo_url if payload.brand_logo_url is not None else payload.logo_url,
    )
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

    original_brand_name = workspace.name
    original_brand_logo_url = workspace.logo_url
    branding_changed, account_changed = _update_workspace_from_payload(workspace, payload)

    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    if branding_changed or account_changed:
        resolved_branding = resolve_report_branding_for_workspace(db, workspace.id)
        logger.info(
            "[BrandAssets][workspace.saved]",
            extra={
                "workspace_id": workspace.id,
                "original_brand_name": original_brand_name,
                "saved_brand_name": workspace.name,
                "saved_account_display_name": workspace.account_display_name,
                "original_brand_logo_url": original_brand_logo_url,
                "saved_brand_logo_url": workspace.logo_url,
                "resolved_brand_name": resolved_branding.get("resolved_brand_name"),
                "resolved_logo_url": resolved_branding.get("resolved_logo_url"),
            },
        )
    return _workspace_out(db, workspace)


@app.patch("/workspaces/{workspace_id}/branding", response_model=WorkspaceOut)
def update_workspace_branding(
    workspace_id: int,
    payload: WorkspaceBrandingUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise http_error(404, "workspace_not_found", "Workspace not found.")
    _require_workspace_access(db, current_user.id, workspace_id)

    original_brand_name = workspace.name
    original_brand_logo_url = workspace.logo_url
    brand_name_provided, resolved_brand_name, logo_provided, resolved_logo_url = (
        _resolve_workspace_branding_update_payload(payload)
    )
    if brand_name_provided:
        workspace.name = resolved_brand_name or ""
    if logo_provided:
        workspace.logo_url = resolved_logo_url

    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    resolved_branding = resolve_report_branding_for_workspace(db, workspace.id)
    synced_reports = _sync_workspace_branding_to_reports(
        db,
        workspace_id=workspace.id,
        resolved_branding=resolved_branding,
    )
    logger.info(
        "[BrandAssets][workspace.saved]",
        extra={
            "endpoint": "/workspaces/{workspace_id}/branding",
            "workspace_id": workspace.id,
            "received_fields": sorted(payload.model_fields_set),
            "brand_name_provided": brand_name_provided,
            "logo_provided": logo_provided,
            "remove_logo": bool(payload.remove_logo) if "remove_logo" in payload.model_fields_set else False,
            "original_brand_name": original_brand_name,
            "saved_brand_name": workspace.name,
            "original_brand_logo_url": original_brand_logo_url,
            "saved_brand_logo_url": workspace.logo_url,
            "resolved_brand_name": resolved_branding.get("resolved_brand_name"),
            "resolved_logo_url": resolved_branding.get("resolved_logo_url"),
            "reports_branding_synced_count": synced_reports,
        },
    )
    return _workspace_out(db, workspace)


@app.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def patch_workspace(
    workspace_id: int,
    payload: WorkspaceUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    return update_workspace(
        workspace_id=workspace_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )


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


def _resolve_meta_ads_status_integration(
    db: Session,
    current_user: User,
    *,
    integration_id: int | None,
    workspace_id: int | None,
) -> Integration:
    if integration_id is not None:
        return _get_meta_ads_integration(db, current_user, integration_id)
    resolved_workspace_id = _resolve_meta_connect_workspace_id(
        db,
        user_id=current_user.id,
        requested_workspace_id=workspace_id,
    )
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    return _get_or_create_meta_ads_integration_for_workspace(db, resolved_workspace_id)


def _run_meta_ads_sync(
    *,
    db: Session,
    integration: Integration,
    account: MetaAdAccount,
    timeframe: str,
    start_date: str | None,
    end_date: str | None,
) -> MetaAdsSyncOut:
    access_token = _get_meta_ads_access_token(db, integration)
    timeframe_config = resolve_meta_pages_timeframe(
        timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    insights = fetch_campaign_insights(
        access_token,
        account.account_id,
        since=str(timeframe_config["since"]),
        until=str(timeframe_config["until"]),
    )

    db.query(MetaAdsInsightDaily).filter(
        MetaAdsInsightDaily.meta_ad_account_id == account.id,
        MetaAdsInsightDaily.date_start >= date.fromisoformat(str(timeframe_config["since"])),
        MetaAdsInsightDaily.date_start <= date.fromisoformat(str(timeframe_config["until"])),
    ).delete(synchronize_session=False)

    persisted_rows: list[dict[str, Any]] = []
    for row in insights:
        date_start_raw = str(row.get("date_start") or "").strip()
        date_stop_raw = str(row.get("date_stop") or date_start_raw).strip()
        if not date_start_raw or not date_stop_raw:
            continue
        try:
            row_date_start = date.fromisoformat(date_start_raw)
            row_date_stop = date.fromisoformat(date_stop_raw)
        except ValueError:
            continue

        db.add(
            MetaAdsInsightDaily(
                integration_id=integration.id,
                workspace_id=integration.workspace_id,
                meta_ad_account_id=account.id,
                date_start=row_date_start,
                date_stop=row_date_stop,
                spend=_meta_number(row.get("spend")),
                impressions=_meta_ads_int(row.get("impressions")) or None,
                reach=_meta_ads_int(row.get("reach")) or None,
                clicks=_meta_ads_int(row.get("clicks")) or None,
                inline_link_clicks=_meta_ads_int(row.get("inline_link_clicks")) or None,
                ctr=_meta_number(row.get("ctr")),
                cpc=_meta_number(row.get("cpc")),
                cpm=_meta_number(row.get("cpm")),
                frequency=_meta_number(row.get("frequency")),
                actions=row.get("actions") if isinstance(row.get("actions"), list) else None,
                cost_per_action_type=(
                    row.get("cost_per_action_type")
                    if isinstance(row.get("cost_per_action_type"), list)
                    else None
                ),
                campaign_id=str(row.get("campaign_id") or "").strip() or None,
                campaign_name=str(row.get("campaign_name") or "").strip() or None,
                adset_id=str(row.get("adset_id") or "").strip() or None,
                adset_name=str(row.get("adset_name") or "").strip() or None,
                ad_id=str(row.get("ad_id") or "").strip() or None,
                ad_name=str(row.get("ad_name") or "").strip() or None,
            )
        )
        persisted_rows.append(
            {
                "date_start": row_date_start.isoformat(),
                "date_stop": row_date_stop.isoformat(),
                "spend": round(_meta_ads_decimal(row.get("spend")), 2),
                "impressions": _meta_ads_int(row.get("impressions")),
                "reach": _meta_ads_int(row.get("reach")),
                "clicks": _meta_ads_int(row.get("clicks")),
                "inline_link_clicks": _meta_ads_int(row.get("inline_link_clicks")),
                "ctr": _meta_number(row.get("ctr")),
                "cpc": _meta_number(row.get("cpc")),
                "cpm": _meta_number(row.get("cpm")),
                "frequency": _meta_number(row.get("frequency")),
                "actions": row.get("actions") if isinstance(row.get("actions"), list) else [],
                "cost_per_action_type": (
                    row.get("cost_per_action_type")
                    if isinstance(row.get("cost_per_action_type"), list)
                    else []
                ),
                "campaign_id": str(row.get("campaign_id") or "").strip() or None,
                "campaign_name": str(row.get("campaign_name") or "").strip() or None,
                "adset_id": str(row.get("adset_id") or "").strip() or None,
                "adset_name": str(row.get("adset_name") or "").strip() or None,
                "ad_id": str(row.get("ad_id") or "").strip() or None,
                "ad_name": str(row.get("ad_name") or "").strip() or None,
            }
        )

    account.last_synced_at = datetime.now(timezone.utc)
    integration.status = "connected"
    db.add(account)
    db.add(integration)
    db.commit()
    db.refresh(account)

    dataset_data = _build_meta_ads_dataset_data(
        account=account,
        timeframe_config=timeframe_config,
        rows=persisted_rows,
    )
    csv_output = io.StringIO()
    fieldnames = [
        "date_start",
        "date_stop",
        "spend",
        "impressions",
        "reach",
        "clicks",
        "inline_link_clicks",
        "ctr",
        "cpc",
        "cpm",
        "frequency",
        "campaign_id",
        "campaign_name",
        "adset_id",
        "adset_name",
        "ad_id",
        "ad_name",
        "actions",
        "cost_per_action_type",
    ]
    writer = csv.DictWriter(csv_output, fieldnames=fieldnames)
    writer.writeheader()
    for row in persisted_rows:
        writer.writerow(
            {
                **row,
                "actions": json.dumps(row.get("actions") or []),
                "cost_per_action_type": json.dumps(row.get("cost_per_action_type") or []),
            }
        )
    csv_bytes = csv_output.getvalue().encode("utf-8")
    filename = f"meta_ads_{account.account_id}_{timeframe_config['since']}_{timeframe_config['until']}.csv"

    _enforce_workspace_storage_for_upload(db, integration.workspace_id, len(csv_bytes))
    dataset = Dataset(
        workspace_id=integration.workspace_id,
        name=filename,
        description="Meta Ads insights",
        data=dataset_data,
    )
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

    return MetaAdsSyncOut(
        integration_id=integration.id,
        dataset_id=dataset.id,
        dataset_file_id=dataset_file.id,
        ad_account_id=account.account_id,
        ad_account_name=account.account_name,
        status="synced",
        timeframe=dataset_data["timeframe"],
        last_synced_at=account.last_synced_at,
    )


@app.get("/integrations/meta-ads/connect", response_model=MetaAdsConnectOut)
def meta_ads_connect(
    workspace_id: int | None = Query(default=None),
    reconnect: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAdsConnectOut:
    resolved_workspace_id = _resolve_meta_connect_workspace_id(
        db,
        user_id=current_user.id,
        requested_workspace_id=workspace_id,
    )
    integration = _get_or_create_meta_ads_integration_for_workspace(db, resolved_workspace_id)
    state = encode_state(
        {
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "provider": "meta_ads",
            "integration_type": "meta_ads",
            "source": "meta_ads",
            "callback_route": "/integrations/meta-ads/callback",
            "reconnect": reconnect,
        }
    )
    _meta_oauth_log(
        "META_OAUTH_SCOPES_REQUESTED",
        provider="meta_ads",
        workspace_id=resolved_workspace_id,
        user_id=current_user.id,
        integration_id=integration.id,
        integration_type="meta_ads",
        reconnect_requested=reconnect,
        callback_route="/integrations/meta-ads/callback",
        redirect_uri=get_meta_ads_redirect_uri(),
        scopes_requested=META_ADS_SCOPES,
        scope=META_ADS_OAUTH_SCOPE,
    )
    return MetaAdsConnectOut(
        auth_url=oauth_connect_url(
            state,
            scope=META_ADS_OAUTH_SCOPE,
            redirect_uri=get_meta_ads_redirect_uri(),
            auth_type="rerequest",
            integration_type="meta_ads",
        ),
        integration_id=integration.id,
        scope=META_ADS_OAUTH_SCOPE,
        message="Connect Meta Ads to sync read-only ad performance data.",
    )


@app.get("/integrations/meta-ads/callback")
def meta_ads_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    selected_integration_type = "meta_ads"
    if error:
        _meta_oauth_log(
            "META_OAUTH_CALLBACK_RECEIVED",
            provider="meta_ads",
            integration_type=selected_integration_type,
            callback_route="/integrations/meta-ads/callback",
            code_received=bool(code),
            state_received=bool(state),
            error=str(error or "").strip() or None,
            error_reason=str(error_reason or "").strip() or None,
        )
        return _meta_oauth_popup_response(
            status="error",
            error=str(error_reason or error).strip() or "oauth_error",
            message=str(error_description or error_reason or "Meta returned an OAuth error.").strip(),
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )
    if not code or not state:
        _meta_oauth_log(
            "META_OAUTH_CALLBACK_RECEIVED",
            provider="meta_ads",
            integration_type=selected_integration_type,
            callback_route="/integrations/meta-ads/callback",
            code_received=bool(code),
            state_received=bool(state),
            error="missing_required_query_params",
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Meta Ads connection could not be verified. Please try again.",
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )

    try:
        state_payload = decode_state(state)
    except ValueError:
        _meta_oauth_log(
            "META_OAUTH_CALLBACK_RECEIVED",
            provider="meta_ads",
            integration_type=selected_integration_type,
            callback_route="/integrations/meta-ads/callback",
            code_received=bool(code),
            state_received=bool(state),
            error="invalid_state",
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Meta Ads connection request expired. Please try again.",
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )

    selected_integration_type = normalize_meta_oauth_integration_type(state_payload.get("integration_type"))
    requested_scopes = _meta_oauth_expected_scopes(selected_integration_type)
    integration_id = int(state_payload.get("integration_id") or 0)
    integration = db.get(Integration, integration_id)
    if integration is None or integration.provider != "meta_ads":
        return _meta_oauth_popup_response(
            status="error",
            error="integration_not_found",
            message="Meta Ads integration not found.",
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )

    try:
        token_payload = exchange_code_for_token(code, redirect_uri=get_meta_ads_redirect_uri())
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = str(detail.get("message") or exc.detail or "").strip()
        return _meta_oauth_popup_response(
            status="error",
            error="token_exchange_failed",
            message=message or "Could not finish the Meta Ads connection.",
            integration_id=integration.id,
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _meta_oauth_popup_response(
            status="error",
            error="missing_token",
            message="Meta Ads did not return an access token.",
            integration_id=integration.id,
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )

    debug_token_summary = _extract_debug_token_summary(debug_token(access_token))
    received_scopes = [
        str(scope).strip()
        for scope in debug_token_summary["scopes"]
        if str(scope).strip()
    ]
    _meta_oauth_log(
        "META_OAUTH_TOKEN_SCOPES_RECEIVED",
        provider="meta_ads",
        integration_type=selected_integration_type,
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        scopes_received=received_scopes,
        granular_target_ids=debug_token_summary["granular_target_ids"],
        requested_scopes=requested_scopes,
        token_valid=debug_token_summary["is_valid"],
    )
    missing_scopes = [scope for scope in requested_scopes if scope not in received_scopes]
    if missing_scopes:
        _meta_oauth_log(
            "META_PERMISSION_MISSING",
            provider="meta_ads",
            integration_type=selected_integration_type,
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            missing_scopes=missing_scopes,
            scopes_received=received_scopes,
        )

    token_account = _ensure_meta_ads_token_account(db, integration)
    _replace_integration_token(
        db,
        account_id=token_account.id,
        workspace_id=integration.workspace_id,
        access_token=access_token,
    )
    accounts_discovered: list[dict[str, Any]] = []
    try:
        for account in list_ad_accounts(access_token):
            account_id = str(account.get("account_id") or account.get("id") or "").strip()
            if not account_id:
                continue
            accounts_discovered.append(
                {
                    "account_id": account_id,
                    "name": str(account.get("name") or "").strip() or None,
                }
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _meta_oauth_log(
            "META_PERMISSION_MISSING",
            provider="meta_ads",
            integration_type=selected_integration_type,
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            missing_scopes=missing_scopes,
            asset_discovery_error=str(detail.get("message") or exc.detail or "").strip() or None,
        )
    _meta_oauth_log(
        "META_CONNECTED_ASSETS_DISCOVERED",
        provider="meta_ads",
        integration_type=selected_integration_type,
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        assets_count=len(accounts_discovered),
        assets=accounts_discovered,
    )
    if not accounts_discovered:
        _set_meta_integration_status(db, integration, status="disconnected")
        return _meta_oauth_popup_response(
            status="error",
            error="no_authorized_assets",
            message="Meta Ads connected, but no authorized ad accounts were returned.",
            integration_id=integration.id,
            callback_path="/integrations/meta-ads/callback",
            provider="meta_ads",
        )
    _set_meta_integration_status(db, integration, status="connected")
    return _meta_oauth_popup_response(
        status="connected",
        source="meta_ads",
        integration_id=integration.id,
        message="Meta Ads connected successfully.",
        callback_path="/integrations/meta-ads/callback",
        provider="meta_ads",
    )


@app.get("/integrations/meta-ads/status", response_model=MetaAdsStatusOut)
def meta_ads_status(
    integration_id: int | None = Query(default=None),
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAdsStatusOut:
    integration = _resolve_meta_ads_status_integration(
        db,
        current_user,
        integration_id=integration_id,
        workspace_id=workspace_id,
    )
    accounts = (
        db.query(MetaAdAccount)
        .filter(MetaAdAccount.integration_id == integration.id)
        .order_by(MetaAdAccount.is_selected.desc(), MetaAdAccount.account_name.asc(), MetaAdAccount.id.asc())
        .all()
    )
    selected_account = next((account for account in accounts if account.is_selected), None)
    connected = integration.status == "connected"
    return MetaAdsStatusOut(
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        connected=connected,
        status=integration.status,
        scope=META_ADS_OAUTH_SCOPE,
        selected_account=_meta_ads_account_out(selected_account, source="cache") if selected_account else None,
        accounts_count=len(accounts),
        last_synced_at=selected_account.last_synced_at if selected_account is not None else None,
        reconnect_required=integration.status == "reauthorization_required",
        permission_missing=integration.status == "reauthorization_required",
        message=_meta_ads_status_message(
            status=integration.status,
            connected=connected,
            accounts_count=len(accounts),
        ),
    )


@app.get("/integrations/meta-ads/accounts", response_model=list[MetaAdsAccountOut])
def meta_ads_accounts(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MetaAdsAccountOut]:
    integration = _get_meta_ads_integration(db, current_user, integration_id)
    access_token = _get_meta_ads_access_token(db, integration)
    try:
        accounts = list_ad_accounts(access_token)
    except HTTPException as exc:
        if _is_meta_ads_permission_error(exc):
            integration.status = "reauthorization_required"
            db.add(integration)
            db.commit()
            raise http_error(
                400,
                "meta_ads_permissions_missing",
                "Meta Ads permissions are missing for this connection.",
            ) from exc
        raise

    selected_record = _get_selected_meta_ads_account(db, integration.id)
    for account_payload in accounts:
        is_selected = (
            selected_record is not None
            and _normalize_meta_ad_account_id(str(account_payload.get("account_id") or account_payload.get("id") or ""))
            == selected_record.account_id
        )
        _upsert_meta_ads_account(
            db,
            integration=integration,
            account_payload=account_payload,
            is_selected=is_selected,
        )
    db.commit()

    stored_accounts = (
        db.query(MetaAdAccount)
        .filter(MetaAdAccount.integration_id == integration.id)
        .order_by(MetaAdAccount.is_selected.desc(), MetaAdAccount.account_name.asc(), MetaAdAccount.id.asc())
        .all()
    )
    return [_meta_ads_account_out(account, source="meta_api") for account in stored_accounts]


@app.post("/integrations/meta-ads/select-account", response_model=MetaAdsAccountOut)
def meta_ads_select_account(
    payload: MetaAdsSelectAccountIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAdsAccountOut:
    integration = _get_meta_ads_integration(db, current_user, payload.integration_id)
    access_token = _get_meta_ads_access_token(db, integration)
    accounts = list_ad_accounts(access_token)
    account_payload = _meta_ads_find_account_payload(accounts, payload.ad_account_id)
    if account_payload is None:
        raise http_error(
            400,
            "meta_ads_account_not_authorized",
            "The requested Meta ad account is not available for this token.",
        )
    selected_account = _save_meta_ads_selected_account(
        db,
        integration=integration,
        account_payload=account_payload,
    )
    return _meta_ads_account_out(selected_account, source="meta_api")


@app.post("/integrations/meta-ads/sync", response_model=MetaAdsSyncOut)
def meta_ads_sync(
    payload: MetaAdsSyncIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAdsSyncOut:
    integration = _get_meta_ads_integration(db, current_user, payload.integration_id)
    account = None
    if payload.ad_account_id:
        account = (
            db.query(MetaAdAccount)
            .filter(
                MetaAdAccount.integration_id == integration.id,
                MetaAdAccount.account_id == _normalize_meta_ad_account_id(payload.ad_account_id),
            )
            .first()
        )
    if account is None:
        account = _get_selected_meta_ads_account(db, integration.id)
    if account is None:
        raise http_error(
            400,
            "meta_ads_account_not_selected",
            "No Meta Ads account selected. Call POST /integrations/meta-ads/select-account first.",
        )

    try:
        return _run_meta_ads_sync(
            db=db,
            integration=integration,
            account=account,
            timeframe=payload.timeframe,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    except HTTPException as exc:
        if _is_meta_ads_permission_error(exc):
            integration.status = "reauthorization_required"
            db.add(integration)
            db.commit()
            raise http_error(
                400,
                "meta_ads_permissions_missing",
                "Meta connected, but Ads permissions are missing for this ad account.",
            ) from exc
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = str(detail.get("message") or "").lower()
        if detail.get("code") == "meta_api_error" and "expired" in message:
            integration.status = "reauthorization_required"
            db.add(integration)
            db.commit()
            raise http_error(
                401,
                "meta_ads_token_expired",
                "Meta Ads token expired. Reconnect required.",
            ) from exc
        raise


@app.delete("/integrations/meta-ads/disconnect", response_model=MetaAdsDisconnectOut)
def meta_ads_disconnect(
    integration_id: int | None = Query(default=None),
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAdsDisconnectOut:
    integration = _resolve_meta_ads_status_integration(
        db,
        current_user,
        integration_id=integration_id,
        workspace_id=workspace_id,
    )
    return _disconnect_meta_ads_integration(db, integration, revoke_permissions=True)


def _resolve_tiktok_sync_date_range(
    *,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str]:
    if start_date is None and end_date is None:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=27)
        end = today
        return start.isoformat(), end.isoformat()
    if start_date is None or end_date is None:
        raise http_error(
            422,
            "missing_date_range",
            "start_date and end_date are both required when specifying a custom TikTok sync range.",
        )
    start = _parse_iso_date_or_400(start_date, field_name="start_date")
    end = _parse_iso_date_or_400(end_date, field_name="end_date")
    if start > end:
        raise http_error(422, "invalid_date_range", "start_date must be on or before end_date.")
    return start.isoformat(), end.isoformat()


def _run_tiktok_sync(
    *,
    db: Session,
    current_user: User,
    integration: Integration,
    advertiser_id: str,
    advertiser_name: str,
    start_date: str,
    end_date: str,
) -> TikTokSyncOut:
    logger.info(
        "TikTok sync started",
        extra={
            "integration_id": integration.id,
            "workspace_id": integration.workspace_id,
            "user_id": current_user.id,
            "advertiser_id": advertiser_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    access_token = _get_tiktok_access_token(db, integration)
    report_payload = fetch_daily_advertiser_report(
        access_token,
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
    )
    rows = report_payload.get("rows") if isinstance(report_payload.get("rows"), list) else []
    if not rows:
        raise http_error(404, "tiktok_empty_report", "TikTok returned no report rows for this date range.")

    dataset_payload = normalize_tiktok_report_to_dataset_payload(
        advertiser_id=advertiser_id,
        advertiser_name=advertiser_name,
        start_date=start_date,
        end_date=end_date,
        report_payload=report_payload,
    )
    normalized_metrics = (
        dataset_payload.get("normalized_report_metrics")
        if isinstance(dataset_payload.get("normalized_report_metrics"), dict)
        else {}
    )

    csv_output = io.StringIO()
    metrics_requested = report_payload.get("metrics_requested") if isinstance(report_payload, dict) else []
    fieldnames = ["stat_time_day"] + [str(metric) for metric in metrics_requested if str(metric).strip()]
    writer = csv.DictWriter(csv_output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        writer.writerow(
            {
                "stat_time_day": dimensions.get("stat_time_day") or row.get("stat_time_day"),
                **{field: metrics.get(field) for field in fieldnames if field != "stat_time_day"},
            }
        )

    csv_bytes = csv_output.getvalue().encode("utf-8")
    filename = f"tiktok_ads_{advertiser_id}_{start_date}_{end_date}.csv"
    _enforce_workspace_storage_for_upload(db, integration.workspace_id, len(csv_bytes))
    dataset = Dataset(
        workspace_id=integration.workspace_id,
        name=filename,
        description="TikTok Ads insights",
        data=dataset_payload,
    )
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

    logger.info(
        "TikTok sync completed",
        extra={
            "integration_id": integration.id,
            "workspace_id": integration.workspace_id,
            "user_id": current_user.id,
            "advertiser_id": advertiser_id,
            "dataset_id": dataset.id,
            "dataset_file_id": dataset_file.id,
        },
    )
    return TikTokSyncOut(
        integration_id=integration.id,
        advertiser_id=advertiser_id,
        advertiser_name=advertiser_name,
        dataset_id=dataset.id,
        dataset_file_id=dataset_file.id,
        status="uploaded",
        start_date=start_date,
        end_date=end_date,
        metrics_summary={
            "reach_total": normalized_metrics.get("reach_total"),
            "impressions_total": normalized_metrics.get("impressions_total"),
            "engagement_total": normalized_metrics.get("engagement_total"),
            "link_clicks_total": normalized_metrics.get("link_clicks_total"),
            "spend_total": normalized_metrics.get("spend_total"),
            "conversions_total": normalized_metrics.get("conversions_total"),
        },
    )


@app.get("/integrations/tiktok/connect", response_model=TikTokConnectOut)
def tiktok_connect(
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokConnectOut:
    resolved_workspace_id = _resolve_tiktok_workspace_id(
        db,
        user_id=current_user.id,
        workspace_id=workspace_id,
    )
    integration = _get_or_create_tiktok_integration_for_workspace(db, resolved_workspace_id)
    state = encode_tiktok_state(
        {
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "source": TIKTOK_OAUTH_STATE_SOURCE,
        }
    )
    logger.info(
        "TikTok connect started",
        extra={
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
        },
    )
    return TikTokConnectOut(
        auth_url=build_tiktok_authorization_url(state),
        integration_id=integration.id,
    )


@app.post("/integrations/tiktok/callback/complete", response_model=TikTokCallbackCompleteOut)
def tiktok_callback_complete(
    payload: TikTokCallbackCompleteIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokCallbackCompleteOut:
    logger.info("TikTok callback completion started", extra={"user_id": current_user.id})
    try:
        state_payload = decode_tiktok_state(payload.state)
    except ValueError as exc:
        raise http_error(400, "invalid_state", "The TikTok connection state is invalid or expired.") from exc

    try:
        state_user_id = int(state_payload.get("user_id", 0))
        workspace_id = int(state_payload.get("workspace_id", 0))
        state_integration_id = int(state_payload.get("integration_id", 0))
    except (TypeError, ValueError) as exc:
        raise http_error(400, "invalid_state", "The TikTok connection state is invalid.") from exc

    if state_user_id != current_user.id:
        raise http_error(403, "state_user_mismatch", "TikTok connection does not belong to the authenticated user.")

    _require_workspace_access(db, current_user.id, workspace_id)
    integration = _resolve_tiktok_integration(
        db,
        current_user=current_user,
        integration_id=state_integration_id,
        workspace_id=workspace_id,
    )
    logger.info(
        "TikTok token exchange started",
        extra={
            "integration_id": integration.id,
            "workspace_id": workspace_id,
            "user_id": current_user.id,
        },
    )
    token_data = exchange_auth_code_for_token(code=payload.code, auth_code=payload.auth_code)
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise http_error(400, "missing_token", "TikTok did not return an access token.")

    expires_at = None
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, (int, float)) or str(expires_in or "").isdigit():
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    token_account = _get_or_create_tiktok_token_account(db, integration)
    _replace_integration_token_with_refresh(
        db,
        account_id=token_account.id,
        workspace_id=integration.workspace_id,
        access_token=access_token,
        refresh_token=str(token_data.get("refresh_token") or "").strip() or None,
        expires_at=expires_at,
    )
    integration.status = "connected"
    db.add(integration)
    db.commit()
    db.refresh(integration)
    logger.info(
        "TikTok token exchange success",
        extra={"integration_id": integration.id, "workspace_id": workspace_id, "user_id": current_user.id},
    )

    advertisers: list[dict[str, Any]] = []
    advertisers_message: str | None = None
    try:
        advertisers = get_authorized_advertisers(access_token)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        advertisers_message = str(detail.get("message") or "TikTok advertiser accounts could not be loaded.")
        logger.warning(
            "TikTok advertisers fetch failed",
            extra={
                "integration_id": integration.id,
                "workspace_id": workspace_id,
                "user_id": current_user.id,
                "error": detail or str(exc.detail),
            },
        )

    stored_accounts = _store_tiktok_advertisers(db, integration=integration, advertisers=advertisers)
    logger.info(
        "TikTok advertisers fetched/stored",
        extra={
            "integration_id": integration.id,
            "workspace_id": workspace_id,
            "user_id": current_user.id,
            "advertisers_count": len(stored_accounts),
        },
    )

    selected_account = _get_selected_tiktok_advertiser_account(db, integration)
    if selected_account is None and stored_accounts:
        selected_account = _select_tiktok_advertiser_account(
            db,
            integration=integration,
            advertiser_id=stored_accounts[0].external_account_id,
        )
    selected_advertiser_id = (
        str(selected_account.external_account_id).strip() if selected_account is not None else None
    )
    selected_out = (
        _tiktok_advertiser_out(
            db,
            integration=integration,
            account=selected_account,
            selected_advertiser_id=selected_advertiser_id,
        )
        if selected_account is not None
        else None
    )
    return TikTokCallbackCompleteOut(
        integration_id=integration.id,
        advertisers_count=len(stored_accounts),
        selected_account=selected_out,
        message=advertisers_message,
    )


@app.get("/integrations/tiktok/advertiser-accounts", response_model=TikTokAdvertiserAccountsOut)
def tiktok_advertiser_accounts(
    integration_id: int | None = None,
    workspace_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokAdvertiserAccountsOut:
    integration = _resolve_tiktok_integration(
        db,
        current_user=current_user,
        integration_id=integration_id,
        workspace_id=workspace_id,
    )
    selected_account = _get_selected_tiktok_advertiser_account(db, integration)
    selected_advertiser_id = (
        str(selected_account.external_account_id).strip() if selected_account is not None else None
    )
    accounts = [
        _tiktok_advertiser_out(
            db,
            integration=integration,
            account=account,
            selected_advertiser_id=selected_advertiser_id,
        )
        for account in _list_tiktok_advertiser_accounts(db, integration.id)
    ]
    return TikTokAdvertiserAccountsOut(
        accounts=accounts,
        message=None if accounts else "No TikTok advertiser accounts are stored for this workspace yet.",
    )


@app.post("/integrations/tiktok/select-account", response_model=TikTokAdvertiserAccountOut)
def tiktok_select_account(
    payload: TikTokSelectAccountIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokAdvertiserAccountOut:
    integration = _resolve_tiktok_integration(
        db,
        current_user=current_user,
        integration_id=payload.integration_id,
        workspace_id=payload.workspace_id,
    )
    selected_account = _select_tiktok_advertiser_account(
        db,
        integration=integration,
        advertiser_id=payload.advertiser_id,
    )
    logger.info(
        "TikTok account selected",
        extra={
            "integration_id": integration.id,
            "workspace_id": integration.workspace_id,
            "user_id": current_user.id,
            "advertiser_id": payload.advertiser_id,
        },
    )
    return _tiktok_advertiser_out(
        db,
        integration=integration,
        account=selected_account,
        selected_advertiser_id=str(selected_account.external_account_id or "").strip(),
    )


@app.post("/integrations/tiktok/sync", response_model=TikTokSyncOut)
def tiktok_sync(
    payload: TikTokSyncIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokSyncOut:
    integration = _resolve_tiktok_integration(
        db,
        current_user=current_user,
        integration_id=payload.integration_id,
        workspace_id=payload.workspace_id,
    )
    advertiser_id = str(payload.advertiser_id or "").strip()
    selected_account = None
    if advertiser_id:
        selected_account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id == advertiser_id,
            )
            .first()
        )
    else:
        selected_account = _get_selected_tiktok_advertiser_account(db, integration)

    if selected_account is None:
        raise http_error(
            400,
            "no_selected_advertiser_account",
            "No TikTok advertiser account selected. Call POST /integrations/tiktok/select-account first.",
        )

    start_date, end_date = _resolve_tiktok_sync_date_range(
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return _run_tiktok_sync(
        db=db,
        current_user=current_user,
        integration=integration,
        advertiser_id=str(selected_account.external_account_id),
        advertiser_name=str(selected_account.display_name or selected_account.external_account_id),
        start_date=start_date,
        end_date=end_date,
    )


@app.get("/integrations/tiktok/status", response_model=TikTokStatusOut)
def tiktok_status(
    workspace_id: int | None = Query(default=None),
    integration_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokStatusOut:
    missing_flags = tiktok_missing_env_flags()
    missing_env = missing_flags["app_id_missing"] or missing_flags["secret_missing"]

    integration: Integration | None = None
    if integration_id is not None:
        integration = db.get(Integration, int(integration_id))
        if integration is not None:
            if integration.provider != "tiktok_ads":
                raise http_error(404, "integration_not_found", "TikTok integration not found.")
            _require_workspace_access(db, current_user.id, integration.workspace_id)
    else:
        resolved_workspace_id = _resolve_tiktok_workspace_id(
            db,
            user_id=current_user.id,
            workspace_id=workspace_id,
        )
        integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == resolved_workspace_id, Integration.provider == "tiktok_ads")
            .order_by(Integration.id.asc())
            .first()
        )

    if integration is None:
        return TikTokStatusOut(missing_env=missing_env)

    selected_account = _get_selected_tiktok_advertiser_account(db, integration)
    selected_advertiser_id = (
        str(selected_account.external_account_id).strip() if selected_account is not None else None
    )
    advertisers = _list_tiktok_advertiser_accounts(db, integration.id)
    selected_out = (
        _tiktok_advertiser_out(
            db,
            integration=integration,
            account=selected_account,
            selected_advertiser_id=selected_advertiser_id,
        )
        if selected_account is not None
        else None
    )
    return TikTokStatusOut(
        connected=str(integration.status or "").strip().lower() == "connected",
        status=str(integration.status or "disconnected"),
        advertisers_count=len(advertisers),
        selected_advertiser=selected_out,
        last_sync=_tiktok_account_last_synced_at(
            db,
            workspace_id=integration.workspace_id,
            advertiser_id=selected_advertiser_id,
        ),
        missing_env=missing_env,
        integration_id=integration.id,
    )


@app.post("/integrations/tiktok/disconnect", response_model=TikTokDisconnectOut)
def tiktok_disconnect(
    integration_id: int | None = Body(default=None),
    workspace_id: int | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokDisconnectOut:
    integration = _resolve_tiktok_integration(
        db,
        current_user=current_user,
        integration_id=integration_id,
        workspace_id=workspace_id,
    )
    return _disconnect_tiktok_integration(db, integration)


def _resolve_shopify_workspace_id(
    db: Session,
    *,
    user_id: int,
    workspace_id: int | None,
) -> int:
    return _resolve_meta_connect_workspace_id(
        db,
        user_id=user_id,
        requested_workspace_id=workspace_id,
    )


def _get_or_create_shopify_integration_for_workspace(db: Session, workspace_id: int) -> Integration:
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == SHOPIFY_PROVIDER)
        .order_by(Integration.id.asc())
        .first()
    )
    if integration is not None:
        return integration
    integration = Integration(
        workspace_id=workspace_id,
        provider=SHOPIFY_PROVIDER,
        name="Shopify",
        status=SHOPIFY_STATUS_DISCONNECTED,
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return integration


def _shopify_connection_for_workspace(db: Session, *, workspace_id: int) -> ShopifyConnection | None:
    return (
        db.query(ShopifyConnection)
        .filter(ShopifyConnection.workspace_id == workspace_id)
        .order_by(ShopifyConnection.updated_at.desc(), ShopifyConnection.id.desc())
        .first()
    )


def _resolve_shopify_connection(
    db: Session,
    *,
    current_user: User,
    workspace_id: int | None,
) -> ShopifyConnection | None:
    resolved_workspace_id = _resolve_shopify_workspace_id(db, user_id=current_user.id, workspace_id=workspace_id)
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    return _shopify_connection_for_workspace(db, workspace_id=resolved_workspace_id)


def _shopify_success_redirect_url() -> str:
    return (
        str(settings.shopify_connect_success_redirect or "").strip()
        or str(settings.frontend_base_url or settings.frontend_url or "").rstrip("/")
        or "http://localhost:3000"
    )


def _shopify_error_redirect_url() -> str:
    return (
        str(settings.shopify_connect_error_redirect or "").strip()
        or _shopify_success_redirect_url()
    )


def _redirect_with_query(base_url: str, **params: Any) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value not in (None, "")}
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}{urlencode(query)}" if query else base_url
    return RedirectResponse(url=url, status_code=302)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _shopify_status_message(connection: ShopifyConnection | None) -> str | None:
    if connection is None:
        return "Connect Shopify to sync store sales and create reports."
    if str(connection.status or "").strip().lower() == SHOPIFY_STATUS_ERROR:
        return "Reconnect Shopify to restore access."
    if not str(connection.access_token_encrypted or "").strip():
        return "Reconnect Shopify to restore access."
    return None


def _shopify_status_out(connection: ShopifyConnection | None, integration_id: int | None = None) -> ShopifyStatusOut:
    if connection is None:
        return ShopifyStatusOut(
            connected=False,
            status=SHOPIFY_STATUS_DISCONNECTED,
            integration_id=integration_id,
            reconnect_required=False,
            message=_shopify_status_message(None),
        )
    status = str(connection.status or SHOPIFY_STATUS_DISCONNECTED).strip().lower()
    return ShopifyStatusOut(
        connected=status == SHOPIFY_STATUS_CONNECTED and bool(str(connection.access_token_encrypted or "").strip()),
        status=status,
        integration_id=connection.integration_id,
        shop_domain=connection.shop_domain,
        shop_name=connection.shop_name,
        last_sync_at=connection.last_sync_at,
        reconnect_required=status == SHOPIFY_STATUS_ERROR or not bool(str(connection.access_token_encrypted or "").strip()),
        message=_shopify_status_message(connection),
    )


def _format_shopify_currency(amount: float | int | None, currency: str | None) -> str:
    if amount is None:
        return "N/A"
    currency_code = str(currency or "USD").upper()
    symbols = {"USD": "$", "MXN": "$", "EUR": "EUR ", "GBP": "GBP "}
    prefix = symbols.get(currency_code, f"{currency_code} ")
    return f"{prefix}{float(amount):,.2f}"


def _shopify_block(block_type: str, order: int, data: dict[str, Any], editable_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": block_type,
        "order": order,
        "data_json": json.dumps(data),
        "editable_fields_json": json.dumps(editable_fields or []),
    }


def _build_shopify_report_blocks(dataset_row: dict[str, Any], *, title: str, branding: dict[str, Any]) -> list[dict[str, Any]]:
    timeframe = dataset_row.get("timeframe") if isinstance(dataset_row.get("timeframe"), dict) else {}
    period_label = str(timeframe.get("label") or "Selected period")
    shop_name = str(dataset_row.get("shop_name") or dataset_row.get("shop_domain") or "Shopify store")
    currency = str(dataset_row.get("currency") or "USD")
    revenue = float(dataset_row.get("revenue") or 0.0)
    orders = int(dataset_row.get("orders") or 0)
    aov = float(dataset_row.get("aov") or 0.0)
    sales_by_day = dataset_row.get("sales_by_day") if isinstance(dataset_row.get("sales_by_day"), list) else []
    orders_by_day = dataset_row.get("orders_by_day") if isinstance(dataset_row.get("orders_by_day"), list) else []
    top_products = dataset_row.get("top_products") if isinstance(dataset_row.get("top_products"), list) else []
    top_variants = dataset_row.get("top_variants") if isinstance(dataset_row.get("top_variants"), list) else []
    summary = str(dataset_row.get("summary") or "").strip() or "No Shopify summary available."
    blocks = [
        _shopify_block(
            "title",
            1,
            {
                "slide_number": 1,
                "slide_type": "cover",
                "text": title,
                "subtitle": f"{shop_name} performance report · {period_label}",
                "timeframe": timeframe,
                "branding": branding,
                "semantic_name": "cover",
            },
            ["text", "subtitle"],
        ),
        _shopify_block(
            "stat",
            2,
            {
                "slide_number": 2,
                "slide_type": "metric",
                "title": "Revenue",
                "label": "Revenue",
                "metric_key": "revenue",
                "total": revenue,
                "current_value": revenue,
                "formatted_total": _format_shopify_currency(revenue, currency),
                "daily_series": sales_by_day,
                "chart": {"label": f"Sales by day - {period_label}", "metric": "revenue", "points": sales_by_day},
                "currency": currency,
                "summary": f"Revenue closed at {_format_shopify_currency(revenue, currency)}.",
                "semantic_name": "revenue_overview",
            },
        ),
        _shopify_block(
            "stat",
            3,
            {
                "slide_number": 3,
                "slide_type": "metric",
                "title": "Orders",
                "label": "Orders",
                "metric_key": "orders",
                "total": orders,
                "current_value": orders,
                "formatted_total": f"{orders:,}",
                "daily_series": orders_by_day,
                "chart": {"label": f"Orders by day - {period_label}", "metric": "orders", "points": orders_by_day},
                "secondary_text": f"AOV: {_format_shopify_currency(aov, currency)}",
                "currency": currency,
                "summary": f"{orders:,} orders with average order value {_format_shopify_currency(aov, currency)}.",
                "semantic_name": "orders_overview",
            },
        ),
        _shopify_block(
            "text",
            4,
            {
                "slide_number": 4,
                "slide_type": "insights",
                "title": "Top Products",
                "top_products": top_products,
                "top_variants": top_variants,
                "summary": "Top products ranked by revenue for the selected period.",
                "text": " | ".join(
                    f"{item.get('title')}: {_format_shopify_currency(item.get('revenue'), currency)}"
                    for item in top_products[:5]
                )
                or "No top products available for the selected period.",
                "semantic_name": "top_products",
            },
            ["text"],
        ),
        _shopify_block(
            "text",
            5,
            {
                "slide_number": 5,
                "slide_type": "summary",
                "title": "Executive Summary",
                "text": summary,
                "summary": summary,
                "ai_summary": summary,
                "semantic_name": "executive_summary",
                "metrics_summary": {
                    "revenue": {
                        "label": "Revenue",
                        "value": revenue,
                        "formatted_value": _format_shopify_currency(revenue, currency),
                    },
                    "orders": {
                        "label": "Orders",
                        "value": orders,
                        "formatted_value": f"{orders:,}",
                    },
                    "aov": {
                        "label": "AOV",
                        "value": aov,
                        "formatted_value": _format_shopify_currency(aov, currency),
                    },
                },
            },
            ["text"],
        ),
    ]
    return blocks


def _build_shopify_dataset_payload(
    *,
    shop_domain: str,
    shop_name: str | None,
    timeframe: dict[str, str],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    currency = str(metrics.get("currency") or "USD")
    revenue = round(float(metrics.get("total_sales") or 0.0), 2)
    orders = int(metrics.get("orders_count") or 0)
    aov = round(float(metrics.get("average_order_value") or 0.0), 2)
    sales_by_day = metrics.get("sales_by_day") if isinstance(metrics.get("sales_by_day"), list) else []
    top_products = metrics.get("top_products") if isinstance(metrics.get("top_products"), list) else []
    top_variants = metrics.get("top_variants") if isinstance(metrics.get("top_variants"), list) else []
    return {
        "integration_type": "shopify",
        "integration_display_name": "Shopify",
        "provider": "shopify",
        "channel": "shopify",
        "social_network": "shopify",
        "account_name": shop_name or shop_domain,
        "page_name": shop_name or shop_domain,
        "shop_domain": shop_domain,
        "shop_name": shop_name,
        "timeframe": timeframe,
        "currency": currency,
        "revenue": revenue,
        "orders": orders,
        "aov": aov,
        "sales_by_day": sales_by_day,
        "orders_by_day": metrics.get("orders_by_day") if isinstance(metrics.get("orders_by_day"), list) else [],
        "top_products": top_products,
        "top_variants": top_variants,
        "discounts": round(float(metrics.get("discounts_total") or 0.0), 2),
        "refunds": round(float(metrics.get("refunds_total") or 0.0), 2),
        "summary": str(metrics.get("summary") or "").strip(),
        "raw_orders_count": int(metrics.get("raw_orders_count") or 0),
        "normalized_report_metrics": {
            "revenue": revenue,
            "orders": orders,
            "aov": aov,
            "sales_by_day": sales_by_day,
            "top_products": top_products,
            "discounts": round(float(metrics.get("discounts_total") or 0.0), 2),
            "refunds": round(float(metrics.get("refunds_total") or 0.0), 2),
        },
    }


def _create_shopify_dataset_snapshot(
    *,
    db: Session,
    connection: ShopifyConnection,
    timeframe: dict[str, str],
    dataset_payload: dict[str, Any],
    raw_metrics: dict[str, Any],
) -> tuple[Dataset, DatasetFile, ShopifySnapshot]:
    csv_output = io.StringIO()
    writer = csv.DictWriter(csv_output, fieldnames=["date", "sales", "orders"])
    writer.writeheader()
    for point in dataset_payload.get("sales_by_day") or []:
        writer.writerow(
            {
                "date": point.get("date"),
                "sales": point.get("value"),
                "orders": point.get("orders"),
            }
        )
    csv_bytes = csv_output.getvalue().encode("utf-8")
    filename = f"shopify_{connection.shop_domain}_{timeframe['since']}_{timeframe['until']}.csv"
    _enforce_workspace_storage_for_upload(db, connection.workspace_id, len(csv_bytes))
    dataset = Dataset(
        workspace_id=connection.workspace_id,
        name=filename,
        description="Shopify sales snapshot",
        data=dataset_payload,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    key = f"workspaces/{connection.workspace_id}/datasets/{dataset.id}/{filename}"
    s3 = boto3.client("s3", region_name=settings.aws_region)
    try:
        s3.put_object(Bucket=settings.s3_inputs_bucket, Key=key, Body=csv_bytes)
    except Exception:
        db.delete(dataset)
        db.commit()
        raise http_error(502, "s3_upload_failed", "Failed to upload file.")

    dataset_file = DatasetFile(
        dataset_id=dataset.id,
        workspace_id=connection.workspace_id,
        s3_key=key,
        size_bytes=len(csv_bytes),
        content_type="text/csv",
    )
    db.add(dataset_file)
    db.commit()
    db.refresh(dataset_file)

    snapshot = ShopifySnapshot(
        user_id=connection.user_id,
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        dataset_id=dataset.id,
        timeframe=timeframe,
        start_date=date.fromisoformat(timeframe["since"]),
        end_date=date.fromisoformat(timeframe["until"]),
        metrics_json=dataset_payload,
        raw_json={"raw_orders": raw_metrics.get("raw_orders")},
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return dataset, dataset_file, snapshot


@app.get("/integrations/shopify/connect")
def shopify_connect(
    shop: str,
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    missing = shopify_missing_config()
    if missing:
        raise http_error(500, "shopify_config_missing", f"Missing Shopify config: {', '.join(missing)}.")
    shop_domain = normalize_shop_domain(shop)
    resolved_workspace_id = _resolve_shopify_workspace_id(
        db,
        user_id=current_user.id,
        workspace_id=workspace_id,
    )
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    integration = _get_or_create_shopify_integration_for_workspace(db, resolved_workspace_id)
    state_token = create_oauth_state(purpose=SHOPIFY_OAUTH_STATE_PURPOSE)
    oauth_state = ShopifyOAuthState(
        user_id=current_user.id,
        workspace_id=resolved_workspace_id,
        shop_domain=shop_domain,
        state_token=state_token,
        purpose=SHOPIFY_OAUTH_STATE_PURPOSE,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(oauth_state)
    db.commit()
    logger.info(
        "Shopify connect started",
        extra={
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "shop_domain": shop_domain,
        },
    )
    return RedirectResponse(url=shopify_authorize_url(shop_domain=shop_domain, state=state_token), status_code=302)


@app.get("/integrations/shopify/callback")
def shopify_callback(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    params = dict(request.query_params)
    if not shopify_callback_hmac_valid(params):
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="invalid_hmac")
    code = str(params.get("code") or "").strip()
    state = str(params.get("state") or "").strip()
    shop_domain = normalize_shop_domain(str(params.get("shop") or ""))
    if not code or not state:
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="invalid_state")
    try:
        state_payload = decode_oauth_state(state)
    except TokenError:
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="invalid_state")
    if str(state_payload.get("purpose") or "") != SHOPIFY_OAUTH_STATE_PURPOSE:
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="invalid_state")
    oauth_state = (
        db.query(ShopifyOAuthState)
        .filter(ShopifyOAuthState.state_token == state)
        .order_by(ShopifyOAuthState.id.desc())
        .first()
    )
    expires_at = _utc_datetime(oauth_state.expires_at) if oauth_state is not None else None
    if oauth_state is None or oauth_state.used_at is not None or expires_at is None or expires_at < datetime.now(timezone.utc):
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="invalid_state")
    if oauth_state.shop_domain != shop_domain:
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="state_shop_mismatch")

    token_payload = exchange_shopify_code_for_access_token(shop_domain=shop_domain, code=code)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _redirect_with_query(_shopify_error_redirect_url(), provider="shopify", status="error", error="missing_token")

    integration = _get_or_create_shopify_integration_for_workspace(db, oauth_state.workspace_id)
    connection = (
        db.query(ShopifyConnection)
        .filter(
            ShopifyConnection.user_id == oauth_state.user_id,
            ShopifyConnection.shop_domain == shop_domain,
        )
        .first()
    )
    if connection is None:
        connection = ShopifyConnection(
            user_id=oauth_state.user_id,
            workspace_id=oauth_state.workspace_id,
            integration_id=integration.id,
            shop_domain=shop_domain,
        )
        db.add(connection)
        db.flush()

    shop_details = {}
    try:
        shop_details = fetch_shop_details(shop_domain=shop_domain, access_token=access_token)
    except HTTPException:
        logger.exception("Shopify shop details fetch failed", extra={"shop_domain": shop_domain})

    connection.workspace_id = oauth_state.workspace_id
    connection.integration_id = integration.id
    connection.shop_name = str(shop_details.get("name") or "").strip() or None
    connection.access_token_encrypted = encrypt_secret(access_token)
    connection.scopes = [scope for scope in str(token_payload.get("scope") or settings.shopify_scopes).split(",") if scope.strip()]
    connection.status = SHOPIFY_STATUS_CONNECTED
    integration.status = SHOPIFY_STATUS_CONNECTED
    oauth_state.used_at = datetime.now(timezone.utc)
    db.add_all([connection, integration, oauth_state])
    db.commit()
    return _redirect_with_query(
        _shopify_success_redirect_url(),
        provider="shopify",
        status="success",
        shop_domain=shop_domain,
        message="Shopify connected successfully.",
    )


@app.get("/integrations/shopify/status", response_model=ShopifyStatusOut)
def shopify_status(
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShopifyStatusOut:
    resolved_workspace_id = _resolve_shopify_workspace_id(db, user_id=current_user.id, workspace_id=workspace_id)
    _require_workspace_access(db, current_user.id, resolved_workspace_id)
    integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == resolved_workspace_id, Integration.provider == SHOPIFY_PROVIDER)
        .order_by(Integration.id.asc())
        .first()
    )
    connection = _shopify_connection_for_workspace(db, workspace_id=resolved_workspace_id)
    return _shopify_status_out(connection, integration_id=integration.id if integration else None)


@app.post("/integrations/shopify/sync", response_model=ShopifySyncOut)
def shopify_sync(
    payload: ShopifySyncIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShopifySyncOut:
    connection = _resolve_shopify_connection(db, current_user=current_user, workspace_id=payload.workspace_id)
    if connection is None:
        raise http_error(404, "shopify_not_connected", "Shopify connection not found.")
    if not str(connection.access_token_encrypted or "").strip():
        raise http_error(401, "shopify_reconnect_required", "Shopify reconnect required.")
    timeframe = resolve_shopify_timeframe(
        payload.timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    try:
        access_token = decrypt_secret(connection.access_token_encrypted)
        shop_details = fetch_shop_details(shop_domain=connection.shop_domain, access_token=access_token)
        metrics = fetch_orders_metrics(
            shop_domain=connection.shop_domain,
            access_token=access_token,
            timeframe=timeframe,
        )
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            connection.status = SHOPIFY_STATUS_ERROR
            connection.integration.status = SHOPIFY_STATUS_ERROR
            db.add(connection)
            db.add(connection.integration)
            db.commit()
        raise
    dataset_payload = _build_shopify_dataset_payload(
        shop_domain=connection.shop_domain,
        shop_name=str(shop_details.get("name") or connection.shop_name or "").strip() or None,
        timeframe=timeframe,
        metrics=metrics,
    )
    connection.shop_name = str(shop_details.get("name") or connection.shop_name or "").strip() or None
    dataset, dataset_file, _snapshot = _create_shopify_dataset_snapshot(
        db=db,
        connection=connection,
        timeframe=timeframe,
        dataset_payload=dataset_payload,
        raw_metrics=metrics,
    )
    connection.status = SHOPIFY_STATUS_CONNECTED
    connection.last_sync_at = datetime.now(timezone.utc)
    connection.integration.status = SHOPIFY_STATUS_CONNECTED
    db.add(connection)
    db.add(connection.integration)
    db.commit()
    metrics_out = {
        "revenue": dataset_payload.get("revenue"),
        "orders": dataset_payload.get("orders"),
        "aov": dataset_payload.get("aov"),
        "sales_by_day": dataset_payload.get("sales_by_day"),
        "top_products": dataset_payload.get("top_products"),
        "top_variants": dataset_payload.get("top_variants"),
        "summary": dataset_payload.get("summary"),
        "currency": dataset_payload.get("currency"),
        "refunds": dataset_payload.get("refunds"),
        "discounts": dataset_payload.get("discounts"),
    }
    return ShopifySyncOut(
        integration_id=connection.integration_id,
        connection_id=connection.id,
        dataset_id=dataset.id,
        dataset_file_id=dataset_file.id,
        shop_domain=connection.shop_domain,
        shop_name=connection.shop_name,
        status="uploaded",
        timeframe=timeframe,
        metrics=metrics_out,
        last_synced_at=connection.last_sync_at,
    )


@app.delete("/integrations/shopify/disconnect", response_model=ShopifyDisconnectOut)
def shopify_disconnect(
    workspace_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShopifyDisconnectOut:
    connection = _resolve_shopify_connection(db, current_user=current_user, workspace_id=workspace_id)
    if connection is None:
        return ShopifyDisconnectOut(success=True, status=SHOPIFY_STATUS_DISCONNECTED, token_cleared=False)
    token_cleared = bool(str(connection.access_token_encrypted or "").strip())
    connection.access_token_encrypted = None
    connection.status = SHOPIFY_STATUS_DISCONNECTED
    connection.integration.status = SHOPIFY_STATUS_DISCONNECTED
    db.add(connection)
    db.add(connection.integration)
    db.commit()
    return ShopifyDisconnectOut(success=True, status=SHOPIFY_STATUS_DISCONNECTED, token_cleared=token_cleared)


@app.post("/integrations/shopify/webhooks")
async def shopify_webhooks(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    raw_body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")
    if not shopify_webhook_hmac_valid(raw_body, hmac_header):
        raise http_error(401, "invalid_shopify_webhook_hmac", "Invalid Shopify webhook signature.")
    topic = str(request.headers.get("X-Shopify-Topic") or "").strip()
    shop_domain = str(request.headers.get("X-Shopify-Shop-Domain") or "").strip().lower() or None
    payload = json.loads(raw_body.decode("utf-8") or "{}") if raw_body else {}
    logger.info("Shopify webhook received", extra={"topic": topic, "shop_domain": shop_domain})
    if shop_domain:
        connection = (
            db.query(ShopifyConnection)
            .filter(ShopifyConnection.shop_domain == shop_domain)
            .order_by(ShopifyConnection.updated_at.desc(), ShopifyConnection.id.desc())
            .first()
        )
    else:
        connection = None
    if topic == "app/uninstalled" and connection is not None:
        connection.access_token_encrypted = None
        connection.status = SHOPIFY_STATUS_DISCONNECTED
        connection.integration.status = SHOPIFY_STATUS_DISCONNECTED
        db.add(connection)
        db.add(connection.integration)
        db.commit()
    elif topic == "shop/redact" and connection is not None:
        connection.access_token_encrypted = None
        connection.shop_name = None
        connection.status = SHOPIFY_STATUS_DISCONNECTED
        db.add(connection)
        snapshots = db.query(ShopifySnapshot).filter(ShopifySnapshot.connection_id == connection.id).all()
        for snapshot in snapshots:
            snapshot.raw_json = None
            db.add(snapshot)
        db.commit()
    elif topic in SHOPIFY_COMPLIANCE_TOPICS:
        logger.info("Shopify compliance webhook acknowledged", extra={"topic": topic, "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else []})
    return Response(status_code=200)


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
    workspace_id: int | None = Query(default=None),
    integration_type: str | None = Query(default=None),
    reconnect: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    resolved_workspace_id = _resolve_meta_connect_workspace_id(
        db,
        user_id=current_user.id,
        requested_workspace_id=workspace_id,
    )
    logger.info(
        "meta_connect_pages_workspace_resolved",
        extra={
            "user_id": current_user.id,
            "workspace_id_received": workspace_id,
            "workspace_ids_available": _workspace_ids_for_user(db, current_user.id),
            "resolved_workspace_id": resolved_workspace_id,
        },
    )
    integration = _get_or_create_meta_integration_for_workspace(db, resolved_workspace_id)
    selected_integration_type = normalize_meta_oauth_integration_type(integration_type)
    selected_scope = _meta_oauth_expected_scope_string(selected_integration_type)
    state = encode_state(
        {
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "integration_type": selected_integration_type,
            "reconnect": reconnect,
            "source": "meta_pages_connect_pages",
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    redirect_uri = _meta_pages_redirect_uri()
    _meta_oauth_log(
        "META_OAUTH_SCOPES_REQUESTED",
        provider="meta_pages",
        workspace_id=resolved_workspace_id,
        user_id=current_user.id,
        integration_id=integration.id,
        integration_type=selected_integration_type,
        reconnect_requested=reconnect,
        callback_route="/integrations/meta/callback-pages",
        redirect_uri=redirect_uri,
        scopes_requested=_meta_oauth_expected_scopes(selected_integration_type),
        scope=selected_scope,
    )
    url = oauth_connect_pages_url(
        state,
        redirect_uri=redirect_uri,
        auth_type="rerequest",
        scope=selected_scope,
        integration_type=selected_integration_type,
    )
    return {
        "auth_url": url,
        "integration_id": integration.id,
        "scope": selected_scope,
        "message": (
            "Connect Meta for Instagram Business insights."
            if selected_integration_type == "instagram_business"
            else "Connect Meta for Facebook Pages insights."
        ),
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


@app.get("/integrations/instagram-business/connect")
def instagram_business_connect(
    workspace_id: int | None = Query(default=None),
    reconnect: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    missing_config = get_missing_instagram_business_config_fields()
    if missing_config:
        logger.info(
            "INSTAGRAM_BUSINESS_CONNECT_FAILED %s",
            json.dumps(
                {
                    "stage": "config_validation",
                    "missing": missing_config,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return {
            "error": "instagram_business_not_configured",
            "missing": missing_config,
        }
    resolved_workspace_id = _resolve_meta_connect_workspace_id(
        db,
        user_id=current_user.id,
        requested_workspace_id=workspace_id,
    )
    integration = _get_or_create_instagram_business_integration_for_workspace(db, resolved_workspace_id)
    state = encode_instagram_business_state(
        {
            "workspace_id": resolved_workspace_id,
            "user_id": current_user.id,
            "integration_id": integration.id,
            "integration_type": "instagram_business",
            "source": "instagram_business_connect",
            "callback_route": INSTAGRAM_BUSINESS_CALLBACK_PATH,
            "reconnect": reconnect,
        }
    )
    auth_url = build_instagram_business_auth_url(state)
    return {
        "auth_url": auth_url,
        "integration_id": integration.id,
        "scope": INSTAGRAM_BUSINESS_OAUTH_SCOPE,
        "message": "Connect Instagram Business to sync read-only account insights.",
    }


@app.get("/integrations/instagram-business/callback")
def instagram_business_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    if error:
        logger.info(
            "INSTAGRAM_BUSINESS_CALLBACK_RECEIVED %s",
            json.dumps(
                {
                    "callback_route": INSTAGRAM_BUSINESS_CALLBACK_PATH,
                    "code_received": bool(code),
                    "state_received": bool(state),
                    "error": str(error or "").strip() or None,
                    "error_reason": str(error_reason or "").strip() or None,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return _meta_oauth_popup_response(
            status="error",
            error=str(error_reason or error).strip() or "oauth_error",
            message=str(error_description or error_reason or "Instagram returned an OAuth error.").strip(),
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )
    if not code or not state:
        logger.info(
            "INSTAGRAM_BUSINESS_CALLBACK_RECEIVED %s",
            json.dumps(
                {
                    "callback_route": INSTAGRAM_BUSINESS_CALLBACK_PATH,
                    "code_received": bool(code),
                    "state_received": bool(state),
                    "error": "missing_required_query_params",
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Instagram Business connection could not be verified. Please try again.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )

    try:
        state_payload = decode_instagram_business_state(state)
    except ValueError:
        logger.info(
            "INSTAGRAM_BUSINESS_CALLBACK_RECEIVED %s",
            json.dumps(
                {
                    "callback_route": INSTAGRAM_BUSINESS_CALLBACK_PATH,
                    "code_received": bool(code),
                    "state_received": bool(state),
                    "error": "invalid_state",
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Instagram Business connection request expired. Please try again.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )

    workspace_id = int(state_payload.get("workspace_id") or 0)
    user_id = int(state_payload.get("user_id") or 0)
    integration_id = int(state_payload.get("integration_id") or 0)
    reconnect_requested = bool(state_payload.get("reconnect"))
    if workspace_id <= 0 or user_id <= 0 or integration_id <= 0:
        logger.info(
            "INSTAGRAM_BUSINESS_CONNECT_FAILED %s",
            json.dumps(
                {
                    "stage": "state_validation",
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "integration_id": integration_id,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Instagram Business connection request is invalid.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )

    integration = db.get(Integration, integration_id)
    if integration is None or integration.provider != "instagram_business" or integration.workspace_id != workspace_id:
        return _meta_oauth_popup_response(
            status="error",
            error="integration_not_found",
            message="Instagram Business integration not found.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )

    logger.info(
        "INSTAGRAM_BUSINESS_CALLBACK_RECEIVED %s",
        json.dumps(
            {
                "callback_route": INSTAGRAM_BUSINESS_CALLBACK_PATH,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "integration_id": integration_id,
                "code_received": bool(code),
                "state_received": bool(state),
                "reconnect_requested": reconnect_requested,
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )

    try:
        token_payload = exchange_instagram_business_code_for_token(code)
        access_token = str(token_payload.get("access_token") or "").strip()
        refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
        expires_in = token_payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int) or (isinstance(expires_in, str) and str(expires_in).isdigit())
            else None
        )
        logger.info(
            "INSTAGRAM_BUSINESS_TOKEN_EXCHANGED %s",
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "integration_id": integration_id,
                    "token_received": bool(access_token),
                    "refresh_token_received": bool(refresh_token),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "status_code": token_payload.get("_http_status_code"),
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        if not access_token:
            raise http_error(400, "instagram_business_token_exchange_failed", "Instagram did not return an access token.")

        granted_scopes_raw = token_payload.get("scope") or token_payload.get("scopes") or token_payload.get("permissions")
        if isinstance(granted_scopes_raw, str):
            granted_scopes = [item.strip() for item in granted_scopes_raw.replace(" ", ",").split(",") if item.strip()]
        elif isinstance(granted_scopes_raw, list):
            granted_scopes = [str(item).strip() for item in granted_scopes_raw if str(item).strip()]
        else:
            granted_scopes = []
        missing_scopes = [scope for scope in INSTAGRAM_BUSINESS_SCOPES if granted_scopes and scope not in granted_scopes]
        if missing_scopes:
            logger.info(
                "INSTAGRAM_BUSINESS_PERMISSION_MISSING %s",
                json.dumps(
                    {
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "integration_id": integration_id,
                        "missing_scopes": missing_scopes,
                        "granted_scopes": granted_scopes,
                    },
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
            )

        profile_payload = fetch_instagram_business_profile(access_token)
        instagram_user_id = str(
            profile_payload.get("user_id")
            or profile_payload.get("id")
            or ""
        ).strip()
        username = str(profile_payload.get("username") or "").strip() or None
        account_type = str(profile_payload.get("account_type") or "").strip() or None
        display_name = str(profile_payload.get("name") or username or instagram_user_id).strip() or instagram_user_id
        profile_picture_url = str(profile_payload.get("profile_picture_url") or "").strip() or None
        if not instagram_user_id:
            raise http_error(400, "instagram_business_account_missing", "Instagram did not return an authorized professional account.")

        logger.info(
            "INSTAGRAM_BUSINESS_ACCOUNT_DISCOVERED %s",
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "integration_id": integration_id,
                    "instagram_user_id": instagram_user_id,
                    "username": username,
                    "account_type": account_type,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )

        token_account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id == _instagram_business_token_account_external_id(integration.id),
            )
            .first()
        )
        if token_account is None:
            token_account = IntegrationAccount(
                integration_id=integration.id,
                workspace_id=workspace_id,
                external_account_id=_instagram_business_token_account_external_id(integration.id),
                display_name="Instagram Business token store",
            )
            db.add(token_account)
            db.commit()
            db.refresh(token_account)

        account_record = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id == instagram_user_id,
            )
            .first()
        )
        if account_record is None:
            account_record = IntegrationAccount(
                integration_id=integration.id,
                workspace_id=workspace_id,
                external_account_id=instagram_user_id,
                display_name=display_name,
            )
            db.add(account_record)
        else:
            account_record.display_name = display_name
            db.add(account_record)
        db.commit()
        db.refresh(account_record)

        _replace_integration_token_encrypted(
            db,
            account_id=token_account.id,
            workspace_id=workspace_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        _cache_meta_pages(
            db,
            integration,
            user_id,
            [
                {
                    "record_type": META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    "page_id": instagram_user_id,
                    "parent_page_id": None,
                    "name": display_name,
                    "instagram_username": username,
                    "profile_picture_url": profile_picture_url,
                    "page_access_token": None,
                    "tasks": None,
                    "perms": granted_scopes or INSTAGRAM_BUSINESS_SCOPES,
                    "category": account_type,
                    "business_name": display_name,
                }
            ],
        )
        _set_meta_integration_status(db, integration, status="connected")
        logger.info(
            "INSTAGRAM_BUSINESS_CONNECT_SUCCESS %s",
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "integration_id": integration_id,
                    "instagram_user_id": instagram_user_id,
                    "username": username,
                    "account_type": account_type,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        return _meta_oauth_popup_response(
            status="connected",
            source="instagram_business_connect",
            integration_id=integration.id,
            message="Instagram Business connected successfully.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        logger.info(
            "INSTAGRAM_BUSINESS_CONNECT_FAILED %s",
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "integration_id": integration_id,
                    "error_code": str(detail.get("code") or "instagram_business_connect_failed"),
                    "message": str(detail.get("message") or exc.detail or "").strip() or None,
                },
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
        )
        if not reconnect_requested:
            _set_meta_integration_status(db, integration, status="disconnected")
        return _meta_oauth_popup_response(
            status="error",
            source="instagram_business_connect",
            integration_id=integration.id,
            error=str(detail.get("code") or "instagram_business_connect_failed"),
            message=str(detail.get("message") or "We could not complete the Instagram Business connection."),
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )
    except Exception:
        logger.exception("Instagram Business OAuth callback failed unexpectedly")
        if not reconnect_requested:
            _set_meta_integration_status(db, integration, status="disconnected")
        return _meta_oauth_popup_response(
            status="error",
            source="instagram_business_connect",
            integration_id=integration.id,
            error="instagram_business_connect_failed",
            message="We could not complete the Instagram Business connection.",
            callback_path="/integrations/instagram-business/callback",
            provider="instagram_business",
        )


@app.get("/integrations/meta/callback-pages")
def meta_callback_pages(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    if error:
        logger.warning(
            "Meta Pages OAuth callback received upstream error error=%s error_reason=%s error_description=%s",
            error,
            error_reason,
            error_description,
        )
        return _meta_oauth_popup_response(
            status="error",
            error=str(error_reason or error).strip() or "oauth_error",
            message=str(error_description or error_reason or "Meta returned an OAuth error.").strip(),
        )
    if not code or not state:
        logger.warning(
            "Meta Pages OAuth callback missing required query params code_received=%s state_received=%s",
            bool(code),
            bool(state),
        )
        return _meta_oauth_popup_response(
            status="error",
            error="invalid_state",
            message="The Meta connection could not be verified. Please close this window and try again.",
        )
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


@app.post("/integrations/meta/disconnect", response_model=MetaDisconnectOut)
def meta_disconnect(
    payload: MetaDisconnectIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaDisconnectOut:
    integration = _resolve_meta_disconnect_integration(
        db,
        current_user,
        integration_id=payload.integration_id,
        workspace_id=payload.workspace_id,
    )
    return _disconnect_meta_integration(db, integration, revoke_permissions=True)


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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
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
        limit=limit,
        offset=offset,
        search=search,
    )


def _get_meta_records_response(
    db: Session,
    current_user: User,
    integration_id: int,
    *,
    record_type: str,
    selected_integration_type: str,
    debug_source: str,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
) -> list[MetaPageOut]:
    return _get_meta_records_catalog_response(
        db,
        current_user,
        integration_id,
        record_type=record_type,
        selected_integration_type=selected_integration_type,
        debug_source=debug_source,
        limit=limit,
        offset=offset,
        search=search,
    ).data


def _get_meta_records_catalog_response(
    db: Session,
    current_user: User,
    integration_id: int,
    *,
    record_type: str,
    selected_integration_type: str,
    debug_source: str,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
) -> MetaPageCatalogOut:
    started_at = perf_counter()
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
    if str(integration.status or "").strip().lower() != "connected":
        logger.info(
            "[META_RECORDS_DISCONNECTED]",
            extra={
                "integration_id": integration.id,
                "workspace_id": integration.workspace_id,
                "record_type": record_type,
                "stored_total_pages_count": len(stored_records_before),
                "stored_count": len(stored_pages_before),
            },
        )
        return MetaPageCatalogOut(
            data=[],
            source="disconnected",
            count=0,
            has_cached_data=False,
            status="disconnected",
            connected=False,
            refresh_available=False,
            refresh_recommended=False,
            message="Meta integration is disconnected.",
            limit=limit,
            offset=offset,
            search=search,
        )
    response_source = _meta_cache_status(stored_pages_before)
    live_refresh_triggered = False
    returned_records = stored_pages_before
    message: str | None = None
    refresh_recommended = response_source == "cached_stale"
    meta_duration_ms: float | None = None

    if not stored_pages_before:
        response_source = "empty_cache"
        try:
            access_token = _get_meta_access_token(db, integration)
        except HTTPException as exc:
            logger.warning(
                "Meta records GET empty cache and missing token integration_id=%s user_id=%s record_type=%s error=%s",
                integration.id,
                current_user.id,
                record_type,
                str(exc.detail),
            )
            message = "No cached pages found. Refresh required."
            returned_records = []
        else:
            live_started_at = perf_counter()
            live_refresh_triggered = True
            cached_pages, diagnostics, facebook_pages = _refresh_meta_pages_from_live_graph(
                db,
                integration,
                access_token=access_token,
                user_id=current_user.id,
                selected_integration_type=selected_integration_type,
                context=f"{debug_source}_initial_discovery",
                return_empty_on_error=True,
            )
            meta_duration_ms = round((perf_counter() - live_started_at) * 1000, 2)
            instagram_accounts = _filter_meta_records(
                cached_pages,
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            )
            returned_records = (
                facebook_pages
                if record_type == META_RECORD_TYPE_FACEBOOK_PAGE
                else instagram_accounts
            )
            response_source = "live" if returned_records else "empty_cache"
            if not returned_records:
                message = "No cached pages found. Refresh required."
            _log_meta_account_summary(
                integration_id=integration.id,
                user_id=current_user.id,
                selected_integration_type=selected_integration_type,
                facebook_pages=facebook_pages,
                instagram_accounts=instagram_accounts,
                context=f"{debug_source}_initial_discovery",
            )
            _log_meta_pages_debug(
                integration_id=integration.id,
                source=f"{debug_source}_initial_discovery",
                pages=returned_records,
                dropdown_count=len(returned_records),
            )
            logger.warning(
                "Meta initial discovery completed integration_id=%s user_id=%s record_type=%s total_pages_from_meta=%s response_source=%s meta_duration_ms=%s",
                integration.id,
                current_user.id,
                record_type,
                len(diagnostics),
                response_source,
                meta_duration_ms,
            )

    filtered_records = _filter_meta_records(
        returned_records,
        record_type=record_type,
    )
    searched_records = _apply_meta_records_search(filtered_records, search)
    paginated_records = _paginate_meta_records(searched_records, limit=limit, offset=offset)
    _log_meta_account_summary(
        integration_id=integration.id,
        user_id=current_user.id,
        selected_integration_type=selected_integration_type,
        facebook_pages=_filter_meta_records(returned_records, record_type=META_RECORD_TYPE_FACEBOOK_PAGE),
        instagram_accounts=_filter_meta_records(returned_records, record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT),
        context=debug_source,
    )
    _log_meta_pages_debug(
        integration_id=integration.id,
        source=debug_source,
        pages=paginated_records,
        dropdown_count=len(paginated_records),
    )
    logger.warning(
        "Meta records GET completed integration_id=%s user_id=%s record_type=%s cached_pages_count=%s cached_instagram_count=%s live_refresh_triggered=%s meta_duration_ms=%s response_source=%s returned_pages_count=%s search=%s limit=%s offset=%s endpoint_duration_ms=%s",
        integration.id,
        current_user.id,
        record_type,
        len(_filter_meta_records(returned_records, record_type=META_RECORD_TYPE_FACEBOOK_PAGE)),
        len(_filter_meta_records(returned_records, record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT)),
        live_refresh_triggered,
        meta_duration_ms,
        response_source,
        len(paginated_records),
        search,
        limit,
        offset,
        round((perf_counter() - started_at) * 1000, 2),
    )
    return MetaPageCatalogOut(
        data=[_meta_page_out_with_cache_status(page, cache_status=response_source) for page in paginated_records],
        source=response_source,
        count=len(searched_records),
        has_cached_data=bool(stored_pages_before),
        refresh_available=True,
        refresh_recommended=refresh_recommended,
        message=message,
        limit=limit,
        offset=offset,
        search=search,
    )


@app.get("/integrations/meta/facebook-pages", response_model=list[MetaPageOut])
def meta_facebook_pages(
    integration_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
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
        limit=limit,
        offset=offset,
        search=search,
    )


@app.get("/integrations/meta/instagram-accounts", response_model=list[MetaPageOut])
def meta_instagram_accounts(
    integration_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
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
        limit=limit,
        offset=offset,
        search=search,
    )


@app.get("/integrations/meta/pages/catalog", response_model=MetaPageCatalogOut)
def meta_pages_catalog(
    integration_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPageCatalogOut:
    return _get_meta_records_catalog_response(
        db,
        current_user,
        integration_id,
        record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
        selected_integration_type="facebook_pages",
        debug_source="pages_catalog_response",
        limit=limit,
        offset=offset,
        search=search,
    )


@app.get("/integrations/meta/instagram-accounts/catalog", response_model=MetaPageCatalogOut)
def meta_instagram_accounts_catalog(
    integration_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPageCatalogOut:
    return _get_meta_records_catalog_response(
        db,
        current_user,
        integration_id,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        selected_integration_type="instagram_accounts",
        debug_source="instagram_catalog_response",
        limit=limit,
        offset=offset,
        search=search,
    )


@app.post("/integrations/meta/refresh-pages", response_model=MetaPagesRefreshOut)
def meta_refresh_pages(
    payload: MetaPagesRefreshIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaPagesRefreshOut:
    started_at = perf_counter()
    integration = _get_meta_integration(db, current_user, payload.integration_id)
    try:
        access_token = _get_meta_access_token(db, integration)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return MetaPagesRefreshOut(
            success=False,
            code=str(detail.get("code") or "META_REFRESH_FAILED"),
            message="No pudimos actualizar las paginas en este momento. Puedes usar las paginas guardadas o intentar de nuevo.",
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )

    try:
        cached_pages, diagnostics, facebook_pages = _refresh_meta_pages_from_live_graph(
            db,
            integration,
            access_token=access_token,
            user_id=current_user.id,
            selected_integration_type="facebook_pages",
            context="refresh_pages_on_demand",
            return_empty_on_error=False,
        )
    except TimeoutError:
        return MetaPagesRefreshOut(
            success=False,
            code="META_REFRESH_TIMEOUT",
            message="No pudimos actualizar todas las paginas en este momento. Puedes usar las paginas guardadas o intentar de nuevo.",
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or "META_REFRESH_FAILED")
        if error_code in {"meta_api_timeout", "request_timeout"}:
            error_code = "META_REFRESH_TIMEOUT"
        return MetaPagesRefreshOut(
            success=False,
            code=error_code,
            message="No pudimos actualizar todas las paginas en este momento. Puedes usar las paginas guardadas o intentar de nuevo.",
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )

    instagram_accounts = _filter_meta_records(
        cached_pages,
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    )
    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    logger.warning(
        "Meta refresh pages completed integration_id=%s workspace_id=%s user_id=%s live_refresh_triggered=%s total_pages_from_meta=%s facebook_pages_count=%s instagram_accounts_count=%s meta_duration_ms=%s response_source=%s endpoint_duration_ms=%s",
        integration.id,
        integration.workspace_id,
        current_user.id,
        True,
        len(diagnostics),
        len(facebook_pages),
        len(instagram_accounts),
        duration_ms,
        "live",
        duration_ms,
    )
    return MetaPagesRefreshOut(
        success=True,
        facebook_pages_count=len(facebook_pages),
        instagram_accounts_count=len(instagram_accounts),
        duration_ms=duration_ms,
        message="Pages refreshed successfully.",
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
        selected_integration_type="instagram_business",
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
        "oauth_scope": INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN,
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
        selected_integration_type="instagram_business",
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
        "scope": INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN,
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
            "instagram_scope_used": INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN,
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
            "instagram_scope_used": INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN,
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


def _run_instagram_business_sync(
    *,
    db: Session,
    current_user: User,
    integration_id: int,
    instagram_account_id: str,
    workspace_id: int | None = None,
    timeframe: str = "last_28_days",
    start_date: str | None = None,
    end_date: str | None = None,
) -> InstagramBusinessSyncOut:
    integration = _get_meta_integration(db, current_user, integration_id)
    if workspace_id is not None and int(workspace_id) != int(integration.workspace_id):
        raise http_error(
            400,
            "workspace_mismatch",
            "workspace_id does not match the integration workspace.",
        )

    selected_meta_record = _resolve_instagram_account_record_for_sync(
        db,
        integration=integration,
        current_user=current_user,
        instagram_account_id=instagram_account_id,
    )
    timeframe_config = resolve_meta_pages_timeframe(
        timeframe,
        start_date=start_date,
        end_date=end_date,
    )

    parent_page_id = str(
        selected_meta_record.parent_page_id or selected_meta_record.page_id or instagram_account_id
    ).strip()
    selected_page = IntegrationAccount(
        integration_id=integration.id,
        workspace_id=integration.workspace_id,
        external_account_id=_meta_page_account_external_id(parent_page_id),
        display_name=selected_meta_record.business_name or selected_meta_record.name,
    )
    sync_result = _sync_meta_instagram_account(
        db=db,
        integration=integration,
        selected_page=selected_page,
        selected_meta_record=selected_meta_record,
        timeframe_config=timeframe_config,
        current_user=current_user,
    )
    return InstagramBusinessSyncOut(
        integration_id=integration.id,
        dataset_id=sync_result.dataset_id,
        dataset_file_id=sync_result.dataset_file_id,
        source_type="instagram_business",
        record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        account_id=selected_meta_record.page_id,
        account_name=sync_result.page_name,
        status="synced",
        timeframe=sync_result.timeframe,
    )


def _run_meta_pages_sync(
    *,
    db: Session,
    current_user: User,
    integration_id: int,
    page_id: str | None = None,
    timeframe: str = "last_28_days",
    start_date: str | None = None,
    end_date: str | None = None,
    raw_query_params: dict[str, Any] | None = None,
    raw_body: dict[str, Any] | None = None,
) -> MetaPagesSyncOut:
    try:
        integration = _get_meta_integration(db, current_user, integration_id)
        selected_page = None
        if page_id:
            selected_page = (
                db.query(IntegrationAccount)
                .filter(
                    IntegrationAccount.integration_id == integration.id,
                    IntegrationAccount.external_account_id == _meta_page_account_external_id(str(page_id)),
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

        resolved_page_id = _get_meta_page_id(selected_page)
        page_name = selected_page.display_name or resolved_page_id
        selected_meta_record = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration.id,
                MetaPage.page_id == resolved_page_id,
            )
            .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
            .first()
        )
        logger.info(
            "[MetaTimeframeBackend][sync.entry]",
            extra={
                "raw_query_params": raw_query_params or {},
                "raw_body": raw_body or {},
                "integration_id_final": integration.id,
                "page_id_final": resolved_page_id,
                "selected_record_type": selected_meta_record.record_type if selected_meta_record else None,
                "timeframe_final_before_resolve": timeframe,
                "start_date_final": start_date,
                "end_date_final": end_date,
            },
        )
        timeframe_config = resolve_meta_pages_timeframe(
            timeframe,
            start_date=start_date,
            end_date=end_date,
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
                "page_id": resolved_page_id,
                "timeframe": timeframe,
            },
        )
        logger.info(
            "FACEBOOK_PAGES_SYNC_START",
            extra={
                "report_id": None,
                "dataset_id": None,
                "page_id": resolved_page_id,
                "page_name": page_name,
                "metric_name": "sync",
                "source_metric": None,
                "raw_value": None,
                "sum_value": None,
                "points_count": 0,
                "unavailable_reason": None,
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
                "page_id": resolved_page_id,
                "has_page_token": bool(access_token),
            },
        )

        page_counts: dict = {}
        insights: dict = {}
        posts: list[dict] = []
        reach_daily: list[dict] = []
        organic_impressions_daily: list[dict] = []
        views_daily: list[dict] = []
        interactions_daily: list[dict] = []
        reactions_daily: list[dict] = []
        reach_metric_name: str | None = None
        organic_impressions_metric_name: str | None = None
        views_metric_name: str | None = None
        interactions_metric_name: str | None = None
        reactions_metric_name: str | None = None
        reach_unavailable_reason: str | None = "Meta did not return this metric for the selected period."
        organic_impressions_unavailable_reason: str | None = "Meta did not return organic post impressions for the selected period."
        engagement_unavailable_reason: str | None = "Meta did not return this metric for the selected period."
        views_unavailable_reason: str | None = "Meta did not return this metric for the selected period."
        followers_unavailable_reason: str | None = "Meta did not return followers for the selected period."
        fans_unavailable_reason: str | None = "Meta did not return fans for the selected period."
        reactions_unavailable_reason: str | None = "Meta did not return reactions for the selected period."

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
                page_info = fetch_page_info_with_metadata(access_token, resolved_page_id, fields="id,name")
                page_name = str(page_info.get("name") or page_name)
                _log_json_event(
                    "FACEBOOK_GRAPH_PAGE_FIELDS_RESPONSE",
                    {
                        "page_id": resolved_page_id,
                        "page_name": page_name,
                        "since": timeframe_config["since"],
                        "until": timeframe_config["until"],
                        "period": "day",
                        "metric_requested": "id,name",
                        "endpoint": f"/{resolved_page_id}",
                        "status_code": page_info.get("_meta_http_status_code"),
                        "metric_returned": "id,name",
                        "raw_response": page_info.get("_meta_raw_body"),
                        "raw_values": [page_info.get("id"), page_info.get("name")],
                        "raw_sum": None,
                        "points_count": 0,
                        "normalized_field": "page_fields",
                        "normalized_value": {"id": page_info.get("id"), "name": page_info.get("name")},
                        "unavailable_reason": None,
                    },
                )
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise
            logger.info(
                "FACEBOOK_PAGE_SELECTED",
                extra={
                    "report_id": None,
                    "dataset_id": None,
                    "page_id": resolved_page_id,
                    "page_name": page_name,
                    "metric_name": "page",
                    "source_metric": None,
                    "raw_value": None,
                    "sum_value": None,
                    "points_count": 0,
                    "unavailable_reason": None,
                },
            )

            try:
                page_counts = fetch_page_info_with_metadata(
                    access_token,
                    resolved_page_id,
                    fields="fan_count,followers_count",
                )
                _log_json_event(
                    "FACEBOOK_GRAPH_PAGE_FIELDS_RESPONSE",
                    {
                        "page_id": resolved_page_id,
                        "page_name": page_name,
                        "since": timeframe_config["since"],
                        "until": timeframe_config["until"],
                        "period": "day",
                        "metric_requested": "fan_count,followers_count",
                        "endpoint": f"/{resolved_page_id}",
                        "status_code": page_counts.get("_meta_http_status_code"),
                        "metric_returned": "fan_count,followers_count",
                        "raw_response": page_counts.get("_meta_raw_body"),
                        "raw_values": [page_counts.get("fan_count"), page_counts.get("followers_count")],
                        "raw_sum": None,
                        "points_count": 0,
                        "normalized_field": "followers_total",
                        "normalized_value": page_counts.get("followers_count"),
                        "unavailable_reason": None,
                    },
                )
            except HTTPException as exc:
                if not _is_meta_api_error(exc):
                    raise

            accepted_metrics: list[str] = []
            rejected_metrics: list[str] = []
            logger.info(
                "FACEBOOK_INSIGHTS_REQUEST",
                extra={
                    "report_id": None,
                    "dataset_id": None,
                    "page_id": resolved_page_id,
                    "page_name": page_name,
                    "metric_name": "insights_request",
                    "source_metric": None,
                    "raw_value": {
                        "since": timeframe_config["since"],
                        "until": timeframe_config["until"],
                    },
                    "sum_value": None,
                    "points_count": 0,
                    "unavailable_reason": None,
                },
            )
            reach_unavailable_reason = "Meta did not return unique reach for the selected period."
            organic_impressions_payload = _fetch_meta_pages_metric_payload(
                access_token,
                resolved_page_id,
                page_name,
                timeframe_config,
                integration.id,
                metric_name="page_posts_impressions_organic",
                label="Organic post impressions",
                daily_key="organic_impressions_daily",
            )
            organic_impressions_metric_name = str(organic_impressions_payload.get("metric_name") or "") or None
            organic_impressions_daily = _expand_meta_daily_series(
                list(organic_impressions_payload.get("organic_impressions_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            _log_meta_history_audit(
                page_id=resolved_page_id,
                page_name=page_name,
                metric_name=organic_impressions_metric_name or "page_posts_impressions_organic",
                selected_timeframe=str(timeframe_config.get("selected_timeframe") or timeframe_config.get("key") or ""),
                since=str(timeframe_config.get("requested_since") or timeframe_config["since"]),
                until=str(timeframe_config.get("requested_until") or timeframe_config["until"]),
                current_since=str(timeframe_config.get("current_since") or timeframe_config["since"]),
                current_until=str(timeframe_config.get("current_until") or timeframe_config["until"]),
                previous_since=str(timeframe_config.get("previous_since") or ""),
                previous_until=str(timeframe_config.get("previous_until") or ""),
                points=organic_impressions_daily,
            )
            insights["report_organic_impressions_total"] = _first_non_none(
                _sum_meta_daily_series(organic_impressions_daily),
                organic_impressions_payload.get("value"),
            )
            insights["report_organic_impressions_end_time"] = organic_impressions_payload.get("end_time")
            organic_impressions_unavailable_reason = _facebook_metric_unavailable_reason(
                value=organic_impressions_payload.get("value"),
                points=organic_impressions_daily,
                source_metric=organic_impressions_metric_name,
            )
            _log_facebook_pages_metric_event(
                "FACEBOOK_METRIC_RESOLVED_ORGANIC_IMPRESSIONS",
                page_id=resolved_page_id,
                page_name=page_name,
                metric_name="organic_impressions",
                source_metric=organic_impressions_metric_name,
                raw_value=organic_impressions_payload.get("value"),
                points=organic_impressions_daily,
                unavailable_reason=organic_impressions_unavailable_reason,
            )
            views_payload = _fetch_meta_pages_metric_payload(
                access_token,
                resolved_page_id,
                page_name,
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
                page_id=resolved_page_id,
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
            insights["report_views_total"] = _first_non_none(_sum_meta_daily_series(views_daily), views_payload.get("value"))
            insights["report_views_end_time"] = views_payload.get("end_time")
            views_unavailable_reason = _facebook_metric_unavailable_reason(
                value=views_payload.get("value"),
                points=views_daily,
                source_metric=views_metric_name,
            )
            _log_facebook_pages_metric_event(
                "FACEBOOK_METRIC_RESOLVED_PAGE_VIEWS",
                page_id=resolved_page_id,
                page_name=page_name,
                metric_name="page_views",
                source_metric=views_metric_name,
                raw_value=views_payload.get("value"),
                points=views_daily,
                unavailable_reason=views_unavailable_reason,
            )

            interactions_payload = _fetch_meta_pages_metric_payload(
                access_token,
                resolved_page_id,
                page_name,
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
                page_id=resolved_page_id,
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
            insights["report_interactions_total"] = _first_non_none(
                _sum_meta_daily_series(interactions_daily),
                interactions_payload.get("value"),
            )
            insights["report_interactions_end_time"] = interactions_payload.get("end_time")
            engagement_unavailable_reason = _facebook_metric_unavailable_reason(
                value=interactions_payload.get("value"),
                points=interactions_daily,
                source_metric=interactions_metric_name,
            )
            _log_facebook_pages_metric_event(
                "FACEBOOK_METRIC_RESOLVED_ENGAGEMENT",
                page_id=resolved_page_id,
                page_name=page_name,
                metric_name="engagement",
                source_metric=interactions_metric_name,
                raw_value=interactions_payload.get("value"),
                points=interactions_daily,
                unavailable_reason=engagement_unavailable_reason,
            )
            reactions_payload = _fetch_meta_pages_metric_payload(
                access_token,
                resolved_page_id,
                page_name,
                timeframe_config,
                integration.id,
                metric_name="page_actions_post_reactions_total",
                label="Reactions",
                daily_key="reactions_daily",
            )
            reactions_metric_name = str(reactions_payload.get("metric_name") or "") or None
            reactions_daily = _expand_meta_daily_series(
                list(reactions_payload.get("reactions_daily") or []),
                since=timeframe_config["since"],
                until=timeframe_config["until"],
            )
            insights["report_reactions_total"] = _first_non_none(
                _sum_meta_daily_series(reactions_daily),
                _sum_nested_numeric_values(reactions_payload.get("value")),
            )
            reactions_unavailable_reason = _facebook_metric_unavailable_reason(
                value=_sum_nested_numeric_values(reactions_payload.get("value")),
                points=reactions_daily,
                source_metric=reactions_metric_name,
            )
            _log_facebook_pages_metric_event(
                "FACEBOOK_METRIC_RESOLVED_REACTIONS",
                page_id=resolved_page_id,
                page_name=page_name,
                metric_name="reactions",
                source_metric=reactions_metric_name,
                raw_value=reactions_payload.get("value"),
                points=reactions_daily,
                unavailable_reason=reactions_unavailable_reason,
            )
            accepted_metrics.extend(
                [
                    metric_name
                    for metric_name in [
                        organic_impressions_metric_name,
                        views_metric_name,
                        interactions_metric_name,
                        reactions_metric_name,
                    ]
                    if metric_name
                ]
            )

            logger.info(
                "Meta Pages insights fetch completed",
                extra={
                    "integration_id": integration.id,
                    "page_id": resolved_page_id,
                    "accepted_metrics": accepted_metrics,
                    "rejected_metrics": rejected_metrics,
                    "reach_source_metric": reach_metric_name,
                    "reach_daily_points": len(reach_daily),
                    "organic_impressions_source_metric": organic_impressions_metric_name,
                    "organic_impressions_daily_points": len(organic_impressions_daily),
                    "views_source_metric": views_metric_name,
                    "views_daily_points": len(views_daily),
                    "timeframe_days": (
                        date.fromisoformat(timeframe_config["until"])
                        - date.fromisoformat(timeframe_config["since"])
                    ).days
                    + 1,
                },
            )
            logger.info(
                "FACEBOOK_INSIGHTS_RESPONSE_METRICS",
                extra={
                    "report_id": None,
                    "dataset_id": None,
                    "page_id": resolved_page_id,
                    "page_name": page_name,
                    "metric_name": "insights_response",
                    "source_metric": None,
                    "raw_value": {
                        "accepted_metrics": accepted_metrics,
                        "rejected_metrics": rejected_metrics,
                    },
                    "sum_value": len(accepted_metrics),
                    "points_count": len(accepted_metrics),
                    "unavailable_reason": None,
                },
            )

            try:
                posts = fetch_page_posts(access_token, resolved_page_id, limit=5)
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
                    "page_id": resolved_page_id,
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
        posts_analyzed_count = len(normalized_posts)
        reactions_total = _first_non_none(
            insights.get("report_reactions_total"),
            sum(int(_meta_number(post.get("reactions")) or 0) for post in normalized_posts),
        )
        comments_total = sum(int(_meta_number(post.get("comments")) or 0) for post in normalized_posts)
        shares_total = sum(int(_meta_number(post.get("shares")) or 0) for post in normalized_posts)
        top_post_by_engagement = max(normalized_posts, key=_meta_post_score) if normalized_posts else None
        followers = page_counts.get("followers_count")
        fans_total = page_counts.get("fan_count")
        followers_unavailable_reason = None if isinstance(followers, (int, float)) else "Meta did not return followers for the selected period."
        fans_unavailable_reason = None if isinstance(fans_total, (int, float)) else "Meta did not return fans for the selected period."
        organic_impressions = insights.get("report_organic_impressions_total")
        reach = None
        engagement = insights.get("report_interactions_total")
        page_views_total = _first_non_none(insights.get("report_views_total"), insights.get("page_views_total"))
        profile_visits = None
        content_interactions = engagement
        link_clicks = None
        followers_growth = None
        _log_facebook_pages_metric_event(
            "FACEBOOK_METRIC_RESOLVED_FOLLOWERS",
            page_id=resolved_page_id,
            page_name=page_name,
            metric_name="followers",
            source_metric="followers_count" if isinstance(followers, (int, float)) else "fan_count",
            raw_value=followers,
            points=[],
            unavailable_reason=followers_unavailable_reason,
        )
        _log_facebook_pages_metric_event(
            "FACEBOOK_METRIC_RESOLVED_FANS",
            page_id=resolved_page_id,
            page_name=page_name,
            metric_name="fans",
            source_metric="fan_count",
            raw_value=fans_total,
            points=[],
            unavailable_reason=fans_unavailable_reason,
        )
        missing_metrics = [
            metric_name
            for metric_name, metric_value in {
                "organic_impressions": organic_impressions,
                "engagement": engagement,
                "page_views": page_views_total,
                "followers": followers,
                "fans": fans_total,
                "reactions": reactions_total,
            }.items()
            if metric_value is None
        ]
        logger.info(
            "Meta Pages sync normalized metrics",
            extra={
                "integration_id": integration.id,
                "page_id": resolved_page_id,
                "missing_metrics": missing_metrics,
            },
        )
        writer.writerow(
            {
                "page_id": resolved_page_id,
                "page_name": page_name,
                "fans": fans_total,
                "followers": followers,
                "impressions": organic_impressions,
                "impressions_date": insights.get("report_organic_impressions_end_time"),
                "reach": reach,
                "reach_date": None,
                "engagement": engagement,
                "engagement_date": insights.get("report_interactions_end_time"),
                "profile_visits": profile_visits,
                "content_interactions": content_interactions,
                "link_clicks": link_clicks,
                "followers_growth": followers_growth,
                "timeframe_preset": timeframe_config["preset"],
                "timeframe_since": timeframe_config["since"],
                "timeframe_until": timeframe_config["until"],
                "reach_source_metric": reach_metric_name,
                "impressions_source_metric": organic_impressions_metric_name,
                "impressions_daily": json.dumps(organic_impressions_daily),
                "reach_daily": json.dumps(reach_daily),
                "recent_posts": json.dumps(normalized_posts),
            }
        )

        csv_bytes = csv_output.getvalue().encode("utf-8")
        filename = f"meta_page_{resolved_page_id}_insights.csv"
        dataset_data = {
            "page_name": page_name,
            "followers": followers,
            "followers_total": followers,
            "fans": fans_total,
            "fans_total": fans_total,
            "reach": reach,
            "reach_total": reach,
            "engagement": engagement,
            "engagement_total": insights.get("report_interactions_total"),
            "organic_impressions": organic_impressions,
            "organic_impressions_total": organic_impressions,
            "impressions": None,
            "impressions_total": None,
            "page_views_total": page_views_total,
            "profile_visits": profile_visits,
            "content_interactions": content_interactions,
            "link_clicks": link_clicks,
            "followers_growth": followers_growth,
            "daily_reach": reach_daily,
            "daily_organic_impressions": organic_impressions_daily,
            "daily_impressions": [],
            "daily_engagement": interactions_daily,
            "daily_page_views": views_daily,
            "posts_analyzed_count": posts_analyzed_count,
            "reactions_total": reactions_total,
            "comments_total": comments_total,
            "shares_total": shares_total,
            "top_post_by_engagement": top_post_by_engagement,
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
            "organic_impressions_source_metric": organic_impressions_metric_name,
            "impressions_source_metric": None,
            "page_views_source_metric": views_metric_name,
            "engagement_source_metric": interactions_metric_name,
            "organic_impressions_daily": organic_impressions_daily,
            "impressions_daily": [],
            "reach_daily": reach_daily,
            "facebook_metric_audit": {
                "reach": _facebook_metric_audit_entry(
                    source_metric=reach_metric_name,
                    raw_value=reach,
                    points=reach_daily,
                    unavailable_reason=reach_unavailable_reason,
                ),
                "organic_impressions": _facebook_metric_audit_entry(
                    source_metric=organic_impressions_metric_name,
                    raw_value=organic_impressions,
                    points=organic_impressions_daily,
                    unavailable_reason=organic_impressions_unavailable_reason,
                ),
                "impressions": _facebook_metric_audit_entry(
                    source_metric=None,
                    raw_value=None,
                    points=[],
                    unavailable_reason="Meta did not return general page impressions for the selected period.",
                ),
                "engagement": _facebook_metric_audit_entry(
                    source_metric=interactions_metric_name,
                    raw_value=insights.get("report_interactions_total"),
                    points=interactions_daily,
                    unavailable_reason=engagement_unavailable_reason,
                ),
                "followers": _facebook_metric_audit_entry(
                    source_metric="followers_count" if isinstance(followers, (int, float)) else "fan_count",
                    raw_value=followers,
                    points=[],
                    unavailable_reason=followers_unavailable_reason,
                ),
                "fans": _facebook_metric_audit_entry(
                    source_metric="fan_count",
                    raw_value=fans_total,
                    points=[],
                    unavailable_reason=fans_unavailable_reason,
                ),
                "page_views": _facebook_metric_audit_entry(
                    source_metric=views_metric_name,
                    raw_value=page_views_total,
                    points=views_daily,
                    unavailable_reason=views_unavailable_reason,
                ),
                "reactions": _facebook_metric_audit_entry(
                    source_metric=reactions_metric_name,
                    raw_value=reactions_total,
                    points=reactions_daily,
                    unavailable_reason=reactions_unavailable_reason,
                ),
            },
            "unavailable_metrics": {
                "reach": reach_unavailable_reason,
                "organic_impressions": organic_impressions_unavailable_reason,
                "impressions": "Meta did not return general page impressions for the selected period.",
                "engagement": engagement_unavailable_reason,
                "page_views": views_unavailable_reason,
                "followers": followers_unavailable_reason,
                "fans": fans_unavailable_reason,
                "reactions": reactions_unavailable_reason,
            },
            "report_metric_mapping": {
                "organic_impressions": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Organic Impressions",
                    source_metric_name=organic_impressions_metric_name,
                    total=organic_impressions,
                    daily_series=organic_impressions_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
                "views": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Visualizaciones",
                    source_metric_name=views_metric_name,
                    total=page_views_total,
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
                "reactions": _build_meta_report_metric_entry(
                    facebook_ui_target_label="Reactions",
                    source_metric_name=reactions_metric_name,
                    total=reactions_total,
                    daily_series=reactions_daily,
                    timeframe_since=timeframe_config["since"],
                    timeframe_until=timeframe_config["until"],
                ),
            },
            "normalized_report_metrics": {
                "organic_impressions_total": organic_impressions,
                "daily_organic_impressions": organic_impressions_daily,
                "organic_impressions_daily": organic_impressions_daily,
                "reach_total": reach,
                "daily_reach": reach_daily,
                "impressions_total": None,
                "impressions_daily": [],
                "daily_impressions": [],
                "page_views_total": page_views_total,
                "daily_page_views": views_daily,
                "engagement_total": insights.get("report_interactions_total"),
                "daily_engagement": interactions_daily,
                "followers_total": followers,
                "fans_total": fans_total,
                "posts_analyzed_count": posts_analyzed_count,
                "reactions_total": reactions_total,
                "comments_total": comments_total,
                "shares_total": shares_total,
                "top_post_by_engagement": top_post_by_engagement,
                "views_total": page_views_total,
                "views_daily": views_daily,
                "viewers_total": reach,
                "viewers_daily": reach_daily,
                "interactions_total": insights.get("report_interactions_total"),
                "interactions_daily": interactions_daily,
                "daily_reactions": reactions_daily,
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
        organic_impressions_first_date, organic_impressions_last_date = _meta_daily_series_bounds(organic_impressions_daily)
        logger.info(
            "[MetaTimeframeBackend][sync.dataset.before_save]",
            extra={
                "reach_daily_points": len(reach_daily),
                "organic_impressions_daily_points": len(organic_impressions_daily),
                "reach_first_date": reach_first_date,
                "reach_last_date": reach_last_date,
                "organic_impressions_first_date": organic_impressions_first_date,
                "organic_impressions_last_date": organic_impressions_last_date,
                "dataset_timeframe_to_save": dataset_data["timeframe"],
            },
        )
        for metric_name, metric_points in {
            "dataset.reach_daily": reach_daily,
            "dataset.organic_impressions_daily": organic_impressions_daily,
            "dataset.views_daily": views_daily,
            "dataset.interactions_daily": interactions_daily,
            "dataset.reactions_daily": reactions_daily,
        }.items():
            _log_meta_history_audit(
                page_id=resolved_page_id,
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
        logger.info(
            "[MetaTimeframeBackend] sync metrics resolved",
            extra={
                "page_id": resolved_page_id,
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
                "organic_impressions_source_metric": organic_impressions_metric_name,
                "organic_impressions_daily_points": len(organic_impressions_daily),
                "organic_impressions_daily_first_date": organic_impressions_first_date,
                "organic_impressions_daily_last_date": organic_impressions_last_date,
                "organic_impressions_daily_total": sum(
                    int(point.get("value"))
                    for point in organic_impressions_daily
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
        logger.info(
            "FACEBOOK_REPORT_DATASET_NORMALIZED",
            extra={
                "report_id": None,
                "dataset_id": dataset.id,
                "page_id": resolved_page_id,
                "page_name": page_name,
                "metric_name": "dataset",
                "source_metric": None,
                "raw_value": {
                    "keys": sorted(dataset_data.keys()),
                    "normalized_metric_keys": sorted((dataset_data.get("normalized_report_metrics") or {}).keys()),
                },
                "sum_value": None,
                "points_count": 0,
                "unavailable_reason": None,
            },
        )
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
        saved_organic_impressions_daily = (
            saved_dataset_data.get("daily_organic_impressions")
            if isinstance(saved_dataset_data.get("daily_organic_impressions"), list)
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
                "organic_impressions_daily_length": len(saved_organic_impressions_daily),
            },
        )
        logger.info(
            "[MetaTimeframeBackend] dataset created",
            extra={
                "integration_id": integration.id,
                "page_id": resolved_page_id,
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
                "organic_impressions_daily_points_saved": len(organic_impressions_daily),
                "organic_impressions_first_date_saved": organic_impressions_first_date,
                "organic_impressions_last_date_saved": organic_impressions_last_date,
            },
        )

        key = f"workspaces/{integration.workspace_id}/datasets/{dataset.id}/{filename}"
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        logger.info(
            "S3 put_object start",
            extra={
                "bucket": settings.s3_inputs_bucket,
                "aws_region": settings.aws_region,
                "key": key,
                "csv_bytes_length": len(csv_bytes),
            },
        )
        try:
            s3.put_object(Bucket=settings.s3_inputs_bucket, Key=key, Body=csv_bytes)
        except Exception as exc:
            print("S3 PUT ERROR")
            print(exc)
            print(repr(exc))
            if hasattr(exc, "response"):
                print(getattr(exc, "response"))
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
                    "exception_repr": repr(exc),
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
                "page_id": resolved_page_id,
                "dataset_id": dataset.id,
                "dataset_file_id": dataset_file.id,
                "dataset_timeframe_key": dataset_data["timeframe"].get("key"),
                "dataset_timeframe_saved": dataset_data["timeframe"],
                "reach_daily_points_saved": len(reach_daily),
                "organic_impressions_daily_points_saved": len(organic_impressions_daily),
            },
        )

        return MetaPagesSyncOut(
            integration_id=integration.id,
            dataset_id=dataset.id,
            dataset_file_id=dataset_file.id,
            page_id=resolved_page_id,
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
                "integration_id": integration_id,
                "timeframe": timeframe,
                "page_id": page_id,
            },
        )
        raise http_error(500, "meta_pages_sync_failed", f"Meta Pages sync failed: {exc}")


def _resolve_meta_sync_all_integration(
    db: Session,
    *,
    current_user: User,
    integration_id: int | None,
    workspace_id: int | None,
) -> Integration:
    if integration_id is not None:
        integration = _get_meta_integration(db, current_user, integration_id)
    else:
        resolved_workspace_id = _resolve_meta_connect_workspace_id(
            db,
            user_id=current_user.id,
            requested_workspace_id=workspace_id,
        )
        integration = (
            db.query(Integration)
            .filter(
                Integration.workspace_id == resolved_workspace_id,
                Integration.provider == "meta",
            )
            .order_by(Integration.id.asc())
            .first()
        )
        if not integration:
            raise http_error(
                400,
                "meta_not_connected",
                "The authenticated user does not have a connected Meta account for this workspace.",
            )
    if workspace_id is not None and int(workspace_id) != int(integration.workspace_id):
        raise http_error(
            400,
            "workspace_mismatch",
            "workspace_id does not match the integration workspace.",
        )
    if str(integration.status or "").strip().lower() != "connected":
        raise http_error(
            400,
            "meta_not_connected",
            "The authenticated user does not have a connected Meta account for this workspace.",
        )
    return integration


def _is_meta_permissions_error_message(message: str) -> bool:
    normalized = str(message or "").lower()
    permission_markers = (
        "permission",
        "permissions",
        "not authorized",
        "does not have permission",
        "access denied",
        "insufficient permission",
    )
    return any(marker in normalized for marker in permission_markers)


def _is_meta_token_expired_error(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if detail.get("code") != "meta_api_error":
        return False
    error_details = _meta_api_error_details(exc)
    error_code = error_details.get("error_code")
    message = str(error_details.get("error_message") or "").lower()
    return error_code == 190 or "expired" in message or "invalid oauth" in message


def _meta_sync_all_error_result(source_label: str, exc: HTTPException) -> MetaSyncSourceResultOut:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error_code = str(detail.get("code") or "sync_failed")
    message = str(detail.get("message") or f"{source_label} sync failed.")
    if _is_meta_token_expired_error(exc):
        message = "Meta access token expired. Reconnect Meta and try again."
    elif _is_meta_api_error(exc) and _is_meta_permissions_error_message(message):
        message = f"Missing required Meta permissions for {source_label}."
    return MetaSyncSourceResultOut(
        success=False,
        message=message,
        error_code=error_code,
        error=message,
    )


def _meta_sync_all_success_result(
    *,
    message: str,
    dataset_id: int,
    dataset_file_id: int,
    timeframe: dict[str, Any] | None,
) -> MetaSyncSourceResultOut:
    return MetaSyncSourceResultOut(
        success=True,
        dataset_id=dataset_id,
        dataset_file_id=dataset_file_id,
        message=message,
        timeframe=timeframe,
    )


def _execute_meta_source_sync(
    *,
    integration_id: int,
    current_user: User,
    source_key: str,
    run_sync,
) -> MetaSyncSourceResultOut:
    source_db = SessionLocal()
    try:
        logger.info(
            "Meta sync-all source started",
            extra={
                "integration_id": integration_id,
                "user_id": current_user.id,
                "source": source_key,
            },
        )
        result = run_sync(source_db)
        logger.info(
            "Meta sync-all source completed",
            extra={
                "integration_id": integration_id,
                "user_id": current_user.id,
                "source": source_key,
                "dataset_id": result.dataset_id,
                "dataset_file_id": result.dataset_file_id,
            },
        )
        return result
    except HTTPException as exc:
        source_db.rollback()
        logger.warning(
            "Meta sync-all source failed",
            extra={
                "integration_id": integration_id,
                "user_id": current_user.id,
                "source": source_key,
                "error": exc.detail if isinstance(exc.detail, dict) else str(exc.detail),
            },
        )
        raise
    except Exception:
        source_db.rollback()
        logger.exception(
            "Meta sync-all source failed unexpectedly",
            extra={
                "integration_id": integration_id,
                "user_id": current_user.id,
                "source": source_key,
            },
        )
        raise
    finally:
        source_db.close()


@app.post("/integrations/meta/sync-instagram-business", response_model=InstagramBusinessSyncOut)
def sync_instagram_business(
    payload: InstagramBusinessSyncIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstagramBusinessSyncOut:
    return _run_instagram_business_sync(
        db=db,
        current_user=current_user,
        integration_id=payload.integration_id,
        instagram_account_id=payload.instagram_account_id,
        workspace_id=payload.workspace_id,
        timeframe=payload.timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
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
    return _run_meta_pages_sync(
        db=db,
        current_user=current_user,
        integration_id=final_integration_id,
        page_id=str(body_page_id).strip() if body_page_id is not None else None,
        timeframe=final_timeframe,
        start_date=final_start_date,
        end_date=final_end_date,
        raw_query_params=raw_query_params,
        raw_body=raw_body,
    )


@app.post("/integrations/meta/sync-all", response_model=MetaSyncAllOut)
def meta_sync_all(
    payload: MetaSyncAllIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaSyncAllOut:
    facebook_page_id = str(payload.facebook_page_id or "").strip() or None
    instagram_business_account_id = str(payload.instagram_business_account_id or "").strip() or None
    if not facebook_page_id and not instagram_business_account_id:
        raise http_error(
            422,
            "missing_data_sources",
            "At least one data source must be selected for sync.",
        )

    integration = _resolve_meta_sync_all_integration(
        db,
        current_user=current_user,
        integration_id=payload.integration_id,
        workspace_id=payload.workspace_id,
    )
    timeframe_preset = str(payload.timeframe.preset or "last_28_days").strip() or "last_28_days"
    timeframe_since = payload.timeframe.since
    timeframe_until = payload.timeframe.until

    results = MetaSyncAllResultsOut()
    source_outcomes: list[bool] = []

    if facebook_page_id:
        def run_facebook_sync(source_db: Session) -> MetaSyncSourceResultOut:
            sync_result = _run_meta_pages_sync(
                db=source_db,
                current_user=current_user,
                integration_id=integration.id,
                page_id=facebook_page_id,
                timeframe=timeframe_preset,
                start_date=timeframe_since,
                end_date=timeframe_until,
                raw_query_params={},
                raw_body={
                    "facebook_page_id": facebook_page_id,
                    "timeframe": payload.timeframe.model_dump(),
                },
            )
            return _meta_sync_all_success_result(
                message="Facebook Pages synced successfully",
                dataset_id=sync_result.dataset_id,
                dataset_file_id=sync_result.dataset_file_id,
                timeframe=sync_result.timeframe,
            )

        try:
            page_result = _execute_meta_source_sync(
                integration_id=integration.id,
                current_user=current_user,
                source_key="facebook_pages",
                run_sync=run_facebook_sync,
            )
            results.facebook_pages = page_result
            source_outcomes.append(True)
        except HTTPException as exc:
            results.facebook_pages = _meta_sync_all_error_result("Facebook Pages", exc)
            source_outcomes.append(False)

    if instagram_business_account_id:
        def run_instagram_sync(source_db: Session) -> MetaSyncSourceResultOut:
            sync_result = _run_instagram_business_sync(
                db=source_db,
                current_user=current_user,
                integration_id=integration.id,
                instagram_account_id=instagram_business_account_id,
                workspace_id=integration.workspace_id,
                timeframe=timeframe_preset,
                start_date=timeframe_since,
                end_date=timeframe_until,
            )
            return _meta_sync_all_success_result(
                message="Instagram Business synced successfully",
                dataset_id=sync_result.dataset_id,
                dataset_file_id=sync_result.dataset_file_id,
                timeframe=sync_result.timeframe,
            )

        try:
            instagram_result = _execute_meta_source_sync(
                integration_id=integration.id,
                current_user=current_user,
                source_key="instagram_business",
                run_sync=run_instagram_sync,
            )
            results.instagram_business = instagram_result
            source_outcomes.append(True)
        except HTTPException as exc:
            results.instagram_business = _meta_sync_all_error_result("Instagram Business", exc)
            source_outcomes.append(False)

    return MetaSyncAllOut(
        success=any(source_outcomes),
        results=results,
    )


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
