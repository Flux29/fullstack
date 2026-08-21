---
id: ADR-001
title: Governance adoption baseline
status: accepted
date: 2026-08-06
components:
  - governance-kernel
---

# ADR-001 — Governance adoption baseline

## Status

Accepted, 2026-08-06.

## Context

The repository adopts the governance system described in
`docs/Fullstack_Agentic_Governance_Blueprint.md`. That blueprint is explicit that this
repository is not a blank slate: generator provenance, operational preflights,
Compose-matrix validation, CI, pre-commit and husky hooks, a generated `AGENTS.md`, and
working pydantic-evals already exist. Governance therefore wraps and extends that
machinery and must never become a second, competing control plane.

This ADR records the decisions taken at adoption that must remain understandable after
individual changes have aged out of `Summary.md`.

## Decision 1 — Authority model adopted by reference, not by copy

The authority matrix is the table in the blueprint's **Design principles §1 (Preserve
authoritative sources)**. It is adopted verbatim by reference and is deliberately *not*
duplicated here or in any manifest: duplicating it would create exactly the second source
of truth the blueprint forbids.

Verified in this working tree at adoption time (each row confirmed against the file named):

| Information | Authoritative source | Confirmed |
| --- | --- | --- |
| Operational stacks, preflights, upgrade workflow | `Makefile` (`COMPOSE_BASE/DEV/FRONTEND/PROD`, `preflight-*`, `compose-check*`, `upgrade*`) | yes |
| Containers, networks, ports, profiles, volumes | `docker-compose.yml` + `dev`/`prod`/`frontend` overrides | yes |
| CI matrix | `.github/workflows/ci.yml` | yes |
| Generator provenance and options | `.fastapi-fullstack.json`, `[tool.fastapi-fullstack]` in `backend/pyproject.toml` | yes |
| Backend configuration types and defaults | `backend/app/core/config.py` (`Settings`) | yes |
| Configuration availability | `backend/.env.example`, `frontend/.env.example`, Compose build args | yes |
| Python / frontend dependencies | `backend/pyproject.toml` + `uv.lock`; `frontend/package.json` + `bun.lock` | yes |
| HTTP contracts | FastAPI/Pydantic; OpenAPI via sanitized exporter | yes |
| Database evolution | `backend/alembic/versions/` | yes |
| Frontend→backend mapping | `frontend/src/app/api/**` proxy handlers | yes |
| Per-user MCP catalog | `frontend/src/lib/mcp-catalog.ts` | yes |
| Runtime privileged-tool grants | `mcp_connections` table | yes (runtime-evidence only) |
| Existing hook layers | `backend/.pre-commit-config.yaml`, `frontend/.husky/` + lint-staged | yes |
| i18n surface | `frontend/messages/{en,pl}.json`, `frontend/src/i18n.ts` | yes |

Governance extracts or references these; it never restates them as independent facts.

## Decision 2 — Generator provenance is the upgrade anchor

Recorded from `.fastapi-fullstack.json` at adoption:

- template: `https://github.com/vstorm-co/full-stack-ai-agent-template`
- `template_ref` / `package_version` / `generator_version`: **0.2.17**
- `generated_at`: `2026-07-31T17:23:40.171144+00:00`
- `context_hash`: `sha256:a5e06fa76d2daa119a5c4845273a5d2f27e14d64e2e9845b4cb9abf5567dd6b9`
- `commit`: null

Notable context flags governance depends on: `enable_billing: false` (billing migrations
exist without ORM models — classified *generator-provided, feature-disabled*, never an
orphan), `tenancy: single`, `enable_rag: true`, `vector_store: pgvector`,
`background_tasks: taskiq`, `enable_mcp_client: true`, `enable_i18n: true`, `ci_type: github`.

Generator upgrades run through the existing `make upgrade*` targets and this
`context_hash`; governance does not build a parallel upgrade mechanism.

## Decision 3 — Git remote established (resolves blueprint known-finding #5)

The blueprint recorded "no git remote" as a durability and enforcement-substrate decision
for the repo owner. The owner has established `origin`:

- `https://github.com/Flux29/fullstack.git`, default branch `main`.

Consequence: the blueprint's "until a git remote exists, PR gates run locally" caveat no
longer applies. Phase 5 attaches governance enforcement to CI on the real remote, and
change-record history is durable off-disk.

## Decision 4 — Secret and protected-state boundaries

**Secret boundary.** `backend/.env` and `frontend/.env.local` hold real values, are
gitignored, and are untracked — verified at adoption. From this commit forward
`backend/.env.example` and `frontend/.env.example` are **placeholder-only**: they are
templates, not credential stores. No governance artifact — manifest, graph metadata, log,
change record, or rendered view — may contain a secret *value*; names and classifications
only.

**Protected pre-existing state.** The external Docker volumes `redis-data` and
`docling-models` are declared `external: true` and hold state the repository did not
create. Governance `bootstrap` and every check treat them as read-only facts: they are
never recreated, normalized, or pruned. `make preflight-volumes` remains the authoritative
existence check.

**Runtime evidence.** Any evidence collected from a live system (notably the
`mcp_connections` inventory) is environment-scoped, TTL-bound, stored only under
`.cache/repo-governance/evidence/` (gitignored), stripped of query strings and userinfo at
capture, aggregated rather than row-level, and never committed. Committed artifacts may
reference evidence only by ID and summary.

## Decision 5 — Extraction never imports the application in-process

`find_env_file()` in `backend/app/core/config.py` walks parent directories for `.env`, and
`app/main.py` configures Logfire at module import scope. Importing application modules from
tooling can therefore load real secrets and emit telemetry. Governance extractors use AST
parsing as the primary method; anything that must execute application code (the OpenAPI
export) runs in an isolated subprocess with a scrubbed environment and a working directory
outside the repository tree.

## Consequences

- The blueprint's authority table becomes a checked contract rather than prose; a
  governance check that contradicts an authoritative source is a governance bug.
- Governance commands wrap `make preflight-*` / `compose-check*` rather than
  reimplementing them, so the two can never disagree about the same fact.
- The known findings registered alongside this ADR (see the adoption change record) are the
  honest starting state; checks report them as known, not as newly discovered drift.

## Evidence at adoption

The blueprint was reconciled against the working tree before adoption. These claims were
verified in code, and each is the reason the corresponding check cross-checks rather than
re-declares the fact:

- **Redis logical databases** are allocated 0 general / 1 Taskiq broker / 2 results /
  3 embedding cache — declared in `backend/app/core/config.py`, consumed in
  `backend/app/worker/taskiq_app.py` and `backend/app/services/rag/embedding_cache.py`.
- **The 1024-dimension embedding contract is already triple-enforced**: the settings
  default, a hard guard plus `vector(1024)` DDL and HNSW index in
  `backend/app/services/rag/vectorstore.py`, and a CHECK constraint in
  `backend/alembic/versions/0027_embedding_cache_and_rag_metadata.py`. Per-collection
  fingerprints are persisted and revalidated on open. This is what ADR-002 formalizes.
- **General and RAG object storage are separate buckets** with separate credentials and
  endpoints, not one bucket with prefixes.
- **GitHub MCP read-only is enforced at three independent layers** — container command
  flags in Compose, a settings-validator allowlist frozenset, and a runtime post-probe
  assert in `backend/app/agents/mcp.py`. Drift between any two layers is the finding worth
  reporting; re-stating the policy in governance would have created a fourth copy.
- **The Compose profile matrix was already validated** by `make compose-check` and
  `make compose-check-prod`, and production images were already digest-pinned. Governance
  wraps these targets instead of reimplementing them.
- **Structured evaluation already had a working beachhead** in `backend/evals/`
  (pydantic-evals, exercised by the normal test run), which is why evaluation is treated
  as an existing mechanism to extend rather than a new one to build.

The blueprint also cited example paths that do not exist in this repository — notably
`backend/app/rag/` (really `backend/app/services/rag/`) and `frontend/src/features/chat/`
(the chat feature spans four roots, which is why `chat-frontend` is declared in
`architectural-intent.json` rather than annotated in a directory).

## References

- `docs/Fullstack_Agentic_Governance_Blueprint.md` — the specification.
- `governance/history/changes/2026-08-06-governance-adoption-baseline.json` — the findings
  registry and the file-level record of this commit.
