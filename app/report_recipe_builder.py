from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from . import main as report_main
from .report_recipe_validation import validate_blocks_against_recipe
from .report_recipes import (
    ReportRecipe,
    ReportRecipeSlide,
    validate_facebook_instagram_10_recipe,
    validate_report_recipe_catalog,
)


class ReportRecipeBuilderError(ValueError):
    pass


class InvalidReportRecipeForBuildError(ReportRecipeBuilderError):
    pass


class UnsupportedRecipeSemanticNameError(ReportRecipeBuilderError):
    pass


class MissingRecipeSemanticHandlerError(ReportRecipeBuilderError):
    pass


@dataclass(frozen=True)
class FacebookPages5RecipeBuildState:
    dataset: dict[str, Any]
    report_timeframe: dict[str, Any]
    period_label: str
    resolved_branding: dict[str, Any]
    metric_context: dict[str, Any]
    organic_impressions_payload: dict[str, Any]
    engagement_payload: dict[str, Any]
    page_views_payload: dict[str, Any]


FacebookInstagram10RecipeBuildState = dict[str, Any]

RecipeSlideBlockHandler = Callable[
    [ReportRecipeSlide, Any],
    dict[str, Any],
]


def _prepare_facebook_pages_5_recipe_build_state(dataset: dict[str, Any]) -> FacebookPages5RecipeBuildState:
    report_timeframe = dataset["report_timeframe"]
    period_label = str(report_timeframe.get("label") or "Selected period")
    resolved_branding = report_main.resolve_report_branding(
        None,
        None,
        str(dataset.get("plan") or ""),
        preferred_branding=dataset.get("branding") if isinstance(dataset.get("branding"), dict) else None,
    )
    metric_context = {**dataset, "branding": resolved_branding}
    report_main._log_report_product_event(
        "REPORT_PRODUCT_RESOLVER_STARTED",
        context=metric_context,
        slide_type="facebook_pages_5_slide_report",
        raw_metric_name="catalog_bootstrap",
        normalized_field="report_metric_catalog",
        availability_status="started",
    )
    organic_impressions_payload = report_main._build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="organic_impressions",
        title="ORGANIC VISIBILITY",
        label="TOTAL ORGANIC IMPRESSIONS",
        semantic_name="organic_impressions_overview",
    )
    engagement_payload = report_main._build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="engagement",
        title="ENGAGEMENT",
        label="TOTAL ENGAGEMENT",
        semantic_name="engagement_overview",
    )
    page_views_payload = report_main._build_facebook_pages_metric_slide_payload(
        metric_context,
        metric_key="page_views",
        title="PAGE VIEWS",
        label="TOTAL PAGE VIEWS",
        semantic_name="page_views_overview",
    )
    followers_details = report_main._facebook_pages_metric_details(metric_context, "followers")
    report_main._log_facebook_pages_report_metric_payload(
        metric_context,
        {
            "metric_key": "followers",
            "metric_source": "followers_count",
            "total": followers_details.get("total"),
            "formatted_total": report_main._format_metric_summary_value(followers_details.get("total")),
            "daily_series": [],
            "unavailable_reason": report_main._facebook_metric_audit_reason(metric_context, "followers"),
        },
    )
    if report_main._meta_integration_type(metric_context) in {"facebook_pages", "meta_pages"}:
        report_inputs = report_main._meta_report_inputs(metric_context)
        report_main.logger.info(
            "[FiveSlideReport][facebook.debug]",
            extra={
                "integration": report_main._meta_integration_type(metric_context),
                "dataset_keys_available": sorted(str(key) for key in metric_context.keys()),
                "report_inputs_keys_available": sorted(str(key) for key in report_inputs.keys()),
                "insights_keys": sorted(str(key) for key in (report_inputs.get("insights") or {}).keys())
                if isinstance(report_inputs.get("insights"), dict)
                else [],
                "daily_keys": sorted(str(key) for key in (report_inputs.get("daily") or {}).keys())
                if isinstance(report_inputs.get("daily"), dict)
                else [],
                "values_keys": sorted(str(key) for key in (report_inputs.get("values") or {}).keys())
                if isinstance(report_inputs.get("values"), dict)
                else [],
                "metric_values_keys": sorted(str(key) for key in (report_inputs.get("metric_values") or {}).keys())
                if isinstance(report_inputs.get("metric_values"), dict)
                else [],
                "chart_data_keys": sorted(str(key) for key in (metric_context.get("chart_data") or {}).keys())
                if isinstance(metric_context.get("chart_data"), dict)
                else [],
                "organic_impressions_daily_source_path": organic_impressions_payload.get("daily_series_source_path"),
                "organic_impressions_daily_source_metric_key": organic_impressions_payload.get("daily_series_source_metric_key"),
                "engagement_daily_source_path": engagement_payload.get("daily_series_source_path"),
                "engagement_daily_source_metric_key": engagement_payload.get("daily_series_source_metric_key"),
                "page_views_daily_source_path": page_views_payload.get("daily_series_source_path"),
                "page_views_daily_source_metric_key": page_views_payload.get("daily_series_source_metric_key"),
            },
        )
    return FacebookPages5RecipeBuildState(
        dataset=dataset,
        report_timeframe=report_timeframe,
        period_label=period_label,
        resolved_branding=resolved_branding,
        metric_context=metric_context,
        organic_impressions_payload=organic_impressions_payload,
        engagement_payload=engagement_payload,
        page_views_payload=page_views_payload,
    )


def _build_cover_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
) -> dict[str, Any]:
    return report_main._build_facebook_pages_5_cover_block(
        dataset=state.dataset,
        report_timeframe=state.report_timeframe,
        resolved_branding=state.resolved_branding,
        metric_context=state.metric_context,
        order=recipe_slide.order,
    )


def _build_organic_impressions_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
) -> dict[str, Any]:
    return report_main._build_facebook_pages_5_metric_block(
        order=recipe_slide.order,
        payload=state.organic_impressions_payload,
    )


def _build_engagement_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
) -> dict[str, Any]:
    return report_main._build_facebook_pages_5_metric_block(
        order=recipe_slide.order,
        payload=state.engagement_payload,
    )


def _build_page_views_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
) -> dict[str, Any]:
    return report_main._build_facebook_pages_5_metric_block(
        order=recipe_slide.order,
        payload=state.page_views_payload,
    )


def _build_executive_summary_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
) -> dict[str, Any]:
    return report_main._build_facebook_pages_5_summary_block(
        order=recipe_slide.order,
        metric_context=state.metric_context,
        period_label=state.period_label,
        organic_impressions_payload=state.organic_impressions_payload,
        engagement_payload=state.engagement_payload,
        page_views_payload=state.page_views_payload,
    )


FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS: Mapping[str, RecipeSlideBlockHandler] = MappingProxyType(
    {
        "cover": _build_cover_block,
        "organic_impressions_overview": _build_organic_impressions_block,
        "engagement_overview": _build_engagement_block,
        "page_views_overview": _build_page_views_block,
        "executive_summary": _build_executive_summary_block,
    }
)


def _prepare_facebook_instagram_10_recipe_build_state(
    context: dict[str, Any],
) -> FacebookInstagram10RecipeBuildState:
    return report_main._multi_source_prepare_10_block_state(context)


def _build_multi_source_cover_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_cover_10_block(state, recipe_slide.order)


def _build_multi_source_reach_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_reach_10_block(state, recipe_slide.order)


def _build_multi_source_impressions_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_impressions_10_block(state, recipe_slide.order)


def _build_multi_source_engagement_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_engagement_10_block(state, recipe_slide.order)


def _build_multi_source_page_visits_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_page_visits_10_block(state, recipe_slide.order)


def _build_multi_source_audience_growth_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_audience_growth_10_block(state, recipe_slide.order)


def _build_multi_source_content_activity_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_content_activity_10_block(state, recipe_slide.order)


def _build_multi_source_top_performing_content_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_top_performing_content_10_block(state, recipe_slide.order)


def _build_multi_source_executive_insights_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_executive_insights_10_block(state, recipe_slide.order)


def _build_multi_source_recommendations_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    return report_main._multi_source_build_recommendations_10_block(state, recipe_slide.order)


FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS: Mapping[str, RecipeSlideBlockHandler] = MappingProxyType(
    {
        "cover": _build_multi_source_cover_block,
        "reach": _build_multi_source_reach_block,
        "impressions": _build_multi_source_impressions_block,
        "engagement": _build_multi_source_engagement_block,
        "page_visits": _build_multi_source_page_visits_block,
        "audience_growth": _build_multi_source_audience_growth_block,
        "content_activity": _build_multi_source_content_activity_block,
        "top_performing_content": _build_multi_source_top_performing_content_block,
        "executive_insights": _build_multi_source_executive_insights_block,
        "recommendations": _build_multi_source_recommendations_block,
    }
)


def _validate_recipe_for_build(recipe: ReportRecipe) -> None:
    try:
        validate_report_recipe_catalog((recipe,))
    except ValueError as exc:
        raise InvalidReportRecipeForBuildError(
            f"Recipe {recipe.id or '<empty>'} is structurally invalid: {exc}"
        ) from exc


def _validate_facebook_instagram_10_recipe_for_build(recipe: ReportRecipe | None) -> None:
    try:
        validate_facebook_instagram_10_recipe(recipe)
    except ValueError as exc:
        recipe_id = getattr(recipe, "id", None) or "<missing>"
        raise InvalidReportRecipeForBuildError(
            f"Recipe {recipe_id} is not a valid facebook_instagram_10 Recipe: {exc}"
        ) from exc


def build_block_for_recipe_slide(
    recipe_slide: ReportRecipeSlide,
    state: FacebookPages5RecipeBuildState,
    *,
    semantic_handlers: Mapping[str, RecipeSlideBlockHandler] | None = None,
) -> dict[str, Any]:
    handlers = semantic_handlers or FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS
    semantic_name = recipe_slide.semantic_name
    if semantic_name not in FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS:
        raise UnsupportedRecipeSemanticNameError(
            f"Unsupported Facebook Pages 5 Recipe semantic_name: {semantic_name}"
        )
    handler = handlers.get(semantic_name)
    if handler is None:
        raise MissingRecipeSemanticHandlerError(
            f"Missing Facebook Pages 5 Recipe handler for semantic_name: {semantic_name}"
        )
    return handler(recipe_slide, state)


def build_facebook_instagram_10_block_for_recipe_slide(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
    *,
    semantic_handlers: Mapping[str, RecipeSlideBlockHandler] | None = None,
) -> dict[str, Any]:
    handlers = semantic_handlers or FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS
    semantic_name = recipe_slide.semantic_name
    if semantic_name not in FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS:
        raise UnsupportedRecipeSemanticNameError(
            f"Unsupported Facebook + Instagram 10 Recipe semantic_name: {semantic_name}"
        )
    handler = handlers.get(semantic_name)
    if handler is None:
        raise MissingRecipeSemanticHandlerError(
            f"Missing Facebook + Instagram 10 Recipe handler for semantic_name: {semantic_name}"
        )
    return handler(recipe_slide, state)


def build_facebook_pages_5_blocks_from_recipe(
    recipe: ReportRecipe,
    context: dict[str, Any],
    *,
    semantic_handlers: Mapping[str, RecipeSlideBlockHandler] | None = None,
) -> list[dict[str, Any]]:
    _validate_recipe_for_build(recipe)
    state = _prepare_facebook_pages_5_recipe_build_state(context)
    raw_blocks = [
        build_block_for_recipe_slide(
            recipe_slide,
            state,
            semantic_handlers=semantic_handlers,
        )
        for recipe_slide in sorted(recipe.slides, key=lambda slide: slide.order)
    ]
    final_blocks = report_main._meta_enrich_data_blocks(
        state.metric_context,
        report_main._renumber_blocks(raw_blocks),
    )
    validation_result = validate_blocks_against_recipe(final_blocks, recipe)
    if not validation_result.valid:
        raise InvalidReportRecipeForBuildError(
            f"Generated blocks do not match Recipe {recipe.id}: "
            + ", ".join(error.code for error in validation_result.errors)
        )
    return final_blocks


def build_facebook_instagram_10_blocks_from_recipe(
    recipe: ReportRecipe | None,
    context: dict[str, Any],
    *,
    semantic_handlers: Mapping[str, RecipeSlideBlockHandler] | None = None,
) -> list[dict[str, Any]]:
    _validate_facebook_instagram_10_recipe_for_build(recipe)
    assert recipe is not None
    state = _prepare_facebook_instagram_10_recipe_build_state(context)
    blocks = [
        build_facebook_instagram_10_block_for_recipe_slide(
            recipe_slide,
            state,
            semantic_handlers=semantic_handlers,
        )
        for recipe_slide in sorted(recipe.slides, key=lambda slide: slide.order)
    ]
    validation_result = validate_blocks_against_recipe(blocks, recipe)
    if not validation_result.valid:
        raise InvalidReportRecipeForBuildError(
            f"Generated blocks do not match Recipe {recipe.id}: "
            + ", ".join(error.code for error in validation_result.errors)
        )
    return blocks


__all__ = [
    "FACEBOOK_INSTAGRAM_10_RECIPE_BLOCK_HANDLERS",
    "FACEBOOK_PAGES_5_RECIPE_BLOCK_HANDLERS",
    "FacebookInstagram10RecipeBuildState",
    "FacebookPages5RecipeBuildState",
    "InvalidReportRecipeForBuildError",
    "MissingRecipeSemanticHandlerError",
    "RecipeSlideBlockHandler",
    "ReportRecipeBuilderError",
    "UnsupportedRecipeSemanticNameError",
    "build_block_for_recipe_slide",
    "build_facebook_instagram_10_block_for_recipe_slide",
    "build_facebook_instagram_10_blocks_from_recipe",
    "build_facebook_pages_5_blocks_from_recipe",
]
