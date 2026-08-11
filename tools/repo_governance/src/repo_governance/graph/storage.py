"""The SQLite materialization of the joined graph.

A derived cache, never an authority: it lives under ``.cache/repo-governance/``
(gitignored), is rebuilt from sources by ``governance scan``, and nothing committed is
ever produced *from* it — the reports in ``governance/graph/reports/`` compute from the
extractors directly, so a stale cache can slow a query but can never corrupt an answer.

Written atomically: built in a temporary file, then swapped in with ``os.replace``, the
same discipline every governance write uses.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from repo_governance.config import Context
from repo_governance.graph.queries import GraphData, assemble

_SCHEMA = """
CREATE TABLE nodes (
    id TEXT NOT NULL PRIMARY KEY,
    kind TEXT NOT NULL
);
CREATE TABLE edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    method TEXT NOT NULL,
    confidence TEXT NOT NULL
);
CREATE INDEX edges_src ON edges (src);
CREATE INDEX edges_dst ON edges (dst);
"""


def sqlite_path(ctx: Context) -> Path:
    return ctx.paths.cache / "repository.sqlite"


def build_sqlite(ctx: Context, data: GraphData | None = None) -> Path:
    """Materialize the joined graph. Returns the cache path."""
    data = data if data is not None else assemble(ctx)
    target = sqlite_path(ctx)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".sqlite.tmp")
    scratch.unlink(missing_ok=True)

    connection = sqlite3.connect(scratch)
    try:
        connection.executescript(_SCHEMA)
        connection.executemany("INSERT INTO nodes VALUES (?, ?)", data.nodes)
        connection.executemany(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)",
            [(e.src, e.dst, e.kind, e.line, e.method, e.confidence) for e in data.edges],
        )
        connection.commit()
    finally:
        connection.close()

    os.replace(scratch, target)
    return target
