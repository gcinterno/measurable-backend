from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from .canonical_metric_catalog import CANONICAL_AGGREGATION_RULES


REPORTSPEC_SCHEMA_VERSION = "1.0"
SUPPORTED_REPORTSPEC_SCHEMA_VERSIONS: tuple[str, ...] = (REPORTSPEC_SCHEMA_VERSION,)

GenerationMode = Literal["template", "ai", "legacy_migration"]

REPORTSPEC_GENERATION_MODES: tuple[str, ...] = (
    "template",
    "ai",
    "legacy_migration",
)

REPORTSPEC_BLOCK_TYPES: tuple[str, ...] = (
    "metric_hero",
    "timeseries_chart",
    "source_contribution",
    "source_split",
    "platform_metric",
    "insight",
    "executive_insight",
    "content_performance",
    "content_ranking",
    "recommendation",
    "text",
    "cover_branding",
)

REPORTSPEC_SLIDE_TYPES: tuple[str, ...] = (
    "cover",
    "metric",
    "content",
    "executive_summary",
    "recommendations",
    "text",
)

REPORTSPEC_LAYOUTS: tuple[str, ...] = (
    "cover",
    "metric_focus",
    "metric_with_timeseries",
    "source_split",
    "content_ranking",
    "executive_summary",
    "recommendations",
    "text",
)

REPORTSPEC_VISIBILITY_RULE_TYPES: tuple[str, ...] = (
    "available",
    "unsupported",
    "missing",
    "empty",
    "not_requested",
    "source_count",
    "minimum_contributions",
)

REPORTSPEC_VISIBILITY_OPERATORS: tuple[str, ...] = (
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
)

REPORTSPEC_DATASOURCE_MODES: tuple[str, ...] = (
    "all",
    "any",
    "exactly",
    "single",
)

DATA_BOUND_BLOCK_TYPES: tuple[str, ...] = (
    "metric_hero",
    "timeseries_chart",
    "source_contribution",
    "source_split",
    "platform_metric",
    "insight",
    "executive_insight",
    "content_performance",
    "content_ranking",
    "recommendation",
)

BLOCK_LAYOUT_COMPATIBILITY: dict[str, tuple[str, ...]] = {
    "cover_branding": ("cover",),
    "content_ranking": ("content_ranking",),
    "content_performance": ("content_ranking", "source_split"),
    "executive_insight": ("executive_summary",),
    "recommendation": ("recommendations",),
}

RAW_PROVIDER_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "page_post_engagements",
        "page_posts_impressions_organic",
        "page_views_total",
        "page_actions_post_reactions_total",
        "followers_count",
        "follower_count",
        "profile_views",
        "website_clicks",
        "total_interactions",
        "accounts_engaged",
        "organic_impressions_total",
        "daily_organic_impressions",
        "profile_views_daily",
        "views_daily",
        "reach_daily",
        "instagram_business_login",
        "meta_pages",
        "meta_ads",
    }
)


@dataclass(frozen=True)
class DataBinding:
    canonical_semantic: str
    metric_path: str | None = None
    source_constraint: dict[str, Any] | None = None
    timeseries_selector: str | None = None
    aggregation_selector: str | None = None
    ranking_selector: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_semantic": self.canonical_semantic,
            "metric_path": self.metric_path,
            "source_constraint": dict(self.source_constraint) if self.source_constraint is not None else None,
            "timeseries_selector": self.timeseries_selector,
            "aggregation_selector": self.aggregation_selector,
            "ranking_selector": self.ranking_selector,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DataBinding:
        source_constraint = payload.get("source_constraint")
        metadata = payload.get("metadata")
        return cls(
            canonical_semantic=str(payload.get("canonical_semantic") or "").strip(),
            metric_path=_optional_str(payload.get("metric_path")),
            source_constraint=dict(source_constraint) if isinstance(source_constraint, Mapping) else None,
            timeseries_selector=_optional_str(payload.get("timeseries_selector")),
            aggregation_selector=_optional_str(payload.get("aggregation_selector")),
            ranking_selector=_optional_str(payload.get("ranking_selector")),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class VisibilityRule:
    rule_type: str
    canonical_semantic: str | None = None
    operator: str | None = None
    value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "canonical_semantic": self.canonical_semantic,
            "operator": self.operator,
            "value": self.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VisibilityRule:
        metadata = payload.get("metadata")
        return cls(
            rule_type=str(payload.get("rule_type") or "").strip(),
            canonical_semantic=_optional_str(payload.get("canonical_semantic")),
            operator=_optional_str(payload.get("operator")),
            value=payload.get("value"),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class BlockSpec:
    id: str
    type: str
    bindings: tuple[DataBinding, ...] = ()
    presentation: dict[str, Any] = field(default_factory=dict)
    visibility_rules: tuple[VisibilityRule, ...] = ()
    ai_config: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "bindings": [binding.as_dict() for binding in self.bindings],
            "presentation": dict(self.presentation),
            "visibility_rules": [rule.as_dict() for rule in self.visibility_rules],
            "ai_config": dict(self.ai_config) if isinstance(self.ai_config, Mapping) else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BlockSpec:
        bindings = payload.get("bindings")
        legacy_binding = payload.get("binding")
        parsed_bindings: list[DataBinding] = []
        if isinstance(bindings, list):
            parsed_bindings.extend(
                DataBinding.from_dict(item)
                for item in bindings
                if isinstance(item, Mapping)
            )
        elif isinstance(legacy_binding, Mapping):
            parsed_bindings.append(DataBinding.from_dict(legacy_binding))

        visibility_rules = payload.get("visibility_rules")
        presentation = payload.get("presentation")
        ai_config = payload.get("ai_config")
        metadata = payload.get("metadata")
        return cls(
            id=str(payload.get("id") or "").strip(),
            type=str(payload.get("type") or "").strip(),
            bindings=tuple(parsed_bindings),
            presentation=dict(presentation) if isinstance(presentation, Mapping) else {},
            visibility_rules=tuple(
                VisibilityRule.from_dict(item)
                for item in visibility_rules
                if isinstance(item, Mapping)
            )
            if isinstance(visibility_rules, list)
            else (),
            ai_config=dict(ai_config) if isinstance(ai_config, Mapping) else None,
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class SlideSpec:
    id: str
    order: int
    slide_type: str
    title: str
    subtitle: str | None = None
    eyebrow: str | None = None
    layout: str = "metric_focus"
    blocks: tuple[BlockSpec, ...] = ()
    visibility_rules: tuple[VisibilityRule, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "slide_type": self.slide_type,
            "title": self.title,
            "subtitle": self.subtitle,
            "eyebrow": self.eyebrow,
            "layout": self.layout,
            "blocks": [block.as_dict() for block in self.blocks],
            "visibility_rules": [rule.as_dict() for rule in self.visibility_rules],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SlideSpec:
        blocks = payload.get("blocks")
        visibility_rules = payload.get("visibility_rules")
        metadata = payload.get("metadata")
        return cls(
            id=str(payload.get("id") or "").strip(),
            order=_int_or_zero(payload.get("order")),
            slide_type=str(payload.get("slide_type") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            subtitle=_optional_str(payload.get("subtitle")),
            eyebrow=_optional_str(payload.get("eyebrow")),
            layout=str(payload.get("layout") or "").strip(),
            blocks=tuple(
                BlockSpec.from_dict(item)
                for item in blocks
                if isinstance(item, Mapping)
            )
            if isinstance(blocks, list)
            else (),
            visibility_rules=tuple(
                VisibilityRule.from_dict(item)
                for item in visibility_rules
                if isinstance(item, Mapping)
            )
            if isinstance(visibility_rules, list)
            else (),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class ReportSpec:
    schema_version: str
    id: str
    name: str
    report_type: str
    template_id: str | None
    generation_mode: GenerationMode
    datasource_requirements: dict[str, Any] = field(default_factory=dict)
    reporting_period: dict[str, Any] = field(default_factory=dict)
    theme_ref: dict[str, Any] = field(default_factory=dict)
    slides: tuple[SlideSpec, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "report_type": self.report_type,
            "template_id": self.template_id,
            "generation_mode": self.generation_mode,
            "datasource_requirements": dict(self.datasource_requirements),
            "reporting_period": dict(self.reporting_period),
            "theme_ref": dict(self.theme_ref),
            "slides": [slide.as_dict() for slide in self.slides],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReportSpec:
        slides = payload.get("slides")
        datasource_requirements = payload.get("datasource_requirements")
        reporting_period = payload.get("reporting_period")
        theme_ref = payload.get("theme_ref")
        metadata = payload.get("metadata")
        return cls(
            schema_version=str(payload.get("schema_version") or "").strip(),
            id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            report_type=str(payload.get("report_type") or "").strip(),
            template_id=_optional_str(payload.get("template_id")),
            generation_mode=str(payload.get("generation_mode") or "").strip(),  # type: ignore[arg-type]
            datasource_requirements=(
                dict(datasource_requirements) if isinstance(datasource_requirements, Mapping) else {}
            ),
            reporting_period=dict(reporting_period) if isinstance(reporting_period, Mapping) else {},
            theme_ref=dict(theme_ref) if isinstance(theme_ref, Mapping) else {},
            slides=tuple(
                SlideSpec.from_dict(item)
                for item in slides
                if isinstance(item, Mapping)
            )
            if isinstance(slides, list)
            else (),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class ReportSpecValidationError:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class ReportSpecValidationResult:
    valid: bool
    errors: tuple[ReportSpecValidationError, ...]


class InvalidReportSpecError(ValueError):
    def __init__(self, result: ReportSpecValidationResult):
        self.result = result
        message = "; ".join(error.message for error in result.errors) or "Invalid ReportSpec"
        super().__init__(message)


def report_spec_to_json(spec: ReportSpec) -> str:
    return json.dumps(spec.as_dict(), sort_keys=True)


def report_spec_from_json(payload: str) -> ReportSpec:
    decoded = json.loads(payload)
    if not isinstance(decoded, Mapping):
        raise ValueError("ReportSpec JSON must decode to an object.")
    return ReportSpec.from_dict(decoded)


def collect_report_spec_bindings(spec: ReportSpec) -> tuple[DataBinding, ...]:
    bindings: list[DataBinding] = []
    for slide in spec.slides:
        for block in slide.blocks:
            bindings.extend(block.bindings)
    return tuple(bindings)


def validate_report_spec(spec: ReportSpec | Mapping[str, Any]) -> ReportSpecValidationResult:
    errors: list[ReportSpecValidationError] = []
    if isinstance(spec, Mapping):
        try:
            spec = ReportSpec.from_dict(spec)
        except Exception as exc:
            return ReportSpecValidationResult(
                valid=False,
                errors=(
                    ReportSpecValidationError(
                        code="MALFORMED_REPORT_SPEC",
                        message=f"ReportSpec is malformed: {exc}",
                    ),
                ),
            )
    elif not isinstance(spec, ReportSpec):
        return ReportSpecValidationResult(
            valid=False,
            errors=(
                ReportSpecValidationError(
                    code="MALFORMED_REPORT_SPEC",
                    message="ReportSpec must be a ReportSpec object or mapping.",
                ),
            ),
        )

    if spec.schema_version not in SUPPORTED_REPORTSPEC_SCHEMA_VERSIONS:
        errors.append(
            ReportSpecValidationError(
                code="UNSUPPORTED_SCHEMA_VERSION",
                message=f"Unsupported ReportSpec schema_version {spec.schema_version!r}.",
                path="schema_version",
            )
        )
    if not spec.id:
        errors.append(ReportSpecValidationError(code="MISSING_ID", message="ReportSpec id is required.", path="id"))
    if not spec.name:
        errors.append(
            ReportSpecValidationError(code="MISSING_NAME", message="ReportSpec name is required.", path="name")
        )
    if not spec.report_type:
        errors.append(
            ReportSpecValidationError(
                code="MISSING_REPORT_TYPE",
                message="ReportSpec report_type is required.",
                path="report_type",
            )
        )
    if spec.generation_mode not in REPORTSPEC_GENERATION_MODES:
        errors.append(
            ReportSpecValidationError(
                code="INVALID_GENERATION_MODE",
                message=f"Invalid ReportSpec generation_mode {spec.generation_mode!r}.",
                path="generation_mode",
            )
        )
    _validate_datasource_requirements(spec.datasource_requirements, errors)
    if not spec.slides:
        errors.append(
            ReportSpecValidationError(
                code="MISSING_SLIDES",
                message="ReportSpec must contain at least one slide.",
                path="slides",
            )
        )

    _validate_slide_ids_and_order(spec, errors)
    _validate_slides(spec, errors)
    return ReportSpecValidationResult(valid=not errors, errors=tuple(errors))


def assert_valid_report_spec(spec: ReportSpec | Mapping[str, Any]) -> ReportSpec:
    result = validate_report_spec(spec)
    if not result.valid:
        raise InvalidReportSpecError(result)
    return ReportSpec.from_dict(spec) if isinstance(spec, Mapping) else spec


def validate_report_spec_datasource_requirements(
    datasource_requirements: Any,
) -> ReportSpecValidationResult:
    errors: list[ReportSpecValidationError] = []
    _validate_datasource_requirements(datasource_requirements, errors)
    return ReportSpecValidationResult(valid=not errors, errors=tuple(errors))


def facebook_instagram_10_reference_reportspec() -> ReportSpec:
    required_semantics = (
        "reach",
        "visibility",
        "engagement",
        "page_profile_activity",
        "audience_size",
        "content_activity",
        "top_content",
    )
    return ReportSpec(
        schema_version=REPORTSPEC_SCHEMA_VERSION,
        id="facebook_instagram_10_reference_v1",
        name="Facebook + Instagram 10 Reference",
        report_type="multi_source_social",
        template_id="facebook_instagram_10",
        generation_mode="legacy_migration",
        datasource_requirements={
            "mode": "all",
            "sources": ["facebook_pages", "instagram_business"],
            "minimum_source_count": 2,
            "required_canonical_semantics": list(required_semantics),
            "catalog_required": True,
        },
        reporting_period={"selector": "request.timeframe"},
        theme_ref={"id": "default_report_theme"},
        slides=(
            SlideSpec(
                id="cover",
                order=1,
                slide_type="cover",
                title="Facebook + Instagram",
                subtitle="Multi-source performance report",
                layout="cover",
                blocks=(
                    BlockSpec(
                        id="cover-branding",
                        type="cover_branding",
                        presentation={"fields": ["name", "period", "brand"]},
                    ),
                ),
            ),
            _metric_slide(
                slide_id="reach",
                order=2,
                title="Reach",
                semantic="reach",
                layout="metric_with_timeseries",
                aggregation_selector="catalog_default",
            ),
            _metric_slide(
                slide_id="visibility",
                order=3,
                title="Visibility",
                semantic="visibility",
                layout="source_split",
                aggregation_selector="primary_available_source",
            ),
            _metric_slide(
                slide_id="engagement",
                order=4,
                title="Engagement",
                semantic="engagement",
                layout="metric_with_timeseries",
                aggregation_selector="catalog_default",
            ),
            _metric_slide(
                slide_id="page-profile-activity",
                order=5,
                title="Page / Profile Activity",
                semantic="page_profile_activity",
                layout="metric_with_timeseries",
                aggregation_selector="catalog_default",
            ),
            _metric_slide(
                slide_id="audience",
                order=6,
                title="Audience",
                semantic="audience_size",
                layout="source_split",
                aggregation_selector="catalog_default",
            ),
            _metric_slide(
                slide_id="content-activity",
                order=7,
                title="Content Activity",
                semantic="content_activity",
                layout="source_split",
                aggregation_selector="catalog_default",
            ),
            SlideSpec(
                id="top-performing-content",
                order=8,
                slide_type="content",
                title="Top Performing Content",
                layout="content_ranking",
                blocks=(
                    BlockSpec(
                        id="top-content-ranking",
                        type="content_ranking",
                        bindings=(
                            DataBinding(
                                canonical_semantic="top_content",
                                ranking_selector="catalog_ranked_content",
                            ),
                        ),
                        presentation={
                            "fields": ["source", "text", "date", "reach", "engagement", "ranking_score"],
                            "limit": 5,
                        },
                        visibility_rules=(
                            VisibilityRule(
                                rule_type="minimum_contributions",
                                canonical_semantic="top_content",
                                operator="gte",
                                value=1,
                            ),
                        ),
                    ),
                ),
            ),
            SlideSpec(
                id="executive-summary",
                order=9,
                slide_type="executive_summary",
                title="Executive Summary",
                layout="executive_summary",
                blocks=(
                    BlockSpec(
                        id="executive-insight",
                        type="executive_insight",
                        bindings=tuple(DataBinding(canonical_semantic=semantic) for semantic in required_semantics),
                        presentation={"summary_scope": "multi_source"},
                    ),
                ),
            ),
            SlideSpec(
                id="next-actions",
                order=10,
                slide_type="recommendations",
                title="Next Actions",
                layout="recommendations",
                blocks=(
                    BlockSpec(
                        id="recommendations",
                        type="recommendation",
                        bindings=(
                            DataBinding(canonical_semantic="visibility"),
                            DataBinding(canonical_semantic="engagement"),
                            DataBinding(canonical_semantic="top_content"),
                        ),
                        presentation={"item_count": 3},
                    ),
                ),
            ),
        ),
        metadata={
            "represented_recipe_id": "facebook_instagram_10",
            "production_wiring": "not_enabled_in_d4a",
        },
    )


def _metric_slide(
    *,
    slide_id: str,
    order: int,
    title: str,
    semantic: str,
    layout: str,
    aggregation_selector: str,
) -> SlideSpec:
    return SlideSpec(
        id=slide_id,
        order=order,
        slide_type="metric",
        title=title,
        layout=layout,
        blocks=(
            BlockSpec(
                id=f"{slide_id}-metric",
                type="metric_hero",
                bindings=(
                    DataBinding(
                        canonical_semantic=semantic,
                        metric_path="primary_value",
                        aggregation_selector=aggregation_selector,
                    ),
                ),
                presentation={"format": "number"},
                visibility_rules=(VisibilityRule(rule_type="available", canonical_semantic=semantic),),
            ),
            BlockSpec(
                id=f"{slide_id}-chart",
                type="timeseries_chart",
                bindings=(
                    DataBinding(
                        canonical_semantic=semantic,
                        metric_path="timeseries",
                        timeseries_selector="catalog_default",
                        aggregation_selector=aggregation_selector,
                    ),
                ),
                presentation={"chart_type": "line"},
            ),
            BlockSpec(
                id=f"{slide_id}-sources",
                type="source_contribution",
                bindings=(
                    DataBinding(
                        canonical_semantic=semantic,
                        metric_path="sources",
                        aggregation_selector=aggregation_selector,
                    ),
                ),
                presentation={"show_support_status": True, "show_provenance": True},
                visibility_rules=(
                    VisibilityRule(
                        rule_type="minimum_contributions",
                        canonical_semantic=semantic,
                        operator="gte",
                        value=1,
                    ),
                ),
            ),
        ),
    )


FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC = facebook_instagram_10_reference_reportspec()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_known_canonical_semantic(value: str | None) -> bool:
    return bool(value) and str(value) in CANONICAL_AGGREGATION_RULES


def _contains_raw_provider_reference(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_raw_provider_reference(key) or _contains_raw_provider_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_raw_provider_reference(item) for item in value)
    if isinstance(value, str):
        tokens = re.findall(r"[a-z0-9_]+", value.strip().lower())
        return any(token in RAW_PROVIDER_FIELD_NAMES for token in tokens)
    return False


def _validate_slide_ids_and_order(
    spec: ReportSpec,
    errors: list[ReportSpecValidationError],
) -> None:
    slide_ids = [slide.id for slide in spec.slides]
    duplicate_slide_ids = sorted({slide_id for slide_id in slide_ids if slide_ids.count(slide_id) > 1})
    if duplicate_slide_ids:
        errors.append(
            ReportSpecValidationError(
                code="DUPLICATE_SLIDE_ID",
                message=f"Duplicate slide IDs: {', '.join(duplicate_slide_ids)}.",
                path="slides",
            )
        )

    orders = [slide.order for slide in spec.slides]
    if any(not _is_positive_int(order) for order in orders):
        errors.append(
            ReportSpecValidationError(
                code="INVALID_SLIDE_ORDER",
                message="Slide orders must be positive integers.",
                path="slides",
            )
        )
        return
    expected_orders = list(range(1, len(spec.slides) + 1))
    if sorted(orders) != expected_orders:
        errors.append(
            ReportSpecValidationError(
                code="INVALID_SLIDE_ORDER",
                message=f"Slide orders must be contiguous from 1: expected {expected_orders}, got {orders}.",
                path="slides",
            )
        )


def _validate_slides(
    spec: ReportSpec,
    errors: list[ReportSpecValidationError],
) -> None:
    seen_block_ids: set[str] = set()
    for slide_index, slide in enumerate(spec.slides):
        slide_path = f"slides[{slide_index}]"
        if not slide.id:
            errors.append(
                ReportSpecValidationError(code="MISSING_SLIDE_ID", message="Slide id is required.", path=slide_path)
            )
        if slide.slide_type not in REPORTSPEC_SLIDE_TYPES:
            errors.append(
                ReportSpecValidationError(
                    code="INVALID_SLIDE_TYPE",
                    message=f"Invalid slide_type {slide.slide_type!r}.",
                    path=f"{slide_path}.slide_type",
                )
            )
        if slide.layout not in REPORTSPEC_LAYOUTS:
            errors.append(
                ReportSpecValidationError(
                    code="INVALID_LAYOUT",
                    message=f"Invalid layout {slide.layout!r}.",
                    path=f"{slide_path}.layout",
                )
            )
        if not slide.title:
            errors.append(
                ReportSpecValidationError(code="MISSING_SLIDE_TITLE", message="Slide title is required.", path=slide_path)
            )
        _validate_visibility_rules(slide.visibility_rules, errors, f"{slide_path}.visibility_rules")
        for block_index, block in enumerate(slide.blocks):
            block_path = f"{slide_path}.blocks[{block_index}]"
            _validate_block(block, slide, errors, block_path, seen_block_ids)


def _validate_block(
    block: BlockSpec,
    slide: SlideSpec,
    errors: list[ReportSpecValidationError],
    path: str,
    seen_block_ids: set[str],
) -> None:
    if not block.id:
        errors.append(ReportSpecValidationError(code="MISSING_BLOCK_ID", message="Block id is required.", path=path))
    elif block.id in seen_block_ids:
        errors.append(
            ReportSpecValidationError(
                code="DUPLICATE_BLOCK_ID",
                message=f"Duplicate block id {block.id!r}.",
                path=path,
            )
        )
    else:
        seen_block_ids.add(block.id)

    if block.type not in REPORTSPEC_BLOCK_TYPES:
        errors.append(
            ReportSpecValidationError(
                code="INVALID_BLOCK_TYPE",
                message=f"Invalid block type {block.type!r}.",
                path=f"{path}.type",
            )
        )
    compatible_layouts = BLOCK_LAYOUT_COMPATIBILITY.get(block.type)
    if compatible_layouts and slide.layout not in compatible_layouts:
        errors.append(
            ReportSpecValidationError(
                code="INVALID_BLOCK_LAYOUT_COMBINATION",
                message=f"Block type {block.type!r} is not valid for layout {slide.layout!r}.",
                path=f"{path}.type",
            )
        )
    if block.type in DATA_BOUND_BLOCK_TYPES and not block.bindings:
        errors.append(
            ReportSpecValidationError(
                code="MISSING_DATA_BINDING",
                message=f"Block type {block.type!r} requires at least one canonical data binding.",
                path=f"{path}.bindings",
            )
        )
    for binding_index, binding in enumerate(block.bindings):
        _validate_binding(binding, errors, f"{path}.bindings[{binding_index}]")
    _validate_visibility_rules(block.visibility_rules, errors, f"{path}.visibility_rules")


def _validate_binding(
    binding: DataBinding,
    errors: list[ReportSpecValidationError],
    path: str,
) -> None:
    if not binding.canonical_semantic:
        errors.append(
            ReportSpecValidationError(
                code="MISSING_CANONICAL_BINDING",
                message="DataBinding canonical_semantic is required.",
                path=f"{path}.canonical_semantic",
            )
        )
    elif not _is_known_canonical_semantic(binding.canonical_semantic):
        errors.append(
            ReportSpecValidationError(
                code="UNKNOWN_CANONICAL_BINDING",
                message=f"Unknown canonical binding {binding.canonical_semantic!r}.",
                path=f"{path}.canonical_semantic",
            )
        )
    binding_payload = binding.as_dict()
    binding_payload.pop("canonical_semantic", None)
    if _contains_raw_provider_reference(binding_payload):
        errors.append(
            ReportSpecValidationError(
                code="RAW_PROVIDER_FIELD_BINDING",
                message="ReportSpec bindings must reference canonical semantics, not provider raw fields.",
                path=path,
            )
        )


def _validate_visibility_rules(
    rules: Iterable[VisibilityRule],
    errors: list[ReportSpecValidationError],
    path: str,
) -> None:
    for index, rule in enumerate(rules):
        rule_path = f"{path}[{index}]"
        if rule.rule_type not in REPORTSPEC_VISIBILITY_RULE_TYPES:
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_VISIBILITY_RULE",
                    message=f"Unknown visibility rule_type {rule.rule_type!r}.",
                    path=f"{rule_path}.rule_type",
                )
            )
        if rule.operator is not None and rule.operator not in REPORTSPEC_VISIBILITY_OPERATORS:
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_VISIBILITY_RULE",
                    message=f"Invalid visibility operator {rule.operator!r}.",
                    path=f"{rule_path}.operator",
                )
            )
        if rule.canonical_semantic is not None and not _is_known_canonical_semantic(rule.canonical_semantic):
            errors.append(
                ReportSpecValidationError(
                    code="UNKNOWN_CANONICAL_BINDING",
                    message=f"Unknown canonical binding {rule.canonical_semantic!r}.",
                    path=f"{rule_path}.canonical_semantic",
                )
            )
        if rule.rule_type in {"available", "unsupported", "missing", "empty", "not_requested"} and not rule.canonical_semantic:
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_VISIBILITY_RULE",
                    message=f"Visibility rule {rule.rule_type!r} requires canonical_semantic.",
                    path=f"{rule_path}.canonical_semantic",
                )
            )
        if rule.rule_type in {"source_count", "minimum_contributions"}:
            if rule.operator is None:
                errors.append(
                    ReportSpecValidationError(
                        code="MALFORMED_VISIBILITY_RULE",
                        message=f"Visibility rule {rule.rule_type!r} requires an operator.",
                        path=f"{rule_path}.operator",
                    )
                )
            if not isinstance(rule.value, int) or isinstance(rule.value, bool) or rule.value < 0:
                errors.append(
                    ReportSpecValidationError(
                        code="MALFORMED_VISIBILITY_RULE",
                        message=f"Visibility rule {rule.rule_type!r} requires a non-negative integer value.",
                        path=f"{rule_path}.value",
                    )
                )


def _validate_datasource_requirements(
    datasource_requirements: Any,
    errors: list[ReportSpecValidationError],
) -> None:
    if not isinstance(datasource_requirements, Mapping):
        errors.append(
            ReportSpecValidationError(
                code="MALFORMED_DATASOURCE_REQUIREMENTS",
                message="ReportSpec datasource_requirements must be an object.",
                path="datasource_requirements",
            )
        )
        return

    mode = datasource_requirements.get("mode")
    if mode is not None and str(mode).strip() not in REPORTSPEC_DATASOURCE_MODES:
        errors.append(
            ReportSpecValidationError(
                code="MALFORMED_DATASOURCE_REQUIREMENTS",
                message=f"Invalid datasource requirement mode {mode!r}.",
                path="datasource_requirements.mode",
            )
        )

    sources = datasource_requirements.get("sources")
    if sources is not None:
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) or not source.strip() for source in sources)
        ):
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_DATASOURCE_REQUIREMENTS",
                    message="datasource_requirements.sources must be a non-empty list of source type strings.",
                    path="datasource_requirements.sources",
                )
            )
        elif len(set(sources)) != len(sources):
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_DATASOURCE_REQUIREMENTS",
                    message="datasource_requirements.sources must not contain duplicates.",
                    path="datasource_requirements.sources",
                )
            )

    minimum_source_count = datasource_requirements.get("minimum_source_count")
    if minimum_source_count is not None and (
        not isinstance(minimum_source_count, int)
        or isinstance(minimum_source_count, bool)
        or minimum_source_count < 0
    ):
        errors.append(
            ReportSpecValidationError(
                code="MALFORMED_DATASOURCE_REQUIREMENTS",
                message="datasource_requirements.minimum_source_count must be a non-negative integer.",
                path="datasource_requirements.minimum_source_count",
            )
        )

    catalog_required = datasource_requirements.get("catalog_required")
    if catalog_required is not None and not isinstance(catalog_required, bool):
        errors.append(
            ReportSpecValidationError(
                code="MALFORMED_DATASOURCE_REQUIREMENTS",
                message="datasource_requirements.catalog_required must be a boolean when provided.",
                path="datasource_requirements.catalog_required",
            )
        )

    for field_name in ("required_canonical_semantics", "optional_canonical_semantics"):
        semantics = datasource_requirements.get(field_name)
        if semantics is None:
            continue
        if not isinstance(semantics, list) or any(
            not isinstance(semantic, str) or not semantic.strip()
            for semantic in semantics
        ):
            errors.append(
                ReportSpecValidationError(
                    code="MALFORMED_DATASOURCE_REQUIREMENTS",
                    message=f"datasource_requirements.{field_name} must be a list of canonical semantic strings.",
                    path=f"datasource_requirements.{field_name}",
                )
            )
            continue
        for semantic in semantics:
            if not _is_known_canonical_semantic(semantic):
                errors.append(
                    ReportSpecValidationError(
                        code="UNKNOWN_CANONICAL_BINDING",
                        message=f"Unknown canonical binding {semantic!r}.",
                        path=f"datasource_requirements.{field_name}",
                    )
                )


__all__ = [
    "BLOCK_LAYOUT_COMPATIBILITY",
    "DATA_BOUND_BLOCK_TYPES",
    "FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC",
    "GenerationMode",
    "InvalidReportSpecError",
    "RAW_PROVIDER_FIELD_NAMES",
    "REPORTSPEC_BLOCK_TYPES",
    "REPORTSPEC_DATASOURCE_MODES",
    "REPORTSPEC_GENERATION_MODES",
    "REPORTSPEC_LAYOUTS",
    "REPORTSPEC_SCHEMA_VERSION",
    "REPORTSPEC_SLIDE_TYPES",
    "REPORTSPEC_VISIBILITY_OPERATORS",
    "REPORTSPEC_VISIBILITY_RULE_TYPES",
    "SUPPORTED_REPORTSPEC_SCHEMA_VERSIONS",
    "BlockSpec",
    "DataBinding",
    "ReportSpec",
    "ReportSpecValidationError",
    "ReportSpecValidationResult",
    "SlideSpec",
    "VisibilityRule",
    "assert_valid_report_spec",
    "collect_report_spec_bindings",
    "facebook_instagram_10_reference_reportspec",
    "report_spec_from_json",
    "report_spec_to_json",
    "validate_report_spec_datasource_requirements",
    "validate_report_spec",
]
