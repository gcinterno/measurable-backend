# Backend Feature: User Suggestions

## Mini-auditoria

- Admin ya existe en `app/main.py` con rutas `/admin/*` protegidas por `require_admin_user`.
- Feedback existente: `AccountDeletionFeedback`, limitado a baja de cuenta. No se reutilizo para sugerencias porque mezcla un evento destructivo con feedback general de producto.
- Reviews/support/admin notes: no se encontro una tabla generica reutilizable.
- AI Assistant y dashboard/admin ya tienen metricas y conversaciones, pero no habia almacenamiento de sugerencias.
- No se toco report generation.

## Modelo / Tabla

Se creo `UserSuggestion` sobre la tabla `user_suggestions`.

Campos:

- `id`
- `user_id`
- `workspace_id`
- `message`
- `status`
- `source`
- `reviewed_at`
- `reviewed_by`
- `created_at`
- `updated_at`

Migracion:

- `alembic/versions/20260521_000022_add_user_suggestions.py`

Indices:

- `user_id`
- `workspace_id`
- `created_at`
- `status`

## Endpoints

- `POST /suggestions`
  - Requiere usuario autenticado.
  - Guarda `message` exactamente como fue enviado.
  - Asocia `user_id` actual.
  - Asocia el primer `workspace_id` del usuario si existe; si no existe, guarda `null`.
  - Status inicial: `new`.
  - Source: `floating_suggestion_button`.
  - Respuesta: `{ "success": true, "suggestion": {...} }`.

- `GET /admin/suggestions`
  - Solo admin.
  - Ordena por `created_at desc`, `id desc`.
  - Incluye usuario (`email`, `full_name`) y workspace (`name`) cuando existen.

- `PATCH /admin/suggestions/{suggestion_id}`
  - Solo admin.
  - Permite `new`, `reviewed`, `archived`.
  - Para `reviewed` y `archived`, llena `reviewed_at` y `reviewed_by`.
  - Para `new`, limpia `reviewed_at` y `reviewed_by`.

## Validaciones

- `message` requerido por schema.
- `message` no puede ser vacio o solo espacios.
- `message` maximo 1000 caracteres.
- Error claro para mensaje vacio: `invalid_message`.
- Error claro para longitud: `message_too_long`.
- No falla si el usuario no tiene workspace.

## Tests Ejecutados

- `poetry run python -m py_compile app/models.py app/schemas.py app/main.py alembic/versions/20260521_000022_add_user_suggestions.py tests/test_admin.py`
  - OK.

- `poetry run pytest tests/test_admin.py::test_authenticated_user_creates_suggestion tests/test_admin.py::test_unauthenticated_user_cannot_create_suggestion tests/test_admin.py::test_empty_suggestion_message_fails tests/test_admin.py::test_non_admin_cannot_list_admin_suggestions tests/test_admin.py::test_admin_can_list_suggestions tests/test_admin.py::test_admin_updates_suggestion_status -q`
  - OK: 6 passed.

- `poetry run pytest tests/test_admin.py -q`
  - Resultado: 10 passed, 3 failed.
  - Los 3 fallos son de tests existentes de admin con fixtures de fechas relativas a "last 7 days" / activacion, no de sugerencias.

- `poetry run alembic upgrade head`
  - OK.
  - Aplico `20260516_000021 -> 20260521_000022`.

## Riesgos Pendientes

- La seleccion de workspace en `POST /suggestions` usa el primer workspace del usuario porque el contrato solicitado no incluye `workspace_id` en el body.
- `tests/test_admin.py` contiene expectativas sensibles a la fecha actual; al 2026-05-22 fallan algunas aserciones existentes de "last 7 days".
