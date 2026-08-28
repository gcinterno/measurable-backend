from __future__ import annotations

from dataclasses import replace

import pytest

from app.canonical_metric_catalog import (
    CANONICAL_AGGREGATION_RULES,
    CanonicalMetricRecord,
    SUM,
    UNSUPPORTED,
    resolve_metric,
)
from app.report_spec import (
    FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC,
    BLOCK_LAYOUT_COMPATIBILITY,
    RAW_PROVIDER_FIELD_NAMES,
    REPORTSPEC_BLOCK_TYPES,
    REPORTSPEC_LAYOUTS,
    REPORTSPEC_SCHEMA_VERSION,
    BlockSpec,
    DataBinding,
    InvalidReportSpecError,
    ReportSpec,
    SlideSpec,
    VisibilityRule,
    assert_valid_report_spec,
    collect_report_spec_bindings,
    facebook_instagram_10_reference_reportspec,
    report_spec_from_json,
    report_spec_to_json,
    validate_report_spec,
)


def _minimal_reportspec(
    *,
    block: BlockSpec | None = None,
    slides: tuple[SlideSpec, ...] | None = None,
    schema_version: str = REPORTSPEC_SCHEMA_VERSION,
) -> ReportSpec:
    default_block = block or BlockSpec(
        id="engagement-metric",
        type="metric_hero",
        bindings=(DataBinding(canonical_semantic="engagement"),),
    )
    return ReportSpec(
        schema_version=schema_version,
        id="generic_report_v1",
        name="Generic Report",
        report_type="channel_health",
        template_id=None,
        generation_mode="template",
        datasource_requirements={
            "minimum_source_count": 1,
            "required_canonical_semantics": ["engagement"],
        },
        reporting_period={"selector": "request.timeframe"},
        theme_ref={"id": "default"},
        slides=slides
        or (
            SlideSpec(
                id="engagement",
                order=1,
                slide_type="metric",
                title="Engagement",
                layout="metric_focus",
                blocks=(default_block,),
            ),
        ),
    )


def test_facebook_instagram_reference_reportspec_validates() -> None:
    result = validate_report_spec(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC)

    assert result.valid
    assert result.errors == ()
    assert_valid_report_spec(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC)


def test_reference_reportspec_uses_only_canonical_bindings() -> None:
    spec = FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC
    bindings = collect_report_spec_bindings(spec)
    binding_semantics = {binding.canonical_semantic for binding in bindings}

    assert {
        "reach",
        "visibility",
        "engagement",
        "page_profile_activity",
        "audience_size",
        "content_activity",
        "top_content",
    }.issubset(binding_semantics)
    assert all(semantic in CANONICAL_AGGREGATION_RULES for semantic in binding_semantics)


def test_reference_reportspec_does_not_require_provider_raw_field_names() -> None:
    serialized = report_spec_to_json(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC)

    for raw_field_name in RAW_PROVIDER_FIELD_NAMES:
        assert raw_field_name not in serialized
    assert "page_post_engagements" not in serialized
    assert "page_posts_impressions_organic" not in serialized
    assert "followers_count" not in serialized


def test_report_spec_json_round_trip_is_stable() -> None:
    spec = facebook_instagram_10_reference_reportspec()

    encoded = report_spec_to_json(spec)
    decoded = report_spec_from_json(encoded)

    assert decoded == spec
    assert report_spec_to_json(decoded) == encoded


def test_reference_reportspec_contains_current_ten_ordered_slides() -> None:
    spec = FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC

    assert [slide.order for slide in spec.slides] == list(range(1, 11))
    assert [slide.title for slide in spec.slides] == [
        "Facebook + Instagram",
        "Reach",
        "Visibility",
        "Engagement",
        "Page / Profile Activity",
        "Audience",
        "Content Activity",
        "Top Performing Content",
        "Executive Summary",
        "Next Actions",
    ]


def test_report_spec_is_integration_agnostic() -> None:
    spec = _minimal_reportspec()
    serialized = report_spec_to_json(spec)

    assert validate_report_spec(spec).valid
    assert '"facebook_pages"' not in serialized
    assert '"instagram_business"' not in serialized
    assert '"instagram_business_login"' not in serialized
    assert '"meta"' not in serialized


def test_unsupported_catalog_metric_remains_null_and_does_not_invalidate_reportspec() -> None:
    catalog = (
        CanonicalMetricRecord(
            source_type="social_source",
            source_label="Source",
            source_metric="provider_impression_metric",
            canonical_metric="impressions",
            metric_family="visibility",
            value=None,
            support_status=UNSUPPORTED,
            aggregation_method=SUM,
            comparability_group="same_source_impressions",
        ),
    )

    resolution = resolve_metric("impressions", catalog)

    assert resolution.combined_value is None
    assert resolution.sources[0].value is None
    assert resolution.sources[0].support_status == UNSUPPORTED
    assert validate_report_spec(FACEBOOK_INSTAGRAM_10_REFERENCE_REPORTSPEC).valid


def test_reportspec_validation_rejects_unknown_and_raw_bindings() -> None:
    spec = _minimal_reportspec(
        block=BlockSpec(
            id="raw-metric",
            type="metric_hero",
            bindings=(DataBinding(canonical_semantic="page_post_engagements"),),
        )
    )

    result = validate_report_spec(spec)
    codes = {error.code for error in result.errors}

    assert not result.valid
    assert codes == {"UNKNOWN_CANONICAL_BINDING"}
    with pytest.raises(InvalidReportSpecError):
        assert_valid_report_spec(spec)


def test_reportspec_validation_allows_source_specific_canonical_semantics_but_not_raw_paths() -> None:
    canonical_spec = _minimal_reportspec(
        block=BlockSpec(
            id="accounts-engaged",
            type="metric_hero",
            bindings=(DataBinding(canonical_semantic="accounts_engaged"),),
        )
    )
    raw_path_spec = _minimal_reportspec(
        block=BlockSpec(
            id="raw-path",
            type="metric_hero",
            bindings=(
                DataBinding(
                    canonical_semantic="engagement",
                    metric_path="normalized_report_metrics.total_interactions",
                ),
            ),
        )
    )

    assert validate_report_spec(canonical_spec).valid
    raw_result = validate_report_spec(raw_path_spec)
    assert not raw_result.valid
    assert {error.code for error in raw_result.errors} == {"RAW_PROVIDER_FIELD_BINDING"}


def test_reportspec_validation_rejects_invalid_block_type_and_layout_combo() -> None:
    invalid_type_result = validate_report_spec(
        _minimal_reportspec(
            block=BlockSpec(
                id="unknown",
                type="unknown_block",
                bindings=(DataBinding(canonical_semantic="engagement"),),
            )
        )
    )
    invalid_layout_result = validate_report_spec(
        _minimal_reportspec(
            block=BlockSpec(
                id="ranking",
                type="content_ranking",
                bindings=(DataBinding(canonical_semantic="top_content"),),
            )
        )
    )

    assert "unknown_block" not in REPORTSPEC_BLOCK_TYPES
    assert "metric_focus" not in BLOCK_LAYOUT_COMPATIBILITY["content_ranking"]
    assert "content_ranking" in REPORTSPEC_LAYOUTS
    assert {error.code for error in invalid_type_result.errors} == {"INVALID_BLOCK_TYPE"}
    assert {error.code for error in invalid_layout_result.errors} == {
        "INVALID_BLOCK_LAYOUT_COMBINATION"
    }


def test_reportspec_validation_rejects_duplicate_ids_and_invalid_ordering() -> None:
    slide = SlideSpec(
        id="duplicate",
        order=1,
        slide_type="metric",
        title="Engagement",
        layout="metric_focus",
        blocks=(
            BlockSpec(
                id="duplicate-block",
                type="metric_hero",
                bindings=(DataBinding(canonical_semantic="engagement"),),
            ),
            BlockSpec(
                id="duplicate-block",
                type="metric_hero",
                bindings=(DataBinding(canonical_semantic="reach"),),
            ),
        ),
    )
    spec = _minimal_reportspec(slides=(slide, replace(slide, order=3)))

    result = validate_report_spec(spec)
    codes = {error.code for error in result.errors}

    assert not result.valid
    assert "DUPLICATE_SLIDE_ID" in codes
    assert "DUPLICATE_BLOCK_ID" in codes
    assert "INVALID_SLIDE_ORDER" in codes


def test_reportspec_validation_rejects_unsupported_schema_version() -> None:
    result = validate_report_spec(_minimal_reportspec(schema_version="2.0"))

    assert not result.valid
    assert {error.code for error in result.errors} == {"UNSUPPORTED_SCHEMA_VERSION"}


def test_reportspec_validation_rejects_malformed_visibility_rules() -> None:
    spec = _minimal_reportspec(
        block=BlockSpec(
            id="engagement-metric",
            type="metric_hero",
            bindings=(DataBinding(canonical_semantic="engagement"),),
            visibility_rules=(
                VisibilityRule(rule_type="available"),
                VisibilityRule(rule_type="source_count", value="two"),
            ),
        )
    )

    result = validate_report_spec(spec)

    assert not result.valid
    assert {error.code for error in result.errors} == {"MALFORMED_VISIBILITY_RULE"}
