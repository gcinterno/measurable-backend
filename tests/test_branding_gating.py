from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_branding_gating_test.db")
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
from app.models import Dataset, DatasetFile, Export, Job, Report, ReportBlock, ReportSource, ReportVersion, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password
from app.services import MEASURABLE_BRANDING_LOGO_URL, build_export_payload


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


BRANDING_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
    Report.__table__,
    ReportVersion.__table__,
    ReportBlock.__table__,
    ReportSource.__table__,
    Export.__table__,
    Job.__table__,
]


@pytest.fixture(autouse=True)
def branding_schema():
    Base.metadata.drop_all(bind=engine, tables=BRANDING_TABLES)
    Base.metadata.create_all(bind=engine, tables=BRANDING_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=BRANDING_TABLES)


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


def _seed_branding_fixture(*, plan: str) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=f"{plan}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            logo_url="https://custom.example/user-logo.png",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(
            name="Acme Workspace",
            logo_url="https://custom.example/workspace-logo.png",
        )
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
            name="Dataset",
            description="Branding test dataset",
            data={},
        )
        db.add(dataset)
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "dataset_id": dataset.id,
        }
    finally:
        db.close()


def test_free_workspace_reports_use_measurable_branding_for_read_and_export(client):
    refs = _seed_branding_fixture(plan="free")

    workspace_response = client.get(
        f"/workspaces/{refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["plan_limits"]["allow_custom_branding"] is False

    create_response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Free plan report",
            "requested_slides": 2,
            "locale": "en",
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["branding"]["logo_url"] == MEASURABLE_BRANDING_LOGO_URL

    db = SessionLocal()
    try:
        report = db.get(Report, payload["id"])
        assert report is not None
        metadata = json.loads(report.description or "{}")
        assert metadata["branding"]["brand_name"] == "Measurable"
        assert metadata["branding"]["logo_url"] == MEASURABLE_BRANDING_LOGO_URL

        metadata["branding"] = {
            "brand_name": "Acme",
            "display_name": "Acme",
            "name": "Acme",
            "logo_url": "https://custom.example/historical-logo.png",
        }
        report.description = json.dumps(metadata)
        db.add(report)
        db.commit()
        db.refresh(report)

        report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id, ReportVersion.version == 1)
            .first()
        )
        if report_version is None:
            report_version = ReportVersion(report_id=report.id, version=1)
            db.add(report_version)
        export = Export(workspace_id=report.workspace_id, report_id=report.id, status="processing")
        db.add(export)
        db.commit()
        db.refresh(report_version)
        db.refresh(export)

        export_payload = build_export_payload(db, export, report, report_version, [])
        assert export_payload["report"]["branding"]["logo_url"] == MEASURABLE_BRANDING_LOGO_URL
        assert export_payload["report"]["branding"]["brand_name"] == "Measurable"
    finally:
        db.close()

    get_response = client.get(
        f"/reports/{payload['id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert get_response.status_code == 200
    assert get_response.json()["branding"]["logo_url"] == MEASURABLE_BRANDING_LOGO_URL

    version_response = client.get(
        f"/reports/{payload['id']}/versions/1",
        headers=_auth_headers(refs["user_id"]),
    )
    assert version_response.status_code == 200
    assert version_response.json()["branding"]["logo_url"] == MEASURABLE_BRANDING_LOGO_URL


def test_paid_workspace_reports_keep_custom_branding(client):
    refs = _seed_branding_fixture(plan="core")

    workspace_response = client.get(
        f"/workspaces/{refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["plan_limits"]["allow_custom_branding"] is True

    create_response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Paid plan report",
            "requested_slides": 2,
            "locale": "en",
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["branding"]["logo_url"] == "https://custom.example/user-logo.png"

    db = SessionLocal()
    try:
        report = db.get(Report, payload["id"])
        assert report is not None
        metadata = json.loads(report.description or "{}")
        assert metadata["branding"]["logo_url"] == "https://custom.example/user-logo.png"

        report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id, ReportVersion.version == 1)
            .first()
        )
        if report_version is None:
            report_version = ReportVersion(report_id=report.id, version=1)
            db.add(report_version)
        export = Export(workspace_id=report.workspace_id, report_id=report.id, status="processing")
        db.add(export)
        db.commit()
        db.refresh(report_version)
        db.refresh(export)

        export_payload = build_export_payload(db, export, report, report_version, [])
        assert export_payload["report"]["branding"]["logo_url"] == "https://custom.example/user-logo.png"
        assert export_payload["report"]["branding"]["brand_name"] is None
    finally:
        db.close()

    get_response = client.get(
        f"/reports/{payload['id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert get_response.status_code == 200
    assert get_response.json()["branding"]["logo_url"] == "https://custom.example/user-logo.png"
