from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
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
    Report,
    ReportSource,
    ReportVersion,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.schemas import InstagramBusinessReportCreateIn, MetaPagesReportCreateOut
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
    Report.__table__,
    Base.metadata.tables["report_generations"],
    Base.metadata.tables["exports"],
    ReportSource.__table__,
    ReportVersion.__table__,
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
    monkeypatch.setattr(main_module.settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(main_module.settings, "frontend_base_url", "https://app.example.test")
    monkeypatch.setattr(main_module.settings, "instagram_graph_api_version", "v19.0")
    monkeypatch.setattr(main_module.settings, "instagram_graph_api_base", "https://graph.instagram.com")
    monkeypatch.setattr(
        main_module,
        "_revoke_instagram_business_login_access_token",
        lambda access_token: "success" if access_token else "skipped",
    )

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


def test_instagram_business_login_normalizes_reach_values_response():
    result = main_module._normalize_instagram_business_login_insight_payload(
        {
            "_instagram_http_status_code": 200,
            "data": [
                {
                    "name": "reach",
                    "period": "day",
                    "values": [
                        {"value": 10, "end_time": "2026-01-02T07:00:00+0000"},
                        {"value": 15, "end_time": "2026-01-03T07:00:00+0000"},
                    ],
                }
            ],
        },
        metric_name="reach",
    )

    assert result["availability"] == "available"
    assert result["response_shape"] == "values"
    assert result["value"] == 25
    assert result["latest_value"] == 15
    assert result["series"] == [
        {"date": "2026-01-02T07:00:00+0000", "value": 10},
        {"date": "2026-01-03T07:00:00+0000", "value": 15},
    ]


def test_instagram_business_login_normalizes_views_total_value_response():
    result = main_module._normalize_instagram_business_login_insight_payload(
        {
            "_instagram_http_status_code": 200,
            "data": [
                {
                    "name": "views",
                    "period": "day",
                    "total_value": {"value": 12345},
                }
            ],
        },
        metric_name="views",
    )

    assert result["availability"] == "available"
    assert result["response_shape"] == "total_value"
    assert result["value"] == 12345
    assert result["series"] == []
    assert result["unavailable_reason"] is None


def test_instagram_business_login_normalizes_total_interactions_total_value_response():
    result = main_module._normalize_instagram_business_login_insight_payload(
        {
            "_instagram_http_status_code": 200,
            "data": [
                {
                    "name": "total_interactions",
                    "period": "day",
                    "total_value": {"value": 678},
                }
            ],
        },
        metric_name="total_interactions",
    )

    assert result["availability"] == "available"
    assert result["response_shape"] == "total_value"
    assert result["value"] == 678


def test_instagram_business_login_empty_data_is_not_returned_by_meta():
    result = main_module._normalize_instagram_business_login_insight_payload(
        {
            "_instagram_http_status_code": 200,
            "data": [],
        },
        metric_name="views",
    )

    assert result["availability"] == "unavailable"
    assert result["response_shape"] == "not_returned_by_meta"
    assert result["value"] is None
    assert result["unavailable_reason"] == "not_returned_by_meta"


def test_instagram_business_login_parameter_incompatibility_is_classified():
    result = main_module._normalize_instagram_business_login_insight_payload(
        {
            "_instagram_http_status_code": 400,
            "_instagram_raw_body": '{"error":{"message":"(#100) The following metrics (views) should be specified with parameter metric_type=total_value"}}',
            "error": {
                "message": "(#100) The following metrics (views) should be specified with parameter metric_type=total_value",
            },
        },
        metric_name="views",
    )

    assert result["availability"] == "unavailable"
    assert result["response_shape"] == "parameter_incompatible"
    assert "metric_type=total_value" in result["unavailable_reason"]


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


def test_instagram_business_login_oauth_exchanges_short_lived_token_before_persistence(
    client,
    monkeypatch,
):
    calls: list[tuple[str, str, dict]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, *, data, timeout):
        calls.append(("POST", url, data))
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "one-time-code"
        return FakeResponse(
            200,
            {
                "access_token": "one-hour-token",
                "scope": "instagram_business_basic,instagram_business_manage_insights",
                "user_id": "17841400000000000",
            },
        )

    def fake_get(url, *, params, timeout):
        calls.append(("GET", url, params))
        assert url == "https://graph.instagram.com/access_token"
        assert params == {
            "grant_type": "ig_exchange_token",
            "client_secret": "ig-login-secret",
            "access_token": "one-hour-token",
        }
        return FakeResponse(
            200,
            {
                "access_token": "sixty-day-token",
                "token_type": "bearer",
                "expires_in": 5_184_000,
            },
        )

    monkeypatch.setattr(instagram_business_module.requests, "post", fake_post)
    monkeypatch.setattr(instagram_business_module.requests, "get", fake_get)

    payload = instagram_business_module.exchange_instagram_business_login_code_for_token("one-time-code")

    assert [method for method, _url, _payload in calls] == ["POST", "GET"]
    assert payload["access_token"] == "sixty-day-token"
    assert payload["expires_in"] == 5_184_000
    assert payload["_token_lifetime"] == "long_lived"
    assert payload["scope"] == "instagram_business_basic,instagram_business_manage_insights"
    assert payload["user_id"] == "17841400000000000"
    assert "one-hour-token" not in str(payload["_raw_body"])
    assert "sixty-day-token" not in str(payload["_raw_body"])


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
    assert payload["cleared_integration_accounts"] == 0
    assert payload["cleared_tokens"] == 1
    assert payload["token_cleared"] is True
    assert payload["remote_revoke_status"] == "success"

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
        assert db.query(IntegrationAccount).filter(IntegrationAccount.integration_id == integration.id).count() == 2
        assert db.query(IntegrationToken).count() == 0
        assert db.query(MetaPage).filter(MetaPage.integration_id == integration.id).count() == 0
    finally:
        db.close()

    second_response = client.post(
        "/integrations/instagram-business-login/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"workspace_id": refs["workspace_id"], "integration_id": refs["integration_id"]},
    )

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["success"] is True
    assert second_payload["status"] == "disconnected"
    assert second_payload["cleared_accounts"] == 0
    assert second_payload["cleared_integration_accounts"] == 0
    assert second_payload["cleared_tokens"] == 0
    assert second_payload["token_cleared"] is False
    assert second_payload["remote_revoke_status"] == "skipped"

    reconnect_response = client.get(
        "/integrations/instagram-business-login/connect",
        headers=_auth_headers(int(refs["user_id"])),
        params={"workspace_id": refs["workspace_id"], "reconnect": True},
    )

    assert reconnect_response.status_code == 200
    assert reconnect_response.json()["integration_id"] == refs["integration_id"]


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
    assert payload["remote_revoke_status"] == "skipped"


def test_instagram_business_login_disconnect_preserves_historical_reports_and_datasets(client):
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == int(refs["integration_id"]),
                IntegrationAccount.external_account_id == refs["instagram_account_id"],
            )
            .one()
        )
        dataset = Dataset(
            workspace_id=int(refs["workspace_id"]),
            name="Historical Instagram dataset",
            description="Historical report data",
            data={
                "provider": "instagram_business_login",
                "integration_type": "instagram_business",
                "account_name": "IG Login Account",
                "reach": 123,
            },
        )
        db.add(dataset)
        db.flush()
        report = Report(
            workspace_id=int(refs["workspace_id"]),
            dataset_id=dataset.id,
            name="Historical Instagram report",
            description=json.dumps({"report_status": "complete"}),
        )
        db.add(report)
        db.flush()
        db.add(
            ReportSource(
                report_id=report.id,
                workspace_id=int(refs["workspace_id"]),
                provider="instagram_business_login",
                source_type="instagram_business",
                integration_id=int(refs["integration_id"]),
                integration_account_id=account.id,
                dataset_id=dataset.id,
                position=0,
                label="Instagram Account",
                config_json={"external_account_id": refs["instagram_account_id"]},
            )
        )
        db.add(ReportVersion(report_id=report.id, version=1))
        db.commit()
        historical_ids = {
            "account_id": account.id,
            "dataset_id": dataset.id,
            "report_id": report.id,
        }
    finally:
        db.close()

    response = client.delete(
        "/integrations/instagram-business-login/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        params={"workspace_id": refs["workspace_id"], "integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cleared_accounts"] == 1
    assert payload["cleared_integration_accounts"] == 0
    assert payload["cleared_tokens"] == 1

    db = SessionLocal()
    try:
        assert db.get(Integration, int(refs["integration_id"])) is not None
        assert db.get(IntegrationAccount, historical_ids["account_id"]) is not None
        assert db.get(Dataset, historical_ids["dataset_id"]) is not None
        assert db.get(Report, historical_ids["report_id"]) is not None
        report_source = (
            db.query(ReportSource)
            .filter(ReportSource.report_id == historical_ids["report_id"])
            .one()
        )
        assert report_source.integration_id == int(refs["integration_id"])
        assert report_source.integration_account_id == historical_ids["account_id"]
        assert db.query(IntegrationToken).count() == 0
        assert db.query(MetaPage).filter(MetaPage.integration_id == int(refs["integration_id"])).count() == 0
    finally:
        db.close()

    report_response = client.get(
        f"/reports/{historical_ids['report_id']}",
        headers=_auth_headers(int(refs["user_id"])),
    )
    assert report_response.status_code == 200
    assert report_response.json()["id"] == historical_ids["report_id"]


def test_instagram_business_login_disconnect_tolerates_invalid_remote_revoke(client, monkeypatch):
    refs = _seed_connected_instagram_login()
    monkeypatch.setattr(
        main_module,
        "_revoke_instagram_business_login_access_token",
        lambda _access_token: "invalid_or_expired",
    )

    response = client.post(
        "/integrations/instagram-business-login/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"workspace_id": refs["workspace_id"], "integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "disconnected"
    assert payload["remote_revoke_status"] == "invalid_or_expired"
    status_response = client.get(
        "/integrations/instagram-business-login/status",
        headers=_auth_headers(int(refs["user_id"])),
        params={"workspace_id": refs["workspace_id"]},
    )
    assert status_response.status_code == 200
    assert status_response.json()["connected"] is False
    assert status_response.json()["status"] == "disconnected"


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
    assert "https://app.example.test/integrations/instagram-business/callback?" in response.text
    assert "status=connected" in response.text
    assert "source=instagram_business_login" in response.text
    assert "provider=instagram_business_login" in response.text
    assert "Instagram+Business+Login+connected+successfully." in response.text
    assert '"workspaceId": ' + str(refs["workspace_id"]) in response.text
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


def test_instagram_business_login_callback_cancellation_uses_existing_frontend_callback(client):
    response = client.get(
        "/integrations/instagram-business-login/callback",
        params={
            "error": "access_denied",
            "error_reason": "user_denied",
            "error_description": "The user cancelled authorization.",
        },
    )

    assert response.status_code == 200
    assert "https://app.example.test/integrations/instagram-business/callback?" in response.text
    assert "status=error" in response.text
    assert "source=instagram_business_login" in response.text
    assert "provider=instagram_business_login" in response.text
    assert "error=user_denied" in response.text
    assert "The+user+cancelled+authorization." in response.text
    assert "/integrations/instagram-business-login/callback?" not in response.text


def test_instagram_business_login_sync_rejects_completely_empty_provider_data(
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
        metric = params["metric"]
        assert metric in set(main_module.INSTAGRAM_BUSINESS_LOGIN_ACCOUNT_INSIGHT_METRICS)
        if metric == "reach":
            assert params.get("metric_type") is None
        else:
            assert params["metric_type"] == "total_value"
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
            "credential_integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
            "timeframe": "last_30d",
            "force_live": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "instagram_business_login_insights_failed"
    assert captured_urls
    assert all("graph.instagram.com" in url for url in captured_urls)
    assert all("graph.facebook.com" not in url for url in captured_urls)
    assert "INSTAGRAM_BUSINESS_LOGIN_INSIGHTS_REQUEST" in caplog.text
    assert "INSTAGRAM_BUSINESS_LOGIN_INSIGHTS_RESPONSE" in caplog.text
    assert "instagram_business_manage_insights" in caplog.text

    assert "INSTAGRAM_BUSINESS_LOGIN_SYNC_NO_USABLE_DATA" in caplog.text
    db = SessionLocal()
    try:
        assert db.query(Dataset).count() == 0
    finally:
        db.close()


def test_instagram_business_login_expired_provider_token_requires_reauthorization(
    client,
    monkeypatch,
    caplog,
):
    refs = _seed_connected_instagram_login()
    caplog.set_level("WARNING")
    calls: list[str] = []

    class FakeResponse:
        status_code = 401

        def json(self):
            return {
                "error": {
                    "message": "Error validating access token: Session has expired.",
                    "type": "OAuthException",
                    "code": 190,
                    "error_subcode": 0,
                    "fbtrace_id": "safe-trace-id",
                }
            }

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append(url)
        assert headers == {"Authorization": "Bearer ig-login-token"}
        assert url.endswith("/me")
        return FakeResponse()

    monkeypatch.setattr(instagram_business_module.requests, "get", fake_get)

    response = client.post(
        "/integrations/instagram-business-login/sync",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "credential_integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
            "timeframe": "last_30d",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "instagram_business_login_reauthorization_required",
        "message": "Instagram Business Login session expired. Reconnect the account and try again.",
    }
    assert len(calls) == 1
    assert "INSTAGRAM_BUSINESS_LOGIN_PROVIDER_ERROR" in caplog.text
    assert '"meta_error_code": 190' in caplog.text
    assert '"meta_error_subcode": 0' in caplog.text
    assert '"meta_error_type": "OAuthException"' in caplog.text
    assert '"stage": "profile_fetch"' in caplog.text
    assert "ig-login-token" not in caplog.text
    assert "190..." not in caplog.text

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration.status == "reauthorization_required"
        assert db.query(Dataset).count() == 0
    finally:
        db.close()

    status = client.get(
        "/integrations/instagram-business-login/status",
        headers=_auth_headers(int(refs["user_id"])),
        params={"integration_id": refs["integration_id"]},
    )
    assert status.status_code == 200
    assert status.json()["connected"] is False
    assert status.json()["status"] == "needs_permission"
    assert "Reconnect" in status.json()["message"]


def test_instagram_business_login_known_expired_token_fails_before_provider_call(
    client,
    monkeypatch,
):
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        token = db.query(IntegrationToken).one()
        token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(token)
        db.commit()
    finally:
        db.close()

    def unexpected_get(*_args, **_kwargs):
        raise AssertionError("Expired stored tokens must not be sent to Meta")

    monkeypatch.setattr(instagram_business_module.requests, "get", unexpected_get)

    response = client.post(
        "/integrations/instagram-business-login/sync",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "credential_integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
            "timeframe": "last_30d",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "instagram_business_login_reauthorization_required"
    db = SessionLocal()
    try:
        assert db.get(Integration, refs["integration_id"]).status == "reauthorization_required"
        assert db.query(Dataset).count() == 0
    finally:
        db.close()


def test_instagram_business_login_refreshes_valid_long_lived_token_near_expiry(
    client,
    monkeypatch,
):
    refs = _seed_connected_instagram_login()
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        token = db.query(IntegrationToken).one()
        token.expires_at = now + timedelta(days=2)
        token.updated_at = now - timedelta(days=3)
        db.add(token)
        db.commit()
        db.refresh(token)
        # SQLite/SQLAlchemy preserves the explicitly supplied timestamp; this
        # is the Meta requirement that the long-lived token be at least 24h old.
        assert main_module._utc_datetime(token.updated_at) <= now - timedelta(hours=24)

        refresh_calls: list[str] = []

        def fake_refresh(access_token: str):
            refresh_calls.append(access_token)
            return {
                "access_token": "refreshed-long-lived-token",
                "expires_in": 5_184_000,
                "_http_status_code": 200,
            }

        monkeypatch.setattr(main_module, "refresh_instagram_business_login_access_token", fake_refresh)

        resolved = main_module._refresh_instagram_business_login_token_if_needed(
            db=db,
            integration=integration,
            access_token="ig-login-token",
            route_name="instagram_business_login_sync",
        )

        assert resolved == "refreshed-long-lived-token"
        assert refresh_calls == ["ig-login-token"]
        _present, decrypt_ok, stored_token = main_module._resolve_instagram_business_login_access_token(
            db,
            integration,
        )
        assert decrypt_ok is True
        assert stored_token == "refreshed-long-lived-token"
        refreshed_row = db.query(IntegrationToken).one()
        assert main_module._utc_datetime(refreshed_row.expires_at) >= now + timedelta(days=59)
    finally:
        db.close()


def test_instagram_business_login_canonical_sync_rejects_missing_identity_before_provider_call(
    client,
    monkeypatch,
):
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        identity_account = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == refs["integration_id"],
                IntegrationAccount.external_account_id == refs["instagram_account_id"],
            )
            .one()
        )
        db.delete(identity_account)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        instagram_business_module.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called for invalid ownership"),
    )
    response = client.post(
        "/integrations/instagram-business-login/sync",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "credential_integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "instagram_business_login_identity_missing"


def test_instagram_business_login_canonical_sync_rejects_wrong_workspace_before_provider_call(
    client,
    monkeypatch,
):
    refs = _seed_connected_instagram_login()
    monkeypatch.setattr(
        instagram_business_module.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called for a workspace mismatch"),
    )

    response = client.post(
        "/integrations/instagram-business-login/sync",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "credential_integration_id": refs["integration_id"],
            "account_id": refs["instagram_account_id"],
            "workspace_id": int(refs["workspace_id"]) + 1,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "workspace_mismatch"


def test_instagram_business_login_sync_classifies_parameter_incompatibility(
    client,
    monkeypatch,
    caplog,
):
    refs = _seed_connected_instagram_login()
    caplog.set_level("INFO")

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, *, params=None, headers=None, timeout=None):
        assert headers == {"Authorization": "Bearer ig-login-token"}
        if url.endswith("/me"):
            return FakeResponse(
                200,
                {
                    "id": refs["instagram_account_id"],
                    "username": "iglogin",
                    "name": "IG Login Account",
                    "followers_count": 1234,
                },
            )
        if url.endswith(f"/{refs['instagram_account_id']}/media"):
            return FakeResponse(200, {"data": []})
        assert url.endswith(f"/{refs['instagram_account_id']}/insights")
        if params["metric"] == "views":
            assert params["metric_type"] == "total_value"
            return FakeResponse(
                400,
                {
                    "error": {
                        "message": "(#100) The following metrics (views) should be specified with parameter metric_type=total_value",
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
    assert payload["metrics_failed"] == ["views"]
    assert "INSTAGRAM_INSIGHT_RESPONSE" in caplog.text
    assert '"response_shape": "parameter_incompatible"' in caplog.text
    assert '"metric_type": "total_value"' in caplog.text

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, payload["dataset_id"])
        assert dataset is not None
        views_audit = dataset.data["instagram_metric_audit"]["metrics"]["views"]
        assert views_audit["response_shape"] == "parameter_incompatible"
        assert views_audit["metric_type"] == "total_value"
        assert dataset.data["unavailable_metrics"]["views"].endswith("metric_type=total_value")
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

    account_metric_series = {
        "reach": [100, 150],
    }
    account_metric_totals = {
        "views": 900,
        "accounts_engaged": 30,
        "total_interactions": 70,
        "likes": 11,
        "comments": 4,
        "shares": 3,
        "saves": 2,
        "replies": 1,
        "profile_links_taps": 6,
        "follows_and_unfollows": 5,
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

    def total_value_payload(metric_name: str, value: int) -> dict:
        return {
            "data": [
                {
                    "name": metric_name,
                    "period": "day",
                    "total_value": {"value": value},
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
            assert metric_name in set(main_module.INSTAGRAM_BUSINESS_LOGIN_ACCOUNT_INSIGHT_METRICS)
            if metric_name == "reach":
                assert params.get("metric_type") is None
                return FakeResponse(200, metric_payload(metric_name, account_metric_series[metric_name]))
            assert params["metric_type"] == "total_value"
            return FakeResponse(200, total_value_payload(metric_name, account_metric_totals[metric_name]))
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
    assert payload["metrics_failed"] == []

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
        assert data["profile_views"] is None
        assert data["profile_visits"] is None
        assert data["website_clicks"] is None
        assert data["profile_links_taps"] == 6
        assert data["profile_activity"] == 6
        assert data["follows_and_unfollows"] == 5
        assert data["followers_count"] == 1234
        assert data["media_count"] == 12
        assert data["normalized_report_metrics"]["views_total"] == 900
        assert data["normalized_report_metrics"]["views_daily"] == []
        assert data["normalized_report_metrics"]["interactions_total"] == 70
        assert data["normalized_report_metrics"]["interactions_daily"] == []
        assert data["normalized_report_metrics"]["profile_links_taps_total"] == 6
        assert data["normalized_report_metrics"]["page_visits_total"] is None
        assert data["normalized_report_metrics"]["followers_total"] == 1234
        assert data["normalized_report_metrics"]["followers_growth_total"] == 1234
        assert data["instagram_metric_audit"]["metrics"]["reach"]["response_shape"] == "values"
        assert data["instagram_metric_audit"]["metrics"]["views"]["response_shape"] == "total_value"
        assert data["instagram_metric_audit"]["metrics"]["views"]["metric_type"] == "total_value"
        assert data["instagram_metric_audit"]["metrics"]["total_interactions"]["response_shape"] == "total_value"
        blocks = main_module.build_instagram_business_5_blocks(
            {
                "dataset_id": dataset.id,
                "page_name": data["page_name"],
                "account_name": data["account_name"],
                "report_inputs": data,
                "report_timeframe": data["timeframe"],
                "branding": {},
            }
        )
        slide_payloads = [json.loads(block["data_json"]) for block in blocks]
        slides = {payload["semantic_name"]: payload for payload in slide_payloads}
        reach_slide = slides["instagram_reach"]
        views_slide = slides["instagram_views"]
        engagement_slide = slides["instagram_engagement"]
        assert reach_slide["total"] == 250
        assert len(reach_slide["daily_series"]) == 2
        assert reach_slide["chart"]["is_available"] is True
        assert views_slide["title"] == "VIEWS"
        assert views_slide["metric_label"] == "Views"
        assert views_slide["total"] == 900
        assert views_slide["daily_series"] == []
        assert views_slide["chart"]["is_available"] is False
        assert views_slide["is_available"] is True
        assert views_slide["unavailable_reason"] is None
        assert engagement_slide["total"] == 70
        assert engagement_slide["daily_series"] == []
        assert engagement_slide["chart"]["is_available"] is False
        assert engagement_slide["is_available"] is True
        assert engagement_slide["unavailable_reason"] is None
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

    monkeypatch.setattr(main_module, "_generate_manual_report", fake_create_meta_dataset_report)

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


def test_direct_instagram_frozen_dataset_builds_authoritative_report_source():
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        dataset = Dataset(
            workspace_id=int(refs["workspace_id"]),
            name="direct-instagram.csv",
            data={
                "source_type": "instagram_business",
                "integration_type": "instagram_business",
                "provider": "instagram_business_login",
                "auth_method": "instagram_business_login",
                "credential_integration_id": refs["integration_id"],
                "asset_integration_id": refs["integration_id"],
                "account_id": refs["instagram_account_id"],
                "username": "iglogin",
                "account_name": "IG Login Account",
                "parent_page_id": None,
            },
        )
        db.add(dataset)
        db.flush()
        source = main_module._build_single_source_report_source(
            db,
            report=Report(workspace_id=int(refs["workspace_id"])),
            dataset=dataset,
            payload=InstagramBusinessReportCreateIn(dataset_id=dataset.id),
            selected_sources=["instagram_business"],
            report_inputs=dict(dataset.data),
        )
        assert source is not None
        assert source.provider == "instagram_business_login"
        assert source.source_type == "instagram_business"
        assert source.integration_id == refs["integration_id"]
        assert source.integration_account_id is not None
        assert source.config_json["instagram_account_id"] == refs["instagram_account_id"]
        assert source.config_json["external_account_id"] == refs["instagram_account_id"]
        assert source.config_json["auth_method"] == "instagram_business_login"
        assert source.config_json["credential_integration_id"] == refs["integration_id"]
        assert source.config_json["asset_integration_id"] == refs["integration_id"]
        assert source.config_json["parent_page_id"] is None
    finally:
        db.rollback()
        db.close()


def test_frozen_instagram_dataset_rejects_conflicting_report_identity(client, monkeypatch):
    refs = _seed_connected_instagram_login()
    db = SessionLocal()
    try:
        dataset = Dataset(
            workspace_id=int(refs["workspace_id"]),
            name="direct-instagram.csv",
            data={
                "source_type": "instagram_business",
                "integration_type": "instagram_business",
                "provider": "instagram_business_login",
                "auth_method": "instagram_business_login",
                "credential_integration_id": refs["integration_id"],
                "asset_integration_id": refs["integration_id"],
                "account_id": refs["instagram_account_id"],
            },
        )
        db.add(dataset)
        db.commit()
        dataset_id = dataset.id
    finally:
        db.close()
    monkeypatch.setattr(
        main_module,
        "_generate_manual_report",
        lambda **_kwargs: pytest.fail("conflicting identity must fail before report generation"),
    )

    response = client.post(
        "/reports/instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={"dataset_id": dataset_id, "account_id": "wrong-account"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "dataset_account_mismatch"
