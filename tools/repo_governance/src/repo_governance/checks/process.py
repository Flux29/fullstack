"""Process rules: change records and explicit policy weakening."""

from __future__ import annotations

import json
import re
import tomllib

from repo_governance.checks import CheckScope, relative
from repo_governance.config import iter_files
from repo_governance.gitutil import changed_since, file_at_ref, untracked_files, working_tree_changes
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue

GOVERNED_PREFIXES = ("governance/", "tools/repo_governance/")
CHANGE_RECORD_PREFIX = "governance/history/changes/"

#: Where a new untracked file legitimately appears. Anything outside these is scratch.
SANCTIONED_UNTRACKED_PREFIXES = (
    "governance/history/",
    "backend/alembic/versions/",
    "backend/tests/",
    "tools/repo_governance/tests/",
    "frontend/e2e/",
    "docs/",
    ".claude/",
)

#: Frontend unit tests sit beside their source, so they are recognised by name, not location.
SANCTIONED_UNTRACKED_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

#: How many paths an issue names before it summarises the rest.
PREVIEW_LIMIT = 5

#: Strictness ordering. Moving down this ladder is a weakening and must be explicit.
MATURITY_RANK = {
    "retired": -2,
    "deprecated": -1,
    "experimental": 0,
    "advisory": 1,
    "warning": 2,
    "blocking": 3,
}


def _preview(paths: list[str]) -> str:
    """The first few paths, with a count of whatever was elided."""
    head = ", ".join(paths[:PREVIEW_LIMIT])
    return head if len(paths) <= PREVIEW_LIMIT else f"{head}, and {len(paths) - PREVIEW_LIMIT} more"


def _change_set(scope: CheckScope) -> tuple[list[str], list[str], bool]:
    """Paths under consideration, the change records anywhere in the change set, and trust.

    The first list is the working-tree change set (plus the `since` range when one is
    given), narrowed to `scope.files` when a scope is set. The second is the change records
    in the *unscoped* change set: pre-commit hands the check only the staged files it is
    batching, and a record staged beside them is still the record for them, so looking for
    it inside the scoped list refuses fully recorded changes.

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
    records = [path for path in combined if path.startswith(CHANGE_RECORD_PREFIX) and path.endswith(".json")]
    if scope.files is not None:
        combined = [path for path in combined if path in scope.files]
    return combined, records, known


def check_governance_change_records(scope: CheckScope) -> list[Issue]:
    """A change touching governance or its tooling carries a change record."""
    changed, records, known = _change_set(scope)

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
        if any(path.startswith(prefix) for prefix in GOVERNED_PREFIXES) and not path.startswith(CHANGE_RECORD_PREFIX)
    ]
    if not governed or records:
        return []

    return [
        Issue(
            message="Governance changed without a change record.",
            path=governed[0],
            evidence=f"Changed without a record: {_preview(governed)}.",
            repair="make governance-change-start SUMMARY=... REASON=...   then   make governance-change-finish",
        )
    ]


def check_untracked_files_are_sanctioned(scope: CheckScope) -> list[Issue]:
    """Untracked files sitting outside the places a new file legitimately appears.

    Scratch accumulates silently. A script written to prove one thing works stays untracked,
    outlives the change that motivated it, and eventually arrives in history on the back of a
    wide `git add`. While it is still untracked is the only cheap moment to name it.
    """
    untracked = untracked_files(scope.ctx.repo_root)
    if untracked is None:
        return []  # check_governance_change_records already reports git being unavailable.

    unsanctioned = [
        path
        for path in untracked
        if scope.selects(path)
        and not path.startswith(SANCTIONED_UNTRACKED_PREFIXES)
        and not path.endswith(SANCTIONED_UNTRACKED_SUFFIXES)
    ]
    if not unsanctioned:
        return []

    return [
        Issue(
            message=f"{len(unsanctioned)} untracked path(s) sit outside the sanctioned locations.",
            path=unsanctioned[0],
            evidence=f"Untracked and unsanctioned: {_preview(unsanctioned)}.",
            repair="Move scratch to the session scratchpad, commit what belongs here, or ignore generated output.",
        )
    ]


def check_policy_weakening_recorded(scope: CheckScope) -> list[Issue]:
    """Lowering a rule's maturity, or removing a rule, is named in a change record."""
    ctx = scope.ctx
    _changed, records, known = _change_set(scope)
    if not known:
        return []

    weakened: list[tuple[str, str, str]] = []

    # Compare against the CI base commit when one is given, else HEAD. Against HEAD alone
    # this could never fire on a clean checkout, which is exactly what CI is.
    base_ref = scope.since or "HEAD"
    for path in iter_files(ctx.paths.policies, suffixes=(".json",)):
        rel = relative(ctx, path)
        previous_text = file_at_ref(ctx.repo_root, base_ref, rel)
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
    for path in records:
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


def check_exception_hygiene(scope: CheckScope) -> list[Issue]:
    """Every exception explains itself and says what would make it worth revisiting."""
    path = scope.ctx.paths.curated_manifests / "exceptions.json"
    if not path.is_file():
        return []
    try:
        document = read_json(path)
    except ValueError:
        return []

    rel = relative(scope.ctx, path)
    known_rules = {rule.id for rule in _all_rule_ids(scope)}

    issues: list[Issue] = []
    for entry in document.get("exceptions", []):
        exception_id = entry.get("id", "(unnamed)")
        if entry.get("status") != "active":
            continue
        if not entry.get("review_trigger"):
            issues.append(
                Issue(
                    message=f"Exception {exception_id!r} has no review trigger.",
                    path=rel,
                    evidence="An exception with no condition for revisiting it is a permanent hole.",
                    repair="State what would make this worth reconsidering, or make it a policy change instead.",
                )
            )
        for policy_ref in entry.get("policy_refs", []):
            if policy_ref not in known_rules:
                issues.append(
                    Issue(
                        message=f"Exception {exception_id!r} departs from rule {policy_ref!r}, which is not declared.",
                        path=rel,
                        evidence="An exception to a nonexistent rule protects nothing.",
                        repair="Declare the rule, or correct the reference.",
                    )
                )

    return issues


def check_suite_validator_coverage(scope: CheckScope) -> list[Issue]:
    """Every declared test suite is reachable through a validator, or excluded with a reason."""
    from repo_governance.documents import load_validators

    manifest = scope.ctx.paths.curated_manifests / "tests.json"
    if not manifest.is_file():
        return []
    try:
        suites = read_json(manifest).get("suites", [])
    except ValueError:
        return []

    registry = load_validators(scope.ctx)
    known = {entry["id"] for entry in registry.get("validators", [])}
    excluded_reasons = {entry["id"]: entry.get("reason") for entry in registry.get("excluded", [])}

    issues: list[Issue] = []
    for suite in suites:
        validator = suite.get("validator")
        if validator is None:
            if not suite.get("gated_by") and not excluded_reasons:
                issues.append(
                    Issue(
                        message=f"Suite {suite['id']!r} has no validator and no gate explaining why.",
                        path="governance/manifests/curated/tests.json",
                        evidence="A suite nobody can select either never runs or runs by accident.",
                        repair="Register a validator, or record it in the registry's excluded list with a reason.",
                    )
                )
            continue
        if validator not in known:
            issues.append(
                Issue(
                    message=f"Suite {suite['id']!r} names validator {validator!r}, which is not registered.",
                    path="governance/manifests/curated/tests.json",
                    repair="Add it to governance/validators.json, or correct the reference.",
                )
            )

    return issues


def check_validator_ci_jobs(scope: CheckScope) -> list[Issue]:
    """Each validator declares the CI job that runs it, or explicitly declares none."""
    from repo_governance.documents import load_validators

    issues: list[Issue] = []
    for entry in load_validators(scope.ctx).get("validators", []):
        if "ci_job" not in entry:
            issues.append(
                Issue(
                    message=f"Validator {entry['id']!r} does not say whether CI runs it.",
                    path="governance/validators.json",
                    evidence="Without this it is impossible to tell which validators gate a merge and which are local only.",
                    repair="Set ci_job to the job name, or to null if it deliberately does not run in CI.",
                )
            )
    return issues


#: The rule a change record must name to lower a floor deliberately.
COVERAGE_RATCHET_RULE = "coverage-floor-does-not-regress"

#: A coverage-floor source: repo-relative path and a function from its text to {name: floor}.
COVERAGE_FLOOR_SOURCES: tuple[tuple[str, str], ...] = (
    ("backend/pyproject.toml", "pyproject"),
    ("tools/repo_governance/pyproject.toml", "pyproject"),
    ("frontend/vitest.config.ts", "vitest"),
)

_VITEST_THRESHOLDS = re.compile(r"thresholds\s*:\s*\{(?P<body>[^}]*)\}", re.DOTALL)
_VITEST_FLOOR = re.compile(r"\b(statements|branches|functions|lines)\s*:\s*(\d+(?:\.\d+)?)")


def _coverage_floors(kind: str, text: str) -> dict[str, float]:
    """The floors a coverage-config file declares, by name. Unparseable text yields {}."""
    if kind == "pyproject":
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
        value = document.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
        return {"fail_under": float(value)} if isinstance(value, int | float) else {}
    if kind == "vitest":
        match = _VITEST_THRESHOLDS.search(text)
        if match is None:
            return {}
        return {name: float(number) for name, number in _VITEST_FLOOR.findall(match.group("body"))}
    return {}


def check_coverage_floor_ratchet(scope: CheckScope) -> list[Issue]:
    """A coverage floor never moves down unless a change record names the ratchet rule.

    Floors are ratchets against measured coverage; lowering one is how a red build gets
    made green without a test being written. Each floor source is compared with its
    content at the CI base commit (or HEAD locally); a decrease is an issue unless a change
    record in the change set names the rule, the same escape hatch policy weakening uses.
    """
    ctx = scope.ctx
    _changed, records, known = _change_set(scope)
    if not known:
        return []  # check_governance_change_records already reports git being unavailable.
    base_ref = scope.since or "HEAD"

    record_text = ""
    for path in records:
        target = ctx.repo_root / path
        if target.is_file():
            record_text += target.read_text(encoding="utf-8")
    if COVERAGE_RATCHET_RULE in record_text:
        return []

    issues: list[Issue] = []
    for rel, kind in COVERAGE_FLOOR_SOURCES:
        current_path = ctx.repo_root / rel
        if not current_path.is_file():
            continue
        previous_text = file_at_ref(ctx.repo_root, base_ref, rel)
        if previous_text is None:
            continue  # New file; nothing to regress from.
        before = _coverage_floors(kind, previous_text)
        after = _coverage_floors(kind, current_path.read_text(encoding="utf-8"))
        for name, old_floor in before.items():
            new_floor = after.get(name)
            if new_floor is None:
                lowered = f"{name} removed (was {old_floor:g})"
            elif new_floor < old_floor:
                lowered = f"{name} {old_floor:g} -> {new_floor:g}"
            else:
                continue
            issues.append(
                Issue(
                    message=f"Coverage floor lowered in {rel} without a change record naming {COVERAGE_RATCHET_RULE!r}.",
                    path=rel,
                    evidence=lowered,
                    repair=(
                        "Raise the floor back, or record the lowering deliberately: a change record that names "
                        f"{COVERAGE_RATCHET_RULE} and says why the measured value fell."
                    ),
                )
            )
    return issues


def _all_rule_ids(scope: CheckScope):
    from repo_governance.checks import iter_rules

    return iter_rules(scope.ctx)
