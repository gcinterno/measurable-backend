# Status

IMPLEMENTED — NOT WIRED TO PRODUCTION GENERATION

# Backend Ownership

Backend is the canonical owner of Report Recipes.

Recipe means stable, reusable report-product structure. It does not contain user data, metric values, AI insight text, charts, captions, timeframe, visual template, JSX, CSS, or fixture data.

# Purpose

The Recipe Catalog gives backend a deterministic place to register canonical report Recipes.

Current catalog:

```text
REPORT_RECIPES = {
    "facebook_pages_5": FACEBOOK_PAGES_5_RECIPE,
}
```

The catalog is code-based, version-controlled, and read-only by convention through a mapping proxy. It does not require database persistence.

# Current Registered Recipes

| Recipe ID | Platform | Name | Version | Derived slide count |
| --- | --- | --- | ---: | ---: |
| `facebook_pages_5` | `facebook_pages` | `Facebook Pages · 5 Slides` | 1 | 5 |

Canonical slides:

| Order | semantic_name |
| ---: | --- |
| 1 | `cover` |
| 2 | `organic_impressions_overview` |
| 3 | `engagement_overview` |
| 4 | `page_views_overview` |
| 5 | `executive_summary` |

# Registration Process

To register a future Recipe:

1. Define a frozen `ReportRecipe` constant.
2. Add it to `_REGISTERED_REPORT_RECIPES`.
3. Keep slide count derived with `len(recipe.slides)`.
4. Do not add metric values, data, AI text, visual config, SlideKind, or dynamic source bindings.
5. Add focused tests for lookup, listing, platform filtering, and catalog invariants.

# Invariants

The catalog validates:

- duplicate recipe id
- empty recipe id
- empty platform
- empty name
- invalid version
- zero slides
- duplicate slide order
- duplicate `semantic_name` inside the same Recipe
- non-contiguous order
- empty `semantic_name`

# Recipe vs Fixture

A Recipe is structure only.

A fixture contains sample or test data. Fixtures may include metric values, daily series, captions, or rendered examples. Those do not belong in the Recipe Catalog.

# Recipe vs Slide Registry

Recipe stores ordered `semantic_name` values.

Slide Registry resolves:

```text
semantic_name -> slide kind / renderer behavior
```

Recipe V1 does not store SlideKind.

# Recipe vs Template

Recipe describes product structure.

Template/renderer systems describe visual presentation, layout, components, colors, charts, and canvas behavior. Those do not belong in Recipe V1.

# Recipe vs Blueprint

Recipe is reusable and product-level.

Blueprint is future instance-specific planning. A Blueprint may later bind sources, metrics, layout variants, planner rationale, and a base Recipe ID/version for a specific generated report.

# Future Architecture

```text
Recipe Catalog
      ↓
Recipe
      ↓
Blueprint / Report Instance
      ↓
Blocks
      ↓
Slide Registry
      ↓
Template / Renderer
```

This phase does not implement Blueprint.

# Production Wiring

NONE

The catalog is importable and validated at module import, but report generation still uses the existing builders. No endpoint, persistence, or report generation path consumes the catalog yet.
