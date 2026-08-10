---
name: frontend-feature
description: Build a new page, view, or data-driven feature in the Next.js frontend. Use when adding a route under the dashboard/marketing area, wiring UI to a backend endpoint, adding client state, or creating a localized page. Covers App Router, data fetching, Zustand stores, and i18n. Runs inside the gov-change envelope; gated by the frontend-test validator plus a browser check.
---

# Frontend Feature (Next.js 15 + React 19)

The frontend lives in `frontend/src/` — App Router, TypeScript, Tailwind, `next-intl`, and Zustand. Routes are **locale-prefixed**: `app/[locale]/…`.

This is a governed change: open with `gov-change` GOV-OPEN (`PATHS="frontend/src"`),
close with GOV-CLOSE. Expect `governance-impact` to select `frontend-test`.

## Layout

| Path | Purpose |
|------|---------|
| `src/app/[locale]/…` | Pages (route groups: `(dashboard)`, `(marketing)`, `(auth)`) |
| `src/app/api/…` | Next.js route handlers that proxy to the backend |
| `src/lib/` | API clients (`api-client.ts`, `*-api.ts`), `query-keys.ts`, helpers |
| `src/components/` | UI by domain (`chat/`, `kb/`, `dashboard/`, `ui/`, …) |
| `src/stores/` | Zustand stores (one per concern, re-exported from `index.ts`) |
| `src/hooks/` | `useChat`, `useWebSocket`, etc. |

## Steps

1. **Page** — add `src/app/[locale]/(dashboard)/<feature>/page.tsx`. Default to a **Server Component**; add `"use client"` only where you need interactivity. Read params via the async App Router APIs.

2. **Data access** — add a typed client in `src/lib/<feature>-api.ts` built on `api-client.ts`. Don't scatter `fetch` calls in components. For server-side fetching use `server-api.ts`. If the backend needs a same-origin proxy (auth cookies), add a handler under `src/app/api/…`.

3. **Caching keys** — register query keys in `src/lib/query-keys.ts` so cache invalidation stays consistent.

4. **Client state** — if the feature needs shared client state, add a store in `src/stores/<feature>-store.ts` and export it from `stores/index.ts`. Keep server data in the data layer; use stores for UI/ephemeral state.

5. **Components** — put reusable pieces in `src/components/<domain>/`; compose primitives from `src/components/ui/`. Keep components under ~100 lines — extract when they grow.

6. **i18n** — user-facing copy goes through `next-intl` messages, not hardcoded strings. Add keys to the message catalog and read them with `useTranslations` / `getTranslations`.

7. **Verify — three layers, not one:**
   ```bash
   make frontend-test    # the registered validator: lint, type-check, AND vitest
   ```
   Then look at it: `bun dev` against a running backend (`make dev`) and drive the page
   in a browser — the Browser pane or Playwright MCP counts; "types pass" does not. UI
   is not done until it has been seen rendering. If the feature spans the proxy hop and
   a stack is running, the `playwright` validator is the only end-to-end proof.

## Rules

- Server Components by default; `"use client"` only when needed (state, effects, event handlers).
- All API calls go through `src/lib/` clients; components consume hooks/clients, not raw `fetch`.
- Localized strings via `next-intl` — never hardcode user-facing copy.
- Match the existing folder-by-domain structure; reuse `components/ui/` primitives.
