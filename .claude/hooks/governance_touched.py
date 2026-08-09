"""Touched-file hook for Claude Code `PostToolUse` on Edit|Write.

Appends one JSONL entry per mutating tool call to the session state dir, giving the Stop
hook its evidence of what this turn actually changed. Whether a touched path is a *new*
file is decided later by the stop-check through git, not guessed here — this script stays
stdlib-only and must never fail a tool call: every error path swallows and exits 0.

Tested by tools/repo_governance/tests/test_hooks.py, which imports this file.
"""

from __future__ import annotations

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_ROOT = os.path.join(REPO_ROOT, ".cache", "repo-governance", "hooks")


def _rel(path_text: str) -> str | None:
    if not path_text:
        return None
    absolute = os.path.abspath(os.path.join(REPO_ROOT, path_text)) if not os.path.isabs(path_text) else path_text
    try:
        relative = os.path.relpath(absolute, REPO_ROOT)
    except ValueError:
        return None  # a different drive on Windows is by definition outside the repository
    if relative == "." or relative.startswith(".."):
        return None
    return relative.replace(os.sep, "/")


def main() -> None:
    try:
        event = json.load(sys.stdin)
        rel = _rel(str((event.get("tool_input") or {}).get("file_path") or ""))
        if rel is None:
            return  # outside the repository: scratchpad and temp files are not governed
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(event.get("session_id") or "unknown"))[:80]
        directory = os.path.join(STATE_ROOT, safe)
        os.makedirs(directory, exist_ok=True)
        entry = {"path": rel, "tool": str(event.get("tool_name") or "")}
        with open(os.path.join(directory, "touched.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
