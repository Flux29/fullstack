"""Import-graph enforcement of declared architecture boundaries.

The counterpart to ``checks.architecture``: that module verifies the declarations are
coherent, this one verifies the code respects them. Everything here reads the graph built
by ``extractors.imports`` — AST facts, never imported application code.

Scope honesty: the graph covers ``backend/app`` Python modules only. A boundary whose
violating edge would have to cross languages (backend into the frontend, a Compose service
into another) cannot fire here, so a clean pass from these checks is a statement about
Python imports, not about every declared boundary. Modules listed in the graph's
``dynamic_import_sites`` have out-edges the graph cannot see.
"""

from __future__ import annotations

from repo_governance.checks import CheckScope
from repo_governance.extractors.imports import RUNTIME_KINDS, get_import_graph
from repo_governance.io_atomic import read_json
from repo_governance.models import Issue

SERVICES_PACKAGE = "app.services"
SERVICES_DIR = "backend/app/services"


def _active_exception_scopes(scope: CheckScope) -> list[str]:
    path = scope.ctx.paths.curated_manifests / "exceptions.json"
    if not path.is_file():
        return []
    try:
        entries = read_json(path).get("exceptions", [])
    except ValueError:
        return []
    return [
        scoped
        for entry in entries
        if entry.get("status") == "active"
        for scoped in entry.get("scope", [])
    ]


def _excused(path: str, exception_scopes: list[str]) -> bool:
    return any(path == scoped or path.startswith(scoped.rstrip("*")) for scoped in exception_scopes)


def check_layering(scope: CheckScope) -> list[Issue]:
    """Routes never import repositories; the service layer is not optional.

    Runtime edges only: a function-local import in a route is still a call path, while a
    ``TYPE_CHECKING`` reference is typing, not a call. ``app.api.deps`` is deliberately
    out of scope — dependency-injection wiring is the sanctioned place to construct
    services around repositories, and routes reach both only through it.
    """
    graph = get_import_graph(scope.ctx)
    issues: list[Issue] = []
    for edge in graph.edges:
        if edge.kind not in RUNTIME_KINDS:
            continue
        if not edge.src.startswith("app.api.routes."):
            continue
        if edge.dst != "app.repositories" and not edge.dst.startswith("app.repositories."):
            continue
        issues.append(
            Issue(
                message=f"Route module {edge.src} imports the repository layer directly.",
                path=graph.path_for_module(edge.src),
                evidence=f"{edge.src} imports {edge.dst} at line {edge.line}.",
                repair=(
                    "Call a service; the service owns the repository, the transaction boundary, "
                    "and the domain exceptions."
                ),
            )
        )
    return issues


def check_forbidden_dependencies(scope: CheckScope) -> list[Issue]:
    """No observed import edge lands in a component's forbidden_dependencies.

    The pass is only as wide as the graph: forbidden pairs whose violating edge would not
    be a Python import inside backend/app (frontend components, Compose services) are not
    exercised here, which the module docstring states rather than hides.
    """
    from repo_governance.merge import build_components
    from repo_governance.renderers.context import owner_of

    graph = get_import_graph(scope.ctx)
    manifest, _ = build_components(scope.ctx)
    components = manifest["components"]
    forbidden_by_id = {
        component["id"]: set(component.get("forbidden_dependencies", []))
        for component in components
    }

    owner_cache: dict[str, str | None] = {}

    def owner(path: str | None) -> str | None:
        if path is None:
            return None
        if path not in owner_cache:
            owner_cache[path] = owner_of(components, path)
        return owner_cache[path]

    issues: list[Issue] = []
    for edge in graph.edges:
        if edge.kind not in RUNTIME_KINDS:
            continue
        src_component = owner(graph.path_for_module(edge.src))
        dst_component = owner(graph.path_for_module(edge.dst))
        if not src_component or not dst_component or src_component == dst_component:
            continue
        if dst_component in forbidden_by_id.get(src_component, set()):
            issues.append(
                Issue(
                    message=(
                        f"Component {src_component!r} imports {dst_component!r}, "
                        "which its declaration forbids."
                    ),
                    path=graph.path_for_module(edge.src),
                    evidence=f"{edge.src} imports {edge.dst} at line {edge.line}.",
                    repair="Remove the dependency, or revise the declared boundary in a governed change.",
                )
            )
    return issues


def _thick_domains(scope: CheckScope) -> list[str]:
    from repo_governance.config import EXCLUDED_DIRS

    services_dir = scope.ctx.repo_root / SERVICES_DIR
    if not services_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in services_dir.iterdir()
        if entry.is_dir() and entry.name not in EXCLUDED_DIRS
    )


def check_dependency_cycles(scope: CheckScope) -> list[Issue]:
    """No strongly connected component larger than one module, in either language.

    A cycle closed only through deferred imports is a coupling smell; one closed at module
    scope also works only while initialization order happens to hold — the issue says
    which it found.
    """
    from repo_governance.graph.queries import build_cycles_report

    report = build_cycles_report(scope.ctx)
    issues: list[Issue] = []
    for language in ("python", "typescript"):
        for entry in report[language]:
            severity = "import-time" if entry["import_time"] else "deferred-import"
            issues.append(
                Issue(
                    message=(
                        f"{len(entry['modules'])} {language} modules form a dependency cycle "
                        f"({severity})."
                    ),
                    path="governance/graph/reports/cycles.json",
                    evidence=" <-> ".join(entry["modules"]),
                    repair=(
                        "Break the cycle by moving the shared piece down, or record the coupling "
                        "as deliberate in an ADR."
                    ),
                )
            )
    return issues


def check_orphans(scope: CheckScope) -> list[Issue]:
    """Every module has an importer or a classification explaining why it does not.

    Scope honesty: this is the module slice of the no-orphaned-surfaces rule. Routes and
    tools gain consumer analysis with the phase-7 site join; unconsumed configuration is
    already surfaced by the configuration extractor.
    """
    from repo_governance.graph.queries import build_orphans_report

    report = build_orphans_report(scope.ctx)
    issues: list[Issue] = []
    for language in ("python", "typescript"):
        orphans = report[language]["orphans"]
        if not orphans:
            continue
        issues.append(
            Issue(
                message=(
                    f"{len(orphans)} {language} module(s) have no importer and no "
                    "classification that explains it."
                ),
                path="governance/graph/reports/orphans.json",
                evidence=", ".join(orphans),
                repair=(
                    "Delete the dead module, wire the unfinished feature, or classify it "
                    "(component entrypoint, feature-disabled, dynamic subtree)."
                ),
            )
        )
    return issues


def check_broken_site_chains(scope: CheckScope) -> list[Issue]:
    """No page calls an API path that nothing serves.

    The route-consumer slice of no-orphaned-surfaces, from the other side: an orphaned
    route has no caller, a broken chain has no servant. Both are dead surface wearing a
    live interface.
    """
    from repo_governance.graph.site import build_site_chains

    issues: list[Issue] = []
    for chain in build_site_chains(scope.ctx):
        if not chain.unmatched_paths:
            continue
        issues.append(
            Issue(
                message=f"Page {chain.route} calls {len(chain.unmatched_paths)} API path(s) that nothing serves.",
                path=chain.file,
                evidence=", ".join(chain.unmatched_paths),
                repair=(
                    "Delete the dead call, add the missing proxy handler or backend route, "
                    "or fix the path."
                ),
            )
        )
    return issues


def check_thick_domain_facades(scope: CheckScope) -> list[Issue]:
    """A thick service subpackage is imported only through its package root.

    Two violation shapes: an outside module importing a sub-module past the facade, and a
    domain that has external importers but no facade at all — the second is the larger
    divergence, because every one of its consumers is forced into the first.
    """
    graph = get_import_graph(scope.ctx)
    exception_scopes = _active_exception_scopes(scope)
    issues: list[Issue] = []

    for domain in _thick_domains(scope):
        root = f"{SERVICES_PACKAGE}.{domain}"
        prefix = root + "."
        external_deep = [
            edge
            for edge in graph.edges
            if edge.kind in RUNTIME_KINDS
            and edge.dst.startswith(prefix)
            and edge.src != root
            and not edge.src.startswith(prefix)
        ]
        if root not in graph.modules and external_deep:
            importer_count = len({edge.src for edge in external_deep})
            issues.append(
                Issue(
                    message=f"Thick domain {SERVICES_DIR}/{domain} has external importers but no facade.",
                    path=f"{SERVICES_DIR}/{domain}",
                    evidence=(
                        f"{importer_count} outside module(s) deep-import its sub-modules; with no "
                        "__init__.py there is no package root to import through."
                    ),
                    repair="Add an __init__.py facade exporting the domain's public API, then migrate the importers.",
                )
            )

        by_importer: dict[str, list[str]] = {}
        for edge in external_deep:
            by_importer.setdefault(edge.src, []).append(f"{edge.dst} at line {edge.line}")
        for importer in sorted(by_importer):
            path = graph.path_for_module(importer)
            if path is None or _excused(path, exception_scopes):
                continue
            issues.append(
                Issue(
                    message=f"{importer} imports {domain!r} sub-modules past the package root.",
                    path=path,
                    evidence="; ".join(sorted(by_importer[importer])),
                    repair=f"Import through {root} so callers depend on the domain, not its internal layout.",
                )
            )
    return issues
