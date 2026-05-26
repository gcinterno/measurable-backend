from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

TEST_DB_PATH = Path("/tmp/measurable_billing_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_URL", "https://app.measurable.test")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_123")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_123")
os.environ.setdefault("STRIPE_PRICE_STARTER_MONTHLY", "price_starter")
os.environ.setdefault("STRIPE_PRICE_PRO_MONTHLY", "price_pro")
os.environ.setdefault("STRIPE_PRICE_ADVANCED_MONTHLY", "price_advanced")
os.environ.setdefault("STRIPE_BILLING_PORTAL_RETURN_URL", "https://app.measurable.test/settings/billing")

from app.db import Base, SessionLocal, engine
from app.deps import get_db
import app.main as main_module
from app.main import app
from app.models import Dataset, Report, Subscription, User, Workspace, WorkspaceMember
from app.security import create_access_token, hash_password


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"


BILLING_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMember.__table__,
    Subscription.__table__,
    Dataset.__table__,
    Report.__table__,
]


@pytest.fixture(autouse=True)
def billing_schema():
    Base.metadata.drop_all(bind=engine, tables=BILLING_TABLES)
    Base.metadata.create_all(bind=engine, tables=BILLING_TABLES)
    yield
    Base.metadata.drop_all(bind=engine, tables=BILLING_TABLES)


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


def _seed_billing_user(plan: str = "free", reports_this_month: int = 0) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = User(
            email=f"{plan}@example.com",
            password_hash=hash_password("Password123!"),
            full_name="Billing Owner",
            email_verified=True,
            auth_provider="email",
            is_active=True,
        )
        workspace = Workspace(name="Billing Workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        dataset = Dataset(
            workspace_id=workspace.id,
            name="Billing dataset",
            description="Test",
            data={"integration_type": "csv_upload"},
        )
        db.add(dataset)
        db.flush()
        subscription = Subscription(
            workspace_id=workspace.id,
            plan=plan,
            status="active",
            billing_status="free" if plan == "free" else "active",
        )
        main_module.apply_plan_entitlements(subscription, plan)
        db.add(subscription)
        db.flush()

        for index in range(reports_this_month):
            db.add(
                Report(
                    workspace_id=workspace.id,
                    dataset_id=dataset.id,
                    name=f"Report {index + 1}",
                    description="{}",
                )
            )

        db.commit()
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "subscription_id": subscription.id,
        }
    finally:
        db.close()


def _fake_stripe(calls: dict[str, list], event_factory=None):
    subscriptions_by_id: dict[str, dict] = {}
    subscriptions_by_customer: dict[str, list[dict]] = {}

    class FakeStripeError(Exception):
        def __init__(self, message: str, *, code: str):
            super().__init__(message)
            self.code = code

    def create_customer(**kwargs):
        calls.setdefault("customers", []).append(kwargs)
        return {"id": "cus_test_123"}

    def create_checkout_session(**kwargs):
        calls.setdefault("checkout_sessions", []).append(kwargs)
        return {"url": "https://checkout.stripe.test/session"}

    def create_portal_session(**kwargs):
        calls.setdefault("portal_sessions", []).append(kwargs)
        return {"url": "https://billing.stripe.test/portal"}

    def retrieve_subscription(subscription_id):
        calls.setdefault("subscription_retrieve", []).append({"subscription_id": subscription_id})
        subscription = subscriptions_by_id.get(subscription_id)
        if subscription is None:
            raise FakeStripeError("No such subscription", code="resource_missing")
        return subscription

    def list_subscriptions(**kwargs):
        calls.setdefault("subscription_list", []).append(kwargs)
        customer = kwargs.get("customer")
        return {"data": list(subscriptions_by_customer.get(customer, []))}

    def modify_subscription(subscription_id, **kwargs):
        calls.setdefault("subscription_modify", []).append(
            {"subscription_id": subscription_id, **kwargs}
        )
        existing = subscriptions_by_id.get(subscription_id)
        if existing is None:
            raise FakeStripeError("No such subscription", code="resource_missing")
        items = kwargs.get("items") or []
        price = None
        item_id = None
        if items:
            item_id = items[0].get("id")
            price = items[0].get("price")
        updated = {
            **existing,
            "cancel_at_period_end": kwargs.get("cancel_at_period_end", existing.get("cancel_at_period_end")),
            "items": {
                "data": [
                    {
                        "id": item_id or existing["items"]["data"][0].get("id"),
                        "price": {"id": price or existing["items"]["data"][0]["price"].get("id")},
                    }
                ]
            },
        }
        subscriptions_by_id[subscription_id] = updated
        customer_id = updated.get("customer")
        if customer_id:
            subscriptions_by_customer[customer_id] = [
                updated if item.get("id") == subscription_id else item
                for item in subscriptions_by_customer.get(customer_id, [])
            ] or [updated]
        return updated

    def construct_event(payload, sig_header, secret):
        calls.setdefault("webhook_construct", []).append(
            {"payload": payload, "sig_header": sig_header, "secret": secret}
        )
        if event_factory is not None:
            return event_factory(payload, sig_header, secret)
        return json.loads(payload.decode("utf-8"))

    return SimpleNamespace(
        Customer=SimpleNamespace(create=create_customer),
        Subscription=SimpleNamespace(
            retrieve=retrieve_subscription,
            list=list_subscriptions,
            modify=modify_subscription,
        ),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create_checkout_session)),
        billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create_portal_session)),
        Webhook=SimpleNamespace(construct_event=construct_event),
        _subscriptions_by_id=subscriptions_by_id,
        _subscriptions_by_customer=subscriptions_by_customer,
    )


def test_get_plan_entitlements_matches_official_pricing():
    free = main_module.get_plan_entitlements("free")
    starter = main_module.get_plan_entitlements("starter")
    pro = main_module.get_plan_entitlements("pro")
    advanced = main_module.get_plan_entitlements("advanced")

    assert free["reports_limit_monthly"] == 10
    assert free["slides_per_report_limit"] == 5
    assert free["measurable_watermark"] is True
    assert starter["price_monthly_usd"] == 19
    assert starter["export_pptx"] is True
    assert pro["scheduled_reports_limit"] == 3
    assert advanced["reports_limit_monthly"] is None
    assert advanced["unlimited_reports"] is True


def test_billing_me_returns_free_entitlements(client):
    refs = _seed_billing_user(plan="free", reports_this_month=2)

    response = client.get("/billing/me", headers=_auth_headers(refs["user_id"]))

    assert response.status_code == 200
    body = response.json()
    assert body["plan_code"] == "free"
    assert body["plan_name"] == "Free"
    assert body["billing_status"] == "free"
    assert body["price_monthly_usd"] == 0
    assert body["reports_limit_monthly"] == 10
    assert body["reports_used_current_month"] == 2
    assert body["slides_per_report_limit"] == 5
    assert body["export_pptx"] is False
    assert body["measurable_watermark"] is True


@pytest.mark.parametrize(
    ("plan_code", "expected_price_id"),
    [
        ("starter", "price_starter"),
        ("pro", "price_pro"),
        ("advanced", "price_advanced"),
    ],
)
def test_create_checkout_session_for_paid_plans(client, monkeypatch, plan_code, expected_price_id):
    refs = _seed_billing_user(plan="free")
    calls: dict[str, list] = {}
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: _fake_stripe(calls))

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": plan_code},
    )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "checkout",
        "checkout_url": "https://checkout.stripe.test/session",
        "plan_code": plan_code,
        "billing_status": None,
        "plan_name": plan_code.title(),
        "price_monthly_usd": {"starter": 19, "pro": 39, "advanced": 99}[plan_code],
        "current_period_end": None,
    }
    assert calls["customers"][0]["metadata"]["user_id"] == str(refs["user_id"])
    assert calls["checkout_sessions"][0]["line_items"][0]["price"] == expected_price_id
    assert calls["checkout_sessions"][0]["metadata"]["plan_code"] == plan_code


def test_create_checkout_session_updates_existing_subscription_instead_of_creating_duplicate(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_start": 1770000000,
        "current_period_end": 1772592000,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "pro"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "updated"
    assert body["checkout_url"] is None
    assert body["plan_code"] == "pro"
    assert body["billing_status"] == "active"
    assert body["plan_name"] == "Pro"
    assert body["price_monthly_usd"] == 39
    assert body["current_period_end"] is not None
    assert "checkout_sessions" not in calls
    assert calls["subscription_modify"][0]["subscription_id"] == "sub_test_123"
    assert calls["subscription_modify"][0]["items"][0]["price"] == "price_pro"
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        assert subscription.plan == "pro"
        assert subscription.stripe_subscription_id == "sub_test_123"
        assert subscription.stripe_price_id == "price_pro"
    finally:
        db.close()


def test_create_checkout_session_returns_already_on_plan_for_same_price(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": False,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "starter"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "already_on_plan",
        "checkout_url": None,
        "plan_code": "starter",
        "billing_status": "active",
        "plan_name": "Starter",
        "price_monthly_usd": 19,
        "current_period_end": None,
    }
    assert "checkout_sessions" not in calls
    assert "subscription_modify" not in calls


def test_create_checkout_session_reactivates_cancel_at_period_end_subscription(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": True,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        subscription.cancel_at_period_end = True
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "starter"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "updated"
    assert response.json()["plan_name"] == "Starter"
    assert calls["subscription_modify"][0]["cancel_at_period_end"] is False


def test_create_checkout_session_creates_checkout_when_existing_subscription_is_canceled(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "canceled",
        "cancel_at_period_end": False,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "advanced"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "checkout"
    assert response.json()["plan_name"] == "Advanced"
    assert calls["checkout_sessions"][0]["line_items"][0]["price"] == "price_advanced"


def test_create_checkout_session_uses_customer_active_subscription_when_local_subscription_is_stale(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    active_subscription = {
        "id": "sub_active_customer_level",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_start": 1770000000,
        "current_period_end": 1772592000,
        "items": {"data": [{"id": "si_test_456", "price": {"id": "price_starter"}}]},
    }
    fake_stripe._subscriptions_by_id["sub_active_customer_level"] = active_subscription
    fake_stripe._subscriptions_by_customer["cus_test_123"] = [active_subscription]
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_missing_local_pointer"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/create-checkout-session",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "advanced"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "updated"
    assert body["plan_name"] == "Advanced"
    assert body["current_period_end"] is not None
    assert calls["subscription_modify"][0]["subscription_id"] == "sub_active_customer_level"
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        assert subscription.stripe_subscription_id == "sub_active_customer_level"
        assert subscription.plan == "advanced"
    finally:
        db.close()


def test_plan_change_preview_for_free_user_returns_checkout_without_confirmation(client):
    refs = _seed_billing_user(plan="free")

    response = client.post(
        "/billing/plan-change-preview",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "starter"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "action_mode": "checkout",
        "requires_confirmation": False,
        "billing_status": "free",
        "current_period_end": None,
        "billing_note": "Your subscription will be updated in Stripe. Any prorated adjustment will be handled automatically by Stripe.",
        "current_plan": {
            "plan_code": "free",
            "plan_name": "Free",
            "price_monthly_usd": 0,
            "reports_limit_monthly": 10,
            "slides_per_report_limit": 5,
            "export_pdf": True,
            "export_pptx": False,
            "brand_personalization": False,
            "measurable_watermark": True,
            "scheduled_reports_limit": 0,
        },
        "new_plan": {
            "plan_code": "starter",
            "plan_name": "Starter",
            "price_monthly_usd": 19,
            "reports_limit_monthly": 10,
            "slides_per_report_limit": 10,
            "export_pdf": True,
            "export_pptx": True,
            "brand_personalization": True,
            "measurable_watermark": False,
            "scheduled_reports_limit": 0,
        },
    }


def test_plan_change_preview_for_paid_user_requires_confirmation_and_update_mode(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": False,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/plan-change-preview",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "pro"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action_mode"] == "updated"
    assert body["requires_confirmation"] is True
    assert body["current_plan"]["plan_code"] == "starter"
    assert body["new_plan"]["plan_code"] == "pro"
    assert body["new_plan"]["price_monthly_usd"] == 39
    assert body["new_plan"]["slides_per_report_limit"] == 15
    assert body["new_plan"]["export_pptx"] is True
    assert body["new_plan"]["brand_personalization"] is True
    assert body["new_plan"]["scheduled_reports_limit"] == 3


def test_plan_change_preview_for_same_paid_plan_returns_already_on_plan(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    calls: dict[str, list] = {}
    fake_stripe = _fake_stripe(calls)
    fake_stripe._subscriptions_by_id["sub_test_123"] = {
        "id": "sub_test_123",
        "customer": "cus_test_123",
        "status": "active",
        "cancel_at_period_end": False,
        "items": {"data": [{"id": "si_test_123", "price": {"id": "price_starter"}}]},
    }
    monkeypatch.setattr(main_module, "_configure_stripe", lambda: fake_stripe)
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_starter"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/billing/plan-change-preview",
        headers=_auth_headers(refs["user_id"]),
        json={"plan_code": "starter"},
    )

    assert response.status_code == 200
    assert response.json()["action_mode"] == "already_on_plan"
    assert response.json()["requires_confirmation"] is True


def test_webhook_subscription_updated_applies_plan_entitlements(client, monkeypatch):
    refs = _seed_billing_user(plan="free")
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    event = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_123",
                "customer": "cus_test_123",
                "status": "active",
                "current_period_start": 1770000000,
                "current_period_end": 1772592000,
                "cancel_at_period_end": False,
                "items": {"data": [{"price": {"id": "price_pro"}}]},
            }
        },
    }
    calls: dict[str, list] = {}
    monkeypatch.setattr(
        main_module,
        "_configure_stripe",
        lambda: _fake_stripe(calls, event_factory=lambda *_args: event),
    )

    response = client.post(
        "/stripe/webhook",
        data=json.dumps({"ignored": True}),
        headers={"stripe-signature": "sig_test"},
    )

    assert response.status_code == 200
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        assert subscription.plan == "pro"
        assert subscription.billing_status == "active"
        assert subscription.stripe_subscription_id == "sub_test_123"
        assert subscription.stripe_price_id == "price_pro"
        assert subscription.slides_per_report_limit == 15
        assert subscription.export_pptx is True
    finally:
        db.close()


def test_webhook_subscription_deleted_downgrades_to_free(client, monkeypatch):
    refs = _seed_billing_user(plan="pro")
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.stripe_price_id = "price_pro"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    event = {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_123",
                "customer": "cus_test_123",
            }
        },
    }
    monkeypatch.setattr(
        main_module,
        "_configure_stripe",
        lambda: _fake_stripe({}, event_factory=lambda *_args: event),
    )

    response = client.post(
        "/stripe/webhook",
        data=json.dumps({"ignored": True}),
        headers={"stripe-signature": "sig_test"},
    )

    assert response.status_code == 200
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        assert subscription.plan == "free"
        assert subscription.billing_status == "free"
        assert subscription.stripe_customer_id == "cus_test_123"
        assert subscription.stripe_subscription_id is None
        assert subscription.measurable_watermark is True
    finally:
        db.close()


def test_webhook_invoice_payment_failed_sets_past_due(client, monkeypatch):
    refs = _seed_billing_user(plan="starter")
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        subscription.stripe_customer_id = "cus_test_123"
        subscription.stripe_subscription_id = "sub_test_123"
        subscription.billing_status = "active"
        db.add(subscription)
        db.commit()
    finally:
        db.close()

    event = {
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "subscription": "sub_test_123",
                "customer": "cus_test_123",
            }
        },
    }
    monkeypatch.setattr(
        main_module,
        "_configure_stripe",
        lambda: _fake_stripe({}, event_factory=lambda *_args: event),
    )

    response = client.post(
        "/stripe/webhook",
        data=json.dumps({"ignored": True}),
        headers={"stripe-signature": "sig_test"},
    )

    assert response.status_code == 200
    db = SessionLocal()
    try:
        subscription = db.get(Subscription, refs["subscription_id"])
        assert subscription.billing_status == "past_due"
        assert subscription.plan == "starter"
    finally:
        db.close()
