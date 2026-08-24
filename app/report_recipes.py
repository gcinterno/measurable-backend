from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


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

_REGISTERED_REPORT_RECIPES: tuple[ReportRecipe, ...] = (
    FACEBOOK_PAGES_5_RECIPE,
)


def validate_report_recipe_catalog(recipes: tuple[ReportRecipe, ...]) -> None:
    errors: list[str] = []
    seen_recipe_ids: set[str] = set()

    for recipe in recipes:
        if not recipe.id:
            errors.append("recipe id is empty")
        elif recipe.id in seen_recipe_ids:
            errors.append(f"duplicate recipe id: {recipe.id}")
        else:
            seen_recipe_ids.add(recipe.id)

        if not recipe.platform:
            errors.append(f"recipe {recipe.id or '<empty>'} platform is empty")
        if not recipe.name:
            errors.append(f"recipe {recipe.id or '<empty>'} name is empty")
        if recipe.version < 1:
            errors.append(f"recipe {recipe.id or '<empty>'} version must be positive")
        if not recipe.slides:
            errors.append(f"recipe {recipe.id or '<empty>'} must contain at least one slide")

        orders = [slide.order for slide in recipe.slides]
        semantic_names = [slide.semantic_name for slide in recipe.slides]
        if len(set(orders)) != len(orders):
            errors.append(f"recipe {recipe.id or '<empty>'} has duplicate slide order")
        if len(set(semantic_names)) != len(semantic_names):
            errors.append(f"recipe {recipe.id or '<empty>'} has duplicate semantic_name")
        if any(not semantic_name for semantic_name in semantic_names):
            errors.append(f"recipe {recipe.id or '<empty>'} has empty semantic_name")
        expected_orders = list(range(1, len(recipe.slides) + 1))
        if sorted(orders) != expected_orders:
            errors.append(
                f"recipe {recipe.id or '<empty>'} slide order must be contiguous from 1"
            )

    if errors:
        raise ValueError("; ".join(errors))


def _build_report_recipe_catalog(
    recipes: tuple[ReportRecipe, ...],
) -> Mapping[str, ReportRecipe]:
    validate_report_recipe_catalog(recipes)
    return MappingProxyType({recipe.id: recipe for recipe in recipes})


REPORT_RECIPES = _build_report_recipe_catalog(_REGISTERED_REPORT_RECIPES)


def get_report_recipe(recipe_id: str) -> ReportRecipe | None:
    return REPORT_RECIPES.get(recipe_id)


def list_report_recipes() -> tuple[ReportRecipe, ...]:
    return tuple(REPORT_RECIPES[recipe_id] for recipe_id in sorted(REPORT_RECIPES))


def get_report_recipes_for_platform(platform: str) -> tuple[ReportRecipe, ...]:
    return tuple(
        recipe
        for recipe in list_report_recipes()
        if recipe.platform == platform
    )


__all__ = [
    "FACEBOOK_PAGES_5_RECIPE",
    "FACEBOOK_PAGES_5_RECIPE_ID",
    "FACEBOOK_PAGES_PLATFORM",
    "REPORT_RECIPES",
    "ReportRecipe",
    "ReportRecipeSlide",
    "get_report_recipe",
    "get_report_recipes_for_platform",
    "list_report_recipes",
    "validate_report_recipe_catalog",
]
