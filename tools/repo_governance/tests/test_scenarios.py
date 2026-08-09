"""The evaluation-scenario corpus and its replay harness.

Scenarios are only useful if their vocabulary stays anchored: an expected component that
no manifest declares, or an expected validator missing from the registry, would make the
before/after comparison meaningless. These tests pin the contract, the anchoring, and the
baseline replay itself.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from scenario_contract import SCENARIO_SCHEMA, load_scenarios, scenario_ids
from scenario_replay import compare_or_update, score_scenario, update_mode

from repo_governance.checks import iter_rules
from repo_governance.config import Context
from repo_governance.documents import validator_ids
from repo_governance.io_atomic import read_json, write_text_atomic


@pytest.mark.parametrize("scenario_id", scenario_ids())
def test_scenario_files_conform_to_scenario_contract(scenario_id: str) -> None:
    scenario = next(s for s in load_scenarios() if s["id"] == scenario_id)
    Draft202012Validator(SCENARIO_SCHEMA).validate(scenario)
    if scenario["kind"] == "historical":
        assert "source_change_record" in scenario, "a historical scenario names the record that seeded it"


def test_scenario_corpus_covers_both_kinds() -> None:
    kinds = {scenario["kind"] for scenario in load_scenarios()}
    assert kinds == {"historical", "synthetic"}


def test_scenario_expected_ids_resolve_against_governance_documents(real_context: Context) -> None:
    components = {
        component["id"]
        for component in read_json(real_context.paths.effective_repository).get("components", [])
    }
    validators = validator_ids(real_context)
    rules = {rule.id for rule in iter_rules(real_context)}

    for scenario in load_scenarios():
        expected = scenario["expected"]
        assert set(expected["components"]) <= components, scenario["id"]
        assert set(expected["validators"]) <= validators, scenario["id"]
        assert set(expected["policies"]) <= rules, scenario["id"]
        record = scenario.get("source_change_record")
        if record is not None:
            assert (real_context.paths.changes / f"{record}.json").is_file(), scenario["id"]


@pytest.mark.parametrize("scenario_id", scenario_ids())
def test_replay_scenario_impact_matches_golden(real_context: Context, scenario_id: str) -> None:
    scenario = next(s for s in load_scenarios() if s["id"] == scenario_id)
    observed, golden, target = compare_or_update(real_context, scenario)
    assert target.is_file(), (
        f"no golden captured for {scenario_id}; run the suite once with "
        "GOVERNANCE_UPDATE_GOLDENS=1 to record the baseline"
    )
    assert observed == golden, f"impact answer for {scenario_id} diverged from {target.name}"


def test_replay_writes_scenario_report_to_cache(real_context: Context) -> None:
    """The per-scenario metrics land in the gitignored cache, never in a committed file:
    the report carries floats and counts that shift with the tree, and runtime, which is
    volatile by definition."""
    report = {
        "scenarios": [score_scenario(real_context, scenario) for scenario in load_scenarios()],
        "update_mode": update_mode(),
    }
    target = real_context.repo_root / ".cache" / "repo-governance" / "scenario-report.json"
    write_text_atomic(target, json.dumps(report, indent=2, sort_keys=True) + "\n")

    written = read_json(target)
    assert len(written["scenarios"]) == len(load_scenarios())
    for entry in written["scenarios"]:
        for field_scores in entry["fields"].values():
            assert 0.0 <= field_scores["precision"] <= 1.0
            assert 0.0 <= field_scores["recall"] <= 1.0
