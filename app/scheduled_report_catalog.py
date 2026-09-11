"""Read-only discovery of existing executable schedule bindings, never account creation."""
from collections import defaultdict

from .models import Integration, IntegrationAccount, MetaAdAccount, MetaPage, ShopifyConnection
from .report_generation import GenerationError
from .scheduled_reports import SourceInput, _sources, _source_problem, _validate_source_configuration


def source_catalog(db, workspace_id, builder):
    from . import main as providers

    accounts = db.query(IntegrationAccount, Integration).join(
        Integration, Integration.id == IntegrationAccount.integration_id
    ).filter(Integration.workspace_id == workspace_id, IntegrationAccount.workspace_id == workspace_id,
             Integration.provider.in_(["meta", "instagram_business", "instagram_business_login", "meta_ads"]))\
        .order_by(Integration.id, IntegrationAccount.id).all()
    pages = defaultdict(list)
    # MetaPage has no workspace column; ownership comes from its parent integration.
    for row in db.query(MetaPage.integration_id, MetaPage.record_type, MetaPage.page_id, MetaPage.name).join(
        Integration, Integration.id == MetaPage.integration_id
    ).filter(Integration.workspace_id == workspace_id):
        pages[row.integration_id].append(row)
    ads = defaultdict(list)
    for row in db.query(MetaAdAccount.integration_id, MetaAdAccount.account_id, MetaAdAccount.account_name).join(
        Integration, Integration.id == MetaAdAccount.integration_id
    ).filter(Integration.workspace_id == workspace_id, MetaAdAccount.workspace_id == workspace_id):
        ads[row.integration_id].append(row)

    items = []

    def append(integration, account, source_type, external_id, label, mapping_problem=None):
        position = 1 if builder == "multi_source" and source_type == "instagram_business" else 0
        binding = SourceInput(integration_id=integration.id, integration_account_id=account.id if account else None,
            external_account_id=external_id, provider=integration.provider, source_type=source_type,
            position=position, label=label or external_id)
        # Reuse the create/edit contract; do not maintain a separate compatibility matrix.
        candidate = binding.model_dump()
        configuration_sources = ([{"source_type": "facebook_pages"}, {"source_type": "instagram_business"}]
                                 if builder == "multi_source" and source_type in {"facebook_pages", "instagram_business"}
                                 else [candidate])
        try:
            _validate_source_configuration({"builder": builder}, configuration_sources)
            _sources(db, workspace_id, [binding.model_copy(update={"position": 0})])
        except GenerationError:
            return
        problem = _source_problem(db, [candidate], workspace_id) or mapping_problem
        items.append({"binding": candidate, "display_label": binding.label,
                      "connected": integration.status == "connected" and problem != "source_disconnected",
                      "available": problem is None, "unavailable_reason": problem})

    for account, integration in accounts:
        external_id = account.external_account_id
        if external_id in {
            providers._meta_token_account_external_id(integration.id),
            providers._instagram_business_token_account_external_id(integration.id),
            providers._instagram_business_login_token_account_external_id(integration.id),
        }:
            continue
        records = pages[integration.id]
        instagram = [record for record in records if record.record_type == providers.META_RECORD_TYPE_INSTAGRAM_ACCOUNT
                     and providers._normalize_instagram_business_alias(record.page_id)
                     == providers._normalize_instagram_business_alias(external_id)]
        if integration.provider == "meta_ads":
            ad_id = providers._normalize_meta_ad_account_id(external_id)
            matches = [record for record in ads[integration.id] if record.account_id in {ad_id, "act_" + ad_id}]
            problem = None if len(matches) == 1 else "source_account_missing" if not matches else "source_account_ambiguous"
            append(integration, account, "meta_ads", external_id,
                   account.display_name or (matches[0].account_name if len(matches) == 1 else None), problem)
        elif integration.provider == "meta" and external_id.startswith(providers.META_PAGE_ACCOUNT_PREFIX):
            page_id = providers._get_meta_page_id(account)
            matches = [record for record in records if record.page_id == page_id]
            # The legacy sync routes an Instagram-typed record into its Instagram builder.
            problem = "source_account_ambiguous" if any(record.record_type != providers.META_RECORD_TYPE_FACEBOOK_PAGE for record in matches) else None
            append(integration, account, "facebook_pages", external_id,
                   account.display_name or (matches[0].name if len(matches) == 1 else None), problem)
        elif instagram or integration.provider in {"instagram_business", "instagram_business_login"}:
            if external_id.startswith(providers.META_PAGE_ACCOUNT_PREFIX):
                continue  # Parent Facebook page rows are not canonical Instagram account IDs.
            problem = None if len(instagram) == 1 else "source_account_missing" if not instagram else "source_account_ambiguous"
            append(integration, account, "instagram_business", external_id,
                   account.display_name or (instagram[0].name if len(instagram) == 1 else None), problem)

    # Shopify's validator and refresh adapter pin the connection's shop domain;
    # no IntegrationAccount is required, and no token-store ID is substituted.
    shops = db.query(ShopifyConnection.shop_domain, ShopifyConnection.shop_name, Integration).join(
        Integration, Integration.id == ShopifyConnection.integration_id
    ).filter(Integration.workspace_id == workspace_id, ShopifyConnection.workspace_id == workspace_id,
             Integration.provider == "shopify").order_by(Integration.id).all()
    for domain, name, integration in shops:
        account = db.query(IntegrationAccount).filter_by(workspace_id=workspace_id, integration_id=integration.id,
                                                       external_account_id=domain).one_or_none()
        append(integration, account, "shopify", domain, name or (account.display_name if account else None))
    return sorted(items, key=lambda item: (item["binding"]["position"], item["display_label"].casefold(),
                                          item["binding"]["integration_id"], item["binding"]["external_account_id"]))
