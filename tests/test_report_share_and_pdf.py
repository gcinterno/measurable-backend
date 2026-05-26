from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_report_share_and_pdf_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_URL", "https://measurableapp.com")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
import app.main as main_module
from app.main import app
from app.models import Dataset, Report, ReportBlock, ReportShare, ReportVersion, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


REPORT_SHARE_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Report.__table__,
    ReportVersion.__table__,
    ReportBlock.__table__,
    ReportShare.__table__,
]


@pytest.fixture(autouse=True)
def report_share_schema():
    Base.metadata.drop_all(bind=engine, tables=REPORT_SHARE_TABLES)
    Base.metadata.create_all(bind=engine, tables=REPORT_SHARE_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=REPORT_SHARE_TABLES)


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


def _seed_report(*, plan: str) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=f"{plan}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Acme Workspace")
        db.add_all([user, workspace])
        db.flush()
        subscription = Subscription(workspace_id=workspace.id, plan=plan, status="active")
        main_module.apply_plan_entitlements(subscription, plan)
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                subscription,
            ]
        )
        dataset = Dataset(
            workspace_id=workspace.id,
            name="Dataset",
            description="Share test dataset",
            data={},
        )
        db.add(dataset)
        db.flush()
        report = Report(
            workspace_id=workspace.id,
            dataset_id=dataset.id,
            name="Quarterly Report / May",
            description='{"locale":"en"}',
        )
        db.add(report)
        db.flush()
        version = ReportVersion(report_id=report.id, version=2)
        db.add(version)
        db.flush()
        db.add(
            ReportBlock(
                report_version_id=version.id,
                type="kpi",
                order=1,
                data_json='{"title":"Reach","value":"180,288"}',
                editable_fields_json='["title"]',
            )
        )
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "report_id": report.id,
            "version_id": version.id,
        }
    finally:
        db.close()


@pytest.mark.parametrize("plan", ["starter", "core", "advanced"])
def test_pdf_export_returns_application_pdf_for_allowed_plans(client, monkeypatch, plan):
    refs = _seed_report(plan=plan)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 fake",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == 'attachment; filename="Quarterly_Report_May.pdf"'
    assert response.content == b"%PDF-1.4 fake"
    assert captured["auth_token"] is None
    assert str(captured["export_url"]).startswith("http://localhost:3000/share/reports/")
    assert "?export=pdf" in str(captured["export_url"])
    assert "&_ts=" in str(captured["export_url"])


def test_private_pdf_export_prefers_report_export_base_url(client, monkeypatch):
    refs = _seed_report(plan="starter")
    captured: dict[str, object] = {}
    monkeypatch.setattr(main_module.settings, "report_export_base_url", "http://localhost:3000")
    monkeypatch.setattr(main_module.settings, "frontend_url", "https://measurableapp.com")
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 private",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    assert str(captured["export_url"]).startswith("http://localhost:3000/share/reports/")


def test_private_pdf_export_uses_requested_template_query(client, monkeypatch):
    refs = _seed_report(plan="starter")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 private-template",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf?template=modern",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    assert "?export=pdf" in str(captured["export_url"])
    assert "&template=modern" in str(captured["export_url"])
    assert "&_ts=" in str(captured["export_url"])


def test_private_pdf_export_preserves_incoming_ts_query(client, monkeypatch):
    refs = _seed_report(plan="starter")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 private-ts",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf?template=modern&_ts=1712345678",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 200
    assert "&template=modern" in str(captured["export_url"])
    assert "&_ts=1712345678" in str(captured["export_url"])


def test_pdf_export_returns_502_when_render_page_fails(client, monkeypatch):
    refs = _seed_report(plan="starter")

    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=502,
                detail={
                    "code": "pdf_render_failed",
                    "message": "PDF render page did not load the report content.",
                    "page_status": 200,
                    "page_title": "Report export unavailable",
                    "page_text_excerpt": "The report could not be loaded for export",
                },
            )
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "pdf_render_failed",
        "message": "PDF render page did not load the report content.",
        "page_status": 200,
        "page_title": "Report export unavailable",
        "page_text_excerpt": "The report could not be loaded for export",
    }


def test_pdf_export_returns_502_when_render_opens_invalid_share(client, monkeypatch):
    refs = _seed_report(plan="starter")

    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=502,
                detail={
                    "code": "pdf_render_share_invalid",
                    "message": "PDF render opened an invalid or expired share link.",
                    "page_status": 200,
                    "page_title": "Reporte compartido",
                    "page_text_excerpt": "No encontramos este reporte compartido",
                    "data_pdf_ready_exists": False,
                    "data_pdf_error_exists": True,
                    "report_slide_count": 0,
                },
            )
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "pdf_render_share_invalid"
    assert response.json()["detail"]["message"] == "PDF render opened an invalid or expired share link."


def test_pdf_export_returns_502_when_frontend_reports_pdf_error(client, monkeypatch):
    refs = _seed_report(plan="starter")

    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=502,
                detail={
                    "code": "pdf_render_frontend_error",
                    "message": "PDF export page reported a frontend render error.",
                    "page_status": 200,
                    "page_title": "Shared report",
                    "page_text_excerpt": "Render error while preparing PDF",
                    "data_pdf_ready_exists": False,
                    "data_pdf_error_exists": True,
                    "report_slide_count": 1,
                },
            )
        ),
    )

    response = client.get(
        f"/reports/{refs['report_id']}/download/pdf",
        headers=_auth_headers(refs["user_id"]),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "pdf_render_frontend_error"
    assert response.json()["detail"]["message"] == "PDF export page reported a frontend render error."


def test_create_share_and_get_public_report_without_auth(client):
    refs = _seed_report(plan="starter")

    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )

    assert create_response.status_code == 200
    body = create_response.json()
    assert body["status"] == "ok"
    assert body["report_id"] == refs["report_id"]
    assert body["share_token"]
    assert body["share_url"] == f"https://measurableapp.com/share/reports/{body['share_token']}"

    public_response = client.get(f"/public/reports/{body['share_token']}")
    assert public_response.status_code == 200
    public_body = public_response.json()
    assert public_body["is_public_share"] is True
    assert public_body["report"]["id"] == refs["report_id"]
    assert public_body["report"]["workspace_id"] == refs["workspace_id"]
    assert public_body["report"]["title"] == "Quarterly Report / May"
    assert public_body["report"]["integration_type"] == "legacy"
    assert public_body["report"]["integration_label"] == "Manual / Legacy report"
    assert public_body["report"]["brand_name"]
    assert public_body["report"]["logo_url"]
    assert public_body["report"]["period_start"] is None
    assert public_body["report"]["period_end"] is None
    assert public_body["version"]["id"] == refs["version_id"]
    assert public_body["version"]["version"] == 2
    assert len(public_body["blocks"]) == 1
    assert public_body["blocks"][0]["type"] == "kpi"


def test_public_report_payload_uses_requested_template_override(client):
    refs = _seed_report(plan="starter")

    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]

    public_response = client.get(f"/public/reports/{share_token}?template=modern")

    assert public_response.status_code == 200
    assert public_response.json()["report"]["template"] == "modern"


def test_revoke_share_invalidates_public_link(client):
    refs = _seed_report(plan="starter")

    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]

    revoke_response = client.delete(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json() == {
        "status": "ok",
        "report_id": refs["report_id"],
        "revoked": True,
    }

    public_response = client.get(f"/public/reports/{share_token}")
    assert public_response.status_code == 404
    assert public_response.json()["detail"]["code"] == "share_link_not_found"


def test_public_pdf_download_works_without_auth(client, monkeypatch):
    refs = _seed_report(plan="starter")
    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 public",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Shared Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(f"/public/reports/{share_token}/download/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == 'attachment; filename="Quarterly_Report_May.pdf"'
    assert response.content == b"%PDF-1.4 public"
    assert captured["auth_token"] is None
    assert str(captured["export_url"]).startswith(
        f"http://localhost:3000/share/reports/{share_token}?export=pdf"
    )
    assert "&_ts=" in str(captured["export_url"])


def test_public_pdf_download_uses_requested_template_query(client, monkeypatch):
    refs = _seed_report(plan="starter")
    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 public-template",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Shared Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(f"/public/reports/{share_token}/download/pdf?template=modern")

    assert response.status_code == 200
    assert str(captured["export_url"]).startswith(
        f"http://localhost:3000/share/reports/{share_token}?export=pdf"
    )
    assert "&template=modern" in str(captured["export_url"])
    assert "&_ts=" in str(captured["export_url"])


def test_public_pdf_download_preserves_incoming_ts_query(client, monkeypatch):
    refs = _seed_report(plan="starter")
    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (
            captured.update(_kwargs) or True
        )
        and (
            b"%PDF-1.4 public-ts",
            {
                "auth_strategy": "public_share_url",
                "report_fetch_succeeded": True,
                "page_count": 1,
                "page_status": 200,
                "page_title": "Shared Report",
                "page_text_excerpt": "Quarterly Report",
            },
        ),
    )

    response = client.get(f"/public/reports/{share_token}/download/pdf?template=modern&_ts=1712345678")

    assert response.status_code == 200
    assert "&template=modern" in str(captured["export_url"])
    assert "&_ts=1712345678" in str(captured["export_url"])


def test_public_pdf_download_returns_404_for_invalid_token(client):
    response = client.get("/public/reports/invalid-token/download/pdf")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "share_link_not_found"


def test_public_pdf_download_returns_public_render_failed(client, monkeypatch):
    refs = _seed_report(plan="starter")
    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]
    monkeypatch.setattr(
        main_module,
        "generate_pdf_from_export_page",
        lambda **_kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=502,
                detail={
                    "code": "pdf_render_failed",
                    "message": "PDF render page did not load the report content.",
                    "page_status": 200,
                    "page_title": "Report export unavailable",
                    "page_text_excerpt": "The report could not be loaded for export",
                },
            )
        ),
    )

    response = client.get(f"/public/reports/{share_token}/download/pdf")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "public_pdf_render_failed",
        "message": "PDF render page did not load the shared report content.",
    }


def test_public_pdf_download_requires_frontend_url(client, monkeypatch):
    refs = _seed_report(plan="starter")
    create_response = client.post(
        f"/reports/{refs['report_id']}/share",
        headers=_auth_headers(refs["user_id"]),
    )
    share_token = create_response.json()["share_token"]
    monkeypatch.setattr(main_module.settings, "frontend_url", None)
    monkeypatch.setattr(main_module.settings, "frontend_base_url", None)
    monkeypatch.setattr(main_module.settings, "report_export_base_url", None)

    response = client.get(f"/public/reports/{share_token}/download/pdf")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "frontend_url_not_configured",
        "message": "FRONTEND_URL or REPORT_EXPORT_BASE_URL is required for PDF export.",
    }
