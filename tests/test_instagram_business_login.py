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


def test_instagram_business_login_disconnect_clears_token_and_cached_account(client):
    refs = _seed_connected_instagram_login()

    response = client.delete(
        "/integrations/instagram-business-login/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "instagram_business_login"
    assert payload["status"] == "disconnected"
    assert payload["integration_id"] == refs["integration_id"]
    assert payload["cleared_accounts"] == 1
    assert payload["cleared_integration_accounts"] == 2
    assert payload["cleared_tokens"] == 1
    assert payload["token_cleared"] is True

    status_response = client.get(
        "/integrations/instagram-business-login/status",
        headers=_auth_headers(int(refs["user_id"])),
        params={"workspace_id": refs["workspace_id"]},
    )
    assert status_response.status_code == 200
    assert status_response.json()["connected"] is False
    assert status_response.json()["status"] == "disconnected"
    assert status_response.json()["account_count"] == 0

    db = SessionLocal()
    try:
        integration = db.get(Integration, int(refs["integration_id"]))
        assert integration is not None
        assert integration.status == "disconnected"
        assert db.query(IntegrationAccount).filter(IntegrationAccount.integration_id == integration.id).count() == 0
        assert db.query(IntegrationToken).count() == 0
        assert db.query(MetaPage).filter(MetaPage.integration_id == integration.id).count() == 0
    finally:
        db.close()


def test_instagram_business_login_disconnect_is_idempotent_without_existing_integration(client):
    refs = _seed_user_workspace("ig-login-disconnect-empty@example.com")

    response = client.post(
        "/integrations/instagram-business-login/disconnect",
        headers=_auth_headers(refs["user_id"]),
        json={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "instagram_business_login"
    assert payload["status"] == "disconnected"
    assert payload["integration_id"] is None
    assert payload["cleared_accounts"] == 0
    assert payload["cleared_tokens"] == 0


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
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, *, params=None, headers=None, timeout=None):
        captured_urls.append(url)
        assert headers == {"Authorization": "Bearer ig-login-token"}
        if url.endswith("/me"):
            assert "followers_count" in params["fields"]
            assert "media_count" in params["fields"]
            return FakeResponse(
                200,
                {
                    "id": refs["instagram_account_id"],
                    "username": "iglogin",
                    "name": "IG Login Account",
                },
            )
        if url.endswith(f"/{refs['instagram_account_id']}/media"):
            return FakeResponse(200, {"data": []})
        assert url.endswith(f"/{refs['instagram_account_id']}/insights")
        assert params["metric"] in set(main_module.INSTAGRAM_BUSINESS_LOGIN_ACCOUNT_INSIGHT_METRICS)
        if params["metric"] == "impressions":
            return FakeResponse(
                400,
                {
                    "error": {
                        "message": "Metric impressions is not valid for this provider path.",
                    }
                },
            )
        return FakeResponse(200, {"data": []})

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
    assert set(payload["metrics_successful"]) == set(main_module.INSTAGRAM_BUSINESS_LOGIN_ACCOUNT_INSIGHT_METRICS) - {
        "impressions"
    }
    assert payload["metrics_failed"] == ["impressions"]
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
        assert dataset.data["impressions"] is None
        assert dataset.data["views"] is None
        assert dataset.data["unavailable_metrics"]["impressions"]
        assert dataset.data["recent_posts"] == []
    finally:
        db.close()


def test_instagram_business_login_sync_persists_supported_metrics_profile_and_media(
    client,
    monkeypatch,
):
    refs = _seed_connected_instagram_login()
    account_id = str(refs["instagram_account_id"])

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    account_metric_values = {
        "reach": [100, 150],
        "views": [400, 500],
        "accounts_engaged": [10, 20],
        "total_interactions": [30, 40],
        "profile_views": [7, 8],
        "website_clicks": [1, 2],
        "follower_count": [1200, 1234],
        "likes": [11],
        "comments": [4],
        "shares": [3],
        "saves": [2],
        "replies": [1],
    }
    media_metric_values = {
        "reach": 1000,
        "views": 2000,
        "likes": 51,
        "comments": 9,
        "shares": 6,
        "saves": 5,
        "total_interactions": 71,
    }

    def metric_payload(metric_name: str, values: list[int]) -> dict:
        return {
            "data": [
                {
                    "name": metric_name,
                    "period": "day",
                    "values": [
                        {"value": value, "end_time": f"2026-01-{index + 2:02d}T07:00:00+0000"}
                        for index, value in enumerate(values)
                    ],
                }
            ]
        }

    def fake_get(url, *, params=None, headers=None, timeout=None):
        assert headers == {"Authorization": "Bearer ig-login-token"}
        if url.endswith("/me"):
            return FakeResponse(
                200,
                {
                    "id": account_id,
                    "username": "iglogin",
                    "name": "IG Login Account",
                    "profile_picture_url": "https://cdn.example.test/ig.jpg",
                    "followers_count": 1234,
                    "media_count": 12,
                },
            )
        if url.endswith(f"/{account_id}/media"):
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "ig-media-1",
                            "caption": "Fresh menu item",
                            "media_type": "IMAGE",
                            "permalink": "https://instagram.example.test/p/1",
                            "timestamp": "2026-01-15T12:00:00+0000",
                            "like_count": 50,
                            "comments_count": 8,
                        },
                        {
                            "id": "ig-media-old",
                            "caption": "Old menu item",
                            "media_type": "IMAGE",
                            "permalink": "https://instagram.example.test/p/old",
                            "timestamp": "2025-11-15T12:00:00+0000",
                            "like_count": 999,
                            "comments_count": 999,
                        },
                    ],
                    "paging": {},
                },
            )
        assert url.endswith("/insights")
        metric_name = params["metric"]
        target_id = url.rstrip("/").split("/")[-2]
        if target_id == account_id:
            if metric_name == "impressions":
                return FakeResponse(
                    400,
                    {
                        "error": {
                            "message": "Metric impressions is not valid for this provider path.",
                        }
                    },
                )
            return FakeResponse(200, metric_payload(metric_name, account_metric_values.get(metric_name, [])))
        if target_id == "ig-media-1":
            if metric_name == "replies":
                return FakeResponse(
                    400,
                    {
                        "error": {
                            "message": "Metric replies is unavailable for this media.",
                        }
                    },
                )
            value = media_metric_values.get(metric_name)
            values = [value] if value is not None else []
            return FakeResponse(200, metric_payload(metric_name, values))
        raise AssertionError(f"unexpected graph request: {url}")

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
            "timeframe": "custom",
            "start_date": "2026-01-01",
            "end_date": "2026-01-30",
            "force_live": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_data"] is True
    assert "reach" in payload["metrics_successful"]
    assert payload["metrics_failed"] == ["impressions"]

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, payload["dataset_id"])
        assert dataset is not None
        data = dataset.data
        assert data["reach"] == 250
        assert data["views"] == 900
        assert data["impressions"] is None
        assert data["engagement"] == 70
        assert data["total_interactions"] == 70
        assert data["accounts_engaged"] == 30
        assert data["content_interactions"] == 21
        assert data["profile_views"] == 15
        assert data["profile_visits"] == 15
        assert data["website_clicks"] == 3
        assert data["followers_count"] == 1234
        assert data["media_count"] == 12
        assert data["unavailable_metrics"]["impressions"]
        assert data["normalized_report_metrics"]["views_total"] == 900
        assert data["normalized_report_metrics"]["interactions_total"] == 70
        assert data["normalized_report_metrics"]["page_visits_total"] == 15
        assert data["normalized_report_metrics"]["followers_total"] == 1234
        assert data["normalized_report_metrics"]["followers_growth_total"] == 1234
        assert data["posts_analyzed_count"] == 1
        assert len(data["recent_posts"]) == 1
        post = data["recent_posts"][0]
        assert post["id"] == "ig-media-1"
        assert post["message"] == "Fresh menu item"
        assert post["media_type"] == "IMAGE"
        assert post["reach"] == 1000
        assert post["views"] == 2000
        assert post["likes"] == 51
        assert post["reactions"] == 51
        assert post["comments"] == 9
        assert post["shares"] == 6
        assert post["saves"] == 5
        assert post["replies"] is None
        assert len(data["top_content"]) == 1

        normalized = main_module._multi_source_normalize_source(
            {
                "source_type": "instagram_business",
                "label": "Instagram Account",
                "dataset_id": dataset.id,
            },
            dataset=dataset,
            locale="en",
        )
        assert len(normalized["content"]) == 1
        assert normalized["content"][0]["id"] == "ig-media-1"
        assert normalized["content"][0]["likes"] == 51
        assert normalized["content"][0]["views"] == 2000
        top_content = main_module._multi_source_top_content([normalized])
        assert top_content is not None
        assert top_content["id"] == "ig-media-1"
        assert top_content["_source_label"] == "Instagram Account"
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
                "reach": 250,
                "impressions": None,
                "views": 900,
                "profile_views": None,
                "unavailable_metrics": {
                    "impressions": "Metric impressions is not valid for this provider path.",
                    "profile_views": "empty_response",
                },
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
