from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from .config import settings
from .crypto import decrypt_secret
from .models import Integration, IntegrationAccount, IntegrationToken, MetaAdAccount, MetaPage

logger = logging.getLogger(__name__)

FACEBOOK_PROVIDER = "facebook_pages"
INSTAGRAM_PROVIDER = "instagram_business"
META_ADS_PROVIDER = "meta_ads"
META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
META_RECORD_TYPE_INSTAGRAM_ACCOUNT = "instagram_account"

FACEBOOK_PAGE_FIELDS = [
    "id",
    "name",
    "username",
    "link",
    "category",
    "fan_count",
    "followers_count",
    "picture{url}",
]
FACEBOOK_PAGE_INSIGHT_METRICS = [
    "page_posts_impressions_organic",
    "page_post_engagements",
    "page_actions_post_reactions_total",
    "page_views_total",
    "page_fans",
    "page_fan_adds",
    "page_fan_removes",
    "page_video_views",
    "page_video_views_paid",
    "page_video_views_organic",
    "page_impressions",
    "page_impressions_unique",
]
FACEBOOK_POST_FIELDS = [
    "id",
    "message",
    "created_time",
    "permalink_url",
    "full_picture",
    "attachments",
    "shares",
    "comments.summary(true)",
    "reactions.summary(true)",
]
FACEBOOK_POST_INSIGHT_METRICS = [
    "post_impressions",
    "post_impressions_unique",
    "post_impressions_paid",
    "post_impressions_organic",
    "post_engaged_users",
    "post_clicks",
    "post_reactions_by_type_total",
    "post_video_views",
]

INSTAGRAM_ACCOUNT_FIELDS = [
    "id",
    "username",
    "name",
    "profile_picture_url",
    "followers_count",
    "follows_count",
    "media_count",
    "website",
    "biography",
]
INSTAGRAM_ACCOUNT_INSIGHT_METRICS = [
    "reach",
    "impressions",
    "views",
    "profile_views",
    "website_clicks",
    "accounts_engaged",
    "total_interactions",
    "follower_count",
    "online_followers",
    "audience_country",
    "audience_city",
    "audience_gender_age",
]
INSTAGRAM_MEDIA_FIELDS = [
    "id",
    "caption",
    "media_type",
    "media_product_type",
    "timestamp",
    "permalink",
    "thumbnail_url",
    "like_count",
    "comments_count",
]
INSTAGRAM_MEDIA_INSIGHT_METRICS = [
    "reach",
    "views",
    "plays",
    "saved",
    "likes",
    "comments",
    "shares",
    "total_interactions",
    "replies",
]

META_AD_ACCOUNT_FIELDS = [
    "account_id",
    "name",
    "currency",
    "timezone_name",
    "account_status",
    "amount_spent",
]
META_AD_CAMPAIGN_FIELDS = [
    "id",
    "name",
    "status",
    "objective",
    "buying_type",
    "created_time",
    "updated_time",
]
META_AD_ADSET_FIELDS = [
    "id",
    "name",
    "status",
    "optimization_goal",
    "billing_event",
    "daily_budget",
    "lifetime_budget",
]
META_AD_AD_FIELDS = [
    "id",
    "name",
    "status",
    "creative",
]
META_AD_INSIGHT_FIELDS = [
    "spend",
    "impressions",
    "reach",
    "frequency",
    "clicks",
    "inline_link_clicks",
    "outbound_clicks",
    "ctr",
    "cpc",
    "cpm",
    "cpp",
    "actions",
    "cost_per_action_type",
    "conversions",
    "conversion_values",
    "purchase_roas",
    "website_purchase_roas",
    "video_plays",
    "video_p25_watched_actions",
    "video_p50_watched_actions",
    "video_p75_watched_actions",
    "video_p95_watched_actions",
    "video_p100_watched_actions",
]
META_AD_BREAKDOWNS = [
    "publisher_platform",
    "platform_position",
    "device_platform",
    "age",
    "gender",
    "country",
]
META_AD_LEVELS = ["account", "campaign", "adset", "ad"]

RECOMMENDED_REPORT_METRICS = {
    FACEBOOK_PROVIDER: [
        "fan_count",
        "followers_count",
        "page_posts_impressions_organic",
        "page_post_engagements",
        "page_actions_post_reactions_total",
        "page_views_total",
        "page_fan_adds",
        "page_fan_removes",
    ],
    INSTAGRAM_PROVIDER: [
        "followers_count",
        "media_count",
        "reach",
        "views",
        "profile_views",
        "website_clicks",
        "accounts_engaged",
        "total_interactions",
        "likes",
        "comments",
        "shares",
        "saved",
    ],
    META_ADS_PROVIDER: [
        "spend",
        "impressions",
        "reach",
        "clicks",
        "inline_link_clicks",
        "ctr",
        "cpc",
        "cpm",
        "cpp",
        "actions",
        "cost_per_action_type",
        "purchase_roas",
    ],
}


def _truncate(value: Any, limit: int = 1200) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _safe_graph_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"access_token", "token", "refresh_token", "authorization_code", "code"}:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _safe_graph_payload(value)
        return redacted
    if isinstance(payload, list):
        return [_safe_graph_payload(item) for item in payload[:10]]
    return payload


def _meta_graph_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    resolved_base_url = (base_url or f"https://graph.facebook.com/{settings.meta_api_version}").rstrip("/")
    url = f"{resolved_base_url}/{path.lstrip('/')}"
    request_params = dict(params or {})
    request_params["access_token"] = access_token
    response = requests.get(url, params=request_params, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    error_code = error_payload.get("code") if isinstance(error_payload, dict) else None
    error_message = str(error_payload.get("message") or "").strip() if isinstance(error_payload, dict) else str(payload or "").strip()
    return {
        "ok": response.status_code == 200,
        "status_code": response.status_code,
        "payload": payload if isinstance(payload, dict) else {"raw_text": _truncate(payload)},
        "error_code": error_code,
        "error_message": error_message or None,
    }


def _latest_token_for_account(db: Session, account_id: int) -> IntegrationToken | None:
    return (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .first()
    )


def _meta_token_account_external_id(integration_id: int) -> str:
    return f"__meta_token__:{integration_id}"


def _instagram_token_account_external_id(integration_id: int) -> str:
    return f"instagram_business_token_{integration_id}"


def _get_token_account(
    db: Session,
    *,
    integration_id: int,
    external_account_id: str,
) -> IntegrationAccount | None:
    return (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration_id,
            IntegrationAccount.external_account_id == external_account_id,
        )
        .first()
    )


def _resolve_facebook_access_token(db: Session, integration: Integration) -> str | None:
    token_account = _get_token_account(
        db,
        integration_id=integration.id,
        external_account_id=_meta_token_account_external_id(integration.id),
    )
    if token_account is None:
        return None
    token = _latest_token_for_account(db, token_account.id)
    return str(token.access_token or "").strip() or None if token else None


def _resolve_instagram_access_token(db: Session, integration: Integration) -> str | None:
    token_account = _get_token_account(
        db,
        integration_id=integration.id,
        external_account_id=_instagram_token_account_external_id(integration.id),
    )
    if token_account is None:
        return None
    token = _latest_token_for_account(db, token_account.id)
    if token is None or not str(token.access_token or "").strip():
        return None
    try:
        return decrypt_secret(token.access_token)
    except Exception:
        logger.warning("META_DATA_CATALOG_INSTAGRAM_TOKEN_DECRYPT_FAILED integration_id=%s", integration.id)
        return None


def _resolve_meta_ads_access_token(db: Session, integration: Integration) -> str | None:
    token_account = _get_token_account(
        db,
        integration_id=integration.id,
        external_account_id=_meta_token_account_external_id(integration.id),
    )
    if token_account is None:
        return None
    token = _latest_token_for_account(db, token_account.id)
    return str(token.access_token or "").strip() or None if token else None


def _availability_status(response: dict[str, Any]) -> str:
    return "available" if response["ok"] else "unavailable"


def _availability_reason(*, error_code: Any, error_message: str | None) -> str | None:
    message = str(error_message or "").strip()
    normalized = message.lower()
    if str(error_code or "") == "100":
        return "invalid_metric"
    if "permission" in normalized or "permissions" in normalized:
        return "missing_permission"
    return None


def _append_record(
    records: list[dict[str, Any]],
    *,
    provider: str,
    workspace_id: int,
    integration_id: int | None,
    endpoint_type: str,
    metric_name: str,
    normalized_field: str,
    raw_available: bool,
    availability_status: str,
    status_code: int | None,
    error_code: Any = None,
    error_message: str | None = None,
    asset_id: str | None = None,
    asset_name: str | None = None,
    sample_value: Any = None,
    reason: str | None = None,
) -> None:
    record = {
        "provider": provider,
        "workspace_id": workspace_id,
        "integration_id": integration_id,
        "endpoint_type": endpoint_type,
        "metric_name": metric_name,
        "normalized_field": normalized_field,
        "raw_available": raw_available,
        "availability_status": availability_status,
        "status_code": status_code,
        "error_code": error_code,
        "error_message": error_message,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "reason": reason,
        "sample_value": _truncate(_safe_graph_payload(sample_value)),
    }
    records.append(record)
    event = (
        "META_DATA_CATALOG_METRIC_AVAILABLE"
        if availability_status == "available"
        else "META_DATA_CATALOG_METRIC_UNAVAILABLE"
    )
    logger.info(
        "%s %s",
        event,
        json.dumps(
            {
                "provider": provider,
                "metric_name": metric_name,
                "endpoint_type": endpoint_type,
                "normalized_field": normalized_field,
                "status_code": status_code,
                "error_code": error_code,
                "reason": reason,
                "asset_id": asset_id,
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )


def _summary_for_provider(provider: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    provider_rows = [row for row in records if row["provider"] == provider]
    available_rows = [row for row in provider_rows if row["availability_status"] == "available"]
    unavailable_rows = [row for row in provider_rows if row["availability_status"] == "unavailable"]
    missing_permissions = [
        row
        for row in provider_rows
        if str(row.get("reason") or "") == "missing_permission"
    ]
    invalid_metrics = [
        row
        for row in provider_rows
        if str(row.get("reason") or "") == "invalid_metric"
    ]
    recommended = [
        metric
        for metric in RECOMMENDED_REPORT_METRICS.get(provider, [])
        if any(
            row["normalized_field"] == metric
            and row["availability_status"] == "available"
            and bool(row["raw_available"])
            for row in provider_rows
        )
    ]
    return {
        "total_available": len(available_rows),
        "total_unavailable": len(unavailable_rows),
        "missing_permissions": len(missing_permissions),
        "invalid_metrics": len(invalid_metrics),
        "recommended_report_metrics": recommended,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "provider",
        "workspace_id",
        "integration_id",
        "endpoint_type",
        "metric_name",
        "normalized_field",
        "raw_available",
        "availability_status",
        "status_code",
        "error_code",
        "error_message",
        "asset_id",
        "asset_name",
        "reason",
        "sample_value",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _time_window() -> tuple[str, str]:
    today = date.today()
    since = (today - timedelta(days=30)).isoformat()
    until = today.isoformat()
    return since, until


def _audit_facebook_pages(
    db: Session,
    *,
    workspace_id: int,
    integration: Integration | None,
    records: list[dict[str, Any]],
) -> None:
    provider = FACEBOOK_PROVIDER
    logger.info(
        "META_DATA_CATALOG_PROVIDER_STARTED %s",
        json.dumps({"provider": provider, "workspace_id": workspace_id}, ensure_ascii=False, sort_keys=True),
    )
    if integration is None:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=None,
            endpoint_type="integration",
            metric_name="integration",
            normalized_field="integration_status",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Facebook Pages integration not found.",
            reason="integration_missing",
        )
        return

    access_token = _resolve_facebook_access_token(db, integration)
    if not access_token:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="token",
            metric_name="access_token",
            normalized_field="access_token",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Facebook Pages token not found.",
            reason="missing_token",
        )
        return

    pages = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
        )
        .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
        .all()
    )
    if not pages:
        pages_response = _meta_graph_get(
            "me/accounts",
            access_token=access_token,
            params={"fields": "id,name,access_token,tasks"},
        )
        live_pages = pages_response["payload"].get("data", []) if isinstance(pages_response["payload"], dict) else []
        for page in live_pages:
            if not isinstance(page, dict):
                continue
            pages.append(
                MetaPage(
                    integration_id=integration.id,
                    user_id=None,
                    record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                    page_id=str(page.get("id") or ""),
                    name=str(page.get("name") or page.get("id") or ""),
                    page_access_token=str(page.get("access_token") or "").strip() or None,
                )
            )

    since, until = _time_window()
    for page in pages:
        page_access_token = str(page.page_access_token or "").strip() or access_token
        page_fields_response = _meta_graph_get(
            page.page_id,
            access_token=page_access_token,
            params={"fields": ",".join(FACEBOOK_PAGE_FIELDS)},
        )
        payload = page_fields_response["payload"] if isinstance(page_fields_response["payload"], dict) else {}
        for field_name in FACEBOOK_PAGE_FIELDS:
            normalized_field = field_name.replace("{url}", "").replace(".summary(true)", "")
            raw_available = normalized_field in payload or field_name in payload
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="page_fields",
                metric_name=normalized_field,
                normalized_field=normalized_field,
                raw_available=raw_available,
                availability_status=_availability_status(page_fields_response),
                status_code=page_fields_response["status_code"],
                error_code=page_fields_response["error_code"],
                error_message=page_fields_response["error_message"],
                asset_id=page.page_id,
                asset_name=page.name,
                sample_value=payload.get(normalized_field) if isinstance(payload, dict) else None,
                reason=_availability_reason(
                    error_code=page_fields_response["error_code"],
                    error_message=page_fields_response["error_message"],
                ),
            )

        for metric in FACEBOOK_PAGE_INSIGHT_METRICS:
            metric_response = _meta_graph_get(
                f"{page.page_id}/insights",
                access_token=page_access_token,
                params={"metric": metric, "period": "day", "since": since, "until": until},
            )
            rows = metric_response["payload"].get("data", []) if isinstance(metric_response["payload"], dict) else []
            raw_available = bool(rows)
            if metric in {"page_impressions", "page_impressions_unique"} and metric_response["error_code"] == 100:
                reason = "invalid_metric"
            else:
                reason = _availability_reason(
                    error_code=metric_response["error_code"],
                    error_message=metric_response["error_message"],
                )
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="page_insights",
                metric_name=metric,
                normalized_field=metric,
                raw_available=raw_available,
                availability_status=_availability_status(metric_response),
                status_code=metric_response["status_code"],
                error_code=metric_response["error_code"],
                error_message=metric_response["error_message"],
                asset_id=page.page_id,
                asset_name=page.name,
                sample_value=rows[:1],
                reason=reason,
            )

        posts_response = _meta_graph_get(
            f"{page.page_id}/posts",
            access_token=page_access_token,
            params={"fields": ",".join(FACEBOOK_POST_FIELDS), "limit": 10},
        )
        posts = posts_response["payload"].get("data", []) if isinstance(posts_response["payload"], dict) else []
        if posts_response["ok"] and not posts:
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="page_posts",
                metric_name="posts_collection",
                normalized_field="posts_collection",
                raw_available=False,
                availability_status="available",
                status_code=posts_response["status_code"],
                asset_id=page.page_id,
                asset_name=page.name,
                sample_value=[],
            )
        for field_name in FACEBOOK_POST_FIELDS:
            normalized_field = field_name.replace(".summary(true)", "")
            raw_available = any(isinstance(post, dict) and normalized_field in post for post in posts)
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="page_posts",
                metric_name=normalized_field,
                normalized_field=normalized_field,
                raw_available=raw_available,
                availability_status=_availability_status(posts_response),
                status_code=posts_response["status_code"],
                error_code=posts_response["error_code"],
                error_message=posts_response["error_message"],
                asset_id=page.page_id,
                asset_name=page.name,
                sample_value=posts[:1],
                reason=_availability_reason(
                    error_code=posts_response["error_code"],
                    error_message=posts_response["error_message"],
                ),
            )
        for post in posts[:5]:
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("id") or "").strip()
            if not post_id:
                continue
            for metric in FACEBOOK_POST_INSIGHT_METRICS:
                post_metric_response = _meta_graph_get(
                    f"{post_id}/insights",
                    access_token=page_access_token,
                    params={"metric": metric},
                )
                rows = post_metric_response["payload"].get("data", []) if isinstance(post_metric_response["payload"], dict) else []
                _append_record(
                    records,
                    provider=provider,
                    workspace_id=workspace_id,
                    integration_id=integration.id,
                    endpoint_type="post_insights",
                    metric_name=metric,
                    normalized_field=metric,
                    raw_available=bool(rows),
                    availability_status=_availability_status(post_metric_response),
                    status_code=post_metric_response["status_code"],
                    error_code=post_metric_response["error_code"],
                    error_message=post_metric_response["error_message"],
                    asset_id=post_id,
                    asset_name=str(post.get("message") or post_id)[:120],
                    sample_value=rows[:1],
                    reason=_availability_reason(
                        error_code=post_metric_response["error_code"],
                        error_message=post_metric_response["error_message"],
                    ),
                )


def _audit_instagram_business(
    db: Session,
    *,
    workspace_id: int,
    integration: Integration | None,
    records: list[dict[str, Any]],
) -> None:
    provider = INSTAGRAM_PROVIDER
    logger.info(
        "META_DATA_CATALOG_PROVIDER_STARTED %s",
        json.dumps({"provider": provider, "workspace_id": workspace_id}, ensure_ascii=False, sort_keys=True),
    )
    if integration is None:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=None,
            endpoint_type="integration",
            metric_name="integration",
            normalized_field="integration_status",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Instagram Business integration not found.",
            reason="integration_missing",
        )
        return

    access_token = _resolve_instagram_access_token(db, integration)
    if not access_token:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="token",
            metric_name="access_token",
            normalized_field="access_token",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Instagram Business token not found.",
            reason="missing_token",
        )
        return

    account_record = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id != _instagram_token_account_external_id(integration.id),
        )
        .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
        .first()
    )
    instagram_account_id = str(account_record.external_account_id or "").strip() if account_record else ""
    if not instagram_account_id:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="account",
            metric_name="account_id",
            normalized_field="account_id",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Instagram Business account not found.",
            reason="account_missing",
        )
        return

    since, until = _time_window()
    account_response = _meta_graph_get(
        instagram_account_id,
        access_token=access_token,
        params={"fields": ",".join(INSTAGRAM_ACCOUNT_FIELDS)},
    )
    payload = account_response["payload"] if isinstance(account_response["payload"], dict) else {}
    for field_name in INSTAGRAM_ACCOUNT_FIELDS:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="account_fields",
            metric_name=field_name,
            normalized_field=field_name,
            raw_available=field_name in payload,
            availability_status=_availability_status(account_response),
            status_code=account_response["status_code"],
            error_code=account_response["error_code"],
            error_message=account_response["error_message"],
            asset_id=instagram_account_id,
            asset_name=str(payload.get("username") or instagram_account_id),
            sample_value=payload.get(field_name) if isinstance(payload, dict) else None,
            reason=_availability_reason(
                error_code=account_response["error_code"],
                error_message=account_response["error_message"],
            ),
        )

    for metric in INSTAGRAM_ACCOUNT_INSIGHT_METRICS:
        metric_response = _meta_graph_get(
            f"{instagram_account_id}/insights",
            access_token=access_token,
            params={"metric": metric, "period": "day", "since": since, "until": until},
        )
        rows = metric_response["payload"].get("data", []) if isinstance(metric_response["payload"], dict) else []
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="account_insights",
            metric_name=metric,
            normalized_field=metric,
            raw_available=bool(rows),
            availability_status=_availability_status(metric_response),
            status_code=metric_response["status_code"],
            error_code=metric_response["error_code"],
            error_message=metric_response["error_message"],
            asset_id=instagram_account_id,
            asset_name=str(payload.get("username") or instagram_account_id),
            sample_value=rows[:1],
            reason=_availability_reason(
                error_code=metric_response["error_code"],
                error_message=metric_response["error_message"],
            ),
        )

    media_response = _meta_graph_get(
        f"{instagram_account_id}/media",
        access_token=access_token,
        params={"fields": ",".join(INSTAGRAM_MEDIA_FIELDS), "limit": 10},
    )
    media_items = media_response["payload"].get("data", []) if isinstance(media_response["payload"], dict) else []
    for field_name in INSTAGRAM_MEDIA_FIELDS:
        raw_available = any(isinstance(item, dict) and field_name in item for item in media_items)
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="media_fields",
            metric_name=field_name,
            normalized_field=field_name,
            raw_available=raw_available,
            availability_status=_availability_status(media_response),
            status_code=media_response["status_code"],
            error_code=media_response["error_code"],
            error_message=media_response["error_message"],
            asset_id=instagram_account_id,
            asset_name=str(payload.get("username") or instagram_account_id),
            sample_value=media_items[:1],
            reason=_availability_reason(
                error_code=media_response["error_code"],
                error_message=media_response["error_message"],
            ),
        )
    for media_item in media_items[:5]:
        if not isinstance(media_item, dict):
            continue
        media_id = str(media_item.get("id") or "").strip()
        if not media_id:
            continue
        for metric in INSTAGRAM_MEDIA_INSIGHT_METRICS:
            media_metric_response = _meta_graph_get(
                f"{media_id}/insights",
                access_token=access_token,
                params={"metric": metric},
            )
            rows = media_metric_response["payload"].get("data", []) if isinstance(media_metric_response["payload"], dict) else []
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="media_insights",
                metric_name=metric,
                normalized_field=metric,
                raw_available=bool(rows),
                availability_status=_availability_status(media_metric_response),
                status_code=media_metric_response["status_code"],
                error_code=media_metric_response["error_code"],
                error_message=media_metric_response["error_message"],
                asset_id=media_id,
                asset_name=str(media_item.get("media_type") or media_id),
                sample_value=rows[:1],
                reason=_availability_reason(
                    error_code=media_metric_response["error_code"],
                    error_message=media_metric_response["error_message"],
                ),
            )


def _normalize_ad_account_id(value: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized.startswith("act_") else f"act_{normalized}"


def _audit_meta_ads(
    db: Session,
    *,
    workspace_id: int,
    integration: Integration | None,
    records: list[dict[str, Any]],
) -> None:
    provider = META_ADS_PROVIDER
    logger.info(
        "META_DATA_CATALOG_PROVIDER_STARTED %s",
        json.dumps({"provider": provider, "workspace_id": workspace_id}, ensure_ascii=False, sort_keys=True),
    )
    if integration is None:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=None,
            endpoint_type="integration",
            metric_name="integration",
            normalized_field="integration_status",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Meta Ads integration not found.",
            reason="integration_missing",
        )
        return

    access_token = _resolve_meta_ads_access_token(db, integration)
    if not access_token:
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="token",
            metric_name="access_token",
            normalized_field="access_token",
            raw_available=False,
            availability_status="unavailable",
            status_code=None,
            error_message="Meta Ads token not found.",
            reason="missing_token",
        )
        return

    ad_accounts_response = _meta_graph_get(
        "me/adaccounts",
        access_token=access_token,
        params={"fields": ",".join(META_AD_ACCOUNT_FIELDS), "limit": 50},
        timeout=60,
    )
    ad_accounts = ad_accounts_response["payload"].get("data", []) if isinstance(ad_accounts_response["payload"], dict) else []
    if not ad_accounts:
        stored_accounts = (
            db.query(MetaAdAccount)
            .filter(MetaAdAccount.integration_id == integration.id)
            .order_by(MetaAdAccount.updated_at.desc(), MetaAdAccount.id.desc())
            .all()
        )
        ad_accounts = [
            {
                "id": _normalize_ad_account_id(account.account_id),
                "account_id": account.account_id,
                "name": account.account_name,
                "currency": account.currency,
                "timezone_name": account.timezone_name,
                "account_status": account.account_status,
            }
            for account in stored_accounts
        ]

    for field_name in META_AD_ACCOUNT_FIELDS:
        raw_available = any(isinstance(account, dict) and field_name in account for account in ad_accounts)
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="ad_accounts",
            metric_name=field_name,
            normalized_field=field_name,
            raw_available=raw_available,
            availability_status=_availability_status(ad_accounts_response),
            status_code=ad_accounts_response["status_code"],
            error_code=ad_accounts_response["error_code"],
            error_message=ad_accounts_response["error_message"],
            sample_value=ad_accounts[:1],
            reason=_availability_reason(
                error_code=ad_accounts_response["error_code"],
                error_message=ad_accounts_response["error_message"],
            ),
        )

    since, until = _time_window()
    for account in ad_accounts[:5]:
        if not isinstance(account, dict):
            continue
        account_id = _normalize_ad_account_id(str(account.get("account_id") or account.get("id") or ""))
        if not account_id:
            continue
        account_name = str(account.get("name") or account_id)

        for endpoint_type, edge, fields in (
            ("campaigns", "campaigns", META_AD_CAMPAIGN_FIELDS),
            ("adsets", "adsets", META_AD_ADSET_FIELDS),
            ("ads", "ads", META_AD_AD_FIELDS),
        ):
            edge_response = _meta_graph_get(
                f"{account_id}/{edge}",
                access_token=access_token,
                params={"fields": ",".join(fields), "limit": 25},
                timeout=60,
            )
            data = edge_response["payload"].get("data", []) if isinstance(edge_response["payload"], dict) else []
            for field_name in fields:
                raw_available = any(isinstance(item, dict) and field_name in item for item in data)
                _append_record(
                    records,
                    provider=provider,
                    workspace_id=workspace_id,
                    integration_id=integration.id,
                    endpoint_type=endpoint_type,
                    metric_name=field_name,
                    normalized_field=field_name,
                    raw_available=raw_available,
                    availability_status=_availability_status(edge_response),
                    status_code=edge_response["status_code"],
                    error_code=edge_response["error_code"],
                    error_message=edge_response["error_message"],
                    asset_id=account_id,
                    asset_name=account_name,
                    sample_value=data[:1],
                    reason=_availability_reason(
                        error_code=edge_response["error_code"],
                        error_message=edge_response["error_message"],
                    ),
                )

        for level in META_AD_LEVELS:
            insights_response = _meta_graph_get(
                f"{account_id}/insights",
                access_token=access_token,
                params={
                    "fields": ",".join(META_AD_INSIGHT_FIELDS),
                    "level": level,
                    "time_increment": 1,
                    "since": since,
                    "until": until,
                    "limit": 25,
                },
                timeout=60,
            )
            data = insights_response["payload"].get("data", []) if isinstance(insights_response["payload"], dict) else []
            for field_name in META_AD_INSIGHT_FIELDS:
                raw_available = any(isinstance(item, dict) and field_name in item for item in data)
                _append_record(
                    records,
                    provider=provider,
                    workspace_id=workspace_id,
                    integration_id=integration.id,
                    endpoint_type=f"insights_{level}",
                    metric_name=field_name,
                    normalized_field=field_name,
                    raw_available=raw_available,
                    availability_status=_availability_status(insights_response),
                    status_code=insights_response["status_code"],
                    error_code=insights_response["error_code"],
                    error_message=insights_response["error_message"],
                    asset_id=account_id,
                    asset_name=account_name,
                    sample_value=data[:1],
                    reason=_availability_reason(
                        error_code=insights_response["error_code"],
                        error_message=insights_response["error_message"],
                    ),
                )

        for breakdown in META_AD_BREAKDOWNS:
            breakdown_response = _meta_graph_get(
                f"{account_id}/insights",
                access_token=access_token,
                params={
                    "fields": "spend,impressions,reach,clicks",
                    "breakdowns": breakdown,
                    "level": "account",
                    "since": since,
                    "until": until,
                    "limit": 25,
                },
                timeout=60,
            )
            data = breakdown_response["payload"].get("data", []) if isinstance(breakdown_response["payload"], dict) else []
            _append_record(
                records,
                provider=provider,
                workspace_id=workspace_id,
                integration_id=integration.id,
                endpoint_type="insights_breakdown",
                metric_name=breakdown,
                normalized_field=breakdown,
                raw_available=bool(data),
                availability_status=_availability_status(breakdown_response),
                status_code=breakdown_response["status_code"],
                error_code=breakdown_response["error_code"],
                error_message=breakdown_response["error_message"],
                asset_id=account_id,
                asset_name=account_name,
                sample_value=data[:1],
                reason=_availability_reason(
                    error_code=breakdown_response["error_code"],
                    error_message=breakdown_response["error_message"],
                ),
            )

        time_increment_response = _meta_graph_get(
            f"{account_id}/insights",
            access_token=access_token,
            params={
                "fields": "spend,impressions,reach,clicks",
                "level": "account",
                "time_increment": 1,
                "since": since,
                "until": until,
                "limit": 40,
            },
            timeout=60,
        )
        data = time_increment_response["payload"].get("data", []) if isinstance(time_increment_response["payload"], dict) else []
        _append_record(
            records,
            provider=provider,
            workspace_id=workspace_id,
            integration_id=integration.id,
            endpoint_type="insights_time_increment",
            metric_name="daily_series",
            normalized_field="daily_series",
            raw_available=bool(data),
            availability_status=_availability_status(time_increment_response),
            status_code=time_increment_response["status_code"],
            error_code=time_increment_response["error_code"],
            error_message=time_increment_response["error_message"],
            asset_id=account_id,
            asset_name=account_name,
            sample_value=data[:2],
            reason=_availability_reason(
                error_code=time_increment_response["error_code"],
                error_message=time_increment_response["error_message"],
            ),
        )


def run_meta_data_catalog_audit(
    db: Session,
    *,
    workspace_id: int,
    providers: list[str] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    requested_providers = providers or [FACEBOOK_PROVIDER, INSTAGRAM_PROVIDER, META_ADS_PROVIDER]
    started_at = datetime.now(UTC)
    logger.info(
        "META_DATA_CATALOG_STARTED %s",
        json.dumps({"workspace_id": workspace_id, "providers": requested_providers}, ensure_ascii=False, sort_keys=True),
    )
    records: list[dict[str, Any]] = []

    facebook_integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "meta")
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .first()
    )
    instagram_integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "instagram_business")
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .first()
    )
    meta_ads_integration = (
        db.query(Integration)
        .filter(Integration.workspace_id == workspace_id, Integration.provider == "meta_ads")
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .first()
    )

    for provider in requested_providers:
        if provider == FACEBOOK_PROVIDER:
            _audit_facebook_pages(db, workspace_id=workspace_id, integration=facebook_integration, records=records)
        elif provider == INSTAGRAM_PROVIDER:
            _audit_instagram_business(db, workspace_id=workspace_id, integration=instagram_integration, records=records)
        elif provider == META_ADS_PROVIDER:
            _audit_meta_ads(db, workspace_id=workspace_id, integration=meta_ads_integration, records=records)

    resolved_output_dir = Path(output_dir or Path.cwd() / "tmp")
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = resolved_output_dir / f"meta_data_catalog_{workspace_id}_{stamp}.json"
    csv_path = resolved_output_dir / f"meta_data_catalog_{workspace_id}_{stamp}.csv"

    provider_summary = {
        FACEBOOK_PROVIDER: _summary_for_provider(FACEBOOK_PROVIDER, records),
        INSTAGRAM_PROVIDER: _summary_for_provider(INSTAGRAM_PROVIDER, records),
        META_ADS_PROVIDER: _summary_for_provider(META_ADS_PROVIDER, records),
    }
    summary = {
        "total_available": sum(item["total_available"] for item in provider_summary.values()),
        "total_unavailable": sum(item["total_unavailable"] for item in provider_summary.values()),
        "missing_permissions": sum(item["missing_permissions"] for item in provider_summary.values()),
        "invalid_metrics": sum(item["invalid_metrics"] for item in provider_summary.values()),
        "recommended_report_metrics_by_provider": {
            provider: item["recommended_report_metrics"]
            for provider, item in provider_summary.items()
        },
    }
    result = {
        "workspace_id": workspace_id,
        "generated_at": started_at.isoformat(),
        "providers": requested_providers,
        "summary": summary,
        "provider_summary": provider_summary,
        "records": records,
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_csv(csv_path, records)
    logger.info(
        "META_DATA_CATALOG_COMPLETED %s",
        json.dumps(
            {
                "workspace_id": workspace_id,
                "json_path": str(json_path),
                "csv_path": str(csv_path),
                "total_available": summary["total_available"],
                "total_unavailable": summary["total_unavailable"],
                "missing_permissions": summary["missing_permissions"],
                "invalid_metrics": summary["invalid_metrics"],
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )
    return result
