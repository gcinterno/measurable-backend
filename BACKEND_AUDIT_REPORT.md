# Backend Audit Report

## 1. Resumen ejecutivo

El backend actual funciona, pero la zona de mayor riesgo está concentrada en `app/main.py`, donde conviven responsabilidades de API, sync de Meta, normalización de datasets, builders de reportes, branding, exportación y endpoints de debug. No hay evidencia de un segundo sistema completo de reportes de 5 slides en producción, pero sí hay lógica histórica y caminos paralelos que aumentan la probabilidad de drift.

El reporte oficial de 5 slides ya tiene una implementación usable en `build_5_blocks()` y hoy ese debe considerarse el mejor candidato a source of truth. El problema no es la ausencia de un builder, sino que sigue coexistiendo con:

- builders más antiguos de 10/15/30 slides;
- un pool legacy de bloques Meta;
- branding resuelto en más de un lugar;
- extracción de daily series en varios niveles;
- distintas formas de normalizar Meta Pages vs Instagram Business.

Antes de refactorizar, conviene congelar el contrato oficial de:

- branding;
- daily metric series;
- slide payloads 1-5;
- export payload;
- sync-to-dataset normalization.

## 2. Mapa del backend

### Archivos principales

- `app/main.py`
  - archivo monolítico principal;
  - define la app FastAPI;
  - contiene handlers HTTP;
  - contiene builders de reportes;
  - contiene sync de Meta Pages e Instagram Business;
  - contiene helpers de daily series, AI insights y debug endpoints.
- `app/models.py`
  - modelos SQLAlchemy;
  - relaciones entre workspaces, reports, datasets, integrations, exports, schedules, jobs y referral tracking.
- `app/schemas.py`
  - contratos Pydantic para auth, workspaces, sync, reports, report versions, branding y exports.
- `app/services.py`
  - helpers de branding;
  - helpers de export;
  - normalización parcial de payloads Meta;
  - lógica de capacidades por plan.
- `app/deps.py`
  - dependencias de autenticación y compatibilidad de esquema;
  - contiene inspección dinámica de columnas para tolerar drift de migrations.
- `app/config.py`
  - configuración principal.
- `app/integrations/meta_ads.py`
  - integración Meta Ads específica;
  - no es el único punto Meta, porque buena parte del flujo social vive en `app/main.py`.
- `alembic/versions/*`
  - historial de migrations desde `20260308_000001_initial.py` hasta `20260516_000021_add_referral_tracking.py`.
- `tests/*`
  - cobertura parcial de auth, admin, branding, 5-slide metrics, Instagram sync, Meta sync-all, multi-source, referrals y delete de reports.

### Modelos SQLAlchemy relevantes

- `User`
- `Workspace`
- `WorkspaceMember`
- `Subscription`
- `Dataset`
- `DatasetFile`
- `Integration`
- `IntegrationAccount`
- `IntegrationToken`
- `MetaPage`
- `Report`
- `ReportVersion`
- `ReportSource`
- `ReportBlock`
- `Export`
- `Schedule`
- `Job`
- `Conversation`
- `Message`
- `AuditLog`
- `ReferralPartner`
- `ReferralClick`
- `UserAttribution`
- `ReferralConversion`
- `AccountDeletionFeedback`

### Schemas Pydantic relevantes

- `MeOut`
- `WorkspaceOut`
- `BrandingOut`
- `ReportCreateIn`
- `MetaPagesReportCreateIn`
- `InstagramBusinessReportCreateIn`
- `ReportOut`
- `ReportVersionOut`
- `ReportExportOut`
- `MetaPagesSyncOut`
- `InstagramBusinessSyncOut`
- `MetaSyncAllIn`
- `MetaSyncAllOut`
- `MultiSourceReportCreateRequest`

### Services/helpers principales

- Branding:
  - `resolve_report_branding()`
  - `resolve_workspace_branding()`
  - `resolve_report_branding_for_workspace()`
- Export:
  - `build_export_payload()`
  - `trigger_export_service()`
  - `generate_pdf_from_export_page()`
- Meta/report input shaping:
  - `extract_meta_pages_report_inputs()`
  - `build_meta_pages_reach_chart_data()`
  - `build_meta_pages_reach_insight()`

### Builders de reportes

- `build_5_blocks()`
- `build_10_blocks()`
- `build_15_blocks()`
- `build_30_blocks()`
- `build_blocks()`
- `_multi_source_build_10_blocks()`
- `_build_meta_report_block_pool()`

### Helpers de daily metrics / charts

- `extractDailyMetricSeries()`
- `_extract_daily_metric_series_details()`
- `_normalize_daily_series_result()`
- `_meta_metric_series()`
- `_meta_posts_daily_series()`
- `_expand_meta_daily_series()`
- `_sum_meta_daily_series()`
- `_meta_daily_series_bounds()`
- `build_meta_pages_reach_chart_data()`

### Sync helpers Meta

- `_run_meta_pages_sync()`
- `_run_instagram_business_sync()`
- `_resolve_meta_sync_all_integration()`
- `_execute_meta_source_sync()`

### Tests actuales

- `tests/test_admin.py`
- `tests/test_auth.py`
- `tests/test_branding_gating.py`
- `tests/test_five_slide_metric_payload.py`
- `tests/test_google_auth.py`
- `tests/test_instagram_business_sync.py`
- `tests/test_meta_report_attribution_resilience.py`
- `tests/test_meta_sync_all.py`
- `tests/test_multi_source_reports.py`
- `tests/test_referrals.py`
- `tests/test_reports_delete.py`

## 3. Auditoría de reportes de 5 slides

### Builders encontrados

Builders o caminos relevantes detectados:

- `build_5_blocks()` en `app/main.py`
- `build_10_blocks()` en `app/main.py`
- `build_15_blocks()` en `app/main.py`
- `build_30_blocks()` en `app/main.py`
- `_multi_source_build_10_blocks()` en `app/main.py`
- `_build_meta_report_block_pool()` en `app/main.py`

### Hallazgos

1. El builder oficial de 5 slides ya existe.
   - `build_5_blocks()` genera:
     - Slide 1 cover
     - Slide 2 reach
     - Slide 3 impressions
     - Slide 4 engagement
     - Slide 5 summary

2. No parece existir un builder exclusivo separado para Facebook Pages y otro para Instagram Business.
   - Ambos terminan convergiendo en un contexto/dataset que luego entra al sistema de bloques.
   - El riesgo no está en builders duplicados por plataforma, sino en datasets con formas distintas antes de converger.

3. Sí existe lógica paralela histórica.
   - `build_10_blocks()`, `build_15_blocks()` y `build_30_blocks()` siguen usando combinaciones distintas de contexto, charts y bloques.
   - `_build_meta_report_block_pool()` todavía actúa como fuente legacy de bloques Meta extensos.
   - `_multi_source_build_10_blocks()` mantiene otra ruta de composición.

4. Hay duplicación conceptual en métricas.
   - Reach/impressions/engagement pueden derivarse desde:
     - dataset directo;
     - `report_inputs`;
     - `normalized_report_metrics`;
     - `report_metric_mapping`;
     - bloques previos en contexto;
     - series por posts en fallback.

5. Hay más de una estructura de payload para reportes del mismo dominio.
   - 5-slide executive actual;
   - block pool legacy;
   - multi-source;
   - export payload con reinyección de branding y cover.

### Riesgo principal

El sistema no está duplicado por plataforma, pero sí está duplicado por capa temporal:

- sync-time normalization;
- report-time extraction;
- export-time reshaping.

Eso facilita inconsistencias entre preview, version payload, PDF y PPTX.

## 4. Estructura oficial recomendada para 5 slides

La estructura objetivo ya coincide con el builder actual y debe congelarse como contrato:

- Slide 1: Cover
- Slide 2: Reach
- Slide 3: Impressions
- Slide 4: Engagement
- Slide 5: Summary final + AI interpretation

### Source of truth recomendado

Para 5 slides, el source of truth recomendado es:

- builder: `build_5_blocks()` en `app/main.py`
- payload métrico: `buildMetricSlidePayload()`
- serie diaria: `_extract_daily_metric_series_details()` + `extractDailyMetricSeries()`
- summary: `_build_five_slide_summary_payload()`

No conviene crear otro builder. Conviene consolidar alrededor de éste.

## 5. Auditoría de branding

### Dónde se guarda actualmente

- `Workspace`
  - `name`
  - `logo_url`
- `User`
  - `full_name`
  - `logo_url`

No se encontró un campo dedicado y exclusivo llamado `brand_name` en modelo persistente. Hoy el “brand name” efectivo se resuelve desde datos de workspace/user/preferred branding.

### Helpers encontrados

En `app/services.py`:

- `resolve_report_branding()`
- `resolve_workspace_branding()`
- `resolve_report_branding_for_workspace()`

En `app/main.py`:

- `_report_branding()`
- `_user_branding()`
- `_inject_cover_branding_payload()`

### Hallazgos

1. Sí existe un helper moderno y centralizable.
   - `resolve_report_branding(user, workspace, plan, preferred_branding=None)` en `app/services.py`
   - ya resuelve:
     - `brand_name`
     - `brand_logo_url`
     - `fallback_logo_url`
     - `resolved_brand_name`
     - `resolved_logo_url`
     - `has_custom_branding`

2. Sí existe fallback oficial de Measurable.
   - `MEASURABLE_BRANDING_LOGO_URL`
   - `MEASURABLE_REPORT_BRANDING_NAME`

3. La lógica Free vs Paid está definida en `services.py`.
   - depende de `get_plan_capabilities(plan).get("allow_custom_branding")`

4. El riesgo está en la coexistencia de branding resuelto en más de un lugar.
   - `services.py` ya es el camino correcto;
   - `main.py` todavía conserva helpers paralelos para cover/thumbnail/report branding.

5. El payload del reporte ya puede incluir branding consistente, pero no toda la app depende exclusivamente del mismo helper.

### Helper oficial recomendado

El helper oficial debe ser:

```json
{
  "brand_name": "...",
  "brand_logo_url": "...",
  "fallback_logo_url": "...",
  "resolved_brand_name": "...",
  "resolved_logo_url": "..."
}
```

Implementación recomendada:

- mantener `resolve_report_branding(user, workspace, plan, preferred_branding=None)` como única fuente;
- dejar `main.py` consumiéndolo, no reinterpretándolo.

## 6. Auditoría de daily series / chart data

### Dónde nace la data diaria real

La data diaria de Meta sí se obtiene durante sync y sí se persiste en dataset/contexto. Ejemplos detectados:

- `reach_daily`
- `impressions_daily`
- `views_daily`
- `interactions_daily`
- `link_clicks_daily`
- `page_visits_daily`
- `followers_growth_daily`

También se detectó persistencia adicional en:

- `report_metric_mapping`
- `normalized_report_metrics`

### Extractores y transformaciones encontradas

- `_expand_meta_daily_series()`
- `_sum_meta_daily_series()`
- `_meta_daily_series_bounds()`
- `_extract_daily_metric_series_details()`
- `extractDailyMetricSeries()`
- `_meta_metric_series()`
- `_meta_posts_daily_series()`
- `build_meta_pages_reach_chart_data()`

### Hallazgos

1. Sí hay varios extractores/normalizadores de daily series.
   - unos actúan en sync;
   - otros al construir el reporte;
   - otros al generar charts o debug payloads.

2. Facebook Pages e Instagram Business llegan con formas distintas.
   - Meta Pages guarda claramente `reach_daily`, `impressions_daily`, `report_metric_mapping`, `normalized_report_metrics`.
   - Instagram Business usa además campos como:
     - `content_interactions`
     - `accounts_engaged`
     - `profile_views`
     - `website_clicks`
     - `metric_series`

3. La data diaria no necesariamente se pierde; con frecuencia queda dispersa.
   - el problema principal es la cantidad de rutas de lectura posibles.

4. El extractor nuevo de 5 slides es el más robusto.
   - `_extract_daily_metric_series_details()` ya busca en:
     - contexto;
     - `report_inputs`;
     - `normalized_report_metrics`;
     - `report.blocks[].daily_series`;
     - `report.blocks[].chart_data`;
     - fallbacks por posts.

5. Riesgo real: drift entre “serie diaria real”, “serie normalizada” y “fallback por posts”.
   - eso puede producir charts vacíos o series inconsistentes si una ruta cambia y otra no.

6. Hay manejo explícito para no perder `0` en el extractor nuevo.
   - eso es positivo;
   - el riesgo histórico sigue estando en helpers más antiguos o payloads legacy.

### Helper oficial recomendado

Debe consolidarse alrededor de:

- `extractDailyMetricSeries(dataset, metric_key)`
- `_extract_daily_metric_series_details(dataset, metric_key)`

Contrato recomendado:

```json
[
  {
    "date": "YYYY-MM-DD",
    "label": "May 15",
    "value": 123
  }
]
```

## 7. Auditoría de Meta sync

### Endpoints principales de sync

- `POST /integrations/meta/sync`
- `POST /integrations/meta/sync-pages`
- `POST /integrations/meta/sync-instagram-business`
- `POST /integrations/meta/sync-all`

### Helpers principales

- `_run_meta_pages_sync()`
- `_run_instagram_business_sync()`
- `_resolve_meta_sync_all_integration()`
- `_execute_meta_source_sync()`

### Hallazgos

1. `sync-all` parece reutilizar la lógica individual.
   - no se detectó un segundo sistema completo de sync;
   - actúa como orquestador de Pages + Instagram Business.

2. Sí hay muchos endpoints Meta auxiliares o de debug.
   - connect/callback/debug pages/debug raw/debug report metrics/live diagnostics/manual token/manual select account.
   - eso incrementa superficie operativa y potencial deuda.

3. El mayor riesgo no está en duplicación total de sync, sino en salida heterogénea.
   - Pages e Instagram Business no aterrizan en exactamente el mismo shape;
   - después el builder debe compensar esa heterogeneidad.

4. Existe riesgo de romper report generation si se toca sync sin congelar primero el contrato de dataset normalizado.

## 8. Auditoría de modelos y migrations

### Hallazgos de consistencia

1. El esquema evolucionó con reparaciones posteriores.
   - la migration inicial no tenía todas las cascadas;
   - `20260507_000019_report_delete_cascades.py` corrige relaciones de delete.

2. Hay señales explícitas de drift tolerado en runtime.
   - `deps.py` inspecciona columnas en caliente:
     - `user_logo_column_available()`
     - `user_onboarding_columns_available()`
     - `user_admin_column_available()`
   - esto indica que la app fue diseñada para sobrevivir a bases parcialmente migradas.

3. Hay lógica defensiva frente a tablas opcionales o faltantes.
   - referral tracking maneja ausencia de tablas opcionales.

4. Relaciones relevantes observadas:
   - `ReportVersion.report_id -> reports.id` con `ondelete="CASCADE"`
   - `ReportBlock.report_version_id -> report_versions.id` con `ondelete="CASCADE"`
   - `Export.report_id -> reports.id` con `ondelete="CASCADE"`
   - `Schedule.report_id -> reports.id` con `ondelete="CASCADE"`
   - `Job.schedule_id -> schedules.id` con `ondelete="SET NULL"`
   - `Job.export_id -> exports.id` con `ondelete="SET NULL"`

5. Muchas otras relaciones siguen sin `ondelete` explícito.
   - `WorkspaceMember.workspace_id`
   - `WorkspaceMember.user_id`
   - `Dataset.workspace_id`
   - `Integration.workspace_id`
   - `Report.workspace_id`
   - `Report.dataset_id`
   - `IntegrationAccount.workspace_id`
   - varias relaciones de workspace e integration.

### Riesgos a documentar

- drift entre modelo y base real en ambientes viejos;
- borrados parciales que dependan de cascadas incompletas;
- fixtures/tests que fallen por dependencias FK si no se limpian tablas en orden correcto;
- tablas añadidas después con cobertura desigual en tests.

### Qué no hacer todavía

- no crear migrations nuevas sin cerrar primero el contrato real de datos;
- no tocar cascadas adicionales sin revisar impacto en deletes y jobs programados.

## 9. Auditoría de endpoints

### Auth y cuenta

- `POST /auth/logout`
- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/google/start`
- `GET /auth/google/callback`
- `POST /auth/verify-email`
- `POST /auth/resend-verification-code`
- `POST /auth/forgot-password`
- `POST /auth/reset-password`
- `DELETE /account/delete`
- `GET /me`
- `GET /auth/me`
- `GET /onboarding/me`
- `POST /onboarding/complete`

Estado:

- parecen activos;
- `/me` y `/auth/me` son candidatos a duplicación funcional.

### Admin / analytics internas

- `GET /admin/metrics`
- `GET /admin/users`
- `GET /admin/funnel`
- `GET /admin/product-metrics`
- `GET /admin/referrals/partners`
- `POST /admin/referrals/partners`
- `GET /admin/referrals/summary`
- `POST /admin/referrals/manual-conversion`
- `GET /admin/cohorts`
- `GET /admin/insights`

Estado:

- activos para backoffice;
- no son foco directo de 5 slides.

### AI

- `POST /ai/chat`
- `GET /ai/conversations`
- `GET /ai/conversations/{conversation_id}/messages`

Estado:

- activos;
- relevantes por generación de insights.

### Datasets

- `POST /datasets/excel`
- `GET /datasets/{dataset_id}`

Estado:

- activos.

### Reports

- `POST /reports`
- `POST /reports/multi-source`
- `POST /reports/meta-pages`
- `POST /reports/instagram-business`
- `GET /reports`
- `GET /reports/{report_id}`
- `DELETE /reports/{report_id}`
- `GET /reports/{report_id}/versions`
- `GET /reports/{report_id}/versions/{version}`
- `POST /reports/{report_id}/thumbnail`
- `POST /reports/{report_id}/export`
- `GET /reports/{report_id}/download/pdf`

Estado:

- activos;
- existe segmentación por tipo de report creation;
- potencial duplicación entre `/reports` genérico y endpoints específicos Meta.

### Workspaces

- `POST /workspaces`
- `GET /workspaces/{workspace_id}`
- `GET /workspaces`

Estado:

- activos;
- relevantes para branding.

### Integrations / Meta

- `GET /integrations`
- `GET /integrations/meta/connect`
- `GET /integrations/meta/connect-pages`
- `GET /integrations/meta/callback`
- `GET /integrations/meta/callback-pages`
- `GET /integrations/meta/businesses`
- `GET /integrations/meta/debug-token`
- `POST /integrations/meta/set-token-manual`
- `GET /integrations/meta/debug-permissions`
- `GET /integrations/meta/pages`
- `GET /integrations/meta/facebook-pages`
- `GET /integrations/meta/instagram-accounts`
- `POST /integrations/meta/select-page`
- `GET /integrations/meta/ad-accounts`
- `POST /integrations/meta/select-account`
- `POST /integrations/meta/select-account-manual`
- `POST /integrations/meta/sync`
- `POST /integrations/meta/sync-instagram-business`
- `POST /integrations/meta/sync-pages`
- `POST /integrations/meta/sync-all`

Estado:

- mezcla de activos, discovery, debug y posibles endpoints heredados;
- `/integrations/meta/pages` y `/integrations/meta/facebook-pages` son un punto claro para revisar duplicación funcional;
- `select-account` y `select-account-manual` también merecen revisión posterior.

### Debug

- `GET /debug/cors`
- `GET /debug/meta-pages-state`
- `GET /debug/meta-instagram-diagnostics`
- `GET /debug/meta-instagram-live`
- `GET /debug/meta-raw`
- `GET /debug/meta-report-metrics`
- `GET /debug/report-render-source`

Estado:

- alta probabilidad de endpoints internos/debug;
- conviene revisarlos antes de producción pública;
- no tocarlos todavía sin confirmar consumo.

## 10. Duplicados y lógica paralela encontrados

### Builders duplicados o paralelos

- `build_5_blocks()`
- `build_10_blocks()`
- `build_15_blocks()`
- `build_30_blocks()`
- `_multi_source_build_10_blocks()`
- `_build_meta_report_block_pool()`

### Helpers duplicados o superpuestos

Branding:

- `resolve_report_branding()` en `services.py`
- `_report_branding()` en `main.py`
- `_user_branding()` en `main.py`
- `_inject_cover_branding_payload()` en `main.py`

Daily series / metrics:

- `extractDailyMetricSeries()`
- `_extract_daily_metric_series_details()`
- `_meta_metric_series()`
- `_meta_posts_daily_series()`
- `_expand_meta_daily_series()`
- `build_meta_pages_reach_chart_data()`

Payload shaping / export:

- builder de preview/version en `main.py`
- reshaping adicional en `build_export_payload()` en `services.py`

### Duplicados/obsolescencia probable en endpoints

- `/me` vs `/auth/me`
- `/integrations/meta/pages` vs `/integrations/meta/facebook-pages`
- `/integrations/meta/select-account` vs `/integrations/meta/select-account-manual`
- gran familia de `/debug/*` con riesgo de volverse contratos no oficiales

## 11. Problemas detectados en branding

- La resolución moderna ya existe, pero no toda la app usa exclusivamente `resolve_report_branding()`.
- El brand name no está modelado como campo dedicado; depende de resolución derivada.
- El branding se vuelve a tocar en cover injection, thumbnail path y export path.
- El riesgo no es tanto “falta de fallback”, sino múltiples puntos donde podría reaparecer drift.

## 12. Problemas detectados en daily series

- La serie diaria real sí existe, pero se distribuye entre varias estructuras.
- Hay demasiadas rutas de lectura válidas para la misma métrica.
- Reach e impressions tienen rutas más claras; engagement depende más de fallback o suma de parciales.
- Parte del sistema todavía conserva lógica basada en charts legacy y block pools extensos.
- Si se toca sync o normalización sin congelar un contrato único, es fácil romper preview o export.

## 13. Problemas detectados en reportes de 5 slides

- La estructura oficial ya está implementada, pero sigue viviendo en un archivo con demasiadas responsabilidades.
- Existen builders antiguos de 10/15/30 slides y pools de bloques que pueden seguir influyendo en comportamiento futuro.
- El sistema aún depende de un contexto Meta amplio y flexible, no de un DTO único explícito para el report social.
- Preview, payload de versión y export comparten intención, pero no siempre el mismo punto exacto de construcción.

## 14. Tests existentes y tests faltantes

### Ya cubierto parcialmente

- branding gating;
- payload métrico 5 slides;
- Instagram Business sync;
- Meta sync-all;
- multi-source reports;
- report delete;
- resiliencia de attribution/report payload.

### Tests faltantes recomendados

1. Happy path end-to-end de `POST /reports/meta-pages` generando 5 slides oficiales.
2. Happy path end-to-end de `POST /reports/instagram-business` generando 5 slides oficiales.
3. Paridad de branding entre preview, `ReportVersionOut`, export payload y PDF.
4. Free plan forcing measurable branding en reportes Meta.
5. Reach daily chart con serie real persistida desde sync-pages.
6. Impressions daily chart con serie real persistida desde sync-pages.
7. Engagement calculado desde interacciones parciales con serie diaria parcial.
8. Caso con daily series existente compuesta sólo por ceros.
9. Caso sin daily series real, verificando `[]` y razón consistente.
10. Slide 5 summary final con `metrics_summary`, `ai_summary` y `recommendation`.
11. `/integrations/meta/sync-all` verificando shape compatible entre datasets producidos.
12. Delete report validando efectos colaterales en versions/blocks/exports/schedules.

## 15. Source of truth recomendado

### Reportes de 5 slides

- `build_5_blocks()`
- `buildMetricSlidePayload()`
- `_build_five_slide_summary_payload()`

### Branding

- `resolve_report_branding()`

### Daily series

- `extractDailyMetricSeries()`
- `_extract_daily_metric_series_details()`

### Export payload

- `build_export_payload()`, pero consumiendo exactamente el mismo branding y bloque cover ya resuelto por el builder oficial, no reinterpretado.

### Sync normalizado

- Pages e Instagram deben seguir convergiendo a un dataset social normalizado único antes de entrar a los builders.

## 16. Plan de unificación por fases

### Fase A: Congelar contratos

- Congelar contrato oficial de branding.
- Congelar contrato oficial de metric slide.
- Congelar contrato oficial de summary slide.
- Congelar contrato oficial de daily series.

### Fase B: Reducir rutas paralelas

- Hacer que preview, version y export lean del mismo payload de 5 slides.
- Hacer que cover branding dependa sólo de `resolve_report_branding()`.
- Mantener `_build_meta_report_block_pool()` sólo para reportes legacy que realmente lo necesiten.

### Fase C: Normalización de datasets

- Alinear Pages e Instagram a un shape social único:
  - totals
  - daily series
  - AI input
  - branding context

### Fase D: Limpieza controlada

- identificar endpoints debug/legacy sin consumo;
- identificar helpers duplicados ya reemplazados;
- recién después eliminar o mover código.

## 17. Qué NO tocar todavía

- No mover builders a nuevos módulos antes de congelar contratos.
- No eliminar endpoints debug sin confirmar si frontend o soporte los usa.
- No crear migrations nuevas.
- No cambiar cascadas adicionales.
- No eliminar `_build_meta_report_block_pool()` ni `build_10/15/30_blocks()` hasta mapear consumo real.
- No reescribir sync-all si hoy ya reutiliza la lógica individual.

## 18. Quick wins seguros

- Documentar formalmente que `build_5_blocks()` es el contrato oficial para 5-slide social reports.
- Documentar que `resolve_report_branding()` es el helper oficial de branding.
- Documentar que `extractDailyMetricSeries()` es la entrada oficial para chart data en 5 slides.
- Alinear tests end-to-end de preview/export sobre ese contrato.
- Agregar trazabilidad temporal uniforme en logs de sync y report build.

## 19. Cambios riesgosos que requieren cuidado

- tocar normalización de datasets Meta;
- tocar sync de Instagram Business;
- tocar cascadas de report delete;
- eliminar endpoints Meta duplicados sin confirmar consumo;
- mover branding de `main.py` a `services.py` sin revisar thumbnail/export;
- borrar helpers legacy antes de validar que 10/15/30 slides o multi-source no dependan de ellos.

## 20. Archivos que probablemente deberán refactorizarse

- `app/main.py`
- `app/services.py`
- `app/schemas.py`
- `app/models.py`
- `app/deps.py`
- eventualmente crear módulos explícitos para:
  - report builders
  - branding
  - metric normalization
  - Meta sync orchestration

## 21. Archivos o zonas que probablemente se podrán eliminar después

No eliminar ahora. Sólo candidatos a revisión futura:

- helpers legacy de branding en `app/main.py`
- endpoints Meta duplicados o manuales que no tengan consumo real
- endpoints `/debug/*` no utilizados
- builders legacy no usados por frontend actual
- bloques/payloads heredados de `_build_meta_report_block_pool()` que no formen parte de contratos vigentes

## 22. Conclusión

El backend no parece tener un segundo sistema completo de reportes de 5 slides en paralelo, pero sí tiene demasiada lógica histórica alrededor del mismo dominio. El mejor camino no es “reescribir” sino:

- declarar source of truth;
- alinear preview/export/sync a ese contrato;
- recién después limpiar duplicados.

El mayor riesgo hoy está en `app/main.py` como punto de concentración. El segundo riesgo está en dataset normalization heterogénea entre Meta Pages e Instagram Business. El tercero está en branding y export reshaping ejecutados en más de una capa.
