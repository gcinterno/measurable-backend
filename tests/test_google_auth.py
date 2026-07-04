from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
from app.integrations import instagram_business
from app.integrations import meta_ads
import app.main as main_module
from app.main import app
from app.models import (
    EmailVerificationCode,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaPage,
    ReferralConversion,
    Subscription,
    User,
    UserAttribution,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, create_oauth_state
from app.security import hash_password


AUTH_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    EmailVerificationCode.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaPage.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
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


def _auth_headers_for(email: str) -> dict[str, str]:
    user = _fetch_user(email)
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


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


def test_meta_connect_pages_uses_single_available_workspace_when_request_is_stale(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-single@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Single",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Meta Single Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        real_workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr("app.main._meta_pages_redirect_uri", lambda: "https://backend.example.com/callback")
    monkeypatch.setattr(
        "app.main.oauth_connect_pages_url",
        lambda state, redirect_uri=None, auth_type=None, scope=None, integration_type=None: (
            f"https://facebook.example.com/oauth?state={state}&redirect_uri={redirect_uri}"
            + (f"&auth_type={auth_type}" if auth_type else "")
            + (f"&scope={scope}" if scope else "")
            + (f"&integration_type={integration_type}" if integration_type else "")
        ),
    )

    response = client.get(
        "/integrations/meta/connect-pages?workspace_id=1",
        headers=_auth_headers_for("meta-single@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_url"].startswith("https://facebook.example.com/oauth?")
    assert payload["integration_id"]

    db = SessionLocal()
    try:
        integration = db.query(Integration).filter(Integration.workspace_id == real_workspace_id).one()
        assert integration.provider == "meta"
    finally:
        db.close()


def test_meta_connect_pages_rejects_invalid_workspace_when_user_has_multiple_workspaces(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-multi@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Multi",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace_a = Workspace(name="Workspace A")
        workspace_b = Workspace(name="Workspace B")
        db.add(workspace_a)
        db.add(workspace_b)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace_a.id, user_id=user.id, role="owner"))
        db.add(WorkspaceMember(workspace_id=workspace_b.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace_a.id, plan="free", status="active"))
        db.add(Subscription(workspace_id=workspace_b.id, plan="free", status="active"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr("app.main._meta_pages_redirect_uri", lambda: "https://backend.example.com/callback")
    monkeypatch.setattr(
        "app.main.oauth_connect_pages_url",
        lambda state, redirect_uri=None, auth_type=None, scope=None, integration_type=None: (
            f"https://facebook.example.com/oauth?state={state}&redirect_uri={redirect_uri}"
            + (f"&auth_type={auth_type}" if auth_type else "")
            + (f"&scope={scope}" if scope else "")
            + (f"&integration_type={integration_type}" if integration_type else "")
        ),
    )

    response = client.get(
        "/integrations/meta/connect-pages?workspace_id=99999",
        headers=_auth_headers_for("meta-multi@example.com"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workspace_access_denied"
    assert response.json()["detail"]["message"] == "Requested workspace does not belong to the authenticated user."


def test_meta_callback_pages_returns_popup_close_html_for_invalid_state(client):
    response = client.get("/integrations/meta/callback-pages?code=meta-code&state=bad-state")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "window.opener.postMessage" in body
    assert "\"MEASURABLE_META_CONNECT_ERROR\"" in body
    assert "\"provider\": \"meta\"" in body
    assert "\"http://localhost:3000\"" in body
    assert "window.close()" in body
    assert "window.location.replace" in body
    assert "We could not complete the connection." in body
    assert "Volver a Measurable" in body
    assert "facebook.com/connect/uiserver.php" not in body


def test_meta_oauth_connect_pages_url_uses_backend_callback_for_both_flows(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    default_url = meta_ads.oauth_connect_pages_url("default-state", integration_type="facebook_pages")
    instagram_url = meta_ads.oauth_connect_pages_url("instagram-state", integration_type="instagram_business")

    expected_redirect_uri = "https://api.measurableapp.com/integrations/meta/callback-pages"
    parsed_default = urlparse(default_url)
    query_default = parse_qs(parsed_default.query)
    assert query_default["redirect_uri"] == [expected_redirect_uri]
    assert query_default["scope"] == [meta_ads.FACEBOOK_PAGES_OAUTH_SCOPE]

    parsed_instagram = urlparse(instagram_url)
    query_instagram = parse_qs(parsed_instagram.query)
    assert query_instagram["redirect_uri"] == [expected_redirect_uri]
    assert query_instagram["scope"] == [meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN]


def test_meta_oauth_connect_pages_url_uses_business_login_config_when_present(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", "pages-config-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    auth_url = meta_ads.oauth_connect_pages_url("pages-state", integration_type="facebook_pages")

    parsed = urlparse(auth_url)
    query = parse_qs(parsed.query)
    assert query["config_id"] == ["pages-config-id"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["https://api.measurableapp.com/integrations/meta/callback-pages"]
    assert "scope" not in query
    assert meta_ads.get_meta_pages_auth_mode("facebook_pages") == "business_login_config_id"


def test_meta_oauth_connect_pages_url_legacy_scope_without_config(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    auth_url = meta_ads.oauth_connect_pages_url("pages-state", integration_type="facebook_pages")

    query = parse_qs(urlparse(auth_url).query)
    assert query["scope"] == [meta_ads.FACEBOOK_PAGES_OAUTH_SCOPE]
    assert "config_id" not in query
    assert meta_ads.get_meta_pages_auth_mode("facebook_pages") == "legacy_scope"


def test_meta_pages_business_config_does_not_affect_instagram_or_ads_oauth(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", "pages-config-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "meta_app_id", "meta-ads-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_app_secret", "meta-ads-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    instagram_url = meta_ads.oauth_connect_pages_url("ig-state", integration_type="instagram_business")
    ads_url = meta_ads.oauth_connect_url("ads-state", integration_type="meta_ads")

    instagram_query = parse_qs(urlparse(instagram_url).query)
    ads_query = parse_qs(urlparse(ads_url).query)
    assert "config_id" not in instagram_query
    assert instagram_query["scope"] == [meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN]
    assert "instagram_business_basic" not in instagram_query["scope"][0]
    assert "instagram_business_manage_insights" not in instagram_query["scope"][0]
    assert "ads_read" not in instagram_query["scope"][0]
    assert "config_id" not in ads_query
    assert ads_query["scope"] == [meta_ads.META_ADS_OAUTH_SCOPE]


def test_instagram_business_connect_uses_facebook_oauth_with_dedicated_endpoint(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-instagram@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Instagram",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Meta Instagram Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    response = client.get(
        f"/integrations/instagram-business/connect?workspace_id={workspace_id}",
        headers=_auth_headers_for("meta-instagram@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    parsed = urlparse(payload["auth_url"])
    query = parse_qs(parsed.query)
    state_payload = meta_ads.decode_state(query["state"][0])
    assert parsed.netloc == "www.facebook.com"
    assert parsed.path.endswith("/dialog/oauth")
    assert query["redirect_uri"] == ["https://api.measurableapp.com/integrations/meta/callback-pages"]
    assert query["scope"] == [meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN]
    assert query["response_type"] == ["code"]
    assert query["auth_type"] == ["rerequest"]
    assert state_payload["integration_type"] == "instagram_business"
    assert state_payload["source"] == "instagram_business"
    assert state_payload["provider"] == "instagram_business"
    assert state_payload["include_linked_instagram"] is True
    assert state_payload["callback_route"] == "/integrations/meta/callback-pages"
    assert payload["provider"] == "instagram_business"
    assert payload["source"] == "facebook_pages_linked_instagram"


def test_meta_connect_pages_rejects_instagram_business_alias(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-instagram-wrong-route@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Instagram Wrong Route",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Meta Instagram Wrong Route Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    response = client.get(
        f"/integrations/meta/connect-pages?workspace_id={workspace_id}&source=instagram_business&integration_type=instagram_business",
        headers=_auth_headers_for("meta-instagram-wrong-route@example.com"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "instagram_business_wrong_connector"


def test_meta_connect_pages_reconnect_uses_rerequest_and_preserves_state(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-reconnect@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Reconnect",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Meta Reconnect Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    response = client.get(
        f"/integrations/meta/connect-pages?workspace_id={workspace_id}&integration_type=facebook_pages&reconnect=true",
        headers=_auth_headers_for("meta-reconnect@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    parsed = urlparse(payload["auth_url"])
    query = parse_qs(parsed.query)
    state_payload = meta_ads.decode_state(query["state"][0])
    assert query["auth_type"] == ["rerequest"]
    assert query["scope"] == [meta_ads.FACEBOOK_PAGES_OAUTH_SCOPE]
    assert state_payload["reconnect"] is True
    assert state_payload["integration_type"] == "facebook_pages"


def test_meta_connect_pages_with_linked_instagram_keeps_facebook_oauth_scopes(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="meta-linked-instagram@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Meta Linked Instagram",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Meta Linked Instagram Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    response = client.get(
        f"/integrations/meta/connect-pages?workspace_id={workspace_id}&source=facebook_pages_with_instagram&include_linked_instagram=true",
        headers=_auth_headers_for("meta-linked-instagram@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    parsed = urlparse(payload["auth_url"])
    query = parse_qs(parsed.query)
    state_payload = meta_ads.decode_state(query["state"][0])
    assert parsed.netloc != "api.instagram.com"
    assert query["scope"] == [meta_ads.FACEBOOK_PAGES_OAUTH_SCOPE]
    assert state_payload["integration_type"] == "facebook_pages"
    assert state_payload["source"] == "facebook_pages_with_instagram"
    assert payload["source"] == "facebook_pages_with_instagram"


def test_meta_exchange_pages_code_uses_same_backend_callback(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        text = "{\"access_token\": \"token-value\"}"

        def json(self):
            return {"access_token": "token-value"}

    def fake_get(url, params=None, timeout=30):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(meta_ads.requests, "get", fake_get)

    payload = meta_ads.exchange_pages_code_for_token("meta-code")

    assert payload["access_token"] == "token-value"
    assert captured["params"]["redirect_uri"] == "https://api.measurableapp.com/integrations/meta/callback-pages"


def test_instagram_business_auth_url_uses_instagram_oauth_endpoint(monkeypatch):
    monkeypatch.setattr(instagram_business.settings, "instagram_app_id", "instagram-app-id")
    monkeypatch.setattr(instagram_business.settings, "instagram_app_secret", "instagram-app-secret")
    monkeypatch.setattr(instagram_business.settings, "instagram_redirect_uri", "https://app.measurableapp.com/integrations/instagram-business/callback")
    monkeypatch.setattr(instagram_business.settings, "api_base_url", "https://api.measurableapp.com")
    monkeypatch.setattr(instagram_business.settings, "instagram_oauth_authorize_url", "https://api.instagram.com/oauth/authorize")

    auth_url = instagram_business.build_instagram_business_auth_url("ig-state")

    parsed = urlparse(auth_url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "api.instagram.com"
    assert parsed.path == "/oauth/authorize"
    assert query["redirect_uri"] == ["https://api.measurableapp.com/integrations/instagram-business/callback"]
    assert query["scope"] == [instagram_business.INSTAGRAM_BUSINESS_OAUTH_SCOPE]
    assert query["response_type"] == ["code"]


def test_instagram_business_connect_returns_facebook_pages_scope(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-business@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Business",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Business Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_config_id", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")

    response = client.get(
        f"/integrations/instagram-business/connect?workspace_id={workspace_id}",
        headers=_auth_headers_for("instagram-business@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    parsed = urlparse(payload["auth_url"])
    query = parse_qs(parsed.query)
    state_payload = meta_ads.decode_state(query["state"][0])
    assert parsed.netloc == "www.facebook.com"
    assert "instagram.com" not in payload["auth_url"]
    assert payload["scope"] == meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN
    assert query["scope"] == [meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN]
    assert "instagram_business_basic" not in query["scope"][0]
    assert "instagram_business_manage_insights" not in query["scope"][0]
    assert "ads_read" not in query["scope"][0]
    assert state_payload["integration_type"] == "instagram_business"
    assert state_payload["callback_route"] == "/integrations/meta/callback-pages"


def test_instagram_business_facebook_callback_connects_linked_accounts(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-facebook-callback@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Facebook Callback",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Facebook Callback Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="disconnected",
        )
        db.add(integration)
        db.commit()
        user_id = user.id
        workspace_id = workspace.id
        integration_id = integration.id
    finally:
        db.close()

    scopes = meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN.split(",")
    state = meta_ads.encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "integration_id": integration_id,
            "integration_type": "instagram_business",
            "source": "instagram_business",
            "provider": "instagram_business",
            "include_linked_instagram": True,
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")
    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "meta-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": scopes}},
    )
    monkeypatch.setattr(
        main_module,
        "list_pages",
        lambda *_args, **_kwargs: [
            {
                "id": "fb-page-1",
                "name": "Facebook Page",
                "access_token": "page-token",
            }
        ],
    )
    captured_page_lookup: dict[str, str] = {}

    def fake_fetch_page_info(token, page_id, *, fields="id,name"):
        captured_page_lookup["token"] = token
        captured_page_lookup["page_id"] = page_id
        captured_page_lookup["fields"] = fields
        return {
            "id": page_id,
            "name": "Facebook Page",
            "access_token": "page-token",
            "_meta_http_status_code": 200,
            "instagram_business_account": {
                "id": "ig-1",
                "username": "brand",
                "name": "Brand IG",
                "profile_picture_url": "https://example.com/ig.jpg",
                "followers_count": 123,
                "media_count": 45,
            },
            "connected_instagram_account": {
                "id": "ig-1",
                "username": "brand",
                "name": "Brand IG",
                "profile_picture_url": "https://example.com/ig.jpg",
            },
        }

    monkeypatch.setattr(main_module, "fetch_page_info_with_metadata", fake_fetch_page_info)
    monkeypatch.setattr(
        main_module,
        "_fetch_instagram_user_details",
        lambda **_kwargs: (
            {"id": "ig-1", "username": "brand", "name": "Brand IG"},
            None,
        ),
    )

    response = client.get(f"/integrations/meta/callback-pages?code=meta-code&state={state}")

    assert response.status_code == 200
    assert "\"provider\": \"instagram_business\"" in response.text
    assert "\"status\": \"connected\"" in response.text
    assert captured_page_lookup["token"] == "page-token"
    assert captured_page_lookup["page_id"] == "fb-page-1"
    assert "access_token" in captured_page_lookup["fields"]
    assert "instagram_business_account" in captured_page_lookup["fields"]
    assert "connected_instagram_account" in captured_page_lookup["fields"]
    assert "followers_count" in captured_page_lookup["fields"]
    assert "media_count" in captured_page_lookup["fields"]
    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.provider == "instagram_business"
        assert integration.status == "connected"
        assert db.query(Integration).filter(Integration.workspace_id == workspace_id, Integration.provider == "meta").count() == 0
        instagram_record = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration_id,
                MetaPage.record_type == "instagram_account",
                MetaPage.page_id == "ig-1",
            )
            .one()
        )
        assert instagram_record.instagram_username == "brand"
        assert instagram_record.parent_page_id == "fb-page-1"
        assert instagram_record.business_name == "Facebook Page"
    finally:
        db.close()


def test_instagram_business_facebook_callback_without_linked_accounts_does_not_connect(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-facebook-empty@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Facebook Empty",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Facebook Empty Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="disconnected",
        )
        db.add(integration)
        db.commit()
        user_id = user.id
        workspace_id = workspace.id
        integration_id = integration.id
    finally:
        db.close()

    scopes = meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN.split(",")
    state = meta_ads.encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "integration_id": integration_id,
            "integration_type": "instagram_business",
            "source": "instagram_business",
            "provider": "instagram_business",
            "include_linked_instagram": True,
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")
    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "meta-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": scopes}},
    )
    monkeypatch.setattr(
        main_module,
        "list_pages",
        lambda *_args, **_kwargs: [{"id": "fb-page-1", "name": "Facebook Page"}],
    )
    monkeypatch.setattr(main_module, "_fetch_instagram_business_account_for_page", lambda **_kwargs: None)

    response = client.get(f"/integrations/meta/callback-pages?code=meta-code&state={state}")

    assert response.status_code == 200
    assert "needs_page_ig_link" in response.text
    assert "Facebook authorization succeeded, but no Instagram Business accounts linked to the selected Pages were found." in response.text
    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "needs_page_ig_link"
        instagram_count = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration_id,
                MetaPage.record_type == "instagram_account",
            )
            .count()
        )
        assert instagram_count == 0
        assert db.query(Integration).filter(Integration.workspace_id == workspace_id, Integration.provider == "meta").count() == 0
    finally:
        db.close()


def test_instagram_business_facebook_callback_connected_instagram_account_without_business_account_returns_needs_business_or_creator_account(
    client,
    monkeypatch,
):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-facebook-connected-only@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Facebook Connected Only",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Facebook Connected Only Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="disconnected",
        )
        db.add(integration)
        db.commit()
        user_id = user.id
        workspace_id = workspace.id
        integration_id = integration.id
    finally:
        db.close()

    scopes = meta_ads.INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN.split(",")
    state = meta_ads.encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "integration_id": integration_id,
            "integration_type": "instagram_business",
            "source": "instagram_business",
            "provider": "instagram_business",
            "include_linked_instagram": True,
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")
    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "meta-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": scopes}},
    )
    monkeypatch.setattr(
        main_module,
        "list_pages",
        lambda *_args, **_kwargs: [
            {
                "id": "fb-page-1",
                "name": "Facebook Page",
                "access_token": "page-token",
            }
        ],
    )

    captured_page_lookup: dict[str, str] = {}

    def fake_fetch_page_info(token, page_id, *, fields="id,name"):
        captured_page_lookup["token"] = token
        captured_page_lookup["page_id"] = page_id
        captured_page_lookup["fields"] = fields
        return {
            "id": page_id,
            "name": "Facebook Page",
            "access_token": "page-token",
            "_meta_http_status_code": 200,
            "connected_instagram_account": {
                "id": "ig-connected-1",
                "username": "brand_connected",
                "name": "Brand Connected",
                "profile_picture_url": "https://example.com/ig-connected.jpg",
            },
        }

    monkeypatch.setattr(main_module, "fetch_page_info_with_metadata", fake_fetch_page_info)

    response = client.get(f"/integrations/meta/callback-pages?code=meta-code&state={state}")

    assert response.status_code == 200
    assert "needs_business_or_creator_account" in response.text
    assert "Instagram account found, but it is not available as an Instagram Business account for reporting yet." in response.text
    assert "connected_instagram_account" in captured_page_lookup["fields"]
    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "needs_business_or_creator_account"
        instagram_count = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration_id,
                MetaPage.record_type == "instagram_account",
            )
            .count()
        )
        assert instagram_count == 0
    finally:
        db.close()


def test_instagram_business_facebook_callback_missing_scopes_returns_needs_permission(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-facebook-permission@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Facebook Permission",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Facebook Permission Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        integration = Integration(
            workspace_id=workspace.id,
            provider="instagram_business",
            name="Instagram Business",
            status="disconnected",
        )
        db.add(integration)
        db.commit()
        user_id = user.id
        workspace_id = workspace.id
        integration_id = integration.id
    finally:
        db.close()

    state = meta_ads.encode_state(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "integration_id": integration_id,
            "integration_type": "instagram_business",
            "source": "instagram_business",
            "provider": "instagram_business",
            "include_linked_instagram": True,
            "callback_route": "/integrations/meta/callback-pages",
        }
    )
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", "meta-pages-app-id")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", "meta-pages-app-secret")
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", "https://app.measurableapp.com/integrations/meta/callback")
    monkeypatch.setattr(meta_ads.settings, "api_base_url", "https://api.measurableapp.com")
    monkeypatch.setattr(
        main_module,
        "exchange_pages_code_for_token",
        lambda _code, *, redirect_uri=None: {"access_token": "meta-token"},
    )
    monkeypatch.setattr(
        main_module,
        "debug_token",
        lambda _token: {"data": {"is_valid": True, "scopes": ["public_profile", "pages_show_list"]}},
    )

    response = client.get(f"/integrations/meta/callback-pages?code=meta-code&state={state}")

    assert response.status_code == 200
    assert "needs_permission" in response.text
    assert "pages_read_engagement" in response.text
    db = SessionLocal()
    try:
        integration = db.get(Integration, integration_id)
        assert integration is not None
        assert integration.status == "needs_permission"
    finally:
        db.close()


def test_instagram_business_connect_returns_controlled_error_when_meta_pages_not_configured(client, monkeypatch):
    db = SessionLocal()
    try:
        user = User(
            email="instagram-missing-config@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Instagram Missing Config",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(name="Instagram Missing Config Workspace")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="free", status="active"))
        db.commit()
        workspace_id = workspace.id
    finally:
        db.close()

    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_id", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_app_secret", None)
    monkeypatch.setattr(meta_ads.settings, "meta_pages_redirect_uri", None)

    response = client.get(
        f"/integrations/instagram-business/connect?workspace_id={workspace_id}",
        headers=_auth_headers_for("instagram-missing-config@example.com"),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "meta_pages_config_missing"


def test_instagram_business_callback_maps_insufficient_developer_role_to_friendly_message(client):
    response = client.get(
        "/integrations/instagram-business/callback",
        params={
            "error": "access_denied",
            "error_reason": "Insufficient Developer Role",
            "error_description": "Insufficient Developer Role",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "\"provider\": \"instagram_business\"" in body
    assert "\"error\": \"insufficient_developer_role\"" in body
    assert "Instagram could not be connected because this Instagram account is not added as a tester/developer" in body


def test_meta_callback_pages_returns_clean_html_when_query_params_are_missing(client):
    response = client.get("/integrations/meta/callback-pages")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "window.opener.postMessage" in body
    assert "\"MEASURABLE_META_CONNECT_ERROR\"" in body
    assert "The Meta connection could not be verified." in body
    assert "window.close()" in body
