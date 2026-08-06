"""Process rules: change records and explicit policy weakening."""

from __future__ import annotations

import json

from repo_governance.checks import CheckScope, relative
from repo_governance.config import iter_files
from repo_governance.gitutil import changed_since, file_at_ref, working_tree_changes
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue

GOVERNED_PREFIXES = ("governance/", "tools/repo_governance/")
CHANGE_RECORD_PREFIX = "governance/history/changes/"

#: Strictness ordering. Moving down this ladder is a weakening and must be explicit.
MATURITY_RANK = {
    "retired": -2,
    "deprecated": -1,
    "experimental": 0,
    "advisory": 1,
    "warning": 2,
    "blocking": 3,
}


def _change_set(scope: CheckScope) -> tuple[list[str], bool]:
    """Paths changed in the working tree, plus the `since` range when one is given.

    The boolean reports whether the answer is trustworthy. When git is unavailable the
    check reports uncertainty rather than passing, because "no changes detected" and
    "cannot detect changes" mean very different things.
    """
    root = scope.ctx.repo_root
    known = True

    working = working_tree_changes(root)
    if working is None:
        working, known = [], False

    ranged: list[str] = []
    if scope.since:
        found = changed_since(root, scope.since)
        if found is None:
            known = False
        else:
            ranged = found

    combined = sorted({*working, *ranged})
    if scope.files is not None:
        combined = [path for path in combined if path in scope.files]
    return combined, known


def check_governance_change_records(scope: CheckScope) -> list[Issue]:
    """A change touching governance or its tooling carries a change record."""
    changed, known = _change_set(scope)

    if not known:
        return [
            Issue(
                message="Could not determine what changed; the change-record requirement was not evaluated.",
                path="governance/",
                evidence="git was unavailable or this is not a repository.",
                repair="Run the check inside a git working tree.",
            )
        ]

    governed = [
        path
        for path in changed
        if any(path.startswith(prefix) for prefix in GOVERNED_PREFIXES)
        and not path.startswith(CHANGE_RECORD_PREFIX)
    ]
    if not governed:
        return []

    records = [path for path in changed if path.startswith(CHANGE_RECORD_PREFIX) and path.endswith(".json")]
    if records:
        return []

    preview = ", ".join(governed[:5])
    if len(governed) > 5:
        preview += f", and {len(governed) - 5} more"

    return [
        Issue(
            message="Governance changed without a change record.",
            path=governed[0],
            evidence=f"Changed without a record: {preview}.",
            repair="make governance-change-start SUMMARY=... REASON=...   then   make governance-change-finish",
        )
    ]


def check_policy_weakening_recorded(scope: CheckScope) -> list[Issue]:
    """Lowering a rule's maturity, or removing a rule, is named in a change record."""
    ctx = scope.ctx
    changed, known = _change_set(scope)
    if not known:
        return []

    weakened: list[tuple[str, str, str]] = []

    for path in iter_files(ctx.paths.policies, suffixes=(".json",)):
        rel = relative(ctx, path)
        previous_text = file_at_ref(ctx.repo_root, "HEAD", rel)
        if previous_text is None:
            continue  # New policy file; nothing to weaken.

        try:
            previous = json.loads(previous_text)
            current = read_json(path)
        except ValueError:
            continue

        before = {rule["id"]: rule.get("maturity", "advisory") for rule in previous.get("rules", [])}
        after = {rule["id"]: rule.get("maturity", "advisory") for rule in current.get("rules", [])}

        for rule_id, old_maturity in before.items():
            new_maturity = after.get(rule_id)
            if new_maturity is None:
                weakened.append((rel, rule_id, f"removed (was {old_maturity})"))
                continue
            if MATURITY_RANK.get(new_maturity, 0) < MATURITY_RANK.get(old_maturity, 0):
                weakened.append((rel, rule_id, f"{old_maturity} -> {new_maturity}"))

    if not weakened:
        return []

    record_text = ""
    for path in changed:
        if path.startswith(CHANGE_RECORD_PREFIX) and path.endswith(".json"):
            target = ctx.repo_root / path
            if target.is_file():
                record_text += target.read_text(encoding="utf-8")

    issues: list[Issue] = []
    for rel, rule_id, transition in weakened:
        if rule_id in record_text:
            continue
        issues.append(
            Issue(
                message=f"Policy rule {rule_id!r} was weakened without being named in a change record.",
                path=rel,
                evidence=transition,
                repair=(
                    "Record the weakening explicitly: a change record naming the rule, why it was "
                    "weakened, and what would restore it."
                ),
            )
        )

    return issues
