from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_referrals_test.db")
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
from app.main import app
from app.models import (
    Dataset,
    EmailVerificationCode,
    ReferralClick,
    ReferralConversion,
    ReferralPartner,
    Report,
    ReportSource,
    Subscription,
    User,
    UserAttribution,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


REFERRAL_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    EmailVerificationCode.__table__,
    Dataset.__table__,
    Report.__table__,
    ReportSource.__table__,
    ReferralPartner.__table__,
    ReferralClick.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
]


@pytest.fixture(autouse=True)
def referral_schema():
    Base.metadata.drop_all(bind=engine, tables=REFERRAL_TABLES)
    Base.metadata.create_all(bind=engine, tables=REFERRAL_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=REFERRAL_TABLES)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("app.main.send_auth_email", lambda **kwargs: None)
    monkeypatch.setattr("app.main.enqueue_job", lambda *args, **kwargs: None)
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


def _auth_headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _seed_admin() -> dict[str, int]:
    db = SessionLocal()
    try:
        admin = User(
            email="admin@example.com",
            password_hash=hash_password("AdminPass123!"),
            full_name="Admin User",
            email_verified=True,
            auth_provider="email",
            is_admin=True,
            is_active=True,
        )
        workspace = Workspace(name="Admin Workspace")
        db.add_all([admin, workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=admin.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="core", status="active"))
        db.commit()
        return {"admin_id": admin.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _seed_verified_user_with_dataset() -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email="reporter@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Reporter User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Reporter Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(Subscription(workspace_id=workspace.id, plan="core", status="active"))
        dataset = Dataset(workspace_id=workspace.id, name="Dataset", description="Test", data={})
        db.add(dataset)
        db.flush()
        db.add(
            UserAttribution(
                user_id=user.id,
                first_referral_code="CREATOR1",
                last_referral_code="CREATOR1",
            )
        )
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id, "dataset_id": dataset.id}
    finally:
        db.close()


def test_referral_click_signup_manual_conversion_and_summary(client):
    admin_refs = _seed_admin()

    click_response = client.post(
        "/referrals/click",
        json={
            "referral_code": "PARTNER1",
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "spring-launch",
            "landing_page": "/pricing",
        },
        headers={"user-agent": "pytest-agent"},
    )
    assert click_response.status_code == 201

    partner_response = client.post(
        "/admin/referrals/partners",
        headers=_auth_headers(admin_refs["admin_id"]),
        json={
            "name": "Partner One",
            "code": "PARTNER1",
            "type": "partner",
            "commission_type": "percentage",
            "commission_value": 20,
            "status": "active",
        },
    )
    assert partner_response.status_code == 201

    register_response = client.post(
        "/auth/register",
        json={
            "email": "signup@example.com",
            "password": "Password123!",
            "full_name": "Signup User",
            "referral_code": "PARTNER1",
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "spring-launch",
            "utm_term": "creator",
            "utm_content": "hero-banner",
        },
    )
    assert register_response.status_code == 201

    db = SessionLocal()
    try:
        signup_user = db.query(User).filter(User.email == "signup@example.com").one()
        attribution = db.query(UserAttribution).filter(UserAttribution.user_id == signup_user.id).one()
        signup_conversion = (
            db.query(ReferralConversion)
            .filter(
                ReferralConversion.user_id == signup_user.id,
                ReferralConversion.conversion_type == "signup",
            )
            .one()
        )
        stored_click = db.query(ReferralClick).filter(ReferralClick.referral_code == "PARTNER1").one()
        assert attribution.first_referral_code == "PARTNER1"
        assert attribution.last_referral_code == "PARTNER1"
        assert attribution.utm_campaign == "spring-launch"
        assert signup_conversion.referral_code == "PARTNER1"
        assert stored_click.ip_hash is not None
        signup_user_id = signup_user.id
    finally:
        db.close()

    manual_conversion_response = client.post(
        "/admin/referrals/manual-conversion",
        headers=_auth_headers(admin_refs["admin_id"]),
        json={
            "user_id": signup_user_id,
            "conversion_type": "paid_subscription",
            "plan": "pro",
            "amount": 19,
            "currency": "USD",
        },
    )
    assert manual_conversion_response.status_code == 201
    assert manual_conversion_response.json()["commission_amount"] == 3.8

    summary_response = client.get(
        "/admin/referrals/summary",
        headers=_auth_headers(admin_refs["admin_id"]),
    )
    assert summary_response.status_code == 200
    summary_rows = summary_response.json()
    partner_row = next(row for row in summary_rows if row["referral_code"] == "PARTNER1")
    assert partner_row["partner_name"] == "Partner One"
    assert partner_row["clicks"] == 1
    assert partner_row["signups"] == 1
    assert partner_row["paid_conversions"] == 1
    assert partner_row["revenue"] == 19.0
    assert partner_row["estimated_commission"] == 3.8


def test_first_report_conversion_is_created_once(client):
    refs = _seed_verified_user_with_dataset()

    first_response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Report One",
            "locale": "en",
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Report Two",
            "locale": "en",
        },
    )
    assert second_response.status_code == 200

    db = SessionLocal()
    try:
        conversions = (
            db.query(ReferralConversion)
            .filter(
                ReferralConversion.user_id == refs["user_id"],
                ReferralConversion.conversion_type == "first_report",
            )
            .all()
        )
        assert len(conversions) == 1
        assert conversions[0].referral_code == "CREATOR1"
    finally:
        db.close()
