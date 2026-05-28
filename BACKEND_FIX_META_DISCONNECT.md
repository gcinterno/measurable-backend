# Backend Fix Meta Disconnect

## Causa encontrada

- `Facebook Pages` e `Instagram Business` comparten la misma `Integration(provider="meta")`.
- El token Meta se guarda en `IntegrationToken` ligado a `IntegrationAccount`.
- Las selecciones activas y referencias de sync se guardan en `IntegrationAccount`.
- Las paginas Facebook y cuentas Instagram cacheadas se guardan en `MetaPage`.
- No existia un endpoint de disconnect real para Meta. El backend podia seguir devolviendo o usando datos viejos si quedaban `MetaPage`, `IntegrationAccount` o `IntegrationToken`.
- `app/services.py` no participa en este flujo; la logica relevante esta en `app/main.py`.

## Endpoint corregido

- Se agrego `POST /integrations/meta/disconnect`.
- Acepta `integration_id` o `workspace_id`.
- Es idempotente: si ya estaba desconectado responde `success: true`.
- Intenta revocar permisos en Meta con `DELETE /me/permissions` si hay token.
- Si la revocacion falla, el disconnect local igual se completa.

## Tablas y caches limpiados

- `integrations`: `status` queda en `disconnected`.
- `integration_accounts`: se eliminan cuentas seleccionadas y storage del token Meta.
- `integration_tokens`: se eliminan al borrar las cuentas asociadas.
- `meta_pages`: se eliminan paginas Facebook y cuentas Instagram cacheadas.
- Los endpoints `GET /integrations/meta/pages` y `GET /integrations/meta/instagram-accounts` ahora devuelven vacio cuando la integracion esta desconectada.
- Los catalog endpoints devuelven `status: disconnected`, `connected: false` y `data: []`.

## Reportes historicos

- No se borran `reports`.
- No se borran `report_versions`.
- No se borran `datasets`.
- Los reportes historicos siguen intactos aunque Meta quede desconectado.

## Revocacion en Meta

- Se intento implementar revocacion remota con Graph API.
- Estado posible: `success`, `failed`, `skipped`.
- Si falla la revocacion remota, el disconnect local sigue siendo exitoso.

## Tests ejecutados

- `poetry run python -m py_compile app/main.py app/services.py app/schemas.py app/models.py`
- `poetry run pytest tests/test_meta_disconnect.py tests/test_meta_pages_loading.py tests/test_account_summary_and_report_metadata.py tests/test_instagram_business_sync.py -q`
- `poetry run pytest tests/test_meta_sync_all.py -q`

## Riesgos pendientes

- `POST /integrations/meta/disconnect` desconecta Meta completo para esa `Integration(provider="meta")`. Eso evita estados inconsistentes entre Facebook Pages e Instagram Business, pero tambien limpia cualquier otra seleccion Meta colgada de la misma integracion.
- Los endpoints list legacy siguen respondiendo una lista vacia cuando esta desconectado; el detalle rico de `status/connected/message` vive en los endpoints `catalog`.
