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
from app.main import app, build_5_blocks
from app.models import Dataset, DatasetFile, Export, Job, ReferralConversion, Report, ReportBlock, ReportSource, ReportVersion, Subscription, User, UserAttribution, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password
from app.services import (
    MEASURABLE_BRANDING_LOGO_URL,
    MEASURABLE_REPORT_BRANDING_NAME,
    build_export_payload,
)


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
    UserAttribution.__table__,
    ReferralConversion.__table__,
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
        assert metadata["branding"]["brand_name"] == MEASURABLE_REPORT_BRANDING_NAME
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
        assert export_payload["report"]["branding"]["brand_name"] == MEASURABLE_REPORT_BRANDING_NAME
        assert export_payload["report"]["branding"]["resolved_brand_name"] == MEASURABLE_REPORT_BRANDING_NAME
        assert export_payload["report"]["branding"]["resolved_logo_url"] == MEASURABLE_BRANDING_LOGO_URL
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
        assert export_payload["report"]["branding"]["brand_name"] == "Acme Workspace"
        assert export_payload["report"]["branding"]["resolved_brand_name"] == "Acme Workspace"
        assert export_payload["report"]["branding"]["resolved_logo_url"] == "https://custom.example/user-logo.png"
    finally:
        db.close()

    get_response = client.get(
        f"/reports/{payload['id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert get_response.status_code == 200
    assert get_response.json()["branding"]["logo_url"] == "https://custom.example/user-logo.png"
    assert get_response.json()["branding"]["brand_name"] == "Acme Workspace"


def test_paid_workspace_reports_fallback_to_measurable_logo_and_default_brand_name(client):
    refs = _seed_branding_fixture(plan="core")

    db = SessionLocal()
    try:
        user = db.get(User, refs["user_id"])
        workspace = db.get(Workspace, refs["workspace_id"])
        assert user is not None
        assert workspace is not None
        user.logo_url = None
        user.full_name = None
        workspace.logo_url = None
        workspace.name = ""
        db.add_all([user, workspace])
        db.commit()
    finally:
        db.close()

    create_response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["dataset_id"],
            "title": "Fallback report",
            "requested_slides": 2,
            "locale": "en",
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["branding"]["resolved_logo_url"] == MEASURABLE_BRANDING_LOGO_URL
    assert payload["branding"]["resolved_brand_name"] == MEASURABLE_REPORT_BRANDING_NAME


def test_build_5_blocks_cover_includes_branding_for_meta_pages():
    branding = {
        "brand_name": "Atria Marketing",
        "brand_logo_url": "https://custom.example/logo.png",
        "logo_url": "https://custom.example/logo.png",
        "resolved_brand_name": "Atria Marketing",
        "resolved_logo_url": "https://custom.example/logo.png",
    }
    blocks = build_5_blocks(
        {
            "title": "Facebook Page Overview",
            "plan": "core",
            "report_timeframe": {"label": "Last 28 days", "since": "2026-04-01", "until": "2026-04-28"},
            "page_name": "Acme Page",
            "followers": 1000,
            "reach": 5000,
            "engagement": 200,
            "impressions": 9000,
            "summary": "Summary",
            "reach_chart_data": {"metric": "reach", "points": [], "timeframe": {"label": "Last 28 days"}},
            "reach_insight": "Reach insight",
            "recent_posts_summary": "Posts summary",
            "ai_summary": "AI summary",
            "general_insights_slide_payload": {},
            "impressions_slide_payload": {"impressions_daily": []},
            "report_inputs": {"integration_type": "meta_pages"},
            "branding": branding,
            "requested_slides": 5,
        }
    )
    cover = json.loads(blocks[0]["data_json"])
    assert cover["semantic_name"] == "cover"
    assert cover["branding"]["resolved_logo_url"] == "https://custom.example/logo.png"
    assert cover["branding"]["resolved_brand_name"] == "Atria Marketing"
    assert cover["resolved_logo_url"] == "https://custom.example/logo.png"
    assert cover["resolved_brand_name"] == "Atria Marketing"


def test_build_5_blocks_cover_includes_branding_for_instagram_business():
    branding = {
        "brand_name": MEASURABLE_REPORT_BRANDING_NAME,
        "brand_logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "logo_url": MEASURABLE_BRANDING_LOGO_URL,
        "resolved_brand_name": MEASURABLE_REPORT_BRANDING_NAME,
        "resolved_logo_url": MEASURABLE_BRANDING_LOGO_URL,
    }
    blocks = build_5_blocks(
        {
            "title": "Instagram Overview",
            "plan": "free",
            "report_timeframe": {"label": "Last 28 days", "since": "2026-04-01", "until": "2026-04-28"},
            "page_name": "Acme IG",
            "followers": 2500,
            "reach": None,
            "engagement": None,
            "impressions": 8000,
            "summary": "Summary",
            "reach_chart_data": {"metric": "reach", "points": [], "timeframe": {"label": "Last 28 days"}},
            "reach_insight": "Reach insight",
            "recent_posts_summary": "Posts summary",
            "ai_summary": "AI summary",
            "general_insights_slide_payload": {},
            "impressions_slide_payload": {"impressions_daily": []},
            "report_inputs": {"integration_type": "instagram_business", "unavailable_metrics": {}},
            "branding": branding,
            "requested_slides": 5,
        }
    )
    cover = json.loads(blocks[0]["data_json"])
    assert cover["semantic_name"] == "cover"
    assert cover["branding"]["resolved_logo_url"] == MEASURABLE_BRANDING_LOGO_URL
    assert cover["branding"]["resolved_brand_name"] == MEASURABLE_REPORT_BRANDING_NAME
