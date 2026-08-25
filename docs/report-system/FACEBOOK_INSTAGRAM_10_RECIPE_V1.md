# Facebook + Instagram 10 Recipe V1

# Status

Facebook + Instagram 10 Recipe V1: COMPLETE

Parallel Recipe Builder: COMPLETE

Payload Parity: PASS

Production Cutover: PENDING

# Canonical Recipe

```text
id: facebook_instagram_10
platform: multi_source
name: Facebook + Instagram · 10 Slides
version: 1
```

Slides:

| Order | semantic_name |
| ---: | --- |
| 1 | `cover` |
| 2 | `reach` |
| 3 | `impressions` |
| 4 | `engagement` |
| 5 | `page_visits` |
| 6 | `audience_growth` |
| 7 | `content_activity` |
| 8 | `top_performing_content` |
| 9 | `executive_insights` |
| 10 | `recommendations` |

The Recipe stores only product structure: `id`, `platform`, `name`, `version`,
and `slides[].order` / `slides[].semantic_name`.

It does not store derived slide count, metric values, source data, account
information, AI text, chart data, timeframe, branding, layout, JSX, CSS, or
ReportBlock payload fields.

# Catalog

The backend Recipe catalog now registers both:

- `facebook_pages_5`
- `facebook_instagram_10`

Existing accessors remain the catalog boundary:

- `get_report_recipe(...)`
- `list_report_recipes(...)`
- `get_report_recipes_for_platform(...)`

# Validation

`facebook_instagram_10` uses the existing Recipe validation architecture plus a
strict product contract validator.

The builder fails closed for:

- missing Recipe
- wrong id
- wrong platform
- invalid version
- wrong slide count
- missing slide
- duplicate order
- non-contiguous order
- duplicate semantic
- unsupported semantic
- missing semantic handler
- malformed Recipe

# Parallel Builder

The parallel builder entry point is:

```text
build_facebook_instagram_10_blocks_from_recipe(recipe, context)
```

The Recipe controls only which slides exist, their order, and their semantic
identity.

The existing multi-source business logic still controls normalized source data,
metric totals, merged time series, engagement calculations, page/profile visits,
audience growth, content activity, top content ranking, strongest/weakest
platform analysis, executive insights, recommendations, branding, timeframe,
titles, `editable_fields_json`, and `data_json`.

The current production builder and the Recipe builder share the same extracted
multi-source 10-slide block construction helpers.

# Semantic Dispatcher

The parallel builder uses explicit handlers for:

- `cover`
- `reach`
- `impressions`
- `engagement`
- `page_visits`
- `audience_growth`
- `content_activity`
- `top_performing_content`
- `executive_insights`
- `recommendations`

Unknown semantics fail closed. There is no fallback to another slide.

# Payload Parity

The parity test compares the current builder:

```text
_multi_source_build_10_blocks(...)
```

against the parallel Recipe builder:

```text
build_facebook_instagram_10_blocks_from_recipe(...)
```

The comparison uses the same canonical context and checks raw block equality,
including count, order, type, `editable_fields_json`, and full `data_json`.

Result:

```text
FULL PAYLOAD PARITY: PASS
```

# Production Boundary

Production still uses:

```text
_multi_source_build_10_blocks(...)
```

No cutover was made.

No feature flag was added.

No `POST /reports/multi-source` behavior was changed.

No `ReportBlock` schema or `data_json` contract was changed.

No database/schema migration was added.

No PDF/export code was changed.
