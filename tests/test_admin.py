from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

TEST_DB_PATH = Path("/tmp/measurable_admin_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")

from app.deps import get_db
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AccountDeletionFeedback,
    Conversation,
    Message,
    ReferralClick,
    ReferralConversion,
    ReferralPartner,
    Report,
    Subscription,
    User,
    UserAttribution,
    UserSuggestion,
    Workspace,
    WorkspaceMember,
)
from app.security import hash_password


ADMIN_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Conversation.__table__,
    Message.__table__,
    ReferralPartner.__table__,
    ReferralClick.__table__,
    UserAttribution.__table__,
    ReferralConversion.__table__,
    Report.__table__,
    AccountDeletionFeedback.__table__,
    UserSuggestion.__table__,
]


@pytest.fixture(autouse=True)
def admin_schema():
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS datasets")
        connection.exec_driver_sql(
            """
            CREATE TABLE datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                data TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    Base.metadata.drop_all(bind=engine, tables=ADMIN_TABLES)
    Base.metadata.create_all(bind=engine, tables=ADMIN_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=ADMIN_TABLES)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS datasets")


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


def _seed_admin_data() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    growth_date = previous_month_end - timedelta(days=1)
    current_month_day_1 = current_month_start + timedelta(days=1)
    current_month_day_2 = current_month_start + timedelta(days=2)
    current_month_day_3 = current_month_start + timedelta(days=3)
    previous_month_day_1 = previous_month_start + timedelta(days=1)
    previous_month_day_2 = previous_month_start + timedelta(days=2)
    previous_month_day_3 = previous_month_start + timedelta(days=3)
    previous_month_day_4 = previous_month_start + timedelta(days=4)
    db = SessionLocal()
    try:
        admin = User(
            email="admin@example.com",
            password_hash=hash_password("AdminPass123!"),
            full_name="Admin User",
            email_verified=True,
            auth_provider="email",
            is_admin=True,
            is_active=True,
            created_at=current_month_day_1,
            updated_at=current_month_day_1,
            last_login_at=current_month_day_1,
        )
        db.add(admin)
        db.flush()

        admin_workspace = Workspace(name="Admin Workspace")
        db.add(admin_workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=admin_workspace.id, user_id=admin.id, role="owner"))
        db.add(Subscription(workspace_id=admin_workspace.id, plan="free", status="active"))

        alice = User(
            email="alice@example.com",
            password_hash=hash_password("AlicePass123!"),
            full_name="Alice Example",
            email_verified=True,
            auth_provider="email",
            onboarding_completed=True,
            user_type="agency",
            goals=["track_growth", "client_reports"],
            platforms=["facebook", "instagram", "meta_ads"],
            is_active=True,
            created_at=current_month_day_2,
            updated_at=current_month_day_2,
            last_login_at=current_month_day_2,
        )
        db.add(alice)
        db.flush()
        alice_workspace = Workspace(name="Alice Workspace")
        db.add(alice_workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=alice_workspace.id, user_id=alice.id, role="owner"))
        db.add(Subscription(workspace_id=alice_workspace.id, plan="pro", status="active"))

        bob = User(
            email="bob@example.com",
            password_hash=hash_password("BobPass123!"),
            full_name="Bob Example",
            email_verified=True,
            auth_provider="google",
            onboarding_completed=False,
            is_active=True,
            created_at=previous_month_day_1,
            updated_at=previous_month_day_1,
            last_login_at=previous_month_day_1,
        )
        db.add(bob)
        db.flush()
        bob_workspace = Workspace(name="Bob Workspace")
        db.add(bob_workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=bob_workspace.id, user_id=bob.id, role="owner"))
        db.add(Subscription(workspace_id=bob_workspace.id, plan="free", status="active"))

        growth_user = User(
            email="growth@example.com",
            password_hash=hash_password("GrowthPass123!"),
            full_name="Growth Example",
            email_verified=True,
            auth_provider="email",
            onboarding_completed=False,
            is_active=True,
            created_at=growth_date,
            updated_at=growth_date,
            last_login_at=growth_date,
        )
        db.add(growth_user)
        db.flush()
        growth_workspace = Workspace(name="Growth Workspace")
        db.add(growth_workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=growth_workspace.id, user_id=growth_user.id, role="owner"))
        db.add(Subscription(workspace_id=growth_workspace.id, plan="free", status="active"))
        growth_dataset_id = db.execute(
            text(
                """
                INSERT INTO datasets (workspace_id, name, description, data, created_at, updated_at)
                VALUES (:workspace_id, :name, :description, :data, :created_at, :updated_at)
                """
            ),
            {
                "workspace_id": growth_workspace.id,
                "name": "Growth Dataset",
                "description": None,
                "data": "{}",
                "created_at": growth_date,
                "updated_at": growth_date,
            },
        ).lastrowid

        old_dataset_id = db.execute(
            text(
                """
                INSERT INTO datasets (workspace_id, name, description, data, created_at, updated_at)
                VALUES (:workspace_id, :name, :description, :data, :created_at, :updated_at)
                """
            ),
            {
                "workspace_id": alice_workspace.id,
                "name": "Old Dataset",
                "description": None,
                "data": "{}",
                "created_at": previous_month_day_2,
                "updated_at": previous_month_day_2,
            },
        ).lastrowid
        recent_dataset_id = db.execute(
            text(
                """
                INSERT INTO datasets (workspace_id, name, description, data, created_at, updated_at)
                VALUES (:workspace_id, :name, :description, :data, :created_at, :updated_at)
                """
            ),
            {
                "workspace_id": alice_workspace.id,
                "name": "Recent Dataset",
                "description": None,
                "data": "{}",
                "created_at": current_month_day_1,
                "updated_at": current_month_day_1,
            },
        ).lastrowid
        bob_dataset_id = db.execute(
            text(
                """
                INSERT INTO datasets (workspace_id, name, description, data, created_at, updated_at)
                VALUES (:workspace_id, :name, :description, :data, :created_at, :updated_at)
                """
            ),
            {
                "workspace_id": bob_workspace.id,
                "name": "Bob Dataset",
                "description": None,
                "data": "{}",
                "created_at": previous_month_day_3,
                "updated_at": previous_month_day_3,
            },
        ).lastrowid

        db.add(
            Report(
                workspace_id=alice_workspace.id,
                dataset_id=old_dataset_id,
                name="Old Report",
                description="{}",
                created_at=previous_month_day_2,
            )
        )
        db.add(
            Report(
                workspace_id=alice_workspace.id,
                dataset_id=recent_dataset_id,
                name="Recent Report 1",
                description="{}",
                created_at=current_month_day_1,
            )
        )
        db.add(
            Report(
                workspace_id=alice_workspace.id,
                dataset_id=recent_dataset_id,
                name="Recent Report 2",
                description="{}",
                created_at=current_month_day_2,
            )
        )
        alice_conversation = Conversation(
            workspace_id=alice_workspace.id,
            title="AI Draft",
            created_at=current_month_day_2,
        )
        db.add(alice_conversation)
        db.flush()
        db.add(
            Message(
                conversation_id=alice_conversation.id,
                role="user",
                content="Summarize this report",
                created_at=current_month_day_2,
            )
        )
        db.add(
            Message(
                conversation_id=alice_conversation.id,
                role="assistant",
                content="Summary",
                created_at=current_month_day_2,
            )
        )
        db.add(
            Report(
                workspace_id=bob_workspace.id,
                dataset_id=bob_dataset_id,
                name="Bob Report",
                description="{}",
                created_at=previous_month_day_3,
            )
        )
        db.add(
            Report(
                workspace_id=growth_workspace.id,
                dataset_id=growth_dataset_id,
                name="Growth Report",
                description="{}",
                created_at=growth_date,
            )
        )

        db.add(
            AccountDeletionFeedback(
                user_id=alice.id,
                email="deleted-one@example.com",
                reason="privacy_concerns",
                details="Please delete my account.",
                created_at=current_month_day_3,
            )
        )
        db.add(
            AccountDeletionFeedback(
                user_id=None,
                email="deleted-two@example.com",
                reason="too_expensive",
                details="Too expensive for my team.",
                created_at=previous_month_day_4,
            )
        )

        db.commit()
        return {"admin_email": admin.email, "admin_password": "AdminPass123!"}
    finally:
        db.close()


def _admin_token(client: TestClient) -> str:
    creds = _seed_admin_data()
    return _login_token(client, creds["admin_email"], creds["admin_password"])


def _login_token(client: TestClient, email: str, password: str) -> str:
    login = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200
    return login.json()["access_token"]


def _timeframe_reference() -> dict[str, datetime]:
    now = datetime.now(timezone.utc)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    return {
        "current_month_start": current_month_start,
        "growth_date": previous_month_end - timedelta(days=1),
        "current_month_day_1": current_month_start + timedelta(days=1),
        "current_month_day_2": current_month_start + timedelta(days=2),
        "current_month_day_3": current_month_start + timedelta(days=3),
        "previous_month_start": previous_month_start,
        "previous_month_day_1": previous_month_start + timedelta(days=1),
        "previous_month_day_2": previous_month_start + timedelta(days=2),
        "previous_month_day_3": previous_month_start + timedelta(days=3),
        "previous_month_day_4": previous_month_start + timedelta(days=4),
    }


def test_non_admin_cannot_access_admin_metrics(client):
    _seed_admin_data()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "alice@example.com").one()
        token = client.post(
            "/auth/login",
            data={"username": user.email, "password": "AlicePass123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).json()["access_token"]
    finally:
        db.close()

    response = client.get("/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_authenticated_user_creates_suggestion(client):
    _seed_admin_data()
    token = _login_token(client, "alice@example.com", "AlicePass123!")
    message = "  Please add custom chart colors.  "

    response = client.post(
        "/suggestions",
        json={"message": message},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["suggestion"]["message"] == message
    assert body["suggestion"]["status"] == "new"
    assert body["suggestion"]["source"] == "floating_suggestion_button"
    assert body["suggestion"]["workspace_id"] is not None


def test_unauthenticated_user_cannot_create_suggestion(client):
    response = client.post("/suggestions", json={"message": "Add saved templates."})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_token"


def test_empty_suggestion_message_fails(client):
    _seed_admin_data()
    token = _login_token(client, "alice@example.com", "AlicePass123!")

    response = client.post(
        "/suggestions",
        json={"message": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_message"


def test_non_admin_cannot_list_admin_suggestions(client):
    _seed_admin_data()
    token = _login_token(client, "alice@example.com", "AlicePass123!")

    response = client.get("/admin/suggestions", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_admin_can_list_suggestions(client):
    _seed_admin_data()
    alice_token = _login_token(client, "alice@example.com", "AlicePass123!")
    admin_token = _login_token(client, "admin@example.com", "AdminPass123!")
    create = client.post(
        "/suggestions",
        json={"message": "Add weekly executive summaries."},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert create.status_code == 201

    response = client.get("/admin/suggestions", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["message"] == "Add weekly executive summaries."
    assert items[0]["user_email"] == "alice@example.com"
    assert items[0]["user_name"] == "Alice Example"
    assert items[0]["workspace_name"] == "Alice Workspace"
    assert items[0]["status"] == "new"
    assert items[0]["created_at"] is not None


def test_admin_updates_suggestion_status(client):
    _seed_admin_data()
    alice_token = _login_token(client, "alice@example.com", "AlicePass123!")
    admin_token = _login_token(client, "admin@example.com", "AdminPass123!")
    create = client.post(
        "/suggestions",
        json={"message": "Let me reorder report slides."},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    suggestion_id = create.json()["suggestion"]["id"]

    response = client.patch(
        f"/admin/suggestions/{suggestion_id}",
        json={"status": "reviewed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reviewed"
    assert body["reviewed_at"] is not None
    assert body["reviewed_by"] is not None


def test_admin_metrics_users_users_and_insights(client):
    token = _admin_token(client)
    refs = _timeframe_reference()
    parse_dt = lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    metrics = client.get("/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert metrics.status_code == 200
    metrics_json = metrics.json()
    assert metrics_json["timeframe"] == "all"
    assert metrics_json["start_date"] is None
    assert metrics_json["end_date"] is None
    assert metrics_json["total_users"] == 4
    assert metrics_json["users_in_period"] == 4
    assert metrics_json["active_users_in_period"] == 4
    assert metrics_json["users_last_7_days"] == 3
    assert metrics_json["active_users_last_7_days"] == 3
    assert metrics_json["reports_in_period"] == 5
    assert metrics_json["onboarding_completed"] == 1
    assert metrics_json["onboarding_pending"] == 3
    assert metrics_json["onboarding_completion_rate"] == 25.0
    assert metrics_json["total_reports"] == 5
    assert metrics_json["reports_last_7_days"] == 3
    assert metrics_json["deletions_in_period"] == 2
    assert metrics_json["paid_users"] == 1
    assert metrics_json["free_users"] == 3
    assert metrics_json["mrr"] == 0.0
    assert metrics_json["users_growth_percent"] is None
    assert metrics_json["reports_growth_percent"] is None
    assert metrics_json["active_users_growth_percent"] is None
    assert 3 <= len(metrics_json["insights"]) <= 4
    assert any(item["type"] == "onboarding" for item in metrics_json["insights"])
    assert any(item["type"] == "growth" for item in metrics_json["insights"])
    assert any(item["type"] == "monetization" for item in metrics_json["insights"])
    assert metrics_json["daily_users"][0]["date"] == refs["previous_month_day_1"].date().isoformat()
    assert metrics_json["daily_users"][0]["users"] == 1
    assert metrics_json["daily_users"][-1]["date"] == refs["current_month_day_2"].date().isoformat()
    assert metrics_json["daily_users"][-1]["users"] == 1
    assert metrics_json["cumulative_users"][-1]["total_users"] == 4

    users = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert users.status_code == 200
    users_json = users.json()
    assert users_json["total"] == 4
    assert users_json["page"] == 1
    assert users_json["page_size"] == 25
    assert len(users_json["items"]) == 4
    alice = next(item for item in users_json["items"] if item["email"] == "alice@example.com")
    assert alice["plan"] == "pro"
    assert alice["reports_count"] == 3
    assert alice["reports_last_7_days"] == 2
    assert parse_dt(alice["last_report_created_at"]).date() == refs["current_month_day_2"].date()
    assert alice["onboarding_completed"] is True
    assert alice["health_status"] == "healthy"
    assert alice["health_score"] == 100
    assert "Email verified" in alice["health_reasons"]
    assert "Onboarding completed" in alice["health_reasons"]
    assert "Generated reports" in alice["health_reasons"]
    growth_user = next(item for item in users_json["items"] if item["email"] == "growth@example.com")
    assert growth_user["reports_count"] == 1
    assert growth_user["reports_last_7_days"] == 1
    assert parse_dt(growth_user["last_report_created_at"]).date() == refs["growth_date"].date()
    assert growth_user["health_status"] == "active"
    assert growth_user["health_score"] == 65
    admin_user = next(item for item in users_json["items"] if item["email"] == "admin@example.com")
    assert admin_user["reports_count"] == 0
    assert admin_user["reports_last_7_days"] == 0
    assert admin_user["last_report_created_at"] is None
    assert admin_user["health_status"] == "at_risk"
    assert admin_user["health_score"] == 30

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"
    assert me.json()["is_admin"] is True

    insights = client.get("/admin/insights", headers={"Authorization": f"Bearer {token}"})
    assert insights.status_code == 200
    insights_json = insights.json()
    assert insights_json["onboarding"]["user_types"]["agency"] == 1
    assert insights_json["onboarding"]["goals"]["track_growth"] == 1
    assert insights_json["onboarding"]["platforms"]["facebook"] == 1
    assert insights_json["onboarding"]["completed"] == 1
    assert insights_json["onboarding"]["pending"] == 3
    assert insights_json["onboarding"]["completion_rate"] == 25.0
    assert insights_json["deletions"]["total"] == 2
    assert insights_json["deletions"]["last_7_days"] == 1
    assert insights_json["deletions"]["reasons"]["privacy_concerns"] == 1
    assert insights_json["deletions"]["reasons"]["too_expensive"] == 1
    assert insights_json["deletions"]["recent_feedback"][0]["email"] == "deleted-one@example.com"


def test_admin_users_health_status_filter(client):
    token = _admin_token(client)
    refs = _timeframe_reference()
    db = SessionLocal()
    try:
        dormant = User(
            email="dormant@example.com",
            password_hash=hash_password("DormantPass123!"),
            full_name="Dormant User",
            email_verified=False,
            auth_provider="email",
            onboarding_completed=False,
            is_active=True,
            created_at=refs["current_month_day_3"],
            updated_at=refs["current_month_day_3"],
            last_login_at=None,
        )
        db.add(dormant)
        db.commit()
    finally:
        db.close()

    healthy = client.get(
        "/admin/users",
        params={"health_status": "healthy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert healthy.status_code == 200
    healthy_json = healthy.json()
    assert healthy_json["total"] == 1
    assert len(healthy_json["items"]) == 1
    assert healthy_json["items"][0]["email"] == "alice@example.com"
    assert healthy_json["items"][0]["health_status"] == "healthy"

    dormant_response = client.get(
        "/admin/users",
        params={"health_status": "dormant"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dormant_response.status_code == 200
    dormant_json = dormant_response.json()
    assert dormant_json["total"] == 1
    assert len(dormant_json["items"]) == 1
    assert dormant_json["items"][0]["email"] == "dormant@example.com"
    assert dormant_json["items"][0]["health_status"] == "dormant"


def test_admin_metrics_this_month_last_month_and_custom(client):
    token = _admin_token(client)
    refs = _timeframe_reference()

    all_time = client.get(
        "/admin/metrics",
        params={"timeframe": "all"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert all_time.status_code == 200
    all_time_json = all_time.json()
    assert all_time_json["timeframe"] == "all"
    assert all_time_json["total_users"] == 4
    assert all_time_json["users_in_period"] == 4
    assert all_time_json["reports_in_period"] == 5
    assert any(item["type"] == "onboarding" for item in all_time_json["insights"])
    assert any(item["type"] == "activation" for item in all_time_json["insights"])
    assert any(item["type"] == "monetization" for item in all_time_json["insights"])

    this_month = client.get(
        "/admin/metrics",
        params={"timeframe": "this_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert this_month.status_code == 200
    this_month_json = this_month.json()
    assert this_month_json["timeframe"] == "this_month"
    assert this_month_json["users_in_period"] == 2
    assert this_month_json["active_users_in_period"] == 2
    assert this_month_json["reports_in_period"] == 2
    assert this_month_json["onboarding_completed_in_period"] == 1
    assert this_month_json["onboarding_completion_rate"] == 50.0
    assert this_month_json["deletions_in_period"] == 1
    assert this_month_json["users_growth_percent"] == 100.0
    assert this_month_json["reports_growth_percent"] == 100.0
    assert this_month_json["active_users_growth_percent"] == 100.0
    assert any(item["type"] == "growth" and item["severity"] == "positive" for item in this_month_json["insights"])
    assert any(item["type"] == "onboarding" for item in this_month_json["insights"])
    assert any(item["type"] == "monetization" for item in this_month_json["insights"])
    assert this_month_json["start_date"] == refs["current_month_start"].date().isoformat()
    assert this_month_json["end_date"] == datetime.now(timezone.utc).date().isoformat()
    daily_users_map = {item["date"]: item["users"] for item in this_month_json["daily_users"]}
    daily_reports_map = {item["date"]: item["reports"] for item in this_month_json["daily_reports"]}
    cumulative_users_map = {item["date"]: item["total_users"] for item in this_month_json["cumulative_users"]}
    assert daily_users_map[refs["current_month_start"].date().isoformat()] == 0
    assert daily_users_map[(refs["current_month_start"] + timedelta(days=1)).date().isoformat()] == 1
    assert daily_users_map[(refs["current_month_start"] + timedelta(days=2)).date().isoformat()] == 1
    assert daily_reports_map[refs["current_month_start"].date().isoformat()] == 0
    assert daily_reports_map[(refs["current_month_start"] + timedelta(days=1)).date().isoformat()] == 1
    assert daily_reports_map[(refs["current_month_start"] + timedelta(days=2)).date().isoformat()] == 1
    assert cumulative_users_map[refs["current_month_start"].date().isoformat()] == 0
    assert cumulative_users_map[(refs["current_month_start"] + timedelta(days=1)).date().isoformat()] == 1
    assert cumulative_users_map[(refs["current_month_start"] + timedelta(days=2)).date().isoformat()] == 2

    last_month = client.get(
        "/admin/metrics",
        params={"timeframe": "last_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert last_month.status_code == 200
    last_month_json = last_month.json()
    assert last_month_json["timeframe"] == "last_month"
    assert last_month_json["users_in_period"] == 2
    assert last_month_json["active_users_in_period"] == 2
    assert last_month_json["reports_in_period"] == 3
    assert last_month_json["onboarding_completed_in_period"] == 0
    assert last_month_json["onboarding_completion_rate"] == 0.0
    assert last_month_json["deletions_in_period"] == 1
    assert last_month_json["start_date"] == refs["previous_month_start"].date().isoformat()
    assert last_month_json["end_date"] == (refs["current_month_start"].date() - timedelta(days=1)).isoformat()
    assert last_month_json["users_growth_percent"] is None
    assert last_month_json["reports_growth_percent"] is None
    assert last_month_json["active_users_growth_percent"] is None
    assert any(item["type"] == "growth" for item in last_month_json["insights"])
    assert any(item["type"] == "onboarding" for item in last_month_json["insights"])
    assert any(item["type"] == "monetization" for item in last_month_json["insights"])

    custom_start = refs["previous_month_start"].date()
    custom_end = (refs["previous_month_start"] + timedelta(days=2)).date()
    custom = client.get(
        "/admin/metrics",
        params={
            "timeframe": "custom",
            "start_date": custom_start.isoformat(),
            "end_date": custom_end.isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert custom.status_code == 200
    custom_json = custom.json()
    assert custom_json["timeframe"] == "custom"
    assert custom_json["start_date"] == custom_start.isoformat()
    assert custom_json["end_date"] == custom_end.isoformat()
    assert custom_json["users_in_period"] == 1
    assert custom_json["active_users_in_period"] == 1
    assert custom_json["reports_in_period"] == 1
    assert custom_json["onboarding_completed_in_period"] == 0
    assert custom_json["onboarding_completion_rate"] == 0.0
    assert custom_json["deletions_in_period"] == 0
    assert custom_json["users_growth_percent"] is None
    assert custom_json["reports_growth_percent"] is None
    assert custom_json["active_users_growth_percent"] is None
    assert any(item["type"] == "growth" for item in custom_json["insights"])
    assert any(item["type"] == "onboarding" for item in custom_json["insights"])
    assert any(item["type"] == "monetization" for item in custom_json["insights"])


def test_admin_funnel_timeframes(client):
    token = _admin_token(client)
    refs = _timeframe_reference()

    def step_map(response_json):
        return {item["name"]: item["count"] for item in response_json["steps"]}

    def step_detail(response_json, name: str):
        return next(item for item in response_json["steps"] if item["name"] == name)

    all_time = client.get("/admin/funnel", headers={"Authorization": f"Bearer {token}"})
    assert all_time.status_code == 200
    assert step_map(all_time.json()) == {
        "Signups": 4,
        "Onboarding": 1,
        "Reports created": 3,
        "AI Assistant used": 1,
        "Activated users": 2,
        "Paid Users": 1,
    }
    all_time_json = all_time.json()
    assert all_time_json["summary"] == {
        "total_conversion": 25.0,
        "biggest_dropoff_stage": "Onboarding",
        "strongest_step": "Signups",
    }
    assert step_detail(all_time_json, "Signups")["conversion_from_start"] == 100.0
    assert step_detail(all_time_json, "Onboarding")["conversion_from_previous"] == 25.0
    assert step_detail(all_time_json, "Reports created")["conversion_from_previous"] == 300.0
    assert step_detail(all_time_json, "AI Assistant used")["conversion_from_previous"] == 33.33

    this_month = client.get(
        "/admin/funnel",
        params={"timeframe": "this_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert this_month.status_code == 200
    assert step_map(this_month.json()) == {
        "Signups": 2,
        "Onboarding": 1,
        "Reports created": 1,
        "AI Assistant used": 1,
        "Activated users": 1,
        "Paid Users": 1,
    }
    this_month_json = this_month.json()
    assert this_month_json["summary"]["total_conversion"] == 50.0
    assert this_month_json["summary"]["strongest_step"] == "Signups"
    assert this_month_json["summary"]["biggest_dropoff_stage"] == "Onboarding"
    assert step_detail(this_month_json, "Onboarding")["conversion_from_start"] == 50.0
    assert step_detail(this_month_json, "Reports created")["conversion_from_previous"] == 100.0

    last_month = client.get(
        "/admin/funnel",
        params={"timeframe": "last_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert last_month.status_code == 200
    assert step_map(last_month.json()) == {
        "Signups": 2,
        "Onboarding": 0,
        "Reports created": 2,
        "AI Assistant used": 0,
        "Activated users": 1,
        "Paid Users": 0,
    }
    last_month_json = last_month.json()
    assert last_month_json["summary"]["total_conversion"] == 0.0
    assert last_month_json["summary"]["strongest_step"] == "Signups"
    assert last_month_json["summary"]["biggest_dropoff_stage"] == "Onboarding"

    custom = client.get(
        "/admin/funnel",
        params={
            "timeframe": "custom",
            "start_date": refs["previous_month_start"].date().isoformat(),
            "end_date": (refs["previous_month_start"] + timedelta(days=2)).date().isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert custom.status_code == 200
    assert step_map(custom.json()) == {
        "Signups": 1,
        "Onboarding": 0,
        "Reports created": 0,
        "AI Assistant used": 0,
        "Activated users": 0,
        "Paid Users": 0,
    }


def test_admin_product_metrics_timeframes(client):
    token = _admin_token(client)
    refs = _timeframe_reference()

    all_time = client.get("/admin/product-metrics", headers={"Authorization": f"Bearer {token}"})
    assert all_time.status_code == 200
    all_time_json = all_time.json()
    assert all_time_json["total_users"] == 4
    assert all_time_json["users_with_reports"] == 3
    assert all_time_json["users_with_2_reports"] == 1
    assert all_time_json["users_used_ai"] == 1
    assert all_time_json["reports_per_user"] == 1.25
    assert all_time_json["ai_usage_rate"] == 25.0
    assert all_time_json["repeat_usage_rate"] == 33.33
    assert all_time_json["time_to_first_report_unit"] == "hours"
    assert all_time_json["avg_time_to_first_report"] == 16.0

    this_month = client.get(
        "/admin/product-metrics",
        params={"timeframe": "this_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert this_month.status_code == 200
    this_month_json = this_month.json()
    assert this_month_json["total_users"] == 2
    assert this_month_json["users_with_reports"] == 1
    assert this_month_json["users_with_2_reports"] == 1
    assert this_month_json["users_used_ai"] == 1
    assert this_month_json["reports_per_user"] == 1.0
    assert this_month_json["ai_usage_rate"] == 50.0
    assert this_month_json["repeat_usage_rate"] == 100.0
    assert this_month_json["avg_time_to_first_report"] == 0.0

    last_month = client.get(
        "/admin/product-metrics",
        params={"timeframe": "last_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert last_month.status_code == 200
    last_month_json = last_month.json()
    assert last_month_json["total_users"] == 2
    assert last_month_json["users_with_reports"] == 2
    assert last_month_json["users_with_2_reports"] == 0
    assert last_month_json["users_used_ai"] == 0
    assert last_month_json["reports_per_user"] == 1.0
    assert last_month_json["ai_usage_rate"] == 0.0
    assert last_month_json["repeat_usage_rate"] == 0.0
    assert last_month_json["avg_time_to_first_report"] == 24.0

    custom = client.get(
        "/admin/product-metrics",
        params={
            "timeframe": "custom",
            "start_date": refs["previous_month_start"].date().isoformat(),
            "end_date": (refs["previous_month_start"] + timedelta(days=2)).date().isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert custom.status_code == 200
    custom_json = custom.json()
    assert custom_json["total_users"] == 1
    assert custom_json["users_with_reports"] == 0
    assert custom_json["users_with_2_reports"] == 0
    assert custom_json["users_used_ai"] == 0
    assert custom_json["reports_per_user"] == 0.0
    assert custom_json["ai_usage_rate"] == 0.0
    assert custom_json["repeat_usage_rate"] == 0.0
    assert custom_json["avg_time_to_first_report"] == 0.0


def test_admin_cohorts_timeframes(client):
    token = _admin_token(client)
    refs = _timeframe_reference()

    previous_month_start = refs["previous_month_start"]
    older_month_end = previous_month_start - timedelta(days=1)
    older_month_start = older_month_end.replace(day=1)
    extra_signup_dates = [older_month_start + timedelta(days=offset) for offset in range(1, 8)]
    db = SessionLocal()
    try:
        for index, signup_date in enumerate(extra_signup_dates, start=1):
            extra_user = User(
                email=f"extra-{index}@example.com",
                password_hash=hash_password(f"ExtraPass123!{index}"),
                full_name=f"Extra User {index}",
                email_verified=True,
                auth_provider="email",
                onboarding_completed=False,
                is_active=True,
                created_at=signup_date,
                updated_at=signup_date,
                last_login_at=signup_date,
            )
            db.add(extra_user)
            db.flush()
            extra_workspace = Workspace(name=f"Extra Workspace {index}")
            db.add(extra_workspace)
            db.flush()
            db.add(WorkspaceMember(workspace_id=extra_workspace.id, user_id=extra_user.id, role="owner"))
            db.add(Subscription(workspace_id=extra_workspace.id, plan="free", status="active"))
            dataset_id = db.execute(
                text(
                    """
                    INSERT INTO datasets (workspace_id, name, description, data, created_at, updated_at)
                    VALUES (:workspace_id, :name, :description, :data, :created_at, :updated_at)
                    """
                ),
                {
                    "workspace_id": extra_workspace.id,
                    "name": f"Extra Dataset {index}",
                    "description": None,
                    "data": "{}",
                    "created_at": signup_date,
                    "updated_at": signup_date,
                },
            ).lastrowid
            db.add(
                Report(
                    workspace_id=extra_workspace.id,
                    dataset_id=dataset_id,
                    name=f"Extra Report {index}",
                    description="{}",
                    created_at=signup_date,
                )
            )
        db.commit()
    finally:
        db.close()

    def cohort_map(response_json):
        return {item["date"]: item for item in response_json["cohorts"]}

    expected_all_time_dates = [
        *(older_month_start + timedelta(days=offset) for offset in range(2, 8)),
        refs["previous_month_day_1"].date().isoformat(),
        refs["growth_date"].date().isoformat(),
        refs["current_month_day_1"].date().isoformat(),
        refs["current_month_day_2"].date().isoformat(),
    ]
    expected_all_time_dates = [
        value.date().isoformat() if hasattr(value, "date") else value for value in expected_all_time_dates
    ]

    all_time = client.get("/admin/cohorts", headers={"Authorization": f"Bearer {token}"})
    assert all_time.status_code == 200
    all_time_json = all_time.json()
    assert len(all_time_json["cohorts"]) == 10
    assert "averages" in all_time_json
    assert set(all_time_json["averages"].keys()) == {"day_1", "day_3", "day_7", "day_14", "day_30"}
    all_time_map = cohort_map(all_time_json)
    assert list(all_time_map.keys()) == expected_all_time_dates
    assert all_time_map[refs["previous_month_day_1"].date().isoformat()]["size"] == 1
    assert all_time_map[refs["previous_month_day_1"].date().isoformat()]["retention"]["day_0"] == 100.0
    assert all_time_map[refs["previous_month_day_1"].date().isoformat()]["retention"]["day_3"] == 0.0
    assert all_time_map[refs["current_month_day_2"].date().isoformat()]["retention"]["day_0"] == 100.0

    this_month = client.get(
        "/admin/cohorts",
        params={"timeframe": "this_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert this_month.status_code == 200
    this_month_json = this_month.json()
    assert len(this_month_json["cohorts"]) == 2
    assert "averages" in this_month_json
    this_month_map = cohort_map(this_month_json)
    assert this_month_map[refs["current_month_day_1"].date().isoformat()]["retention"]["day_0"] == 100.0
    assert this_month_map[refs["current_month_day_2"].date().isoformat()]["retention"]["day_1"] == 0.0

    last_month = client.get(
        "/admin/cohorts",
        params={"timeframe": "last_month"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert last_month.status_code == 200
    last_month_json = last_month.json()
    assert len(last_month_json["cohorts"]) == 2
    assert "averages" in last_month_json
    last_month_map = cohort_map(last_month_json)
    assert last_month_map[refs["previous_month_day_1"].date().isoformat()]["size"] == 1
    assert last_month_map[refs["growth_date"].date().isoformat()]["retention"]["day_0"] == 100.0

    custom = client.get(
        "/admin/cohorts",
        params={
            "timeframe": "custom",
            "start_date": refs["previous_month_start"].date().isoformat(),
            "end_date": (refs["previous_month_start"] + timedelta(days=2)).date().isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert custom.status_code == 200
    custom_json = custom.json()
    assert len(custom_json["cohorts"]) == 1
    assert "averages" in custom_json
    custom_cohort = custom_json["cohorts"][0]
    assert custom_cohort["date"] == refs["previous_month_day_1"].date().isoformat()
    assert custom_cohort["size"] == 1
    assert custom_cohort["retention"]["day_0"] == 100.0
