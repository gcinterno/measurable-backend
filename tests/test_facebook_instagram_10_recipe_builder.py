from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

TEST_DB_PATH = Path("/tmp/measurable_facebook_instagram_10_recipe_builder.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app import main as report_main
from app.report_recipe_builder import (
    FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS,
    InvalidReportRecipeForBuildError,
    MissingRecipeSemanticHandlerError,
    UnsupportedRecipeSemanticNameError,
    build_facebook_instagram_10_block_for_recipe_slide,
    build_facebook_instagram_10_blocks_from_recipe,
)
from app.report_recipe_validation import validate_blocks_against_recipe
from app.report_recipes import (
    FACEBOOK_INSTAGRAM_10_RECIPE,
    FACEBOOK_INSTAGRAM_10_SEMANTIC_NAMES,
    ReportRecipe,
    ReportRecipeSlide,
)


def _source(
    *,
    dataset_id: int,
    source_type: str,
    label: str,
    account_name: str,
    followers: int,
    reach: int,
    impressions: int,
    engagement: int,
    profile_visits: int,
    previous_reach: int,
    previous_impressions: int,
    previous_engagement: int,
    previous_page_views: int,
    previous_followers: int,
    daily_multiplier: int,
    post: dict[str, Any],
) -> dict[str, Any]:
    timeframe = {
        "label": "June 2026",
        "since": "2026-06-01",
        "until": "2026-06-30",
    }
    base_timeseries = {
        "followers_growth": [
            {"date": "2026-06-01", "value": 2 * daily_multiplier},
            {"date": "2026-06-02", "value": 3 * daily_multiplier},
        ],
        "followers": [
            {"date": "2026-06-01", "value": followers - 10},
            {"date": "2026-06-02", "value": followers},
        ],
        "reach": [
            {"date": "2026-06-01", "value": 100 * daily_multiplier},
            {"date": "2026-06-02", "value": 120 * daily_multiplier},
        ],
        "impressions": [],
        "engagement": [
            {"date": "2026-06-01", "value": 18 * daily_multiplier},
            {"date": "2026-06-02", "value": 22 * daily_multiplier},
        ],
        "page_visits": [
            {"date": "2026-06-01", "value": 9 * daily_multiplier},
            {"date": "2026-06-02", "value": 12 * daily_multiplier},
        ],
    }
    metrics = {
        "followers": followers,
        "reach": reach,
        "impressions": None,
        "engagement": engagement,
        "profile_visits": profile_visits,
        "page_visits": profile_visits,
        "link_clicks": 20 * daily_multiplier,
        "views": None,
        "content_interactions": engagement,
    }
    report_inputs = {
        "integration_type": source_type,
        "account_name": account_name,
        "page_name": account_name,
        "followers": followers,
        "followers_total": followers,
        "reach": reach,
        "reach_total": reach,
        "engagement": engagement,
        "engagement_total": engagement,
        "profile_visits": profile_visits,
        "page_views_total": profile_visits,
        "recent_posts": [post],
        "top_content": [post],
        "posts_analyzed_count": 1,
        "previous_period": {
            "previous_reach": previous_reach,
            "previous_impressions": previous_impressions,
            "previous_engagement": previous_engagement,
            "previous_page_views": previous_page_views,
            "previous_followers": previous_followers,
        },
    }
    if source_type == "facebook_pages":
        report_inputs.update(
            {
                "organic_impressions": impressions,
                "organic_impressions_total": impressions,
                "impressions": None,
                "impressions_total": None,
                "daily_organic_impressions": [
                    {"date": "2026-06-01", "value": 180 * daily_multiplier},
                    {"date": "2026-06-02", "value": 210 * daily_multiplier},
                ],
                "daily_engagement": base_timeseries["engagement"],
                "daily_page_views": base_timeseries["page_visits"],
                "unavailable_metrics": {
                    "impressions": "General page impressions are not available in this dataset."
                },
            }
        )
    else:
        metrics.update({"engagement": None, "content_interactions": None, "views": impressions})
        base_timeseries["engagement"] = []
        report_inputs.update(
            {
                "username": "instagramaccount",
                "provider": "instagram_business_login",
                "followers_count": followers,
                "media_count": 42,
                "views": impressions,
                "impressions": None,
                "impressions_total": None,
                "engagement": None,
                "engagement_total": None,
                "total_interactions": None,
                "accounts_engaged": None,
                "profile_views": profile_visits,
                "reach_daily": base_timeseries["reach"],
                "views_daily": [
                    {"date": "2026-06-01", "value": 180 * daily_multiplier},
                    {"date": "2026-06-02", "value": 210 * daily_multiplier},
                ],
                "daily_engagement": [],
                "profile_views_daily": base_timeseries["page_visits"],
                "unavailable_metrics": {
                    "impressions": "metric[0] must be one of: reach, views, total_interactions"
                },
            }
        )
    return {
        "dataset_id": dataset_id,
        "source_type": source_type,
        "provider": "meta",
        "label": label,
        "account_name": account_name,
        "metrics": metrics,
        "timeseries": base_timeseries,
        "content": [post],
        "raw_summary": f"{label} summary",
        "report_inputs": report_inputs,
        "report_timeframe": timeframe,
    }


def _canonical_context() -> dict[str, Any]:
    timeframe = {
        "label": "June 2026",
        "since": "2026-06-01",
        "until": "2026-06-30",
    }
    normalized_sources = [
        _source(
            dataset_id=101,
            source_type="facebook_pages",
            label="Facebook Page",
            account_name="Facebook Page",
            followers=1200,
            reach=5400,
            impressions=8700,
            engagement=320,
            profile_visits=410,
            previous_reach=5000,
            previous_impressions=8100,
            previous_engagement=300,
            previous_page_views=390,
            previous_followers=1180,
            daily_multiplier=1,
            post={
                "id": "fb-post-1",
                "message": "Facebook launch update",
                "created_time": "2026-06-10",
                "reach": 900,
                "impressions": 1400,
                "engagement": 90,
                "reactions": 45,
                "comments": 12,
                "shares": 8,
            },
        ),
        _source(
            dataset_id=202,
            source_type="instagram_business",
            label="Instagram Account",
            account_name="Instagram Account",
            followers=1800,
            reach=7600,
            impressions=12000,
            engagement=540,
            profile_visits=630,
            previous_reach=7000,
            previous_impressions=11400,
            previous_engagement=500,
            previous_page_views=590,
            previous_followers=1760,
            daily_multiplier=2,
            post={
                "id": "ig-post-1",
                "caption": "Instagram reel performance",
                "created_time": "2026-06-12",
                "reach": 1100,
                "impressions": 1900,
                "engagement": 160,
                "likes": 110,
                "comments": 16,
                "saves": 21,
            },
        ),
    ]
    return report_main._multi_source_build_context(
        title="Cross-source report",
        locale="en",
        timeframe=timeframe,
        branding={
            "brand_name": "Agency",
            "brand_logo_url": "https://example.com/logo.png",
            "resolved_brand_name": "Agency",
            "resolved_logo_url": "https://example.com/logo.png",
        },
        normalized_sources=normalized_sources,
    )


def _payloads(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(str(block["data_json"])) for block in blocks]


def _assert_primary_value_fields(payload: dict[str, Any], expected_value: Any) -> None:
    assert payload["value"] == expected_value
    assert payload["current_value"] == expected_value
    assert payload["primary_value"] == expected_value
    assert payload["metric_value"] == expected_value
    assert payload["total"] == expected_value
    assert payload["canonical_metric_resolution"]["value"] == expected_value


def test_facebook_instagram_10_recipe_builder_aligns_blocks_to_catalog_semantics() -> None:
    context = _canonical_context()

    legacy_blocks = report_main._multi_source_build_10_blocks(context)
    recipe_blocks = build_facebook_instagram_10_blocks_from_recipe(
        FACEBOOK_INSTAGRAM_10_RECIPE,
        context,
    )

    assert recipe_blocks != legacy_blocks
    assert len(recipe_blocks) == len(legacy_blocks) == 10
    assert [block["order"] for block in recipe_blocks] == [block["order"] for block in legacy_blocks]
    assert [block["type"] for block in recipe_blocks] == [block["type"] for block in legacy_blocks]
    assert [block["editable_fields_json"] for block in recipe_blocks] == [
        block["editable_fields_json"] for block in legacy_blocks
    ]
    recipe_payloads = _payloads(recipe_blocks)
    assert [payload["semantic_name"] for payload in recipe_payloads] == list(
        FACEBOOK_INSTAGRAM_10_SEMANTIC_NAMES
    )
    assert recipe_payloads[2]["canonical_semantic"] == "visibility"
    _assert_primary_value_fields(recipe_payloads[2], 8700)
    assert recipe_payloads[2]["provenance"]["aggregation_method"] == "not_comparable"
    assert recipe_payloads[2]["canonical_metric_resolution"]["aggregation_method"] == "not_comparable"
    assert recipe_payloads[2]["provenance"]["value_is_aggregated"] is False
    assert recipe_payloads[2]["previous_value"] is None
    assert {
        source["source_metric"]
        for source in recipe_payloads[2]["source_contributions"]
    } == {"page_posts_impressions_organic", "views"}
    assert recipe_payloads[3]["canonical_semantic"] == "engagement"
    _assert_primary_value_fields(recipe_payloads[3], 480)
    instagram_engagement = next(
        source
        for source in recipe_payloads[3]["source_contributions"]
        if source["source_type"] == "instagram_business"
    )
    assert instagram_engagement["source_metric"] == "media.engagement"
    assert instagram_engagement["provenance"]["fallback_used"] is True
    assert recipe_payloads[5]["semantic_name"] == "audience_growth"
    assert recipe_payloads[5]["canonical_semantic"] == "audience_size"
    _assert_primary_value_fields(recipe_payloads[5], 3000)
    assert recipe_payloads[5]["audience_value_type"] == "base_size"
    assert recipe_payloads[6]["canonical_semantic"] == "content_activity"
    _assert_primary_value_fields(recipe_payloads[6], 2)
    assert recipe_payloads[6]["media_count_contributions"][0]["value"] == 42
    assert {
        item["source"]
        for item in recipe_payloads[7]["top_posts"]
    } == {"Facebook Page", "Instagram Account"}
    assert all(item["ranking_score"] is not None for item in recipe_payloads[7]["top_posts"])
    assert all("engagement_interactions" in item for item in recipe_payloads[7]["top_posts"])
    assert recipe_payloads[8]["metrics"]["summary_scope"] == "multi_source"
    assert validate_blocks_against_recipe(recipe_blocks, FACEBOOK_INSTAGRAM_10_RECIPE).valid


@pytest.mark.parametrize(
    "recipe",
    [
        None,
        ReportRecipe(
            id="wrong_id",
            platform="multi_source",
            name="Wrong ID",
            version=1,
            slides=FACEBOOK_INSTAGRAM_10_RECIPE.slides,
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="facebook_pages",
            name="Wrong Platform",
            version=1,
            slides=FACEBOOK_INSTAGRAM_10_RECIPE.slides,
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Wrong Version",
            version=2,
            slides=FACEBOOK_INSTAGRAM_10_RECIPE.slides,
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Wrong Slide Count",
            version=1,
            slides=FACEBOOK_INSTAGRAM_10_RECIPE.slides[:9],
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Missing Slide",
            version=1,
            slides=(
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[:4],
                ReportRecipeSlide(order=5, semantic_name="profile_visits"),
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[5:],
            ),
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Duplicate Order",
            version=1,
            slides=(
                ReportRecipeSlide(order=1, semantic_name="cover"),
                ReportRecipeSlide(order=1, semantic_name="reach"),
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[2:],
            ),
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Non Contiguous Order",
            version=1,
            slides=(
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[:9],
                ReportRecipeSlide(order=11, semantic_name="recommendations"),
            ),
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Duplicate Semantic",
            version=1,
            slides=(
                *FACEBOOK_INSTAGRAM_10_RECIPE.slides[:9],
                ReportRecipeSlide(order=10, semantic_name="executive_insights"),
            ),
        ),
        ReportRecipe(
            id="facebook_instagram_10",
            platform="multi_source",
            name="Malformed Slides",
            version=1,
            slides=None,  # type: ignore[arg-type]
        ),
    ],
)
def test_facebook_instagram_10_recipe_builder_rejects_invalid_recipe(recipe: ReportRecipe | None) -> None:
    with pytest.raises(InvalidReportRecipeForBuildError):
        build_facebook_instagram_10_blocks_from_recipe(recipe, _canonical_context())


def test_facebook_instagram_10_recipe_dispatcher_rejects_unsupported_semantic() -> None:
    state = report_main._multi_source_prepare_10_block_state(_canonical_context())

    with pytest.raises(UnsupportedRecipeSemanticNameError):
        build_facebook_instagram_10_block_for_recipe_slide(
            ReportRecipeSlide(order=1, semantic_name="unsupported_metric"),
            state,
        )


def test_facebook_instagram_10_recipe_builder_rejects_missing_semantic_handler() -> None:
    handlers = {
        semantic_name: handler
        for semantic_name, handler in FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS.items()
        if semantic_name != "reach"
    }

    with pytest.raises(MissingRecipeSemanticHandlerError):
        build_facebook_instagram_10_blocks_from_recipe(
            FACEBOOK_INSTAGRAM_10_RECIPE,
            _canonical_context(),
            semantic_handlers=handlers,
        )
