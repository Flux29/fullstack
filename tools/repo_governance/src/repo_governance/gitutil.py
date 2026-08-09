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


def _porcelain_entries(root: Path) -> list[tuple[str, str]] | None:
    """`(status, path)` pairs from `git status --porcelain -z`, or None if unknown.

    The status field is fixed-width — two characters, then a space, then the path — so it is
    sliced rather than split on the first space. Splitting loses the path of every entry
    whose index column is blank, which is every unstaged modification.
    """
    output = _run(["status", "--porcelain", "-z"], root)
    if output is None:
        return None

    entries: list[tuple[str, str]] = []
    fields = [field for field in output.split("\0") if field]
    index = 0
    while index < len(fields):
        entry = fields[index]
        status, path = entry[:2], entry[3:]  # `-z` emits paths verbatim, never quoted.
        # Renames emit the source path as a following field.
        if status[0] == "R" and index + 1 < len(fields):
            index += 1
            entries.append((status, fields[index]))
        if path:
            entries.append((status, path))
        index += 1

    return entries


def working_tree_changes(root: Path) -> list[str] | None:
    """Repo-relative POSIX paths changed in the working tree or index, or None if unknown."""
    entries = _porcelain_entries(root)
    if entries is None:
        return None
    return sorted({path for _, path in entries})


def untracked_files(root: Path) -> list[str] | None:
    """Repo-relative POSIX paths git reports as untracked, or None if unknown.

    Ignored paths never appear: porcelain marks `??` only for what no ignore rule covers.
    Untracked directories stay collapsed, so a scratch directory is one path, not everything in it.
    """
    entries = _porcelain_entries(root)
    if entries is None:
        return None
    return sorted({path for status, path in entries if status == "??"})


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


def tracked_files(root: Path) -> list[str] | None:
    """Repo-relative POSIX paths git tracks, or None if unknown."""
    output = _run(["ls-files", "-z"], root)
    if output is None:
        return None
    return sorted({field for field in output.split("\0") if field})


def toplevel(root: Path) -> Path | None:
    """The repository top-level git actually answers for, or None if unknown.

    git walks upward from `root`, so a directory nested inside some other repository gets
    that repository's answers. Callers comparing porcelain output against `root`-relative
    paths must check this is really `root` before trusting the result.
    """
    output = _run(["rev-parse", "--show-toplevel"], root)
    if not output:
        return None
    return Path(output.strip()).resolve()


def is_clean(root: Path) -> bool | None:
    changes = working_tree_changes(root)
    if changes is None:
        return None
    return not changes
