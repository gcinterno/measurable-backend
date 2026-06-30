from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    jwt_secret: str
    jwt_alg: str = "HS256"
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    aws_region: str
    s3_inputs_bucket: str
    s3_outputs_bucket: str
    aws_access_key_id: str | None = Field(default=None, validation_alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, validation_alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(default=None, validation_alias="AWS_SESSION_TOKEN")

    export_lambda_url: str
    export_api_key: str | None = None
    ses_from_email: str | None = Field(default=None, validation_alias="SES_FROM_EMAIL")
    ses_configuration_set_name: str | None = Field(
        default=None,
        validation_alias="SES_CONFIGURATION_SET_NAME",
    )
    api_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("API_BASE_URL", "BACKEND_BASE_URL", "NEXT_PUBLIC_API_URL"),
    )
    report_export_base_url: str | None = None
    frontend_base_url: str | None = Field(default=None, validation_alias="FRONTEND_BASE_URL")
    frontend_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FRONTEND_URL", "FRONTEND_BASE_URL"),
    )
    stripe_secret_key: str | None = Field(default=None, validation_alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = Field(default=None, validation_alias="STRIPE_WEBHOOK_SECRET")
    stripe_price_starter_monthly: str | None = Field(
        default=None,
        validation_alias="STRIPE_PRICE_STARTER_MONTHLY",
    )
    stripe_price_pro_monthly: str | None = Field(
        default=None,
        validation_alias="STRIPE_PRICE_PRO_MONTHLY",
    )
    stripe_price_advanced_monthly: str | None = Field(
        default=None,
        validation_alias="STRIPE_PRICE_ADVANCED_MONTHLY",
    )
    stripe_billing_portal_return_url: str | None = Field(
        default=None,
        validation_alias="STRIPE_BILLING_PORTAL_RETURN_URL",
    )
    cors_origins: str | None = Field(default=None, validation_alias="CORS_ORIGINS")
    report_export_path_template: str = "/reports/{report_id}/export/pdf-view"
    pdf_export_ready_selector: str = '[data-pdf-ready="true"]'
    pdf_export_timeout_ms: int = 15000
    pdf_export_viewport_width: int = 1160
    pdf_export_viewport_height: int = 670
    pdf_export_device_scale_factor: float = 1.0
    pdf_export_scale: float = 1.0
    pdf_export_margin_top: str = "0"
    pdf_export_margin_right: str = "0"
    pdf_export_margin_bottom: str = "0"
    pdf_export_margin_left: str = "0"
    chromium_executable_path: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"

    meta_app_id: str | None = Field(default=None, validation_alias="META_APP_ID")
    meta_app_secret: str | None = Field(default=None, validation_alias="META_APP_SECRET")
    meta_redirect_uri: str | None = Field(default=None, validation_alias="META_REDIRECT_URI")
    meta_ads_app_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_ADS_APP_ID", "META_APP_ID"),
    )
    meta_ads_app_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_ADS_APP_SECRET", "META_APP_SECRET"),
    )
    meta_ads_redirect_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_ADS_REDIRECT_URI", "META_REDIRECT_URI"),
    )
    meta_pages_app_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_PAGES_APP_ID", "META_APP_ID"),
    )
    meta_pages_app_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_PAGES_APP_SECRET", "META_APP_SECRET"),
    )
    meta_pages_redirect_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("META_PAGES_REDIRECT_URI", "META_REDIRECT_URI"),
    )
    meta_capi_pixel_id: str | None = Field(default=None, validation_alias="META_CAPI_PIXEL_ID")
    meta_capi_access_token: str | None = Field(default=None, validation_alias="META_CAPI_ACCESS_TOKEN")
    meta_capi_test_event_code: str | None = Field(default=None, validation_alias="META_CAPI_TEST_EVENT_CODE")
    meta_capi_api_version: str = Field(default="v25.0", validation_alias="META_CAPI_API_VERSION")
    meta_capi_enabled: bool = Field(default=False, validation_alias="META_CAPI_ENABLED")
    google_client_id: str | None = Field(default=None, validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(default=None, validation_alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str | None = Field(default=None, validation_alias="GOOGLE_REDIRECT_URI")
    meta_api_version: str = "v19.0"
    instagram_app_id: str | None = Field(default=None, validation_alias="INSTAGRAM_APP_ID")
    instagram_app_secret: str | None = Field(default=None, validation_alias="INSTAGRAM_APP_SECRET")
    instagram_redirect_uri: str | None = Field(default=None, validation_alias="INSTAGRAM_REDIRECT_URI")
    instagram_oauth_authorize_url: str = Field(
        default="https://api.instagram.com/oauth/authorize",
        validation_alias="INSTAGRAM_OAUTH_AUTHORIZE_URL",
    )
    instagram_oauth_access_token_url: str = Field(
        default="https://api.instagram.com/oauth/access_token",
        validation_alias="INSTAGRAM_OAUTH_ACCESS_TOKEN_URL",
    )
    instagram_graph_api_base: str = Field(
        default="https://graph.instagram.com",
        validation_alias="INSTAGRAM_GRAPH_API_BASE",
    )
    tiktok_app_id: str | None = Field(default=None, validation_alias="TIKTOK_APP_ID")
    tiktok_secret: str | None = Field(default=None, validation_alias="TIKTOK_SECRET")
    tiktok_api_base: str = Field(
        default="https://business-api.tiktok.com/open_api/v1.3",
        validation_alias="TIKTOK_API_BASE",
    )
    tiktok_redirect_uri: str | None = Field(default=None, validation_alias="TIKTOK_REDIRECT_URI")
    tiktok_connect_success_redirect: str | None = Field(
        default=None,
        validation_alias="TIKTOK_CONNECT_SUCCESS_REDIRECT",
    )
    tiktok_connect_error_redirect: str | None = Field(
        default=None,
        validation_alias="TIKTOK_CONNECT_ERROR_REDIRECT",
    )
    integration_encryption_key: str | None = Field(
        default=None,
        validation_alias="INTEGRATION_ENCRYPTION_KEY",
    )
    shopify_api_key: str | None = Field(default=None, validation_alias="SHOPIFY_API_KEY")
    shopify_api_secret: str | None = Field(default=None, validation_alias="SHOPIFY_API_SECRET")
    shopify_redirect_uri: str | None = Field(default=None, validation_alias="SHOPIFY_REDIRECT_URI")
    shopify_scopes: str = Field(default="read_orders,read_products", validation_alias="SHOPIFY_SCOPES")
    shopify_api_version: str = Field(default="2025-10", validation_alias="SHOPIFY_API_VERSION")
    shopify_connect_success_redirect: str | None = Field(
        default=None,
        validation_alias="SHOPIFY_CONNECT_SUCCESS_REDIRECT",
    )
    shopify_connect_error_redirect: str | None = Field(
        default=None,
        validation_alias="SHOPIFY_CONNECT_ERROR_REDIRECT",
    )


settings = Settings()
