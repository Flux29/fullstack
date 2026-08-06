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
