---
id: ADR-NNN
title: <A sentence that can be true or false>
status: proposed
date: YYYY-MM-DD
components: []
supersedes: []
related_paths: []
policy_refs: []
---

# ADR-NNN — <title>

## Status

Proposed, YYYY-MM-DD. One sentence on what is settled and what deliberately is not.

## Context

What forces are in play: the constraint in the code, the operational reality, the thing
that would go wrong without a decision. Cite files as `path:line`. No solution here.

## Decision

The decision, stated as the rule an agent can check a change against. Prefer "X must Y
because Z" over narrative.

## Consequences

What becomes easier, what becomes harder, what now has to be maintained. Name the
invariants this decision adds to component annotations, and the validator that proves them.

## Alternatives considered

One paragraph each: the alternative, why it was rejected, and the condition under which it
would be reconsidered (that condition is what a future `supersedes` ADR will cite).

## Open questions

Things this ADR explicitly does not accept or settle. Empty is a valid answer; say so.

## References

Change records, findings, HOWTOs, external material. Never a git SHA (history can be
rewritten); use change-record ids.
