from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserSchema(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RegisterIn(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None


class RegisterOut(BaseModel):
    user_id: int
    email: str
    workspace_id: int
    plan: str
    message: str


class LoginIn(BaseModel):
    email: str
    password: str


class ChatMessageIn(BaseModel):
    message: str
    conversation_id: Optional[int] = None


class ChatReplyOut(BaseModel):
    conversation_id: int
    reply: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class BrandingOut(BaseModel):
    logo_url: Optional[str] = None


class MeOut(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
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
    description: Optional[dict] = None
    timeframe: Optional[dict] = None
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
