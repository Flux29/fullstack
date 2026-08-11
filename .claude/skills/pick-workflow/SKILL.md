---
name: pick-workflow
description: Choose the right workflow skill and command chain before starting work in this repository. Use at the start of any non-trivial task, when a request could plausibly be a refactor, a fix, or a feature, or when unsure whether a change needs a governance session. Routes to gov-change, refactor-governed, rag-change, endpoint-fullstack, or debt-sweep and names the validators the work will need.
---

# Pick the workflow

Answer the questions in order. The first one that matches wins — they are ordered by how
expensive the mistake is, not by how common the case is.

## Q1 — Does this change any tracked file?

**No** (explaining, exploring, mapping, answering a question) → **no change session.**
Use `make governance-context PATHS="..." TASK="..."`, then `Explore` or
`feature-dev:code-explorer` for breadth. Opening a session you never finish leaves the `Stop`
hook blocking turn-end for the next person.

**Yes** → continue. Every branch below runs inside `gov-change`'s envelope.

## Q2 — Is the work *known*, or does it need ranking first?

Needs ranking ("what should we clean up", "reduce tech debt", "code health") →
**`debt-sweep`**. It surveys read-only, ranks by harm, and then routes each item back into
this table one governed session at a time.

Known target → continue.

## Q3 — Does it touch `backend/app/services/rag/`, pgvector, the embedding cache, or Docker Model Runner?

→ **`rag-change`**. Take this branch even when the change looks like a plain refactor. The
subsystem's failure mode is silent: mismatched vectors produce plausible answers, and no unit
test catches it. `rag-change` carries the embedding-dimension compatibility gate that the
generic refactor path does not.

Operational-only work — ingest a document, run a search, list collections, no code change —
is **`rag-knowledge`** instead, and needs no session.

## Q4 — Does it change an HTTP contract the browser consumes?

A new REST endpoint, a changed request or response shape, a changed status code, new auth on
an existing route → **`endpoint-fullstack`**.

This branch exists because backend-only work passes every backend validator while leaving the
endpoint unreachable: every browser REST call goes through a Next.js proxy handler in
`frontend/src/app/api/**`. Chat WebSocket (`/api/v1/ws/agent`) is the one exception.

Frontend-only work against an endpoint that already exists and already proxies →
**`frontend-feature`** inside `gov-change`.

## Q5 — Does behaviour change?

**No — same behaviour, better structure.** Layering repair, thin→thick promotion,
consolidation, dead-code deletion → **`refactor-governed`**. Its contract is a negative diff
and untouched tests.

**Yes — something is wrong and should behave differently.** → **`fix-issue`** inside
`gov-change`, regression test first. A symptom from a **running deployment** starts at
**`prod-debug`** (Logfire traces before code); a cause not yet understood locally runs
`engineering:debug` before `fix-issue`.

**Yes — new capability.** Route by surface:

| Surface | Skill |
| --- | --- |
| Agent tool / PydanticAI toolset | `agent-tool` → validator `agent-evals` |
| MCP server, per-user connection, OAuth, catalog | `mcp-connection` + `security-review` |
| Taskiq job, schedule, webhook, email | `background-task` |
| Next.js page, view, store, i18n | `frontend-feature` |
| DB schema, column, index, backfill | `alembic-migration` → validator `migrations` |
| Tests for existing code | `pytest-suite`, planned with `engineering:testing-strategy` |
| End-to-end proof of a user flow | `e2e-playwright` → validator `playwright` |
| Large multi-layer feature | `engineering:system-design` → `feature-dev:feature-dev`, then `review` |

## Q6 — Is it infrastructure, config, or governance itself?

| Work | Path |
| --- | --- |
| New or renamed env var | edit source → `governance-sync` regenerates `ENV_VARS.md` (never hand-edit) → `compose-check` |
| Compose / profile / service | `gov-change` → `compose-check` → `preflight-ports`, `preflight-volumes` → `compose-check-prod` |
| Curated manifest, exception, ADR | `gov-change`; generated manifests are rebuilt, never edited |
| Template upgrade | `template-upgrade` (dry-run → classify against ownership provenance → sweep) |
| CLAUDE.md, hooks, skills | `gov-change` + `claude-md-management:claude-md-improver` / `hookify:hookify` / `skill-creator` |

## Cross-cutting passes

These attach to a branch above rather than replacing it:

- `security-review` — anything touching auth, secrets, API keys, MCP authorization, or
  user-supplied input reaching a query.
- `simplify` then `review` — the standard tail of every code-touching branch.
- `engineering:code-review` — a second, adversarial pass when the change is risky.
- `ship` — the close of every branch: commit **after** `governance-change-finish`,
  push, and the PR flow when asked. It carries the single-branch rules.

## Composition rules

- `gov-change` is the frame. Every other workflow skill calls it rather than restating it.
- `debt-sweep` dispatches; it never edits during its own survey phase.
- One reason per change session. If a branch surfaces a second problem, finish the current
  session and open another.
- Chained skills share one context. Put reading-heavy phases behind `Explore` or `Plan`
  subagents, and add a ⏸ `/context` checkpoint before the close of any chain longer than four
  skills. `/context` and `/compact` are terminal commands — a skill cannot invoke them, so the
  checkpoint has to be handed to the user explicitly.
- **Unlock the read gate before delegating readers.** Glob/Grep rooted at the repo root
  or a bare top-level directory draws a gate warning until a scoped
  `make governance-context` / `governance-impact` query — or the
  `make governance-change-start` you likely already ran — has registered this session,
  and the gate covers subagents too. Open the envelope or run the scoped query *first*,
  then spawn `Explore`, and put component directories (not repo-root globs) in the
  subagent's prompt so its searches stay inside allowed roots.

## When two branches both fit

- *Refactor that also fixes a bug* → two sessions. Refactor first with tests untouched, then
  `fix-issue` with a regression test.
- *Endpoint whose service needs restructuring* → `refactor-governed` first, then
  `endpoint-fullstack`. Never restructure and extend under one reason.
- *RAG change that is also a schema change* → `rag-change`; it chains `alembic-migration`
  itself and knows the vector column is not convertible in place.
- *Nothing fits* → `gov-change` alone. The envelope is always correct; only the filling
  varies.
