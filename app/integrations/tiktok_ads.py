from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import HTTPException
from jose import JWTError, jwt

from ..config import settings
from ..errors import http_error

logger = logging.getLogger(__name__)

TIKTOK_STATE_PURPOSE = "tiktok_ads_oauth"
TIKTOK_AUTH_BASE_URL = "https://ads.tiktok.com/marketing_api/auth"
TIKTOK_CORE_REPORT_METRICS = [
    "impressions",
    "clicks",
    "spend",
    "cpc",
    "cpm",
    "ctr",
    "conversions",
]
TIKTOK_OPTIONAL_REPORT_METRICS = [
    "reach",
    "video_views",
    "likes",
    "comments",
    "shares",
]


def _truncate_log_value(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def tiktok_missing_env_flags() -> dict[str, bool]:
    return {
        "app_id_missing": not bool(str(settings.tiktok_app_id or "").strip()),
        "secret_missing": not bool(str(settings.tiktok_secret or "").strip()),
        "redirect_uri_missing": not bool(str(settings.tiktok_redirect_uri or "").strip()),
    }


def _require_tiktok_config(*, require_secret: bool) -> None:
    missing = tiktok_missing_env_flags()
    missing_fields: list[str] = []
    if missing["app_id_missing"]:
        missing_fields.append("TIKTOK_APP_ID")
    if require_secret and missing["secret_missing"]:
        missing_fields.append("TIKTOK_SECRET")
    if missing["redirect_uri_missing"]:
        missing_fields.append("TIKTOK_REDIRECT_URI")
    if missing_fields:
        raise http_error(
            500,
            "tiktok_config_missing",
            "Missing TikTok config: " + ", ".join(missing_fields),
        )


def _api_base() -> str:
    return str(settings.tiktok_api_base or "https://business-api.tiktok.com/open_api/v1.3").rstrip("/")


def encode_state(payload: dict[str, Any], *, expires_seconds: int = 1800) -> str:
    now = datetime.now(timezone.utc)
    signed_payload = {
        "purpose": TIKTOK_STATE_PURPOSE,
        "payload": payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(signed_payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise ValueError("invalid_state") from exc
    if payload.get("purpose") != TIKTOK_STATE_PURPOSE:
        raise ValueError("invalid_state")
    signed_payload = payload.get("payload")
    if not isinstance(signed_payload, dict):
        raise ValueError("invalid_state")
    return signed_payload


def build_authorization_url(
    state: str,
    *,
    scope: str | None = None,
) -> str:
    _require_tiktok_config(require_secret=False)
    params = {
        "app_id": str(settings.tiktok_app_id),
        "redirect_uri": str(settings.tiktok_redirect_uri),
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{TIKTOK_AUTH_BASE_URL}?{urlencode(params)}"


def _extract_tiktok_response_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise http_error(
            502,
            "tiktok_invalid_response",
            "TikTok returned a non-JSON response.",
        ) from exc
    if not isinstance(payload, dict):
        raise http_error(
            502,
            "tiktok_invalid_response",
            "TikTok returned an unexpected response shape.",
        )
    return payload


def _raise_tiktok_api_error(
    response: requests.Response,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    response_payload = payload if isinstance(payload, dict) else _extract_tiktok_response_payload(response)
    data = response_payload.get("data") if isinstance(response_payload.get("data"), dict) else {}
    if response.ok and str(response_payload.get("code", "0")) in {"0", ""}:
        return

    message = (
        str(response_payload.get("message") or "").strip()
        or str(data.get("message") or "").strip()
        or str(response.text or "").strip()
        or "TikTok API request failed."
    )
    exc = http_error(400, "tiktok_api_error", message)
    if isinstance(exc.detail, dict):
        exc.detail["upstream_status_code"] = response.status_code
        exc.detail["tiktok_code"] = response_payload.get("code")
        exc.detail["request_id"] = (
            response_payload.get("request_id")
            or response_payload.get("requestId")
            or data.get("request_id")
            or data.get("requestId")
        )
        exc.detail["log_id"] = (
            response_payload.get("log_id")
            or response_payload.get("logId")
            or data.get("log_id")
            or data.get("logId")
        )
        exc.detail["response_body"] = _truncate_log_value(response_payload)
    raise exc


def exchange_auth_code_for_token(
    *,
    code: str | None = None,
    auth_code: str | None = None,
) -> dict[str, Any]:
    _require_tiktok_config(require_secret=True)
    normalized_code = str(code or auth_code or "").strip()
    if not normalized_code:
        raise http_error(400, "missing_auth_code", "code or auth_code is required.")

    response = requests.post(
        f"{_api_base()}/oauth2/access_token/",
        data={
            "app_id": str(settings.tiktok_app_id),
            "secret": str(settings.tiktok_secret),
            "auth_code": normalized_code,
            "grant_type": "authorized_code",
        },
        timeout=30,
    )
    payload = _extract_tiktok_response_payload(response)
    _raise_tiktok_api_error(response, payload=payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        "access_token": str(data.get("access_token") or "").strip() or None,
        "refresh_token": str(data.get("refresh_token") or "").strip() or None,
        "expires_in": data.get("expires_in"),
        "refresh_expires_in": data.get("refresh_expires_in"),
        "advertiser_ids": data.get("advertiser_ids") if isinstance(data.get("advertiser_ids"), list) else [],
        "raw": payload,
    }


def get_authorized_advertisers(access_token: str) -> list[dict[str, Any]]:
    normalized_token = str(access_token or "").strip()
    if not normalized_token:
        raise http_error(401, "missing_token", "TikTok access token not found.")

    response = requests.get(
        f"{_api_base()}/oauth2/advertiser/get/",
        headers={"Access-Token": normalized_token},
        timeout=30,
    )
    payload = _extract_tiktok_response_payload(response)
    _raise_tiktok_api_error(response, payload=payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    advertisers = data.get("list") if isinstance(data.get("list"), list) else []

    normalized: list[dict[str, Any]] = []
    for advertiser in advertisers:
        if not isinstance(advertiser, dict):
            continue
        advertiser_id = str(
            advertiser.get("advertiser_id")
            or advertiser.get("id")
            or advertiser.get("account_id")
            or ""
        ).strip()
        if not advertiser_id:
            continue
        normalized.append(
            {
                "advertiser_id": advertiser_id,
                "advertiser_name": str(
                    advertiser.get("advertiser_name")
                    or advertiser.get("name")
                    or advertiser_id
                ).strip(),
                "currency": str(advertiser.get("currency") or "").strip() or None,
                "timezone": str(advertiser.get("timezone") or "").strip() or None,
                "raw_json": advertiser,
            }
        )
    return normalized


def fetch_daily_advertiser_report(
    access_token: str,
    *,
    advertiser_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    normalized_token = str(access_token or "").strip()
    normalized_advertiser_id = str(advertiser_id or "").strip()
    if not normalized_token:
        raise http_error(401, "missing_token", "TikTok access token not found.")
    if not normalized_advertiser_id:
        raise http_error(400, "missing_advertiser_id", "advertiser_id is required.")

    request_payload = {
        "advertiser_id": normalized_advertiser_id,
        "service_type": "AUCTION",
        "report_type": "BASIC",
        "data_level": "AUCTION_ADVERTISER",
        "dimensions": ["stat_time_day"],
        "start_date": start_date,
        "end_date": end_date,
        "page": 1,
        "page_size": 1000,
    }

    requested_metrics = list(TIKTOK_CORE_REPORT_METRICS + TIKTOK_OPTIONAL_REPORT_METRICS)
    used_optional_fallback = False
    last_error: HTTPException | None = None

    for metrics in (requested_metrics, list(TIKTOK_CORE_REPORT_METRICS)):
        try:
            response = requests.post(
                f"{_api_base()}/report/integrated/get/",
                headers={"Access-Token": normalized_token},
                json={**request_payload, "metrics": metrics},
                timeout=60,
            )
            payload = _extract_tiktok_response_payload(response)
            _raise_tiktok_api_error(response, payload=payload)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            rows = data.get("list") if isinstance(data.get("list"), list) else []
            return {
                "rows": rows,
                "metrics_requested": metrics,
                "used_optional_fallback": used_optional_fallback,
                "raw": payload,
            }
        except HTTPException as exc:
            last_error = exc
            if metrics == requested_metrics:
                used_optional_fallback = True
                logger.warning(
                    "TikTok report optional metrics failed; retrying core metrics only",
                    extra={
                        "advertiser_id": normalized_advertiser_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "error": exc.detail if isinstance(exc.detail, dict) else str(exc.detail),
                    },
                )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise http_error(502, "tiktok_report_failed", "TikTok report request failed.")


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _daily_series(rows: list[dict[str, Any]], metric_key: str) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for row in rows:
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else row
        metric_value = _as_float(metrics.get(metric_key))
        date_value = str(
            dimensions.get("stat_time_day")
            or row.get("stat_time_day")
            or row.get("date")
            or ""
        ).strip()
        if not date_value:
            continue
        series.append(
            {
                "date": date_value,
                "value": None if metric_value is None else round(metric_value, 4),
            }
        )
    return series


def _series_total(series: list[dict[str, Any]]) -> float | None:
    numeric_values = [point.get("value") for point in series if isinstance(point.get("value"), (int, float))]
    if not numeric_values:
        return None
    return round(float(sum(numeric_values)), 4)


def _series_average(series: list[dict[str, Any]]) -> float | None:
    numeric_values = [point.get("value") for point in series if isinstance(point.get("value"), (int, float))]
    if not numeric_values:
        return None
    return round(float(sum(numeric_values) / len(numeric_values)), 4)


def normalize_tiktok_report_to_dataset_payload(
    *,
    advertiser_id: str,
    advertiser_name: str,
    start_date: str,
    end_date: str,
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    rows = report_payload.get("rows") if isinstance(report_payload.get("rows"), list) else []
    normalized_rows = [row for row in rows if isinstance(row, dict)]

    reach_daily = _daily_series(normalized_rows, "reach")
    impressions_daily = _daily_series(normalized_rows, "impressions")
    clicks_daily = _daily_series(normalized_rows, "clicks")
    spend_daily = _daily_series(normalized_rows, "spend")
    conversions_daily = _daily_series(normalized_rows, "conversions")
    likes_daily = _daily_series(normalized_rows, "likes")
    comments_daily = _daily_series(normalized_rows, "comments")
    shares_daily = _daily_series(normalized_rows, "shares")

    engagement_daily: list[dict[str, Any]] = []
    engagement_dates = {
        point["date"]
        for point in likes_daily + comments_daily + shares_daily
        if isinstance(point, dict) and point.get("date")
    }
    for date_value in sorted(engagement_dates):
        values: list[float] = []
        for series in (likes_daily, comments_daily, shares_daily):
            point = next((item for item in series if item.get("date") == date_value), None)
            if point is not None and isinstance(point.get("value"), (int, float)):
                values.append(float(point["value"]))
        engagement_daily.append(
            {
                "date": date_value,
                "value": round(sum(values), 4) if values else None,
            }
        )

    ctr_series = _daily_series(normalized_rows, "ctr")
    cpc_series = _daily_series(normalized_rows, "cpc")
    cpm_series = _daily_series(normalized_rows, "cpm")

    warnings: list[str] = []
    if not reach_daily:
        warnings.append("reach_unavailable")
    if not engagement_daily:
        warnings.append("engagement_unavailable")

    return {
        "integration_type": "tiktok_ads",
        "account_id": advertiser_id,
        "page_id": advertiser_id,
        "account_name": advertiser_name,
        "page_name": advertiser_name,
        "timeframe": {
            "key": "custom",
            "label": f"{start_date} to {end_date}",
            "preset": "custom",
            "since": start_date,
            "until": end_date,
            "requested_since": start_date,
            "requested_until": end_date,
        },
        "reach": _series_total(reach_daily),
        "impressions": _series_total(impressions_daily),
        "engagement": _series_total(engagement_daily),
        "link_clicks": _series_total(clicks_daily),
        "spend": _series_total(spend_daily),
        "conversions": _series_total(conversions_daily),
        "normalized_report_metrics": {
            "reach_total": _series_total(reach_daily),
            "reach_daily": reach_daily,
            "impressions_total": _series_total(impressions_daily),
            "impressions_daily": impressions_daily,
            "engagement_total": _series_total(engagement_daily),
            "engagement_daily": engagement_daily,
            "link_clicks_total": _series_total(clicks_daily),
            "link_clicks_daily": clicks_daily,
            "spend_total": _series_total(spend_daily),
            "spend_daily": spend_daily,
            "conversions_total": _series_total(conversions_daily),
            "conversions_daily": conversions_daily,
            "ctr": _series_average(ctr_series),
            "cpc": _series_average(cpc_series),
            "cpm": _series_average(cpm_series),
        },
        "raw_json": {
            "report": report_payload.get("raw"),
            "rows_count": len(normalized_rows),
            "metrics_requested": report_payload.get("metrics_requested"),
            "used_optional_fallback": bool(report_payload.get("used_optional_fallback")),
        },
        "debug": {
            "warnings": warnings,
            "likes_daily": likes_daily,
            "comments_daily": comments_daily,
            "shares_daily": shares_daily,
        },
    }
