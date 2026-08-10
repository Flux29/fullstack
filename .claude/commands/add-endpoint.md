---
description: Scaffold a new API endpoint with full layering
---

Create a new API endpoint: $ARGUMENTS

This command is the backend scaffold only — the dependency order below. The conventions
for each layer auto-load from `.claude/rules/` when you edit the matching file; do not
restate them, follow them. For the full delivery (proxy handler, frontend caller,
migration, tests, governance envelope), this runs as step 2 of `endpoint-fullstack` —
invoke that skill instead when the endpoint must reach the browser.

Create files in dependency order:

1. **Schema** — `backend/app/schemas/` (`*Create`, `*Update`, `*Read`, `*List`)
2. **DB Model** — `backend/app/db/models/` (import it in `models/__init__.py` so autogenerate sees it)
3. **Repository** — `backend/app/repositories/`
4. **Service** — `backend/app/services/`
5. **DI** — factory + `Annotated` alias in `backend/app/api/deps.py`
6. **Route** — `backend/app/api/routes/v1/` (reuse the entity's existing route module if one exists)
7. **Register** the router in `backend/app/api/routes/v1/__init__.py`
8. **Migration** — invoke `alembic-migration` (review autogenerate; round-trip once)
9. **Tests** — invoke `pytest-suite` (success path, auth failure, domain-exception path)
10. **Lint** — `make lint`

Auth is explicit at step 6: `CurrentUser`, `CurrentAdmin`, or `ValidAPIKey` — decide
which; never default to unauthenticated.
