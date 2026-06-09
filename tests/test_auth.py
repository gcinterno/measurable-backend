from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

TEST_DB_PATH = Path("/tmp/measurable_auth_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")

from app.deps import get_db
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AccountDeletionFeedback,
    AuditLog,
    EmailVerificationCode,
    MetaPage,
    ReferralConversion,
    Subscription,
    User,
    UserAttribution,
    Workspace,
    WorkspaceMember,
)
from app.services import build_auth_email_html, build_auth_email_text, send_auth_email
from app.errors import http_error


AUTH_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    EmailVerificationCode.__table__,
    AuditLog.__table__,
    MetaPage.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
    AccountDeletionFeedback.__table__,
]


@pytest.fixture(autouse=True)
def auth_schema():
    Base.metadata.drop_all(bind=engine, tables=AUTH_TABLES)
    Base.metadata.create_all(bind=engine, tables=AUTH_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=AUTH_TABLES)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("app.main.send_auth_email", lambda **kwargs: None)
    monkeypatch.setattr("app.services.generate_six_digit_code", lambda: "123456")

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


def _latest_code(email: str, purpose: str = "email_verification") -> EmailVerificationCode:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        return (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.user_id == user.id,
                EmailVerificationCode.purpose == purpose,
            )
            .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
            .first()
        )
    finally:
        db.close()


def _age_latest_code(email: str, *, purpose: str = "email_verification", seconds: int = 120) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        code = (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.user_id == user.id,
                EmailVerificationCode.purpose == purpose,
            )
            .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
            .first()
        )
        assert code is not None
        code.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        db.add(code)
        db.commit()
    finally:
        db.close()


def test_register_verify_login_flow(client):
    email = "alice@example.com"
    password = "CorrectHorseBattery1!"

    register = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Alice Example"},
    )
    assert register.status_code == 201
    assert register.json()["verification_required"] is True

    user = _fetch_user(email)
    assert user.email_verified is False

    login_before = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_before.status_code == 401

    verify = client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    assert verify.status_code == 200
    verify_payload = verify.json()
    assert verify_payload["ok"] is True
    assert verify_payload["token_type"] == "bearer"
    assert verify_payload["access_token"]
    assert verify_payload["user"]["email"] == email
    assert verify_payload["user"]["email_verified"] is True
    assert verify_payload["user"]["onboarding_completed"] is False
    assert "access_token=" in verify.headers.get("set-cookie", "")

    me_via_cookie = client.get("/auth/me")
    assert me_via_cookie.status_code == 200
    assert me_via_cookie.json()["email"] == email
    assert me_via_cookie.json()["email_verified"] is True
    assert me_via_cookie.json()["is_admin"] is False

    user = _fetch_user(email)
    assert user.email_verified is True

    login_after = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_after.status_code == 200
    payload = login_after.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]

    user = _fetch_user(email)
    assert user.last_login_at is not None

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    me_json = me.json()
    assert me_json["email"] == email
    assert me_json["email_verified"] is True
    assert me_json["auth_provider"] == "email"
    assert me_json["is_admin"] is False


def test_register_generates_verification_code_and_logs_email_send(client, monkeypatch, caplog):
    email = "alice@example.com"
    calls: list[dict[str, str]] = []

    def fake_send_auth_email(**kwargs):
        calls.append(kwargs)
        return "ses-message-123"

    monkeypatch.setattr("app.main.send_auth_email", fake_send_auth_email)

    with caplog.at_level("INFO"):
        response = client.post(
            "/auth/register",
            json={"email": email, "password": "CorrectHorseBattery1!", "full_name": "Alice Example"},
        )

    assert response.status_code == 201
    code_row = _latest_code(email)
    assert code_row is not None
    assert code_row.purpose == "email_verification"
    assert code_row.used_at is None
    assert calls and calls[0]["recipient_email"] == email
    assert calls[0]["subject"] == "Verify your Measurable email"

    attempt = next(record for record in caplog.records if record.message == "REGISTER_EMAIL_ATTEMPT")
    sent = next(record for record in caplog.records if record.message == "REGISTER_EMAIL_SENT")
    assert getattr(attempt, "email", None) == "a***e@example.com"
    assert getattr(sent, "message_id", None) == "ses-message-123"
    assert all("123456" not in record.getMessage() for record in caplog.records)


def test_register_email_failure_returns_friendly_error_and_safe_log(client, monkeypatch, caplog):
    def fake_send_auth_email(**kwargs):
        error = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email address is on the suppression list."}},
            "SendEmail",
        )
        raise http_error(503, "email_delivery_failed", "Unable to send verification email.") from error

    monkeypatch.setattr("app.main.send_auth_email", fake_send_auth_email)

    with caplog.at_level("INFO"):
        response = client.post(
            "/auth/register",
            json={"email": "failed@example.com", "password": "CorrectHorseBattery1!", "full_name": "Failed Send"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "email_delivery_failed"
    assert response.json()["detail"]["message"] == "We could not send your verification email. Please try again in a moment."

    attempt = next(record for record in caplog.records if record.message == "REGISTER_EMAIL_ATTEMPT")
    failed = next(record for record in caplog.records if record.message == "REGISTER_EMAIL_FAILED")
    assert getattr(attempt, "email", None) == "f***d@example.com"
    assert getattr(failed, "reason", None) == "MessageRejected: Email address is on the suppression list."
    assert not any(record.message == "REGISTER_EMAIL_SENT" for record in caplog.records)


def test_resend_verification_logs_email_send(client, monkeypatch, caplog):
    email = "resend@example.com"
    client.post(
        "/auth/register",
        json={"email": email, "password": "CorrectHorseBattery1!", "full_name": "Resend Example"},
    )
    _age_latest_code(email)

    calls: list[dict[str, str]] = []

    def fake_send_auth_email(**kwargs):
        calls.append(kwargs)
        return "ses-message-resend"

    monkeypatch.setattr("app.main.send_auth_email", fake_send_auth_email)

    with caplog.at_level("INFO"):
        response = client.post("/auth/resend-verification-code", json={"email": email})

    assert response.status_code == 200
    assert calls and calls[0]["recipient_email"] == email
    attempt = next(record for record in caplog.records if record.message == "RESEND_EMAIL_ATTEMPT")
    sent = next(record for record in caplog.records if record.message == "RESEND_EMAIL_SENT")
    assert getattr(attempt, "email", None) == "r***d@example.com"
    assert getattr(sent, "message_id", None) == "ses-message-resend"
    assert not any(record.message == "RESEND_EMAIL_FAILED" for record in caplog.records)


def test_resend_verification_email_failure_returns_friendly_error_and_safe_log(client, monkeypatch, caplog):
    email = "resend-failure@example.com"
    client.post(
        "/auth/register",
        json={"email": email, "password": "CorrectHorseBattery1!", "full_name": "Resend Failure"},
    )
    _age_latest_code(email)

    def fake_send_auth_email(**kwargs):
        error = ClientError(
            {"Error": {"Code": "SuppressedRecipient", "Message": "Address is suppressed."}},
            "SendEmail",
        )
        raise http_error(503, "email_delivery_failed", "Unable to send verification email.") from error

    monkeypatch.setattr("app.main.send_auth_email", fake_send_auth_email)

    with caplog.at_level("INFO"):
        response = client.post("/auth/resend-verification-code", json={"email": email})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "email_delivery_failed"
    assert response.json()["detail"]["message"] == "We could not send the verification email. Please try again."

    attempt = next(record for record in caplog.records if record.message == "RESEND_EMAIL_ATTEMPT")
    failed = next(record for record in caplog.records if record.message == "RESEND_EMAIL_FAILED")
    assert getattr(attempt, "email", None) == "r***e@example.com"
    assert getattr(failed, "reason", None) == "SuppressedRecipient: Address is suppressed."
    assert not any(record.message == "RESEND_EMAIL_SENT" for record in caplog.records)


def test_register_and_forgot_password_use_shared_auth_email_sender(client, monkeypatch):
    calls: list[dict[str, str]] = []

    def fake_send_auth_email(**kwargs):
        calls.append(kwargs)
        return "shared-message-id"

    monkeypatch.setattr("app.main.send_auth_email", fake_send_auth_email)

    email = "shared-sender@example.com"
    password = "CorrectHorseBattery1!"
    register = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Shared Sender"},
    )
    assert register.status_code == 201

    verify = client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    assert verify.status_code == 200

    forgot = client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 200

    assert len(calls) == 2
    assert calls[0]["recipient_email"] == email
    assert calls[0]["subject"] == "Verify your Measurable email"
    assert calls[1]["recipient_email"] == email
    assert calls[1]["subject"] == "Reset your Measurable password"


def test_login_invalid_password_returns_401_and_logs_failure(client, caplog):
    email = "bad-password@example.com"
    password = "Password123!"

    client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Bad Password"},
    )
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})

    with caplog.at_level("INFO"):
        response = client.post(
            "/auth/login",
            data={"username": email, "password": "wrong-password"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert any(record.message == "auth_login_password_verify_failed" for record in caplog.records)


def test_login_db_failure_returns_db_unavailable(client, monkeypatch, caplog):
    def broken_lookup(*args, **kwargs):
        raise OperationalError("select users", None, Exception("db down"))

    monkeypatch.setattr("app.main.load_user_by_email", broken_lookup)

    with caplog.at_level("ERROR"):
        response = client.post(
            "/auth/login",
            data={"username": "db-error@example.com", "password": "Password123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "db_unavailable"
    assert any(record.message == "auth_login_error" for record in caplog.records)


def test_login_missing_jwt_secret_returns_invalid_configuration(client, monkeypatch, caplog):
    email = "missing-secret@example.com"
    password = "Password123!"

    client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Missing Secret"},
    )
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    monkeypatch.setattr("app.main.settings.jwt_secret", "   ")

    with caplog.at_level("ERROR"):
        response = client.post(
            "/auth/login",
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "invalid_configuration"
    assert any(record.message == "auth_login_error" for record in caplog.records)


def test_verify_wrong_code_increments_attempts(client):
    email = "wrong-code@example.com"
    register = client.post(
        "/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Wrong Code"},
    )
    assert register.status_code == 201

    verify = client.post("/auth/verify-email", json={"email": email, "code": "000000"})
    assert verify.status_code == 400
    assert verify.json()["detail"]["code"] == "invalid_or_expired_code"

    code_row = _latest_code(email)
    assert code_row is not None
    assert code_row.attempts == 1
    assert code_row.used_at is None


def test_verify_expired_code_is_rejected(client):
    email = "expired-code@example.com"
    register = client.post(
        "/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Expired Code"},
    )
    assert register.status_code == 201

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        code_row = (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.user_id == user.id,
                EmailVerificationCode.purpose == "email_verification",
            )
            .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
            .first()
        )
        assert code_row is not None
        code_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(code_row)
        db.commit()
    finally:
        db.close()

    verify = client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    assert verify.status_code == 400
    assert verify.json()["detail"]["code"] == "invalid_or_expired_code"


def test_onboarding_completion_and_state(client):
    email = "onboarding@example.com"
    password = "Password123!"

    register = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Onboarding User"},
    )
    assert register.status_code == 201
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})

    onboarding_via_cookie = client.get("/onboarding/me")
    assert onboarding_via_cookie.status_code == 200
    assert onboarding_via_cookie.json() == {
        "onboarding_completed": False,
        "user_type": None,
        "goals": [],
        "platforms": [],
    }

    login = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    onboarding_before = client.get("/onboarding/me", headers={"Authorization": f"Bearer {token}"})
    assert onboarding_before.status_code == 200
    assert onboarding_before.json() == {
        "onboarding_completed": False,
        "user_type": None,
        "goals": [],
        "platforms": [],
    }

    complete = client.post(
        "/onboarding/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "user_type": "agency",
            "goals": ["track_growth", "client_reports"],
            "platforms": ["facebook", "instagram", "meta_ads"],
        },
    )
    assert complete.status_code == 200
    assert complete.json() == {"ok": True, "onboarding_completed": True}

    onboarding_after = client.get("/onboarding/me", headers={"Authorization": f"Bearer {token}"})
    assert onboarding_after.status_code == 200
    assert onboarding_after.json() == {
        "onboarding_completed": True,
        "user_type": "agency",
        "goals": ["track_growth", "client_reports"],
        "platforms": ["facebook", "instagram", "meta_ads"],
    }

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        assert user.onboarding_completed is True
        assert user.user_type == "agency"
        assert user.goals == ["track_growth", "client_reports"]
        assert user.platforms == ["facebook", "instagram", "meta_ads"]
    finally:
        db.close()


def test_onboarding_rejects_invalid_values(client):
    email = "invalid-onboarding@example.com"
    password = "Password123!"

    register = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Invalid Onboarding"},
    )
    assert register.status_code == 201
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})

    login = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    response = client.post(
        "/onboarding/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "user_type": "invalid",
            "goals": ["track_growth", "bad_goal"],
            "platforms": ["facebook", "invalid_platform"],
        },
    )
    assert response.status_code == 422


def test_onboarding_requires_authentication(client):
    response = client.get("/onboarding/me")
    assert response.status_code == 401


def test_account_delete_requires_authentication(client):
    response = client.request(
        "DELETE",
        "/account/delete",
        json={"reason": "no_longer_needed", "details": None, "confirmation": "Eliminar"},
    )
    assert response.status_code == 401


def test_account_delete_requires_exact_confirmation(client):
    email = "delete-confirmation@example.com"
    password = "Password123!"

    client.post("/auth/register", json={"email": email, "password": password, "full_name": "Delete User"})
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    user = _fetch_user(email)
    user_id = user.id
    login = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = login.json()["access_token"]

    response = client.request(
        "DELETE",
        "/account/delete",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "no_longer_needed", "details": None, "confirmation": "Eliminar!!"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_confirmation"


def test_account_delete_stores_feedback_and_blocks_login(client):
    email = "delete@example.com"
    password = "Password123!"

    client.post("/auth/register", json={"email": email, "password": password, "full_name": "Delete User"})
    client.post("/auth/verify-email", json={"email": email, "code": "123456"})
    user_id = _fetch_user(email).id
    login = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = login.json()["access_token"]
    client.cookies.set("access_token", token, path="/")

    delete_response = client.request(
        "DELETE",
        "/account/delete",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "reason": "privacy_concerns",
            "details": "Please remove my data permanently.",
            "confirmation": "Eliminar",
        },
    )
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}
    set_cookie = delete_response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "max-age=0" in set_cookie.lower() or "Max-Age=0" in set_cookie

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).one()
        assert user is not None
        assert user.email == f"deleted_{user_id}@deleted.measurable.local"
        assert user.is_deleted is True
        assert user.is_active is False
        assert user.google_sub is None
        assert user.facebook_sub is None
        assert user.password_hash

        feedback = db.query(AccountDeletionFeedback).filter(AccountDeletionFeedback.email == email).one()
        assert feedback.reason == "privacy_concerns"
        assert feedback.details == "Please remove my data permanently."
    finally:
        db.close()

    login_after = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_after.status_code == 401

    me_after = client.get("/auth/me")
    assert me_after.status_code == 401


def test_auth_email_sender_uses_multipart_reply_to_and_expected_subject(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSes:
        def send_email(self, **kwargs):
            captured.update(kwargs)
            return {"MessageId": "ses-123"}

    monkeypatch.setattr("app.services._ses_client", lambda: FakeSes())

    html_body = build_auth_email_html(
        full_name="Alice",
        code="123456",
        purpose="email_verification",
        expires_minutes=15,
    )
    text_body = build_auth_email_text(
        full_name="Alice",
        code="123456",
        purpose="email_verification",
        expires_minutes=15,
    )

    message_id = send_auth_email(
        recipient_email="alice@example.com",
        subject="Your Measurable verification code",
        html_body=html_body,
        text_body=text_body,
    )

    assert message_id == "ses-123"
    assert captured["Source"] == "no-reply@measurable.test"
    assert captured["ReplyToAddresses"] == ["hello@measurableapp.com"]
    assert captured["Message"]["Subject"]["Data"] == "Your Measurable verification code"
    assert "Html" in captured["Message"]["Body"]
    assert "Text" in captured["Message"]["Body"]


def test_ses_client_uses_configured_aws_region(monkeypatch):
    captured: dict[str, object] = {}

    def fake_boto_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.services.settings.aws_region", "us-west-2")
    monkeypatch.setattr("app.services.boto3.client", fake_boto_client)

    from app.services import _ses_client

    _ses_client()

    assert captured["service_name"] == "ses"
    assert captured["region_name"] == "us-west-2"


def test_ses_client_uses_configured_aws_credentials_and_optional_session_token(monkeypatch):
    captured: dict[str, object] = {}

    def fake_boto_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.services.settings.aws_region", "us-east-2")
    monkeypatch.setattr("app.services.settings.aws_access_key_id", "test-access-key")
    monkeypatch.setattr("app.services.settings.aws_secret_access_key", "test-secret-key")
    monkeypatch.setattr("app.services.settings.aws_session_token", "test-session-token")
    monkeypatch.setattr("app.services.boto3.client", fake_boto_client)

    from app.services import _ses_client

    _ses_client()

    assert captured["service_name"] == "ses"
    assert captured["region_name"] == "us-east-2"
    assert captured["aws_access_key_id"] == "test-access-key"
    assert captured["aws_secret_access_key"] == "test-secret-key"
    assert captured["aws_session_token"] == "test-session-token"


def test_safe_auth_email_formatter_includes_only_whitelisted_fields():
    from app.main import _SafeAuthEmailFormatter

    formatter = _SafeAuthEmailFormatter("%(levelname)s %(name)s %(message)s")
    record = logging.LogRecord(
        name="app.main",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="RESEND_EMAIL_SENT",
        args=(),
        exc_info=None,
    )
    record.email = "a***e@example.com"
    record.message_id = "ses-123"
    record.reason = "MessageRejected"
    record.token = "secret-token"

    formatted = formatter.format(record)

    assert "RESEND_EMAIL_SENT" in formatted
    assert "email=a***e@example.com" in formatted
    assert "message_id=ses-123" in formatted
    assert "reason=MessageRejected" in formatted
    assert "secret-token" not in formatted
