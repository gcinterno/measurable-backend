from __future__ import annotations

from dataclasses import dataclass


FACEBOOK_PAGES_5_RECIPE_ID = "facebook_pages_5"
FACEBOOK_PAGES_PLATFORM = "facebook_pages"


@dataclass(frozen=True)
class ReportRecipeSlide:
    """A reusable Recipe slide definition.

    Python uses semantic_name. The wire/frontend field name is semanticName.
    """

    order: int
    semantic_name: str


@dataclass(frozen=True)
class ReportRecipe:
    id: str
    platform: str
    name: str
    version: int
    slides: tuple[ReportRecipeSlide, ...]


FACEBOOK_PAGES_5_RECIPE = ReportRecipe(
    id=FACEBOOK_PAGES_5_RECIPE_ID,
    platform=FACEBOOK_PAGES_PLATFORM,
    name="Facebook Pages · 5 Slides",
    version=1,
    slides=(
        ReportRecipeSlide(order=1, semantic_name="cover"),
        ReportRecipeSlide(order=2, semantic_name="organic_impressions_overview"),
        ReportRecipeSlide(order=3, semantic_name="engagement_overview"),
        ReportRecipeSlide(order=4, semantic_name="page_views_overview"),
        ReportRecipeSlide(order=5, semantic_name="executive_summary"),
    ),
)

_REPORT_RECIPES_BY_ID: dict[str, ReportRecipe] = {
    FACEBOOK_PAGES_5_RECIPE.id: FACEBOOK_PAGES_5_RECIPE,
}


def get_report_recipe(recipe_id: str) -> ReportRecipe | None:
    return _REPORT_RECIPES_BY_ID.get(recipe_id)


__all__ = [
    "FACEBOOK_PAGES_5_RECIPE",
    "FACEBOOK_PAGES_5_RECIPE_ID",
    "FACEBOOK_PAGES_PLATFORM",
    "ReportRecipe",
    "ReportRecipeSlide",
    "get_report_recipe",
]
