"""Read-only git queries.

Governance reads git; it never writes to it. Every command here is non-mutating, and every
one degrades to an explicit `None` when git is unavailable or the path is not a repository —
a parser failure is reported as unknown, never interpreted as "nothing changed".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 30


def _run(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def is_repository(root: Path) -> bool:
    return _run(["rev-parse", "--git-dir"], root) is not None


def working_tree_changes(root: Path) -> list[str] | None:
    """Repo-relative POSIX paths changed in the working tree or index, or None if unknown."""
    output = _run(["status", "--porcelain", "-z"], root)
    if output is None:
        return None

    paths: list[str] = []
    fields = [field for field in output.split("\0") if field]
    index = 0
    while index < len(fields):
        entry = fields[index]
        status, _, path = entry.partition(" ")
        path = path.strip()
        # Renames emit the source path as a following field.
        if status and status[0] == "R" and index + 1 < len(fields):
            index += 1
            paths.append(fields[index].strip())
        if path:
            paths.append(path)
        index += 1

    return sorted({item.strip('"') for item in paths if item})


def changed_since(root: Path, ref: str) -> list[str] | None:
    """Repo-relative POSIX paths changed between `ref` and HEAD, or None if unknown."""
    output = _run(["diff", "--name-only", f"{ref}...HEAD"], root)
    if output is None:
        return None
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def file_at_ref(root: Path, ref: str, relative_path: str) -> str | None:
    """File contents at a ref, or None when git or the path is unavailable."""
    return _run(["show", f"{ref}:{relative_path}"], root)


def head_commit(root: Path) -> str | None:
    output = _run(["rev-parse", "HEAD"], root)
    return output.strip() if output else None


def is_clean(root: Path) -> bool | None:
    changes = working_tree_changes(root)
    if changes is None:
        return None
    return not changes
