# Status

IMPLEMENTED - NOT COMMITTED

# Scope

This cutover applies only to the official Facebook Pages 5-slide report:

```text
report_source = meta_pages_v2
integration_type = facebook_pages | meta_pages
effective_slide_limit = 5
```

The cutover does not apply to Instagram, Meta Ads, Shopify, multi-source reports,
10-slide reports, 15-slide reports, 30-slide reports, legacy reports, or generic
`requested_slides <= 5` flows.

# Old Production Path

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

# New Production Path

```text
POST /reports/meta-pages
-> create_meta_pages_report(...)
-> _create_meta_dataset_report(...)
-> should_enforce_facebook_pages_5_recipe(...)
-> build_facebook_pages_5_blocks(...)
-> build_facebook_pages_5_blocks_from_recipe(FACEBOOK_PAGES_5_RECIPE, ...)
-> _ensure_facebook_pages_five_slide_structure(...)
-> _enforce_facebook_pages_5_recipe(...)
-> ReportBlock persistence
```

# Cutover Location

The cutover is inside `_create_meta_dataset_report(...)`, after the Facebook
Pages report inputs and block build context are prepared.

The official Facebook Pages 5 guard is still:

```python
should_enforce_facebook_pages_5_recipe(
    report_source=report_source,
    integration_type=report_inputs["integration_type"],
    effective_slide_limit=slide_limits["effective_slide_limit"],
)
```

The generic `build_blocks(...)` behavior is unchanged.

# Recipe Source

The production Recipe path uses:

```text
FACEBOOK_PAGES_5_RECIPE
```

from `app/report_recipes.py`.

Canonical order:

```text
01 cover
02 organic_impressions_overview
03 engagement_overview
04 page_views_overview
05 executive_summary
```

# Builder Selector

`build_facebook_pages_5_blocks(...)` is the production selector for the official
Facebook Pages 5 report.

It calls:

```text
recipe mode -> build_facebook_pages_5_blocks_from_recipe(...)
legacy mode -> build_5_blocks(...)
```

The legacy builder remains available and unchanged.

# Rollback Switch

Environment variable:

```text
FACEBOOK_PAGES_5_RECIPE_BUILDER
```

Allowed values:

```text
missing -> recipe
recipe  -> Recipe-driven builder
legacy  -> build_5_blocks(...)
```

Unknown values fail closed with:

```text
HTTP 500
code = facebook_pages_recipe_builder_config_invalid
```

# Enforcement

Recipe enforcement remains separate from generation:

```text
_ensure_facebook_pages_five_slide_structure(...)
->_enforce_facebook_pages_5_recipe(...)
```

The builder constructs the canonical structure. Enforcement verifies the final
structure before `ReportBlock` persistence.

# Parity

Phase 3C.2 established exact parity:

```text
build_facebook_pages_5_blocks_from_recipe(FACEBOOK_PAGES_5_RECIPE, context)
==
build_5_blocks(context)
```

The production cutover test repeats this comparison through the selector and
requires exact equality for the serialized block structures, including
`data_json`.

# Unchanged Contracts

This phase does not change:

- `ReportBlock`
- `ReportVersion`
- `data_json`
- `editable_fields_json`
- API response schemas
- database schema
- semantic names
- slide order
- titles
- metric calculations
- daily series
- AI insight text
- top content
- timeframe
- branding
- frontend rendering

# Explicitly Unaffected Reports

- Instagram Business
- Meta Ads
- Shopify
- Multi-source reports
- 10-slide reports
- 15-slide reports
- 30-slide reports
- Legacy/non-official `requested_slides <= 5` flows

# Rollback Instructions

Set:

```text
FACEBOOK_PAGES_5_RECIPE_BUILDER=legacy
```

The official Facebook Pages 5 path will use `build_5_blocks(...)` again while
keeping Recipe enforcement active.

# Next Step

Monitor production behavior with the Recipe builder enabled by default. After a
separate validation window, legacy builder removal can be planned as a later
phase.
