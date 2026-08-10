# Frontend Conventions

## Stack

- Next.js 15 App Router + React 19, TypeScript strict mode
- Tailwind CSS; UI primitives in `frontend/src/components/ui/`
- `next-intl` for i18n — routes are **locale-prefixed**: `app/[locale]/…`
- Zustand for client state, TanStack Query keys in `frontend/src/lib/query-keys.ts`

## Structure

| Path | Purpose |
|------|---------|
| `frontend/src/app/[locale]/…` | Pages (route groups: `(dashboard)`, `(marketing)`, `(auth)`) |
| `frontend/src/app/api/…` | Proxy route handlers to the backend |
| `frontend/src/lib/` | API clients (`api-client.ts`, `server-api.ts`, `*-api.ts`), helpers |
| `frontend/src/components/` | UI by domain (`chat/`, `kb/`, `dashboard/`, `ui/`, …) |
| `frontend/src/stores/` | Zustand stores, re-exported from `index.ts` |
| `frontend/src/hooks/` | `useChat`, `useWebSocket`, etc. |
| `frontend/src/types/` | Shared types |

## Conventions

- Server Components by default; `"use client"` only where interactivity requires it
  (state, effects, event handlers)
- **Every REST call from the browser goes through a proxy handler in
  `frontend/src/app/api/**`** — the chat WebSocket (`/api/v1/ws/agent`) is the one
  documented exception that talks to the backend directly
- Components consume typed clients from `frontend/src/lib/` — never raw `fetch` in a
  component; server-side fetching goes through `server-api.ts`
- Register query keys in `frontend/src/lib/query-keys.ts` so cache invalidation stays
  consistent
- Stores hold UI/ephemeral state only; server data lives in the data layer
- User-facing copy goes through `next-intl` messages — never hardcode strings
- Keep components under ~100 lines — extract when they grow

## Verify

```bash
make frontend-test    # lint, type-check, vitest — the registered validator
```
