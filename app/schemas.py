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
    last_report_created_at: Optional[datetime] = None
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
    has_custom_branding: bool = False


class MeOut(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
    email_verified: bool
    auth_provider: str
    is_admin: bool = False
    last_login_at: Optional[datetime] = None
    logo_url: Optional[str] = None
    branding: BrandingOut = BrandingOut()
    created_at: datetime
    updated_at: datetime


class MeUpdateIn(BaseModel):
    full_name: Optional[str] = None
    logo_url: Optional[str] = None


class WorkspaceSchema(BaseSchema):
    id: int
    name: str
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
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    created_at: datetime
    updated_at: datetime


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


class WorkspaceUpdateIn(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None


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
    logo_url: Optional[str] = None
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


class MetaSelectAccountManualIn(BaseModel):
    integration_id: int
    ad_account_id: str


class MetaSetTokenManualIn(BaseModel):
    integration_id: int
    access_token: str


class MetaSelectPageIn(BaseModel):
    integration_id: int
    page_id: str


class MetaPageOut(BaseModel):
    id: str
    account_id: Optional[str] = None
    page_id: Optional[str] = None
    type: str = "facebook_page"
    parent_page_id: Optional[str] = None
    facebook_page_id: Optional[str] = None
    facebook_page_name: Optional[str] = None
    username: Optional[str] = None
    name: str
    category: Optional[str] = None
    instagram_username: Optional[str] = None
    profile_picture_url: Optional[str] = None
    fan_count: Optional[int] = None
    followers_count: Optional[int] = None
    source: Optional[str] = None
    business_name: Optional[str] = None


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


class ReportOut(BaseSchema):
    id: int
    workspace_id: int
    dataset_id: int
    title: str
    status: Optional[str] = None
    description: Optional[dict] = None
    timeframe: Optional[dict] = None
    report_sources: list[ReportSourceRead] = Field(default_factory=list)
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
    report_sources: list[ReportSourceRead] = Field(default_factory=list)
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


class ReportBlockUpdateIn(BaseModel):
    data: dict
