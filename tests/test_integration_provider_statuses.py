from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_integration_provider_statuses.db")
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
from app.integrations import meta_ads as meta_ads_module
import app.meta_data_catalog as meta_data_catalog_module
import app.main as main_module
from app.report_metric_catalog import (
    FACEBOOK_PAGES_PROVIDER,
    INSTAGRAM_BUSINESS_PROVIDER,
    META_ADS_PROVIDER,
    explain_metric_availability,
    get_available_report_metrics,
    get_metric_catalog,
    get_recommended_report_metrics,
    is_metric_available,
    normalize_metric_key,
)
from app.main import (
    META_RECORD_TYPE_FACEBOOK_PAGE,
    META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    app,
)
from app.models import Integration, IntegrationAccount, IntegrationToken, MetaAdAccount, MetaPage, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


INTEGRATION_STATUS_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaAdAccount.__table__,
    MetaPage.__table__,
]


@pytest.fixture(autouse=True)
def integration_status_schema():
    Base.metadata.drop_all(bind=engine, tables=INTEGRATION_STATUS_TABLES)
    Base.metadata.create_all(bind=engine, tables=INTEGRATION_STATUS_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=INTEGRATION_STATUS_TABLES)


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


def _seed_workspace_with_legacy_meta() -> dict[str, int]:
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
            provider="meta",
            name="Meta Pages",
            status="connected",
        )
        db.add(integration)
        db.flush()
        db.add_all(
            [
                MetaPage(
                    integration_id=integration.id,
                    user_id=user.id,
                    record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                    page_id="fb-1",
                    name="Botanero FB",
                ),
                MetaPage(
                    integration_id=integration.id,
                    user_id=user.id,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    page_id="ig-1",
                    parent_page_id="fb-1",
                    name="Botanero IG",
                    instagram_username="botaneroig",
                ),
            ]
        )
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _seed_admin_workspace_with_tokens() -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email="admin@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Admin User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
            is_admin=True,
        )
        workspace = Workspace(name="Admin Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        fb_integration = Integration(
            workspace_id=workspace.id,
            provider="meta",
            name="Meta Pages",
            status="connected",
        )
        ads_integration = Integration(
            workspace_id=workspace.id,
            provider="meta_ads",
            name="Meta Ads",
            status="connected",
        )
        db.add_all([fb_integration, ads_integration])
        db.flush()

        db.add(
            MetaPage(
                integration_id=fb_integration.id,
                user_id=user.id,
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-1",
                name="Botanero FB",
                page_access_token="page-token",
            )
        )
        db.add(
            MetaPage(
                integration_id=fb_integration.id,
                user_id=user.id,
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                page_id="ig-1",
                parent_page_id="fb-1",
                name="Botanero IG",
                instagram_username="botaneroig",
            )
        )
        fb_token_account = IntegrationAccount(
            integration_id=fb_integration.id,
            workspace_id=workspace.id,
            external_account_id=f"__meta_token__:{fb_integration.id}",
            display_name="Meta token store",
        )
        ads_token_account = IntegrationAccount(
            integration_id=ads_integration.id,
            workspace_id=workspace.id,
            external_account_id=f"__meta_token__:{ads_integration.id}",
            display_name="Meta Ads token store",
        )
        db.add_all([fb_token_account, ads_token_account])
        db.flush()
        db.add_all(
            [
                IntegrationToken(
                    account_id=fb_token_account.id,
                    workspace_id=workspace.id,
                    token_type="access_token",
                    access_token="fb-token",
                ),
                IntegrationToken(
                    account_id=ads_token_account.id,
                    workspace_id=workspace.id,
                    token_type="access_token",
                    access_token="ads-token",
                ),
            ]
        )
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _seed_workspace_with_suite_token() -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email="suite-status@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Suite Status User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Suite Status Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        suite_integration = Integration(
            workspace_id=workspace.id,
            provider="meta_business_suite",
            name="Meta Business Suite",
            status="connected",
        )
        facebook_integration = Integration(
            workspace_id=workspace.id,
            provider="meta",
            name="Meta Pages",
            status="disconnected",
        )
        instagram_integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="disconnected",
        )
        ads_integration = Integration(
            workspace_id=workspace.id,
            provider="meta_ads",
            name="Meta Ads",
            status="disconnected",
        )
        db.add_all([suite_integration, facebook_integration, instagram_integration, ads_integration])
        db.flush()
        suite_token_account = IntegrationAccount(
            integration_id=suite_integration.id,
            workspace_id=workspace.id,
            external_account_id=f"__meta_token__:{suite_integration.id}",
            display_name="Meta Business Suite token store",
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
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "suite_integration_id": suite_integration.id,
            "facebook_integration_id": facebook_integration.id,
            "instagram_integration_id": instagram_integration.id,
            "meta_ads_integration_id": ads_integration.id,
        }
    finally:
        db.close()


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_instagram_business_status_endpoint_never_returns_404(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["provider"] == "instagram_business"
    assert payload["status"] == "no_token"
    assert payload["asset_count"] == 0
    assert payload["missing_scopes"] == []


def test_meta_ads_connect_without_config_returns_controlled_error(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    for key in (
        "META_ADS_APP_ID",
        "META_ADS_APP_SECRET",
        "META_ADS_REDIRECT_URI",
        "META_APP_ID",
        "META_APP_SECRET",
        "META_REDIRECT_URI",
        "META_PAGES_APP_ID",
        "META_PAGES_APP_SECRET",
        "META_PAGES_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    for key in (
        "meta_ads_app_id",
        "meta_ads_app_secret",
        "meta_ads_redirect_uri",
        "meta_app_id",
        "meta_app_secret",
        "meta_redirect_uri",
        "meta_pages_app_id",
        "meta_pages_app_secret",
        "meta_pages_redirect_uri",
    ):
        monkeypatch.setattr(main_module.settings, key, None)
        monkeypatch.setattr(meta_ads_module.settings, key, None)

    response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "meta_ads_not_configured"


def test_meta_ads_status_does_not_crash_when_reporting_tables_are_missing(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    db = SessionLocal()
    try:
        integration = Integration(
            workspace_id=refs["workspace_id"],
            provider="meta_ads",
            name="Meta Ads",
            status="connected",
        )
        db.add(integration)
        db.commit()
    finally:
        db.close()

    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: False)

    response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["accounts_count"] == 0
    assert "database tables are not available yet" in response.json()["message"]


def test_meta_ads_status_returns_needs_permission_when_business_management_missing(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    integration_id = refs["meta_ads_integration_id"]

    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "ads_read"]}},
    )

    response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": integration_id, "refresh": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_permission"
    assert payload["connected"] is False
    assert payload["missing_scopes"] == ["business_management"]
    assert payload["permission_missing"] is True


def test_meta_ads_status_returns_checking_before_first_live_discovery(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    integration_id = refs["meta_ads_integration_id"]

    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": ["public_profile", "ads_read", "business_management"],
            }
        },
    )

    response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": integration_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "checking"
    assert payload["connected"] is True
    assert payload["asset_count"] == 0
    assert payload["discovery_status"] == "pending"
    assert payload["account_names"] == []


def test_integrations_returns_single_meta_business_suite_card(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    provider_map = {item["provider"]: item for item in payload}

    assert "meta" not in provider_map
    assert "facebook_pages" not in provider_map
    assert "instagram_business" not in provider_map
    assert "meta_ads" not in provider_map
    assert provider_map["meta_business_suite"]["status"] == "no_token"
    assert provider_map["meta_business_suite"]["connected"] is False
    assert provider_map["meta_business_suite"]["asset_count"] == 1


def test_suite_backed_connect_returns_visible_provider_ids(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    for key, value in (
        ("meta_pages_app_id", "meta-pages-app-id"),
        ("meta_pages_app_secret", "meta-pages-app-secret"),
        ("meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback"),
        ("api_base_url", "https://api.measurableapp.com"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)

    instagram_response = client.get(
        "/integrations/instagram-business/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    meta_ads_response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert instagram_response.status_code == 200
    assert meta_ads_response.status_code == 200
    instagram_payload = instagram_response.json()
    meta_ads_payload = meta_ads_response.json()
    instagram_state = meta_ads_module.decode_state(
        parse_qs(urlparse(instagram_payload["auth_url"]).query)["state"][0]
    )
    meta_ads_state = meta_ads_module.decode_state(
        parse_qs(urlparse(meta_ads_payload["auth_url"]).query)["state"][0]
    )

    db = SessionLocal()
    try:
        instagram_integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "instagram_business")
            .one()
        )
        ads_integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "meta_ads")
            .one()
        )
        suite_integration = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "meta_business_suite")
            .one()
        )
    finally:
        db.close()

    assert instagram_payload["integration_id"] == instagram_integration.id
    assert instagram_state["integration_id"] == instagram_integration.id
    assert instagram_state["suite_integration_id"] == suite_integration.id
    assert instagram_state["provider"] == "instagram_business"
    assert instagram_state["source"] == "instagram_business"
    assert instagram_state["integration_type"] == "instagram_business"

    assert meta_ads_payload["integration_id"] == ads_integration.id
    assert meta_ads_state["integration_id"] == ads_integration.id
    assert meta_ads_state["suite_integration_id"] == suite_integration.id
    assert meta_ads_state["provider"] == "meta_ads"
    assert meta_ads_state["source"] == "meta_ads"
    assert meta_ads_state["integration_type"] == "meta_ads"


def test_instagram_business_suite_callback_returns_canonical_event_without_discovery(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    for key, value in (
        ("meta_pages_app_id", "meta-pages-app-id"),
        ("meta_pages_app_secret", "meta-pages-app-secret"),
        ("meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback"),
        ("api_base_url", "https://api.measurableapp.com"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)

    connect_response = client.get(
        "/integrations/instagram-business/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    assert connect_response.status_code == 200
    connect_payload = connect_response.json()
    state = parse_qs(urlparse(connect_payload["auth_url"]).query)["state"][0]

    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "suite-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE.split(","),
            }
        },
    )

    def fail_discovery(*_args, **_kwargs):
        raise AssertionError("Suite-backed visible provider callback should not block on discovery")

    monkeypatch.setattr(main_module, "_refresh_meta_pages_from_live_graph", fail_discovery)
    monkeypatch.setattr(main_module, "_collect_meta_instagram_diagnostics", fail_discovery)
    monkeypatch.setattr(main_module, "_discover_meta_ads_accounts_for_suite", fail_discovery)

    response = client.get(
        "/integrations/meta/callback-pages",
        params={"code": "meta-code", "state": state},
    )

    assert response.status_code == 200
    assert "measurable:integration-oauth-complete" in response.text
    assert '"provider": "instagram_business"' in response.text
    assert '"status": "connected"' in response.text
    assert f'"integrationId": {connect_payload["integration_id"]}' in response.text
    assert f'"workspaceId": {refs["workspace_id"]}' in response.text
    assert "MEASURABLE_META_CONNECT_SUCCESS" in response.text

    db = SessionLocal()
    try:
        suite = (
            db.query(Integration)
            .filter(Integration.workspace_id == refs["workspace_id"], Integration.provider == "meta_business_suite")
            .one()
        )
        instagram = db.get(Integration, connect_payload["integration_id"])
        assert suite.status == "connected"
        assert instagram is not None
        assert instagram.provider == "instagram_business"
        assert instagram.status == "checking"
    finally:
        db.close()

    suite_status_response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    statuses_response = client.get(
        "/integrations/meta/statuses",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert suite_status_response.status_code == 200
    suite_payload = suite_status_response.json()
    assert suite_payload["connected"] is True
    assert suite_payload["status"] == "connected"
    assert suite_payload["discovery_status"] == "pending"
    assert suite_payload["children"]["instagram_business"]["status"] == "checking"
    assert suite_payload["children"]["instagram_business"]["connected"] is True

    assert statuses_response.status_code == 200
    status_map = {item["provider"]: item for item in statuses_response.json()}
    assert status_map["instagram_business"]["status"] == "checking"
    assert status_map["instagram_business"]["connected"] is True
    assert status_map["instagram_business"]["discovery_status"] == "pending"


def test_meta_business_suite_callback_continues_when_client_ad_account_discovery_fails(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="suite-callback@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Suite Callback User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Suite Callback Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        db.commit()
        user_id = user.id
        workspace_id = workspace.id
    finally:
        db.close()

    for key, value in (
        ("meta_pages_app_id", "meta-pages-app-id"),
        ("meta_pages_app_secret", "meta-pages-app-secret"),
        ("meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback"),
        ("api_base_url", "https://api.measurableapp.com"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)

    state = meta_ads_module.encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "integration_type": "meta_business_suite",
            "source": "meta_business_suite",
            "provider": "meta_business_suite",
            "oauth_suite": "meta_business_suite",
            "callback_route": "/integrations/meta/callback-pages",
        }
    )

    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "suite-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE.split(","),
            }
        },
    )

    def fail_discovery(*_args, **_kwargs):
        raise AssertionError("OAuth callback should not run live discovery before returning popup HTML")

    monkeypatch.setattr(main_module, "_refresh_meta_pages_from_live_graph", fail_discovery)
    monkeypatch.setattr(main_module, "_collect_meta_instagram_diagnostics", fail_discovery)
    monkeypatch.setattr(main_module, "_discover_meta_ads_accounts_for_suite", fail_discovery)
    main_module._table_names.cache_clear()

    response = client.get(
        "/integrations/meta/callback-pages",
        params={"code": "meta-code", "state": state},
    )

    assert response.status_code == 200
    assert "Meta Business Suite connected successfully." in response.text
    assert "status=connected" in response.text
    assert "provider=meta_business_suite" in response.text

    db = SessionLocal()
    try:
        provider_statuses = {
            integration.provider: integration.status
            for integration in db.query(Integration).filter(Integration.workspace_id == workspace_id).all()
        }
        assert provider_statuses["meta_business_suite"] == "connected"
        assert provider_statuses["meta"] == "checking"
        assert provider_statuses["instagram_business"] == "checking"
        assert provider_statuses["meta_ads"] == "checking"
    finally:
        db.close()
        main_module._table_names.cache_clear()


def test_linked_instagram_discovery_does_not_mark_instagram_business_connected(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_meta_provider_statuses_are_cache_first_without_live_graph(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    main_module._table_names.cache_clear()
    db = SessionLocal()
    try:
        db.query(Integration).filter(
            Integration.workspace_id == refs["workspace_id"],
            Integration.provider == "meta",
        ).one().status = "connected_no_assets"
        db.query(Integration).filter(
            Integration.workspace_id == refs["workspace_id"],
            Integration.provider == "instagram_business",
        ).one().status = "connected_no_assets"
        db.query(Integration).filter(
            Integration.workspace_id == refs["workspace_id"],
            Integration.provider == "meta_ads",
        ).one().status = "connected_no_assets"
        db.commit()
    finally:
        db.close()

    def fail_live_graph(*_args, **_kwargs):
        raise AssertionError("status endpoints must not perform live Meta discovery without refresh=true")

    monkeypatch.setattr(main_module, "debug_token", fail_live_graph)
    monkeypatch.setattr(main_module, "_collect_meta_instagram_diagnostics", fail_live_graph)
    monkeypatch.setattr(main_module, "_discover_meta_ads_accounts_for_suite", fail_live_graph)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)

    statuses_response = client.get(
        "/integrations/meta/statuses",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    integrations_response = client.get(
        "/integrations",
        headers=_auth_headers(refs["user_id"]),
    )
    instagram_response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    ads_response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert statuses_response.status_code == 200
    status_map = {item["provider"]: item for item in statuses_response.json()}
    assert status_map["facebook_pages"]["status"] == "connected_no_assets"
    assert status_map["facebook_pages"]["connected"] is True
    assert status_map["instagram_business"]["status"] == "connected_no_assets"
    assert status_map["instagram_business"]["connected"] is True
    assert status_map["meta_ads"]["status"] == "connected_no_assets"
    assert status_map["meta_ads"]["connected"] is True

    assert integrations_response.status_code == 200
    integration_map = {item["provider"]: item for item in integrations_response.json()}
    assert set(integration_map) == {"meta_business_suite"}
    assert integration_map["meta_business_suite"]["status"] == "connected_no_assets"
    assert integration_map["meta_business_suite"]["connected"] is True

    assert instagram_response.status_code == 200
    assert instagram_response.json()["connected"] is True
    assert instagram_response.json()["status"] == "connected_no_assets"
    assert ads_response.status_code == 200
    assert ads_response.json()["connected"] is True
    assert ads_response.json()["status"] == "connected_no_assets"


def test_meta_business_suite_status_connects_facebook_child_from_cached_pages(client):
    refs = _seed_workspace_with_suite_token()
    db = SessionLocal()
    try:
        db.add(
            MetaPage(
                integration_id=refs["facebook_integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-suite-1",
                name="Suite Facebook Page",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["status"] == "connected"
    assert payload["children"]["facebook_pages"]["status"] == "connected"
    assert payload["children"]["facebook_pages"]["asset_count"] == 1
    assert payload["children"]["instagram_business"]["status"] == "checking"
    assert payload["children"]["instagram_business"]["connected"] is True
    assert payload["children"]["meta_ads"]["status"] == "checking"
    assert payload["children"]["meta_ads"]["connected"] is True


def test_meta_business_suite_status_connects_instagram_child_from_cached_accounts(client):
    refs = _seed_workspace_with_suite_token()
    db = SessionLocal()
    try:
        db.add(
            MetaPage(
                integration_id=refs["instagram_integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                page_id="ig-suite-1",
                parent_page_id="fb-suite-1",
                name="Suite Instagram Account",
                instagram_username="suiteig",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    instagram_response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["children"]["instagram_business"]["status"] == "connected"
    assert payload["children"]["instagram_business"]["asset_count"] == 1
    assert instagram_response.status_code == 200
    assert instagram_response.json()["status"] == payload["children"]["instagram_business"]["status"]
    assert instagram_response.json()["connected"] == payload["children"]["instagram_business"]["connected"]


def test_meta_business_suite_instagram_accounts_returns_pending_before_discovery(client):
    refs = _seed_workspace_with_suite_token()

    response = client.get(
        "/integrations/meta-business-suite/instagram-accounts",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "instagram_business"
    assert payload["integration_id"] == refs["instagram_integration_id"]
    assert payload["suite_integration_id"] == refs["suite_integration_id"]
    assert payload["connected"] is True
    assert payload["status"] == "checking"
    assert payload["discovery_status"] == "pending"
    assert payload["source"] == "discovery_pending"
    assert payload["data"] == []
    assert payload["message"] == "Instagram Business assets are still being prepared."


def test_meta_business_suite_instagram_accounts_returns_persisted_child_assets_without_direct_oauth(client):
    refs = _seed_workspace_with_suite_token()
    db = SessionLocal()
    try:
        instagram_direct_token_accounts = (
            db.query(IntegrationAccount)
            .filter(IntegrationAccount.integration_id == refs["instagram_integration_id"])
            .count()
        )
        assert instagram_direct_token_accounts == 0
        instagram_integration = db.get(Integration, refs["instagram_integration_id"])
        assert instagram_integration is not None
        instagram_integration.status = "connected"
        db.add(
            MetaPage(
                integration_id=refs["instagram_integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                page_id="17841400000000001",
                parent_page_id="fb-page-1",
                name="Atria Instagram",
                instagram_username="atria",
                business_name="Atria Facebook Page",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/integrations/meta-business-suite/instagram-accounts",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "instagram_business"
    assert payload["integration_id"] == refs["instagram_integration_id"]
    assert payload["suite_integration_id"] == refs["suite_integration_id"]
    assert payload["status"] == "connected"
    assert payload["connected"] is True
    assert payload["discovery_status"] == "succeeded"
    assert payload["source"] == "persisted_db"
    assert payload["count"] == 1
    assert len(payload["data"]) == 1
    account = payload["data"][0]
    assert account["id"] == "17841400000000001"
    assert account["account_id"] == "17841400000000001"
    assert account["page_id"] == "17841400000000001"
    assert account["parent_page_id"] == "fb-page-1"
    assert account["facebook_page_id"] == "fb-page-1"
    assert account["facebook_page_name"] == "Atria Facebook Page"
    assert account["name"] == "Atria Instagram"
    assert account["username"] == "atria"
    assert account["instagram_username"] == "atria"
    assert account["provider"] == "instagram_business"
    assert account["integration_id"] == refs["instagram_integration_id"]
    assert account["source"] == "meta_business_suite_cache"


def test_meta_business_suite_instagram_accounts_empty_only_after_discovery_completed(client):
    refs = _seed_workspace_with_suite_token()
    db = SessionLocal()
    try:
        instagram_integration = db.get(Integration, refs["instagram_integration_id"])
        assert instagram_integration is not None
        instagram_integration.status = "connected_no_assets"
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/integrations/meta-business-suite/instagram-accounts",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "instagram_business"
    assert payload["integration_id"] == refs["instagram_integration_id"]
    assert payload["connected"] is True
    assert payload["status"] == "connected_no_assets"
    assert payload["discovery_status"] == "succeeded"
    assert payload["source"] == "persisted_db"
    assert payload["data"] == []
    assert payload["message"] == "No Instagram Business accounts found."


def test_meta_business_suite_status_connects_ads_child_from_cached_accounts(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    db = SessionLocal()
    try:
        db.add(
            MetaAdAccount(
                integration_id=refs["meta_ads_integration_id"],
                workspace_id=refs["workspace_id"],
                account_id="123456",
                account_name="Suite Ads Account",
            )
        )
        db.commit()
    finally:
        db.close()

    suite_response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    ads_response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    statuses_response = client.get(
        "/integrations/meta/statuses",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert suite_response.status_code == 200
    suite_payload = suite_response.json()
    assert suite_payload["children"]["meta_ads"]["status"] == "connected"
    assert suite_payload["children"]["meta_ads"]["asset_count"] == 1
    assert ads_response.status_code == 200
    assert ads_response.json()["status"] == "connected"
    assert ads_response.json()["connected"] is True
    status_map = {item["provider"]: item for item in statuses_response.json()}
    assert status_map["meta_ads"]["status"] == suite_payload["children"]["meta_ads"]["status"]
    assert status_map["meta_ads"]["connected"] == suite_payload["children"]["meta_ads"]["connected"]


def test_meta_business_suite_refresh_discovers_persists_and_cache_reads_child_assets(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE.split(","),
            }
        },
    )
    monkeypatch.setattr(
        main_module,
        "_collect_meta_instagram_diagnostics",
        lambda *_args, **_kwargs: (
            [
                {
                    "record_type": META_RECORD_TYPE_FACEBOOK_PAGE,
                    "page_id": "fb-live-1",
                    "name": "Live Facebook Page",
                    "page_access_token": "page-token",
                },
                {
                    "record_type": META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    "page_id": "ig-live-1",
                    "parent_page_id": "fb-live-1",
                    "name": "Live Instagram Account",
                    "instagram_username": "liveig",
                },
            ],
            [
                {
                    "page_id": "fb-live-1",
                    "page_name": "Live Facebook Page",
                    "has_instagram_business_account": True,
                    "has_connected_instagram_account": False,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "_discover_meta_ads_accounts_for_suite",
        lambda *_args, **_kwargs: (
            [
                {
                    "id": "act_123456",
                    "account_id": "123456",
                    "name": "Live Ads Account",
                    "business": {"id": "biz-1", "name": "Live Business"},
                }
            ],
            [{"ad_account_id": "123456", "name": "Live Ads Account", "source": "/me/adaccounts"}],
            None,
            False,
        ),
    )

    refresh_response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"], "refresh": True},
    )

    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["children"]["facebook_pages"]["status"] == "connected"
    assert refresh_payload["children"]["facebook_pages"]["asset_count"] == 1
    assert refresh_payload["children"]["instagram_business"]["status"] == "connected"
    assert refresh_payload["children"]["instagram_business"]["asset_count"] == 1
    assert refresh_payload["children"]["meta_ads"]["status"] == "connected"
    assert refresh_payload["children"]["meta_ads"]["asset_count"] == 1

    db = SessionLocal()
    try:
        assert (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == refs["facebook_integration_id"],
                MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
            )
            .count()
            == 1
        )
        assert (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == refs["instagram_integration_id"],
                MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            )
            .count()
            == 1
        )
        assert (
            db.query(MetaAdAccount)
            .filter(MetaAdAccount.integration_id == refs["meta_ads_integration_id"])
            .count()
            == 1
        )
    finally:
        db.close()

    def fail_live_graph(*_args, **_kwargs):
        raise AssertionError("refresh=false must read cached suite assets without live Graph calls")

    monkeypatch.setattr(main_module, "debug_token", fail_live_graph)
    monkeypatch.setattr(main_module, "_collect_meta_instagram_diagnostics", fail_live_graph)
    monkeypatch.setattr(main_module, "_discover_meta_ads_accounts_for_suite", fail_live_graph)

    cached_response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    statuses_response = client.get(
        "/integrations/meta/statuses",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert cached_response.status_code == 200
    cached_payload = cached_response.json()
    assert cached_payload["children"]["facebook_pages"]["asset_count"] == 1
    assert cached_payload["children"]["instagram_business"]["asset_count"] == 1
    assert cached_payload["children"]["meta_ads"]["asset_count"] == 1
    assert statuses_response.status_code == 200
    status_map = {item["provider"]: item for item in statuses_response.json()}
    assert status_map["facebook_pages"]["status"] == "connected"
    assert status_map["instagram_business"]["status"] == "connected"
    assert status_map["meta_ads"]["status"] == "connected"


def test_shared_suite_token_resolves_provider_statuses_independently(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    main_module._table_names.cache_clear()
    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE.split(","),
            }
        },
    )
    monkeypatch.setattr(
        main_module,
        "_collect_meta_instagram_diagnostics",
        lambda *_args, **_kwargs: (
            [
                {
                    "record_type": META_RECORD_TYPE_FACEBOOK_PAGE,
                    "page_id": "fb-1",
                    "name": "Suite FB Page",
                }
            ],
            [
                {
                    "page_id": "fb-1",
                    "page_name": "Suite FB Page",
                    "has_page_access_token": True,
                    "has_instagram_business_account": False,
                    "has_connected_instagram_account": True,
                    "graph_status": 200,
                    "graph_error_code": None,
                    "graph_error_message": None,
                    "token_used_type": "page_token",
                }
            ],
        ),
    )
    monkeypatch.setattr(main_module, "_discover_meta_ads_accounts_for_suite", lambda *_args, **_kwargs: ([], [], None, False))

    integrations_response = client.get(
        "/integrations",
        headers=_auth_headers(refs["user_id"]),
    )

    assert integrations_response.status_code == 200
    provider_map = {item["provider"]: item for item in integrations_response.json()}
    assert set(provider_map) == {"meta_business_suite"}
    assert provider_map["meta_business_suite"]["status"] == "connected"
    assert provider_map["meta_business_suite"]["connected"] is True

    refreshed_response = client.get(
        "/integrations/meta/statuses",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"], "refresh": True},
    )

    assert refreshed_response.status_code == 200
    refreshed_map = {item["provider"]: item for item in refreshed_response.json()}
    assert refreshed_map["facebook_pages"]["status"] == "connected"
    assert refreshed_map["instagram_business"]["status"] == "connected_no_assets"
    assert refreshed_map["meta_ads"]["status"] == "connected_no_assets"
    assert refreshed_map["meta_ads"]["connected"] is True

    suite_response = client.get(
        "/integrations/meta-business-suite/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"], "refresh": True},
    )
    assert suite_response.status_code == 200
    suite_payload = suite_response.json()
    assert suite_payload["provider"] == "meta_business_suite"
    assert suite_payload["connected"] is True
    assert suite_payload["children"]["facebook_pages"]["status"] == "connected"
    assert suite_payload["children"]["instagram_business"]["status"] == "connected_no_assets"
    assert suite_payload["children"]["meta_ads"]["status"] == "connected_no_assets"

    instagram_response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"], "refresh": True},
    )
    assert instagram_response.status_code == 200
    instagram_payload = instagram_response.json()
    assert instagram_payload["connected"] is True
    assert instagram_payload["provider"] == "instagram_business"
    assert instagram_payload["status"] == "connected_no_assets"
    assert instagram_payload["asset_count"] == 0


def test_shared_suite_token_returns_connected_no_assets_for_instagram_when_no_accounts_found(client, monkeypatch):
    refs = _seed_workspace_with_suite_token()
    main_module._table_names.cache_clear()
    for key, value in (
        ("meta_ads_app_id", "meta-app-id"),
        ("meta_ads_app_secret", "meta-app-secret"),
        ("meta_ads_redirect_uri", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setattr(main_module.settings, key, value)
        monkeypatch.setattr(meta_ads_module.settings, key, value)
    monkeypatch.setattr(main_module, "_meta_ads_reporting_tables_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {
            "data": {
                "is_valid": True,
                "scopes": meta_ads_module.META_BUSINESS_SUITE_OAUTH_SCOPE.split(","),
            }
        },
    )
    monkeypatch.setattr(main_module, "_collect_meta_instagram_diagnostics", lambda *_args, **_kwargs: ([], []))

    integrations_response = client.get(
        "/integrations",
        headers=_auth_headers(refs["user_id"]),
    )

    assert integrations_response.status_code == 200
    provider_map = {item["provider"]: item for item in integrations_response.json()}
    assert set(provider_map) == {"meta_business_suite"}
    assert provider_map["meta_business_suite"]["status"] == "connected"
    assert provider_map["meta_business_suite"]["connected"] is True

    refreshed_response = client.get(
        "/integrations/meta/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"], "provider": "instagram_business", "refresh": True},
    )
    assert refreshed_response.status_code == 200
    refreshed_payload = refreshed_response.json()
    assert refreshed_payload["connected"] is True
    assert refreshed_payload["status"] == "connected_no_assets"
    assert refreshed_payload["asset_count"] == 0

    instagram_response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )
    assert instagram_response.status_code == 200
    instagram_payload = instagram_response.json()
    assert instagram_payload["connected"] is True
    assert instagram_payload["provider"] == "instagram_business"
    assert instagram_payload["status"] == "connected_no_assets"
    assert instagram_payload["asset_count"] == 0

    ads_response = client.get(
        "/integrations/meta-ads/status",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["meta_ads_integration_id"]},
    )
    assert ads_response.status_code == 200
    ads_payload = ads_response.json()
    assert ads_payload["connected"] is True
    assert ads_payload["status"] == "connected_no_assets"
    assert ads_payload["missing_scopes"] == []


def test_instagram_business_connect_without_meta_pages_env_returns_409(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    monkeypatch.setattr(meta_ads_module.settings, "meta_pages_app_id", None)
    monkeypatch.setattr(meta_ads_module.settings, "meta_pages_app_secret", None)
    monkeypatch.setattr(meta_ads_module.settings, "meta_pages_redirect_uri", None)

    response = client.get(
        "/integrations/instagram-business/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "meta_pages_config_missing"


def test_admin_meta_data_catalog_returns_actionable_details(client, monkeypatch):
    refs = _seed_admin_workspace_with_tokens()
    for key in ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (
        ("META_ADS_APP_ID", "meta-ads-app-id"),
        ("META_ADS_APP_SECRET", "meta-ads-app-secret"),
        ("META_ADS_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback"),
    ):
        monkeypatch.setenv(key, value)

    def fake_get(url, params=None, timeout=30):
        if url.endswith("/fb-1") and params and params.get("fields") == "followers_count,fan_count":
            return _FakeResponse(200, {"followers_count": 321, "fan_count": 123})
        if url.endswith("/fb-1/insights"):
            metric = params.get("metric")
            if metric == "page_posts_impressions_organic":
                return _FakeResponse(200, {"data": [{"name": metric, "values": [{"value": 10187}]}]})
            if metric == "page_post_engagements":
                return _FakeResponse(400, {"error": {"code": 100, "message": "Invalid metric"}})
            if metric == "page_actions_post_reactions_total":
                return _FakeResponse(403, {"error": {"code": 10, "message": "Permissions error"}})
            if metric == "page_views_total":
                return _FakeResponse(200, {"data": [{"name": metric, "values": [{"value": 42}]}]})
        if url.endswith("/me/adaccounts"):
            return _FakeResponse(200, {"data": []})
        return _FakeResponse(404, {"error": {"code": 404, "message": "Not found"}})

    monkeypatch.setattr(meta_data_catalog_module.requests, "get", fake_get)

    response = client.get(
        "/admin/meta-data-catalog",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "details" in payload
    assert "rows_preview" in payload

    facebook_rows = payload["details"]["facebook_pages"]
    assert any(row["db_provider"] == "meta" for row in facebook_rows)
    assert any(row["record_type"] == "facebook_page" for row in facebook_rows)
    assert any(
        row["metric_name"] == "page_posts_impressions_organic"
        and row["availability_status"] == "available"
        and row["sample_value"] == 10187
        for row in facebook_rows
    )
    assert any(
        row["metric_name"] == "page_post_engagements"
        and row["availability_status"] == "invalid_metric"
        for row in facebook_rows
    )
    assert any(
        row["metric_name"] == "page_actions_post_reactions_total"
        and row["availability_status"] == "missing_permission"
        for row in facebook_rows
    )
    assert "page_posts_impressions_organic" in payload["provider_summary"]["facebook_pages"]["recommended_report_metrics"]

    instagram_rows = payload["details"]["instagram_business"]
    assert any(row["availability_status"] == "config_missing" for row in instagram_rows)
    assert any("INSTAGRAM_APP_ID" in row["missing"] for row in instagram_rows if row["availability_status"] == "config_missing")

    meta_ads_rows = payload["details"]["meta_ads"]
    assert any(row["availability_status"] == "no_assets" for row in meta_ads_rows)


def test_report_metric_catalog_normalizes_facebook_pages_metrics_without_alias_collisions():
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "page_posts_impressions_organic") == "organic_impressions"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "page_post_engagements") == "engagement"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "page_views_total") == "page_views"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "followers_count") == "followers"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "fan_count") == "fans"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "page_views_total") != "impressions"
    assert normalize_metric_key(FACEBOOK_PAGES_PROVIDER, "page_impressions_unique") != "reach"
    assert is_metric_available(FACEBOOK_PAGES_PROVIDER, "page_impressions_unique") is False
    assert "must not be aliased" in explain_metric_availability(FACEBOOK_PAGES_PROVIDER, "page_impressions_unique")


def test_report_metric_catalog_keeps_provider_namespaces_isolated():
    facebook_catalog = get_metric_catalog(FACEBOOK_PAGES_PROVIDER)
    instagram_catalog = get_metric_catalog(INSTAGRAM_BUSINESS_PROVIDER)
    meta_ads_catalog = get_metric_catalog(META_ADS_PROVIDER)

    assert any(entry["real_metric_name"] == "page_posts_impressions_organic" for entry in facebook_catalog)
    assert all(entry["provider"] == FACEBOOK_PAGES_PROVIDER for entry in facebook_catalog)

    assert any(entry["real_metric_name"] == "reach" for entry in instagram_catalog)
    assert all(entry["status"] in {"pending_config", "pending_permission"} for entry in instagram_catalog)
    assert all(entry["provider"] == INSTAGRAM_BUSINESS_PROVIDER for entry in instagram_catalog)
    assert normalize_metric_key(INSTAGRAM_BUSINESS_PROVIDER, "page_views_total") is None

    assert any(entry["real_metric_name"] == "spend" for entry in meta_ads_catalog)
    assert any(entry["real_metric_name"] == "reach" for entry in meta_ads_catalog)
    assert all(entry["provider"] == META_ADS_PROVIDER for entry in meta_ads_catalog)


def test_report_metric_catalog_exposes_recommended_and_available_metrics_by_provider():
    facebook_available = get_available_report_metrics(FACEBOOK_PAGES_PROVIDER)
    facebook_recommended = get_recommended_report_metrics(FACEBOOK_PAGES_PROVIDER)
    instagram_recommended = get_recommended_report_metrics(INSTAGRAM_BUSINESS_PROVIDER)
    meta_ads_recommended = get_recommended_report_metrics(META_ADS_PROVIDER)

    assert {entry["real_metric_name"] for entry in facebook_available} >= {
        "page_posts_impressions_organic",
        "page_post_engagements",
        "page_views_total",
        "followers_count",
        "fan_count",
    }
    assert {entry["real_metric_name"] for entry in facebook_recommended} >= {
        "page_posts_impressions_organic",
        "page_post_engagements",
        "page_views_total",
        "page_actions_post_reactions_total",
        "followers_count",
        "fan_count",
    }
    assert any(entry["real_metric_name"] == "followers_count" for entry in instagram_recommended)
    assert any(entry["real_metric_name"] == "spend" for entry in meta_ads_recommended)
