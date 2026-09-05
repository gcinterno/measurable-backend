from __future__ import annotations

import json
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

from app import main as main_module
from app.db import Base, SessionLocal, engine
from app.deps import get_db
from app.models import Dataset, Integration, IntegrationAccount, ReferralConversion, Report, ReportBlock, ReportSource, ReportVersion, Subscription, User, UserAttribution, Workspace, WorkspaceMember
from app.report_recipe_validation import validate_blocks_against_recipe
from app.report_recipes import FACEBOOK_INSTAGRAM_10_RECIPE, ReportRecipe, ReportRecipeSlide
from app.security import create_access_token, hash_password


app = main_module.app


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
    UserAttribution.__table__,
    ReferralConversion.__table__,
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


def _semantic_name_from_payload_block(block: dict) -> str | None:
    if block.get("semantic_name"):
        return str(block["semantic_name"])
    data = block.get("data_json")
    if isinstance(data, str):
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return decoded.get("semantic_name")
    if isinstance(data, dict):
        return data.get("semantic_name")
    return None


def _canonical_multi_source_payload(refs: dict[str, int]) -> dict:
    return {
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
                "provider": "instagram_business_login",
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
    }


def _persisted_block_specs(report_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        report_version = (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report_id)
            .order_by(ReportVersion.version.asc())
            .one()
        )
        blocks = (
            db.query(ReportBlock)
            .filter(ReportBlock.report_version_id == report_version.id)
            .order_by(ReportBlock.order.asc())
            .all()
        )
        return [
            {
                "type": block.type,
                "order": block.order,
                "data_json": block.data_json,
                "editable_fields_json": block.editable_fields_json,
            }
            for block in blocks
        ]
    finally:
        db.close()


def _block_payloads(block_specs: list[dict]) -> list[dict]:
    return [json.loads(str(block["data_json"])) for block in block_specs]


def _assert_primary_value_fields(payload: dict, expected_value) -> None:
    assert payload["value"] == expected_value
    assert payload["current_value"] == expected_value
    assert payload["primary_value"] == expected_value
    assert payload["metric_value"] == expected_value
    assert payload["total"] == expected_value
    assert payload["canonical_metric_resolution"]["value"] == expected_value


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
                "impressions": None,
                "organic_impressions": 8700,
                "organic_impressions_total": 8700,
                "page_views_total": 410,
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
                "daily_organic_impressions": [
                    {"date": "2026-04-01", "value": 310},
                    {"date": "2026-04-02", "value": 360},
                ],
                "daily_page_views": [
                    {"date": "2026-04-01", "value": 190},
                    {"date": "2026-04-02", "value": 220},
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
                        "reach": 900,
                        "engagement": 65,
                        "created_time": "2026-04-02",
                    }
                ],
                "normalized_report_metrics": {
                    "organic_impressions_total": 8700,
                    "daily_organic_impressions": [
                        {"date": "2026-04-01", "value": 310},
                        {"date": "2026-04-02", "value": 360},
                    ],
                    "page_views_total": 410,
                    "followers_growth_daily": [
                        {"date": "2026-04-01", "value": 4},
                        {"date": "2026-04-02", "value": 6},
                    ]
                },
                "unavailable_metrics": {
                    "impressions": "General page impressions are not available in this dataset."
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
                "followers_count": 1800,
                "media_count": 42,
                "reach": 7600,
                "engagement": None,
                "total_interactions": None,
                "impressions": None,
                "views": 12000,
                "profile_views": 630,
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
                "views_daily": [
                    {"date": "2026-04-01", "value": 420},
                    {"date": "2026-04-02", "value": 470},
                ],
                "daily_engagement": [],
                "profile_views_daily": [
                    {"date": "2026-04-01", "value": 300},
                    {"date": "2026-04-02", "value": 330},
                ],
                "recent_posts": [
                    {
                        "id": "ig-post-1",
                        "caption": "Instagram reel performance",
                        "reach": 1100,
                        "views": 1900,
                        "engagement": 160,
                        "likes": 110,
                        "comments": 16,
                        "saves": 21,
                        "shares": 13,
                        "created_time": "2026-04-03",
                    }
                ],
                "posts_analyzed_count": 1,
                "normalized_report_metrics": {
                    "views_total": 12000,
                    "views_daily": [
                        {"date": "2026-04-01", "value": 420},
                        {"date": "2026-04-02", "value": 470},
                    ],
                    "followers_total": 1800,
                    "media_count": 42,
                    "posts_analyzed_count": 1,
                },
                "unavailable_metrics": {
                    "impressions": "metric[0] must be one of: reach, views, total_interactions"
                },
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
                    "provider": "instagram_business_login",
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
        semantic_order = [json.loads(block.data_json).get("semantic_name") for block in blocks]
        assert len(blocks) == 10
        assert blocks[0].type == "title"
        assert blocks[-1].type == "text"
        assert semantic_order == [
            "cover",
            "reach",
            "impressions",
            "engagement",
            "page_visits",
            "audience_growth",
            "content_activity",
            "top_performing_content",
            "executive_insights",
            "recommendations",
        ]
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
    assert version_payload["blocks"][1]["type"] == "stat"
    assert [_semantic_name_from_payload_block(block) for block in version_payload["blocks"]] == [
        "cover",
        "reach",
        "impressions",
        "engagement",
        "page_visits",
        "audience_growth",
        "content_activity",
        "top_performing_content",
        "executive_insights",
        "recommendations",
    ]


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
                    "provider": "instagram_business_login",
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


def test_canonical_multi_source_report_uses_catalog_aligned_recipe_builder_by_default(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    import app.report_recipe_builder as recipe_builder_module

    refs = _seed_sources()
    original_recipe_builder = recipe_builder_module.build_facebook_instagram_10_blocks_from_recipe
    recipe_calls: list[str] = []

    def wrapped_recipe_builder(recipe, context, **kwargs):
        recipe_calls.append(recipe.id)
        return original_recipe_builder(recipe, context, **kwargs)

    monkeypatch.delenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, raising=False)
    monkeypatch.setattr(
        recipe_builder_module,
        "build_facebook_instagram_10_blocks_from_recipe",
        wrapped_recipe_builder,
    )

    recipe_response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=_canonical_multi_source_payload(refs),
    )

    assert recipe_response.status_code == 200
    assert recipe_calls == ["facebook_instagram_10"]
    assert recipe_response.json()["integration_metadata"]["integration_type"] == "multi_source"
    assert recipe_response.json()["integration_metadata"]["integration_display_name"] == "Facebook + Instagram"
    assert recipe_response.json()["integration_metadata"]["source_name"] == "Facebook + Instagram"
    assert recipe_response.json()["report_sources"][0]["provider"] == "meta"
    assert recipe_response.json()["report_sources"][1]["provider"] == "instagram_business_login"
    assert [source["source_type"] for source in recipe_response.json()["report_sources"]] == [
        "facebook_pages",
        "instagram_business",
    ]
    recipe_block_specs = _persisted_block_specs(recipe_response.json()["id"])
    recipe_payloads = _block_payloads(recipe_block_specs)
    assert len(recipe_block_specs) == 10
    assert [block["order"] for block in recipe_block_specs] == list(range(1, 11))
    assert [payload["semantic_name"] for payload in recipe_payloads] == [
        "cover",
        "reach",
        "impressions",
        "engagement",
        "page_visits",
        "audience_growth",
        "content_activity",
        "top_performing_content",
        "executive_insights",
        "recommendations",
    ]
    assert validate_blocks_against_recipe(recipe_block_specs, FACEBOOK_INSTAGRAM_10_RECIPE).valid
    assert all(isinstance(block["data_json"], str) for block in recipe_block_specs)
    assert all(isinstance(block["editable_fields_json"], str) for block in recipe_block_specs)
    assert recipe_payloads[2]["canonical_semantic"] == "visibility"
    _assert_primary_value_fields(recipe_payloads[2], 8700)
    assert recipe_payloads[2]["provenance"]["aggregation_method"] == "not_comparable"
    assert recipe_payloads[2]["canonical_metric_resolution"]["aggregation_method"] == "not_comparable"
    assert recipe_payloads[2]["previous_value"] is None
    instagram_visibility = next(
        source
        for source in recipe_payloads[2]["source_contributions"]
        if source["source_type"] == "instagram_business"
    )
    assert instagram_visibility["source_metric"] == "views"
    assert instagram_visibility["value"] == 12000
    assert recipe_payloads[3]["canonical_semantic"] == "engagement"
    _assert_primary_value_fields(recipe_payloads[3], 480)
    instagram_engagement = next(
        source
        for source in recipe_payloads[3]["source_contributions"]
        if source["source_type"] == "instagram_business"
    )
    assert instagram_engagement["source_metric"] == "media.engagement"
    assert instagram_engagement["provenance"]["fallback_used"] is True
    assert recipe_payloads[5]["canonical_semantic"] == "audience_size"
    _assert_primary_value_fields(recipe_payloads[5], 3000)
    assert recipe_payloads[5]["audience_value_type"] == "base_size"
    _assert_primary_value_fields(recipe_payloads[6], 2)
    assert recipe_payloads[6]["media_count_contributions"][0]["value"] == 42
    assert {
        item["source"]
        for item in recipe_payloads[7]["top_posts"]
    } == {"Facebook Page", "Instagram Account"}
    assert all(item["ranking_score"] is not None for item in recipe_payloads[7]["top_posts"])
    assert all("engagement_interactions" in item for item in recipe_payloads[7]["top_posts"])

    monkeypatch.setenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, "legacy")
    recipe_calls.clear()
    legacy_response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=_canonical_multi_source_payload(refs),
    )

    assert legacy_response.status_code == 200
    assert recipe_calls == []
    assert legacy_response.json()["integration_metadata"]["integration_type"] == "multi_source"
    legacy_block_specs = _persisted_block_specs(legacy_response.json()["id"])
    legacy_payloads = _block_payloads(legacy_block_specs)
    assert legacy_block_specs != recipe_block_specs
    assert len(legacy_block_specs) == 10
    assert [payload["semantic_name"] for payload in legacy_payloads] == [
        payload["semantic_name"] for payload in recipe_payloads
    ]
    assert "canonical_semantic" not in legacy_payloads[2]


def test_real_provider_pair_visibility_does_not_persist_unsupported_instagram_impressions_as_zero(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    refs = _seed_sources()
    db = SessionLocal()
    try:
        instagram_dataset = db.get(Dataset, refs["dataset_two_id"])
        assert instagram_dataset is not None
        data = dict(instagram_dataset.data)
        data["views"] = None
        data["views_daily"] = []
        normalized = dict(data.get("normalized_report_metrics") or {})
        normalized["views_total"] = None
        normalized["views_daily"] = []
        data["normalized_report_metrics"] = normalized
        unavailable_metrics = dict(data.get("unavailable_metrics") or {})
        unavailable_metrics["views"] = "empty_response"
        unavailable_metrics["impressions"] = "metric[0] must be one of: reach, views, total_interactions"
        data["unavailable_metrics"] = unavailable_metrics
        instagram_dataset.data = data
        db.add(instagram_dataset)
        db.commit()
    finally:
        db.close()

    monkeypatch.delenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, raising=False)
    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=_canonical_multi_source_payload(refs),
    )

    assert response.status_code == 200
    payloads = _block_payloads(_persisted_block_specs(response.json()["id"]))
    visibility_payload = payloads[2]
    assert visibility_payload["semantic_name"] == "impressions"
    assert visibility_payload["canonical_semantic"] == "visibility"
    _assert_primary_value_fields(visibility_payload, 8700)
    assert visibility_payload["value"] != 0
    assert visibility_payload["canonical_metric_resolution"]["aggregation_method"] == "not_comparable"

    instagram_visibility = next(
        source
        for source in visibility_payload["source_contributions"]
        if source["source_type"] == "instagram_business"
    )
    assert instagram_visibility["source_metric"] == "views"
    assert instagram_visibility["value"] is None
    assert instagram_visibility["support_status"] == "empty"


def test_facebook_instagram_10_recipe_builder_invalid_env_fails_closed(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    refs = _seed_sources()
    monkeypatch.setenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, "unsupported")

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=_canonical_multi_source_payload(refs),
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "facebook_instagram_10_recipe_builder_config_invalid"


@pytest.mark.parametrize(
    "recipe",
    [
        None,
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Malformed Facebook + Instagram 10",
            version=1,
            slides=(ReportRecipeSlide(order=1, semantic_name="cover"),),
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Unsupported Semantic",
            version=1,
            slides=(
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[:9],
                ReportRecipeSlide(order=10, semantic_name="unsupported_semantic"),
            ),
        ),
    ],
)
def test_facebook_instagram_10_recipe_builder_bad_recipe_fails_closed(
    client,
    monkeypatch: pytest.MonkeyPatch,
    recipe: ReportRecipe | None,
):
    refs = _seed_sources()
    monkeypatch.delenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, raising=False)
    monkeypatch.setattr(main_module, "get_report_recipe", lambda _recipe_id: recipe)

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=_canonical_multi_source_payload(refs),
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "facebook_instagram_10_recipe_builder_failed"


def test_facebook_instagram_10_recipe_guard_is_narrow() -> None:
    assert main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "instagram_business_login", "source_type": "instagram_business"},
        ],
        requested_slides=10,
    )
    assert main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "meta", "source_type": "instagram_business"},
        ],
        requested_slides=10,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [{"provider": "meta", "source_type": "facebook_pages"}],
        requested_slides=5,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [{"provider": "meta", "source_type": "instagram_business"}],
        requested_slides=10,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "meta", "source_type": "meta_ads"},
        ],
        requested_slides=10,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "instagram_business_login", "source_type": "instagram_business"},
        ],
        requested_slides=5,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "instagram_business_login", "source_type": "instagram_business"},
            {"provider": "meta", "source_type": "meta_ads"},
        ],
        requested_slides=10,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta_ads", "source_type": "facebook_pages"},
            {"provider": "instagram_business_login", "source_type": "instagram_business"},
        ],
        requested_slides=10,
    )
    assert not main_module.should_use_facebook_instagram_10_recipe(
        [
            {"provider": "meta", "source_type": "facebook_pages"},
            {"provider": "meta_ads", "source_type": "instagram_business"},
        ],
        requested_slides=10,
    )


def test_noncanonical_multi_source_report_does_not_select_facebook_instagram_recipe(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    import app.report_recipe_builder as recipe_builder_module

    refs = _seed_sources()
    monkeypatch.delenv(main_module.FACEBOOK_INSTAGRAM_10_RECIPE_BUILDER_ENV, raising=False)

    def fail_recipe_builder(*_args, **_kwargs):
        raise AssertionError("Facebook + Instagram Recipe builder should not run")

    monkeypatch.setattr(
        recipe_builder_module,
        "build_facebook_instagram_10_blocks_from_recipe",
        fail_recipe_builder,
    )
    payload = _canonical_multi_source_payload(refs)
    payload["sources"][1]["source_type"] = "meta_ads"

    response = client.post(
        "/reports/multi-source",
        headers=_auth_headers(refs["user_id"]),
        json=payload,
    )

    assert response.status_code == 200
    block_specs = _persisted_block_specs(response.json()["id"])
    assert len(block_specs) == 10
