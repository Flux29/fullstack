---
name: template-upgrade
description: Upgrade this repository against a new release of its generator template (fastapi-fullstack) without clobbering local divergence. Use when a template release lands, when asked to pull in upstream features, or when make upgrade-dry-run shows pending changes. The largest-blast-radius operation in the repository — dry-run first, classify every conflict against ownership.json provenance, full validator sweep after.
---

# Template upgrade (fastapi-fullstack)

This repository was generated from the Full-Stack AI Agent Template and has since
diverged deliberately. The upgrade tooling does not know which divergence is drift and
which is decision — **`governance/manifests/curated/ownership.json` does.** Its
`generator_provenance` records name each diverged file and how its conflicts resolve:
`classify-conflict` (a human call, local version presumed right), `regenerate`
(governance rebuilds it), `accept-upstream`. Files without a record are presumed
upstream's to update.

Run the whole thing inside `gov-change` — one upgrade, one session, one record.

## 1. Dry-run and classify

```bash
git status -sb            # clean tree first; an upgrade over dirty state is unreviewable
PYTHONUTF8=1 make upgrade-dry-run
```

`PYTHONUTF8=1` is load-bearing on Windows: the upstream tool prints Unicode arrows and
dies mid-plan on a cp1252 console without it (verified 2026-08-10, v0.2.17 plan).

Sort every file the dry-run would touch into three piles using `generator_provenance`:

- **Recorded, `classify-conflict`** — the local version embodies a decision
  (AGENTS.md, CLAUDE.md, CI workflow, the governance-wired skills and commands,
  `.env.example`, `backend/pyproject.toml`). Resolve in favour of local unless the
  upstream change is material — then merge deliberately and update the record's reason.
- **Recorded, `regenerate`** — e.g. `ENV_VARS.md`: take either side, then
  `make governance-sync` rebuilds it; never hand-merge.
- **Unrecorded** — presumed safe to accept. If accepting one *feels* wrong, that is a
  missing provenance record: add it in this session.

For a material architectural conflict, run `engineering:architecture` before deciding —
an upgrade is not a reason to silently re-litigate a recorded decision.

## 2. Apply

```bash
make upgrade                  # or: make upgrade-new-features to opt into new template features
# resolve conflicts per the classification above
make upgrade-finalize
```

## 3. Prove nothing regressed

The blast radius is "everything the generator ever touched", so the sweep is wide:

```bash
make governance-sync          # regenerates manifests; READ the diff - it is the upgrade's true footprint
make governance-skills-check  # upstream skill/command edits may cite paths this repo does not have
make lint && make test
make frontend-test
make compose-check
make governance-check
```

Add `migrations` and `rag-docker-integration` (resolve via `governance/validators.json`)
when the upgrade touched models or the RAG surface.

## 4. Close and ship

GOV-CLOSE, then `ship`. The record lists which conflicts were resolved in favour of
local, which upstream changes were adopted, and any **new** provenance records added.

## Rules

- Never upgrade over a dirty tree, and never mix an upgrade with a feature change.
- A conflict on a `classify-conflict` file is never auto-resolved — each one is named
  in the record with its resolution.
- If the upgrade rewrites a generated file (`ENV_VARS.md`, manifests), the fix is
  `make governance-sync`, not a hand edit.
- An upgrade that touches `.claude/` must leave `make governance-skills-check` green —
  upstream template skills cite template paths, not necessarily this repository's.
