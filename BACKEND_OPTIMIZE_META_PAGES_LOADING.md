# BACKEND_OPTIMIZE_META_PAGES_LOADING

## Mini-auditoría

### Source of truth existente
- Cache persistido de páginas/cuentas Meta: `MetaPage` en `app/models.py`
- Refresh live oficial contra Meta Graph: `_refresh_meta_pages_from_live_graph(...)` en `app/main.py`
- OAuth / token storage existente: `Integration`, `IntegrationAccount`, `IntegrationToken`
- Endpoints públicos existentes:
  - `GET /integrations/meta/pages`
  - `GET /integrations/meta/facebook-pages`
  - `GET /integrations/meta/instagram-accounts`
  - `POST /integrations/meta/sync-all`

### Cuello de botella detectado
- `GET /integrations/meta/pages` y `GET /integrations/meta/instagram-accounts` llamaban refresh live en cada entrada.
- Eso obligaba al New Report sync step a esperar discovery completo contra Meta aunque ya existiera cache en DB.
- Con portfolios grandes, la latencia subía fácilmente a 30s+.

## Resolución aplicada

### 1. Cache-first en lectura
Los endpoints existentes ahora:
- leen primero desde `MetaPage`
- responden rápido si ya hay cache
- solo hacen discovery live si el cache está vacío

### 2. Refresh explícito bajo demanda
Se agregó:
- `POST /integrations/meta/refresh-pages`

Ese endpoint:
- usa el helper live oficial existente
- actualiza/recupera Facebook Pages + Instagram Business accounts
- devuelve counts y `duration_ms`
- captura timeout/error con respuesta controlada

### 3. TTL / estado del cache
TTL definido:
- `6 horas`

Estados usados:
- `cached`
- `cached_stale`
- `empty_cache`
- `live`

### 4. Endpoints para catálogo enriquecido
Se agregaron wrappers de catálogo para frontend nuevo:
- `GET /integrations/meta/pages/catalog`
- `GET /integrations/meta/instagram-accounts/catalog`

Devuelven:
- `data`
- `source`
- `count`
- `has_cached_data`
- `refresh_available`
- `refresh_recommended`
- `message`
- `limit`
- `offset`
- `search`

Los endpoints legacy existentes siguen respondiendo lista simple para no romper compatibilidad.

### 5. Búsqueda y paginación
Soportado en lectura:
- `limit`
- `offset`
- `search`

Aplicado sobre cache local antes de responder.

### 6. Logs agregados
Se agregaron logs con:
- `workspace_id`
- `integration_id`
- `cached_pages_count`
- `cached_instagram_count`
- `live_refresh_triggered`
- `meta_duration_ms`
- `total_pages_from_meta`
- `response_source`
- `endpoint_duration_ms`

También se removió `token_preview` del logging central del refresh para no imprimir tokens en ese flujo.

## Endpoints resultantes

### Lectura rápida cache-first
- `GET /integrations/meta/pages`
- `GET /integrations/meta/facebook-pages`
- `GET /integrations/meta/instagram-accounts`

### Lectura enriquecida para UI nueva
- `GET /integrations/meta/pages/catalog`
- `GET /integrations/meta/instagram-accounts/catalog`

### Refresh vivo bajo demanda
- `POST /integrations/meta/refresh-pages`

## Tests ejecutados

```bash
poetry run pytest -q tests/test_meta_pages_loading.py tests/test_meta_sync_all.py tests/test_account_summary_and_report_metadata.py tests/test_instagram_business_sync.py
```

Resultado:
- `19 passed`

## Riesgos pendientes

- El frontend de New Report no está en este repo, así que aquí no se cambió qué endpoint consume el sync step.
- Para obtener la UX óptima, el frontend debería usar:
  - lectura inicial: `GET /integrations/meta/pages/catalog` / `...instagram-accounts/catalog`
  - refresh manual: `POST /integrations/meta/refresh-pages`
- El discovery inicial con cache vacío todavía puede tardar, pero ya no corre en cada entrada cuando existe cache.
- El refresh live sigue dependiendo de la latencia de Meta Graph; se encapsuló con respuesta controlada, no con refactor masivo de jobs/background workers.
