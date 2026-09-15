from dataclasses import replace

import pytest
from sqlalchemy import event

from test_report_generation import factory, seed
from test_scheduled_reports import body, client, headers
import app.main as providers
from app.models import Integration, IntegrationAccount, IntegrationToken, MetaAdAccount, MetaPage, ShopifyConnection, Subscription
from app.scheduled_reports import SourceInput, _sources, _validate_source_configuration


def add_sources(factory, command):
    identities = {}
    with factory() as db:
        for key, provider, external in (
            ("facebook_pages", "meta", "__meta_page__:123"),
            ("instagram_meta", "meta", "456"),
            ("instagram_business", "instagram_business", "789"),
            ("instagram_business_login", "instagram_business_login", "987"),
            ("meta_ads", "meta_ads", "act_321"),
            ("shopify", "shopify", "acme.myshopify.com"),
        ):
            integration = Integration(workspace_id=command.workspace_id, provider=provider, status="connected")
            db.add(integration)
            db.flush()
            account = IntegrationAccount(workspace_id=command.workspace_id, integration_id=integration.id,
                                         external_account_id=external, display_name=key)
            db.add(account)
            db.flush()
            identities[key] = (integration.id, account.id, external)
            if key == "facebook_pages" or key.startswith("instagram"):
                db.add(MetaPage(integration_id=integration.id, user_id=command.actor_user_id,
                    record_type="facebook_page" if key == "facebook_pages" else "instagram_account",
                    page_id="123" if key == "facebook_pages" else external, name=key,
                    parent_page_id="private-parent", page_access_token="NEVER_SERIALIZE_PAGE_TOKEN"))
            elif key == "meta_ads":
                db.add(MetaAdAccount(workspace_id=command.workspace_id, integration_id=integration.id,
                    account_id="321", account_name=key, is_selected=False))
            else:
                db.add(ShopifyConnection(workspace_id=command.workspace_id, integration_id=integration.id,
                    user_id=command.actor_user_id, shop_domain=external, shop_name=key, status="connected",
                    access_token_encrypted="NEVER_SERIALIZE_SHOP_SECRET"))
            db.add(IntegrationToken(workspace_id=command.workspace_id, account_id=account.id, token_type="oauth",
                access_token="NEVER_SERIALIZE_ACCESS_TOKEN", refresh_token="NEVER_SERIALIZE_REFRESH_TOKEN"))
        db.commit()
    return identities


def catalog(client, command, builder):
    return client.get("/scheduled-reports/source-catalog", params={"workspace_id": command.workspace_id, "builder": builder},
                      headers=headers(command))


@pytest.mark.parametrize("key,builder", [
    ("facebook_pages", "meta_pages"), ("instagram_meta", "instagram_business"),
    ("instagram_business", "instagram_business"), ("instagram_business_login", "instagram_business"),
    ("meta_ads", "meta_ads"), ("shopify", "shopify"),
])
def test_catalog_binding_round_trips_through_create_and_edit(factory, client, key, builder):
    command = seed(factory)
    identities = add_sources(factory, command)
    response = catalog(client, command, builder)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    item = next(item for item in response.json()["items"] if item["binding"]["integration_id"] == identities[key][0])
    binding = item["binding"]
    assert (binding["integration_id"], binding["integration_account_id"], binding["external_account_id"]) == identities[key]
    assert binding["dataset_id"] is None and binding["position"] == 0
    assert item["available"] and item["connected"] and item["unavailable_reason"] is None
    assert item["display_label"] == key
    payload = {**body(command), "configuration": {"builder": builder, "requested_slides": 5}, "sources": [binding]}
    created = client.post("/scheduled-reports", headers=headers(command), json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["sources"] == [binding]
    edited = client.patch(f"/scheduled-reports/{created.json()['id']}?workspace_id={command.workspace_id}",
                          headers=headers(command), json={"sources": [binding]})
    assert edited.status_code == 200 and edited.json()["sources"] == [binding]
    assert edited.json()["configuration_revision"] == 1


def test_discovered_facebook_pages_materialize_catalog_bindings_accepted_by_validator(factory, client):
    command = seed(factory)
    with factory() as db:
        integration = Integration(
            workspace_id=command.workspace_id,
            provider="meta",
            name="Meta Pages",
            status="connected",
        )
        db.add(integration)
        db.flush()
        providers._cache_meta_pages(
            db,
            integration,
            command.actor_user_id,
            [
                {"record_type": "facebook_page", "page_id": f"fb-{index}", "name": f"Page {index}"}
                for index in range(14)
            ],
        )
        integration_id = integration.id

    response = catalog(client, command, "meta_pages")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["availability"]["scheduling_enabled"] is True
    items = [item for item in payload["items"] if item["binding"]["integration_id"] == integration_id]
    assert len(items) == 14
    assert all(item["connected"] and item["available"] and item["unavailable_reason"] is None for item in items)
    assert {item["binding"]["external_account_id"] for item in items} == {
        providers._meta_page_account_external_id(f"fb-{index}") for index in range(14)
    }

    with factory() as db:
        account_ids = set()
        for item in items:
            binding = SourceInput.model_validate(item["binding"])
            _validate_source_configuration({"builder": "meta_pages"}, [binding.model_dump()])
            assert _sources(db, command.workspace_id, [binding]) == [binding.model_dump()]
            account = db.get(IntegrationAccount, binding.integration_account_id)
            assert account is not None
            assert account.workspace_id == command.workspace_id
            assert account.integration_id == integration_id
            assert account.external_account_id == binding.external_account_id
            account_ids.add(account.id)
        assert len(account_ids) == 14


@pytest.mark.parametrize("instagram_key", ["instagram_meta", "instagram_business", "instagram_business_login"])
def test_multisource_slots_are_submit_ready_without_identity_or_position_inference(factory, client, instagram_key):
    command = seed(factory)
    identities = add_sources(factory, command)
    items = catalog(client, command, "multi_source").json()["items"]
    assert {item["binding"]["source_type"] for item in items} == {"facebook_pages", "instagram_business"}
    bindings = [next(item["binding"] for item in items if item["binding"]["integration_id"] == identities[key][0])
                for key in ("facebook_pages", instagram_key)]
    assert [binding["position"] for binding in bindings] == [0, 1]
    response = client.post("/scheduled-reports", headers=headers(command), json={**body(command),
        "configuration": {"builder": "multi_source", "requested_slides": 10}, "sources": bindings})
    assert response.status_code == 201, response.text
    assert response.json()["sources"] == bindings


@pytest.mark.parametrize("builder", ["meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"])
def test_workspace_isolation_including_inconsistent_child_ownership(factory, client, builder):
    command, other = seed(factory), seed(factory)
    ours, theirs = add_sources(factory, command), add_sources(factory, other)
    with factory() as db:
        # Both directions of inconsistent parent/child ownership must be excluded.
        for parent, workspace in ((ours["meta_ads"][0], other.workspace_id), (theirs["meta_ads"][0], command.workspace_id)):
            db.add(IntegrationAccount(integration_id=parent, workspace_id=workspace, external_account_id="foreign-inconsistent"))
            db.add(MetaAdAccount(integration_id=parent, workspace_id=workspace, account_id="foreign-inconsistent", account_name="FOREIGN"))
        db.commit()
    response = catalog(client, command, builder)
    assert response.status_code == 200
    allowed = {identity[0] for identity in ours.values()}
    assert response.json()["items"]
    assert all(item["binding"]["integration_id"] in allowed for item in response.json()["items"])
    assert "foreign-inconsistent" not in response.text
    response = catalog(client, replace(command, workspace_id=other.workspace_id), builder)
    assert response.status_code == 403


def test_catalog_requires_authentication_and_valid_workspace_and_builder(factory, client):
    command = seed(factory)
    assert client.get("/scheduled-reports/source-catalog", params={"workspace_id": command.workspace_id, "builder": "meta_pages"}).status_code == 401
    for params in ({"workspace_id": command.workspace_id}, {"workspace_id": 0, "builder": "meta_pages"},
                   {"workspace_id": command.workspace_id, "builder": "tiktok"}):
        assert client.get("/scheduled-reports/source-catalog", params=params, headers=headers(command)).status_code == 422


@pytest.mark.parametrize("key,builder", [("facebook_pages", "meta_pages"), ("instagram_business_login", "instagram_business"),
                                       ("meta_ads", "meta_ads"), ("shopify", "shopify")])
def test_disconnected_identities_are_preserved_and_marked_unavailable(factory, client, key, builder):
    command = seed(factory)
    identities = add_sources(factory, command)
    with factory() as db:
        db.get(Integration, identities[key][0]).status = "disconnected"
        if key == "instagram_business_login":
            db.query(MetaPage).filter_by(integration_id=identities[key][0]).delete()
        db.commit()
    item = next(item for item in catalog(client, command, builder).json()["items"]
                if item["binding"]["integration_id"] == identities[key][0])
    assert not item["connected"] and not item["available"]
    assert item["unavailable_reason"] == "source_disconnected"
    assert item["binding"]["integration_account_id"] == identities[key][1]


def test_shopify_without_account_uses_existing_nullable_account_contract(factory, client):
    command = seed(factory)
    identities = add_sources(factory, command)
    with factory() as db:
        db.delete(db.get(IntegrationAccount, identities["shopify"][1]))
        db.commit()
    item = catalog(client, command, "shopify").json()["items"][0]
    assert item["binding"]["integration_account_id"] is None
    response = client.post("/scheduled-reports", headers=headers(command), json={**body(command),
        "configuration": {"builder": "shopify", "requested_slides": 5}, "sources": [item["binding"]]})
    assert response.status_code == 201, response.text
    with factory() as db:
        db.query(ShopifyConnection).filter_by(integration_id=identities["shopify"][0]).update({"status": "disconnected"})
        db.commit()
    assert catalog(client, command, "shopify").json()["items"][0]["unavailable_reason"] == "source_disconnected"


def test_missing_or_ambiguous_provider_mapping_is_not_advertised_as_available(factory, client):
    command = seed(factory)
    identities = add_sources(factory, command)
    with factory() as db:
        db.query(MetaPage).filter_by(integration_id=identities["instagram_business"][0]).delete()
        db.add(MetaAdAccount(integration_id=identities["meta_ads"][0], workspace_id=command.workspace_id,
                            account_id="act_321", account_name="Ambiguous legacy alias"))
        db.commit()
    item = next(item for item in catalog(client, command, "instagram_business").json()["items"]
                if item["binding"]["integration_id"] == identities["instagram_business"][0])
    assert not item["available"] and item["unavailable_reason"] == "source_account_missing"
    item = catalog(client, command, "meta_ads").json()["items"][0]
    assert not item["available"] and item["unavailable_reason"] == "source_account_ambiguous"


def test_no_fake_accounts_token_stores_unsupported_providers_or_parent_aliases(factory, client):
    command = seed(factory)
    identities = add_sources(factory, command)
    with factory() as db:
        for key, (integration_id, _, _) in identities.items():
            for external in (providers._meta_token_account_external_id(integration_id),
                             providers._instagram_business_token_account_external_id(integration_id),
                             providers._instagram_business_login_token_account_external_id(integration_id)):
                db.add(IntegrationAccount(integration_id=integration_id, workspace_id=command.workspace_id, external_account_id=external))
        db.add(MetaPage(integration_id=identities["facebook_pages"][0], record_type="facebook_page", page_id="no-canonical-row", name="Unbound"))
        db.add(MetaAdAccount(integration_id=identities["meta_ads"][0], workspace_id=command.workspace_id,
                            account_id="no-canonical-row", account_name="Unbound"))
        db.add(IntegrationAccount(integration_id=identities["instagram_business"][0], workspace_id=command.workspace_id,
                                  external_account_id="__meta_page__:private-parent"))
        for provider in ("tiktok", "google_ads", "meta_business_suite"):
            integration = Integration(workspace_id=command.workspace_id, provider=provider, status="connected")
            db.add(integration)
            db.flush()
            db.add(IntegrationAccount(integration_id=integration.id, workspace_id=command.workspace_id,
                                      external_account_id="unsupported-provider-account"))
        db.commit()
    for builder in ("meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"):
        response = catalog(client, command, builder)
        assert response.status_code == 200
        for forbidden in ("token_", "__meta_token__", "private-parent", "no-canonical-row", "unsupported-provider-account"):
            assert forbidden not in response.text


def test_catalog_is_read_only_secret_free_and_available_for_free_workspace_inspection(factory, client, monkeypatch):
    command = seed(factory)
    add_sources(factory, command)
    with factory() as db:
        db.query(Subscription).update({"plan": "free"})
        db.commit()
    for helper in ("_run_meta_pages_sync", "_run_instagram_business_login_sync", "_sync_meta_instagram_account", "_run_meta_ads_sync", "_run_shopify_connection_sync"):
        monkeypatch.setattr(providers, helper, lambda **kwargs: pytest.fail("Catalog must not refresh providers"))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for builder in ("meta_pages", "instagram_business", "meta_ads", "shopify", "multi_source"):
            response = catalog(client, command, builder)
            assert response.status_code == 200
            assert not response.json()["availability"]["scheduling_enabled"]
            for forbidden in ("NEVER_SERIALIZE", "access_token", "refresh_token", "encrypted", "secret", "credential"):
                assert forbidden not in response.text
            assert all(set(item) == {"binding", "display_label", "connected", "available", "unavailable_reason"}
                       for item in response.json()["items"])
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}
    finally:
        event.remove(engine, "before_cursor_execute", capture)
