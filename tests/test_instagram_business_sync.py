from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_instagram_sync_test.db")
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
from app.main import META_RECORD_TYPE_INSTAGRAM_ACCOUNT, app
from app.models import Integration, MetaPage, Subscription, User, Workspace, WorkspaceMember
from app.schemas import MetaPagesSyncOut
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


SYNC_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    MetaPage.__table__,
]


@pytest.fixture(autouse=True)
def sync_schema():
    Base.metadata.drop_all(bind=engine, tables=SYNC_TABLES)
    Base.metadata.create_all(bind=engine, tables=SYNC_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=SYNC_TABLES)


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


def _seed_instagram_integration() -> dict[str, int | str]:
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
        db.flush()

        instagram_record = MetaPage(
            integration_id=integration.id,
            user_id=user.id,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            page_id="17841400000000000",
            parent_page_id="1234567890",
            name="IG Account",
            instagram_username="igaccount",
            business_name="Facebook Page Parent",
        )
        db.add(instagram_record)
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
            "instagram_account_id": instagram_record.page_id,
        }
    finally:
        db.close()


def test_sync_instagram_business_does_not_require_selected_facebook_page(client, monkeypatch):
    refs = _seed_instagram_integration()
    captured: dict[str, str | int | None] = {}

    def fake_sync_meta_instagram_account(*, db, integration, selected_page, selected_meta_record, timeframe_config, current_user):
        captured["integration_id"] = integration.id
        captured["selected_external_account_id"] = selected_page.external_account_id
        captured["selected_meta_record_type"] = selected_meta_record.record_type
        captured["selected_meta_record_id"] = selected_meta_record.page_id
        return MetaPagesSyncOut(
            integration_id=integration.id,
            dataset_id=321,
            dataset_file_id=654,
            page_id=selected_meta_record.page_id,
            page_name=selected_meta_record.name,
            status="uploaded",
            timeframe=timeframe_config,
        )

    monkeypatch.setattr("app.main._sync_meta_instagram_account", fake_sync_meta_instagram_account)

    response = client.post(
        "/integrations/meta/sync-instagram-business",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "integration_id": refs["integration_id"],
            "workspace_id": refs["workspace_id"],
            "instagram_account_id": refs["instagram_account_id"],
            "timeframe": "last_28_days",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_id"] == 321
    assert payload["dataset_file_id"] == 654
    assert payload["source_type"] == "instagram_business"
    assert payload["record_type"] == "instagram_account"
    assert payload["account_id"] == refs["instagram_account_id"]
    assert payload["account_name"] == "IG Account"
    assert payload["status"] == "synced"

    assert captured["integration_id"] == refs["integration_id"]
    assert captured["selected_meta_record_type"] == "instagram_account"
    assert captured["selected_meta_record_id"] == refs["instagram_account_id"]
    assert str(captured["selected_external_account_id"]).startswith("__meta_page__:")
