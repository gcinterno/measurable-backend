from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_tiktok_sync.db")
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
import app.main as main_module
from app.main import (
    _tiktok_selected_advertiser_external_id,
    _tiktok_token_account_external_id,
    app,
)
from app.models import Dataset, DatasetFile, Integration, IntegrationAccount, IntegrationToken, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


TIKTOK_SYNC_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
]


@pytest.fixture(autouse=True)
def sync_schema():
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_SYNC_TABLES)
    Base.metadata.create_all(bind=engine, tables=TIKTOK_SYNC_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_SYNC_TABLES)


@pytest.fixture()
def client():
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


def _seed_tiktok_sync_fixture() -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email="owner@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        integration = Integration(
            workspace_id=workspace.id,
            provider="tiktok_ads",
            name="TikTok Ads",
            status="connected",
        )
        db.add(integration)
        db.flush()

        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_tiktok_token_account_external_id(integration.id),
            display_name="TikTok token store",
        )
        advertiser_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id="adv_123",
            display_name="Advertiser 123",
        )
        selected_marker = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_tiktok_selected_advertiser_external_id("adv_123"),
            display_name="Advertiser 123",
        )
        db.add_all([token_account, advertiser_account, selected_marker])
        db.flush()

        db.add(
            IntegrationToken(
                account_id=token_account.id,
                workspace_id=workspace.id,
                token_type="access_token",
                access_token="tiktok-access-token",
                refresh_token="tiktok-refresh-token",
            )
        )
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
        }
    finally:
        db.close()


def test_tiktok_sync_creates_dataset_and_dataset_file(client, monkeypatch):
    refs = _seed_tiktok_sync_fixture()

    monkeypatch.setattr(
        main_module,
        "fetch_daily_advertiser_report",
        lambda *_args, **_kwargs: {
            "rows": [
                {
                    "dimensions": {"stat_time_day": "2026-06-01"},
                    "metrics": {
                        "impressions": "100",
                        "clicks": "10",
                        "spend": "25.5",
                        "cpc": "2.55",
                        "cpm": "255",
                        "ctr": "0.10",
                        "conversions": "2",
                        "reach": "70",
                        "likes": "5",
                        "comments": "2",
                        "shares": "1",
                    },
                },
                {
                    "dimensions": {"stat_time_day": "2026-06-02"},
                    "metrics": {
                        "impressions": "120",
                        "clicks": "9",
                        "spend": "30",
                        "cpc": "3.33",
                        "cpm": "250",
                        "ctr": "0.075",
                        "conversions": "1",
                        "reach": "80",
                        "likes": "4",
                        "comments": "1",
                        "shares": "0",
                    },
                },
            ],
            "metrics_requested": [
                "impressions",
                "clicks",
                "spend",
                "cpc",
                "cpm",
                "ctr",
                "conversions",
                "reach",
                "likes",
                "comments",
                "shares",
            ],
            "used_optional_fallback": False,
            "raw": {"code": 0},
        },
    )

    class _FakeS3:
        def put_object(self, **_kwargs):
            return {"ok": True}

    monkeypatch.setattr(main_module.boto3, "client", lambda *_args, **_kwargs: _FakeS3())

    response = client.post(
        "/integrations/tiktok/sync",
        headers=_auth_headers(refs["user_id"]),
        json={
            "integration_id": refs["integration_id"],
            "advertiser_id": "adv_123",
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "uploaded"
    assert payload["advertiser_id"] == "adv_123"
    assert payload["metrics_summary"]["impressions_total"] == 220.0
    assert payload["metrics_summary"]["spend_total"] == 55.5

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, payload["dataset_id"])
        assert dataset is not None
        assert isinstance(dataset.data, dict)
        assert dataset.data["integration_type"] == "tiktok_ads"
        assert dataset.data["normalized_report_metrics"]["engagement_total"] == 13.0

        dataset_file = db.get(DatasetFile, payload["dataset_file_id"])
        assert dataset_file is not None
        assert dataset_file.content_type == "text/csv"
    finally:
        db.close()
