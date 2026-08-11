"""The TypeScript module graph over the frontend application.

The frontend counterpart to ``extractors.imports``, and a deliberate reuse of it: the
graph container, the edge dataclass, and the IMPORTS / IMPORTS_TYPE_ONLY /
IMPORTS_DEFERRED vocabulary are language-agnostic, so this module implements only what is
TypeScript-specific — the walk, the statement regexes, and specifier resolution. Modules
are named by repo-relative POSIX path (``frontend/src/lib/api-client.ts``), the
``ts-module`` node kind in ``governance/graph/node-types.json``.

**Strategy: targeted regex, not a parser** — the same stance ``typescript_ast`` states,
applied to the one shape ES modules make highly regular: the import statement. The
safeguard that makes it acceptable is unchanged: a non-match is recorded, never skipped.
An in-tree specifier that resolves to no file lands in ``unresolved``; an unreadable file
lands in ``unknowns``; a dynamic ``import()`` whose argument is not a string literal lands
in ``dynamic_import_sites``. Package imports are skipped silently — they are outside the
tree, not uncertainty about it. So are asset imports (styles, JSON, images), which are not
modules. Bare ``baseUrl`` specifiers (``src/lib/x`` with no alias) would be misread as
packages; the codebase imports through ``@/`` and relative paths, and a rising
unresolved-rate is the signal that this shortcut stopped holding.

The regex-vs-parser decision stays evidence-based: ``summarize_uncertainty`` reports the
rates, and growth there is the recorded justification for a real parser later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.extractors.imports import (
    EDGE_IMPORTS,
    EDGE_IMPORTS_DEFERRED,
    EDGE_IMPORTS_TYPE_ONLY,
    ImportEdge,
    ImportGraph,
)
from repo_governance.io_atomic import relative_posix

NODE_TS_MODULE = "ts-module"

PACKAGE_DIR = "frontend/src"
TSCONFIG = "frontend/tsconfig.json"

#: `import ... from "spec"` — default, named, namespace, and mixed clauses; the clause
#: character class includes whitespace so multi-line braces match without DOTALL.
STATIC_FROM = re.compile(
    r"^[ \t]*import\s+(?P<type_only>type\s+)?[\w$*{},\s]*?from\s*['\"](?P<spec>[^'\"]+)['\"]",
    re.MULTILINE,
)
#: `import "spec"` — a side-effect import is still executed at module scope.
SIDE_EFFECT = re.compile(r"^[ \t]*import\s*['\"](?P<spec>[^'\"]+)['\"]", re.MULTILINE)
#: `export ... from "spec"` — a re-export imports the source module.
EXPORT_FROM = re.compile(
    r"^[ \t]*export\s+(?P<type_only>type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[\w$,\s]*?\})\s*from\s*['\"](?P<spec>[^'\"]+)['\"]",
    re.MULTILINE,
)
#: `import("spec")` with a literal argument — lazy, but still a call path.
DYNAMIC_LITERAL = re.compile(r"\bimport\s*\(\s*['\"](?P<spec>[^'\"]+)['\"]\s*\)")
#: `import(expr)` with anything else — an edge the graph cannot see.
DYNAMIC_OPAQUE = re.compile(r"\bimport\s*\(\s*(?!['\"])[^)]")

#: Imports that are content, not modules: resolving them adds noise, not edges.
ASSET_SUFFIXES = (".css", ".scss", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")

CANDIDATE_SUFFIXES = (".ts", ".tsx", "/index.ts", "/index.tsx")


def _load_aliases(ctx: Context, unknowns: list[str]) -> list[tuple[str, str]]:
    """Wildcard path aliases from tsconfig, as (specifier prefix, repo-relative prefix).

    A missing or unparseable tsconfig is recorded, and aliased specifiers then land in
    ``unresolved`` rather than being guessed at.
    """
    path = ctx.repo_root / TSCONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        unknowns.append(f"{TSCONFIG}: {error}; aliased specifiers will be unresolved")
        return []

    aliases: list[tuple[str, str]] = []
    for pattern, targets in (config.get("compilerOptions", {}).get("paths", {}) or {}).items():
        if not pattern.endswith("*") or not targets:
            continue
        target = str(targets[0]).removeprefix("./").removesuffix("*")
        aliases.append((pattern.removesuffix("*"), f"frontend/{target}"))
    return sorted(aliases, key=lambda item: -len(item[0]))


def _normalize(base_dir: str, spec: str) -> str:
    """Collapse `.` and `..` segments of a relative specifier against the importer's directory."""
    parts: list[str] = base_dir.split("/") if base_dir else []
    for segment in spec.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                return spec  # Escapes the repository; left as-is to land in unresolved.
            parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)


def _candidates(base: str) -> list[str]:
    if base.endswith((".ts", ".tsx")):
        return [base]
    if base.endswith(".js"):  # ESM-style extension on a TS source.
        base = base[: -len(".js")]
    return [base + suffix for suffix in CANDIDATE_SUFFIXES]


def build_ts_import_graph(ctx: Context, *, package_dir: str = PACKAGE_DIR) -> ImportGraph:
    """Extract the module inventory and typed import edges for the frontend tree."""
    root = ctx.repo_root / Path(package_dir)

    modules: dict[str, str] = {}
    for path in iter_files(root, suffixes=(".ts", ".tsx")):
        rel = relative_posix(path, ctx.repo_root)
        modules[rel] = rel

    edges: set[ImportEdge] = set()
    dynamic_sites: set[str] = set()
    unresolved: set[tuple[str, str]] = set()
    unknowns: list[str] = []
    aliases = _load_aliases(ctx, unknowns)

    def resolve(importer: str, spec: str) -> str | None:
        """A module path, or None when the specifier is outside the graph's scope.

        Raises LookupError for an in-tree specifier that resolves to no module, so the
        caller records it as uncertainty rather than dropping it.
        """
        base: str | None = None
        for prefix, target in aliases:
            if spec.startswith(prefix):
                base = target + spec[len(prefix) :]
                break
        if base is None and spec.startswith("."):
            base = _normalize(importer.rsplit("/", 1)[0], spec)
        if base is None and spec.startswith("@/"):
            # "@/" is never an npm scope (scopes cannot be empty): this is the in-tree
            # alias with no tsconfig mapping for it, which is uncertainty, not a package.
            raise LookupError(spec)
        if base is None:
            return None  # A package import: outside the tree, not uncertainty.
        if base.endswith(ASSET_SUFFIXES):
            return None  # Content, not a module.
        for candidate in _candidates(base):
            if candidate in modules:
                return candidate
        raise LookupError(spec)

    def add(importer: str, spec: str, kind: str, line: int) -> None:
        try:
            target = resolve(importer, spec)
        except LookupError:
            unresolved.add((importer, spec))
            return
        if target is not None and target != importer:
            edges.add(ImportEdge(src=importer, dst=target, kind=kind, line=line))

    for rel in sorted(modules):
        try:
            text = (ctx.repo_root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            unknowns.append(f"{rel}: {error}")
            continue

        def line_of(offset: int) -> int:
            return text.count("\n", 0, offset) + 1

        for pattern in (STATIC_FROM, EXPORT_FROM):
            for match in pattern.finditer(text):
                kind = EDGE_IMPORTS_TYPE_ONLY if match.group("type_only") else EDGE_IMPORTS
                add(rel, match.group("spec"), kind, line_of(match.start()))
        for match in SIDE_EFFECT.finditer(text):
            add(rel, match.group("spec"), EDGE_IMPORTS, line_of(match.start()))
        for match in DYNAMIC_LITERAL.finditer(text):
            add(rel, match.group("spec"), EDGE_IMPORTS_DEFERRED, line_of(match.start()))
        if DYNAMIC_OPAQUE.search(text):
            dynamic_sites.add(rel)

    return ImportGraph(
        modules=dict(sorted(modules.items())),
        edges=tuple(sorted(edges)),
        dynamic_import_sites=tuple(sorted(dynamic_sites)),
        unresolved=tuple(sorted(unresolved)),
        unknowns=tuple(sorted(unknowns)),
        _paths_to_modules=dict(modules),
    )


def summarize_uncertainty(graph: ImportGraph) -> dict[str, float | int]:
    """The evidence the regex-vs-parser decision is judged on. A rising unresolved rate
    means the statement shapes stopped being regular enough for regex."""
    edge_count = len(graph.edges)
    unresolved_count = len(graph.unresolved)
    denominator = edge_count + unresolved_count
    return {
        "modules": len(graph.modules),
        "edges": edge_count,
        "unresolved": unresolved_count,
        "unknown_files": len(graph.unknowns),
        "dynamic_import_sites": len(graph.dynamic_import_sites),
        "unresolved_rate": round(unresolved_count / denominator, 4) if denominator else 0.0,
    }


#: A full frontend API path literal — `fetch("/api/files/upload")` and friends.
API_PATH_LITERAL = re.compile(r"[`\"'](/api/[^`\"'\s?]+)")
#: A bare resource path handed to the shared client, which prefixes `/api` at runtime:
#: `apiClient.get("/conversations")`.
API_CLIENT_CALL = re.compile(r"\bapiClient\.(?:get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*[`\"'](/[^`\"'\s?]+)")
_INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def called_api_paths(ctx: Context, modules: list[str]) -> list[str]:
    """The frontend API paths the given modules call, template-normalized.

    Regex over exactly the named files, so the cost is bounded by the caller's module set.
    Unreadable files contribute nothing here — the import graph already reported them.
    """
    paths: set[str] = set()
    for module in modules:
        try:
            text = (ctx.repo_root / module).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in API_PATH_LITERAL.finditer(text):
            paths.add(_INTERPOLATION.sub("{param}", match.group(1)).rstrip("/"))
        for match in API_CLIENT_CALL.finditer(text):
            paths.add("/api" + _INTERPOLATION.sub("{param}", match.group(1)).rstrip("/"))
    return sorted(paths)


_MEMO: dict[str, ImportGraph] = {}


def get_ts_import_graph(ctx: Context) -> ImportGraph:
    """Process-memoized graph, same contract as :func:`imports.get_import_graph`."""
    key = str(ctx.repo_root.resolve())
    if key not in _MEMO:
        _MEMO[key] = build_ts_import_graph(ctx)
    return _MEMO[key]
