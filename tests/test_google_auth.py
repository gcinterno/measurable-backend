from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB_PATH = Path("/tmp/measurable_google_auth_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "google-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://testserver/auth/google/callback")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.deps import get_db
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import EmailVerificationCode, Subscription, User, Workspace, WorkspaceMember
from app.security import create_oauth_state
from app.security import hash_password


AUTH_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    EmailVerificationCode.__table__,
]


@pytest.fixture(autouse=True)
def auth_schema():
    Base.metadata.drop_all(bind=engine, tables=AUTH_TABLES)
    Base.metadata.create_all(bind=engine, tables=AUTH_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=AUTH_TABLES)


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


def _fetch_user(email: str) -> User:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one()
    finally:
        db.close()


def test_google_start_redirects_to_consent(client):
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "accounts.google.com/o/oauth2/v2/auth" in location
    assert "scope=openid+email+profile" in location
    assert "state=" in location


def test_google_callback_creates_new_user_and_sets_cookie(client, monkeypatch):
    monkeypatch.setattr(
        "app.main._exchange_google_code_for_tokens",
        lambda code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        "app.main._verify_google_id_token",
        lambda token: {
            "email": "new-google@example.com",
            "name": "New Google User",
            "sub": "google-sub-new",
            "picture": "https://example.com/avatar.png",
            "email_verified": True,
        },
    )

    state = create_oauth_state(purpose="google_oauth")
    response = client.get(
        f"/auth/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login#")
    assert "access_token=" in response.headers["location"]
    assert "session=expired" not in response.headers["location"]
    assert "access_token=" in response.headers.get("set-cookie", "")

    user = _fetch_user("new-google@example.com")
    assert user.google_sub == "google-sub-new"
    assert user.email_verified is True
    assert user.auth_provider == "google"
    assert user.last_login_at is not None
    assert user.logo_url == "https://example.com/avatar.png"
    assert user.onboarding_completed is False
    assert user.user_type is None
    assert user.goals == []
    assert user.platforms == []

    me = client.get("/auth/me")
    assert me.status_code == 200
    me_json = me.json()
    assert me_json["email"] == "new-google@example.com"
    assert me_json["is_admin"] is False


def test_google_callback_links_existing_user_by_email(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="existing@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Existing User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Workspace de Existing User")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main._exchange_google_code_for_tokens",
        lambda code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        "app.main._verify_google_id_token",
        lambda token: {
            "email": "existing@example.com",
            "name": "Existing Google Name",
            "sub": "google-sub-existing",
            "picture": "https://example.com/new-avatar.png",
            "email_verified": True,
        },
    )

    state = create_oauth_state(purpose="google_oauth")
    response = client.get(
        f"/auth/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login#")
    assert "session=expired" not in response.headers["location"]

    user = _fetch_user("existing@example.com")
    assert user.google_sub == "google-sub-existing"
    assert user.email_verified is True
    assert user.auth_provider == "google"


def test_google_callback_invalid_state_fails(client):
    response = client.get("/auth/google/callback?code=google-code&state=bad-state", follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_state"


def test_logout_clears_access_token_cookie(client, monkeypatch):
    monkeypatch.setattr(
        "app.main._exchange_google_code_for_tokens",
        lambda code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        "app.main._verify_google_id_token",
        lambda token: {
            "email": "logout@example.com",
            "name": "Logout User",
            "sub": "google-sub-logout",
            "picture": "https://example.com/avatar.png",
            "email_verified": True,
        },
    )

    state = create_oauth_state(purpose="google_oauth")
    callback = client.get(
        f"/auth/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert "access_token=" in callback.headers["location"]

    me_before = client.get("/auth/me")
    assert me_before.status_code == 200

    logout = client.post("/auth/logout", follow_redirects=False)
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    set_cookie = logout.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    me_after = client.get("/auth/me")
    assert me_after.status_code == 401


def test_google_callback_creates_workspace_for_existing_user_without_membership(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="orphan@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Orphan User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.main._exchange_google_code_for_tokens",
        lambda code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        "app.main._verify_google_id_token",
        lambda token: {
            "email": "orphan@example.com",
            "name": "Orphan User",
            "sub": "google-sub-orphan",
            "picture": "https://example.com/orphan-avatar.png",
            "email_verified": True,
        },
    )

    state = create_oauth_state(purpose="google_oauth")
    response = client.get(
        f"/auth/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login#")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "orphan@example.com").one()
        membership = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).one()
        subscription = db.query(Subscription).filter(Subscription.workspace_id == membership.workspace_id).one()
        assert membership.role == "owner"
        assert subscription.plan == "free"
        assert subscription.status == "active"
    finally:
        db.close()
