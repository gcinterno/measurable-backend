"""Production execution bridge for immutable, published ReportSpec versions."""
from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from .canonical_metric_catalog import (
    AVAILABLE,
    NOT_COMPARABLE,
    SOURCE_ONLY,
    ResolvedMetric,
    ResolvedMetricSource,
    build_canonical_metric_catalog_for_sources,
    resolve_metrics,
)
from .models import ReportTemplate, ReportTemplateVersion
from .report_generation import GenerateReportCommand, GenerationError, ReportDraft
from .report_spec import (
    REPORTSPEC_DATASOURCE_PRODUCTION_MODES,
    ReportSpec,
    assert_valid_report_spec,
    collect_report_spec_bindings,
)


def resolve_published_report_template(
    db: Session,
    command: GenerateReportCommand,
) -> GenerateReportCommand:
    """Resolve request IDs to one immutable published spec before quota reservation."""
    configuration = command.configuration
    template_id = configuration.report_template_id
    version_id = configuration.report_template_version_id
    if template_id is None and version_id is None:
        return command
    if template_id is None:
        raise GenerationError(
            "report_template_id_required",
            "report_template_id is required when report_template_version_id is supplied.",
        )

    template = db.get(ReportTemplate, template_id)
    if template is None:
        raise GenerationError("report_template_not_found", "Report template not found.", status_code=404)
    if template.workspace_id not in {None, command.workspace_id}:
        raise GenerationError(
            "report_template_forbidden",
            "Report template is not available to this workspace.",
            status_code=403,
        )
    if template.archived_at is not None or template.status == "archived":
        raise GenerationError(
            "report_template_archived",
            "Archived report templates cannot be used for generation.",
            status_code=409,
        )

    resolved_version_id = version_id if version_id is not None else template.published_version_id
    if resolved_version_id is None:
        raise GenerationError(
            "report_template_not_published",
            "Report template does not have a published version.",
            status_code=409,
        )
    version = db.get(ReportTemplateVersion, resolved_version_id)
    if version is None:
        raise GenerationError("report_template_version_not_found", "Report template version not found.", status_code=404)
    if version.report_template_id != template.id:
        raise GenerationError(
            "report_template_version_mismatch",
            "Report template version does not belong to the selected template.",
            status_code=409,
        )
    if version.published_at is None:
        raise GenerationError(
            "report_template_version_unpublished",
            "Only published report template versions can be used for generation.",
            status_code=409,
        )

    try:
        spec = assert_valid_report_spec(version.spec_json or {})
    except Exception as exc:
        raise GenerationError(
            "published_report_spec_invalid",
            "Published report template version contains an invalid ReportSpec.",
            status_code=409,
        ) from exc
    return replace(
        command,
        configuration=replace(
            configuration,
            report_template_id=template.id,
            report_template_version_id=version.id,
            report_spec=spec.as_dict(),
        ),
    )


def _requirement(requirements: Mapping[str, Any], snake_name: str, camel_name: str) -> Any:
    if snake_name in requirements:
        return requirements[snake_name]
    if camel_name in requirements:
        return requirements[camel_name]
    raw = requirements.get("raw")
    return raw.get(snake_name) if isinstance(raw, Mapping) else None


def _semantic_is_available(resolution: ResolvedMetric) -> bool:
    return any(source.support_status == AVAILABLE and source.value is not None for source in resolution.sources)


def validate_report_spec_datasources(
    command: GenerateReportCommand,
    spec: ReportSpec,
    catalog: Iterable[Any],
    resolutions: Mapping[str, ResolvedMetric],
) -> None:
    requirements = spec.datasource_requirements
    source_types = [str(source.source_type or "").strip() for source in command.sources]
    source_type_set = set(source_types)

    supported_modes = _requirement(requirements, "supported_modes", "supportedModes")
    if supported_modes is not None and command.configuration.builder not in set(supported_modes):
        raise GenerationError(
            "report_template_mode_incompatible",
            "Selected report template does not support this report generation mode.",
            status_code=409,
            details={"supported_modes": list(supported_modes), "actual_mode": command.configuration.builder},
        )

    required_count = _requirement(requirements, "required_source_count", "requiredSourceCount")
    if required_count is not None and len(command.sources) != int(required_count):
        raise GenerationError(
            "report_template_source_count_incompatible",
            "Selected report template requires a different number of report sources.",
            status_code=409,
            details={"required_source_count": int(required_count), "actual_source_count": len(command.sources)},
        )
    minimum_count = requirements.get("minimum_source_count")
    if minimum_count is not None and len(command.sources) < int(minimum_count):
        raise GenerationError(
            "report_template_source_count_incompatible",
            "Selected report template requires more report sources.",
            status_code=409,
            details={"minimum_source_count": int(minimum_count), "actual_source_count": len(command.sources)},
        )

    required_sources = requirements.get("sources")
    mode = str(requirements.get("mode") or "all").strip()
    if isinstance(required_sources, list) and required_sources:
        required_source_set = {str(value).strip() for value in required_sources}
        compatible = (
            required_source_set.issubset(source_type_set)
            if mode == "all"
            else bool(required_source_set & source_type_set)
            if mode == "any"
            else source_type_set == required_source_set
            if mode == "exactly"
            else len(source_types) == 1 and source_types[0] in required_source_set
        )
        if not compatible:
            raise GenerationError(
                "report_template_sources_incompatible",
                "Selected report sources do not satisfy the report template requirements.",
                status_code=409,
                details={"required_sources": sorted(required_source_set), "actual_sources": source_types, "mode": mode},
            )

    catalog_tuple = tuple(catalog)
    if requirements.get("catalog_required") is True and not catalog_tuple:
        raise GenerationError(
            "report_template_catalog_unavailable",
            "Selected report sources do not provide a canonical metric catalog.",
            status_code=409,
        )
    required_semantics = _requirement(
        requirements, "required_canonical_semantics", "requiredCanonicalSemantics"
    ) or []
    missing = [
        semantic
        for semantic in required_semantics
        if semantic not in resolutions or not _semantic_is_available(resolutions[semantic])
    ]
    if missing:
        raise GenerationError(
            "report_template_required_semantic_unavailable",
            "Required canonical report data is unavailable for the selected sources.",
            status_code=409,
            details={"missing_canonical_semantics": missing},
        )


def _support_status(resolution: ResolvedMetric) -> str:
    statuses = [source.support_status for source in resolution.sources]
    if AVAILABLE in statuses:
        return AVAILABLE
    for status in ("unsupported", "empty", "missing", "not_requested"):
        if status in statuses:
            return status
    return "missing"


def _primary_source(resolution: ResolvedMetric) -> ResolvedMetricSource | None:
    return next(
        (
            source
            for source in resolution.sources
            if source.support_status == AVAILABLE and source.value is not None
        ),
        None,
    )


def _primary_value(resolution: ResolvedMetric) -> int | float | None:
    if resolution.aggregation_method in {NOT_COMPARABLE, SOURCE_ONLY}:
        source = _primary_source(resolution)
        return source.value if source is not None else None
    return resolution.combined_value


def _source_contributions(resolution: ResolvedMetric) -> list[dict[str, Any]]:
    return [
        {
            "key": source.source_type,
            "label": source.source_label,
            "source_type": source.source_type,
            "source_metric": source.source_metric,
            "canonical_metric": resolution.canonical_metric,
            "value": source.value,
            "support_status": source.support_status,
            "timeseries": [dict(point) for point in source.timeseries],
            "aggregation": resolution.aggregation_method,
            "aggregation_method": resolution.aggregation_method,
            "dataset_id": source.dataset_id,
            "metadata": dict(source.metadata),
        }
        for source in resolution.sources
    ]


def _timeseries(resolution: ResolvedMetric) -> list[dict[str, Any]]:
    if resolution.aggregation_method in {NOT_COMPARABLE, SOURCE_ONLY}:
        source = _primary_source(resolution)
        return [dict(point) for point in source.timeseries] if source is not None else []
    totals: dict[str, dict[str, Any]] = {}
    for source in resolution.sources:
        if source.support_status != AVAILABLE:
            continue
        for point in source.timeseries:
            key = str(point.get("date") or point.get("label") or "").strip()
            value = point.get("value")
            if not key or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            row = totals.setdefault(key, {"date": key, "label": point.get("label") or key, "value": 0})
            row["value"] += value
    return [totals[key] for key in sorted(totals)]


def _canonical_block_payload(
    semantic: str,
    resolution: ResolvedMetric,
    *,
    timeframe: Mapping[str, Any],
) -> dict[str, Any]:
    value = _primary_value(resolution)
    status = _support_status(resolution)
    contributions = _source_contributions(resolution)
    points = _timeseries(resolution)
    series = [
        {
            "label": source.source_label,
            "source_type": source.source_type,
            "source_metric": source.source_metric,
            "support_status": source.support_status,
            "points": [dict(point) for point in source.timeseries],
        }
        for source in resolution.sources
    ]
    items: list[Any] = []
    if semantic == "top_content":
        for source in resolution.sources:
            top_content = source.metadata.get("top_content")
            if isinstance(top_content, list):
                items.extend(top_content)
    canonical_resolution = {
        "semantic": semantic,
        "metric_family": resolution.metric_family,
        "value": value,
        "combined_value": resolution.combined_value,
        "aggregation": resolution.aggregation_method,
        "aggregation_method": resolution.aggregation_method,
        "comparability_group": resolution.comparability_group,
        "status": status,
        "support_status": status,
        "sources": contributions,
    }
    return {
        "semantic_name": semantic,
        "canonical_semantic": semantic,
        "canonical_metric": semantic,
        "metric_family": resolution.metric_family,
        "support_status": status,
        "availability_status": status,
        "is_available": value is not None and status == AVAILABLE,
        "value": value,
        "current_value": value,
        "primary_value": value,
        "metric_value": value,
        "total": value,
        "combined_value": resolution.combined_value,
        "aggregation": resolution.aggregation_method,
        "aggregation_method": resolution.aggregation_method,
        "comparability_group": resolution.comparability_group,
        "source_contributions": contributions,
        "canonical_metric_resolution": canonical_resolution,
        "points": points,
        "series": series,
        "items": items,
        "chart": {
            "metric": semantic,
            "points": points,
            "data": points,
            "series": series,
            "timeframe": dict(timeframe),
            "is_available": bool(points),
            "primary_value": value,
        },
        "timeframe": dict(timeframe),
        "report_spec_runtime": True,
    }


def build_report_spec_report(
    command: GenerateReportCommand,
    prepared: Any,
    providers: Any,
) -> ReportDraft:
    spec = assert_valid_report_spec(command.configuration.report_spec or {})
    normalized_sources: list[dict[str, Any]] = []
    for source in command.sources:
        dataset = prepared.datasets.get(source.dataset_id)
        if dataset is None:
            raise GenerationError("dataset_not_found", "Dataset no longer exists.", status_code=404)
        normalized_sources.append(
            providers._multi_source_normalize_source(
                {
                    "dataset_id": source.dataset_id,
                    "source_type": source.source_type,
                    "provider": source.provider,
                    "label": source.label,
                    "config_json": dict(source.config_json or {}),
                },
                dataset=dataset,
                locale=command.options.locale,
            )
        )
    if len(command.sources) >= 2 and not prepared.multi_platform_allowed:
        raise GenerationError(
            "plan_restricted",
            "Current plan does not allow multi-platform reports.",
            status_code=403,
        )

    catalog = build_canonical_metric_catalog_for_sources(normalized_sources)
    required = _requirement(
        spec.datasource_requirements,
        "required_canonical_semantics",
        "requiredCanonicalSemantics",
    ) or []
    optional = _requirement(
        spec.datasource_requirements,
        "optional_canonical_semantics",
        "optionalCanonicalSemantics",
    ) or []
    semantics = list(
        dict.fromkeys(
            [
                binding.canonical_semantic
                for binding in collect_report_spec_bindings(spec)
                if binding.canonical_semantic
            ]
            + list(required)
            + list(optional)
        )
    )
    resolutions = resolve_metrics(semantics, catalog)
    validate_report_spec_datasources(command, spec, catalog, resolutions)
    timeframe = {
        "key": command.period.timeframe,
        "since": command.period.start_date,
        "until": command.period.end_date,
    }
    block_specs = [
        {
            "order": index,
            "type": "canonical_metric",
            "data_json": json.dumps(
                _canonical_block_payload(semantic, resolutions[semantic], timeframe=timeframe),
                separators=(",", ":"),
            ),
            "editable_fields_json": "[]",
        }
        for index, semantic in enumerate(semantics, start=1)
    ]
    if not block_specs:
        block_specs.append(
            {
                "order": 1,
                "type": "report_spec_context",
                "data_json": json.dumps(
                    {
                        "semantic_name": "report_context",
                        "timeframe": timeframe,
                        "source_count": len(normalized_sources),
                        "report_spec_runtime": True,
                    },
                    separators=(",", ":"),
                ),
                "editable_fields_json": "[]",
            }
        )
    return ReportDraft(
        name=command.options.title or spec.name,
        metadata={
            "locale": command.options.locale,
            "branding": prepared.branding,
            "generation_mode": "report_spec",
            "report_type": spec.report_type,
            "report_spec_schema_version": spec.schema_version,
            "timeframe": timeframe,
        },
        block_specs=tuple(block_specs),
        sources=command.sources,
    )


__all__ = [
    "REPORTSPEC_DATASOURCE_PRODUCTION_MODES",
    "build_report_spec_report",
    "resolve_published_report_template",
    "validate_report_spec_datasources",
]
