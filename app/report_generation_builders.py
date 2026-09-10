from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .models import Dataset, DatasetFile, Integration, IntegrationAccount
from .report_generation import ReportDraft, GenerateReportCommand, GenerationError, SourceIdentity


def copy_generation_row(row: Any) -> Any:
    """Copy scalar columns only; builders cannot lazy-load relationships or reopen a transaction."""
    if row is None:
        return None
    return type(row)(**{column.key: deepcopy(getattr(row, column.key)) for column in row.__mapper__.column_attrs})


@dataclass(frozen=True)
class PreparedGeneration:
    datasets: dict[int, Dataset]
    dataset_files: dict[int, DatasetFile | None]
    integrations: dict[int, Integration]
    accounts: dict[int, IntegrationAccount]
    slide_limits: dict[str, Any]
    branding: dict[str, Any]
    multi_platform_allowed: bool


def prepare_report_inputs(db: Session, command: GenerateReportCommand) -> PreparedGeneration:
    from . import main as providers
    from .services import can_use_multi_platform_report, resolve_report_branding_for_workspace, resolve_report_slide_limits

    datasets = {source.dataset_id: copy_generation_row(db.get(Dataset, source.dataset_id)) for source in command.sources if source.dataset_id is not None}
    if any(dataset is None for dataset in datasets.values()):
        raise GenerationError("dataset_not_found", "Dataset no longer exists.", status_code=404)
    builder = command.configuration.builder
    files = {dataset_id: copy_generation_row(providers._get_latest_dataset_file(db, dataset_id)) for dataset_id in datasets} if builder != "multi_source" else {}
    integrations = {source.integration_id: copy_generation_row(db.get(Integration, source.integration_id)) for source in command.sources if source.integration_id is not None}
    accounts = {source.integration_account_id: copy_generation_row(db.get(IntegrationAccount, source.integration_account_id)) for source in command.sources if source.integration_account_id is not None}
    requested = 5 if builder in {"instagram_business", "shopify"} else command.configuration.requested_slides
    default = providers.DEFAULT_GENERATED_REPORT_SLIDE_COUNT if builder == "dataset" else 5 if builder in {"instagram_business", "shopify"} else 11
    limits = resolve_report_slide_limits(db, command.workspace_id, requested_slides=requested, default_slides=default) if builder != "multi_source" else {}
    return PreparedGeneration(datasets, files, integrations, accounts, limits,
                              deepcopy(resolve_report_branding_for_workspace(db, command.workspace_id)),
                              can_use_multi_platform_report(db, command.workspace_id))


def validate_source_dataset_identity(source: SourceIdentity, dataset: Dataset, account: IntegrationAccount | None) -> None:
    from . import main as providers

    row = dataset.data if isinstance(dataset.data, dict) else {}
    integration_id = row.get("integration_id")
    if source.integration_id is not None and integration_id is not None and str(integration_id) != str(source.integration_id):
        raise GenerationError("dataset_integration_mismatch", "Dataset belongs to a different integration.")
    if source.source_type == "meta_ads":
        aliases = providers._meta_ads_dataset_account_aliases(row)
        normalize = providers._normalize_meta_ad_account_id
    elif source.source_type in {"facebook_pages", "instagram_business"}:
        normalize = providers._normalize_instagram_business_alias if source.source_type == "instagram_business" else lambda value: str(value or "").strip()
        aliases = {normalize(value) for value in providers._single_source_external_account_candidates(source.source_type, row, None)}
    else:
        return
    if aliases:
        for identity in (source.external_account_id, account.external_account_id if account is not None else None):
            if identity is not None and normalize(identity) not in aliases:
                raise GenerationError("dataset_account_mismatch", "Dataset belongs to a different source account.")


@dataclass(frozen=True)
class BuilderParameters:
    """Compatibility input for the existing provider builders, independent of HTTP schemas."""

    dataset_id: int
    workspace_id: int
    sources: tuple[SourceIdentity, ...]
    title: str | None
    locale: str
    ai_mode: str
    requested_slides: int | None
    timeframe: str
    start_date: str | None
    end_date: str | None
    template: str | None
    integration_id: int | None
    integration_account_id: int | None
    account_id: str | None
    page_id: str | None
    ad_account_id: str | None
    slide_count: int | None = None

    @classmethod
    def from_command(cls, command: GenerateReportCommand) -> BuilderParameters:
        source = command.sources[0]
        return cls(
            dataset_id=int(source.dataset_id), workspace_id=command.workspace_id, sources=command.sources,
            title=command.options.title, locale=command.options.locale, ai_mode=command.options.ai_mode,
            requested_slides=command.configuration.requested_slides, template=command.configuration.template,
            timeframe=command.period.timeframe, start_date=command.period.start_date, end_date=command.period.end_date,
            integration_id=source.integration_id, integration_account_id=source.integration_account_id,
            account_id=source.external_account_id, page_id=source.external_account_id, ad_account_id=source.external_account_id,
        )


def build_report(command: GenerateReportCommand, prepared: PreparedGeneration) -> ReportDraft:
    # Existing builders still share helpers with main.py. Keep one implementation during extraction.
    from . import main as providers

    dataset = prepared.datasets[command.sources[0].dataset_id]
    parameters = BuilderParameters.from_command(command)
    builder = command.configuration.builder
    if builder == "multi_source":
        return providers._create_multi_source_report(payload=parameters, prepared=prepared)
    elif builder == "shopify":
        return providers._create_shopify_report(dataset=dataset, payload=parameters, prepared=prepared)
    elif builder == "dataset":
        return _build_dataset_report(command, dataset, parameters, providers, prepared)
    else:
        return providers._create_meta_dataset_report(
            dataset=dataset, payload=parameters, prepared=prepared,
            report_source={"meta_pages": "meta_pages_v2", "instagram_business": "instagram_business_v1", "meta_ads": "meta_ads"}[builder],
            generation_mode=builder,
        )


def _build_dataset_report(command: GenerateReportCommand, dataset: Dataset, payload: BuilderParameters, providers: Any, prepared: PreparedGeneration) -> ReportDraft:

    from .services import (
        extract_meta_pages_report_inputs, normalize_report_locale,
    )
    limits = prepared.slide_limits
    if payload.ai_mode == "agents" and not limits["capabilities"]["allow_ai_agents"]:
        raise GenerationError("plan_restricted", "AI agents are not available for current plan.", status_code=403)
    row = dataset.data
    if not isinstance(row, dict) or not row:
        dataset_file = prepared.dataset_files[dataset.id]
        if dataset_file is None:
            raise GenerationError("dataset_file_not_found", "Dataset file not found.", status_code=404)
        row = providers._load_dataset_row(dataset_file)
    locale = normalize_report_locale(payload.locale)
    branding = prepared.branding
    inputs = extract_meta_pages_report_inputs(row)
    timeframe = row.get("timeframe") if isinstance(row.get("timeframe"), dict) else providers.resolve_meta_pages_timeframe(
        payload.timeframe, start_date=payload.start_date, end_date=payload.end_date,
    )
    context = {
        **row, **inputs, "title": payload.title or dataset.name, "locale": locale, "branding": branding,
        "report_timeframe": timeframe, "plan": limits["plan"], "page_name": inputs.get("page_name") or dataset.name,
        "report_inputs": inputs, "requested_slides": limits["requested_slides"],
        "summary": providers.build_meta_pages_summary(inputs, locale),
        "recent_posts_summary": providers.build_meta_pages_recent_posts_summary(inputs, locale),
        "ai_summary": providers.generate_meta_pages_ai_summary(providers.build_meta_pages_ai_payload({"data": row}), locale),
    }
    specs = providers.build_blocks(limits["effective_slide_limit"], context)[:limits["effective_slide_limit"]]
    agent_result = None
    if payload.ai_mode == "agents":
        agent_plan = providers.build_ai_agent_plan_context(
            plan=limits["plan"], effective_slide_limit=limits["effective_slide_limit"],
            dataset_context={"dataset_id": dataset.id}, report_context={"generation_mode": "standard"},
        )
        agent_result = providers.run_ai_agents_pipeline(
            ai_mode=payload.ai_mode, plan_context=agent_plan, block_specs=specs,
            dataset_context={"dataset_id": dataset.id, "workspace_id": command.workspace_id,
                             "timeframe": timeframe, "report_inputs": inputs},
            report_context={"generation_mode": "standard", "ai_mode": payload.ai_mode,
                            "locale": locale, "title": context["title"], "branding": branding},
        )
        agent_specs = list(agent_result.get("blocks") or [])
        if len(agent_specs) == limits["effective_slide_limit"]:
            specs = agent_specs
        else:
            agent_result = {**agent_result, "used": False, "fallback_used": True,
                            "errors": list(agent_result.get("errors") or []) + ["AI agents returned an unexpected block count."]}
    ai_metadata = providers.build_ai_agent_metadata(
        ai_mode=payload.ai_mode, allow_ai_agents=limits["capabilities"]["allow_ai_agents"], pipeline_result=agent_result,
    )
    return ReportDraft(
        name=payload.title or dataset.name,
        metadata={
            "locale": locale, "branding": branding, "generation_mode": "standard", **ai_metadata,
            "requested_slides": limits["requested_slides"], "effective_slide_limit": limits["effective_slide_limit"],
            "plan_at_generation": limits["plan"], "plan_capabilities": limits["capabilities"],
            "timeframe": timeframe,
        },
        block_specs=tuple(specs), sources=command.sources,
    )
