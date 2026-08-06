"""Schema validity, generated-file integrity, idempotence, and contract version."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from repo_governance.checks import CheckScope, relative
from repo_governance.documents import (
    INTERNAL_SCHEMAS,
    governed_json_documents,
    load_ownership,
    schema_for,
)
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue
from repo_governance.pipeline import compare_generated, render_all


def check_document_schemas(scope: CheckScope) -> list[Issue]:
    """Every governed JSON document parses and validates against its schema.

    A document with no binding is reported rather than skipped: an unvalidated governance
    file is a hole in the contract, not a convenience.
    """
    ctx = scope.ctx
    issues: list[Issue] = []
    schema_cache: dict[str, Draft202012Validator] = {}

    for path in governed_json_documents(ctx):
        rel = relative(ctx, path)
        if not scope.selects(rel):
            continue

        try:
            document = read_json(path)
        except json.JSONDecodeError as exc:
            issues.append(
                Issue(
                    message="Document is not valid JSON.",
                    path=rel,
                    evidence=f"{exc.msg} at line {exc.lineno}, column {exc.colno}",
                    repair="Fix the syntax error.",
                )
            )
            continue

        binding = schema_for(rel)
        if binding is None:
            issues.append(
                Issue(
                    message="No schema validates this document.",
                    path=rel,
                    repair=(
                        "Add a binding in documents.py SCHEMA_BINDINGS, or record it as "
                        "kernel-internal in ownership.json internal_schemas."
                    ),
                )
            )
            continue

        kind, key = binding
        validator = schema_cache.get(key)
        if validator is None:
            if kind == "file":
                schema_path = ctx.paths.schemas / key
                if not schema_path.is_file():
                    issues.append(
                        Issue(
                            message=f"Schema {key} is missing.",
                            path=rel,
                            repair=f"Restore governance/schemas/{key}.",
                        )
                    )
                    continue
                schema = read_json(schema_path)
            else:
                schema = INTERNAL_SCHEMAS[key]

            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                issues.append(
                    Issue(
                        message=f"Schema {key} is itself invalid.",
                        path=f"governance/schemas/{key}" if kind == "file" else rel,
                        evidence=exc.message,
                        repair="Fix the schema before validating instances against it.",
                    )
                )
                continue

            validator = Draft202012Validator(schema)
            schema_cache[key] = validator

        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
            location = "/".join(str(part) for part in error.absolute_path) or "(document root)"
            issues.append(
                Issue(
                    message=f"Schema violation at {location}.",
                    path=rel,
                    evidence=error.message,
                    repair=f"Make the document conform to {key}.",
                )
            )

    return issues


def check_generated_files(scope: CheckScope) -> list[Issue]:
    """Generated files reproduce byte-identically from their sources."""
    ctx = scope.ctx
    issues: list[Issue] = []

    for path, reason in compare_generated(ctx):
        if not scope.selects(path):
            continue
        issues.append(
            Issue(
                message=f"Generated file is out of date or hand-edited: {reason}.",
                path=path,
                evidence="Regenerating from the current sources produces different bytes.",
                repair="make governance-sync",
            )
        )

    return issues


def check_idempotence(scope: CheckScope) -> list[Issue]:
    """Rendering twice produces identical bytes.

    This catches nondeterminism inside a single process — unsorted iteration, a stray
    timestamp. Cross-process byte identity, which additionally catches hash-seed-dependent
    ordering, is covered by the tool's own test suite and by the CI job's clean rebuild.
    """
    first = render_all(scope.ctx)
    second = render_all(scope.ctx)

    issues: list[Issue] = []
    for path in sorted(set(first) | set(second)):
        if first.get(path) != second.get(path):
            issues.append(
                Issue(
                    message="Rendering the same sources twice produced different output.",
                    path=path,
                    evidence="Something in the render path is nondeterministic.",
                    repair="Remove the volatile value; sort any set or dict iteration that reaches output.",
                )
            )
    return issues


def check_curated_not_generated(scope: CheckScope) -> list[Issue]:
    """No path is claimed by both the generated registry and the curated list."""
    ctx = scope.ctx
    ownership = load_ownership(ctx)
    generated = {entry["path"].rstrip("/") for entry in ownership.get("generated_files", [])}
    curated = {entry.rstrip("/") for entry in ownership.get("curated_files", [])}

    issues: list[Issue] = []
    for path in sorted(generated & curated):
        issues.append(
            Issue(
                message="Path is listed as both generated and curated.",
                path=path,
                evidence="Ownership must be unambiguous; sync would overwrite curated intent.",
                repair="Remove the path from one of the two lists in ownership.json.",
            )
        )

    rendered = set(render_all(ctx))
    for path in sorted(rendered):
        if path.rstrip("/") in curated:
            issues.append(
                Issue(
                    message="Sync produces a file that ownership.json calls curated.",
                    path=path,
                    evidence="A sync would silently overwrite human-authored content.",
                    repair="Either stop generating this path or move it out of curated_files.",
                )
            )
        if path not in generated and not any(path.startswith(prefix) for prefix in generated):
            issues.append(
                Issue(
                    message="Sync produces a file that is not in the generated-file registry.",
                    path=path,
                    evidence="Direct-edit detection only covers registered files, so this one is unprotected.",
                    repair="Add the path to generated_files in ownership.json.",
                )
            )

    return issues


def check_contract_version(scope: CheckScope) -> list[Issue]:
    """The tool supports the governance contract version it is being asked to interpret."""
    from repo_governance import __version__

    ctx = scope.ctx
    requirement = ctx.config.raw.get("governance", {}).get("requires_tool", "")
    issues: list[Issue] = []

    tool_major = __version__.split(".")[0]
    contract_major = ctx.config.version.split(".")[0]
    if tool_major != contract_major:
        issues.append(
            Issue(
                message=f"Tool version {__version__} cannot interpret governance contract {ctx.config.version}.",
                path="governance/governance.toml",
                evidence=f"The contract declares requires_tool = {requirement!r}.",
                repair="Upgrade the tool, or run `governance migrate --to` to move the contract.",
            )
        )

    if ctx.config.schema_version != 1:
        issues.append(
            Issue(
                message=f"Unsupported schema_version {ctx.config.schema_version}.",
                path="governance/governance.toml",
                evidence="This tool implements schema version 1 only.",
                repair="Refusing to interpret a newer contract is deliberate; upgrade the tool.",
            )
        )

    return issues
