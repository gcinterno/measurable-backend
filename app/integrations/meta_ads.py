import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from jose import JWTError, jwt

from ..config import settings
from ..errors import http_error

logger = logging.getLogger(__name__)
FACEBOOK_PAGES_SCOPES = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "read_insights",
    "pages_read_user_content",
]
INSTAGRAM_BUSINESS_SCOPES_LEGACY_FACEBOOK_LOGIN = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "read_insights",
    # The instagram_business_* scopes belong to Instagram Business Login /
    # Instagram Platform and must not be requested through Facebook Login.
    # Reserve them for a future dedicated Instagram auth flow.
    "instagram_basic",
    "instagram_manage_insights",
]
META_ADS_SCOPES = [
    "public_profile",
    "ads_read",
]
FACEBOOK_PAGES_OAUTH_SCOPE = ",".join(FACEBOOK_PAGES_SCOPES)
INSTAGRAM_BUSINESS_OAUTH_SCOPE_LEGACY_FACEBOOK_LOGIN = ",".join(
    INSTAGRAM_BUSINESS_SCOPES_LEGACY_FACEBOOK_LOGIN
)
META_ADS_OAUTH_SCOPE = ",".join(META_ADS_SCOPES)
META_PAGES_OAUTH_SCOPE = FACEBOOK_PAGES_OAUTH_SCOPE
META_PAGES_CALLBACK_PATH = "/integrations/meta/callback-pages"
META_ADS_CALLBACK_PATH = "/integrations/meta-ads/callback"
META_OAUTH_STATE_PURPOSE = "meta_pages_oauth"


def _meta_oauth_log_message(event: str, payload: dict[str, Any]) -> str:
    return f"{event} {json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)}"


def normalize_meta_oauth_integration_type(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"instagram_business", "instagram_accounts", "instagram"}:
        return "instagram_business"
    if normalized in {"meta_ads", "meta-ad", "metaads", "ads"}:
        return "meta_ads"
    return "facebook_pages"


def meta_oauth_scopes_for_integration_type(integration_type: str | None) -> list[str]:
    normalized = normalize_meta_oauth_integration_type(integration_type)
    if normalized == "instagram_business":
        return list(INSTAGRAM_BUSINESS_SCOPES_LEGACY_FACEBOOK_LOGIN)
    if normalized == "meta_ads":
        return list(META_ADS_SCOPES)
    return list(FACEBOOK_PAGES_SCOPES)


def meta_oauth_scope_string_for_integration_type(integration_type: str | None) -> str:
    return ",".join(meta_oauth_scopes_for_integration_type(integration_type))


def _truncate_meta_log_value(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _redact_meta_tokens(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "access_token":
                text = str(item or "")
                redacted[key] = f"{text[:8]}..." if text else None
            else:
                redacted[key] = _redact_meta_tokens(item)
        return redacted
    if isinstance(value, list):
        return [_redact_meta_tokens(item) for item in value]
    return value


def _raise_meta_api_error(resp: requests.Response) -> None:
    if resp.status_code != 200:
        response_payload: dict[str, Any] | None = None
        meta_error: dict[str, Any] | None = None
        try:
            parsed_payload = resp.json()
        except ValueError:
            parsed_payload = None
        if isinstance(parsed_payload, dict):
            response_payload = parsed_payload
            error_payload = parsed_payload.get("error")
            if isinstance(error_payload, dict):
                meta_error = error_payload

        message = (
            str(meta_error.get("message") or "").strip()
            if meta_error
            else resp.text
        )
        exc = http_error(status_code=400, code="meta_api_error", message=message)
        if isinstance(exc.detail, dict):
            exc.detail["upstream_status_code"] = resp.status_code
            exc.detail["meta_error"] = meta_error
            exc.detail["response_body"] = _truncate_meta_log_value(
                _redact_meta_tokens(response_payload if response_payload is not None else resp.text)
            )
        raise exc


def _require_meta_config() -> None:
    if not settings.meta_app_id or not settings.meta_app_secret or not settings.meta_redirect_uri:
        raise RuntimeError("META config missing")


def get_missing_meta_ads_config_fields() -> list[str]:
    missing_fields: list[str] = []
    if not settings.meta_app_id:
        missing_fields.append("META_APP_ID")
    if not settings.meta_app_secret:
        missing_fields.append("META_APP_SECRET")
    if not settings.meta_redirect_uri:
        missing_fields.append("META_REDIRECT_URI")
    return missing_fields


def get_meta_ads_redirect_uri() -> str:
    _require_meta_config()
    configured_redirect_uri = str(settings.meta_redirect_uri or "").strip()
    api_base_url = str(settings.api_base_url or "").strip().rstrip("/")
    if api_base_url:
        return f"{api_base_url}{META_ADS_CALLBACK_PATH}"

    parsed = urlparse(configured_redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        raise http_error(
            status_code=500,
            code="meta_ads_config_invalid",
            message="META_REDIRECT_URI must be an absolute URL.",
        )
    return urlunparse((parsed.scheme, parsed.netloc, META_ADS_CALLBACK_PATH, "", "", ""))


def _require_meta_pages_config() -> None:
    app_id_loaded = bool(settings.meta_pages_app_id)
    redirect_uri_loaded = bool(settings.meta_pages_redirect_uri)
    logger.info(
        "Meta Pages config check",
        extra={
            "meta_pages_app_id_loaded": app_id_loaded,
            "meta_pages_redirect_uri_loaded": redirect_uri_loaded,
        },
    )

    missing_fields: list[str] = []
    if not settings.meta_pages_app_id:
        missing_fields.append("META_PAGES_APP_ID")
    if not settings.meta_pages_app_secret:
        missing_fields.append("META_PAGES_APP_SECRET")
    if not settings.meta_pages_redirect_uri:
        missing_fields.append("META_PAGES_REDIRECT_URI")

    if missing_fields:
        raise http_error(
            status_code=500,
            code="meta_pages_config_missing",
            message="Missing Meta Pages config: " + ", ".join(missing_fields),
        )


def get_meta_pages_redirect_uri() -> str:
    _require_meta_pages_config()
    configured_redirect_uri = str(settings.meta_pages_redirect_uri or "").strip()
    api_base_url = str(settings.api_base_url or "").strip().rstrip("/")
    if api_base_url:
        resolved_redirect_uri = f"{api_base_url}{META_PAGES_CALLBACK_PATH}"
    else:
        parsed = urlparse(configured_redirect_uri)
        if not parsed.scheme or not parsed.netloc:
            raise http_error(
                status_code=500,
                code="meta_pages_config_invalid",
                message="META_PAGES_REDIRECT_URI must be an absolute URL.",
            )
        resolved_redirect_uri = urlunparse(
            (parsed.scheme, parsed.netloc, META_PAGES_CALLBACK_PATH, "", "", "")
        )

    if resolved_redirect_uri != configured_redirect_uri:
        logger.warning(
            "Meta Pages redirect URI normalized configured=%s resolved=%s api_base_url_loaded=%s",
            configured_redirect_uri,
            resolved_redirect_uri,
            bool(api_base_url),
        )
    return resolved_redirect_uri


def encode_state(payload: dict[str, Any], *, expires_seconds: int = 1800) -> str:
    now = datetime.now(timezone.utc)
    signed_payload = {
        "purpose": META_OAUTH_STATE_PURPOSE,
        "payload": payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(signed_payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def _decode_legacy_state(state: str) -> dict[str, Any]:
    padding = (-len(state)) % 4
    if padding:
        state = state + ("=" * padding)
    raw = base64.urlsafe_b64decode(state.encode("utf-8"))
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_state")
    return payload


def decode_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_alg])
        if payload.get("purpose") != META_OAUTH_STATE_PURPOSE:
            raise ValueError("invalid_state")
        signed_payload = payload.get("payload")
        if not isinstance(signed_payload, dict):
            raise ValueError("invalid_state")
        return signed_payload
    except JWTError:
        try:
            return _decode_legacy_state(state)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError("invalid_state") from exc


def oauth_connect_url(
    state: str,
    *,
    scope: str | None = None,
    redirect_uri: str | None = None,
    auth_type: str | None = None,
    integration_type: str | None = None,
) -> str:
    _require_meta_config()
    base = f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth"
    normalized_integration_type = normalize_meta_oauth_integration_type(integration_type)
    final_scope = scope or meta_oauth_scope_string_for_integration_type(normalized_integration_type)
    final_redirect_uri = redirect_uri or settings.meta_redirect_uri
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": final_redirect_uri,
        "state": state,
        "scope": final_scope,
        "response_type": "code",
    }
    if auth_type:
        params["auth_type"] = auth_type
    logger.info(
        _meta_oauth_log_message(
            "META_OAUTH_AUTH_URL_CREATED",
            {
                "provider": "meta_ads",
                "integration_type": normalized_integration_type,
                "client_id_loaded": bool(settings.meta_app_id),
                "redirect_uri": final_redirect_uri,
                "response_type": "code",
                "auth_type": auth_type,
                "scope": final_scope,
                "scopes_requested": meta_oauth_scopes_for_integration_type(normalized_integration_type),
                "state_present": bool(state),
                "oauth_dialog_url": base,
            },
        )
    )
    return f"{base}?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())


def exchange_code_for_token(code: str, *, redirect_uri: str | None = None) -> dict[str, Any]:
    _require_meta_config()
    url = f"https://graph.facebook.com/{settings.meta_api_version}/oauth/access_token"
    params = {
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "redirect_uri": redirect_uri or settings.meta_redirect_uri,
        "code": code,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    return resp.json()


def oauth_connect_pages_url(
    state: str,
    *,
    redirect_uri: str | None = None,
    auth_type: str | None = None,
    scope: str | None = None,
    integration_type: str | None = None,
) -> str:
    _require_meta_pages_config()
    base = f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth"
    final_redirect_uri = redirect_uri or get_meta_pages_redirect_uri()
    normalized_integration_type = normalize_meta_oauth_integration_type(integration_type)
    final_scope = scope or meta_oauth_scope_string_for_integration_type(normalized_integration_type)
    params = {
        "client_id": settings.meta_pages_app_id,
        "redirect_uri": final_redirect_uri,
        "state": state,
        "scope": final_scope,
        "response_type": "code",
    }
    if auth_type:
        params["auth_type"] = auth_type
    logger.info(
        _meta_oauth_log_message(
            "META_OAUTH_AUTH_URL_CREATED",
            {
                "provider": "meta_pages",
                "integration_type": normalized_integration_type,
                "client_id_loaded": bool(settings.meta_pages_app_id),
                "redirect_uri": final_redirect_uri,
                "response_type": "code",
                "auth_type": auth_type,
                "scope": final_scope,
                "scopes_requested": meta_oauth_scopes_for_integration_type(normalized_integration_type),
                "state_present": bool(state),
                "oauth_dialog_url": base,
            },
        )
    )
    return f"{base}?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())


def exchange_pages_code_for_token(code: str, *, redirect_uri: str | None = None) -> dict[str, Any]:
    _require_meta_pages_config()
    url = f"https://graph.facebook.com/{settings.meta_api_version}/oauth/access_token"
    params = {
        "client_id": settings.meta_pages_app_id,
        "client_secret": settings.meta_pages_app_secret,
        "redirect_uri": redirect_uri or get_meta_pages_redirect_uri(),
        "code": code,
    }
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    _raise_meta_api_error(resp)
    payload = response_payload if isinstance(response_payload, dict) else {}
    payload["_meta_http_status_code"] = resp.status_code
    payload["_meta_raw_body"] = _truncate_meta_log_value(_redact_meta_tokens(response_payload))
    return payload


def list_ad_accounts(access_token: str) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/me/adaccounts"
    params = {
        "fields": "id,account_id,name,currency,timezone_name,account_status,business{id,name}",
        "limit": 200,
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])


def get_user_businesses(access_token: str) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/me/businesses"
    params = {"fields": "id,name", "access_token": access_token}
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])


def get_businesses(access_token: str) -> list[dict[str, Any]]:
    return get_user_businesses(access_token)


def get_owned_ad_accounts(access_token: str, business_id: str) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{business_id}/owned_ad_accounts"
    params = {
        "fields": "id,account_id,name,currency,timezone_name,account_status,business{id,name}",
        "limit": 200,
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])


def debug_ads_permissions(access_token: str) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/me/adaccounts"
    params = {"fields": "id,name,account_id", "access_token": access_token}
    resp = requests.get(url, params=params, timeout=30)
    payload: dict[str, Any] = {
        "status": "ok" if resp.status_code == 200 else "error",
        "status_code": resp.status_code,
        "response_text": resp.text,
    }
    if resp.status_code == 200:
        try:
            data = resp.json().get("data", [])
        except ValueError:
            data = []
        payload["ad_accounts_count"] = len(data)
    return payload


def debug_token(access_token: str) -> dict[str, Any]:
    _require_meta_pages_config()
    url = f"https://graph.facebook.com/{settings.meta_api_version}/debug_token"
    params = {
        "input_token": access_token,
        "access_token": f"{settings.meta_pages_app_id}|{settings.meta_pages_app_secret}",
    }
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    logger.warning(
        "Meta /debug_token status=%s raw_body=%s",
        resp.status_code,
        _truncate_meta_log_value(_redact_meta_tokens(response_payload)),
    )
    _raise_meta_api_error(resp)
    return response_payload if isinstance(response_payload, dict) else {}


def list_pages(
    access_token: str,
    *,
    context: str = "meta_pages",
    integration_id: int | None = None,
    user_id: int | None = None,
    token_received: bool | None = None,
) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/me/accounts"
    params = {
        "fields": "id,name,access_token,tasks,picture,instagram_business_account{id,username,profile_picture_url,name}",
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    logger.warning(
        "Meta /me/accounts context=%s token_received=%s integration_id=%s user_id=%s status=%s raw_body=%s",
        context,
        token_received,
        integration_id,
        user_id,
        resp.status_code,
        _truncate_meta_log_value(_redact_meta_tokens(response_payload)),
    )
    _raise_meta_api_error(resp)
    data = response_payload if isinstance(response_payload, dict) else {}
    pages = data.get("data", [])
    logger.warning(
        "Meta /me/accounts parsed context=%s integration_id=%s user_id=%s pages_count=%s page_names=%s",
        context,
        integration_id,
        user_id,
        len(pages),
        [str(page.get("name") or "") for page in pages if page.get("name")],
    )
    return pages


def _get_business_page_nodes(
    business_id: str,
    access_token: str,
    edge: str,
) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{business_id}/{edge}"
    params = {
        "fields": "id,name,category,fan_count,followers_count",
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])


def get_business_pages(business_id: str, access_token: str) -> list[dict[str, Any]]:
    return _get_business_page_nodes(business_id, access_token, "owned_pages") + _get_business_page_nodes(
        business_id, access_token, "client_pages"
    )


def fetch_page_info(
    access_token: str,
    page_id: str,
    *,
    fields: str = "id,name",
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}"
    params = {
        "fields": fields,
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    return resp.json()


def fetch_page_info_with_metadata(
    access_token: str,
    page_id: str,
    *,
    fields: str = "id,name",
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}"
    params = {
        "fields": fields,
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    _raise_meta_api_error(resp)
    payload = response_payload if isinstance(response_payload, dict) else {}
    payload["_meta_http_status_code"] = resp.status_code
    payload["_meta_raw_body"] = _truncate_meta_log_value(_redact_meta_tokens(response_payload))
    return payload


def fetch_page_posts(access_token: str, page_id: str, limit: int = 5) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}/posts"
    params = {
        "fields": "id,message,created_time,permalink_url,shares,comments.summary(true),reactions.summary(true)",
        "limit": limit,
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])


def fetch_page_insights(
    access_token: str,
    page_id: str,
    metrics: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    period: str = "day",
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}/insights"
    params = {
        "metric": ",".join(
            metrics
            or [
                "page_reach",
                "page_engaged_users",
                "page_profile_views",
                "page_post_engagements",
                "page_fan_adds",
                "page_fans",
                "page_consumptions",
                "page_consumptions_unique",
                "page_views_total",
            ]
        ),
        "period": period,
        "access_token": access_token,
    }
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    _raise_meta_api_error(resp)
    data = response_payload.get("data", []) if isinstance(response_payload, dict) else []

    metrics_payload: dict[str, Any] = {}
    snapshot_metrics = {"page_fans"}
    for item in data:
        name = str(item.get("name") or "")
        values = item.get("values") or []
        latest_value = values[-1] if values else {}
        if (since is not None or until is not None) and name not in snapshot_metrics:
            numeric_values = [
                value.get("value")
                for value in values
                if isinstance(value, dict) and isinstance(value.get("value"), (int, float))
            ]
            metrics_payload[name] = sum(numeric_values) if numeric_values else latest_value.get("value")
        else:
            metrics_payload[name] = latest_value.get("value")
        metrics_payload[f"{name}_end_time"] = latest_value.get("end_time")
    metrics_payload["_meta_http_status_code"] = resp.status_code
    metrics_payload["_meta_raw_body"] = _truncate_meta_log_value(_redact_meta_tokens(response_payload))
    return metrics_payload


def fetch_page_insights_timeseries(
    access_token: str,
    page_id: str,
    metric: str,
    *,
    since: str | None = None,
    until: str | None = None,
    period: str = "day",
) -> list[dict[str, Any]]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}/insights"
    params = {
        "metric": metric,
        "period": period,
        "access_token": access_token,
    }
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until

    logger.info(
        "[META_HISTORY_AUDIT][request]",
        extra={
            "endpoint": url,
            "metric": metric,
            "since": since,
            "until": until,
            "period": period,
            "page_id": page_id,
            "request_format": "page_insights_timeseries",
        },
    )
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    response_json = resp.json()
    data = response_json.get("data", [])

    if not data:
        logger.info(
            "[META_HISTORY_AUDIT][response]",
            extra={
                "endpoint": url,
                "metric": metric,
                "since": since,
                "until": until,
                "period": period,
                "page_id": page_id,
                "data_points_returned": 0,
                "raw_response": _truncate_meta_log_value(response_json),
            },
        )
        return []

    values = data[0].get("values") or []
    first_value = values[0] if values and isinstance(values[0], dict) else {}
    last_value = values[-1] if values and isinstance(values[-1], dict) else {}
    logger.info(
        "[META_HISTORY_AUDIT][response]",
        extra={
            "endpoint": url,
            "metric": metric,
            "since": since,
            "until": until,
            "period": period,
            "page_id": page_id,
            "data_points_returned": len(values),
            "first_end_time": first_value.get("end_time"),
            "last_end_time": last_value.get("end_time"),
            "first_value": first_value.get("value"),
            "last_value": last_value.get("value"),
            "raw_response": _truncate_meta_log_value(response_json),
        },
    )
    points: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        end_time = value.get("end_time")
        metric_value = value.get("value")
        points.append(
            {
                "date": str(end_time).split("T", 1)[0] if end_time else None,
                "value": metric_value if isinstance(metric_value, (int, float)) else None,
                "end_time": end_time,
            }
        )
    return points


def fetch_post_metrics(access_token: str, post_id: str) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{post_id}/insights"
    params = {
        "metric": "post_impressions",
        "access_token": access_token,
    }
    resp = requests.get(url, params=params, timeout=30)
    _raise_meta_api_error(resp)
    data = resp.json().get("data", [])

    metrics: dict[str, Any] = {}
    for item in data:
        name = str(item.get("name") or "")
        values = item.get("values") or []
        latest_value = values[-1] if values else {}
        value = latest_value.get("value")
        metrics[name] = value
        metrics[f"{name}_end_time"] = latest_value.get("end_time")
    return metrics


def fetch_instagram_insights_metric_with_metadata(
    access_token: str,
    instagram_user_id: str,
    *,
    metric_name: str,
    since: str | None = None,
    until: str | None = None,
    period: str = "day",
    metric_type: str | None = None,
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{instagram_user_id}/insights"
    params: dict[str, Any] = {
        "metric": metric_name,
        "period": period,
        "access_token": access_token,
    }
    if metric_type:
        params["metric_type"] = metric_type
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    resp = requests.get(url, params=params, timeout=30)
    try:
        response_payload = resp.json()
    except ValueError:
        response_payload = resp.text
    _raise_meta_api_error(resp)
    payload = response_payload if isinstance(response_payload, dict) else {}
    payload["_meta_http_status_code"] = resp.status_code
    payload["_meta_raw_body"] = _truncate_meta_log_value(_redact_meta_tokens(response_payload))
    return payload


def _normalize_meta_ad_account_node_id(ad_account_id: str) -> str:
    normalized_id = ad_account_id.strip()
    if normalized_id.startswith("act_"):
        return normalized_id
    return f"act_{normalized_id}"


def fetch_campaign_insights(
    access_token: str,
    ad_account_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    account_node_id = _normalize_meta_ad_account_node_id(ad_account_id)
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{account_node_id}/insights"
    params: dict[str, Any] = {
        "fields": (
            "date_start,date_stop,spend,impressions,reach,clicks,inline_link_clicks,"
            "ctr,cpc,cpm,frequency,actions,cost_per_action_type,"
            "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name"
        ),
        "level": "ad",
        "time_increment": 1,
        "access_token": access_token,
    }
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    logger.info("Fetching Meta campaign insights", extra={"url": url, "ad_account_id": account_node_id})
    resp = requests.get(url, params=params, timeout=60)
    _raise_meta_api_error(resp)
    data = resp.json()
    return data.get("data", [])
