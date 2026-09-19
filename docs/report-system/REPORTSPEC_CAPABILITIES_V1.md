# ReportSpec Capabilities Contract V1

`GET /report-spec/capabilities` is the authoritative editor-facing capability
contract for ReportSpec schema `1.0`. The endpoint is admin-authenticated,
matching the Report Studio and report-template APIs.

The response is built from the same layout, block, binding-requirement,
visibility, datasource, and canonical metric definitions used by
`validate_report_spec`. Consumers should use `contract_version` to version the
metadata integration and `schema_version` when serializing a ReportSpec.

## Registry reconciliation

Before D5E.0A, the backend accepted eight layouts and twelve block types. The
D5 frontend composition and renderer registries had fourteen layouts and
sixteen block types. The normalized backend registry is now the union of the
real, rendered D4/D5 concepts, without presentation-only pseudo-metrics.

Layouts:

- `cover`
- `metric_chart`
- `metric_chart_source_split`
- `platform_comparison`
- `source_specific_metric`
- `audience_split`
- `source_split`
- `content_ranking`
- `executive_summary`
- `recommendations`
- `multi_kpi_dashboard`
- `metric_focus`
- `metric_with_timeseries`
- `text`

Block types:

- `metric_hero`
- `timeseries_chart`
- `bar_chart`
- `source_contribution`
- `source_split`
- `platform_metric`
- `insight`
- `executive_read`
- `executive_insight`
- `content_card`
- `content_performance`
- `content_ranking`
- `recommendation`
- `multi_kpi`
- `text`
- `cover_branding`

`metric_chart_source_split` and `executive_read` are legitimate D5 stable IDs,
not aliases. The older `metric_with_timeseries` and `executive_insight` IDs are
also retained because they are used by persisted D4 ReportSpecs.

## Canonical semantics versus presentation semantics

Canonical bindings are sourced only from `CANONICAL_AGGREGATION_RULES`. They
represent normalized report data with an aggregation method and comparability
group. `executive_insights`, `recommendations`, and `cover` are not canonical
metric semantics.

`executive_insights` remains a generated recipe/slide semantic in the existing
production report pipeline. It must not be serialized as a ReportSpec
`canonical_semantic`. `executive_read`, `executive_insight`, `insight`, and
`recommendation` are presentation/derived blocks. Their canonical bindings are
optional: they may consume explicitly selected canonical evidence, or consume
the report/slide context without a pseudo-metric binding.

This decision does not rename or modify production report recipe semantics or
generation behavior.

## Binding serialization

The canonical persisted block binding field is `bindings`, containing an array
of binding objects. A Metric Hero bound to engagement is serialized as:

```json
{
  "id": "engagement-hero",
  "type": "metric_hero",
  "bindings": [
    {
      "canonical_semantic": "engagement"
    }
  ]
}
```

The complete binding fields are `canonical_semantic`, `metric_path`,
`source_constraint`, `timeseries_selector`, `aggregation_selector`,
`ranking_selector`, and `metadata`. Only `canonical_semantic` is required for
each binding object.

The singular `binding` object remains accepted as a legacy input alias and is
normalized to `bindings` when persisted. `binding.semantic`, `binding.path`,
and named-object `bindings` are frontend/editor representations, not the
backend persistence contract. They must be normalized before submission.

## Other validated structures

ReportSpec `generation_mode` accepts `template`, `ai`, or `legacy_migration`.
This is distinct from report-template metadata `generation_mode`, which accepts
`manual_template`, `ai_generated`, or `system`.

Visibility rules use `rule_type`, optional `canonical_semantic`, optional
`operator`, `value`, and `metadata`. The supported rule types and operators,
including which rules require semantic or comparison fields, are returned by
the capabilities endpoint.

`datasource_requirements` is an object. Validated fields are `mode`, `sources`,
`minimum_source_count`, `catalog_required`, `required_canonical_semantics`, and
`optional_canonical_semantics`. Unknown extension fields remain allowed and
preserved for backward compatibility.

No database migration is required for this contract.
