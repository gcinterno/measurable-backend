# Executive Summary

Backend Recipe/Blueprint Contract Audit: COMPLETE

This audit was run on branch `report-engine-reform` at base commit `7df301f Enable Instagram Business Login disconnect`.

The current Facebook Pages 5 structure is not represented by a declarative Recipe object. It is encoded imperatively in `app/main.py` through `build_blocks(...)`, `build_5_blocks(...)`, and a final Facebook-specific correction layer named `_ensure_facebook_pages_five_slide_structure(...)`.

Current production persistence stores `ReportBlock.order`, `ReportBlock.type`, and a JSON string in `ReportBlock.data_json`. `semantic_name` is not a database column; it lives inside `data_json`.

Backend is currently the source of truth for Facebook Pages 5 order and data population. Frontend should not duplicate backend metric bindings, source aliases, or metric availability logic.

Recommended V1 direction: define the canonical Facebook Pages 5 Recipe in backend code constants, validate current builder output against it, and expose or mirror that contract to frontend. Do not store Recipe V1 in the database.

# Current Facebook 5 Generation Flow

Official endpoint and call chain:

```text
POST /reports/meta-pages
-> create_meta_pages_report(...)
-> _create_meta_dataset_report(...)
-> extract_meta_pages_report_inputs(...)
-> build_blocks(...)
-> build_5_blocks(...)
-> optional run_ai_agents_pipeline(...) when ai_mode == "agents"
-> _ensure_facebook_pages_five_slide_structure(...) for official Facebook Pages 5
-> Report
-> ReportVersion(version=1)
-> ReportBlock rows
-> MetaPagesReportCreateOut
```

Real locations:

- `create_meta_pages_report(...)`: `app/main.py`
- `_create_meta_dataset_report(...)`: `app/main.py`
- `extract_meta_pages_report_inputs(...)`: `app/services.py`
- `build_blocks(...)`: `app/main.py`
- `build_5_blocks(...)`: `app/main.py`
- `_ensure_facebook_pages_five_slide_structure(...)`: `app/main.py`
- `Report`, `ReportVersion`, `ReportSource`, `ReportBlock`: `app/models.py`
- `MetaPagesReportCreateOut`, `ReportVersionOut`, `ReportBlockOut`: `app/schemas.py`

Important correction to the historical approximation: there is no separate `_create_meta_pages_report(...)` function in the audited code path. The official endpoint calls `_create_meta_dataset_report(...)`.

The create response returns report metadata only. Blocks are exposed later through:

- `GET /reports/{report_id}/versions`
- `GET /reports/{report_id}/versions/{version}`

Those endpoints serialize `ReportVersionOut.blocks`, ordered by `ReportBlock.order.asc()`.

# Current Structure Source of Truth

The current structure source of truth is split:

1. `build_blocks(requested_slides, dataset)` chooses the builder by requested slide count.
2. `build_5_blocks(dataset)` creates five block specs in hardcoded array order.
3. `_renumber_blocks(...)` rewrites block `order` according to array position.
4. `_meta_enrich_data_blocks(...)` enriches each block and may add compatibility fields.
5. `_ensure_facebook_pages_five_slide_structure(...)` overwrites `order`, `slide_number`, `slide_type`, and `semantic_name` by position for official Facebook Pages 5.
6. Persistence stores the corrected `order` and `data_json`.
7. API serialization sorts persisted blocks by `ReportBlock.order`.

Backend currently owns report order: YES.

If there is a conflict:

- Before persistence, `_ensure_facebook_pages_five_slide_structure(...)` wins for official Facebook Pages 5 because it assigns order by array position.
- After persistence, `ReportBlock.order` wins because version serialization sorts by that field.

Semantic names for official Facebook Pages 5 are enforced by the list:

```python
FACEBOOK_PAGES_FIVE_SLIDE_TYPES = [
    "cover",
    "organic_impressions_overview",
    "engagement_overview",
    "page_views_overview",
    "executive_summary",
]
```

`semantic_name` can function as the stable Recipe slide identifier for official Facebook Pages 5: YES, with one caveat. It is stable after enforcement, but it is not a first-class database column or schema field.

# ReportBlock / Data Contract

Real `ReportBlock` model fields:

| Field | Category | Notes |
| --- | --- | --- |
| `id` | SOURCE METADATA | Database identity. |
| `report_version_id` | COMPOSITION | Associates block with a version snapshot. |
| `type` | COMPOSITION | Coarse renderer type such as `title`, `stat`, `text`, `chart`. |
| `order` | COMPOSITION | Persisted ordering source for API reads. |
| `data_json` | MIXED | JSON string containing structure, content, metrics, AI-labeled text, visual metadata, source metadata, and legacy compatibility fields. |
| `editable_fields_json` | COMPOSITION | JSON string allowlist for editable fields. |
| `created_at` | SOURCE METADATA | Storage timestamp. |
| `updated_at` | SOURCE METADATA | Storage timestamp. |

There is no `semantic_name` column on `ReportBlock`. It is stored inside `data_json`.

General Facebook Pages 5 `data_json` categories:

| Category | Examples |
| --- | --- |
| COMPOSITION | `slide_number`, `slide_type`, `semantic_name` |
| CONTENT | `title`, `title_en`, `text`, `subtitle`, `page_name`, `platform`, `label`, `top_content_title` |
| METRICS | `metric_key`, `value`, `total`, `current_value`, `formatted_total`, `is_available`, `daily_series`, `highest_day`, `lowest_day`, `frequency`, `metrics_summary`, `top_content` |
| AI CONTENT | `insight_short`, `insight`, `insight_full`, `insight_tone`, `insight_max_chars`, `ai_summary`, `recommendation` |
| VISUAL | `branding`, `cover_branding`, `brand_name`, `brand_logo_url`, `resolved_brand_name`, `resolved_logo_url`, `chart` |
| SOURCE METADATA | `provider`, `metric_source`, `raw_metric_name`, `normalized_field`, `availability_status`, `source_metrics_used`, `unavailable_reason`, `unavailable_message`, `daily_series_source_path`, `daily_series_source_metric_key` |
| LEGACY / COMPATIBILITY | `points`, `summary`, `content`, `previous_value`, `change_absolute`, `change_percentage`, `trend` when added generically by `_meta_enrich_existing_block(...)` |

Facebook Pages 5 block shapes:

| Order | Block type | Semantic name | Primary data source | Notes |
| ---: | --- | --- | --- | --- |
| 1 | `title` | `cover` | Inline cover payload in `build_5_blocks(...)` | Uses page name, timeframe, branding. |
| 2 | `stat` | `organic_impressions_overview` | `_build_facebook_pages_metric_slide_payload(...)` | Metric key `organic_impressions`; source metric `page_posts_impressions_organic`. |
| 3 | `stat` | `engagement_overview` | `_build_facebook_pages_metric_slide_payload(...)` | Metric key `engagement`; source metric `page_post_engagements`. |
| 4 | `stat` | `page_views_overview` | `_build_facebook_pages_metric_slide_payload(...)` | Metric key `page_views`; source metric `page_views_total`. |
| 5 | `text` | `executive_summary` | `_build_five_slide_summary_payload(...)` | KPI cards, top content, deterministic AI-labeled summary and recommendation. |

# build_5_blocks Analysis

`build_5_blocks(dataset)` is currently the implicit Recipe: PARTIALLY.

It is Recipe-like because it hardcodes:

- slide count;
- slide order;
- block type;
- semantic names;
- metric choices;
- titles and labels for metric story slides;
- summary placement.

It is not a clean Recipe because it also performs data population and analysis:

- resolves branding;
- chooses metric keys;
- calls metric payload builders;
- logs product events;
- resolves followers for summary logging;
- constructs summary cards;
- ranks top content indirectly through `_build_five_slide_summary_payload(...)`;
- invokes deterministic AI-labeled insight builders;
- calls `_meta_enrich_data_blocks(...)`.

Structure responsibilities inside `build_5_blocks(...)`:

- Create five block specs in array order.
- Assign top-level block `type` values: `title`, `stat`, `stat`, `stat`, `text`.
- Assign top-level block `order` values: 1 through 5.
- Assign `slide_number`.
- Assign initial `semantic_name`.

Data population responsibilities inside `build_5_blocks(...)`:

- Cover metadata and branding.
- Metric payloads for organic impressions, engagement, and page views.
- Summary payload through `_build_five_slide_summary_payload(...)`.

AI / analysis responsibilities:

- Metric insights are built by `build_metric_ai_insight(...)`.
- Final summary and recommendation are built by `build_final_ai_summary(...)`.
- These are deterministic text builders in the official Facebook 5 path.

Compatibility responsibilities:

- `_renumber_blocks(...)` normalizes top-level order.
- `_meta_enrich_data_blocks(...)` adds or rewrites compatibility fields.
- `_ensure_facebook_pages_five_slide_structure(...)` later corrects official Facebook Pages 5 structure by position.

# Five-Slide Enforcement Analysis

`_ensure_facebook_pages_five_slide_structure(block_specs)` exists as a final guard and correction layer for official Facebook Pages 5.

It runs only when:

- `report_source == "meta_pages_v2"`;
- `integration_type` is `facebook_pages` or `meta_pages`;
- `effective_slide_limit == 5`.

What it does:

- Requires exactly five block specs; otherwise returns unchanged.
- Iterates by array position.
- Sets top-level `order` to the position.
- Sets `data_json.slide_number` to the position.
- Sets `data_json.slide_type` to the expected semantic string.
- Sets `data_json.semantic_name` to the expected semantic string.
- Sets summary title fields for the executive summary.
- Validates final slide type order against `FACEBOOK_PAGES_FIVE_SLIDE_TYPES`.
- Raises `facebook_pages_report_structure_invalid` if the final order is not valid.

What it does not do:

- It does not create missing blocks.
- It does not remove extra blocks.
- It does not inspect or map blocks by existing semantic name.
- It does not change top-level `type`.
- It does not validate every required data field.

It is a compatibility/correction layer: YES.

Could it evolve into `validate_blocks_against_recipe(...)`: PARTIALLY.

It already compares output against an expected ordered list, but a true Recipe validator should validate semantic support, order uniqueness, count, expected block kind/type mapping, and required data groups without silently rewriting by position unless explicitly configured.

# Metric Story Data Responsibilities

Metric story blocks:

- `organic_impressions_overview`
- `engagement_overview`
- `page_views_overview`

Current responsibility split:

| Responsibility | Current owner | Function / source |
| --- | --- | --- |
| Metric choice | BACKEND STRUCTURE | `build_5_blocks(...)` hardcodes `organic_impressions`, `engagement`, `page_views`. |
| Metric source binding | BACKEND DATA | `report_metric_catalog.py`, `_facebook_pages_catalog_entry(...)`, `_facebook_pages_metric_details(...)`. |
| Metric total | BACKEND DATA | `_facebook_pages_metric_details(...)`, `buildMetricSlidePayload(...)`, `_resolve_metric_total_details(...)`. |
| Metric aliases | BACKEND DATA | `METRIC_ALIASES`, `_metric_aliases(...)`, `_extract_daily_metric_series_details(...)`, total candidate helpers. |
| Daily series | BACKEND DATA | `_extract_daily_metric_series_details(...)`, `extractDailyMetricSeries(...)`. |
| Comparison | BACKEND ANALYSIS | `_meta_enrich_existing_block(...)`, `_meta_change_payload(...)`, `_meta_metric_comparison(...)`. |
| Growth payload | BACKEND ANALYSIS | Used heavily in 10-slide builder; 5-slide path stores simpler previous/change/trend compatibility fields. |
| AI Insight | BACKEND ANALYSIS | `build_metric_ai_insight(...)`, deterministic. |
| Semantic name | BACKEND STRUCTURE | `build_5_blocks(...)`, then `_ensure_facebook_pages_five_slide_structure(...)`. |
| Title / label | BACKEND STRUCTURE + CONTENT | Hardcoded in `build_5_blocks(...)`, partly enriched by catalog display names. |
| Timeframe | BACKEND DATA | `_create_meta_dataset_report(...)` resolves `report_timeframe`; block payloads embed it. |
| Rendering layout | FRONTEND PRESENTATION | Should remain frontend responsibility. |

Dangerous duplication with frontend:

- Backend already chooses metric keys and source aliases.
- Backend already emits `metric_label`, `metric_label_en`, `metric_label_es`, `title`, `label`, `primary_metric_label`, and source metadata.
- Frontend `metric-story-config.ts` should not independently choose backend source aliases or metric bindings.
- Frontend can own visual presentation labels, icons, formatting choices, and renderer variants, but should prefer backend `data_json` labels when displaying report instance content.

# Executive Summary Responsibilities

`executive_summary` is produced by `_build_five_slide_summary_payload(...)`.

Backend prepared fields:

- KPI cards in `metrics_summary`.
- Organic Impressions card.
- Engagement card.
- Page Views card.
- Followers card.
- Fans card.
- Reactions card.
- `ai_summary`.
- `recommendation`.
- `top_content_title`.
- `top_content`.
- `provider`.
- `availability_status`.
- `source_metrics_used`.
- `timeframe`.

Top content pipeline:

```text
dataset row / normalized_report_metrics
-> extract_meta_pages_report_inputs(...)
-> normalize_meta_top_content(...)
-> _meta_top_content(...)
-> _rank_facebook_page_top_content(..., limit=5)
-> executive_summary.data_json.top_content
```

Top content item fields are normalized before report composition. They include post identifiers and public/reporting metadata such as `post_id`, `created_time`, `message_preview`, `permalink_url`, `media_type`, `impressions`, `reach`, `engaged_users`, `reactions`, `comments`, `shares`, `engagement_total`, and `score`.

Ranking is backend prepared. It sorts by `score`, `engagement_total`, `impressions`, and `created_time`.

Fallback behavior:

- Missing metrics become unavailable cards with availability metadata.
- Missing top content becomes an empty `top_content` list.
- Missing usable metrics produces a deterministic fallback `ai_summary`.

Frontend should derive:

- component layout;
- table/card presentation;
- truncation for display;
- visual emphasis;
- navigation and renderer state.

Frontend should not derive:

- KPI totals;
- metric availability;
- top content ranking;
- source metric names;
- executive summary text.

# Existing Builder Landscape

| Builder | Structure hardcoded? | Data population? | Platform-specific? |
| --- | --- | --- | --- |
| `build_5_blocks(...)` | YES | YES | YES, official social 5 path and Facebook Pages 5 source of truth. |
| `build_10_blocks(...)` | YES | YES | PARTIAL, social/Meta style builder. |
| `build_15_blocks(...)` | YES | YES | PARTIAL, legacy social/Meta style builder. |
| `build_30_blocks(...)` | YES | YES | PARTIAL, uses `_build_meta_report_block_pool(...)`; legacy candidate. |
| `_build_meta_report_block_pool(...)` | YES | YES | PARTIAL, legacy Meta/social block pool. |
| `_build_meta_ads_5_blocks(...)` | YES | YES | YES, Meta Ads. |
| `_build_shopify_report_blocks(...)` | YES | YES | YES, Shopify. |
| `_multi_source_build_10_blocks(...)` | YES | YES | YES, multi-source. |

Current pattern:

```text
report product / integration / requested slide count
-> bespoke builder
-> block specs
-> ReportBlock rows
```

Does this pattern exist today: YES.

Recipe could progressively replace the structural part of these builders while keeping current data population functions.

# Recipe Contract Review

Frontend proposal:

```json
{
  "id": "facebook_pages_5",
  "platform": "facebook_pages",
  "name": "Facebook Pages - 5 Slides",
  "version": 1,
  "slides": [
    { "order": 1, "semanticName": "cover" },
    { "order": 2, "semanticName": "organic_impressions_overview" },
    { "order": 3, "semanticName": "engagement_overview" },
    { "order": 4, "semanticName": "page_views_overview" },
    { "order": 5, "semanticName": "executive_summary" }
  ]
}
```

Field review:

| Field | Recommendation | Reason |
| --- | --- | --- |
| `id` | KEEP | Needed as stable product structure identifier. |
| `platform` | KEEP | Current backend routing and product meaning are platform/integration specific. |
| `name` | KEEP | Useful display/admin label; not used for generation logic. |
| `version` | KEEP | Needed for deterministic contract evolution. |
| `slides[].order` | KEEP | Backend order is currently first-class and persisted. |
| `slides[].semanticName` | KEEP | Best existing stable slide identifier. |
| `slides[].kind` | REMOVE / DO NOT ADD | Can be resolved from semantic name by backend/frontend registry. |
| `slideCount` | REMOVE / DO NOT ADD | Derive from `len(slides)`. |
| `metricKey` | DO NOT ADD TO RECIPE V1 | Metric binding exists in backend builder/catalog today; can live in backend slide registry/factory, not reusable structure. |
| `blockType` | DO NOT ADD TO RECIPE V1 | Can be resolved from semantic name in a registry/factory. |
| layout/component fields | DO NOT ADD | Frontend presentation concern. |

No additional field is required for Facebook Pages 5 Recipe V1.

# Recipe Ownership

Recommended source of truth: SHARED-BACKEND.

Meaning:

- Backend owns canonical Recipe definitions and validation.
- Backend uses those definitions to validate or generate report block structure.
- Frontend consumes canonical recipe IDs and a typed/read-only representation of the contract.
- Frontend may maintain a renderer registry keyed by `semanticName`, but it should not define the canonical report product order independently.

Why not frontend-only:

- Backend already owns report creation, order, metric binding, persistence, and version serialization.
- A frontend-only Recipe would not protect backend-generated `ReportBlock` output.

Why not fully shared package first:

- The repo currently has backend builders in Python and no evidence of an existing shared schema package for report recipes.
- V1 should be deterministic and low complexity.

# Recipe Storage Recommendation

Recommended V1 storage: CODE.

Concretely: Python constants/config in backend code, with tests validating builder output against them.

Rationale:

- deterministic;
- version-controlled;
- easy to test;
- no admin UI needed;
- no migrations;
- no database runtime complexity;
- easy to migrate later to JSON/shared schema if needed.

Recipe does not need database persistence for Facebook Pages 5 V1: NO.

Future DB persistence may be appropriate for custom user templates, saved recipes, or AI-generated blueprints, but that is not required for the current product-level Facebook Pages 5 Recipe.

# Recipe → Block Generation Strategy

Conceptual future path:

```text
Recipe
+
Dataset / Report Inputs
-> Block Builder / Factory
-> ReportBlock[]
```

Current code can evolve incrementally without rewriting the report generation pipeline.

Most natural migration point:

- Start at `build_blocks(...)` / `build_5_blocks(...)`.
- Extract the structural list from `build_5_blocks(...)` into a canonical Recipe constant.
- Keep existing payload functions unchanged:
  - cover payload logic;
  - `_build_facebook_pages_metric_slide_payload(...)`;
  - `_build_five_slide_summary_payload(...)`;
  - `_meta_enrich_data_blocks(...)`.

Suggested shape of migration:

1. Define canonical Recipe V1 for Facebook Pages 5.
2. Validate current `build_5_blocks(...)` output against Recipe after enrichment and enforcement.
3. Expose Recipe metadata to Report Lab/frontend.
4. Add an internal factory mapping `semanticName -> existing payload builder`.
5. Let `build_5_blocks(...)` iterate Recipe slides while still using current data functions.
6. Later remove duplicated hardcoded order from enforcement and builder.

# Recipe vs Blueprint

The frontend separation works for backend:

- Recipe = stable reusable report-product structure.
- Blueprint = future instance-specific report plan.

Recipe should contain:

- `id`;
- `platform`;
- `name`;
- `version`;
- ordered `slides`;
- `semanticName` per slide.

Blueprint may contain:

- `baseRecipeId`;
- `baseRecipeVersion`;
- selected sources;
- ordered slide plan;
- semantic metric binding when dynamic/custom;
- source binding per slide when multi-source;
- optional layout variant identifier;
- planner rationale for audit/debug.

Blueprint should not contain:

- metric values;
- generated insight text as structural definition;
- JSX;
- CSS;
- renderer internals;
- access tokens or secrets.

Slide kind should be resolved elsewhere, not stored in Recipe V1. For Blueprint, kind can also be resolved from `semanticName` unless AI planning creates new dynamic semantic names.

# Multi-source Evolution

`platform: "facebook_pages"` does not block Recipe V1.

For V1, Facebook Pages 5 is a single-source product recipe. Future multi-source products can evolve by adding new recipe IDs and/or a broader platform/report product field, for example:

```text
facebook_pages_5
meta_ads_5
shopify_5
multi_source_social_10
paid_social_plus_commerce_10
```

Do not add `sources[]` to Facebook Pages 5 Recipe V1.

Future source selection belongs better in Blueprint because it is instance-specific:

- which accounts were selected;
- which providers are bound;
- which dataset/integration powers each slide;
- how cross-source metrics are merged.

# AI Planner Boundary

Conceptual future flow:

```text
Available Sources + Metrics
-> AI Planner
-> Blueprint
-> Block Generation
-> Renderer
```

AI Planner should receive:

- available integrations/sources;
- provider/account labels safe for planning;
- metric catalog entries and statuses;
- permissions/availability summaries;
- timeframe;
- report goal or selected report product;
- plan/slide count constraints;
- supported Recipe IDs and semantic names;
- source metric capabilities;
- prior block contract rules.

AI Planner should not receive:

- JSX;
- CSS;
- renderer component internals;
- access tokens;
- secrets;
- raw private comments or identities;
- arbitrary full datasets when aggregate/normalized metrics are enough;
- instructions to invent unavailable metric values.

Planner output should be a Blueprint, not rendered UI.

# AI Insight Boundary

Current Facebook Pages 5 AI-labeled content is deterministic in the official slide path:

- Slide 2-4 insights: `build_metric_ai_insight(...)`.
- Slide 5 summary/recommendation: `build_final_ai_summary(...)`.

`generate_meta_pages_ai_summary(...)` can call Anthropic when configured, but its returned summary is not the official Slide 5 content in the audited Facebook Pages 5 path.

Recipe must not contain generated insight text.

Blueprint should not use generated insight text as structural definition.

Future generated insight content should live in analysis output and then in report instance data, likely:

- `ReportBlock.data_json.insight`;
- `ReportBlock.data_json.insight_short`;
- `ReportBlock.data_json.ai_summary`;
- `ReportBlock.data_json.recommendation`;
- a future analysis snapshot if reproducibility requires provider/model/prompt metadata.

# Migration Strategy

Incremental path:

1. Define canonical Facebook Pages 5 Recipe V1 in backend code.
2. Add validation that current official Facebook Pages 5 output matches Recipe after `_ensure_facebook_pages_five_slide_structure(...)`.
3. Let Report Lab/frontend consume the canonical recipe contract or a generated mirror.
4. Add a backend block factory that maps `semanticName` to existing payload functions.
5. Update `build_5_blocks(...)` internally to iterate Recipe while preserving output.
6. Convert `_ensure_facebook_pages_five_slide_structure(...)` into a validator/correction layer against Recipe.
7. Move duplicated frontend/backend presentation aliases out of source metric selection.
8. Later repeat for Meta Ads, Shopify, multi-source, 10/15/30 social builders.

Do not start by changing persistence or schemas. The current `ReportBlock` contract can support Recipe-backed generation without database changes.

# Recommended Facebook 5 Recipe

Recommended exact V1 structure:

```json
{
  "id": "facebook_pages_5",
  "platform": "facebook_pages",
  "name": "Facebook Pages - 5 Slides",
  "version": 1,
  "slides": [
    { "order": 1, "semanticName": "cover" },
    { "order": 2, "semanticName": "organic_impressions_overview" },
    { "order": 3, "semanticName": "engagement_overview" },
    { "order": 4, "semanticName": "page_views_overview" },
    { "order": 5, "semanticName": "executive_summary" }
  ]
}
```

Do not include:

- user data;
- metric values;
- generated insight text;
- visual layout;
- CSS;
- renderer component names;
- source account bindings;
- slide count.

# Validation Rules

Minimum validation rules:

- Recipe `id` is non-empty and unique.
- `version` is a positive integer.
- `platform` is non-empty and supported.
- `slides` has at least one item.
- `slides[].order` is unique.
- `slides[].order` is contiguous for fixed-count product recipes.
- `slides[].semanticName` is non-empty.
- `slides[].semanticName` is supported by backend registry/factory for that recipe.
- Duplicate semantic names are rejected unless explicitly supported.
- Generated blocks must match Recipe order and semantic names.
- Official Facebook Pages 5 generated blocks must have exactly five slides.

# Answers to Frontend Audit Questions

1. Where is the Facebook Pages 5 block list generated today?

   In `build_5_blocks(...)`, selected by `build_blocks(...)` for requested slide counts `<= 5`. The official Facebook Pages endpoint reaches this through `create_meta_pages_report(...)` and `_create_meta_dataset_report(...)`.

2. Does backend own `slide_number` / `order` / both?

   Backend owns both. `slide_number` is inside `data_json`; `order` is top-level `ReportBlock.order`. `_ensure_facebook_pages_five_slide_structure(...)` aligns both for official Facebook Pages 5.

3. Is `semantic_name` guaranteed for all Facebook 5 blocks?

   YES for persisted official Facebook Pages 5 blocks after enforcement, assuming valid JSON block data. It is not guaranteed by a database constraint because it lives inside `data_json`.

4. Can `slide_type` conflict with `semantic_name`?

   Theoretically YES before enforcement or in other builders. For official persisted Facebook Pages 5, enforcement overwrites both to the same expected semantic string.

5. Is `rawData` always serialized from data?

   Backend does not expose a separate `rawData` field. It stores and returns `ReportBlock.data_json` as a JSON string created from block data by `_meta_report_block(...)`; `_report_block_out(...)` parses and re-serializes it, injecting cover branding when needed.

6. Which service chooses metric blocks and aliases?

   Backend chooses them. `build_5_blocks(...)` chooses the three metric blocks; `_build_facebook_pages_metric_slide_payload(...)`, `report_metric_catalog.py`, `METRIC_ALIASES`, `_extract_daily_metric_series_details(...)`, and `_resolve_metric_total_details(...)` resolve source metric bindings and aliases.

7. Which service produces Executive Summary cards and Top Content?

   `_build_five_slide_summary_payload(...)` produces summary cards and calls `_rank_facebook_page_top_content(...)` over `_meta_top_content(...)`. The top content data is normalized by `extract_meta_pages_report_inputs(...)` and `normalize_meta_top_content(...)`.

8. Can backend emit stable `recipe_id` metadata?

   YES conceptually. The current code does not emit `recipe_id`, but `Report.description` already stores generation metadata such as source, locale, timeframe, slide limits, generation mode, plan, branding, and AI agent metadata. A stable `recipe_id` / `recipe_version` could be added later without changing block persistence.

# Risks

- `data_json` mixes composition, content, metrics, visual metadata, source metadata, AI-labeled text, and legacy compatibility fields.
- `semantic_name` is stable by convention and enforcement, not by database schema.
- `_ensure_facebook_pages_five_slide_structure(...)` can hide upstream builder mistakes by rewriting by position.
- Frontend metric config can diverge from backend if it duplicates metric aliases or source metric bindings.
- Current `ai_*` field names are misleading because official Facebook Pages 5 insight text is deterministic.
- Recipe migration must preserve exact `ReportBlock.data_json` keys that current renderers and tests expect.
- Facebook Pages reports currently infer source provenance mostly from dataset/report metadata, not `ReportSource` rows.
- Future AI Planner must not invent unavailable metrics or replace backend availability semantics.

# Recommended Next Step

Define the canonical Facebook Pages 5 Recipe V1 in backend code and add a documentation/test-only validation target that compares current official `build_5_blocks(...)` plus enforcement output against that Recipe.

Do not change production generation yet.
