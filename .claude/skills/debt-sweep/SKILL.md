---
name: debt-sweep
description: Audit the repository for technical debt and work it down one governed session at a time — dead code, duplication, layering violations, lint and type debt, coverage gaps, stale dependencies. Use for "what should we clean up", a recurring code-health pass, or when debt has accumulated and needs ranking rather than a specific known fix.
---

# Debt sweep

Two phases with a hard boundary between them. The **survey** is read-only and cheap. The
**paydown** is one governed change session per item — never one session for the batch.

Batching is the failure mode this skill exists to prevent. A single session that fixes nine
debt items produces a change record whose stated reason cannot be true of all nine, a diff
nobody reviews, and a bisect that lands on a 900-line commit.

## Phase 1 — Survey (read-only, no change session)

```bash
make governance-check-fast     # blocking, advisory, and not-yet-enforced counts
make governance-summary        # open findings, accepted exceptions, active contracts
```

Then invoke `engineering:tech-debt` via the Skill tool, scoped to the area under review.
For a repo-wide sweep, delegate the reading to `Explore` rather than pulling every module
into this context — but run one scoped `make governance-context PATHS="..." TASK="..."`
query **before** spawning it: discovery sweeps (Glob/Grep at the repo root or a bare
top-level directory) draw gate warnings until a scoped query or a `governance-change-start`
has registered in the session. Give the subagent component directories to search, not
repo-root globs.

Cross-check the skill's findings against governance's own:

- Items already recorded as **accepted exceptions** in
  `governance/manifests/curated/exceptions.json` are deliberate, reviewed departures. They
  are not debt. Do not "fix" them; if one looks wrong, that is a governance change with its
  own argument, not a cleanup.
- Items in the **not-yet-enforced** count are policies on the maturity ladder. Promoting one
  is a governance change, not a code change.
- Untracked files outside `backend/tests/`, `frontend/e2e/`, and the frontend source tree are
  flagged by `governance check` — usually stray verification scripts that belong in the
  scratchpad, and usually the cheapest deletion available.

Also worth a direct look, since these produce ranked, actionable output:

```bash
make lint                    # ruff + ty debt, file by file
make test-cov                # coverage against the ratcheted floor
make governance-skills-check # citation integrity of the agent surface
make governance-gate-metrics # read-gate verdicts: where agents are being denied
```

The **agent surface is part of the sweep**: skills-check proves citations resolve, not
that prose is true — spot-check one skill per sweep against the code it describes.
Known standing item: **the `services/rag/` facade** — the thick-domain rule promises a
facade that rag has never had, and its ~25 direct sub-module import sites
(`main.py`, `deps.py`, worker, CLI) are the `thick-domains-expose-a-facade` advisory
findings. Shape: `refactor-governed`.

## Phase 2 — Rank

Order by **evidence of harm**, not by count. A rule violated 200 times in generated-adjacent
code matters less than one route that calls a repository directly.

Rank highest:

1. **Layering violations** — routes touching repositories, `db.commit()` in a repository,
   services returning `None` for "not found". These break invariants the whole codebase
   assumes.
2. **Security-policy divergence** — anything `governance/policies/security.json` covers:
   exposure invariants, secret handling, MCP authorization.
3. **Contract drift** — a backend route whose proxy handler or frontend caller was never
   updated. Silent breakage.
4. **Dead code** — highest value per line, lowest risk, and it moves the diff negative.
5. **Duplication** — consolidate only where the copies genuinely share a reason to change.
6. **Lint / type debt** — mechanical, safe, but rarely the thing actually hurting you.
7. **Coverage gaps** — rank by blast radius, not by percentage.

Present the ranking with a one-line harm statement per item and stop. Let the user choose.
Do not begin paydown on an unranked list, and do not start work on item 1 while presenting
the ranking as though it were the whole plan.

## Phase 3 — Pay down, one session per item

For each chosen item, route it to the skill that fits its shape:

| Item shape | Skill |
| --- | --- |
| Structural — layering, thin→thick, duplication, dead code | `refactor-governed` |
| A real defect the audit surfaced | `fix-issue` (regression test first) |
| Missing tests | `pytest-suite`, planned with `engineering:testing-strategy` |
| Mechanical lint / type debt | `simplify`, then `make lint` |
| Stale or vulnerable dependency | `gov-change` directly; validators `backend-unit`, `frontend-test`, `compose-check` |
| RAG-subsystem debt | `rag-change` |

Each runs its own full envelope. Finish and close one before opening the next.

⏸ **Checkpoint between items:** run `/context`. If free context is under ~25%, close the
current record and resume the sweep in a fresh session. A chained sweep that runs out of
context mid-item leaves a change session open and the `Stop` hook blocking.

## Phase 4 — Report

Report per item: what was deleted or consolidated, the net line delta, which validators ran.
Then state what remains on the ranked list and was **not** done. A sweep that reports only
completed items reads as "the repo is clean now", which is almost never true.

## Rules

- One item, one session, one reason. No batches.
- Accepted exceptions are not debt.
- Never raise the coverage floor by deleting tests, and never lower it to make a session pass.
- Deletion beats abstraction. If an item can be resolved by removing code, prefer that.
- The survey is read-only. Do not start editing during phase 1 — findings you fix while
  auditing land outside any change session and trip the `Stop` hook.
