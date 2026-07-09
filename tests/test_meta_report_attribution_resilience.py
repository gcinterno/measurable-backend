from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_meta_report_attribution_resilience.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
from app.main import app
from app.models import (
    Dataset,
    DatasetFile,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaPage,
    ReferralConversion,
    Report,
    ReportBlock,
    ReportVersion,
    Subscription,
    User,
    UserAttribution,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password
from app.main import META_RECORD_TYPE_INSTAGRAM_ACCOUNT


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


REPORT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaPage.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
    Report.__table__,
    ReportVersion.__table__,
    ReportBlock.__table__,
]


@pytest.fixture(autouse=True)
def report_schema():
    Base.metadata.drop_all(bind=engine, tables=REPORT_TABLES)
    Base.metadata.create_all(bind=engine, tables=REPORT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=REPORT_TABLES)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("app.main.generate_meta_pages_ai_summary", lambda *_args, **_kwargs: "AI summary")
    monkeypatch.setattr("app.main.build_meta_pages_summary", lambda *_args, **_kwargs: {"headline": "Summary"})
    monkeypatch.setattr("app.main.build_meta_pages_recent_posts_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main.build_meta_pages_ai_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "app.main.build_meta_pages_reach_chart_data",
        lambda *_args, **_kwargs: {
            "label": "Last 28 days",
            "timeframe": {"since": "2026-04-01", "until": "2026-04-28"},
            "points": [],
            "source_metric": "reach",
        },
    )
    monkeypatch.setattr("app.main.build_meta_pages_reach_insight", lambda *_args, **_kwargs: "Reach insight")
    monkeypatch.setattr(
        "app.main._build_impressions_slide_payload",
        lambda *_args, **_kwargs: {
            "label": "Last 28 days",
            "timeframe": {"since": "2026-04-01", "until": "2026-04-28"},
            "impressions_daily": [],
            "impressions_daily_count": 0,
        },
    )
    monkeypatch.setattr("app.main._build_general_insights_slide_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "app.main.extract_meta_pages_report_inputs",
        lambda row: {
            "integration_type": row.get("integration_type"),
            "page_name": row.get("page_name") or row.get("account_name") or "Account",
            "followers": row.get("followers", 1200),
            "reach": row.get("reach", 5400),
            "engagement": row.get("engagement", 320),
            "impressions": row.get("impressions", 8700),
            "profile_visits": row.get("profile_visits", 91),
            "link_clicks": row.get("link_clicks", 24),
            "content_interactions": row.get("content_interactions", 180),
            "reach_daily": row.get("reach_daily", []),
            "engagement_daily": row.get("engagement_daily", []),
            "timeframe_label": "Last 28 days",
            "unavailable_metrics": [],
        },
    )
    monkeypatch.setattr(
        "app.main.build_blocks",
        lambda requested_slides, _context: [
            {
                "type": "metric",
                "order": index,
                "data_json": json.dumps({"title": f"Slide {index + 1}"}),
                "editable_fields_json": "[]",
            }
            for index in range(int(requested_slides))
        ],
    )
    monkeypatch.setattr(
        "app.main._meta_timeframe_range",
        lambda *_args, **_kwargs: {
            "timeframe_key": "last_28_days",
            "selected_timeframe": "last_28_days",
            "requested_since": "2026-04-01",
            "requested_until": "2026-04-28",
            "current_since": "2026-04-01",
            "current_until": "2026-04-28",
            "previous_since": "2026-03-04",
            "previous_until": "2026-03-31",
            "duration_days": 28,
        },
    )
    monkeypatch.setattr("app.main._generate_and_store_report_thumbnail", lambda **_kwargs: None)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth_headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _seed_report_dataset(*, integration_type: str) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=f"{integration_type}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="core", status="active"))

        dataset = Dataset(
            workspace_id=workspace.id,
            name=f"{integration_type} dataset",
            description="Test",
            data={
                "integration_type": integration_type,
                "page_name": "Test Account",
                "account_name": "Test Account",
                "account_id": "17841400000000000",
                "followers": 1200,
                "reach": 5400,
                "engagement": 320,
                "impressions": 8700,
                "profile_visits": 91,
                "link_clicks": 24,
                "content_interactions": 180,
                "timeframe": {
                    "preset": "last_28_days",
                    "since": "2026-04-01",
                    "until": "2026-04-28",
                    "label": "Last 28 days",
                },
                "normalized_report_metrics": {},
            },
        )
        db.add(dataset)
        db.flush()
        db.add(
            DatasetFile(
                dataset_id=dataset.id,
                workspace_id=workspace.id,
                s3_key=f"inputs/{integration_type}.csv",
                size_bytes=128,
                content_type="text/csv",
            )
        )
        db.commit()
        return {"user_id": user.id, "dataset_id": dataset.id}
    finally:
        db.close()


def _seed_instagram_report_context(
    *,
    integration_provider: str,
    include_dataset: bool = True,
) -> dict[str, int | str]:
    db = SessionLocal()
    try:
        user = User(
            email=f"instagram-{integration_provider}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name=f"Workspace {integration_provider}")
        db.add_all([user, workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="core", status="active"))

        suite_integration = Integration(
            workspace_id=workspace.id,
            provider="meta_business_suite",
            name="Meta Business Suite",
            status="connected",
        )
        instagram_integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="connected",
        )
        legacy_meta_integration = Integration(
            workspace_id=workspace.id,
            provider="meta",
            name="Meta Pages",
            status="connected",
        )
        db.add_all([suite_integration, instagram_integration, legacy_meta_integration])
        db.flush()

        suite_token_account = IntegrationAccount(
            integration_id=suite_integration.id,
            workspace_id=workspace.id,
            external_account_id=f"__meta_token__:{suite_integration.id}",
            display_name="Suite token",
        )
        db.add(suite_token_account)
        db.flush()
        db.add(
            IntegrationToken(
                account_id=suite_token_account.id,
                workspace_id=workspace.id,
                token_type="access_token",
                access_token="suite-token",
            )
        )

        selected_integration = {
            "meta_business_suite": suite_integration,
            "instagram_business": instagram_integration,
            "meta": legacy_meta_integration,
        }[integration_provider]
        asset_integration = legacy_meta_integration if integration_provider == "meta" else instagram_integration
        db.add(
            MetaPage(
                integration_id=asset_integration.id,
                user_id=user.id,
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                page_id="17841400000000000",
                parent_page_id="fb-linked-page-1",
                name="Suite Instagram Account",
                instagram_username="suiteig",
                business_name="Linked Facebook Page",
            )
        )

        dataset_id: int | None = None
        if include_dataset:
            dataset = Dataset(
                workspace_id=workspace.id,
                name="meta_instagram_17841400000000000_insights.csv",
                description="Instagram sync dataset",
                data={
                    "integration_type": "instagram_business",
                    "page_name": "Suite Instagram Account",
                    "account_name": "Suite Instagram Account",
                    "account_id": "17841400000000000",
                    "username": "suiteig",
                    "followers": 1200,
                    "reach": 5400,
                    "engagement": 320,
                    "impressions": 8700,
                    "profile_visits": 91,
                    "link_clicks": 24,
                    "content_interactions": 180,
                    "timeframe": {
                        "key": "custom",
                        "preset": None,
                        "since": "2026-04-01",
                        "until": "2026-04-28",
                        "label": "Custom (2026-04-01 to 2026-04-28)",
                    },
                    "normalized_report_metrics": {},
                },
            )
            db.add(dataset)
            db.flush()
            db.add(
                DatasetFile(
                    dataset_id=dataset.id,
                    workspace_id=workspace.id,
                    s3_key="inputs/instagram-business.csv",
                    size_bytes=128,
                    content_type="text/csv",
                )
            )
            dataset_id = dataset.id

        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "suite_integration_id": suite_integration.id,
            "instagram_integration_id": instagram_integration.id,
            "legacy_meta_integration_id": legacy_meta_integration.id,
            "selected_integration_id": selected_integration.id,
            "dataset_id": dataset_id or 0,
            "account_id": "17841400000000000",
            "username": "suiteig",
            "parent_page_id": "fb-linked-page-1",
        }
    finally:
        db.close()


def _drop_optional_referral_tables() -> None:
    UserAttribution.__table__.drop(bind=engine)
    ReferralConversion.__table__.drop(bind=engine)


def test_instagram_business_report_succeeds_when_attribution_tables_are_missing(client):
    refs = _seed_report_dataset(integration_type="instagram_business")
    _drop_optional_referral_tables()

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Instagram Report",
            "locale": "en",
            "requested_slides": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["dataset_id"] == refs["dataset_id"]
    assert payload["version"] == 1


@pytest.mark.parametrize(
    "integration_provider",
    ["meta_business_suite", "instagram_business", "meta"],
)
def test_instagram_business_report_resolves_suite_child_and_legacy_integration_ids(client, integration_provider):
    refs = _seed_instagram_report_context(integration_provider=integration_provider)

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "integration_id": refs["selected_integration_id"],
            "workspace_id": refs["workspace_id"],
            "account_id": refs["account_id"],
            "timeframe": "custom",
            "start_date": "2026-04-01",
            "end_date": "2026-04-28",
            "requested_slides": 5,
            "ai_mode": "standard",
            "locale": "en",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["dataset_id"] == refs["dataset_id"]
    assert payload["version"] == 1


def test_instagram_business_report_resolves_account_id_aliases_from_suite_assets(client):
    refs = _seed_instagram_report_context(integration_provider="meta_business_suite")

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "integration_id": refs["suite_integration_id"],
            "workspace_id": refs["workspace_id"],
            "account_id": refs["parent_page_id"],
            "timeframe": "custom",
            "start_date": "2026-04-01",
            "end_date": "2026-04-28",
            "requested_slides": 5,
            "ai_mode": "standard",
            "locale": "en",
        },
    )

    assert response.status_code == 200
    assert response.json()["dataset_id"] == refs["dataset_id"]


def test_instagram_business_report_returns_actionable_error_when_account_has_no_dataset(client):
    refs = _seed_instagram_report_context(integration_provider="meta_business_suite", include_dataset=False)

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "integration_id": refs["suite_integration_id"],
            "workspace_id": refs["workspace_id"],
            "account_id": refs["account_id"],
            "timeframe": "custom",
            "start_date": "2026-04-01",
            "end_date": "2026-04-28",
            "requested_slides": 5,
            "ai_mode": "standard",
            "locale": "en",
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "error"
    assert detail["code"] == "instagram_dataset_not_synced"
    assert "Sync data" in detail["message"]
    assert detail["debug"]["workspace_id"] == refs["workspace_id"]
    assert detail["debug"]["suite_integration_id"] == refs["suite_integration_id"]
    assert detail["debug"]["instagram_child_integration_id"] == refs["instagram_integration_id"]
    assert detail["debug"]["requested_integration_id"] == refs["suite_integration_id"]


def test_meta_pages_report_succeeds_when_attribution_tables_are_missing(client):
    refs = _seed_report_dataset(integration_type="facebook_pages")
    _drop_optional_referral_tables()

    response = client.post(
        "/reports/meta-pages",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Facebook Report",
            "locale": "en",
            "requested_slides": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["dataset_id"] == refs["dataset_id"]
    assert payload["version"] == 1
