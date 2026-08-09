"""Replay scenarios through impact analysis and score the answers.

The phase-6 exit criterion is judged here: replay the corpus, compare precision, recall,
and context size against the committed baselines. Runtime is measured but written only to
`.cache/` - a committed golden must never carry a volatile value.

Golden discipline: `<id>.before.json` is the manifest-only answer, captured before the
import graph existed; `<id>.after.json` is the graph-aware answer. The replay test
compares against `.after.json` when it exists, so the before-goldens stay committed as the
comparison input rather than being overwritten.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

from scenario_contract import GOLDEN_DIR

from repo_governance.config import Context
from repo_governance.io_atomic import canonical_json, read_json, write_text_atomic
from repo_governance.renderers.context import CHARS_PER_TOKEN, analyse_impact, render_context, rules_for

UPDATE_ENV = "GOVERNANCE_UPDATE_GOLDENS"

#: The Impact fields a scenario's `expected` block is scored against, paired with how the
#: observed value is obtained. `context_documents` comes from rules_for rather than Impact
#: because convention-file selection is path-driven, not component-driven.
SCORED_FIELDS = ("components", "validators", "proxy_routes", "context_documents", "findings")


def update_mode() -> bool:
    return os.environ.get(UPDATE_ENV, "") not in ("", "0")


def replay(ctx: Context, scenario: dict[str, Any]) -> dict[str, Any]:
    """Run impact analysis for a scenario and return the serialized answer."""
    change = scenario["change"]
    impact = analyse_impact(ctx, list(change["paths"]), depth=change.get("depth", 1))
    return dataclasses.asdict(impact)


def observed_values(ctx: Context, scenario: dict[str, Any], impact: dict[str, Any]) -> dict[str, list[str]]:
    """The observed counterpart of each scored `expected` field."""
    observed = {field: list(impact.get(field, [])) for field in SCORED_FIELDS}
    observed["context_documents"] = rules_for(list(scenario["change"]["paths"]))
    return observed


def score(expected: list[str], observed: list[str]) -> dict[str, float]:
    """Set precision and recall. Both are 1.0 when both sides are empty: an empty answer
    to an empty question is exactly right, not undefined."""
    expected_set, observed_set = set(expected), set(observed)
    hits = len(expected_set & observed_set)
    precision = 1.0 if not observed_set else hits / len(observed_set)
    recall = 1.0 if not expected_set else hits / len(expected_set)
    return {"precision": round(precision, 4), "recall": round(recall, 4)}


def context_tokens(ctx: Context, scenario: dict[str, Any]) -> int:
    briefing = render_context(ctx, list(scenario["change"]["paths"]), scenario["title"], token_budget=6000)
    return len(briefing) // CHARS_PER_TOKEN


def score_scenario(ctx: Context, scenario: dict[str, Any]) -> dict[str, Any]:
    impact = replay(ctx, scenario)
    observed = observed_values(ctx, scenario, impact)
    return {
        "id": scenario["id"],
        "fields": {
            field: score(scenario["expected"][field], observed[field]) for field in SCORED_FIELDS
        },
        "context_tokens": context_tokens(ctx, scenario),
        "graph_files_count": len(impact.get("graph_files", [])),
        "unassigned": list(impact.get("unassigned", [])),
    }


def golden_path(scenario_id: str, side: str) -> Path:
    return GOLDEN_DIR / f"{scenario_id}.{side}.json"


def golden_for(scenario_id: str) -> Path:
    """The golden the replay currently compares against: after when it exists, else before."""
    after = golden_path(scenario_id, "after")
    return after if after.is_file() else golden_path(scenario_id, "before")


def capture_side() -> str:
    """Which golden an update run writes: before until the graph feeds impact, after once
    it does. Detected from the Impact dataclass rather than configured, so the capture
    side can never disagree with the code under test."""
    from repo_governance.renderers.context import Impact

    return "after" if "graph_files" in {f.name for f in dataclasses.fields(Impact)} else "before"


def compare_or_update(ctx: Context, scenario: dict[str, Any]) -> tuple[str, str, Path]:
    """Return (observed_json, golden_json, golden_path); in update mode, write first."""
    observed = canonical_json(replay(ctx, scenario))
    if update_mode():
        target = golden_path(scenario["id"], capture_side())
        write_text_atomic(target, observed)
    target = golden_for(scenario["id"])
    golden = canonical_json(read_json(target)) if target.is_file() else ""
    return observed, golden, target
