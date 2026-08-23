# Status

IMPLEMENTED — NOT WIRED TO PRODUCTION

# Purpose

Compare a canonical `ReportRecipe` with actual ReportBlock-like output or builder output.

The initial target is proving whether the current Facebook Pages 5 block structure matches `FACEBOOK_PAGES_5_RECIPE`.

# Validated Fields

- slide count
- top-level block `order`
- `semantic_name`

`order` is read from the same top-level field that becomes `ReportBlock.order`, because persisted API serialization sorts blocks by `ReportBlock.order`.

`semantic_name` is read from a direct block field when present, otherwise from `data_json.semantic_name` or `data_json.semanticName`.

# Not Validated

- `data_json` contents beyond `semantic_name`
- metric values
- daily series
- comparisons or growth
- AI insight text
- executive summary text
- captions
- top content
- timeframe
- chart data
- followers
- branding or visual config
- block type
- `slide_number`
- `slide_type`

# Validator API

Module:

```text
app/report_recipe_validation.py
```

Function:

```python
validate_blocks_against_recipe(blocks, recipe)
```

The function is pure:

- does not mutate blocks
- does not mutate Recipe
- does not query the database
- does not fetch external data
- does not call LLMs
- does not persist anything

# Validation Result

`RecipeValidationResult` fields:

- `valid`
- `expected_count`
- `actual_count`
- `errors`

`RecipeValidationError` fields:

- `code`
- `message`
- `block_index`
- `expected_order`
- `actual_order`
- `expected_semantic_name`
- `actual_semantic_name`

V1 error codes:

- `SLIDE_COUNT_MISMATCH`
- `ORDER_MISMATCH`
- `SEMANTIC_NAME_MISMATCH`
- `MISSING_ORDER`
- `MISSING_SEMANTIC_NAME`

# Facebook Pages 5 Validation

Canonical Recipe:

```text
facebook_pages_5
1 cover
2 organic_impressions_overview
3 engagement_overview
4 page_views_overview
5 executive_summary
```

Structural command results:

| Case | Result |
| --- | --- |
| Canonical matching sample | PASS |
| Wrong semantic | FAIL detected |
| Wrong order | FAIL detected |
| Missing slide | FAIL detected |

# Raw Builder Result

MATCH

`build_5_blocks(...)` output matches `FACEBOOK_PAGES_5_RECIPE` for slide count, top-level `order`, and `semantic_name`.

The comparison was run without external requests, credentials, database writes, persistence, or LLM calls. The system `python3` runtime could not import `app.main` because `requests` is not installed there, so the builder comparison used the existing Poetry environment.

# Enforced Final Result

MATCH

`_ensure_facebook_pages_five_slide_structure(...)` output also matches `FACEBOOK_PAGES_5_RECIPE` for slide count, top-level `order`, and `semantic_name`.

# Production Wiring

NONE

`validate_blocks_against_recipe(...)` is not imported by `app/main.py`, `build_blocks(...)`, `build_5_blocks(...)`, persistence, endpoints, or API response serialization.

# Next Step

Add a focused test or non-production validation script for Facebook Pages 5 recipe conformance once the project test environment is available.
