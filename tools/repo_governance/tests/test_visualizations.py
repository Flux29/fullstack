"""The bounded views: every view renders self-contained, truncation is visible, and the
error paths name what they need."""

from __future__ import annotations

import pytest

from repo_governance.config import Context
from repo_governance.renderers.visualizations import (
    ViewData,
    ViewError,
    _trim,
    render_view,
    write_view,
)


def test_unknown_view_raises_with_the_known_list(real_context: Context) -> None:
    with pytest.raises(ViewError, match="architecture"):
        render_view(real_context, "nonsense", None)


def test_impact_view_requires_a_focus(real_context: Context) -> None:
    with pytest.raises(ViewError, match="--focus"):
        render_view(real_context, "impact", None)


def test_truncation_is_drawn_never_silent() -> None:
    data = ViewData(
        title="t",
        subtitle="s",
        layers=[("A", [f"a{i}" for i in range(30)]), ("B", [f"b{i}" for i in range(30)])],
        edges=[("a0", "b0")],
    )
    trimmed = _trim(data, budget=10)
    assert sum(len(nodes) for _, nodes in trimmed.layers) <= 10
    assert any("Truncated" in note for note in trimmed.notes)


@pytest.mark.parametrize(
    ("view", "focus", "markers"),
    [
        ("architecture", None, ["rag", "backend-api", "Component architecture"]),
        ("architecture", "rag", ["rag", "postgres-pgvector"]),
        ("site", "/chat", ["/chat", "proxy bypass"]),
        ("site", None, ["/rag", "Backend route modules"]),
        ("configuration", "EMBEDDING_DIMENSION", ["EMBEDDING_DIMENSION"]),
        ("migration", None, ["0027_embedding_cache", "Head: 0030_mcp_url_origin_only"]),
        ("security", None, ["assistant", "github-internal", "mcp-approval-gating-asymmetry"]),
        ("impact", "frontend/src/lib/file-api.ts", ["backend-api", "files/upload"]),
    ],
)
def test_each_view_renders_its_markers(real_context: Context, view: str, focus: str | None, markers: list[str]) -> None:
    page = render_view(real_context, view, focus)
    for marker in markers:
        assert marker in page, f"{view} view missing {marker!r}"


@pytest.mark.parametrize("view", ["architecture", "site", "security"])
def test_views_are_self_contained(real_context: Context, view: str) -> None:
    """No scripts, no external requests: the page works offline and leaks nothing."""
    page = render_view(real_context, view, None)
    assert "<script" not in page
    assert "http://" not in page and "https://" not in page


def test_write_view_lands_under_gitignored_artifacts(real_context: Context) -> None:
    relative = write_view(real_context, "architecture", "rag")
    assert relative == "artifacts/governance/architecture-rag.html"
    assert (real_context.repo_root / relative).is_file()
