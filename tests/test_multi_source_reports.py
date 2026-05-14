from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_multi_source_reports_test.db")
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
from app.models import Dataset, Integration, IntegrationAccount, Report, ReportBlock, ReportSource, ReportVersion, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


MULTI_SOURCE_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    Report.__table__,
    ReportBlock.__table__,
    ReportSource.__table__,
    ReportVersion.__table__,
]


@pytest.fixture(autouse=True)
def multi_source_schema():
    Base.metadata.drop_all(bind=engine, tables=MULTI_SOURCE_TABLES)
    Base.metadata.create_all(bind=engine, tables=MULTI_SOURCE_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=MULTI_SOURCE_TABLES)


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


def _seed_sources() -> dict[str, int]:
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

        dataset_one = Dataset(
            workspace_id=workspace.id,
            name="Facebook dataset",
            description="Test",
            data={
                "integration_type": "facebook_pages",
                "page_name": "Facebook Page",
                "account_name": "Facebook Page",
                "followers": 1200,
                "reach": 5400,
                "engagement": 320,
                "impressions": 8700,
                "timeframe": {
                    "preset": "last_28_days",
                    "since": "2026-04-01",
                    "until": "2026-04-28",
                    "label": "Last 28 days",
                },
                "reach_daily": [
                    {"date": "2026-04-01", "value": 180},
                    {"date": "2026-04-02", "value": 220},
                ],
                "impressions_daily": [
                    {"date": "2026-04-01", "value": 310},
                    {"date": "2026-04-02", "value": 360},
                ],
                "daily_engagement": [
                    {"date": "2026-04-01", "value": 14},
                    {"date": "2026-04-02", "value": 18},
                ],
                "recent_posts": [
                    {
                        "id": "fb-post-1",
                        "message": "Facebook launch update",
                        "reactions": 45,
                        "comments": 12,
                        "shares": 8,
                    }
                ],
                "normalized_report_metrics": {
                    "followers_growth_daily": [
                        {"date": "2026-04-01", "value": 4},
                        {"date": "2026-04-02", "value": 6},
                    ]
                },
            },
        )
        dataset_two = Dataset(
            workspace_id=workspace.id,
            name="Instagram dataset",
            description="Test",
            data={
                "integration_type": "instagram_business",
                "account_name": "Instagram Account",
                "page_name": "Instagram Account",
                "followers": 1800,
                "reach": 7600,
                "engagement": 540,
                "impressions": 12000,
                "timeframe": {
                    "preset": "last_28_days",
                    "since": "2026-04-01",
                    "until": "2026-04-28",
                    "label": "Last 28 days",
                },
                "reach_daily": [
                    {"date": "2026-04-01", "value": 260},
                    {"date": "2026-04-02", "value": 290},
                ],
                "impressions_daily": [
                    {"date": "2026-04-01", "value": 420},
                    {"date": "2026-04-02", "value": 470},
                ],
                "daily_engagement": [
                    {"date": "2026-04-01", "value": 24},
                    {"date": "2026-04-02", "value": 28},
                ],
                "recent_posts": [
                    {
                        "id": "ig-post-1",
                        "caption": "Instagram reel performance",
                        "likes": 110,
                        "comments": 16,
                        "saves": 21,
                    }
                ],
            },
        )
        integration = Integration(workspace_id=workspace.id, provider="meta", name="Meta", status="connected")
        db.add_all([dataset_one, dataset_two, integration])
        db.flush()

        integration_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id="ig_123",
            display_name="Instagram Account",
        )
        db.add(integration_account)
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "dataset_one_id": dataset_one.id,
            "dataset_two_id": dataset_two.id,
            "integration_id": integration.id,
            "integration_account_id": integration_account.id,
        }
    finally:
        db.close()


def test_create_multi_source_report_creates_ten_visual_blocks_for_two_sources(client):
    refs = _seed_sources()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Cross-source report",
            "timeframe": "last_28_days",
            "requested_slides": 10,
            "ai_mode": "standard",
            "locale": "en",
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_one_id"],
                    "position": 0,
                    "label": "Facebook Page",
                },
                {
                    "provider": "meta",
                    "source_type": "instagram_business",
                    "integration_id": refs["integration_id"],
                    "integration_account_id": "ig_123",
                    "dataset_id": refs["dataset_two_id"],
                    "position": 1,
                    "label": "Instagram Account",
                    "config_json": {
                        "external_account_id": "ig_123",
                        "account_name": "Instagram Account",
                    },
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Cross-source report"
    assert payload["status"] == "sources_configured"
    assert payload["dataset_id"] == refs["dataset_one_id"]
    assert len(payload["report_sources"]) == 2
    assert payload["version"] == 1
    assert payload["version_id"] is not None
    assert payload["report_sources"][0]["position"] == 0
    assert payload["report_sources"][0]["dataset_id"] == refs["dataset_one_id"]
    assert payload["report_sources"][1]["position"] == 1
    assert payload["report_sources"][1]["integration_account_id"] == refs["integration_account_id"]

    db = SessionLocal()
    try:
        report = db.get(Report, payload["id"])
        assert report is not None
        assert report.dataset_id == refs["dataset_one_id"]
        report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(ReportVersion.version.asc())
            .one()
        )
        assert report_version.version == 1
        blocks = (
            db.query(ReportBlock)
            .filter(ReportBlock.report_version_id == report_version.id)
            .order_by(ReportBlock.order.asc())
            .all()
        )
        assert len(blocks) == 10
        assert blocks[0].type == "title"
        assert blocks[-1].type == "text"
        stored_sources = (
            db.query(ReportSource)
            .filter(ReportSource.report_id == report.id)
            .order_by(ReportSource.position.asc())
            .all()
        )
        assert len(stored_sources) == 2
        assert stored_sources[0].label == "Facebook Page"
        assert stored_sources[1].label == "Instagram Account"
        assert stored_sources[1].config_json["external_account_id"] == "ig_123"
        assert stored_sources[1].config_json["account_name"] == "Instagram Account"
    finally:
        db.close()

    get_response = client.get(f"/reports/{payload['id']}", headers=_auth_headers(refs["user_id"]))
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["status"] == "sources_configured"
    assert len(get_payload["report_sources"]) == 2
    assert get_payload["version"] == 1

    versions_response = client.get(
        f"/reports/{payload['id']}/versions",
        headers=_auth_headers(refs["user_id"]),
    )
    assert versions_response.status_code == 200
    versions_payload = versions_response.json()
    assert len(versions_payload) == 1
    assert versions_payload[0]["version"] == 1
    assert len(versions_payload[0]["blocks"]) == 10

    version_response = client.get(
        f"/reports/{payload['id']}/versions/1",
        headers=_auth_headers(refs["user_id"]),
    )
    assert version_response.status_code == 200
    version_payload = version_response.json()
    assert version_payload["version"] == 1
    assert len(version_payload["blocks"]) == 10
    assert [block["order"] for block in version_payload["blocks"]] == list(range(1, 11))
    assert version_payload["blocks"][0]["type"] == "title"
    assert version_payload["blocks"][1]["type"] == "text"


def test_create_multi_source_report_rejects_two_sources_with_non_ten_slide_request(client):
    refs = _seed_sources()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Cross-source report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_one_id"],
                    "position": 0,
                    "label": "Facebook Page",
                },
                {
                    "provider": "meta",
                    "source_type": "instagram_business",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_two_id"],
                    "position": 1,
                    "label": "Instagram Account",
                },
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Multi-source reports require the 10-slide format."


def test_create_multi_source_report_allows_single_source_with_five_slides(client):
    refs = _seed_sources()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "title": "Single-source report",
            "requested_slides": 5,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_one_id"],
                    "position": 0,
                    "label": "Facebook Page",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "sources_configured"
    assert payload["version"] == 1
    assert len(payload["report_sources"]) == 1

    version_response = client.get(
        f"/reports/{payload['id']}/versions/1",
        headers=_auth_headers(refs["user_id"]),
    )
    assert version_response.status_code == 200
    assert version_response.json()["blocks"] == []


def test_create_multi_source_report_requires_first_source_dataset_for_compatibility(client):
    refs = _seed_sources()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "integration_account_id": refs["integration_account_id"],
                    "position": 0,
                    "label": "Configured source",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "first_source_dataset_required"


def test_create_multi_source_report_allows_dataset_fallback_without_integration_account(client):
    refs = _seed_sources()

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json={
            "requested_slides": 10,
            "sources": [
                {
                    "provider": "meta",
                    "source_type": "facebook_pages",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_one_id"],
                    "position": 0,
                    "label": "Facebook Page",
                },
                {
                    "provider": "meta",
                    "source_type": "instagram_business",
                    "integration_id": refs["integration_id"],
                    "dataset_id": refs["dataset_two_id"],
                    "position": 1,
                    "label": "Instagram Account",
                    "config_json": {
                        "external_account_id": "missing-external-id",
                        "account_name": "Instagram Account",
                    },
                },
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "sources_configured"
    assert payload["version"] == 1
    assert len(payload["report_sources"]) == 2
    assert payload["report_sources"][1]["integration_account_id"] is None
    assert payload["report_sources"][1]["dataset_id"] == refs["dataset_two_id"]
