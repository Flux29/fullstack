"""The site graph: every page joined to the backend surface it actually reaches.

The join the blueprint calls the site and user-journey view: page → imported modules →
called API paths → proxy handler → backend path template → FastAPI route module. The chat
WebSocket is modeled as the one documented edge that bypasses the proxy — detected, not
special-cased away. A called path that matches no proxy handler is a **broken chain** and
is reported per page, because impact analysis that silently drops it reports wrong blast
radii — the exact failure the blueprint warns about.

Pages with no API calls at all (marketing, legal) are a normal shape, not a gap; the
report distinguishes them from broken chains.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repo_governance.config import Context
from repo_governance.extractors.imports import RUNTIME_KINDS, get_import_graph
from repo_governance.extractors.relations import get_relations
from repo_governance.extractors.ts_imports import called_api_paths, get_ts_import_graph, normalize_template
from repo_governance.io_atomic import read_json

#: How many import waves from a page count as "the page's own code". Two reaches the API
#: client through a component; three buys little beyond shared UI primitives.
FORWARD_WAVES = 3


@dataclass
class PageChain:
    route: str
    file: str
    called_paths: list[str] = field(default_factory=list)
    proxy_handlers: list[str] = field(default_factory=list)
    backend_modules: list[str] = field(default_factory=list)
    #: Called paths matching a backend route but no proxy handler: server-side fetching
    #: (server components through server-api.ts), which legitimately skips the proxy.
    server_calls: list[str] = field(default_factory=list)
    #: Called paths matching nothing at all — a call into the void.
    unmatched_paths: list[str] = field(default_factory=list)
    websocket: bool = False


def build_site_chains(ctx: Context) -> list[PageChain]:
    """One chain record per page, deterministic order by file."""
    ts_graph = get_ts_import_graph(ctx)
    python = get_import_graph(ctx)
    relations = get_relations(ctx)

    interfaces_path = ctx.paths.generated_manifests / "interfaces.json"
    interfaces = read_json(interfaces_path) if interfaces_path.is_file() else {}
    proxy_routes = interfaces.get("proxy_routes", [])
    pages = interfaces.get("frontend_pages", [])
    ws_endpoint = interfaces.get("websocket", {}).get("endpoint", "/api/v1/ws/agent")

    #: Normalized frontend path -> (handler file, backend templates).
    by_frontend_path: dict[str, tuple[str, set[str]]] = {}
    for route in proxy_routes:
        templates = {normalize_template(target.get("path_template", "")) for target in route.get("backend_targets", [])}
        by_frontend_path[normalize_template(route.get("frontend_path", ""))] = (route["file"], templates)

    #: Normalized backend path -> route modules.
    modules_by_template: dict[str, set[str]] = {}
    for api_route in relations.api_routes:
        modules_by_template.setdefault(normalize_template(api_route.path), set()).add(api_route.module)

    #: Modules whose text names the WS endpoint — the consumers of the documented exception.
    ws_modules = {module for module in ts_graph.modules if _module_mentions(ctx, module, ws_endpoint)}

    chains: list[PageChain] = []
    for page in pages:
        page_file = page["file"]
        closure = {page_file}
        frontier = {page_file}
        for _ in range(FORWARD_WAVES):
            frontier = {
                edge.dst for edge in ts_graph.edges if edge.src in frontier and edge.kind in RUNTIME_KINDS
            } - closure
            closure |= frontier

        chain = PageChain(route=page["route"], file=page_file, websocket=bool(closure & ws_modules))
        chain.called_paths = called_api_paths(ctx, sorted(closure))

        backend_templates: set[str] = set()
        for called in chain.called_paths:
            normalized = normalize_template(called)
            entry = by_frontend_path.get(normalized)
            if entry is not None:
                handler, templates = entry
                chain.proxy_handlers.append(handler)
                backend_templates |= templates
            elif normalized == normalize_template(ws_endpoint):
                pass  # The documented exception; the websocket flag carries it.
            elif normalized in modules_by_template:
                chain.server_calls.append(called)
                backend_templates.add(normalized)
            else:
                chain.unmatched_paths.append(called)

        modules = {module for template in backend_templates for module in modules_by_template.get(template, set())}
        chain.backend_modules = sorted(module for module in modules if python.path_for_module(module))
        chain.proxy_handlers = sorted(set(chain.proxy_handlers))
        chain.server_calls = sorted(set(chain.server_calls))
        chain.unmatched_paths = sorted(set(chain.unmatched_paths))
        chains.append(chain)

    return sorted(chains, key=lambda chain: chain.file)


def _module_mentions(ctx: Context, module: str, needle: str) -> bool:
    try:
        return needle in (ctx.repo_root / module).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def build_boundaries_report(ctx: Context) -> dict:
    """The committed chain-coverage report: which pages reach the backend, which chains
    break, and where the WS exception is consumed."""
    chains = build_site_chains(ctx)
    entries = []
    for chain in chains:
        entry: dict = {"route": chain.route, "file": chain.file}
        if chain.proxy_handlers:
            entry["proxy_handlers"] = chain.proxy_handlers
        if chain.backend_modules:
            entry["backend_modules"] = chain.backend_modules
        if chain.server_calls:
            entry["server_calls"] = chain.server_calls
        if chain.unmatched_paths:
            entry["unmatched_paths"] = chain.unmatched_paths
        if chain.websocket:
            entry["websocket"] = True
        entries.append(entry)

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": [
                "backend/app/api/routes/",
                "frontend/src/app/",
                "governance/manifests/generated/interfaces.json",
            ],
            "extractor_version": ctx.config.version,
        },
        "summary": {
            "pages": len(chains),
            "pages_with_api_chains": sum(1 for chain in chains if chain.proxy_handlers),
            "pages_with_broken_chains": sum(1 for chain in chains if chain.unmatched_paths),
            "websocket_exception_pages": sum(1 for chain in chains if chain.websocket),
        },
        "pages": entries,
    }
