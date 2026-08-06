"""The committed governance tree is valid, and the schemas themselves are valid."""

from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from repo_governance.checks import CheckScope
from repo_governance.checks.schemas import check_document_schemas
from repo_governance.config import Context, iter_files
from repo_governance.documents import INTERNAL_SCHEMAS, governed_json_documents, schema_for
from repo_governance.io_atomic import read_json, relative_posix

#: The blueprint fixes the committed schema set at exactly these twelve files.
EXPECTED_SCHEMAS = {
    "ai-runtime.schema.json",
    "catalog.schema.json",
    "change-record.schema.json",
    "component.schema.json",
    "configuration.schema.json",
    "data-store.schema.json",
    "decision-index.schema.json",
    "graph-model.schema.json",
    "interface.schema.json",
    "local-governance.schema.json",
    "policy.schema.json",
    "service.schema.json",
}


def test_the_committed_schema_set_is_exactly_the_blueprint_set(real_context: Context) -> None:
    found = {path.name for path in iter_files(real_context.paths.schemas, suffixes=(".json",))}
    assert found == EXPECTED_SCHEMAS


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_each_committed_schema_is_a_valid_draft_2020_12_schema(real_context: Context, name: str) -> None:
    Draft202012Validator.check_schema(read_json(real_context.paths.schemas / name))


@pytest.mark.parametrize("key", sorted(INTERNAL_SCHEMAS))
def test_each_internal_schema_is_valid(key: str) -> None:
    Draft202012Validator.check_schema(INTERNAL_SCHEMAS[key])


def test_every_governed_document_has_a_schema(real_context: Context) -> None:
    unbound = [
        relative_posix(path, real_context.repo_root)
        for path in governed_json_documents(real_context)
        if schema_for(relative_posix(path, real_context.repo_root)) is None
    ]
    assert unbound == [], f"documents with no schema binding: {unbound}"


def test_the_committed_tree_validates(real_context: Context) -> None:
    issues = check_document_schemas(CheckScope(ctx=real_context))
    assert issues == [], "\n".join(f"{issue.path}: {issue.message} — {issue.evidence}" for issue in issues)


def test_internal_schema_documents_are_declared_in_ownership(real_context: Context) -> None:
    """The kernel-internal arrangement is visible in ownership.json, not buried in code."""
    from repo_governance.documents import INTERNAL_BINDINGS, load_ownership

    declared = {entry["path"] for entry in load_ownership(real_context).get("internal_schemas", [])}
    bound = {pattern for pattern, _ in INTERNAL_BINDINGS}
    assert bound <= declared, f"undeclared kernel-internal schemas: {sorted(bound - declared)}"


def test_a_malformed_annotation_is_reported(minimal_repo: Path) -> None:
    (minimal_repo / "component").mkdir()
    (minimal_repo / "component" / ".governance.json").write_text(
        '{"id": "Not A Slug", "kind": "backend-subsystem", "purpose": "x", "owns": []}',
        encoding="utf-8",
    )

    issues = check_document_schemas(CheckScope(ctx=Context.discover(minimal_repo)))
    messages = " ".join(issue.message + (issue.evidence or "") for issue in issues)
    assert "Schema violation" in messages


def test_invalid_json_is_reported_as_a_syntax_error_not_a_crash(minimal_repo: Path) -> None:
    (minimal_repo / "governance" / "history" / "changes" / "2026-01-01-broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    issues = check_document_schemas(CheckScope(ctx=Context.discover(minimal_repo)))
    assert any("not valid JSON" in issue.message for issue in issues)
