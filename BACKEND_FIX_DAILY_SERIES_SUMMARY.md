# Backend Fix Daily Series Summary

## Qué se corrigió

Se corrigió el comportamiento del builder oficial de 5 slides en `app/main.py` para estabilizar:

- Reach daily series
- Impressions daily series
- Summary slide `metrics_summary`

Sin crear otro builder, sin abrir otro sistema de charts y sin cambiar rutas públicas.

## Dónde estaba el bug de Reach daily_series

El extractor oficial `_extract_daily_metric_series_details()` no estaba buscando suficientemente bien en estructuras ya normalizadas del dataset, especialmente:

- `normalized_report_metrics`
- `report_metric_mapping`

Además, para Reach faltaban aliases importantes que sí aparecen en datasets normalizados o integraciones Meta:

- `page_reach`
- `viewers`

Resultado:

- el total de Reach podía existir;
- pero la serie diaria no se encontraba;
- por eso el slide mostraba “daily series not available” y `highest_day` / `lowest_day` quedaban vacíos.

## Dónde estaba el bug de Impressions daily_series

Había dos problemas combinados:

1. El extractor no priorizaba correctamente series con datos reales si antes encontraba una serie con puros `0`.
2. Si el total venía positivo pero la única serie encontrada era todo `0`, el payload podía quedar visualmente inconsistente.

Eso explicaba el caso:

- `total = 1367`
- chart plano en `0`
- `highest_day` y `lowest_day` en `0`

El fix aplicado fue:

- buscar también dentro de `normalized_report_metrics` y `report_metric_mapping`;
- para cada candidata encontrada, preferir una serie con al menos un valor no-cero;
- si para Reach o Impressions sólo aparece una serie toda en `0` mientras el total es positivo, tratarla como inconsistente y no enviarla como chart válido.

Con eso se evita mostrar una línea artificial de ceros cuando no hay una serie diaria confiable.

## Por qué aparecía la inconsistencia total vs chart

La inconsistencia venía de una mezcla de:

- búsqueda incompleta de rutas normalizadas;
- aliases incompletos para Reach;
- selección demasiado temprana de la primera serie encontrada;
- falta de validación explícita entre `total` y `daily_series`.

Ahora el flujo es:

1. Se buscan más rutas válidas.
2. Se prefieren candidatas con datos reales.
3. Si una serie toda en `0` contradice un total positivo, esa serie no se usa.

## Cómo se corrigió `metrics_summary` para evitar `[object Object]`

Antes, el Summary Slide guardaba objetos ricos por métrica, con campos como:

- `metric_key`
- `metric_label`
- `highest_day`
- `lowest_day`
- `frequency`

Eso hacía fácil que frontend intentara renderizar la card completa o algún campo ambiguo y terminara mostrando `[object Object]`.

Ahora `metrics_summary` se normaliza a tarjetas primitivas y renderizables:

```json
{
  "reach": {
    "label": "Reach",
    "value": 44851,
    "formatted_value": "44,851",
    "description": "Total reach"
  }
}
```

Se corrigió para que:

- `value` nunca sea un objeto crudo;
- `formatted_value` sea siempre string;
- `description` sea texto simple;
- no se envíen bloques métricos completos dentro del summary.

## Logs temporales agregados

Se dejaron logs temporales en la resolución de métricas del reporte 5 slides:

- `metric_key`
- `total`
- `daily_series_length`
- `daily_series_values`
- `daily_series_source_path`
- `daily_series_source_metric_key`
- candidate keys disponibles
- `highest_day`
- `lowest_day`

También se agregó un log especial para Facebook Pages 5 slides con:

- keys disponibles de contexto
- keys disponibles en `report_inputs`
- keys disponibles en `insights`, `daily`, `values`, `metric_values`, `chart_data`
- source path resuelto para reach e impressions

## Tests ejecutados

Se ejecutaron:

- `poetry run python -m py_compile app/main.py app/services.py tests/test_five_slide_metric_payload.py tests/test_branding_gating.py`
- `poetry run python -m pytest tests/test_five_slide_metric_payload.py tests/test_branding_gating.py`

Resultado:

- `16 passed`

## Cobertura agregada/ajustada

Se reforzó cobertura para:

- Reach leyendo desde `normalized_report_metrics` y aliases reales
- Impressions con serie real
- Impressions inconsistentes con total positivo y serie toda en `0`
- Preservation de serie diaria en `0` cuando el total también es `0`
- Summary slide con `metrics_summary` renderizable
- Facebook Pages 5 slides
- Instagram Business 5 slides

## Riesgos pendientes

- `app/main.py` sigue concentrando demasiada lógica.
- Export/preview/PDF/PPTX todavía deben verificarse en una fase posterior para confirmar que el mismo `metrics_summary` plano viaja idéntico en todos los canales.
- Sigue existiendo lógica histórica de charts y builders más largos, aunque esta corrección no la tocó.
- Los logs temporales deben retirarse o reducirse cuando termine la estabilización.
