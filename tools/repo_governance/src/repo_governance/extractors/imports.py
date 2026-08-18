"""The Python import/module graph over the backend application.

Phase 6's trimmed slice: modules and typed import edges, extracted by AST and never by
importing application code. The inventory is a filesystem walk, deliberately not
``__init__``-based package discovery — ``app/services/`` and four other packages are
PEP-420 namespace packages with no ``__init__.py``, and a discoverer that requires one
silently drops the largest package in the tree.

Uncertainty is first-class output. A file that cannot be parsed lands in ``unknowns``; an
``app.*`` import that resolves to no module lands in ``unresolved``; a module that calls
``importlib``/``pkgutil``/``__import__`` lands in ``dynamic_import_sites``. None of these
are edges, and none of them are silence: a consumer that treats this graph as complete
must first check that all three are empty for the modules it cares about.

Edge-kind and node-kind identifiers conform to the reserved patterns in
``governance/schemas/graph-model.schema.json`` (edges ``^[A-Z][A-Z_]*$``, nodes
``^[a-z0-9][a-z0-9-]{0,63}$``), so promoting this vocabulary into ``governance/graph/``
later is a copy, not a redesign.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.io_atomic import relative_posix

NODE_PYTHON_MODULE = "python-module"

#: A module-scope import: executed at import time, unconditionally.
EDGE_IMPORTS = "IMPORTS"
#: An import inside ``if TYPE_CHECKING:`` — a typing reference, never a runtime call path.
EDGE_IMPORTS_TYPE_ONLY = "IMPORTS_TYPE_ONLY"
#: A function-local import — a runtime call path that is lazy, usually a cycle-breaker.
EDGE_IMPORTS_DEFERRED = "IMPORTS_DEFERRED"

#: The kinds that constitute a runtime dependency. A lazy import still executes.
RUNTIME_KINDS = frozenset({EDGE_IMPORTS, EDGE_IMPORTS_DEFERRED})
ALL_KINDS = frozenset({EDGE_IMPORTS, EDGE_IMPORTS_TYPE_ONLY, EDGE_IMPORTS_DEFERRED})

_DYNAMIC_IMPORT_CALLS = {
    ("importlib", "import_module"),
    ("pkgutil", "iter_modules"),
    ("pkgutil", "walk_packages"),
}
#: The same callables when imported bare (`from importlib import import_module`); the rag
#: facade's PEP 562 lazy exports call this form and went undetected until phase 6.
_DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module", "iter_modules", "walk_packages"})


@dataclass(frozen=True, order=True)
class ImportEdge:
    src: str
    dst: str
    kind: str
    line: int


@dataclass
class ImportGraph:
    """Modules, typed edges, and the three kinds of explicitly-reported uncertainty."""

    modules: dict[str, str] = field(default_factory=dict)  # dotted module -> repo-relative POSIX path
    edges: tuple[ImportEdge, ...] = ()
    dynamic_import_sites: tuple[str, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()  # (importing module, unresolvable target)
    unknowns: tuple[str, ...] = ()  # "path: parse error"
    _paths_to_modules: dict[str, str] = field(default_factory=dict)

    def module_for_path(self, relative_path: str) -> str | None:
        return self._paths_to_modules.get(relative_path)

    def path_for_module(self, module: str) -> str | None:
        return self.modules.get(module)

    def importers_of(self, module: str, kinds: frozenset[str] = RUNTIME_KINDS) -> set[str]:
        return {edge.src for edge in self.edges if edge.dst == module and edge.kind in kinds}

    def reverse_closure(
        self,
        seeds: set[str],
        *,
        max_depth: int,
        max_nodes: int,
        kinds: frozenset[str] = RUNTIME_KINDS,
    ) -> tuple[set[str], bool]:
        """Modules that import the seeds, transitively. Returns (importers, truncated).

        Seeds are not included in the answer. Truncation trims deterministically (sorted)
        and reports itself, because a silently bounded radius reads as a complete one.
        """
        reverse: dict[str, set[str]] = {}
        for edge in self.edges:
            if edge.kind in kinds:
                reverse.setdefault(edge.dst, set()).add(edge.src)

        reached: set[str] = set()
        frontier = set(seeds)
        truncated = False
        for _ in range(max(max_depth, 0)):
            wave = sorted(
                {importer for module in frontier for importer in reverse.get(module, set())} - reached - seeds
            )
            if not wave:
                break
            room = max_nodes - len(reached)
            if len(wave) > room:
                wave = wave[:room]
                truncated = True
            reached.update(wave)
            frontier = set(wave)
            if truncated:
                break
        return reached, truncated

    def as_payload(self) -> dict:
        """A JSON-serializable form for tests and reports. Deterministic by construction."""
        return {
            "modules": self.modules,
            "edges": [[edge.src, edge.dst, edge.kind, edge.line] for edge in self.edges],
            "dynamic_import_sites": list(self.dynamic_import_sites),
            "unresolved": [list(item) for item in self.unresolved],
            "unknowns": list(self.unknowns),
        }


def _module_name(relative_to_backend: Path) -> str:
    """``app/services/rag/config.py`` -> ``app.services.rag.config``; ``__init__`` names the package."""
    parts = list(relative_to_backend.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(importer: str, is_package: bool, level: int, module: str | None) -> str | None:
    """Resolve a ``from .x import y`` anchor per Python's relative-import semantics."""
    anchor = importer.split(".") if is_package else importer.split(".")[:-1]
    strip = level - 1
    if strip > len(anchor):
        return None
    anchor = anchor[: len(anchor) - strip] if strip else anchor
    if module:
        anchor = [*anchor, *module.split(".")]
    return ".".join(anchor) if anchor else None


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_dynamic_import_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _DYNAMIC_IMPORT_NAMES:
        return True
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _DYNAMIC_IMPORT_CALLS
    return False


def build_import_graph(
    ctx: Context,
    *,
    package_dir: str = "backend/app",
    top_package: str = "app",
) -> ImportGraph:
    """Extract the module inventory and typed import edges for one Python package tree."""
    package_root = ctx.repo_root / Path(package_dir)
    backend_root = package_root.parent

    modules: dict[str, str] = {}
    for path in iter_files(package_root, suffixes=(".py",)):
        dotted = _module_name(path.relative_to(backend_root))
        modules[dotted] = relative_posix(path, ctx.repo_root)

    edges: set[ImportEdge] = set()
    dynamic_sites: set[str] = set()
    unresolved: set[tuple[str, str]] = set()
    unknowns: list[str] = []
    prefix = top_package + "."

    def in_tree(name: str) -> bool:
        return name == top_package or name.startswith(prefix)

    def add_edge(src: str, target: str, kind: str, line: int) -> None:
        # `from M import name` names either the submodule M.name or a symbol in M; try the
        # submodule first, fall back to the module. An app.* target matching neither is
        # recorded as uncertainty, never dropped.
        if target in modules:
            edges.add(ImportEdge(src=src, dst=target, kind=kind, line=line))
        elif "." in target and target.rsplit(".", 1)[0] in modules:
            edges.add(ImportEdge(src=src, dst=target.rsplit(".", 1)[0], kind=kind, line=line))
        else:
            unresolved.add((src, target))

    def walk(node: ast.AST, src: str, is_package: bool, in_function: bool, type_only: bool) -> None:
        kind = EDGE_IMPORTS_TYPE_ONLY if type_only else EDGE_IMPORTS_DEFERRED if in_function else EDGE_IMPORTS
        if isinstance(node, ast.Import):
            for alias in node.names:
                if in_tree(alias.name):
                    add_edge(src, alias.name, kind, node.lineno)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(src, is_package, node.level, node.module)
                if base is None or not in_tree(base):
                    unresolved.add((src, f"{'.' * node.level}{node.module or ''}"))
                    return
            elif node.module and in_tree(node.module):
                base = node.module
            else:
                return
            for alias in node.names:
                add_edge(src, f"{base}.{alias.name}", kind, node.lineno)
            return
        if isinstance(node, ast.Call) and _is_dynamic_import_call(node):
            dynamic_sites.add(src)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            for child in ast.iter_child_nodes(node):
                walk(child, src, is_package, True, type_only)
            return
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in node.body:
                walk(child, src, is_package, in_function, True)
            for child in node.orelse:
                walk(child, src, is_package, in_function, type_only)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, src, is_package, in_function, type_only)

    for dotted in sorted(modules):
        path = ctx.repo_root / modules[dotted]
        is_package = path.name == "__init__.py"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, ValueError) as error:
            unknowns.append(f"{modules[dotted]}: {error}")
            continue
        walk(tree, dotted, is_package, in_function=False, type_only=False)

    return ImportGraph(
        modules=dict(sorted(modules.items())),
        edges=tuple(sorted(edges)),
        dynamic_import_sites=tuple(sorted(dynamic_sites)),
        unresolved=tuple(sorted(unresolved)),
        unknowns=tuple(sorted(unknowns)),
        _paths_to_modules={path: module for module, path in modules.items()},
    )


_MEMO: dict[str, ImportGraph] = {}


def get_import_graph(ctx: Context) -> ImportGraph:
    """Process-memoized graph. The CLI is one-shot, so staleness cannot occur; tests that
    mutate a tree between builds must call :func:`build_import_graph` directly."""
    key = str(ctx.repo_root.resolve())
    if key not in _MEMO:
        _MEMO[key] = build_import_graph(ctx)
    return _MEMO[key]
