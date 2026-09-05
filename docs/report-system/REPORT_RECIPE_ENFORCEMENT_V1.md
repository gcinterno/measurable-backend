# Status

IMPLEMENTED — FACEBOOK PAGES 5 ONLY

# Production Call Path

Official Facebook Pages report creation currently flows through:

```text
POST /reports/meta-pages
-> app/main.py:create_meta_pages_report(...)
-> app/main.py:_create_meta_dataset_report(...)
-> app/main.py:extract_meta_pages_report_inputs(...)
-> app/main.py:build_blocks(...)
-> app/main.py:build_5_blocks(...)
-> app/main.py:_ensure_facebook_pages_five_slide_structure(...)
-> app/main.py:_enforce_facebook_pages_5_recipe(...)
-> app/report_recipe_enforcement.py:enforce_report_recipe(...)
-> app/report_recipe_validation.py:validate_blocks_against_recipe(...)
-> app/main.py ReportBlock(...) persistence
```

`build_blocks(requested_slides, dataset)` still routes `requested_slides <= 5` to `build_5_blocks(dataset)`.

# Enforcement Location

Recipe enforcement happens in `app/main.py` inside `_create_meta_dataset_report(...)`, after the existing Facebook Pages 5 structure normalization and before `ReportBlock` rows are constructed.

The guard is:

```text
report_source == "meta_pages_v2"
integration_type in {"facebook_pages", "meta_pages"}
effective_slide_limit == 5
```

# Scope

Included:

- Facebook Pages / Meta Pages official 5-slide report only

Excluded:

- 10-slide reports
- 15-slide reports
- 30-slide reports
- Instagram Business
- Meta Ads
- Shopify
- multi-source reports
- legacy reports

# Recipe Source

The enforcement uses the canonical backend Recipe:

```text
app/report_recipes.py:FACEBOOK_PAGES_5_RECIPE
```

The expected structure is not redefined in the enforcement hook.

# Builder Source

`app/main.py:build_5_blocks(...)` remains the builder for the official five-slide content. It still creates the report block payloads, metrics, daily series, AI insight text, top content, and summary content exactly as before.

# Validation Behavior

`app/report_recipe_enforcement.py:enforce_report_recipe(blocks, recipe)` calls:

```text
validate_blocks_against_recipe(blocks, recipe)
```

It validates:

- slide count
- top-level `order`
- `semantic_name`

It intentionally does not validate:

- `data_json` metric values
- AI text
- chart data
- captions
- top content
- timeframe
- visual config
- block type
- `slide_number`

# Mutation Behavior

When valid, `enforce_report_recipe(...)` returns the original block collection unchanged.

It does not:

- mutate blocks
- reorder blocks
- rewrite semantic names
- inject fallback slides
- modify `data_json`

# Failure Behavior

Invalid structure raises `ReportRecipeEnforcementError`, which includes:

- recipe id
- expected order / semantic names
- actual order / semantic names
- validation errors

The production Facebook Pages hook logs that internal payload and returns an API-safe structured HTTP 500:

```text
code: facebook_pages_recipe_enforcement_failed
message: Facebook Pages report structure does not match the canonical Recipe.
```

No internal stack trace is intentionally exposed to API consumers.

# Production Behavior

Valid Facebook Pages 5 reports should have no visible behavior change.

The enforcement layer is a fail-fast structural guard only. It does not change the generated block payloads or persisted `ReportBlock.data_json`.

# Next Migration Step

Use this enforcement layer as the first production guard while keeping `build_5_blocks(...)` unchanged. A later phase can move the structural portion of `build_5_blocks(...)` toward iterating the canonical Recipe after output equivalence is protected by tests.
