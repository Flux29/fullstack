---
description: Investigate and fix an issue
---

Fix the issue: $ARGUMENTS

This is a governed behaviour change. Open with `gov-change` GOV-OPEN before editing;
close with GOV-CLOSE. Write the session reason as the defect, not the activity.

1. **Understand** — search the codebase for relevant code, read the files, understand current behavior. If the cause is not yet clear, run `engineering:debug` before touching anything.
2. **Reproduce** — identify a test case or request that triggers the issue.
3. **Regression test first** — write the failing test in `backend/tests/` (or the frontend equivalent) *before* the fix. A fix without a failing-then-passing test is a claim, not evidence.
4. **Root cause** — trace Routes → Services → Repositories to find where the bug originates. Fix the cause, not the symptom.
5. **Fix** — following project conventions (the path-scoped rules auto-load):
   - Domain exceptions in services (not HTTP errors)
   - `db.flush()` in repositories (not `commit`)
   - Type hints on all changed signatures
6. **Validate** — run what `make governance-impact` selects; at minimum the `backend-lint` and `backend-unit` validators. If the fix touched an HTTP contract, the proxy handler and frontend caller move in the same session (see `endpoint-fullstack`).
7. **Close** — `gov-change` GOV-CLOSE. The record states the regression test by name, what was validated, and what remains uncertain.
