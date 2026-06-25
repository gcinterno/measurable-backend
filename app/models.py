from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    logo_url: Mapped[Optional[str]] = mapped_column(String(2048))
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="email")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    google_sub: Mapped[Optional[str]] = mapped_column(String(255))
    facebook_sub: Mapped[Optional[str]] = mapped_column(String(255))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_type: Mapped[Optional[str]] = mapped_column(String(50))
    goals: Mapped[Optional[list[str]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)
    platforms: Mapped[Optional[list[str]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="user")
    email_verification_codes: Mapped[list[EmailVerificationCode]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    attribution: Mapped[Optional[UserAttribution]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    referral_conversions: Mapped[list[ReferralConversion]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AccountDeletionFeedback(Base):
    __tablename__ = "account_deletion_feedback"
    __table_args__ = (
        Index("ix_account_deletion_feedback_user_id", "user_id"),
        Index("ix_account_deletion_feedback_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(50))
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserSuggestion(Base):
    __tablename__ = "user_suggestions"
    __table_args__ = (
        Index("ix_user_suggestions_user_id", "user_id"),
        Index("ix_user_suggestions_workspace_id", "workspace_id"),
        Index("ix_user_suggestions_created_at", "created_at"),
        Index("ix_user_suggestions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="new")
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="floating_suggestion_button")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WishlistLead(Base):
    __tablename__ = "wishlist_leads"
    __table_args__ = (
        Index("ix_wishlist_leads_user_id", "user_id"),
        Index("ix_wishlist_leads_workspace_id", "workspace_id"),
        Index("ix_wishlist_leads_email", "email"),
        Index("ix_wishlist_leads_source", "source"),
        Index("ix_wishlist_leads_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="upgrade_page")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_display_name: Mapped[Optional[str]] = mapped_column(String(255))
    logo_url: Mapped[Optional[str]] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    datasets: Mapped[list[Dataset]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    integrations: Mapped[list[Integration]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    exports: Mapped[list[Export]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    schedules: Mapped[list[Schedule]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        Index("ix_workspace_members_workspace_id", "workspace_id"),
        Index("ix_workspace_members_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")


class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"
    __table_args__ = (
        Index("ix_email_verification_codes_user_id", "user_id"),
        Index("ix_email_verification_codes_purpose", "purpose"),
        Index("ix_email_verification_codes_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, default="email_verification")
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="email_verification_codes")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_workspace_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    plan: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_status: Mapped[Optional[str]] = mapped_column(String(50))
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255))
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255))
    stripe_price_id: Mapped[Optional[str]] = mapped_column(String(255))
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[Optional[bool]] = mapped_column(Boolean)
    reports_limit_monthly: Mapped[Optional[int]] = mapped_column(Integer)
    reports_limit_is_temporary: Mapped[Optional[bool]] = mapped_column(Boolean)
    slides_per_report_limit: Mapped[Optional[int]] = mapped_column(Integer)
    platform_report_type: Mapped[Optional[str]] = mapped_column(String(100))
    ai_chat_with_data: Mapped[Optional[bool]] = mapped_column(Boolean)
    storage_limit_gb: Mapped[Optional[int]] = mapped_column(Integer)
    export_pdf: Mapped[Optional[bool]] = mapped_column(Boolean)
    export_pptx: Mapped[Optional[bool]] = mapped_column(Boolean)
    brand_personalization: Mapped[Optional[bool]] = mapped_column(Boolean)
    measurable_watermark: Mapped[Optional[bool]] = mapped_column(Boolean)
    scheduled_reports_limit: Mapped[Optional[int]] = mapped_column(Integer)
    trial_new_features: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="subscriptions")


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (Index("ix_datasets_workspace_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="datasets")
    files: Mapped[list[DatasetFile]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetFile(Base):
    __tablename__ = "dataset_files"
    __table_args__ = (
        Index("ix_dataset_files_dataset_id", "dataset_id"),
        Index("ix_dataset_files_workspace_id", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    dataset: Mapped[Dataset] = relationship(back_populates="files")
    workspace: Mapped[Workspace] = relationship()


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (Index("ix_integrations_workspace_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="disconnected")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="integrations")
    accounts: Mapped[list[IntegrationAccount]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )
    meta_pages: Mapped[list[MetaPage]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )
    meta_ad_accounts: Mapped[list[MetaAdAccount]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )
    meta_ads_insights_daily: Mapped[list[MetaAdsInsightDaily]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )
    shopify_connections: Mapped[list[ShopifyConnection]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )


class IntegrationAccount(Base):
    __tablename__ = "integration_accounts"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_account_id", name="uq_integration_account"),
        Index("ix_integration_accounts_integration_id", "integration_id"),
        Index("ix_integration_accounts_workspace_id", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    integration: Mapped[Integration] = relationship(back_populates="accounts")
    workspace: Mapped[Workspace] = relationship()
    tokens: Mapped[list[IntegrationToken]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class IntegrationToken(Base):
    __tablename__ = "integration_tokens"
    __table_args__ = (
        Index("ix_integration_tokens_account_id", "account_id"),
        Index("ix_integration_tokens_workspace_id", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("integration_accounts.id"), nullable=False
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    token_type: Mapped[str] = mapped_column(String(50), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[IntegrationAccount] = relationship(back_populates="tokens")
    workspace: Mapped[Workspace] = relationship()


class MetaPage(Base):
    __tablename__ = "meta_pages"
    __table_args__ = (
        UniqueConstraint("integration_id", "record_type", "page_id", name="uq_meta_pages_integration_record"),
        Index("ix_meta_pages_integration_id", "integration_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    record_type: Mapped[str] = mapped_column(String(50), nullable=False, default="facebook_page")
    page_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_page_id: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instagram_username: Mapped[Optional[str]] = mapped_column(String(255))
    profile_picture_url: Mapped[Optional[str]] = mapped_column(String(2048))
    page_access_token: Mapped[Optional[str]] = mapped_column(Text)
    tasks: Mapped[Optional[list]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    perms: Mapped[Optional[list]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    category: Mapped[Optional[str]] = mapped_column(String(255))
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    integration: Mapped[Integration] = relationship(back_populates="meta_pages")


class MetaAdAccount(Base):
    __tablename__ = "meta_ad_accounts"
    __table_args__ = (
        UniqueConstraint("integration_id", "account_id", name="uq_meta_ad_accounts_integration_account"),
        Index("ix_meta_ad_accounts_integration_id", "integration_id"),
        Index("ix_meta_ad_accounts_workspace_id", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    timezone_name: Mapped[Optional[str]] = mapped_column(String(100))
    account_status: Mapped[Optional[str]] = mapped_column(String(50))
    business_id: Mapped[Optional[str]] = mapped_column(String(255))
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    integration: Mapped[Integration] = relationship(back_populates="meta_ad_accounts")
    workspace: Mapped[Workspace] = relationship()
    insights_daily: Mapped[list[MetaAdsInsightDaily]] = relationship(
        back_populates="meta_ad_account", cascade="all, delete-orphan"
    )


class MetaAdsInsightDaily(Base):
    __tablename__ = "meta_ads_insights_daily"
    __table_args__ = (
        UniqueConstraint(
            "meta_ad_account_id",
            "date_start",
            "campaign_id",
            "adset_id",
            "ad_id",
            name="uq_meta_ads_insights_daily_grain",
        ),
        Index("ix_meta_ads_insights_daily_integration_id", "integration_id"),
        Index("ix_meta_ads_insights_daily_workspace_id", "workspace_id"),
        Index("ix_meta_ads_insights_daily_meta_ad_account_id", "meta_ad_account_id"),
        Index("ix_meta_ads_insights_daily_date_start", "date_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    meta_ad_account_id: Mapped[int] = mapped_column(ForeignKey("meta_ad_accounts.id"), nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_stop: Mapped[date] = mapped_column(Date, nullable=False)
    spend: Mapped[Optional[float]] = mapped_column(Numeric(14, 4))
    impressions: Mapped[Optional[int]] = mapped_column(Integer)
    reach: Mapped[Optional[int]] = mapped_column(Integer)
    clicks: Mapped[Optional[int]] = mapped_column(Integer)
    inline_link_clicks: Mapped[Optional[int]] = mapped_column(Integer)
    ctr: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    cpc: Mapped[Optional[float]] = mapped_column(Numeric(14, 4))
    cpm: Mapped[Optional[float]] = mapped_column(Numeric(14, 4))
    frequency: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    actions: Mapped[Optional[list[dict]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    cost_per_action_type: Mapped[Optional[list[dict]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    campaign_id: Mapped[Optional[str]] = mapped_column(String(255))
    campaign_name: Mapped[Optional[str]] = mapped_column(String(255))
    adset_id: Mapped[Optional[str]] = mapped_column(String(255))
    adset_name: Mapped[Optional[str]] = mapped_column(String(255))
    ad_id: Mapped[Optional[str]] = mapped_column(String(255))
    ad_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    integration: Mapped[Integration] = relationship(back_populates="meta_ads_insights_daily")
    workspace: Mapped[Workspace] = relationship()
    meta_ad_account: Mapped[MetaAdAccount] = relationship(back_populates="insights_daily")


class ShopifyConnection(Base):
    __tablename__ = "shopify_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "shop_domain", name="uq_shopify_connections_user_shop_domain"),
        UniqueConstraint("integration_id", name="uq_shopify_connections_integration_id"),
        Index("ix_shopify_connections_user_id", "user_id"),
        Index("ix_shopify_connections_workspace_id", "workspace_id"),
        Index("ix_shopify_connections_shop_domain", "shop_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_name: Mapped[Optional[str]] = mapped_column(String(255))
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    scopes: Mapped[Optional[list[str]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="disconnected")
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    integration: Mapped[Integration] = relationship(back_populates="shopify_connections")
    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()
    snapshots: Mapped[list[ShopifySnapshot]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class ShopifySnapshot(Base):
    __tablename__ = "shopify_snapshots"
    __table_args__ = (
        Index("ix_shopify_snapshots_user_id", "user_id"),
        Index("ix_shopify_snapshots_workspace_id", "workspace_id"),
        Index("ix_shopify_snapshots_connection_id", "connection_id"),
        Index("ix_shopify_snapshots_dataset_id", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[int] = mapped_column(ForeignKey("shopify_connections.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    timeframe: Mapped[Optional[dict]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    connection: Mapped[ShopifyConnection] = relationship(back_populates="snapshots")
    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()
    dataset: Mapped[Optional[Dataset]] = relationship()


class ShopifyOAuthState(Base):
    __tablename__ = "shopify_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_token", name="uq_shopify_oauth_states_state_token"),
        Index("ix_shopify_oauth_states_user_id", "user_id"),
        Index("ix_shopify_oauth_states_workspace_id", "workspace_id"),
        Index("ix_shopify_oauth_states_shop_domain", "shop_domain"),
        Index("ix_shopify_oauth_states_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    state_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, default="shopify_oauth")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReferralPartner(Base):
    __tablename__ = "referral_partners"
    __table_args__ = (
        Index("ix_referral_partners_code", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    type: Mapped[Optional[str]] = mapped_column(String(50))
    commission_type: Mapped[Optional[str]] = mapped_column(String(50))
    commission_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReferralClick(Base):
    __tablename__ = "referral_clicks"
    __table_args__ = (
        Index("ix_referral_clicks_referral_code", "referral_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(255))
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(255))
    utm_content: Mapped[Optional[str]] = mapped_column(String(255))
    landing_page: Mapped[Optional[str]] = mapped_column(String(2048))
    ip_hash: Mapped[Optional[str]] = mapped_column(String(255))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserAttribution(Base):
    __tablename__ = "user_attributions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_attributions_user_id"),
        Index("ix_user_attributions_first_referral_code", "first_referral_code"),
        Index("ix_user_attributions_last_referral_code", "last_referral_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    first_referral_code: Mapped[Optional[str]] = mapped_column(String(255))
    last_referral_code: Mapped[Optional[str]] = mapped_column(String(255))
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(255))
    utm_content: Mapped[Optional[str]] = mapped_column(String(255))
    first_touch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    signup_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="attribution")


class ReferralConversion(Base):
    __tablename__ = "referral_conversions"
    __table_args__ = (
        Index("ix_referral_conversions_referral_code", "referral_code"),
        Index("ix_referral_conversions_user_id", "user_id"),
        Index("ix_referral_conversions_conversion_type", "conversion_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    referral_code: Mapped[Optional[str]] = mapped_column(String(255))
    conversion_type: Mapped[str] = mapped_column(String(50), nullable=False)
    plan: Mapped[Optional[str]] = mapped_column(String(100))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    commission_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="referral_conversions")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_workspace_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id", "conversation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_workspace_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    folder_id: Mapped[Optional[str]] = mapped_column(String(255))
    folder_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="reports")
    versions: Mapped[list[ReportVersion]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    report_sources: Mapped[list[ReportSource]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )
    exports: Mapped[list[Export]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )
    schedules: Mapped[list[Schedule]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )
    shares: Mapped[list[ReportShare]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )


class ReportVersion(Base):
    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "version", name="uq_report_version"),
        Index("ix_report_versions_report_id", "report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    report: Mapped[Report] = relationship(back_populates="versions")
    blocks: Mapped[list[ReportBlock]] = relationship(
        back_populates="report_version", cascade="all, delete-orphan"
    )


class ReportSource(Base):
    __tablename__ = "report_sources"
    __table_args__ = (
        Index("ix_report_sources_report_id", "report_id"),
        Index("ix_report_sources_workspace_id", "workspace_id"),
        Index("ix_report_sources_integration_id", "integration_id"),
        Index("ix_report_sources_dataset_id", "dataset_id"),
        UniqueConstraint("report_id", "position", name="uq_report_sources_report_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    integration_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("integration_accounts.id"))
    dataset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("datasets.id"))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    config_json: Mapped[Optional[dict]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    report: Mapped[Report] = relationship(back_populates="report_sources")
    workspace: Mapped[Workspace] = relationship()
    integration: Mapped[Integration] = relationship()
    integration_account: Mapped[Optional[IntegrationAccount]] = relationship()
    dataset: Mapped[Optional[Dataset]] = relationship()


class ReportBlock(Base):
    __tablename__ = "report_blocks"
    __table_args__ = (Index("ix_report_blocks_report_version_id", "report_version_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_version_id: Mapped[int] = mapped_column(
        ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_json: Mapped[Optional[str]] = mapped_column(Text)
    editable_fields_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    report_version: Mapped[ReportVersion] = relationship(back_populates="blocks")


class ReportShare(Base):
    __tablename__ = "report_shares"
    __table_args__ = (
        Index("ix_report_shares_report_id", "report_id"),
        Index("ix_report_shares_workspace_id", "workspace_id"),
        Index("ix_report_shares_token", "token", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    report: Mapped[Report] = relationship(back_populates="shares")
    workspace: Mapped[Workspace] = relationship()
    created_by_user: Mapped[Optional[User]] = relationship()


class Export(Base):
    __tablename__ = "exports"
    __table_args__ = (
        Index("ix_exports_workspace_id", "workspace_id"),
        Index("ix_exports_report_id", "report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    report_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    output_s3_key: Mapped[Optional[str]] = mapped_column(String(1024))
    download_key: Mapped[Optional[str]] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="exports")
    report: Mapped[Optional[Report]] = relationship(back_populates="exports")


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_workspace_id", "workspace_id"),
        Index("ix_schedules_report_id", "report_id"),
        Index("ix_schedules_integration_id", "integration_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    report_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    integration_id: Mapped[Optional[int]] = mapped_column(ForeignKey("integrations.id"))
    freq: Mapped[str] = mapped_column(String(50), nullable=False, default="monthly")
    day_of_month: Mapped[Optional[int]] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="schedules")
    report: Mapped[Optional[Report]] = relationship(back_populates="schedules")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_workspace_id", "workspace_id"),
        Index("ix_jobs_schedule_id", "schedule_id"),
        Index("ix_jobs_export_id", "export_id"),
        Index("ix_jobs_type", "type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    schedule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"))
    export_id: Mapped[Optional[int]] = mapped_column(ForeignKey("exports.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="sync_integration")
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="jobs")
    schedule: Mapped[Optional[Schedule]] = relationship()
    export: Mapped[Optional[Export]] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_workspace_id", "workspace_id"),
        Index("ix_audit_logs_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="audit_logs")
    user: Mapped[Optional[User]] = relationship(back_populates="audit_logs")
