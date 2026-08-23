"""Compile annotations and curated declarations into the components and effective manifests.

Two rules shape everything here:

* A component is declared in a directory annotation **or** in the curated manifest, never
  both. Two declarations of one component are two sources of truth for its intent.
* Inheritance can only make a component stricter. Only `forbidden_dependencies` and
  `validation` are inherited — one restricts, the other adds checks. `allowed_dependencies`
  is deliberately not inheritable: inheriting it would let a child silently widen what it
  may depend on by saying nothing at all.

Disagreements the rules do not resolve become conflicts in the effective manifest rather
than a silently chosen winner.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from repo_governance.config import Context
from repo_governance.documents import annotation_files
from repo_governance.io_atomic import read_json, relative_posix

#: Fields a component inherits from its nearest governed ancestor when it does not set them.
#: Both are monotonically restrictive, so inheritance can never widen a permission.
INHERITABLE_FIELDS = ("forbidden_dependencies", "validation")

COMPONENT_FIELDS = (
    "id",
    "kind",
    "purpose",
    "declared_in",
    "annotation_path",
    "owns",
    "entrypoints",
    "public_interfaces",
    "invariants",
    "allowed_dependencies",
    "forbidden_dependencies",
    "configuration_refs",
    "compose_services",
    "decision_refs",
    "finding_refs",
    "validation",
    "generator_feature",
    "graph_hints",
)


def _load_annotations(ctx: Context) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for path in annotation_files(ctx):
        try:
            document = read_json(path)
        except ValueError:
            continue  # Reported by the schema check; not silently dropped from findings.
        record = {key: value for key, value in document.items() if key != "$schema"}
        record["declared_in"] = "annotation"
        record["annotation_path"] = relative_posix(path, ctx.repo_root)
        components.append(record)
    return components


def _load_curated(ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = ctx.paths.curated_manifests / "architectural-intent.json"
    if not path.is_file():
        return [], {}
    document = read_json(path)
    components = []
    for record in document.get("components", []):
        entry = dict(record)
        entry["declared_in"] = "curated"
        components.append(entry)
    return components, document.get("defaults", {})


def _ancestor_of(annotation_path: str, candidates: dict[str, str]) -> str | None:
    """Find the nearest governed ancestor annotation, by directory depth."""
    own_dir = PurePosixPath(annotation_path).parent
    best: tuple[int, str] | None = None
    for other_path, component_id in candidates.items():
        if other_path == annotation_path:
            continue
        other_dir = PurePosixPath(other_path).parent
        if own_dir == other_dir or other_dir not in own_dir.parents:
            continue
        depth = len(other_dir.parts)
        if best is None or depth > best[0]:
            best = (depth, component_id)
    return best[1] if best else None


#: ADR statuses whose decisions still bind. A superseded, deprecated, or rejected decision
#: is history: its successor carries the constraint, and surfacing it in a briefing would
#: send a reader to a document that no longer describes the repository.
BINDING_DECISION_STATUSES: frozenset[str] = frozenset({"accepted", "proposed"})


def _derive_decision_refs(decision_index: dict[str, Any]) -> dict[str, list[str]]:
    """Invert the ADR index into `{component id: [ADR ids]}`.

    The ADR's `components` list is the single declaration of this link. Inverting it here
    means a component's `decision_refs` cannot disagree with the ADR that names it — before
    this, both sides declared the link and five of thirteen had drifted one-directional.
    """
    by_component: dict[str, list[str]] = {}
    for decision in decision_index.get("decisions", []):
        if decision.get("status") not in BINDING_DECISION_STATUSES:
            continue
        for component_id in decision.get("components", []):
            by_component.setdefault(component_id, []).append(decision["id"])
    return {component_id: sorted(set(refs)) for component_id, refs in by_component.items()}


def build_components(
    ctx: Context, decision_index: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Compile every declaration into the components manifest.

    Returns the manifest and any conflicts found while compiling it.

    `decision_index` is accepted so the sync pipeline can hand over the index it already
    built rather than paying to parse the ADRs again; when it is omitted the index is built
    here, so every caller gets derived `decision_refs` whether or not it knows about ADRs.
    """
    from repo_governance.builders import build_decision_index

    annotations = _load_annotations(ctx)
    curated, defaults = _load_curated(ctx)
    if decision_index is None:
        decision_index = build_decision_index(ctx)
    decision_refs = _derive_decision_refs(decision_index)
    conflicts: list[dict[str, str]] = []

    by_id: dict[str, dict[str, Any]] = {}
    for record in [*annotations, *curated]:
        component_id = record.get("id")
        if not component_id:
            continue
        if component_id in by_id:
            first = by_id[component_id]
            conflicts.append(
                {
                    "component": component_id,
                    "field": "declared_in",
                    "detail": (
                        f"Declared twice: once as {first['declared_in']} and once as "
                        f"{record['declared_in']}. A component must be declared in exactly one place."
                    ),
                }
            )
            continue
        by_id[component_id] = record

    annotation_index = {record["annotation_path"]: record["id"] for record in annotations if record.get("id")}

    for record in by_id.values():
        if record["declared_in"] == "annotation":
            ancestor_id = _ancestor_of(record["annotation_path"], annotation_index)
            source = by_id.get(ancestor_id) if ancestor_id else None
        else:
            source = defaults

        if not source:
            continue
        for field in INHERITABLE_FIELDS:
            if field not in record and field in source:
                record[field] = list(source[field])

    # Derivation is assignment, not merge: a hand-declared `decision_refs` is replaced, and
    # a component no ADR names loses the field entirely. Anything softer would let a stale
    # declaration survive as the very drift this removes.
    for component_id, record in by_id.items():
        derived = decision_refs.get(component_id)
        if derived:
            record["decision_refs"] = derived
        else:
            record.pop("decision_refs", None)

    components = []
    for component_id in sorted(by_id):
        record = by_id[component_id]
        ordered = {field: record[field] for field in COMPONENT_FIELDS if field in record}
        components.append(ordered)

    manifest = {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": [
                "**/.governance.json",
                "governance/manifests/curated/architectural-intent.json",
            ],
            "extractor_version": ctx.config.version,
        },
        "components": components,
    }
    return manifest, conflicts


def build_effective(ctx: Context, decision_index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge generated facts with curated intent and the exception overlay."""
    components_manifest, conflicts = build_components(ctx, decision_index)

    exceptions_path = ctx.paths.curated_manifests / "exceptions.json"
    exceptions = read_json(exceptions_path).get("exceptions", []) if exceptions_path.is_file() else []
    active = [entry for entry in exceptions if entry.get("status") == "active"]

    by_component: dict[str, list[str]] = {}
    for entry in active:
        for component_id in entry.get("component_refs", []):
            by_component.setdefault(component_id, []).append(entry["id"])

    enriched = []
    for component in components_manifest["components"]:
        record = dict(component)
        applicable = sorted(by_component.get(component["id"], []))
        if applicable:
            record["exceptions"] = applicable
        enriched.append(record)

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "merged",
            "sources": [
                "governance/manifests/generated/components.json",
                "governance/manifests/curated/architectural-intent.json",
                "governance/manifests/curated/exceptions.json",
            ],
            "extractor_version": ctx.config.version,
        },
        "components": enriched,
        "conflicts": sorted(conflicts, key=lambda item: (item["component"], item["field"])),
        "exceptions_applied": sorted(entry["id"] for entry in active),
    }
