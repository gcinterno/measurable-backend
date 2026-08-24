from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

TEST_DB_PATH = Path("/tmp/measurable_report_recipe_enforcement.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.main import build_5_blocks
from app.report_recipe_enforcement import (
    ReportRecipeEnforcementError,
    enforce_report_recipe,
    should_enforce_facebook_pages_5_recipe,
)
from app.report_recipes import FACEBOOK_PAGES_5_RECIPE


def _base_context(*, integration_type: str = "facebook_pages") -> dict:
    return {
        "title": "Executive report",
        "plan": "core",
        "report_timeframe": {
            "label": "Last 28 days",
            "since": "2026-05-01",
            "until": "2026-05-28",
        },
        "page_name": "Acme Account",
        "summary": "Summary",
        "recent_posts_summary": "Posts summary",
        "ai_summary": "AI summary",
        "general_insights_slide_payload": {},
        "report_inputs": {
            "integration_type": integration_type,
            "daily_organic_impressions": [
                {"date": "2026-05-15", "value": 1234},
                {"date": "2026-05-16", "value": 900},
            ],
            "daily_engagement": [
                {"date": "2026-05-15", "value": 80},
                {"date": "2026-05-16", "value": 40},
            ],
            "daily_page_views": [
                {"date": "2026-05-15", "value": 3300},
                {"date": "2026-05-16", "value": 2448},
            ],
            "organic_impressions_total": 10187,
            "engagement_total": 320,
            "page_views_total": 5748,
            "followers_total": 1200,
            "fans_total": 1190,
            "reactions_total": 342,
            "normalized_report_metrics": {
                "organic_impressions_total": 10187,
                "daily_organic_impressions": [
                    {"date": "2026-05-15", "value": 1234},
                    {"date": "2026-05-16", "value": 900},
                ],
                "engagement_total": 320,
                "daily_engagement": [
                    {"date": "2026-05-15", "value": 80},
                    {"date": "2026-05-16", "value": 40},
                ],
                "page_views_total": 5748,
                "daily_page_views": [
                    {"date": "2026-05-15", "value": 3300},
                    {"date": "2026-05-16", "value": 2448},
                ],
                "followers_total": 1200,
                "fans_total": 1190,
                "reactions_total": 342,
            },
        },
        "branding": {},
        "requested_slides": 5,
    }


def _replace_data(block: dict, **updates) -> dict:
    updated = dict(block)
    data = json.loads(str(updated["data_json"]))
    data.update(updates)
    updated["data_json"] = json.dumps(data)
    return updated


def _assert_enforcement_codes(exc: ReportRecipeEnforcementError, *codes: str) -> None:
    actual_codes = {error["code"] for error in exc.validation_errors}
    for code in codes:
        assert code in actual_codes


def test_canonical_build_5_blocks_output_passes_recipe_enforcement() -> None:
    blocks = build_5_blocks(_base_context())

    assert enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE) is blocks


def test_recipe_enforcement_rejects_wrong_semantic_name() -> None:
    blocks = build_5_blocks(_base_context())
    blocks[2] = _replace_data(blocks[2], semantic_name="reach_overview")

    with pytest.raises(ReportRecipeEnforcementError) as exc_info:
        enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE)

    assert exc_info.value.recipe_id == FACEBOOK_PAGES_5_RECIPE.id
    _assert_enforcement_codes(exc_info.value, "SEMANTIC_NAME_MISMATCH")


def test_recipe_enforcement_rejects_wrong_order() -> None:
    blocks = build_5_blocks(_base_context())
    blocks[2] = {**blocks[2], "order": 4}

    with pytest.raises(ReportRecipeEnforcementError) as exc_info:
        enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE)

    _assert_enforcement_codes(exc_info.value, "ORDER_MISMATCH")


def test_recipe_enforcement_rejects_missing_slide() -> None:
    blocks = build_5_blocks(_base_context())[:-1]

    with pytest.raises(ReportRecipeEnforcementError) as exc_info:
        enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE)

    _assert_enforcement_codes(exc_info.value, "SLIDE_COUNT_MISMATCH", "ORDER_MISMATCH")


def test_recipe_enforcement_rejects_extra_slide() -> None:
    blocks = build_5_blocks(_base_context())
    extra_block = _replace_data(blocks[-1], semantic_name="extra_summary")
    extra_block = {**extra_block, "order": 6}
    blocks.append(extra_block)

    with pytest.raises(ReportRecipeEnforcementError) as exc_info:
        enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE)

    _assert_enforcement_codes(exc_info.value, "SLIDE_COUNT_MISMATCH", "ORDER_MISMATCH")


def test_recipe_enforcement_returns_original_blocks_unchanged() -> None:
    blocks = build_5_blocks(_base_context())
    original_snapshot = copy.deepcopy(blocks)
    original_object_ids = [id(block) for block in blocks]

    returned = enforce_report_recipe(blocks, FACEBOOK_PAGES_5_RECIPE)

    assert returned is blocks
    assert blocks == original_snapshot
    assert [id(block) for block in blocks] == original_object_ids


def test_non_facebook_report_paths_are_not_recipe_enforcement_targets() -> None:
    assert should_enforce_facebook_pages_5_recipe(
        report_source="meta_pages_v2",
        integration_type="facebook_pages",
        effective_slide_limit=5,
    )
    assert not should_enforce_facebook_pages_5_recipe(
        report_source="meta_pages_v2",
        integration_type="instagram_business",
        effective_slide_limit=5,
    )
    assert not should_enforce_facebook_pages_5_recipe(
        report_source="meta_pages_v2",
        integration_type="facebook_pages",
        effective_slide_limit=10,
    )
    assert not should_enforce_facebook_pages_5_recipe(
        report_source="meta_ads",
        integration_type="meta_ads",
        effective_slide_limit=5,
    )
    assert not should_enforce_facebook_pages_5_recipe(
        report_source="multi_source_v1",
        integration_type="facebook_pages",
        effective_slide_limit=5,
    )
