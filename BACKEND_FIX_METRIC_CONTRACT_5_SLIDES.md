# Backend Fix Metric Contract 5 Slides

## Resumen

Se estabilizo el contrato oficial de metricas para el reporte social de 5 slides sin crear otro builder ni otro sistema de charts.

Source of truth mantenido:

- `build_5_blocks()`
- `resolve_report_branding()`
- `extractDailyMetricSeries()` / `extractDailyMetricsSeries()`

## Que metrica estaba mal mapeada

El riesgo principal estaba en engagement e impressions:

- `likes`, `comments`, `shares`, `saves`, `reactions` y `link_clicks` estaban dentro de aliases de engagement.
- Eso podia hacer que un componente individual, por ejemplo `likes`, se tomara como engagement directo.
- Impressions ya no usa reach como fallback; si no hay dato real de impressions, queda como no disponible.

## Si impressions estaba usando reach

No se dejo ningun fallback de reach hacia impressions.

El extractor de impressions solo revisa aliases de impressions:

- `impressions`
- `page_impressions`
- `profile_impressions`
- `account_impressions`
- `views`
- `content_views`
- `profile_views`

Tambien se agrego log temporal si la fuente diaria de impressions coincide con la de reach, para detectar cualquier mapping sospechoso en datasets reales.

## KPI usado para engagement

La resolucion de engagement ahora separa metricas directas y componentes:

- Directas:
  - `engagement`
  - `engagements`
  - `interactions`
  - `total_interactions`
  - `post_engagements`
  - `content_interactions`
- Componentes calculables:
  - `reactions`
  - `likes`
  - `comments`
  - `shares`
  - `saves`
  - `link_clicks`

Si existe una metrica directa, se usa como `metric_source = direct_meta_metric`.

Si no existe directa pero si existen componentes, se calcula:

```text
engagement = reactions + likes + comments + shares + saves + link_clicks
```

En ese caso se manda `metric_source = calculated_from_components`.

## Cuando engagement queda N/A

Engagement queda no disponible cuando:

- no hay metrica directa;
- no hay componentes suficientes;
- no hay serie diaria real de engagement/interactions.

Payload esperado en ese caso:

```json
{
  "total": null,
  "formatted_total": "N/A",
  "is_available": false,
  "unavailable_reason": "not_returned_by_meta",
  "unavailable_message": "Dato no disponible en este momento con los permisos actuales de Meta.",
  "metric_source": "not_available"
}
```

## App Review / permisos

El mensaje de no disponibilidad se mantiene neutral y compatible con App Review:

```text
Dato no disponible en este momento con los permisos actuales de Meta.
```

No se culpa a Meta ni se inventan ceros cuando el dato no existe.

## Fechas y daily_series

La serie diaria conserva las fechas reales entregadas por el dataset:

- no se inventa el ultimo dia;
- no se recorta el ultimo dia si esta presente;
- si `period_end` existe pero la serie termina antes, se registra un log temporal para confirmar si Meta no entrego el ultimo dia o si hay un problema aguas arriba.

Se agrego log temporal:

- `period_start`
- `period_end`
- `first_date`
- `last_date`
- `daily_series_source_path`

## Branding

Cada slide del reporte de 5 slides ahora recibe branding resuelto:

- cover;
- reach;
- impressions;
- engagement;
- summary.

El builder usa `resolve_report_branding()` y propaga el branding resuelto al contexto usado por las slides.

## Contrato metric slide

Cada slide metrica ahora incluye:

- `metric_key`
- `metric_label`
- `metric_label_es`
- `metric_label_en`
- `total`
- `formatted_total`
- `is_available`
- `unavailable_reason`
- `unavailable_message`
- `metric_source`
- `branding`
- `daily_series`
- `highest_day`
- `lowest_day`
- `insight_short`
- `insight`

Cuando una metrica no esta disponible:

- `total = null`
- `formatted_total = "N/A"`
- `is_available = false`
- `highest_day = null`
- `lowest_day = null`

## Summary final

`metrics_summary` se mantiene plano y renderizable:

```json
{
  "reach": {
    "label": "Reach",
    "value": 23768,
    "formatted_value": "23,768",
    "is_available": true,
    "description": "Total reach"
  },
  "impressions": {
    "label": "Impressions",
    "value": null,
    "formatted_value": "N/A",
    "is_available": false,
    "description": "Dato no disponible en este momento con los permisos actuales de Meta."
  }
}
```

No se mandan objetos crudos dentro de `value`.

## Tests ejecutados

Se ejecuto:

```bash
poetry run python -m py_compile app/main.py app/services.py tests/test_five_slide_metric_payload.py tests/test_branding_gating.py
poetry run python -m pytest tests/test_five_slide_metric_payload.py tests/test_branding_gating.py
```

Resultado:

```text
19 passed
```

## Riesgos pendientes

- Los logs temporales deben retirarse o bajarse de nivel despues de estabilizar produccion.
- Falta verificar visualmente preview, PDF y PPTX con este contrato.
- Las rutas legacy de 10/15/30 slides siguen vivas y no fueron refactorizadas.
- Si Meta entrega metricas nuevas o cambia nombres, se deben agregar aliases al extractor oficial, no crear otro builder.
