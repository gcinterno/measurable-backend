from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
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
            "instagram_dataset_id": instagram_dataset.id,
            "csv_dataset_id": csv_dataset.id,
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
    assert payload["reports_limit_this_month"] == 10
    assert payload["reports_remaining_this_month"] == 8
    assert payload["reports_available_count"] == 8
    assert payload["integrations_connected_count"] == 1
    assert payload["integrations_total_available"] == 3
    assert payload["current_plan_name"] == "free"
    assert payload["current_plan_code"] == "free"
    assert payload["is_free_plan"] is True
    assert payload["can_use_custom_branding"] is False
    assert payload["report_branding_mode"] == "measurable"
    assert payload["account_display_name"] == "Acme Header"
    assert payload["account_display_name_effective"] == "Acme Header"

    compat_patch = client.patch(
        "/workspace",
        headers=_auth_headers(refs["user_id"]),
        json={"brand_name": "Acme Brand Updated", "brand_logo_url": "https://example.com/logo-2.png"},
    )
    assert compat_patch.status_code == 200
    assert compat_patch.json()["brand_name"] == "Acme Brand Updated"
    assert compat_patch.json()["brand_logo_url"] == "https://example.com/logo-2.png"
    assert compat_patch.json()["account_display_name"] == "Acme Header"

    branding_alias_patch = client.patch(
        "/workspace/branding",
        headers=_auth_headers(refs["user_id"]),
        json={"brandName": "Hola", "logoUrl": "https://example.com/logo-3.png"},
    )
    assert branding_alias_patch.status_code == 200
    assert branding_alias_patch.json()["brand_name"] == "Hola"
    assert branding_alias_patch.json()["logo_url"] == "https://example.com/logo-3.png"
    assert branding_alias_patch.json()["brand_logo_url"] == "https://example.com/logo-3.png"
    assert branding_alias_patch.json()["account_display_name"] == "Acme Header"

    remove_logo_patch = client.patch(
        "/workspace/branding",
        headers=_auth_headers(refs["user_id"]),
        json={"remove_logo": True},
    )
    assert remove_logo_patch.status_code == 200
    assert remove_logo_patch.json()["brand_name"] == "Hola"
    assert remove_logo_patch.json()["logo_url"] is None
    assert remove_logo_patch.json()["brand_logo_url"] is None


def test_workspace_brand_logo_upload_and_save_flow(client, monkeypatch):
    refs = _seed_workspace_fixture()
    stored_objects: dict[tuple[str, str], dict[str, object]] = {}

    class _Body:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

    class FakeS3Client:
        def put_object(self, *, Bucket, Key, Body, ContentType=None, CacheControl=None):
            stored_objects[(Bucket, Key)] = {
                "Body": Body,
                "ContentType": ContentType,
                "CacheControl": CacheControl,
            }

        def get_object(self, *, Bucket, Key):
            item = stored_objects[(Bucket, Key)]
            return {
                "Body": _Body(item["Body"]),
                "ContentType": item["ContentType"],
                "CacheControl": item["CacheControl"],
            }

    monkeypatch.setattr("app.main.boto3.client", lambda *_args, **_kwargs: FakeS3Client())

    upload = client.post(
        "/workspace/branding/logo",
        headers=_auth_headers(refs["user_id"]),
        files={"file": ("logo.png", BytesIO(b"fake-png-binary"), "image/png")},
    )
    assert upload.status_code == 200
    logo_url = upload.json()["logo_url"]
    assert "/workspace/branding/logo/1/" in logo_url

    logo_fetch = client.get(logo_url)
    assert logo_fetch.status_code == 200
    assert logo_fetch.content == b"fake-png-binary"
    assert logo_fetch.headers["content-type"] == "image/png"

    save = client.patch(
        "/workspace/branding",
        headers=_auth_headers(refs["user_id"]),
        json={"brand_name": "Uploaded Brand", "logo_url": logo_url},
    )
    assert save.status_code == 200
    payload = save.json()
    assert payload["brand_name"] == "Uploaded Brand"
    assert payload["logo_url"] == logo_url
    assert payload["brand_logo_url"] == logo_url
    assert payload["account_display_name"] is None

    workspace_response = client.get(
        f"/workspaces/{refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["brand_name"] == "Uploaded Brand"
    assert workspace_response.json()["logo_url"] == logo_url


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


def test_reports_facebook_pages_fallback_to_dataset_metadata_not_legacy(client):
    refs = _seed_workspace_fixture()
    db = SessionLocal()
    try:
        facebook_dataset = Dataset(
            workspace_id=refs["workspace_id"],
            name="Facebook dataset",
            description="Test",
            data={
                "integration_type": "facebook_pages",
                "page_name": "Botanero NL",
            },
        )
        db.add(facebook_dataset)
        db.flush()

        facebook_report = Report(
            workspace_id=refs["workspace_id"],
            dataset_id=facebook_dataset.id,
            name="Botanero NL Overview",
            description='{"source_name":"Botanero NL"}',
        )
        db.add(facebook_report)
        db.flush()
        db.add(ReportVersion(report_id=facebook_report.id, version=1))
        db.commit()
        facebook_report_id = facebook_report.id
    finally:
        db.close()

    reports_response = client.get("/reports", headers=_auth_headers(refs["user_id"]))
    assert reports_response.status_code == 200
    facebook_item = next(item for item in reports_response.json() if item["id"] == facebook_report_id)
    assert facebook_item["integration_metadata"]["integration_type"] == "facebook"
    assert facebook_item["integration_metadata"]["integration_display_name"] == "Facebook Pages"
    assert facebook_item["integration_metadata"]["source_name"] == "Botanero NL"
    assert facebook_item["integration_metadata"]["source_handle"] is None
    assert facebook_item["integration_metadata"]["social_network"] == "facebook"
    assert facebook_item["integration_metadata"]["channel"] == "facebook"

    detail_response = client.get(
        f"/reports/{facebook_report_id}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["integration_metadata"]["integration_type"] == "facebook"
    assert detail_response.json()["integration_metadata"]["channel"] == "facebook"


def test_reports_facebook_pages_fallback_to_cover_subtitle_not_legacy(client):
    refs = _seed_workspace_fixture()
    db = SessionLocal()
    try:
        facebook_dataset = Dataset(
            workspace_id=refs["workspace_id"],
            name="Facebook fallback dataset",
            description="Test",
            data={
                "page_name": "Botanero NL",
            },
        )
        db.add(facebook_dataset)
        db.flush()

        facebook_report = Report(
            workspace_id=refs["workspace_id"],
            dataset_id=facebook_dataset.id,
            name="Botanero NL Overview",
            description='{"sourceSummary":"Botanero NL","subtitle":"Facebook Pages Report - Summary & Insights"}',
        )
        db.add(facebook_report)
        db.flush()

        report_version = ReportVersion(report_id=facebook_report.id, version=1)
        db.add(report_version)
        db.commit()
        facebook_report_id = facebook_report.id
    finally:
        db.close()

    reports_response = client.get("/reports", headers=_auth_headers(refs["user_id"]))
    assert reports_response.status_code == 200
    facebook_item = next(item for item in reports_response.json() if item["id"] == facebook_report_id)
    assert facebook_item["integration_metadata"]["integration_type"] == "facebook"
    assert facebook_item["integration_metadata"]["integration_display_name"] == "Facebook Pages"
    assert facebook_item["integration_metadata"]["source_name"] == "Botanero NL"
    assert facebook_item["integration_metadata"]["social_network"] == "facebook"
    assert facebook_item["integration_metadata"]["channel"] == "facebook"


def test_reports_facebook_pages_fallback_to_generation_mode_not_legacy(client):
    refs = _seed_workspace_fixture()
    db = SessionLocal()
    try:
        facebook_dataset = Dataset(
            workspace_id=refs["workspace_id"],
            name="Facebook generation mode dataset",
            description="Test",
            data={
                "page_name": "Botanero NL",
            },
        )
        db.add(facebook_dataset)
        db.flush()

        facebook_report = Report(
            workspace_id=refs["workspace_id"],
            dataset_id=facebook_dataset.id,
            name="Botanero NL Overview",
            description='{"source":"meta_pages_v2","generation_mode":"meta_pages","sourceSummary":"Botanero NL"}',
        )
        db.add(facebook_report)
        db.flush()
        db.add(ReportVersion(report_id=facebook_report.id, version=1))
        db.commit()
        facebook_report_id = facebook_report.id
    finally:
        db.close()

    reports_response = client.get("/reports", headers=_auth_headers(refs["user_id"]))
    assert reports_response.status_code == 200
    facebook_item = next(item for item in reports_response.json() if item["id"] == facebook_report_id)
    assert facebook_item["integration_metadata"]["integration_type"] == "facebook"
    assert facebook_item["integration_metadata"]["integration_display_name"] == "Facebook Pages"
    assert facebook_item["integration_metadata"]["source_name"] == "Botanero NL"
    assert facebook_item["integration_metadata"]["social_network"] == "facebook"
    assert facebook_item["integration_metadata"]["channel"] == "facebook"


def test_refresh_report_thumbnail_does_not_fail_when_export_ready_selector_times_out(client, monkeypatch):
    refs = _seed_workspace_fixture()

    def _raise_thumbnail_timeout(**_kwargs):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "thumbnail_ready_timeout",
                "message": "Thumbnail page did not reach the ready state before timeout.",
            },
        )

    monkeypatch.setattr("app.main.generate_thumbnail_from_export_page", _raise_thumbnail_timeout)

    response = client.post(
        f"/reports/{refs['instagram_report_id']}/thumbnail",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_id"] == refs["instagram_report_id"]
    assert payload["thumbnail_s3_key"] is None
    assert payload["thumbnail_url"] is None


def test_report_creation_limit_returns_monthly_limit_code_and_detail(client):
    refs = _seed_workspace_fixture()
    db = SessionLocal()
    try:
        subscription = (
            db.query(Subscription)
            .filter(Subscription.workspace_id == refs["workspace_id"])
            .one()
        )
        subscription.plan = "starter"
        db.add(subscription)
        db.commit()

        base_time = datetime.now(timezone.utc) - timedelta(days=1)
        for index in range(8):
            db.add(
                Report(
                    workspace_id=refs["workspace_id"],
                    dataset_id=refs["csv_dataset_id"],
                    name=f"Monthly limit report {index}",
                    description="{}",
                    created_at=base_time + timedelta(minutes=index + 1),
                    updated_at=base_time + timedelta(minutes=index + 1),
                )
            )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/reports",
        headers=_auth_headers(refs["user_id"]),
        json={
            "dataset_id": refs["csv_dataset_id"],
            "title": "Blocked by monthly limit",
            "requested_slides": 2,
            "locale": "en",
        },
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "monthly_report_limit_reached"
    assert payload["detail"] == "You have reached your monthly report limit."
    assert payload["message"] == "You have reached your monthly report limit."
    assert payload["reports_used"] == 10
    assert payload["reports_limit"] == 10
    assert payload["reports_remaining"] == 0
