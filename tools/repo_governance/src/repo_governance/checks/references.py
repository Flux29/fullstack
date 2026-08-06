"""Cross-document reference integrity.

A reference that does not resolve is worse than a missing one: it reads as an assurance
that something was checked when nothing was.
"""

from __future__ import annotations

import re

from repo_governance.checks import CheckScope, relative, resolvable_check_impls
from repo_governance.config import iter_files
from repo_governance.documents import governed_json_documents, load_validators
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue

#: Anything that looks like a command rather than an identifier. Validator references must
#: be IDs resolved through the registry, never executable text.
SHELL_TEXT = re.compile(r"[\s/\\|&;><$`]|--")

VALIDATOR_FIELDS = ("validation",)


def _iter_validator_references(document: object, path: str) -> list[tuple[str, str]]:
    """Collect `(field, value)` validator references anywhere in a document."""
    found: list[tuple[str, str]] = []

    def walk(node: object, trail: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in VALIDATOR_FIELDS and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            found.append((f"{trail}/{key}", item))
                else:
                    walk(value, f"{trail}/{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{trail}/{index}")

    walk(document, path)
    return found


def check_validator_references(scope: CheckScope) -> list[Issue]:
    """Every validator reference resolves to a registry ID and carries no shell text."""
    ctx = scope.ctx
    registry = load_validators(ctx)
    known = {entry["id"] for entry in registry.get("validators", [])}
    excluded = {entry["id"] for entry in registry.get("excluded", [])}

    issues: list[Issue] = []
    for path in governed_json_documents(ctx):
        rel = relative(ctx, path)
        if not scope.selects(rel):
            continue
        try:
            document = read_json(path)
        except ValueError:
            continue  # Reported by the schema check.

        for field, value in _iter_validator_references(document, rel):
            if SHELL_TEXT.search(value):
                issues.append(
                    Issue(
                        message="Validator reference looks like executable text rather than an ID.",
                        path=rel,
                        evidence=f"{field} = {value!r}",
                        repair="Reference a validator ID from governance/validators.json instead.",
                    )
                )
                continue
            if value in excluded:
                issues.append(
                    Issue(
                        message=f"Validator {value!r} is deliberately excluded from the registry.",
                        path=rel,
                        evidence="Excluded validators mutate live services and must never be selectable.",
                        repair="Remove the reference; run that suite by hand, knowingly.",
                    )
                )
                continue
            if value not in known:
                issues.append(
                    Issue(
                        message=f"Validator {value!r} is not in the registry.",
                        path=rel,
                        evidence=f"{field} references an unknown validator ID.",
                        repair="Add it to governance/validators.json (a reviewed change) or fix the reference.",
                    )
                )

    return issues


def check_excluded_validators(scope: CheckScope) -> list[Issue]:
    """Excluded validators stay out of the registry, and the registry holds nothing destructive."""
    ctx = scope.ctx
    registry = load_validators(ctx)
    validators = registry.get("validators", [])
    excluded_ids = {entry["id"] for entry in registry.get("excluded", [])}

    issues: list[Issue] = []
    for entry in validators:
        if entry["id"] in excluded_ids:
            issues.append(
                Issue(
                    message=f"Validator {entry['id']!r} appears in both the registry and the excluded list.",
                    path="governance/validators.json",
                    repair="Remove it from one. Excluded means unselectable.",
                )
            )
        if entry.get("destructive"):
            issues.append(
                Issue(
                    message=f"Validator {entry['id']!r} is marked destructive but is in the registry.",
                    path="governance/validators.json",
                    evidence="Destructive validators must be excluded so impact analysis cannot select them.",
                    repair="Move it to the excluded list.",
                )
            )

    for entry in registry.get("excluded", []):
        if not entry.get("reason"):
            issues.append(
                Issue(
                    message=f"Excluded validator {entry['id']!r} has no reason.",
                    path="governance/validators.json",
                    evidence="An unexplained omission is indistinguishable from an oversight.",
                    repair="State why it is excluded.",
                )
            )

    return issues


def check_document_references(scope: CheckScope) -> list[Issue]:
    """ADR, exception, component, and finding references resolve to something real."""
    ctx = scope.ctx
    issues: list[Issue] = []

    known_adrs = {
        path.name.split("-")[0] + "-" + path.name.split("-")[1]
        for path in iter_files(ctx.paths.decisions, suffixes=(".md",))
        if path.name.startswith("ADR-")
    }

    known_exceptions: set[str] = set()
    exceptions_path = ctx.paths.curated_manifests / "exceptions.json"
    if exceptions_path.is_file():
        known_exceptions = {entry["id"] for entry in read_json(exceptions_path).get("exceptions", [])}

    known_findings: set[str] = set()
    for path in iter_files(ctx.paths.changes, suffixes=(".json",)):
        try:
            record = read_json(path)
        except ValueError:
            continue
        known_findings.update(finding["id"] for finding in record.get("findings", []))

    for path in governed_json_documents(ctx):
        rel = relative(ctx, path)
        if not scope.selects(rel):
            continue
        try:
            document = read_json(path)
        except ValueError:
            continue

        for field, value in _collect(document, rel, ("decision_refs", "adr_refs")):
            if value not in known_adrs:
                issues.append(
                    Issue(
                        message=f"Decision reference {value!r} does not resolve to an ADR.",
                        path=rel,
                        evidence=f"{field} references a missing decision record.",
                        repair="Write the ADR, or correct the reference.",
                    )
                )

        for field, value in _collect(
            document, rel, ("exception_refs", "deliberate_exception_ref", "proxy_exception_ref", "accepted_history_ref")
        ):
            if value not in known_exceptions:
                issues.append(
                    Issue(
                        message=f"Exception reference {value!r} does not resolve.",
                        path=rel,
                        evidence=f"{field} references an exception that is not declared.",
                        repair="Declare it in governance/manifests/curated/exceptions.json, or correct the reference.",
                    )
                )

        for field, value in _collect(document, rel, ("finding_refs", "standing_findings")):
            if value not in known_findings:
                issues.append(
                    Issue(
                        message=f"Finding reference {value!r} does not resolve.",
                        path=rel,
                        evidence=f"{field} references a finding that no change record registers.",
                        repair="Register the finding in a change record, or correct the reference.",
                    )
                )

    return issues


def check_check_impl_resolves(scope: CheckScope) -> list[Issue]:
    """Every policy rule's `check_impl` is implemented, deferred, or explicitly manual."""
    ctx = scope.ctx
    resolvable = resolvable_check_impls()
    issues: list[Issue] = []

    for path in iter_files(ctx.paths.policies, suffixes=(".json",)):
        try:
            document = read_json(path)
        except ValueError:
            continue
        rel = relative(ctx, path)
        for rule in document.get("rules", []):
            impl = rule.get("check_impl", "")
            if impl == "manual" or impl.startswith("phase-") or impl in resolvable:
                continue
            issues.append(
                Issue(
                    message=f"Rule {rule.get('id')!r} declares an unimplemented check_impl {impl!r}.",
                    path=rel,
                    evidence="The rule would silently never run.",
                    repair="Implement and register the check, or mark it `manual` or a phase marker.",
                )
            )

    return issues


def _collect(document: object, trail: str, fields: tuple[str, ...]) -> list[tuple[str, str]]:
    """Collect `(field path, value)` for the named fields anywhere in a document."""
    found: list[tuple[str, str]] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in fields:
                    if isinstance(value, str):
                        found.append((f"{path}/{key}", value))
                    elif isinstance(value, list):
                        found.extend((f"{path}/{key}", item) for item in value if isinstance(item, str))
                else:
                    walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}/{index}")

    walk(document, trail)
    return found
