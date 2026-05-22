# Backend Fix AI Insights 5 Slides

## Mini-auditoria

La ruta oficial de 5 slides sigue siendo `build_5_blocks()`.

Los textos secos venian de dos lugares:

- `buildMetricSlidePayload()` recibia textos legacy y solo los truncaba, sin interpretar el payload oficial de la metrica.
- `_meta_enrich_existing_block()` podia rellenar slides sin `text` con frases genericas tipo `Daily resumen final is available for this period.`

El summary final tambien dependia de una mezcla de `context.ai_summary`, `_meta_overview_insight()` y un recommendation fijo, por lo que podia sentirse generico o no considerar metricas N/A.

## Source of truth

Se mantuvo el source of truth existente:

- Builder: `build_5_blocks()`
- Payload metrico: `buildMetricSlidePayload()`
- Summary final: `_build_five_slide_summary_payload()`

Helpers oficiales agregados/mejorados:

- `truncateInsight(text, max_chars)`
- `build_metric_ai_insight(metric_slide, context)`
- `build_final_ai_summary(slides, context)`

No se creo otro builder ni otro sistema paralelo.

## Limites aplicados

Slides metricas:

- `insight_short`: maximo 260 caracteres
- `insight`: maximo 420 caracteres
- `insight_tone`: `executive_ai`
- `insight_max_chars`: `260`

Slide 5:

- `ai_summary`: maximo 520 caracteres
- `recommendation`: maximo 220 caracteres

El truncado intenta cortar por frase o por palabra antes de agregar `...`.

## Comportamiento nuevo

Para metricas disponibles, el insight ahora usa:

- total formateado;
- daily series cuando existe;
- highest day / lowest day cuando aporta contexto;
- una implicacion o recomendacion breve.

Para metricas no disponibles:

- no dice que el valor es 0;
- mantiene `N/A`;
- usa lenguaje compatible con App Review:
  - `Este dato no está disponible en este momento con los permisos actuales de Meta.`

El summary final ahora considera metricas disponibles y no disponibles, y evita interpretar como 0 cualquier metrica marcada como N/A.

## Ejemplos de output

Reach:

```text
El alcance acumuló 44,851 personas durante el periodo. El pico más alto aparece en Friday, May 15, 2026. La serie diaria de reach cerró por debajo del inicio, lo que ayuda a ubicar el momento con mayor tracción.
```

Impressions:

```text
Las impresiones sumaron 90,120 vistas del contenido. El pico más alto aparece en Friday, May 15, 2026. Este dato ayuda a entender la exposición real sin mezclarlo con alcance.
```

N/A:

```text
Este dato no está disponible en este momento con los permisos actuales de Meta. El reporte puede seguir interpretando las métricas disponibles y esta sección se actualizará cuando la fuente entregue más información.
```

## Tests ejecutados

```bash
poetry run python -m py_compile app/main.py app/services.py app/schemas.py tests/test_five_slide_metric_payload.py tests/test_branding_gating.py
poetry run python -m pytest tests/test_five_slide_metric_payload.py tests/test_branding_gating.py
```

Resultado:

```text
22 passed
```

## Cobertura agregada

Se agrego/actualizo cobertura para:

- `insight_short <= 260`
- `insight <= 420`
- `ai_summary <= 520`
- `recommendation <= 220`
- metrica disponible genera insight humano y accionable
- metrica N/A usa mensaje compatible con permisos Meta
- placeholders viejos no aparecen:
  - `insights will appear`
  - `daily resumen final`
  - `source includes enough contextual detail`
  - `Daily resumen final is available`

## Riesgos pendientes

- Los builders legacy de 10/15/30 slides siguen teniendo textos propios y no fueron refactorizados.
- Falta validar visualmente preview/PDF/PPTX para confirmar que frontend usa `insight_short` en cards y conserva `insight` para detalles/export.
- El tono esta construido desde reglas deterministicas del payload; si mas adelante se conecta un LLM real para copy final, debe seguir pasando por estos limites de longitud.
