"""The joined graph: assembly, cycle and orphan analysis, shortest paths, SQLite cache.

Synthetic trees pin each classification mechanism; the real-tree tests pin the two known
cycles and the verified orphan set, so a change to either shows up as a diff here as well
as in the committed reports.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repo_governance.config import Context
from repo_governance.graph.queries import (
    assemble,
    build_cycles_report,
    build_orphans_report,
    shortest_path,
    strongly_connected_components,
)
from repo_governance.graph.storage import build_sqlite
from repo_governance.io_atomic import canonical_json


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scc_finds_a_cycle_and_ignores_a_chain() -> None:
    modules = {"a", "b", "c", "d"}
    edges = [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d")]
    assert strongly_connected_components(modules, edges) == [["a", "b"]]


def test_cycle_closed_through_deferred_import_is_not_import_time(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/one.py", "import app.two\n")
    _write(
        minimal_repo,
        "backend/app/two.py",
        "def call():\n    import app.one\n",
    )

    report = build_cycles_report(Context.discover(minimal_repo))
    assert report["python"] == [{"modules": ["app.one", "app.two"], "import_time": False}]


def test_cycle_closed_at_module_scope_is_import_time(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/one.py", "import app.two\n")
    _write(minimal_repo, "backend/app/two.py", "import app.one\n")

    report = build_cycles_report(Context.discover(minimal_repo))
    assert report["python"] == [{"modules": ["app.one", "app.two"], "import_time": True}]


def test_package_roots_and_dynamic_subtrees_are_excluded_not_orphaned(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/commands/__init__.py", "from importlib import import_module\nimport_module('x')\n")
    _write(minimal_repo, "backend/app/commands/seed.py", "")
    _write(minimal_repo, "backend/app/dead.py", "")

    report = build_orphans_report(Context.discover(minimal_repo))
    assert report["python"]["orphans"] == ["app.dead"]
    assert report["python"]["excluded"]["package-root"] == 1
    assert report["python"]["excluded"]["dynamic-subtree"] == 1


def test_next_convention_and_test_files_are_excluded_not_orphaned(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", "{}")
    _write(minimal_repo, "frontend/src/app/page.tsx", "")
    _write(minimal_repo, "frontend/src/lib/utils.test.ts", "")
    _write(minimal_repo, "frontend/src/types/global.d.ts", "")
    _write(minimal_repo, "frontend/src/components/dead.tsx", "")

    report = build_orphans_report(Context.discover(minimal_repo))
    assert report["typescript"]["orphans"] == ["frontend/src/components/dead.tsx"]
    assert report["typescript"]["excluded"]["framework-invoked"] == 1
    assert report["typescript"]["excluded"]["test-module"] == 1
    assert report["typescript"]["excluded"]["ambient-types"] == 1


def test_shortest_path_is_found_and_absence_is_none() -> None:
    edges = [("a", "b"), ("b", "c"), ("a", "c"), ("c", "d")]
    assert shortest_path(edges, "a", "d") == ["a", "c", "d"]
    assert shortest_path(edges, "d", "a") is None
    assert shortest_path(edges, "a", "a") == ["a"]


def test_sqlite_cache_matches_the_assembled_graph(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/one.py", "import app.two\n")
    _write(minimal_repo, "backend/app/two.py", "")
    ctx = Context.discover(minimal_repo)

    data = assemble(ctx)
    cache = build_sqlite(ctx, data)

    connection = sqlite3.connect(cache)
    try:
        nodes = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        provenance = connection.execute("SELECT DISTINCT method, confidence FROM edges").fetchall()
    finally:
        connection.close()
    assert nodes == len(data.nodes)
    assert edges == len(data.edges)
    assert provenance == [("ast", "high")]


def test_real_tree_cycles_are_the_two_known_ones(real_context: Context) -> None:
    report = build_cycles_report(real_context)
    members = [set(entry["modules"]) for entry in report["python"]]
    assert {"app.services.rag.connectors", "app.services.rag.connectors.google_drive"} <= members[0] | members[1]
    assert report["typescript"] == []


def test_real_tree_orphans_are_the_verified_set_not_the_classified_ones(real_context: Context) -> None:
    """The ratchet moved on 2026-08-11: the debt sweep deleted every verified python orphan
    (the research shim went, the email providers got real match arms) and every verified
    typescript orphan except the shadcn library stock, which stays by design."""
    report = build_orphans_report(real_context)
    python = set(report["python"]["orphans"])
    assert python == set()

    typescript = set(report["typescript"]["orphans"])
    assert typescript == {
        "frontend/src/components/ui/radio-group.tsx",
        "frontend/src/components/ui/scroll-area.tsx",
    }
    assert not any(module.endswith("/page.tsx") for module in typescript)


def test_reports_are_deterministic(real_context: Context) -> None:
    assert canonical_json(build_cycles_report(real_context)) == canonical_json(build_cycles_report(real_context))
    assert canonical_json(build_orphans_report(real_context)) == canonical_json(build_orphans_report(real_context))
