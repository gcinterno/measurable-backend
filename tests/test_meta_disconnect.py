from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_meta_disconnect.db")
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
from app.integrations.meta_ads import encode_state
import app.main as main_module
from app.main import (
    META_RECORD_TYPE_FACEBOOK_PAGE,
    META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    _meta_page_account_external_id,
    _meta_token_account_external_id,
    app,
)
from app.models import (
    Dataset,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaPage,
    Report,
    ReportVersion,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


META_DISCONNECT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaPage.__table__,
    Dataset.__table__,
    Report.__table__,
    ReportVersion.__table__,
]


@pytest.fixture(autouse=True)
def meta_disconnect_schema():
    Base.metadata.drop_all(bind=engine, tables=META_DISCONNECT_TABLES)
    Base.metadata.create_all(bind=engine, tables=META_DISCONNECT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=META_DISCONNECT_TABLES)


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


def _seed_meta_disconnect_fixture() -> dict[str, int | str]:
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
            name="Meta",
            status="connected",
        )
        db.add(integration)
        db.flush()

        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_meta_token_account_external_id(integration.id),
            display_name="Meta token store",
        )
        selected_page_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_meta_page_account_external_id("fb-1"),
            display_name="Botanero FB",
        )
        db.add_all([token_account, selected_page_account])
        db.flush()

        db.add(
            IntegrationToken(
                account_id=token_account.id,
                workspace_id=workspace.id,
                token_type="access_token",
                access_token="meta-access-token",
                refresh_token="meta-refresh-token",
            )
        )
        db.add_all(
            [
                MetaPage(
                    integration_id=integration.id,
                    user_id=user.id,
                    record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                    page_id="fb-1",
                    name="Botanero FB",
                    page_access_token="page-token",
                ),
                MetaPage(
                    integration_id=integration.id,
                    user_id=user.id,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    page_id="ig-1",
                    parent_page_id="fb-1",
                    name="Botanero IG",
                    instagram_username="botaneroig",
                    business_name="Botanero FB",
                ),
            ]
        )

        dataset = Dataset(
            workspace_id=workspace.id,
            name="Meta dataset",
            description="Disconnect test dataset",
            data={"integration_type": "facebook_pages", "page_id": "fb-1"},
        )
        db.add(dataset)
        db.flush()

        report = Report(
            workspace_id=workspace.id,
            dataset_id=dataset.id,
            name="Historical report",
            description="{}",
        )
        db.add(report)
        db.flush()
        report_version = ReportVersion(report_id=report.id, version=1)
        db.add(report_version)
        db.commit()

        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
            "dataset_id": dataset.id,
            "report_id": report.id,
            "report_version_id": report_version.id,
        }
    finally:
        db.close()


def test_meta_disconnect_clears_token_and_status(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "meta"
    assert payload["status"] == "disconnected"
    assert payload["cleared"]["tokens"] is True
    assert payload["meta_revoke_status"] == "success"

    db = SessionLocal()
    try:
        integration = db.get(Integration, int(refs["integration_id"]))
        assert integration is not None
        assert integration.status == "disconnected"
        assert db.query(IntegrationToken).count() == 0
    finally:
        db.close()


def test_meta_reconnect_callback_failure_preserves_existing_state(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()

    state = encode_state(
        {
            "workspace_id": refs["workspace_id"],
            "user_id": refs["user_id"],
            "integration_id": refs["integration_id"],
            "integration_type": "facebook_pages",
            "reconnect": True,
            "source": "meta_pages_connect_pages",
            "callback_route": "/integrations/meta/callback-pages",
        }
    )

    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda code, redirect_uri=None: {
            "access_token": "new-meta-token",
            "_meta_http_status_code": 200,
            "_meta_raw_body": "{}",
        },
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda access_token: {"data": {"is_valid": True, "scopes": ["pages_show_list"]}},
    )
    monkeypatch.setattr(
        main_module,
        "_refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            main_module.http_error(502, "meta_fetch_failed", "Meta refresh failed.")
        ),
    )

    response = client.get(f"/integrations/meta/callback-pages?code=meta-code&state={state}")

    assert response.status_code == 200

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration is not None
        assert integration.status == "connected"

        token_account = (
            db.query(IntegrationAccount)
            .filter(IntegrationAccount.integration_id == refs["integration_id"])
            .filter(IntegrationAccount.external_account_id == _meta_token_account_external_id(refs["integration_id"]))
            .one()
        )
        tokens = (
            db.query(IntegrationToken)
            .filter(IntegrationToken.account_id == token_account.id)
            .order_by(IntegrationToken.id.asc())
            .all()
        )
        assert len(tokens) == 1
        assert tokens[0].access_token == "new-meta-token"

        pages = db.query(MetaPage).filter(MetaPage.integration_id == refs["integration_id"]).all()
        assert len(pages) == 2
    finally:
        db.close()


def test_meta_disconnect_clears_facebook_pages_cache(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    assert response.json()["cleared"]["facebook_pages"] == 1

    db = SessionLocal()
    try:
        facebook_pages = (
            db.query(MetaPage)
            .filter(MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE)
            .all()
        )
        assert facebook_pages == []
    finally:
        db.close()


def test_meta_disconnect_clears_instagram_accounts_cache(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    assert response.json()["cleared"]["instagram_accounts"] == 1

    db = SessionLocal()
    try:
        instagram_accounts = (
            db.query(MetaPage)
            .filter(MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT)
            .all()
        )
        assert instagram_accounts == []
    finally:
        db.close()


def test_meta_disconnect_keeps_existing_reports_intact(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200

    db = SessionLocal()
    try:
        assert db.get(Report, int(refs["report_id"])) is not None
        assert db.get(ReportVersion, int(refs["report_version_id"])) is not None
        assert db.get(Dataset, int(refs["dataset_id"])) is not None
    finally:
        db.close()


def test_meta_disconnect_is_idempotent(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")

    first_response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )
    second_response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["success"] is True
    assert second_response.json()["status"] == "disconnected"
    assert second_response.json()["cleared"]["tokens"] is False
    assert second_response.json()["cleared"]["facebook_pages"] == 0
    assert second_response.json()["cleared"]["instagram_accounts"] == 0


def test_pages_after_disconnect_return_empty_and_disconnected_catalog(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")
    client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    pages_response = client.get(
        "/integrations/meta/pages",
        headers=_auth_headers(int(refs["user_id"])),
        params={"integration_id": refs["integration_id"]},
    )
    catalog_response = client.get(
        "/integrations/meta/pages/catalog",
        headers=_auth_headers(int(refs["user_id"])),
        params={"integration_id": refs["integration_id"]},
    )

    assert pages_response.status_code == 200
    assert pages_response.json() == []
    assert catalog_response.status_code == 200
    assert catalog_response.json()["data"] == []
    assert catalog_response.json()["status"] == "disconnected"
    assert catalog_response.json()["connected"] is False


def test_instagram_accounts_after_disconnect_return_empty_and_disconnected_catalog(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")
    client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    accounts_response = client.get(
        "/integrations/meta/instagram-accounts",
        headers=_auth_headers(int(refs["user_id"])),
        params={"integration_id": refs["integration_id"]},
    )
    catalog_response = client.get(
        "/integrations/meta/instagram-accounts/catalog",
        headers=_auth_headers(int(refs["user_id"])),
        params={"integration_id": refs["integration_id"]},
    )

    assert accounts_response.status_code == 200
    assert accounts_response.json() == []
    assert catalog_response.status_code == 200
    assert catalog_response.json()["data"] == []
    assert catalog_response.json()["status"] == "disconnected"
    assert catalog_response.json()["connected"] is False


def test_integrations_and_account_summary_show_disconnected_after_disconnect(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")
    client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    integrations_response = client.get("/integrations", headers=_auth_headers(int(refs["user_id"])))
    summary_response = client.get("/account/summary", headers=_auth_headers(int(refs["user_id"])))

    assert integrations_response.status_code == 200
    providers = {item["provider"]: item["status"] for item in integrations_response.json()}
    assert providers["facebook_pages"] == "no_token"
    assert providers["instagram_business"] == "no_token"
    assert providers["meta_ads"] == "no_token"
    assert summary_response.status_code == 200
    assert summary_response.json()["integrations_connected_count"] == 0


def test_sync_pages_cannot_use_old_cached_selection_after_disconnect(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "success")
    client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    response = client.post(
        "/integrations/meta/sync-pages",
        headers=_auth_headers(int(refs["user_id"])),
        json={
            "integration_id": refs["integration_id"],
            "page_id": "fb-1",
            "timeframe": "last_28_days",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "meta_page_not_selected"


def test_meta_disconnect_succeeds_when_meta_revoke_fails(client, monkeypatch):
    refs = _seed_meta_disconnect_fixture()
    monkeypatch.setattr(main_module, "_revoke_meta_permissions", lambda _token: "failed")

    response = client.post(
        "/integrations/meta/disconnect",
        headers=_auth_headers(int(refs["user_id"])),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"] == "disconnected"
    assert response.json()["meta_revoke_status"] == "failed"

    db = SessionLocal()
    try:
        integration = db.get(Integration, int(refs["integration_id"]))
        assert integration is not None
        assert integration.status == "disconnected"
        assert db.query(MetaPage).count() == 0
        assert db.query(IntegrationAccount).count() == 0
    finally:
        db.close()
