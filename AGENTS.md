# AGENTS.md

The entry point for every AI coding agent working in this repository — Claude Code, Codex,
Copilot, Cursor, Zed, OpenCode. Read this file, then retrieve only what your task needs.

**Do not recursively read `governance/`.** It is a queryable control plane, not documentation.
Ask it questions with the commands below; it returns bounded, source-backed answers.

## Project

**fullstack** — a FastAPI + Next.js application generated from the
[Full-Stack AI Agent Template](https://github.com/vstorm-co/full-stack-ai-agent-template) (v0.2.17).

FastAPI + Pydantic v2 · PostgreSQL/pgvector via asyncpg · Redis · Taskiq · PydanticAI
agents with MCP · RAG · Next.js 15 with i18n · JWT and API-key auth.

## Entry rules

Follow these in order for any non-trivial change.

1. **Run the governance preflight.** `make governance-preflight`
   Reports drift that already exists, so the drift you cause stays distinguishable from it.
2. **Start a governed change session with a stated reason.**
   `make governance-change-start SUMMARY="..." REASON="..."`
   The reason is captured before it can be lost. A diff shows what changed, never why.
3. **Retrieve task-specific context** instead of recursively reading governance files.
   `make governance-context PATHS="backend/app/..." TASK="..."`
4. **Make the smallest coherent change.**
5. **Synchronize manifests and evidence.** `make governance-sync`, then read the diff.
6. **Run the validators the impact analysis selects.** `make governance-impact PATHS="..."`
   returns validator IDs; resolve them through `governance/validators.json`.
7. **Finalize the change record and regenerate the summary.**
   `make governance-change-finish`
8. **Run the read-only full check.** `make governance-check`

If a generated manifest disagrees with an authoritative source, the manifest is stale.
Never change application behaviour to make a stale generated file pass.

Report remaining uncertainty rather than inventing missing facts.

## Commands

The Makefile is the single operational entry point. Prefer it over raw `uv`/`docker` calls.

| Purpose | Command |
| --- | --- |
| Governance preflight / check / sync | `make governance-preflight` · `make governance-check` · `make governance-sync` |
| Governance context / impact / explain | `make governance-context` · `make governance-impact` · `make governance-explain ID=...` |
| Governance views (HTML to artifacts/) | `make governance-visualize VIEW=architecture\|site\|impact\|configuration\|migration\|security` |
| Governance diagnosis / agent-surface citations | `make governance-doctor` · `make governance-skills-check` |
| Backend lint / tests | `make lint` · `make test` · `make test-cov` |
| Frontend lint, types, unit tests with coverage | `make frontend-test` |
| Frontend format check / production build | `make frontend-format-check` · `make frontend-build` |
| End-to-end (local only, needs a running stack) | `make playwright` |
| Dev stack up / down / logs | `make dev` · `make dev-down` · `make dev-logs` |
| Compose matrix validation | `make compose-check` |
| Operational preflights | `make preflight-volumes` · `make preflight-model` · `make preflight-ports` |
| Migrations | `make db-migrate` · `make db-upgrade` · `make db-current` |
| Template upgrade | `make upgrade-dry-run` · `make upgrade` |

Run a single backend test with `uv run --directory backend pytest tests/test_file.py::test_name -v`.

## Hard boundaries

Non-obvious rules that are easy to violate:

- Repositories use `db.flush()` + `db.refresh()`, **never** `db.commit()` — the session
  auto-commits via `get_db_session`.
- Routes call services only — **never** import or call repositories directly.
- Route handlers return `-> Any`; serialization is handled by `response_model`.
- `datetime.now(UTC)`, never `datetime.utcnow()`.
- `secrets.compare_digest()` for API key comparison, never `==`.
- Every REST call from the browser goes through a Next.js proxy handler in
  `frontend/src/app/api/**`. An API change is not complete until the proxy handler and its
  frontend caller are updated too. Chat WebSocket (`/api/v1/ws/agent`) is the one
  documented exception that talks to the backend directly.
- Creating a **new file** is a last resort; extend an existing module first. Ad-hoc
  verification scripts, one-off runners, and debug harnesses go in the session scratchpad,
  never in the repository. Durable tests go in `backend/tests/`, `frontend/e2e/`, or beside
  the frontend source they cover. A `PreToolUse` hook in `.claude/settings.json` prompts for
  confirmation on a new-file Write; `governance check` lists untracked files outside these
  locations.
- Reviewer and checker agents verify against authoritative sources — the diff, the
  neighboring code, the actual imports — never against the governance briefing the
  implementing agent already consumed. A checker that reads the same summary inherits the
  same blind spots; checkers are only a real check on the map if they read the territory.
- Lifecycle hooks in `.claude/settings.json` enforce parts of this file (steering, not a
  security boundary — they fail open, announce their own degradation, and never run
  `sync` themselves). `SessionStart` injects a compact orientation map. A read gate on
  `Read|Glob|Grep` (a stdlib script reading the sync-generated
  `governance/manifests/generated/read-surface.json`) blocks bulk enumeration of
  `governance/history/` and `governance/manifests/` — query those instead — blocks reads
  outside the governed surface, and warns on discovery sweeps (Glob/Grep rooted at the
  repository root or a bare top-level directory) until one scoped `governance context` /
  `governance impact` query or a `governance change start` has run in the session;
  navigate by the map, then search. Reading one named file is always allowed, as is any
  search rooted inside a component directory. A `Stop` hook
  blocks turn-end with exact repair commands while touched files lack a change session or
  record, or generated files are stale. Modes are set in the `[gate]` table of
  `governance/governance.toml`; the `GOVERNANCE_GATE` environment variable (`off|warn|deny`,
  user-set only) overrides both surfaces.
- Default to a **net-zero or net-negative diff**. Reuse, consolidate, or delete before you
  add. A change that only adds has to say, in its change record, why nothing could be
  removed or reused instead.
- Never commit a secret value. `.env.example` files are placeholder-only templates.
- Never edit a generated file by hand. `governance/manifests/generated/**`,
  `governance/catalog.json`, `governance/Summary.md`, and `ENV_VARS.md` are rebuilt by
  `make governance-sync`; `governance/manifests/curated/**` and `.governance.json`
  annotations are yours to edit.

## Where to look

Read these when the task touches them — not before.

| Need | Source |
| --- | --- |
| What exists, what owns it, what is unresolved | `governance/catalog.json`, then `governance/Summary.md` |
| Conventions for the file you are editing | `.claude/rules/*.md` — path-scoped: `architecture`, `api-conventions`, `schemas-models`, `exceptions-security`, `code-style`, `testing`, `frontend` |
| Why a durable decision was made | `governance/history/decisions/` (indexed in `index.json`) |
| Why a recent change was made | `governance/history/changes/` — query by component or path, do not read the directory |
| What proves a change works | `governance/validators.json` |
| Longer-form architecture and how-to | `docs/architecture.md`, `docs/adding_features.md`, `docs/testing.md`, `docs/patterns.md` |
| The governance system itself | `docs/Fullstack_Agentic_Governance_Blueprint.md` |

Claude Code additionally auto-loads `CLAUDE.md` and the matching `.claude/rules/*` file.

## Structure

```
backend/app/
├── api/routes/v1/    # HTTP and WebSocket endpoints
├── services/         # Business logic (rag/ and email/ are thick subpackages)
├── repositories/     # Data access
├── schemas/          # Pydantic models
├── db/models/        # SQLAlchemy models
├── agents/           # PydanticAI agent, toolsets, MCP client, Google REST executor
├── worker/           # Taskiq tasks and scheduler
└── commands/         # CLI commands (auto-discovered)

frontend/src/
├── app/[locale]/     # Pages
├── app/api/          # Proxy route handlers to the backend
├── components/       # UI
├── hooks/ stores/    # Client state
└── lib/              # API clients and shared utilities

governance/           # The control plane — query it, do not read it recursively
tools/repo_governance/# The governance CLI (standalone uv project, never imports app.*)
```
