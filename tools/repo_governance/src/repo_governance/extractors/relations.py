"""Relation extraction: what the backend modules *are*, not just what they import.

The import graph answers "who depends on whom"; this answers "which module is a route
surface, a background task, an ORM model, a test, a tool source" — the node kinds the
committed vocabulary in ``governance/graph/`` declares beyond plain modules. AST only,
never importing application code, with each of the blueprint's known extraction limits
handled the way it prescribes:

- Google tools are **data, not AST-visible functions**: the ``DIRECT_GOOGLE_PRODUCTS``
  registry is read as a table of ``_product(kind, ...)`` calls, never as a call graph.
- Taskiq tasks register by decorator plus import side-effect; the ``@broker.task``
  decorators are the extraction target and the explicit imports in ``taskiq_app.py`` are
  already edges in the import graph.
- Route paths are assembled where routers are *included*, not where they are declared, so
  the v1 registry's ``include_router(..., prefix=...)`` calls are parsed first. Routers
  included somewhere other than the v1 registry keep a router-relative path and are
  recorded in ``unknowns`` rather than silently misnamed.

Uncertainty is first-class output, same contract as the import graphs: a file that cannot
be parsed lands in ``unknowns`` and is never counted as containing nothing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.io_atomic import relative_posix

NODE_API_ROUTE = "api-route"
NODE_TASK = "task"
NODE_DB_MODEL = "db-model"
NODE_TEST_MODULE = "test-module"
NODE_TOOL = "tool"

EDGE_TESTS = "TESTS"
EDGE_ROUTES_TO = "ROUTES_TO"

ROUTES_DIR = "backend/app/api/routes"
WORKER_DIR = "backend/app/worker"
MODELS_DIR = "backend/app/db/models"
TESTS_DIR = "backend/tests"
PRODUCTS_FILE = "backend/app/agents/google_apis/products.py"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "websocket"})


@dataclass(frozen=True, order=True)
class ApiRoute:
    method: str  # Upper-case HTTP verb, or WEBSOCKET.
    path: str  # Full path once the include prefix is applied.
    module: str  # Dotted module (app.api.routes.v1.sessions).
    function: str
    line: int


@dataclass(frozen=True, order=True)
class TaskDef:
    name: str
    module: str
    line: int


@dataclass(frozen=True, order=True)
class ModelDef:
    name: str
    table: str | None
    module: str
    line: int


@dataclass
class Relations:
    api_routes: tuple[ApiRoute, ...] = ()
    tasks: tuple[TaskDef, ...] = ()
    models: tuple[ModelDef, ...] = ()
    #: (test module repo-relative path, dotted app module it imports at runtime).
    test_edges: tuple[tuple[str, str], ...] = ()
    #: One entry per registry product: {"kind": ..., "tools": [names]}.
    tools: tuple[dict, ...] = ()
    unknowns: tuple[str, ...] = ()

    def as_payload(self) -> dict:
        return {
            "api_routes": [
                [route.method, route.path, route.module, route.function, route.line] for route in self.api_routes
            ],
            "tasks": [[task.name, task.module, task.line] for task in self.tasks],
            "models": [[model.name, model.table, model.module, model.line] for model in self.models],
            "test_edges": [list(edge) for edge in self.test_edges],
            "tools": list(self.tools),
            "unknowns": list(self.unknowns),
        }


def _module_name(path: Path, backend_root: Path) -> str:
    parts = list(path.relative_to(backend_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse(path: Path, unknowns: list[str], repo_root: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError) as error:
        unknowns.append(f"{relative_posix(path, repo_root)}: {error}")
        return None


def _include_prefixes(ctx: Context, unknowns: list[str]) -> dict[str, str]:
    """``{module stem: prefix}`` from the v1 registry's include_router calls.

    A module included without a prefix keyword maps to the empty prefix, which is a real
    value (health, agent, files, contact), not an absence.
    """
    registry = ctx.repo_root / ROUTES_DIR / "v1" / "__init__.py"
    if not registry.is_file():
        unknowns.append(f"{ROUTES_DIR}/v1/__init__.py does not exist; route paths stay router-relative")
        return {}
    tree = _parse(registry, unknowns, ctx.repo_root)
    if tree is None:
        return {}

    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        target = node.args[0]
        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)):
            continue  # Something other than `module.router`; nothing to name.
        prefix = ""
        for keyword in node.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                prefix = str(keyword.value.value)
        prefixes[target.value.id] = prefix
    return prefixes


def _own_router_prefix(tree: ast.Module) -> str:
    """The prefix declared on the module's own ``router = APIRouter(prefix=...)``."""
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            continue
        func = node.value.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "APIRouter":
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return ""


def _extract_routes(ctx: Context, unknowns: list[str]) -> list[ApiRoute]:
    routes_root = ctx.repo_root / ROUTES_DIR
    backend_root = ctx.repo_root / "backend"
    prefixes = _include_prefixes(ctx, unknowns)

    routes: list[ApiRoute] = []
    for path in iter_files(routes_root, suffixes=(".py",)):
        if path.name == "__init__.py":
            continue
        tree = _parse(path, unknowns, ctx.repo_root)
        if tree is None:
            continue
        module = _module_name(path, backend_root)
        in_v1 = "v1" in path.relative_to(routes_root).parts
        if in_v1 and path.stem not in prefixes:
            unknowns.append(
                f"{relative_posix(path, ctx.repo_root)}: not included by the v1 registry; "
                "its route paths stay router-relative"
            )
        # FastAPI concatenates both prefixes: include_router(prefix=P) wrapping
        # APIRouter(prefix=Q) mounts at P + Q. files.py carries its whole prefix on the
        # router itself and none in the registry.
        prefix = prefixes.get(path.stem, "") + _own_router_prefix(tree)
        base = f"/api/v1{prefix}" if in_v1 else prefix

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                    continue
                if decorator.func.attr not in _HTTP_METHODS:
                    continue
                if not (isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "router"):
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    unknowns.append(
                        f"{relative_posix(path, ctx.repo_root)}:{node.lineno}: {node.name} has a non-literal route path"
                    )
                    continue
                routes.append(
                    ApiRoute(
                        method=decorator.func.attr.upper(),
                        path=base + str(decorator.args[0].value),
                        module=module,
                        function=node.name,
                        line=node.lineno,
                    )
                )
    return sorted(routes)


def _extract_tasks(ctx: Context, unknowns: list[str]) -> list[TaskDef]:
    backend_root = ctx.repo_root / "backend"
    tasks: list[TaskDef] = []
    for path in iter_files(ctx.repo_root / WORKER_DIR, suffixes=(".py",)):
        tree = _parse(path, unknowns, ctx.repo_root)
        if tree is None:
            continue
        module = _module_name(path, backend_root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "task"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "broker"
                ):
                    tasks.append(TaskDef(name=node.name, module=module, line=node.lineno))
    return sorted(tasks)


def _extract_models(ctx: Context, unknowns: list[str]) -> list[ModelDef]:
    backend_root = ctx.repo_root / "backend"
    models: list[ModelDef] = []
    for path in iter_files(ctx.repo_root / MODELS_DIR, suffixes=(".py",)):
        tree = _parse(path, unknowns, ctx.repo_root)
        if tree is None:
            continue
        module = _module_name(path, backend_root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(isinstance(base, ast.Name) and base.id == "Base" for base in node.bases):
                continue
            table = None
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in statement.targets)
                    and isinstance(statement.value, ast.Constant)
                ):
                    table = str(statement.value.value)
            models.append(ModelDef(name=node.name, table=table, module=module, line=node.lineno))
    return sorted(models)


def _extract_test_edges(ctx: Context, unknowns: list[str]) -> list[tuple[str, str]]:
    """(test file, app module) for every runtime import of ``app.*`` from the test suite.

    Resolution follows the import graph's rule: try the full name as a module, fall back to
    its parent, and record what matches neither instead of dropping it.
    """
    from repo_governance.extractors.imports import get_import_graph

    modules = get_import_graph(ctx).modules
    edges: set[tuple[str, str]] = set()
    for path in iter_files(ctx.repo_root / TESTS_DIR, suffixes=(".py",)):
        tree = _parse(path, unknowns, ctx.repo_root)
        if tree is None:
            continue
        rel = relative_posix(path, ctx.repo_root)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [f"{node.module}.{alias.name}" for alias in node.names]
            for name in names:
                if not (name == "app" or name.startswith("app.")):
                    continue
                if name in modules:
                    edges.add((rel, name))
                elif "." in name and name.rsplit(".", 1)[0] in modules:
                    edges.add((rel, name.rsplit(".", 1)[0]))
                else:
                    unknowns.append(f"{rel}: imports {name}, which resolves to no app module")
    return sorted(edges)


def _extract_tools(ctx: Context, unknowns: list[str]) -> list[dict]:
    """The Google product registry as a table: kind plus tool names, per product."""
    path = ctx.repo_root / PRODUCTS_FILE
    if not path.is_file():
        unknowns.append(f"{PRODUCTS_FILE} does not exist; the Google tool registry is unread")
        return []
    tree = _parse(path, unknowns, ctx.repo_root)
    if tree is None:
        return []

    products: list[dict] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "_product" or len(node.args) < 4:
            continue
        kind_node, tools_node = node.args[0], node.args[3]
        if not isinstance(kind_node, ast.Constant):
            unknowns.append(f"{PRODUCTS_FILE}:{node.lineno}: _product call with a non-literal kind")
            continue
        names: list[str] = []
        if isinstance(tools_node, ast.Tuple | ast.List):
            for element in tools_node.elts:
                if isinstance(element, ast.Tuple | ast.List) and element.elts:
                    first = element.elts[0]
                    if isinstance(first, ast.Constant):
                        names.append(str(first.value))
        if not names:
            unknowns.append(
                f"{PRODUCTS_FILE}:{node.lineno}: no literal tool names found for product {kind_node.value!r}"
            )
        products.append({"kind": str(kind_node.value), "tools": names})
    if not products:
        unknowns.append(f"{PRODUCTS_FILE}: no _product calls matched; the registry shape may have changed")
    return sorted(products, key=lambda item: item["kind"])


def build_relations(ctx: Context) -> Relations:
    unknowns: list[str] = []
    api_routes = _extract_routes(ctx, unknowns)
    tasks = _extract_tasks(ctx, unknowns)
    models = _extract_models(ctx, unknowns)
    test_edges = _extract_test_edges(ctx, unknowns)
    tools = _extract_tools(ctx, unknowns)
    return Relations(
        api_routes=tuple(api_routes),
        tasks=tuple(tasks),
        models=tuple(models),
        test_edges=tuple(test_edges),
        tools=tuple(tools),
        unknowns=tuple(sorted(unknowns)),
    )


def routes_to_edges(ctx: Context, relations: Relations) -> list[tuple[str, str]]:
    """ROUTES_TO: (route module, service module) for every runtime import a route module
    makes into the service layer. A join of the relation layer with the import graph."""
    from repo_governance.extractors.imports import RUNTIME_KINDS, get_import_graph

    graph = get_import_graph(ctx)
    route_modules = {route.module for route in relations.api_routes}
    return sorted(
        {
            (edge.src, edge.dst)
            for edge in graph.edges
            if edge.kind in RUNTIME_KINDS and edge.src in route_modules and edge.dst.startswith("app.services")
        }
    )


_MEMO: dict[str, Relations] = {}


def get_relations(ctx: Context) -> Relations:
    """Process-memoized, same contract as the import graphs."""
    key = str(ctx.repo_root.resolve())
    if key not in _MEMO:
        _MEMO[key] = build_relations(ctx)
    return _MEMO[key]
