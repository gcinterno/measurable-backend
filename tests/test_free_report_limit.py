from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_free_report_limit_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
from app.main import app
from app.models import Dataset, Integration, ReferralConversion, Report, ReportSource, ReportVersion, Subscription, User, UserAttribution, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password
from app.services import get_workspace_report_quota_status


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


FREE_LIMIT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Integration.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
    Report.__table__,
    ReportSource.__table__,
    ReportVersion.__table__,
]


@pytest.fixture(autouse=True)
def free_limit_schema():
    Base.metadata.drop_all(bind=engine, tables=FREE_LIMIT_TABLES)
    Base.metadata.create_all(bind=engine, tables=FREE_LIMIT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=FREE_LIMIT_TABLES)


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


def _seed_workspace(
    plan: str = "free",
    existing_reports: int = 0,
    *,
    report_created_ats: list[datetime] | None = None,
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=f"{plan}-{existing_reports}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name=f"{plan.title()} Workspace")
        db.add_all([user, workspace])
        db.flush()

        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(
                    workspace_id=workspace.id,
                    plan=plan,
                    status="active",
                    current_period_start=current_period_start,
                    current_period_end=current_period_end,
                ),
            ]
        )

        dataset = Dataset(
            workspace_id=workspace.id,
            name="Facebook dataset",
            description="Test",
            data={
                "integration_type": "facebook_pages",
                "page_name": "Facebook Page",
                "account_name": "Facebook Page",
            },
        )
        integration = Integration(workspace_id=workspace.id, provider="meta", name="Meta", status="connected")
        db.add_all([dataset, integration])
        db.flush()

        report_timestamps = report_created_ats or [None] * existing_reports
        for index in range(existing_reports):
            created_at = report_timestamps[index] if index < len(report_timestamps) else None
            report_kwargs = {
                "workspace_id": workspace.id,
                "dataset_id": dataset.id,
                "name": f"Existing report {index + 1}",
                "description": "{}",
            }
            if created_at is not None:
                report_kwargs["created_at"] = created_at
                report_kwargs["updated_at"] = created_at
            report = Report(
                **report_kwargs,
            )
            db.add(report)
            db.flush()
            db.add(ReportVersion(report_id=report.id, version=1))

        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "dataset_id": dataset.id,
            "integration_id": integration.id,
        }
    finally:
        db.close()


def test_free_workspace_with_less_than_ten_reports_can_create_report(client):
    refs = _seed_workspace(plan="free", existing_reports=9)

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Allowed report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Allowed report"


def test_free_workspace_with_ten_reports_is_blocked(client):
    refs = _seed_workspace(plan="free", existing_reports=10)

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Blocked report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "monthly_report_limit_reached"
    assert payload["message"] == "You have reached your monthly report limit."
    assert payload["reports_used"] == 10
    assert payload["reports_limit"] == 10
    assert payload["reports_remaining"] == 0

    retry_response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Blocked report retry",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )
    assert retry_response.status_code == 403

    db = SessionLocal()
    try:
        assert db.query(Report).filter(Report.workspace_id == refs["workspace_id"]).count() == 10
    finally:
        db.close()


def test_paid_workspace_is_not_blocked_after_ten_reports(client):
    refs = _seed_workspace(plan="core", existing_reports=10)

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Paid report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Paid report"


def test_free_workspace_quota_is_consistent_between_sidebar_billing_and_generation(client):
    now = datetime.now(timezone.utc)
    report_created_ats = [now - timedelta(days=1, minutes=index) for index in range(6)] + [
        now - timedelta(days=40 + index) for index in range(4)
    ]
    refs = _seed_workspace(plan="free", existing_reports=10, report_created_ats=report_created_ats)

    summary_before = client.get("/account/summary", headers=_auth_headers(refs["user_id"]))
    assert summary_before.status_code == 200
    assert summary_before.json()["reports_remaining_this_month"] == 4
    assert summary_before.json()["reports_limit_this_month"] == 10

    billing_before = client.get("/billing/me", headers=_auth_headers(refs["user_id"]))
    assert billing_before.status_code == 200
    assert billing_before.json()["reports_used_current_month"] == 6
    assert billing_before.json()["reports_limit_monthly"] == 10

    db = SessionLocal()
    try:
        quota = get_workspace_report_quota_status(db, refs["workspace_id"])
        assert quota["reports_used"] == 6
        assert quota["limit_reached"] is False
    finally:
        db.close()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Quota synced report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )
    assert response.status_code == 200

    summary_after = client.get("/account/summary", headers=_auth_headers(refs["user_id"]))
    assert summary_after.status_code == 200
    assert summary_after.json()["reports_remaining_this_month"] == 3
    assert summary_after.json()["reports_limit_this_month"] == 10

    billing_after = client.get("/billing/me", headers=_auth_headers(refs["user_id"]))
    assert billing_after.status_code == 200
    assert billing_after.json()["reports_used_current_month"] == 7
    assert billing_after.json()["reports_limit_monthly"] == 10


def test_free_workspace_with_nine_reports_can_create_then_reaches_ten(client):
    now = datetime.now(timezone.utc)
    refs = _seed_workspace(
        plan="free",
        existing_reports=9,
        report_created_ats=[now - timedelta(hours=index + 1) for index in range(9)],
    )

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Tenth report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_id"],
                    "position": 0,
                }
            ],
        },
    )
    assert response.status_code == 200

    billing_after = client.get("/billing/me", headers=_auth_headers(refs["user_id"]))
    assert billing_after.status_code == 200
    assert billing_after.json()["reports_used_current_month"] == 10

    summary_after = client.get("/account/summary", headers=_auth_headers(refs["user_id"]))
    assert summary_after.status_code == 200
    assert summary_after.json()["reports_remaining_this_month"] == 0
    assert summary_after.json()["reports_limit_this_month"] == 10


def test_deleted_reports_do_not_consume_quota(client):
    now = datetime.now(timezone.utc)
    refs = _seed_workspace(
        plan="free",
        existing_reports=7,
        report_created_ats=[now - timedelta(hours=index + 1) for index in range(7)],
    )

    db = SessionLocal()
    try:
        report_id_to_delete = (
            db.query(Report)
            .filter(Report.workspace_id == refs["workspace_id"])
            .order_by(Report.id.asc())
            .limit(1)
            .with_entities(Report.id)
            .scalar()
        )
        assert report_id_to_delete is not None
        deleted_rows = (
            db.query(Report)
            .filter(Report.id == report_id_to_delete)
            .delete(synchronize_session=False)
        )
        assert deleted_rows == 1
        db.commit()
        quota = get_workspace_report_quota_status(db, refs["workspace_id"])
        assert quota["reports_used"] == 6
        assert quota["reports_remaining"] == 4
    finally:
        db.close()


def test_failed_reports_still_consume_quota_when_persisted(client):
    now = datetime.now(timezone.utc)
    refs = _seed_workspace(plan="free", existing_reports=0)

    db = SessionLocal()
    try:
        dataset_id = refs["dataset_id"]
        failed_report = Report(
            workspace_id=refs["workspace_id"],
            dataset_id=dataset_id,
            name="Failed report",
            description='{"report_status":"failed"}',
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        db.add(failed_report)
        db.commit()
        quota = get_workspace_report_quota_status(db, refs["workspace_id"])
        assert quota["reports_used"] == 1
        assert quota["reports_remaining"] == 9
    finally:
        db.close()


def test_report_quota_uses_subscription_cycle_for_paid_plans():
    period_start = datetime(2026, 5, 15, tzinfo=timezone.utc)
    period_end = datetime(2026, 6, 15, tzinfo=timezone.utc)
    refs = _seed_workspace(
        plan="starter",
        existing_reports=3,
        current_period_start=period_start,
        current_period_end=period_end,
        report_created_ats=[
            datetime(2026, 5, 10, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc),
        ],
    )

    db = SessionLocal()
    try:
        quota = get_workspace_report_quota_status(
            db,
            refs["workspace_id"],
            now=datetime(2026, 6, 3, tzinfo=timezone.utc),
        )
        assert quota["plan"] == "starter"
        assert quota["reports_used"] == 2
        assert quota["reports_limit"] == 10
        assert quota["reports_remaining"] == 8
        assert quota["period_start"] == period_start
        assert quota["period_end"] == period_end
    finally:
        db.close()


@pytest.mark.parametrize(
    ("plan", "expected_limit"),
    [("starter", 10), ("pro", 30), ("advanced", None)],
)
def test_report_quota_matches_plan_limits(plan, expected_limit):
    refs = _seed_workspace(plan=plan, existing_reports=0)

    db = SessionLocal()
    try:
        quota = get_workspace_report_quota_status(db, refs["workspace_id"])
        assert quota["plan"] == ("pro" if plan == "core" else plan)
        assert quota["reports_limit"] == expected_limit
    finally:
        db.close()
