from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TEST_DB_PATH = Path("/tmp/measurable_canonical_metric_catalog.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app import main as report_main
from app import report_recipe_builder
from app.canonical_metric_catalog import (
    AVAILABLE,
    MISSING,
    NOT_COMPARABLE,
    SUM,
    SUM_NON_DEDUPED,
    UNSUPPORTED,
    build_canonical_metric_catalog_for_sources,
    build_source_contribution_table,
    resolve_metric,
)
from app.report_recipe_builder import build_facebook_instagram_10_blocks_from_recipe
from app.report_recipe_validation import validate_blocks_against_recipe
from app.report_recipes import FACEBOOK_INSTAGRAM_10_RECIPE


def _facebook_source() -> dict[str, Any]:
    post = {
        "id": "fb-post-1",
        "message": "Facebook post",
        "created_time": "2026-06-02T12:00:00+00:00",
        "reach": 200,
        "impressions": None,
        "engagement": 35,
        "reactions": 25,
        "comments": 5,
        "shares": 5,
    }
    report_inputs = {
        "integration_type": "facebook_pages",
        "account_name": "Facebook Page",
        "page_name": "Facebook Page",
        "followers": 300,
        "followers_total": 300,
        "fans": 250,
        "fans_total": 250,
        "reach": None,
        "reach_total": None,
        "organic_impressions": 900,
        "organic_impressions_total": 900,
        "impressions": None,
        "impressions_total": None,
        "engagement": 75,
        "engagement_total": 75,
        "page_views_total": 120,
        "profile_visits": None,
        "reactions_total": 40,
        "daily_organic_impressions": [
            {"date": "2026-06-01", "value": 400},
            {"date": "2026-06-02", "value": 500},
        ],
        "daily_engagement": [
            {"date": "2026-06-01", "value": 30},
            {"date": "2026-06-02", "value": 45},
        ],
        "daily_page_views": [
            {"date": "2026-06-01", "value": 50},
            {"date": "2026-06-02", "value": 70},
        ],
        "recent_posts": [post],
        "top_content": [
            {
                "post_id": "fb-post-1",
                "message_preview": "Facebook post",
                "engagement_total": 35,
                "reactions": 25,
                "comments": 5,
                "shares": 5,
            }
        ],
        "previous_period": {
            "previous_reach": None,
            "previous_impressions": None,
            "previous_engagement": 70,
            "previous_page_views": 100,
            "previous_followers": 295,
        },
        "unavailable_metrics": {
            "reach": "Meta did not return unique reach for the selected period.",
            "impressions": "Meta did not return general page impressions for the selected period.",
        },
        "normalized_report_metrics": {
            "organic_impressions_total": 900,
            "daily_organic_impressions": [
                {"date": "2026-06-01", "value": 400},
                {"date": "2026-06-02", "value": 500},
            ],
            "engagement_total": 75,
            "page_views_total": 120,
            "followers_total": 300,
            "posts_analyzed_count": 1,
        },
    }
    return {
        "dataset_id": 101,
        "source_type": "facebook_pages",
        "provider": "meta",
        "label": "Facebook Page",
        "account_name": "Facebook Page",
        "metrics": {
            "followers": 300,
            "reach": None,
            "impressions": None,
            "engagement": 75,
            "profile_visits": 120,
            "page_visits": 120,
            "views": 120,
        },
        "timeseries": {
            "reach": [],
            "impressions": [],
            "engagement": report_inputs["daily_engagement"],
            "page_visits": report_inputs["daily_page_views"],
        },
        "content": [post],
        "raw_summary": "Facebook Page summary",
        "report_inputs": report_inputs,
        "report_timeframe": _timeframe(),
    }


def _instagram_source() -> dict[str, Any]:
    post = {
        "id": "ig-media-1",
        "message": "Instagram reel",
        "caption": "Instagram reel",
        "created_time": "2026-06-03T12:00:00+00:00",
        "timestamp": "2026-06-03T12:00:00+00:00",
        "media_type": "REELS",
        "reach": 100,
        "views": 500,
        "impressions": None,
        "engagement": 70,
        "interactions": 70,
        "likes": 50,
        "comments": 10,
        "shares": 4,
        "saves": 5,
        "replies": 1,
    }
    report_inputs = {
        "integration_type": "instagram_business",
        "account_name": "Instagram Account",
        "username": "instagramaccount",
        "followers": 1234,
        "followers_count": 1234,
        "followers_total": 1234,
        "media_count": 12,
        "reach": 0,
        "reach_total": 0,
        "views": 500,
        "impressions": None,
        "impressions_total": None,
        "engagement": 70,
        "engagement_total": 70,
        "engagement_source_metric": "total_interactions",
        "total_interactions": 70,
        "accounts_engaged": 30,
        "profile_views": 15,
        "profile_visits": 15,
        "website_clicks": 3,
        "link_clicks": 3,
        "posts_analyzed_count": 1,
        "reach_daily": [{"date": "2026-06-01", "value": 0}],
        "views_daily": [{"date": "2026-06-01", "value": 500}],
        "daily_engagement": [{"date": "2026-06-01", "value": 70}],
        "profile_views_daily": [{"date": "2026-06-01", "value": 15}],
        "recent_posts": [post],
        "top_content": [
            {
                "post_id": "ig-media-1",
                "message_preview": "Instagram reel",
                "reach": 100,
                "views": 500,
                "engagement_total": 70,
                "likes": 50,
                "comments": 10,
                "shares": 4,
                "saves": 5,
                "replies": 1,
            }
        ],
        "previous_period": {
            "previous_reach": 0,
            "previous_impressions": None,
            "previous_engagement": 50,
            "previous_page_views": 10,
            "previous_followers": 1200,
        },
        "unavailable_metrics": {
            "impressions": "metric[0] must be one of: reach, views, total_interactions",
        },
        "normalized_report_metrics": {
            "views_total": 500,
            "views_daily": [{"date": "2026-06-01", "value": 500}],
            "viewers_total": 0,
            "viewers_daily": [{"date": "2026-06-01", "value": 0}],
            "interactions_total": 70,
            "interactions_daily": [{"date": "2026-06-01", "value": 70}],
            "accounts_engaged_total": 30,
            "total_interactions_total": 70,
            "page_visits_total": 15,
            "page_visits_daily": [{"date": "2026-06-01", "value": 15}],
            "followers_total": 1234,
            "posts_analyzed_count": 1,
            "top_content": [],
        },
    }
    return {
        "dataset_id": 202,
        "source_type": "instagram_business",
        "provider": "instagram_business_login",
        "label": "Instagram Account",
        "account_name": "Instagram Account",
        "metrics": {
            "followers": 1234,
            "reach": 0,
            "impressions": None,
            "engagement": 70,
            "profile_visits": 15,
            "page_visits": 15,
            "views": 500,
            "content_interactions": 70,
        },
        "timeseries": {
            "reach": report_inputs["reach_daily"],
            "impressions": [],
            "engagement": report_inputs["daily_engagement"],
            "page_visits": report_inputs["profile_views_daily"],
        },
        "content": [post],
        "raw_summary": "Instagram Account summary",
        "report_inputs": report_inputs,
        "report_timeframe": _timeframe(),
    }


def _timeframe() -> dict[str, str]:
    return {
        "label": "June 2026",
        "since": "2026-06-01",
        "until": "2026-06-30",
    }


def _catalog():
    return build_canonical_metric_catalog_for_sources([_facebook_source(), _instagram_source()])


def _records(canonical_metric: str):
    return [record for record in _catalog() if record.canonical_metric == canonical_metric]


def _context() -> dict[str, Any]:
    return report_main._multi_source_build_context(
        title="Facebook + Instagram report",
        locale="en",
        timeframe=_timeframe(),
        branding={"brand_name": "Agency"},
        normalized_sources=[_facebook_source(), _instagram_source()],
    )


def _assert_primary_value_fields(payload: dict[str, Any], expected_value: Any) -> None:
    assert payload["value"] == expected_value
    assert payload["current_value"] == expected_value
    assert payload["primary_value"] == expected_value
    assert payload["metric_value"] == expected_value
    assert payload["total"] == expected_value
    assert payload["canonical_metric_resolution"]["value"] == expected_value


def test_catalog_preserves_zero_unsupported_and_missing_distinctions() -> None:
    catalog = _catalog()

    reach = resolve_metric("reach", catalog)
    instagram_reach = next(source for source in reach.sources if source.source_type == "instagram_business")
    facebook_reach = next(source for source in reach.sources if source.source_type == "facebook_pages")
    assert instagram_reach.value == 0
    assert instagram_reach.support_status == AVAILABLE
    assert facebook_reach.value is None
    assert facebook_reach.support_status == MISSING
    assert reach.combined_value == 0
    assert reach.aggregation_method == SUM_NON_DEDUPED

    impressions = resolve_metric("impressions", catalog)
    instagram_impressions = next(source for source in impressions.sources if source.source_type == "instagram_business")
    assert instagram_impressions.value is None
    assert instagram_impressions.support_status == UNSUPPORTED
    assert impressions.combined_value is None


def test_catalog_retains_source_provenance_for_single_platform_metrics() -> None:
    organic_visibility = resolve_metric("organic_visibility", _catalog())
    assert organic_visibility.combined_value is None
    assert organic_visibility.sources[0].source_type == "facebook_pages"
    assert organic_visibility.sources[0].source_metric == "page_posts_impressions_organic"
    assert organic_visibility.sources[0].value == 900
    assert organic_visibility.sources[0].dataset_id == 101

    instagram_views = resolve_metric("views", _catalog())
    assert instagram_views.sources[0].source_type == "instagram_business"
    assert instagram_views.sources[0].source_metric == "views"
    assert instagram_views.sources[0].value == 500
    assert instagram_views.sources[0].dataset_id == 202

    media_count = resolve_metric("media_count", _catalog())
    assert media_count.sources[0].source_type == "instagram_business"
    assert media_count.sources[0].source_metric == "media_count"
    assert media_count.sources[0].value == 12
    assert media_count.sources[0].support_status == AVAILABLE


def test_shared_semantics_aggregate_and_visibility_remains_not_comparable() -> None:
    catalog = _catalog()

    engagement = resolve_metric("engagement", catalog)
    assert engagement.combined_value == 145
    assert engagement.aggregation_method == SUM
    assert {source.source_metric for source in engagement.sources} == {
        "page_post_engagements",
        "total_interactions",
    }

    visibility = resolve_metric("visibility", catalog)
    assert visibility.combined_value is None
    assert visibility.aggregation_method == NOT_COMPARABLE
    assert {source.source_metric for source in visibility.sources} == {
        "page_posts_impressions_organic",
        "views",
    }
    assert [source.value for source in visibility.sources] == [900, 500]

    audience = resolve_metric("audience_size", catalog)
    assert audience.combined_value == 1534
    assert audience.aggregation_method == SUM_NON_DEDUPED


def test_instagram_engagement_falls_back_to_media_level_interactions_when_account_metric_missing() -> None:
    instagram = _instagram_source()
    report_inputs = dict(instagram["report_inputs"])
    for key in (
        "engagement",
        "engagement_total",
        "engagement_source_metric",
        "total_interactions",
        "accounts_engaged",
    ):
        report_inputs.pop(key, None)
    normalized = dict(report_inputs["normalized_report_metrics"])
    for key in (
        "interactions_total",
        "total_interactions_total",
        "accounts_engaged_total",
        "interactions_daily",
    ):
        normalized.pop(key, None)
    report_inputs["normalized_report_metrics"] = normalized
    instagram["report_inputs"] = report_inputs
    instagram["metrics"] = {**instagram["metrics"], "engagement": None, "content_interactions": None}
    instagram["timeseries"] = {**instagram["timeseries"], "engagement": []}

    engagement = resolve_metric("engagement", build_canonical_metric_catalog_for_sources([_facebook_source(), instagram]))
    instagram_engagement = next(source for source in engagement.sources if source.source_type == "instagram_business")

    assert instagram_engagement.value == 70
    assert instagram_engagement.source_metric == "media.engagement"
    assert instagram_engagement.support_status == AVAILABLE
    assert instagram_engagement.metadata["fallback_used"] is True
    assert len(instagram_engagement.timeseries) == 1
    assert engagement.combined_value == 145


def test_content_metrics_include_instagram_media_for_top_content() -> None:
    catalog = _catalog()
    content_activity = resolve_metric("content_activity", catalog)
    assert content_activity.combined_value == 2

    top_content = resolve_metric("top_content", catalog)
    instagram_top_content = next(source for source in top_content.sources if source.source_type == "instagram_business")
    assert instagram_top_content.support_status == AVAILABLE
    assert instagram_top_content.metadata["top_content"][0]["post_id"] == "ig-media-1"

    multi_source_top_content = report_main._multi_source_top_content([_instagram_source()])
    assert multi_source_top_content is not None
    assert multi_source_top_content["id"] == "ig-media-1"
    assert multi_source_top_content["_source_label"] == "Instagram Account"


def test_source_contribution_table_reports_raw_metric_status_and_aggregation() -> None:
    rows = build_source_contribution_table(_catalog())
    by_metric = {row["canonical_metric"]: row for row in rows}

    assert by_metric["reach"]["facebook_raw_metric"] == "reach"
    assert by_metric["reach"]["facebook_status"] == MISSING
    assert by_metric["reach"]["instagram_raw_metric"] == "reach"
    assert by_metric["reach"]["instagram_value"] == "0"
    assert by_metric["reach"]["combined_value"] == 0
    assert by_metric["visibility"]["aggregation_method"] == NOT_COMPARABLE


def test_recipe_builder_output_remains_reportblock_compatible_with_catalog_state() -> None:
    context = _context()

    legacy_blocks = report_main._multi_source_build_10_blocks(context)
    recipe_blocks = build_facebook_instagram_10_blocks_from_recipe(
        FACEBOOK_INSTAGRAM_10_RECIPE,
        context,
    )

    assert recipe_blocks != legacy_blocks
    assert validate_blocks_against_recipe(recipe_blocks, FACEBOOK_INSTAGRAM_10_RECIPE).valid
    assert len(recipe_blocks) == 10
    for block in recipe_blocks:
        assert "data_json" in block
        assert isinstance(json.loads(str(block["data_json"])), dict)

    payloads = [json.loads(str(block["data_json"])) for block in recipe_blocks]
    visibility_payload = payloads[2]
    assert visibility_payload["semantic_name"] == "impressions"
    assert visibility_payload["canonical_semantic"] == "visibility"
    _assert_primary_value_fields(visibility_payload, 900)
    assert visibility_payload["support_status"] == AVAILABLE
    assert visibility_payload["provenance"]["aggregation_method"] == NOT_COMPARABLE
    assert visibility_payload["canonical_metric_resolution"]["aggregation_method"] == NOT_COMPARABLE
    assert visibility_payload["provenance"]["value_is_aggregated"] is False
    assert visibility_payload["previous_value"] is None
    assert visibility_payload["chart"]["metric"] == "visibility"
    assert {source["source_metric"] for source in visibility_payload["source_contributions"]} == {
        "page_posts_impressions_organic",
        "views",
    }

    engagement_payload = payloads[3]
    assert engagement_payload["canonical_semantic"] == "engagement"
    _assert_primary_value_fields(engagement_payload, 145)
    assert {source["source_metric"] for source in engagement_payload["source_contributions"]} == {
        "page_post_engagements",
        "total_interactions",
    }

    audience_payload = payloads[5]
    assert audience_payload["semantic_name"] == "audience_growth"
    assert audience_payload["canonical_semantic"] == "audience_size"
    _assert_primary_value_fields(audience_payload, 1534)
    assert audience_payload["audience_value_type"] == "base_size"

    content_payload = payloads[6]
    assert content_payload["canonical_semantic"] == "content_activity"
    _assert_primary_value_fields(content_payload, 2)
    assert content_payload["media_count_contributions"][0]["value"] == 12

    top_content_payload = payloads[7]
    assert top_content_payload["canonical_semantic"] == "top_content"
    assert all(item["ranking_score"] is not None for item in top_content_payload["top_posts"])
    assert all("engagement_interactions" in item for item in top_content_payload["top_posts"])

    state = report_recipe_builder._prepare_facebook_instagram_10_recipe_build_state(context)
    assert state["canonical_metric_resolutions"]["reach"]["aggregation_method"] == SUM_NON_DEDUPED
    assert state["canonical_metric_resolutions"]["visibility"]["combined_value"] is None
    assert any(
        record["canonical_metric"] == "visibility"
        and record["source_metric"] == "page_posts_impressions_organic"
        for record in state["canonical_metric_catalog"]
    )
