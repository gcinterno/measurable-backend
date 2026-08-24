# Status

IMPLEMENTED — READ-ONLY API FOUNDATION

# Ownership

Backend remains the canonical owner of Report Recipes.

The API serializes the code-defined Recipe Catalog for future Report Lab consumers. It does not make frontend a source of truth and does not expose report data, datasets, AI insight text, fixture data, renderer config, JSX, or CSS.

# Endpoints

## List Recipes

```text
GET /report-recipes
```

Returns every registered Recipe in deterministic catalog order.

Optional platform filter:

```text
GET /report-recipes?platform=facebook_pages
```

The platform filter is implemented with `get_report_recipes_for_platform(platform)`.

## Detail

```text
GET /report-recipes/{recipe_id}
```

Returns one Recipe by ID using `get_report_recipe(recipe_id)`.

# Authentication Scope

AUTHENTICATED

The endpoint follows the app's existing authenticated product API pattern with `get_current_user`. No new auth system, admin scope, workspace lookup, or database-backed Recipe permission model was introduced.

# Response Contract

Each Recipe response contains:

```json
{
  "id": "facebook_pages_5",
  "platform": "facebook_pages",
  "name": "Facebook Pages · 5 Slides",
  "version": 1,
  "slide_count": 5,
  "slides": [
    {
      "order": 1,
      "semantic_name": "cover"
    },
    {
      "order": 2,
      "semantic_name": "organic_impressions_overview"
    },
    {
      "order": 3,
      "semantic_name": "engagement_overview"
    },
    {
      "order": 4,
      "semantic_name": "page_views_overview"
    },
    {
      "order": 5,
      "semantic_name": "executive_summary"
    }
  ]
}
```

`slide_count` is derived from `len(recipe.slides)`. It is response metadata, not a stored Recipe field or source of truth.

# 404 Behavior

Unknown Recipe IDs return:

```json
{
  "detail": {
    "code": "recipe_not_found",
    "message": "Report recipe not found."
  }
}
```

Status code: `404`

# Read-only Nature

Only `GET` endpoints exist.

No `POST`, `PUT`, `PATCH`, or `DELETE` endpoint was added. Recipes V1 remain code-defined and database-free.

# Intended Frontend Consumer

Report Lab can use this API later to read the backend-owned Recipe Catalog instead of maintaining a manual frontend copy.

This phase does not modify frontend code.

# Rendering Boundary

The API does not own rendering. It exposes reusable Recipe structure only:

```text
Recipe Catalog
      ↓
serialized Recipe metadata
      ↓
frontend consumers / Report Lab
      ↓
Slide Registry / Template / Renderer
```

Slide kind, component selection, visual layout, chart styling, JSX, and CSS remain outside the Recipe Catalog API.

# Production Wiring

NONE

Report generation still uses the existing builders. `build_5_blocks`, `build_blocks`, report creation endpoints, dataset processing, AI insight generation, Meta Ads, and Instagram integrations were not wired to this API.
