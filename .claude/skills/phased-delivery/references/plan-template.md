# Plan NNN — <topic>

ADR: <ADR-00X, or "none — no architectural decision in this work">
Workspace: <workspace name the phases run in>
Branch slug: <slug used in sandbox/<slug>-phase-N>
Status: phase 0 of <N> complete

Standing rules for every phase (the executing agent reads these; phase prompts
just point here): work happens in /workspace; each phase starts with
`git pull --ff-only origin main` and a fresh `sandbox/<slug>-phase-N` branch off
main; every numbered step below becomes exactly one commit whose message is the
step's name; the phase's Validation runs before the branch is pushed; the phase
ends with the push and a short summary of what changed.

## Phase 1: <name>

Goal: <one sentence — what is true after this phase that was not before>
Validation: <the commands or validator IDs that prove this phase, runnable in the sandbox>

Steps:
1. <step name> — <what to do; the step name becomes the commit message>
2. <step name> — <...>

## Phase 2: <name>

Goal: <...>
Validation: <...>

Steps:
1. <step name> — <...>

<!--
The gate script synthesizes each phase's chat prompt from the heading and the
standing rules, so phases need no prompt block. To override the default for one
phase (extra context, a warning, a file to read first), add a fenced block to
that phase's section:

```prompt
You are executing Phase 2 of docs/plans/NNN-<topic>.md in this workspace.
<custom instructions for this phase>
```
-->
