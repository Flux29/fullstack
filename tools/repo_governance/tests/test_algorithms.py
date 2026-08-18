"""Ranking and risk: deterministic scores, sensible structure, honest churn handling.

Synthetic graphs pin the algorithmic properties (a hub ranks above a leaf, a bridge has
the betweenness, personalization pulls rank toward the focus, ties break on id); the
real-tree tests pin the architectural facts the rankings exist to surface.
"""

from __future__ import annotations

import networkx as nx

from repo_governance.config import Context
from repo_governance.graph.algorithms import (
    betweenness,
    build_churn_report,
    build_hotspots_report,
    pagerank,
    top,
)
from repo_governance.io_atomic import canonical_json


def _diamond() -> nx.DiGraph:
    """a and b both feed hub; hub feeds leaf. hub should out-rank everything."""
    graph = nx.DiGraph()
    graph.add_edges_from([("a", "hub"), ("b", "hub"), ("hub", "leaf")])
    return graph


def test_pagerank_ranks_the_hub_above_its_feeders() -> None:
    scores = pagerank(_diamond())
    assert scores["hub"] > scores["a"] == scores["b"]
    assert abs(sum(scores.values()) - 1.0) < 0.01


def test_pagerank_personalization_pulls_rank_toward_the_focus() -> None:
    plain = pagerank(_diamond())
    focused = pagerank(_diamond(), personalization={"a": 1.0})
    assert focused["a"] > plain["a"]


def test_pagerank_handles_the_empty_and_dangling_graphs() -> None:
    assert pagerank(nx.DiGraph()) == {}
    lonely = nx.DiGraph()
    lonely.add_node("only")
    assert pagerank(lonely) == {"only": 1.0}


def test_betweenness_finds_the_bridge() -> None:
    graph = nx.DiGraph()
    graph.add_edges_from([("a", "bridge"), ("b", "bridge"), ("bridge", "c"), ("bridge", "d")])
    scores = betweenness(graph)
    assert scores["bridge"] > 0
    assert all(scores[node] == 0 for node in ("a", "b", "c", "d"))


def test_top_breaks_ties_on_id_and_drops_zeros() -> None:
    ranked = top({"b": 0.5, "a": 0.5, "z": 0.0, "c": 0.7}, n=3)
    assert ranked == [{"id": "c", "score": 0.7}, {"id": "a", "score": 0.5}, {"id": "b", "score": 0.5}]


def test_real_tree_rankings_surface_the_known_architecture(real_context: Context) -> None:
    report = build_hotspots_report(real_context)
    python_pagerank = [entry["id"] for entry in report["views"]["python"]["pagerank"][:5]]
    assert "app.core.config" in python_pagerank, "settings is the most-imported module"
    python_bridges = [entry["id"] for entry in report["views"]["python"]["betweenness"][:5]]
    assert "app.api.deps" in python_bridges, "the DI seam is the bridge between routes and everything"


def test_real_tree_risk_join_ranks_covered_gaps_by_centrality(real_context: Context) -> None:
    report = build_hotspots_report(real_context)
    assert report["risk"], "the tests manifest records known coverage gaps"
    centralities = [entry["max_module_centrality"] for entry in report["risk"]]
    assert centralities == sorted(centralities, reverse=True)
    top_entry = report["risk"][0]
    assert top_entry["component"] == "rag", "the highest-centrality component with a gap"


def test_real_tree_hotspots_report_is_deterministic(real_context: Context) -> None:
    assert canonical_json(build_hotspots_report(real_context)) == canonical_json(build_hotspots_report(real_context))


def test_churn_report_is_cache_material_sorted_by_hotspot(real_context: Context) -> None:
    report = build_churn_report(real_context)
    assert report["status"] == "ok"
    hotspots = report["hotspots"]
    assert hotspots, "500 commits of history touch central modules"
    scores = [entry["hotspot"] for entry in hotspots]
    assert scores == sorted(scores, reverse=True)


def test_churn_without_git_history_is_unknown(minimal_repo) -> None:
    report = build_churn_report(Context.discover(minimal_repo))
    assert report == {"status": "unknown", "reason": "git history unavailable"}
