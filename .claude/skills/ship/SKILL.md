---
name: ship
description: Land a finished governed change — commit, push, and (when asked) PR and merge. Use after gov-change GOV-CLOSE, when work is validated and the change record is written. Exists because committing before governance-change-finish strands the record outside the commit it describes, and because this repository's single-branch workflow has rules that improvised git steps get wrong.
---

# Ship a governed change

The ordering rule this skill enforces: **commit after `governance-change-finish`, never
before.** The change record, the regenerated `governance/Summary.md`, and the code must
land in the same commit — a record committed separately describes a diff it does not
contain.

## Preconditions — do not ship past a red light

1. GOV-CLOSE is complete: record written, `make governance-sync` run after it, and
   `make governance-check` reporting **0 blocking**.
2. No unfinished second session: one reason per commit, same as one reason per session.
3. `git status` shows only the files this session touched. Anything unexpected is
   someone else's work — stop and ask, do not sweep it in.

## Sequence

```bash
git fetch --prune
```

Fetch first — this repository's branch workflow deletes PR branches on merge, so local
refs go stale fast; never trust them unfetched.

**Direct-to-main** (the default for a session-sized change):

```bash
git add -A
git commit -m "<the change summary, as the session stated it>"
git push origin main
```

**PR flow** (when asked, or when the change wants review): branch → commit → push →
`commit-commands:commit-push-pr` opens the PR. Branches are short-lived and auto-deleted
on merge; after merging, run `commit-commands:clean_gone` to drop local `[gone]`
branches and their worktrees.

The commit message is the session's summary — the reason is already captured in the
change record; the message names the change, it does not re-argue it.

## Verify

```bash
git log -1 --stat     # the record file is IN this commit
git status -sb        # clean, and up to date with origin
```

If the push is rejected, fetch and rebase — never force-push `main`.

## Rules

- Never commit mid-session "to checkpoint" — the Stop hook and the record exist so
  half-done work is visible, not hidden in WIP commits.
- Never skip hooks (`--no-verify`) and never bypass signing.
- A commit that includes governance/history/changes/ must also include the synced
  `governance/Summary.md` — if `make governance-sync` reports changes after your commit,
  you committed too early; amendless repair is a second commit, stated plainly.
- Secrets never ship: `.env.example` files stay placeholder-only.
