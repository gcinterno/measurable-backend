from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_meta_sync_all_test.db")
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
from app.errors import http_error
from app.main import app
from app.models import Integration, Subscription, User, Workspace, WorkspaceMember
from app.schemas import InstagramBusinessSyncOut, MetaPagesSyncOut
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


SYNC_ALL_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
]


@pytest.fixture(autouse=True)
def sync_schema():
    Base.metadata.drop_all(bind=engine, tables=SYNC_ALL_TABLES)
    Base.metadata.create_all(bind=engine, tables=SYNC_ALL_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=SYNC_ALL_TABLES)


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


def _seed_meta_integration() -> dict[str, int]:
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

        integration = Integration(workspace_id=workspace.id, provider="meta", name="Meta", status="connected")
        db.add(integration)
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
        }
    finally:
        db.close()


def test_sync_all_returns_partial_success_when_one_source_fails(client, monkeypatch):
    refs = _seed_meta_integration()

    def fake_sync_pages(**_kwargs):
        return MetaPagesSyncOut(
            integration_id=refs["integration_id"],
            dataset_id=101,
            dataset_file_id=201,
            page_id="fb-page-1",
            page_name="FB Page",
            status="uploaded",
            timeframe={"preset": "last_28_days"},
        )

    def fake_sync_instagram(**_kwargs):
        raise http_error(400, "meta_permissions_missing", "Missing permissions.")

    monkeypatch.setattr("app.main._run_meta_pages_sync", fake_sync_pages)
    monkeypatch.setattr("app.main._run_instagram_business_sync", fake_sync_instagram)

    response = client.post(
        "/integrations/meta/sync-all",
        headers=_auth_headers(refs["user_id"]),
        json={
            "integration_id": refs["integration_id"],
            "facebook_page_id": "fb-page-1",
            "instagram_business_account_id": "ig-1",
            "timeframe": {"preset": "last_28_days", "since": None, "until": None},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["results"]["facebook_pages"]["success"] is True
    assert payload["results"]["facebook_pages"]["dataset_id"] == 101
    assert payload["results"]["instagram_business"]["success"] is False
    assert payload["results"]["instagram_business"]["error_code"] == "meta_permissions_missing"


def test_sync_all_returns_failure_when_all_sources_fail(client, monkeypatch):
    refs = _seed_meta_integration()

    def fake_sync_pages(**_kwargs):
        raise http_error(401, "missing_token", "Meta token not found.")

    def fake_sync_instagram(**_kwargs):
        raise http_error(404, "instagram_account_not_found", "Instagram Business account not found.")

    monkeypatch.setattr("app.main._run_meta_pages_sync", fake_sync_pages)
    monkeypatch.setattr("app.main._run_instagram_business_sync", fake_sync_instagram)

    response = client.post(
        "/integrations/meta/sync-all",
        headers=_auth_headers(refs["user_id"]),
        json={
            "integration_id": refs["integration_id"],
            "facebook_page_id": "fb-page-1",
            "instagram_business_account_id": "ig-1",
            "timeframe": {"preset": "last_28_days", "since": None, "until": None},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["results"]["facebook_pages"]["success"] is False
    assert payload["results"]["instagram_business"]["success"] is False


def test_sync_all_returns_success_when_both_sources_sync(client, monkeypatch):
    refs = _seed_meta_integration()

    def fake_sync_pages(**_kwargs):
        return MetaPagesSyncOut(
            integration_id=refs["integration_id"],
            dataset_id=301,
            dataset_file_id=401,
            page_id="fb-page-1",
            page_name="FB Page",
            status="uploaded",
            timeframe={"preset": "last_28_days"},
        )

    def fake_sync_instagram(**_kwargs):
        return InstagramBusinessSyncOut(
            integration_id=refs["integration_id"],
            dataset_id=302,
            dataset_file_id=402,
            source_type="instagram_business",
            record_type="instagram_account",
            account_id="ig-1",
            account_name="IG Account",
            status="synced",
            timeframe={"preset": "last_28_days"},
        )

    monkeypatch.setattr("app.main._run_meta_pages_sync", fake_sync_pages)
    monkeypatch.setattr("app.main._run_instagram_business_sync", fake_sync_instagram)

    response = client.post(
        "/integrations/meta/sync-all",
        headers=_auth_headers(refs["user_id"]),
        json={
            "integration_id": refs["integration_id"],
            "facebook_page_id": "fb-page-1",
            "instagram_business_account_id": "ig-1",
            "timeframe": {"preset": "last_28_days", "since": None, "until": None},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["results"]["facebook_pages"]["dataset_id"] == 301
    assert payload["results"]["instagram_business"]["dataset_id"] == 302
