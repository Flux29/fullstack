---
name: gov-change
description: Wrap a repository change in the mandatory governance envelope — preflight, a change session with a stated reason, scoped context, sync, impact-selected validators, and the change record. Use for every non-trivial edit to backend/, frontend/, curated governance files, or infrastructure. The other workflow skills call this one instead of restating its steps.
---

# Governed change envelope

Every non-trivial change runs inside this envelope. The `Stop` hook blocks turn-end while
touched files lack a change session or record, so skipping the envelope does not save
time — it strands the work half-done.

This skill is the frame. The change itself is the filling, and usually comes from another
skill: `refactor-governed`, `rag-change`, `endpoint-fullstack`, `debt-sweep`. If you are
not sure which, use `pick-workflow`.

## GOV-OPEN

```bash
make governance-preflight
make governance-change-start SUMMARY="<what>" REASON="<why>"
make governance-context PATHS="<paths>" TASK="<task>"
```

**`governance-preflight`** reports drift that already exists, so drift you cause stays
distinguishable from it. Record whatever it reports before touching anything — that drift
is not yours and must not end up in your change record.

Known benign failure: `preflight-ports` fails whenever the dev stack is already running
(ports 3000, 5001, 8100, 8201-8203 occupied). That is an environment probe for starting the
stack, not a repo-health signal. When it is the only failure, substitute
`make governance-check-fast` for the repo-side baseline and note the substitution.

**`governance-change-start`** captures the reason before it can be lost. Write the reason as
a causal claim — what was wrong, and what the change makes true instead. "Refactor the RAG
service" is not a reason. "Retrieval and ingestion shared a client whose lifecycle neither
owned, so a failed ingest poisoned live search" is.

**`governance-context`** replaces reading `governance/` recursively. Pass the narrowest
`PATHS` that covers the work. If it reports *unassigned paths*, the blast radius it returns
is incomplete — widen the paths or say so in the final report.

## Make the change

- **Smallest coherent change.** One reason per session. If you find a second problem, finish
  this session and open another.
- **Net-zero or net-negative diff.** Reuse, consolidate, or delete before you add. A change
  that only adds must say in its record why nothing could be removed or reused instead.
- **New files are a last resort.** Extend an existing module first. A `PreToolUse` hook
  prompts on new-file writes. Ad-hoc verification scripts and debug harnesses go in the
  session scratchpad, never the repository.
- **Never hand-edit a generated file.** `governance/manifests/generated/**`,
  `governance/catalog.json`, `governance/Summary.md`, and `ENV_VARS.md` are rebuilt by
  `governance-sync`. Curated manifests and `.governance.json` annotations are yours.

## GOV-CLOSE

**Shrink pass first.** Before anything else in the close, invoke the `simplify` skill on the
session diff. It hunts reuse, consolidation, and dead weight in exactly the code this session
touched — the diff is complete, so this is the cheapest moment to delete from it. If the pass
changes nothing, say so in the report. If the session added files, this is where "why could
nothing be extended instead" gets answered honestly rather than asserted.

```bash
make governance-sync
make governance-impact PATHS="<paths>"
# run each validator ID returned, resolved through governance/validators.json
make governance-change-finish
make governance-check
```

**Read the `governance-sync` diff.** It is evidence, not ceremony — it shows which manifests
your change moved. A sync that touches manifests you did not expect means the blast radius
was wider than you thought.

**`governance-impact` returns validator IDs, not commands.** Resolve each through
[validators.json](governance/validators.json) — it maps every ID to its exact command
and is the **only** source of truth for them; this skill deliberately does not carry a
copy, because a copied table drifts and `skills-check` would rightly flag it. Never
invent a validation command; the registry exists so an annotation cannot introduce
executable shell text.

`google-live-e2e` is deliberately excluded from the registry. It sends real email and mutates
a live Google Workspace account. Never select it automatically; invoke it by hand, knowingly.

Use `make governance-explain ID=<id>` for the reasoning behind a specific record or decision.

## Report

State plainly:

- what changed, and what was **deleted or consolidated** (or why nothing could be)
- which validators ran and their results — failures quoted, not summarized away
- what was **not** validated and what remains uncertain

If a generated manifest disagrees with an authoritative source, the manifest is stale. Never
change application behaviour to make a stale generated file pass.

## Rules

- One reason per change session. Batches produce unreviewable records.
- Never skip GOV-CLOSE because the change "was small". Small changes are exactly the ones
  whose reasons get lost.
- If validation fails and you cannot fix it in this session, say so in the record rather than
  narrowing the change to make it pass.
