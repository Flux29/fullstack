"""Consistency of the documentation governance reads as fact.

An ADR is not prose to this system: its front matter is the authoritative declaration of
which components a decision binds, and `merge.build_components` derives every component's
`decision_refs` from it. That makes a malformed or self-contradictory ADR a data problem,
not a style problem — a `components` entry naming a component that does not exist silently
binds nothing, and a `supersedes` pointing at a live ADR leaves two decisions claiming the
same ground.

These checks detect; they never rewrite. The agent or human in the change session fixes
what is reported, the same division of labour every other governance surface uses.
"""

from __future__ import annotations

import fnmatch

from repo_governance.builders import read_adr_front_matter
from repo_governance.checks import CheckScope, relative
from repo_governance.config import iter_files
from repo_governance.io_atomic import read_json, relative_posix
from repo_governance.models import Issue

#: Statuses an ADR may hold. Mirrors the enum in decision-index.schema.json; duplicated as a
#: constant only so a check can name the allowed set in its repair text.
ADR_STATUSES: frozenset[str] = frozenset({"proposed", "accepted", "superseded", "deprecated", "rejected"})


def _adr_files(scope: CheckScope) -> list:
    return [path for path in iter_files(scope.ctx.paths.decisions, suffixes=(".md",)) if path.name.startswith("ADR-")]


def _id_from_filename(name: str) -> str:
    parts = name.split("-")
    return f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else name


def _known_component_ids(scope: CheckScope) -> set[str]:
    from repo_governance.merge import build_components

    manifest, _ = build_components(scope.ctx)
    return {component["id"] for component in manifest.get("components", [])}


def _known_policy_rule_ids(scope: CheckScope) -> set[str]:
    """Rule ids across every policy file.

    Read from the policy documents directly rather than through `checks.iter_rules` so this
    module does not import the dispatcher that imports it.
    """
    found: set[str] = set()
    for path in iter_files(scope.ctx.paths.policies, suffixes=(".json",)):
        try:
            document = read_json(path)
        except ValueError:
            continue
        found.update(rule["id"] for rule in document.get("rules", []) if "id" in rule)
    return found


def check_adr_front_matter(scope: CheckScope) -> list[Issue]:
    """Every ADR's front matter parses, carries the required keys, and owns its id."""
    issues: list[Issue] = []
    seen: dict[str, str] = {}

    for path in _adr_files(scope):
        rel = relative(scope.ctx, path)
        if not scope.selects(rel):
            continue

        fields, problems = read_adr_front_matter(path)
        for problem in problems:
            issues.append(
                Issue(
                    message=problem,
                    path=rel,
                    evidence="ADR front matter is the authoritative decision-to-component link, so it is parsed as data.",
                    repair="Correct the front matter; `governance/templates/adr.md` shows the full vocabulary.",
                )
            )

        declared_id = str(fields.get("id", "")).strip()
        expected = _id_from_filename(path.name)
        if declared_id and declared_id != expected:
            issues.append(
                Issue(
                    message=f"ADR declares id {declared_id!r} but its filename says {expected!r}.",
                    path=rel,
                    evidence="Every reference resolves by id, and the index resolves an id to this file by name.",
                    repair="Rename the file or correct the `id` so the two agree.",
                )
            )

        status = str(fields.get("status", "")).strip()
        if status and status not in ADR_STATUSES:
            issues.append(
                Issue(
                    message=f"ADR status {status!r} is not one of {', '.join(sorted(ADR_STATUSES))}.",
                    path=rel,
                    repair="Use a declared status; `deprecated` is for a decision retired with no successor.",
                )
            )

        if declared_id:
            if declared_id in seen:
                issues.append(
                    Issue(
                        message=f"ADR id {declared_id!r} is declared by two files.",
                        path=rel,
                        evidence=f"Also declared in {seen[declared_id]}.",
                        repair="Give one of them the next free id and update anything that references it.",
                    )
                )
            else:
                seen[declared_id] = rel

    return issues


def check_adr_consistency(scope: CheckScope) -> list[Issue]:
    """ADR cross-references agree: components exist, supersession is mutual, globs match."""
    issues: list[Issue] = []
    paths = _adr_files(scope)
    if not paths:
        return issues

    parsed = {path: read_adr_front_matter(path)[0] for path in paths}
    by_id = {str(fields.get("id", "")).strip(): (path, fields) for path, fields in parsed.items() if fields.get("id")}

    known_components = _known_component_ids(scope)
    known_rules = _known_policy_rule_ids(scope)
    superseded_targets: set[str] = set()
    for fields in parsed.values():
        superseded_targets.update(fields.get("supersedes", []))

    from repo_governance.gitutil import tracked_files

    # None when git is unavailable. A glob check that cannot see the tree reports nothing
    # rather than reporting everything as unmatched.
    tracked = tracked_files(scope.ctx.repo_root)

    for path, fields in parsed.items():
        rel = relative(scope.ctx, path)
        if not scope.selects(rel):
            continue
        adr_id = str(fields.get("id", "")).strip() or _id_from_filename(path.name)

        for component_id in fields.get("components", []):
            if component_id not in known_components:
                issues.append(
                    Issue(
                        message=f"{adr_id} names component {component_id!r}, which no component declares.",
                        path=rel,
                        evidence="decision_refs is derived from this list, so an unknown id binds the decision to nothing.",
                        repair="Correct the id, or declare the component in an annotation or architectural-intent.json.",
                    )
                )

        for target in fields.get("supersedes", []):
            if target not in by_id:
                issues.append(
                    Issue(
                        message=f"{adr_id} supersedes {target!r}, which does not exist.",
                        path=rel,
                        repair="Correct the reference, or write the ADR it means to replace.",
                    )
                )
                continue
            target_status = str(by_id[target][1].get("status", "")).strip()
            if target_status != "superseded":
                issues.append(
                    Issue(
                        message=f"{adr_id} supersedes {target}, but {target} still has status {target_status!r}.",
                        path=rel,
                        evidence="Two ADRs claiming the same ground with no losing side is how a stale decision keeps binding components.",
                        repair=f"Set {target} to status `superseded` in the same change.",
                    )
                )

        if str(fields.get("status", "")).strip() == "superseded" and adr_id not in superseded_targets:
            issues.append(
                Issue(
                    message=f"{adr_id} is marked superseded, but no ADR lists it in `supersedes`.",
                    path=rel,
                    evidence="A superseded decision must send the reader somewhere; `superseded_by` is derived from that link.",
                    repair="Name it in the superseding ADR's `supersedes`, or use status `deprecated` if there is no successor.",
                )
            )

        if tracked is not None:
            for pattern in fields.get("related_paths", []):
                if not any(fnmatch.fnmatch(candidate, pattern) for candidate in tracked):
                    issues.append(
                        Issue(
                            message=f"{adr_id} related_paths glob {pattern!r} matches no tracked file.",
                            path=rel,
                            evidence="governance context joins these globs to the paths under change; one that matches nothing is a stale decision.",
                            repair="Correct the glob, or drop it if the code it described is gone.",
                        )
                    )

        for rule_id in fields.get("policy_refs", []):
            if rule_id not in known_rules:
                issues.append(
                    Issue(
                        message=f"{adr_id} policy_refs {rule_id!r} does not resolve to a policy rule.",
                        path=rel,
                        repair="Correct the id, or declare the rule in governance/policies/.",
                    )
                )

    return issues


def check_decision_refs_are_derived(scope: CheckScope) -> list[Issue]:
    """No curated document or annotation declares `decision_refs`.

    The field is generated into components.json from ADR front matter. A hand-declaration is
    silently overwritten at the next sync, so it reads as a link that governance honours
    while being inert — precisely the drift making the ADR authoritative removed.
    """
    from repo_governance.documents import annotation_files

    issues: list[Issue] = []
    candidates = [scope.ctx.paths.curated_manifests / "architectural-intent.json", *annotation_files(scope.ctx)]

    for path in candidates:
        if not path.is_file():
            continue
        rel = relative_posix(path, scope.ctx.repo_root)
        if not scope.selects(rel):
            continue
        try:
            document = read_json(path)
        except ValueError:
            continue

        holders = [document] if "decision_refs" in document else []
        holders += [entry for entry in document.get("components", []) if "decision_refs" in entry]
        for holder in holders:
            issues.append(
                Issue(
                    message=f"{holder.get('id', path.name)!r} declares decision_refs by hand.",
                    path=rel,
                    evidence="decision_refs is derived at sync from the `components` list in each ADR's front matter.",
                    repair="Delete it here and name the component in the ADR's `components` instead.",
                )
            )

    return issues
