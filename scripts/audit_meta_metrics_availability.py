from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.integrations.meta_ads import fetch_instagram_insights_metric_with_metadata, fetch_page_info
from app.models import Dataset, Integration, IntegrationAccount, MetaPage, WorkspaceMember
from app.services import extract_meta_pages_report_inputs

# Reuse existing backend helpers. This script is intentionally diagnostic-only.
from app.main import (  # noqa: E402
    META_PAGES_IMPRESSIONS_METRIC_CANDIDATES,
    META_PAGES_REACH_METRIC_CANDIDATES,
    META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
    _extract_daily_metric_series_details,
    _fetch_meta_pages_impressions_payload,
    _fetch_meta_pages_metric_payload,
    _fetch_meta_pages_reach_payload,
    _get_meta_access_token,
    _get_meta_page_access_token,
    _is_meta_api_error,
    _is_total_interactions_metric_type_error,
    _meta_api_error_details,
    _normalize_instagram_insight_series,
    normalizeMetricValue,
)


ROOT = Path(__file__).resolve().parent.parent
JSON_OUTPUT_PATH = ROOT / "META_METRICS_AVAILABILITY_AUDIT.json"
MD_OUTPUT_PATH = ROOT / "META_METRICS_AVAILABILITY_AUDIT.md"

INTEGRATION_CHOICES = {"facebook_pages", "instagram_business", "all"}
DAILY_CONTAINERS = (
    "daily",
    "daily_metrics",
    "daily_series",
    "time_series",
    "insights",
    "metric_values",
    "values",
    "data",
    "breakdowns",
    "metrics",
    "normalized_report_metrics",
    "report_metric_mapping",
)
CONSOLE_DIVIDER = "=" * 88
CURRENT_DATE = datetime.now().date().isoformat()


METRIC_SPECS: list[dict[str, Any]] = [
    {"key": "followers", "aliases": ["followers", "followers_count", "follower_count"]},
    {"key": "follower_count", "aliases": ["follower_count", "followers_count", "followers"]},
    {"key": "fans", "aliases": ["fans", "fan_count", "page_fans"]},
    {"key": "page_fans", "aliases": ["page_fans", "fan_count", "fans"]},
    {"key": "reach", "aliases": ["reach", "page_reach", "viewers_total", "viewers"]},
    {"key": "impressions", "aliases": ["impressions", "page_impressions", "impressions_total"]},
    {"key": "engagement", "aliases": ["engagement", "engagements", "daily_engagement"]},
    {"key": "interactions", "aliases": ["interactions", "interactions_total", "total_interactions", "accounts_engaged", "content_interactions"]},
    {"key": "reactions", "aliases": ["reactions"]},
    {"key": "likes", "aliases": ["likes"]},
    {"key": "comments", "aliases": ["comments"]},
    {"key": "shares", "aliases": ["shares"]},
    {"key": "saves", "aliases": ["saves"]},
    {"key": "link_clicks", "aliases": ["link_clicks", "website_clicks", "link_clicks_total"]},
    {"key": "page_views", "aliases": ["page_views", "page_visits", "page_visits_total"]},
    {"key": "profile_views", "aliases": ["profile_views", "profile_visits"]},
    {"key": "website_clicks", "aliases": ["website_clicks"]},
    {"key": "video_views", "aliases": ["video_views", "views", "views_total"]},
    {"key": "post_count", "aliases": ["post_count"]},
    {"key": "daily_reach", "aliases": ["daily_reach", "reach_daily"], "daily_of": "reach"},
    {"key": "daily_impressions", "aliases": ["daily_impressions", "impressions_daily"], "daily_of": "impressions"},
    {"key": "daily_engagement", "aliases": ["daily_engagement", "engagement_daily", "interactions_daily"], "daily_of": "engagement"},
    {"key": "daily_interactions", "aliases": ["daily_interactions", "interactions_daily"], "daily_of": "interactions"},
    {"key": "daily_followers", "aliases": ["daily_followers", "followers_growth_daily"], "daily_of": "followers"},
    {"key": "daily_profile_views", "aliases": ["daily_profile_views", "profile_views_daily", "profile_visits_daily"], "daily_of": "profile_views"},
    {"key": "daily_page_views", "aliases": ["daily_page_views", "page_views_daily", "page_visits_daily"], "daily_of": "page_views"},
]


@dataclass
class Candidate:
    path: str
    value: Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Meta metrics availability in saved datasets and live Meta API.")
    parser.add_argument("--workspace-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--integration", choices=sorted(INTEGRATION_CHOICES), default="all")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--live", type=parse_bool, default=False)
    return parser.parse_args()


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def safe_json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def format_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return str(value)


def infer_integration_type(dataset: Dataset) -> str | None:
    data = dataset.data if isinstance(dataset.data, dict) else {}
    integration_type = str(data.get("integration_type") or "").strip()
    if integration_type in {"facebook_pages", "instagram_business"}:
        return integration_type
    name = str(dataset.name or "").strip().lower()
    description = str(dataset.description or "").strip().lower()
    if name.startswith("meta_instagram_") or "instagram" in description:
        return "instagram_business"
    if name.startswith("meta_page_") or "meta pages" in description:
        return "facebook_pages"
    return None


def infer_record_id(dataset: Dataset, integration_type: str | None) -> str | None:
    data = dataset.data if isinstance(dataset.data, dict) else {}
    direct_candidates = (
        "page_id",
        "account_id",
        "instagram_account_id",
        "facebook_page_id",
    )
    for key in direct_candidates:
        raw = str(data.get(key) or "").strip()
        if raw:
            return raw
    pattern = r"meta_instagram_(?P<id>[^_]+)_insights\.csv" if integration_type == "instagram_business" else r"meta_page_(?P<id>[^_]+)_insights\.csv"
    match = re.match(pattern, str(dataset.name or "").strip())
    if match:
        return str(match.group("id"))
    return None


def resolve_workspace_scope(
    db: Session,
    *,
    workspace_id: int | None,
    user_id: int | None,
    integration_filter: str,
) -> tuple[int | None, dict[str, Any]]:
    scope_note: dict[str, Any] = {
        "requested_workspace_id": workspace_id,
        "requested_user_id": user_id,
        "selection_mode": None,
        "resolved_workspace_id": workspace_id,
    }
    if workspace_id is not None:
        scope_note["selection_mode"] = "explicit_workspace"
        return workspace_id, scope_note
    if user_id is not None:
        scope_note["selection_mode"] = "all_user_workspaces"
        return None, scope_note

    datasets = db.query(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()).all()
    for dataset in datasets:
        dataset_integration = infer_integration_type(dataset)
        if dataset_integration is None:
            continue
        if integration_filter != "all" and dataset_integration != integration_filter:
            continue
        scope_note["selection_mode"] = "latest_workspace_from_matching_dataset"
        scope_note["resolved_workspace_id"] = dataset.workspace_id
        scope_note["dataset_id_used_for_scope"] = dataset.id
        return dataset.workspace_id, scope_note

    scope_note["selection_mode"] = "no_matching_dataset_found"
    return None, scope_note


def query_datasets(
    db: Session,
    *,
    workspace_id: int | None,
    user_id: int | None,
    integration_filter: str,
    limit: int,
) -> tuple[list[Dataset], dict[str, Any]]:
    resolved_workspace_id, scope_note = resolve_workspace_scope(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        integration_filter=integration_filter,
    )
    user_workspace_ids: set[int] | None = None
    if user_id is not None:
        user_workspace_ids = {
            int(row.workspace_id)
            for row in db.query(WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id == user_id)
            .all()
        }
    datasets = db.query(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()).all()
    selected: list[Dataset] = []
    for dataset in datasets:
        dataset_integration = infer_integration_type(dataset)
        if dataset_integration is None:
            continue
        if integration_filter != "all" and dataset_integration != integration_filter:
            continue
        if resolved_workspace_id is not None and int(dataset.workspace_id) != int(resolved_workspace_id):
            continue
        if user_workspace_ids is not None and int(dataset.workspace_id) not in user_workspace_ids:
            continue
        selected.append(dataset)
        if len(selected) >= limit:
            break
    scope_note["matched_dataset_count"] = len(selected)
    return selected, scope_note


def workspace_user_ids(db: Session, workspace_id: int) -> list[int]:
    return [
        int(row.user_id)
        for row in db.query(WorkspaceMember.user_id)
        .filter(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.user_id.asc())
        .all()
    ]


def find_exact_key_candidates(value: Any, target_keys: set[str], path: str, results: list[Candidate]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            next_path = f"{path}.{key}"
            if str(key) in target_keys:
                results.append(Candidate(path=next_path, value=nested))
            find_exact_key_candidates(nested, target_keys, next_path, results)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            find_exact_key_candidates(item, target_keys, f"{path}[{index}]", results)


def build_context(data: dict[str, Any]) -> dict[str, Any]:
    report_inputs = extract_meta_pages_report_inputs(dict(data))
    context = dict(data)
    context["report_inputs"] = report_inputs
    return context


def series_points(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    points: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        date_value = item.get("date") or item.get("end_time") or item.get("label")
        if date_value is None:
            continue
        normalized_value = normalizeMetricValue(item.get("value"))
        points.append(
            {
                "date": str(date_value)[:10],
                "value": normalized_value,
            }
        )
    return [point for point in points if point.get("date")]


def normalize_series(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for point in points:
        point_date = str(point.get("date") or "").strip()[:10]
        if not point_date:
            continue
        value = normalizeMetricValue(point.get("value"))
        if point_date not in normalized:
            normalized[point_date] = {"date": point_date, "value": value}
            continue
        existing = normalizeMetricValue(normalized[point_date].get("value"))
        if existing is None:
            normalized[point_date]["value"] = value
        elif value is not None:
            normalized[point_date]["value"] = normalizeMetricValue(existing + value)
    return [normalized[key] for key in sorted(normalized.keys())]


def extract_series_from_candidate(value: Any) -> list[dict[str, Any]]:
    direct = normalize_series(series_points(value))
    if direct:
        return direct
    if isinstance(value, dict):
        for key in ("points", "daily_series", "daily", "daily_metrics", "time_series", "metric_values", "values", "data", "breakdowns", "series"):
            nested = normalize_series(series_points(value.get(key)))
            if nested:
                return nested
    return []


def numeric_candidate_from_value(path: str, value: Any) -> list[Candidate]:
    candidates: list[Candidate] = []
    normalized = normalizeMetricValue(value)
    if normalized is not None:
        candidates.append(Candidate(path=path, value=normalized))
    if isinstance(value, dict):
        for suffix in ("total", "value"):
            normalized_nested = normalizeMetricValue(value.get(suffix))
            if normalized_nested is not None:
                candidates.append(Candidate(path=f"{path}.{suffix}", value=normalized_nested))
        summary = value.get("summary")
        if isinstance(summary, dict):
            total_count = normalizeMetricValue(summary.get("total_count"))
            if total_count is not None:
                candidates.append(Candidate(path=f"{path}.summary.total_count", value=total_count))
    return candidates


def source_origin(source_path: str | None) -> str:
    if not source_path:
        return "unknown"
    if source_path == "len(dataset.data.recent_posts)":
        return "calculated_by_backend"
    if ".normalized_report_metrics." in source_path or ".report_metric_mapping." in source_path:
        return "normalized_or_calculated_by_backend"
    if ".instagram_metric_audit." in source_path:
        return "meta_api_audit_metadata"
    if source_path.startswith("dataset.data."):
        return "direct_saved_dataset_field"
    if source_path.startswith("report_inputs."):
        return "derived_report_input"
    return "unknown"


def metric_base_key(metric_key: str) -> str:
    return metric_key.removeprefix("daily_")


def metric_spec(metric_key: str) -> dict[str, Any]:
    for spec in METRIC_SPECS:
        if spec["key"] == metric_key:
            return spec
    return {"key": metric_key, "aliases": [metric_key]}


def explicit_metric_candidates(context: dict[str, Any], spec: dict[str, Any]) -> tuple[list[Candidate], list[Candidate]]:
    aliases = list(dict.fromkeys(spec.get("aliases") or [spec["key"]]))
    data = {k: v for k, v in context.items() if k != "report_inputs"}
    report_inputs = context.get("report_inputs") if isinstance(context.get("report_inputs"), dict) else {}
    numeric: list[Candidate] = []
    series: list[Candidate] = []
    for source_name, source in (("dataset.data", data), ("report_inputs", report_inputs)):
        if not isinstance(source, dict):
            continue
        for alias in aliases:
            candidate_paths = [
                (f"{source_name}.{alias}", source.get(alias)),
                (f"{source_name}.{alias}_total", source.get(f"{alias}_total")),
                (f"{source_name}.total_{alias}", source.get(f"total_{alias}")),
                (f"{source_name}.{alias}_daily", source.get(f"{alias}_daily")),
                (f"{source_name}.daily_{alias}", source.get(f"daily_{alias}")),
                (f"{source_name}.{alias}_daily_series", source.get(f"{alias}_daily_series")),
                (f"{source_name}.daily_series_{alias}", source.get(f"daily_series_{alias}")),
                (f"{source_name}.{alias}_series", source.get(f"{alias}_series")),
            ]
            for container_key in DAILY_CONTAINERS:
                container = source.get(container_key)
                if not isinstance(container, dict):
                    continue
                candidate_paths.extend(
                    [
                        (f"{source_name}.{container_key}.{alias}", container.get(alias)),
                        (f"{source_name}.{container_key}.{alias}_total", container.get(f"{alias}_total")),
                        (f"{source_name}.{container_key}.{alias}_daily", container.get(f"{alias}_daily")),
                        (f"{source_name}.{container_key}.daily_{alias}", container.get(f"daily_{alias}")),
                        (f"{source_name}.{container_key}.{alias}_daily_series", container.get(f"{alias}_daily_series")),
                    ]
                )
            for path, value in candidate_paths:
                numeric.extend(numeric_candidate_from_value(path, value))
                extracted_series = extract_series_from_candidate(value)
                if extracted_series:
                    series.append(Candidate(path=path, value=extracted_series))
        recursive_matches: list[Candidate] = []
        find_exact_key_candidates(source, set(aliases), source_name, recursive_matches)
        for match in recursive_matches:
            numeric.extend(numeric_candidate_from_value(match.path, match.value))
            extracted_series = extract_series_from_candidate(match.value)
            if extracted_series:
                series.append(Candidate(path=match.path, value=extracted_series))
    return dedupe_candidates(numeric), dedupe_series_candidates(series)


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.path, str(candidate.value))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def dedupe_series_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Candidate] = []
    for candidate in candidates:
        signature = safe_json_dump(candidate.value)
        key = (candidate.path, signature)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def pick_numeric_candidate(metric_key: str, candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None

    def score(candidate: Candidate) -> tuple[int, int]:
        path = candidate.path
        if metric_key == "followers" and path.endswith(".followers"):
            return (0, len(path))
        if path.startswith("dataset.data.") and ".normalized_report_metrics." not in path and ".report_metric_mapping." not in path:
            return (0, len(path))
        if ".normalized_report_metrics." in path:
            return (1, len(path))
        if ".report_metric_mapping." in path:
            return (2, len(path))
        if path.startswith("report_inputs."):
            return (3, len(path))
        return (4, len(path))

    return sorted(candidates, key=score)[0]


def pick_series_candidate(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None

    def score(candidate: Candidate) -> tuple[int, int]:
        path = candidate.path
        if path.startswith("dataset.data.") and ".normalized_report_metrics." not in path and ".report_metric_mapping." not in path:
            return (0, len(path))
        if ".normalized_report_metrics." in path:
            return (1, len(path))
        if ".report_metric_mapping." in path:
            return (2, len(path))
        if path.startswith("report_inputs."):
            return (3, len(path))
        return (4, len(path))

    return sorted(candidates, key=score)[0]


def extractor_series_candidate(context: dict[str, Any], metric_key: str) -> Candidate | None:
    base_key = metric_base_key(metric_key)
    if base_key not in {"reach", "impressions", "engagement"}:
        return None
    points, source_path, matched_key = _extract_daily_metric_series_details(context, base_key)
    if not points:
        return None
    return Candidate(
        path=source_path or f"extractDailyMetricSeries({base_key})",
        value=normalize_series(points),
    )


def aggregate_recent_posts_metric(data: dict[str, Any], metric_key: str) -> Candidate | None:
    posts = data.get("recent_posts")
    if not isinstance(posts, list):
        return None
    if metric_key == "post_count":
        return Candidate(path="len(dataset.data.recent_posts)", value=len([post for post in posts if isinstance(post, dict)]))
    if metric_key not in {"reactions", "likes", "comments", "shares", "saves"}:
        return None
    total = 0
    found = False
    for post in posts:
        if not isinstance(post, dict):
            continue
        value = normalizeMetricValue(post.get(metric_key))
        if value is None and metric_key == "likes":
            value = normalizeMetricValue(post.get("reactions"))
        if value is None:
            continue
        total += int(value)
        found = True
    if not found:
        return None
    return Candidate(path=f"sum(dataset.data.recent_posts[*].{metric_key})", value=total)


def audit_metric(context: dict[str, Any], metric_key: str) -> dict[str, Any]:
    data = {k: v for k, v in context.items() if k != "report_inputs"}
    spec = metric_spec(metric_key)
    numeric_candidates, explicit_series_candidates = explicit_metric_candidates(context, spec)
    extractor_candidate = extractor_series_candidate(context, metric_key)
    if extractor_candidate is not None:
        explicit_series_candidates = dedupe_series_candidates([extractor_candidate, *explicit_series_candidates])
    aggregate_candidate = aggregate_recent_posts_metric(data, metric_key)
    if aggregate_candidate is not None:
        numeric_candidates = dedupe_candidates([aggregate_candidate, *numeric_candidates])

    value_candidate = pick_numeric_candidate(metric_key, numeric_candidates)
    series_candidate = pick_series_candidate(explicit_series_candidates)
    series_points_value = series_candidate.value if series_candidate else []
    first_date = series_points_value[0]["date"] if series_points_value else None
    last_date = series_points_value[-1]["date"] if series_points_value else None

    if metric_key.startswith("daily_"):
        return {
            "metric_key": metric_key,
            "value": None,
            "formatted_value": None,
            "value_type": "daily_series",
            "available": bool(series_points_value),
            "source_path": None,
            "daily_series_available": bool(series_points_value),
            "daily_series_source_path": series_candidate.path if series_candidate else None,
            "daily_series_length": len(series_points_value),
            "daily_series_first_date": first_date,
            "daily_series_last_date": last_date,
            "metric_origin": source_origin(series_candidate.path if series_candidate else None),
            "reason": None if series_points_value else "not_found_in_dataset",
            "matched_candidates": [candidate.path for candidate in explicit_series_candidates[:10]],
        }

    if value_candidate is None and series_points_value:
        derived_total = sum(
            int(point["value"])
            for point in series_points_value
            if isinstance(point.get("value"), (int, float))
        )
        value_candidate = Candidate(
            path=f"sum({series_candidate.path})" if series_candidate else "sum(daily_series)",
            value=derived_total,
        )

    return {
        "metric_key": metric_key,
        "value": value_candidate.value if value_candidate is not None else None,
        "formatted_value": format_value(value_candidate.value) if value_candidate is not None else None,
        "value_type": type(value_candidate.value).__name__ if value_candidate is not None else None,
        "available": value_candidate is not None or bool(series_points_value),
        "source_path": value_candidate.path if value_candidate is not None else None,
        "daily_series_available": bool(series_points_value),
        "daily_series_source_path": series_candidate.path if series_candidate else None,
        "daily_series_length": len(series_points_value),
        "daily_series_first_date": first_date,
        "daily_series_last_date": last_date,
        "metric_origin": source_origin(value_candidate.path if value_candidate is not None else (series_candidate.path if series_candidate else None)),
        "reason": None if (value_candidate is not None or series_points_value) else "not_found_in_dataset",
        "matched_candidates": [candidate.path for candidate in numeric_candidates[:10]],
    }


def dataset_primary_keys(data: dict[str, Any]) -> list[str]:
    return sorted(str(key) for key in data.keys())


def dataset_timeframe(data: dict[str, Any]) -> dict[str, Any] | None:
    timeframe = data.get("timeframe")
    if isinstance(timeframe, dict):
        return json_ready(timeframe)
    since = str(data.get("timeframe_since") or "").strip() or None
    until = str(data.get("timeframe_until") or "").strip() or None
    if since or until:
        return {"since": since, "until": until}
    return None


def series_signature(points: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(str(point.get("date")), point.get("value")) for point in points]


def compare_total_vs_series(metric_entry: dict[str, Any]) -> str | None:
    if not metric_entry.get("daily_series_available"):
        return None
    value = metric_entry.get("value")
    if not isinstance(value, (int, float)):
        return None
    matched_candidates = metric_entry.get("matched_candidates") or []
    if any(".normalized_report_metrics." in str(path) for path in matched_candidates):
        # normalized totals are often explicit rollups, still worth checking but not auto-warning here.
        pass
    return None


def metric_series_for_warning(context: dict[str, Any], metric_key: str) -> list[dict[str, Any]]:
    entry = audit_metric(context, metric_key)
    if not entry.get("daily_series_available"):
        return []
    candidate = extractor_series_candidate(context, metric_key)
    if candidate is not None:
        return candidate.value
    spec = metric_spec(metric_key)
    _, explicit_series_candidates = explicit_metric_candidates(context, spec)
    chosen = pick_series_candidate(explicit_series_candidates)
    return chosen.value if chosen is not None else []


def dataset_warnings(context: dict[str, Any], integration_type: str) -> list[str]:
    warnings: list[str] = []
    reach_entry = audit_metric(context, "reach")
    impressions_entry = audit_metric(context, "impressions")
    engagement_entry = audit_metric(context, "engagement")
    reach_series = metric_series_for_warning(context, "reach")
    impressions_series = metric_series_for_warning(context, "impressions")
    if reach_series and impressions_series and series_signature(reach_series) == series_signature(impressions_series):
        warnings.append(
            "WARNING: impressions appears to reuse reach daily series. "
            f"source_path reach = {reach_entry.get('daily_series_source_path')}; "
            f"source_path impressions = {impressions_entry.get('daily_series_source_path')}"
        )
    if reach_entry.get("source_path") and reach_entry.get("source_path") == impressions_entry.get("source_path"):
        warnings.append(
            "WARNING: reach and impressions use the same source_path. "
            f"source_path = {reach_entry.get('source_path')}"
        )
    if str(context.get("impressions_source_metric") or "").strip() == "reach":
        warnings.append("WARNING: impressions_source_metric is using reach as fallback.")
    if str(context.get("engagement_source_metric") or "").strip() in {"reach", "impressions"}:
        warnings.append(
            f"WARNING: engagement_source_metric is using {context.get('engagement_source_metric')} as fallback."
        )
    for metric_key, entry in (("reach", reach_entry), ("impressions", impressions_entry), ("engagement", engagement_entry)):
        if not entry.get("daily_series_available"):
            continue
        series = metric_series_for_warning(context, metric_key)
        series_sum = sum(
            int(point["value"])
            for point in series
            if isinstance(point.get("value"), (int, float))
        )
        total_value = entry.get("value")
        if isinstance(total_value, (int, float)) and series_sum > 0 and int(total_value) != int(series_sum):
            warnings.append(
                "WARNING: total does not match sum(daily_series). "
                f"metric = {metric_key}; total = {total_value}; sum(daily_series) = {series_sum}; "
                f"source_path = {entry.get('source_path')}; daily_source_path = {entry.get('daily_series_source_path')}"
            )
    if integration_type == "facebook_pages" and audit_metric(context, "daily_engagement").get("daily_series_available") and not engagement_entry.get("daily_series_available"):
        warnings.append("WARNING: daily engagement series exists but the engagement metric resolver did not select it directly.")
    for metric_key in ("reach", "impressions", "engagement", "page_views", "profile_views", "link_clicks"):
        entry = audit_metric(context, metric_key)
        if entry.get("value") == 0 and entry.get("reason") == "not_found_in_dataset":
            warnings.append(f"WARNING: {metric_key} may be converting null to 0.")
    for metric_key in ("reach", "impressions", "engagement", "page_views", "profile_views", "link_clicks"):
        entry = audit_metric(context, metric_key)
        if entry.get("value") is None and entry.get("daily_series_available"):
            warnings.append(f"WARNING: {metric_key} has daily_series but total is null; check null vs 0 handling.")
    return warnings


def find_meta_record(
    db: Session,
    *,
    workspace_id: int,
    integration_type: str,
    record_id: str | None,
) -> tuple[Integration | None, MetaPage | None, dict[str, Any]]:
    record_type = META_RECORD_TYPE_INSTAGRAM_ACCOUNT if integration_type == "instagram_business" else "facebook_page"
    diagnostics: dict[str, Any] = {
        "lookup_workspace_id": workspace_id,
        "record_id": record_id,
        "record_type": record_type,
    }
    rows = (
        db.query(MetaPage, Integration)
        .join(Integration, MetaPage.integration_id == Integration.id)
        .filter(
            Integration.provider == "meta",
            MetaPage.record_type == record_type,
        )
        .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc(), Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )
    diagnostics["candidate_count"] = len(rows)
    exact_record = None
    workspace_record = None
    for meta_page, integration in rows:
        if record_id and str(meta_page.page_id) == str(record_id):
            exact_record = (integration, meta_page)
            diagnostics["exact_match_workspace_id"] = integration.workspace_id
            if int(integration.workspace_id) == int(workspace_id):
                workspace_record = (integration, meta_page)
                break
    if workspace_record is not None:
        diagnostics["selection_mode"] = "exact_record_same_workspace"
        return workspace_record[0], workspace_record[1], diagnostics
    if exact_record is not None:
        diagnostics["selection_mode"] = "exact_record_cross_workspace"
        return exact_record[0], exact_record[1], diagnostics
    diagnostics["selection_mode"] = "record_not_found"
    return None, None, diagnostics


def find_selected_page_account(db: Session, integration_id: int, page_id: str | None) -> IntegrationAccount | None:
    if not page_id:
        return None
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id.like(f"%{page_id}%"),
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )


def resolve_live_token(
    db: Session,
    *,
    integration: Integration,
    record: MetaPage,
) -> tuple[str | None, str]:
    selected_page = find_selected_page_account(db, integration.id, record.parent_page_id or record.page_id)
    if selected_page is not None:
        try:
            return _get_meta_page_access_token(db, integration, selected_page), "selected_page_access_token"
        except Exception:
            pass
    if record.page_access_token:
        return record.page_access_token, "meta_page_cache_token"
    try:
        return _get_meta_access_token(db, integration), "integration_access_token"
    except Exception:
        return None, "missing_token"


def live_facebook_audit(
    db: Session,
    *,
    integration: Integration,
    record: MetaPage,
    timeframe: dict[str, Any],
) -> dict[str, Any]:
    token, token_source = resolve_live_token(db, integration=integration, record=record)
    result: dict[str, Any] = {
        "status": "not_attempted",
        "token_source": token_source,
        "metrics": {},
        "errors": [],
    }
    if not token:
        result["status"] = "missing_token"
        return result
    since = str(timeframe.get("since") or "")[:10]
    until = str(timeframe.get("until") or CURRENT_DATE)[:10]
    timeframe_config = {
        "key": str(timeframe.get("key") or "custom"),
        "label": str(timeframe.get("label") or "Audit"),
        "preset": str(timeframe.get("preset") or "custom"),
        "since": since,
        "until": until,
        "requested_since": since,
        "requested_until": until,
    }
    try:
        reach_payload = _fetch_meta_pages_reach_payload(token, record.page_id, timeframe_config, integration.id)
        impressions_payload = _fetch_meta_pages_impressions_payload(token, record.page_id, timeframe_config, integration.id)
        views_payload = _fetch_meta_pages_metric_payload(
            token, record.page_id, timeframe_config, integration.id, metric_name="page_views_total", label="Visualizaciones", daily_key="views_daily"
        )
        interactions_payload = _fetch_meta_pages_metric_payload(
            token, record.page_id, timeframe_config, integration.id, metric_name="page_post_engagements", label="Interacciones", daily_key="interactions_daily"
        )
        link_clicks_payload = _fetch_meta_pages_metric_payload(
            token, record.page_id, timeframe_config, integration.id, metric_name="page_consumptions", label="Clics", daily_key="link_clicks_daily"
        )
        page_visits_payload = _fetch_meta_pages_metric_payload(
            token, record.page_id, timeframe_config, integration.id, metric_name="page_profile_views", label="Visitas", daily_key="page_visits_daily"
        )
        followers_growth_payload = _fetch_meta_pages_metric_payload(
            token, record.page_id, timeframe_config, integration.id, metric_name="page_fan_adds", label="Seguidores", daily_key="followers_growth_daily"
        )
        page_counts = fetch_page_info(token, record.page_id, fields="fan_count,followers_count")
        result["status"] = "ok"
        result["metrics"] = {
            "followers_count": {"value": normalizeMetricValue(page_counts.get("followers_count")), "source_metric": "followers_count"},
            "fan_count": {"value": normalizeMetricValue(page_counts.get("fan_count")), "source_metric": "fan_count"},
            "reach": {"value": normalizeMetricValue(reach_payload.get("value")), "source_metric": reach_payload.get("metric_name"), "daily_points": len(reach_payload.get("reach_daily") or [])},
            "impressions": {"value": normalizeMetricValue(impressions_payload.get("value")), "source_metric": impressions_payload.get("metric_name"), "daily_points": len(impressions_payload.get("impressions_daily") or [])},
            "page_views_total": {"value": normalizeMetricValue(views_payload.get("value")), "source_metric": views_payload.get("metric_name"), "daily_points": len(views_payload.get("views_daily") or [])},
            "page_post_engagements": {"value": normalizeMetricValue(interactions_payload.get("value")), "source_metric": interactions_payload.get("metric_name"), "daily_points": len(interactions_payload.get("interactions_daily") or [])},
            "page_consumptions": {"value": normalizeMetricValue(link_clicks_payload.get("value")), "source_metric": link_clicks_payload.get("metric_name"), "daily_points": len(link_clicks_payload.get("link_clicks_daily") or [])},
            "page_profile_views": {"value": normalizeMetricValue(page_visits_payload.get("value")), "source_metric": page_visits_payload.get("metric_name"), "daily_points": len(page_visits_payload.get("page_visits_daily") or [])},
            "page_fan_adds": {"value": normalizeMetricValue(followers_growth_payload.get("value")), "source_metric": followers_growth_payload.get("metric_name"), "daily_points": len(followers_growth_payload.get("followers_growth_daily") or [])},
        }
        result["meta_candidates"] = {
            "reach_candidates": META_PAGES_REACH_METRIC_CANDIDATES,
            "impressions_candidates": META_PAGES_IMPRESSIONS_METRIC_CANDIDATES,
        }
    except Exception as exc:
        result["status"] = "error"
        if hasattr(exc, "detail") and _is_meta_api_error(exc):
            details = _meta_api_error_details(exc)
            result["errors"].append(
                {
                    "metric_key": "facebook_pages_live_request",
                    "status": "error",
                    "error_code": details.get("meta_error_code"),
                    "error_message": details.get("error_message"),
                    "permission_issue": "permission" in str(details.get("error_message") or "").lower(),
                }
            )
        else:
            result["errors"].append(
                {
                    "metric_key": "facebook_pages_live_request",
                    "status": "error",
                    "error_code": None,
                    "error_message": str(exc),
                    "permission_issue": False,
                }
            )
    return result


def live_instagram_audit(
    db: Session,
    *,
    integration: Integration,
    record: MetaPage,
    timeframe: dict[str, Any],
) -> dict[str, Any]:
    token, token_source = resolve_live_token(db, integration=integration, record=record)
    result: dict[str, Any] = {
        "status": "not_attempted",
        "token_source": token_source,
        "metrics": {},
        "errors": [],
    }
    if not token:
        result["status"] = "missing_token"
        return result
    since = str(timeframe.get("since") or "")[:10]
    until = str(timeframe.get("until") or CURRENT_DATE)[:10]
    requested_metrics = [
        "reach",
        "impressions",
        "views",
        "total_interactions",
        "accounts_engaged",
        "content_interactions",
        "profile_views",
        "website_clicks",
    ]
    result["status"] = "ok"
    try:
        profile_payload = fetch_page_info(token, record.page_id, fields="id,username,name,profile_picture_url,followers_count")
        result["profile"] = {
            "followers_count": normalizeMetricValue(profile_payload.get("followers_count")),
            "username": profile_payload.get("username"),
            "name": profile_payload.get("name"),
        }
    except Exception as exc:
        result["status"] = "partial_error"
        result["errors"].append(
            {
                "metric_key": "followers_count",
                "status": "error",
                "error_code": None,
                "error_message": str(exc),
                "permission_issue": "permission" in str(exc).lower(),
            }
        )

    for metric_name in requested_metrics:
        metric_type: str | None = None
        fallback_used = False
        try:
            payload = fetch_instagram_insights_metric_with_metadata(
                token,
                record.page_id,
                metric_name=metric_name,
                since=since,
                until=until,
                metric_type=metric_type,
            )
        except Exception as exc:
            if hasattr(exc, "detail") and metric_name == "total_interactions" and _is_total_interactions_metric_type_error(exc):
                metric_type = "total_value"
                fallback_used = True
                try:
                    payload = fetch_instagram_insights_metric_with_metadata(
                        token,
                        record.page_id,
                        metric_name=metric_name,
                        since=since,
                        until=until,
                        metric_type=metric_type,
                    )
                except Exception as retry_exc:
                    exc = retry_exc
                    payload = None
            else:
                payload = None
            if payload is None:
                if hasattr(exc, "detail") and _is_meta_api_error(exc):
                    details = _meta_api_error_details(exc)
                    result["errors"].append(
                        {
                            "metric_key": metric_name,
                            "status": "error",
                            "error_code": details.get("meta_error_code"),
                            "error_message": details.get("error_message"),
                            "permission_issue": "permission" in str(details.get("error_message") or "").lower(),
                            "metric_type": metric_type,
                        }
                    )
                else:
                    result["errors"].append(
                        {
                            "metric_key": metric_name,
                            "status": "error",
                            "error_code": None,
                            "error_message": str(exc),
                            "permission_issue": "permission" in str(exc).lower(),
                            "metric_type": metric_type,
                        }
                    )
                continue
        insight_rows = payload.get("data")
        metric_row = insight_rows[0] if isinstance(insight_rows, list) and insight_rows else {}
        values = metric_row.get("values") if isinstance(metric_row, dict) else []
        total_value, latest_value, end_time, normalized_series, raw_values = _normalize_instagram_insight_series(values if isinstance(values, list) else [])
        result["metrics"][metric_name] = {
            "value": total_value,
            "latest_value": latest_value,
            "end_time": end_time,
            "daily_points": len(normalized_series),
            "metric_type": metric_type,
            "fallback_used": fallback_used,
            "raw_values_sample": raw_values[:3],
        }
    return result


def live_audit_for_dataset(
    db: Session,
    *,
    dataset: Dataset,
    integration_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    record_id = infer_record_id(dataset, integration_type)
    integration, record, record_lookup = find_meta_record(
        db,
        workspace_id=dataset.workspace_id,
        integration_type=integration_type,
        record_id=record_id,
    )
    if integration is None or record is None:
        return {
            "status": "record_not_found",
            "record_id": record_id,
            "integration_id": None,
            "record_lookup": record_lookup,
            "metrics": {},
            "errors": [],
        }
    timeframe = dataset_timeframe(data) or {"since": CURRENT_DATE, "until": CURRENT_DATE}
    if integration_type == "facebook_pages":
        live_result = live_facebook_audit(db, integration=integration, record=record, timeframe=timeframe)
    else:
        live_result = live_instagram_audit(db, integration=integration, record=record, timeframe=timeframe)
    live_result["integration_id"] = integration.id
    live_result["record_id"] = record.page_id
    live_result["record_lookup"] = record_lookup
    if int(integration.workspace_id) != int(dataset.workspace_id):
        live_result["workspace_mismatch"] = {
            "dataset_workspace_id": dataset.workspace_id,
            "integration_workspace_id": integration.workspace_id,
        }
    return live_result


def compare_live_vs_dataset(dataset_metrics: dict[str, dict[str, Any]], live_result: dict[str, Any], integration_type: str) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    live_metrics = live_result.get("metrics") if isinstance(live_result.get("metrics"), dict) else {}
    mapping = {
        "facebook_pages": {
            "followers": ["followers_count", "fan_count"],
            "reach": ["reach"],
            "impressions": ["impressions"],
            "engagement": ["page_post_engagements"],
            "link_clicks": ["page_consumptions"],
            "page_views": ["page_views_total", "page_profile_views"],
        },
        "instagram_business": {
            "followers": ["followers_count"],
            "reach": ["reach"],
            "impressions": ["impressions"],
            "engagement": ["total_interactions", "accounts_engaged", "content_interactions"],
            "profile_views": ["profile_views"],
            "website_clicks": ["website_clicks"],
            "video_views": ["views"],
        },
    }
    for dataset_metric, live_keys in mapping.get(integration_type, {}).items():
        dataset_entry = dataset_metrics.get(dataset_metric) or {}
        live_entry = None
        live_key_used = None
        for live_key in live_keys:
            candidate = live_metrics.get(live_key)
            if isinstance(candidate, dict) and candidate.get("value") is not None:
                live_entry = candidate
                live_key_used = live_key
                break
        comparisons.append(
            {
                "dataset_metric": dataset_metric,
                "dataset_value": dataset_entry.get("value"),
                "dataset_source_path": dataset_entry.get("source_path"),
                "live_metric_key": live_key_used,
                "live_value": live_entry.get("value") if isinstance(live_entry, dict) else None,
                "live_daily_points": live_entry.get("daily_points") if isinstance(live_entry, dict) else None,
            }
        )
    return comparisons


def audit_dataset(db: Session, dataset: Dataset, live: bool) -> dict[str, Any]:
    data = dataset.data if isinstance(dataset.data, dict) else {}
    integration_type = infer_integration_type(dataset) or "unknown"
    context = build_context(data)
    users_in_workspace = workspace_user_ids(db, dataset.workspace_id)
    report_inputs = context.get("report_inputs") if isinstance(context.get("report_inputs"), dict) else {}
    metrics = {spec["key"]: audit_metric(context, spec["key"]) for spec in METRIC_SPECS}
    live_result = live_audit_for_dataset(db, dataset=dataset, integration_type=integration_type, data=data) if live else None
    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "integration_type": integration_type,
        "workspace_id": dataset.workspace_id,
        "user_id": users_in_workspace[0] if len(users_in_workspace) == 1 else None,
        "workspace_user_ids": users_in_workspace,
        "record_id": infer_record_id(dataset, integration_type),
        "record_type": "instagram_account" if integration_type == "instagram_business" else "facebook_page",
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "timeframe": dataset_timeframe(data),
        "top_level_keys": dataset_primary_keys(data),
        "report_inputs_keys": sorted(str(key) for key in report_inputs.keys()),
        "metrics": metrics,
        "warnings": dataset_warnings(context, integration_type),
        "live_audit": live_result,
        "live_vs_dataset": compare_live_vs_dataset(metrics, live_result, integration_type) if live_result else [],
    }


def summarize_integration(datasets: list[dict[str, Any]], integration_type: str) -> dict[str, Any]:
    relevant = [dataset for dataset in datasets if dataset.get("integration_type") == integration_type]
    if not relevant:
        return {
            "integration_type": integration_type,
            "dataset_count": 0,
            "latest_dataset_id": None,
            "metrics": {},
            "warnings": [],
        }
    latest = relevant[0]
    metric_summary: dict[str, Any] = {}
    for spec in METRIC_SPECS:
        key = spec["key"]
        entry = latest["metrics"].get(key) or {}
        metric_summary[key] = {
            "available": entry.get("available"),
            "value": entry.get("value"),
            "formatted_value": entry.get("formatted_value"),
            "source_path": entry.get("source_path"),
            "daily_series_available": entry.get("daily_series_available"),
            "daily_series_length": entry.get("daily_series_length"),
            "daily_series_first_date": entry.get("daily_series_first_date"),
            "daily_series_last_date": entry.get("daily_series_last_date"),
            "metric_origin": entry.get("metric_origin"),
        }
    return {
        "integration_type": integration_type,
        "dataset_count": len(relevant),
        "latest_dataset_id": latest["dataset_id"],
        "metrics": metric_summary,
        "warnings": latest.get("warnings") or [],
    }


def slide_recommendation(summary: dict[str, Any], metric_key: str) -> str:
    metric = (summary.get("metrics") or {}).get(metric_key) or {}
    if metric.get("available"):
        path = metric.get("source_path") or metric.get("daily_series_source_path") or "dataset source"
        return f"Use `{metric_key}` from `{path}`."
    return f"Show `N/A` for `{metric_key}`."


def build_markdown(audit: dict[str, Any]) -> str:
    scope = audit.get("scope") or {}
    summaries = audit.get("summaries") or {}
    fb = summaries.get("facebook_pages") or {}
    ig = summaries.get("instagram_business") or {}
    all_warnings = audit.get("warnings") or []

    def render_metric_block(summary: dict[str, Any], title: str) -> list[str]:
        lines = [f"## {title}"]
        metrics = summary.get("metrics") or {}
        if not metrics:
            lines.append("No matching datasets found.")
            return lines
        for metric_key in (
            "followers",
            "reach",
            "impressions",
            "engagement",
            "interactions",
            "link_clicks",
            "page_views",
            "profile_views",
            "website_clicks",
            "video_views",
            "daily_reach",
            "daily_impressions",
            "daily_engagement",
            "daily_interactions",
        ):
            metric = metrics.get(metric_key) or {}
            value = metric.get("formatted_value") if metric.get("formatted_value") is not None else "null"
            availability = "available" if metric.get("available") else "unavailable"
            daily_suffix = (
                f", daily_series={metric.get('daily_series_length')} points"
                if metric.get("daily_series_available")
                else ""
            )
            lines.append(
                f"- {metric_key}: {availability}; value={value}; source_path={metric.get('source_path')}; origin={metric.get('metric_origin')}{daily_suffix}"
            )
        if summary.get("warnings"):
            lines.append("")
            lines.append("Warnings:")
            for warning in summary["warnings"]:
                lines.append(f"- {warning}")
        return lines

    lines = [
        "# META Metrics Availability Audit",
        "",
        "## Resumen Ejecutivo",
        f"- Generated at: {audit.get('generated_at')}",
        f"- Scope mode: {scope.get('selection_mode')}",
        f"- Workspace id: {scope.get('resolved_workspace_id')}",
        f"- Requested user id: {scope.get('requested_user_id')}",
        f"- Dataset count audited: {len(audit.get('datasets') or [])}",
        f"- Live audit enabled: {audit.get('live_enabled')}",
        "",
    ]
    lines.extend(render_metric_block(fb, "Facebook Pages"))
    lines.append("")
    lines.extend(render_metric_block(ig, "Instagram Business"))
    lines.append("")
    lines.append("## Métricas Con Daily Series")
    for integration_key, summary in (("facebook_pages", fb), ("instagram_business", ig)):
        metrics = summary.get("metrics") or {}
        lines.append(f"- {integration_key}:")
        found_any = False
        for metric_key, metric in metrics.items():
            if metric.get("daily_series_available"):
                found_any = True
                lines.append(
                    f"  - {metric_key}: {metric.get('daily_series_length')} points "
                    f"({metric.get('daily_series_first_date')} -> {metric.get('daily_series_last_date')})"
                )
        if not found_any:
            lines.append("  - none")
    lines.append("")
    lines.append("## Métricas No Disponibles")
    for integration_key, summary in (("facebook_pages", fb), ("instagram_business", ig)):
        metrics = summary.get("metrics") or {}
        unavailable = [key for key, metric in metrics.items() if not metric.get("available")]
        lines.append(f"- {integration_key}: {', '.join(unavailable) if unavailable else 'none'}")
    lines.append("")
    lines.append("## Posibles Problemas de Permisos")
    permission_errors = []
    for dataset in audit.get("datasets") or []:
        live_audit = dataset.get("live_audit") or {}
        for error in live_audit.get("errors") or []:
            if error.get("permission_issue"):
                permission_errors.append(
                    f"dataset_id={dataset.get('dataset_id')} metric={error.get('metric_key')} "
                    f"code={error.get('error_code')} message={error.get('error_message')}"
                )
    if permission_errors:
        lines.extend(f"- {item}" for item in permission_errors)
    else:
        lines.append("- none detected or live audit not executed")
    lines.append("")
    lines.append("## Posibles Errores de Mapping")
    if all_warnings:
        lines.extend(f"- {warning}" for warning in all_warnings)
    else:
        lines.append("- none detected")
    lines.append("")
    lines.append("## Datos Directos Vs Calculados")
    lines.append("- `direct_saved_dataset_field`: valor persistido directamente en `dataset.data`.")
    lines.append("- `normalized_or_calculated_by_backend`: valor persistido en `normalized_report_metrics` o `report_metric_mapping`.")
    lines.append("- `calculated_by_backend`: valor derivado por esta auditoría, por ejemplo `post_count` o `sum(recent_posts)`.")
    lines.append("")
    lines.append("## Recomendación Para Reporte 5 Slides")
    lines.append(f"- Slide 2 Reach: {slide_recommendation(fb or ig, 'reach')}")
    lines.append(f"- Slide 3 Impressions: {slide_recommendation(fb or ig, 'impressions')}")
    lines.append(f"- Slide 4 Engagement: {slide_recommendation(fb or ig, 'engagement')}")
    lines.append(
        "- Mostrar `N/A` cuando la métrica no tenga valor real en el dataset ni respuesta válida de Meta."
    )
    return "\n".join(lines) + "\n"


def print_console_summary(audit: dict[str, Any]) -> None:
    print(CONSOLE_DIVIDER)
    print("META METRICS AVAILABILITY AUDIT")
    print(CONSOLE_DIVIDER)
    print(f"Generated at: {audit['generated_at']}")
    print(f"Scope: {audit['scope']}")
    print(f"Live enabled: {audit['live_enabled']}")
    print(f"Datasets audited: {len(audit['datasets'])}")
    for integration_key in ("facebook_pages", "instagram_business"):
        summary = (audit.get("summaries") or {}).get(integration_key) or {}
        print(CONSOLE_DIVIDER)
        print(integration_key)
        print(CONSOLE_DIVIDER)
        metrics = summary.get("metrics") or {}
        if not metrics:
            print("No matching datasets found.")
            continue
        for metric_key in (
            "followers",
            "reach",
            "impressions",
            "engagement",
            "interactions",
            "link_clicks",
            "page_views",
            "profile_views",
            "website_clicks",
            "video_views",
            "daily_reach",
            "daily_impressions",
            "daily_engagement",
            "daily_interactions",
        ):
            metric = metrics.get(metric_key) or {}
            value = metric.get("formatted_value") if metric.get("formatted_value") is not None else "null"
            if metric.get("daily_series_available"):
                daily = f"available, {metric.get('daily_series_length')} points"
            else:
                daily = "unavailable"
            print(
                f"- {metric_key}: value={value}; available={metric.get('available')}; "
                f"source={metric.get('source_path')}; daily={daily}"
            )
        for warning in summary.get("warnings") or []:
            print(warning)
    if audit.get("warnings"):
        print(CONSOLE_DIVIDER)
        print("GLOBAL WARNINGS")
        print(CONSOLE_DIVIDER)
        for warning in audit["warnings"]:
            print(warning)
    print(CONSOLE_DIVIDER)
    print(f"JSON written to: {JSON_OUTPUT_PATH}")
    print(f"Markdown written to: {MD_OUTPUT_PATH}")


def main() -> None:
    args = parse_args()
    with SessionLocal() as db:
        datasets, scope_note = query_datasets(
            db,
            workspace_id=args.workspace_id,
            user_id=args.user_id,
            integration_filter=args.integration,
            limit=args.limit,
        )
        dataset_audits = [audit_dataset(db, dataset, args.live) for dataset in datasets]
    warnings = [warning for dataset in dataset_audits for warning in (dataset.get("warnings") or [])]
    audit = {
        "generated_at": datetime.now(UTC).isoformat(),
        "live_enabled": bool(args.live),
        "scope": json_ready(scope_note),
        "datasets": dataset_audits,
        "summaries": {
            "facebook_pages": summarize_integration(dataset_audits, "facebook_pages"),
            "instagram_business": summarize_integration(dataset_audits, "instagram_business"),
        },
        "warnings": warnings,
    }
    JSON_OUTPUT_PATH.write_text(safe_json_dump(json_ready(audit)) + "\n", encoding="utf-8")
    MD_OUTPUT_PATH.write_text(build_markdown(audit), encoding="utf-8")
    print_console_summary(audit)


if __name__ == "__main__":
    main()
