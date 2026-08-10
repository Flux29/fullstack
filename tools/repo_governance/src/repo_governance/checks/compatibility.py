"""Contract checks that can run against manifests alone.

The five-point dimension agreement, the migration graph, and the WebSocket event set all
need extractors and are marked as later phases in the policy files. What is checkable now is
whether the manifest's own claims are internally coherent.
"""

from __future__ import annotations

from typing import Any

from repo_governance.checks import CheckScope
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue

CONFIGURATION_MANIFEST = "governance/manifests/generated/configuration.json"


def _variables(scope: CheckScope) -> list[dict[str, Any]]:
    path = scope.ctx.repo_root / CONFIGURATION_MANIFEST
    if not path.is_file():
        return []
    try:
        return read_json(path).get("variables", [])
    except ValueError:
        return []


def check_alias_milestones(scope: CheckScope) -> list[Issue]:
    """Every deprecated alias records what it aliases and when it can go."""
    issues: list[Issue] = []
    for variable in _variables(scope):
        if "deprecated-alias" not in variable.get("classification", []):
            continue
        name = variable["name"]
        if not variable.get("alias_of"):
            issues.append(
                Issue(
                    message=f"{name} is classified as a deprecated alias but does not say what it aliases.",
                    path=CONFIGURATION_MANIFEST,
                    repair="Set alias_of to the canonical variable name.",
                )
            )
        if not variable.get("removal_milestone"):
            issues.append(
                Issue(
                    message=f"{name} is a deprecated alias with no removal milestone.",
                    path=CONFIGURATION_MANIFEST,
                    evidence="An alias with no removal condition outlives the migration it was meant to smooth.",
                    repair="Record what has to be true before it can be deleted.",
                )
            )
    return issues


def check_build_arg_classification(scope: CheckScope) -> list[Issue]:
    """Frontend NEXT_PUBLIC_* values are classified as build arguments."""
    issues: list[Issue] = []
    for variable in _variables(scope):
        name = variable["name"]
        if not name.startswith("NEXT_PUBLIC_"):
            continue
        classification = variable.get("classification", [])
        if "build-arg" in classification:
            continue
        issues.append(
            Issue(
                message=f"{name} is not classified as a build argument.",
                path=CONFIGURATION_MANIFEST,
                evidence=f"Classified as {classification}. NEXT_PUBLIC_* values are baked into the image at build time.",
                repair="Add build-arg to its classification, so a change to it is known to require a rebuild.",
            )
        )
    return issues


def check_migration_graph(scope: CheckScope) -> list[Issue]:
    """The revision graph is acyclic and connected, with the recorded head set.

    Contiguity is graph connectivity, never filename numbering — this repository's filenames
    skip several numbers while the chain itself is unbroken.
    """
    from repo_governance.extractors.alembic import extract_revisions

    graph = extract_revisions(scope.ctx)
    issues: list[Issue] = []

    for unknown in graph.unknowns:
        issues.append(
            Issue(
                message="A migration could not be read.",
                path="backend/alembic/versions/",
                evidence=unknown,
                repair="A parser failure is reported as unknown, never as an absent revision.",
            )
        )

    for orphan in graph.orphans:
        issues.append(
            Issue(
                message="The revision graph is disconnected.",
                path="backend/alembic/versions/",
                evidence=orphan,
                repair="Restore the missing revision, or correct the down_revision reference.",
            )
        )

    for cycle in graph.cycles:
        issues.append(
            Issue(
                message="The revision graph contains a cycle.",
                path="backend/alembic/versions/",
                evidence=" -> ".join(cycle),
                repair="Break the cycle; Alembic cannot resolve an ordering through it.",
            )
        )

    manifest = scope.ctx.paths.generated_manifests / "data-stores.json"
    if manifest.is_file():
        try:
            expected = read_json(manifest)["postgres"]["migrations"]["head_expected"]
        except (ValueError, KeyError):
            expected = None
        if expected is not None and sorted(expected) != sorted(graph.heads):
            issues.append(
                Issue(
                    message="The migration head set does not match the recorded expectation.",
                    path="governance/manifests/generated/data-stores.json",
                    evidence=f"expected {sorted(expected)}, observed {sorted(graph.heads)}",
                    repair=(
                        "If the new head is intended, update head_expected in the same change that "
                        "adds the revision. Branches and merge revisions are legal; an unexpected "
                        "head is what is not."
                    ),
                )
            )

    return issues


def check_orm_migration_pairing(scope: CheckScope) -> list[Issue]:
    """A change-set touching SQLAlchemy models also touches an Alembic revision.

    Deliberately a file-path pairing heuristic, and deliberately advisory: it false-positives
    on comment-only model edits and false-negatives when a schema change ships beside any
    unrelated migration. That noise profile is exactly why the rule's promotion criterion
    demands symbol-level detection — this implementation does NOT satisfy
    ``promotion_criteria`` and must never be promoted to blocking in this form; promoting a
    heuristic teaches people to commit empty migrations to silence it.

    Operates only on a change-set (working tree, plus the ``--since`` range in CI). A
    full-tree run has no change-set and emits nothing.
    """
    from repo_governance.checks.process import _change_set

    changed, known = _change_set(scope)
    if not known:
        return [
            Issue(
                message="Could not determine what changed; the model-migration pairing was not evaluated.",
                path="backend/app/db/models/",
                evidence="git was unavailable or this is not a repository.",
                repair="Run the check inside a git working tree.",
            )
        ]

    models_changed = [
        path for path in changed if path.startswith("backend/app/db/models/") and path.endswith(".py")
    ]
    if not models_changed:
        return []
    if any(path.startswith("backend/alembic/versions/") for path in changed):
        return []

    return [
        Issue(
            message="SQLAlchemy models changed with no Alembic revision in the same change-set.",
            path=models_changed[0],
            evidence=(
                f"Model files changed: {', '.join(models_changed)}. "
                "No backend/alembic/versions/ change accompanies them."
            ),
            repair=(
                "Generate a revision (uv run --directory backend alembic revision --autogenerate) or, "
                "for a change that provably needs none, say so in the change record."
            ),
        )
    ]


def check_redis_allocations(scope: CheckScope) -> list[Issue]:
    """Each Redis logical database has exactly one declared purpose."""
    manifest = scope.ctx.paths.generated_manifests / "data-stores.json"
    if not manifest.is_file():
        return []
    try:
        databases = read_json(manifest)["redis"]["databases"]
    except (ValueError, KeyError):
        return []

    seen: dict[int, str] = {}
    issues: list[Issue] = []
    for entry in databases:
        number = entry["db"]
        if number in seen:
            issues.append(
                Issue(
                    message=f"Redis database {number} is allocated twice.",
                    path="governance/manifests/generated/data-stores.json",
                    evidence=f"{seen[number]!r} and {entry['purpose']!r}",
                    repair="Two consumers sharing a logical database work fine until one of them flushes it.",
                )
            )
        seen[number] = entry["purpose"]
    return issues
