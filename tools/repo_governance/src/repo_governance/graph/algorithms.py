"""Ranking and risk analysis over the joined graph. Advisory by design.

The blueprint's warning is the design constraint here: PageRank is a relevance and
dependency-centrality signal, never a quality score or a merge decision, and it is
computed **within typed views** — an undifferentiated graph lets generic utilities
dominate misleadingly. Determinism is engineered, not assumed: graphs are built in
sorted node order, iteration counts and tolerances are fixed, scores are rounded, and
ties break on node id.

What is committed versus cached follows one rule: a committed report must be a pure
function of the sources. Centrality and invariant coverage are; **churn is not** — every
commit shifts the counts, so a committed churn report would drift the moment it landed.
Churn × centrality therefore lives in the scan-built cache.
"""

from __future__ import annotations

import networkx as nx

from repo_governance.config import Context
from repo_governance.extractors.imports import RUNTIME_KINDS, get_import_graph
from repo_governance.extractors.ts_imports import get_ts_import_graph
from repo_governance.io_atomic import read_json

PAGERANK_ALPHA = 0.85
PAGERANK_MAX_ITER = 200
PAGERANK_TOL = 1e-10
SCORE_DECIMALS = 4
TOP_N = 20


def _view(nodes: list[str], edges: list[tuple[str, str]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(nodes))
    graph.add_edges_from(sorted(edges))
    return graph


def python_view(ctx: Context) -> nx.DiGraph:
    imports = get_import_graph(ctx)
    return _view(
        list(imports.modules),
        [(e.src, e.dst) for e in imports.edges if e.kind in RUNTIME_KINDS],
    )


def typescript_view(ctx: Context) -> nx.DiGraph:
    imports = get_ts_import_graph(ctx)
    return _view(
        list(imports.modules),
        [(e.src, e.dst) for e in imports.edges if e.kind in RUNTIME_KINDS],
    )


def pagerank(view: nx.DiGraph, *, personalization: dict[str, float] | None = None) -> dict[str, float]:
    """Rounded PageRank by power iteration, implemented here rather than via
    ``nx.pagerank`` because networkx 3.x delegates that to scipy — a numeric stack this
    tool does not otherwise need. Sorted node order, fixed iteration ceiling, and
    rounding keep the answer byte-stable. Pass ``personalization`` (node -> weight) to
    rank relative to a task or subsystem — what the visualizers focus with."""
    nodes = sorted(view.nodes)
    if not nodes:
        return {}
    count = len(nodes)
    if personalization:
        total = sum(personalization.values()) or 1.0
        base = {node: personalization.get(node, 0.0) / total for node in nodes}
    else:
        base = dict.fromkeys(nodes, 1.0 / count)

    successors = {node: sorted(view.successors(node)) for node in nodes}
    rank = dict(base)
    for _ in range(PAGERANK_MAX_ITER):
        previous = rank
        rank = {node: (1.0 - PAGERANK_ALPHA) * base[node] for node in nodes}
        dangling = 0.0
        for node in nodes:
            targets = successors[node]
            if targets:
                share = PAGERANK_ALPHA * previous[node] / len(targets)
                for target in targets:
                    rank[target] += share
            else:
                dangling += PAGERANK_ALPHA * previous[node]
        if dangling:
            for node in nodes:
                rank[node] += dangling * base[node]
        if sum(abs(rank[node] - previous[node]) for node in nodes) < count * PAGERANK_TOL:
            break
    return {node: round(score, SCORE_DECIMALS) for node, score in rank.items()}


def betweenness(view: nx.DiGraph) -> dict[str, float]:
    """Exact (unsampled) betweenness, so the answer is deterministic."""
    if not view:
        return {}
    scores = nx.betweenness_centrality(view, normalized=True)
    return {node: round(score, SCORE_DECIMALS) for node, score in scores.items()}


def top(scores: dict[str, float], n: int = TOP_N) -> list[dict]:
    """Highest n, zeros dropped, ties broken by node id so the order never wobbles."""
    ranked = sorted(
        ((node, score) for node, score in scores.items() if score > 0),
        key=lambda item: (-item[1], item[0]),
    )
    return [{"id": node, "score": score} for node, score in ranked[:n]]


def _component_risk(ctx: Context, python_rank: dict[str, float]) -> list[dict]:
    """The centrality × weak-invariant-coverage join: components whose declared
    invariants lack a dedicated test (or carry a recorded gap), ranked by how central
    their most central module is. High rank + weak coverage = the code a regression
    hurts most where a test is least likely to catch it."""
    from repo_governance.renderers.context import owner_of

    tests_path = ctx.paths.curated_manifests / "tests.json"
    effective_path = ctx.paths.effective_repository
    if not tests_path.is_file() or not effective_path.is_file():
        return []
    coverage = read_json(tests_path).get("invariant_coverage", [])
    components = read_json(effective_path).get("components", [])

    imports = get_import_graph(ctx)
    central_by_component: dict[str, float] = {}
    for module, score in python_rank.items():
        path = imports.path_for_module(module)
        owner = owner_of(components, path) if path else None
        if owner and score > central_by_component.get(owner, 0.0):
            central_by_component[owner] = score

    by_component: dict[str, dict] = {}
    for entry in coverage:
        component = entry.get("component", "")
        record = by_component.setdefault(
            component, {"component": component, "invariants": 0, "uncovered": [], "gaps": []}
        )
        record["invariants"] += 1
        if not entry.get("covered_by"):
            record["uncovered"].append(entry["invariant"])
        elif entry.get("gap"):
            record["gaps"].append(entry["invariant"])

    risk = []
    for record in by_component.values():
        if not record["uncovered"] and not record["gaps"]:
            continue
        record["max_module_centrality"] = central_by_component.get(record["component"], 0.0)
        record["uncovered"].sort()
        record["gaps"].sort()
        risk.append(record)
    return sorted(risk, key=lambda item: (-item["max_module_centrality"], item["component"]))


def build_hotspots_report(ctx: Context) -> dict:
    """The committed ranking report: structural centrality per view, plus the
    invariant-coverage risk join. Stable inputs only — see the module docstring for why
    churn is excluded."""
    python_rank = pagerank(python_view(ctx))
    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": [
                "backend/app/",
                "frontend/src/",
                "governance/manifests/effective/repository.json",
                "governance/manifests/curated/tests.json",
            ],
            "extractor_version": ctx.config.version,
        },
        "views": {
            "python": {
                "pagerank": top(python_rank),
                "betweenness": top(betweenness(python_view(ctx))),
            },
            "typescript": {
                "pagerank": top(pagerank(typescript_view(ctx))),
                "betweenness": top(betweenness(typescript_view(ctx))),
            },
        },
        "risk": _component_risk(ctx, python_rank),
    }


def build_churn_report(ctx: Context) -> dict:
    """Churn × centrality, cache-only: maintenance hotspots over the recent history
    window. Unknown churn (no git) is reported as unknown, never as zero."""
    from repo_governance.gitutil import churn_counts

    counts = churn_counts(ctx.repo_root)
    if counts is None:
        return {"status": "unknown", "reason": "git history unavailable"}

    entries = []
    for view_name, view_builder, to_path in (
        ("python", python_view, lambda ctx, m: get_import_graph(ctx).path_for_module(m)),
        ("typescript", typescript_view, lambda ctx, m: m),
    ):
        rank = pagerank(view_builder(ctx))
        for module, score in rank.items():
            path = to_path(ctx, module)
            churn = counts.get(path or "", 0)
            if churn and score > 0:
                entries.append(
                    {
                        "id": module,
                        "view": view_name,
                        "churn": churn,
                        "centrality": score,
                        "hotspot": round(churn * score, SCORE_DECIMALS),
                    }
                )
    entries.sort(key=lambda item: (-item["hotspot"], item["id"]))
    return {"status": "ok", "window_commits": 500, "hotspots": entries[:50]}
