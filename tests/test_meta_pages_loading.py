from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_meta_pages_loading.db")
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
from app.integrations import meta_ads as meta_ads_module
import app.main as main_module
from app.main import (
    META_PAGES_CACHE_TTL,
    META_RECORD_TYPE_FACEBOOK_PAGE,
    META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    app,
)
from app.models import Integration, MetaPage, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


META_LOADING_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    MetaPage.__table__,
]


@pytest.fixture(autouse=True)
def meta_loading_schema():
    Base.metadata.drop_all(bind=engine, tables=META_LOADING_TABLES)
    Base.metadata.create_all(bind=engine, tables=META_LOADING_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=META_LOADING_TABLES)


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


def _seed_meta_fixture() -> dict[str, int]:
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
        return {"user_id": user.id, "workspace_id": workspace.id, "integration_id": integration.id}
    finally:
        db.close()


def _create_meta_page(
    *,
    integration_id: int,
    user_id: int,
    record_type: str,
    page_id: str,
    name: str,
    updated_at: datetime | None = None,
    instagram_username: str | None = None,
) -> MetaPage:
    page = MetaPage(
        integration_id=integration_id,
        user_id=user_id,
        record_type=record_type,
        page_id=page_id,
        name=name,
        instagram_username=instagram_username,
    )
    if updated_at is not None:
        page.updated_at = updated_at
    return page


def test_get_pages_returns_cached_results_without_calling_meta(client, monkeypatch):
    refs = _seed_meta_fixture()
    db = SessionLocal()
    try:
        db.add(
            _create_meta_page(
                integration_id=refs["integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-1",
                name="Botanero NL",
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live refresh should not run")),
    )

    response = client.get(
        "/integrations/meta/pages",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "Botanero NL"
    assert payload[0]["source"] == "cached"
    assert payload[0]["cache_status"] == "cached"


def test_pages_catalog_with_stale_cache_returns_cached_stale(client, monkeypatch):
    refs = _seed_meta_fixture()
    stale_timestamp = datetime.now(timezone.utc) - META_PAGES_CACHE_TTL - timedelta(minutes=5)
    db = SessionLocal()
    try:
        db.add(
            _create_meta_page(
                integration_id=refs["integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-1",
                name="Old Cached Page",
                updated_at=stale_timestamp,
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale cache should not auto refresh")),
    )

    response = client.get(
        "/integrations/meta/pages/catalog",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cached_stale"
    assert payload["has_cached_data"] is True
    assert payload["refresh_recommended"] is True
    assert payload["count"] == 1


def test_refresh_pages_returns_counts(client, monkeypatch):
    refs = _seed_meta_fixture()

    monkeypatch.setattr("app.main._get_meta_access_token", lambda db, integration: "secret-token")

    def fake_refresh(*_args, **_kwargs):
        facebook_page = _create_meta_page(
            integration_id=refs["integration_id"],
            user_id=refs["user_id"],
            record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
            page_id="fb-1",
            name="Botanero NL",
            updated_at=datetime.now(timezone.utc),
        )
        instagram_page = _create_meta_page(
            integration_id=refs["integration_id"],
            user_id=refs["user_id"],
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            page_id="ig-1",
            name="Botanero NL",
            instagram_username="botaneronl",
            updated_at=datetime.now(timezone.utc),
        )
        return [facebook_page, instagram_page], [{"page_name": "Botanero NL"}], [facebook_page]

    monkeypatch.setattr("app.main._refresh_meta_pages_from_live_graph", fake_refresh)

    response = client.post(
        "/integrations/meta/refresh-pages",
        headers=_auth_headers(refs["user_id"]),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["facebook_pages_count"] == 1
    assert payload["instagram_accounts_count"] == 1
    assert payload["duration_ms"] >= 0


def test_refresh_pages_timeout_returns_controlled_error(client, monkeypatch):
    refs = _seed_meta_fixture()
    monkeypatch.setattr("app.main._get_meta_access_token", lambda db, integration: "secret-token")
    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
    )

    response = client.post(
        "/integrations/meta/refresh-pages",
        headers=_auth_headers(refs["user_id"]),
        json={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["code"] == "META_REFRESH_TIMEOUT"


def test_pages_endpoint_supports_limit_offset_and_search(client, monkeypatch):
    refs = _seed_meta_fixture()
    db = SessionLocal()
    try:
        for index in range(120):
            db.add(
                _create_meta_page(
                    integration_id=refs["integration_id"],
                    user_id=refs["user_id"],
                    record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                    page_id=f"fb-{index}",
                    name=f"Page {index}",
                    updated_at=datetime.now(timezone.utc),
                )
            )
        db.add(
            _create_meta_page(
                integration_id=refs["integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-botanero",
                name="Botanero NL",
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live refresh should not run")),
    )

    limited = client.get(
        "/integrations/meta/pages",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["integration_id"], "limit": 50, "offset": 10},
    )
    assert limited.status_code == 200
    assert len(limited.json()) == 50

    searched = client.get(
        "/integrations/meta/pages/catalog",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["integration_id"], "search": "botanero"},
    )
    assert searched.status_code == 200
    searched_payload = searched.json()
    assert searched_payload["count"] == 1
    assert searched_payload["data"][0]["name"] == "Botanero NL"


def test_refresh_logs_do_not_include_access_token(client, monkeypatch, caplog):
    refs = _seed_meta_fixture()
    monkeypatch.setattr("app.main._get_meta_access_token", lambda db, integration: "super-secret-token-value")
    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: ([], [], []),
    )

    with caplog.at_level("WARNING"):
        response = client.post(
            "/integrations/meta/refresh-pages",
            headers=_auth_headers(refs["user_id"]),
            json={"integration_id": refs["integration_id"]},
        )

    assert response.status_code == 200
    assert "super-secret-token-value" not in caplog.text


def test_facebook_pages_scopes_include_business_management_only(client):
    assert "business_management" in meta_ads_module.FACEBOOK_PAGES_SCOPES
    assert "instagram_business_basic" not in meta_ads_module.FACEBOOK_PAGES_SCOPES
    assert "instagram_business_manage_insights" not in meta_ads_module.FACEBOOK_PAGES_SCOPES
    assert "ads_read" not in meta_ads_module.FACEBOOK_PAGES_SCOPES


def test_refresh_pages_saves_all_pages_returned_from_graph(client, monkeypatch):
    refs = _seed_meta_fixture()
    graph_pages = [
        {"id": f"fb-{index}", "name": f"Page {index}", "access_token": f"page-token-{index}"}
        for index in range(5)
    ]
    monkeypatch.setattr(main_module, "list_pages", lambda *_args, **_kwargs: graph_pages)
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "pages_show_list"]}},
    )
    monkeypatch.setattr(main_module, "_fetch_instagram_business_account_for_page", lambda **_kwargs: None)

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration is not None
        cached_pages, diagnostics, facebook_pages = main_module._refresh_meta_pages_from_live_graph(
            db,
            integration,
            access_token="meta-token",
            user_id=refs["user_id"],
            selected_integration_type="facebook_pages",
            context="test_direct_only",
        )
        assert len(facebook_pages) == 5
        assert len(cached_pages) == 5
        assert {page.page_id for page in facebook_pages} == {f"fb-{index}" for index in range(5)}
        saved_log = next(item for item in diagnostics if item.get("_facebook_pages_discovery_summary") is True)
        assert saved_log["total_pages_count"] == 5
    finally:
        db.close()


def test_refresh_pages_does_not_filter_non_personal_valid_pages(client, monkeypatch):
    refs = _seed_meta_fixture()
    monkeypatch.setattr(
        main_module,
        "list_pages",
        lambda *_args, **_kwargs: [
            {
                "id": "fb-client-1",
                "name": "Client Managed Page",
                "access_token": "page-client-1",
                "business_name": "Client Portfolio",
            },
            {
                "id": "fb-client-2",
                "name": "Agency Assigned Page",
                "access_token": "page-client-2",
                "business": {"name": "Agency Portfolio"},
            },
        ],
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "pages_show_list"]}},
    )
    monkeypatch.setattr(main_module, "_fetch_instagram_business_account_for_page", lambda **_kwargs: None)

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration is not None
        cached_pages, diagnostics, facebook_pages = main_module._refresh_meta_pages_from_live_graph(
            db,
            integration,
            access_token="meta-token",
            user_id=refs["user_id"],
            selected_integration_type="facebook_pages",
            context="test_client_pages",
        )
        assert {page.page_id for page in facebook_pages} == {"fb-client-1", "fb-client-2"}
        assert all(page.page_access_token for page in facebook_pages)
        assert {page.business_name for page in facebook_pages} == {"Client Portfolio", "Agency Portfolio"}
    finally:
        db.close()


def test_refresh_pages_does_not_require_instagram_to_save_facebook_pages(client, monkeypatch):
    refs = _seed_meta_fixture()
    monkeypatch.setattr(
        main_module,
        "list_pages",
        lambda *_args, **_kwargs: [
            {"id": "fb-1", "name": "Page Without IG", "access_token": "page-1"},
            {"id": "fb-2", "name": "Second Page Without IG", "access_token": "page-2"},
        ],
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "pages_show_list"]}},
    )
    monkeypatch.setattr(main_module, "_fetch_instagram_business_account_for_page", lambda **_kwargs: None)

    db = SessionLocal()
    try:
        integration = db.get(Integration, refs["integration_id"])
        assert integration is not None
        cached_pages, diagnostics, facebook_pages = main_module._refresh_meta_pages_from_live_graph(
            db,
            integration,
            access_token="meta-token",
            user_id=refs["user_id"],
            selected_integration_type="facebook_pages",
            context="test_no_instagram_required",
        )
        instagram_records = [page for page in cached_pages if page.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT]
        assert len(facebook_pages) == 2
        assert len(instagram_records) == 0
        assert [page.name for page in facebook_pages] == ["Page Without IG", "Second Page Without IG"]
        assert any(item.get("page_name") == "Page Without IG" for item in diagnostics)
    finally:
        db.close()


def test_pages_catalog_exposes_legacy_discovery_summary(client, monkeypatch):
    refs = _seed_meta_fixture()
    db = SessionLocal()
    try:
        db.add(
            _create_meta_page(
                integration_id=refs["integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-1",
                name="Saved Page 1",
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            _create_meta_page(
                integration_id=refs["integration_id"],
                user_id=refs["user_id"],
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-2",
                name="Saved Page 2",
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(main_module, "_get_meta_access_token", lambda *_args, **_kwargs: "meta-token")
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "pages_show_list"]}},
    )
    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live refresh should not run")),
    )

    response = client.get(
        "/integrations/meta/pages/catalog",
        headers=_auth_headers(refs["user_id"]),
        params={"integration_id": refs["integration_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["direct_pages_count"] == 2
    assert payload["business_pages_count"] == 0
    assert payload["total_pages_count"] == 2
    assert payload["business_discovery_status"] == "legacy_graph_accounts"
