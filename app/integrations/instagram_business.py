import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from jose import JWTError, jwt

from ..config import settings
from ..errors import http_error
from .meta_ads import oauth_connect_pages_url

logger = logging.getLogger(__name__)

INSTAGRAM_BUSINESS_SCOPES = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "read_insights",
    "instagram_basic",
    "business_management",
]
INSTAGRAM_BUSINESS_OAUTH_SCOPE = ",".join(INSTAGRAM_BUSINESS_SCOPES)
INSTAGRAM_BUSINESS_CALLBACK_PATH = "/integrations/instagram-business/callback"
INSTAGRAM_BUSINESS_OAUTH_STATE_PURPOSE = "instagram_business_oauth"

INSTAGRAM_BUSINESS_LOGIN_PROVIDER = "instagram_business_login"
INSTAGRAM_BUSINESS_LOGIN_AUTH_TYPE = "instagram_login"
INSTAGRAM_BUSINESS_LOGIN_GRAPH_HOST = "graph.instagram.com"
INSTAGRAM_BUSINESS_LOGIN_CALLBACK_PATH = "/integrations/instagram-business-login/callback"
INSTAGRAM_BUSINESS_LOGIN_SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_insights",
]
INSTAGRAM_BUSINESS_LOGIN_OAUTH_SCOPE = ",".join(INSTAGRAM_BUSINESS_LOGIN_SCOPES)
INSTAGRAM_BUSINESS_LOGIN_OAUTH_STATE_PURPOSE = "instagram_business_login_oauth"


def _instagram_business_log_message(event: str, payload: dict[str, Any]) -> str:
    return f"{event} {json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)}"


def _truncate_instagram_business_log_value(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _redact_instagram_business_tokens(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"access_token", "refresh_token", "authorization_code", "code"}:
                text = str(item or "")
                redacted[key] = f"{text[:8]}..." if text else None
            else:
                redacted[key] = _redact_instagram_business_tokens(item)
        return redacted
    if isinstance(value, list):
        return [_redact_instagram_business_tokens(item) for item in value]
    return value


def get_missing_instagram_business_config_fields() -> list[str]:
    missing_fields: list[str] = []
    if not settings.instagram_app_id:
        missing_fields.append("INSTAGRAM_APP_ID")
    if not settings.instagram_app_secret:
        missing_fields.append("INSTAGRAM_APP_SECRET")
    if not settings.instagram_redirect_uri:
        missing_fields.append("INSTAGRAM_REDIRECT_URI")
    return missing_fields


def _instagram_business_login_app_id() -> str | None:
    return str(settings.instagram_business_login_app_id or settings.instagram_app_id or "").strip() or None


def _instagram_business_login_app_secret() -> str | None:
    return str(settings.instagram_business_login_app_secret or settings.instagram_app_secret or "").strip() or None


def _instagram_business_login_redirect_uri_configured() -> str | None:
    return (
        str(settings.instagram_business_login_redirect_uri or settings.instagram_redirect_uri or "").strip()
        or None
    )


def get_missing_instagram_business_login_config_fields() -> list[str]:
    missing_fields: list[str] = []
    if not _instagram_business_login_app_id():
        missing_fields.append("INSTAGRAM_BUSINESS_LOGIN_APP_ID")
    if not _instagram_business_login_app_secret():
        missing_fields.append("INSTAGRAM_BUSINESS_LOGIN_APP_SECRET")
    if not _instagram_business_login_redirect_uri_configured() and not str(settings.api_base_url or "").strip():
        missing_fields.append("INSTAGRAM_BUSINESS_LOGIN_REDIRECT_URI")
    return missing_fields


def _require_instagram_business_login_config() -> None:
    missing_fields = get_missing_instagram_business_login_config_fields()
    if missing_fields:
        raise http_error(
            status_code=500,
            code="instagram_business_login_config_missing",
            message="Missing Instagram Business Login config: " + ", ".join(missing_fields),
        )


def _require_instagram_business_config() -> None:
    missing_fields = get_missing_instagram_business_config_fields()
    if missing_fields:
        raise http_error(
            status_code=500,
            code="instagram_business_config_missing",
            message="Missing Instagram Business config: " + ", ".join(missing_fields),
        )


def get_instagram_business_redirect_uri() -> str:
    _require_instagram_business_config()
    configured_redirect_uri = str(settings.instagram_redirect_uri or "").strip()
    api_base_url = str(settings.api_base_url or "").strip().rstrip("/")
    if api_base_url:
        return f"{api_base_url}{INSTAGRAM_BUSINESS_CALLBACK_PATH}"
    return configured_redirect_uri


def get_instagram_business_login_redirect_uri() -> str:
    _require_instagram_business_login_config()
    api_base_url = str(settings.api_base_url or "").strip().rstrip("/")
    if api_base_url:
        return f"{api_base_url}{INSTAGRAM_BUSINESS_LOGIN_CALLBACK_PATH}"
    return str(_instagram_business_login_redirect_uri_configured() or "").strip()


def encode_instagram_business_state(payload: dict[str, Any], *, expires_seconds: int = 1800) -> str:
    now = datetime.now(timezone.utc)
    signed_payload = {
        "purpose": INSTAGRAM_BUSINESS_OAUTH_STATE_PURPOSE,
        "payload": payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(signed_payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_instagram_business_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise ValueError("invalid_state") from exc
    if payload.get("purpose") != INSTAGRAM_BUSINESS_OAUTH_STATE_PURPOSE:
        raise ValueError("invalid_state")
    signed_payload = payload.get("payload")
    if not isinstance(signed_payload, dict):
        raise ValueError("invalid_state")
    return signed_payload


def encode_instagram_business_login_state(payload: dict[str, Any], *, expires_seconds: int = 1800) -> str:
    now = datetime.now(timezone.utc)
    signed_payload = {
        "purpose": INSTAGRAM_BUSINESS_LOGIN_OAUTH_STATE_PURPOSE,
        "payload": payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(signed_payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_instagram_business_login_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise ValueError("invalid_state") from exc
    if payload.get("purpose") != INSTAGRAM_BUSINESS_LOGIN_OAUTH_STATE_PURPOSE:
        raise ValueError("invalid_state")
    signed_payload = payload.get("payload")
    if not isinstance(signed_payload, dict):
        raise ValueError("invalid_state")
    return signed_payload


def build_instagram_business_auth_url(state: str) -> str:
    auth_url = oauth_connect_pages_url(state, integration_type="instagram_business")
    logger.info(
        _instagram_business_log_message(
            "INSTAGRAM_BUSINESS_AUTH_URL_CREATED",
            {
                "auth_url": auth_url,
                "provider": "instagram_business",
                "uses_facebook_oauth": "facebook.com/" in auth_url,
                "uses_instagram_oauth": "instagram.com/oauth" in auth_url,
                "scopes_requested": INSTAGRAM_BUSINESS_SCOPES,
                "state_present": bool(state),
            },
        )
    )
    return auth_url


def build_instagram_business_login_auth_url(state: str) -> str:
    _require_instagram_business_login_config()
    base_url = str(settings.instagram_oauth_authorize_url or "").strip()
    params = {
        "client_id": _instagram_business_login_app_id(),
        "redirect_uri": get_instagram_business_login_redirect_uri(),
        "scope": INSTAGRAM_BUSINESS_LOGIN_OAUTH_SCOPE,
        "response_type": "code",
        "state": state,
    }
    from urllib.parse import urlencode

    auth_url = f"{base_url}?{urlencode(params)}"
    logger.info(
        _instagram_business_log_message(
            "INSTAGRAM_BUSINESS_LOGIN_OAUTH_STARTED",
            {
                "provider": INSTAGRAM_BUSINESS_LOGIN_PROVIDER,
                "auth_type": INSTAGRAM_BUSINESS_LOGIN_AUTH_TYPE,
                "graph_host": INSTAGRAM_BUSINESS_LOGIN_GRAPH_HOST,
                "scopes_requested": INSTAGRAM_BUSINESS_LOGIN_SCOPES,
                "redirect_uri": params["redirect_uri"],
                "state_present": bool(state),
            },
        )
    )
    return auth_url


def exchange_instagram_business_code_for_token(code: str) -> dict[str, Any]:
    _require_instagram_business_config()
    url = str(settings.instagram_oauth_access_token_url or "").strip()
    data = {
        "client_id": settings.instagram_app_id,
        "client_secret": settings.instagram_app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": get_instagram_business_redirect_uri(),
        "code": code,
    }
    response = requests.post(url, data=data, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code != 200:
        logger.warning(
            _instagram_business_log_message(
                "INSTAGRAM_BUSINESS_CONNECT_FAILED",
                {
                    "stage": "token_exchange",
                    "status_code": response.status_code,
                    "response_body": _truncate_instagram_business_log_value(
                        _redact_instagram_business_tokens(payload)
                    ),
                },
            )
        )
        message = payload.get("error_message") if isinstance(payload, dict) else str(payload)
        raise http_error(400, "instagram_business_token_exchange_failed", str(message or "Instagram token exchange failed."))
    if isinstance(payload, dict):
        payload["_http_status_code"] = response.status_code
        payload["_raw_body"] = _truncate_instagram_business_log_value(_redact_instagram_business_tokens(payload))
        return payload
    return {"_http_status_code": response.status_code, "_raw_body": _truncate_instagram_business_log_value(payload)}


def exchange_instagram_business_login_code_for_token(code: str) -> dict[str, Any]:
    _require_instagram_business_login_config()
    url = str(settings.instagram_oauth_access_token_url or "").strip()
    data = {
        "client_id": _instagram_business_login_app_id(),
        "client_secret": _instagram_business_login_app_secret(),
        "grant_type": "authorization_code",
        "redirect_uri": get_instagram_business_login_redirect_uri(),
        "code": code,
    }
    response = requests.post(url, data=data, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code != 200:
        logger.warning(
            _instagram_business_log_message(
                "INSTAGRAM_BUSINESS_LOGIN_CONNECT_FAILED",
                {
                    "stage": "token_exchange",
                    "provider": INSTAGRAM_BUSINESS_LOGIN_PROVIDER,
                    "auth_type": INSTAGRAM_BUSINESS_LOGIN_AUTH_TYPE,
                    "status_code": response.status_code,
                    "response_body": _truncate_instagram_business_log_value(
                        _redact_instagram_business_tokens(payload)
                    ),
                },
            )
        )
        message = payload.get("error_message") if isinstance(payload, dict) else str(payload)
        raise http_error(
            400,
            "instagram_business_login_token_exchange_failed",
            str(message or "Instagram Business Login token exchange failed."),
        )
    if isinstance(payload, dict):
        payload["_http_status_code"] = response.status_code
        payload["_raw_body"] = _truncate_instagram_business_log_value(_redact_instagram_business_tokens(payload))
        return payload
    return {"_http_status_code": response.status_code, "_raw_body": _truncate_instagram_business_log_value(payload)}


def fetch_instagram_business_profile(access_token: str) -> dict[str, Any]:
    _require_instagram_business_config()
    base = str(settings.instagram_graph_api_base or "").strip().rstrip("/")
    url = f"{base}/me"
    params = {
        "fields": "id,username,account_type,name,profile_picture_url",
        "access_token": access_token,
    }
    response = requests.get(url, params=params, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code != 200:
        logger.warning(
            _instagram_business_log_message(
                "INSTAGRAM_BUSINESS_CONNECT_FAILED",
                {
                    "stage": "account_discovery",
                    "status_code": response.status_code,
                    "response_body": _truncate_instagram_business_log_value(
                        _redact_instagram_business_tokens(payload)
                    ),
                },
            )
        )
        message = payload.get("error_message") if isinstance(payload, dict) else str(payload)
        raise http_error(400, "instagram_business_profile_fetch_failed", str(message or "Instagram account fetch failed."))
    return payload if isinstance(payload, dict) else {}


def fetch_instagram_business_login_profile(access_token: str) -> dict[str, Any]:
    _require_instagram_business_login_config()
    base = str(settings.instagram_graph_api_base or "").strip().rstrip("/")
    url = f"{base}/me"
    params = {
        "fields": "id,username,account_type,name,profile_picture_url,followers_count",
    }
    response = requests.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code != 200:
        logger.warning(
            _instagram_business_log_message(
                "INSTAGRAM_BUSINESS_LOGIN_CONNECT_FAILED",
                {
                    "stage": "profile_fetch",
                    "provider": INSTAGRAM_BUSINESS_LOGIN_PROVIDER,
                    "auth_type": INSTAGRAM_BUSINESS_LOGIN_AUTH_TYPE,
                    "graph_host": INSTAGRAM_BUSINESS_LOGIN_GRAPH_HOST,
                    "status_code": response.status_code,
                    "response_body": _truncate_instagram_business_log_value(
                        _redact_instagram_business_tokens(payload)
                    ),
                },
            )
        )
        message = payload.get("error_message") if isinstance(payload, dict) else str(payload)
        raise http_error(
            400,
            "instagram_business_login_profile_fetch_failed",
            str(message or "Instagram Business Login account fetch failed."),
        )
    if isinstance(payload, dict):
        payload["_http_status_code"] = response.status_code
        payload["_raw_body"] = _truncate_instagram_business_log_value(_redact_instagram_business_tokens(payload))
        return payload
    return {"_http_status_code": response.status_code, "_raw_body": _truncate_instagram_business_log_value(payload)}


def fetch_instagram_business_login_insights_metric_with_metadata(
    access_token: str,
    instagram_user_id: str,
    *,
    metric_name: str,
    since: str | None = None,
    until: str | None = None,
    period: str = "day",
) -> dict[str, Any]:
    _require_instagram_business_login_config()
    version = str(settings.instagram_graph_api_version or "").strip().strip("/")
    base = str(settings.instagram_graph_api_base or "").strip().rstrip("/")
    version_path = f"/{version}" if version else ""
    url = f"{base}{version_path}/{instagram_user_id}/insights"
    params: dict[str, str] = {
        "metric": metric_name,
        "period": period,
    }
    if since:
        params["since"] = str(since)
    if until:
        params["until"] = str(until)
    response = requests.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    response_payload: dict[str, Any] = payload if isinstance(payload, dict) else {"raw": payload}
    response_payload["_instagram_http_status_code"] = response.status_code
    response_payload["_instagram_raw_body"] = _truncate_instagram_business_log_value(
        _redact_instagram_business_tokens(payload)
    )
    response_payload["_instagram_graph_host"] = INSTAGRAM_BUSINESS_LOGIN_GRAPH_HOST
    response_payload["_instagram_graph_endpoint"] = f"/{instagram_user_id}/insights"
    response_payload["_instagram_metric_name"] = metric_name
    response_payload["_instagram_period"] = period
    return response_payload
