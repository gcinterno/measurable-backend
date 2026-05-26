from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_ai_chat_followup.db")
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
from app.models import Conversation, Dataset, Message, Report, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password
import app.main as main_module
import app.services as services_module


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


AI_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Report.__table__,
    Conversation.__table__,
    Message.__table__,
]


@pytest.fixture(autouse=True)
def ai_schema():
    Base.metadata.drop_all(bind=engine, tables=AI_TABLES)
    Base.metadata.create_all(bind=engine, tables=AI_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=AI_TABLES)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "app.main.generate_workspace_ai_reply",
        lambda _db, conversation, history, user_message, chat_context=None: (
            f"reply-{conversation.id}-{len(history)}-{user_message}"
        ),
    )

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


def _seed_ai_fixture() -> dict[str, int]:
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
        dataset = Dataset(workspace_id=workspace.id, name="Dataset", description="Test", data={})
        db.add(dataset)
        db.flush()
        report = Report(workspace_id=workspace.id, dataset_id=dataset.id, name="Report", description="{}")
        db.add(report)
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id, "dataset_id": dataset.id, "report_id": report.id}
    finally:
        db.close()


def test_ai_chat_followup_works_with_only_conversation_id(client):
    refs = _seed_ai_fixture()

    first = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "First prompt",
            "workspace_id": refs["workspace_id"],
            "report_id": refs["report_id"],
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    conversation_id = first_payload["conversation_id"]
    assert first_payload["reply"].startswith(f"reply-{conversation_id}-1-")

    second = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "Second prompt",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["conversation_id"] == conversation_id
    assert second_payload["reply"].startswith(f"reply-{conversation_id}-3-")

    third = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "Third prompt",
            "conversation_id": conversation_id,
        },
    )
    assert third.status_code == 200
    third_payload = third.json()
    assert third_payload["conversation_id"] == conversation_id
    assert third_payload["reply"].startswith(f"reply-{conversation_id}-5-")

    messages = client.get(
        f"/ai/conversations/{conversation_id}/messages",
        headers=_auth_headers(refs["user_id"]),
    )
    assert messages.status_code == 200
    payload = messages.json()
    assert [item["role"] for item in payload] == ["user", "assistant", "user", "assistant", "user", "assistant"]


def test_ai_chat_ignores_mismatched_dataset_id_when_report_id_is_present(client):
    refs = _seed_ai_fixture()
    db = SessionLocal()
    try:
        other_dataset = Dataset(
            workspace_id=refs["workspace_id"],
            name="Other dataset",
            description="Other",
            data={},
        )
        db.add(other_dataset)
        db.commit()
        mismatched_dataset_id = other_dataset.id
    finally:
        db.close()

    response = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "Use the report context",
            "report_id": refs["report_id"],
            "dataset_id": mismatched_dataset_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"].startswith("reply-")


def test_ai_chat_context_is_summarized_and_does_not_embed_raw_dataset(client, monkeypatch):
    captured_context: dict[str, object] = {}

    def _capture_reply(_db, conversation, history, user_message, chat_context=None):
        captured_context["value"] = chat_context
        return f"reply-{conversation.id}-{len(history)}-{user_message}"

    monkeypatch.setattr(main_module, "generate_workspace_ai_reply", _capture_reply)

    db = SessionLocal()
    try:
        user = User(
            email="summary@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Summary User",
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
        dataset = Dataset(
            workspace_id=workspace.id,
            name="Dataset",
            description="Test dataset",
            data={
                "integration_type": "facebook_pages",
                "page_name": "Botanero NL",
                "normalized_report_metrics": {"reach": 1234, "engagement": 456},
                "rows": [{"campaign": f"C{i}", "sales": i, "notes": "x" * 300} for i in range(25)],
                "raw_csv": "a,b,c\n" + ("1,2,3\n" * 300),
            },
        )
        db.add(dataset)
        db.flush()
        report = Report(workspace_id=workspace.id, dataset_id=dataset.id, name="Report", description='{"summary":"%s"}' % ("y" * 500))
        db.add(report)
        db.commit()
        refs = {"user_id": user.id, "report_id": report.id}
    finally:
        db.close()

    response = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "How did the report perform?",
            "report_id": refs["report_id"],
            "page_context": {"rawTable": [{"campaign": f"huge-{index}", "csv": "z" * 1000} for index in range(6)]},
        },
    )

    assert response.status_code == 200
    context = captured_context["value"]
    assert isinstance(context, dict)
    dataset_snapshot = context["dataset"]
    assert "data" not in dataset_snapshot
    assert dataset_snapshot["summary"]["page_name"] == "Botanero NL"
    assert len(dataset_snapshot["summary"]["sample_rows"]) == 20
    first_row = dataset_snapshot["summary"]["sample_rows"][0]
    assert len(first_row["notes"]) < 220
    route_context = context["route_context"]
    assert "page_context" in route_context
    assert isinstance(route_context["page_context"], dict)
    assert route_context["page_context"]["rawTable"][-1] == {"truncated_items": 3}


def test_ai_chat_returns_clear_error_when_report_dataset_is_missing(client):
    refs = _seed_ai_fixture()
    db = SessionLocal()
    try:
        report = db.get(Report, refs["report_id"])
        assert report is not None
        report.dataset_id = 999999
        db.add(report)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/ai/chat",
        headers=_auth_headers(refs["user_id"]),
        json={
            "message": "Use the report context",
            "report_id": refs["report_id"],
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "dataset_not_found",
            "message": "No dataset found for this report.",
        }
    }


def test_ai_product_question_returns_redirect_response_without_inventing_features():
    response = services_module.generate_workspace_ai_reply(
        db=SessionLocal(),
        conversation=Conversation(id=1, workspace_id=1, title="AI"),
        history=[],
        user_message="¿Puedo subir un Excel para que se vuelva reporte?",
        chat_context={
            "workspace": {"id": 1, "snapshot": {"datasets_count": 1}},
            "report": {"id": 332, "title": "Botanero NL Overview"},
            "dataset": {"id": 196, "summary": {"metrics": {"reach": 78000}}},
        },
    )
    assert response == services_module.AI_PRODUCT_REDIRECT_RESPONSE


def test_ai_analysis_question_is_not_classified_as_product_question():
    assert services_module._is_ai_product_question(
        "Si tuve ventas de 3000 a 6000 promedio durante estos 28 días, ¿cómo lo relaciono con el alcance?"
    ) is False


def test_ai_system_prompt_is_specialized_for_report_analysis():
    prompt = services_module._build_ai_system_prompt(
        {
            "report": {"id": 332, "title": "Botanero NL Overview"},
            "dataset": {"id": 196, "summary": {"metrics": {"reach": 78000}}},
            "workspace": {"id": 1, "snapshot": {"datasets_count": 1}},
        }
    )
    assert "Report Analysis Assistant" in prompt
    assert "Do not invent product features" in prompt
    assert "If spend or investment is missing, do not calculate cost metrics" in prompt
