from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_meta_ads_integration.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")
os.environ.setdefault("API_BASE_URL", "http://localhost:8000")
os.environ.setdefault("META_APP_ID", "meta-app-id")
os.environ.setdefault("META_APP_SECRET", "meta-app-secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback")
os.environ.setdefault("META_ADS_APP_ID", "meta-app-id")
os.environ.setdefault("META_ADS_APP_SECRET", "meta-app-secret")
os.environ.setdefault("META_ADS_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
from app.integrations import meta_ads as meta_ads_module
from app.integrations.meta_ads import META_ADS_OAUTH_SCOPE, encode_state
import app.main as main_module
from app.main import app
from app.models import (
    Dataset,
    DatasetFile,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaAdAccount,
    MetaAdsInsightDaily,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


META_ADS_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaAdAccount.__table__,
    MetaAdsInsightDaily.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
]


@pytest.fixture(autouse=True)
def meta_ads_schema():
    Base.metadata.drop_all(bind=engine, tables=META_ADS_TABLES)
    Base.metadata.create_all(bind=engine, tables=META_ADS_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=META_ADS_TABLES)


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


def _seed_workspace() -> dict[str, int]:
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
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _create_meta_ads_integration(*, workspace_id: int, status: str = "connected") -> int:
    db = SessionLocal()
    try:
        integration = Integration(
            workspace_id=workspace_id,
            provider="meta_ads",
            name="Meta Ads",
            status=status,
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return integration.id
    finally:
        db.close()


def _create_meta_suite_ads_connection(
    *,
    workspace_id: int,
    account_id: str | None = None,
    account_name: str = "Suite Ads Account",
) -> dict[str, int]:
    db = SessionLocal()
    try:
        suite_integration = Integration(
            workspace_id=workspace_id,
            provider="meta_business_suite",
            name="Meta Business Suite",
            status="connected",
        )
        ads_integration = Integration(
            workspace_id=workspace_id,
            provider="meta_ads",
            name="Meta Ads",
            status="connected",
        )
        db.add_all([suite_integration, ads_integration])
        db.flush()
        suite_token_account = IntegrationAccount(
            integration_id=suite_integration.id,
            workspace_id=workspace_id,
            external_account_id=f"__meta_token__:{suite_integration.id}",
            display_name="Meta Business Suite token store",
        )
        db.add(suite_token_account)
        db.flush()
        db.add(
            IntegrationToken(
                account_id=suite_token_account.id,
                workspace_id=workspace_id,
                token_type="access_token",
                access_token="suite-access-token",
            )
        )
        if account_id is not None:
            db.add(
                MetaAdAccount(
                    integration_id=ads_integration.id,
                    workspace_id=workspace_id,
                    account_id=account_id,
                    account_name=account_name,
                )
            )
        db.commit()
        return {
            "suite_integration_id": suite_integration.id,
            "meta_ads_integration_id": ads_integration.id,
        }
    finally:
        db.close()


class _FakeS3Client:
    def put_object(self, **_kwargs):
        return None


def test_meta_ads_connect_creates_separate_integration(client):
    refs = _seed_workspace()
    for key, value in (
        ("META_ADS_APP_ID", "meta-app-id"),
        ("META_ADS_APP_SECRET", "meta-app-secret"),
        ("META_ADS_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        os.environ[key] = value
    for key, value in (("api_base_url", "http://localhost:8000"),):
        setattr(main_module.settings, key, value)
        setattr(meta_ads_module.settings, key, value)

    response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE
    assert payload["scope"] == "public_profile,pages_show_list,pages_read_engagement,read_insights,pages_read_user_content,instagram_basic,instagram_manage_insights,business_management,ads_read"
    assert "ads_read" in payload["auth_url"]
    assert "business_management" in payload["auth_url"]
    assert "pages_show_list" in payload["auth_url"]
    assert "instagram_basic" in payload["auth_url"]
    query = parse_qs(urlparse(payload["auth_url"]).query)
    assert query["scope"] == [meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE]
    assert query["auth_type"] == ["rerequest"]
    assert query["redirect_uri"] == ["http://localhost:8000/integrations/meta/callback-pages"]

    db = SessionLocal()
    try:
        integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "meta_ads")
            .one()
        )
        assert integration.name == "Meta Ads"
    finally:
        db.close()


def test_meta_ads_callback_without_business_management_sets_needs_permission(client, monkeypatch):
    refs = _seed_workspace()
    integration_id = _create_meta_ads_integration(workspace_id=refs["workspace_id"], status="disconnected")
    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
        ("api_base_url", "http://localhost:8000"),
    ):
        setattr(main_module.settings, key, value)
        setattr(meta_ads_module.settings, key, value)

    state = encode_state(
        {
            "workspace_id": refs["workspace_id"],
            "user_id": refs["user_id"],
            "integration_id": integration_id,
            "provider": "meta_ads",
        }
    )
    monkeypatch.setattr(
        "app.main.exchange_code_for_token",
        lambda code, redirect_uri=None: {"access_token": f"token-for-{code}"},
    )
    monkeypatch.setattr(
        "app.main.debug_token",
        lambda _access_token: {"data": {"is_valid": True, "scopes": ["public_profile", "ads_read"]}},
    )

    response = client.get(
        "/integrations/meta-ads/callback",
        params={"code": "oauth-code", "state": state},
    )

    assert response.status_code == 200
    assert "Business Manager asset access is required" in response.text

    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "needs_permission"
        token_account = (
            db.query(IntegrationAccount)
            .filter(IntegrationAccount.integration_id == integration_id)
            .one()
        )
        token = (
            db.query(IntegrationToken)
            .filter(IntegrationToken.account_id == token_account.id)
            .one()
        )
        assert token.access_token == "token-for-oauth-code"
    finally:
        db.close()


def test_meta_ads_callback_without_accounts_sets_connected_no_assets(client, monkeypatch):
    refs = _seed_workspace()
    integration_id = _create_meta_ads_integration(workspace_id=refs["workspace_id"], status="disconnected")
    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
        ("api_base_url", "http://localhost:8000"),
    ):
        setattr(main_module.settings, key, value)
        setattr(meta_ads_module.settings, key, value)

    state = encode_state(
        {
            "workspace_id": refs["workspace_id"],
            "user_id": refs["user_id"],
            "integration_id": integration_id,
            "provider": "meta_ads",
        }
    )
    monkeypatch.setattr(
        "app.main.exchange_code_for_token",
        lambda code, redirect_uri=None: {"access_token": f"token-for-{code}"},
    )
    monkeypatch.setattr(
        "app.main.debug_token",
        lambda _access_token: {
            "data": {
                "is_valid": True,
                "scopes": ["public_profile", "ads_read", "business_management"],
            }
        },
    )
    monkeypatch.setattr("app.main.list_ad_accounts", lambda _access_token: [])
    monkeypatch.setattr("app.main.get_business_ad_accounts", lambda _access_token: [])

    response = client.get(
        "/integrations/meta-ads/callback",
        params={"code": "oauth-code", "state": state},
    )

    assert response.status_code == 200
    assert "no authorized ad accounts were returned" in response.text

    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "connected_no_assets"
    finally:
        db.close()


def test_meta_ads_callback_persists_accounts_when_available(client, monkeypatch):
    refs = _seed_workspace()
    integration_id = _create_meta_ads_integration(workspace_id=refs["workspace_id"], status="disconnected")
    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
        ("api_base_url", "http://localhost:8000"),
    ):
        setattr(main_module.settings, key, value)
        setattr(meta_ads_module.settings, key, value)

    state = encode_state(
        {
            "workspace_id": refs["workspace_id"],
            "user_id": refs["user_id"],
            "integration_id": integration_id,
            "provider": "meta_ads",
        }
    )
    monkeypatch.setattr(
        "app.main.exchange_code_for_token",
        lambda code, redirect_uri=None: {"access_token": f"token-for-{code}"},
    )
    monkeypatch.setattr(
        "app.main.debug_token",
        lambda _access_token: {
            "data": {
                "is_valid": True,
                "scopes": ["public_profile", "ads_read", "business_management"],
            }
        },
    )
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(main_module, "_table_available", lambda _name: True)
    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _access_token: [{"id": "act_123", "account_id": "123", "name": "Primary Ads Account"}],
    )

    response = client.get(
        "/integrations/meta-ads/callback",
        params={"code": "oauth-code", "state": state},
    )

    assert response.status_code == 200
    assert "Meta Ads connected successfully" in response.text

    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "connected"
        account = (
            db.query(MetaAdAccount)
            .filter(MetaAdAccount.integration_id == integration_id)
            .one()
        )
        assert account.account_id == "123"
        assert account.account_name == "Primary Ads Account"
    finally:
        db.close()


def test_meta_ads_connect_accepts_preferred_env_family(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.setenv("META_ADS_APP_ID", "meta-app-id")
    monkeypatch.setenv("META_ADS_APP_SECRET", "meta-app-secret")
    monkeypatch.setenv("META_ADS_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback")
    monkeypatch.setattr(main_module.settings, "api_base_url", "http://localhost:8000")
    monkeypatch.setattr(meta_ads_module.settings, "api_base_url", "http://localhost:8000")

    response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json()["scope"] == meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE


def test_meta_ads_connect_accepts_legacy_fallback_env_family(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.delenv("META_ADS_APP_ID", raising=False)
    monkeypatch.delenv("META_ADS_APP_SECRET", raising=False)
    monkeypatch.delenv("META_ADS_REDIRECT_URI", raising=False)
    monkeypatch.setenv("META_APP_ID", "legacy-meta-app-id")
    monkeypatch.setenv("META_APP_SECRET", "legacy-meta-app-secret")
    monkeypatch.setenv("META_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback")
    monkeypatch.setattr(main_module.settings, "api_base_url", "http://localhost:8000")
    monkeypatch.setattr(meta_ads_module.settings, "api_base_url", "http://localhost:8000")

    response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    query = parse_qs(urlparse(payload["auth_url"]).query)
    assert query["scope"] == [meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE]
    assert query["redirect_uri"] == ["http://localhost:8000/integrations/meta/callback-pages"]


def test_meta_ads_select_accepts_cached_account_returned_by_accounts_with_child_id(client, monkeypatch):
    refs = _seed_workspace()
    ids = _create_meta_suite_ads_connection(
        workspace_id=refs["workspace_id"],
        account_id="265",
        account_name="Cached Ads Account",
    )
    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _token: pytest.fail("select-account should not require live Graph when account is cached"),
    )

    accounts_response = client.get(
        "/integrations/meta-ads/accounts",
        headers=_auth_headers(refs["user_id"]),
        params={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["meta_ads_integration_id"],
        },
    )
    assert accounts_response.status_code == 200
    assert accounts_response.json()[0]["account_id"] == "265"

    select_response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["meta_ads_integration_id"],
            "ad_account_id": "265",
        },
    )

    assert select_response.status_code == 200
    select_payload = select_response.json()
    assert select_payload["account_id"] == "265"
    assert select_payload["is_selected"] is True
    assert select_payload["source"] == "persisted_db"


def test_meta_ads_select_normalizes_act_prefixed_cached_account_with_suite_id(client, monkeypatch):
    refs = _seed_workspace()
    ids = _create_meta_suite_ads_connection(
        workspace_id=refs["workspace_id"],
        account_id="act_265",
        account_name="Legacy Cached Ads Account",
    )
    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _token: pytest.fail("select-account should accept persisted act_ account without live Graph"),
    )

    accounts_response = client.get(
        "/integrations/meta-ads/accounts",
        headers=_auth_headers(refs["user_id"]),
        params={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["suite_integration_id"],
        },
    )
    assert accounts_response.status_code == 200
    assert accounts_response.json()[0]["account_id"] == "act_265"

    select_response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["suite_integration_id"],
            "ad_account_id": "265",
        },
    )

    assert select_response.status_code == 200
    select_payload = select_response.json()
    assert select_payload["account_id"] == "265"
    assert select_payload["is_selected"] is True


def test_meta_ads_select_resolves_internal_row_id_to_real_ad_account_id(client, monkeypatch):
    refs = _seed_workspace()
    real_ad_account_id = "123456789012345"
    ids = _create_meta_suite_ads_connection(
        workspace_id=refs["workspace_id"],
        account_id=real_ad_account_id,
        account_name="11:11 Day&Night Ads",
    )
    db = SessionLocal()
    try:
        account = (
            db.query(MetaAdAccount)
            .filter(
                MetaAdAccount.integration_id == ids["meta_ads_integration_id"],
                MetaAdAccount.account_id == real_ad_account_id,
            )
            .one()
        )
        local_row_id = account.id
    finally:
        db.close()
    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _token: pytest.fail("select-account should resolve valid cached local row ids without live Graph"),
    )

    response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["suite_integration_id"],
            "ad_account_id": str(local_row_id),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == local_row_id
    assert payload["account_id"] == real_ad_account_id
    assert payload["name"] == "11:11 Day&Night Ads"
    assert payload["is_selected"] is True

    db = SessionLocal()
    try:
        selected_account = db.get(MetaAdAccount, local_row_id)
        assert selected_account is not None
        assert selected_account.account_id == real_ad_account_id
        assert selected_account.is_selected is True
    finally:
        db.close()


def test_meta_ads_accounts_normalizes_numeric_live_account_and_select_accepts_string(client, monkeypatch):
    refs = _seed_workspace()
    _create_meta_suite_ads_connection(workspace_id=refs["workspace_id"])
    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _token: [
            {
                "id": "act_265",
                "account_id": 265,
                "name": "Live Numeric Ads Account",
            }
        ],
    )
    monkeypatch.setattr("app.main.get_businesses", lambda _token: [])

    accounts_response = client.get(
        "/integrations/meta-ads/accounts",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    assert accounts_response.status_code == 200
    assert accounts_response.json()[0]["account_id"] == "265"

    select_response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={
            "workspace_id": refs["workspace_id"],
            "ad_account_id": "265",
        },
    )

    assert select_response.status_code == 200
    assert select_response.json()["account_id"] == "265"


def test_meta_ads_select_returns_structured_unavailable_error(client, monkeypatch):
    refs = _seed_workspace()
    ids = _create_meta_suite_ads_connection(workspace_id=refs["workspace_id"])
    monkeypatch.setattr("app.main.list_ad_accounts", lambda _token: [])
    monkeypatch.setattr("app.main.get_businesses", lambda _token: [])

    response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={
            "workspace_id": refs["workspace_id"],
            "integration_id": ids["suite_integration_id"],
            "ad_account_id": "act_999",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "meta_ad_account_not_available"
    assert detail["selected_ad_account_id"] == "act_999"
    assert detail["normalized_selected_ad_account_id"] == "999"
    assert detail["available_account_ids_count"] == 0
    assert detail["integration_id_used"] == ids["meta_ads_integration_id"]
    assert detail["suite_integration_id_used"] == ids["suite_integration_id"]
    assert detail["token_present"] is True


def test_meta_ads_accounts_select_sync_and_disconnect(client, monkeypatch):
    refs = _seed_workspace()
    integration_id = _create_meta_ads_integration(workspace_id=refs["workspace_id"], status="connected")

    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=f"__meta_token__:{integration.id}",
            display_name="Meta Ads token store",
        )
        db.add(token_account)
        db.flush()
        db.add(
            IntegrationToken(
                account_id=token_account.id,
                workspace_id=integration.workspace_id,
                token_type="access_token",
                access_token="meta-ads-access-token",
            )
        )
        suite_integration = Integration(
            workspace_id=integration.workspace_id,
            provider="meta_business_suite",
            name="Meta Business Suite",
            status="connected",
        )
        db.add(suite_integration)
        db.flush()
        suite_token_account = IntegrationAccount(
            integration_id=suite_integration.id,
            workspace_id=integration.workspace_id,
            external_account_id=f"__meta_token__:{suite_integration.id}",
            display_name="Meta Business Suite token store",
        )
        db.add(suite_token_account)
        db.flush()
        db.add(
            IntegrationToken(
                account_id=suite_token_account.id,
                workspace_id=integration.workspace_id,
                token_type="access_token",
                access_token="suite-access-token",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main.list_ad_accounts",
        lambda _token: [
            {
                "id": "act_123456",
                "account_id": "123456",
                "name": "Measurable Ads",
                "currency": "USD",
                "timezone_name": "America/Mexico_City",
                "account_status": "1",
                "business": {"id": "biz-1", "name": "Measurable Business"},
            }
        ],
    )
    monkeypatch.setattr(
        "app.main.debug_token",
        lambda _access_token: {
            "data": {
                "is_valid": True,
                "scopes": ["public_profile", "ads_read", "business_management"],
            }
        },
    )
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(main_module, "_table_available", lambda _name: True)
    monkeypatch.setattr("app.main.boto3.client", lambda *_args, **_kwargs: _FakeS3Client())
    monkeypatch.setattr(
        "app.main.fetch_campaign_insights",
        lambda *_args, **_kwargs: [
            {
                "date_start": "2026-06-01",
                "date_stop": "2026-06-01",
                "spend": "125.50",
                "impressions": "1000",
                "reach": "850",
                "clicks": "42",
                "inline_link_clicks": "21",
                "ctr": "4.2",
                "cpc": "2.9881",
                "cpm": "125.5",
                "frequency": "1.18",
                "actions": [{"action_type": "lead", "value": "3"}],
                "cost_per_action_type": [{"action_type": "lead", "value": "41.8333"}],
                "campaign_id": "cmp-1",
                "campaign_name": "Launch",
                "adset_id": "aset-1",
                "adset_name": "Audience A",
                "ad_id": "ad-1",
                "ad_name": "Creative A",
            },
            {
                "date_start": "2026-06-02",
                "date_stop": "2026-06-02",
                "spend": "74.50",
                "impressions": "500",
                "reach": "410",
                "clicks": "18",
                "inline_link_clicks": "9",
                "ctr": "3.6",
                "cpc": "4.1389",
                "cpm": "149.0",
                "frequency": "1.22",
                "actions": [{"action_type": "lead", "value": "2"}],
                "cost_per_action_type": [{"action_type": "lead", "value": "37.25"}],
                "campaign_id": "cmp-1",
                "campaign_name": "Launch",
                "adset_id": "aset-1",
                "adset_name": "Audience A",
                "ad_id": "ad-2",
                "ad_name": "Creative B",
            },
        ],
    )
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    accounts_response = client.get(
        "/integrations/meta-ads/accounts",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": integration_id},
    )
    assert accounts_response.status_code == 200
    accounts_payload = accounts_response.json()
    assert len(accounts_payload) == 1
    assert accounts_payload[0]["account_id"] == "123456"

    select_response = client.post(
        "/integrations/meta-ads/select-account",
        headers=_auth_headers(refs["user_id"]),
        json={"integration_id": integration_id, "ad_account_id": "123456"},
    )
    assert select_response.status_code == 200
    assert select_response.json()["is_selected"] is True

    sync_response = client.post(
        "/integrations/meta-ads/sync",
        headers=_auth_headers(refs["user_id"]),
        json={"integration_id": integration_id, "timeframe": "last_7d"},
    )
    assert sync_response.status_code == 200
    sync_payload = sync_response.json()
    assert sync_payload["status"] == "synced"
    assert sync_payload["ad_account_id"] == "123456"
    assert sync_payload["timeframe"]["key"] == "last_7_days"

    status_response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": integration_id},
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["connected"] is True
    assert status_payload["selected_account"]["account_id"] == "123456"
    assert status_payload["last_synced_at"] is not None

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, sync_payload["dataset_id"])
        assert dataset is not None
        assert dataset.data["integration_type"] == "meta_ads"
        assert dataset.data["total_spend"] == 200.0
        assert dataset.data["total_results"] == 5.0
        assert dataset.data["cost_per_result"] == 40.0
        assert dataset.data["top_campaigns"][0]["campaign_id"] == "cmp-1"
        assert len(dataset.data["daily_trend"]) == 2
        assert db.query(MetaAdsInsightDaily).filter(MetaAdsInsightDaily.integration_id == integration_id).count() == 2
    finally:
        db.close()

    disconnect_response = client.delete(
        "/integrations/meta-ads/disconnect",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": integration_id},
    )
    assert disconnect_response.status_code == 200
    disconnect_payload = disconnect_response.json()
    assert disconnect_payload["success"] is True
    assert disconnect_payload["cleared_accounts"] == 1
    assert disconnect_payload["cleared_rows"] == 2
    assert disconnect_payload["token_revoked"] is True
