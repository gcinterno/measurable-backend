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
from .db import engine
from .integrations.instagram_business import get_missing_instagram_business_config_fields
from .integrations.meta_ads import get_meta_ads_config_snapshot
from .models import Integration, IntegrationAccount, IntegrationToken, MetaAdAccount, MetaPage
from .report_metric_catalog import (
    FACEBOOK_PAGES_PROVIDER,
    INSTAGRAM_BUSINESS_PROVIDER,
    META_ADS_PROVIDER,
    MetricCatalogEntry,
    get_metric_catalog_entries,
    get_recommended_report_metrics,
)

logger = logging.getLogger(__name__)

META_RECORD_TYPE_FACEBOOK_PAGE = "facebook_page"
META_RECORD_TYPE_INSTAGRAM_ACCOUNT = "instagram_account"


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


def _availability_from_response(
    *,
    default_status: str,
    status_code: int | None,
    error_code: Any,
    error_message: str | None,
    has_value: bool,
) -> str:
    if status_code == 200 and has_value:
        return "available"
    if status_code == 403:
        return "missing_permission"
    if str(error_code or "").strip() == "10":
        return "missing_permission"
    normalized_message = str(error_message or "").lower()
    if str(error_code or "").strip() == "100" or "invalid metric" in normalized_message:
        return "invalid_metric"
    if "permission" in normalized_message:
        return "missing_permission"
    if status_code and status_code >= 500:
        return "unexpected_error"
    return default_status


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


def _reason_for_status(status: str, *, metric: MetricCatalogEntry, missing: list[str] | None = None) -> str:
    if status == "available":
        return f"{metric.display_name_en} is available and product-ready."
    if status == "config_missing":
        return f"Missing configuration for {metric.provider}: {', '.join(missing or [])}."
    if status == "no_token":
        return f"No stored token is available to audit {metric.provider}."
    if status == "no_assets":
        return f"No authorized assets were found for {metric.provider}."
    if status == "pending_permission":
        return f"Requires permissions: {', '.join(metric.required_permissions)}."
    if status == "missing_permission":
        return f"Permission is missing for {metric.real_metric_name}."
    if status == "pending_config":
        return metric.notes
    if status == "invalid_metric":
        return metric.notes
    if status == "unsupported":
        return metric.notes
    if status == "schema_missing":
        return "Database tables required by this provider are missing."
    if status == "unavailable":
        return f"{metric.display_name_en} is unavailable for the current asset or time range."
    if status == "unexpected_error":
        return "The provider returned an unexpected error."
    return metric.notes


def _base_row(
    *,
    metric: MetricCatalogEntry,
    integration: Integration | None,
    token_present: bool,
    token_decrypt_ok: bool,
    asset_count: int,
    asset_ids: list[str],
    asset_names: list[str],
    availability_status: str,
    status_code: int | None = None,
    error_code: Any = None,
    error_message: str | None = None,
    sample_value: Any = None,
    missing: list[str] | None = None,
    raw_available: bool | None = None,
) -> dict[str, Any]:
    return {
        "provider": metric.provider,
        "integration_id": integration.id if integration else None,
        "db_provider": integration.provider if integration else None,
        "record_type": META_RECORD_TYPE_FACEBOOK_PAGE if metric.provider == FACEBOOK_PAGES_PROVIDER else META_RECORD_TYPE_INSTAGRAM_ACCOUNT if metric.provider == INSTAGRAM_BUSINESS_PROVIDER else None,
        "status": str(integration.status or "").strip() if integration else "missing",
        "token_present": token_present,
        "token_decrypt_ok": token_decrypt_ok,
        "asset_count": asset_count,
        "asset_ids": asset_ids[:5],
        "asset_names": asset_names[:5],
        "endpoint_type": metric.source_type,
        "metric_name": metric.real_metric_name,
        "measurable_key": metric.measurable_key,
        "display_name_en": metric.display_name_en,
        "display_name_es": metric.display_name_es,
        "category": metric.category,
        "catalog_status": metric.status,
        "status_code": status_code,
        "availability_status": availability_status,
        "error_code": error_code,
        "error_message": error_message,
        "reason": _reason_for_status(availability_status, metric=metric, missing=missing),
        "missing": missing or [],
        "raw_available": raw_available,
        "sample_value": _safe_sample(sample_value),
        "required_permissions": list(metric.required_permissions),
        "source_type": metric.source_type,
        "has_total": metric.has_total,
        "has_daily_series": metric.has_daily_series,
        "total_key": metric.total_key,
        "daily_key": metric.daily_key,
        "recommended_slide": metric.recommended_slide,
        "is_primary": metric.is_primary,
        "notes": metric.notes,
    }


def _log_json(event: str, payload: dict[str, Any]) -> None:
    logger.info("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))


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


def _resolve_instagram_integrations(db: Session, workspace_id: int) -> list[Integration]:
    return (
        db.query(Integration)
        .filter(
            Integration.workspace_id == workspace_id,
            Integration.provider == "instagram_business",
        )
        .order_by(Integration.updated_at.desc(), Integration.id.desc())
        .all()
    )


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


def _provider_assets(db: Session, integration: Integration | None, provider: str) -> tuple[list[str], list[str], str | None]:
    if integration is None:
        return [], [], None
    if provider == FACEBOOK_PAGES_PROVIDER:
        records = (
            db.query(MetaPage)
            .filter(
                MetaPage.integration_id == integration.id,
                MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
            )
            .order_by(MetaPage.updated_at.desc(), MetaPage.id.desc())
            .all()
        )
        return (
            [str(item.page_id) for item in records if str(item.page_id or "").strip()],
            [str(item.name or item.page_id) for item in records if str(item.page_id or "").strip()],
            str(records[0].page_id) if records else None,
        )
    if provider == INSTAGRAM_BUSINESS_PROVIDER:
        accounts = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.integration_id == integration.id,
                IntegrationAccount.external_account_id != _instagram_token_account_external_id(integration.id),
            )
            .order_by(IntegrationAccount.updated_at.desc(), IntegrationAccount.id.desc())
            .all()
        )
        return (
            [str(item.external_account_id) for item in accounts if str(item.external_account_id or "").strip()],
            [str(item.display_name or item.external_account_id) for item in accounts if str(item.external_account_id or "").strip()],
            str(accounts[0].external_account_id) if accounts else None,
        )
    accounts = []
    if _table_available("meta_ad_accounts"):
        accounts = (
            db.query(MetaAdAccount)
            .filter(MetaAdAccount.integration_id == integration.id)
            .order_by(MetaAdAccount.updated_at.desc(), MetaAdAccount.id.desc())
            .all()
        )
    return (
        [str(item.account_id) for item in accounts if str(item.account_id or "").strip()],
        [str(item.account_name or item.account_id) for item in accounts if str(item.account_id or "").strip()],
        str(accounts[0].account_id) if accounts else None,
    )


def _static_catalog_rows(
    *,
    provider: str,
    integration: Integration | None,
    token_present: bool,
    token_decrypt_ok: bool,
    asset_ids: list[str],
    asset_names: list[str],
    statuses: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in get_metric_catalog_entries(provider):
        if metric.status not in statuses:
            continue
        rows.append(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status=metric.status,
            )
        )
    return rows


def _facebook_available_rows(
    db: Session,
    integration: Integration,
    access_token: str,
    asset_ids: list[str],
    asset_names: list[str],
    page_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = (
        db.query(MetaPage)
        .filter(
            MetaPage.integration_id == integration.id,
            MetaPage.record_type == META_RECORD_TYPE_FACEBOOK_PAGE,
            MetaPage.page_id == page_id,
        )
        .first()
    )
    page_token = str(page.page_access_token or "").strip() if page and str(page.page_access_token or "").strip() else access_token
    fields_response = _graph_get(
        page_id,
        access_token=page_token,
        params={"fields": "followers_count,fan_count"},
    )
    payload = fields_response["payload"] if isinstance(fields_response["payload"], dict) else {}
    for metric in get_metric_catalog_entries(FACEBOOK_PAGES_PROVIDER):
        if metric.real_metric_name not in {"followers_count", "fan_count"}:
            continue
        has_value = metric.real_metric_name in payload
        row = _base_row(
            metric=metric,
            integration=integration,
            token_present=True,
            token_decrypt_ok=True,
            asset_count=len(asset_ids),
            asset_ids=asset_ids,
            asset_names=asset_names,
            availability_status=_availability_from_response(
                default_status="unavailable",
                status_code=fields_response["status_code"],
                error_code=fields_response["error_code"],
                error_message=fields_response["error_message"],
                has_value=has_value,
            ),
            status_code=fields_response["status_code"],
            error_code=fields_response["error_code"],
            error_message=fields_response["error_message"],
            sample_value=payload.get(metric.real_metric_name),
            raw_available=has_value,
        )
        rows.append(row)
    since, until = _time_window()
    for metric in get_metric_catalog_entries(FACEBOOK_PAGES_PROVIDER):
        if metric.status != "available" or metric.source_type != "insights_metric":
            continue
        response = _graph_get(
            f"{page_id}/insights",
            access_token=page_token,
            params={"metric": metric.real_metric_name, "period": "day", "since": since, "until": until},
        )
        data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
        sample_value = None
        if data and isinstance(data[0], dict):
            values = data[0].get("values") or []
            if values and isinstance(values[-1], dict):
                sample_value = values[-1].get("value")
        has_value = bool(data)
        rows.append(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status=_availability_from_response(
                    default_status="unavailable",
                    status_code=response["status_code"],
                    error_code=response["error_code"],
                    error_message=response["error_message"],
                    has_value=has_value,
                ),
                status_code=response["status_code"],
                error_code=response["error_code"],
                error_message=response["error_message"],
                sample_value=sample_value if sample_value is not None else data[:1],
                raw_available=has_value,
            )
        )
    return rows


def _instagram_available_rows(
    integration: Integration,
    access_token: str,
    asset_ids: list[str],
    asset_names: list[str],
    account_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since, until = _time_window()
    for metric in get_metric_catalog_entries(INSTAGRAM_BUSINESS_PROVIDER):
        if metric.status not in {"pending_config", "pending_permission"}:
            continue
        endpoint = f"{account_id}/insights"
        metric_name = metric.real_metric_name
        if metric_name == "followers_count":
            response = _graph_get(
                account_id,
                access_token=access_token,
                params={"fields": "followers_count"},
            )
            payload = response["payload"] if isinstance(response["payload"], dict) else {}
            has_value = "followers_count" in payload
            rows.append(
                _base_row(
                    metric=metric,
                    integration=integration,
                    token_present=True,
                    token_decrypt_ok=True,
                    asset_count=len(asset_ids),
                    asset_ids=asset_ids,
                    asset_names=asset_names,
                    availability_status="available" if response["status_code"] == 200 and has_value else metric.status,
                    status_code=response["status_code"],
                    error_code=response["error_code"],
                    error_message=response["error_message"],
                    sample_value=payload.get("followers_count"),
                    raw_available=has_value,
                )
            )
            continue
        probe_metric = "views" if metric_name == "views_or_impressions" else metric_name
        response = _graph_get(
            endpoint,
            access_token=access_token,
            params={"metric": probe_metric, "period": "day", "since": since, "until": until},
        )
        data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
        availability_status = "available" if response["status_code"] == 200 and bool(data) else metric.status
        if response["status_code"] == 403:
            availability_status = "missing_permission"
        rows.append(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status=availability_status,
                status_code=response["status_code"],
                error_code=response["error_code"],
                error_message=response["error_message"],
                sample_value=data[:1],
                raw_available=bool(data),
            )
        )
    return rows


def _meta_ads_available_rows(
    integration: Integration,
    access_token: str,
    asset_ids: list[str],
    asset_names: list[str],
    account_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since, until = _time_window()
    account_node = account_id if account_id.startswith("act_") else f"act_{account_id}"
    response = _graph_get(
        f"{account_node}/insights",
        access_token=access_token,
        params={
            "fields": ",".join(
                [
                    "spend",
                    "impressions",
                    "reach",
                    "clicks",
                    "inline_link_clicks",
                    "ctr",
                    "cpc",
                    "cpm",
                    "actions",
                    "cost_per_action_type",
                    "purchase_roas",
                ]
            ),
            "level": "account",
            "time_increment": 1,
            "since": since,
            "until": until,
            "limit": 25,
        },
        timeout=60,
    )
    data = response["payload"].get("data", []) if isinstance(response["payload"], dict) else []
    first_row = data[0] if data and isinstance(data[0], dict) else {}
    for metric in get_metric_catalog_entries(META_ADS_PROVIDER):
        if metric.source_type != "ads_insights_field":
            rows.append(
                _base_row(
                    metric=metric,
                    integration=integration,
                    token_present=True,
                    token_decrypt_ok=True,
                    asset_count=len(asset_ids),
                    asset_ids=asset_ids,
                    asset_names=asset_names,
                    availability_status=metric.status,
                )
            )
            continue
        provider_field = metric.real_metric_name
        value = first_row.get(provider_field) if first_row else None
        availability_status = "available" if response["status_code"] == 200 and value is not None else metric.status
        if response["status_code"] == 403:
            availability_status = "missing_permission"
        rows.append(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status=availability_status,
                status_code=response["status_code"],
                error_code=response["error_code"],
                error_message=response["error_message"],
                sample_value=value,
                raw_available=value is not None,
            )
        )
    return rows


def _facebook_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    integrations = _resolve_facebook_integrations(db, workspace_id)
    integration = integrations[0] if integrations else None
    token_present, token_decrypt_ok, access_token = _resolve_meta_token(db, integration) if integration else (False, False, None)
    asset_ids, asset_names, page_id = _provider_assets(db, integration, FACEBOOK_PAGES_PROVIDER)
    rows = _static_catalog_rows(
        provider=FACEBOOK_PAGES_PROVIDER,
        integration=integration,
        token_present=token_present,
        token_decrypt_ok=token_decrypt_ok,
        asset_ids=asset_ids,
        asset_names=asset_names,
        statuses={"invalid_metric", "pending_permission"},
    )
    if integration is None:
        rows.extend(
            _base_row(
                metric=metric,
                integration=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(FACEBOOK_PAGES_PROVIDER)
            if metric.status == "available"
        )
        return rows
    if not token_present or not access_token:
        _log_json("META_METRIC_CATALOG_NO_TOKEN", {"provider": FACEBOOK_PAGES_PROVIDER, "integration_id": integration.id})
        rows.extend(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(FACEBOOK_PAGES_PROVIDER)
            if metric.status == "available"
        )
        return rows
    if not page_id:
        rows.extend(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_assets",
            )
            for metric in get_metric_catalog_entries(FACEBOOK_PAGES_PROVIDER)
            if metric.status == "available"
        )
        return rows
    rows.extend(_facebook_available_rows(db, integration, access_token, asset_ids, asset_names, page_id))
    return rows


def _instagram_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    integrations = _resolve_instagram_integrations(db, workspace_id)
    integration = integrations[0] if integrations else None
    missing_config = get_missing_instagram_business_config_fields()
    token_present, token_decrypt_ok, access_token = _resolve_instagram_token(db, integration) if integration else (False, False, None)
    asset_ids, asset_names, account_id = _provider_assets(db, integration, INSTAGRAM_BUSINESS_PROVIDER)
    rows: list[dict[str, Any]] = []
    if missing_config:
        _log_json("META_METRIC_CATALOG_MISSING_CONFIG", {"provider": INSTAGRAM_BUSINESS_PROVIDER, "missing": missing_config})
        rows.extend(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status="config_missing",
                missing=missing_config,
            )
            for metric in get_metric_catalog_entries(INSTAGRAM_BUSINESS_PROVIDER)
        )
        return rows
    if integration is None:
        rows.extend(
            _base_row(
                metric=metric,
                integration=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(INSTAGRAM_BUSINESS_PROVIDER)
        )
        return rows
    if not token_present or not access_token:
        _log_json("META_METRIC_CATALOG_NO_TOKEN", {"provider": INSTAGRAM_BUSINESS_PROVIDER, "integration_id": integration.id})
        rows.extend(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(INSTAGRAM_BUSINESS_PROVIDER)
        )
        return rows
    if not account_id:
        rows.extend(
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_assets",
            )
            for metric in get_metric_catalog_entries(INSTAGRAM_BUSINESS_PROVIDER)
        )
        return rows
    rows.extend(_instagram_available_rows(integration, access_token, asset_ids, asset_names, account_id))
    return rows


def _meta_ads_rows(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    integrations = _resolve_meta_ads_integrations(db, workspace_id)
    integration = integrations[0] if integrations else None
    snapshot = get_meta_ads_config_snapshot()
    missing = [name for name in ("META_ADS_APP_ID", "META_ADS_APP_SECRET", "META_ADS_REDIRECT_URI") if not snapshot.get({
        "META_ADS_APP_ID": "app_id_present",
        "META_ADS_APP_SECRET": "app_secret_present",
        "META_ADS_REDIRECT_URI": "redirect_uri_present",
    }[name])]
    token_present, token_decrypt_ok, access_token = _resolve_meta_token(db, integration) if integration else (False, False, None)
    asset_ids, asset_names, account_id = _provider_assets(db, integration, META_ADS_PROVIDER)
    if missing:
        _log_json("META_METRIC_CATALOG_MISSING_CONFIG", {"provider": META_ADS_PROVIDER, "missing": missing})
        return [
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status="config_missing",
                missing=missing,
            )
            for metric in get_metric_catalog_entries(META_ADS_PROVIDER)
        ]
    if not _table_available("meta_ad_accounts"):
        return [
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="schema_missing",
                error_message="Meta Ads database tables are not available yet.",
            )
            for metric in get_metric_catalog_entries(META_ADS_PROVIDER)
        ]
    if integration is None:
        return [
            _base_row(
                metric=metric,
                integration=None,
                token_present=False,
                token_decrypt_ok=False,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(META_ADS_PROVIDER)
        ]
    if not token_present or not access_token:
        _log_json("META_METRIC_CATALOG_NO_TOKEN", {"provider": META_ADS_PROVIDER, "integration_id": integration.id})
        return [
            _base_row(
                metric=metric,
                integration=integration,
                token_present=token_present,
                token_decrypt_ok=token_decrypt_ok,
                asset_count=len(asset_ids),
                asset_ids=asset_ids,
                asset_names=asset_names,
                availability_status="no_token",
            )
            for metric in get_metric_catalog_entries(META_ADS_PROVIDER)
        ]
    if not account_id:
        accounts_response = _graph_get(
            "me/adaccounts",
            access_token=access_token,
            params={"fields": "id,account_id,name,currency,timezone_name,account_status", "limit": 25},
            timeout=60,
        )
        accounts = accounts_response["payload"].get("data", []) if isinstance(accounts_response["payload"], dict) else []
        asset_ids = [str(item.get("account_id") or item.get("id") or "") for item in accounts if str(item.get("account_id") or item.get("id") or "").strip()]
        asset_names = [str(item.get("name") or item.get("account_id") or item.get("id") or "") for item in accounts if str(item.get("account_id") or item.get("id") or "").strip()]
        account_id = asset_ids[0] if asset_ids else None
    if not account_id:
        return [
            _base_row(
                metric=metric,
                integration=integration,
                token_present=True,
                token_decrypt_ok=True,
                asset_count=0,
                asset_ids=[],
                asset_names=[],
                availability_status="no_assets",
            )
            for metric in get_metric_catalog_entries(META_ADS_PROVIDER)
        ]
    return _meta_ads_available_rows(integration, access_token, asset_ids, asset_names, account_id)


def _provider_summary(provider: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row["availability_status"] == "available"]
    pending = [row for row in rows if row["availability_status"] in {"pending_permission", "pending_config", "config_missing", "no_token", "no_assets", "missing_permission"}]
    unsupported = [row for row in rows if row["availability_status"] in {"unsupported", "invalid_metric", "schema_missing"}]
    recommended = get_recommended_report_metrics(provider)
    _log_json(
        "META_METRIC_CATALOG_PROVIDER_SUMMARY",
        {
            "provider": provider,
            "available_metrics": len(available),
            "pending_metrics": len(pending),
            "unsupported_metrics": len(unsupported),
        },
    )
    return {
        "provider": provider,
        "available_metrics": len(available),
        "pending_metrics": len(pending),
        "unsupported_metrics": len(unsupported),
        "recommended_report_metrics": [entry["real_metric_name"] for entry in recommended],
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
        "measurable_key",
        "display_name_en",
        "display_name_es",
        "category",
        "catalog_status",
        "status_code",
        "availability_status",
        "error_code",
        "error_message",
        "reason",
        "missing",
        "raw_available",
        "sample_value",
        "required_permissions",
        "source_type",
        "has_total",
        "has_daily_series",
        "total_key",
        "daily_key",
        "recommended_slide",
        "is_primary",
        "notes",
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
    requested_providers = providers or [FACEBOOK_PAGES_PROVIDER, INSTAGRAM_BUSINESS_PROVIDER, META_ADS_PROVIDER]
    _log_json("META_METRIC_CATALOG_LOADED", {"workspace_id": workspace_id, "providers": requested_providers})

    provider_builders = {
        FACEBOOK_PAGES_PROVIDER: lambda: _facebook_rows(db, workspace_id),
        INSTAGRAM_BUSINESS_PROVIDER: lambda: _instagram_rows(db, workspace_id),
        META_ADS_PROVIDER: lambda: _meta_ads_rows(db, workspace_id),
    }
    details: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    for provider in requested_providers:
        provider_rows = provider_builders[provider]()
        details[provider] = provider_rows
        rows.extend(provider_rows)
        _log_json(
            "META_METRIC_CATALOG_RECOMMENDED_METRICS",
            {
                "provider": provider,
                "recommended": [entry["real_metric_name"] for entry in get_recommended_report_metrics(provider)],
            },
        )

    provider_summary = {provider: _provider_summary(provider, details.get(provider, [])) for provider in requested_providers}
    recommended_report_metrics_by_provider = {
        provider: get_recommended_report_metrics(provider)
        for provider in requested_providers
    }
    summary = {
        "total_available": sum(1 for row in rows if row["availability_status"] == "available"),
        "total_pending": sum(1 for row in rows if row["availability_status"] in {"pending_permission", "pending_config", "config_missing", "no_token", "no_assets", "missing_permission"}),
        "total_unsupported": sum(1 for row in rows if row["availability_status"] in {"unsupported", "invalid_metric", "schema_missing"}),
        "recommended_report_metrics_by_provider": recommended_report_metrics_by_provider,
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
    _log_json(
        "META_METRIC_CATALOG_COMPLETED",
        {
            "workspace_id": workspace_id,
            "providers": requested_providers,
            "total_available": summary["total_available"],
            "total_pending": summary["total_pending"],
            "total_unsupported": summary["total_unsupported"],
            "json_path": str(json_path),
            "csv_path": str(csv_path),
        },
    )
    return payload
