from __future__ import annotations

import argparse
import json
import re
from typing import Any

import requests

from app.config import settings
from app.db import SessionLocal
from app.main import _get_meta_access_token, _get_meta_page_access_token
from app.models import Dataset, Integration, IntegrationAccount, MetaPage, Report, ReportVersion

META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
TOKEN_ACCOUNT_PREFIX = "__meta_token__:"
POSTS_LIMIT = 5
PAGE_REACH_CANDIDATES = [
    "page_impressions_unique",
    "page_posts_impressions_unique",
]
PAGE_IMPRESSIONS_CANDIDATES = [
    "page_impressions",
    "page_posts_impressions",
    "page_posts_impressions_paid",
    "page_posts_impressions_organic",
]
PAGE_CONTROL_CANDIDATES = [
    "page_post_engagements",
    "page_actions_post_reactions_total",
    "page_consumptions",
    "page_views_total",
    "page_fans",
]
POST_CANDIDATES = [
    "post_impressions_unique",
    "post_impressions",
]
PAGE_PERIODS = ["day", "days_28", "total_over_range"]


def _truncate(value: Any, limit: int = 6000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


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


def _extract_values(payload: dict[str, Any]) -> list[Any]:
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


def _extract_error(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    return {
        "code": error.get("code"),
        "subcode": error.get("error_subcode"),
        "message": error.get("message"),
        "type": error.get("type"),
        "fbtrace_id": error.get("fbtrace_id"),
    }


def _find_selected_page_account(
    db,
    *,
    integration_id: int,
    page_id: str,
) -> IntegrationAccount | None:
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


def _parse_page_id_from_dataset_name(dataset_name: str | None) -> str | None:
    if not dataset_name:
        return None
    match = re.search(r"meta_page_(\d+)_insights", dataset_name)
    return match.group(1) if match else None


def _resolve_report_context(db, report_id: int) -> dict[str, Any]:
    report = db.get(Report, report_id)
    if report is None:
        raise SystemExit(json.dumps({"error": "report_not_found", "report_id": report_id}, ensure_ascii=False))

    dataset = db.get(Dataset, report.dataset_id) if report.dataset_id else None
    dataset_data = dataset.data if dataset and isinstance(dataset.data, dict) else {}
    timeframe = dataset_data.get("timeframe") if isinstance(dataset_data.get("timeframe"), dict) else {}
    version = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report.id)
        .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
        .first()
    )

    page_id = (
        dataset_data.get("page_id")
        or dataset_data.get("selected_page_id")
        or (dataset_data.get("report_inputs") or {}).get("page_id")
        or _parse_page_id_from_dataset_name(getattr(dataset, "name", None))
    )
    page_name = (
        dataset_data.get("page_name")
        or (dataset_data.get("report_inputs") or {}).get("page_name")
        or report.name
    )
    since = str(timeframe.get("since") or "")[:10]
    until = str(timeframe.get("until") or "")[:10]

    integrations = (
        db.query(Integration)
        .filter(Integration.workspace_id == report.workspace_id, Integration.provider == "meta")
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )

    meta_page = None
    integration = None
    if page_id:
        for candidate in integrations:
            meta_page = (
                db.query(MetaPage)
                .filter(
                    MetaPage.integration_id == candidate.id,
                    MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
                    MetaPage.page_id == str(page_id),
                )
                .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
                .first()
            )
            if meta_page is not None:
                integration = candidate
                break
    if meta_page is None:
        normalized_page_name = str(page_name or "").strip().lower()
        for candidate in integrations:
            rows = (
                db.query(MetaPage)
                .filter(
                    MetaPage.integration_id == candidate.id,
                    MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
                )
                .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
                .all()
            )
            for row in rows:
                if str(row.name or "").strip().lower() == normalized_page_name:
                    meta_page = row
                    integration = candidate
                    page_id = row.page_id
                    break
            if meta_page is not None:
                break

    if integration is None or meta_page is None or not page_id:
        raise SystemExit(
            json.dumps(
                {
                    "error": "meta_page_not_found",
                    "report_id": report_id,
                    "workspace_id": report.workspace_id,
                    "page_id": page_id,
                    "page_name": page_name,
                },
                ensure_ascii=False,
            )
        )

    selected_page = _find_selected_page_account(db, integration_id=integration.id, page_id=str(page_id))
    token = None
    token_source = "missing_token"
    if selected_page is not None:
        try:
            token = _get_meta_page_access_token(db, integration, selected_page)
            token_source = "selected_page_access_token"
        except Exception:
            token = None
    if not token and meta_page.page_access_token:
        token = meta_page.page_access_token
        token_source = "meta_page_cache_token"
    if not token:
        token = _get_meta_access_token(db, integration)
        token_source = "integration_access_token"

    return {
        "report": report,
        "report_version": version,
        "dataset": dataset,
        "dataset_data": dataset_data,
        "integration": integration,
        "meta_page": meta_page,
        "page_id": str(page_id),
        "page_name": meta_page.name or page_name,
        "since": since,
        "until": until,
        "token": token,
        "token_source": token_source,
    }


def _graph_get(
    endpoint: str,
    *,
    access_token: str,
    params: dict[str, Any],
) -> tuple[int | None, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{endpoint.lstrip('/')}"
    response = requests.get(url, params={**params, "access_token": access_token}, timeout=30)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


def _build_metric_result(
    *,
    metric_requested: str,
    endpoint: str,
    period: str | None,
    since: str | None,
    until: str | None,
    status_code: int | None,
    payload: Any,
) -> dict[str, Any]:
    raw_values = _extract_values(payload) if isinstance(payload, dict) else []
    last_value = raw_values[-1].get("value") if raw_values and isinstance(raw_values[-1], dict) else None
    raw_sum = _sum_numeric([item.get("value") if isinstance(item, dict) else item for item in raw_values])
    error = _extract_error(payload)
    available = bool(status_code == 200 and isinstance(payload, dict) and payload.get("data"))
    return {
        "metric_requested": metric_requested,
        "endpoint": endpoint,
        "period": period,
        "since": since,
        "until": until,
        "status_code": status_code,
        "raw_response": _truncate(payload),
        "values_count": len(raw_values),
        "raw_values": raw_values,
        "raw_sum": raw_sum if raw_sum is not None else _sum_numeric(last_value),
        "error": error,
        "available": available,
    }


def _audit_page_metric(
    *,
    access_token: str,
    page_id: str,
    metric: str,
    since: str,
    until: str,
    period: str,
) -> dict[str, Any]:
    endpoint = f"{page_id}/insights"
    status_code, payload = _graph_get(
        endpoint,
        access_token=access_token,
        params={
            "metric": metric,
            "period": period,
            "since": since,
            "until": until,
        },
    )
    return _build_metric_result(
        metric_requested=metric,
        endpoint=endpoint,
        period=period,
        since=since,
        until=until,
        status_code=status_code,
        payload=payload,
    )


def _audit_page_fields(
    *,
    access_token: str,
    page_id: str,
    fields: str,
    page_name: str,
) -> dict[str, Any]:
    endpoint = page_id
    status_code, payload = _graph_get(
        endpoint,
        access_token=access_token,
        params={"fields": fields},
    )
    return {
        "metric_requested": fields,
        "endpoint": endpoint,
        "period": None,
        "since": None,
        "until": None,
        "status_code": status_code,
        "raw_response": _truncate(payload),
        "values_count": 1 if isinstance(payload, dict) else 0,
        "raw_values": payload,
        "raw_sum": None,
        "error": _extract_error(payload),
        "available": bool(status_code == 200 and isinstance(payload, dict)),
        "page_name": page_name,
    }


def _fetch_recent_posts(
    *,
    access_token: str,
    page_id: str,
) -> dict[str, Any]:
    endpoint = f"{page_id}/posts"
    status_code, payload = _graph_get(
        endpoint,
        access_token=access_token,
        params={
            "fields": "id,created_time,message,permalink_url",
            "limit": POSTS_LIMIT,
        },
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    return {
        "metric_requested": "recent_posts",
        "endpoint": endpoint,
        "period": None,
        "since": None,
        "until": None,
        "status_code": status_code,
        "raw_response": _truncate(payload),
        "values_count": len(data) if isinstance(data, list) else 0,
        "raw_values": data if isinstance(data, list) else [],
        "raw_sum": None,
        "error": _extract_error(payload),
        "available": bool(status_code == 200 and isinstance(data, list)),
    }


def _audit_post_metric(
    *,
    access_token: str,
    post_id: str,
    metric: str,
) -> dict[str, Any]:
    endpoint = f"{post_id}/insights"
    status_code, payload = _graph_get(
        endpoint,
        access_token=access_token,
        params={"metric": metric},
    )
    result = _build_metric_result(
        metric_requested=metric,
        endpoint=endpoint,
        period=None,
        since=None,
        until=None,
        status_code=status_code,
        payload=payload,
    )
    result["post_id"] = post_id
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-id", type=int, required=True)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        context = _resolve_report_context(session, args.report_id)
        page_id = context["page_id"]
        page_name = context["page_name"]
        since = context["since"]
        until = context["until"]
        access_token = context["token"]

        page_results: list[dict[str, Any]] = []
        for metric in PAGE_REACH_CANDIDATES + PAGE_IMPRESSIONS_CANDIDATES + PAGE_CONTROL_CANDIDATES:
            for period in PAGE_PERIODS:
                page_results.append(
                    _audit_page_metric(
                        access_token=access_token,
                        page_id=page_id,
                        metric=metric,
                        since=since,
                        until=until,
                        period=period,
                    )
                )

        fields_results = [
            _audit_page_fields(
                access_token=access_token,
                page_id=page_id,
                page_name=page_name,
                fields="id,name,fan_count,followers_count",
            )
        ]

        posts_result = _fetch_recent_posts(access_token=access_token, page_id=page_id)
        post_results: list[dict[str, Any]] = []
        if posts_result["available"]:
            for post in posts_result["raw_values"]:
                if not isinstance(post, dict) or not post.get("id"):
                    continue
                for metric in POST_CANDIDATES:
                    post_results.append(
                        _audit_post_metric(
                            access_token=access_token,
                            post_id=str(post["id"]),
                            metric=metric,
                        )
                    )

        output = {
            "report_id": context["report"].id,
            "report_version": {
                "id": context["report_version"].id if context["report_version"] else None,
                "version": context["report_version"].version if context["report_version"] else None,
            },
            "dataset_id": context["dataset"].id if context["dataset"] else None,
            "dataset_name": context["dataset"].name if context["dataset"] else None,
            "workspace_id": context["report"].workspace_id,
            "integration_id": context["integration"].id,
            "integration_name": context["integration"].name,
            "page_id": page_id,
            "page_name": page_name,
            "token_source": context["token_source"],
            "period": {
                "since": since,
                "until": until,
            },
            "page_fields": fields_results,
            "recent_posts": posts_result,
            "page_metric_requests": page_results,
            "post_metric_requests": post_results,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
