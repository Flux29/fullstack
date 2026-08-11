"""The import-graph extractor.

Synthetic trees pin each mechanism the real tree depends on — namespace packages,
relative-import resolution, TYPE_CHECKING and function-local classification, dynamic
import sites, the flat-module/package name collision — and the real-tree tests pin the
properties that make the graph trustworthy: full inventory, zero unresolved imports,
deterministic serialization.
"""

from __future__ import annotations

from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.extractors.imports import (
    EDGE_IMPORTS,
    EDGE_IMPORTS_DEFERRED,
    EDGE_IMPORTS_TYPE_ONLY,
    ImportGraph,
    build_import_graph,
)
from repo_governance.io_atomic import canonical_json


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _graph(minimal_repo: Path) -> ImportGraph:
    return build_import_graph(Context.discover(minimal_repo))


def _edge_set(graph: ImportGraph) -> set[tuple[str, str, str]]:
    return {(edge.src, edge.dst, edge.kind) for edge in graph.edges}


def test_build_graph_resolves_absolute_import_to_module_edge(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/user.py", "from app.repositories import user as user_repo\n")
    _write(minimal_repo, "backend/app/repositories/user.py")

    graph = _graph(minimal_repo)
    assert ("app.services.user", "app.repositories.user", EDGE_IMPORTS) in _edge_set(graph)
    assert graph.unresolved == ()


def test_build_graph_resolves_relative_import_with_level_against_package(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/core/__init__.py", "from .config import settings\n")
    _write(minimal_repo, "backend/app/core/config.py")
    _write(minimal_repo, "backend/app/api/deps.py", "from ..core import config\n")

    graph = _graph(minimal_repo)
    edges = _edge_set(graph)
    assert ("app.core", "app.core.config", EDGE_IMPORTS) in edges
    assert ("app.api.deps", "app.core.config", EDGE_IMPORTS) in edges
    assert graph.unresolved == ()


def test_build_graph_walks_namespace_packages_without_init_files(minimal_repo: Path) -> None:
    """app/services/ has no __init__.py in the real tree; discovery must not require one."""
    _write(minimal_repo, "backend/app/services/conversation.py", "from app.services.session import x\n")
    _write(minimal_repo, "backend/app/services/session.py")

    graph = _graph(minimal_repo)
    assert "app.services.conversation" in graph.modules
    assert ("app.services.conversation", "app.services.session", EDGE_IMPORTS) in _edge_set(graph)


def test_build_graph_marks_type_checking_imports_type_only(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/services/tool.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.services.session import Session\n",
    )
    _write(minimal_repo, "backend/app/services/session.py")

    graph = _graph(minimal_repo)
    assert ("app.services.tool", "app.services.session", EDGE_IMPORTS_TYPE_ONLY) in _edge_set(graph)


def test_build_graph_marks_function_local_imports_deferred(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/services/upload.py",
        "def ingest():\n    from app.services.session import x\n    return x\n",
    )
    _write(minimal_repo, "backend/app/services/session.py")

    graph = _graph(minimal_repo)
    assert ("app.services.upload", "app.services.session", EDGE_IMPORTS_DEFERRED) in _edge_set(graph)


def test_build_graph_records_dynamic_import_sites_without_fabricating_edges(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/commands/__init__.py",
        "import importlib\nimport pkgutil\n"
        "for _, name, _ in pkgutil.iter_modules(['x']):\n"
        "    importlib.import_module(f'app.commands.{name}')\n",
    )

    graph = _graph(minimal_repo)
    assert graph.dynamic_import_sites == ("app.commands",)


def test_bare_import_module_call_is_a_dynamic_site(minimal_repo: Path) -> None:
    """`from importlib import import_module` then a bare call — the rag facade's PEP 562
    form, which the attribute-only detector missed until phase 6."""
    _write(
        minimal_repo,
        "backend/app/services/rag/__init__.py",
        "from importlib import import_module\n"
        "def __getattr__(name):\n"
        "    return import_module(f'app.services.rag.{name}')\n",
    )

    graph = _graph(minimal_repo)
    assert graph.dynamic_import_sites == ("app.services.rag",)
    assert not any(edge.src == "app.commands" for edge in graph.edges)
    assert graph.unresolved == ()


def test_build_graph_ignores_import_text_inside_docstrings(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/core/crypto.py",
        '"""Usage:\n\n    from app.core.missing import derive_key\n"""\n',
    )

    graph = _graph(minimal_repo)
    assert graph.edges == ()
    assert graph.unresolved == ()


def test_build_graph_distinguishes_flat_module_from_sibling_package(minimal_repo: Path) -> None:
    """app.services.rag_document (module) and app.services.rag.documents (package member)
    must never collapse into one node."""
    _write(minimal_repo, "backend/app/services/rag_document.py")
    _write(minimal_repo, "backend/app/services/rag/documents.py")
    _write(
        minimal_repo,
        "backend/app/api/deps.py",
        "from app.services.rag_document import RagDocumentService\n"
        "from app.services.rag.documents import DocumentStore\n",
    )

    graph = _graph(minimal_repo)
    edges = _edge_set(graph)
    assert ("app.api.deps", "app.services.rag_document", EDGE_IMPORTS) in edges
    assert ("app.api.deps", "app.services.rag.documents", EDGE_IMPORTS) in edges
    assert graph.path_for_module("app.services.rag_document") == "backend/app/services/rag_document.py"
    assert graph.path_for_module("app.services.rag.documents") == "backend/app/services/rag/documents.py"


def test_build_graph_reports_parse_error_as_unknown_not_absence(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/broken.py", "def broken(:\n")

    graph = _graph(minimal_repo)
    assert "app.services.broken" in graph.modules
    assert len(graph.unknowns) == 1
    assert graph.unknowns[0].startswith("backend/app/services/broken.py:")


def test_reverse_closure_respects_depth_and_node_bounds_and_reports_truncation(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/a.py", "import app.b\n")
    _write(minimal_repo, "backend/app/b.py", "import app.c\n")
    _write(minimal_repo, "backend/app/c.py", "import app.d\n")
    _write(minimal_repo, "backend/app/d.py")

    graph = _graph(minimal_repo)
    one_hop, truncated = graph.reverse_closure({"app.d"}, max_depth=1, max_nodes=50)
    assert (one_hop, truncated) == ({"app.c"}, False)

    full, truncated = graph.reverse_closure({"app.d"}, max_depth=5, max_nodes=50)
    assert (full, truncated) == ({"app.a", "app.b", "app.c"}, False)

    bounded, truncated = graph.reverse_closure({"app.d"}, max_depth=5, max_nodes=1)
    assert (bounded, truncated) == ({"app.c"}, True)


def test_build_graph_twice_produces_identical_serialization(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/user.py", "from app.repositories import user\n")
    _write(minimal_repo, "backend/app/repositories/user.py")

    first = canonical_json(_graph(minimal_repo).as_payload())
    second = canonical_json(_graph(minimal_repo).as_payload())
    assert first == second


def test_real_tree_graph_inventories_every_app_python_file(real_context: Context) -> None:
    graph = build_import_graph(real_context)
    expected = iter_files(real_context.repo_root / "backend" / "app", suffixes=(".py",))
    assert len(graph.modules) == len(expected)
    assert graph.unknowns == ()


def test_real_tree_graph_resolves_all_absolute_app_imports(real_context: Context) -> None:
    """Every app.* import in the tree resolves to an inventoried module. A failure here is
    a finding about the tree (or the resolver), never a reason to relax the assertion."""
    graph = build_import_graph(real_context)
    assert graph.unresolved == ()
    # app.commands discovers via pkgutil; app.services.rag lazy-loads via PEP 562 —
    # the bare-name import_module form the detector learned in phase 6.
    assert graph.dynamic_import_sites == ("app.commands", "app.services.rag")
