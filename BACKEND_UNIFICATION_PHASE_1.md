# Backend Unification Phase 1

## 1. Source of truth definido

Esta fase congela oficialmente estos puntos para reportes sociales de 5 slides:

- Builder oficial: `build_5_blocks()` en `app/main.py`
- Branding oficial: `resolve_report_branding()` en `app/services.py`
- Daily series oficial:
  - `extractDailyMetricSeries()` como helper estable
  - `extractDailyMetricsSeries()` como wrapper de compatibilidad
  - `_extract_daily_metric_series_details()` como resolver interno
- Estructura oficial:
  - Slide 1: Cover
  - Slide 2: Reach
  - Slide 3: Impressions
  - Slide 4: Engagement
  - Slide 5: Summary final

## 2. Funciones legacy que siguen vivas

No se eliminó ninguna función legacy. Se dejaron vivas y marcadas con comentarios temporales:

- `build_10_blocks()`
- `build_15_blocks()`
- `build_30_blocks()`
- `_multi_source_build_10_blocks()`
- `_build_meta_report_block_pool()`
- `_report_branding()`
- `_user_branding()`
- `_inject_cover_branding_payload()`
- `_expand_meta_daily_series()`
- `_meta_metric_series()`
- `build_meta_pages_reach_chart_data()` en `app/services.py`

También se marcó explícitamente como source of truth:

- `_extract_daily_metric_series_details()`
- `build_5_blocks()`
- `build_blocks()` para el branch de `requested_slides <= 5`

## 3. Qué funciones legacy ahora delegan al source of truth

- `build_blocks()` mantiene compatibilidad pública, pero documenta y fuerza que todo social report de `<= 5` slides use `build_5_blocks()`.
- `build_5_blocks()` ahora resuelve branding usando `resolve_report_branding()` y deja el payload de cover ya resuelto.
- `_meta_enrich_existing_block()` ahora respeta primero el branding ya resuelto dentro del bloque cover antes de usar branding del contexto.
- `extractDailyMetricsSeries()` se agregó como wrapper mínimo de compatibilidad para usar el helper oficial sin abrir otro sistema.

## 4. Qué no se tocó todavía

- No se borró ningún archivo.
- No se eliminaron endpoints.
- No se eliminaron builders legacy.
- No se cambiaron rutas públicas.
- No se cambiaron migrations.
- No se movió código fuera de `app/main.py`.
- No se unificó todavía el sistema de 10/15/30 slides.
- No se limpió todavía la familia de endpoints Meta debug/manual/legacy.

## 5. Cambios aplicados en esta fase

- Se agregaron comentarios técnicos temporales sobre funciones legacy indicando:
  - `LEGACY / candidate for removal after frontend/backend contract is stable`
  - el source of truth recomendado
- `build_5_blocks()` quedó documentado como builder oficial de 5 slides.
- `build_5_blocks()` ahora:
  - usa branding resuelto por `resolve_report_branding()`
  - usa `extractDailyMetricsSeries()` para su lectura explícita de daily series
  - devuelve siempre la estructura oficial de 5 slides
  - completa top-level cover fields:
    - `branding`
    - `brand_name`
    - `brand_logo_url`
    - `resolved_brand_name`
    - `resolved_logo_url`
    - `cover_branding`
- El contexto del flow social que construye bloques ahora pasa `plan` al builder para que el branding oficial respete Free vs Paid.

## 6. Tests corridos

Se corrieron estos checks:

- `poetry run python -m py_compile app/main.py app/services.py tests/test_five_slide_metric_payload.py tests/test_branding_gating.py`
- `poetry run python -m pytest tests/test_five_slide_metric_payload.py tests/test_branding_gating.py`

Resultado:

- `13 passed`

## 7. Tests actualizados

Se reforzó cobertura mínima para validar:

- `build_5_blocks()` genera exactamente 5 slides
- Slide 1 es cover
- Slide 2 es reach
- Slide 3 es impressions
- Slide 4 es engagement
- Slide 5 es summary
- branding custom se conserva en cover
- branding fallback de Measurable aparece cuando falta branding custom
- daily series con valores `0` se conserva y no se marca como unavailable
- daily series real sigue llegando normalizada

## 8. Riesgos pendientes

- `app/main.py` sigue concentrando demasiadas responsabilidades.
- Branding todavía tiene helpers legacy vivos en `main.py`.
- Daily series sigue teniendo más de una capa histórica:
  - sync-time
  - report-time
  - chart helper legacy
- Los builders de 10/15/30 slides siguen coexistiendo con el contrato nuevo.
- Multi-source sigue usando su propio builder.
- Export preview/PDF/PPTX todavía requiere una fase posterior de alineación total con el mismo contrato de 5 slides.

## 9. Próxima fase recomendada

Fase 2 recomendada:

- alinear preview, `ReportVersionOut`, PDF y PPTX para que reutilicen exactamente el mismo payload oficial de 5 slides;
- reducir duplicación de branding en `main.py` haciendo que las rutas legacy lean branding ya resuelto;
- congelar formalmente el shape normalizado de dataset social que consumen Facebook Pages e Instagram Business;
- recién después evaluar eliminación real de builders/helpers legacy sin romper consumo del frontend.
