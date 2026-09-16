from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_report_generation import factory, postgres_factory, seed
from app.deps import get_db
import app.main as main
from app.models import Dataset, Integration, IntegrationAccount, IntegrationToken, Job, Report, ReportGeneration, Schedule, ShopifyConnection, Subscription, Workspace
from app.report_spec import FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC
from app.scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun, ScheduledReportSource
from app.scheduled_report_recurrence import occurrence_identity, reporting_period
from app.security import create_access_token
import app.scheduled_reports as schedules


NOW = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)


@pytest.fixture
def client(factory, monkeypatch):
    def override_db():
        with factory() as db:
            yield db

    monkeypatch.setattr(schedules, "_now", lambda: NOW)
    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()


def headers(command):
    return {"Authorization": "Bearer " + create_access_token(str(command.actor_user_id))}


def body(command, *, monthly=False):
    source = asdict(command.sources[0])
    source.pop("config_json")
    return {
        "workspace_id": command.workspace_id, "name": "Monthly client performance" if monthly else "Weekly client performance",
        "frequency": "MONTHLY" if monthly else "WEEKLY", "day_of_month": 31 if monthly else None,
        "day_of_week": None if monthly else 0, "local_time": "08:00", "timezone": "America/Mexico_City",
        "period_policy": "previous_month" if monthly else "previous_week",
        "configuration": {"builder": command.configuration.builder, "requested_slides": 5},
        "generation_options": {"title": "Client performance", "locale": "en", "ai_mode": "standard"},
        "sources": [source],
    }


def create(client, command, payload=None):
    response = client.post("/scheduled-reports", json=payload or body(command), headers=headers(command))
    assert response.status_code == 201, response.text
    return response.json()


def url(command, schedule, suffix=""):
    return f"/scheduled-reports/{schedule['id']}{suffix}?workspace_id={command.workspace_id}"


def add_history(db, schedule, *, scheduled_for=NOW, status="SUCCEEDED"):
    recurrence = schedules._recurrence(schedule["configuration_snapshot"])
    period = reporting_period(recurrence, scheduled_for=scheduled_for)
    run = ScheduledReportRun(
        schedule_id=schedule["id"], workspace_id=schedule["workspace_id"],
        configuration_revision=schedule["configuration_revision"], configuration_hash=schedule["configuration_hash"],
        trigger_type="SCHEDULED", scheduled_for=scheduled_for,
        reporting_period_start=period.start, reporting_period_end=period.end,
        reporting_start_date=period.start_date, reporting_end_date=period.end_date, timezone=recurrence.timezone,
        idempotency_key=occurrence_identity(schedule["id"], scheduled_for), status=status, stage="COMPLETE", attempt_count=1,
    )
    db.add(run)
    db.flush()
    return run


@pytest.mark.parametrize("plan", ["starter", "pro", "advanced", "core"])
@pytest.mark.parametrize("monthly", [False, True])
def test_paid_workspace_creates_weekly_and_monthly_without_execution(factory, client, plan, monthly):
    command = seed(factory, used=10, provider="facebook_pages")
    with factory() as db:
        db.query(Subscription).update({"plan": plan})
        db.commit()
    schedule = create(client, command, body(command, monthly=monthly))
    assert schedule["status"] == "ACTIVE"
    assert schedule["configuration_revision"] == 1
    assert schedule["availability"]["scheduling_enabled"] is True
    assert datetime.fromisoformat(schedule["next_run_at"]) > NOW
    with factory() as db:
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).count() == db.query(ScheduledReportRun).count() == db.query(Job).count() == 0
        assert db.query(Schedule).count() == 0
        assert db.query(ScheduledReportRevision).count() == db.query(ScheduledReportSource).count() == 1


def test_no_artificial_schedule_count_limit(factory, client):
    command = seed(factory, provider="facebook_pages")
    for index in range(55):
        create(client, command, {**body(command), "name": f"Client {index}"})
    response = client.get(f"/scheduled-reports?workspace_id={command.workspace_id}", headers=headers(command))
    assert response.status_code == 200
    assert response.json()["total"] == 55
    assert "limit" not in response.json()["availability"]


@pytest.mark.parametrize("plan,status,billing_status,expired", [
    ("free", "active", "active", False), ("starter", "canceled", "canceled", False),
    ("pro", "active", "past_due", False), ("advanced", "active", "active", True),
])
def test_free_and_ineligible_subscriptions_cannot_create(factory, client, plan, status, billing_status, expired):
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        db.query(Subscription).update({"plan": plan, "status": status, "billing_status": billing_status,
                                      "current_period_end": NOW - timedelta(days=1) if expired else None})
        db.commit()
    response = client.post("/scheduled-reports", json=body(command), headers=headers(command))
    assert response.status_code == 403
    with factory() as db:
        assert db.query(ScheduledReport).count() == 0


def test_pause_resume_future_only_and_archive_are_nondestructive(factory, client, monkeypatch):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        add_history(db, schedule)
        db.commit()
    paused = client.post(url(command, schedule, "/pause"), headers=headers(command))
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED" and paused.json()["next_run_at"] is None
    future = NOW + timedelta(days=80)
    monkeypatch.setattr(schedules, "_now", lambda: future)
    resumed = client.post(url(command, schedule, "/resume"), headers=headers(command))
    assert resumed.status_code == 200
    assert datetime.fromisoformat(resumed.json()["next_run_at"]) > future
    assert resumed.json()["configuration_revision"] == 1
    archived = client.post(url(command, schedule, "/archive"), headers=headers(command))
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED" and archived.json()["next_run_at"] is None
    assert archived.json()["archived_at"]
    assert client.post(url(command, schedule, "/archive"), headers=headers(command)).status_code == 200
    assert client.post(url(command, schedule, "/resume"), headers=headers(command)).status_code == 409
    assert client.patch(url(command, schedule), json={"name": "No"}, headers=headers(command)).status_code == 409
    with factory() as db:
        assert db.query(ScheduledReport).count() == db.query(ScheduledReportRun).count() == db.query(ScheduledReportRevision).count() == db.query(ScheduledReportSource).count() == 1
        assert db.query(ScheduledReport).filter(ScheduledReport.status == "ACTIVE", ScheduledReport.next_run_at <= future).count() == 0
    assert client.get(f"/scheduled-reports?workspace_id={command.workspace_id}", headers=headers(command)).json()["total"] == 0
    assert client.get(f"/scheduled-reports?workspace_id={command.workspace_id}&include_archived=true", headers=headers(command)).json()["total"] == 1
    assert client.get(url(command, schedule), headers=headers(command)).status_code == 200


def test_downgrade_preserves_active_configuration_and_history_but_cannot_resume(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        add_history(db, schedule, status="QUOTA_BLOCKED")
        db.query(Subscription).update({"plan": "free"})
        db.commit()
    response = client.get(url(command, schedule), headers=headers(command))
    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["configuration_snapshot"] == schedule["configuration_snapshot"]
    assert response.json()["availability"]["execution_available"] is False
    assert response.json()["availability"]["upgrade_required"] is True
    assert client.post(url(command, schedule, "/resume"), headers=headers(command)).status_code == 403
    assert client.patch(url(command, schedule), json={"name": "New"}, headers=headers(command)).status_code == 403
    assert client.post(url(command, schedule, "/pause"), headers=headers(command)).status_code == 200
    assert client.post(url(command, schedule, "/resume"), headers=headers(command)).status_code == 403
    history = client.get(url(command, schedule, "/runs"), headers=headers(command)).json()
    assert history["total"] == 1
    assert history["items"][0]["status"] == "QUOTA_BLOCKED"
    assert history["items"][0]["upgrade_required"] is True
    assert client.post(url(command, schedule, "/archive"), headers=headers(command)).status_code == 200


@pytest.mark.parametrize("method,suffix", [("get", ""), ("get", "/runs"), ("post", "/pause"), ("post", "/resume"), ("post", "/archive"), ("patch", "")])
def test_workspace_isolation_for_every_item_endpoint(factory, client, method, suffix):
    first = seed(factory, provider="facebook_pages")
    second = seed(factory, provider="facebook_pages")
    schedule = create(client, first)
    kwargs = {"json": {"name": "Intrusion"}} if method == "patch" else {}
    assert getattr(client, method)(url(second, schedule, suffix), headers=headers(second), **kwargs).status_code == 404
    assert getattr(client, method)(url(first, schedule, suffix), headers=headers(second), **kwargs).status_code == 403
    assert client.get(f"/scheduled-reports?workspace_id={second.workspace_id}", headers=headers(second)).json()["total"] == 0
    assert client.get(f"/scheduled-reports?workspace_id={first.workspace_id}", headers=headers(second)).status_code == 403


@pytest.mark.parametrize("updates", [
    {"timezone": "Invalid/Zone"}, {"timezone": "../UTC"}, {"day_of_week": None}, {"day_of_week": 7},
    {"day_of_month": 3}, {"frequency": "DAILY"}, {"local_time": "08:00:01"}, {"local_time": "08:00+01:00"},
    {"period_policy": "previous_month"}, {"day_of_week": True},
    {"frequency": "MONTHLY", "day_of_week": None, "day_of_month": 0, "period_policy": "previous_month"},
    {"frequency": "MONTHLY", "day_of_week": None, "day_of_month": 32, "period_policy": "previous_month"},
])
def test_invalid_recurrence_rejected_atomically(factory, client, updates):
    command = seed(factory, provider="facebook_pages")
    response = client.post("/scheduled-reports", json={**body(command), **updates}, headers=headers(command))
    assert response.status_code == 422, response.text
    with factory() as db:
        assert db.query(ScheduledReport).count() == 0


@pytest.mark.parametrize("field", ["integration_id", "integration_account_id", "dataset_id"])
def test_cross_workspace_source_binding_rejected(factory, client, field):
    command = seed(factory, provider="facebook_pages")
    other = seed(factory, provider="facebook_pages")
    payload = body(command)
    payload["sources"][0][field] = getattr(other.sources[0], field)
    response = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert response.status_code == 400, response.text


def test_disconnected_source_identity_survives_and_reconnect_can_resume(factory, client):
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        db.query(Integration).update({"status": "disconnected"})
        db.commit()
    schedule = create(client, command)
    assert schedule["status"] == "BLOCKED" and schedule["status_reason"] == "source_disconnected"
    assert schedule["next_run_at"] is None
    assert client.post(url(command, schedule, "/resume"), headers=headers(command)).status_code == 409
    with factory() as db:
        db.query(Integration).update({"status": "connected"})
        db.commit()
    resumed = client.post(url(command, schedule, "/resume"), headers=headers(command))
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ACTIVE"
    assert resumed.json()["sources"] == schedule["sources"]


def test_deleted_live_account_preserves_binding_and_snapshot(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        db.delete(db.get(IntegrationAccount, command.sources[0].integration_account_id))
        db.commit()
        assert db.query(ScheduledReportSource).one().integration_account_id is None
        assert db.query(ScheduledReportSource).one().external_account_id == "account_123"
        assert db.query(ScheduledReportRevision).one().snapshot_json == schedule["configuration_snapshot"]
    response = client.get(url(command, schedule), headers=headers(command))
    assert response.status_code == 200
    assert response.json()["availability"]["execution_available"] is False
    assert client.post(url(command, schedule, "/resume"), headers=headers(command)).status_code == 409


def test_snapshot_and_responses_never_include_tokens_metric_payloads_or_raw_errors(factory, client):
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        db.add(IntegrationToken(workspace_id=command.workspace_id, account_id=command.sources[0].integration_account_id,
                                token_type="page", access_token="private-access-secret", refresh_token="private-refresh-secret"))
        dataset = db.get(Dataset, command.sources[0].dataset_id)
        dataset.data = {**dataset.data, "access_token": "dataset-secret", "analysis": "private-ai-conclusion"}
        db.commit()
    schedule = create(client, command)
    with factory() as db:
        run = add_history(db, schedule, status="FAILED")
        run.error_detail = "provider url?access_token=private-access-secret"
        db.commit()
    output = json.dumps(schedule) + client.get(url(command, schedule, "/runs"), headers=headers(command)).text
    for secret in ("private-access-secret", "private-refresh-secret", "dataset-secret", "private-ai-conclusion", "reach_daily", "normalized_report_metrics"):
        assert secret not in output
    payload = body(command)
    payload["sources"][0]["access_token"] = "secret"
    rejected = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert rejected.status_code == 422
    assert '"secret"' not in rejected.text


@pytest.mark.parametrize("config,code", [
    ({"requested_slides": 10}, "configuration_not_repeatable"),
    ({"template_version_id": 123}, "template_not_executable"),
    ({"report_spec": {"invalid": True}}, "invalid_report_spec"),
    ({"report_spec": FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC.as_dict()}, "report_spec_not_executable"),
    ({"template": "unsupported"}, "template_not_executable"),
])
def test_unexecutable_contracts_rejected_without_silent_conversion(factory, client, config, code):
    command = seed(factory, provider="facebook_pages")
    payload = body(command)
    payload["configuration"].update(config)
    response = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert response.status_code == 400, response.text
    assert code in response.text


@pytest.mark.parametrize("change", ["recurrence", "configuration", "generation_options", "source"])
def test_execution_affecting_edits_append_revision_and_history_stays_pinned(factory, client, change):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        add_history(db, schedule)
        db.commit()
    updates = {
        "recurrence": {"local_time": "09:00"},
        "configuration": {"configuration": {**body(command)["configuration"], "branding": {"brand_name": "Pinned agency"}}},
        "generation_options": {"generation_options": {"title": "Updated report"}},
        "source": {"sources": [{**body(command)["sources"][0], "label": "Updated source label"}]},
    }[change]
    response = client.patch(url(command, schedule), json={**updates, "expected_revision": 1}, headers=headers(command))
    assert response.status_code == 200, response.text
    assert response.json()["configuration_revision"] == 2
    assert response.json()["configuration_hash"] != schedule["configuration_hash"]
    if change == "recurrence":
        assert response.json()["next_run_at"] != schedule["next_run_at"]
    with factory() as db:
        assert db.query(ScheduledReportRevision).count() == db.query(ScheduledReportSource).count() == 2
        assert db.get(ScheduledReportRevision, (schedule["id"], command.workspace_id, 1)).snapshot_json == schedule["configuration_snapshot"]
        assert db.query(ScheduledReportRun).one().configuration_revision == 1
        assert db.query(ScheduledReportRun).one().configuration_hash == schedule["configuration_hash"]
    assert client.patch(url(command, schedule), json={"local_time": "10:00", "expected_revision": 1}, headers=headers(command)).status_code == 409


def test_cosmetic_name_and_identical_edits_do_not_create_revision(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    for patch in ({"name": "Renamed"}, {"local_time": "08:00"}, {}):
        response = client.patch(url(command, schedule), json=patch, headers=headers(command))
        assert response.status_code == 200
        assert response.json()["configuration_revision"] == 1
        assert response.json()["configuration_hash"] == schedule["configuration_hash"]


def test_frozen_branding_survives_workspace_edits_and_unsupported_theme_rejected(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        db.query(Workspace).update({"name": "Changed workspace"})
        db.commit()
    response = client.patch(url(command, schedule), json={"local_time": "09:00"}, headers=headers(command))
    assert response.json()["configuration_snapshot"]["configuration"]["branding"] == schedule["configuration_snapshot"]["configuration"]["branding"]
    payload = body(command)
    payload["configuration"]["theme"] = {"layout": "unsupported"}
    assert client.post("/scheduled-reports", json=payload, headers=headers(command)).status_code == 422


def test_duplicate_occurrence_and_wrong_revision_hash_are_rejected(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        add_history(db, schedule)
        db.commit()
        with pytest.raises(IntegrityError), db.begin_nested():
            add_history(db, schedule)
        wrong = {**schedule, "configuration_hash": "0" * 64}
        with pytest.raises(IntegrityError), db.begin_nested():
            add_history(db, wrong, scheduled_for=NOW + timedelta(days=7))
        assert db.query(ScheduledReportRun).count() == 1


def test_atomic_revision_failure_never_leaves_partial_schedule(factory, client, monkeypatch):
    command = seed(factory, provider="facebook_pages")
    original = schedules._append_revision
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise IntegrityError("test", {}, Exception("forced failure"))
    monkeypatch.setattr(schedules, "_append_revision", fail)
    response = client.post("/scheduled-reports", json=body(command), headers=headers(command))
    assert response.status_code == 409
    with factory() as db:
        assert db.query(ScheduledReport).count() == db.query(ScheduledReportRevision).count() == db.query(ScheduledReportSource).count() == 0


@pytest.mark.parametrize("provider", ["facebook_pages", "instagram_business", "meta_ads"])
def test_supported_provider_configuration_snapshots(factory, client, provider):
    command = seed(factory, provider=provider)
    result = create(client, command)
    assert result["configuration_snapshot"]["configuration"]["builder"] == command.configuration.builder


def test_multi_source_schedule_creation_is_paused(factory, client):
    command = seed(factory, provider="facebook_pages")
    payload = body(command)
    with factory() as db:
        integration = Integration(workspace_id=command.workspace_id, provider="instagram_business_login", status="connected")
        db.add(integration)
        db.flush()
        account = IntegrationAccount(workspace_id=command.workspace_id, integration_id=integration.id, external_account_id="ig-account")
        db.add(account)
        db.flush()
        payload["sources"].append({"integration_id": integration.id, "integration_account_id": account.id,
                                   "provider": "instagram_business_login", "source_type": "instagram_business", "external_account_id": "ig-account", "position": 1})
        db.commit()
    payload["configuration"] = {"builder": "multi_source", "requested_slides": 10}
    response = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert response.status_code == 422
    assert "schedule_builder_not_supported" in response.text


def test_pinned_builder_contract_change_prevents_resume(factory, client, monkeypatch):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    assert client.post(url(command, schedule, "/pause"), headers=headers(command)).status_code == 200
    monkeypatch.setenv("FACEBOOK_PAGES_5_RECIPE_BUILDER", "legacy")
    response = client.post(url(command, schedule, "/resume"), headers=headers(command))
    assert response.status_code == 409 and "builder_contract_changed" in response.text


def test_signed_or_credential_urls_not_stored(factory, client):
    command = seed(factory, provider="facebook_pages")
    payload = body(command)
    payload["configuration"]["branding"] = {"brand_name": "Agency", "logo_url": "https://example.test/logo.png?access_token=secret"}
    response = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert response.status_code == 400, response.text


def test_malformed_branding_url_is_rejected_without_server_error(factory, client):
    command = seed(factory, provider="facebook_pages")
    payload = body(command)
    payload["configuration"]["branding"] = {"brand_name": "Agency", "logo_url": "https://[malformed"}
    response = client.post("/scheduled-reports", json=payload, headers=headers(command))
    assert response.status_code == 422


def test_source_replacement_preserves_original_binding_and_history(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        add_history(db, schedule)
        account = IntegrationAccount(workspace_id=command.workspace_id, integration_id=command.sources[0].integration_id,
                                     external_account_id="replacement-account")
        db.add(account)
        db.flush()
        replacement_id = account.id
        db.commit()
    replacement = {**body(command)["sources"][0], "integration_account_id": replacement_id,
                   "external_account_id": "replacement-account", "dataset_id": None}
    response = client.patch(url(command, schedule), json={"sources": [replacement]}, headers=headers(command))
    assert response.status_code == 200, response.text
    assert response.json()["configuration_revision"] == 2
    with factory() as db:
        bindings = db.query(ScheduledReportSource).order_by(ScheduledReportSource.configuration_revision).all()
        assert [binding.integration_account_id for binding in bindings] == [command.sources[0].integration_account_id, replacement_id]
        assert db.query(ScheduledReportRun).one().configuration_revision == 1


def test_changed_provider_identity_is_not_silently_reused(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    with factory() as db:
        db.query(Integration).update({"provider": "instagram_business"})
        db.commit()
    response = client.get(url(command, schedule), headers=headers(command))
    assert response.json()["availability"]["execution_unavailable_reason"] == "source_identity_changed"


def test_dispatch_and_execution_are_not_public_routes():
    assert not any("scheduled-reports" in route.path and any(word in route.path for word in ("dispatch", "execute")) for route in main.app.routes)


@pytest.mark.parametrize("operation", ["resume", "edit"])
def test_stale_active_schedule_gets_future_time_without_backfill(factory, client, monkeypatch, operation):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    future = NOW + timedelta(days=100)
    monkeypatch.setattr(schedules, "_now", lambda: future)
    if operation == "resume":
        response = client.post(url(command, schedule, "/resume"), headers=headers(command))
    else:
        response = client.patch(url(command, schedule), json={"name": "Renamed"}, headers=headers(command))
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["next_run_at"]) > future
    with factory() as db:
        assert db.query(ScheduledReportRun).count() == 0
        assert db.query(ScheduledReportRevision).count() == 1


def test_weekly_to_monthly_edit_is_validated_as_one_configuration(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    response = client.patch(url(command, schedule), json={"frequency": "MONTHLY", "day_of_week": None,
                            "day_of_month": 31, "period_policy": "previous_month"}, headers=headers(command))
    assert response.status_code == 200, response.text
    assert response.json()["configuration_revision"] == 2
    assert response.json()["next_run_at"].startswith("2026-09-30T14:00")


def test_paused_edit_stays_not_due(factory, client):
    command = seed(factory, provider="facebook_pages")
    schedule = create(client, command)
    client.post(url(command, schedule, "/pause"), headers=headers(command))
    response = client.patch(url(command, schedule), json={"local_time": "09:00"}, headers=headers(command))
    assert response.status_code == 200
    assert response.json()["status"] == "PAUSED" and response.json()["next_run_at"] is None
    assert response.json()["configuration_revision"] == 2
