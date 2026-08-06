"""Sync is idempotent, including across process boundaries.

The cross-process case matters on its own: Python randomizes string hashing per process, so
a set or dict iteration that leaks into output can be stable within one run and different
in the next. An in-process double render would not catch it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from repo_governance.config import Context
from repo_governance.pipeline import render_all, sync


def _sync_subprocess(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "repo_governance", "sync"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )


def test_two_syncs_produce_identical_bytes(minimal_repo: Path) -> None:
    ctx = Context.discover(minimal_repo)

    sync(ctx)
    first = {path: (minimal_repo / path).read_bytes() for path in render_all(ctx)}

    sync(ctx)
    second = {path: (minimal_repo / path).read_bytes() for path in render_all(ctx)}

    assert first == second


def test_second_sync_reports_no_change(minimal_repo: Path) -> None:
    ctx = Context.discover(minimal_repo)
    assert sync(ctx), "the first sync should write something"
    assert sync(ctx) == [], "the second sync must change nothing"


def test_sync_is_idempotent_across_processes(minimal_repo: Path) -> None:
    _sync_subprocess(minimal_repo)
    catalog = minimal_repo / "governance" / "catalog.json"
    first = catalog.read_bytes()

    result = _sync_subprocess(minimal_repo)
    assert catalog.read_bytes() == first
    assert "Already in sync" in result.stdout


def test_sync_from_a_clean_checkout_converges_immediately(minimal_repo: Path) -> None:
    """A first sync must produce the final bytes, not bytes a second sync would revise.

    The catalog describes the output set including itself, so a naive existence test makes
    the first run emit a catalog missing its own entry — non-idempotent exactly where a
    fresh CI checkout would notice.
    """
    ctx = Context.discover(minimal_repo)
    sync(ctx)
    after_first = (minimal_repo / "governance" / "catalog.json").read_bytes()

    sync(ctx)
    assert (minimal_repo / "governance" / "catalog.json").read_bytes() == after_first


def test_no_generated_file_reads_another_generated_file_from_disk(minimal_repo: Path) -> None:
    """A generated document that summarizes another must be handed the value, not read it.

    Reading it back means the first sync of a clean checkout renders against files that do
    not exist yet, and the next sync fills them in — non-idempotent precisely where a fresh
    CI checkout notices, and invisible on a developer machine where the files already exist.
    Both the catalog and the summary have fallen into this; the test exists so a third does
    not.
    """
    ctx = Context.discover(minimal_repo)

    first_pass = render_all(ctx)
    sync(ctx)
    second_pass = render_all(ctx)

    assert first_pass == second_pass, (
        "rendering changed once the generated files existed on disk, which means something "
        "in the render path is reading its own output"
    )


def test_catalog_lists_itself(minimal_repo: Path) -> None:
    from repo_governance.io_atomic import read_json

    ctx = Context.discover(minimal_repo)
    sync(ctx)
    catalog = read_json(minimal_repo / "governance" / "catalog.json")
    paths = {entry["path"] for entry in catalog["entries"]}
    assert "governance/catalog.json" in paths


def test_real_repository_is_in_sync(real_context: Context) -> None:
    """The committed governance tree matches what the current sources produce."""
    from repo_governance.pipeline import compare_generated

    assert compare_generated(real_context) == []
