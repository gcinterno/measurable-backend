from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

TEST_DB_PATH = Path("/tmp/measurable_meta_capi_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
from app.integrations import meta_capi
from app.main import app
from app.models import User
from app.security import create_access_token, hash_password


META_CAPI_TABLES = [User.__table__]


@pytest.fixture(autouse=True)
def meta_capi_schema():
    Base.metadata.drop_all(bind=engine, tables=META_CAPI_TABLES)
    Base.metadata.create_all(bind=engine, tables=META_CAPI_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=META_CAPI_TABLES)


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


def _seed_user() -> User:
    db = SessionLocal()
    try:
        user = User(
            email="alice@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Alice Example",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def test_hash_email_normalization():
    assert (
        meta_capi._hash_email(" Alice@Example.com ")
        == "ff8d9819fc0e12bf0d24892e45987e249a28dce836a85cad60e28eaaa8c6d976"
    )


def test_send_meta_capi_event_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(meta_capi.settings, "meta_capi_enabled", False)
    monkeypatch.setattr(meta_capi.settings, "meta_capi_pixel_id", "pixel-123")
    monkeypatch.setattr(meta_capi.settings, "meta_capi_access_token", "token-123")
    monkeypatch.setattr(
        meta_capi.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requests.post should not be called")),
    )

    sent = meta_capi.send_meta_capi_event(
        event_name="CompleteRegistration",
        event_id="event-123",
        event_source_url="https://measurableapp.com/register",
        user_email="alice@example.com",
        user_id="123",
        client_ip_address="1.2.3.4",
        client_user_agent="pytest",
        fbp="fb.1.123",
        fbc="fb.1.456",
    )

    assert sent is False


def test_send_meta_capi_event_builds_expected_payload_without_test_code(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        ok = True

        def json(self):
            return {"events_received": 1}

    def fake_post(url, json=None, timeout=0):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(meta_capi.settings, "meta_capi_enabled", True)
    monkeypatch.setattr(meta_capi.settings, "meta_capi_pixel_id", "pixel-123")
    monkeypatch.setattr(meta_capi.settings, "meta_capi_access_token", "token-123")
    monkeypatch.setattr(meta_capi.settings, "meta_capi_test_event_code", "")
    monkeypatch.setattr(meta_capi.settings, "meta_capi_api_version", "v25.0")
    monkeypatch.setattr(meta_capi.requests, "post", fake_post)

    sent = meta_capi.send_meta_capi_event(
        event_name="CompleteRegistration",
        event_id="event-123",
        event_source_url="https://measurableapp.com/register",
        user_email="alice@example.com",
        user_id="123",
        client_ip_address="1.2.3.4",
        client_user_agent="pytest-agent",
        fbp="fb.1.abc",
        fbc="fb.1.xyz",
        custom_data={"plan": "free"},
    )

    assert sent is True
    assert captured["url"] == "https://graph.facebook.com/v25.0/pixel-123/events"
    assert captured["timeout"] == meta_capi.META_CAPI_DEFAULT_TIMEOUT_SECONDS
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["access_token"] == "token-123"
    assert "test_event_code" not in payload
    event = payload["data"][0]
    assert event["event_name"] == "CompleteRegistration"
    assert event["event_id"] == "event-123"
    assert event["action_source"] == "website"
    assert isinstance(event["event_time"], int)
    assert event["event_source_url"] == "https://measurableapp.com/register"
    assert event["custom_data"] == {"plan": "free"}
    assert event["user_data"]["em"] == [
        "ff8d9819fc0e12bf0d24892e45987e249a28dce836a85cad60e28eaaa8c6d976"
    ]
    assert event["user_data"]["external_id"] == [
        "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3"
    ]
    assert event["user_data"]["client_ip_address"] == "1.2.3.4"
    assert event["user_data"]["client_user_agent"] == "pytest-agent"


def test_tracking_meta_event_endpoint_uses_current_user_and_request_metadata(client, monkeypatch):
    user = _seed_user()
    captured: dict[str, object] = {}

    def fake_send_meta_capi_event(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr("app.main.send_meta_capi_event", fake_send_meta_capi_event)

    response = client.post(
        "/tracking/meta/event",
        headers={
            "Authorization": f"Bearer {create_access_token(str(user.id))}",
            "User-Agent": "pytest-agent",
            "X-Forwarded-For": "8.8.8.8",
        },
        cookies={"_fbp": "fb.1.cookie", "_fbc": "fb.1.click"},
        json={
            "event_name": "CompleteRegistration",
            "event_source_url": "https://measurableapp.com/signup",
            "custom_data": {"plan": "starter"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "sent": True}
    assert captured["event_name"] == "CompleteRegistration"
    assert captured["event_source_url"] == "https://measurableapp.com/signup"
    assert captured["user_email"] == "alice@example.com"
    assert captured["user_id"] == str(user.id)
    assert captured["client_ip_address"] == "8.8.8.8"
    assert captured["client_user_agent"] == "pytest-agent"
    assert captured["fbp"] == "fb.1.cookie"
    assert captured["fbc"] == "fb.1.click"
    assert captured["custom_data"] == {"plan": "starter"}
    assert str(captured["event_id"]).strip()
