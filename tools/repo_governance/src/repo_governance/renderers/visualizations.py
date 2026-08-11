"""Bounded view rendering: self-contained HTML from graph slices.

Every page is generated from the small subgraph a view definition bounds, never from the
whole graph — the blueprint's rule, and also what keeps these readable. Output is fully
self-contained: inline CSS, no scripts, no external requests, so a page opened from
``artifacts/governance/`` works offline and leaks nothing. Truncation is drawn on the
page itself; a silently bounded view reads as a complete one.

The visual language is deliberately plain: layered columns of labeled boxes with curved
connectors, plus the same edges as text underneath. The picture is for orientation; the
text is for grepping and for screen readers.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from repo_governance.config import Context
from repo_governance.io_atomic import read_json

BOX_WIDTH = 250
BOX_HEIGHT = 26
COLUMN_GAP = 90
ROW_GAP = 8
LABEL_CHARS = 34


@dataclass
class ViewData:
    title: str
    subtitle: str
    #: Ordered columns: (column label, [node ids]).
    layers: list[tuple[str, list[str]]] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ViewError(Exception):
    """A view could not be built: unknown id, missing focus, missing source."""


def load_definitions(ctx: Context) -> dict:
    path = ctx.repo_root / "governance" / "views" / "definitions.json"
    if not path.is_file():
        raise ViewError("governance/views/definitions.json does not exist")
    return {view["id"]: view for view in read_json(path).get("views", [])}


def _trim(data: ViewData, budget: int) -> ViewData:
    total = sum(len(nodes) for _, nodes in data.layers)
    if total <= budget:
        return data
    kept: set[str] = set()
    trimmed_layers: list[tuple[str, list[str]]] = []
    remaining = budget
    for label, nodes in data.layers:
        share = max(1, remaining * len(nodes) // max(total, 1)) if remaining > 0 else 0
        keep = nodes[:share]
        remaining -= len(keep)
        kept.update(keep)
        trimmed_layers.append((label, keep))
    data.layers = trimmed_layers
    data.edges = [(src, dst) for src, dst in data.edges if src in kept and dst in kept]
    data.notes.append(
        f"Truncated to {budget} of {total} nodes; the slice beyond the budget is not drawn. "
        "Narrow with --focus for the full neighborhood."
    )
    return data


def _label(node: str) -> str:
    return node if len(node) <= LABEL_CHARS else "…" + node[-(LABEL_CHARS - 1) :]


def render_html(data: ViewData) -> str:
    """The self-contained page. No scripts, no external references."""
    positions: dict[str, tuple[int, int]] = {}
    column_height = max((len(nodes) for _, nodes in data.layers), default=0)
    width = max(1, len(data.layers)) * (BOX_WIDTH + COLUMN_GAP) - COLUMN_GAP + 20
    height = max(1, column_height) * (BOX_HEIGHT + ROW_GAP) + 40

    svg: list[str] = []
    for column, (label, nodes) in enumerate(data.layers):
        x = 10 + column * (BOX_WIDTH + COLUMN_GAP)
        svg.append(
            f'<text x="{x}" y="16" class="col">{html.escape(label)}</text>'
        )
        for row, node in enumerate(nodes):
            y = 28 + row * (BOX_HEIGHT + ROW_GAP)
            positions[node] = (x, y)
            svg.append(
                f'<g><rect x="{x}" y="{y}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" rx="4"/>'
                f'<text x="{x + 8}" y="{y + 17}"><title>{html.escape(node)}</title>'
                f"{html.escape(_label(node))}</text></g>"
            )

    paths: list[str] = []
    for src, dst in data.edges:
        if src not in positions or dst not in positions:
            continue
        sx, sy = positions[src]
        dx, dy = positions[dst]
        start_x, start_y = sx + BOX_WIDTH, sy + BOX_HEIGHT // 2
        end_x, end_y = dx, dy + BOX_HEIGHT // 2
        mid = (start_x + end_x) // 2
        paths.append(
            f'<path d="M {start_x} {start_y} C {mid} {start_y}, {mid} {end_y}, {end_x} {end_y}"/>'
        )

    edge_lines = "".join(
        f"<li><code>{html.escape(src)}</code> → <code>{html.escape(dst)}</code></li>"
        for src, dst in data.edges
    )
    note_lines = "".join(f"<li>{html.escape(note)}</li>" for note in data.notes)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(data.title)}</title>
<style>
body {{ font: 14px/1.5 system-ui, sans-serif; margin: 2rem; color: #1a1a2e; background: #fafafa; }}
h1 {{ font-size: 1.3rem; }} .sub {{ color: #555; margin-bottom: 1.5rem; }}
svg {{ background: #fff; border: 1px solid #ddd; border-radius: 6px; max-width: 100%; height: auto; }}
svg rect {{ fill: #eef2ff; stroke: #6474d8; }} svg text {{ font: 12px monospace; fill: #1a1a2e; }}
svg text.col {{ font: bold 12px system-ui; fill: #555; }}
svg path {{ fill: none; stroke: #b0b8e8; stroke-width: 1.2; }}
ul {{ columns: 1; }} code {{ background: #eee; padding: 0 3px; border-radius: 3px; }}
.notes li {{ color: #7a4a00; }}
footer {{ margin-top: 2rem; color: #888; font-size: 12px; }}
</style></head><body>
<h1>{html.escape(data.title)}</h1>
<p class="sub">{html.escape(data.subtitle)}</p>
<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{html.escape(data.title)}">
{"".join(paths)}
{"".join(svg)}
</svg>
{f'<h2>Notes</h2><ul class="notes">{note_lines}</ul>' if data.notes else ""}
<h2>Edges</h2><ul>{edge_lines or "<li>none in this slice</li>"}</ul>
<footer>Generated by governance visualize from a bounded graph slice. Regenerate rather than edit.</footer>
</body></html>
"""


# ------------------------------------------------------------------------- view builders


def _architecture(ctx: Context, focus: str | None) -> ViewData:
    components = read_json(ctx.paths.effective_repository).get("components", [])
    by_id = {component["id"]: component for component in components}
    if focus and focus not in by_id:
        raise ViewError(f"no component {focus!r}; try one of: {', '.join(sorted(by_id))}")

    edges = sorted(
        (component["id"], dependency)
        for component in components
        for dependency in component.get("allowed_dependencies", [])
        if dependency in by_id
    )
    if focus:
        keep = {focus} | {dst for src, dst in edges if src == focus} | {src for src, dst in edges if dst == focus}
        edges = [(src, dst) for src, dst in edges if src in keep and dst in keep]
    else:
        keep = set(by_id)

    groups: dict[str, list[str]] = {"Frontend": [], "Application": [], "Data & infrastructure": []}
    for component_id in sorted(keep):
        kind = by_id[component_id]["kind"]
        if kind in ("frontend-feature",):
            groups["Frontend"].append(component_id)
        elif kind in ("data", "infrastructure", "host-runtime", "image-only-service", "mcp-sidecar", "db-resident-surface"):
            groups["Data & infrastructure"].append(component_id)
        else:
            groups["Application"].append(component_id)

    return ViewData(
        title="Component architecture" + (f" — {focus}" if focus else ""),
        subtitle="Declared dependencies between governed components. An edge means the left declares it may depend on the right.",
        layers=[(label, nodes) for label, nodes in groups.items()],
        edges=edges,
    )


def _site(ctx: Context, focus: str | None) -> ViewData:
    report_path = ctx.repo_root / "governance" / "graph" / "reports" / "boundaries.json"
    if not report_path.is_file():
        raise ViewError("boundaries.json is missing; run `make governance-sync` first")
    report = read_json(report_path)
    pages = report.get("pages", [])
    if focus:
        pages = [page for page in pages if page["route"] == focus]
        if not pages:
            raise ViewError(f"no page with route {focus!r}")
    else:
        pages = [page for page in pages if page.get("proxy_handlers") or page.get("websocket")]

    ws_node = "WS /api/v1/ws/agent (proxy bypass)"
    page_nodes, handler_nodes, backend_nodes = [], set(), set()
    edges: list[tuple[str, str]] = []
    notes: list[str] = []
    for page in pages:
        page_nodes.append(page["route"])
        for handler in page.get("proxy_handlers", []):
            handler_nodes.add(handler)
            edges.append((page["route"], handler))
        if page.get("websocket"):
            handler_nodes.add(ws_node)
            edges.append((page["route"], ws_node))
        for module in page.get("backend_modules", []):
            backend_nodes.add(module)
            for handler in page.get("proxy_handlers", []):
                edges.append((handler, module))
        for unmatched in page.get("unmatched_paths", []):
            notes.append(f"{page['route']} calls {unmatched}, which nothing serves (broken chain).")

    return ViewData(
        title="Site map" + (f" — {focus}" if focus else ""),
        subtitle="Pages, the proxy handlers they call through, and the backend route modules on the other side. The WebSocket edge is the one documented proxy bypass.",
        layers=[
            ("Pages", page_nodes),
            ("Proxy handlers", sorted(handler_nodes)),
            ("Backend route modules", sorted(backend_nodes)),
        ],
        edges=sorted(set(edges)),
        notes=notes,
    )


def _impact(ctx: Context, focus: str | None) -> ViewData:
    if not focus:
        raise ViewError("the impact view needs --focus <repo-relative path>")
    from repo_governance.renderers.context import analyse_impact

    impact = analyse_impact(ctx, [focus.replace("\\", "/")], 1)
    edges: list[tuple[str, str]] = []
    for component in impact.components:
        edges.append((focus, component))
        for validator in impact.validators:
            edges.append((component, validator))

    return ViewData(
        title=f"Change impact — {focus}",
        subtitle="The blast radius impact analysis reports: components reached, proxy handlers in the chain, validators that prove the change.",
        layers=[
            ("Changed + importers", [focus, *impact.graph_files[:20]]),
            ("Components", impact.components),
            ("Proxy handlers", impact.proxy_routes),
            ("Validators", impact.validators),
        ],
        edges=sorted(set(edges)),
        notes=list(impact.notes),
    )


def _configuration(ctx: Context, focus: str | None) -> ViewData:
    manifest = read_json(ctx.paths.generated_manifests / "configuration.json")
    variables = manifest.get("variables", [])
    edges = sorted(
        (variable["name"], consumer)
        for variable in variables
        for consumer in variable.get("consumed_by", [])
    )
    if focus:
        edges = [(name, consumer) for name, consumer in edges if focus in (name, consumer)]
        if not edges:
            raise ViewError(f"{focus!r} is neither a consumed variable nor a consuming component")

    names = sorted({name for name, _ in edges})
    consumers = sorted({consumer for _, consumer in edges})
    return ViewData(
        title="Configuration flow" + (f" — {focus}" if focus else ""),
        subtitle="Environment variables and their consumers. Names only — values never enter governance output.",
        layers=[("Variables", names), ("Consumers", consumers)],
        edges=edges,
    )


def _migration(ctx: Context, focus: str | None) -> ViewData:
    from repo_governance.extractors.alembic import extract_revisions

    graph = extract_revisions(ctx)
    ordered = [revision.id for revision in graph.revisions]
    edges: list[tuple[str, str]] = []
    for revision in graph.revisions:
        parents = revision.down_revision
        for parent in [parents] if isinstance(parents, str) else (parents or []):
            edges.append((parent, revision.id))

    notes = [f"Head: {head}" for head in graph.heads]
    notes += [f"Unknown: {unknown}" for unknown in graph.unknowns]
    half = (len(ordered) + 1) // 2
    return ViewData(
        title="Migration graph",
        subtitle="Alembic revisions in chain order. Branches show as one parent with two children; merges as two parents joining.",
        layers=[("Revisions (older)", ordered[:half]), ("Revisions (newer)", ordered[half:])],
        edges=sorted(edges),
        notes=notes,
    )


def _security(ctx: Context, focus: str | None) -> ViewData:
    runtime = read_json(ctx.paths.generated_manifests / "ai-runtime.json")
    agent_nodes = [agent["id"] for agent in runtime.get("agents", [])]
    source_nodes: list[str] = []
    gate_nodes: list[str] = []
    edges: list[tuple[str, str]] = []

    for agent in runtime.get("agents", []):
        for toolset in agent.get("toolsets", []):
            source_nodes.append(toolset)
            edges.append((agent["id"], toolset))

    for server in runtime.get("deployment_servers", []):
        name = f"deployment: {server['name']}"
        source_nodes.append(name)
        for agent in agent_nodes:
            edges.append((agent, name))
        gate = f"approval: {server.get('approval_gating', 'unknown')}"
        gate_nodes.append(gate)
        edges.append((name, gate))
        for layer in server.get("readonly_enforcement_layers", []):
            gate = f"readonly: {layer['layer']}"
            gate_nodes.append(gate)
            edges.append((name, gate))

    findings = runtime.get("standing_findings", []) or [
        "mcp-approval-gating-asymmetry",
        "mcp-no-connection-time-ssrf-revalidation",
        "mcp-url-embedded-credentials-unencrypted",
    ]
    notes = [f"Standing finding: {finding}" if isinstance(finding, str) else str(finding) for finding in findings]
    notes.append("Per-user MCP connections exist only as database rows; they appear here as a toolset class, never as enumerated servers (runtime-evidence rule).")

    return ViewData(
        title="Agent and MCP permission map",
        subtitle="The agent, its tool sources, and the gates each source passes through. What has no gate edge has no gate.",
        layers=[
            ("Agents", agent_nodes),
            ("Tool sources", sorted(set(source_nodes))),
            ("Gates", sorted(set(gate_nodes))),
        ],
        edges=sorted(set(edges)),
        notes=notes,
    )


_BUILDERS = {
    "architecture": _architecture,
    "site": _site,
    "impact": _impact,
    "configuration": _configuration,
    "migration": _migration,
    "security": _security,
}


def render_view(ctx: Context, view_id: str, focus: str | None) -> str:
    definitions = load_definitions(ctx)
    if view_id not in definitions or view_id not in _BUILDERS:
        known = ", ".join(sorted(set(definitions) & set(_BUILDERS)))
        raise ViewError(f"no view {view_id!r}; known views: {known}")
    data = _BUILDERS[view_id](ctx, focus)
    data = _trim(data, definitions[view_id].get("node_budget", 100))
    return render_html(data)


def write_view(ctx: Context, view_id: str, focus: str | None) -> str:
    """Render and write under artifacts/governance/ (gitignored). Returns the repo-relative path."""
    from repo_governance.io_atomic import write_text_atomic

    content = render_view(ctx, view_id, focus)
    slug = view_id if not focus else f"{view_id}-{''.join(ch if ch.isalnum() else '-' for ch in focus).strip('-')}"
    relative = f"artifacts/governance/{slug}.html"
    write_text_atomic(ctx.repo_root / relative, content)
    return relative
