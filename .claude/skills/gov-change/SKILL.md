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

**A dirty baseline may belong to a live parallel session.** Attribute the changes before
deciding what to do with them: anything you did not write is never committed, stashed, or
reverted — branch a fresh checkout or worktree from the pushed baseline and work there. A
start-of-session snapshot is not safe to hold for the session's duration; the other session
can commit under you mid-turn. When preflight reports generated-artifact drift, check whether
the artifact is merely stale or actually truncated — those need opposite responses.

**Check the slot is free, and that the work is not already done.** This checkout holds exactly
one HEAD, one index, and one `.cache/repo-governance/session.json`. A session file whose
summary is not yours means a peer is mid-change here: work in a separate `git worktree`, and
never `git switch` a directory carrying changes you did not make. Then re-read `git status`
and the last few commits — in a multi-session repository the requested change may already
have landed with its record, and the honest answer is "already shipped", not a second session
for the same reason.

**In a worktree or fresh clone, hydrate the runtime files the validators read.** You get the
tracked tree, not the working environment: `backend/.env` and its kin are gitignored and live
only in the primary checkout, so compose-backed validators fail on a missing env file rather
than on the change under test. Copy them at session open, not when a validator trips over them.

**`governance-change-start`** captures the reason before it can be lost. Write the reason as
a causal claim — what was wrong, and what the change makes true instead. "Refactor the RAG
service" is not a reason. "Retrieval and ingestion shared a client whose lifecycle neither
owned, so a failed ingest poisoned live search" is.

**Resuming an interrupted session.** An open session records its summary, reason, and
`start_commit` — but not the branch it was opened on. Before continuing one, compare the
current branch and `HEAD` against it: an interruption can leave the working tree on a
different branch still carrying the uncommitted governed edit, and nothing in the envelope
flags the mismatch. Switch back, or say in the record why not.

**`governance-context`** replaces reading `governance/` recursively. Pass the narrowest
`PATHS` that covers the work. If it reports *unassigned paths*, the blast radius it returns
is incomplete — widen the paths or say so in the final report.

The gate forbids *enumerating* `governance/history/` but still allows a point read of a named
file. When you need a specific record whose filename you do not know, derive the name rather
than sweeping the directory: a record ID from `governance-context` maps to `<ID>.json`, and
`git diff-tree --name-only <sha>` on the commit that wrote a record prints its exact path.

## Make the change

- **Smallest coherent change.** One reason per session. If you find a second problem, finish
  this session and open another.
- **Net-zero or net-negative diff.** Reuse, consolidate, or delete before you add. A change
  that only adds must say in its record why nothing could be removed or reused instead.
- **New files are a last resort.** Extend an existing module first. A `PreToolUse` hook
  prompts on new-file writes. Ad-hoc verification scripts and debug harnesses go in the
  session scratchpad, never the repository.
- **Arguments from absence carry no path literal.** `skills-check` reads a path written in a
  skill, command, or ADR as a claim that the path exists. When the point is that something is
  *missing*, say it in prose ("a skills directory beside `app/` that was never created") or
  cite the code site that expects it — a path token is a claim of existence you did not mean
  to make.
- **Check the diff's size, not the edit tool's success message.** This checkout normalizes
  line endings (index LF, worktree CRLF), and a scripted edit that rewrites them turns a
  three-line change into a whole-file diff while reporting success. After any scripted or
  tool-driven edit, run `git diff --stat` and `git ls-files --eol` on the touched files. A line
  count far above the intended change, or a text attribute that changed (`w/-text`), means the
  endings moved: regenerate the file from `git show HEAD:<file>`, apply the edit to that form,
  and write it back with the worktree's existing endings — do not patch the damaged copy.
- **Never hand-edit a generated file.** `governance/manifests/generated/**`,
  `governance/catalog.json`, `governance/Summary.md`, and `ENV_VARS.md` are rebuilt by
  `governance-sync`. Curated manifests and `.governance.json` annotations are yours.

## GOV-CLOSE

**Shrink pass first.** Before anything else in the close, invoke the `simplify` skill on the
session diff. It hunts reuse, consolidation, and dead weight in exactly the code this session
touched — the diff is complete, so this is the cheapest moment to delete from it. If the pass
changes nothing, say so in the report. If the session added files, this is where "why could
nothing be extended instead" gets answered honestly rather than asserted.

**Size the pass to the diff.** `simplify`'s four-agent fan-out is priced for multi-file code
changes, and on a small docs-, plan-, or governance-record-only diff two of its four angles
(efficiency, helper reuse) do not apply to prose at all. Those get an inline pass instead —
duplication, redundancy, could-this-extend-an-existing-file — at a fraction of the cost. As a
threshold: a documentation-only diff, or one under roughly fifty changed lines, takes the
inline pass; anything larger takes `simplify`. Name which form ran in the report; the maximal
pass is not mandatory at every size.

```bash
make governance-sync
make governance-impact PATHS="<paths>"
# run each validator ID returned, resolved through governance/validators.json
make governance-change-finish ARGS="..."
make governance-check
```

**`governance-change-finish` refuses to guess:** it exits without writing unless
`--security-impact`, `--compatibility-impact`, and `--rollback` are all supplied, and a
fabricated answer is worse than an absent one. `None` is an acceptable security impact when
stated deliberately. Put in `--rollback` whatever a revert would *not* restore: state
outside the repository, an already-applied migration, an installed artifact. For the full
flag list run `make governance-change-finish ARGS="--help"` — this file is not a second copy
of it and will drift if treated as one.

The optional flags are where the Report below actually lands, so fill them in rather than
only in chat. Two are tied to this skill's own steps: `--validation` (one per validator ID
`governance-impact` selected, as `id=result:evidence`) and `--limitation` (the shrink pass's
honest answer to "why could nothing be removed"). Everything after `ARGS=` is passed through
verbatim; when the nested quoting fights you on a long record, run
`uv run --project tools/repo_governance governance change finish ...` directly instead.

**Read the `governance-sync` diff.** It is evidence, not ceremony — it shows which manifests
your change moved. A sync that touches manifests you did not expect means the blast radius
was wider than you thought.

If the generated diff *misrepresents* your change, do not commit it. The manifest ships as
truth, which means it constrains the source forms you may use: either fix the extractor (when
that is this session's reason) or reshape the source into a form the extractor represents
honestly, and record the trade-off as a limitation. Truth in the artifact outranks elegance in
the source.

**`governance-impact` returns validator IDs, not commands.** Resolve each through
[validators.json](governance/validators.json) — it maps every ID to its exact command
and is the **only** source of truth for them; this skill deliberately does not carry a
copy, because a copied table drifts and `skills-check` would rightly flag it. Never
invent a validation command; the registry exists so an annotation cannot introduce
executable shell text.

**The selected validators are a lower bound; the CI job is the gate.** Impact selection
returns what the registry maps to the touched paths, but the job gating those paths may chain
steps the registry does not list — a lint step ahead of a test target is the usual one, and
local pre-commit hooks may exclude the same directory. Find the CI job that gates the surfaces
you touched and run its full target list (jobs map one-to-one onto Make targets here), or
expect a green local close and a red push.

`google-live-e2e` is deliberately excluded from the registry. It sends real email and mutates
a live Google Workspace account. Never select it automatically; invoke it by hand, knowingly.

Use `make governance-explain ID=<id>` for the reasoning behind a specific record or decision.

## Verify what you claim

`--validation` lines are evidence, not assertion. Three claims fail verification in the same
way — everything short of exercising the behaviour passes, and all of it is wrong:

- **A security posture read from configuration is a hypothesis.** A flag can be set, present
  in the container's environment, and read by real code, and still be overridden by a
  dependency's own default. A `--security-impact` of the form "X cannot reach Y" needs X
  observed trying and failing. Same for a boundary asserted in a comment, ADR, or README: the
  remediation is to move the claim into the artifact that enforces it, then verify the
  **denials**, not only the allowed path.
- **Persistence needs the other interface.** Writing and reading back through one live session
  proves the cache in front of the storage and nothing about durability. Read the same data
  through the far side — the volume itself, an offline API, a restart.
- **The running container is an older tree.** A probe that imports a dependency this branch
  added fails inside an image built before it, for reasons unrelated to the change. Speak the
  service's wire contract instead; rebuilding an image just to run a check is the last resort.

Two environment rules decide whether you are verifying the change or a ghost. Any per-service
`up` / `restart` / `build` must reuse the project's canonical compose file set (read the
Makefile's `COMPOSE_*` variables, or use the Make target that wraps them) — a bare
`docker compose up -d <service>` does not restart a service, it redefines it from the base
file, silently dropping bind mounts, published ports, and reload flags. And before any smoke
that depends on a compose-provided setting, `docker inspect` the **running** container for
that setting: the files describe intent, only the container describes reality.

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
- Any command you hand the **user** to run is written for the user's shell, not yours. This
  environment is PowerShell-primary: emit `$env:VAR`, `Select-String`, and `powershell`-tagged
  fences, never `export` / `grep` / `$(...)` in a block meant for the user's prompt. That you
  ran the equivalent through a Bash tool says nothing about the terminal the user pastes into,
  and a harness convention that rewards bash-tagged fences is not evidence either.
