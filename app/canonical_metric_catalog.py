from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


SupportStatus = Literal["available", "unsupported", "missing", "empty", "not_requested"]
AggregationMethod = Literal["sum", "sum_non_deduped", "latest", "source_only", "not_comparable", "derived"]

AVAILABLE: SupportStatus = "available"
UNSUPPORTED: SupportStatus = "unsupported"
MISSING: SupportStatus = "missing"
EMPTY: SupportStatus = "empty"
NOT_REQUESTED: SupportStatus = "not_requested"

SUM: AggregationMethod = "sum"
SUM_NON_DEDUPED: AggregationMethod = "sum_non_deduped"
LATEST: AggregationMethod = "latest"
SOURCE_ONLY: AggregationMethod = "source_only"
NOT_COMPARABLE: AggregationMethod = "not_comparable"
DERIVED: AggregationMethod = "derived"

FACEBOOK_PAGES = "facebook_pages"
INSTAGRAM_BUSINESS = "instagram_business"


@dataclass(frozen=True)
class CanonicalMetricRecord:
    source_type: str
    source_label: str
    source_metric: str
    canonical_metric: str
    metric_family: str
    value: int | float | None
    timeseries: tuple[dict[str, Any], ...] = ()
    unit: str = "count"
    support_status: SupportStatus = MISSING
    aggregation_method: AggregationMethod = NOT_COMPARABLE
    comparability_group: str = "not_comparable"
    dataset_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_label": self.source_label,
            "source_metric": self.source_metric,
            "canonical_metric": self.canonical_metric,
            "metric_family": self.metric_family,
            "value": self.value,
            "timeseries": [dict(point) for point in self.timeseries],
            "unit": self.unit,
            "support_status": self.support_status,
            "aggregation_method": self.aggregation_method,
            "comparability_group": self.comparability_group,
            "dataset_id": self.dataset_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedMetricSource:
    source_type: str
    source_label: str
    source_metric: str
    value: int | float | None
    support_status: SupportStatus
    timeseries: tuple[dict[str, Any], ...]
    dataset_id: int | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_label": self.source_label,
            "source_metric": self.source_metric,
            "value": self.value,
            "support_status": self.support_status,
            "timeseries": [dict(point) for point in self.timeseries],
            "dataset_id": self.dataset_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedMetric:
    canonical_metric: str
    metric_family: str
    sources: tuple[ResolvedMetricSource, ...]
    combined_value: int | float | None
    aggregation_method: AggregationMethod
    comparability_group: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_metric": self.canonical_metric,
            "metric_family": self.metric_family,
            "sources": [source.as_dict() for source in self.sources],
            "combined_value": self.combined_value,
            "aggregation_method": self.aggregation_method,
            "comparability_group": self.comparability_group,
            "metadata": dict(self.metadata),
        }


CANONICAL_AGGREGATION_RULES: dict[str, dict[str, str]] = {
    "reach": {
        "metric_family": "visibility",
        "aggregation_method": SUM_NON_DEDUPED,
        "comparability_group": "cross_platform_reach_non_deduped",
    },
    "visibility": {
        "metric_family": "visibility",
        "aggregation_method": NOT_COMPARABLE,
        "comparability_group": "platform_visibility_not_comparable",
    },
    "organic_visibility": {
        "metric_family": "visibility",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "facebook_organic_impressions",
    },
    "impressions": {
        "metric_family": "visibility",
        "aggregation_method": SUM,
        "comparability_group": "same_source_impressions",
    },
    "views": {
        "metric_family": "visibility",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "instagram_views",
    },
    "engagement": {
        "metric_family": "engagement",
        "aggregation_method": SUM,
        "comparability_group": "platform_interactions_non_deduped",
    },
    "accounts_engaged": {
        "metric_family": "engagement",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "instagram_accounts_engaged",
    },
    "page_profile_activity": {
        "metric_family": "page_profile_activity",
        "aggregation_method": SUM,
        "comparability_group": "platform_profile_actions_non_deduped",
    },
    "website_clicks": {
        "metric_family": "page_profile_activity",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "instagram_website_clicks",
    },
    "audience_size": {
        "metric_family": "audience",
        "aggregation_method": SUM_NON_DEDUPED,
        "comparability_group": "platform_audience_non_deduped",
    },
    "fans": {
        "metric_family": "audience",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "facebook_fans",
    },
    "content_activity": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "platform_content_count",
    },
    "media_count": {
        "metric_family": "content",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "instagram_profile_media_count",
    },
    "top_content": {
        "metric_family": "content",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "ranked_content_source_only",
    },
    "reactions": {
        "metric_family": "engagement",
        "aggregation_method": SOURCE_ONLY,
        "comparability_group": "facebook_reactions",
    },
    "media_reach": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_reach",
    },
    "media_views": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_views",
    },
    "media_likes": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_likes",
    },
    "media_comments": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_comments",
    },
    "media_shares": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_shares",
    },
    "media_saves": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_saves",
    },
    "media_replies": {
        "metric_family": "content",
        "aggregation_method": SUM,
        "comparability_group": "instagram_media_replies",
    },
}


def _number(value: Any) -> int | float | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _first_number(*values: Any) -> int | float | None:
    for value in values:
        normalized = _number(value)
        if normalized is not None:
            return normalized
    return None


def _container_value(container: dict[str, Any], key: str) -> Any:
    value = container.get(key)
    if value is not None:
        return value
    normalized = container.get("normalized_report_metrics")
    if isinstance(normalized, dict):
        return normalized.get(key)
    return None


def _first_container_number(containers: Iterable[dict[str, Any]], *keys: str) -> int | float | None:
    for key in keys:
        for container in containers:
            value = _container_value(container, key)
            normalized = _number(value)
            if normalized is not None:
                return normalized
    return None


def _series_from(containers: Iterable[dict[str, Any]], *keys: str) -> tuple[dict[str, Any], ...]:
    for key in keys:
        for container in containers:
            value = _container_value(container, key)
            if not isinstance(value, list):
                continue
            points: list[dict[str, Any]] = []
            for point in value:
                if not isinstance(point, dict):
                    continue
                point_value = _number(point.get("value"))
                points.append(
                    {
                        "date": point.get("date") or point.get("label"),
                        "label": point.get("label"),
                        "value": point_value,
                    }
                )
            if points:
                return tuple(points)
    return ()


def _source_report_inputs(source: dict[str, Any]) -> dict[str, Any]:
    report_inputs = source.get("report_inputs")
    return report_inputs if isinstance(report_inputs, dict) else {}


def _source_metrics(source: dict[str, Any]) -> dict[str, Any]:
    metrics = source.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _source_timeseries(source: dict[str, Any]) -> dict[str, Any]:
    timeseries = source.get("timeseries")
    return timeseries if isinstance(timeseries, dict) else {}


def _source_unavailable_metrics(source: dict[str, Any], report_inputs: dict[str, Any]) -> dict[str, Any]:
    for container in (report_inputs, source):
        value = container.get("unavailable_metrics") if isinstance(container, dict) else None
        if isinstance(value, dict):
            return value
    normalized = report_inputs.get("normalized_report_metrics")
    if isinstance(normalized, dict) and isinstance(normalized.get("unavailable_metrics"), dict):
        return normalized["unavailable_metrics"]
    return {}


def _source_label(source: dict[str, Any], report_inputs: dict[str, Any]) -> str:
    return str(
        source.get("label")
        or source.get("account_name")
        or report_inputs.get("account_name")
        or report_inputs.get("page_name")
        or source.get("source_type")
        or "Source"
    )


def _dataset_id(source: dict[str, Any]) -> int | None:
    value = _number(source.get("dataset_id"))
    return int(value) if value is not None else None


def _status_from_value(
    *,
    source_metric: str,
    value: int | float | None,
    timeseries: tuple[dict[str, Any], ...],
    unavailable_metrics: dict[str, Any],
    requested: bool = True,
) -> SupportStatus:
    if value is not None or any(_number(point.get("value")) is not None for point in timeseries):
        return AVAILABLE
    reason = str(unavailable_metrics.get(source_metric) or "").strip().lower()
    if reason:
        if "empty" in reason:
            return EMPTY
        if (
            "unsupported" in reason
            or "not valid" in reason
            or "not available" in reason
            or "invalid metric" in reason
            or "must be one of" in reason
        ):
            return UNSUPPORTED
        return MISSING
    return MISSING if requested else NOT_REQUESTED


def _rule(canonical_metric: str) -> dict[str, str]:
    return CANONICAL_AGGREGATION_RULES.get(
        canonical_metric,
        {
            "metric_family": "other",
            "aggregation_method": NOT_COMPARABLE,
            "comparability_group": "not_comparable",
        },
    )


def _record(
    *,
    source_type: str,
    source_label: str,
    source_metric: str,
    canonical_metric: str,
    value: int | float | None,
    timeseries: tuple[dict[str, Any], ...],
    unavailable_metrics: dict[str, Any],
    dataset_id: int | None,
    metadata: dict[str, Any] | None = None,
    requested: bool = True,
    unit: str = "count",
) -> CanonicalMetricRecord:
    rule = _rule(canonical_metric)
    return CanonicalMetricRecord(
        source_type=source_type,
        source_label=source_label,
        source_metric=source_metric,
        canonical_metric=canonical_metric,
        metric_family=rule["metric_family"],
        value=value,
        timeseries=timeseries,
        unit=unit,
        support_status=_status_from_value(
            source_metric=source_metric,
            value=value,
            timeseries=timeseries,
            unavailable_metrics=unavailable_metrics,
            requested=requested,
        ),
        aggregation_method=rule["aggregation_method"],  # type: ignore[arg-type]
        comparability_group=rule["comparability_group"],
        dataset_id=dataset_id,
        metadata=metadata or {},
    )


def _content_count(report_inputs: dict[str, Any], source: dict[str, Any]) -> int | None:
    explicit_count = _first_container_number(
        (report_inputs, _source_metrics(source)),
        "posts_analyzed_count",
    )
    if explicit_count is not None:
        return explicit_count
    content = source.get("content")
    if isinstance(content, list) and content:
        return len(content)
    recent_posts = report_inputs.get("recent_posts")
    if isinstance(recent_posts, list) and recent_posts:
        return len(recent_posts)
    return None


def _content_items(report_inputs: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    content = source.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    recent_posts = report_inputs.get("recent_posts")
    if isinstance(recent_posts, list):
        return [item for item in recent_posts if isinstance(item, dict)]
    return []


def _top_content_items(report_inputs: dict[str, Any]) -> list[dict[str, Any]]:
    top_content = report_inputs.get("top_content")
    if isinstance(top_content, list):
        return [item for item in top_content if isinstance(item, dict)]
    normalized = report_inputs.get("normalized_report_metrics")
    if isinstance(normalized, dict) and isinstance(normalized.get("top_content"), list):
        return [item for item in normalized["top_content"] if isinstance(item, dict)]
    return []


def _content_series(content: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    counts: dict[str, int] = {}
    for item in content:
        date_value = str(item.get("created_time") or item.get("timestamp") or item.get("date") or "").strip()
        if not date_value:
            continue
        day = date_value[:10]
        counts[day] = counts.get(day, 0) + 1
    return tuple({"date": day, "label": day, "value": count} for day, count in sorted(counts.items()))


def _content_metric_series(content: list[dict[str, Any]], *keys: str) -> tuple[dict[str, Any], ...]:
    totals: dict[str, int | float] = {}
    for item in content:
        date_value = str(item.get("created_time") or item.get("timestamp") or item.get("date") or "").strip()
        if not date_value:
            continue
        value = _first_number(*(item.get(key) for key in keys))
        if value is None:
            continue
        day = date_value[:10]
        totals[day] = totals.get(day, 0) + value
    return tuple({"date": day, "label": day, "value": value} for day, value in sorted(totals.items()))


def _sum_content_metric(content: list[dict[str, Any]], *keys: str) -> int | float | None:
    total: int | float = 0
    has_value = False
    for item in content:
        value = _first_number(*(item.get(key) for key in keys))
        if value is None:
            continue
        total += value
        has_value = True
    return total if has_value else None


def _facebook_records(source: dict[str, Any]) -> list[CanonicalMetricRecord]:
    report_inputs = _source_report_inputs(source)
    metrics = _source_metrics(source)
    timeseries = _source_timeseries(source)
    unavailable = _source_unavailable_metrics(source, report_inputs)
    containers = (report_inputs, metrics, timeseries)
    source_type = FACEBOOK_PAGES
    source_label = _source_label(source, report_inputs)
    dataset_id = _dataset_id(source)
    content = _content_items(report_inputs, source)
    top_content = _top_content_items(report_inputs)

    records = [
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="reach",
            canonical_metric="reach",
            value=_first_container_number(containers, "reach", "reach_total"),
            timeseries=_series_from(containers, "reach_daily", "daily_reach", "reach"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="page_posts_impressions_organic",
            canonical_metric="organic_visibility",
            value=_first_container_number(
                containers,
                "organic_impressions_total",
                "organic_impressions",
                "page_posts_impressions_organic",
            ),
            timeseries=_series_from(
                containers,
                "organic_impressions_daily",
                "daily_organic_impressions",
                "page_posts_impressions_organic_daily",
            ),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"display_name": "Facebook organic impressions"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="page_posts_impressions_organic",
            canonical_metric="visibility",
            value=_first_container_number(
                containers,
                "organic_impressions_total",
                "organic_impressions",
                "page_posts_impressions_organic",
            ),
            timeseries=_series_from(
                containers,
                "organic_impressions_daily",
                "daily_organic_impressions",
                "page_posts_impressions_organic_daily",
            ),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"visibility_kind": "organic_impressions"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="impressions",
            canonical_metric="impressions",
            value=_first_container_number(containers, "impressions", "impressions_total"),
            timeseries=_series_from(containers, "impressions_daily", "daily_impressions", "impressions"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="page_post_engagements",
            canonical_metric="engagement",
            value=_first_container_number(containers, "engagement", "engagement_total", "interactions_total"),
            timeseries=_series_from(containers, "daily_engagement", "engagement_daily", "interactions_daily", "engagement"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="page_views_total",
            canonical_metric="page_profile_activity",
            value=_first_container_number(containers, "page_views_total", "page_views", "profile_visits"),
            timeseries=_series_from(containers, "daily_page_views", "page_views_daily", "page_visits"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="followers_count",
            canonical_metric="audience_size",
            value=_first_container_number(containers, "followers", "followers_total", "followers_count"),
            timeseries=_series_from(containers, "followers", "followers_daily", "followers_growth_daily"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"source_value_type": "latest_account_field"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="fan_count",
            canonical_metric="fans",
            value=_first_container_number(containers, "fans", "fans_total", "fan_count"),
            timeseries=_series_from(containers, "fan_count_daily"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"source_value_type": "latest_account_field"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="page_actions_post_reactions_total",
            canonical_metric="reactions",
            value=_first_container_number(containers, "reactions_total", "reactions"),
            timeseries=_series_from(containers, "daily_reactions", "reactions_daily"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="recent_posts",
            canonical_metric="content_activity",
            value=_content_count(report_inputs, source),
            timeseries=_content_series(content),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"content_count": len(content)},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="top_content",
            canonical_metric="top_content",
            value=len(top_content) if top_content else None,
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"top_content": top_content},
        ),
    ]
    return records


def _instagram_profile_activity_value(report_inputs: dict[str, Any], metrics: dict[str, Any]) -> int | float | None:
    value = _first_container_number((report_inputs, metrics), "profile_views")
    if value is not None:
        return value
    unavailable = _source_unavailable_metrics({}, report_inputs)
    if unavailable.get("profile_views"):
        return None
    return _first_container_number((report_inputs, metrics), "profile_visits")


def _instagram_records(source: dict[str, Any]) -> list[CanonicalMetricRecord]:
    report_inputs = _source_report_inputs(source)
    metrics = _source_metrics(source)
    timeseries = _source_timeseries(source)
    unavailable = _source_unavailable_metrics(source, report_inputs)
    containers = (report_inputs, metrics, timeseries)
    source_type = INSTAGRAM_BUSINESS
    source_label = _source_label(source, report_inputs)
    dataset_id = _dataset_id(source)
    content = _content_items(report_inputs, source)
    top_content = _top_content_items(report_inputs)
    account_engagement = _first_container_number(
        containers,
        "total_interactions",
        "engagement",
        "engagement_total",
        "content_interactions",
        "accounts_engaged",
    )
    media_engagement = _sum_content_metric(content, "engagement", "interactions")
    engagement_value = account_engagement if account_engagement is not None else media_engagement
    engagement_source_metric = str(
        report_inputs.get("engagement_source_metric")
        or ("media.engagement" if account_engagement is None and media_engagement is not None else "total_interactions")
    )
    engagement_timeseries = _series_from(
        containers,
        "daily_engagement",
        "interactions_daily",
        "total_interactions",
        "accounts_engaged",
        "engagement",
    )
    if not engagement_timeseries and account_engagement is None:
        engagement_timeseries = _content_metric_series(content, "engagement", "interactions")
    followers_source_metric = "followers_count" if report_inputs.get("followers_count") is not None else "follower_count"
    profile_activity_value = _instagram_profile_activity_value(report_inputs, metrics)

    records = [
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="reach",
            canonical_metric="reach",
            value=_first_container_number(containers, "reach", "reach_total", "viewers_total"),
            timeseries=_series_from(containers, "reach_daily", "daily_reach", "viewers_daily", "reach"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="views",
            canonical_metric="visibility",
            value=_first_container_number(containers, "views", "views_total"),
            timeseries=_series_from(containers, "views_daily", "views"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"visibility_kind": "views"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="views",
            canonical_metric="views",
            value=_first_container_number(containers, "views", "views_total"),
            timeseries=_series_from(containers, "views_daily", "views"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="impressions",
            canonical_metric="impressions",
            value=_first_container_number(containers, "impressions", "impressions_total"),
            timeseries=_series_from(containers, "impressions_daily", "daily_impressions", "impressions"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric=engagement_source_metric,
            canonical_metric="engagement",
            value=engagement_value,
            timeseries=engagement_timeseries,
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={
                "fallback_used": account_engagement is None and media_engagement is not None,
                "fallback_source_metric": "media.engagement"
                if account_engagement is None and media_engagement is not None
                else None,
            },
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="accounts_engaged",
            canonical_metric="accounts_engaged",
            value=_first_container_number(containers, "accounts_engaged", "accounts_engaged_total"),
            timeseries=_series_from(containers, "accounts_engaged"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="profile_views",
            canonical_metric="page_profile_activity",
            value=profile_activity_value,
            timeseries=_series_from(containers, "profile_views_daily", "page_visits_daily"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="website_clicks",
            canonical_metric="website_clicks",
            value=_first_container_number(containers, "website_clicks", "link_clicks", "link_clicks_total"),
            timeseries=_series_from(containers, "website_clicks_daily", "link_clicks_daily"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric=followers_source_metric,
            canonical_metric="audience_size",
            value=_first_container_number(containers, "followers", "followers_count", "followers_total", "follower_count"),
            timeseries=_series_from(containers, "followers_growth_daily", "followers_daily", "follower_count"),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"source_value_type": "latest_account_field"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media_count",
            canonical_metric="media_count",
            value=_first_container_number(containers, "media_count"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"source_value_type": "latest_account_field"},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="recent_posts",
            canonical_metric="content_activity",
            value=_content_count(report_inputs, source),
            timeseries=_content_series(content),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"content_count": len(content)},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="top_content",
            canonical_metric="top_content",
            value=len(top_content) if top_content else None,
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
            metadata={"top_content": top_content},
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.reach",
            canonical_metric="media_reach",
            value=_sum_content_metric(content, "reach"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.views",
            canonical_metric="media_views",
            value=_sum_content_metric(content, "views"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.likes",
            canonical_metric="media_likes",
            value=_sum_content_metric(content, "likes", "reactions"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.comments",
            canonical_metric="media_comments",
            value=_sum_content_metric(content, "comments"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.shares",
            canonical_metric="media_shares",
            value=_sum_content_metric(content, "shares"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.saves",
            canonical_metric="media_saves",
            value=_sum_content_metric(content, "saves"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
        _record(
            source_type=source_type,
            source_label=source_label,
            source_metric="media.replies",
            canonical_metric="media_replies",
            value=_sum_content_metric(content, "replies"),
            timeseries=(),
            unavailable_metrics=unavailable,
            dataset_id=dataset_id,
        ),
    ]
    return records


def build_canonical_metric_catalog_for_source(source: dict[str, Any]) -> tuple[CanonicalMetricRecord, ...]:
    source_type = str(source.get("source_type") or "").strip()
    if source_type in {"facebook_pages", "meta_pages"}:
        return tuple(_facebook_records(source))
    if source_type == INSTAGRAM_BUSINESS:
        return tuple(_instagram_records(source))
    return ()


def build_canonical_metric_catalog_for_sources(
    sources: Iterable[dict[str, Any]],
) -> tuple[CanonicalMetricRecord, ...]:
    records: list[CanonicalMetricRecord] = []
    for source in sources:
        if isinstance(source, dict):
            records.extend(build_canonical_metric_catalog_for_source(source))
    return tuple(records)


def resolve_metric(
    canonical_metric: str,
    catalog: Iterable[CanonicalMetricRecord],
) -> ResolvedMetric:
    records = tuple(record for record in catalog if record.canonical_metric == canonical_metric)
    rule = _rule(canonical_metric)
    method = rule["aggregation_method"]
    available_values = [
        record.value
        for record in records
        if record.support_status == AVAILABLE and _number(record.value) is not None
    ]
    combined_value: int | float | None
    if method in {SUM, SUM_NON_DEDUPED}:
        combined_value = sum(value for value in available_values if value is not None) if available_values else None
    elif method == LATEST:
        combined_value = available_values[-1] if available_values else None
    else:
        combined_value = None
    sources = tuple(
        ResolvedMetricSource(
            source_type=record.source_type,
            source_label=record.source_label,
            source_metric=record.source_metric,
            value=record.value,
            support_status=record.support_status,
            timeseries=record.timeseries,
            dataset_id=record.dataset_id,
            metadata={
                "metric_family": record.metric_family,
                "aggregation_method": record.aggregation_method,
                "comparability_group": record.comparability_group,
                **record.metadata,
            },
        )
        for record in records
    )
    metric_family = records[0].metric_family if records else rule["metric_family"]
    return ResolvedMetric(
        canonical_metric=canonical_metric,
        metric_family=metric_family,
        sources=sources,
        combined_value=combined_value,
        aggregation_method=method,  # type: ignore[arg-type]
        comparability_group=rule["comparability_group"],
        metadata={"available_source_count": len(available_values), "source_count": len(records)},
    )


def resolve_metrics(
    canonical_metrics: Iterable[str],
    catalog: Iterable[CanonicalMetricRecord],
) -> dict[str, ResolvedMetric]:
    catalog_tuple = tuple(catalog)
    return {canonical_metric: resolve_metric(canonical_metric, catalog_tuple) for canonical_metric in canonical_metrics}


def build_source_contribution_table(
    catalog: Iterable[CanonicalMetricRecord],
    *,
    canonical_metrics: Iterable[str] = (
        "reach",
        "visibility",
        "engagement",
        "page_profile_activity",
        "audience_size",
        "content_activity",
        "top_content",
    ),
) -> list[dict[str, Any]]:
    catalog_tuple = tuple(catalog)
    rows: list[dict[str, Any]] = []
    for canonical_metric in canonical_metrics:
        resolution = resolve_metric(canonical_metric, catalog_tuple)
        facebook = [source for source in resolution.sources if source.source_type == FACEBOOK_PAGES]
        instagram = [source for source in resolution.sources if source.source_type == INSTAGRAM_BUSINESS]
        rows.append(
            {
                "canonical_metric": canonical_metric,
                "facebook_raw_metric": ", ".join(source.source_metric for source in facebook) or None,
                "facebook_value": ", ".join(str(source.value) for source in facebook) if facebook else None,
                "facebook_status": ", ".join(source.support_status for source in facebook) if facebook else None,
                "instagram_raw_metric": ", ".join(source.source_metric for source in instagram) or None,
                "instagram_value": ", ".join(str(source.value) for source in instagram) if instagram else None,
                "instagram_status": ", ".join(source.support_status for source in instagram) if instagram else None,
                "combined_value": resolution.combined_value,
                "aggregation_method": resolution.aggregation_method,
            }
        )
    return rows


__all__ = [
    "AVAILABLE",
    "EMPTY",
    "MISSING",
    "NOT_REQUESTED",
    "UNSUPPORTED",
    "AggregationMethod",
    "CANONICAL_AGGREGATION_RULES",
    "CanonicalMetricRecord",
    "ResolvedMetric",
    "ResolvedMetricSource",
    "SupportStatus",
    "build_canonical_metric_catalog_for_source",
    "build_canonical_metric_catalog_for_sources",
    "build_source_contribution_table",
    "resolve_metric",
    "resolve_metrics",
]
