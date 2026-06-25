from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests
from fastapi import HTTPException

from ..config import settings
from ..errors import http_error

SHOPIFY_PROVIDER = "shopify"
SHOPIFY_STATUS_CONNECTED = "connected"
SHOPIFY_STATUS_DISCONNECTED = "disconnected"
SHOPIFY_STATUS_ERROR = "error"
SHOPIFY_OAUTH_STATE_PURPOSE = "shopify_oauth"
SHOPIFY_COMPLIANCE_TOPICS = {
    "app/uninstalled",
    "customers/data_request",
    "customers/redact",
    "shop/redact",
}
SHOPIFY_DEFAULT_TIMEFRAME = "last_30d"
SHOPIFY_SCOPE_LIST = [scope.strip() for scope in str(settings.shopify_scopes or "").split(",") if scope.strip()]


def shopify_missing_config() -> list[str]:
    missing: list[str] = []
    if not str(settings.shopify_api_key or "").strip():
        missing.append("SHOPIFY_API_KEY")
    if not str(settings.shopify_api_secret or "").strip():
        missing.append("SHOPIFY_API_SECRET")
    if not str(settings.shopify_redirect_uri or "").strip():
        missing.append("SHOPIFY_REDIRECT_URI")
    return missing


def normalize_shop_domain(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        raise http_error(422, "invalid_shop_domain", "shop is required.")
    if "://" in value:
        value = urlsplit(value).netloc or value
    value = value.split("/")[0].strip(".")
    if "." not in value:
        value = f"{value}.myshopify.com"
    if not value.endswith(".myshopify.com"):
        raise http_error(422, "invalid_shop_domain", "shop must be a valid myshopify.com domain.")
    if not all(part and part.replace("-", "").isalnum() for part in value.split(".")):
        raise http_error(422, "invalid_shop_domain", "shop must be a valid myshopify.com domain.")
    return value


def shopify_authorize_url(*, shop_domain: str, state: str) -> str:
    missing = shopify_missing_config()
    if missing:
        raise http_error(500, "shopify_config_missing", f"Missing Shopify config: {', '.join(missing)}.")
    query = urlencode(
        {
            "client_id": settings.shopify_api_key,
            "scope": ",".join(SHOPIFY_SCOPE_LIST),
            "redirect_uri": settings.shopify_redirect_uri,
            "state": state,
        }
    )
    return f"https://{shop_domain}/admin/oauth/authorize?{query}"


def shopify_callback_hmac_valid(params: dict[str, Any]) -> bool:
    secret = str(settings.shopify_api_secret or "").encode("utf-8")
    received = str(params.get("hmac") or "").strip()
    if not secret or not received:
        return False
    message_pairs: list[tuple[str, str]] = []
    for key, value in sorted(params.items()):
        if key in {"hmac", "signature"}:
            continue
        if isinstance(value, list):
            serialized = ",".join(str(item) for item in value)
        else:
            serialized = str(value)
        message_pairs.append((key, serialized))
    message = "&".join(f"{key}={value}" for key, value in message_pairs)
    digest = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, received)


def shopify_webhook_hmac_valid(raw_body: bytes, encoded_hmac: str | None) -> bool:
    secret = str(settings.shopify_api_secret or "").encode("utf-8")
    if not secret or not encoded_hmac:
        return False
    digest = hmac.new(secret, raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, str(encoded_hmac))


def exchange_code_for_access_token(*, shop_domain: str, code: str) -> dict[str, Any]:
    missing = shopify_missing_config()
    if missing:
        raise http_error(500, "shopify_config_missing", f"Missing Shopify config: {', '.join(missing)}.")
    response = requests.post(
        f"https://{shop_domain}/admin/oauth/access_token",
        json={
            "client_id": settings.shopify_api_key,
            "client_secret": settings.shopify_api_secret,
            "code": code,
        },
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code < 200 or response.status_code >= 300:
        raise http_error(
            502,
            "shopify_token_exchange_failed",
            str(payload.get("error_description") or payload.get("error") or "Shopify token exchange failed."),
        )
    return payload


def _graphql_headers(access_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }


def shopify_graphql(
    *,
    shop_domain: str,
    access_token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    url = f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/graphql.json"
    last_error: HTTPException | None = None
    for attempt in range(1, max_attempts + 1):
        response = requests.post(
            url,
            headers=_graphql_headers(access_token),
            json={"query": query, "variables": variables or {}},
            timeout=45,
        )
        if response.status_code in {429, 430} and attempt < max_attempts:
            time.sleep(1.5 * attempt)
            continue
        if response.status_code in {401, 403}:
            raise http_error(response.status_code, "shopify_reconnect_required", "Shopify reconnect required.")
        if response.status_code < 200 or response.status_code >= 300:
            last_error = http_error(502, "shopify_graphql_failed", "Shopify GraphQL request failed.")
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise last_error
        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            raise http_error(502, "shopify_graphql_failed", "Shopify GraphQL returned errors.")
        return payload
    if last_error is not None:
        raise last_error
    raise http_error(502, "shopify_graphql_failed", "Shopify GraphQL request failed.")


def fetch_shop_details(*, shop_domain: str, access_token: str) -> dict[str, Any]:
    query = """
    query ShopDetails {
      shop {
        name
        myshopifyDomain
        currencyCode
      }
    }
    """
    payload = shopify_graphql(shop_domain=shop_domain, access_token=access_token, query=query)
    return ((payload.get("data") or {}).get("shop") or {}) if isinstance(payload, dict) else {}


def resolve_shopify_timeframe(
    timeframe: str | None,
    *,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, str]:
    preset = str(timeframe or SHOPIFY_DEFAULT_TIMEFRAME).strip().lower()
    today = datetime.now(timezone.utc).date()
    preset_days = {
        "last_7d": 7,
        "last_7_days": 7,
        "last_30d": 30,
        "last_30_days": 30,
        "last_60d": 60,
        "last_60_days": 60,
    }
    if start_date or end_date or preset == "custom":
        if not start_date or not end_date:
            raise http_error(422, "invalid_date_range", "start_date and end_date are required for custom timeframe.")
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
        if start > end:
            raise http_error(422, "invalid_date_range", "start_date must be on or before end_date.")
        return {
            "key": "custom",
            "preset": "custom",
            "label": "Custom",
            "since": start.isoformat(),
            "until": end.isoformat(),
        }
    days = preset_days.get(preset, 30)
    end = today
    start = end - timedelta(days=days - 1)
    labels = {7: "Last 7 days", 30: "Last 30 days", 60: "Last 60 days"}
    return {
        "key": f"last_{days}_days",
        "preset": f"last_{days}_days",
        "label": labels[days],
        "since": start.isoformat(),
        "until": end.isoformat(),
    }


def _money_amount(node: dict[str, Any] | None, key: str) -> Decimal:
    if not isinstance(node, dict):
        return Decimal("0")
    money = (((node.get(key) or {}).get("shopMoney") or {}).get("amount")) if isinstance(node.get(key), dict) else None
    if money in (None, ""):
        return Decimal("0")
    return Decimal(str(money))


def fetch_orders_metrics(
    *,
    shop_domain: str,
    access_token: str,
    timeframe: dict[str, str],
) -> dict[str, Any]:
    start_dt = f"{timeframe['since']}T00:00:00Z"
    end_dt = f"{timeframe['until']}T23:59:59Z"
    query_filter = f"processed_at:>={start_dt} processed_at:<={end_dt}"
    query = """
    query ShopifyOrders($cursor: String, $query: String!) {
      orders(first: 100, after: $cursor, query: $query, sortKey: PROCESSED_AT, reverse: false) {
        pageInfo { hasNextPage endCursor }
        edges {
          cursor
          node {
            id
            name
            processedAt
            createdAt
            currentTotalPriceSet { shopMoney { amount currencyCode } }
            currentTotalDiscountsSet { shopMoney { amount currencyCode } }
            totalRefundedSet { shopMoney { amount currencyCode } }
            lineItems(first: 50) {
              edges {
                node {
                  title
                  sku
                  quantity
                  variantTitle
                  product { id title }
                  discountedTotalSet { shopMoney { amount currencyCode } }
                  originalTotalSet { shopMoney { amount currencyCode } }
                }
              }
            }
          }
        }
      }
    }
    """
    cursor: str | None = None
    orders: list[dict[str, Any]] = []
    currency: str | None = None
    while True:
        payload = shopify_graphql(
            shop_domain=shop_domain,
            access_token=access_token,
            query=query,
            variables={"cursor": cursor, "query": query_filter},
        )
        orders_payload = (((payload.get("data") or {}).get("orders")) or {}) if isinstance(payload, dict) else {}
        edges = orders_payload.get("edges") if isinstance(orders_payload, dict) else []
        for edge in edges or []:
            node = edge.get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict):
                orders.append(node)
                currency = currency or (
                    ((((node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}).get("currencyCode")))
                )
        page_info = orders_payload.get("pageInfo") if isinstance(orders_payload, dict) else {}
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        cursor = str(page_info.get("endCursor") or "").strip() or None
        if not cursor:
            break

    sales_by_day: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    orders_by_day: dict[str, int] = defaultdict(int)
    top_products_map: dict[str, dict[str, Any]] = {}
    top_variants_map: dict[str, dict[str, Any]] = {}
    total_sales = Decimal("0")
    total_discounts = Decimal("0")
    total_refunds = Decimal("0")
    for order in orders:
        total_price = _money_amount(order, "currentTotalPriceSet")
        total_sales += total_price
        total_discounts += _money_amount(order, "currentTotalDiscountsSet")
        total_refunds += _money_amount(order, "totalRefundedSet")
        processed_at = str(order.get("processedAt") or order.get("createdAt") or "")[:10]
        if processed_at:
            sales_by_day[processed_at] += total_price
            orders_by_day[processed_at] += 1
        line_item_edges = (((order.get("lineItems") or {}).get("edges")) or []) if isinstance(order.get("lineItems"), dict) else []
        for edge in line_item_edges:
            line_item = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(line_item, dict):
                continue
            quantity = int(line_item.get("quantity") or 0)
            revenue = _money_amount(line_item, "discountedTotalSet") or _money_amount(line_item, "originalTotalSet")
            product = line_item.get("product") if isinstance(line_item.get("product"), dict) else {}
            product_key = str(product.get("id") or line_item.get("title") or "unknown_product")
            product_entry = top_products_map.setdefault(
                product_key,
                {
                    "product_id": str(product.get("id") or "") or None,
                    "title": str(product.get("title") or line_item.get("title") or "Untitled product"),
                    "quantity": 0,
                    "revenue": Decimal("0"),
                },
            )
            product_entry["quantity"] += quantity
            product_entry["revenue"] += revenue

            variant_key = str(line_item.get("sku") or line_item.get("variantTitle") or line_item.get("title") or "unknown_variant")
            variant_entry = top_variants_map.setdefault(
                variant_key,
                {
                    "sku": str(line_item.get("sku") or "") or None,
                    "variant_title": str(line_item.get("variantTitle") or line_item.get("title") or "Untitled variant"),
                    "quantity": 0,
                    "revenue": Decimal("0"),
                },
            )
            variant_entry["quantity"] += quantity
            variant_entry["revenue"] += revenue

    sales_by_day_payload = [
        {
            "date": day,
            "label": day,
            "value": round(float(total), 2),
            "orders": int(orders_by_day.get(day, 0)),
        }
        for day, total in sorted(sales_by_day.items())
    ]
    top_products = sorted(
        [
            {**item, "revenue": round(float(item["revenue"]), 2)}
            for item in top_products_map.values()
        ],
        key=lambda item: (item["revenue"], item["quantity"]),
        reverse=True,
    )[:10]
    top_variants = sorted(
        [
            {**item, "revenue": round(float(item["revenue"]), 2)}
            for item in top_variants_map.values()
        ],
        key=lambda item: (item["revenue"], item["quantity"]),
        reverse=True,
    )[:10]
    orders_count = len(orders)
    average_order_value = round(float(total_sales / orders_count), 2) if orders_count else 0.0
    return {
        "currency": currency or "USD",
        "orders_count": orders_count,
        "total_sales": round(float(total_sales), 2),
        "average_order_value": average_order_value,
        "sales_by_day": sales_by_day_payload,
        "orders_by_day": [
            {"date": day, "label": day, "value": int(count)}
            for day, count in sorted(orders_by_day.items())
        ],
        "top_products": top_products,
        "top_variants": top_variants,
        "discounts_total": round(float(total_discounts), 2),
        "refunds_total": round(float(total_refunds), 2),
        "raw_orders_count": orders_count,
        "raw_orders": orders,
        "summary": (
            "No orders were found for the selected period."
            if orders_count == 0
            else f"{orders_count} orders generated {round(float(total_sales), 2)} {currency or 'USD'} in sales."
        ),
    }
