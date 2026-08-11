"""Which schema validates which document, and the kernel-internal schemas.

The blueprint fixes the committed schema set at exactly twelve files under
`governance/schemas/`. Documents outside that set — the validator registry, the ownership
rules, the exceptions manifest, the tests manifest, the merged effective manifest, the
read-gate surface, and evaluation records — have no schema in the committed twelve. Rather
than adding a thirteenth committed schema and diverging from the specified tree, those are
validated against schemas defined here, in the kernel. `governance/manifests/curated/ownership.json` records which documents this
applies to and why, so the arrangement is visible rather than buried in code.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from repo_governance.config import Context
from repo_governance.io_atomic import read_json, relative_posix

#: Document glob (repo-root-relative, POSIX) mapped to a schema file under
#: `governance/schemas/`. First match wins, so more specific patterns come first.
SCHEMA_BINDINGS: tuple[tuple[str, str], ...] = (
    ("governance/catalog.json", "catalog.schema.json"),
    ("governance/manifests/generated/components.json", "component.schema.json"),
    ("governance/manifests/curated/architectural-intent.json", "component.schema.json"),
    ("governance/manifests/generated/services.json", "service.schema.json"),
    ("governance/manifests/generated/configuration.json", "configuration.schema.json"),
    ("governance/manifests/generated/data-stores.json", "data-store.schema.json"),
    ("governance/manifests/generated/ai-runtime.json", "ai-runtime.schema.json"),
    ("governance/manifests/generated/interfaces.json", "interface.schema.json"),
    ("governance/policies/*.json", "policy.schema.json"),
    ("governance/history/changes/*.json", "change-record.schema.json"),
    ("governance/history/decisions/index.json", "decision-index.schema.json"),
    ("governance/graph/node-types.json", "graph-model.schema.json"),
    ("governance/graph/edge-types.json", "graph-model.schema.json"),
    ("*/.governance.json", "local-governance.schema.json"),
    ("**/.governance.json", "local-governance.schema.json"),
    (".governance.json", "local-governance.schema.json"),
)

#: Document glob mapped to a kernel-internal schema key in `INTERNAL_SCHEMAS`.
INTERNAL_BINDINGS: tuple[tuple[str, str], ...] = (
    ("governance/validators.json", "validators"),
    ("governance/manifests/curated/ownership.json", "ownership"),
    ("governance/manifests/curated/exceptions.json", "exceptions"),
    ("governance/manifests/generated/tests.json", "tests"),
    ("governance/manifests/generated/read-surface.json", "read-surface"),
    ("governance/manifests/effective/repository.json", "effective"),
    ("governance/history/evaluations/*.json", "evaluations"),
    ("governance/graph/reports/cycles.json", "graph-cycles"),
    ("governance/graph/reports/orphans.json", "graph-orphans"),
    ("governance/graph/reports/boundaries.json", "graph-boundaries"),
    ("governance/graph/reports/hotspots.json", "graph-hotspots"),
    ("governance/views/definitions.json", "views"),
)

_SLUG = {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"}
_PROVENANCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["method", "extractor_version"],
    "properties": {
        "method": {"enum": ["extracted", "reviewed-baseline", "curated", "merged"]},
        "sources": {"type": "array", "items": {"type": "string"}},
        "extractor_version": {"type": "string"},
    },
}

INTERNAL_SCHEMAS: dict[str, dict[str, Any]] = {
    "validators": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/validators/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "validators"],
        "properties": {
            "schema_version": {"const": 1},
            "description": {"type": "string"},
            "excluded": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "reason"],
                    "properties": {
                        "id": _SLUG,
                        "location": {"type": "string"},
                        "reason": {"type": "string", "minLength": 1},
                        "gates": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "validators": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "command", "description", "destructive"],
                    "properties": {
                        "id": _SLUG,
                        "command": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                        "destructive": {"const": False},
                        "requires": {"type": "array", "items": {"type": "string"}},
                        "ci_job": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
    "ownership": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/ownership/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "merge_rules", "generated_files", "curated_files"],
        "properties": {
            "schema_version": {"const": 1},
            "description": {"type": "string"},
            "merge_rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "statement"],
                    "properties": {
                        "id": _SLUG,
                        "statement": {"type": "string", "minLength": 1},
                        "rationale": {"type": "string"},
                    },
                },
            },
            "generated_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "regenerable", "source", "repair"],
                    "properties": {
                        "path": {"type": "string"},
                        "regenerable": {"type": "boolean"},
                        "source": {"type": "string"},
                        "repair": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "curated_files": {"type": "array", "items": {"type": "string"}},
            "generator_provenance": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "origin", "status", "on_upgrade", "reason"],
                    "properties": {
                        "path": {"type": "string"},
                        "origin": {"enum": ["generator", "local"]},
                        "status": {"enum": ["unchanged", "locally-diverged", "governance-generated"]},
                        "on_upgrade": {"enum": ["accept-upstream", "classify-conflict", "regenerate"]},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
            "internal_schemas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "reason"],
                    "properties": {
                        "path": {"type": "string"},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
    "exceptions": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/exceptions/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "exceptions"],
        "properties": {
            "schema_version": {"const": 1},
            "description": {"type": "string"},
            "exceptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "statement", "reason", "scope", "status"],
                    "properties": {
                        "id": _SLUG,
                        "statement": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                        "scope": {"type": "array", "items": {"type": "string"}},
                        "status": {"enum": ["active", "expired", "superseded"]},
                        "review_trigger": {"type": "string"},
                        "policy_refs": {"type": "array", "items": _SLUG},
                        "component_refs": {"type": "array", "items": _SLUG},
                    },
                },
            },
        },
    },
    "tests": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/tests/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "suites", "invariant_coverage"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "coverage_floors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["surface", "kind", "value", "basis"],
                    "properties": {
                        "surface": {"type": "string"},
                        "kind": {"type": "string"},
                        "value": {"type": "number"},
                        "basis": {"type": "string"},
                        "location": {"type": "string"},
                    },
                },
            },
            "suites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "framework", "location"],
                    "properties": {
                        "id": _SLUG,
                        "framework": {"type": "string"},
                        "location": {"type": "string"},
                        "validator": {"type": ["string", "null"]},
                        "gated_by": {"type": "array", "items": {"type": "string"}},
                        "requires": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "string"},
                    },
                },
            },
            "invariant_coverage": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["invariant", "component", "covered_by"],
                    "properties": {
                        "invariant": {"type": "string", "minLength": 1},
                        "component": _SLUG,
                        "covered_by": {"type": "array", "items": {"type": "string"}},
                        "gap": {"type": "string"},
                    },
                },
            },
        },
    },
    "read-surface": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/read-surface/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "default_modes", "breadth", "corpus", "repo"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "default_modes": {
                "type": "object",
                "additionalProperties": False,
                "required": ["breadth", "corpus", "repo"],
                "properties": {
                    "breadth": {"enum": ["off", "warn", "deny"]},
                    "corpus": {"enum": ["off", "warn", "deny"]},
                    "repo": {"enum": ["off", "warn", "deny"]},
                },
            },
            "breadth": {
                "type": "object",
                "additionalProperties": False,
                "required": ["roots", "shadow_roots"],
                "properties": {
                    "roots": {"type": "array", "items": {"type": "string"}},
                    "shadow_roots": {"type": "array", "items": {"type": "string"}},
                },
            },
            "corpus": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gated_roots", "bounded_surfaces"],
                "properties": {
                    "gated_roots": {"type": "array", "items": {"type": "string"}},
                    "bounded_surfaces": {"type": "array", "items": {"type": "string"}},
                },
            },
            "repo": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exact_files", "dir_prefixes", "glob_patterns"],
                "properties": {
                    "exact_files": {"type": "array", "items": {"type": "string"}},
                    "dir_prefixes": {"type": "array", "items": {"type": "string"}},
                    "glob_patterns": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    "effective": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/effective/v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "components", "conflicts"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "components": {"type": "array", "items": {"type": "object"}},
            "conflicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["component", "field", "detail"],
                    "properties": {
                        "component": {"type": "string"},
                        "field": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                },
            },
            "exceptions_applied": {"type": "array", "items": _SLUG},
        },
    },
    "evaluations": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/evaluations/v1",
        "description": (
            "Evidence about governance behavior itself: misses, false positives, context "
            "quality, and growth-trigger decisions. The blueprint requires a trigger to "
            "become an evaluation record before it becomes a build task."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "id",
            "date",
            "kind",
            "summary",
            "observation",
            "evidence",
            "proposed_action",
            "status",
            "tool_version",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "id": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}-[a-z0-9][a-z0-9-]{0,63}$"},
            "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
            "kind": {
                "enum": [
                    "growth-trigger",
                    "impact-quality",
                    "policy-outcome",
                    "sampler-mismatch",
                    "scenario-replay",
                ]
            },
            "summary": {"type": "string", "minLength": 1},
            "observation": {
                "type": "string",
                "minLength": 1,
                "description": "The symptom as observed, stated as fact, separate from what to do about it.",
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "detail"],
                    "properties": {
                        "source": {"type": "string", "minLength": 1},
                        "detail": {"type": "string", "minLength": 1},
                        "ref": {"type": "string"},
                    },
                },
            },
            "proposed_action": {"type": "string", "minLength": 1},
            "status": {"enum": ["open", "acted-on", "declined", "superseded"]},
            "initiated_by": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind"],
                "properties": {
                    "kind": {"enum": ["user", "agent", "automation"]},
                    "ref": {"type": "string"},
                },
            },
            "trigger_ref": {
                "type": "string",
                "description": "The named growth trigger or policy rule this record evaluates.",
            },
            "policy_refs": {"type": "array", "items": _SLUG},
            "component_refs": {"type": "array", "items": _SLUG},
            "related_change_records": {
                "type": "array",
                "items": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}-[a-z0-9][a-z0-9-]{0,63}$"},
            },
            "limitations": {"type": "array", "items": {"type": "string"}},
            "tool_version": {"type": "string"},
        },
    },
    "views": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/views/v1",
        "description": "The bounded views governance visualize can render.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "views"],
        "properties": {
            "schema_version": {"const": 1},
            "description": {"type": "string"},
            "views": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "title", "description", "focus", "sources", "node_budget"],
                    "properties": {
                        "id": _SLUG,
                        "title": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                        "focus": {"type": "string", "minLength": 1},
                        "sources": {"type": "array", "items": {"type": "string"}},
                        "node_budget": {"type": "integer", "minimum": 10},
                    },
                },
            },
        },
    },
    "graph-cycles": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/graph-cycles/v1",
        "description": "Dependency cycles per language, each classified import-time or deferred-import.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "python", "typescript"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "python": {"$ref": "#/$defs/cycles"},
            "typescript": {"$ref": "#/$defs/cycles"},
        },
        "$defs": {
            "cycles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["modules", "import_time"],
                    "properties": {
                        "modules": {"type": "array", "minItems": 2, "items": {"type": "string"}},
                        "import_time": {"type": "boolean"},
                    },
                },
            }
        },
    },
    "graph-boundaries": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/graph-boundaries/v1",
        "description": "Per-page chain coverage: page to proxy to backend, broken chains, and WS-exception consumers.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "summary", "pages"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "summary": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "pages",
                    "pages_with_api_chains",
                    "pages_with_broken_chains",
                    "websocket_exception_pages",
                ],
                "properties": {
                    "pages": {"type": "integer", "minimum": 0},
                    "pages_with_api_chains": {"type": "integer", "minimum": 0},
                    "pages_with_broken_chains": {"type": "integer", "minimum": 0},
                    "websocket_exception_pages": {"type": "integer", "minimum": 0},
                },
            },
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["route", "file"],
                    "properties": {
                        "route": {"type": "string"},
                        "file": {"type": "string"},
                        "proxy_handlers": {"type": "array", "items": {"type": "string"}},
                        "backend_modules": {"type": "array", "items": {"type": "string"}},
                        "server_calls": {"type": "array", "items": {"type": "string"}},
                        "unmatched_paths": {"type": "array", "items": {"type": "string"}},
                        "websocket": {"const": True},
                    },
                },
            },
        },
    },
    "graph-hotspots": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/graph-hotspots/v1",
        "description": "Structural centrality per typed view plus the invariant-coverage risk join. Churn is cache-only by design.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "views", "risk"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "views": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["pagerank", "betweenness"],
                    "properties": {
                        "pagerank": {"$ref": "#/$defs/ranking"},
                        "betweenness": {"$ref": "#/$defs/ranking"},
                    },
                },
            },
            "risk": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["component", "invariants", "uncovered", "gaps", "max_module_centrality"],
                    "properties": {
                        "component": {"type": "string"},
                        "invariants": {"type": "integer", "minimum": 0},
                        "uncovered": {"type": "array", "items": {"type": "string"}},
                        "gaps": {"type": "array", "items": {"type": "string"}},
                        "max_module_centrality": {"type": "number", "minimum": 0},
                    },
                },
            },
        },
        "$defs": {
            "ranking": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "score"],
                    "properties": {
                        "id": {"type": "string"},
                        "score": {"type": "number", "minimum": 0},
                    },
                },
            }
        },
    },
    "graph-orphans": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fullstack-governance:internal/graph-orphans/v1",
        "description": "Modules with no importer and no explaining classification, plus what each exclusion class absorbed.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "provenance", "python", "typescript"],
        "properties": {
            "schema_version": {"const": 1},
            "provenance": _PROVENANCE,
            "python": {"$ref": "#/$defs/side"},
            "typescript": {"$ref": "#/$defs/side"},
        },
        "$defs": {
            "side": {
                "type": "object",
                "additionalProperties": False,
                "required": ["orphans", "excluded"],
                "properties": {
                    "orphans": {"type": "array", "items": {"type": "string"}},
                    "excluded": {
                        "type": "object",
                        "additionalProperties": {"type": "integer", "minimum": 0},
                    },
                },
            }
        },
    },
}


def _matches(relative: str, pattern: str) -> bool:
    if pattern.endswith("/.governance.json"):
        return relative == ".governance.json" or relative.endswith("/.governance.json")
    return fnmatch.fnmatch(relative, pattern)


def schema_for(relative_path: str) -> tuple[str, str] | None:
    """Return `(kind, key)` for a document, or None when nothing validates it.

    `kind` is `"file"` for a schema under `governance/schemas/`, `"internal"` for a
    kernel-internal schema.
    """
    for pattern, schema_file in SCHEMA_BINDINGS:
        if _matches(relative_path, pattern):
            return ("file", schema_file)
    for pattern, key in INTERNAL_BINDINGS:
        if _matches(relative_path, pattern):
            return ("internal", key)
    return None


def governed_json_documents(ctx: Context) -> list[Path]:
    """Every JSON document governance is responsible for validating.

    Includes the governance tree and every directory annotation in the repository. Schemas
    themselves are excluded — they are validated as schemas, not as instances.
    """
    from repo_governance.config import iter_files

    documents = [
        path
        for path in iter_files(ctx.paths.governance, suffixes=(".json",))
        if ctx.paths.schemas not in path.parents
    ]
    documents.extend(annotation_files(ctx))
    return sorted(documents, key=lambda item: relative_posix(item, ctx.repo_root))


def annotation_files(ctx: Context) -> list[Path]:
    """Every `.governance.json` directory annotation, in stable order."""
    from repo_governance.config import EXCLUDED_DIRS

    found: list[Path] = []
    for path in ctx.repo_root.rglob(".governance.json"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ctx.repo_root).parts):
            continue
        found.append(path)
    return sorted(found, key=lambda item: relative_posix(item, ctx.repo_root))


def load_validators(ctx: Context) -> dict[str, Any]:
    if not ctx.paths.validators.is_file():
        return {"validators": [], "excluded": []}
    return read_json(ctx.paths.validators)


def validator_ids(ctx: Context) -> set[str]:
    return {entry["id"] for entry in load_validators(ctx).get("validators", [])}


def load_ownership(ctx: Context) -> dict[str, Any]:
    path = ctx.paths.curated_manifests / "ownership.json"
    if not path.is_file():
        return {"generated_files": [], "curated_files": []}
    return read_json(path)
