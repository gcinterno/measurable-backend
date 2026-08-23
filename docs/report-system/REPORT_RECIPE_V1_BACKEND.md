# Status

IMPLEMENTED — NOT USED BY PRODUCTION

# Canonical Recipe

`facebook_pages_5`

# Contract

Root fields:

- `id`
- `platform`
- `name`
- `version`
- `slides`

Slide fields:

- Python: `order`, `semantic_name`
- Wire/frontend representation: `order`, `semanticName`

# Canonical Slides

| Order | semantic_name |
| ---: | --- |
| 1 | `cover` |
| 2 | `organic_impressions_overview` |
| 3 | `engagement_overview` |
| 4 | `page_views_overview` |
| 5 | `executive_summary` |

# Ownership

Backend is the canonical source for Recipe V1. Frontend should consume or mirror this contract as typed, read-only data in a later phase.

# Non-Responsibilities

Recipe V1 does not store:

- metric values
- daily series
- comparisons or growth
- followers, top content, page metadata, or timeframe
- AI summary or AI insight text
- visual config, layout, chart type, component names, JSX, CSS, colors, or fonts
- SlideKind
- slideCount
- dynamic source bindings

# Production Wiring

NONE

`app/report_recipes.py` is importable but is not imported by `app/main.py`, `build_blocks(...)`, `build_5_blocks(...)`, persistence, or API response serialization.

# Next Step

Validate current `build_5_blocks(...)` output against `FACEBOOK_PAGES_5_RECIPE` without changing report generation behavior.
