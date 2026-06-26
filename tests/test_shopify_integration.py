from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_shopify_integration.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.crypto import encrypt_secret
from app.db import Base, SessionLocal, engine
from app.deps import get_db
import app.main as main_module
from app.main import app
from app.models import (
    Dataset,
    DatasetFile,
    Integration,
    Report,
    ReportBlock,
    ReportSource,
    ReportVersion,
    ShopifyConnection,
    ShopifyOAuthState,
    ShopifySnapshot,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


SHOPIFY_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Integration.__table__,
    ShopifyConnection.__table__,
    ShopifyOAuthState.__table__,
    Dataset.__table__,
    DatasetFile.__table__,
    ShopifySnapshot.__table__,
    Report.__table__,
    ReportSource.__table__,
    ReportVersion.__table__,
    ReportBlock.__table__,
]


@pytest.fixture(autouse=True)
def shopify_schema():
    Base.metadata.drop_all(bind=engine, tables=SHOPIFY_TABLES)
    Base.metadata.create_all(bind=engine, tables=SHOPIFY_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=SHOPIFY_TABLES)


@pytest.fixture()
def client():
    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth_headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _seed_workspace() -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email="owner@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Owner User",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
                Subscription(workspace_id=workspace.id, plan="core", status="active"),
            ]
        )
        db.commit()
        return {"user_id": user.id, "workspace_id": workspace.id}
    finally:
        db.close()


def _shopify_callback_hmac(secret: str, params: dict[str, str]) -> str:
    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if key not in {"hmac", "signature"})
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def test_shopify_connect_and_callback_persist_encrypted_connection(client, monkeypatch):
    refs = _seed_workspace()
    monkeypatch.setattr(main_module.settings, "shopify_api_key", "shopify-key")
    monkeypatch.setattr(main_module.settings, "shopify_api_secret", "shopify-secret")
    monkeypatch.setattr(main_module.settings, "shopify_redirect_uri", "https://api.example.com/integrations/shopify/callback")
    monkeypatch.setattr(main_module.settings, "shopify_connect_success_redirect", "http://localhost:3000/integrations")
    monkeypatch.setattr(main_module.settings, "shopify_connect_error_redirect", "http://localhost:3000/integrations")

    response = client.get(
        f"/integrations/shopify/connect?workspace_id={refs['workspace_id']}&shop=my-store",
        headers=_auth_headers(refs["user_id"]),
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://my-store.myshopify.com/admin/oauth/authorize?")
    state = parse_qs(urlparse(location).query)["state"][0]

    monkeypatch.setattr(
        main_module,
        "exchange_shopify_code_for_access_token",
        lambda **_kwargs: {"access_token": "shopify-access-token", "scope": "read_orders,read_products"},
    )
    monkeypatch.setattr(
        main_module,
        "fetch_shop_details",
        lambda **_kwargs: {
            "name": "My Test Store",
            "myshopifyDomain": "my-store.myshopify.com",
            "currencyCode": "USD",
        },
    )

    callback_params = {
        "code": "test-code",
        "shop": "my-store.myshopify.com",
        "state": state,
        "timestamp": "1710000000",
    }
    callback_params["hmac"] = _shopify_callback_hmac("shopify-secret", callback_params)

    callback_response = client.get(
        "/integrations/shopify/callback",
        params=callback_params,
        follow_redirects=False,
    )

    assert callback_response.status_code == 302
    assert "provider=shopify" in callback_response.headers["location"]
    assert "status=success" in callback_response.headers["location"]

    status_response = client.get(
        f"/integrations/shopify/status?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["connected"] is True
    assert payload["shop_domain"] == "my-store.myshopify.com"
    assert payload["shop_name"] == "My Test Store"

    db = SessionLocal()
    try:
        connection = db.query(ShopifyConnection).first()
        assert connection is not None
        assert connection.status == "connected"
        assert connection.access_token_encrypted != "shopify-access-token"
        assert connection.shop_domain == "my-store.myshopify.com"
        oauth_state = db.query(ShopifyOAuthState).first()
        assert oauth_state is not None
        assert oauth_state.used_at is not None
    finally:
        db.close()


def test_shopify_sync_creates_dataset_snapshot_and_report(client, monkeypatch):
    refs = _seed_workspace()
    db = SessionLocal()
    try:
        integration = Integration(
            workspace_id=refs["workspace_id"],
            provider="shopify",
            name="Shopify",
            status="connected",
        )
        db.add(integration)
        db.flush()
        connection = ShopifyConnection(
            user_id=refs["user_id"],
            workspace_id=refs["workspace_id"],
            integration_id=integration.id,
            shop_domain="my-store.myshopify.com",
            shop_name="My Test Store",
            access_token_encrypted=encrypt_secret("shopify-access-token"),
            scopes=["read_orders", "read_products"],
            status="connected",
        )
        db.add(connection)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        main_module,
        "fetch_shop_details",
        lambda **_kwargs: {
            "name": "My Test Store",
            "myshopifyDomain": "my-store.myshopify.com",
            "currencyCode": "USD",
        },
    )
    monkeypatch.setattr(
        main_module,
        "fetch_orders_metrics",
        lambda **_kwargs: {
            "currency": "USD",
            "orders_count": 3,
            "total_sales": 420.5,
            "average_order_value": 140.17,
            "sales_by_day": [
                {"date": "2026-06-01", "label": "2026-06-01", "value": 200.0, "orders": 1},
                {"date": "2026-06-02", "label": "2026-06-02", "value": 220.5, "orders": 2},
            ],
            "orders_by_day": [
                {"date": "2026-06-01", "label": "2026-06-01", "value": 1},
                {"date": "2026-06-02", "label": "2026-06-02", "value": 2},
            ],
            "top_products": [
                {"product_id": "prod_1", "title": "Bundle A", "quantity": 2, "revenue": 300.0},
                {"product_id": "prod_2", "title": "Bundle B", "quantity": 1, "revenue": 120.5},
            ],
            "top_variants": [
                {"sku": "SKU-1", "variant_title": "Bundle A / Large", "quantity": 2, "revenue": 300.0}
            ],
            "discounts_total": 12.0,
            "refunds_total": 5.0,
            "raw_orders_count": 3,
            "raw_orders": [{"id": "gid://shopify/Order/1"}],
            "summary": "3 orders generated 420.5 USD in sales.",
        },
    )

    class _FakeS3:
        def put_object(self, **_kwargs):
            return {"ok": True}

    monkeypatch.setattr(main_module.boto3, "client", lambda *_args, **_kwargs: _FakeS3())
    monkeypatch.setattr(main_module, "_generate_and_store_report_thumbnail", lambda **_kwargs: None)

    sync_response = client.post(
        "/integrations/shopify/sync",
        headers=_auth_headers(refs["user_id"]),
        json={"workspace_id": refs["workspace_id"], "timeframe": "last_30d"},
    )

    assert sync_response.status_code == 200
    sync_payload = sync_response.json()
    assert sync_payload["status"] == "uploaded"
    assert sync_payload["metrics"]["revenue"] == 420.5
    assert sync_payload["metrics"]["orders"] == 3
    assert sync_payload["metrics"]["aov"] == 140.17

    report_response = client.post(
        "/reports/shopify",
        headers=_auth_headers(refs["user_id"]),
        json={"dataset_id": sync_payload["dataset_id"], "title": "Shopify Weekly Report"},
    )

    assert report_response.status_code == 200
    report_payload = report_response.json()
    assert report_payload["status"] == "ready"
    assert report_payload["selected_integration_metadata"]["integration_type"] == "shopify"

    disconnect_response = client.delete(
        f"/integrations/shopify/disconnect?workspace_id={refs['workspace_id']}",
        headers=_auth_headers(refs["user_id"]),
    )
    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["status"] == "disconnected"

    db = SessionLocal()
    try:
        dataset = db.get(Dataset, sync_payload["dataset_id"])
        assert dataset is not None
        assert dataset.data["integration_type"] == "shopify"
        assert dataset.data["normalized_report_metrics"]["revenue"] == 420.5

        dataset_file = db.get(DatasetFile, sync_payload["dataset_file_id"])
        assert dataset_file is not None
        assert dataset_file.content_type == "text/csv"

        snapshot = db.query(ShopifySnapshot).first()
        assert snapshot is not None
        assert snapshot.dataset_id == dataset.id

        report = db.get(Report, report_payload["report_id"])
        assert report is not None
        report_source = db.query(ReportSource).filter(ReportSource.report_id == report.id).first()
        assert report_source is not None
        assert report_source.source_type == "shopify"
        blocks = db.query(ReportBlock).join(ReportVersion, ReportVersion.id == ReportBlock.report_version_id).filter(
            ReportVersion.report_id == report.id
        ).all()
        assert len(blocks) == 5

        connection = db.query(ShopifyConnection).first()
        assert connection is not None
        assert connection.status == "disconnected"
        assert connection.access_token_encrypted is None
    finally:
        db.close()
