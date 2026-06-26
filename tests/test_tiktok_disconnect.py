from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_tiktok_disconnect.db")
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
from app.main import (
    _tiktok_selected_advertiser_external_id,
    _tiktok_token_account_external_id,
    app,
)
from app.models import Dataset, Integration, IntegrationAccount, IntegrationToken, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


TIKTOK_DISCONNECT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    Dataset.__table__,
]


@pytest.fixture(autouse=True)
def disconnect_schema():
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_DISCONNECT_TABLES)
    Base.metadata.create_all(bind=engine, tables=TIKTOK_DISCONNECT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=TIKTOK_DISCONNECT_TABLES)


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


def _seed_tiktok_disconnect_fixture() -> dict[str, int]:
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
        db.add(
            Dataset(
                workspace_id=workspace.id,
                name="TikTok dataset",
                description="Historical dataset",
                data={"integration_type": "tiktok_ads", "account_id": "adv_123"},
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


def test_tiktok_disconnect_clears_tokens_and_selected_marker_but_keeps_history(client):
    refs = _seed_tiktok_disconnect_fixture()

    response = client.post(
        "/integrations/tiktok/disconnect",
        headers=_auth_headers(refs["user_id"]),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] == "disconnected"
    assert payload["tokens_cleared"] is True
    assert payload["selected_account_cleared"] is True

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration is not None
        assert integration.status == "disconnected"

        tokens = db.query(IntegrationToken).all()
        assert tokens == []

        advertiser_accounts = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == refs["integration_id"],
                IntegrationAccount.external_account_id == "adv_123",
            )
            .all()
        )
        assert len(advertiser_accounts) == 1

        selected_markers = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == refs["integration_id"],
                IntegrationAccount.external_account_id.like("__tiktok_selected__:%"),
            )
            .all()
        )
        assert selected_markers == []

        datasets = db.query(Dataset).all()
        assert len(datasets) == 1
        assert datasets[0].data["integration_type"] == "tiktok_ads"
    finally:
        db.close()
