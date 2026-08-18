"""Graph-aware impact analysis.

The contract under test: file-level expansion is additive and conservative. Backend seeds
gain their direct reverse importers; non-backend paths pass through untouched; a graph
failure or truncation is a note, never silence; and the whole answer stays deterministic.
"""

from __future__ import annotations

import dataclasses

import pytest

from repo_governance.config import Context
from repo_governance.renderers.context import analyse_impact, render_context


def test_analyse_impact_expands_backend_seed_with_reverse_importers(real_context: Context) -> None:
    # Since the 2026-08-11 facade, external callers import the rag package, not
    # config.py directly - its reverse importers are now the pipeline sub-modules.
    result = analyse_impact(real_context, ["backend/app/services/rag/config.py"])
    assert result.graph_files, "rag config has direct importers; the graph must surface them"
    assert "backend/app/services/rag/vectorstore.py" in result.graph_files
    assert "rag" in result.components
    assert any(note.startswith("Import graph:") for note in result.notes)


def test_analyse_impact_leaves_non_backend_paths_untouched(real_context: Context) -> None:
    result = analyse_impact(real_context, ["docker-compose.yml"])
    assert result.graph_files == []
    assert not any("Import graph" in note for note in result.notes)


def test_analyse_impact_keeps_unassigned_to_caller_paths_only(real_context: Context) -> None:
    """A graph-derived file with no owner is a discovery, not a coverage warning."""
    result = analyse_impact(real_context, ["backend/app/repositories/user.py", "docker-compose.yml"])
    assert result.unassigned == ["docker-compose.yml"]


def test_analyse_impact_notes_fallback_when_graph_unavailable(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(ctx: Context) -> None:
        raise RuntimeError("synthetic graph failure")

    monkeypatch.setattr("repo_governance.extractors.imports.get_import_graph", broken)
    result = analyse_impact(real_context, ["backend/app/repositories/user.py"])
    assert result.graph_files == []
    assert any("Import graph unavailable" in note for note in result.notes)
    assert "backend-api" in result.components, "the manifest-declared radius must survive the fallback"


def test_analyse_impact_notes_truncation_at_node_bound(real_context: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("repo_governance.renderers.context.GRAPH_EXPANSION_MAX_NODES", 1)
    result = analyse_impact(real_context, ["backend/app/services/rag/config.py"])
    assert len(result.graph_files) == 1
    assert any("truncated at 1 modules" in note for note in result.notes)


def test_analyse_impact_suppresses_expansion_for_hub_modules(real_context: Context) -> None:
    """core/config.py is imported by nearly every module; expanding it would launder the
    manifest's own answer into dozens of file entries."""
    result = analyse_impact(real_context, ["backend/app/core/config.py"])
    assert result.graph_files == []
    assert any("hub" in note and "suppressed" in note for note in result.notes)


def test_render_context_includes_reverse_importers_section(real_context: Context) -> None:
    briefing = render_context(real_context, ["backend/app/repositories/user.py"], "test", token_budget=100_000)
    assert "## Reverse importers (import graph)" in briefing


def test_render_context_drops_graph_section_under_tight_budget(real_context: Context) -> None:
    briefing = render_context(real_context, ["backend/app/repositories/user.py"], "test", token_budget=700)
    assert "## Reverse importers (import graph)" not in briefing
    assert "# Task context" in briefing


def test_impact_output_is_deterministic_across_two_runs(real_context: Context) -> None:
    paths = ["backend/app/services/rag/config.py", "backend/app/services/user.py"]
    first = dataclasses.asdict(analyse_impact(real_context, paths))
    second = dataclasses.asdict(analyse_impact(real_context, paths))
    assert first == second
