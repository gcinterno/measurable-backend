from __future__ import annotations

import os
from pathlib import Path

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


def _seed_workspace(plan: str = "free", existing_reports: int = 0) -> dict[str, int]:
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
                Subscription(workspace_id=workspace.id, plan=plan, status="active"),
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

        for index in range(existing_reports):
            report = Report(
                workspace_id=workspace.id,
                dataset_id=dataset.id,
                name=f"Existing report {index + 1}",
                description="{}",
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
    assert response.json() == {
        "code": "FREE_REPORT_LIMIT_REACHED",
        "message": "Has alcanzado el límite de 10 reportes gratuitos.",
        "upgrade_url": "https://measurableapp.com/wishlist",
    }


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
