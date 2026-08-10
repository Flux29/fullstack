"""Bounded context, impact, and component briefings.

All three answer questions from the manifests rather than from the repository, which is what
keeps them cheap and bounded. The blueprint's Milestone 1 accepts conservative — that is,
over-inclusive — answers here: a blast radius that is slightly too large costs a few extra
validator runs, while one that is too small costs a broken deployment.

Impact deliberately follows the proxy hop. Every REST call from the browser goes through a
Next.js route handler, so an analysis that stops at the FastAPI route reports a change as
backend-only when it also breaks the page that calls it.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from repo_governance.config import Context
from repo_governance.io_atomic import read_json

#: Rough token estimate. Four characters per token is close enough to keep a briefing under
#: budget without pulling in a tokenizer.
CHARS_PER_TOKEN = 4

#: Bounds for the import-graph seed expansion. Depth 1 — direct importers — is the review
#: surface: those files hold an import that the change can break. Scenario replay showed
#: depth 2 routing through hub modules (core/config.py imports rag.config, and everything
#: imports core.config) and truncating at the node cap, which is over-selection, not
#: insight; transitive reach at component granularity is the declared-dependency BFS's
#: job. Truncation at the node cap is reported as a note, never silent.
GRAPH_EXPANSION_DEPTH = 1
GRAPH_EXPANSION_MAX_NODES = 50

#: A seed whose direct importers exceed this is a hub (core/config.py has ~48 — nearly
#: every module reads settings). Expanding a hub is over-selection, not insight: scenario
#: replay showed the env-var-rename scenario's components precision collapsing from 0.2 to
#: 0.07 because "everything imports config" laundered the manifest's own answer into 48
#: file entries. A hub is reported as a note; its manifest-declared radius stands.
GRAPH_HUB_IMPORTER_LIMIT = 25

#: Which convention file applies to which paths. Context returns references to these rather
#: than restating them — they already implement progressive disclosure.
RULE_SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".claude/rules/architecture.md", ("backend/app/services/**", "backend/app/repositories/**", "backend/app/api/**")),
    (".claude/rules/api-conventions.md", ("backend/app/api/**",)),
    (".claude/rules/schemas-models.md", ("backend/app/schemas/**", "backend/app/db/models/**")),
    (".claude/rules/exceptions-security.md", ("backend/app/core/**", "backend/app/api/**")),
    (".claude/rules/code-style.md", ("backend/**",)),
    (".claude/rules/testing.md", ("backend/tests/**",)),
    (".claude/rules/frontend.md", ("frontend/**",)),
)


@dataclass
class Impact:
    seeds: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    configuration: list[str] = field(default_factory=list)
    proxy_routes: list[str] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unassigned: list[str] = field(default_factory=list)
    #: Files that import the changed backend modules, from the AST import graph. The
    #: file-level blast radius the component globs cannot express.
    graph_files: list[str] = field(default_factory=list)


def _expand_with_import_graph(ctx: Context, paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Expand backend Python seeds with their reverse importers.

    Returns ``(expanded_paths, graph_files, notes)``. Conservative by contract: when no
    input path maps to a graph module the paths come back unchanged, and when the graph
    cannot be built the failure is a note and the manifest-declared radius stands —
    unknowns are reported, never treated as an empty radius.
    """
    try:
        from repo_governance.extractors.imports import get_import_graph

        graph = get_import_graph(ctx)
    except Exception as error:  # noqa: BLE001 - the fallback IS the contract here
        return paths, [], [f"Import graph unavailable ({error}); falling back to manifest-declared dependencies."]

    seeds = {module for path in paths if (module := graph.module_for_path(path)) is not None}
    if not seeds:
        return paths, [], []

    notes: list[str] = []
    hubs = {
        module: count
        for module in sorted(seeds)
        if (count := len(graph.importers_of(module))) > GRAPH_HUB_IMPORTER_LIMIT
    }
    for module, count in hubs.items():
        notes.append(
            f"{module} is a hub with {count} direct importers; file-level expansion is "
            "suppressed for it because the radius would be the whole application - the "
            "manifest-declared blast radius stands."
        )

    graph_files: list[str] = []
    expansion_seeds = seeds - set(hubs)
    if expansion_seeds:
        importers, truncated = graph.reverse_closure(
            expansion_seeds, max_depth=GRAPH_EXPANSION_DEPTH, max_nodes=GRAPH_EXPANSION_MAX_NODES
        )
        graph_files = sorted(
            {file for module in importers if (file := graph.path_for_module(module)) is not None}
        )
        notes.append(
            f"Import graph: {len(graph_files)} module(s) import the changed backend modules "
            f"(depth <= {GRAPH_EXPANSION_DEPTH})."
        )
        if truncated:
            notes.append(
                f"Reverse import closure truncated at {GRAPH_EXPANSION_MAX_NODES} modules; "
                "the radius beyond the bound is unknown."
            )
    dynamic = sorted(set(graph.dynamic_import_sites) & seeds)
    if dynamic:
        notes.append(
            f"Changed module(s) {', '.join(dynamic)} use dynamic imports; their true "
            "dependency set is wider than the graph can see."
        )
    return sorted({*paths, *graph_files}), graph_files, notes


def _components(ctx: Context) -> list[dict[str, Any]]:
    path = ctx.paths.effective_repository
    if not path.is_file():
        return []
    return read_json(path).get("components", [])


def owner_of(components: list[dict[str, Any]], path: str) -> str | None:
    """Longest matching `owns` pattern wins, so a subsystem beats the root containing it."""
    best: tuple[int, str] | None = None
    for component in components:
        for pattern in component.get("owns", []):
            if fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*")):
                if best is None or len(pattern) > best[0]:
                    best = (len(pattern), component["id"])
    return best[1] if best else None


def analyse_impact(ctx: Context, paths: list[str], depth: int = 1) -> Impact:
    components = _components(ctx)
    by_id = {component["id"]: component for component in components}
    result = Impact()

    # File-level expansion runs before component mapping, so a service-only change whose
    # importers include route files reaches backend-api (and from there the proxy join)
    # even though no component declares that edge.
    expanded, graph_files, graph_notes = _expand_with_import_graph(ctx, paths)
    result.graph_files = graph_files
    result.notes.extend(graph_notes)

    seeds: set[str] = set()
    for path in expanded:
        owner = owner_of(components, path)
        if owner:
            seeds.add(owner)
    # Only the caller's own paths raise the coverage warning; a graph-derived file with no
    # owner is a discovery, not a hole in what the caller asked about.
    result.unassigned = [path for path in paths if owner_of(components, path) is None]
    result.seeds = sorted(seeds)

    # Reverse dependency closure: who depends on the seeds, transitively to `depth`.
    reached = set(seeds)
    frontier = set(seeds)
    for _ in range(max(depth, 0)):
        nextwave = {
            component["id"]
            for component in components
            if set(component.get("allowed_dependencies", [])) & frontier
        }
        frontier = nextwave - reached
        reached |= nextwave
        if not frontier:
            break

    configuration = {
        name
        for component_id in reached
        for name in by_id.get(component_id, {}).get("configuration_refs", [])
    }
    # A variable shared with another component pulls that component in too: changing a
    # variable's meaning affects everything that reads it.
    for component in components:
        if set(component.get("configuration_refs", [])) & configuration:
            reached.add(component["id"])

    interfaces_path = ctx.paths.generated_manifests / "interfaces.json"
    if interfaces_path.is_file():
        interfaces = read_json(interfaces_path)
        public = {
            interface
            for component_id in reached
            for interface in by_id.get(component_id, {}).get("public_interfaces", [])
        }
        for route in interfaces.get("proxy_routes", []):
            for target in route.get("backend_targets", []):
                template = target.get("path_template", "")
                if any(template and template in interface for interface in public) or any(
                    path.startswith(route["file"].rsplit("/", 1)[0]) for path in expanded
                ):
                    result.proxy_routes.append(route["file"])
                    owner = owner_of(components, route["file"])
                    if owner:
                        reached.add(owner)

        websocket = interfaces.get("websocket", {})
        if any("ws/agent" in str(interface) for interface in public):
            result.notes.append(
                "The chat WebSocket bypasses the proxy layer; see the "
                f"{websocket.get('proxy_exception_ref')} exception. Its consumer is chat-frontend."
            )
            reached.add("chat-frontend")

    result.components = sorted(reached)
    result.configuration = sorted(configuration)
    result.proxy_routes = sorted(set(result.proxy_routes))
    result.contracts = sorted(
        {
            interface
            for component_id in reached
            for interface in by_id.get(component_id, {}).get("public_interfaces", [])
        }
    )
    result.validators = sorted(
        {
            validator
            for component_id in reached
            for validator in by_id.get(component_id, {}).get("validation", [])
        }
    )
    result.decisions = sorted(
        {ref for component_id in reached for ref in by_id.get(component_id, {}).get("decision_refs", [])}
    )
    result.findings = sorted(
        {ref for component_id in reached for ref in by_id.get(component_id, {}).get("finding_refs", [])}
    )

    if any(path.startswith("docker-compose") or path == "Makefile" for path in paths):
        result.validators = sorted({*result.validators, "compose-check"})

    return result


def rules_for(paths: list[str]) -> list[str]:
    """Convention files that apply, as references. Their content is never restated here."""
    applicable = []
    for rule_file, scopes in RULE_SCOPES:
        if any(fnmatch.fnmatch(path, scope) or path.startswith(scope.rstrip("*")) for path in paths for scope in scopes):
            applicable.append(rule_file)
    return applicable


def render_context(ctx: Context, paths: list[str], task: str | None, token_budget: int) -> str:
    """Assemble a task briefing, trimmed to fit the budget.

    Trim order is deliberate: invariants and contracts survive longest because they are what
    a change can break silently, while history is dropped first because it is the easiest
    thing to go and look up.
    """
    components = _components(ctx)
    by_id = {component["id"]: component for component in components}
    impact = analyse_impact(ctx, paths)

    sections: list[tuple[str, str]] = []

    header = ["# Task context\n"]
    if task:
        header.append(f"\n**Task:** {task}\n")
    header.append(f"\n**Paths:** {', '.join(paths)}\n")
    if impact.unassigned:
        header.append(
            f"\n**Unassigned paths:** {', '.join(impact.unassigned)} — no component owns these, "
            "so the blast radius below may be incomplete.\n"
        )
    sections.append(("header", "".join(header)))

    invariants = ["\n## Invariants you must not break\n\n"]
    for component_id in impact.components:
        component = by_id.get(component_id, {})
        for invariant in component.get("invariants", []):
            invariants.append(f"- **{component_id}** — {invariant}\n")
    if len(invariants) > 1:
        sections.append(("invariants", "".join(invariants)))

    if impact.contracts:
        sections.append(
            (
                "contracts",
                "\n## Public interfaces in scope\n\n" + "".join(f"- {item}\n" for item in impact.contracts),
            )
        )

    if impact.proxy_routes:
        sections.append(
            (
                "proxy",
                "\n## Proxy handlers in the chain\n\n"
                "Every REST call from the browser passes through these. An API change is not "
                "complete until they are updated too.\n\n"
                + "".join(f"- `{item}`\n" for item in impact.proxy_routes),
            )
        )

    if impact.notes:
        sections.append(("notes", "\n## Notes\n\n" + "".join(f"- {note}\n" for note in impact.notes)))

    if impact.configuration:
        sections.append(
            (
                "configuration",
                "\n## Configuration referenced\n\n"
                + ", ".join(f"`{name}`" for name in impact.configuration)
                + "\n",
            )
        )

    if impact.findings:
        sections.append(
            (
                "findings",
                "\n## Open findings constraining this area\n\n"
                + "".join(f"- `{item}` — see governance/Summary.md\n" for item in impact.findings),
            )
        )

    if impact.validators:
        sections.append(
            (
                "validators",
                "\n## Validators to run\n\n"
                + "".join(f"- `{item}`\n" for item in impact.validators)
                + "\nResolve each through `governance/validators.json`.\n",
            )
        )

    rules = rules_for(paths)
    if rules:
        sections.append(
            (
                "rules",
                "\n## Conventions that apply\n\n"
                + "".join(f"- `{item}`\n" for item in rules)
                + "\nRead these files; their content is deliberately not repeated here.\n",
            )
        )

    if impact.decisions:
        sections.append(
            (
                "decisions",
                "\n## Decisions\n\n" + "".join(f"- {item}\n" for item in impact.decisions),
            )
        )

    history = _related_history(ctx, impact.components)
    if history:
        sections.append(("history", "\n## Recent related changes\n\n" + history))

    if impact.graph_files:
        sections.append(
            (
                "graph",
                "\n## Reverse importers (import graph)\n\n"
                "Files that import what you are changing; the file-level review surface.\n\n"
                + "".join(f"- `{item}`\n" for item in impact.graph_files),
            )
        )

    # Trim from the least load-bearing end until the budget is met. The graph section goes
    # second: cheap to re-derive with `governance impact`, unlike invariants and contracts.
    priority = ["history", "graph", "decisions", "rules", "findings", "configuration", "notes", "proxy", "contracts"]
    budget = token_budget * CHARS_PER_TOKEN
    while sum(len(text) for _, text in sections) > budget and priority:
        drop = priority.pop(0)
        sections = [(name, text) for name, text in sections if name != drop]

    return "".join(text for _, text in sections)


def _related_history(ctx: Context, components: list[str], limit: int = 3) -> str:
    from repo_governance.config import iter_files

    wanted = set(components)
    matches = []
    for path in iter_files(ctx.paths.changes, suffixes=(".json",)):
        try:
            record = read_json(path)
        except ValueError:
            continue
        if wanted & set(record.get("affected_components", [])):
            matches.append(record)

    matches.sort(key=lambda item: item.get("date", ""), reverse=True)
    return "".join(
        f"- **{record['date']}** {record['summary']} (`{record['id']}`)\n" for record in matches[:limit]
    )


def render_explain(ctx: Context, component_id: str) -> str:
    components = _components(ctx)
    component = next((item for item in components if item["id"] == component_id), None)
    if component is None:
        known = ", ".join(sorted(item["id"] for item in components))
        return f"No component {component_id!r}. Known components: {known}\n"

    parts = [f"# {component['id']}\n\n{component['purpose']}\n"]
    parts.append(f"\n- Kind: {component['kind']}\n")
    parts.append(f"- Declared in: {component['declared_in']}")
    if component.get("annotation_path"):
        parts.append(f" (`{component['annotation_path']}`)")
    parts.append("\n")

    for label, key in (
        ("Owns", "owns"),
        ("Entry points", "entrypoints"),
        ("Public interfaces", "public_interfaces"),
        ("Invariants", "invariants"),
        ("Allowed dependencies", "allowed_dependencies"),
        ("Forbidden dependencies", "forbidden_dependencies"),
        ("Configuration", "configuration_refs"),
        ("Compose services", "compose_services"),
        ("Validators", "validation"),
        ("Decisions", "decision_refs"),
        ("Open findings", "finding_refs"),
        ("Exceptions", "exceptions"),
    ):
        values = component.get(key) or []
        if values:
            parts.append(f"\n## {label}\n\n" + "".join(f"- {value}\n" for value in values))

    return "".join(parts)
