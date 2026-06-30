from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from .crypto import decrypt_secret
from .integrations.instagram_business import get_missing_instagram_business_config_fields
from .integrations.meta_ads import get_meta_ads_config_snapshot
from .db import engine
from .models import Integration, IntegrationAccount, IntegrationToken, MetaAdAccount, MetaPage

logger = logging.getLogger(__name__)

FACEBOOK_PROVIDER = "facebook_pages"
INSTAGRAM_PROVIDER = "instagram_business"
META_ADS_PROVIDER = "meta_ads"

META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
META_RECORD_TYPE_INSTAGRAM_ACCOUNT = "instagram_account"

FACEBOOK_READY_METRICS = [
    "page_posts_impressions_organic",
    "page_post_engagements",
    "page_actions_post_reactions_total",
    "page_views_total",
    "followers_count",
    "fan_count",
]
INSTAGRAM_READY_METRICS = [
    "reach",
    "views",
    "profile_views",
    "website_clicks",
    "accounts_engaged",
    "total_interactions",
]
META_ADS_READY_METRICS = [
    "spend",
    "impressions",
    "reach",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "actions",
]


def _truncate(value: Any, limit: int = 400) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _safe_sample(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"access_token", "refresh_token", "token", "authorization_code", "code"}:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _safe_sample(item)
        return redacted
    if isinstance(value, list):
        return [_safe_sample(item) for item in value[:3]]
    return value


def _normalize_error_message(value: Any) -> str | None:
    text = str(value or "").strip()
    return _truncate(text, limit=240) if text else None


def _meta_token_account_external_id(integration_id: int) -> str:
    return f"__meta_token__:{integration_id}"


def _instagram_token_account_external_id(integration_id: int) -> str:
    return f"instagram_business_token_{integration_id}"


def _latest_token_for_account(db: Session, account_id: int) -> IntegrationToken | None:
    return (
        db.query(IntegrationToken)
        .filter(IntegrationToken.account_id == account_id)
        .order_by(IntegrationToken.updated_at.desc(), IntegrationToken.id.desc())
        .first()
    )


def _table_available(table_name: str) -> bool:
    from sqlalchemy import inspect
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return table_name in set(inspect(engine).get_table_names())
    except SQLAlchemyError:
        return False


def _time_window() -> tuple[str, str]:
    today = date.today()
    return ((today - timedelta(days=30)).isoformat(), today.isoformat())


def _metric_status(status_code: int | None, error_code: Any, error_message: str | None, *, has_data: bool) -> str:
    if status_code == 200 and has_data:
        return "available"
    if str(error_code or "") == "100":
        return "invalid_metric"
    normalized = str(error_message or "").lower()
    if "permission" in normalized or "permissions" in normalized:
        return "missing_permission"
    if str(error_code or "") in {"190", "102", "104"} or status_code == 401:
        return "auth_error"
    if status_code == 200:
        return "unavailable"
    return "unexpected_error"


def _reason_for_status(status: str, *, metric_name: str | None = None, missing: list[str] | None = None) -> str:
    if status == "config_missing":
        return f"OAuth config is incomplete: {', '.join(missing or [])}."
    if status == "no_assets":
        return "No authorized assets were found for this provider."
    if status == "no_token":
        return "No stored access token was found for this provider."
    if status == "missing_permission":
        return f"Permission is missing for {metric_name or 'this metric'}."
    if status == "invalid_metric":
        return f"Meta rejected {metric_name or 'this metric'} as invalid for this asset."
    if status == "auth_error":
        return "The stored access token could not be used to read this asset."
    if status == "available":
        return "Metric is available."
    if status == "unexpected_error":
        return "Meta returned an unexpected error."
    return "Metric is unavailable."


def _graph_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    from .config import settings

    url = f"https://graph.facebook.com/{settings.meta_api_version}/{path.lstrip('/')}"
    request_params = dict(params or {})
    request_params["access_token"] = access_token
    response = requests.get(url, params=request_params, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": str(response.text or "")}
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    error_code = error_payload.get("code") if isinstance(error_payload, dict) else None
    error_message = (
        str(error_payload.get("message") or "").strip()
        if isinstance(error_payload, dict)
        else str(payload or "").strip()
    )
    return {
        "status_code": response.status_code,
        "payload": payload if isinstance(payload, dict) else {"raw_text": str(payload)},
        "error_code": error_code,
        "error_message": _normalize_error_message(error_message),
    }


def _group_details(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {
        FACEBOOK_PROVIDER: [],
        INSTAGRAM_PROVIDER: [],
        META_ADS_PROVIDER: [],
    }
    for row in rows:
        grouped.setdefault(str(row["provider"]), []).append(row)
    return grouped


def _base_row(
    *,
    provider: str,
    integration: Integration | None,
    record_type: str | None,
    token_present: bool,
    token_decrypt_ok: bool,
    asset_count: int,
    asset_ids: list[str],
    asset_names: list[str],
    endpoint_type: str,
    metric_name: str | None,
    availability_status: str,
    status_code: int | None = None,
    error_code: Any = None,
    error_message: str | None = None,
    sample_value: Any = None,
    missing: list[str] | None = None,
    raw_available: bool | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "integration_id": integration.id if integration else None,
        "db_provider": integration.provider if integration else None,
        "record_type": record_type,
        "status": str(integration.status or "").strip() if integration else "missing",
        "token_present": token_present,
        "token_decrypt_ok": token_decrypt_ok,
        "asset_count": asset_count,
        "asset_ids": asset_ids[:5],
        "asset_names": asset_names[:5],
        "endpoint_type": endpoint_type,
        "metric_name": metric_name,
        "status_code": status_code,
        "availability_status": availability_status,
        "error_code": error_code,
        "error_message": error_message,
        "reason": _reason_for_status(
            availability_status,
            metric_name=metric_name,
            missing=missing,
        ),
        "missing": missing or [],
        "raw_available": raw_available,
        "sample_value": _safe_sample(sample_value),
    }


def _log_provider_found(provider: str, integrations: list[Integration]) -> None:
    logger.info(
        "META_DATA_CATALOG_PROVIDER_INTEGRATIONS_FOUND %s",
        json.dumps(
            {
                "provider": provider,
                "integration_ids": [integration.id for integration in integrations],
                "db_providers": [integration.provider for integration in integrations],
                "statuses": [integration.status for integration in integrations],
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )


def _log_assets_found(provider: str, integration: Integration | None, asset_ids: list[str], asset_names: list[str]) -> None:
    logger.info(
        "META_DATA_CATALOG_ASSETS_FOUND %s",
        json.dumps(
            {
                "provider": provider,
                "integration_id": integration.id if integration else None,
                "asset_count": len(asset_ids),
                "asset_ids": asset_ids[:5],
                "asset_names": asset_names[:5],
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )


def _log_metric_tested(row: dict[str, Any]) -> None:
    logger.info(
        "META_DATA_CATALOG_METRIC_TESTED %s",
        json.dumps(
            {
                "provider": row["provider"],
                "integration_id": row["integration_id"],
                "endpoint_type": row["endpoint_type"],
                "metric_name": row["metric_name"],
                "status_code": row["status_code"],
                "availability_status": row["availability_status"],
                "error_code": row["error_code"],
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )


def _resolve_facebook_integrations(db: Session, workspace_id: int) -> list[Integration]:
    return (
        db.query(Integration)
        .filter(
            Integration.workspace_id == workspace_id,
            Integration.provider.in_(["meta", "facebook_pages"]),
        )
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )


def _resolve_instagram_sources(db: Session, workspace_id: int) -> tuple[list[Integration], list[Integration]]:
    direct = (
        db.query(Integration)
        .filter(
            Integration.workspace_id == workspace_id,
            Integration.provider == "instagram_business",
        )
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )
    legacy = (
        db.query(Integration)
        .filter(
            Integration.workspace_id == workspace_id,
            Integration.provider.in_(["meta", "facebook_pages"]),
        )
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )
    return direct, legacy


def _resolve_meta_ads_integrations(db: Session, workspace_id: int) -> list[Integration]:
    return (
        db.query(Integration)
        .filter(
            Integration.workspace_id == workspace_id,
            Integration.provider == "meta_ads",
        )
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )


def _resolve_meta_token(db: Session, integration: Integration) -> tuple[bool, bool, str | None]:
    token_account = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == _meta_token_account_external_id(integration.id),
        )
        .first()
    )
    if token_account is None:
        return False, False, None
    token = _latest_token_for_account(db, token_account.id)
    if token is None or not str(token.access_token or "").strip():
        return False, False, None
    return True, True, str(token.access_token)


def _resolve_instagram_token(db: Session, integration: Integration) -> tuple[bool, bool, str | None]:
    token_account = (
        db.query(IntegrationAccount)
        .filter(
            IntegrationAccount.integration_id == integration.id,
            IntegrationAccount.external_account_id == _instagram_token_account_external_id(integration.id),
        )
        .first()
    )
    if token_account is None:
        return False, False, None
    token = _latest_token_for_account(db, token_account.id)
    if token is None or not str(token.access_token or "").strip():
        return False, False, None
    try:
        return True, True, decrypt_secret(token.access_token)
    except Exception:
        return True, False, None


def _facebook_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    provider = FACEBOOK_PROVIDER
    integrations = _resolve_facebook_integrations(db, workspace_id)
    _log_provider_found(provider, integrations)
    rows: list[dict[str, Any]] = []
    if not integrations:
        row = _base_row(
            provider=provider,
            integration=None,
            record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
            token_present=False,
            token_decrypt_ok=False,
            asset_count=0,
            asset_ids=[],
            asset_names=[],
            endpoint_type="integration",
            metric_name=None,
            availability_status="unavailable",
            error_message="Facebook Pages integration not found.",
        )
        rows.append(row)
        return rows

    integration = next(
        (
            item
            for item in integrations
            if db.query(MetaPage)
            .filter(
                MetaPage.integration_id == item.id,
                MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
            )
            .count()
            > 0
        ),
        integrations[0],
    )
    token_present, token_decrypt_ok, access_token = _resolve_meta_token(db, integration)
    pages = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
        )
        .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
        .all()
    )
    asset_ids = [str(page.page_id) for page in pages if str(page.page_id or "").strip()]
    asset_names = [str(page.name or page.page_id) for page in pages if str(page.page_id or "").strip()]
    _log_assets_found(provider, integration, asset_ids, asset_names)
    if not token_present or not access_token:
        rows.append(
            _base_row(
                provider=provider,
                integration=integration,
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                endpoint_type="token",
                metric_name=None,
                availability_status="no_token",
            )
        )
        return rows
    if not pages:
        rows.append(
            _base_row(
                provider=provider,
                integration=integration,
                record_type=META_RECORD_TYPE_FACEBOOK_PAGE,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="page_assets",
                metric_name=None,
                availability_status="no_assets",
            )
        )
        return rows

    since, until = _time_window()
    page = pages[0]
    page_token = str(page.page_access_token or "").strip() or access_token
    fields_response = _graph_get(
        page.page_id,
        access_token=page_token,
        params={"fields": "followers_count,fan_count"},
    )
    payload = fields_response["payload"] if isinstance(fields_response["payload"], dict) else {}
    for field_name in ("followers_count", "fan_count"):
        row = _base_row(
            provider=provider,
            integration=integration,
            record_type=page.record_type,
            token_present=token_present,
            token_decrypt_ok=token_decrypt_ok,
            asset_count=len(asset_ids),
            asset_ids=asset_ids,
            asset_names=asset_names,
            endpoint_type="page_fields",
            metric_name=field_name,
            availability_status=_metric_status(
                fields_response["status_code"],
                fields_response["error_code"],
                fields_response["error_message"],
                has_data=field_name in payload,
            ),
            status_code=fields_response["status_code"],
            error_code=fields_response["error_code"],
            error_message=fields_response["error_message"],
            raw_available=field_name in payload,
            sample_value=payload.get(field_name),
        )
        _log_metric_tested(row)
        rows.append(row)
    for metric_name in FACEBOOK_READY_METRICS[:4]:
        response = _graph_get(
            f"{page.page_id}/insights",
            access_token=page_token,
            params={"metric": metric_name, "period": "day", "since": since, "until": until},
        )
        data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
        value = None
        if data and isinstance(data[0], dict):
            values = data[0].get("values") or []
            if values and isinstance(values[0], dict):
                value = values[-1].get("value")
        row = _base_row(
            provider=provider,
            integration=integration,
            record_type=page.record_type,
            token_present=token_present,
            token_decrypt_ok=token_decrypt_ok,
            asset_count=len(asset_ids),
            asset_ids=asset_ids,
            asset_names=asset_names,
            endpoint_type="page_insights",
            metric_name=metric_name,
            availability_status=_metric_status(
                response["status_code"],
                response["error_code"],
                response["error_message"],
                has_data=bool(data),
            ),
            status_code=response["status_code"],
            error_code=response["error_code"],
            error_message=response["error_message"],
            raw_available=bool(data),
            sample_value=value if value is not None else data[:1],
        )
        _log_metric_tested(row)
        rows.append(row)
    return rows


def _instagram_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    provider = INSTAGRAM_PROVIDER
    direct_integrations, legacy_integrations = _resolve_instagram_sources(db, workspace_id)
    _log_provider_found(provider, direct_integrations + legacy_integrations)
    rows: list[dict[str, Any]] = []

    missing_config = get_missing_instagram_business_config_fields()
    if missing_config:
        candidate = direct_integrations[0] if direct_integrations else (legacy_integrations[0] if legacy_integrations else None)
        row = _base_row(
            provider=provider,
            integration=candidate,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            token_present=False,
            token_decrypt_ok=False,
            asset_count=0,
            asset_ids=[],
            asset_names=[],
            endpoint_type="config",
            metric_name=None,
            availability_status="config_missing",
            missing=missing_config,
        )
        rows.append(row)

    if direct_integrations:
        integration = direct_integrations[0]
        token_present, token_decrypt_ok, access_token = _resolve_instagram_token(db, integration)
        accounts = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id != _instagram_token_account_external_id(integration.id),
            )
            .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
            .all()
        )
        asset_ids = [str(item.external_account_id) for item in accounts if str(item.external_account_id or "").strip()]
        asset_names = [str(item.display_name or item.external_account_id) for item in accounts if str(item.external_account_id or "").strip()]
        _log_assets_found(provider, integration, asset_ids, asset_names)
        if not token_present or not access_token:
            rows.append(
                _base_row(
                    provider=provider,
                    integration=integration,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    token_present=token_present,
                    token_decrypt_ok=token_decrypt_ok,
                    asset_count=len(asset_ids),
                    asset_ids=asset_ids,
                    asset_names=asset_names,
                    endpoint_type="token",
                    metric_name=None,
                    availability_status="no_token",
                )
            )
        elif not accounts:
            rows.append(
                _base_row(
                    provider=provider,
                    integration=integration,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    token_present=token_present,
                    token_decrypt_ok=token_decrypt_ok,
                    asset_count=0,
                    asset_ids=[],
                    asset_names=[],
                    endpoint_type="instagram_assets",
                    metric_name=None,
                    availability_status="no_assets",
                )
            )
        else:
            since, until = _time_window()
            account_id = str(accounts[0].external_account_id)
            for metric_name in INSTAGRAM_READY_METRICS:
                response = _graph_get(
                    f"{account_id}/insights",
                    access_token=access_token,
                    params={"metric": metric_name, "period": "day", "since": since, "until": until},
                )
                data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
                row = _base_row(
                    provider=provider,
                    integration=integration,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    token_present=token_present,
                    token_decrypt_ok=token_decrypt_ok,
                    asset_count=len(asset_ids),
                    asset_ids=asset_ids,
                    asset_names=asset_names,
                    endpoint_type="account_insights",
                    metric_name=metric_name,
                    availability_status=_metric_status(
                        response["status_code"],
                        response["error_code"],
                        response["error_message"],
                        has_data=bool(data),
                    ),
                    status_code=response["status_code"],
                    error_code=response["error_code"],
                    error_message=response["error_message"],
                    raw_available=bool(data),
                    sample_value=data[:1],
                )
                _log_metric_tested(row)
                rows.append(row)
            return rows

    legacy_candidate = next(
        (
            item
            for item in legacy_integrations
            if db.query(MetaPage)
            .filter(
                MetaPage.integration_id == item.id,
                MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            )
            .count()
            > 0
        ),
        None,
    )
    if legacy_candidate is None:
        if not rows:
            rows.append(
                _base_row(
                    provider=provider,
                    integration=None,
                    record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                    token_present=False,
                    token_decrypt_ok=False,
                    asset_count=0,
                    asset_ids=[],
                    asset_names=[],
                    endpoint_type="instagram_assets",
                    metric_name=None,
                    availability_status="no_assets",
                )
            )
        return rows

    token_present, token_decrypt_ok, access_token = _resolve_meta_token(db, legacy_candidate)
    records = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == legacy_candidate.id,
            MetaPage.record_type == META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
        )
        .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
        .all()
    )
    asset_ids = [str(item.page_id) for item in records if str(item.page_id or "").strip()]
    asset_names = [str(item.instagram_username or item.name or item.page_id) for item in records if str(item.page_id or "").strip()]
    _log_assets_found(provider, legacy_candidate, asset_ids, asset_names)
    if not token_present or not access_token:
        rows.append(
            _base_row(
                provider=provider,
                integration=legacy_candidate,
                record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                endpoint_type="token",
                metric_name=None,
                availability_status="no_token",
            )
        )
        return rows
    since, until = _time_window()
    for metric_name in INSTAGRAM_READY_METRICS:
        response = _graph_get(
            f"{records[0].page_id}/insights",
            access_token=access_token,
            params={"metric": metric_name, "period": "day", "since": since, "until": until},
        )
        data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
        row = _base_row(
            provider=provider,
            integration=legacy_candidate,
            record_type=META_RECORD_TYPE_INSTAGRAM_ACCOUNT,
            token_present=token_present,
            token_decrypt_ok=token_decrypt_ok,
            asset_count=len(asset_ids),
            asset_ids=asset_ids,
            asset_names=asset_names,
            endpoint_type="account_insights",
            metric_name=metric_name,
            availability_status=_metric_status(
                response["status_code"],
                response["error_code"],
                response["error_message"],
                has_data=bool(data),
            ),
            status_code=response["status_code"],
            error_code=response["error_code"],
            error_message=response["error_message"],
            raw_available=bool(data),
            sample_value=data[:1],
        )
        _log_metric_tested(row)
        rows.append(row)
    return rows


def _meta_ads_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    provider = META_ADS_PROVIDER
    integrations = _resolve_meta_ads_integrations(db, workspace_id)
    _log_provider_found(provider, integrations)
    rows: list[dict[str, Any]] = []

    snapshot = get_meta_ads_config_snapshot()
    missing = [name for name in ("META_ADS_APP_ID", "META_ADS_APP_SECRET", "META_ADS_REDIRECT_URI") if not snapshot.get({
        "META_ADS_APP_ID": "app_id_present",
        "META_ADS_APP_SECRET": "app_secret_present",
        "META_ADS_REDIRECT_URI": "redirect_uri_present",
    }[name])]
    if missing:
        rows.append(
            _base_row(
                provider=provider,
                integration=integrations[0] if integrations else None,
                record_type=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="config",
                metric_name=None,
                availability_status="config_missing",
                missing=missing,
            )
        )
        return rows

    if not _table_available("meta_ad_accounts"):
        rows.append(
            _base_row(
                provider=provider,
                integration=integrations[0] if integrations else None,
                record_type=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="schema",
                metric_name=None,
                availability_status="unexpected_error",
                error_message="Meta Ads database tables are not available yet. Apply database migrations.",
            )
        )
        return rows

    if not integrations:
        rows.append(
            _base_row(
                provider=provider,
                integration=None,
                record_type=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="integration",
                metric_name=None,
                availability_status="unavailable",
                error_message="Meta Ads integration not found.",
            )
        )
        return rows

    integration = integrations[0]
    token_present, token_decrypt_ok, access_token = _resolve_meta_token(db, integration)
    if not token_present or not access_token:
        rows.append(
            _base_row(
                provider=provider,
                integration=integration,
                record_type=None,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="token",
                metric_name=None,
                availability_status="no_token",
            )
        )
        return rows

    accounts_response = _graph_get(
        "me/adaccounts",
        access_token=access_token,
        params={"fields": "id,account_id,name,currency,timezone_name,account_status", "limit": 25},
        timeout=60,
    )
    accounts = accounts_response["payload"].get("data", []) if isinstance(accounts_response["payload"], dict) else []
    if not accounts:
        stored_accounts = (
            db.query(MetaAdAccount)
            .filter(MetaAdAccount.integration_id == integration.id)
            .order_by(MetaAdAccount.updated_at.desc(), MetaAdAccount.id.desc())
            .all()
        )
        accounts = [
            {
                "id": f"act_{item.account_id}",
                "account_id": item.account_id,
                "name": item.account_name,
            }
            for item in stored_accounts
        ]
    asset_ids = [str(item.get("account_id") or item.get("id") or "") for item in accounts if str(item.get("account_id") or item.get("id") or "").strip()]
    asset_names = [str(item.get("name") or item.get("account_id") or item.get("id") or "") for item in accounts if str(item.get("account_id") or item.get("id") or "").strip()]
    _log_assets_found(provider, integration, asset_ids, asset_names)
    if not accounts:
        rows.append(
            _base_row(
                provider=provider,
                integration=integration,
                record_type=None,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                endpoint_type="ad_accounts",
                metric_name=None,
                availability_status="no_assets",
            )
        )
        return rows

    since, until = _time_window()
    account_id = str(accounts[0].get("account_id") or accounts[0].get("id") or "")
    account_node = account_id if account_id.startswith("act_") else f"act_{account_id}"
    response = _graph_get(
        f"{account_node}/insights",
        access_token=access_token,
        params={
            "fields": ",".join(META_ADS_READY_METRICS),
            "level": "account",
            "time_increment": 1,
            "since": since,
            "until": until,
            "limit": 25,
        },
        timeout=60,
    )
    data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
    for metric_name in META_ADS_READY_METRICS:
        value = data[0].get(metric_name) if data and isinstance(data[0], dict) else None
        row = _base_row(
            provider=provider,
            integration=integration,
            record_type=None,
            token_present=token_present,
            token_decrypt_ok=token_decrypt_ok,
            asset_count=len(asset_ids),
            asset_ids=asset_ids,
            asset_names=asset_names,
            endpoint_type="insights_account",
            metric_name=metric_name,
            availability_status=_metric_status(
                response["status_code"],
                response["error_code"],
                response["error_message"],
                has_data=value is not None,
            ),
            status_code=response["status_code"],
            error_code=response["error_code"],
            error_message=response["error_message"],
            raw_available=value is not None,
            sample_value=value,
        )
        _log_metric_tested(row)
        rows.append(row)
    return rows


def _provider_summary(provider: str, rows: list[dict[str, Any]], recommended_metrics: list[str]) -> dict[str, Any]:
    available = [row for row in rows if row["availability_status"] == "available"]
    unavailable = [row for row in rows if row["availability_status"] != "available"]
    missing_permissions = [row for row in rows if row["availability_status"] == "missing_permission"]
    invalid_metrics = [row for row in rows if row["availability_status"] == "invalid_metric"]
    recommended = [
        metric
        for metric in recommended_metrics
        if any(
            row["metric_name"] == metric
            and row["availability_status"] == "available"
            for row in rows
        )
    ]
    return {
        "provider": provider,
        "total_available": len(available),
        "total_unavailable": len(unavailable),
        "missing_permissions": len(missing_permissions),
        "invalid_metrics": len(invalid_metrics),
        "recommended_report_metrics": recommended,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "provider",
        "integration_id",
        "db_provider",
        "record_type",
        "status",
        "token_present",
        "token_decrypt_ok",
        "asset_count",
        "asset_ids",
        "asset_names",
        "endpoint_type",
        "metric_name",
        "status_code",
        "availability_status",
        "error_code",
        "error_message",
        "reason",
        "missing",
        "raw_available",
        "sample_value",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row[key], ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key)
                    for key in columns
                }
            )


def run_meta_data_catalog_audit(
    db: Session,
    *,
    workspace_id: int,
    providers: list[str] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    requested_providers = providers or [FACEBOOK_PROVIDER, INSTAGRAM_PROVIDER, META_ADS_PROVIDER]
    logger.info(
        "META_DATA_CATALOG_STARTED %s",
        json.dumps({"workspace_id": workspace_id, "providers": requested_providers}, ensure_ascii=False, sort_keys=True),
    )
    details: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    provider_builders = {
        FACEBOOK_PROVIDER: lambda: _facebook_rows(db, workspace_id),
        INSTAGRAM_PROVIDER: lambda: _instagram_rows(db, workspace_id),
        META_ADS_PROVIDER: lambda: _meta_ads_rows(db, workspace_id),
    }
    for provider in requested_providers:
        logger.info(
            "META_DATA_CATALOG_PROVIDER_STARTED %s",
            json.dumps({"workspace_id": workspace_id, "provider": provider}, ensure_ascii=False, sort_keys=True),
        )
        provider_rows = provider_builders[provider]()
        details[provider] = provider_rows
        rows.extend(provider_rows)
        logger.info(
            "META_DATA_CATALOG_PROVIDER_COMPLETED %s",
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "provider": provider,
                    "rows": len(provider_rows),
                    "available": len([row for row in provider_rows if row["availability_status"] == "available"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    provider_summary = {
        FACEBOOK_PROVIDER: _provider_summary(FACEBOOK_PROVIDER, details.get(FACEBOOK_PROVIDER, []), FACEBOOK_READY_METRICS),
        INSTAGRAM_PROVIDER: _provider_summary(INSTAGRAM_PROVIDER, details.get(INSTAGRAM_PROVIDER, []), INSTAGRAM_READY_METRICS),
        META_ADS_PROVIDER: _provider_summary(META_ADS_PROVIDER, details.get(META_ADS_PROVIDER, []), META_ADS_READY_METRICS),
    }
    summary = {
        "total_available": sum(item["total_available"] for item in provider_summary.values()),
        "total_unavailable": sum(item["total_unavailable"] for item in provider_summary.values()),
        "missing_permissions": sum(item["missing_permissions"] for item in provider_summary.values()),
        "invalid_metrics": sum(item["invalid_metrics"] for item in provider_summary.values()),
        "recommended_report_metrics_by_provider": {
            provider: item["recommended_report_metrics"] for provider, item in provider_summary.items()
        },
    }

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    resolved_output_dir = Path(output_dir or Path.cwd() / "tmp")
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    json_path = resolved_output_dir / f"meta_data_catalog_{workspace_id}_{stamp}.json"
    csv_path = resolved_output_dir / f"meta_data_catalog_{workspace_id}_{stamp}.csv"
    payload = {
        "workspace_id": workspace_id,
        "summary": summary,
        "provider_summary": provider_summary,
        "details": details,
        "rows_preview": details,
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_csv(csv_path, rows)
    logger.info(
        "META_DATA_CATALOG_COMPLETED %s",
        json.dumps(
            {
                "workspace_id": workspace_id,
                "total_available": summary["total_available"],
                "total_unavailable": summary["total_unavailable"],
                "missing_permissions": summary["missing_permissions"],
                "invalid_metrics": summary["invalid_metrics"],
                "json_path": str(json_path),
                "csv_path": str(csv_path),
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    )
    return payload
