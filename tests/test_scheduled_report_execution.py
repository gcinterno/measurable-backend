from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from test_report_generation import factory, postgres_factory, seed, persist_stub
from test_scheduled_reports import body, client, create, headers, url
from app.models import Dataset, DatasetFile, Report, ReportGeneration, ReportSource, Subscription, User
from app.report_generation import GenerationError, generate_report
import app.report_generation as canonical
import app.report_generation_builders as builders
from app.scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun
import app.scheduled_reports as schedules
import app.scheduled_report_execution as execution


def make_schedule(factory, *, used=0, monthly=False, provider="facebook_pages"):
    command = seed(factory, used=used, provider=provider)
    with factory() as db:
        output = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command, monthly=monthly)), db.get(User, command.actor_user_id), db)
        schedule = db.get(ScheduledReport, output["id"])
        schedule.next_run_at = datetime.now(timezone.utc) - timedelta(days=29)
        db.commit()
    return command, output


def refresh(factory, *, workspace_id, actor_user_id, source, start_date, end_date):
    with factory() as db:
        data = deepcopy(db.get(Dataset, source.dataset_id).data)
        data["timeframe"] = {"key": "custom", "since": start_date, "until": end_date}
        dataset = Dataset(workspace_id=workspace_id, name="Exact refreshed dataset", data=data)
        db.add(dataset)
        db.flush()
        db.add(DatasetFile(workspace_id=workspace_id, dataset_id=dataset.id, s3_key="safe-fixture.csv", content_type="text/csv", size_bytes=1))
        db.commit()
        return replace(source, dataset_id=dataset.id)


@pytest.fixture(autouse=True)
def stub_generation(monkeypatch):
    monkeypatch.setattr(builders, "build_report", persist_stub)


def dispatch_claim(factory, output):
    ids = execution.dispatch_due(factory)
    assert len(ids) == 1
    return execution.claim_run(factory, worker_id="test", run_id=ids[0])


@pytest.mark.parametrize("monthly", [False, True])
def test_dispatch_exact_occurrence_revision_and_no_backlog(factory, monthly):
    command, output = make_schedule(factory, monthly=monthly)
    with factory() as db:
        instant = db.get(ScheduledReport, output["id"]).next_run_at
    ids = execution.dispatch_due(factory)
    assert execution.dispatch_due(factory) == []
    with factory() as db:
        run = db.get(ScheduledReportRun, ids[0])
        assert canonical._utc(run.scheduled_for) == canonical._utc(instant)
        assert run.configuration_hash == output["configuration_hash"]
        assert run.configuration_revision == 1
        period = schedules._recurrence(output["configuration_snapshot"])
        expected = execution.reporting_period(period, scheduled_for=canonical._utc(instant))
        assert canonical._utc(run.reporting_period_start) == expected.start
        assert canonical._utc(run.reporting_period_end) == expected.end
        assert canonical._utc(db.get(ScheduledReport, output["id"]).next_run_at) > datetime.now(timezone.utc)
        assert db.query(ScheduledReportRun).count() == 1


@pytest.mark.parametrize("status", ["PAUSED", "ARCHIVED", "BLOCKED"])
def test_nonactive_schedule_not_dispatched(factory, status):
    _, output = make_schedule(factory)
    with factory() as db:
        row = db.get(ScheduledReport, output["id"])
        row.status, row.next_run_at = status, None
        row.archived_at = datetime.now(timezone.utc) if status == "ARCHIVED" else None
        db.commit()
    assert execution.dispatch_due(factory) == []


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_concurrent_dispatch_and_claim_are_unique(request, fixture):
    factory = request.getfixturevalue(fixture)
    make_schedule(factory)
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: execution.dispatch_due(factory), range(2)))
        claims = list(pool.map(lambda i: execution.claim_run(factory, worker_id=str(i)), range(2)))
    assert sum(map(len, ids)) == 1
    assert sum(c is not None for c in claims) == 1


def test_final_capacity_success_then_quota_block_no_replay(factory):
    command, output = make_schedule(factory, used=9)
    claim = dispatch_claim(factory, output)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "SUCCEEDED"
        assert run.quota_json["reports_used"] == run.quota_json["reports_limit"] == 10
        assert run.quota_json["limit_reached"] is True
        source = db.query(ReportSource).filter_by(report_id=run.report_id).one()
        assert source.dataset_id == run.execution_sources_json[0]["dataset_id"] != command.sources[0].dataset_id
        assert source.integration_account_id == command.sources[0].integration_account_id
        schedule = db.get(ScheduledReport, output["id"])
        schedule.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    second = dispatch_claim(factory, output)
    execution.execute_run(factory, second, refresher=lambda *a, **kw: pytest.fail("quota preflight must skip refresh"))
    with factory() as db:
        run = db.get(ScheduledReportRun, second.run_id)
        assert run.status == "QUOTA_BLOCKED" and run.report_id is None
        assert db.query(Report).count() == 10 and db.query(ReportGeneration).count() == 1
        assert db.get(ScheduledReport, output["id"]).status == "ACTIVE"
        assert run.configuration_revision == 1 and run.reporting_period_start
    assert execution.claim_run(factory, worker_id="no-backfill") is None
    assert execution.dispatch_due(factory) == []
    # Simulate allowance reset: only a new occurrence is eligible.
    with factory() as db:
        old = datetime.now(timezone.utc) - timedelta(days=65)
        db.query(Report).update({"created_at": old})
        db.query(ReportGeneration).update({"charged_at": old})
        db.get(ScheduledReport, output["id"]).next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    third = dispatch_claim(factory, output)
    execution.execute_run(factory, third, refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, third.run_id).status == "SUCCEEDED"
        assert db.get(ScheduledReportRun, second.run_id).status == "QUOTA_BLOCKED"


@pytest.mark.parametrize("plan,status", [("free", "active"), ("starter", "canceled")])
def test_downgrade_occurrence_preserved_without_quota(factory, plan, status):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        db.query(Subscription).update({"plan": plan, "status": status})
        db.commit()
    execution.execute_run(factory, claim, refresher=lambda *a, **kw: pytest.fail("no refresh for ineligible workspace"))
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "SKIPPED" and run.error_code == "SCHEDULE_ENTITLEMENT_REQUIRED"
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 0
        assert db.get(ScheduledReport, output["id"]).status == "ACTIVE"


@pytest.mark.parametrize("kind", ["network", "rate_limit", "generation"])
def test_retry_same_run_same_datasets_and_capacity_released(factory, monkeypatch, kind):
    _, output = make_schedule(factory, used=9)
    claim = dispatch_claim(factory, output)
    def fail(*a, **kw):
        raise HTTPException(429, detail="access_token=DO_NOT_PERSIST") if kind == "rate_limit" else RuntimeError("secret")
    if kind == "generation":
        monkeypatch.setattr(builders, "build_report", fail)
    execution.execute_run(factory, claim, refresher=refresh if kind == "generation" else fail)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "QUEUED" and run.attempt_count == 1 and run.retry_after
        assert "secret" not in (run.error_detail or "")
        assert execution.quota_state(db, run.workspace_id)["capacity_remaining"] == 1
        saved = run.execution_sources_json
        run.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(builders, "build_report", persist_stub)
    retry = execution.claim_run(factory, worker_id="retry")
    assert retry.run_id == claim.run_id
    execution.execute_run(factory, retry, refresher=(lambda *a, **kw: pytest.fail("refresh must not repeat")) if saved else refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, retry.run_id)
        assert run.status == "SUCCEEDED" and run.attempt_count == 2
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).count() == 1
        assert run.quota_json["reports_used"] == 10


def test_bounded_retries(factory):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    def fail(*a, **kw):
        raise HTTPException(503, "secret")
    for attempt in range(3):
        execution.execute_run(factory, claim, refresher=fail)
        with factory() as db:
            run = db.get(ScheduledReportRun, claim.run_id)
            assert run.attempt_count == attempt + 1
            assert run.status == ("FAILED" if attempt == 2 else "QUEUED")
            if attempt < 2:
                run.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        claim = execution.claim_run(factory, worker_id="retry")
    assert claim is None


@pytest.mark.parametrize("failure", ["disconnect", "cross_workspace", "configuration"])
def test_persistent_error_blocks_without_destroying_revision(factory, failure):
    from app.models import Integration
    command, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        if failure == "configuration":
            revision = db.query(ScheduledReportRevision).one()
            bad = deepcopy(revision.snapshot_json)
            bad["configuration"]["requested_slides"] = 13
            revision.snapshot_json = bad
        else:
            integration = db.get(Integration, command.sources[0].integration_id)
            if failure == "disconnect":
                integration.status = "disconnected"
            else:
                other = seed(factory, provider="facebook_pages")
                integration.workspace_id = other.workspace_id
        db.commit()
    execution.execute_run(factory, claim, refresher=lambda *a, **kw: pytest.fail("invalid source cannot refresh"))
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "FAILED"
        assert db.get(ScheduledReport, output["id"]).status == "BLOCKED"
        assert db.query(ScheduledReportRevision).count() == 1
        assert db.query(ReportGeneration).count() == 0


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_expired_lease_reclaimed_and_stale_finalization_fenced(request, fixture):
    factory = request.getfixturevalue(fixture)
    _, output = make_schedule(factory)
    old = dispatch_claim(factory, output)
    with factory() as db:
        db.get(ScheduledReportRun, old.run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    new = execution.claim_run(factory, worker_id="new")
    assert old.token != new.token and old.run_id == new.run_id
    with pytest.raises(execution.LeaseLost):
        execution.finish_run(factory, old, status="SUCCEEDED")
    with pytest.raises(execution.LeaseLost):
        execution.RunHeartbeat(factory, old).pulse()
    execution.execute_run(factory, new, refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, new.run_id).status == "SUCCEEDED"


@pytest.mark.parametrize("boundary", ["REFRESH", "GENERATE", "FINALIZE"])
def test_crash_recovery_at_persisted_boundaries(factory, monkeypatch, boundary):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    original = execution._save_stage
    class Crash(BaseException):
        pass
    def crash(factory, claim, stage, sources=None):
        original(factory, claim, stage, sources)
        if stage == boundary and (stage != "REFRESH" or sources):
            raise Crash()
    monkeypatch.setattr(execution, "_save_stage", crash)
    with pytest.raises(Crash):
        execution.execute_run(factory, claim, refresher=refresh)
    monkeypatch.setattr(execution, "_save_stage", original)
    with factory() as db:
        db.get(ScheduledReportRun, claim.run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    retry = execution.claim_run(factory, worker_id="recover")
    execution.execute_run(factory, retry, refresher=lambda *a, **kw: pytest.fail("persisted refresh must be reused"))
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "SUCCEEDED"
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 1


@pytest.mark.parametrize("paused", [False, True])
def test_run_now_idempotency_intentional_regeneration_and_history(factory, client, paused):
    command = seed(factory, provider="facebook_pages")
    output = create(client, command)
    if paused:
        output = client.post(url(command, output, "/pause"), headers=headers(command)).json()
    cursor = output["next_run_at"]
    def run_now(key):
        return client.post(url(command, output, "/run-now"), headers={**headers(command), "Idempotency-Key": key})
    first = run_now("one")
    assert first.status_code == 202, first.text
    assert run_now("one").json()["id"] == first.json()["id"]
    claim = execution.claim_run(factory, worker_id="run-now")
    execution.execute_run(factory, claim, refresher=refresh)
    retry = run_now("one")
    assert retry.status_code == 200 and retry.json()["status"] == "SUCCEEDED"
    assert run_now("two").json()["id"] != first.json()["id"]
    execution.execute_run(factory, execution.claim_run(factory, worker_id="run-now"), refresher=refresh)
    history = client.get(url(command, output, "/runs"), headers=headers(command)).json()
    assert all(run["report_id"] for run in history["items"])
    assert "lease_token" not in json.dumps(history) and "worker_id" not in json.dumps(history)
    assert client.get(url(command, output), headers=headers(command)).json()["next_run_at"] == cursor
    with factory() as db:
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 2


@pytest.mark.parametrize("blocked", ["ARCHIVED", "BLOCKED", "free", "quota", "missing_key"])
def test_run_now_rejections(factory, client, blocked):
    from app.models import Integration
    command = seed(factory, provider="facebook_pages", used=10 if blocked == "quota" else 0)
    output = create(client, command)
    with factory() as db:
        schedule = db.get(ScheduledReport, output["id"])
        if blocked in {"ARCHIVED", "BLOCKED"}:
            schedule.status, schedule.next_run_at = blocked, None
            schedule.archived_at = datetime.now(timezone.utc) if blocked == "ARCHIVED" else None
            if blocked == "BLOCKED":
                db.get(Integration, command.sources[0].integration_id).status = "disconnected"
        elif blocked == "free":
            db.query(Subscription).update({"plan": "free"})
        db.commit()
    response = client.post(url(command, output, "/run-now"), headers={**headers(command), **({} if blocked == "missing_key" else {"Idempotency-Key": "one"})})
    assert response.status_code == (422 if blocked == "missing_key" else 403 if blocked in {"free", "quota"} else 409)
    if blocked == "quota":
        assert response.json()["status"] == "QUOTA_BLOCKED"
        assert response.json()["quota"]["reports_used"] == 10


def test_manual_wins_quota_race_while_scheduled_refreshing(postgres_factory):
    factory = postgres_factory
    command, output = make_schedule(factory, used=9)
    claim = dispatch_claim(factory, output)
    def raced_refresh(*a, **kw):
        with factory() as db:
            generate_report(db, command)
        return refresh(*a, **kw)
    execution.execute_run(factory, claim, refresher=raced_refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "QUOTA_BLOCKED"
        assert db.query(Report).count() == 10 and db.query(ReportGeneration).count() == 1
        assert execution.quota_state(db, command.workspace_id)["reports_used"] == 10


def test_reservation_renewal_beyond_original_expiry_and_stale_worker_fence(postgres_factory, monkeypatch):
    factory = postgres_factory
    command, output = make_schedule(factory, used=9)
    claim = dispatch_claim(factory, output)
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(canonical, "_database_now", lambda db: clock)
    monkeypatch.setattr(execution, "_database_now", lambda db: clock)
    def long_build(command, prepared):
        nonlocal clock
        # Advance twenty minutes in one-minute steps, explicitly renew as a heartbeat.
        with factory() as db:
            generation = db.query(ReportGeneration).one()
            reservation = canonical.GenerationReservation(generation.id, command.workspace_id, generation.attempt_token)
        heart = execution.RunHeartbeat(factory, claim)
        heart.reservation = reservation
        for _ in range(20):
            clock += timedelta(minutes=1)
            heart.pulse()
        return persist_stub(command, prepared)
    monkeypatch.setattr(builders, "build_report", long_build)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "SUCCEEDED"
        assert db.query(Report).count() == 10


def test_stale_worker_cannot_commit_canonical_report(postgres_factory, monkeypatch):
    factory = postgres_factory
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    def lose_ownership(command, prepared):
        with factory() as db:
            db.get(ScheduledReportRun, claim.run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        assert execution.claim_run(factory, worker_id="replacement")
        return persist_stub(command, prepared)
    monkeypatch.setattr(builders, "build_report", lose_ownership)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        assert db.query(Report).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"


def test_configuration_edit_does_not_change_materialized_run(factory):
    command, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        schedules.update_scheduled_report(output["id"], schedules.ScheduleUpdateInput(local_time="09:00"), command.workspace_id,
                                          db.get(User, command.actor_user_id), db)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "SUCCEEDED" and run.configuration_revision == 1
        assert db.get(ScheduledReport, output["id"]).configuration_revision == 2


@pytest.mark.parametrize("boundary", ["during_refresh", "reserved", "building"])
def test_crash_before_generation_commit_releases_or_recovers_capacity(factory, monkeypatch, boundary):
    _, output = make_schedule(factory, used=9)
    claim = dispatch_claim(factory, output)
    class Crash(BaseException):
        pass
    def crash(*a, **kw):
        raise Crash()
    reserved = execution.RunHeartbeat.reserved
    if boundary == "reserved":
        def reserved_crash(self, reservation):
            reserved(self, reservation)
            raise Crash()
        monkeypatch.setattr(execution.RunHeartbeat, "reserved", reserved_crash)
    elif boundary == "building":
        monkeypatch.setattr(builders, "build_report", crash)
    with pytest.raises(Crash):
        execution.execute_run(factory, claim, refresher=crash if boundary == "during_refresh" else refresh)
    with factory() as db:
        assert db.query(Report).count() == 9
        db.get(ScheduledReportRun, claim.run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(execution.RunHeartbeat, "reserved", reserved)
    monkeypatch.setattr(builders, "build_report", persist_stub)
    execution.execute_run(factory, execution.claim_run(factory, worker_id="recovery"), refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "SUCCEEDED"
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).count() == 1


def test_two_unique_scheduled_generations_compete_for_final_capacity(postgres_factory):
    factory = postgres_factory
    command, first = make_schedule(factory, used=9)
    with factory() as db:
        second = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command)), db.get(User, command.actor_user_id), db)
        db.get(ScheduledReport, second["id"]).next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert len(execution.dispatch_due(factory)) == 2
    claims = [execution.claim_run(factory, worker_id=str(i)) for i in range(2)]
    barrier = Barrier(2)
    def refreshed(*a, **kw):
        result = refresh(*a, **kw)
        barrier.wait(timeout=10)
        return result
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda claim: execution.execute_run(factory, claim, refresher=refreshed), claims))
    with factory() as db:
        assert sorted(r.status for r in db.query(ScheduledReportRun)) == ["QUOTA_BLOCKED", "SUCCEEDED"]
        assert db.query(Report).count() == 10 and db.query(ReportGeneration).count() == 1
        assert execution.quota_state(db, command.workspace_id)["reports_used"] == 10


def test_post_commit_finalization_failure_on_last_attempt_recovers(factory, monkeypatch):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        db.get(ScheduledReportRun, claim.run_id).attempt_count = 3
        db.commit()
    original, calls = execution.finish_run, []
    def fail_once(*a, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("temporary finalization failure")
        return original(*a, **kw)
    monkeypatch.setattr(execution, "finish_run", fail_once)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "SUCCEEDED"
        assert db.query(Report).count() == 1


def test_run_identity_cannot_reconcile_different_manual_inputs(factory):
    command, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        key = db.get(ScheduledReportRun, claim.run_id).idempotency_key
        generate_report(db, replace(command, idempotency_key=key))
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.report_id is None and run.error_code == "idempotency_conflict"
        assert db.query(Report).count() == 1


def test_background_heartbeat_continues_while_builder_is_running(factory, monkeypatch):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    def build(command, prepared):
        with factory() as db:
            before = db.get(ScheduledReportRun, claim.run_id).heartbeat_at
        Event().wait(0.15)
        with factory() as db:
            assert db.get(ScheduledReportRun, claim.run_id).heartbeat_at > before
        return persist_stub(command, prepared)
    monkeypatch.setattr(builders, "build_report", build)
    execution.execute_run(factory, claim, refresher=refresh, heartbeat_interval=0.02)
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "SUCCEEDED"


def test_dispatch_rollback_preserves_cursor_and_creates_no_run(factory, monkeypatch):
    _, output = make_schedule(factory)
    with factory() as db:
        before = db.get(ScheduledReport, output["id"]).next_run_at
    monkeypatch.setattr(execution, "next_occurrence", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash before cursor")))
    with pytest.raises(RuntimeError):
        execution.dispatch_due(factory)
    with factory() as db:
        assert db.query(ScheduledReportRun).count() == 0
        assert db.get(ScheduledReport, output["id"]).next_run_at == before


@pytest.mark.parametrize("detail,retryable", [
    ({"upstream_status_code": 429}, True), ({"upstream_status_code": 503}, True),
    ({"meta_error": {"code": 190}}, False), ({"meta_error": {"code": 4}}, True),
    ({"meta_error": {"is_transient": True}}, True), ({"code": "instagram_business_login_insights_failed"}, True),
])
def test_wrapped_provider_failure_classification(detail, retryable):
    code, retry, block = execution.classify_failure(HTTPException(400, {**detail, "message": "access_token=secret"}))
    assert retry is retryable and block is not retryable and "secret" not in code


@pytest.mark.parametrize("status", ["PAUSED", "ARCHIVED", "BLOCKED"])
def test_queued_automatic_run_is_cancelled_when_schedule_disabled_before_execution(factory, status):
    _, output = make_schedule(factory)
    claim = dispatch_claim(factory, output)
    with factory() as db:
        schedule = db.get(ScheduledReport, output["id"])
        schedule.status, schedule.next_run_at = status, None
        if status == "ARCHIVED":
            schedule.archived_at = datetime.now(timezone.utc)
        db.commit()
    execution.execute_run(factory, claim, refresher=lambda *a, **kw: pytest.fail("disabled recurrence cannot refresh"))
    with factory() as db:
        assert db.get(ScheduledReportRun, claim.run_id).status == "CANCELLED"
        assert db.query(ReportGeneration).count() == 0


def test_run_now_retry_does_not_mutate_existing_queued_run(factory, client):
    command = seed(factory, provider="facebook_pages", used=9)
    output = create(client, command)
    request_headers = {**headers(command), "Idempotency-Key": "same"}
    first = client.post(url(command, output, "/run-now"), headers=request_headers).json()
    with factory() as db:
        generate_report(db, command)
    response = client.post(url(command, output, "/run-now"), headers=request_headers)
    assert response.status_code == 202
    assert response.json()["id"] == first["id"] and response.json()["status"] == "QUEUED"


def test_run_now_concurrent_same_key_and_distinct_keys_at_same_instant(postgres_factory, monkeypatch):
    factory = postgres_factory
    command, output = make_schedule(factory)
    instant = datetime.now(timezone.utc)
    monkeypatch.setattr(execution, "_database_now", lambda db: instant)
    def submit(key):
        with factory() as db:
            schedule = schedules._schedule(db, output["id"], command.workspace_id, lock=True)
            run, created = execution.request_run_now(db, schedule, key)
            result = run.id
            db.commit()
            return result, created
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, ["same", "same"]))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    assert submit("intentional-new-request")[0] != results[0][0]
    with factory() as db:
        assert db.query(ScheduledReportRun).count() == 2
        assert len({row.scheduled_for for row in db.query(ScheduledReportRun)}) == 1


@pytest.mark.parametrize("reason,expected", [("source_authorization_required", 409), ("source_disconnected", 202)])
def test_blocked_run_now_requires_provable_repair_or_explicit_resume(factory, client, reason, expected):
    command = seed(factory, provider="facebook_pages")
    output = create(client, command)
    with factory() as db:
        schedule = db.get(ScheduledReport, output["id"])
        schedule.status, schedule.status_reason, schedule.next_run_at = "BLOCKED", reason, None
        db.commit()
    response = client.post(url(command, output, "/run-now"), headers={**headers(command), "Idempotency-Key": "repaired"})
    assert response.status_code == expected
