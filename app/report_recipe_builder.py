from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from . import main as report_main
from .canonical_metric_catalog import (
    AVAILABLE,
    NOT_COMPARABLE,
    SOURCE_ONLY,
    ResolvedMetric,
    ResolvedMetricSource,
    build_canonical_metric_catalog_for_sources,
    resolve_metrics,
)
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
_PREVIOUS_VALUE_UNSET = object()


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
    state = report_main._multi_source_prepare_10_block_state(context)
    catalog = build_canonical_metric_catalog_for_sources(state.get("sources") or [])
    resolutions = resolve_metrics(
        (
            "reach",
            "visibility",
            "impressions",
            "engagement",
            "page_profile_activity",
            "audience_size",
            "content_activity",
            "top_content",
            "media_count",
            "media_reach",
            "media_views",
            "media_likes",
            "media_comments",
            "media_shares",
            "media_saves",
            "media_replies",
        ),
        catalog,
    )
    state["_canonical_metric_catalog"] = catalog
    state["_canonical_metric_resolutions"] = resolutions
    state["canonical_metric_catalog"] = [record.as_dict() for record in catalog]
    state["canonical_metric_resolutions"] = {
        canonical_metric: resolution.as_dict()
        for canonical_metric, resolution in resolutions.items()
    }
    return state


def _facebook_instagram_block_payload(block: dict[str, Any]) -> dict[str, Any]:
    raw_payload = block.get("data_json")
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _with_facebook_instagram_block_payload(
    block: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(block)
    updated["data_json"] = json.dumps(payload)
    return updated


def _facebook_instagram_resolution(
    state: FacebookInstagram10RecipeBuildState,
    canonical_metric: str,
) -> ResolvedMetric:
    resolutions = state.get("_canonical_metric_resolutions")
    if isinstance(resolutions, dict) and canonical_metric in resolutions:
        return resolutions[canonical_metric]
    raise ReportRecipeBuilderError(f"Missing canonical metric resolution: {canonical_metric}")


def _source_value_alias(source: ResolvedMetricSource, alias: str | None) -> dict[str, Any]:
    if alias == "engagement":
        return {"value": source.value, "engagement": source.value}
    if alias == "followers":
        return {
            "value": source.value,
            "followers": source.value,
            "net_follower_change": _sum_points(source.timeseries),
        }
    return {"value": source.value}


def _sum_points(points: tuple[dict[str, Any], ...]) -> int | float | None:
    total: int | float = 0
    has_value = False
    for point in points:
        value = report_main._meta_number(point.get("value"))
        if value is None:
            continue
        total += value
        has_value = True
    return total if has_value else None


def _source_contributions(
    resolution: ResolvedMetric,
    *,
    value_alias: str | None = None,
) -> list[dict[str, Any]]:
    contributions: list[dict[str, Any]] = []
    for source in resolution.sources:
        contribution = {
            "label": source.source_label,
            "source_type": source.source_type,
            "source_metric": source.source_metric,
            "canonical_metric": resolution.canonical_metric,
            "support_status": source.support_status,
            "timeseries_points": len(source.timeseries),
            "dataset_id": source.dataset_id,
            "provenance": {
                "metric_family": source.metadata.get("metric_family"),
                "aggregation_method": source.metadata.get("aggregation_method"),
                "comparability_group": source.metadata.get("comparability_group"),
                "fallback_used": source.metadata.get("fallback_used"),
                "fallback_source_metric": source.metadata.get("fallback_source_metric"),
            },
        }
        contribution.update(_source_value_alias(source, value_alias))
        contributions.append(contribution)
    return contributions


def _resolution_support_status(resolution: ResolvedMetric) -> str:
    statuses = [source.support_status for source in resolution.sources]
    if any(status == AVAILABLE for status in statuses):
        return AVAILABLE
    for status in ("unsupported", "empty", "missing", "not_requested"):
        if status in statuses:
            return status
    return "missing"


def _primary_available_source(resolution: ResolvedMetric) -> ResolvedMetricSource | None:
    for source in resolution.sources:
        if source.support_status == AVAILABLE and source.value is not None:
            return source
    return None


def _resolution_primary_value(resolution: ResolvedMetric) -> int | float | None:
    if resolution.aggregation_method in {NOT_COMPARABLE, SOURCE_ONLY}:
        source = _primary_available_source(resolution)
        return source.value if source is not None else None
    return resolution.combined_value


def _source_series(resolution: ResolvedMetric) -> list[dict[str, Any]]:
    return [
        {
            "label": source.source_label,
            "source_type": source.source_type,
            "source_metric": source.source_metric,
            "support_status": source.support_status,
            "points": [dict(point) for point in source.timeseries],
        }
        for source in resolution.sources
    ]


def _resolution_points(resolution: ResolvedMetric) -> list[dict[str, Any]]:
    if resolution.aggregation_method in {NOT_COMPARABLE, SOURCE_ONLY}:
        source = _primary_available_source(resolution)
        return [dict(point) for point in source.timeseries] if source is not None else []
    grouped: dict[str, dict[str, Any]] = {}
    for source in resolution.sources:
        if source.support_status != AVAILABLE:
            continue
        for point in source.timeseries:
            point_date = str(point.get("date") or point.get("label") or "").strip()
            if not point_date:
                continue
            value = report_main._meta_number(point.get("value"))
            if value is None:
                continue
            item = grouped.setdefault(
                point_date,
                {
                    "date": point_date,
                    "label": point.get("label") or point_date,
                    "value": 0,
                },
            )
            item["value"] += int(round(value))
    return [grouped[key] for key in sorted(grouped.keys())]


def _metric_provenance(
    resolution: ResolvedMetric,
    *,
    primary_source: ResolvedMetricSource | None,
    primary_value: int | float | None,
) -> dict[str, Any]:
    return {
        "canonical_metric": resolution.canonical_metric,
        "metric_family": resolution.metric_family,
        "aggregation_method": resolution.aggregation_method,
        "comparability_group": resolution.comparability_group,
        "combined_value": resolution.combined_value,
        "primary_value": primary_value,
        "primary_value_source": primary_source.source_label if primary_source is not None else None,
        "primary_value_source_metric": primary_source.source_metric if primary_source is not None else None,
        "value_is_aggregated": resolution.aggregation_method not in {NOT_COMPARABLE, SOURCE_ONLY},
    }


def _canonical_metric_resolution_payload(
    resolution: ResolvedMetric,
    *,
    primary_value: int | float | None,
    support_status: str,
    source_contributions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "semantic": resolution.canonical_metric,
        "metric_family": resolution.metric_family,
        "value": primary_value,
        "combined_value": resolution.combined_value,
        "aggregation": resolution.aggregation_method,
        "aggregation_method": resolution.aggregation_method,
        "comparability_group": resolution.comparability_group,
        "status": support_status,
        "support_status": support_status,
        "sources": source_contributions,
    }


def _apply_metric_resolution_payload(
    block: dict[str, Any],
    state: FacebookInstagram10RecipeBuildState,
    canonical_metric: str,
    *,
    title: str | None = None,
    label: str | None = None,
    chart_metric: str | None = None,
    value_alias: str | None = None,
    value: int | float | None = None,
    previous_value: Any = _PREVIOUS_VALUE_UNSET,
    points: list[dict[str, Any]] | None = None,
    text: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolution = _facebook_instagram_resolution(state, canonical_metric)
    payload = _facebook_instagram_block_payload(block)
    primary_source = _primary_available_source(resolution)
    resolved_value = _resolution_primary_value(resolution) if value is None else value
    resolved_previous_value = (
        payload.get("previous_value")
        if previous_value is _PREVIOUS_VALUE_UNSET
        else previous_value
    )
    resolved_points = _resolution_points(resolution) if points is None else points
    support_status = _resolution_support_status(resolution)
    growth = report_main._growth_metadata_from_values(resolved_value, resolved_previous_value)
    source_contributions = _source_contributions(resolution, value_alias=value_alias)
    formatted_value = report_main._format_metric_summary_value(resolved_value)
    provenance = _metric_provenance(
        resolution,
        primary_source=primary_source,
        primary_value=resolved_value,
    )
    canonical_resolution_payload = _canonical_metric_resolution_payload(
        resolution,
        primary_value=resolved_value,
        support_status=support_status,
        source_contributions=source_contributions,
    )
    payload.update(
        {
            "canonical_semantic": canonical_metric,
            "canonical_metric": canonical_metric,
            "canonical_metric_resolution": canonical_resolution_payload,
            "metric_family": resolution.metric_family,
            "support_status": support_status,
            "source_contributions": source_contributions,
            "provenance": provenance,
            "timeseries_points": len(resolved_points),
            "value": resolved_value,
            "current_value": resolved_value,
            "primary_value": resolved_value,
            "metric_value": resolved_value,
            "total": resolved_value,
            "formatted_value": formatted_value,
            "formatted_total": formatted_value,
            "is_available": resolved_value is not None,
            "availability_status": support_status,
            "previous_value": resolved_previous_value,
            "growth": growth,
            "growth_percent": growth.get("growth_percent"),
            "growth_label": growth.get("growth_label"),
            "points": resolved_points,
        }
    )
    if title is not None:
        payload["title"] = title
    if label is not None:
        payload["label"] = label
    if text is not None:
        payload["text"] = text
    metric_name = chart_metric or canonical_metric
    payload["chart"] = {
        **(payload.get("chart") if isinstance(payload.get("chart"), dict) else {}),
        "label": f"{payload.get('title') or metric_name} - {state['period_label']}",
        "metric": metric_name,
        "points": resolved_points,
        "data": resolved_points,
        "series": _source_series(resolution),
        "timeframe": state["report_timeframe"],
        "is_available": bool(resolved_points),
        "primary_value": resolved_value,
    }
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    metrics.update(
        {
            "main": growth,
            "sources": source_contributions,
            "support_status": support_status,
            "provenance": provenance,
            "primary_value": resolved_value,
            "metric_value": resolved_value,
            "total": resolved_value,
            "formatted_value": formatted_value,
            "formatted_total": formatted_value,
            "is_available": resolved_value is not None,
            "canonical_metric_resolution": canonical_resolution_payload,
        }
    )
    payload["metrics"] = metrics
    if extra:
        payload.update(extra)
    return _with_facebook_instagram_block_payload(block, payload)


def _format_source_value(value: Any) -> str:
    return report_main._meta_format_number(value) if value is not None else "unavailable"


def _visibility_text(resolution: ResolvedMetric, period_label: str) -> str:
    parts = [
        f"{source.source_label} {source.source_metric}: {_format_source_value(source.value)} ({source.support_status})"
        for source in resolution.sources
    ]
    return (
        "Visibility is source-specific and is not summed across platforms for "
        f"{period_label}. "
        + "; ".join(parts)
        + "."
    )


def _source_metric_map(resolution: ResolvedMetric) -> dict[tuple[str, str], ResolvedMetricSource]:
    return {
        (source.source_type, source.source_label): source
        for source in resolution.sources
    }


def _source_performance_rows(state: FacebookInstagram10RecipeBuildState) -> list[dict[str, Any]]:
    reach = _source_metric_map(_facebook_instagram_resolution(state, "reach"))
    engagement = _source_metric_map(_facebook_instagram_resolution(state, "engagement"))
    content = _source_metric_map(_facebook_instagram_resolution(state, "content_activity"))
    rows: list[dict[str, Any]] = []
    for source in state.get("sources") or []:
        key = (
            str(source.get("source_type") or ""),
            str(source.get("label") or source.get("account_name") or ""),
        )
        reach_source = reach.get(key)
        engagement_source = engagement.get(key)
        content_source = content.get(key)
        reach_value = report_main._meta_number(reach_source.value if reach_source else None)
        engagement_value = report_main._meta_number(engagement_source.value if engagement_source else None)
        engagement_rate = (
            round((engagement_value / reach_value) * 100, 2)
            if reach_value not in (None, 0) and engagement_value is not None
            else None
        )
        rows.append(
            {
                "label": key[1],
                "source_type": key[0],
                "dataset_id": source.get("dataset_id"),
                "reach": reach_value,
                "engagement": engagement_value,
                "engagement_rate": engagement_rate,
                "content_count": content_source.value if content_source else None,
                "score": engagement_value
                if engagement_value is not None
                else content_source.value
                if content_source and content_source.value is not None
                else None,
            }
        )
    return rows


def _top_content_interaction_total(item: dict[str, Any]) -> int | float | None:
    total: int | float = 0
    has_value = False
    for key in ("likes", "reactions", "comments", "shares", "saves", "replies"):
        value = report_main._meta_number(item.get(key))
        if value is None:
            continue
        total += value
        has_value = True
    return total if has_value else None


def _catalog_top_posts(state: FacebookInstagram10RecipeBuildState) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for post in state.get("top_posts") or []:
        if not isinstance(post, dict):
            continue
        ranking_score = report_main._meta_number(post.get("ranking_score"))
        if ranking_score is None:
            ranking_score = report_main._meta_number(post.get("score"))
        if ranking_score is None:
            ranking_score = report_main._meta_number(post.get("engagement"))
        item = dict(post)
        item["ranking_score"] = ranking_score
        item["engagement_interactions"] = _top_content_interaction_total(item)
        item["ranking_method"] = "cross_source_meta_post_score"
        items.append(item)
    return items


def _catalog_executive_lines(state: FacebookInstagram10RecipeBuildState) -> list[str]:
    reach = _facebook_instagram_resolution(state, "reach")
    visibility = _facebook_instagram_resolution(state, "visibility")
    engagement = _facebook_instagram_resolution(state, "engagement")
    content = _facebook_instagram_resolution(state, "content_activity")
    top_post = state.get("top_post") if isinstance(state.get("top_post"), dict) else None
    performance_rows = [row for row in _source_performance_rows(state) if row.get("score") is not None]
    strongest = max(performance_rows, key=lambda row: row.get("score") or -1, default=None)
    weakest = min(performance_rows, key=lambda row: row.get("score") or float("inf"), default=None)
    visibility_parts = [
        f"{source.source_label} {source.source_metric} {_format_source_value(source.value)} ({source.support_status})"
        for source in visibility.sources
    ]
    lines = [
        (
            "Combined reach uses non-deduped available source reach and totaled "
            f"{_format_source_value(reach.combined_value)}."
        ),
        (
            "Visibility remains source-specific rather than summed: "
            + "; ".join(visibility_parts)
            + "."
        ),
        (
            "Combined engagement from available interaction metrics totaled "
            f"{_format_source_value(engagement.combined_value)}."
        ),
        (
            f"Strongest platform by available engagement volume: {strongest['label']}."
            if strongest
            else "Strongest platform could not be identified from available engagement metrics."
        ),
        (
            f"Weakest platform by available engagement volume: {weakest['label']}."
            if weakest
            else "Weakest platform could not be identified from available engagement metrics."
        ),
        (
            f"Tracked content pieces across sources totaled {_format_source_value(content.combined_value)}."
        ),
        (
            f"Top content came from {top_post.get('source')} with {_format_source_value(top_post.get('engagement'))} ranking signals."
            if top_post
            else "No post-level content was available for cross-source ranking."
        ),
    ]
    return lines


def _catalog_recommendations(state: FacebookInstagram10RecipeBuildState) -> list[str]:
    visibility = _facebook_instagram_resolution(state, "visibility")
    engagement = _facebook_instagram_resolution(state, "engagement")
    top_post = state.get("top_post") if isinstance(state.get("top_post"), dict) else None
    unavailable_sources = [
        source
        for source in visibility.sources + engagement.sources
        if source.support_status != AVAILABLE
    ]
    recommendations = [
        "Review visibility by source because Facebook organic impressions and Instagram views are not directly summed.",
        (
            f"Use the creative pattern from \"{top_post.get('title')}\" as the next cross-platform test."
            if top_post
            else "Improve post-level tracking so the next report can rank cross-platform content."
        ),
        (
            "Improve unavailable source metrics before treating platform comparisons as complete: "
            + ", ".join(f"{source.source_label} {source.source_metric}" for source in unavailable_sources[:3])
            + "."
            if unavailable_sources
            else "Keep the same source mix in the next reporting cycle to compare movement period over period."
        ),
    ]
    return recommendations


def _build_multi_source_cover_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_cover_10_block(state, recipe_slide.order)
    payload = _facebook_instagram_block_payload(block)
    integration_metadata = {
        "integration_type": "multi_source",
        "integration_display_name": "Facebook + Instagram",
        "source_name": "Facebook + Instagram",
        "source_handle": None,
        "social_network": "meta",
        "channel": "multi_source",
    }
    payload.update(
        {
            "canonical_semantic": "cover",
            "integration_metadata": integration_metadata,
            "source_count": len(state.get("sources") or []),
            "source_contributions": [
                {
                    "label": source.get("label"),
                    "source_type": source.get("source_type"),
                    "dataset_id": source.get("dataset_id"),
                }
                for source in state.get("sources") or []
            ],
        }
    )
    return _with_facebook_instagram_block_payload(block, payload)


def _build_multi_source_reach_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_reach_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "reach")
    return _apply_metric_resolution_payload(
        block,
        state,
        "reach",
        title="Reach",
        label="Total Reach",
        chart_metric="reach",
        text=(
            "Combined reach uses non-deduped available source reach and totaled "
            f"{_format_source_value(resolution.combined_value)} during {state['period_label']}."
        ),
    )


def _build_multi_source_impressions_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_impressions_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "visibility")
    return _apply_metric_resolution_payload(
        block,
        state,
        "visibility",
        title="Visibility",
        label="Organic Impressions / Views",
        chart_metric="visibility",
        previous_value=None,
        text=_visibility_text(resolution, state["period_label"]),
        extra={
            "legacy_semantic_name": "impressions",
            "visibility_components": _source_contributions(resolution),
            "metric_note": (
                "Facebook organic impressions and Instagram views are source-specific "
                "visibility metrics and are not summed as cross-platform impressions."
            ),
        },
    )


def _build_multi_source_engagement_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_engagement_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "engagement")
    updated = _apply_metric_resolution_payload(
        block,
        state,
        "engagement",
        title="Engagement",
        label="Total Engagement",
        chart_metric="engagement",
        value_alias="engagement",
        text=(
            "Combined engagement from available source interaction metrics totaled "
            f"{_format_source_value(resolution.combined_value)} during {state['period_label']}."
        ),
    )
    payload = _facebook_instagram_block_payload(updated)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    reach_value = report_main._meta_number(_facebook_instagram_resolution(state, "reach").combined_value)
    engagement_value = report_main._meta_number(resolution.combined_value)
    metrics["engagement_rate"] = {
        "value": round((engagement_value / reach_value) * 100, 2)
        if reach_value not in (None, 0) and engagement_value is not None
        else None,
        "label": report_main._multi_source_format_rate(
            (engagement_value / reach_value) * 100
            if reach_value not in (None, 0) and engagement_value is not None
            else None
        ),
    }
    payload["metrics"] = metrics
    return _with_facebook_instagram_block_payload(updated, payload)


def _build_multi_source_page_visits_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_page_visits_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "page_profile_activity")
    return _apply_metric_resolution_payload(
        block,
        state,
        "page_profile_activity",
        title="Page/Profile Activity",
        label="Page/Profile Activity",
        chart_metric="page_profile_activity",
        text=(
            "Page and profile activity totaled "
            f"{_format_source_value(resolution.combined_value)} from available source metrics."
        ),
    )


def _build_multi_source_audience_growth_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_audience_growth_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "audience_size")
    return _apply_metric_resolution_payload(
        block,
        state,
        "audience_size",
        title="Audience Base",
        label="Followers / Audience Size",
        chart_metric="audience_size",
        value_alias="followers",
        text=(
            "This slide reports available follower or audience base values. "
            "It represents growth only when source datasets include valid period-over-period deltas."
        ),
        extra={
            "legacy_semantic_name": "audience_growth",
            "semantic_note": (
                "Recipe semantic audience_growth is currently backed by the catalog "
                "audience_size metric unless real audience delta data is available."
            ),
            "audience_value_type": "base_size",
            "growth_semantic_available": any(source.timeseries for source in resolution.sources),
        },
    )


def _build_multi_source_content_activity_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_content_activity_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "content_activity")
    updated = _apply_metric_resolution_payload(
        block,
        state,
        "content_activity",
        title="Content Activity",
        label="Published Content",
        chart_metric="content_activity",
        value=resolution.combined_value,
        previous_value=None,
        text=(
            f"{_format_source_value(resolution.combined_value)} tracked content pieces were available "
            "across the selected platforms."
            if resolution.combined_value is not None
            else "No post-level content was available, so publishing rhythm could not be evaluated."
        ),
    )
    payload = _facebook_instagram_block_payload(updated)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    content_value = report_main._meta_number(resolution.combined_value)
    reach_value = report_main._meta_number(_facebook_instagram_resolution(state, "reach").combined_value)
    engagement_value = report_main._meta_number(_facebook_instagram_resolution(state, "engagement").combined_value)
    metrics.update(
        {
            "average_reach_per_post": round(reach_value / content_value, 2)
            if content_value not in (None, 0) and reach_value is not None
            else None,
            "average_engagement_per_post": round(engagement_value / content_value, 2)
            if content_value not in (None, 0) and engagement_value is not None
            else None,
        }
    )
    payload["metrics"] = metrics
    payload["media_count_contributions"] = _source_contributions(
        _facebook_instagram_resolution(state, "media_count")
    )
    return _with_facebook_instagram_block_payload(updated, payload)


def _build_multi_source_top_performing_content_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_top_performing_content_10_block(state, recipe_slide.order)
    resolution = _facebook_instagram_resolution(state, "top_content")
    top_posts = _catalog_top_posts(state)
    top_post = top_posts[0] if top_posts else None
    source_contributions = _source_contributions(resolution)
    support_status = _resolution_support_status(resolution)
    payload = _facebook_instagram_block_payload(block)
    payload.update(
        {
            "canonical_semantic": "top_content",
            "canonical_metric": "top_content",
            "canonical_metric_resolution": _canonical_metric_resolution_payload(
                resolution,
                primary_value=None,
                support_status=support_status,
                source_contributions=source_contributions,
            ),
            "metric_family": resolution.metric_family,
            "support_status": support_status,
            "source_contributions": source_contributions,
            "provenance": {
                "canonical_metric": "top_content",
                "metric_family": resolution.metric_family,
                "aggregation_method": resolution.aggregation_method,
                "comparability_group": resolution.comparability_group,
                "combined_value": resolution.combined_value,
                "primary_value": None,
                "ranking_method": "cross_source_meta_post_score",
                "value_is_aggregated": False,
            },
            "timeseries_points": 0,
            "top_posts": top_posts,
            "main_metric": top_post,
            "empty_state": top_post is None,
        }
    )
    return _with_facebook_instagram_block_payload(block, payload)


def _build_multi_source_executive_insights_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_executive_insights_10_block(state, recipe_slide.order)
    payload = _facebook_instagram_block_payload(block)
    insights = _catalog_executive_lines(state)
    source_count = len(state.get("sources") or [])
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    metrics.update(
        {
            "summary_scope": "multi_source" if source_count > 1 else "single_source",
            "source_count": source_count,
            "source_performance": _source_performance_rows(state),
            "catalog_resolutions": {
                key: state["canonical_metric_resolutions"].get(key)
                for key in (
                    "reach",
                    "visibility",
                    "engagement",
                    "page_profile_activity",
                    "audience_size",
                    "content_activity",
                )
            },
        }
    )
    payload.update(
        {
            "canonical_semantic": "executive_insights",
            "text": report_main._multi_source_block_text_lines(insights),
            "insights": insights,
            "metrics": metrics,
            "source_contributions": _source_performance_rows(state),
        }
    )
    return _with_facebook_instagram_block_payload(block, payload)


def _build_multi_source_recommendations_block(
    recipe_slide: ReportRecipeSlide,
    state: FacebookInstagram10RecipeBuildState,
) -> dict[str, Any]:
    block = report_main._multi_source_build_recommendations_10_block(state, recipe_slide.order)
    payload = _facebook_instagram_block_payload(block)
    recommendations = _catalog_recommendations(state)
    payload.update(
        {
            "canonical_semantic": "recommendations",
            "text": report_main._multi_source_block_text_lines(recommendations),
            "recommendations": recommendations,
            "source_contributions": _source_performance_rows(state),
            "catalog_resolutions": {
                key: state["canonical_metric_resolutions"].get(key)
                for key in ("visibility", "engagement", "top_content")
            },
        }
    )
    return _with_facebook_instagram_block_payload(block, payload)


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
