from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_account_summary_and_report_metadata.db")
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
from app.main import META_RECORD_TYPE_INSTAGRAM_ACCOUNT, app
from app.models import Dataset, Integration, MetaPage, Report, ReportSource, ReportVersion, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


ACCOUNT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    MetaPage.__table__,
    Dataset.__table__,
    Report.__table__,
    ReportSource.__table__,
    ReportVersion.__table__,
]


@pytest.fixture(autouse=True)
def account_schema():
    Base.metadata.drop_all(bind=engine, tables=ACCOUNT_TABLES)
    Base.metadata.create_all(bind=engine, tables=ACCOUNT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=ACCOUNT_TABLES)


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


def _seed_workspace_fixture() -> dict[str, int]:
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
        workspace = Workspace(
            name="Acme Brand",
            logo_url="https://example.com/logo.png",
        )
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="free", status="active"),
            ]
        )

        integration = Integration(
            workspace_id=workspace.id,
            provider="meta",
            name="Meta",
            status="connected",
        )
        db.add(integration)
        db.flush()

        instagram_dataset = Dataset(
            workspace_id=workspace.id,
            name="Instagram dataset",
            description="Test",
            data={
                "integration_type": "instagram_business",
                "page_name": "Botanero NL",
                "account_name": "Botanero NL",
                "username": "botaneronl",
            },
        )
        csv_dataset = Dataset(
            workspace_id=workspace.id,
            name="CSV dataset",
            description="Test",
            data={
                "integration_type": "csv_upload",
                "file_name": "sales.csv",
            },
        )
        db.add_all([instagram_dataset, csv_dataset])
        db.flush()

        instagram_report = Report(
            workspace_id=workspace.id,
            dataset_id=instagram_dataset.id,
            name="Instagram report",
            description="{}",
        )
        csv_report = Report(
            workspace_id=workspace.id,
            dataset_id=csv_dataset.id,
            name="CSV report",
            description="{}",
        )
        db.add_all([instagram_report, csv_report])
        db.flush()

        db.add(
            ReportSource(
                report_id=instagram_report.id,
                workspace_id=workspace.id,
                provider="meta",
                source_type="instagram_business",
                integration_id=integration.id,
                dataset_id=instagram_dataset.id,
                position=0,
                label="Botanero NL",
                config_json={
                    "account_name": "Botanero NL",
                    "instagram_username": "botaneronl",
                    "channel": "instagram",
                    "social_network": "instagram",
                },
            )
        )
        db.add_all(
            [
                ReportVersion(report_id=instagram_report.id, version=1),
                ReportVersion(report_id=csv_report.id, version=1),
            ]
        )

        instagram_meta_page = MetaPage(
            integration_id=integration.id,
            user_id=user.id,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            page_id="17841400000000000",
            parent_page_id="1234567890",
            name="Botanero NL",
            instagram_username="botaneronl",
            profile_picture_url="https://example.com/ig.png",
            business_name="Botanero FB",
        )
        db.add(instagram_meta_page)
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
            "instagram_report_id": instagram_report.id,
            "csv_report_id": csv_report.id,
        }
    finally:
        db.close()


def test_account_summary_and_account_display_name_update_do_not_change_brand_assets(client):
    refs = _seed_workspace_fixture()

    me_before = client.get("/me", headers=_auth_headers(refs["user_id"]))
    assert me_before.status_code == 200
    assert me_before.json()["account_display_name"] is None
    assert me_before.json()["account_display_name_effective"] == "Acme Brand"
    assert me_before.json()["is_free_plan"] is True
    assert me_before.json()["can_use_custom_branding"] is False
    assert me_before.json()["report_branding_mode"] == "measurable"

    patch_response = client.patch(
        "/me/workspace",
        headers=_auth_headers(refs["user_id"]),
        json={"account_display_name": "Acme Header"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["account_display_name"] == "Acme Header"
    assert patch_response.json()["account_display_name_effective"] == "Acme Header"
    assert patch_response.json()["brand_name"] == "Acme Brand"
    assert patch_response.json()["brand_logo_url"] == "https://example.com/logo.png"

    workspace_response = client.get(
        f"/workspaces/{refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["account_display_name"] == "Acme Header"
    assert workspace_response.json()["brand_name"] == "Acme Brand"
    assert workspace_response.json()["brand_logo_url"] == "https://example.com/logo.png"

    summary = client.get("/account/summary", headers=_auth_headers(refs["user_id"]))
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["reports_created_count"] == 2
    assert payload["reports_limit_this_month"] == 5
    assert payload["reports_remaining_this_month"] == 3
    assert payload["reports_available_count"] == 3
    assert payload["integrations_connected_count"] == 1
    assert payload["integrations_total_available"] == 3
    assert payload["current_plan_name"] == "free"
    assert payload["current_plan_code"] == "free"
    assert payload["is_free_plan"] is True
    assert payload["can_use_custom_branding"] is False
    assert payload["report_branding_mode"] == "measurable"
    assert payload["account_display_name"] == "Acme Header"
    assert payload["account_display_name_effective"] == "Acme Header"


def test_reports_expose_integration_metadata_filters_and_folder_update(client):
    refs = _seed_workspace_fixture()

    reports_response = client.get("/reports", headers=_auth_headers(refs["user_id"]))
    assert reports_response.status_code == 200
    reports_payload = reports_response.json()
    assert len(reports_payload) == 2

    instagram_item = next(item for item in reports_payload if item["id"] == refs["instagram_report_id"])
    assert instagram_item["integration_metadata"]["integration_type"] == "instagram"
    assert instagram_item["integration_metadata"]["integration_display_name"] == "Instagram Business"
    assert instagram_item["integration_metadata"]["source_name"] == "Botanero NL"
    assert instagram_item["integration_metadata"]["source_handle"] == "@botaneronl"
    assert instagram_item["integration_metadata"]["channel"] == "instagram"

    csv_item = next(item for item in reports_payload if item["id"] == refs["csv_report_id"])
    assert csv_item["integration_metadata"]["integration_type"] == "csv"
    assert csv_item["integration_metadata"]["integration_display_name"] == "CSV Upload"
    assert csv_item["integration_metadata"]["source_name"] == "sales.csv"

    instagram_filtered = client.get(
        "/reports",
        params={"integration_type": "instagram"},
        headers=_auth_headers(refs["user_id"]),
    )
    assert instagram_filtered.status_code == 200
    assert [item["id"] for item in instagram_filtered.json()] == [refs["instagram_report_id"]]

    csv_filtered = client.get(
        "/reports",
        params={"channel": "csv"},
        headers=_auth_headers(refs["user_id"]),
    )
    assert csv_filtered.status_code == 200
    assert [item["id"] for item in csv_filtered.json()] == [refs["csv_report_id"]]

    detail = client.get(
        f"/reports/{refs['instagram_report_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["integration_metadata"]["integration_type"] == "instagram"
    assert detail_payload["integration_metadata"]["source_handle"] == "@botaneronl"

    folder_update = client.patch(
        f"/reports/{refs['instagram_report_id']}/folder",
        headers=_auth_headers(refs["user_id"]),
        json={"folder_id": "folder-123", "folder_name": "Client Reports"},
    )
    assert folder_update.status_code == 200
    assert folder_update.json() == {
        "report_id": refs["instagram_report_id"],
        "folder_id": "folder-123",
        "folder_name": "Client Reports",
        "updated": True,
    }

    detail_after = client.get(
        f"/reports/{refs['instagram_report_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert detail_after.status_code == 200
    assert detail_after.json()["folder_id"] == "folder-123"
    assert detail_after.json()["folder_name"] == "Client Reports"


def test_meta_instagram_accounts_include_display_label_from_username(client, monkeypatch):
    refs = _seed_workspace_fixture()
    db = SessionLocal()
    try:
        instagram_record = (
            db.query(MetaPage)
            .filter(MetaPage.integration_id == refs["integration_id"], MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT)
            .one()
        )
    finally:
        db.close()

    monkeypatch.setattr("app.main._get_meta_access_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(
        "app.main._refresh_meta_pages_from_live_graph",
        lambda *_args, **_kwargs: ([instagram_record], [], []),
    )

    response = client.get(
        "/integrations/meta/instagram-accounts",
        params={"integration_id": refs["integration_id"]},
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["username"] == "botaneronl"
    assert payload[0]["display_label"] == "@botaneronl"
    assert payload[0]["profile_picture_url"] == "https://example.com/ig.png"
