"""The evaluation-scenario file contract.

Scenarios live beside the tests, not under governance/: they are instruments for judging
the governance system, not governed documents themselves, so their schema stays test-side
and the committed twelve stay untouched. The shape mirrors the README contract - each
scenario declares the components, policies, validators, context documents, and findings a
change should produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repo_governance.io_atomic import read_json

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
GOLDEN_DIR = Path(__file__).parent / "golden" / "impact-baselines"

SCENARIO_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "kind", "title", "description", "change", "expected", "notes"],
    "properties": {
        "schema_version": {"const": 1},
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
        "kind": {"enum": ["historical", "synthetic"]},
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "source_change_record": {
            "type": "string",
            "pattern": "^\\d{4}-\\d{2}-\\d{2}-[a-z0-9][a-z0-9-]{0,63}$",
        },
        "change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["paths"],
            "properties": {
                "paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "depth": {"type": "integer", "minimum": 0},
            },
        },
        "expected": {
            "type": "object",
            "additionalProperties": False,
            "required": ["components", "validators", "proxy_routes", "context_documents", "policies", "findings"],
            "properties": {
                "components": {"type": "array", "items": {"type": "string"}},
                "validators": {"type": "array", "items": {"type": "string"}},
                "proxy_routes": {"type": "array", "items": {"type": "string"}},
                "context_documents": {"type": "array", "items": {"type": "string"}},
                "policies": {"type": "array", "items": {"type": "string"}},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
        },
        "notes": {"type": "string", "minLength": 1},
    },
}


def load_scenarios() -> list[dict[str, Any]]:
    """Every scenario, in stable id order. Raises when a file is not valid JSON."""
    scenarios = [read_json(path) for path in sorted(SCENARIOS_DIR.glob("*.json"))]
    return sorted(scenarios, key=lambda scenario: scenario.get("id", ""))


def scenario_ids() -> list[str]:
    return [scenario["id"] for scenario in load_scenarios()]
