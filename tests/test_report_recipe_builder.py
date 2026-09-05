from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

TEST_DB_PATH = Path("/tmp/measurable_report_recipe_builder.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.main import build_5_blocks
from app.report_recipe_builder import (
    FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS,
    InvalidReportRecipeForBuildError,
    MissingRecipeSemanticHandlerError,
    UnsupportedRecipeSemanticNameError,
    build_facebook_pages_5_blocks_from_recipe,
)
from app.report_recipe_validation import validate_blocks_against_recipe
from app.report_recipes import FACEBOOK_PAGES_5_RECIPE, ReportRecipe, ReportRecipeSlide


def _base_context() -> dict:
    return {
        "title": "Executive report",
        "plan": "core",
        "report_timeframe": {
            "label": "June 2026",
            "since": "2026-06-01",
            "until": "2026-06-30",
        },
        "page_name": "Acme Account",
        "summary": "Summary",
        "recent_posts_summary": "Posts summary",
        "ai_summary": "AI summary",
        "general_insights_slide_payload": {},
        "report_inputs": {
            "integration_type": "facebook_pages",
            "daily_organic_impressions": [
                {"date": "2026-06-01", "value": 1234},
                {"date": "2026-06-02", "value": 900},
            ],
            "daily_engagement": [
                {"date": "2026-06-01", "value": 80},
                {"date": "2026-06-02", "value": 40},
            ],
            "daily_page_views": [
                {"date": "2026-06-01", "value": 3300},
                {"date": "2026-06-02", "value": 2448},
            ],
            "organic_impressions_total": 10187,
            "engagement_total": 320,
            "page_views_total": 5748,
            "followers_total": 1200,
            "fans_total": 1190,
            "reactions_total": 342,
            "top_content": [
                {
                    "post_id": "post-1",
                    "created_time": "2026-06-10T12:00:00+0000",
                    "message_preview": "Strong content",
                    "permalink_url": "https://facebook.com/post-1",
                    "media_type": "photo",
                    "impressions": 1000,
                    "reach": 800,
                    "engaged_users": 90,
                    "reactions": 40,
                    "comments": 8,
                    "shares": 3,
                    "engagement_total": 51,
                    "score": 90,
                },
                {
                    "post_id": "post-2",
                    "created_time": "2026-06-12T12:00:00+0000",
                    "message_preview": "Fallback content",
                    "permalink_url": "https://facebook.com/post-2",
                    "media_type": "video",
                    "impressions": None,
                    "reach": None,
                    "engaged_users": None,
                    "reactions": 20,
                    "comments": 4,
                    "shares": 2,
                    "engagement_total": 26,
                    "score": 26,
                },
            ],
            "normalized_report_metrics": {
                "organic_impressions_total": 10187,
                "daily_organic_impressions": [
                    {"date": "2026-06-01", "value": 1234},
                    {"date": "2026-06-02", "value": 900},
                ],
                "engagement_total": 320,
                "daily_engagement": [
                    {"date": "2026-06-01", "value": 80},
                    {"date": "2026-06-02", "value": 40},
                ],
                "page_views_total": 5748,
                "daily_page_views": [
                    {"date": "2026-06-01", "value": 3300},
                    {"date": "2026-06-02", "value": 2448},
                ],
                "followers_total": 1200,
                "fans_total": 1190,
                "reactions_total": 342,
            },
        },
        "branding": {
            "brand_name": "Agency",
            "brand_logo_url": "https://example.com/logo.png",
            "resolved_brand_name": "Agency",
            "resolved_logo_url": "https://example.com/logo.png",
        },
        "requested_slides": 5,
    }


def _payloads(blocks: list[dict]) -> list[dict]:
    return [json.loads(str(block["data_json"])) for block in blocks]


def test_recipe_builder_matches_build_5_blocks_exactly_for_facebook_pages_5() -> None:
    context = _base_context()

    legacy_blocks = build_5_blocks(context)
    recipe_blocks = build_facebook_pages_5_blocks_from_recipe(FACEBOOK_PAGES_5_RECIPE, context)

    assert recipe_blocks == legacy_blocks

    legacy_payloads = _payloads(legacy_blocks)
    recipe_payloads = _payloads(recipe_blocks)
    assert recipe_payloads == legacy_payloads
    assert [block["order"] for block in recipe_blocks] == [1, 2, 3, 4, 5]
    assert [block["type"] for block in recipe_blocks] == ["title", "stat", "stat", "stat", "text"]
    assert [payload["semantic_name"] for payload in recipe_payloads] == [
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
        "executive_summary",
    ]
    assert [payload["slide_number"] for payload in recipe_payloads] == [1, 2, 3, 4, 5]
    assert recipe_payloads[0]["timeframe"] == context["report_timeframe"]
    assert recipe_payloads[1]["total"] == 10187
    assert recipe_payloads[1]["daily_series"] == [
        {"date": "2026-06-01", "label": "Jun 1", "value": 1234.0},
        {"date": "2026-06-02", "label": "Jun 2", "value": 900.0},
    ]
    assert recipe_payloads[2]["total"] == 320
    assert recipe_payloads[3]["total"] == 5748
    assert [item["post_id"] for item in recipe_payloads[4]["top_content"]] == ["post-1", "post-2"]
    assert recipe_payloads[4]["metrics_summary"] == legacy_payloads[4]["metrics_summary"]
    assert recipe_payloads[4]["ai_summary"] == legacy_payloads[4]["ai_summary"]
    assert validate_blocks_against_recipe(recipe_blocks, FACEBOOK_PAGES_5_RECIPE).valid


def test_recipe_builder_rejects_unsupported_semantic_name() -> None:
    recipe = ReportRecipe(
        id="facebook_pages_5_unsupported",
        platform="facebook_pages",
        name="Unsupported Semantic",
        version=1,
        slides=(
            ReportRecipeSlide(order=1, semantic_name="cover"),
            ReportRecipeSlide(order=2, semantic_name="unsupported_metric"),
            ReportRecipeSlide(order=3, semantic_name="engagement_overview"),
            ReportRecipeSlide(order=4, semantic_name="page_views_overview"),
            ReportRecipeSlide(order=5, semantic_name="executive_summary"),
        ),
    )

    with pytest.raises(UnsupportedRecipeSemanticNameError):
        build_facebook_pages_5_blocks_from_recipe(recipe, _base_context())


def test_recipe_builder_rejects_missing_semantic_handler() -> None:
    handlers = {
        semantic_name: handler
        for semantic_name, handler in FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS.items()
        if semantic_name != "cover"
    }

    with pytest.raises(MissingRecipeSemanticHandlerError):
        build_facebook_pages_5_blocks_from_recipe(
            FACEBOOK_PAGES_5_RECIPE,
            _base_context(),
            semantic_handlers=handlers,
        )


def test_recipe_order_controls_generated_block_order() -> None:
    reordered_recipe = ReportRecipe(
        id="facebook_pages_5_reordered",
        platform="facebook_pages",
        name="Reordered Facebook Pages 5",
        version=1,
        slides=(
            ReportRecipeSlide(order=1, semantic_name="executive_summary"),
            ReportRecipeSlide(order=2, semantic_name="cover"),
            ReportRecipeSlide(order=3, semantic_name="organic_impressions_overview"),
            ReportRecipeSlide(order=4, semantic_name="engagement_overview"),
            ReportRecipeSlide(order=5, semantic_name="page_views_overview"),
        ),
    )

    blocks = build_facebook_pages_5_blocks_from_recipe(reordered_recipe, _base_context())
    payloads = _payloads(blocks)

    assert [block["order"] for block in blocks] == [1, 2, 3, 4, 5]
    assert [payload["semantic_name"] for payload in payloads] == [
        "executive_summary",
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
    ]
    assert validate_blocks_against_recipe(blocks, reordered_recipe).valid


def test_recipe_builder_rejects_structurally_invalid_recipe() -> None:
    invalid_recipe = ReportRecipe(
        id="facebook_pages_5_invalid",
        platform="facebook_pages",
        name="Invalid Facebook Pages 5",
        version=1,
        slides=(
            ReportRecipeSlide(order=1, semantic_name="cover"),
            ReportRecipeSlide(order=1, semantic_name="organic_impressions_overview"),
            ReportRecipeSlide(order=3, semantic_name="engagement_overview"),
            ReportRecipeSlide(order=4, semantic_name="page_views_overview"),
            ReportRecipeSlide(order=5, semantic_name="executive_summary"),
        ),
    )

    with pytest.raises(InvalidReportRecipeForBuildError):
        build_facebook_pages_5_blocks_from_recipe(invalid_recipe, _base_context())
