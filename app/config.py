from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    jwt_secret: str
    jwt_alg: str = "HS256"

    aws_region: str
    s3_inputs_bucket: str
    s3_outputs_bucket: str

    export_lambda_url: str
    export_api_key: str | None = None
    report_export_base_url: str | None = None
    frontend_base_url: str | None = Field(default=None, validation_alias="FRONTEND_BASE_URL")
    cors_origins: str | None = Field(default=None, validation_alias="CORS_ORIGINS")
    report_export_path_template: str = "/reports/{report_id}/export/pdf-view"
    pdf_export_ready_selector: str = '[data-report-export-ready="true"]'
    pdf_export_timeout_ms: int = 30000
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
    meta_api_version: str = "v19.0"


settings = Settings()
