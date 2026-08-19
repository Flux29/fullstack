---
name: e2e-playwright
description: Write or run Playwright end-to-end tests in frontend/e2e — the only validator that proves the browser-to-proxy-to-backend chain actually works. Use when a change spans the proxy hop, when a user-visible flow needs proof beyond unit tests, or when asked to run e2e. Knows the auth.setup project, the stack prerequisite, and when the spin-up is worth it.
---

# End-to-end tests (Playwright)

Specs live in `frontend/e2e/` — `home.spec.ts`, `auth.spec.ts`, `chat.spec.ts`,
`integrations.spec.ts` — with `auth.setup.ts` as the setup project that authenticates
first and shares its storage state with the rest. The registered validator is
`playwright` (`make playwright`), `requires: running-stack`, `ci_job: e2e` — CI runs
it on chromium against a backend booted on the pgvector and redis services with
`LLM_PROVIDER=test` (deterministic fake replies, no key). A local run adds the other
browsers and a real model; a CI run is the evidence on every push.

## When the spin-up is worth it

No backend test can prove the proxy hop, and `make frontend-test` mocks the network.
So run e2e when the change touches: a proxy handler in `frontend/src/app/api/**`, an
auth flow, the chat WebSocket path, or any contract both sides of the hop consume.
Skip it for pure styling, copy, or component-internal changes — `make frontend-test`
plus a browser look covers those (see `frontend-feature`).

## Run

```bash
make dev              # the stack: backend, db, redis - e2e is meaningless without it
make playwright       # the registered validator
```

The Playwright config self-starts the frontend (`bun run dev`, reused if already up)
and targets `http://localhost:3000` (`PLAYWRIGHT_BASE_URL` to override). Useful modes
from `frontend/`:

```bash
bun run test:e2e:ui        # headed, interactive
bun run test:e2e:debug     # step through
bun run test:e2e:report    # open the last HTML report
```

## Write

- New spec: `frontend/e2e/<flow>.spec.ts`. Reuse the auth setup project's storage state
  for authenticated flows instead of logging in per-test; extend `auth.setup.ts` only
  when a *new kind* of principal is needed.
- Assert on user-visible outcomes (text, navigation, rendered state), not on network
  internals — the point of e2e is the user's view of the chain.
- Keep specs independent and parallel-safe (`fullyParallel` is on); no shared mutable
  fixtures between files.
- Failure artifacts: traces are collected on retry — read the trace before adding
  `waitForTimeout`-style patches; flake fixed by sleeping is flake kept.

New specs are a governed change like any other (`gov-change` envelope); `frontend/e2e/`
is a sanctioned location for new files.

## Rules

- A red e2e run with a running stack is a real finding — never mark the work done with
  it red, and never delete a spec to make a session pass.
- A skipped run is not a passing run: if the stack was not available, the record says
  "playwright: not run - no stack", not "tests pass".
- e2e proves the chain; it does not replace `pytest-suite` (backend behavior) or
  `make frontend-test` (types, lint, unit) — those still run.
