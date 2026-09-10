"""Explicit account adapters around the existing provider sync implementations."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import logging

from sqlalchemy.orm import Session

from .models import Dataset, Integration, IntegrationAccount, MetaAdAccount, MetaPage, ShopifyConnection, User
from .report_generation import GenerationError, SourceIdentity
from .report_generation_builders import validate_source_dataset_identity


_private_io = ContextVar("scheduled_private_io", default=False)


class ProviderLogFilter(logging.Filter):
    def filter(self, record):
        # Legacy provider exceptions can contain request URLs/credentials. The runner
        # emits allowlisted structured events instead of forwarding those diagnostics.
        return not _private_io.get() or record.name.startswith("app.scheduled_report")


@contextmanager
def private_provider_io():
    handlers = list(logging.getLogger().handlers)
    filter_ = ProviderLogFilter()
    for handler in handlers:
        handler.addFilter(filter_)
    token = _private_io.set(True)
    try:
        yield
    finally:
        _private_io.reset(token)
        for handler in handlers:
            handler.removeFilter(filter_)


def refresh_source(factory, *, workspace_id, actor_user_id, source: SourceIdentity, start_date, end_date) -> SourceIdentity:
    from . import main as providers
    from .scheduled_reports import _source_problem
    from dataclasses import asdict

    # Legacy sync persists incremental caches/uploads. A dedicated AUTOCOMMIT
    # connection prevents its read transactions spanning provider/S3 I/O. Report
    # persistence and all scheduling/quota transactions use ordinary Sessions.
    with factory() as probe:
        engine = probe.get_bind()
    with Session(engine.execution_options(isolation_level="AUTOCOMMIT"), expire_on_commit=False) as db:
        problem = _source_problem(db, [asdict(source)], workspace_id)
        if problem:
            raise GenerationError(problem, "The pinned source requires attention.", status_code=409)
        integration = db.get(Integration, source.integration_id)
        account = db.get(IntegrationAccount, source.integration_account_id) if source.integration_account_id else None
        user = db.get(User, actor_user_id) if actor_user_id else None
        if user is None:
            raise GenerationError("schedule_actor_unavailable", "Schedule actor is no longer available.", status_code=403)
        period = dict(timeframe="custom", start_date=start_date, end_date=end_date)
        with private_provider_io():
            if source.source_type == "facebook_pages":
                result = providers._run_meta_pages_sync(db=db, current_user=user, integration_id=integration.id,
                                                       page_id=providers._get_meta_page_id(account), **period)
            elif source.source_type == "instagram_business" and source.provider == "instagram_business_login":
                result = providers._run_instagram_business_login_sync(
                    db=db, current_user=user, route_name="scheduled_report_worker",
                    payload=providers.InstagramBusinessLoginSyncIn(workspace_id=workspace_id, integration_id=integration.id,
                        instagram_account_id=source.external_account_id, force_live=True, **period))
            elif source.source_type == "instagram_business":
                # Resolve only inside the pinned integration. Never invoke workspace
                # discovery/selected-account fallback when the cached identity is absent.
                records = db.query(MetaPage).filter(MetaPage.integration_id == integration.id,
                    MetaPage.record_type == providers.META_RECORD_TYPE_INSTAGRAM_ACCOUNT).all()
                matches = [record for record in records if providers._normalize_instagram_business_alias(record.page_id)
                           == providers._normalize_instagram_business_alias(source.external_account_id)]
                if len(matches) != 1:
                    raise GenerationError("source_account_missing", "Pinned Instagram account is unavailable.")
                record = matches[0]
                page = IntegrationAccount(integration_id=integration.id, workspace_id=workspace_id,
                    external_account_id=providers._meta_page_account_external_id(record.parent_page_id or record.page_id),
                    display_name=record.business_name or record.name)
                result = providers._sync_meta_instagram_account(db=db, integration=integration, selected_page=page,
                    selected_meta_record=record, timeframe_config=providers.resolve_meta_pages_timeframe(**period), current_user=user)
            elif source.source_type == "meta_ads":
                ad_id = providers._normalize_meta_ad_account_id(source.external_account_id)
                ad_account = db.query(MetaAdAccount).filter(MetaAdAccount.integration_id == integration.id,
                    MetaAdAccount.workspace_id == workspace_id, MetaAdAccount.account_id.in_([ad_id, "act_" + ad_id])).one_or_none()
                if ad_account is None:
                    raise GenerationError("source_account_missing", "Pinned ad account is unavailable.")
                result = providers._run_meta_ads_sync(db=db, integration=integration, account=ad_account, **period)
            elif source.source_type == "shopify":
                connection = db.query(ShopifyConnection).filter(ShopifyConnection.integration_id == integration.id,
                    ShopifyConnection.workspace_id == workspace_id, ShopifyConnection.shop_domain == source.external_account_id).one()
                result = providers._run_shopify_connection_sync(db=db, connection=connection, **period)
            else:
                raise GenerationError("configuration_not_supported", "Unsupported scheduled provider.")
        refreshed = replace(source, dataset_id=result.dataset_id)
        dataset = db.get(Dataset, result.dataset_id)
        if dataset is None or dataset.workspace_id != workspace_id:
            raise GenerationError("refresh_dataset_invalid", "Refresh returned an invalid dataset.")
        validate_source_dataset_identity(refreshed, dataset, account)
        if source.source_type == "shopify" and (dataset.data or {}).get("shop_domain") != source.external_account_id:
            raise GenerationError("refresh_identity_changed", "Refresh returned a different shop.")
        timeframe = (dataset.data or {}).get("timeframe", {})
        if str(timeframe.get("since")) != start_date or str(timeframe.get("until")) != end_date:
            raise GenerationError("refresh_period_mismatch", "Refresh returned an unexpected reporting period.")
        return refreshed
