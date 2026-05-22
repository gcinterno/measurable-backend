# Backend Update: 5-Slides Metrics Structure

## Why Impressions Was Removed

The Meta metrics audit showed that `impressions` is not a reliable source for the official 5-slide social report right now:

- Facebook Pages currently persists an impressions daily series, but the stored total is inconsistent or mapped to `0` in audited datasets.
- Instagram Business did not return impressions reliably with the current permissions and metric setup.
- Keeping impressions as an official slide would push the report toward a misleading metric instead of a trustworthy one.

For that reason, the official 5-slide structure now prioritizes metrics that are actually available and usable in the current Meta data flow.

## New Official Structure

The official `build_5_blocks()` structure is now:

1. Cover
2. Reach
3. Engagement
4. Page Views
5. Final Summary

No new builder was introduced. The update keeps using:

- `build_5_blocks()`
- `resolve_report_branding()`
- `extractDailyMetricSeries()`
- `build_metric_ai_insight()`
- `build_final_ai_summary()`

## Metric Used By Each Slide

### Slide 2: Reach

- Metric key: `reach`
- Total resolution:
  - direct `reach`
  - Meta aliases already supported by the official resolver
- Daily series:
  - `reach_daily`
  - official aliases inside `dataset.data`, `normalized_report_metrics`, nested daily containers, and `report_inputs`
- Extra payload:
  - `highest_day`
  - `lowest_day`
  - `insight_short`
  - `insight`

### Slide 3: Engagement

- Metric key: `engagement`
- Total resolution order:
  - direct engagement-like values such as `engagement`, `engagements`, `interactions`, `total_interactions`, `post_engagements`, `content_interactions`
  - if direct engagement is unavailable, calculate from components:
    - `reactions`
    - `likes`
    - `comments`
    - `shares`
    - `saves`
    - `link_clicks`
- Explicit non-rule:
  - no fallback from `reach`
  - no fallback from `impressions`
- Daily series:
  - `daily_engagement`
  - `engagement_daily`
  - `content_interactions_daily`
  - `interactions_daily`

### Slide 4: Page Views

- Metric key: `page_views`
- Total resolution order prioritizes page-view style fields:
  - `page_views`
  - `page_views_total`
  - `page_visits`
  - `page_visits_total`
  - `views`
  - `views_total`
  - `profile_views`
  - `profile_visits`
  - `page_views_login`
  - `page_views_logout`
  - `profile_activity`
- Daily series:
  - `page_views_daily`
  - `page_visits_daily`
  - `profile_views_daily`
  - `views_daily`
- Explicit non-rule:
  - no fallback from `reach_daily`
  - no fallback from `impressions_daily`

For Facebook Pages this uses the persisted page-view family fields and daily series where present.

For Instagram Business, when page/profile view metrics are not returned by Meta under current permissions, the slide resolves to:

- `total = null`
- `formatted_total = "N/A"`
- `is_available = false`
- standard Meta unavailable message

## Final Summary

Slide 5 now contains exactly four summary cards:

- Reach
- Engagement
- Followers
- Page Views

Impressions is intentionally excluded from the official 5-slide summary.

The summary cards only expose renderable primitives:

- `label`
- `value`
- `formatted_value`
- `is_available`
- `description`

No raw blocks or nested payload objects are included inside `metrics_summary`.

## AI Insight Behavior

The official metric insight flow remains the same, but the metric mix changed:

- Reach insight interprets visibility and daily traction.
- Engagement insight interprets interaction and highlights the peak day.
- Page Views insight interprets visit intent and ties peaks to conversion opportunity.
- Final summary interprets Reach, Engagement, Followers, and Page Views only.

When a metric is unavailable, the insight remains soft and explicitly states that the data is not available with current Meta permissions.

## Daily Series Resolution

`extractDailyMetricSeries()` remains the source of truth.

It now officially resolves daily series for:

- `reach`
- `engagement`
- `page_views`

It still searches the same official storage layers:

- `dataset.data`
- `normalized_report_metrics`
- `report_metric_mapping`
- nested daily containers such as `daily`, `daily_metrics`, `daily_series`, `time_series`, `metric_values`, `values`, `data`, and `breakdowns`

Zero-valued real series are preserved. No synthetic daily series is invented.

## Tests Executed

- `poetry run python -m py_compile app/main.py scripts/audit_meta_metrics_availability.py tests/test_five_slide_metric_payload.py`
- `poetry run pytest tests/test_five_slide_metric_payload.py -q`
- `poetry run pytest tests/test_branding_gating.py -q`

## Pending Risks

- Facebook Pages still shows audited inconsistencies where some stored totals do not match the sum of persisted daily series.
- `page_views` persistence can still differ between `views_total` and `page_visits_total` depending on the dataset version and sync path.
- Instagram Business still depends on Meta permission/model behavior for `views`, `profile_views`, `website_clicks`, and some engagement metrics that may require `metric_type=total_value` or may be unavailable.
- The official 5-slide report no longer depends on impressions, but legacy impressions logic still exists elsewhere in the backend and should be cleaned only after confirming there are no remaining consumers.
