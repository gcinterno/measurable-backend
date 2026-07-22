from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_instagram_business_login_test.db")
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
from app.integrations import instagram_business as instagram_business_module
import app.main as main_module
from app.main import META_RECORD_TYPE_INSTAGRAM_ACCOUNT, app
from app.models import (
    Dataset,
    DatasetFile,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaPage,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.schemas import MetaPagesReportCreateOut
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


INSTAGRAM_LOGIN_TABLES = [
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
]


@pytest.fixture(autouse=True)
def instagram_business_login_schema():
    Base.metadata.drop_all(bind=engine, tables=INSTAGRAM_LOGIN_TABLES)
    Base.metadata.create_all(bind=engine, tables=INSTAGRAM_LOGIN_TABLES)
    main_module._table_names.cache_clear()
    yield
    Base.metadata.drop_all(bind=engine, tables=INSTAGRAM_LOGIN_TABLES)
    main_module._table_names.cache_clear()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main_module.settings, "instagram_business_login_app_id", "ig-login-app-id")
    monkeypatch.setattr(main_module.settings, "instagram_business_login_app_secret", "ig-login-secret")
    monkeypatch.setattr(
        main_module.settings,
        "instagram_business_login_redirect_uri",
        "https://api.example.test/integrations/instagram-business-login/callback",
    )
    monkeypatch.setattr(main_module.settings, "api_base_url", "https://api.example.test")
    monkeypatch.setattr(main_module.settings, "instagram_graph_api_version", "v19.0")
    monkeypatch.setattr(main_module.settings, "instagram_graph_api_base", "https://graph.instagram.com")

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


def _seed_user_workspace(email: str = "ig-login@example.com") -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=email,
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
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _seed_connected_instagram_login() -> dict[str, int | str]:
    refs = _seed_user_workspace("ig-login-connected@example.com")
    db = SessionLocal()
    try:
        integration = Integration(
            workspace_id=int(refs["workspace_id"]),
            provider="instagram_business_login",
            name="Instagram Business Login",
            status="connected",
        )
        db.add(integration)
        db.flush()
        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=int(refs["workspace_id"]),
            external_account_id=f"instagram_business_login_token_{integration.id}",
            display_name="Instagram Business Login token store",
        )
        account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=int(refs["workspace_id"]),
            external_account_id="17841400000000000",
            display_name="IG Login Account",
        )
        db.add_all([token_account, account])
        db.flush()
        db.add(
            IntegrationToken(
                account_id=token_account.id,
                workspace_id=int(refs["workspace_id"]),
                token_type="access_token",
                access_token="ig-login-token",
            )
        )
        db.add(
            MetaPage(
                integration_id=integration.id,
                user_id=int(refs["user_id"]),
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                page_id="17841400000000000",
                parent_page_id=None,
                name="IG Login Account",
                instagram_username="iglogin",
                category="BUSINESS",
                business_name="IG Login Account",
                perms=[
                    "instagram_business_basic",
                    "instagram_business_manage_insights",
                ],
            )
        )
        db.commit()
        refs["integration_id"] = integration.id
        refs["instagram_account_id"] = "17841400000000000"
        return refs
    finally:
        db.close()


def test_instagram_business_login_connect_uses_instagram_scopes(client):
    refs = _seed_user_workspace()

    response = client.get(
        f"/integrations/instagram-business-login/connect?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "instagram_business_login"
    assert payload["scope"] == "instagram_business_basic,instagram_business_manage_insights"
    auth_url = payload["auth_url"]
    parsed = urlparse(auth_url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "api.instagram.com"
    assert query["scope"] == ["instagram_business_basic,instagram_business_manage_insights"]
    assert "instagram_basic" not in query["scope"][0]
    assert "instagram_manage_insights" not in query["scope"][0]
    assert query["redirect_uri"] == [
        "https://api.example.test/integrations/instagram-business-login/callback"
    ]


def test_instagram_business_login_callback_saves_standalone_provider_and_token(client, monkeypatch):
    refs = _seed_user_workspace("ig-login-callback@example.com")
    connect_response = client.get(
        f"/integrations/instagram-business-login/connect?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    state = parse_qs(urlparse(connect_response.json()["auth_url"]).query)["state"][0]

    monkeypatch.setattr(
        main_module,
        "exchange_instagram_business_login_code_for_token",
        lambda _code: {
            "access_token": "ig-login-access-token",
            "scope": "instagram_business_basic,instagram_business_manage_insights",
            "_http_status_code": 200,
        },
    )
    monkeypatch.setattr(
        main_module,
        "fetch_instagram_business_login_profile",
        lambda _token: {
            "id": "17841400000000000",
            "username": "iglogin",
            "account_type": "BUSINESS",
            "name": "IG Login Account",
            "_http_status_code": 200,
        },
    )

    response = client.get(
        "/integrations/instagram-business-login/callback",
        params={"code": "ig-code", "state": state},
    )

    assert response.status_code == 200
    assert '"provider": "instagram_business_login"' in response.text
    db = SessionLocal()
    try:
        integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "instagram_business_login")
            .one()
        )
        assert integration.status == "connected"
        token_account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id == f"instagram_business_login_token_{integration.id}",
            )
            .one()
        )
        assert token_account.display_name == "Instagram Business Login token store"
        assert db.query(IntegrationToken).filter(IntegrationToken.account_id == token_account.id).count() == 1
        account = (
            db.query(MetaPage)
            .filter(MetaPage.integration_id == integration.id, MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT)
            .one()
        )
        assert account.page_id == "17841400000000000"
        assert account.parent_page_id is None
        assert account.page_access_token is None
        assert account.perms == [
            "instagram_business_basic",
            "instagram_business_manage_insights",
        ]
    finally:
        db.close()


def test_instagram_business_login_sync_calls_graph_instagram_and_accepts_empty_data(
    client,
    monkeypatch,
    caplog,
):
    refs = _seed_connected_instagram_login()
    caplog.set_level("INFO")
    captured_urls: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": []}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        captured_urls.append(url)
        assert headers == {"Authorization": "Bearer ig-login-token"}
        assert params["metric"] in {"reach", "impressions", "profile_views"}
        return FakeResponse()

    class FakeS3:
        def put_object(self, **_kwargs):
            return {}

    monkeypatch.setattr(instagram_business_module.requests, "get", fake_get)
    monkeypatch.setattr(main_module, "_enforce_workspace_storage_for_upload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.boto3, "client", lambda *_args, **_kwargs: FakeS3())

    response = client.post(
        "/integrations/instagram-business-login/sync",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": refs["integration_id"],
            "instagram_account_id": refs["instagram_account_id"],
            "timeframe": "last_30d",
            "force_live": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "instagram_business_login"
    assert payload["source_type"] == "instagram_business"
    assert payload["has_data"] is False
    assert set(payload["metrics_successful"]) == {"reach", "impressions", "profile_views"}
    assert payload["metrics_failed"] == []
    assert captured_urls
    assert all("graph.instagram.com" in url for url in captured_urls)
    assert all("graph.facebook.com" not in url for url in captured_urls)
    assert "INSTAGRAM_BUSINESS_LOGIN_INSIGHTS_REQUEST" in caplog.text
    assert "INSTAGRAM_BUSINESS_LOGIN_INSIGHTS_RESPONSE" in caplog.text
    assert "instagram_business_manage_insights" in caplog.text

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, payload["dataset_id"])
        assert dataset is not None
        assert dataset.data["integration_type"] == "instagram_business"
        assert dataset.data["provider"] == "instagram_business_login"
        assert dataset.data["source"] == "instagram_business_login"
        assert dataset.data["auth_type"] == "instagram_login"
        assert dataset.data["graph_host"] == "graph.instagram.com"
        assert dataset.data["permissions_used"] == [
            "instagram_business_basic",
            "instagram_business_manage_insights",
        ]
        assert dataset.data["has_data"] is False
    finally:
        db.close()


def test_instagram_business_report_accepts_dataset_from_instagram_business_login(client, monkeypatch):
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        dataset = Dataset(
            workspace_id=int(refs["workspace_id"]),
            name="instagram_business_login_17841400000000000_insights.csv",
            description="Instagram Business Login insights",
            data={
                "integration_type": "instagram_business",
                "provider": "instagram_business_login",
                "source": "instagram_business_login",
                "auth_type": "instagram_login",
                "graph_host": "graph.instagram.com",
                "account_id": refs["instagram_account_id"],
                "instagram_account_id": refs["instagram_account_id"],
                "ig_user_id": refs["instagram_account_id"],
                "account_name": "IG Login Account",
                "page_name": "IG Login Account",
                "reach": 0,
                "impressions": 0,
                "profile_views": 0,
                "timeframe": {"preset": "last_30_days", "since": "2026-01-01", "until": "2026-01-30"},
                "normalized_report_metrics": {},
            },
        )
        db.add(dataset)
        db.flush()
        db.add(
            DatasetFile(
                dataset_id=dataset.id,
                workspace_id=int(refs["workspace_id"]),
                s3_key="inputs/instagram_business_login.csv",
                size_bytes=128,
                content_type="text/csv",
            )
        )
        db.commit()
        dataset_id = dataset.id
    finally:
        db.close()

    def fake_create_meta_dataset_report(*, dataset, payload, **_kwargs):
        return MetaPagesReportCreateOut(
            report_id=999,
            version_id=1000,
            version=1,
            dataset_id=dataset.id,
            title=payload.title or "Instagram Business report",
            locale=payload.locale,
            status="ready",
        )

    monkeypatch.setattr(main_module, "_create_meta_dataset_report", fake_create_meta_dataset_report)

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "dataset_id": dataset_id,
            "title": "Instagram Business Login Report",
            "locale": "en",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_id"] == dataset_id
    assert payload["status"] == "ready"

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
            "timeframe": "last_30d",
            "title": "Instagram Business Login Report",
            "locale": "en",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_id"] == dataset_id
    assert payload["status"] == "ready"
