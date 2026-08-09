"""Read-gate hook for Claude Code `PreToolUse` on Read|Glob|Grep.

Reads one sync-generated artifact and answers in milliseconds, without uv or the
governance package. Steering, not a security boundary: it denies only on positive
certainty and fails open — visibly — on everything else.

Decision ladder (a deny requires every rung):
  the event parses -> the artifact loads and its schema_version is supported -> the
  target resolves inside the repository -> the target is confidently outside the allowed
  repo surface, or is a bulk enumeration rooted in a gated corpus root.
Anything less: allow, record a reason-coded degraded entry in the session state dir, and
surface it once per reason via systemMessage. The session-context and stop-check hooks
report accumulated degradations, so a broken gate is announced, never silent.

Modes come from the artifact's `default_modes`, overridden per session by the
GOVERNANCE_GATE environment variable (off|warn|deny) — set by the user, outside agent
reach. Tested by tools/repo_governance/tests/test_hooks.py, which imports this file.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACT_PATH = os.path.join(REPO_ROOT, "governance", "manifests", "generated", "read-surface.json")
STATE_ROOT = os.path.join(REPO_ROOT, ".cache", "repo-governance", "hooks")
SUPPORTED_SCHEMA_VERSION = 1
STATE_ENTRY_CAP = 500

QUERY_INSTEAD = (
    "The governance corpus is queried, not bulk-read: `make governance-context PATHS=... TASK=...`, "
    "`make governance-impact PATHS=...`, or `make governance-explain ID=...`. "
    "Reading one named file is always allowed."
)
REGISTER_INSTEAD = (
    "If this path is a legitimate part of the repository, register it: give its directory a "
    ".governance.json annotation or add it to the [gate] always_allow list in governance/governance.toml, "
    "then `make governance-sync`."
)


def _rel(path_text: str) -> str | None:
    """Repository-relative POSIX path, or None when the target sits outside the repo."""
    if not path_text:
        return None
    absolute = os.path.abspath(os.path.join(REPO_ROOT, path_text)) if not os.path.isabs(path_text) else path_text
    try:
        relative = os.path.relpath(absolute, REPO_ROOT)
    except ValueError:
        return None  # a different drive on Windows is by definition outside the repository
    if relative == ".":
        return ""
    if relative.startswith(".."):
        return None
    return relative.replace(os.sep, "/")


def _static_prefix(pattern: str) -> str:
    """The literal path segments of a glob pattern before its first wildcard."""
    parts: list[str] = []
    for segment in pattern.replace("\\", "/").split("/"):
        if any(char in segment for char in "*?[") or not segment:
            break
        parts.append(segment)
    return "/".join(parts)


def _fold(text: str) -> str:
    return text.casefold()


class Surface:
    """The parsed read-surface artifact."""

    def __init__(self, document: dict) -> None:
        if document.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"unsupported read-surface schema_version {document.get('schema_version')!r}")
        self.modes = dict(document["default_modes"])
        corpus = document["corpus"]
        repo = document["repo"]
        self.gated_roots = [_fold(root) for root in corpus["gated_roots"]]
        self.bounded = [_fold(item) for item in corpus["bounded_surfaces"]]
        self.exact = {_fold(item) for item in repo["exact_files"]}
        self.prefixes = [_fold(item) for item in repo["dir_prefixes"]]
        self.patterns = [_fold(item) for item in repo["glob_patterns"]]

    def repo_allows_file(self, rel: str) -> bool:
        folded = _fold(rel)
        if folded in self.exact:
            return True
        if any(folded.startswith(prefix) for prefix in self.prefixes):
            return True
        return any(fnmatch.fnmatchcase(folded, pattern) for pattern in self.patterns)

    def repo_allows_root(self, rel: str) -> bool:
        """A directory may be enumerated when it sits inside, or contains, allowed ground."""
        if rel == "":
            return True
        folded = _fold(rel) + "/"
        if any(folded.startswith(prefix) for prefix in self.prefixes):
            return True
        return any(prefix.startswith(folded) for prefix in self.prefixes)

    def corpus_gate_for(self, rel: str) -> str | None:
        """The gated root a bulk enumeration would sweep, or None."""
        folded = _fold(rel) + "/"
        if any(folded.startswith(surface) for surface in self.bounded if surface.endswith("/")):
            return None
        for root in self.gated_roots:
            if folded.startswith(root):
                return root
        if folded == "governance/":
            return "governance/"
        return None


class SessionState:
    """Per-session dedupe and degradation memory. Every failure here is swallowed."""

    def __init__(self, session_id: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")[:80]
        self.directory = os.path.join(STATE_ROOT, safe)

    def _load(self, name: str) -> dict:
        try:
            with open(os.path.join(self.directory, name), encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _store(self, name: str, document: dict) -> None:
        try:
            os.makedirs(self.directory, exist_ok=True)
            with open(os.path.join(self.directory, name), "w", encoding="utf-8") as handle:
                json.dump(document, handle)
        except Exception:
            pass

    def first_occurrence(self, name: str, key: str, detail: str) -> bool:
        document = self._load(name)
        if key in document:
            return False
        if len(document) < STATE_ENTRY_CAP:
            document[key] = detail
            self._store(name, document)
        return True


def _respond(decision: str | None = None, reason: str = "", system_message: str = "") -> None:
    output: dict = {}
    if decision:
        payload = {"hookEventName": "PreToolUse", "permissionDecision": decision}
        if reason:
            payload["permissionDecisionReason"] = reason
        output["hookSpecificOutput"] = payload
    if system_message:
        output["systemMessage"] = system_message
    print(json.dumps(output) if output else "{}")


def _degraded(state: SessionState, reason: str, detail: str) -> None:
    """Fail open, once-visibly: the gate stood down and says so."""
    message = ""
    if state.first_occurrence("degraded.json", reason, detail):
        message = (
            f"governance gate degraded ({reason}): {detail} — reads are ungated this session; "
            "run `make governance-sync` if the read-surface artifact is missing or stale."
        )
    _respond("allow", system_message=message)


def _verdict(state: SessionState, mode: str, surface: str, subject: str, summary: str, repair: str) -> None:
    if mode == "deny":
        _respond("deny", reason=f"{summary}. {repair}")
        return
    message = ""
    if mode == "warn" and state.first_occurrence("warns.json", f"{surface}:{subject}", summary):
        message = f"governance gate ({surface}, warn): {summary}. {repair}"
    _respond("allow", system_message=message)


def selftest() -> None:
    """Stateless health probe: no stdin, no dedupe. Exits 1 on degraded so CI can gate
    on it; the session-context hook reads stdout and ignores the exit code."""
    mode = os.environ.get("GOVERNANCE_GATE", "").strip().lower()
    if mode == "off":
        print(json.dumps({"gate": "off"}))
        return
    try:
        with open(ARTIFACT_PATH, encoding="utf-8") as handle:
            surface = Surface(json.load(handle))
        print(json.dumps({"gate": "healthy", "modes": surface.modes}))
    except Exception as error:
        print(json.dumps({"gate": "degraded", "reason": str(error)}))
        raise SystemExit(1) from None


def main() -> None:
    if "--selftest" in sys.argv[1:]:
        selftest()
        return
    state = SessionState("unknown")
    try:
        try:
            event = json.load(sys.stdin)
        except Exception:
            _degraded(state, "malformed-event", "stdin was not valid hook-event JSON")
            return
        state = SessionState(str(event.get("session_id") or "unknown"))

        env_mode = os.environ.get("GOVERNANCE_GATE", "").strip().lower()
        if env_mode == "off":
            _respond("allow")
            return
        if env_mode not in ("", "warn", "deny"):
            _degraded(state, "bad-mode", f"GOVERNANCE_GATE={env_mode!r} is not off|warn|deny")
            return

        try:
            with open(ARTIFACT_PATH, encoding="utf-8") as handle:
                surface = Surface(json.load(handle))
        except FileNotFoundError:
            _degraded(state, "artifact-missing", f"{os.path.relpath(ARTIFACT_PATH, REPO_ROOT)} not found")
            return
        except Exception as error:
            _degraded(state, "artifact-invalid", str(error))
            return

        tool = str(event.get("tool_name") or "")
        tool_input = event.get("tool_input") or {}
        corpus_mode = env_mode or str(surface.modes.get("corpus", "warn"))
        repo_mode = env_mode or str(surface.modes.get("repo", "warn"))

        if tool == "Read":
            rel = _rel(str(tool_input.get("file_path") or ""))
            # Reading one named governance file is always allowed — the corpus surface
            # gates bulk enumeration only. AGENTS.md: query it, don't recurse into it.
            if rel is None or _fold(rel).startswith("governance/") or surface.repo_allows_file(rel):
                _respond("allow")
                return
            _verdict(state, repo_mode, "repo", rel, f"{rel} is outside the governed read surface", REGISTER_INSTEAD)
            return

        if tool in ("Glob", "Grep"):
            base = str(tool_input.get("path") or "") or str(event.get("cwd") or "") or REPO_ROOT
            root = base
            if tool == "Glob":
                prefix = _static_prefix(str(tool_input.get("pattern") or ""))
                if prefix:
                    root = os.path.join(base, prefix)
            rel = _rel(root)
            if rel is None:
                _respond("allow")
                return
            # A file-rooted search is a named read, not an enumeration: Grep with
            # path=<file> (or a Glob whose static prefix lands on a file) reads exactly
            # that file, so it takes the Read branch's semantics — including the
            # named-corpus-file allowance.
            absolute_root = root if os.path.isabs(root) else os.path.join(REPO_ROOT, root)
            if os.path.isfile(absolute_root):
                if _fold(rel).startswith("governance/") or surface.repo_allows_file(rel):
                    _respond("allow")
                    return
                _verdict(
                    state, repo_mode, "repo", rel, f"{rel} is outside the governed read surface", REGISTER_INSTEAD
                )
                return
            gated = surface.corpus_gate_for(rel)
            if gated:
                _verdict(
                    state,
                    corpus_mode,
                    "corpus",
                    rel,
                    f"bulk enumeration under {gated} sweeps the governance corpus",
                    QUERY_INSTEAD,
                )
                return
            if surface.repo_allows_root(rel):
                _respond("allow")
                return
            _verdict(state, repo_mode, "repo", rel, f"{rel}/ is outside the governed read surface", REGISTER_INSTEAD)
            return

        _respond("allow")
    except Exception as error:  # pragma: no cover - the ladder above should catch first
        _degraded(state, "internal-error", repr(error))


if __name__ == "__main__":
    main()
