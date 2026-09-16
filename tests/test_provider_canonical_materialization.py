import os
from pathlib import Path

TEST_DB_PATH = Path("/tmp/measurable_provider_canonical_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("META_APP_ID", "meta-app-id")
os.environ.setdefault("META_APP_SECRET", "meta-app-secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/integrations/meta/callback")
os.environ.setdefault("META_PAGES_APP_ID", "meta-pages-app-id")
os.environ.setdefault("META_PAGES_APP_SECRET", "meta-pages-app-secret")
os.environ.setdefault("META_PAGES_REDIRECT_URI", "http://localhost:8000/integrations/meta/callback-pages")
os.environ.setdefault("META_ADS_APP_ID", "meta-ads-app-id")
os.environ.setdefault("META_ADS_APP_SECRET", "meta-ads-app-secret")
os.environ.setdefault("META_ADS_REDIRECT_URI", "http://localhost:8000/integrations/meta-ads/callback")

import app.main as providers
from app.models import Integration, IntegrationAccount, MetaAdAccount, Workspace
from app.scheduled_reports import SourceInput, _sources, _validate_source_configuration
from test_report_generation import factory, seed
from test_scheduled_reports import client, headers


def _catalog(client, command, builder):
    return client.get(
        "/scheduled-reports/source-catalog",
        params={"workspace_id": command.workspace_id, "builder": builder},
        headers=headers(command),
    )


def test_meta_suite_instagram_records_materialize_stable_workspace_accounts(factory, client):
    command = seed(factory)
    records = [
        {"record_type": "facebook_page", "page_id": "fb-1", "name": "Internal Page"},
        {
            "record_type": "instagram_account",
            "page_id": "ig-1",
            "name": "Internal Instagram",
            "instagram_username": "internal_ig",
        },
    ]
    with factory() as db:
        integration = Integration(
            workspace_id=command.workspace_id,
            provider="meta",
            name="Meta Pages",
            status="connected",
        )
        db.add(integration)
        db.flush()
        providers._cache_meta_pages(db, integration, command.actor_user_id, records)
        first = db.query(IntegrationAccount).filter_by(
            integration_id=integration.id,
            external_account_id="ig-1",
        ).one()
        first_id = first.id
        assert first.workspace_id == command.workspace_id
        assert first.display_name == "Internal Instagram"

        records[1]["name"] = "Renamed Internal Instagram"
        providers._cache_meta_pages(db, integration, command.actor_user_id, records)
        second = db.query(IntegrationAccount).filter_by(
            integration_id=integration.id,
            external_account_id="ig-1",
        ).one()
        assert second.id == first_id
        assert second.display_name == "Renamed Internal Instagram"
        integration_id = integration.id

    response = _catalog(client, command, "instagram_business")
    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"]
        if item["binding"]["integration_id"] == integration_id
    )
    assert item["available"] and item["connected"]
    assert item["display_label"] == "Renamed Internal Instagram"
    with factory() as db:
        binding = SourceInput.model_validate(item["binding"])
        _validate_source_configuration({"builder": "instagram_business"}, [binding.model_dump()])
        assert _sources(db, command.workspace_id, [binding]) == [binding.model_dump()]


def test_meta_suite_instagram_reconciliation_removes_only_stale_instagram_identity(factory):
    command = seed(factory)
    with factory() as db:
        integration = Integration(workspace_id=command.workspace_id, provider="meta", status="connected")
        db.add(integration)
        db.flush()
        providers._cache_meta_pages(db, integration, command.actor_user_id, [
            {"record_type": "facebook_page", "page_id": "fb-1", "name": "Facebook"},
            {"record_type": "instagram_account", "page_id": "ig-1", "name": "Instagram One"},
            {"record_type": "instagram_account", "page_id": "ig-2", "name": "Instagram Two"},
        ])
        providers._cache_meta_pages(db, integration, command.actor_user_id, [
            {"record_type": "facebook_page", "page_id": "fb-1", "name": "Facebook"},
            {"record_type": "instagram_account", "page_id": "ig-2", "name": "Instagram Two"},
        ])
        external_ids = {
            row.external_account_id
            for row in db.query(IntegrationAccount).filter_by(integration_id=integration.id)
        }
        assert providers._meta_page_account_external_id("fb-1") in external_ids
        assert "ig-2" in external_ids
        assert "ig-1" not in external_ids


def test_meta_ads_discovery_materializes_stable_accounts_and_catalog_bindings(factory, client, monkeypatch):
    command = seed(factory)
    monkeypatch.setattr(providers, "_table_available", lambda _name: True)
    payloads = [
        {"id": "act_100", "account_id": "100", "name": "Measurable App"},
        {"id": "act_200", "account_id": "200", "name": "Second Internal Account"},
    ]
    with factory() as db:
        integration = Integration(
            workspace_id=command.workspace_id,
            provider="meta_ads",
            name="Meta Ads",
            status="connected",
        )
        db.add(integration)
        db.flush()
        providers._persist_meta_ads_accounts(db, integration=integration, accounts=payloads, clear_missing=True)
        first = {
            row.external_account_id: row.id
            for row in db.query(IntegrationAccount).filter_by(integration_id=integration.id)
        }
        assert set(first) == {"100", "200"}

        payloads[0]["name"] = "Measurable App Renamed"
        providers._persist_meta_ads_accounts(db, integration=integration, accounts=payloads, clear_missing=True)
        second = {
            row.external_account_id: row.id
            for row in db.query(IntegrationAccount).filter_by(integration_id=integration.id)
        }
        assert second == first
        assert db.query(IntegrationAccount).filter_by(
            integration_id=integration.id,
            external_account_id="100",
        ).one().display_name == "Measurable App Renamed"
        integration_id = integration.id

    response = _catalog(client, command, "meta_ads")
    assert response.status_code == 200, response.text
    items = [
        item for item in response.json()["items"]
        if item["binding"]["integration_id"] == integration_id
    ]
    assert len(items) == 2
    assert all(item["available"] and item["connected"] for item in items)
    with factory() as db:
        for item in items:
            binding = SourceInput.model_validate(item["binding"])
            _validate_source_configuration({"builder": "meta_ads"}, [binding.model_dump()])
            assert _sources(db, command.workspace_id, [binding]) == [binding.model_dump()]


def test_meta_ads_clear_missing_and_cross_workspace_are_isolated(factory, monkeypatch):
    command = seed(factory)
    monkeypatch.setattr(providers, "_table_available", lambda _name: True)
    with factory() as db:
        first = Integration(workspace_id=command.workspace_id, provider="meta_ads", status="connected")
        other_workspace = Workspace(name="Other Workspace")
        db.add_all([first, other_workspace])
        db.flush()
        second = Integration(workspace_id=other_workspace.id, provider="meta_ads", status="connected")
        db.add(second)
        db.flush()
        accounts = [{"account_id": "100", "name": "Workspace Account"}]
        providers._persist_meta_ads_accounts(db, integration=first, accounts=accounts, clear_missing=True)
        providers._persist_meta_ads_accounts(db, integration=second, accounts=accounts, clear_missing=True)
        first_account = db.query(IntegrationAccount).filter_by(integration_id=first.id).one()
        second_account = db.query(IntegrationAccount).filter_by(integration_id=second.id).one()
        assert first_account.id != second_account.id
        assert first_account.workspace_id == command.workspace_id
        assert second_account.workspace_id == other_workspace.id

        providers._persist_meta_ads_accounts(db, integration=first, accounts=[], clear_missing=True)
        assert db.query(IntegrationAccount).filter_by(integration_id=first.id).count() == 0
        assert db.query(MetaAdAccount).filter_by(integration_id=first.id).count() == 0
        assert db.query(IntegrationAccount).filter_by(integration_id=second.id).count() == 1
