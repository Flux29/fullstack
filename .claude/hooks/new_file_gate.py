"""PreToolUse gate for new-file creation (Write and NotebookEdit).

Creating a new file is a last resort in this repository - extend an existing module
first. This gate asks for confirmation on a genuinely new file while staying silent
where new files are the norm: the sanctioned untracked locations, frontend test files
beside their source, and the session scratchpad.

Stdlib-only, like every script under .claude/hooks/ - a uv cold start would be paid on
every gated tool call. Fails open: a broken gate must degrade to the pre-gate world,
never block work. Behaviour is intentionally a superset of the old inline `python -c`
gate in settings.json: everything it allowed is still allowed.

NotebookEdit carries its target as `notebook_path`, not `file_path` - reading only
`file_path` would silently allow every notebook write, which is exactly the bypass this
script was extracted to close.
"""

from __future__ import annotations

import json
import os
import sys

#: Mirrors SANCTIONED_UNTRACKED_PREFIXES in tools/repo_governance/checks/process.py,
#: narrowed for .claude: new skills are expected, new hooks/settings are worth an ask.
SANCTIONED_PREFIXES = (
    "governance/history/",
    "backend/alembic/versions/",
    "backend/tests/",
    "tools/repo_governance/tests/",
    "frontend/e2e/",
    "docs/",
    ".claude/skills/",
)

#: Frontend unit tests sit beside their source - recognised by name, not location.
SANCTIONED_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")


def _emit(decision: str, reason: str | None = None) -> None:
    output: dict = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        output["permissionDecisionReason"] = reason
    print(json.dumps({"hookSpecificOutput": output}))


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    try:
        event = json.load(sys.stdin)
        tool_input = event.get("tool_input") or {}
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not raw or os.path.exists(raw):
            _emit("allow")
            return

        normalized = raw.replace("\\", "/")
        repo = _repo_root().replace("\\", "/")
        relative = os.path.relpath(raw, repo).replace("\\", "/") if repo else normalized

        if relative.startswith(".."):
            # Outside the repository: ungoverned, except the scratchpad stays explicit.
            if "/Temp/claude/" in normalized:
                _emit("allow")
            else:
                _emit("ask", f"NEW file outside the repository: {raw}")
            return

        if relative.startswith(SANCTIONED_PREFIXES):
            _emit("allow")
            return
        if relative.startswith("frontend/") and relative.endswith(SANCTIONED_SUFFIXES):
            _emit("allow")
            return

        _emit(
            "ask",
            f"NEW file: {relative} - prefer extending an existing module; create only if genuinely needed.",
        )
    except Exception:  # noqa: BLE001 - a broken gate must fail open, never block work
        _emit("allow")


if __name__ == "__main__":
    main()
