from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_reports_delete_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.deps import get_db
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Dataset, Export, Integration, Job, Report, ReportBlock, ReportSource, ReportVersion, Schedule, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


REPORT_DELETE_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Integration.__table__,
    Report.__table__,
    ReportSource.__table__,
    ReportVersion.__table__,
    ReportBlock.__table__,
    Export.__table__,
    Schedule.__table__,
    Job.__table__,
]


@pytest.fixture(autouse=True)
def report_delete_schema():
    Base.metadata.drop_all(bind=engine, tables=REPORT_DELETE_TABLES)
    Base.metadata.create_all(bind=engine, tables=REPORT_DELETE_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=REPORT_DELETE_TABLES)


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


def _seed_report_graph(*, owner_role: str = "owner") -> dict[str, int]:
    db = SessionLocal()
    try:
        owner = User(
            email="owner@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        member = User(
            email="member@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Member User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Workspace")
        db.add_all([owner, member, workspace])
        db.flush()

        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role=owner_role),
                WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="member"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )

        dataset = Dataset(workspace_id=workspace.id, name="Dataset", description="Test", data={})
        integration = Integration(workspace_id=workspace.id, provider="meta", name="Meta", status="connected")
        db.add_all([dataset, integration])
        db.flush()

        report = Report(
            workspace_id=workspace.id,
            dataset_id=dataset.id,
            name="Report",
            description='{"thumbnail_s3_key":"report-thumbnails/1/cover.png"}',
        )
        db.add(report)
        db.flush()

        version = ReportVersion(report_id=report.id, version=1)
        db.add(version)
        db.flush()

        report_source = ReportSource(
            report_id=report.id,
            workspace_id=workspace.id,
            provider="meta",
            source_type="facebook_pages",
            integration_id=integration.id,
            dataset_id=dataset.id,
            position=0,
            label="Primary source",
        )
        block = ReportBlock(
            report_version_id=version.id,
            type="title",
            order=0,
            data_json='{"title":"hello"}',
            editable_fields_json='["title"]',
        )
        export = Export(
            workspace_id=workspace.id,
            report_id=report.id,
            status="done",
            output_s3_key=f"exports/{report.id}/deck.pptx",
            download_key=f"exports/{report.id}/deck.pptx",
        )
        schedule = Schedule(
            workspace_id=workspace.id,
            report_id=report.id,
            integration_id=None,
            freq="monthly",
            day_of_month=1,
            timezone="UTC",
            enabled=True,
        )
        db.add_all([report_source, block, export, schedule])
        db.flush()

        db.add_all(
            [
                Job(workspace_id=workspace.id, schedule_id=schedule.id, type="run_schedule", status="queued"),
                Job(workspace_id=workspace.id, export_id=export.id, type="deliver_export", status="queued"),
            ]
        )
        db.commit()
        return {
            "owner_id": owner.id,
            "member_id": member.id,
            "workspace_id": workspace.id,
            "report_id": report.id,
            "export_id": export.id,
            "schedule_id": schedule.id,
        }
    finally:
        db.close()


def test_delete_report_success_deletes_related_rows_and_cleans_job_refs(client, monkeypatch):
    refs = _seed_report_graph()
    deleted_objects: list[tuple[str, str]] = []

    class FakeS3Client:
        def delete_object(self, *, Bucket: str, Key: str) -> None:
            deleted_objects.append((Bucket, Key))

    monkeypatch.setattr("app.main.boto3.client", lambda *args, **kwargs: FakeS3Client())

    response = client.delete(f"/reports/{refs['report_id']}", headers=_auth_headers(refs["owner_id"]))

    assert response.status_code == 200
    assert response.json() == {"success": True}

    db = SessionLocal()
    try:
        assert db.get(Report, refs["report_id"]) is None
        assert db.query(ReportSource).filter(ReportSource.report_id == refs["report_id"]).count() == 0
        assert db.query(ReportVersion).filter(ReportVersion.report_id == refs["report_id"]).count() == 0
        assert db.query(ReportBlock).count() == 0
        assert db.query(Export).filter(Export.id == refs["export_id"]).count() == 0
        assert db.query(Schedule).filter(Schedule.id == refs["schedule_id"]).count() == 0

        jobs = db.query(Job).order_by(Job.id.asc()).all()
        assert len(jobs) == 2
        assert jobs[0].schedule_id is None
        assert jobs[1].export_id is None
    finally:
        db.close()

    assert deleted_objects == [
        ("test-outputs", "report-thumbnails/1/cover.png"),
        ("test-outputs", f"exports/{refs['report_id']}/deck.pptx"),
    ]


def test_delete_report_returns_404_when_report_missing(client):
    refs = _seed_report_graph()

    response = client.delete("/reports/999999", headers=_auth_headers(refs["owner_id"]))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "report_not_found"


def test_delete_report_returns_403_when_user_is_not_owner(client):
    refs = _seed_report_graph()

    response = client.delete(f"/reports/{refs['report_id']}", headers=_auth_headers(refs["member_id"]))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


def test_delete_report_rolls_back_on_database_error(monkeypatch):
    refs = _seed_report_graph()
    original_commit = SessionLocal.class_.commit
    state = {"failed": False}

    def failing_commit(self):
        if not state["failed"]:
            state["failed"] = True
            raise SQLAlchemyError("forced commit failure")
        return original_commit(self)

    monkeypatch.setattr(SessionLocal.class_, "commit", failing_commit)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.delete(
            f"/reports/{refs['report_id']}",
            headers=_auth_headers(refs["owner_id"]),
        )
    app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "report_delete_failed"

    db = SessionLocal()
    try:
        assert db.get(Report, refs["report_id"]) is not None
        assert db.query(Export).count() == 1
        assert db.query(Schedule).count() == 1
        jobs = db.query(Job).order_by(Job.id.asc()).all()
        assert jobs[0].schedule_id is not None
        assert jobs[1].export_id is not None
    finally:
        db.close()
