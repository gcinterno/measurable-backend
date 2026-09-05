# Status

IMPLEMENTED — PARALLEL PATH ONLY

# Old Generation Path

Production still uses the existing Facebook Pages 5 builder:

```text
POST /reports/meta-pages
-> create_meta_pages_report(...)
-> _create_meta_dataset_report(...)
-> build_blocks(...)
-> build_5_blocks(...)
-> _ensure_facebook_pages_five_slide_structure(...)
-> _enforce_facebook_pages_5_recipe(...)
-> ReportBlock persistence
```

`build_5_blocks(...)` remains the production builder.

# New Parallel Generation Path

The parity-only Recipe path is:

```text
FACEBOOK_PAGES_5_RECIPE
-> build_facebook_pages_5_blocks_from_recipe(...)
-> build_block_for_recipe_slide(...)
-> existing Facebook Pages 5 block/payload helpers
-> validate_blocks_against_recipe(...)
```

This path is not wired to report persistence, API report creation, frontend, renderer, Meta fetches, AI generation, or database models.

# Semantic Dispatcher Mapping

| semantic_name | Existing helper used |
| --- | --- |
| `cover` | `_build_facebook_pages_5_cover_block(...)` |
| `organic_impressions_overview` | `_build_facebook_pages_metric_slide_payload(...)` through `_build_facebook_pages_5_metric_block(...)` |
| `engagement_overview` | `_build_facebook_pages_metric_slide_payload(...)` through `_build_facebook_pages_5_metric_block(...)` |
| `page_views_overview` | `_build_facebook_pages_metric_slide_payload(...)` through `_build_facebook_pages_5_metric_block(...)` |
| `executive_summary` | `_build_five_slide_summary_payload(...)` through `_build_facebook_pages_5_summary_block(...)` |

# Helper Extraction

Small block-spec wrappers were extracted in `app/main.py`:

- `_build_facebook_pages_5_cover_block(...)`
- `_build_facebook_pages_5_metric_block(...)`
- `_build_facebook_pages_5_summary_block(...)`

They preserve the same `_meta_report_block(...)` output shape used by `build_5_blocks(...)`. The metric payload, summary payload, AI insight, top content, daily series, and KPI summary logic were not duplicated.

# Parity Strategy

The parity test compares:

```text
legacy = build_5_blocks(context)
recipe = build_facebook_pages_5_blocks_from_recipe(FACEBOOK_PAGES_5_RECIPE, context)
```

The test requires exact equality:

```text
recipe == legacy
```

No nondeterministic fields required normalization.

# Fields Compared

The exact equality assertion covers every block field, including:

- block count
- top-level `order`
- block `type`
- `editable_fields_json`
- full `data_json`
- `slide_number`
- `slide_type`
- `semantic_name`
- titles and labels
- timeframe fields
- branding fields
- metric totals
- formatted totals
- daily series
- chart payloads
- AI/fallback insight text
- executive summary text
- KPI summary cards
- top content
- provider / source metric metadata

# Unsupported Semantic Behavior

Unsupported semantic names raise:

```text
UnsupportedRecipeSemanticNameError
```

Known semantic names without a registered handler raise:

```text
MissingRecipeSemanticHandlerError
```

Structurally invalid Recipes raise:

```text
InvalidReportRecipeForBuildError
```

Generated canonical output is also validated with:

```text
validate_blocks_against_recipe(...)
```

# Production Status

NOT SWITCHED

Production current:

```text
data/context
  -> build_5_blocks(...)
  -> Recipe enforcement
  -> persistence
```

Parallel verified path:

```text
Recipe
  -> semantic dispatcher
  -> existing payload builders
  -> Recipe-driven blocks
  -> parity comparison
```

# Next Step

After parity review, a later phase can switch the official Facebook Pages 5 production path from direct `build_5_blocks(...)` to the Recipe-driven builder behind the existing enforcement layer.
