"""Graph assembly and the analyses over it: cycles, orphans, shortest paths.

Everything here derives from the extractors directly, so the committed reports are pure
functions of the sources — the determinism contract render_all requires. Node and edge
kinds are exactly the committed vocabulary; an extractor emitting an undeclared kind is a
bug the tests pin.

Orphan honesty: "no importer found" is only a finding when nothing else explains the
module. The exclusion classes are each a *reason a module legitimately has no static
importer* — a framework invokes it, a dynamic-import site makes its subtree unknowable, a
component declares it as an entrypoint, a package root names a package rather than code,
tests are discovered by the runner, ambient type declarations are compiler input. What
remains is dead weight or an unfinished feature, and the report says which modules those
are rather than hiding them in a count.
"""

from __future__ import annotations

from dataclasses import dataclass

from repo_governance.config import Context
from repo_governance.extractors.imports import (
    EDGE_IMPORTS,
    RUNTIME_KINDS,
    ImportGraph,
    get_import_graph,
)
from repo_governance.extractors.relations import (
    EDGE_ROUTES_TO,
    EDGE_TESTS,
    NODE_API_ROUTE,
    NODE_DB_MODEL,
    NODE_TASK,
    NODE_TEST_MODULE,
    NODE_TOOL,
    get_relations,
    routes_to_edges,
)
from repo_governance.extractors.ts_imports import NODE_TS_MODULE, get_ts_import_graph

NODE_PYTHON_MODULE = "python-module"

#: Next.js invokes these by filename convention; no import edge will ever point at them.
_NEXT_CONVENTION_NAMES = frozenset(
    {
        "page",
        "layout",
        "template",
        "loading",
        "error",
        "global-error",
        "not-found",
        "default",
        "route",
        "middleware",
        "instrumentation",
        "manifest",
        "robots",
        "sitemap",
        "icon",
        "apple-icon",
        "opengraph-image",
        "twitter-image",
    }
)

#: Generator features that ship surface while disabled: expected-present-unused, not
#: orphaned. Keyed by the flag in .fastapi-fullstack.json's context; values are dotted
#: module prefixes (Python) or path prefixes (TypeScript).
_FEATURE_DISABLED_PREFIXES: dict[str, tuple[str, ...]] = {
    "enable_billing": ("app.billing", "frontend/src/components/billing"),
}


@dataclass(frozen=True)
class GraphEdge:
    src: str
    dst: str
    kind: str
    line: int
    method: str  # ast | regex | join
    confidence: str  # high | medium


@dataclass
class GraphData:
    """The joined graph: every node typed, every edge carrying provenance."""

    nodes: tuple[tuple[str, str], ...]  # (id, kind)
    edges: tuple[GraphEdge, ...]


def assemble(ctx: Context) -> GraphData:
    """Join the three extraction layers into one typed graph."""
    python = get_import_graph(ctx)
    typescript = get_ts_import_graph(ctx)
    relations = get_relations(ctx)

    nodes: set[tuple[str, str]] = set()
    edges: set[GraphEdge] = set()

    for module in python.modules:
        nodes.add((module, NODE_PYTHON_MODULE))
    for edge in python.edges:
        edges.add(GraphEdge(edge.src, edge.dst, edge.kind, edge.line, "ast", "high"))

    for module in typescript.modules:
        nodes.add((module, NODE_TS_MODULE))
    for edge in typescript.edges:
        edges.add(GraphEdge(edge.src, edge.dst, edge.kind, edge.line, "regex", "high"))

    for route in relations.api_routes:
        nodes.add((f"{route.method} {route.path}", NODE_API_ROUTE))
    for task in relations.tasks:
        nodes.add((f"{task.module}.{task.name}", NODE_TASK))
    for model in relations.models:
        nodes.add((f"{model.module}.{model.name}", NODE_DB_MODEL))
    for test_file, target in relations.test_edges:
        nodes.add((test_file, NODE_TEST_MODULE))
        edges.add(GraphEdge(test_file, target, EDGE_TESTS, 0, "ast", "high"))
    for product in relations.tools:
        for name in product["tools"]:
            nodes.add((f"google:{product['kind']}:{name}", NODE_TOOL))
    for src, dst in routes_to_edges(ctx, relations):
        edges.add(GraphEdge(src, dst, EDGE_ROUTES_TO, 0, "join", "high"))

    return GraphData(
        nodes=tuple(sorted(nodes)), edges=tuple(sorted(edges, key=lambda e: (e.src, e.dst, e.kind, e.line)))
    )


def strongly_connected_components(modules: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Tarjan, iterative, deterministic: components sorted, members sorted. Only
    components larger than one module are returned — a lone module is not a cycle."""
    graph: dict[str, list[str]] = {}
    for src, dst in edges:
        if src in modules and dst in modules:
            graph.setdefault(src, []).append(dst)
    for adjacency in graph.values():
        adjacency.sort()

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for start in sorted(modules):
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, position = work.pop()
            if position == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            descended = False
            successors = graph.get(node, [])
            for i in range(position, len(successors)):
                successor = successors[i]
                if successor not in index:
                    work.append((node, i + 1))
                    work.append((successor, 0))
                    descended = True
                    break
                if successor in on_stack:
                    low[node] = min(low[node], index[successor])
            if descended:
                continue
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(components)


def _cycles_for(graph: ImportGraph) -> list[dict]:
    modules = set(graph.modules)
    runtime = [(e.src, e.dst) for e in graph.edges if e.kind in RUNTIME_KINDS]
    import_time = [(e.src, e.dst) for e in graph.edges if e.kind == EDGE_IMPORTS]
    entries = []
    for component in strongly_connected_components(modules, runtime):
        # A cycle closed only through deferred imports is a design smell; one closed at
        # module scope survives only while initialization order happens to hold — worth
        # distinguishing.
        member_set = set(component)
        crashes = bool(strongly_connected_components(member_set, import_time))
        entries.append({"modules": component, "import_time": crashes})
    return entries


def build_cycles_report(ctx: Context) -> dict:
    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": ["backend/app/", "frontend/src/"],
            "extractor_version": ctx.config.version,
        },
        "python": _cycles_for(get_import_graph(ctx)),
        "typescript": _cycles_for(get_ts_import_graph(ctx)),
    }


def _component_entrypoints(ctx: Context) -> set[str]:
    from repo_governance.io_atomic import read_json

    path = ctx.paths.effective_manifests / "repository.json"
    if not path.is_file():
        return set()
    try:
        components = read_json(path).get("components", [])
    except ValueError:
        return set()
    return {entry for component in components for entry in component.get("entrypoints", [])}


def _dynamic_subtrees(graph: ImportGraph) -> list[str]:
    """Dotted prefixes within which the graph cannot see edges: the package around each
    dynamic-import site. A module inside one is unknowable, never orphaned."""
    prefixes = set()
    for site in graph.dynamic_import_sites:
        path = graph.path_for_module(site) or ""
        package = site if path.endswith("__init__.py") else site.rsplit(".", 1)[0]
        prefixes.add(package)
    return sorted(prefixes)


def _feature_disabled(ctx: Context) -> list[str]:
    from repo_governance.io_atomic import read_json

    provenance = ctx.repo_root / ".fastapi-fullstack.json"
    if not provenance.is_file():
        return []
    try:
        context = read_json(provenance).get("context", {})
    except ValueError:
        return []
    prefixes: list[str] = []
    for flag, patterns in _FEATURE_DISABLED_PREFIXES.items():
        if context.get(flag) is False:
            prefixes.extend(patterns)
    return sorted(prefixes)


def _python_orphans(ctx: Context) -> dict:
    graph = get_import_graph(ctx)
    imported = {edge.dst for edge in graph.edges if edge.kind in RUNTIME_KINDS}
    entrypoint_files = _component_entrypoints(ctx)
    dynamic_prefixes = _dynamic_subtrees(graph)
    disabled = _feature_disabled(ctx)

    orphans: list[str] = []
    excluded = {"package-root": 0, "entrypoint": 0, "dynamic-subtree": 0, "feature-disabled": 0}
    for module, path in graph.modules.items():
        if module in imported:
            continue
        if path.endswith("__init__.py"):
            excluded["package-root"] += 1
        elif path in entrypoint_files:
            excluded["entrypoint"] += 1
        elif any(module == prefix or module.startswith(prefix + ".") for prefix in dynamic_prefixes):
            excluded["dynamic-subtree"] += 1
        elif any(module.startswith(prefix) for prefix in disabled):
            excluded["feature-disabled"] += 1
        else:
            orphans.append(module)
    return {"orphans": sorted(orphans), "excluded": excluded}


def _typescript_orphans(ctx: Context) -> dict:
    graph = get_ts_import_graph(ctx)
    imported = {edge.dst for edge in graph.edges if edge.kind in RUNTIME_KINDS}
    entrypoint_files = _component_entrypoints(ctx)
    disabled = _feature_disabled(ctx)

    orphans: list[str] = []
    excluded = {
        "framework-invoked": 0,
        "test-module": 0,
        "ambient-types": 0,
        "entrypoint": 0,
        "feature-disabled": 0,
    }
    for module in graph.modules:
        if module in imported:
            continue
        stem = module.rsplit("/", 1)[-1].removesuffix(".tsx").removesuffix(".ts")
        if module.endswith(".d.ts"):
            excluded["ambient-types"] += 1
        elif ".test" in stem or ".spec" in stem:
            excluded["test-module"] += 1
        elif stem in _NEXT_CONVENTION_NAMES:
            excluded["framework-invoked"] += 1
        elif module in entrypoint_files:
            excluded["entrypoint"] += 1
        elif any(module.startswith(prefix) for prefix in disabled):
            excluded["feature-disabled"] += 1
        else:
            orphans.append(module)
    return {"orphans": sorted(orphans), "excluded": excluded}


def build_orphans_report(ctx: Context) -> dict:
    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": [
                ".fastapi-fullstack.json",
                "backend/app/",
                "frontend/src/",
                "governance/manifests/effective/repository.json",
            ],
            "extractor_version": ctx.config.version,
        },
        "python": _python_orphans(ctx),
        "typescript": _typescript_orphans(ctx),
    }


def shortest_path(edges: list[tuple[str, str]], source: str, target: str) -> list[str] | None:
    """BFS with sorted neighbor expansion, so equal-length answers are stable."""
    if source == target:
        return [source]
    adjacency: dict[str, list[str]] = {}
    for src, dst in edges:
        adjacency.setdefault(src, []).append(dst)
    for neighbors in adjacency.values():
        neighbors.sort()

    parents: dict[str, str] = {}
    frontier = [source]
    seen = {source}
    while frontier:
        next_frontier: list[str] = []
        for node in frontier:
            for neighbor in adjacency.get(node, []):
                if neighbor in seen:
                    continue
                parents[neighbor] = node
                if neighbor == target:
                    path = [target]
                    while path[-1] != source:
                        path.append(parents[path[-1]])
                    return list(reversed(path))
                seen.add(neighbor)
                next_frontier.append(neighbor)
        frontier = next_frontier
    return None
