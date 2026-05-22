# META Metrics Availability Audit

## Resumen Ejecutivo
- Generated at: 2026-05-22T21:08:36.140337+00:00
- Scope mode: latest_workspace_from_matching_dataset
- Workspace id: 1
- Requested user id: None
- Dataset count audited: 5
- Live audit enabled: True

## Facebook Pages
- followers: available; value=4,575; source_path=dataset.data.followers; origin=direct_saved_dataset_field
- reach: available; value=180,294; source_path=dataset.data.reach; origin=direct_saved_dataset_field, daily_series=22 points
- impressions: available; value=0; source_path=sum(dataset.data.impressions_daily); origin=unknown, daily_series=22 points
- engagement: available; value=4,965; source_path=dataset.data.engagement; origin=direct_saved_dataset_field, daily_series=22 points
- interactions: available; value=4,965; source_path=dataset.data.content_interactions; origin=direct_saved_dataset_field, daily_series=22 points
- link_clicks: available; value=0; source_path=sum(dataset.data.normalized_report_metrics.link_clicks_daily); origin=normalized_or_calculated_by_backend, daily_series=22 points
- page_views: available; value=0; source_path=sum(dataset.data.normalized_report_metrics.page_visits_daily); origin=normalized_or_calculated_by_backend, daily_series=22 points
- profile_views: available; value=6,092; source_path=report_inputs.profile_visits; origin=derived_report_input
- website_clicks: unavailable; value=null; source_path=None; origin=unknown
- video_views: available; value=6,092; source_path=dataset.data.normalized_report_metrics.views_total; origin=normalized_or_calculated_by_backend, daily_series=22 points
- daily_reach: available; value=null; source_path=None; origin=direct_saved_dataset_field, daily_series=22 points
- daily_impressions: available; value=null; source_path=None; origin=direct_saved_dataset_field, daily_series=22 points
- daily_engagement: available; value=null; source_path=None; origin=normalized_or_calculated_by_backend, daily_series=22 points
- daily_interactions: available; value=null; source_path=None; origin=normalized_or_calculated_by_backend, daily_series=22 points

Warnings:
- WARNING: total does not match sum(daily_series). metric = reach; total = 180294; sum(daily_series) = 187575; source_path = dataset.data.reach; daily_source_path = dataset.data.reach_daily
- WARNING: total does not match sum(daily_series). metric = impressions; total = 0; sum(daily_series) = 6092; source_path = sum(dataset.data.impressions_daily); daily_source_path = dataset.data.impressions_daily
- WARNING: total does not match sum(daily_series). metric = engagement; total = 4965; sum(daily_series) = 5156; source_path = dataset.data.engagement; daily_source_path = report_inputs.daily_engagement

## Instagram Business
- followers: available; value=7,827; source_path=dataset.data.followers; origin=direct_saved_dataset_field
- reach: available; value=97,072; source_path=dataset.data.reach; origin=direct_saved_dataset_field, daily_series=21 points
- impressions: unavailable; value=null; source_path=None; origin=unknown
- engagement: unavailable; value=null; source_path=None; origin=unknown
- interactions: unavailable; value=null; source_path=None; origin=unknown
- link_clicks: unavailable; value=null; source_path=None; origin=unknown
- page_views: unavailable; value=null; source_path=None; origin=unknown
- profile_views: unavailable; value=null; source_path=None; origin=unknown
- website_clicks: unavailable; value=null; source_path=None; origin=unknown
- video_views: unavailable; value=null; source_path=None; origin=unknown
- daily_reach: available; value=null; source_path=None; origin=direct_saved_dataset_field, daily_series=21 points
- daily_impressions: unavailable; value=null; source_path=None; origin=unknown
- daily_engagement: unavailable; value=null; source_path=None; origin=unknown
- daily_interactions: unavailable; value=null; source_path=None; origin=unknown

## Métricas Con Daily Series
- facebook_pages:
  - reach: 22 points (2026-05-01 -> 2026-05-22)
  - impressions: 22 points (2026-05-01 -> 2026-05-22)
  - engagement: 22 points (2026-05-01 -> 2026-05-22)
  - interactions: 22 points (2026-05-01 -> 2026-05-22)
  - link_clicks: 22 points (2026-05-01 -> 2026-05-22)
  - page_views: 22 points (2026-05-01 -> 2026-05-22)
  - video_views: 22 points (2026-05-01 -> 2026-05-22)
  - daily_reach: 22 points (2026-05-01 -> 2026-05-22)
  - daily_impressions: 22 points (2026-05-01 -> 2026-05-22)
  - daily_engagement: 22 points (2026-05-01 -> 2026-05-22)
  - daily_interactions: 22 points (2026-05-01 -> 2026-05-22)
  - daily_followers: 22 points (2026-05-01 -> 2026-05-22)
  - daily_page_views: 22 points (2026-05-01 -> 2026-05-22)
- instagram_business:
  - reach: 21 points (2026-05-01 -> 2026-05-21)
  - daily_reach: 21 points (2026-05-01 -> 2026-05-21)

## Métricas No Disponibles
- facebook_pages: fans, page_fans, reactions, likes, comments, shares, saves, website_clicks, daily_profile_views
- instagram_business: fans, page_fans, impressions, engagement, interactions, reactions, likes, comments, shares, saves, link_clicks, page_views, profile_views, website_clicks, video_views, daily_impressions, daily_engagement, daily_interactions, daily_followers, daily_profile_views, daily_page_views

## Posibles Problemas de Permisos
- none detected or live audit not executed

## Posibles Errores de Mapping
- WARNING: total does not match sum(daily_series). metric = reach; total = 180294; sum(daily_series) = 187575; source_path = dataset.data.reach; daily_source_path = dataset.data.reach_daily
- WARNING: total does not match sum(daily_series). metric = impressions; total = 0; sum(daily_series) = 6092; source_path = sum(dataset.data.impressions_daily); daily_source_path = dataset.data.impressions_daily
- WARNING: total does not match sum(daily_series). metric = engagement; total = 4965; sum(daily_series) = 5156; source_path = dataset.data.engagement; daily_source_path = report_inputs.daily_engagement
- WARNING: total does not match sum(daily_series). metric = reach; total = 180288; sum(daily_series) = 187569; source_path = dataset.data.reach; daily_source_path = dataset.data.reach_daily
- WARNING: total does not match sum(daily_series). metric = impressions; total = 0; sum(daily_series) = 6092; source_path = sum(dataset.data.impressions_daily); daily_source_path = dataset.data.impressions_daily
- WARNING: total does not match sum(daily_series). metric = engagement; total = 4965; sum(daily_series) = 5156; source_path = dataset.data.engagement; daily_source_path = report_inputs.daily_engagement
- WARNING: total does not match sum(daily_series). metric = reach; total = 180288; sum(daily_series) = 187569; source_path = dataset.data.reach; daily_source_path = dataset.data.reach_daily
- WARNING: total does not match sum(daily_series). metric = impressions; total = 0; sum(daily_series) = 6092; source_path = sum(dataset.data.impressions_daily); daily_source_path = dataset.data.impressions_daily
- WARNING: total does not match sum(daily_series). metric = engagement; total = 4965; sum(daily_series) = 5156; source_path = dataset.data.engagement; daily_source_path = report_inputs.daily_engagement
- WARNING: total does not match sum(daily_series). metric = reach; total = 44860; sum(daily_series) = 53908; source_path = dataset.data.reach; daily_source_path = dataset.data.reach_daily
- WARNING: total does not match sum(daily_series). metric = impressions; total = 0; sum(daily_series) = 1367; source_path = sum(dataset.data.impressions_daily); daily_source_path = dataset.data.impressions_daily
- WARNING: total does not match sum(daily_series). metric = engagement; total = 1129; sum(daily_series) = 1386; source_path = dataset.data.engagement; daily_source_path = report_inputs.daily_engagement

## Datos Directos Vs Calculados
- `direct_saved_dataset_field`: valor persistido directamente en `dataset.data`.
- `normalized_or_calculated_by_backend`: valor persistido en `normalized_report_metrics` o `report_metric_mapping`.
- `calculated_by_backend`: valor derivado por esta auditoría, por ejemplo `post_count` o `sum(recent_posts)`.

## Recomendación Para Reporte 5 Slides
- Slide 2 Reach: Use `reach` from `dataset.data.reach`.
- Slide 3 Impressions: Use `impressions` from `sum(dataset.data.impressions_daily)`.
- Slide 4 Engagement: Use `engagement` from `dataset.data.engagement`.
- Mostrar `N/A` cuando la métrica no tenga valor real en el dataset ni respuesta válida de Meta.
