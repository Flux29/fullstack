"""Evaluation records are governed documents, not loose notes.

The blueprint requires a growth trigger to become an evaluation record before it becomes a
build task. That only means something if a malformed record is refused the same way any
other governed document is, so these tests pin the binding, the schema, and the failure
mode.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from repo_governance.checks import CheckScope
from repo_governance.checks.schemas import check_document_schemas
from repo_governance.config import Context, iter_files
from repo_governance.documents import INTERNAL_SCHEMAS, schema_for
from repo_governance.io_atomic import read_json


def test_evaluation_records_validate_against_internal_schema(real_context: Context) -> None:
    records = iter_files(real_context.paths.evaluations, suffixes=(".json",))
    assert records, "the evaluations directory exists but holds no records"
    validator = Draft202012Validator(INTERNAL_SCHEMAS["evaluations"])
    for record in records:
        validator.validate(read_json(record))


def test_evaluation_binding_resolves_for_evaluation_paths() -> None:
    resolved = schema_for("governance/history/evaluations/2026-08-09-example.json")
    assert resolved == ("internal", "evaluations")


def test_malformed_evaluation_record_fails_document_check(minimal_repo: Path) -> None:
    record = {
        "schema_version": 1,
        "id": "2026-01-01-missing-observation",
        "date": "2026-01-01",
        "kind": "growth-trigger",
        "summary": "A record with no observation field.",
        "evidence": [{"source": "test", "detail": "synthetic"}],
        "proposed_action": "none",
        "status": "open",
        "tool_version": "1.0.0",
    }
    evaluations = minimal_repo / "governance" / "history" / "evaluations"
    evaluations.mkdir(parents=True)
    (evaluations / "2026-01-01-missing-observation.json").write_text(json.dumps(record), encoding="utf-8")

    issues = check_document_schemas(CheckScope(ctx=Context.discover(minimal_repo)))
    messages = " ".join(issue.message + (issue.evidence or "") for issue in issues)
    assert "observation" in messages
