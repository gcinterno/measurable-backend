from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

TEST_DB_PATH = Path("/tmp/measurable_report_recipe_production_cutover.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app import main as main_module
from app.report_recipe_validation import validate_blocks_against_recipe
from app.report_recipes import FACEBOOK_PAGES_5_RECIPE


def _base_context(*, integration_type: str = "facebook_pages") -> dict[str, Any]:
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
            "integration_type": integration_type,
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


def _slide_limits(*, requested_slides: int = 5, effective_slide_limit: int = 5) -> dict[str, Any]:
    return {
        "requested_slides": requested_slides,
        "effective_slide_limit": effective_slide_limit,
    }


def _payloads(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(str(block["data_json"])) for block in blocks]


def test_default_mode_uses_recipe_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.report_recipe_builder as recipe_builder_module

    calls: list[tuple[Any, dict[str, Any]]] = []
    expected = [{"type": "sentinel", "order": 1, "data_json": "{}", "editable_fields_json": "[]"}]

    def fake_recipe_builder(recipe: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append((recipe, context))
        return expected

    monkeypatch.delenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, raising=False)
    monkeypatch.setattr(recipe_builder_module, "build_facebook_pages_5_blocks_from_recipe", fake_recipe_builder)

    assert main_module.build_facebook_pages_5_blocks(_base_context()) == expected
    assert calls == [(FACEBOOK_PAGES_5_RECIPE, _base_context())]


def test_explicit_recipe_mode_uses_recipe_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.report_recipe_builder as recipe_builder_module

    calls: list[Any] = []

    def fake_recipe_builder(recipe: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(recipe)
        return []

    monkeypatch.setenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, "recipe")
    monkeypatch.setattr(recipe_builder_module, "build_facebook_pages_5_blocks_from_recipe", fake_recipe_builder)

    assert main_module.build_facebook_pages_5_blocks(_base_context()) == []
    assert calls == [FACEBOOK_PAGES_5_RECIPE]


def test_legacy_mode_uses_build_5_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.report_recipe_builder as recipe_builder_module

    calls: list[dict[str, Any]] = []
    expected = [{"type": "legacy", "order": 1, "data_json": "{}", "editable_fields_json": "[]"}]

    def fake_legacy_builder(context: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(context)
        return expected

    def fail_recipe_builder(recipe: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise AssertionError("Recipe builder should not run in legacy mode")

    monkeypatch.setenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, "legacy")
    monkeypatch.setattr(main_module, "build_5_blocks", fake_legacy_builder)
    monkeypatch.setattr(recipe_builder_module, "build_facebook_pages_5_blocks_from_recipe", fail_recipe_builder)

    context = _base_context()
    assert main_module.build_facebook_pages_5_blocks(context) == expected
    assert calls == [context]


def test_unknown_builder_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, "unsupported")

    with pytest.raises(HTTPException) as exc_info:
        main_module.build_facebook_pages_5_blocks(_base_context())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "facebook_pages_recipe_builder_config_invalid"


def test_recipe_builder_failure_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.report_recipe_builder as recipe_builder_module

    def fail_recipe_builder(recipe: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise recipe_builder_module.InvalidReportRecipeForBuildError("invalid")

    monkeypatch.setenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, "recipe")
    monkeypatch.setattr(recipe_builder_module, "build_facebook_pages_5_blocks_from_recipe", fail_recipe_builder)

    with pytest.raises(HTTPException) as exc_info:
        main_module.build_facebook_pages_5_blocks(_base_context())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "facebook_pages_recipe_builder_failed"


def test_recipe_mode_output_matches_legacy_build_5_blocks_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, raising=False)
    context = _base_context()

    legacy_blocks = main_module.build_5_blocks(context)
    recipe_blocks = main_module.build_facebook_pages_5_blocks(context)

    assert recipe_blocks == legacy_blocks
    assert _payloads(recipe_blocks) == _payloads(legacy_blocks)


def test_recipe_output_still_passes_production_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, raising=False)
    blocks = main_module.build_facebook_pages_5_blocks(_base_context())

    assert main_module._enforce_facebook_pages_5_recipe(blocks) is blocks


def test_invalid_recipe_structure_fails_before_reportblock_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, raising=False)
    blocks = main_module.build_facebook_pages_5_blocks(_base_context())[:-1]

    with pytest.raises(HTTPException) as exc_info:
        main_module._enforce_facebook_pages_5_recipe(blocks)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "facebook_pages_recipe_enforcement_failed"


def test_official_facebook_pages_5_path_uses_controlled_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = [{"type": "official", "order": 1, "data_json": "{}", "editable_fields_json": "[]"}]
    calls: list[dict[str, Any]] = []

    def fake_facebook_pages_5_builder(context: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(context)
        return expected

    def fail_generic_builder(requested_slides: int, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise AssertionError("Generic build_blocks should not run for official Facebook Pages 5")

    context = _base_context()
    monkeypatch.setattr(main_module, "build_facebook_pages_5_blocks", fake_facebook_pages_5_builder)
    monkeypatch.setattr(main_module, "build_blocks", fail_generic_builder)

    blocks, used_recipe_path = main_module._build_meta_dataset_report_blocks(
        report_source="meta_pages_v2",
        report_inputs={"integration_type": "facebook_pages"},
        slide_limits=_slide_limits(),
        block_build_context=context,
    )

    assert blocks == expected
    assert used_recipe_path is True
    assert calls == [context]


@pytest.mark.parametrize(
    ("report_source", "integration_type"),
    [
        ("meta_pages_v2", "instagram_business"),
        ("meta_ads", "meta_ads"),
        ("multi_source_v1", "facebook_pages"),
        ("legacy", "facebook_pages"),
    ],
)
def test_non_facebook_pages_5_paths_keep_generic_builder(
    monkeypatch: pytest.MonkeyPatch,
    report_source: str,
    integration_type: str,
) -> None:
    expected = [{"type": "generic", "order": 1, "data_json": "{}", "editable_fields_json": "[]"}]
    calls: list[tuple[int, dict[str, Any]]] = []

    def fail_facebook_pages_5_builder(context: dict[str, Any]) -> list[dict[str, Any]]:
        raise AssertionError("Facebook Pages 5 Recipe builder should not run")

    def fake_generic_builder(requested_slides: int, context: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append((requested_slides, context))
        return expected

    context = _base_context(integration_type=integration_type)
    monkeypatch.setattr(main_module, "build_facebook_pages_5_blocks", fail_facebook_pages_5_builder)
    monkeypatch.setattr(main_module, "build_blocks", fake_generic_builder)

    blocks, used_recipe_path = main_module._build_meta_dataset_report_blocks(
        report_source=report_source,
        report_inputs={"integration_type": integration_type},
        slide_limits=_slide_limits(requested_slides=5, effective_slide_limit=5),
        block_build_context=context,
    )

    assert blocks == expected
    assert used_recipe_path is False
    assert calls == [(5, context)]


def test_generic_requested_slides_under_five_still_uses_legacy_build_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.report_recipe_builder as recipe_builder_module

    def fail_recipe_builder(recipe: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise AssertionError("Generic build_blocks must not call the Recipe builder")

    monkeypatch.setattr(recipe_builder_module, "build_facebook_pages_5_blocks_from_recipe", fail_recipe_builder)

    blocks = main_module.build_blocks(5, _base_context())

    assert len(blocks) == 5
    assert _payloads(blocks)[0]["semantic_name"] == "cover"


def test_production_like_official_path_generates_valid_persistence_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(main_module.FACEBOOK_PAGES_5_RECIPE_BUILDER_ENV, raising=False)
    context = _base_context()

    blocks, used_recipe_path = main_module._build_meta_dataset_report_blocks(
        report_source="meta_pages_v2",
        report_inputs={"integration_type": "facebook_pages"},
        slide_limits=_slide_limits(requested_slides=5, effective_slide_limit=5),
        block_build_context=context,
    )
    blocks = main_module._ensure_facebook_pages_five_slide_structure(blocks)
    blocks = main_module._enforce_facebook_pages_5_recipe(blocks)

    assert used_recipe_path is True
    assert len(blocks) == 5
    assert [block["order"] for block in blocks] == [1, 2, 3, 4, 5]
    assert [payload["semantic_name"] for payload in _payloads(blocks)] == [
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
        "executive_summary",
    ]
    assert validate_blocks_against_recipe(blocks, FACEBOOK_PAGES_5_RECIPE).valid
    for block in blocks:
        assert set(block) >= {"type", "order", "data_json", "editable_fields_json"}
        assert isinstance(block["data_json"], str)
        assert isinstance(block["editable_fields_json"], str)
