from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserSchema(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
    email_verified: bool
    auth_provider: str
    is_admin: bool = False
    onboarding_completed: bool = False
    user_type: Optional[str] = None
    goals: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    last_login_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RegisterIn(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    referral_code: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None


class RegisterOut(BaseModel):
    message: str
    verification_required: bool = True
    user_id: Optional[int] = None
    email: Optional[str] = None
    workspace_id: Optional[int] = None
    plan: Optional[str] = None


class VerifyEmailIn(BaseModel):
    email: str
    code: str


class VerifyEmailUserOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    email_verified: bool
    onboarding_completed: bool = False


class VerifyEmailOut(BaseModel):
    ok: bool = True
    access_token: str
    token_type: str = "bearer"
    user: VerifyEmailUserOut


class ResendVerificationCodeIn(BaseModel):
    email: str


class ForgotPasswordIn(BaseModel):
    email: str


class ResetPasswordIn(BaseModel):
    email: str
    code: str
    new_password: str


class AuthMessageOut(BaseModel):
    message: str


class MetaTrackingEventIn(BaseModel):
    event_name: str
    event_id: Optional[str] = None
    event_source_url: Optional[str] = None
    fbp: Optional[str] = None
    fbc: Optional[str] = None
    custom_data: Optional[dict[str, Any]] = None


class MetaTrackingEventOut(BaseModel):
    ok: bool = True
    sent: bool = False


class ReferralPartnerCreateIn(BaseModel):
    name: str
    code: str
    type: Optional[str] = None
    commission_type: Optional[str] = None
    commission_value: Optional[float] = None
    status: str = "active"


class ReferralPartnerOut(BaseModel):
    id: int
    name: str
    code: str
    type: Optional[str] = None
    commission_type: Optional[str] = None
    commission_value: Optional[float] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ReferralClickIn(BaseModel):
    referral_code: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None
    landing_page: Optional[str] = None


class ReferralClickOut(BaseModel):
    id: int
    referral_code: Optional[str] = None
    created_at: datetime


class ReferralSummaryOut(BaseModel):
    referral_code: Optional[str] = None
    partner_name: Optional[str] = None
    clicks: int = 0
    signups: int = 0
    first_reports: int = 0
    paid_conversions: int = 0
    revenue: float = 0.0
    estimated_commission: float = 0.0


class ReferralManualConversionIn(BaseModel):
    user_id: int
    conversion_type: str
    plan: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "USD"


class ReferralConversionOut(BaseModel):
    id: int
    user_id: int
    referral_code: Optional[str] = None
    conversion_type: str
    plan: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "USD"
    commission_amount: Optional[float] = None
    status: str
    created_at: datetime


class DeleteAccountIn(BaseModel):
    reason: Optional[str] = None
    details: Optional[str] = None
    confirmation: str


class DeleteAccountOut(BaseModel):
    ok: bool = True


class OnboardingUpdate(BaseModel):
    user_type: str
    goals: list[str]
    platforms: list[str]

    _allowed_user_types: ClassVar[set[str]] = {"freelancer", "agency", "business", "team"}
    _allowed_goals: ClassVar[set[str]] = {
        "track_growth",
        "client_reports",
        "fast_insights",
        "improve_performance",
        "understand_data",
        "export_reports",
        "automate_reports",
    }
    _allowed_platforms: ClassVar[set[str]] = {
        "facebook",
        "instagram",
        "tiktok",
        "google_analytics",
        "shopify",
        "meta_ads",
        "google_ads",
        "other",
    }

    @field_validator("user_type")
    @classmethod
    def validate_user_type(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in cls._allowed_user_types:
            raise ValueError("Invalid user_type.")
        return normalized

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        invalid = [item for item in normalized if item not in cls._allowed_goals]
        if invalid:
            raise ValueError("Invalid goals value.")
        return normalized

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        invalid = [item for item in normalized if item not in cls._allowed_platforms]
        if invalid:
            raise ValueError("Invalid platforms value.")
        return normalized


class OnboardingStateOut(BaseModel):
    onboarding_completed: bool
    user_type: Optional[str] = None
    goals: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class OnboardingCompleteOut(BaseModel):
    ok: bool = True
    onboarding_completed: bool = True


class LoginIn(BaseModel):
    email: str
    password: str


class ChatMessageIn(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    workspace_id: Optional[int] = None
    report_id: Optional[int] = None
    dataset_id: Optional[int] = None
    current_route: Optional[str] = None
    page_context: Optional[dict[str, Any]] = None


class ChatReplyOut(BaseModel):
    conversation_id: int
    reply: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminMetricsOut(BaseModel):
    timeframe: str = "all"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    total_users: int
    users_in_period: int
    active_users_in_period: int
    reports_in_period: int
    onboarding_completed_in_period: int
    onboarding_completion_rate: float
    deletions_in_period: int = 0
    users_last_7_days: int = 0
    active_users_last_7_days: int = 0
    onboarding_completed: int = 0
    onboarding_pending: int = 0
    total_reports: int = 0
    reports_last_7_days: int = 0
    paid_users: int
    free_users: int
    mrr: float
    daily_users: list["AdminDailyUsersOut"] = Field(default_factory=list)
    daily_reports: list["AdminDailyReportsOut"] = Field(default_factory=list)
    cumulative_users: list["AdminCumulativeUsersOut"] = Field(default_factory=list)
    users_growth_percent: Optional[float] = None
    reports_growth_percent: Optional[float] = None
    active_users_growth_percent: Optional[float] = None
    insights: list["AdminInsightOut"] = Field(default_factory=list)


class AdminDailyUsersOut(BaseModel):
    date: date
    users: int


class AdminDailyReportsOut(BaseModel):
    date: date
    reports: int


class AdminCumulativeUsersOut(BaseModel):
    date: date
    total_users: int


class AdminFunnelStepOut(BaseModel):
    name: str
    count: int
    conversion_from_previous: float = 0.0
    conversion_from_start: float = 0.0
    dropoff: int = 0


class AdminFunnelOut(BaseModel):
    steps: list[AdminFunnelStepOut]
    summary: dict[str, str | float] = Field(default_factory=dict)


class AdminCohortRetentionOut(BaseModel):
    day_0: float = 100.0
    day_1: float = 0.0
    day_3: float = 0.0
    day_7: float = 0.0
    day_14: float = 0.0
    day_30: float = 0.0


class AdminCohortOut(BaseModel):
    date: date
    size: int
    retention: AdminCohortRetentionOut


class AdminCohortAveragesOut(BaseModel):
    day_1: float = 0.0
    day_3: float = 0.0
    day_7: float = 0.0
    day_14: float = 0.0
    day_30: float = 0.0


class AdminCohortsOut(BaseModel):
    cohorts: list[AdminCohortOut]
    averages: AdminCohortAveragesOut = Field(default_factory=AdminCohortAveragesOut)


class AdminInsightOut(BaseModel):
    type: str
    message: str
    severity: Literal["positive", "neutral", "warning", "critical"]


class AdminUserOut(BaseModel):
    id: int
    full_name: Optional[str] = None
    email: str
    auth_provider: str
    email_verified: bool
    onboarding_completed: bool
    user_type: Optional[str] = None
    plan: Optional[str] = None
    reports_count: int = 0
    last_login_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    last_report_created_at: Optional[datetime] = None
    last_report_at: Optional[datetime] = None
    last_report_created: Optional[datetime] = None
    reports_last_7_days: int = 0
    health_score: int = 0
    health_status: Literal["healthy", "active", "at_risk", "dormant"] = "dormant"
    health_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    is_active: bool
    is_deleted: bool


class AdminUsersOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int


class AdminProductMetricsOut(BaseModel):
    avg_time_to_first_report: float = 0.0
    time_to_first_report_unit: Literal["hours", "days"] = "hours"
    reports_per_user: float = 0.0
    ai_usage_rate: float = 0.0
    repeat_usage_rate: float = 0.0
    total_users: int = 0
    users_with_reports: int = 0
    users_with_2_reports: int = 0
    users_used_ai: int = 0


class AdminOnboardingCountsOut(BaseModel):
    freelancer: int = 0
    agency: int = 0
    business: int = 0
    team: int = 0


class AdminGoalCountsOut(BaseModel):
    track_growth: int = 0
    client_reports: int = 0
    fast_insights: int = 0
    improve_performance: int = 0
    understand_data: int = 0
    export_reports: int = 0
    automate_reports: int = 0


class AdminPlatformCountsOut(BaseModel):
    facebook: int = 0
    instagram: int = 0
    tiktok: int = 0
    google_analytics: int = 0
    shopify: int = 0
    meta_ads: int = 0
    google_ads: int = 0
    other: int = 0


class AdminOnboardingInsightsOut(BaseModel):
    user_types: AdminOnboardingCountsOut
    goals: AdminGoalCountsOut
    platforms: AdminPlatformCountsOut
    completed: int
    pending: int
    completion_rate: float


class AdminDeletionReasonCountsOut(BaseModel):
    too_expensive: int = 0
    missing_features: int = 0
    hard_to_use: int = 0
    no_longer_needed: int = 0
    switching_tool: int = 0
    privacy_concerns: int = 0
    other: int = 0


class AdminDeletionFeedbackOut(BaseModel):
    email: str
    reason: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime


class AdminDeletionInsightsOut(BaseModel):
    total: int
    last_7_days: int
    reasons: AdminDeletionReasonCountsOut
    recent_feedback: list[AdminDeletionFeedbackOut]


class SuggestionCreateIn(BaseModel):
    message: str


class SuggestionStatusUpdateIn(BaseModel):
    status: Literal["new", "reviewed", "archived"]


class UserSuggestionOut(BaseModel):
    id: int
    user_id: int
    workspace_id: Optional[int] = None
    message: str
    status: str
    source: str
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class SuggestionCreateOut(BaseModel):
    success: bool = True
    suggestion: UserSuggestionOut


class AdminSuggestionOut(UserSuggestionOut):
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    workspace_name: Optional[str] = None


class WishlistCreateIn(BaseModel):
    name: str
    email: str
    company: Optional[str] = None
    message: str
    source: str = "upgrade_page"


class WishlistLeadOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    workspace_id: Optional[int] = None
    name: str
    email: str
    company: Optional[str] = None
    message: str
    source: str
    created_at: datetime


class WishlistCreateOut(BaseModel):
    success: bool = True
    lead: WishlistLeadOut


class AdminWishlistLeadOut(WishlistLeadOut):
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    workspace_name: Optional[str] = None


class AdminInsightsOut(BaseModel):
    onboarding: AdminOnboardingInsightsOut
    deletions: AdminDeletionInsightsOut


class BrandingOut(BaseModel):
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    brand_logo_url: Optional[str] = None
    fallback_logo_url: Optional[str] = None
    resolved_logo_url: Optional[str] = None
    resolved_brand_name: Optional[str] = None
    source: Optional[Literal["user", "measurable"]] = None
    watermark_enabled: Optional[bool] = None
    watermark_label: Optional[str] = None
    watermark_logo_light_url: Optional[str] = None
    watermark_logo_dark_url: Optional[str] = None
    has_custom_branding: bool = False


class MeOut(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
    workspace_id: Optional[int] = None
    account_display_name: Optional[str] = None
    account_display_name_effective: str = "Measurable Account"
    email_verified: bool
    auth_provider: str
    is_admin: bool = False
    last_login_at: Optional[datetime] = None
    logo_url: Optional[str] = None
    branding: BrandingOut = BrandingOut()
    current_plan_name: Optional[str] = None
    current_plan_code: Optional[str] = None
    is_free_plan: bool = False
    can_use_custom_branding: bool = False
    report_branding_mode: Literal["measurable", "custom"] = "measurable"
    created_at: datetime
    updated_at: datetime


class MeUpdateIn(BaseModel):
    full_name: Optional[str] = None
    logo_url: Optional[str] = None


class WorkspaceSchema(BaseSchema):
    id: int
    name: str
    account_display_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberSchema(BaseSchema):
    id: int
    workspace_id: int
    user_id: int
    role: str
    created_at: datetime
    updated_at: datetime


class ConversationOut(BaseSchema):
    id: int
    workspace_id: int
    title: Optional[str]
    created_at: datetime


class MessageOut(BaseSchema):
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime


class SubscriptionSchema(BaseSchema):
    id: int
    workspace_id: int
    plan: str
    status: str
    billing_status: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_price_id: Optional[str] = None
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    cancel_at_period_end: Optional[bool] = None
    reports_limit_monthly: Optional[int] = None
    slides_per_report_limit: Optional[int] = None
    platform_report_type: Optional[str] = None
    ai_chat_with_data: Optional[bool] = None
    storage_limit_gb: Optional[int] = None
    export_pdf: Optional[bool] = None
    export_pptx: Optional[bool] = None
    brand_personalization: Optional[bool] = None
    measurable_watermark: Optional[bool] = None
    scheduled_reports_limit: Optional[int] = None
    trial_new_features: Optional[bool] = None
    created_at: datetime
    updated_at: datetime


class BillingMeOut(BaseModel):
    plan_code: str = "free"
    plan_name: str = "Free"
    billing_status: str = "free"
    current_period_end: Optional[datetime] = None
    price_monthly_usd: int = 0
    cancel_at_period_end: bool = False
    reports_limit_monthly: Optional[int] = None
    reports_used_current_month: int = 0
    slides_per_report_limit: int = 5
    platform_report_type: str = "single_platform"
    ai_chat_with_data: bool = True
    storage_limit_gb: int = 1
    export_pdf: bool = True
    export_pptx: bool = False
    brand_personalization: bool = False
    measurable_watermark: bool = True
    scheduled_reports_limit: Optional[int] = 0
    trial_new_features: bool = False


class BillingCheckoutSessionIn(BaseModel):
    plan_code: Literal["starter", "pro", "advanced"]


class BillingPlanSnapshotOut(BaseModel):
    plan_code: str
    plan_name: str
    price_monthly_usd: int
    reports_limit_monthly: Optional[int] = None
    slides_per_report_limit: int
    export_pdf: bool
    export_pptx: bool
    brand_personalization: bool
    measurable_watermark: bool
    scheduled_reports_limit: Optional[int] = 0


class BillingPlanChangePreviewOut(BaseModel):
    action_mode: Literal["checkout", "updated", "already_on_plan"] = "checkout"
    requires_confirmation: bool = False
    billing_status: str = "free"
    current_period_end: Optional[datetime] = None
    billing_note: str
    current_plan: BillingPlanSnapshotOut
    new_plan: BillingPlanSnapshotOut


class BillingCheckoutSessionOut(BaseModel):
    mode: Literal["checkout", "updated", "already_on_plan"] = "checkout"
    checkout_url: Optional[str] = None
    plan_code: Optional[Literal["starter", "pro", "advanced"]] = None
    billing_status: Optional[str] = None
    plan_name: Optional[str] = None
    price_monthly_usd: Optional[int] = None
    current_period_end: Optional[datetime] = None


class BillingPortalSessionOut(BaseModel):
    portal_url: str


class DatasetSchema(BaseSchema):
    id: int
    workspace_id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class DatasetFileSchema(BaseSchema):
    id: int
    dataset_id: int
    workspace_id: int
    s3_key: str
    size_bytes: Optional[int]
    content_type: Optional[str]
    created_at: datetime
    updated_at: datetime


class IntegrationSchema(BaseSchema):
    id: int
    workspace_id: int
    provider: str
    name: Optional[str]
    status: str
    integration_id: Optional[int] = None
    connected: bool = False
    asset_count: int = 0
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    last_connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class IntegrationAccountSchema(BaseSchema):
    id: int
    integration_id: int
    workspace_id: int
    external_account_id: str
    display_name: Optional[str]
    created_at: datetime
    updated_at: datetime


class IntegrationTokenSchema(BaseSchema):
    id: int
    account_id: int
    workspace_id: int
    token_type: str
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ReportSchema(BaseSchema):
    id: int
    workspace_id: int
    dataset_id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class ReportVersionSchema(BaseSchema):
    id: int
    report_id: int
    version: int
    created_at: datetime
    updated_at: datetime


class ReportBlockSchema(BaseSchema):
    id: int
    report_version_id: int
    type: str
    order: int
    data_json: Optional[str]
    editable_fields_json: Optional[str]
    created_at: datetime
    updated_at: datetime


class ExportSchema(BaseSchema):
    id: int
    workspace_id: int
    report_id: Optional[int]
    status: str
    output_s3_key: Optional[str]
    created_at: datetime
    updated_at: datetime


class ScheduleSchema(BaseSchema):
    id: int
    workspace_id: int
    report_id: Optional[int]
    integration_id: Optional[int]
    freq: str
    day_of_month: Optional[int]
    timezone: str
    enabled: bool
    next_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class JobSchema(BaseSchema):
    id: int
    workspace_id: int
    schedule_id: Optional[int]
    export_id: Optional[int]
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ScheduleCreateIn(BaseModel):
    workspace_id: int
    integration_id: int
    freq: str = "monthly"
    day_of_month: Optional[int] = None
    timezone: str


class ScheduleUpdateIn(BaseModel):
    enabled: bool


class WorkspaceCreateIn(BaseModel):
    name: str
    logo_url: Optional[str] = None
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None


class WorkspaceUpdateIn(BaseModel):
    name: Optional[str] = None
    account_display_name: Optional[str] = None
    logo_url: Optional[str] = None
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None


class WorkspaceAccountDisplayNameUpdateIn(BaseModel):
    account_display_name: Optional[str] = None


class WorkspaceBrandingUpdateIn(BaseModel):
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brandName: Optional[str] = None
    name: Optional[str] = None
    logo_url: Optional[str] = None
    logoUrl: Optional[str] = None
    remove_logo: Optional[bool] = None


class WorkspaceBrandingLogoUploadOut(BaseModel):
    logo_url: str


class PlanLimitsOut(BaseModel):
    reports_per_month: Optional[int] = None
    max_slides_per_report: int
    max_slides: int
    storage_limit_bytes: int
    allow_pdf_export: bool = False
    allow_pptx_export: bool = False
    allow_ai_agents: bool = False
    allow_custom_branding: bool = False


class WorkspaceOut(BaseSchema):
    id: int
    name: str
    account_display_name: Optional[str] = None
    account_display_name_effective: str = "Measurable Account"
    logo_url: Optional[str] = None
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    branding: BrandingOut = BrandingOut()
    plan: str = "free"
    plan_limits: PlanLimitsOut
    storage_used_bytes: int = 0
    storage_limit_bytes: int
    created_at: datetime
    updated_at: datetime


class MetaSelectAccountIn(BaseModel):
    integration_id: int
    business_id: str
    ad_account_id: str


class MetaAdsConnectOut(BaseModel):
    auth_url: str
    integration_id: int
    scope: str
    message: str


class MetaAdsAccountOut(BaseModel):
    id: int
    account_id: str
    name: str
    currency: Optional[str] = None
    timezone_name: Optional[str] = None
    account_status: Optional[str] = None
    business_id: Optional[str] = None
    business_name: Optional[str] = None
    is_selected: bool = False
    last_synced_at: Optional[datetime] = None
    source: Optional[str] = None


MetaVisibleProvider = Literal["facebook_pages", "instagram_business", "meta_ads"]
MetaVisibleProviderStatus = Literal[
    "connected",
    "connected_no_assets",
    "needs_permission",
    "no_token",
    "disconnected",
    "available",
    "checking",
    "error",
]


class MetaProviderStatusOut(BaseModel):
    provider: MetaVisibleProvider
    integration_id: Optional[int] = None
    status: MetaVisibleProviderStatus = "available"
    connected: bool = False
    asset_count: int = 0
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    last_connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: str = "idle"


class MetaBusinessSuiteChildStatusOut(BaseModel):
    status: MetaVisibleProviderStatus = "available"
    connected: bool = False
    asset_count: int = 0
    integration_id: Optional[int] = None
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: str = "idle"


class MetaBusinessSuiteStatusOut(BaseModel):
    provider: Literal["meta_business_suite"] = "meta_business_suite"
    connected: bool = False
    status: MetaVisibleProviderStatus = "available"
    integration_id: Optional[int] = None
    last_connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: str = "idle"
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    children: dict[MetaVisibleProvider, MetaBusinessSuiteChildStatusOut] = Field(default_factory=dict)


class MetaAdsStatusOut(BaseModel):
    integration_id: int
    workspace_id: int
    provider: str = "meta_ads"
    connected: bool = False
    status: str = "disconnected"
    scope: str
    selected_account: Optional[MetaAdsAccountOut] = None
    accounts_count: int = 0
    asset_count: int = 0
    account_names: list[str] = Field(default_factory=list)
    missing_scopes: list[str] = Field(default_factory=list)
    last_synced_at: Optional[datetime] = None
    reconnect_required: bool = False
    permission_missing: bool = False
    message: Optional[str] = None
    last_connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: str = "idle"


class InstagramBusinessStatusOut(BaseModel):
    connected: bool = False
    provider: str = "instagram_business"
    integration_id: Optional[int] = None
    asset_count: int = 0
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    last_connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    discovery_status: str = "idle"
    status: Literal[
        "connected",
        "disconnected",
        "needs_permission",
        "connected_no_assets",
        "no_token",
        "available",
        "checking",
        "error",
    ] = "disconnected"


class InstagramBusinessLoginConnectOut(BaseModel):
    provider: str = "instagram_business_login"
    integration_id: int
    auth_url: str
    scope: str
    scopes: list[str] = Field(default_factory=list)
    message: str


class InstagramBusinessLoginStatusOut(BaseModel):
    provider: str = "instagram_business_login"
    connected: bool = False
    status: Literal["connected", "needs_permission", "no_token", "disconnected", "error"] = "disconnected"
    integration_id: Optional[int] = None
    account_count: int = 0
    missing_scopes: list[str] = Field(default_factory=list)
    message: Optional[str] = None


class InstagramBusinessLoginAccountOut(BaseModel):
    id: str
    instagram_user_id: str
    username: Optional[str] = None
    name: Optional[str] = None
    account_type: Optional[str] = None
    integration_id: int


class InstagramBusinessLoginAccountsOut(BaseModel):
    provider: str = "instagram_business_login"
    integration_id: Optional[int] = None
    accounts: list[InstagramBusinessLoginAccountOut] = Field(default_factory=list)


class MetaAdsSelectAccountIn(BaseModel):
    integration_id: Optional[int] = None
    workspace_id: Optional[int] = None
    ad_account_id: str


class MetaAdsSyncIn(BaseModel):
    integration_id: int
    ad_account_id: Optional[str] = None
    timeframe: str = "last_30d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class MetaAdsSyncOut(BaseModel):
    integration_id: int
    dataset_id: int
    dataset_file_id: int
    provider: str = "meta_ads"
    source_type: str = "meta_ads"
    ad_account_id: str
    ad_account_name: str
    status: str
    timeframe: Optional[dict] = None
    last_synced_at: Optional[datetime] = None


class MetaAdsDisconnectOut(BaseModel):
    success: bool = True
    provider: str = "meta_ads"
    status: str = "disconnected"
    cleared_accounts: int = 0
    cleared_rows: int = 0
    token_revoked: bool = False


class MetaSelectAccountManualIn(BaseModel):
    integration_id: int
    ad_account_id: str


class MetaSetTokenManualIn(BaseModel):
    integration_id: int
    access_token: str


class MetaDisconnectIn(BaseModel):
    integration_id: Optional[int] = None
    workspace_id: Optional[int] = None


class MetaDisconnectClearedOut(BaseModel):
    tokens: bool = False
    facebook_pages: int = 0
    instagram_accounts: int = 0
    integration_accounts: int = 0


class MetaDisconnectOut(BaseModel):
    success: bool = True
    provider: str = "meta"
    status: str = "disconnected"
    disconnected_integrations: list[str] = Field(
        default_factory=lambda: ["facebook_pages", "instagram_business"]
    )
    cleared: MetaDisconnectClearedOut = Field(default_factory=MetaDisconnectClearedOut)
    meta_revoke_status: str = "skipped"


class TikTokConnectOut(BaseModel):
    auth_url: str
    integration_id: int
    status: str = "pending"


class TikTokCallbackCompleteIn(BaseModel):
    code: Optional[str] = None
    auth_code: Optional[str] = None
    state: str


class TikTokAdvertiserAccountOut(BaseModel):
    advertiser_id: str
    advertiser_name: str
    currency: Optional[str] = None
    timezone: Optional[str] = None
    selected: bool = False
    last_synced_at: Optional[datetime] = None


class TikTokAdvertiserAccountsOut(BaseModel):
    accounts: list[TikTokAdvertiserAccountOut] = Field(default_factory=list)
    message: Optional[str] = None


class TikTokCallbackCompleteOut(BaseModel):
    connected: bool = True
    integration_id: int
    advertisers_count: int = 0
    selected_account: Optional[TikTokAdvertiserAccountOut] = None
    status: str = "connected"
    message: Optional[str] = None


class TikTokSelectAccountIn(BaseModel):
    advertiser_id: str
    integration_id: Optional[int] = None
    workspace_id: Optional[int] = None


class TikTokSyncIn(BaseModel):
    advertiser_id: Optional[str] = None
    integration_id: Optional[int] = None
    workspace_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class TikTokSyncOut(BaseModel):
    integration_id: int
    advertiser_id: str
    advertiser_name: str
    dataset_id: int
    dataset_file_id: int
    status: str
    start_date: str
    end_date: str
    metrics_summary: dict[str, Any] = Field(default_factory=dict)


class TikTokStatusOut(BaseModel):
    connected: bool = False
    status: str = "disconnected"
    advertisers_count: int = 0
    selected_advertiser: Optional[TikTokAdvertiserAccountOut] = None
    last_sync: Optional[datetime] = None
    missing_env: bool = False
    integration_id: Optional[int] = None


class TikTokDisconnectOut(BaseModel):
    success: bool = True
    provider: str = "tiktok_ads"
    status: str = "disconnected"
    advertisers_cleared: int = 0
    selected_account_cleared: bool = False
    tokens_cleared: bool = False


class ShopifyStatusOut(BaseModel):
    connected: bool = False
    status: str = "disconnected"
    provider: str = "shopify"
    integration_id: Optional[int] = None
    shop_domain: Optional[str] = None
    shop_name: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    reconnect_required: bool = False
    message: Optional[str] = None


class ShopifySyncIn(BaseModel):
    workspace_id: Optional[int] = None
    timeframe: str = "last_30d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class ShopifySyncOut(BaseModel):
    integration_id: int
    connection_id: int
    dataset_id: int
    dataset_file_id: int
    provider: str = "shopify"
    source_type: str = "shopify"
    shop_domain: str
    shop_name: Optional[str] = None
    status: str
    timeframe: dict[str, Any]
    metrics: dict[str, Any] = Field(default_factory=dict)
    last_synced_at: Optional[datetime] = None


class ShopifyDisconnectOut(BaseModel):
    success: bool = True
    provider: str = "shopify"
    status: str = "disconnected"
    token_cleared: bool = False


class ShopifyReportCreateIn(BaseModel):
    dataset_id: int
    title: Optional[str] = None
    locale: str = "en"
    timeframe: str = "last_30d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    ai_mode: Literal["standard", "agents"] = "standard"


class MetaSelectPageIn(BaseModel):
    integration_id: int
    page_id: str


class MetaPageOut(BaseModel):
    id: str
    integration_id: Optional[int] = None
    provider: Optional[str] = None
    account_id: Optional[str] = None
    page_id: Optional[str] = None
    type: str = "facebook_page"
    parent_page_id: Optional[str] = None
    facebook_page_id: Optional[str] = None
    facebook_page_name: Optional[str] = None
    username: Optional[str] = None
    name: str
    display_label: Optional[str] = None
    category: Optional[str] = None
    instagram_username: Optional[str] = None
    profile_picture_url: Optional[str] = None
    fan_count: Optional[int] = None
    followers_count: Optional[int] = None
    source: Optional[str] = None
    business_name: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    cache_status: Optional[str] = None


class MetaPageCatalogOut(BaseModel):
    data: list[MetaPageOut]
    source: str
    count: int
    provider: Optional[str] = None
    integration_id: Optional[int] = None
    suite_integration_id: Optional[int] = None
    direct_pages_count: int = 0
    business_pages_count: int = 0
    total_pages_count: int = 0
    business_management_scope_present: bool = False
    business_discovery_status: str = "skipped_missing_scope"
    discovery_status: str = "idle"
    has_cached_data: bool
    status: str = "connected"
    connected: bool = True
    refresh_available: bool = True
    refresh_recommended: bool = False
    message: Optional[str] = None
    limit: int = 50
    offset: int = 0
    search: Optional[str] = None


class MetaPagesRefreshIn(BaseModel):
    integration_id: int


class MetaPagesRefreshOut(BaseModel):
    success: bool
    code: Optional[str] = None
    message: Optional[str] = None
    facebook_pages_count: int = 0
    instagram_accounts_count: int = 0
    direct_pages_count: int = 0
    business_pages_count: int = 0
    total_pages_count: int = 0
    business_management_scope_present: bool = False
    business_discovery_status: str = "skipped_missing_scope"
    duration_ms: float = 0.0


class MetaPagesSyncOut(BaseModel):
    integration_id: int
    dataset_id: int
    dataset_file_id: int
    page_id: str
    page_name: str
    status: str
    timeframe: Optional[dict] = None


class MetaSyncAllTimeframeIn(BaseModel):
    preset: str = "last_28_days"
    since: Optional[str] = None
    until: Optional[str] = None


class MetaSyncAllIn(BaseModel):
    integration_id: Optional[int] = None
    workspace_id: Optional[int] = None
    facebook_page_id: Optional[str] = None
    instagram_business_account_id: Optional[str] = None
    timeframe: MetaSyncAllTimeframeIn = Field(default_factory=MetaSyncAllTimeframeIn)


class MetaSyncSourceResultOut(BaseModel):
    success: bool
    dataset_id: Optional[int] = None
    dataset_file_id: Optional[int] = None
    message: str
    error_code: Optional[str] = None
    error: Optional[str] = None
    timeframe: Optional[dict] = None


class MetaSyncAllResultsOut(BaseModel):
    facebook_pages: Optional[MetaSyncSourceResultOut] = None
    instagram_business: Optional[MetaSyncSourceResultOut] = None


class MetaSyncAllOut(BaseModel):
    success: bool
    results: MetaSyncAllResultsOut


class InstagramBusinessSyncIn(BaseModel):
    integration_id: int
    instagram_account_id: str
    workspace_id: Optional[int] = None
    timeframe: str = "last_28_days"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class InstagramBusinessLoginSyncIn(BaseModel):
    workspace_id: Optional[int] = None
    integration_id: Optional[int] = None
    instagram_account_id: str
    timeframe: str = "last_30d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    force_live: bool = True


class InstagramBusinessSyncOut(BaseModel):
    integration_id: int
    dataset_id: int
    dataset_file_id: int
    source_type: str = "instagram_business"
    record_type: str = "instagram_account"
    account_id: str
    account_name: str
    status: str
    timeframe: Optional[dict] = None


class InstagramBusinessLoginSyncOut(BaseModel):
    integration_id: int
    dataset_id: int
    dataset_file_id: int
    provider: str = "instagram_business_login"
    source_type: str = "instagram_business"
    record_type: str = "instagram_account"
    account_id: str
    account_name: str
    status: str
    has_data: bool = False
    metrics_successful: list[str] = Field(default_factory=list)
    metrics_failed: list[str] = Field(default_factory=list)
    timeframe: Optional[dict] = None


class DatasetDetailOut(BaseModel):
    id: int
    workspace_id: int
    name: str
    description: Optional[str]
    data: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    file_id: Optional[int] = None
    file_key: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class AuditLogSchema(BaseSchema):
    id: int
    workspace_id: int
    user_id: Optional[int]
    action: str
    metadata_json: Optional[str]
    created_at: datetime


class DatasetUploadOut(BaseModel):
    dataset_id: int
    status: str


class ReportSourceCreate(BaseModel):
    provider: str
    source_type: str
    integration_id: int
    integration_account_id: Optional[int | str] = None
    dataset_id: Optional[int] = None
    position: int = 0
    label: Optional[str] = None
    config_json: Optional[dict] = None


class ReportSourceRead(BaseSchema):
    id: int
    report_id: int
    workspace_id: int
    provider: str
    source_type: str
    integration_id: int
    integration_account_id: Optional[int] = None
    dataset_id: Optional[int] = None
    position: int
    label: Optional[str] = None
    config_json: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class ReportIntegrationMetadataOut(BaseModel):
    integration_type: str = "legacy"
    integration_display_name: str = "Manual / Legacy report"
    source_name: Optional[str] = "Unknown source"
    source_handle: Optional[str] = None
    social_network: Optional[str] = None
    channel: Optional[str] = None


class MultiSourceReportCreateRequest(BaseModel):
    sources: list[ReportSourceCreate]
    timeframe: str = "last_28_days"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    ai_mode: Literal["standard", "agents"] = "standard"
    locale: str = "en"
    title: Optional[str] = None


class ReportCreateIn(BaseModel):
    dataset_id: int
    title: str
    locale: str = "en"
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    ai_mode: Literal["standard", "agents"] = "standard"


class MetaPagesReportCreateIn(BaseModel):
    dataset_id: int
    title: Optional[str] = None
    locale: str = "en"
    timeframe: str = "last_28_days"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    ai_mode: Literal["standard", "agents"] = "standard"


class InstagramBusinessReportCreateIn(BaseModel):
    dataset_id: Optional[int] = None
    workspace_id: Optional[int] = None
    integration_id: Optional[int] = None
    account_id: Optional[str] = None
    page_id: Optional[str] = None
    title: Optional[str] = None
    locale: str = "en"
    timeframe: str = "last_28_days"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    ai_mode: Literal["standard", "agents"] = "standard"


class MetaAdsReportCreateIn(BaseModel):
    workspace_id: Optional[int | str] = None
    integration_id: Optional[int | str] = None
    dataset_id: Optional[int | str] = None
    ad_account_id: Optional[str] = None
    account_id: Optional[str] = None
    title: Optional[str] = None
    locale: str = "en"
    timeframe: Optional[str] = "last_30d"
    start_date: Optional[str | date] = None
    end_date: Optional[str | date] = None
    requested_slides: Optional[int] = None
    slide_count: Optional[int] = None
    slides: Optional[int] = None
    template: Optional[str] = None
    ai_mode: Optional[str] = "standard"


class ReportOut(BaseSchema):
    id: int
    workspace_id: int
    dataset_id: int
    title: str
    status: Optional[str] = None
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    description: Optional[dict] = None
    timeframe: Optional[dict] = None
    report_sources: list[ReportSourceRead] = Field(default_factory=list)
    integration_metadata: ReportIntegrationMetadataOut = Field(default_factory=ReportIntegrationMetadataOut)
    version_id: Optional[int] = None
    version: Optional[int] = None
    locale: str = "en"
    branding: BrandingOut = BrandingOut()
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ReportListItemOut(BaseModel):
    id: int
    name: str
    status: str
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    integration_metadata: ReportIntegrationMetadataOut = Field(default_factory=ReportIntegrationMetadataOut)
    thumbnail_url: Optional[str] = None
    created_at: datetime


class ReportDeleteOut(BaseModel):
    success: bool = True


class MetaPagesReportCreateOut(BaseModel):
    report_id: int
    version_id: Optional[int] = None
    version: int
    dataset_id: int
    title: str
    locale: str = "en"
    status: str
    selected_integration_metadata: ReportIntegrationMetadataOut = Field(default_factory=ReportIntegrationMetadataOut)


class ReportBlockOut(BaseSchema):
    id: int
    report_version_id: int
    type: str
    order: int
    data_json: Optional[str]
    editable_fields_json: Optional[str]
    created_at: datetime
    updated_at: datetime


class ReportVersionOut(BaseSchema):
    id: int
    version_id: Optional[int] = None
    report_id: int
    version: int
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    report_sources: list[ReportSourceRead] = Field(default_factory=list)
    integration_metadata: ReportIntegrationMetadataOut = Field(default_factory=ReportIntegrationMetadataOut)
    description: Optional[dict] = None
    timeframe: Optional[dict] = None
    locale: str = "en"
    branding: BrandingOut = BrandingOut()
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    blocks: list[ReportBlockOut]


class ReportExportOut(BaseModel):
    status: str
    download_url: str
    file_name: str


class ReportShareCreateOut(BaseModel):
    status: str = "ok"
    report_id: int
    share_token: str
    share_url: str


class ReportShareRevokeOut(BaseModel):
    status: str = "ok"
    report_id: int
    revoked: bool = True


class PublicSharedReportOut(BaseModel):
    id: int
    workspace_id: int
    title: str
    integration_type: Optional[str] = None
    integration_label: Optional[str] = None
    source_name: Optional[str] = None
    channel: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    template: Optional[str] = None
    description: Optional[dict] = None
    timeframe: Optional[dict] = None
    locale: str = "en"
    branding: BrandingOut = BrandingOut()
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PublicSharedReportVersionOut(BaseModel):
    id: int
    report_id: int
    version: int
    created_at: datetime
    updated_at: datetime


class PublicReportOut(BaseModel):
    report: PublicSharedReportOut
    version: PublicSharedReportVersionOut
    blocks: list[ReportBlockOut] = Field(default_factory=list)
    is_public_share: bool = True


class ReportBlockUpdateIn(BaseModel):
    data: dict


class ReportFolderUpdateIn(BaseModel):
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None


class ReportFolderUpdateOut(BaseModel):
    report_id: int
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    updated: bool = True


class AccountSummaryOut(BaseModel):
    reports_created_count: int = 0
    reports_available_count: Optional[int] = None
    reports_remaining_this_month: Optional[int] = None
    reports_limit_this_month: Optional[int] = None
    integrations_connected_count: int = 0
    integrations_total_available: int = 0
    current_plan_name: str = "free"
    current_plan_code: Optional[str] = None
    is_free_plan: bool = False
    can_use_custom_branding: bool = False
    report_branding_mode: Literal["measurable", "custom"] = "measurable"
    account_display_name: Optional[str] = None
    account_display_name_effective: str = "Measurable Account"
