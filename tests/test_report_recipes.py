from __future__ import annotations

import pytest

from app.report_recipes import (
    FACEBOOK_PAGES_5_RECIPE,
    FACEBOOK_PAGES_5_RECIPE_ID,
    FACEBOOK_PAGES_PLATFORM,
    REPORT_RECIPES,
    ReportRecipe,
    ReportRecipeSlide,
    get_report_recipe,
    get_report_recipes_for_platform,
    list_report_recipes,
    validate_report_recipe_catalog,
)


def test_facebook_pages_5_recipe_remains_canonical() -> None:
    recipe = get_report_recipe(FACEBOOK_PAGES_5_RECIPE_ID)

    assert recipe is FACEBOOK_PAGES_5_RECIPE
    assert recipe.id == "facebook_pages_5"
    assert recipe.platform == "facebook_pages"
    assert recipe.name == "Facebook Pages · 5 Slides"
    assert recipe.version == 1
    assert len(recipe.slides) == 5
    assert [slide.order for slide in recipe.slides] == [1, 2, 3, 4, 5]
    assert [slide.semantic_name for slide in recipe.slides] == [
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
        "executive_summary",
    ]
    assert not hasattr(recipe, "slide_count")
    assert all(not hasattr(slide, "kind") for slide in recipe.slides)


def test_list_report_recipes_is_deterministic() -> None:
    assert list_report_recipes() == (FACEBOOK_PAGES_5_RECIPE,)


def test_get_report_recipe_returns_none_for_unknown_recipe() -> None:
    assert get_report_recipe("unknown") is None


def test_get_report_recipes_for_platform_filters_catalog() -> None:
    assert get_report_recipes_for_platform(FACEBOOK_PAGES_PLATFORM) == (
        FACEBOOK_PAGES_5_RECIPE,
    )
    assert get_report_recipes_for_platform("instagram_business") == ()


def test_report_recipes_catalog_is_read_only() -> None:
    with pytest.raises(TypeError):
        REPORT_RECIPES["other"] = FACEBOOK_PAGES_5_RECIPE  # type: ignore[index]


def test_validate_report_recipe_catalog_rejects_invalid_recipes() -> None:
    duplicate_id = ReportRecipe(
        id=FACEBOOK_PAGES_5_RECIPE.id,
        platform="facebook_pages",
        name="Duplicate",
        version=1,
        slides=(ReportRecipeSlide(order=1, semantic_name="cover"),),
    )
    invalid = ReportRecipe(
        id="",
        platform="",
        name="",
        version=0,
        slides=(
            ReportRecipeSlide(order=2, semantic_name="cover"),
            ReportRecipeSlide(order=2, semantic_name="cover"),
        ),
    )
    empty_slides = ReportRecipe(
        id="empty_slides",
        platform="facebook_pages",
        name="Empty Slides",
        version=1,
        slides=(),
    )
    empty_semantic_name = ReportRecipe(
        id="empty_semantic_name",
        platform="facebook_pages",
        name="Empty Semantic Name",
        version=1,
        slides=(ReportRecipeSlide(order=1, semantic_name=""),),
    )

    with pytest.raises(ValueError) as exc_info:
        validate_report_recipe_catalog(
            (
                FACEBOOK_PAGES_5_RECIPE,
                duplicate_id,
                invalid,
                empty_slides,
                empty_semantic_name,
            )
        )

    message = str(exc_info.value)
    assert "duplicate recipe id: facebook_pages_5" in message
    assert "recipe id is empty" in message
    assert "platform is empty" in message
    assert "name is empty" in message
    assert "version must be positive" in message
    assert "must contain at least one slide" in message
    assert "duplicate slide order" in message
    assert "duplicate semantic_name" in message
    assert "empty semantic_name" in message
    assert "slide order must be contiguous from 1" in message
