"""The TypeScript import-graph extractor.

Synthetic trees pin each mechanism the frontend depends on — the tsconfig alias, relative
resolution with parent segments, index-file resolution, re-exports, type-only imports,
dynamic imports both literal and opaque — and the real-tree tests pin the properties that
make the graph trustworthy: full inventory, readable files, a bounded unresolved rate, and
deterministic serialization.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_governance.config import Context
from repo_governance.extractors.imports import (
    EDGE_IMPORTS,
    EDGE_IMPORTS_DEFERRED,
    EDGE_IMPORTS_TYPE_ONLY,
    ImportGraph,
)
from repo_governance.extractors.ts_imports import build_ts_import_graph, summarize_uncertainty
from repo_governance.io_atomic import canonical_json

TSCONFIG = json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}})


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _graph(minimal_repo: Path, *, tsconfig: str | None = TSCONFIG) -> ImportGraph:
    if tsconfig is not None:
        _write(minimal_repo, "frontend/tsconfig.json", tsconfig)
    return build_ts_import_graph(Context.discover(minimal_repo))


def _edge_set(graph: ImportGraph) -> set[tuple[str, str, str]]:
    return {(edge.src, edge.dst, edge.kind) for edge in graph.edges}


def test_alias_import_resolves_through_tsconfig_paths(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/components/chat/panel.tsx", 'import { api } from "@/lib/api-client";\n')
    _write(minimal_repo, "frontend/src/lib/api-client.ts")

    graph = _graph(minimal_repo)
    assert (
        "frontend/src/components/chat/panel.tsx",
        "frontend/src/lib/api-client.ts",
        EDGE_IMPORTS,
    ) in _edge_set(graph)
    assert graph.unresolved == ()


def test_relative_import_with_parent_segments_resolves(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/components/chat/panel.tsx", 'import { helper } from "../../lib/util";\n')
    _write(minimal_repo, "frontend/src/lib/util.ts")

    graph = _graph(minimal_repo)
    assert ("frontend/src/components/chat/panel.tsx", "frontend/src/lib/util.ts", EDGE_IMPORTS) in _edge_set(graph)
    assert graph.unresolved == ()


def test_directory_import_resolves_to_index_file(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/app/page.tsx", 'import { Panel } from "@/components";\n')
    _write(minimal_repo, "frontend/src/components/index.tsx")

    graph = _graph(minimal_repo)
    assert ("frontend/src/app/page.tsx", "frontend/src/components/index.tsx", EDGE_IMPORTS) in _edge_set(graph)


def test_re_export_is_an_import_edge(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/stores/index.ts", 'export { useChat } from "./chat-store";\n')
    _write(minimal_repo, "frontend/src/stores/chat-store.ts")

    graph = _graph(minimal_repo)
    assert ("frontend/src/stores/index.ts", "frontend/src/stores/chat-store.ts", EDGE_IMPORTS) in _edge_set(graph)


def test_type_only_import_is_classified_type_only(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/lib/api.ts", 'import type { User } from "./types";\n')
    _write(minimal_repo, "frontend/src/lib/types.ts")

    graph = _graph(minimal_repo)
    assert ("frontend/src/lib/api.ts", "frontend/src/lib/types.ts", EDGE_IMPORTS_TYPE_ONLY) in _edge_set(graph)


def test_multiline_import_clause_matches(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "frontend/src/lib/api.ts",
        'import {\n  one,\n  two,\n} from "./util";\n',
    )
    _write(minimal_repo, "frontend/src/lib/util.ts")

    graph = _graph(minimal_repo)
    assert ("frontend/src/lib/api.ts", "frontend/src/lib/util.ts", EDGE_IMPORTS) in _edge_set(graph)


def test_dynamic_literal_import_is_deferred_and_opaque_is_a_site(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "frontend/src/app/page.tsx",
        'const Chart = () => import("./chart");\nconst lazy = (name: string) => import(name);\n',
    )
    _write(minimal_repo, "frontend/src/app/chart.tsx")

    graph = _graph(minimal_repo)
    assert ("frontend/src/app/page.tsx", "frontend/src/app/chart.tsx", EDGE_IMPORTS_DEFERRED) in _edge_set(graph)
    assert graph.dynamic_import_sites == ("frontend/src/app/page.tsx",)


def test_package_and_asset_imports_are_skipped_silently(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "frontend/src/app/page.tsx",
        'import React from "react";\nimport "./globals.css";\nimport data from "./copy.json";\n',
    )

    graph = _graph(minimal_repo)
    assert graph.edges == ()
    assert graph.unresolved == ()


def test_in_tree_specifier_with_no_module_is_recorded_unresolved(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/app/page.tsx", 'import { gone } from "@/lib/deleted";\n')

    graph = _graph(minimal_repo)
    assert ("frontend/src/app/page.tsx", "@/lib/deleted") in graph.unresolved


def test_unreadable_file_is_an_unknown_not_an_absence(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", TSCONFIG)
    target = minimal_repo / "frontend" / "src" / "broken.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe invalid utf-8 \xff")

    graph = build_ts_import_graph(Context.discover(minimal_repo))
    assert any(entry.startswith("frontend/src/broken.ts") for entry in graph.unknowns)


def test_missing_tsconfig_is_recorded_and_alias_lands_unresolved(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/src/app/page.tsx", 'import { api } from "@/lib/api-client";\n')
    _write(minimal_repo, "frontend/src/lib/api-client.ts")

    graph = _graph(minimal_repo, tsconfig=None)
    assert any("tsconfig" in entry for entry in graph.unknowns)
    assert ("frontend/src/app/page.tsx", "@/lib/api-client") in graph.unresolved


def test_real_tree_inventory_is_full_and_readable(real_context: Context) -> None:
    graph = build_ts_import_graph(real_context)
    summary = summarize_uncertainty(graph)
    assert summary["modules"] > 100, "frontend/src holds far more modules than this"
    assert summary["edges"] > 100
    assert summary["unknown_files"] == 0, graph.unknowns


def test_real_tree_unresolved_rate_is_bounded(real_context: Context) -> None:
    """The evidence gate for the regex strategy: if this rate climbs, the recorded
    remedy is a real parser, not a looser assertion."""
    graph = build_ts_import_graph(real_context)
    summary = summarize_uncertainty(graph)
    assert summary["unresolved_rate"] < 0.02, sorted(graph.unresolved)


def test_serialization_is_deterministic(real_context: Context) -> None:
    first = canonical_json(build_ts_import_graph(real_context).as_payload())
    second = canonical_json(build_ts_import_graph(real_context).as_payload())
    assert first == second
