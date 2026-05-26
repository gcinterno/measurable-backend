# BACKEND FIX — Meta OAuth Popup Close

## Mini-auditoría

- `GET /integrations/meta/connect-pages` construye `auth_url` con `oauth_connect_pages_url(...)`.
- El `redirect_uri` usado por Meta Pages sale de `META_PAGES_REDIRECT_URI` vía `get_meta_pages_redirect_uri()` y `_meta_pages_redirect_uri()`.
- El callback backend real es `GET /integrations/meta/callback-pages`.
- El flujo validaba `state`, workspace e integración correctamente y completaba el token exchange/sync.
- El problema estaba al final del callback: en vez de devolver una página controlada por Measurable, hacía redirect al frontend y dejaba el popup sin mecanismo de autocierre.

## Source of truth reutilizado

- Construcción de auth URL: `app/integrations/meta_ads.py`
- Callback y validación de `state`: `app/main.py`, `_run_meta_pages_oauth_callback(...)`
- URL frontend permitida: `FRONTEND_URL` / `FRONTEND_BASE_URL`

No se creó un segundo flujo OAuth. Se mantuvo el mismo `connect-pages -> callback-pages`.

## Cambio aplicado

- `GET /integrations/meta/callback-pages` ahora responde con una mini página HTML segura cuando el flujo se abrió como popup.
- La página:
  - envía `postMessage` a `window.opener` con origen explícito,
  - intenta cerrar la pestaña,
  - si no hay opener, hace fallback a la URL del frontend,
  - si falla el cierre, deja mensaje y link para volver a Measurable.

## Mensajes enviados al frontend

### Éxito

```json
{
  "type": "MEASURABLE_META_CONNECT_SUCCESS",
  "provider": "meta",
  "status": "connected",
  "integrationId": 123
}
```

### Error

```json
{
  "type": "MEASURABLE_META_CONNECT_ERROR",
  "provider": "meta",
  "status": "error",
  "error": "invalid_state",
  "message": "No pudimos completar la conexion con Meta."
}
```

## Configuración requerida en Meta App Dashboard

La URL exacta registrada debe coincidir con `META_PAGES_REDIRECT_URI`.

### Local

```text
http://localhost:8001/integrations/meta/callback-pages
```

### Producción

```text
https://api.measurableapp.com/integrations/meta/callback-pages
```

Usa la URL backend pública real del ambiente. Debe coincidir exactamente con la configurada en Meta.

## Variables relevantes

- `FRONTEND_URL`
- `FRONTEND_BASE_URL`
- `META_PAGES_REDIRECT_URI`
- `META_PAGES_APP_ID`
- `META_PAGES_APP_SECRET`

## Validación esperada

1. `Connect` abre popup.
2. Meta usa `META_PAGES_REDIRECT_URI`.
3. Meta regresa a `/integrations/meta/callback-pages`.
4. El callback envía `postMessage` al frontend.
5. El popup se cierra automáticamente.
6. Si no existe `window.opener`, el usuario vuelve a Measurable vía redirect.
