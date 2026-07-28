from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_facebook_pages_top_content_test.db")
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
from app.integrations import meta_ads as meta_ads_module
import app.main as main_module
from app.main import (
    META_RECORD_TYPE_FACEBOOK_PAGE,
    _build_facebook_page_top_content_item,
    _meta_page_account_external_id,
    _meta_token_account_external_id,
    _rank_facebook_page_top_content,
    app,
)
from app.models import (
    Dataset,
    DatasetFile,
    Integration,
    IntegrationAccount,
    IntegrationToken,
    MetaPage,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


FACEBOOK_TOP_CONTENT_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    IntegrationAccount.__table__,
    IntegrationToken.__table__,
    MetaPage.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
]


@pytest.fixture(autouse=True)
def facebook_top_content_schema():
    Base.metadata.drop_all(bind=engine, tables=FACEBOOK_TOP_CONTENT_TABLES)
    Base.metadata.create_all(bind=engine, tables=FACEBOOK_TOP_CONTENT_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=FACEBOOK_TOP_CONTENT_TABLES)


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


def _seed_facebook_pages_sync_fixture() -> dict[str, int]:
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
        integration = Integration(workspace_id=workspace.id, provider="meta", name="Meta", status="connected")
        db.add(integration)
        db.flush()

        token_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_meta_token_account_external_id(integration.id),
            display_name="Meta token store",
        )
        selected_page_account = IntegrationAccount(
            integration_id=integration.id,
            workspace_id=workspace.id,
            external_account_id=_meta_page_account_external_id("fb-page-1"),
            display_name="Botanero FB",
        )
        db.add_all([token_account, selected_page_account])
        db.flush()
        db.add(
            IntegrationToken(
                account_id=token_account.id,
                workspace_id=workspace.id,
                token_type="access_token",
                access_token="meta-access-token",
            )
        )
        db.add(
            MetaPage(
                integration_id=integration.id,
                user_id=user.id,
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                page_id="fb-page-1",
                name="Botanero FB",
                page_access_token="page-access-token",
            )
        )
        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "integration_id": integration.id,
        }
    finally:
        db.close()


def test_facebook_page_top_content_ranking_prefers_engaged_users_then_engagement_total():
    posts = [
        _build_facebook_page_top_content_item(
            {
                "id": "post-low-engaged",
                "message": "Low engaged users",
                "comments": {"summary": {"total_count": 5}},
                "reactions": {"summary": {"total_count": 10}},
                "shares": {"count": 2},
            },
            {"post_engaged_users": 20},
        ),
        _build_facebook_page_top_content_item(
            {
                "id": "post-high-fallback",
                "message": "No engaged users but strong visible engagement",
                "comments": {"summary": {"total_count": 25}},
                "reactions": {"summary": {"total_count": 30}},
                "shares": {"count": 4},
            },
            {},
        ),
        _build_facebook_page_top_content_item(
            {
                "id": "post-high-engaged",
                "message": "Highest engaged users",
                "comments": {"summary": {"total_count": 1}},
                "reactions": {"summary": {"total_count": 1}},
                "shares": {"count": 1},
            },
            {"post_engaged_users": 80},
        ),
    ]

    ranked = _rank_facebook_page_top_content([post for post in posts if post is not None], limit=2)

    assert [item["post_id"] for item in ranked] == ["post-high-engaged", "post-high-fallback"]
    assert ranked[1]["engagement_total"] == 59
    assert ranked[1]["score"] == 59


def test_facebook_pages_sync_fetches_top_content_without_comment_text(client, monkeypatch):
    refs = _seed_facebook_pages_sync_fixture()
    post_calls: list[dict] = []

    class FakeS3Client:
        def put_object(self, **_kwargs):
            return {}

    monkeypatch.setattr(main_module.boto3, "client", lambda *_args, **_kwargs: FakeS3Client())
    monkeypatch.setattr(main_module, "_refresh_meta_pages_authorized_cache", lambda *_args, **_kwargs: [])

    def fake_fetch_page_info(_token, page_id, fields="id,name"):
        if fields == "fan_count,followers_count":
            return {
                "id": page_id,
                "fan_count": 250,
                "followers_count": 300,
                "_meta_http_status_code": 200,
                "_meta_raw_body": "{}",
            }
        return {
            "id": page_id,
            "name": "Botanero FB",
            "_meta_http_status_code": 200,
            "_meta_raw_body": "{}",
        }

    monkeypatch.setattr(main_module, "fetch_page_info_with_metadata", fake_fetch_page_info)

    def fake_metric_payload(
        _access_token,
        _page_id,
        _page_name,
        timeframe_config,
        _integration_id,
        *,
        metric_name,
        label,
        daily_key,
    ):
        totals = {
            "page_posts_impressions_organic": 900,
            "page_views_total": 120,
            "page_post_engagements": 75,
            "page_actions_post_reactions_total": {"like": 61},
        }
        return {
            "metric_name": metric_name,
            "value": totals.get(metric_name),
            "end_time": timeframe_config["until"],
            daily_key: [{"date": timeframe_config["since"], "value": totals.get(metric_name)}],
        }

    monkeypatch.setattr(main_module, "_fetch_meta_pages_metric_payload", fake_metric_payload)

    def fake_fetch_page_posts(_token, page_id, limit=25, *, since=None, until=None, fields=None):
        post_calls.append(
            {
                "page_id": page_id,
                "limit": limit,
                "since": since,
                "until": until,
                "fields": fields,
            }
        )
        return [
            {
                "id": "post-1",
                "created_time": "2026-06-02T12:00:00+0000",
                "message": "Low performer",
                "permalink_url": "https://facebook.com/post-1",
                "attachments": {"data": [{"media_type": "photo"}]},
                "shares": {"count": 0},
                "reactions": {"summary": {"total_count": 4}},
                "comments": {"summary": {"total_count": 1}},
            },
            {
                "id": "post-2",
                "created_time": "2026-06-03T12:00:00+0000",
                "message": "Fallback post",
                "permalink_url": "https://facebook.com/post-2",
                "attachments": {"data": [{"media_type": "video"}]},
                "shares": {"count": 1},
                "reactions": {"summary": {"total_count": 40}},
                "comments": {
                    "summary": {"total_count": 5},
                    "data": [{"message": "private commenter text", "from": {"name": "A Person"}}],
                },
            },
            {
                "id": "post-3",
                "created_time": "2026-06-04T12:00:00+0000",
                "message": "Top engaged users",
                "permalink_url": "https://facebook.com/post-3",
                "shares": {"count": 0},
                "reactions": {"summary": {"total_count": 2}},
                "comments": {"summary": {"total_count": 1}},
            },
            {
                "id": "post-4",
                "created_time": "2026-06-05T12:00:00+0000",
                "message": "Middle post",
                "shares": {"count": 5},
                "reactions": {"summary": {"total_count": 20}},
                "comments": {"summary": {"total_count": 5}},
            },
            {
                "id": "post-5",
                "created_time": "2026-06-06T12:00:00+0000",
                "message": "Fifth post",
                "shares": {"count": 2},
                "reactions": {"summary": {"total_count": 6}},
                "comments": {"summary": {"total_count": 2}},
            },
            {
                "id": "post-6",
                "created_time": "2026-06-07T12:00:00+0000",
                "message": "Second by engaged users",
                "shares": {"count": 0},
                "reactions": {"summary": {"total_count": 1}},
                "comments": {"summary": {"total_count": 0}},
            },
        ]

    def fake_fetch_post_metrics(_token, post_id):
        if post_id == "post-2":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "meta_api_error",
                    "message": "Post insights unavailable",
                    "response_body": "{}",
                },
            )
        return {
            "post_impressions": {
                "post-1": 50,
                "post-3": 200,
                "post-4": 120,
                "post-5": 70,
                "post-6": 180,
            }.get(post_id),
            "post_impressions_unique": {
                "post-1": 40,
                "post-3": 150,
                "post-4": 90,
                "post-5": 60,
                "post-6": 140,
            }.get(post_id),
            "post_engaged_users": {
                "post-1": 5,
                "post-3": 90,
                "post-4": 30,
                "post-5": None,
                "post-6": 80,
            }.get(post_id),
        }

    monkeypatch.setattr(main_module, "fetch_page_posts", fake_fetch_page_posts)
    monkeypatch.setattr(main_module, "fetch_post_metrics", fake_fetch_post_metrics)

    response = client.post(
        "/integrations/meta/sync-pages",
        headers=_auth_headers(refs["user_id"]),
        json={
            "integration_id": refs["integration_id"],
            "page_id": "fb-page-1",
            "timeframe": "custom",
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
        },
    )

    assert response.status_code == 200
    assert post_calls == [
        {
            "page_id": "fb-page-1",
            "limit": 25,
            "since": "2026-06-01",
            "until": "2026-06-30",
            "fields": meta_ads_module.FACEBOOK_PAGE_TOP_CONTENT_POST_FIELDS,
        }
    ]
    db = SessionLocal()
    try:
        dataset = db.get(Dataset, response.json()["dataset_id"])
        assert dataset is not None
        dataset_data = dataset.data
        top_content = dataset_data["top_content"]
    finally:
        db.close()

    assert [item["post_id"] for item in top_content] == ["post-3", "post-6", "post-2", "post-4", "post-5"]
    fallback_item = next(item for item in top_content if item["post_id"] == "post-2")
    assert fallback_item["impressions"] is None
    assert fallback_item["reactions"] == 40
    assert fallback_item["comments"] == 5
    assert fallback_item["shares"] == 1
    assert fallback_item["engagement_total"] == 46
    assert fallback_item["score"] == 46
    assert "private commenter text" not in str(top_content)
    assert "from" not in fallback_item
    assert dataset_data["normalized_report_metrics"]["top_content"] == top_content


def test_facebook_page_posts_fields_request_only_comment_summary():
    fields = meta_ads_module.FACEBOOK_PAGE_TOP_CONTENT_POST_FIELDS

    assert "comments.summary(true).limit(0)" in fields
    assert "reactions.summary(true).limit(0)" in fields
    assert "comments{" not in fields
    assert "from" not in fields
