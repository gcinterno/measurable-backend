# Report Engine Reform Final Audit

# Status

COMPLETE

This final audit closes the Facebook Pages 5-slide Recipe V1 / Report Engine
Reform cycle after manual production-path QA passed and the production cutover
was committed.

Latest production cutover commit:

```text
64cd39a6e0d2779183303a065a2beca4c685b1d9
feat(report-engine): cut over Facebook 5 to recipe builder
```

# Canonical Recipe

The backend canonical Recipe exists in `app/report_recipes.py`:

```text
id: facebook_pages_5
platform: facebook_pages
version: 1
```

Slides:

```text
01 cover
02 organic_impressions_overview
03 engagement_overview
04 page_views_overview
05 executive_summary
```

The Recipe contains reusable structure only. It does not contain metric values,
AI insight text, user data, chart data, visual layout, CSS, JSX, or persisted
report payloads.

# Production Generation

Official Facebook Pages 5-slide production generation now uses the canonical
Recipe by default.

Production path:

```text
POST /reports/meta-pages
-> create_meta_pages_report(...)
-> _create_meta_dataset_report(...)
-> _build_meta_dataset_report_blocks(...)
-> build_facebook_pages_5_blocks(...)
-> build_facebook_pages_5_blocks_from_recipe(FACEBOOK_PAGES_5_RECIPE, ...)
-> _ensure_facebook_pages_five_slide_structure(...)
-> _enforce_facebook_pages_5_recipe(...)
-> ReportBlock persistence
```

The cutover is guarded by the official Facebook Pages 5 predicate:

```text
report_source == "meta_pages_v2"
integration_type in {"facebook_pages", "meta_pages"}
effective_slide_limit == 5
```

Generic `requested_slides <= 5` routing remains unchanged.

# Recipe Enforcement

Recipe enforcement remains active before `ReportBlock` persistence.

Enforcement validates:

- slide count
- top-level `order`
- `semantic_name`

Enforcement intentionally does not validate:

- metric values
- full `data_json`
- AI insight text
- captions
- top content
- timeframe
- chart data
- visual configuration
- database schema

Invalid official Facebook Pages 5 structure fails before persistence with a
structured API-safe error.

# Rollback

Operational rollback remains available:

```text
FACEBOOK_PAGES_5_RECIPE_BUILDER=legacy
```

Rollback behavior:

```text
missing -> recipe builder
recipe  -> recipe builder
legacy  -> build_5_blocks(...)
```

Unknown values fail closed with:

```text
facebook_pages_recipe_builder_config_invalid
```

The legacy `build_5_blocks(...)` function remains available and must not be
removed in this closure phase.

# Contract And Catalog

Backend Recipe contract exists:

- `ReportRecipe`
- `ReportRecipeSlide`
- Python field: `semantic_name`
- frontend/wire equivalent: `semanticName`

Backend Recipe catalog exists:

- `get_report_recipe(...)`
- `list_report_recipes(...)`
- `get_report_recipes_for_platform(...)`

Authenticated Recipe API exists:

- `GET /report-recipes`
- `GET /report-recipes/{recipe_id}`
- optional `platform` filter
- auth dependency: `get_current_user`

Frontend/backend normalization is compatible because backend exposes ordered
Recipe slides and stable semantic identifiers, while frontend can normalize
`semantic_name` to its `semanticName` contract.

# Frontend Rendering Architecture

The completed frontend architecture is confirmed as the consumer side of this
contract:

- canonical 16:9 slides
- Report Lab
- Facebook Pages 5 fixture
- CoverBlockAdapter
- MetricStorySlide
- metric story configuration
- ExecutiveSummarySlide
- ExecutiveSummaryBlockAdapter
- Facebook Pages 5 Slide Registry
- Recipe integration in Report Lab

Frontend rendering remains downstream of persisted `ReportBlock` payloads and
`semantic_name`.

# Source Of Truth Hierarchy

The intended source-of-truth hierarchy is now:

```text
Backend Recipe
    -> Production block builder
    -> ReportBlock persisted payload
    -> semantic_name
    -> Frontend Slide Registry
    -> slide adapter/component
    -> canonical 16:9 renderer
```

Backend owns the canonical Recipe and production block composition. Frontend
owns the slide registry, adapter/component selection, and visual rendering.

# Systems Confirmed Unchanged

No intentional behavioral regression was introduced to:

- Instagram
- Meta Ads
- multi-source reports
- legacy reports

Unchanged contracts:

- `ReportBlock.data_json` schema
- `ReportBlock` persistence shape
- database schema
- report response schema
- frontend renderer contract

No migrations were created.

# Validation Summary

Completed validation status:

- Recipe structural validation: PASS
- builder parity: PASS
- production integration: PASS
- frontend Report Lab Recipe compatibility: PASS
- manual production report QA: PASS
- latest production cutover focused suite: 61 passed

Manual QA result:

```text
PASS
```

A brand-new Facebook Pages 5-slide report was generated through the normal
application flow using the default Recipe builder and was visually and
functionally identical to the previous implementation.

# PDF Export

PDF/export is explicitly out of scope for this reform closure audit.

Known PDF export/rendering mismatch:

```text
OUT OF SCOPE / SEPARATE FOLLOW-UP CYCLE
```

No PDF/export code was investigated or modified for this audit.

# Remaining Technical Debt

Future-only items:

- Remove legacy `build_5_blocks(...)` only after a separate production
  validation window and rollback retirement decision.
- Retire `FACEBOOK_PAGES_5_RECIPE_BUILDER=legacy` only after operational
  rollback is no longer required.
- Expand Recipes beyond `facebook_pages_5` in separate phases.
- Define Blueprint V1 separately.
- Handle PDF/export mismatch in a separate follow-up cycle.

# Closure Recommendation

Create a documentation-only checkpoint for:

```text
docs(report-engine): add final reform audit
```

Do not include preexisting untracked baseline docs, `.DS_Store`, ZIP files,
temporary catalog files, PDF/export changes, or unrelated work.
