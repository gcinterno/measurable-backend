from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from app.config import settings
from app.db import SessionLocal
from app.main import _get_meta_access_token, _get_meta_page_access_token
from app.models import Dataset, Integration, IntegrationAccount, MetaPage, Report, ReportVersion

META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
TOKEN_ACCOUNT_PREFIX = "__meta_token__:"
DEFAULT_POST_LIMIT = 25
PAGE_FIELDS = [
    "id",
    "name",
    "username",
    "about",
    "category",
    "category_list",
    "link",
    "website",
    "phone",
    "emails",
    "location",
    "hours",
    "picture",
    "cover",
    "followers_count",
    "fan_count",
    "rating_count",
    "overall_star_rating",
    "verification_status",
    "is_verified",
    "were_here_count",
    "talking_about_count",
    "new_like_count",
    "access_token",
    "tasks",
]
PAGE_INSIGHTS_METRICS = [
    "page_post_engagements",
    "page_actions_post_reactions_total",
    "page_views_total",
    "page_posts_impressions_organic",
    "page_posts_impressions_paid",
    "page_posts_impressions_unique",
    "page_posts_impressions",
    "page_impressions",
    "page_impressions_unique",
    "page_impressions_paid",
    "page_impressions_organic",
    "page_fans",
    "page_fans_city",
    "page_fans_country",
    "page_fans_gender_age",
    "page_fan_adds",
    "page_fan_removes",
    "page_views_by_site_logged_in_unique",
    "page_views_by_profile_tab_total",
    "page_total_actions",
    "page_cta_clicks_logged_in_total",
    "page_get_directions_clicks_logged_in_unique",
    "page_website_clicks_logged_in_unique",
    "page_call_phone_clicks_logged_in_unique",
    "page_consumptions",
    "page_consumptions_unique",
    "page_positive_feedback_by_type",
    "page_negative_feedback_by_type",
    "page_places_checkin_total",
    "page_places_checkin_total_unique",
    "page_posts_impressions_organic_unique",
    "page_posts_impressions_viral",
    "page_posts_impressions_viral_unique",
]
PAGE_INSIGHT_PERIODS = ["day", "days_28", "total_over_range"]
POST_FIELDS = [
    "id",
    "message",
    "created_time",
    "permalink_url",
    "full_picture",
    "status_type",
    "story",
    "attachments",
    "shares",
    "comments.summary(true)",
    "reactions.summary(true)",
    "likes.summary(true)",
]
POST_INSIGHT_METRICS = [
    "post_impressions",
    "post_impressions_unique",
    "post_impressions_paid",
    "post_impressions_paid_unique",
    "post_impressions_fan",
    "post_impressions_fan_unique",
    "post_impressions_organic",
    "post_impressions_organic_unique",
    "post_engaged_users",
    "post_clicks",
    "post_clicks_by_type",
    "post_reactions_by_type_total",
    "post_activity",
    "post_activity_by_action_type",
    "post_video_views",
    "post_video_views_unique",
    "post_video_avg_time_watched",
    "post_video_complete_views_organic",
    "post_video_complete_views_paid",
]
VIDEO_INSIGHT_METRICS = [
    "total_video_views",
    "total_video_views_unique",
    "total_video_impressions",
    "total_video_impressions_unique",
    "total_video_complete_views",
    "total_video_avg_time_watched",
    "total_video_view_total_time",
    "total_video_10s_views",
    "total_video_15s_views",
]


def _truncate(value: Any, limit: int = 1200) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _sum_numeric(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        total = 0.0
        found = False
        for item in value.values():
            numeric = _sum_numeric(item)
            if numeric is not None:
                total += float(numeric)
                found = True
        if not found:
            return None
        return int(total) if total.is_integer() else total
    if isinstance(value, list):
        total = 0.0
        found = False
        for item in value:
            numeric = _sum_numeric(item)
            if numeric is not None:
                total += float(numeric)
                found = True
        if not found:
            return None
        return int(total) if total.is_integer() else total
    return None


def _extract_error(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    return {
        "error_code": error.get("code"),
        "error_subcode": error.get("error_subcode"),
        "error_message": error.get("message"),
        "error_type": error.get("type"),
        "fbtrace_id": error.get("fbtrace_id"),
    }


def _extract_values(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    values: list[Any] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        metric_values = item.get("values")
        if isinstance(metric_values, list):
            values.extend(metric_values)
    return values


def _extract_latest_value(raw_values: list[Any]) -> Any:
    if not raw_values:
        return None
    last = raw_values[-1]
    if isinstance(last, dict) and "value" in last:
        return last.get("value")
    return last


def _parse_page_id_from_dataset_name(dataset_name: str | None) -> str | None:
    if not dataset_name:
        return None
    match = re.search(r"meta_page_(\d+)_insights", dataset_name)
    return match.group(1) if match else None


def _find_selected_page_account(db, *, integration_id: int, page_id: str) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id.like(f"%{page_id}%"),
            IntegrationAccount.external_account_id != f"{TOKEN_ACCOUNT_PREFIX}{integration_id}",
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )


def _resolve_page_context_from_report(db, report_id: int) -> dict[str, Any]:
    report = db.get(Report, report_id)
    if report is None:
        raise SystemExit(json.dumps({"error": "report_not_found", "report_id": report_id}, ensure_ascii=False))

    dataset = db.get(Dataset, report.dataset_id) if report.dataset_id else None
    dataset_data = dataset.data if dataset and isinstance(dataset.data, dict) else {}
    report_inputs = dataset_data.get("report_inputs") if isinstance(dataset_data.get("report_inputs"), dict) else {}
    timeframe = dataset_data.get("timeframe") if isinstance(dataset_data.get("timeframe"), dict) else {}
    page_id = (
        dataset_data.get("page_id")
        or dataset_data.get("selected_page_id")
        or report_inputs.get("page_id")
        or _parse_page_id_from_dataset_name(getattr(dataset, "name", None))
    )
    page_name = dataset_data.get("page_name") or report_inputs.get("page_name") or report.name

    version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report.id)
        .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
        .first()
    )

    return {
        "mode": "report",
        "report_id": report.id,
        "report_version_id": version.id if version else None,
        "report_version": version.version if version else None,
        "dataset_id": dataset.id if dataset else None,
        "dataset_name": dataset.name if dataset else None,
        "workspace_id": report.workspace_id,
        "page_id": str(page_id) if page_id else None,
        "page_name": page_name,
        "since": str(timeframe.get("since") or "")[:10] or None,
        "until": str(timeframe.get("until") or "")[:10] or None,
    }


def _resolve_integration_and_page(db, *, workspace_id: int | None, page_id: str | None, page_name: str | None) -> tuple[Integration, MetaPage]:
    integrations_query = db.query(Integration).filter(Integration.provider == "meta")
    if workspace_id is not None:
        integrations_query = integrations_query.filter(Integration.workspace_id == workspace_id)
    integrations = integrations_query.order_by(Integration.updated_at.desc(), Integration.id.desc()).all()

    for integration in integrations:
        query = db.query(MetaPage).filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
        )
        if page_id:
            page = (
                query.filter(MetaPage.page_id == str(page_id))
                .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
                .first()
            )
            if page:
                return integration, page
        if page_name:
            page = (
                query.filter(MetaPage.name.ilike(page_name.strip()))
                .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
                .first()
            )
            if page:
                return integration, page

    raise SystemExit(
        json.dumps(
            {
                "error": "meta_page_not_found",
                "workspace_id": workspace_id,
                "page_id": page_id,
                "page_name": page_name,
            },
            ensure_ascii=False,
        )
    )


def _resolve_token(db, *, integration: Integration, meta_page: MetaPage) -> tuple[str, str]:
    selected_page = _find_selected_page_account(db, integration_id=integration.id, page_id=meta_page.page_id)
    if selected_page is not None:
        try:
            return _get_meta_page_access_token(db, integration, selected_page), "selected_page_access_token"
        except Exception:
            pass
    if meta_page.page_access_token:
        return meta_page.page_access_token, "meta_page_cache_token"
    return _get_meta_access_token(db, integration), "integration_access_token"


def _resolve_context(db, *, report_id: int | None, page_id: str | None) -> dict[str, Any]:
    if report_id is None and page_id is None:
        raise SystemExit(json.dumps({"error": "missing_identifier", "message": "Provide --report-id or --page-id"}, ensure_ascii=False))

    base_context = _resolve_page_context_from_report(db, report_id) if report_id is not None else {
        "mode": "page",
        "report_id": None,
        "report_version_id": None,
        "report_version": None,
        "dataset_id": None,
        "dataset_name": None,
        "workspace_id": None,
        "page_id": page_id,
        "page_name": None,
        "since": None,
        "until": None,
    }
    integration, meta_page = _resolve_integration_and_page(
        db,
        workspace_id=base_context["workspace_id"],
        page_id=base_context["page_id"],
        page_name=base_context["page_name"],
    )
    access_token, token_source = _resolve_token(db, integration=integration, meta_page=meta_page)
    base_context.update(
        {
            "workspace_id": integration.workspace_id,
            "integration_id": integration.id,
            "integration_name": integration.name,
            "page_id": meta_page.page_id,
            "page_name": meta_page.name,
            "token_source": token_source,
            "access_token": access_token,
            "meta_page_id": meta_page.id,
        }
    )
    return base_context


def _graph_get(endpoint: str, *, access_token: str, params: dict[str, Any]) -> tuple[int, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{endpoint.lstrip('/')}"
    response = requests.get(url, params={**params, "access_token": access_token}, timeout=30)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


def _audit_page_fields(*, access_token: str, page_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field in PAGE_FIELDS:
        status_code, payload = _graph_get(page_id, access_token=access_token, params={"fields": field})
        error = _extract_error(payload)
        value = payload.get(field) if status_code == 200 and isinstance(payload, dict) else None
        rows.append(
            {
                "section": "page_fields",
                "field": field,
                "available": bool(status_code == 200 and isinstance(payload, dict) and field in payload),
                "value_type": _value_type(value),
                "sample_value": _truncate(value),
                "status_code": status_code,
                "error_code": error.get("error_code") if error else None,
                "error_subcode": error.get("error_subcode") if error else None,
                "error_message": error.get("error_message") if error else None,
                "fbtrace_id": error.get("fbtrace_id") if error else None,
                "raw_response": _truncate(payload),
            }
        )
    return rows


def _audit_page_insights(*, access_token: str, page_id: str, since: str | None, until: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    endpoint = f"{page_id}/insights"
    for metric in PAGE_INSIGHTS_METRICS:
        period_results: dict[str, dict[str, Any]] = {}
        for period in PAGE_INSIGHT_PERIODS:
            params: dict[str, Any] = {"metric": metric, "period": period}
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            status_code, payload = _graph_get(endpoint, access_token=access_token, params=params)
            raw_values = _extract_values(payload)
            latest_value = _extract_latest_value(raw_values)
            raw_sum = _sum_numeric([item.get("value") if isinstance(item, dict) else item for item in raw_values])
            error = _extract_error(payload)
            row = {
                "section": "page_insights",
                "metric": metric,
                "period": period,
                "available": bool(status_code == 200 and isinstance(payload, dict) and payload.get("data")),
                "status_code": status_code,
                "values_count": len(raw_values),
                "raw_values": raw_values,
                "raw_sum": raw_sum if raw_sum is not None else _sum_numeric(latest_value),
                "latest_value": latest_value,
                "supports_daily_series": False,
                "supports_total_over_range": False,
                "error_code": error.get("error_code") if error else None,
                "error_subcode": error.get("error_subcode") if error else None,
                "error_message": error.get("error_message") if error else None,
                "fbtrace_id": error.get("fbtrace_id") if error else None,
                "raw_response": _truncate(payload),
            }
            period_results[period] = row
        day_available = period_results.get("day", {}).get("available", False)
        total_available = period_results.get("total_over_range", {}).get("available", False)
        for period in PAGE_INSIGHT_PERIODS:
            row = period_results[period]
            row["supports_daily_series"] = bool(day_available and period == "day" and row["values_count"] > 0)
            row["supports_total_over_range"] = bool(total_available)
            rows.append(row)
    return rows


def _fetch_published_posts(*, access_token: str, page_id: str, since: str | None, until: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    endpoint = f"{page_id}/published_posts"
    params: dict[str, Any] = {
        "fields": ",".join(POST_FIELDS),
        "limit": DEFAULT_POST_LIMIT,
    }
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    status_code, payload = _graph_get(endpoint, access_token=access_token, params=params)
    data = payload.get("data") if status_code == 200 and isinstance(payload, dict) else []
    if not isinstance(data, list):
        data = []
    return data, {
        "section": "page_posts_request",
        "endpoint": endpoint,
        "status_code": status_code,
        "available": bool(status_code == 200),
        "posts_count": len(data),
        "error": _extract_error(payload),
        "raw_response": _truncate(payload),
    }


def _audit_post_fields(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_field_names = {
        "comments.limit(0).summary(true)": "comments",
        "reactions.limit(0).summary(true)": "reactions",
        "likes.limit(0).summary(true)": "likes",
    }
    for field in POST_FIELDS:
        actual_field = normalized_field_names.get(field, field)
        available_posts = [post for post in posts if isinstance(post, dict) and actual_field in post and post.get(actual_field) is not None]
        sample_value = available_posts[0].get(actual_field) if available_posts else None
        rows.append(
            {
                "section": "page_posts",
                "field": field,
                "available": bool(available_posts),
                "sample_value": _truncate(sample_value),
                "coverage_count": len(available_posts),
                "error_message": None if available_posts else "field_not_present_in_sampled_posts",
            }
        )
    return rows


def _audit_post_insights(*, access_token: str, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in posts:
        post_id = str(post.get("id") or "")
        if not post_id:
            continue
        endpoint = f"{post_id}/insights"
        for metric in POST_INSIGHT_METRICS:
            status_code, payload = _graph_get(endpoint, access_token=access_token, params={"metric": metric})
            raw_values = _extract_values(payload)
            latest_value = _extract_latest_value(raw_values)
            raw_sum = _sum_numeric([item.get("value") if isinstance(item, dict) else item for item in raw_values])
            error = _extract_error(payload)
            rows.append(
                {
                    "section": "post_insights",
                    "post_id": post_id,
                    "metric": metric,
                    "available": bool(status_code == 200 and isinstance(payload, dict) and payload.get("data")),
                    "status_code": status_code,
                    "raw_values": raw_values,
                    "raw_sum": raw_sum if raw_sum is not None else _sum_numeric(latest_value),
                    "latest_value": latest_value,
                    "error_code": error.get("error_code") if error else None,
                    "error_subcode": error.get("error_subcode") if error else None,
                    "error_message": error.get("error_message") if error else None,
                    "fbtrace_id": error.get("fbtrace_id") if error else None,
                    "raw_response": _truncate(payload),
                }
            )
    return rows


def _extract_video_ids_from_attachments(attachments: Any) -> list[str]:
    video_ids: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("type") or node.get("media_type") or "").lower()
            target = node.get("target")
            if isinstance(target, dict):
                target_id = target.get("id")
                if target_id and "video" in node_type:
                    video_ids.add(str(target_id))
            media = node.get("media")
            if isinstance(media, dict):
                image = media.get("image")
                if isinstance(image, dict) and "video" in node_type:
                    media_id = image.get("id")
                    if media_id:
                        video_ids.add(str(media_id))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(attachments)
    return sorted(video_ids)


def _audit_video_insights(*, access_token: str, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    post_video_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for post in posts:
        post_id = str(post.get("id") or "")
        for video_id in _extract_video_ids_from_attachments(post.get("attachments")):
            pair = (post_id, video_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                post_video_pairs.append(pair)

    if not post_video_pairs:
        rows.append(
            {
                "section": "video_insights",
                "status": "not_tested_no_video_posts_found",
                "available": False,
                "post_id": None,
                "video_id": None,
                "metric": None,
                "raw_values": None,
                "raw_sum": None,
                "error_code": None,
                "error_subcode": None,
                "error_message": "not_tested_no_video_posts_found",
                "fbtrace_id": None,
            }
        )
        return rows

    for post_id, video_id in post_video_pairs:
        endpoint = f"{video_id}/video_insights"
        for metric in VIDEO_INSIGHT_METRICS:
            status_code, payload = _graph_get(endpoint, access_token=access_token, params={"metric": metric})
            raw_values = _extract_values(payload)
            latest_value = _extract_latest_value(raw_values)
            raw_sum = _sum_numeric([item.get("value") if isinstance(item, dict) else item for item in raw_values])
            error = _extract_error(payload)
            rows.append(
                {
                    "section": "video_insights",
                    "post_id": post_id,
                    "video_id": video_id,
                    "metric": metric,
                    "available": bool(status_code == 200 and isinstance(payload, dict) and payload.get("data")),
                    "status_code": status_code,
                    "raw_values": raw_values,
                    "raw_sum": raw_sum if raw_sum is not None else _sum_numeric(latest_value),
                    "latest_value": latest_value,
                    "error_code": error.get("error_code") if error else None,
                    "error_subcode": error.get("error_subcode") if error else None,
                    "error_message": error.get("error_message") if error else None,
                    "fbtrace_id": error.get("fbtrace_id") if error else None,
                    "raw_response": _truncate(payload),
                }
            )
    return rows


def _flatten_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        flat: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                flat[key] = json.dumps(value, ensure_ascii=False, default=str)
            else:
                flat[key] = value
        flattened.append(flat)
    return flattened


def _recommended_slide_use(metric_or_field: str, section: str) -> str | None:
    mapping = {
        ("followers_count", "page_fields"): "summary_card_followers",
        ("fan_count", "page_fields"): "summary_card_followers",
        ("page_post_engagements", "page_insights"): "engagement_slide",
        ("page_actions_post_reactions_total", "page_insights"): "summary_card_reactions",
        ("page_views_total", "page_insights"): "summary_card_page_views",
        ("page_posts_impressions_organic", "page_insights"): "visibility_slide_or_summary",
        ("post_engaged_users", "post_insights"): "top_posts_detail",
        ("post_reactions_by_type_total", "post_insights"): "top_posts_detail",
        ("post_video_views", "post_insights"): "video_performance_detail",
        ("total_video_views", "video_insights"): "video_performance_detail",
    }
    return mapping.get((metric_or_field, section))


def _build_executive_summary(
    *,
    page_fields: list[dict[str, Any]],
    page_insights: list[dict[str, Any]],
    post_fields: list[dict[str, Any]],
    post_insights: list[dict[str, Any]],
    video_insights: list[dict[str, Any]],
) -> dict[str, Any]:
    available_and_product_ready: list[dict[str, Any]] = []
    invalid_or_deprecated: list[dict[str, Any]] = []
    empty_but_valid: list[dict[str, Any]] = []
    missing_permission: list[dict[str, Any]] = []

    for row in page_fields:
        if row["available"]:
            available_and_product_ready.append(
                {
                    "metric_or_field": row["field"],
                    "section": "page_fields",
                    "total_available": True,
                    "daily_series_available": False,
                    "recommended_slide_use": _recommended_slide_use(row["field"], "page_fields"),
                    "confidence": "high" if row["field"] in {"followers_count", "fan_count", "name"} else "medium",
                    "notes": f"value_type={row['value_type']}",
                }
            )
        elif row.get("error_code") == 10:
            missing_permission.append(
                {
                    "metric_or_field": row["field"],
                    "error_code": row.get("error_code"),
                    "error_message": row.get("error_message"),
                }
            )

    metrics_grouped: dict[str, list[dict[str, Any]]] = {}
    for row in page_insights:
        metrics_grouped.setdefault(row["metric"], []).append(row)

    for metric, rows in metrics_grouped.items():
        available_rows = [row for row in rows if row["available"]]
        invalid_rows = [row for row in rows if row.get("error_code") == 100]
        permission_rows = [row for row in rows if row.get("error_code") == 10]
        if available_rows:
            best_row = next((row for row in available_rows if row["period"] == "total_over_range"), available_rows[0])
            raw_sum = best_row.get("raw_sum")
            if raw_sum is None and best_row.get("latest_value") is None:
                empty_but_valid.append({"metric": metric, "reason": "valid_metric_but_no_values_returned"})
            else:
                daily_ok = any(row["period"] == "day" and row["values_count"] > 0 for row in available_rows)
                confidence = "high" if daily_ok and any(row["period"] == "total_over_range" for row in available_rows) else "medium"
                available_and_product_ready.append(
                    {
                        "metric_or_field": metric,
                        "section": "page_insights",
                        "total_available": any(row["period"] == "total_over_range" for row in available_rows) or bool(best_row.get("raw_sum") is not None),
                        "daily_series_available": daily_ok,
                        "recommended_slide_use": _recommended_slide_use(metric, "page_insights"),
                        "confidence": confidence,
                        "notes": f"available_periods={','.join(row['period'] for row in available_rows)}",
                    }
                )
        elif invalid_rows:
            invalid_or_deprecated.append(
                {
                    "metric": metric,
                    "error_code": invalid_rows[0].get("error_code"),
                    "error_message": invalid_rows[0].get("error_message"),
                }
            )
        elif permission_rows:
            missing_permission.append(
                {
                    "metric_or_field": metric,
                    "error_code": permission_rows[0].get("error_code"),
                    "error_message": permission_rows[0].get("error_message"),
                }
            )
        else:
            empty_but_valid.append({"metric": metric, "reason": "no_available_rows"})

    post_metrics_grouped: dict[str, list[dict[str, Any]]] = {}
    for row in post_insights:
        post_metrics_grouped.setdefault(row["metric"], []).append(row)
    for metric, rows in post_metrics_grouped.items():
        available_rows = [row for row in rows if row["available"]]
        if available_rows:
            available_and_product_ready.append(
                {
                    "metric_or_field": metric,
                    "section": "post_insights",
                    "total_available": True,
                    "daily_series_available": False,
                    "recommended_slide_use": _recommended_slide_use(metric, "post_insights"),
                    "confidence": "medium",
                    "notes": f"posts_with_metric={len(available_rows)}",
                }
            )
        elif rows and rows[0].get("error_code") == 100:
            invalid_or_deprecated.append(
                {
                    "metric": metric,
                    "error_code": rows[0].get("error_code"),
                    "error_message": rows[0].get("error_message"),
                }
            )

    for row in video_insights:
        if row.get("status") == "not_tested_no_video_posts_found":
            empty_but_valid.append({"metric": "video_insights", "reason": "not_tested_no_video_posts_found"})
            continue
        if row["available"]:
            available_and_product_ready.append(
                {
                    "metric_or_field": row["metric"],
                    "section": "video_insights",
                    "total_available": True,
                    "daily_series_available": False,
                    "recommended_slide_use": _recommended_slide_use(row["metric"], "video_insights"),
                    "confidence": "medium",
                    "notes": f"video_id={row.get('video_id')}",
                }
            )
        elif row.get("error_code") == 100:
            invalid_or_deprecated.append(
                {
                    "metric": row["metric"],
                    "error_code": row.get("error_code"),
                    "error_message": row.get("error_message"),
                }
            )

    available_and_product_ready.sort(key=lambda item: (item["section"], item["metric_or_field"]))
    invalid_or_deprecated.sort(key=lambda item: str(item["metric"]))
    missing_permission.sort(key=lambda item: str(item["metric_or_field"]))
    empty_but_valid.sort(key=lambda item: str(item["metric"]))

    return {
        "available_and_product_ready": available_and_product_ready,
        "invalid_or_deprecated": invalid_or_deprecated,
        "empty_but_valid": empty_but_valid,
        "missing_permission": missing_permission,
        "post_field_coverage": post_fields,
    }


def _build_product_suggestion(summary: dict[str, Any]) -> dict[str, Any]:
    preferred_order = [
        ("page_posts_impressions_organic", "Organic Post Impressions", "available visibility metric returned directly by Page Insights", True),
        ("page_post_engagements", "Page Post Engagements", "stable page-level engagement metric with daily series and total range", True),
        ("page_actions_post_reactions_total", "Post Reactions", "reaction totals are available and interpretable", True),
        ("page_views_total", "Page Views", "page view totals and daily series are available", True),
        ("followers_count", "Followers", "direct page field available from Page node", False),
        ("fan_count", "Fans", "direct page field available from Page node", False),
    ]
    available_lookup = {
        item["metric_or_field"]: item
        for item in summary["available_and_product_ready"]
    }
    recommendations: list[dict[str, Any]] = []
    for metric, display_name, reason, chartable in preferred_order:
        available = available_lookup.get(metric)
        if not available:
            continue
        recommendations.append(
            {
                "metric": metric,
                "display_name": display_name,
                "reason": reason,
                "chartable": chartable and bool(available.get("daily_series_available")),
                "confidence": available.get("confidence", "medium"),
            }
        )
    return {"recommended_facebook_report_metrics": recommendations}


def _write_csv(path: Path, sections: dict[str, list[dict[str, Any]]]) -> None:
    rows: list[dict[str, Any]] = []
    for section_rows in sections.values():
        rows.extend(_flatten_rows(section_rows))
    fieldnames: list[str] = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", type=str)
    parser.add_argument("--report-id", type=int)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        context = _resolve_context(session, report_id=args.report_id, page_id=args.page_id)
        page_fields = _audit_page_fields(access_token=context["access_token"], page_id=context["page_id"])
        page_insights = _audit_page_insights(
            access_token=context["access_token"],
            page_id=context["page_id"],
            since=context["since"],
            until=context["until"],
        )
        posts, posts_request = _fetch_published_posts(
            access_token=context["access_token"],
            page_id=context["page_id"],
            since=context["since"],
            until=context["until"],
        )
        post_fields = _audit_post_fields(posts)
        post_insights = _audit_post_insights(access_token=context["access_token"], posts=posts)
        video_insights = _audit_video_insights(access_token=context["access_token"], posts=posts)

        executive_summary = _build_executive_summary(
            page_fields=page_fields,
            page_insights=page_insights,
            post_fields=post_fields,
            post_insights=post_insights,
            video_insights=video_insights,
        )
        product_suggestion = _build_product_suggestion(executive_summary)

        output = {
            "generated_at": datetime.now(UTC).isoformat(),
            "context": {
                "mode": context["mode"],
                "report_id": context["report_id"],
                "report_version_id": context["report_version_id"],
                "report_version": context["report_version"],
                "dataset_id": context["dataset_id"],
                "dataset_name": context["dataset_name"],
                "workspace_id": context["workspace_id"],
                "integration_id": context["integration_id"],
                "integration_name": context["integration_name"],
                "meta_page_id": context["meta_page_id"],
                "page_id": context["page_id"],
                "page_name": context["page_name"],
                "since": context["since"],
                "until": context["until"],
                "token_source": context["token_source"],
                "graph_api_version": settings.meta_api_version,
            },
            "page_fields": page_fields,
            "page_insights": page_insights,
            "page_posts_request": posts_request,
            "page_posts": post_fields,
            "sampled_posts": posts,
            "post_insights": post_insights,
            "video_insights": video_insights,
            "executive_summary": executive_summary,
            "product_suggestion": product_suggestion,
        }

        date_label = datetime.now(UTC).strftime("%Y%m%d")
        tmp_dir = Path("tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        json_path = tmp_dir / f"facebook_data_catalog_{context['page_id']}_{date_label}.json"
        csv_path = tmp_dir / f"facebook_data_catalog_{context['page_id']}_{date_label}.csv"
        json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        _write_csv(
            csv_path,
            {
                "page_fields": page_fields,
                "page_insights": page_insights,
                "page_posts": post_fields,
                "post_insights": post_insights,
                "video_insights": video_insights,
                "summary_available": executive_summary["available_and_product_ready"],
                "summary_invalid": executive_summary["invalid_or_deprecated"],
                "summary_empty": executive_summary["empty_but_valid"],
                "summary_missing_permission": executive_summary["missing_permission"],
                "product_suggestion": product_suggestion["recommended_facebook_report_metrics"],
            },
        )

        console_summary = {
            "page_id": context["page_id"],
            "page_name": context["page_name"],
            "since": context["since"],
            "until": context["until"],
            "sampled_posts_count": len(posts),
            "available_and_product_ready_count": len(executive_summary["available_and_product_ready"]),
            "invalid_or_deprecated_count": len(executive_summary["invalid_or_deprecated"]),
            "empty_but_valid_count": len(executive_summary["empty_but_valid"]),
            "missing_permission_count": len(executive_summary["missing_permission"]),
            "top_recommendations": product_suggestion["recommended_facebook_report_metrics"][:8],
            "json_path": str(json_path),
            "csv_path": str(csv_path),
        }
        print(json.dumps(console_summary, indent=2, ensure_ascii=False, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
