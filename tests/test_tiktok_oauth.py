from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_tiktok_oauth.db")
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
from app.integrations.tiktok_ads import encode_state
import app.main as main_module
from app.main import app
from app.models import Integration, IntegrationAccount, IntegrationToken, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


TIKTOK_OAUTH_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
]


@pytest.fixture(autouse=True)
def oauth_schema():
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_OAUTH_TABLES)
    Base.metadata.create_all(bind=engine, tables=TIKTOK_OAUTH_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_OAUTH_TABLES)


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


def test_tiktok_status_works_without_env(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.setattr(main_module.settings, "tiktok_app_id", None)
    monkeypatch.setattr(main_module.settings, "tiktok_secret", None)
    monkeypatch.setattr(main_module.settings, "tiktok_redirect_uri", None)

    response = client.get(
        f"/integrations/tiktok/status?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["missing_env"] is True
    assert payload["status"] == "disconnected"


def test_tiktok_connect_returns_controlled_error_when_env_missing(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.setattr(main_module.settings, "tiktok_app_id", None)
    monkeypatch.setattr(main_module.settings, "tiktok_secret", None)
    monkeypatch.setattr(main_module.settings, "tiktok_redirect_uri", None)

    response = client.get(
        f"/integrations/tiktok/connect?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "tiktok_config_missing"


def test_tiktok_callback_complete_supports_auth_code_and_stores_accounts(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.setattr(main_module.settings, "tiktok_app_id", "app-id")
    monkeypatch.setattr(main_module.settings, "tiktok_secret", "secret")
    monkeypatch.setattr(main_module.settings, "tiktok_redirect_uri", "https://app.measurableapp.com/integrations/tiktok/callback")

    db = SessionLocal()
    try:
        integration = Integration(
            workspace_id=refs["workspace_id"],
            provider="tiktok_ads",
            name="TikTok Ads",
            status="disconnected",
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        integration_id = integration.id
    finally:
        db.close()

    state = encode_state(
        {
            "workspace_id": refs["workspace_id"],
            "user_id": refs["user_id"],
            "integration_id": integration_id,
            "source": "tiktok_ads_connect",
        }
    )

    monkeypatch.setattr(
        main_module,
        "exchange_auth_code_for_token",
        lambda **_kwargs: {
            "access_token": "tiktok-access-token",
            "refresh_token": "tiktok-refresh-token",
            "expires_in": 3600,
            "raw": {"code": 0},
        },
    )
    monkeypatch.setattr(
        main_module,
        "get_authorized_advertisers",
        lambda _token: [
            {"advertiser_id": "adv_1", "advertiser_name": "Advertiser One"},
            {"advertiser_id": "adv_2", "advertiser_name": "Advertiser Two"},
        ],
    )

    response = client.post(
        "/integrations/tiktok/callback/complete",
        headers=_auth_headers(refs["user_id"]),
        json={"auth_code": "auth-code-1", "state": state},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["advertisers_count"] == 2
    assert payload["selected_account"]["advertiser_id"] == "adv_1"
    assert "access_token" not in payload
    assert "refresh_token" not in payload

    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "connected"

        accounts = (
            db.query(IntegrationAccount)
            .filter(IntegrationAccount.integration_id == integration_id)
            .all()
        )
        external_ids = {account.external_account_id for account in accounts}
        assert "adv_1" in external_ids
        assert "adv_2" in external_ids
        assert any(external_id.startswith("__tiktok_token__:") for external_id in external_ids)

        token_account = next(
            account for account in accounts if account.external_account_id.startswith("__tiktok_token__:")
        )
        token = (
            db.query(IntegrationToken)
            .filter(IntegrationToken.account_id == token_account.id)
            .first()
        )
        assert token is not None
        assert token.access_token == "tiktok-access-token"
        assert token.refresh_token == "tiktok-refresh-token"
    finally:
        db.close()
