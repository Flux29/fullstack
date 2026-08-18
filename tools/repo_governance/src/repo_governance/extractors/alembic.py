"""Alembic revision graph extraction.

Reads `revision` and `down_revision` from each version module's AST. Contiguity means graph
connectivity, never filename numbering: this repository's filenames skip 0017 and 0019
through 0021 and interleave a 0004_5, while the chain itself is unbroken. A policy keyed to
filenames would fire on day one and be switched off by day two.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from repo_governance.config import Context, iter_files


@dataclass
class Revision:
    id: str
    down_revision: str | list[str] | None
    file: str


@dataclass
class RevisionGraph:
    revisions: list[Revision] = field(default_factory=list)
    heads: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


def _module_string(tree: ast.Module, name: str) -> str | list[str] | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant):
            return value.value
        if isinstance(value, ast.Tuple | ast.List):
            return [item.value for item in value.elts if isinstance(item, ast.Constant)]
    return None


def extract_revisions(ctx: Context) -> RevisionGraph:
    graph = RevisionGraph()
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    if not versions.is_dir():
        graph.unknowns.append("backend/alembic/versions does not exist")
        return graph

    for path in iter_files(versions, suffixes=(".py",)):
        if path.name.startswith("__"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            graph.unknowns.append(f"could not parse {path.name}: {exc}")
            continue

        revision = _module_string(tree, "revision")
        if not isinstance(revision, str):
            graph.unknowns.append(f"{path.name} declares no string revision identifier")
            continue

        graph.revisions.append(
            Revision(
                id=revision,
                down_revision=_module_string(tree, "down_revision"),
                file=f"backend/alembic/versions/{path.name}",
            )
        )

    graph.revisions.sort(key=lambda item: item.id)
    known = {revision.id for revision in graph.revisions}

    referenced: set[str] = set()
    for revision in graph.revisions:
        down = revision.down_revision
        parents = [down] if isinstance(down, str) else list(down or [])
        if not parents:
            graph.roots.append(revision.id)
        for parent in parents:
            referenced.add(parent)
            if parent not in known:
                graph.orphans.append(f"{revision.id} descends from unknown revision {parent}")

    graph.heads = sorted(known - referenced)
    graph.roots.sort()

    # Cycle detection by iterative walk; a revision graph is small enough that clarity beats
    # cleverness here.
    parents_of: dict[str, list[str]] = {}
    for revision in graph.revisions:
        down = revision.down_revision
        parents_of[revision.id] = [down] if isinstance(down, str) else list(down or [])

    for start in sorted(known):
        seen: list[str] = []
        current = [start]
        while current:
            node = current.pop()
            if node in seen:
                graph.cycles.append([*seen, node])
                break
            seen.append(node)
            current.extend(parent for parent in parents_of.get(node, []) if parent in known)

    return graph
