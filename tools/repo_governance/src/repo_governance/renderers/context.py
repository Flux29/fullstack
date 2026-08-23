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
    #: Files that import the changed modules, from the import graphs of both languages.
    #: The file-level blast radius the component globs cannot express.
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
        graph_files = sorted({file for module in importers if (file := graph.path_for_module(module)) is not None})
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


def _expand_with_site_chain(ctx: Context, paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Walk touched frontend code to the backend surface it calls through the proxy.

    Chain: touched modules (plus what they import, two waves — the page's client, the
    client's shared pieces) → the `/api/` paths that code calls → the proxy handlers whose
    route matches → their backend path templates → the FastAPI route modules those name.
    Returns ``(extra_paths, ts_files, notes)`` where extra_paths seed the component and
    Python expansions and ts_files are the frontend review surface (reverse importers).

    Same conservative contract as the Python expansion: no frontend seeds or a missing
    manifest means the caller's radius stands unchanged, and hubs are noted, not expanded.
    """
    try:
        from repo_governance.extractors.imports import RUNTIME_KINDS
        from repo_governance.extractors.ts_imports import (
            called_api_paths,
            get_ts_import_graph,
            normalize_template,
        )

        graph = get_ts_import_graph(ctx)
    except Exception as error:  # noqa: BLE001 - the fallback IS the contract here
        return [], [], [f"TypeScript graph unavailable ({error}); the frontend chain is not followed."]

    seeds = {module for path in paths if (module := graph.module_for_path(path)) is not None}
    if not seeds:
        return [], [], []

    notes: list[str] = []
    hubs = {
        module: count
        for module in sorted(seeds)
        if (count := len(graph.importers_of(module))) > GRAPH_HUB_IMPORTER_LIMIT
    }
    for module, count in hubs.items():
        notes.append(
            f"{module} is a hub with {count} direct importers; file-level expansion is "
            "suppressed for it - the manifest-declared blast radius stands."
        )

    ts_files: list[str] = []
    expansion_seeds = seeds - set(hubs)
    if expansion_seeds:
        importers, truncated = graph.reverse_closure(
            expansion_seeds, max_depth=GRAPH_EXPANSION_DEPTH, max_nodes=GRAPH_EXPANSION_MAX_NODES
        )
        ts_files = sorted(importers)
        notes.append(f"TypeScript graph: {len(ts_files)} module(s) import the changed frontend modules.")
        if truncated:
            notes.append(
                f"Frontend reverse closure truncated at {GRAPH_EXPANSION_MAX_NODES} modules; "
                "the radius beyond the bound is unknown."
            )

    # The touched code and what it calls through, two import waves deep — the page's API
    # client, then the client's shared pieces. Reverse importers are deliberately not
    # sources of called paths: they break when the seed changes, but their *other* calls
    # are not part of this change's chain.
    calling = set(seeds)
    frontier = set(seeds)
    for _ in range(2):
        frontier = {edge.dst for edge in graph.edges if edge.src in frontier and edge.kind in RUNTIME_KINDS} - calling
        calling |= frontier

    called = called_api_paths(ctx, sorted(calling))
    if not called:
        return [], ts_files, notes

    interfaces_path = ctx.paths.generated_manifests / "interfaces.json"
    if not interfaces_path.is_file():
        notes.append("interfaces.json missing; called API paths could not be joined to the proxy layer.")
        return [], ts_files, notes

    called_normalized = {normalize_template(path) for path in called}
    handler_files: set[str] = set()
    backend_templates: set[str] = set()
    for route in read_json(interfaces_path).get("proxy_routes", []):
        if normalize_template(route.get("frontend_path", "")) in called_normalized:
            handler_files.add(route["file"])
            for target in route.get("backend_targets", []):
                backend_templates.add(normalize_template(target.get("path_template", "")))

    route_module_paths: set[str] = set()
    if backend_templates:
        try:
            from repo_governance.extractors.imports import get_import_graph
            from repo_governance.extractors.relations import get_relations

            python = get_import_graph(ctx)
            for api_route in get_relations(ctx).api_routes:
                if normalize_template(api_route.path) in backend_templates:
                    module_path = python.path_for_module(api_route.module)
                    if module_path:
                        route_module_paths.add(module_path)
        except Exception as error:  # noqa: BLE001 - same fallback contract
            notes.append(f"Relation extraction unavailable ({error}); the chain stops at the proxy handlers.")

    if handler_files:
        notes.append(
            f"Site chain: {len(called)} called API path(s) -> {len(handler_files)} proxy "
            f"handler(s) -> {len(route_module_paths)} backend route module(s)."
        )
    return sorted({*ts_files, *handler_files, *route_module_paths}), ts_files, notes


def _components(ctx: Context) -> list[dict[str, Any]]:
    path = ctx.paths.effective_repository
    if not path.is_file():
        return []
    return read_json(path).get("components", [])


def _decisions(ctx: Context) -> dict[str, dict[str, Any]]:
    """The ADR index keyed by id, or empty when it has not been generated yet."""
    path = ctx.paths.decision_index
    if not path.is_file():
        return {}
    return {entry["id"]: entry for entry in read_json(path).get("decisions", []) if "id" in entry}


def _decisions_matching_paths(decisions: dict[str, dict[str, Any]], paths: list[str]) -> set[str]:
    """ADRs whose `related_paths` globs cover any of `paths`.

    The component route reaches a decision only when some component both owns the changed
    file and is named by the ADR. A decision about a cross-cutting concern — a file pattern
    that no single component owns — had no way to reach a briefing before this.
    """
    from repo_governance.merge import BINDING_DECISION_STATUSES

    matched: set[str] = set()
    for decision in decisions.values():
        if decision.get("status") not in BINDING_DECISION_STATUSES:
            continue
        for pattern in decision.get("related_paths", []):
            if any(fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*")) for path in paths):
                matched.add(decision["id"])
                break
    return matched


def owner_of(components: list[dict[str, Any]], path: str) -> str | None:
    """Longest matching `owns` pattern wins, so a subsystem beats the root containing it."""
    best: tuple[int, str] | None = None
    for component in components:
        for pattern in component.get("owns", []):
            matches = fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*"))
            if matches and (best is None or len(pattern) > best[0]):
                best = (len(pattern), component["id"])
    return best[1] if best else None


def analyse_impact(ctx: Context, paths: list[str], depth: int = 1) -> Impact:
    components = _components(ctx)
    by_id = {component["id"]: component for component in components}
    result = Impact()

    # File-level expansion runs before component mapping, so a service-only change whose
    # importers include route files reaches backend-api (and from there the proxy join)
    # even though no component declares that edge. The site chain does the same in the
    # other direction: a frontend change reaches the backend route modules it calls
    # through the proxy, so their components and validators enter the radius.
    expanded, graph_files, graph_notes = _expand_with_import_graph(ctx, paths)
    site_extra, ts_files, site_notes = _expand_with_site_chain(ctx, paths)
    if site_extra:
        expanded = sorted({*expanded, *site_extra})
    result.graph_files = sorted({*graph_files, *ts_files})
    result.notes.extend(graph_notes)
    result.notes.extend(site_notes)

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
            component["id"] for component in components if set(component.get("allowed_dependencies", [])) & frontier
        }
        frontier = nextwave - reached
        reached |= nextwave
        if not frontier:
            break

    configuration = {
        name for component_id in reached for name in by_id.get(component_id, {}).get("configuration_refs", [])
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
        {validator for component_id in reached for validator in by_id.get(component_id, {}).get("validation", [])}
    )
    # Two routes to a decision: the component that owns the changed file is named by the
    # ADR, or the ADR's own related_paths globs cover the file directly. The second exists
    # because a cross-cutting decision belongs to no single component.
    decisions = _decisions(ctx)
    result.decisions = sorted(
        {ref for component_id in reached for ref in by_id.get(component_id, {}).get("decision_refs", [])}
        | _decisions_matching_paths(decisions, sorted({*paths, *result.graph_files}))
    )
    result.findings = sorted(
        {ref for component_id in reached for ref in by_id.get(component_id, {}).get("finding_refs", [])}
    )

    if any(path.startswith("docker-compose") or path == "Makefile" for path in paths):
        result.validators = sorted({*result.validators, "compose-check"})

    # The envelope runs the full check on every governed change; an impact answer that
    # omits it claims a validator holiday that does not exist.
    result.validators = sorted({*result.validators, "governance-check"})

    return result


def rules_for(paths: list[str]) -> list[str]:
    """Convention files that apply, as references. Their content is never restated here."""
    applicable = []
    for rule_file, scopes in RULE_SCOPES:
        if any(
            fnmatch.fnmatch(path, scope) or path.startswith(scope.rstrip("*")) for path in paths for scope in scopes
        ):
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
                "complete until they are updated too.\n\n" + "".join(f"- `{item}`\n" for item in impact.proxy_routes),
            )
        )

    if impact.notes:
        sections.append(("notes", "\n## Notes\n\n" + "".join(f"- {note}\n" for note in impact.notes)))

    if impact.configuration:
        sections.append(
            (
                "configuration",
                "\n## Configuration referenced\n\n" + ", ".join(f"`{name}`" for name in impact.configuration) + "\n",
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
        index = _decisions(ctx)
        lines = []
        for item in impact.decisions:
            entry = index.get(item)
            if entry is None:
                lines.append(f"- **{item}** — not in the decision index; run `make governance-sync`\n")
                continue
            lines.append(f"- **{item}** — {entry['title']} ({entry['status']}) — `{entry['file']}`\n")
        sections.append(
            (
                "decisions",
                "\n## Decisions\n\nProposed decisions are listed too: they constrain work in flight.\n\n"
                + "".join(lines),
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
    # Decisions now survive longer than conventions: a rule file is one `Read` away and says
    # so, while a decision names a constraint whose reasoning exists nowhere in the code.
    priority = ["history", "graph", "rules", "decisions", "findings", "configuration", "notes", "proxy", "contracts"]
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
    return "".join(f"- **{record['date']}** {record['summary']} (`{record['id']}`)\n" for record in matches[:limit])


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
