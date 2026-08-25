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
    return {
        "dataset_id": dataset_id,
        "source_type": source_type,
        "provider": "meta",
        "label": label,
        "account_name": account_name,
        "metrics": {
            "followers": followers,
            "reach": reach,
            "impressions": impressions,
            "engagement": engagement,
            "profile_visits": profile_visits,
            "page_visits": profile_visits,
            "link_clicks": 20 * daily_multiplier,
            "views": impressions,
            "content_interactions": engagement,
        },
        "timeseries": {
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
            "impressions": [
                {"date": "2026-06-01", "value": 180 * daily_multiplier},
                {"date": "2026-06-02", "value": 210 * daily_multiplier},
            ],
            "engagement": [
                {"date": "2026-06-01", "value": 18 * daily_multiplier},
                {"date": "2026-06-02", "value": 22 * daily_multiplier},
            ],
            "page_visits": [
                {"date": "2026-06-01", "value": 9 * daily_multiplier},
                {"date": "2026-06-02", "value": 12 * daily_multiplier},
            ],
        },
        "content": [post],
        "raw_summary": f"{label} summary",
        "report_inputs": {
            "previous_period": {
                "previous_reach": previous_reach,
                "previous_impressions": previous_impressions,
                "previous_engagement": previous_engagement,
                "previous_page_views": previous_page_views,
                "previous_followers": previous_followers,
            }
        },
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


def test_facebook_instagram_10_recipe_builder_matches_multi_source_builder_exactly() -> None:
    context = _canonical_context()

    legacy_blocks = report_main._multi_source_build_10_blocks(context)
    recipe_blocks = build_facebook_instagram_10_blocks_from_recipe(
        FACEBOOK_INSTAGRAM_10_RECIPE,
        context,
    )

    assert recipe_blocks == legacy_blocks
    assert len(recipe_blocks) == len(legacy_blocks) == 10
    assert [block["order"] for block in recipe_blocks] == [block["order"] for block in legacy_blocks]
    assert [block["type"] for block in recipe_blocks] == [block["type"] for block in legacy_blocks]
    assert [block["editable_fields_json"] for block in recipe_blocks] == [
        block["editable_fields_json"] for block in legacy_blocks
    ]
    assert [block["data_json"] for block in recipe_blocks] == [
        block["data_json"] for block in legacy_blocks
    ]
    assert [payload["semantic_name"] for payload in _payloads(recipe_blocks)] == list(
        FACEBOOK_INSTAGRAM_10_SEMANTIC_NAMES
    )
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
