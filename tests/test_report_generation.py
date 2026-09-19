from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import runpy
from threading import Barrier, Event
from time import perf_counter
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/measurable_report_generation_test.db")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")

from app.db import Base
from app.deps import get_db
import app.main as main
from app.models import (
    Dataset, DatasetFile, Integration, IntegrationAccount, Job, Report, ReportBlock,
    ReportGeneration, ReportSource, ReportVersion, Subscription, User, Workspace, WorkspaceMember,
)
from app.report_generation import (
    ReportDraft, ExecutableReportConfiguration, GenerateReportCommand, GenerationError,
    GenerationOptions, ReportingPeriod, SourceIdentity, generate_report,
)
import app.report_generation_builders as builders
import app.report_generation as generation_module
from app.report_spec import FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC
from app.security import create_access_token
from app.services import get_workspace_report_quota_status


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation.db'}", connect_args={"timeout": 20, "check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


@pytest.fixture
def postgres_factory():
    url = os.environ.get("GENERATION_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("GENERATION_TEST_POSTGRES_URL is required for real PostgreSQL locking tests")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or parsed.database != "measurable_phase1_test":
        pytest.fail("PostgreSQL tests require the dedicated local measurable_phase1_test database")
    schema = "generation_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        # PostgreSQL's cyclic-FK DDL annotates constraint objects. Keep that state out
        # of the shared model metadata subsequently used by SQLite tests.
        metadata = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(metadata)
        metadata.create_all(engine)
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def seed(factory, *, used=0, provider="dataset") -> GenerateReportCommand:
    with factory() as db:
        user = User(email=f"{uuid4().hex}@example.test", password_hash="unused-test-hash", full_name="Owner", email_verified=True, is_active=True)
        workspace = Workspace(name="Generation workspace")
        db.add_all([user, workspace])
        db.flush()
        db.add_all([
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"),
            Subscription(workspace_id=workspace.id, plan="starter", status="active", billing_status="active"),
        ])
        integration = Integration(workspace_id=workspace.id, provider="meta" if provider == "facebook_pages" else provider, name=provider, status="connected")
        db.add(integration)
        db.flush()
        account = IntegrationAccount(workspace_id=workspace.id, integration_id=integration.id, external_account_id="account_123", display_name="Account")
        db.add(account)
        db.flush()
        dataset = Dataset(workspace_id=workspace.id, name="Pinned dataset", data={
            "integration_type": provider, "integration_id": integration.id, "account_id": "account_123", "page_id": "account_123",
            "page_name": "Account", "account_name": "Account", "username": "account", "currency": "USD",
            "timeframe": {"key": "custom", "since": "2026-08-01", "until": "2026-08-31", "label": "August 2026"},
            "followers": 100, "reach": 500, "impressions": 800, "engagement": 50, "profile_visits": 20,
            "reach_daily": [{"date": "2026-08-01", "value": 500}],
            "impressions_daily": [{"date": "2026-08-01", "value": 800}],
            "shop_name": "Store", "revenue": 420.5, "orders": 3, "aov": 140.17,
            "total_spend": 120.5, "total_impressions": 800, "total_reach": 500,
        })
        db.add(dataset)
        db.flush()
        db.add(DatasetFile(workspace_id=workspace.id, dataset_id=dataset.id, s3_key="inputs/test.csv", content_type="text/csv", size_bytes=10))
        for index in range(used):
            db.add(Report(workspace_id=workspace.id, dataset_id=dataset.id, name=f"Legacy {index}", description="{}"))
        db.commit()
        return GenerateReportCommand(
            workspace_id=workspace.id, actor_user_id=user.id,
            sources=(SourceIdentity(dataset.id, integration.provider, provider, integration.id, account.id, "account_123"),),
            configuration=ExecutableReportConfiguration("meta_pages" if provider == "facebook_pages" else provider, requested_slides=5),
            period=ReportingPeriod("custom", "2026-08-01", "2026-08-31"),
            options=GenerationOptions(title="Canonical report"), idempotency_key="logical-generation",
        )


def persist_stub(command, prepared):
    return ReportDraft(
        name=command.options.title or "Report", metadata={}, sources=command.sources,
        block_specs=({"order": 1, "type": "stat", "data_json": '{"canonical_binding":"reach.total","value":500}',
                      "editable_fields_json": "[]"},),
    )


@pytest.fixture
def stub_builder(monkeypatch):
    monkeypatch.setattr(builders, "build_report", persist_stub)


def test_final_allowance_completes_and_next_attempt_is_rejected(factory, stub_builder):
    command = seed(factory, used=9)
    with factory() as db:
        result = generate_report(db, command)
        assert result.outcome == "completed"
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10
        assert json.loads(db.get(Report, result.report_id).description)["generation_status"] == "completed"
    with factory() as db, pytest.raises(GenerationError, match="monthly report limit"):
        generate_report(db, replace(command, idempotency_key="next-attempt"))
    with factory() as db:
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).count() == 1


def test_retry_at_exhausted_quota_reuses_report_and_preserves_bindings(factory, stub_builder):
    command = seed(factory, used=9)
    with factory() as db:
        first = generate_report(db, command)
    with factory() as db:
        second = generate_report(db, command)
        assert second.replayed is True
        assert (second.report_id, second.version_id, second.generation_id) == (first.report_id, first.version_id, first.generation_id)
        assert db.query(ReportVersion).count() == 1
        assert db.query(ReportBlock).count() == 1
        assert db.query(ReportSource).one().integration_account_id == command.sources[0].integration_account_id
        assert json.loads(db.query(ReportBlock).one().data_json)["canonical_binding"] == "reach.total"
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10


def test_failed_generation_rolls_back_and_same_identity_can_retry(factory, monkeypatch):
    command = seed(factory, used=9)

    def fail(command, prepared):
        persist_stub(command, prepared)
        raise RuntimeError("technical failure")

    monkeypatch.setattr(builders, "build_report", fail)
    with factory() as db, pytest.raises(RuntimeError, match="technical failure"):
        generate_report(db, command)
    with factory() as db:
        assert db.query(Report).count() == 9
        assert db.query(ReportVersion).count() == db.query(ReportBlock).count() == db.query(ReportSource).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"
        quota = get_workspace_report_quota_status(db, command.workspace_id)
        assert quota["capacity_remaining"] == 1
        assert quota["reports_reserved"] == 0
    monkeypatch.setattr(builders, "build_report", persist_stub)
    with factory() as db:
        assert generate_report(db, command).outcome == "completed"
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10
        assert db.query(ReportGeneration).one().attempt_count == 2


def test_lower_level_commit_is_rejected_and_rolled_back(factory, monkeypatch, stub_builder):
    command = seed(factory)
    original = generation_module._persist_report_draft

    def bad_persistence(db, command, draft):
        result = original(db, command, draft)
        db.commit()
        return result

    monkeypatch.setattr(generation_module, "_persist_report_draft", bad_persistence)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, command)
    assert exc.value.code == "generation_transaction_violation"
    with factory() as db:
        assert db.query(Report).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"


def test_idempotency_conflict_does_not_create_or_charge(factory, stub_builder):
    command = seed(factory)
    with factory() as db:
        generate_report(db, command)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, replace(command, options=replace(command.options, title="Different report")))
    assert exc.value.code == "idempotency_conflict"
    with factory() as db:
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 1


def test_deleted_result_does_not_refund_usage_or_regenerate_on_retry(factory, stub_builder):
    command = seed(factory)
    with factory() as db:
        result = generate_report(db, command)
        db.delete(db.get(Report, result.report_id))
        db.commit()
        assert db.query(ReportGeneration).one().report_id is None
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 1
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, command)
    assert exc.value.code == "generated_report_deleted"


@pytest.mark.parametrize("missing", ["blocks", "sources", "wrong_account"])
def test_invalid_final_state_never_commits(factory, monkeypatch, stub_builder, missing):
    command = seed(factory)
    original = generation_module._persist_report_draft

    def invalid(db, command, draft):
        built = original(db, command, draft)
        if missing == "blocks":
            db.query(ReportBlock).delete()
        elif missing == "sources":
            db.query(ReportSource).delete()
        else:
            db.query(ReportSource).one().integration_account_id = None
        return built

    monkeypatch.setattr(generation_module, "_persist_report_draft", invalid)
    with factory() as db, pytest.raises(GenerationError):
        generate_report(db, command)
    with factory() as db:
        assert db.query(Report).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"


def test_report_spec_validation_is_authoritative_and_valid_but_unexecutable_spec_is_rejected(factory, stub_builder):
    command = seed(factory)
    for spec, code in [({}, "invalid_report_spec"), (FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC.as_dict(), "report_spec_not_executable")]:
        with factory() as db, pytest.raises(GenerationError) as exc:
            generate_report(db, replace(command, configuration=replace(command.configuration, report_spec=spec)))
        assert exc.value.code == code
    with factory() as db:
        assert db.query(ReportGeneration).count() == 0


def test_cross_workspace_source_is_rejected(factory, stub_builder):
    first = seed(factory)
    other = seed(factory)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, replace(first, sources=other.sources))
    assert exc.value.code == "source_workspace_mismatch"


def concurrent_attempts(factory, command, monkeypatch, *, same_key=False, first_fails=False):
    started = Barrier(2)
    builder_entered = Event()
    release_builder = Event()
    rejected_while_building = Event()
    calls = []

    def controlled_builder(command, prepared):
        calls.append(command.idempotency_key)
        builder_entered.set()
        assert release_builder.wait(10)
        if first_fails:
            raise RuntimeError("first attempt failed")
        return persist_stub(command, prepared)

    monkeypatch.setattr(builders, "build_report", controlled_builder)

    def execute(index):
        with factory() as db:
            started.wait(timeout=10)
            logical_command = replace(command, idempotency_key="same" if same_key else f"attempt-{index}")
            try:
                return logical_command, generate_report(db, logical_command)
            except (GenerationError, RuntimeError) as exc:
                if isinstance(exc, GenerationError):
                    rejected_while_building.set()
                return logical_command, exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, index) for index in range(2)]
        try:
            assert builder_entered.wait(10)
            assert rejected_while_building.wait(10), "Quota lock remained held during generation"
            with factory() as observer:
                quota = get_workspace_report_quota_status(observer, command.workspace_id)
                assert quota["reports_used"] == 9
                assert quota["reports_reserved"] == 1
                assert quota["capacity_remaining"] == 0
                observer.query(Workspace).filter(Workspace.id == command.workspace_id).with_for_update(nowait=True).one()
                assert observer.query(Report).count() == 9
        finally:
            release_builder.set()
        results = [future.result(timeout=20) for future in futures]

    errors = [(logical, result) for logical, result in results if isinstance(result, GenerationError)]
    assert len(errors) == 1
    rejected_command, error = errors[0]
    assert error.code == ("generation_in_progress" if same_key else "monthly_report_limit_reached")
    assert len(calls) == 1
    successes = [result for _logical, result in results if not isinstance(result, Exception)]
    if first_fails:
        assert not successes
        with factory() as db:
            assert get_workspace_report_quota_status(db, command.workspace_id)["capacity_remaining"] == 1
        monkeypatch.setattr(builders, "build_report", persist_stub)
        with factory() as db:
            assert generate_report(db, rejected_command).outcome == "completed"
    else:
        assert len(successes) == 1
        if same_key:
            with factory() as db:
                retry = generate_report(db, rejected_command)
                assert retry.replayed
                assert retry.report_id == successes[0].report_id
                assert db.query(ReportGeneration).one().attempt_count == 1
    with factory() as db:
        quota = get_workspace_report_quota_status(db, command.workspace_id)
        assert quota["reports_used"] == 10
        assert quota["reports_reserved"] == 0
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).filter(ReportGeneration.state == "consumed").count() == 1


@pytest.mark.parametrize("same_key,first_fails", [(False, False), (True, False), (False, True)])
def test_sqlite_concurrent_capacity(factory, monkeypatch, same_key, first_fails):
    concurrent_attempts(factory, seed(factory, used=9), monkeypatch, same_key=same_key, first_fails=first_fails)


@pytest.mark.parametrize("same_key,first_fails", [(False, False), (True, False), (False, True)])
def test_postgres_concurrent_capacity(postgres_factory, monkeypatch, same_key, first_fails):
    concurrent_attempts(postgres_factory, seed(postgres_factory, used=9), monkeypatch, same_key=same_key, first_fails=first_fails)


@pytest.fixture
def api_client(factory, monkeypatch):
    def override_db():
        with factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main, "_generate_and_store_report_thumbnail", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_track_meta_event", lambda **_kwargs: None)
    monkeypatch.setattr(main, "generate_meta_pages_ai_summary", lambda *_args, **_kwargs: "Report analysis")
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("endpoint,provider", [
    ("/reports", "dataset"), ("/reports/meta-pages", "facebook_pages"),
    ("/reports/instagram-business", "instagram_business"), ("/reports/meta-ads", "meta_ads"),
    ("/reports/shopify", "shopify"),
])
def test_manual_provider_endpoint_uses_real_builder_and_one_generation(factory, api_client, endpoint, provider):
    command = seed(factory, provider=provider)
    response = api_client.post(endpoint, headers={"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}"}, json={
        "dataset_id": command.sources[0].dataset_id, "title": "Manual report", "requested_slides": 5,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    report_id = result.get("report_id", result.get("id"))
    with factory() as db:
        report = db.get(Report, report_id)
        assert json.loads(report.description)["generation_status"] == "completed"
        assert db.query(ReportGeneration).one().report_id == report_id
        assert db.query(ReportVersion).count() == 1
        assert db.query(ReportBlock).count() == 5
        if provider != "dataset":
            assert db.query(ReportSource).one().dataset_id == command.sources[0].dataset_id
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 1


def test_manual_header_is_optional_and_explicit_retry_reuses_generation(factory, api_client):
    command = seed(factory, provider="shopify")
    headers = {"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}", "Idempotency-Key": "http-retry"}
    body = {"dataset_id": command.sources[0].dataset_id, "title": "Shopify"}
    first = api_client.post("/reports/shopify", headers=headers, json=body)
    second = api_client.post("/reports/shopify", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["report_id"] == second.json()["report_id"]
    with factory() as db:
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 1


def test_generic_csv_generation_loads_metrics_and_preserves_default_slide_count(factory, api_client, monkeypatch):
    command = seed(factory)
    with factory() as db:
        dataset = db.get(Dataset, command.sources[0].dataset_id)
        csv_row = dict(dataset.data)
        dataset.data = {}
        db.commit()
    monkeypatch.setattr(main, "_load_dataset_row", lambda _file: csv_row)
    response = api_client.post("/reports", headers={"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}"},
                               json={"dataset_id": command.sources[0].dataset_id, "title": "CSV report"})
    assert response.status_code == 200, response.text
    with factory() as db:
        report = db.get(Report, response.json()["id"])
        metadata = json.loads(report.description)
        assert metadata["timeframe"] == csv_row["timeframe"]
        assert metadata["requested_slides"] == main.DEFAULT_GENERATED_REPORT_SLIDE_COUNT
        assert db.query(ReportBlock).count() == main.DEFAULT_GENERATED_REPORT_SLIDE_COUNT
        assert db.query(ReportGeneration).count() == 1


def test_generic_agent_mode_reuses_existing_pipeline_and_metadata(factory, api_client, monkeypatch):
    command = seed(factory)
    with factory() as db:
        db.query(Subscription).one().plan = "advanced"
        db.commit()
    calls = []

    def agent_pipeline(**kwargs):
        calls.append(kwargs)
        return {"blocks": kwargs["block_specs"], "used": True, "fallback_used": False, "errors": []}

    monkeypatch.setattr(main, "run_ai_agents_pipeline", agent_pipeline)
    response = api_client.post("/reports", headers={"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}"},
                               json={"dataset_id": command.sources[0].dataset_id, "title": "Agent report", "ai_mode": "agents"})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["dataset_context"]["dataset_id"] == command.sources[0].dataset_id
    with factory() as db:
        metadata = json.loads(db.get(Report, response.json()["id"]).description)
        assert metadata["ai_mode"] == "agents"
        assert metadata["ai_agents_enabled"] is True
        assert metadata["ai_agents_used"] is True
        assert metadata["ai_agent_fallback_used"] is False
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 1


def test_extra_report_cannot_bypass_capacity(factory, monkeypatch, stub_builder):
    command = seed(factory, used=9)
    original = generation_module._persist_report_draft

    def duplicate_persistence(db, command, draft):
        result = original(db, command, draft)
        original(db, command, draft)
        return result

    monkeypatch.setattr(generation_module, "_persist_report_draft", duplicate_persistence)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, command)
    assert exc.value.code == "invalid_generation_cardinality"
    with factory() as db:
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 9


def test_expired_subscription_window_cannot_hide_new_usage(factory, stub_builder):
    command = seed(factory, used=9)
    with factory() as db:
        subscription = db.query(Subscription).one()
        subscription.current_period_start = datetime.now(timezone.utc) - timedelta(days=90)
        subscription.current_period_end = datetime.now(timezone.utc) - timedelta(days=60)
        db.commit()
        generate_report(db, command)
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10
    with factory() as db, pytest.raises(GenerationError):
        generate_report(db, replace(command, idempotency_key="next"))


def test_pinned_period_mismatch_is_rejected(factory, stub_builder):
    command = seed(factory)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, replace(command, period=ReportingPeriod("custom", "2026-07-01", "2026-07-31")))
    assert exc.value.code == "dataset_period_mismatch"


@pytest.mark.parametrize("provider", ["facebook_pages", "instagram_business", "meta_ads"])
def test_pinned_account_cannot_use_another_accounts_dataset(factory, stub_builder, provider):
    command = seed(factory, provider=provider)
    with factory() as db:
        other = IntegrationAccount(workspace_id=command.workspace_id, integration_id=command.sources[0].integration_id,
                                   external_account_id="other_account", display_name="Other account")
        db.add(other)
        db.commit()
        wrong_source = replace(command.sources[0], integration_account_id=other.id, external_account_id="other_account")
        with pytest.raises(GenerationError) as exc:
            generate_report(db, replace(command, sources=(wrong_source,)))
        assert exc.value.code == "dataset_account_mismatch"
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 0


def test_dataset_cannot_be_pinned_to_another_integration(factory, stub_builder):
    command = seed(factory)
    with factory() as db:
        other = Integration(workspace_id=command.workspace_id, provider="dataset", name="Other", status="connected")
        db.add(other)
        db.commit()
        wrong_source = replace(command.sources[0], integration_id=other.id, integration_account_id=None)
        with pytest.raises(GenerationError) as exc:
            generate_report(db, replace(command, sources=(wrong_source,)))
        assert exc.value.code == "dataset_integration_mismatch"


@pytest.mark.parametrize("configuration,options,code", [
    (ExecutableReportConfiguration("dataset", template="unsupported"), GenerationOptions(), "template_not_executable"),
    (ExecutableReportConfiguration("dataset", requested_slides=0), GenerationOptions(), "invalid_slide_count"),
    (ExecutableReportConfiguration("dataset"), GenerationOptions(ai_mode="unsupported"), "invalid_ai_mode"),
])
def test_unsupported_generation_options_are_not_silently_converted(factory, stub_builder, configuration, options, code):
    command = seed(factory)
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, replace(command, configuration=configuration, options=options))
    assert exc.value.code == code


def test_post_commit_failure_does_not_fail_or_recharge_final_report(factory, api_client, monkeypatch):
    command = seed(factory, provider="shopify", used=9)
    calls = []

    def failing_thumbnail(**kwargs):
        with factory() as observer:
            assert observer.get(Report, kwargs["report"].id) is not None
            assert observer.query(ReportGeneration).count() == 1
            assert get_workspace_report_quota_status(observer, command.workspace_id)["reports_used"] == 10
        calls.append(True)
        raise RuntimeError("thumbnail unavailable")

    monkeypatch.setattr(main, "_generate_and_store_report_thumbnail", failing_thumbnail)
    headers = {"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}", "Idempotency-Key": "final-report"}
    body = {"dataset_id": command.sources[0].dataset_id, "title": "Final report"}
    first = api_client.post("/reports/shopify", headers=headers, json=body)
    retry = api_client.post("/reports/shopify", headers=headers, json=body)
    assert first.status_code == retry.status_code == 200
    assert first.json()["report_id"] == retry.json()["report_id"]
    assert calls == [True]
    headers["Idempotency-Key"] = "another-report"
    blocked = api_client.post("/reports/shopify", headers=headers, json=body)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "monthly_report_limit_reached"


def test_multi_source_manual_and_service_generation_have_identical_blocks(factory, api_client):
    first = seed(factory, provider="facebook_pages")
    with factory() as db:
        row = dict(db.get(Dataset, first.sources[0].dataset_id).data)
        row.update(integration_type="instagram_business", account_id="ig_456", page_id="ig_456")
        integration = Integration(workspace_id=first.workspace_id, provider="instagram_business", name="Instagram", status="connected")
        db.add(integration)
        db.flush()
        row["integration_id"] = integration.id
        account = IntegrationAccount(workspace_id=first.workspace_id, integration_id=integration.id, external_account_id="ig_456", display_name="Instagram")
        dataset = Dataset(workspace_id=first.workspace_id, name="Instagram data", data=row)
        db.add_all([account, dataset])
        db.flush()
        second = SourceIdentity(dataset.id, integration.provider, "instagram_business", integration.id, account.id, "ig_456", position=1)
        db.commit()
    sources = (first.sources[0], second)
    body = {
        "title": "Cross-source", "requested_slides": 10, "timeframe": "custom", "start_date": "2026-08-01", "end_date": "2026-08-31",
        "sources": [{"dataset_id": source.dataset_id, "provider": source.provider, "source_type": source.source_type,
                     "integration_id": source.integration_id, "integration_account_id": source.integration_account_id,
                     "position": source.position} for source in sources],
    }
    response = api_client.post("/reports/multi-source", headers={"Authorization": f"Bearer {create_access_token(str(first.actor_user_id))}"}, json=body)
    assert response.status_code == 200, response.text
    manual_id = response.json()["id"]
    command = replace(first, actor_user_id=None, sources=sources, configuration=ExecutableReportConfiguration("multi_source", requested_slides=10), options=GenerationOptions(title="Cross-source"))
    with factory() as db:
        service_result = generate_report(db, command)
        assert service_result.outcome == "completed"
        assert db.query(ReportGeneration).count() == 2
        assert db.query(ReportSource).count() == 4
        versions = [db.query(ReportVersion).filter(ReportVersion.report_id == report_id).one() for report_id in (manual_id, service_result.report_id)]
        block_sets = [[json.loads(block.data_json) for block in db.query(ReportBlock).filter(ReportBlock.report_version_id == version.id).order_by(ReportBlock.order)] for version in versions]
        assert len(block_sets[0]) == len(block_sets[1]) == 10
        assert block_sets[0] == block_sets[1]


def _downgrade_execution_for_ledger_migration(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/20260910_000032_scheduled_report_execution.py"))
    with Operations.context(MigrationContext.configure(connection)):
        migration["downgrade"]()


def test_postgres_migration_preserves_historical_reports_and_usage(postgres_factory):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    command = seed(postgres_factory, used=9)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/20260909_000030_add_report_generations.py"))
    engine = postgres_factory.kw["bind"]
    with engine.begin() as connection:
        _downgrade_execution_for_ledger_migration(connection)
        ReportGeneration.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
    with postgres_factory() as db:
        assert db.query(Report).count() == 9
        assert db.query(ReportGeneration).count() == 9
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 9


@pytest.mark.parametrize("factory_name", ["factory", "postgres_factory"])
def test_expired_attempt_is_fenced_and_same_identity_reclaims_only_one_unit(request, factory_name, monkeypatch):
    factory = request.getfixturevalue(factory_name)
    command = seed(factory, used=9)
    with factory() as db:
        stale = generation_module._reserve_generation(db, command)
        generation = db.get(ReportGeneration, stale.generation_id)
        generation.lease_expires_at = generation_module._database_now(db) - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        current = generation_module._reserve_generation(db, command)
        assert current.generation_id == stale.generation_id
        assert current.attempt_token != stale.attempt_token
        assert db.get(ReportGeneration, current.generation_id).attempt_count == 2
        db.rollback()
        with pytest.raises(GenerationError) as exc:
            generation_module._finalize_generation(db, command, stale, persist_stub(command, None))
        assert exc.value.code == "generation_reservation_lost"
        generation_module._release_reservation(db, stale, "late_worker_failure")
        assert db.get(ReportGeneration, current.generation_id).state == "reserved"
        db.rollback()
        completed = generation_module._finalize_generation(db, command, current, persist_stub(command, None))
        replay = generate_report(db, command)
        assert replay.replayed and replay.report_id == completed.report_id
        assert db.query(Report).count() == 10
        assert db.query(ReportGeneration).count() == 1
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10


@pytest.mark.parametrize("factory_name", ["factory", "postgres_factory"])
def test_crashed_reservation_expires_without_manual_cleanup(request, factory_name, stub_builder):
    factory = request.getfixturevalue(factory_name)
    command = seed(factory, used=9)
    with factory() as db:
        abandoned = generation_module._reserve_generation(db, command)
        db.get(ReportGeneration, abandoned.generation_id).lease_expires_at = generation_module._database_now(db) - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        result = generate_report(db, replace(command, idempotency_key="new-occurrence"))
        assert result.outcome == "completed"
        assert db.get(ReportGeneration, abandoned.generation_id).state == "failed"
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10


@pytest.mark.parametrize("stage", ["prepare", "persist"])
def test_postgres_failures_release_durable_capacity(postgres_factory, monkeypatch, stub_builder, stage):
    command = seed(postgres_factory, used=9)
    target, attribute = (builders, "prepare_report_inputs") if stage == "prepare" else (generation_module, "_persist_report_draft")
    original = getattr(target, attribute)

    def fail(*args):
        original(*args)
        raise RuntimeError("forced failure")

    monkeypatch.setattr(target, attribute, fail)
    with postgres_factory() as db, pytest.raises(RuntimeError, match="forced failure"):
        generate_report(db, command)
    with postgres_factory() as db:
        assert db.query(Report).count() == 9
        assert db.query(ReportVersion).count() == db.query(ReportBlock).count() == db.query(ReportSource).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"
        assert get_workspace_report_quota_status(db, command.workspace_id)["capacity_remaining"] == 1
    monkeypatch.setattr(target, attribute, original)
    with postgres_factory() as db:
        assert generate_report(db, command).outcome == "completed"
        assert db.query(ReportGeneration).one().attempt_count == 2
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 10


@pytest.mark.parametrize("provider", ["dataset", "facebook_pages", "instagram_business", "meta_ads", "shopify"])
def test_postgres_real_builders_have_no_open_transaction_or_quota_lock(postgres_factory, monkeypatch, provider):
    command = seed(postgres_factory, provider=provider)
    real_builder = builders.build_report
    with postgres_factory() as db:
        def assert_unlocked():
            assert not db.in_transaction()
            with postgres_factory() as observer:
                observer.query(Workspace).filter(Workspace.id == command.workspace_id).with_for_update(nowait=True).one()
                quota = get_workspace_report_quota_status(observer, command.workspace_id)
                assert quota["reports_reserved"] == 1
                assert quota["reports_used"] == 0
                assert observer.query(Report).count() == 0

        def build_without_database(command, prepared):
            assert_unlocked()
            result = real_builder(command, prepared)
            assert_unlocked()
            return result

        def fake_ai(*_args, **_kwargs):
            assert_unlocked()
            return "Generated analysis"

        monkeypatch.setattr(builders, "build_report", build_without_database)
        monkeypatch.setattr(main, "generate_meta_pages_ai_summary", fake_ai)
        assert generate_report(db, command).outcome == "completed"


def test_csv_and_thumbnail_io_do_not_hold_generation_session_transaction(factory, monkeypatch):
    command = seed(factory)
    with factory() as db:
        dataset = db.get(Dataset, command.sources[0].dataset_id)
        row = dict(dataset.data)
        dataset.data = {}
        db.commit()
        calls = []

        def load_csv(_file):
            assert not db.in_transaction()
            calls.append("csv")
            return row

        def thumbnail(**_kwargs):
            assert not db.in_transaction()
            calls.append("thumbnail")
            return b"image", {}

        def store_thumbnail(_report_id, _image):
            assert not db.in_transaction()
            calls.append("s3")
            return "report-thumbnails/test.png"

        def track(**_kwargs):
            assert not db.in_transaction()
            calls.append("analytics")

        monkeypatch.setattr(main, "_load_dataset_row", load_csv)
        monkeypatch.setattr(main, "generate_meta_pages_ai_summary", lambda *_a, **_k: "Analysis")
        monkeypatch.setattr(main, "generate_thumbnail_from_export_page", thumbnail)
        monkeypatch.setattr(main, "store_report_thumbnail", store_thumbnail)
        monkeypatch.setattr(main, "_track_meta_event", track)
        response = main._generate_manual_report(
            dataset=db.get(Dataset, command.sources[0].dataset_id), payload=main.ReportCreateIn(dataset_id=command.sources[0].dataset_id, title="CSV"),
            current_user=db.get(User, command.actor_user_id), request=None, db=db, builder="dataset",
        )
        assert response.report_id is not None
        assert calls == ["csv", "csv", "thumbnail", "s3", "analytics"]


def test_legacy_job_generation_is_rejected_before_any_write(factory):
    from app.services import _run_local_job, enqueue_job

    command = seed(factory)
    with factory() as db:
        with pytest.raises(RuntimeError, match="canonical generate_report"):
            enqueue_job(db, "generate_report", {"dataset_id": command.sources[0].dataset_id}, command.workspace_id)
        with pytest.raises(RuntimeError, match="canonical generate_report"):
            _run_local_job(db, Job(workspace_id=command.workspace_id, type="generate_report", payload_json="{}", status="queued"))
        assert db.query(Job).count() == db.query(Report).count() == db.query(ReportGeneration).count() == 0


def test_postgres_migration_downgrade_is_guarded(postgres_factory):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    command = seed(postgres_factory, used=1)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/20260909_000030_add_report_generations.py"))
    engine = postgres_factory.kw["bind"]
    with engine.begin() as connection:
        _downgrade_execution_for_ledger_migration(connection)
        ReportGeneration.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            migration["downgrade"]()
            migration["upgrade"]()
    with postgres_factory() as db:
        generation_module._reserve_generation(db, command)
    with pytest.raises(RuntimeError, match="Cannot downgrade"), engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["downgrade"]()
    with postgres_factory() as db:
        assert db.query(Report).count() == 1
        assert db.query(ReportGeneration).count() == 2


def test_postgres_migration_backfill_100000_reports(postgres_factory, record_property):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    command = seed(postgres_factory)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/20260909_000030_add_report_generations.py"))
    engine = postgres_factory.kw["bind"]
    with engine.begin() as connection:
        _downgrade_execution_for_ledger_migration(connection)
        connection.execute(text("INSERT INTO reports (workspace_id, dataset_id, name, description) SELECT :workspace, :dataset, 'Legacy ' || n, '{}' FROM generate_series(1, 100000) n"),
                           {"workspace": command.workspace_id, "dataset": command.sources[0].dataset_id})
        ReportGeneration.__table__.drop(connection)
    started = perf_counter()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
    record_property("backfill_100000_seconds", round(perf_counter() - started, 3))
    with postgres_factory() as db:
        assert db.query(Report).count() == db.query(ReportGeneration).count() == 100000
        assert get_workspace_report_quota_status(db, command.workspace_id)["reports_used"] == 100000


def test_optional_snapshot_fields_preserve_phase_one_idempotency_hash(factory):
    from dataclasses import asdict
    import hashlib

    command = seed(factory)
    original_payload = asdict(command)
    original_payload.pop("idempotency_key")
    original_payload["configuration"].pop("branding")
    original_payload["configuration"].pop("builder_contract")
    original_payload["configuration"].pop("report_template_id")
    original_payload["configuration"].pop("report_template_version_id")
    original_hash = hashlib.sha256(json.dumps(original_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert generation_module._command_hash(command) == original_hash


def test_pinned_branding_reaches_same_builder_without_mutable_workspace_lookup(factory, monkeypatch):
    from app.services import resolve_report_branding_for_workspace

    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        branding = resolve_report_branding_for_workspace(db, command.workspace_id, preferred_branding={"brand_name": "Frozen agency"})
        db.get(Workspace, command.workspace_id).name = "Changed after snapshot"
        db.commit()
    command = replace(command, configuration=replace(command.configuration, branding=branding,
                                                       builder_contract=builders.current_builder_contract("meta_pages")))
    seen = []
    def build(command, prepared):
        seen.append(prepared.branding)
        return persist_stub(command, prepared)
    monkeypatch.setattr(builders, "build_report", build)
    with factory() as db:
        first = generate_report(db, command)
        retry = generate_report(db, command)
    assert seen == [branding]
    assert retry.replayed and first.report_id == retry.report_id


def test_incompatible_pinned_builder_rejected_before_capacity_reservation(factory, stub_builder):
    command = seed(factory, provider="facebook_pages")
    command = replace(command, configuration=replace(command.configuration, builder_contract="obsolete"))
    with factory() as db:
        with pytest.raises(GenerationError, match="pinned builder"):
            generate_report(db, command)
        assert db.query(ReportGeneration).count() == db.query(Report).count() == 0
