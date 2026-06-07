# Backend Fix Meta OAuth First Login

## Source of truth found

- Official Meta Pages/Instagram Business connect entrypoint: `GET /integrations/meta/connect-pages` in [app/main.py](/Users/mac/measurable/apps/api/app/main.py:15657)
- Official Meta Pages auth URL builder: `oauth_connect_pages_url(...)` in [app/integrations/meta_ads.py](/Users/mac/measurable/apps/api/app/integrations/meta_ads.py:191)
- Official backend redirect URI resolver: `get_meta_pages_redirect_uri()` in [app/integrations/meta_ads.py](/Users/mac/measurable/apps/api/app/integrations/meta_ads.py:108)
- Official callback used by popup flow: `GET /integrations/meta/callback-pages` in [app/main.py](/Users/mac/measurable/apps/api/app/main.py:15736)
- Official callback executor and state resolution: `_run_meta_pages_oauth_callback(...)` in [app/main.py](/Users/mac/measurable/apps/api/app/main.py:7866)
- There is no separate persisted “pending Meta OAuth” table. The existing source of truth is:
  - one `Integration(provider="meta")` per workspace via `_get_or_create_meta_integration_for_workspace(...)`
  - signed `state` payload carrying `workspace_id`, `user_id`, `integration_id`, `integration_type`, `source`, `callback_route`

## Legacy / non-official paths found

- Legacy route still present: `GET /integrations/meta/callback` in [app/main.py](/Users/mac/measurable/apps/api/app/main.py:15708)
- Legacy frontend callback URL shape still exists only as fallback target for popup HTML: `/integrations/meta/callback`
- Legacy env aliasing still exists in config:
  - `META_PAGES_REDIRECT_URI` falls back to `META_REDIRECT_URI`
  - `META_PAGES_APP_ID` falls back to `META_APP_ID`
  - `FRONTEND_URL` falls back to `FRONTEND_BASE_URL`

The Pages/Instagram popup flow should treat `/integrations/meta/callback-pages` as the only official redirect target.

## Fix applied

- Reused the existing Meta Pages flow. No second OAuth system was added.
- `get_meta_pages_redirect_uri()` now canonicalizes the redirect target to `/integrations/meta/callback-pages`, preferring `API_BASE_URL` when available.
- Auth URL generation and token exchange now resolve through that same canonical helper.
- Meta `state` is now signed and expiring, with backward-compatible decoding for in-flight legacy states during rollout.
- `/integrations/meta/callback-pages` now handles:
  - upstream OAuth errors from Meta
  - missing `code`
  - missing `state`
  - invalid/expired state
  by returning a clean Measurable popup HTML page instead of raw errors or broken redirects.
- Popup HTML keeps:
  - `window.opener.postMessage(...)`
  - explicit frontend origin from `FRONTEND_URL` / `FRONTEND_BASE_URL`
  - `window.close()` with delay
  - visible fallback copy and link back to Measurable

## Railway env vars required

- `META_PAGES_APP_ID`
- `META_PAGES_APP_SECRET`
- `META_PAGES_REDIRECT_URI`
- `API_BASE_URL`
- `FRONTEND_URL` or `FRONTEND_BASE_URL`

Recommended production values:

- `API_BASE_URL=https://api.measurableapp.com`
- `META_PAGES_REDIRECT_URI=https://api.measurableapp.com/integrations/meta/callback-pages`
- `FRONTEND_URL=<frontend public origin>`

## Meta App Dashboard

Exact Valid OAuth Redirect URI to register in Meta:

`https://api.measurableapp.com/integrations/meta/callback-pages`

This must match the backend redirect URI used in:

- auth URL generation
- token exchange
- Meta App Dashboard configuration

## Tests run

- `poetry run python -m py_compile app/main.py app/integrations/meta_ads.py`
- `poetry run python -m pytest -q tests/test_google_auth.py`
- `poetry run python -m pytest -q tests/test_meta_pages_loading.py`
- `poetry run python -m pytest -q tests/test_instagram_business_sync.py`

## Files changed

- [app/integrations/meta_ads.py](/Users/mac/measurable/apps/api/app/integrations/meta_ads.py:1)
- [app/main.py](/Users/mac/measurable/apps/api/app/main.py:7700)
- [tests/test_google_auth.py](/Users/mac/measurable/apps/api/tests/test_google_auth.py:1)
- [BACKEND_FIX_META_OAUTH_FIRST_LOGIN.md](/Users/mac/measurable/apps/api/BACKEND_FIX_META_OAUTH_FIRST_LOGIN.md:1)

## Remaining risks

- If Railway production env still points `API_BASE_URL` or `META_PAGES_REDIRECT_URI` to the wrong public host, Meta will still reject or misroute the callback.
- Existing legacy route `/integrations/meta/callback` remains in the codebase for compatibility, but it is no longer the source of truth for Pages/Instagram popup connect.
- State is now expiring. Very long abandoned popups will fail cleanly instead of proceeding with stale state.
