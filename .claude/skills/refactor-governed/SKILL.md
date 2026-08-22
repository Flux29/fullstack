---
name: refactor-governed
description: Restructure existing code without changing behaviour — repair layering violations, promote a thin service to a thick subpackage, consolidate duplication, or delete dead code. Use when the goal is a smaller or better-shaped codebase rather than new capability. Chains tech-debt analysis, architecture review, simplify, and review inside the governance envelope.
---

# Governed refactor

A refactor here succeeds when the diff is **negative** and behaviour is provably unchanged.
If you cannot say what was deleted or merged, you extended the codebase rather than
restructuring it, and the change record has to justify that.

## 1. Open

Follow `gov-change` GOV-OPEN. Scope `PATHS` to the module under refactor, and write the
reason as the structural defect being removed, not the activity being performed.

## 2. Audit

Invoke the `engineering:tech-debt` skill via the Skill tool, scoped to the target path.
For a module you do not yet understand, run `Explore` or `feature-dev:code-explorer` first
and take its conclusions rather than reading every file into this context. GOV-OPEN has
already unlocked the read gate for this session (`change-start` and the `governance-context`
query both register the unlock) — keep the subagent inside it by rooting its searches in
the component directory, not the repo root.

Rank the findings, then **pick exactly one.** A refactor session that fixes three things
produces a change record no one can review and a diff no one can bisect.

## 3. Plan the shape

Invoke `engineering:architecture` for the chosen finding. Decide against the repo's actual
layering rules, not general principles:

- Routes call **services only** — never import or call a repository directly.
- Services raise domain exceptions (`NotFoundError`, `AlreadyExistsError`), never HTTP errors.
- Repositories are stateless async functions using `db.flush()` + `db.refresh()`, **never**
  `db.commit()` — the session auto-commits via `get_db_session`.
- DI goes through `Annotated` aliases in `deps.py`, never raw `Depends()` in a signature.

**Thin vs. thick is the most common shape decision.** A domain stays a flat module
(`app/services/<domain>.py`) until it owns infrastructure — clients, adapters, pipelines,
parsers, templates. Then it becomes a subpackage (`app/services/<domain>/`) with the infra
inside it and a single facade re-exported from `__init__.py`. Routes and workers import the
facade only; sub-modules are package-internal. Both thick domains ship the facade:
`services/email/` re-exports eagerly, `services/rag/` lazily (PEP 562) so importing it
carries no import-time side effects. The `thick-domains-expose-a-facade` policy rule tracks
any deep import as an advisory finding — a new thick domain ships its facade from the start.

Do not create a new top-level package under `app/`. That level is reserved for framework
concerns (`api/`, `core/`, `db/`, `repositories/`, `schemas/`, `services/`, `worker/`,
`agents/`, `commands/`, `clients/`).

State before editing: **what will be deleted, merged, or moved.** If the answer is "nothing",
stop and reconsider — you are probably about to add an abstraction over the problem instead
of removing it.

### Deletion-only sessions

The strictest shape this skill takes: the diff is all minus. Behaviour, tests, and the
new-files rule are already governed above — what a deletion-only session adds is that every
removed hunk carries a justification of **dead, duplicated, or superseded**, and that the
opening reason says the session is deletion-only, so the record cannot later read as a
restructure.

The failure mode is not timidity. It is confident deletion of something reached by string,
where no import edge exists to warn you. **Absence of a static importer is not proof of
death.**

1. Start from the graph's own answer, `governance/graph/reports/orphans.json`. Its exclusion
   classes are named reasons a module legitimately has no importer — `package-root`,
   `entrypoint`, `dynamic-subtree`, `feature-disabled`, `framework-invoked`, `test-module`,
   `ambient-types`. A module inside one is unknowable, never dead, and `framework-invoked`
   is the largest class in the TypeScript view by an order of magnitude.
2. That report is **module-level**. `no-orphaned-surfaces` in
   `governance/policies/architecture.json` is advisory for exactly that reason: it covers the
   module slice, not routes, tools, or symbols. Below a module, the proof is yours.
3. Grep the name as a **string**, not an identifier, in both languages. The string-keyed
   surfaces here are Next.js convention filenames (`page.tsx`, `layout.tsx`, `route.ts`),
   `next-intl` keys in `frontend/messages/en.json` and `pl.json`, taskiq `@broker.task`
   names, Alembic `down_revision` ids, validator IDs in `governance/validators.json`, and
   `check_impl` dotted paths in `governance/policies/*.json`.
4. WebSocket events are hand-tracked in `governance/manifests/generated/interfaces.json` by
   `producer_ref` and `consumer_ref`, because the payloads are untyped dicts with no schema.
   A handler nothing imports can still be the live half of a contract.
5. i18n keys are per-locale. Removing one from `en.json` and not `pl.json` desyncs silently,
   and no type-checker sees it.

**If you cannot prove a line is unused, leave it and say so in the report.** An unproven line
left in place costs a line. An unproven line deleted costs an incident — and the change
record will have claimed it was justified.

## 4. Execute

Make the move. Then, in order:

1. Invoke `simplify` — reuse, altitude, and consistency cleanups over the changed code.
2. Invoke `review` — the project convention pass with file:line findings.

Behaviour must not change. If a test needs editing to keep passing, that is a behaviour
change: stop, and either revert or reopen the session as a behaviour change with its own
reason.

## 5. Close

Follow `gov-change` GOV-CLOSE. For a backend refactor `governance-impact` will typically
select `backend-lint` and `backend-unit`; a service that owns infra also draws its dedicated
validator (`rag-docker-integration` for RAG, `agent-evals` for the agent surface). Run what
it selects.

Report the line delta explicitly — added, removed, net. A refactor that reports a positive
net without a reason has not met the repo's default.

## Rules

- One structural defect per session.
- Tests are the behaviour contract. Editing them to pass converts a refactor into a rewrite.
- Moving a file is a delete plus an add; make sure the delete actually happened and no
  compatibility shim was left behind "just in case".
- Import sites are part of the refactor. A facade nobody imports is dead code you just wrote.
- If the audit surfaces a bug rather than a structural defect, hand it to `fix-issue` — a
  refactor session is the wrong container for a behaviour fix.
