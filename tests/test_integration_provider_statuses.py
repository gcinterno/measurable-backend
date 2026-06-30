from __future__ import annotations

import os
from pathlib import Path

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
import app.main as main_module
from app.main import (
    META_RECORD_TYPE_FACEBOOK_PAGE,
    META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    app,
)
from app.models import Integration, MetaPage, Subscription, User, Workspace, WorkspaceMember
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


def test_instagram_business_status_endpoint_never_returns_404(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "provider": "instagram_business",
        "status": "disconnected",
    }


def test_meta_ads_connect_without_config_returns_controlled_error(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    for key in (
        "META_ADS_APP_ID",
        "META_ADS_APP_SECRET",
        "META_ADS_REDIRECT_URI",
        "META_APP_ID",
        "META_APP_SECRET",
        "META_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    for key in (
        "meta_ads_app_id",
        "meta_ads_app_secret",
        "meta_ads_redirect_uri",
        "meta_app_id",
        "meta_app_secret",
        "meta_redirect_uri",
    ):
        monkeypatch.setattr(main_module.settings, key, None)
        monkeypatch.setattr(meta_ads_module.settings, key, None)

    response = client.get(
        "/integrations/meta-ads/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "meta_ads_not_configured",
        "missing": [
            "META_ADS_APP_ID",
            "META_APP_ID",
            "META_ADS_APP_SECRET",
            "META_APP_SECRET",
            "META_ADS_REDIRECT_URI",
            "META_REDIRECT_URI",
        ],
        "message": "Meta Ads OAuth is not fully configured.",
    }


def test_integrations_returns_independent_provider_states(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    provider_map = {item["provider"]: item for item in payload}

    assert "meta" not in provider_map
    assert provider_map["facebook_pages"]["status"] == "connected"
    assert provider_map["instagram_business"]["status"] == "disconnected"
    assert provider_map["meta_ads"]["status"] == "disconnected"


def test_linked_instagram_discovery_does_not_mark_instagram_business_connected(client):
    refs = _seed_workspace_with_legacy_meta()

    response = client.get(
        "/integrations/instagram-business/status",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_instagram_business_connect_without_env_returns_409(client, monkeypatch):
    refs = _seed_workspace_with_legacy_meta()
    monkeypatch.setattr(instagram_business_module.settings, "instagram_app_id", None)
    monkeypatch.setattr(instagram_business_module.settings, "instagram_app_secret", None)
    monkeypatch.setattr(instagram_business_module.settings, "instagram_redirect_uri", None)

    response = client.get(
        "/integrations/instagram-business/connect",
        headers=_auth_headers(refs["user_id"]),
        params={"workspace_id": refs["workspace_id"]},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "instagram_business_not_configured",
        "missing": ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_REDIRECT_URI"],
    }
