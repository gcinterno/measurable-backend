from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import logging
from types import SimpleNamespace

import pytest

from test_report_generation import factory, postgres_factory, seed
from test_scheduled_reports import body
from test_scheduled_report_execution import refresh, dispatch_claim
import app.main as providers
from app.models import Dataset, Integration, IntegrationAccount, MetaAdAccount, MetaPage, Report, ReportBlock, ReportGeneration, ReportSource, ShopifyConnection, User
from app.report_generation import GenerationError, SourceIdentity
from app.scheduled_report_models import ScheduledReport, ScheduledReportRun
from app.scheduled_report_refresh import refresh_source, private_provider_io
import app.scheduled_report_execution as execution
import app.scheduled_reports as schedules


def seed_provider(factory, provider):
    source_type = "instagram_business" if provider in {"instagram_business_login", "instagram_meta"} else provider
    command = seed(factory, provider=source_type)
    source = command.sources[0]
    with factory() as db:
        integration = db.get(Integration, source.integration_id)
        if provider in {"instagram_business_login", "instagram_meta"}:
            integration.provider = "meta" if provider == "instagram_meta" else provider
            source = replace(source, provider=integration.provider)
        if source_type == "instagram_business":
            db.add(MetaPage(integration_id=integration.id, user_id=command.actor_user_id,
                           record_type="instagram_account", page_id=source.external_account_id, name="Pinned", parent_page_id="parent"))
        if provider == "meta_ads":
            db.add(MetaAdAccount(integration_id=integration.id, workspace_id=command.workspace_id,
                                account_id=source.external_account_id, account_name="Pinned", is_selected=False))
        if provider == "shopify":
            db.add(ShopifyConnection(integration_id=integration.id, workspace_id=command.workspace_id, user_id=command.actor_user_id,
                                     shop_domain=source.external_account_id, status="connected", access_token_encrypted="not-a-real-secret"))
            dataset = db.get(Dataset, source.dataset_id)
            dataset.data = {**dataset.data, "shop_domain": source.external_account_id}
        db.commit()
    return replace(command, sources=(source,))


PROVIDERS = ["facebook_pages", "instagram_business", "instagram_meta", "instagram_business_login", "meta_ads", "shopify"]


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_adapter_explicit_source_exact_dataset_and_no_io_transaction(request, fixture, provider, monkeypatch):
    factory = request.getfixturevalue(fixture)
    command = seed_provider(factory, provider)
    source, calls = command.sources[0], []
    def sync(**kwargs):
        db = kwargs["db"]
        calls.append(kwargs)
        assert db.connection().get_execution_options()["isolation_level"] == "AUTOCOMMIT"
        if fixture == "postgres_factory":
            db.connection().exec_driver_sql("SELECT 1")
            assert db.connection().connection.driver_connection.get_transaction_status() == 0
        if provider == "facebook_pages":
            assert kwargs["page_id"] == source.external_account_id
            assert kwargs["integration_id"] == source.integration_id
        elif provider in {"instagram_business", "instagram_meta"}:
            assert kwargs["selected_meta_record"].integration_id == source.integration_id
            assert kwargs["selected_meta_record"].page_id == source.external_account_id
        elif provider == "instagram_business_login":
            assert kwargs["payload"].integration_id == source.integration_id
            assert kwargs["payload"].instagram_account_id == source.external_account_id
            assert kwargs["payload"].force_live
        elif provider == "meta_ads":
            assert kwargs["account"].account_id == source.external_account_id
            assert not kwargs["account"].is_selected
        elif provider == "shopify":
            assert kwargs["connection"].shop_domain == source.external_account_id
        result = refresh(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                         source=source, start_date="2026-08-01", end_date="2026-08-31")
        return SimpleNamespace(dataset_id=result.dataset_id, status="synced")
    helper = {"facebook_pages": "_run_meta_pages_sync", "instagram_business": "_sync_meta_instagram_account",
              "instagram_meta": "_sync_meta_instagram_account", "instagram_business_login": "_run_instagram_business_login_sync",
              "meta_ads": "_run_meta_ads_sync", "shopify": "_run_shopify_connection_sync"}[provider]
    monkeypatch.setattr(providers, helper, sync)
    monkeypatch.setattr(providers, "_get_selected_meta_page", lambda *a, **kw: pytest.fail("selected account fallback forbidden"))
    result = refresh_source(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                            source=source, start_date="2026-08-01", end_date="2026-08-31")
    assert result.dataset_id != source.dataset_id and result.integration_account_id == source.integration_account_id
    assert len(calls) == 1


@pytest.mark.parametrize(
    "provider",
    ["facebook_pages", "instagram_business", "instagram_meta", "instagram_business_login", "meta_ads"],
)
def test_scheduled_execution_uses_real_canonical_provider_builders(factory, provider, monkeypatch):
    command = seed_provider(factory, provider)
    payload = body(command)
    with factory() as db:
        output = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(payload), db.get(User, command.actor_user_id), db)
        db.get(ScheduledReport, output["id"]).next_run_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    monkeypatch.setattr(providers, "generate_meta_pages_ai_summary", lambda *a, **kw: "Fresh report analysis")
    claim = dispatch_claim(factory, output)
    seen = []
    def pinned_refresh(*args, **kwargs):
        seen.append(kwargs["source"])
        return refresh(*args, **kwargs)
    execution.execute_run(factory, claim, refresher=pinned_refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "SUCCEEDED", (run.error_code, run.stage)
        report = db.get(Report, run.report_id)
        assert json.loads(report.description)["generation_status"] == "completed"
        assert db.query(ReportBlock).filter_by(report_version_id=run.report_version_id).count() == 5
        sources = db.query(ReportSource).filter_by(report_id=report.id).order_by(ReportSource.position).all()
        assert [s.dataset_id for s in sources] == [s["dataset_id"] for s in run.execution_sources_json]
        assert [s.integration_account_id for s in sources] == [s.integration_account_id for s in seen]
        assert db.query(ReportGeneration).count() == 1


def test_provider_logs_and_exception_details_are_not_forwarded(caplog):
    with caplog.at_level(logging.INFO):
        with private_provider_io():
            logging.getLogger("app.main").error("access_token=SUPER_SECRET", extra={"credential": "SUPER_SECRET"})
            logging.getLogger("requests").error("Authorization: SUPER_SECRET")
        logging.getLogger("app.scheduled_report_execution").info("safe_result")
    assert "SUPER_SECRET" not in caplog.text
    assert "safe_result" in caplog.text


@pytest.mark.parametrize("problem", ["workspace", "account", "period"])
def test_invalid_refresh_results_rejected(factory, monkeypatch, problem):
    command = seed_provider(factory, "facebook_pages")
    source = command.sources[0]
    def sync(**kwargs):
        result = refresh(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                         source=source, start_date="2026-08-01", end_date="2026-08-31")
        with factory() as db:
            dataset = db.get(Dataset, result.dataset_id)
            if problem == "workspace":
                other = seed(factory)
                dataset.workspace_id = other.workspace_id
            else:
                data = deepcopy(dataset.data)
                if problem == "account":
                    data["page_id"] = data["account_id"] = "wrong"
                else:
                    data["timeframe"]["until"] = "2026-08-30"
                dataset.data = data
            db.commit()
        return SimpleNamespace(dataset_id=result.dataset_id)
    monkeypatch.setattr(providers, "_run_meta_pages_sync", sync)
    with pytest.raises(GenerationError):
        refresh_source(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                       source=source, start_date="2026-08-01", end_date="2026-08-31")


def test_prefixed_facebook_account_matches_graph_dataset_and_uses_unprefixed_sync_id(factory, monkeypatch):
    command = seed_provider(factory, "facebook_pages")
    source = command.sources[0]
    prefixed = providers.META_PAGE_ACCOUNT_PREFIX + source.external_account_id
    with factory() as db:
        db.get(IntegrationAccount, source.integration_account_id).external_account_id = prefixed
        db.commit()
    source = replace(source, external_account_id=prefixed)
    command = replace(command, sources=(source,))
    with factory() as db:
        # The existing Phase 2 snapshot contract keeps the actual account identity.
        schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command)), db.get(User, command.actor_user_id), db)
    def sync(**kwargs):
        assert kwargs["page_id"] == prefixed.removeprefix(providers.META_PAGE_ACCOUNT_PREFIX)
        result = refresh(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                         source=source, start_date="2026-08-01", end_date="2026-08-31")
        return SimpleNamespace(dataset_id=result.dataset_id)
    monkeypatch.setattr(providers, "_run_meta_pages_sync", sync)
    result = refresh_source(factory, workspace_id=command.workspace_id, actor_user_id=command.actor_user_id,
                            source=source, start_date="2026-08-01", end_date="2026-08-31")
    assert result.external_account_id == prefixed and result.integration_account_id == source.integration_account_id
