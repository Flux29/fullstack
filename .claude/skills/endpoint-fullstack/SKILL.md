---
name: endpoint-fullstack
description: Deliver a new or changed API endpoint all the way to the browser — schema, model, repository, service, DI, route, migration, Next.js proxy handler, frontend caller, and tests. Use whenever a REST surface is added or its contract changes. Exists because a backend-only endpoint looks finished, passes every backend validator, and is still unreachable from the UI.
---

# Full-stack endpoint

The recurring failure this skill prevents: the backend layers get built, `make test` passes,
the change record closes — and the browser cannot call the endpoint, because **every REST
call from the browser goes through a Next.js proxy handler in `frontend/src/app/api/**`.**
An API change is not complete until the proxy handler and its frontend caller are updated too.

The one documented exception is the chat WebSocket (`/api/v1/ws/agent`), which talks to the
backend directly. Everything else proxies.

## 1. Open

Follow `gov-change` GOV-OPEN with
`PATHS="backend/app/api/routes/v1,backend/app/services,frontend/src/app/api"`.

For a change to an existing contract, read `governance/manifests/generated/interfaces.json`
through `governance-context` rather than directly — it holds the HTTP contracts, the proxy-hop
requirement, and the WebSocket event inventory.

## 2. Backend layers

Invoke `add-endpoint` via the Skill tool. It scaffolds in dependency order: schema → DB model
→ repository → service → DI alias → route → router registration.

Enforce while it runs:

- Routes call **services only**. A route that imports a repository is a layering violation.
- Services raise domain exceptions with `message` and `details`; never HTTP errors, never
  `None` for "not found".
- Repositories: stateless async functions, keyword-only args after `db`, `db.flush()` +
  `db.refresh()`, **never** `db.commit()`.
- Route handlers return `-> Any`; `response_model` does serialization.
- `201` on POST, `204` + `response_model=None` on DELETE.
- Pagination: `skip: int = Query(0, ge=0)`, `limit: int = Query(50, ge=1, le=100)`, returning
  a `*List` schema with `items` and `total`.
- Auth is explicit: `CurrentUser`, `CurrentAdmin`, or `ValidAPIKey` — decide which, do not
  default to unauthenticated.

## 3. Migration

If a DB model was added or changed, invoke `alembic-migration`. Autogenerate is a draft:
review `upgrade()` and `downgrade()`, confirm `down_revision` chains onto the current head,
and round-trip once. A model change without a migration **passes the backend suite** — the
session is mocked — and breaks on real Postgres.

## 4. Cross the proxy — the step that gets skipped

Invoke `frontend-feature`. Do not treat the endpoint as done until all three exist:

1. **Proxy handler** in `frontend/src/app/api/**` — forwards to the backend route, passes auth
   through, maps errors.
2. **Client function** in `frontend/src/lib/` — typed against the endpoint's schemas.
3. **Caller** — the page, component, or store that actually uses it, Server Component by
   default; `"use client"` only where interactivity requires it.

Types belong in `frontend/src/types/`. If the endpoint is user-visible, it needs its i18n
strings in the same pass.

## 5. Test

Invoke `pytest-suite`. API tests use `httpx.AsyncClient`, not `TestClient`. Cover the
success path, the auth failure, and the domain-exception path (`404`/`409` as applicable).

Frontend gets `make frontend-test`. If the endpoint drives a user-visible flow and a stack is
running locally, `playwright` is the only thing that proves the proxy hop actually works
end to end — no backend test can.

## 6. Tidy and close

Invoke `simplify`, then `review`. Then follow `gov-change` GOV-CLOSE. Expect
`governance-impact` to select `backend-lint`, `backend-unit`, `frontend-test`, and
`migrations` where a migration was added.

Before finishing the record, verify the proxy hop by name: state the handler path, the client
function, and the caller. If you cannot name all three, the endpoint is not delivered.

## Rules

- Backend-only is not done. Name the proxy handler or the change is incomplete.
- New files are expected here, but reuse an existing route module when the entity already has
  one — do not create a parallel module per verb.
- Never widen `limit` past 100 or drop the `ge=0` on `skip`.
- A changed response shape is a contract change: the proxy handler, the client types, and
  every caller move together, in this session.
