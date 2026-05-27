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
