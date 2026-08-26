---
name: phased-delivery
description: Run a multi-phase feature through the plan → sandbox agent → host review → PR pipeline. Use when work is big enough to plan in phases and execute through the fullstack coding-workspace agent — writing the plan (and ADR when a decision is involved), kicking off each phase, reviewing and correcting the sandbox branch, opening the PR, and letting the phase-gate script advance phases on green CI at zero token cost.
---

# Phased delivery: plan → sandbox agent → review → PR → next phase

Three actors, strictly separated so each does what it is cheapest at:

- **Host Claude (you)** plans, reviews, corrects, and opens PRs. You never implement
  the phases yourself — the point of the pipeline is that implementation runs in the
  sandbox where mistakes are disposable.
- **The fullstack coding-workspace agent** implements one phase per conversation turn
  inside its sandbox (ADR-006), and pushes `sandbox/<slug>-phase-N` branches through
  the dev git daemon into the local repository.
- **The phase-gate script** (`scripts/watch-phase-gate.ps1` in this skill's directory)
  owns the CI gap at zero token cost — see Stage 4.

## Stage 1 — Plan (and ADR only when there is a decision)

A plan and an ADR are different artifacts: the ADR records a *decision* and should
stop churning once accepted; the plan is an *execution checklist* that churns every
phase. Write an ADR (in `docs/architecture/decisions/`, status `proposed`) only when
the work embodies an architectural choice, and have the plan cite it. Every delivery
gets a plan.

Plans are named `docs/plans/NNN-<topic>.md` (create the directory with the first
plan), shaped exactly like [references/plan-template.md](references/plan-template.md):
a `Status:` line, standing rules, and one `## Phase N: <name>` heading per phase with
a Goal, a Validation line, and numbered steps. The heading format is load-bearing —
the gate script parses it and synthesizes each phase's chat prompt from it; a
` ```prompt ` fence in a phase section overrides the default prompt when a phase
needs custom instructions.

Land the plan (and ADR) through `gov-change` and `ship` like any other edit. The
sandbox picks the plan up automatically — workspaces clone from this repository
through the dev git daemon, so once the plan is on local main, a fresh clone has it
and an existing one gets it from the `git pull --ff-only origin main` that every
phase prompt starts with. There is no copy step.

**Plan approval is a consent boundary:** once the user approves the plan, corrections
you make to the agent's branches during review (Stage 3) need no further sign-off.

## Stage 2 — Kick off a phase

```bash
pwsh .claude/skills/phased-delivery/scripts/watch-phase-gate.ps1 -PlanPath docs/plans/NNN-<topic>.md -Phase 1 -Kickoff
```

That copies the phase prompt to the clipboard; the user pastes it into a fullstack
chat with the workspace attached, and the agent executes the phase per the plan's
standing rules.

## Stage 3 — Review and correct the sandbox branch

The push lands the branch in the local repository. Review it host-side:

- Diff the branch against the plan's phase: `git diff main...sandbox/<slug>-phase-N`.
  Check the plan's steps happened, the commits are named per step, and the diff
  honours the conventions `.claude/rules/` loads for the touched files.
- Validate host-side per gov-change's GOV-CLOSE (impact-selected validators resolved
  through the registry) — the sandbox cannot run them all (no Docker, no live
  services), so host-side validation is part of review, not optional.
- **Wrong but unambiguous → fix it yourself**: commit corrections directly on the
  sandbox branch (plan approval covered this). Say what you corrected in the phase
  report.
- **Ambiguous intent or a design fork → stop and ask the user.** Never guess on
  something the plan does not settle.

## Stage 4 — PR and the automated gate

The PR **is** the merge to main — never merge the sandbox branch into local main
yourself; local main follows GitHub after the gate (`ship` owns the single-branch
workflow rules this follows).

```bash
git push origin sandbox/<slug>-phase-N
gh pr create --base main --head sandbox/<slug>-phase-N --fill
pwsh .claude/skills/phased-delivery/scripts/watch-phase-gate.ps1 -PlanPath docs/plans/NNN-<topic>.md -Phase 1 -Pr <number>
```

Run the gate script in a separate terminal (or backgrounded); from here to the next
phase no model is involved. It polls the checks, and:

- **green** → merges (`--merge --delete-branch`), fast-forwards local main, deletes
  the local branch, bumps the plan's Status line, and puts the *next* phase's prompt
  on the clipboard with an audible alert — the "move on" signal is the user pasting it;
- **red** → alarms and stops **without merging**; you review the failed run;
- **conflict** → alarms and stops; rebase the sandbox branch onto main host-side,
  force-push the branch (never main), and rerun the gate.

## Stage 5 — Close out

After the last phase merges: flip the ADR to `accepted` if one was written, and
finish with a delivery report — phases landed, corrections made, validators run.
Plan edits mid-delivery (re-scoping later phases from what earlier phases taught)
are normal; land them like any other governed edit so the sandbox sees them.
