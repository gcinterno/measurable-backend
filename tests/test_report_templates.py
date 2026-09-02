from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


TEST_DB_PATH = Path("/tmp/measurable_report_templates_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


from app.db import Base, SessionLocal, engine
from app.deps import get_current_user, get_db
from app.main import app
from app.models import (
    Dataset,
    Report,
    ReportTemplate,
    ReportTemplateVersion,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.report_spec import (
    FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC,
    BlockSpec,
    DataBinding,
    ReportSpec,
    SlideSpec,
    report_spec_from_json,
    report_spec_to_json,
)
from app.report_templates import seed_facebook_instagram_reference_template


REPORT_TEMPLATE_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    ReportTemplate.__table__,
    ReportTemplateVersion.__table__,
    Dataset.__table__,
    Report.__table__,
]


@pytest.fixture(autouse=True)
def report_template_schema():
    Base.metadata.drop_all(bind=engine, tables=REPORT_TEMPLATE_TABLES)
    Base.metadata.create_all(bind=engine, tables=REPORT_TEMPLATE_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=REPORT_TEMPLATE_TABLES)


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


def _seed_identity() -> dict[str, int]:
    db = SessionLocal()
    try:
        admin = User(
            email="admin@example.com",
            password_hash="unused",
            email_verified=True,
            auth_provider="email",
            is_admin=True,
            is_active=True,
        )
        member = User(
            email="member@example.com",
            password_hash="unused",
            email_verified=True,
            auth_provider="email",
            is_admin=False,
            is_active=True,
        )
        workspace = Workspace(name="Template Workspace")
        db.add_all([admin, member, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=admin.id, role="owner"),
                WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        db.commit()
        return {
            "admin_id": int(admin.id),
            "member_id": int(member.id),
            "workspace_id": int(workspace.id),
        }
    finally:
        db.close()


def _current_user(user_id: int, *, is_admin: bool) -> User:
    return User(
        id=user_id,
        email=f"user-{user_id}@example.com",
        password_hash="unused",
        email_verified=True,
        auth_provider="email",
        is_admin=is_admin,
        is_active=True,
    )


def _authorize(user_id: int, *, is_admin: bool = True) -> None:
    app.dependency_overrides[get_current_user] = lambda: _current_user(user_id, is_admin=is_admin)


def _reference_spec() -> dict:
    return copy.deepcopy(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC.as_dict())


def _template_payload(workspace_id: int, *, spec_json: dict | None = None) -> dict:
    return {
        "workspace_id": workspace_id,
        "name": "Facebook + Instagram Executive",
        "description": "Reusable canonical report template.",
        "slug": "facebook-instagram-executive",
        "generation_mode": "manual_template",
        "template_type": "multi_source_social",
        "datasource_requirements": {
            "mode": "all",
            "sources": ["facebook_pages", "instagram_business"],
            "minimum_source_count": 2,
            "required_canonical_semantics": ["reach", "visibility", "engagement"],
            "catalog_required": True,
        },
        "metadata": {"owner": "report_studio"},
        "spec_json": spec_json,
    }


def _create_template(client: TestClient, workspace_id: int, *, spec_json: dict | None = None) -> dict:
    response = client.post("/report-templates", json=_template_payload(workspace_id, spec_json=spec_json))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_template(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])

    payload = _create_template(client, refs["workspace_id"])

    assert payload["name"] == "Facebook + Instagram Executive"
    assert payload["status"] == "draft"
    assert payload["workspace_id"] == refs["workspace_id"]
    assert payload["active_version_id"] is None


def test_fetch_template(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])

    response = client.get(f"/report-templates/{template['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == template["id"]


def test_update_draft_metadata(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])

    response = client.patch(
        f"/report-templates/{template['id']}",
        json={"name": "Updated Template", "metadata": {"owner": "analytics"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Updated Template"
    assert payload["metadata"] == {"owner": "analytics"}


def test_create_version(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])

    response = client.post(
        f"/report-templates/{template['id']}/versions",
        json={"spec_json": _reference_spec(), "notes": "Initial draft."},
    )

    assert response.status_code == 201
    version = response.json()
    assert version["version_number"] == 1
    assert version["schema_version"] == "1.0"
    assert version["notes"] == "Initial draft."


def test_reportspec_round_trip_integrity(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"], spec_json=_reference_spec())

    versions = client.get(f"/report-templates/{template['id']}/versions").json()
    stored_spec = versions[0]["spec_json"]

    decoded = report_spec_from_json(report_spec_to_json(ReportSpec.from_dict(stored_spec)))
    assert decoded == FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC


def test_invalid_reportspec_rejected(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])
    invalid_spec = _reference_spec()
    invalid_spec["slides"][1]["blocks"][0]["bindings"][0]["canonical_semantic"] = "unknown_metric"

    response = client.post(
        f"/report-templates/{template['id']}/versions",
        json={"spec_json": invalid_spec},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_report_spec"
    assert "UNKNOWN_CANONICAL_BINDING" in response.json()["detail"]["message"]


def test_raw_provider_metric_binding_rejected(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])
    invalid_spec = _reference_spec()
    invalid_spec["slides"][1]["blocks"][0]["bindings"][0]["metric_path"] = "page_post_engagements"

    response = client.post(
        f"/report-templates/{template['id']}/versions",
        json={"spec_json": invalid_spec},
    )

    assert response.status_code == 400
    assert "RAW_PROVIDER_FIELD_BINDING" in response.json()["detail"]["message"]


def test_canonical_metric_binding_accepted(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])
    spec = ReportSpec(
        schema_version="1.0",
        id="engagement-template-v1",
        name="Engagement Template",
        report_type="generic_social",
        template_id="engagement_template",
        generation_mode="template",
        datasource_requirements={"mode": "any", "sources": ["social_source"]},
        slides=(
            SlideSpec(
                id="engagement",
                order=1,
                slide_type="metric",
                title="Engagement",
                layout="metric_focus",
                blocks=(
                    BlockSpec(
                        id="engagement-hero",
                        type="metric_hero",
                        bindings=(DataBinding(canonical_semantic="engagement"),),
                    ),
                ),
            ),
        ),
    )

    response = client.post(
        f"/report-templates/{template['id']}/versions",
        json={"spec_json": spec.as_dict()},
    )

    assert response.status_code == 201
    assert response.json()["spec_json"]["slides"][0]["blocks"][0]["bindings"][0]["canonical_semantic"] == "engagement"


def test_publish_version(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"], spec_json=_reference_spec())
    version = client.get(f"/report-templates/{template['id']}/versions").json()[0]

    response = client.post(f"/report-templates/{template['id']}/publish", json={"version_id": version["id"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "published"
    assert payload["published_version_id"] == version["id"]


def test_published_version_immutable_and_new_draft_version_created_after_publish(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"], spec_json=_reference_spec())
    v1 = client.get(f"/report-templates/{template['id']}/versions").json()[0]
    publish_response = client.post(f"/report-templates/{template['id']}/publish", json={"version_id": v1["id"]})
    assert publish_response.status_code == 200
    direct_patch = client.patch(f"/report-templates/{template['id']}", json={"name": "Should Not Mutate"})
    assert direct_patch.status_code == 409

    v2_spec = _reference_spec()
    v2_spec["slides"][1]["title"] = "Updated Reach"
    v2_response = client.post(
        f"/report-templates/{template['id']}/versions",
        json={"spec_json": v2_spec, "change_summary": "Change slide title."},
    )

    assert v2_response.status_code == 201
    v1_after = client.get(f"/report-templates/{template['id']}/versions/{v1['id']}").json()
    template_after = client.get(f"/report-templates/{template['id']}").json()
    assert v1_after["spec_json"]["slides"][1]["title"] == "Reach"
    assert v2_response.json()["spec_json"]["slides"][1]["title"] == "Updated Reach"
    assert template_after["status"] == "draft"
    assert template_after["published_version_id"] == v1["id"]
    assert template_after["active_version_id"] == v2_response.json()["id"]


def test_duplicate_template(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"], spec_json=_reference_spec())

    response = client.post(
        f"/report-templates/{template['id']}/duplicate",
        json={"name": "Duplicate Template"},
    )

    assert response.status_code == 201
    duplicate = response.json()
    assert duplicate["id"] != template["id"]
    assert duplicate["name"] == "Duplicate Template"
    assert duplicate["status"] == "draft"
    assert duplicate["active_version_id"] is not None


def test_archive_template(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])

    response = client.post(f"/report-templates/{template['id']}/archive")

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["archived_at"] is not None


def test_datasource_requirements_persistence_and_validation(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])

    created = _create_template(client, refs["workspace_id"])
    assert created["datasource_requirements"]["sources"] == ["facebook_pages", "instagram_business"]

    response = client.patch(
        f"/report-templates/{created['id']}",
        json={"datasource_requirements": {"mode": "all", "sources": []}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_datasource_requirements"


def test_schema_version_validation(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])
    spec = _reference_spec()
    spec["schema_version"] = "2.0"

    response = client.post(f"/report-templates/{template['id']}/versions", json={"spec_json": spec})

    assert response.status_code == 400
    assert "UNSUPPORTED_SCHEMA_VERSION" in response.json()["detail"]["message"]


def test_missing_template_id_rejected_for_persistence(client: TestClient) -> None:
    refs = _seed_identity()
    _authorize(refs["admin_id"])
    template = _create_template(client, refs["workspace_id"])
    spec = _reference_spec()
    spec["template_id"] = None

    response = client.post(f"/report-templates/{template['id']}/versions", json={"spec_json": spec})

    assert response.status_code == 400
    assert "MISSING_TEMPLATE_ID" in response.json()["detail"]["message"]


def test_facebook_instagram_reference_reportspec_persistence_round_trip() -> None:
    refs = _seed_identity()
    db = SessionLocal()
    try:
        template, version = seed_facebook_instagram_reference_template(db, created_by_user_id=refs["admin_id"])

        assert template.status == "published"
        assert template.published_version_id == version.id
        assert template.datasource_requirements["sources"] == ["facebook_pages", "instagram_business"]
        assert report_spec_from_json(report_spec_to_json(ReportSpec.from_dict(version.spec_json))) == (
            FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC
        )
    finally:
        db.close()


def test_authorization_boundaries(client: TestClient) -> None:
    refs = _seed_identity()

    unauthenticated = client.get("/report-templates")
    assert unauthenticated.status_code == 401

    _authorize(refs["member_id"], is_admin=False)
    forbidden = client.get("/report-templates")
    assert forbidden.status_code == 403

    _authorize(refs["admin_id"], is_admin=True)
    allowed = client.get("/report-templates")
    assert allowed.status_code == 200


def test_report_model_can_store_template_traceability() -> None:
    refs = _seed_identity()
    db = SessionLocal()
    try:
        template, version = seed_facebook_instagram_reference_template(db, created_by_user_id=refs["admin_id"])
        dataset = Dataset(workspace_id=refs["workspace_id"], name="Dataset", data={})
        db.add(dataset)
        db.flush()
        report = Report(
            workspace_id=refs["workspace_id"],
            dataset_id=dataset.id,
            name="Generated Report",
            report_template_id=template.id,
            report_template_version_id=version.id,
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        assert report.report_template_id == template.id
        assert report.report_template_version_id == version.id
    finally:
        db.close()
