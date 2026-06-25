from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests

from ..config import settings

logger = logging.getLogger(__name__)

META_CAPI_DEFAULT_TIMEOUT_SECONDS = 3
META_CAPI_MAX_ATTEMPTS = 2


def _normalize_email_for_hash(email: str | None) -> str | None:
    value = str(email or "").strip().lower()
    return value or None


def _sha256_hexdigest(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hash_email(email: str | None) -> str | None:
    normalized = _normalize_email_for_hash(email)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _meta_capi_enabled_and_configured() -> bool:
    return bool(
        settings.meta_capi_enabled
        and str(settings.meta_capi_pixel_id or "").strip()
        and str(settings.meta_capi_access_token or "").strip()
    )


def _meta_capi_endpoint_url() -> str:
    api_version = str(settings.meta_capi_api_version or "v25.0").strip() or "v25.0"
    pixel_id = str(settings.meta_capi_pixel_id or "").strip()
    return f"https://graph.facebook.com/{api_version}/{pixel_id}/events"


def _meta_capi_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or "").strip()
            if code and message:
                return f"{code}: {message}"[:300]
            if message:
                return message[:300]
    return str(response.text or response.reason or "Meta CAPI request failed.").strip()[:300]


def send_meta_capi_event(
    event_name: str,
    event_id: str,
    event_source_url: str | None,
    user_email: str | None,
    user_id: str | None,
    client_ip_address: str | None,
    client_user_agent: str | None,
    fbp: str | None,
    fbc: str | None,
    custom_data: dict | None = None,
    action_source: str = "website",
) -> bool:
    if not _meta_capi_enabled_and_configured():
        logger.info(
            "meta_capi_event_skipped",
            extra={
                "event_name": event_name,
                "event_id": event_id,
                "enabled": bool(settings.meta_capi_enabled),
                "has_pixel_id": bool(str(settings.meta_capi_pixel_id or "").strip()),
                "has_access_token": bool(str(settings.meta_capi_access_token or "").strip()),
            },
        )
        return False

    user_data: dict[str, Any] = {}
    hashed_email = _hash_email(user_email)
    if hashed_email:
        user_data["em"] = [hashed_email]
    hashed_external_id = _sha256_hexdigest(user_id)
    if hashed_external_id:
        user_data["external_id"] = [hashed_external_id]
    if client_ip_address:
        user_data["client_ip_address"] = str(client_ip_address).strip()
    if client_user_agent:
        user_data["client_user_agent"] = str(client_user_agent).strip()
    if fbp:
        user_data["fbp"] = str(fbp).strip()
    if fbc:
        user_data["fbc"] = str(fbc).strip()

    event_payload: dict[str, Any] = {
        "event_name": event_name,
        "event_time": int(datetime.now(timezone.utc).timestamp()),
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user_data,
    }
    if event_source_url:
        event_payload["event_source_url"] = str(event_source_url).strip()
    if custom_data:
        event_payload["custom_data"] = dict(custom_data)

    payload: dict[str, Any] = {
        "data": [event_payload],
        "access_token": str(settings.meta_capi_access_token or "").strip(),
    }
    test_event_code = str(settings.meta_capi_test_event_code or "").strip()
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = _meta_capi_endpoint_url()
    last_error_message = "Meta CAPI request failed."
    last_status_code: int | None = None

    for attempt in range(1, META_CAPI_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=META_CAPI_DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error_message = str(exc).strip()[:300] or exc.__class__.__name__
            if attempt >= META_CAPI_MAX_ATTEMPTS:
                logger.warning(
                    "meta_capi_event_failed",
                    extra={
                        "event_name": event_name,
                        "event_id": event_id,
                        "status_code": last_status_code,
                        "reason": last_error_message,
                        "attempt": attempt,
                    },
                )
                return False
            continue

        last_status_code = response.status_code
        if response.ok:
            logger.info(
                "meta_capi_event_sent",
                extra={
                    "event_name": event_name,
                    "event_id": event_id,
                    "status_code": response.status_code,
                    "configuration": "meta_capi",
                },
            )
            return True

        last_error_message = _meta_capi_error_message(response)
        if response.status_code < 500 or attempt >= META_CAPI_MAX_ATTEMPTS:
            logger.warning(
                "meta_capi_event_failed",
                extra={
                    "event_name": event_name,
                    "event_id": event_id,
                    "status_code": response.status_code,
                    "reason": last_error_message,
                    "attempt": attempt,
                },
            )
            return False

    logger.warning(
        "meta_capi_event_failed",
        extra={
            "event_name": event_name,
            "event_id": event_id,
            "status_code": last_status_code,
            "reason": last_error_message,
            "attempt": META_CAPI_MAX_ATTEMPTS,
        },
    )
    return False
