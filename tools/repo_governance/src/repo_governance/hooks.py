"""Claude Code lifecycle hook endpoints: the once-per-session map and the per-turn stop check.

Only the slow, rich hooks live here. The per-read gate and the per-edit touched log run
dozens of times per turn, so they are stdlib scripts under `.claude/hooks/` reading the
sync-generated read-surface artifact — a `uv run` cold start costs seconds on Windows and
would be paid on every Read. This module is invoked once per session (`session-context`)
and once per turn (`stop-check`), where seconds are acceptable.

Failure posture mirrors the gate: these hooks fail open. A broken session-context emits a
one-line apology instead of a map; a broken stop-check lets the turn end. The fail-closed
layer is CI, where blocking on error inconveniences nobody mid-session.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import click

from repo_governance.config import Context

#: The orientation map's hard ceiling, in tokens, at four characters per token.
SESSION_CONTEXT_TOKEN_BUDGET = 2000

#: How the stop check labels what it did and did not verify. Rule classes, not rule IDs:
#: the count of unenforced rules is computed live so the label never goes stale.
VERIFIED_CLASSES = "generated-file freshness, document schemas, change records, sanctioned file locations"

_ENTRY_RULES = (
    "Entry rules: `make governance-preflight` -> `make governance-change-start SUMMARY=... REASON=...` -> "
    "`make governance-context PATHS=... TASK=...` -> smallest coherent change -> `make governance-sync` -> "
    "validators from `make governance-impact` -> `make governance-change-finish` -> `make governance-check`."
)
_CORPUS_RULE = (
    "Do not bulk-read governance/history/ or governance/manifests/ - query them: "
    "`make governance-context` / `governance-impact` / `governance-explain ID=...`."
)


def _safe_session(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")[:80]


def _hook_state_dir(ctx: Context, session_id: str) -> Path:
    return ctx.paths.cache / "hooks" / _safe_session(session_id)


def _gate_script(ctx: Context) -> Path:
    return ctx.repo_root / ".claude" / "hooks" / "governance_gate.py"


def _gate_health(ctx: Context) -> str:
    """One line from the gate's stateless selftest, degraded-by-default on any surprise."""
    script = _gate_script(ctx)
    if not script.is_file():
        return "gate: not installed (.claude/hooks/governance_gate.py missing)"
    try:
        probe = subprocess.run(
            [sys.executable, str(script), "--selftest"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(ctx.repo_root),
        )
        status = json.loads(probe.stdout.strip() or "{}")
    except Exception as error:
        return f"gate: degraded (selftest failed: {error}) - reads are ungated; run `make governance-sync`"
    if status.get("gate") == "healthy":
        modes = status.get("modes", {})
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(modes.items())) or "modes unknown"
        return f"gate: healthy ({rendered})"
    if status.get("gate") == "off":
        return "gate: off (GOVERNANCE_GATE=off set by the user)"
    return f"gate: degraded ({status.get('reason', 'unknown')}) - reads are ungated; run `make governance-sync`"


def _read_state(directory: Path, name: str) -> dict:
    try:
        return json.loads((directory / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_touched(directory: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        for line in (directory / "touched.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return entries


def _unenforced_count(ctx: Context) -> int:
    from repo_governance.checks import iter_rules

    return sum(
        1
        for rule in iter_rules(ctx)
        if rule.maturity not in {"retired", "deprecated"}
        and (rule.check_impl == "manual" or rule.check_impl.startswith("phase-"))
    )


def render_session_context(ctx: Context) -> str:
    """The compact orientation map: gate health, entry rules, and the names-only index."""
    from repo_governance.io_atomic import read_json
    from repo_governance.renderers.context import RULE_SCOPES

    lines: list[str] = [
        "# Governance orientation",
        "",
        _gate_health(ctx),
        "",
        _ENTRY_RULES,
        _CORPUS_RULE,
        "",
        "## Index (governance/catalog.json)",
    ]
    try:
        entries = read_json(ctx.paths.catalog).get("entries", [])
    except Exception:
        entries = []
    if not entries:
        lines.append("(catalog unavailable - run `make governance-sync`)")
    for entry in entries:
        description = str(entry.get("description", "")).rstrip(".")
        lines.append(f"- {entry.get('path')} - {description}")

    lines.append("")
    lines.append("## Conventions (auto-loaded per file; listed here for non-Claude agents)")
    for rule_file, scopes in RULE_SCOPES:
        lines.append(f"- {rule_file}: {', '.join(scopes)}")

    text = "\n".join(lines) + "\n"
    budget = SESSION_CONTEXT_TOKEN_BUDGET * 4
    if len(text) > budget:
        text = text[: budget - 25].rsplit("\n", 1)[0] + "\n(truncated at budget)\n"
    return text


@click.group()
def hook() -> None:
    """Claude Code lifecycle hook endpoints. Humans run the plain commands instead."""


@hook.command("session-context")
def session_context() -> None:
    """Emit the orientation map for SessionStart. stdout becomes session context."""
    try:
        ctx = Context.discover()
        click.echo(render_session_context(ctx), nl=False)
    except Exception as error:
        click.echo(f"# Governance orientation unavailable ({error}) - run `make governance-doctor`.")


def _stop_evaluation(ctx: Context, session_id: str) -> tuple[list[str], list[str]]:
    """Returns (blockers, annotations) for a turn that touched repository files."""
    from repo_governance.checks import CheckScope, run_checks
    from repo_governance.gitutil import toplevel, untracked_files, working_tree_changes
    from repo_governance.session import SessionError, load

    directory = _hook_state_dir(ctx, session_id)
    touched = _read_touched(directory)
    blockers: list[str] = []
    annotations: list[str] = []

    degraded = _read_state(directory, "degraded.json")
    if degraded:
        annotations.append(
            "the read gate degraded this session ("
            + "; ".join(f"{reason}: {detail}" for reason, detail in sorted(degraded.items()))
            + ") - reads were ungated; `make governance-sync` rebuilds the artifact"
        )
    warns = _read_state(directory, "warns.json")
    if warns:
        annotations.append(f"{len(warns)} read(s) outside the governed surface this session (gate in warn mode)")

    if not touched:
        return blockers, annotations

    report = run_checks(CheckScope(ctx=ctx, fast=True))
    for finding in report.blocking:
        blockers.append(f"[{finding.rule}] {finding.path}: {finding.message} Repair: {finding.repair}")

    # The touched log is session memory: it outlives commits and never sees change
    # records written by the governance CLI rather than the agent's editor. Only paths
    # still dirty in the working tree may block; a committed touch was already carried
    # through the governed flow. When git cannot answer for THIS root — unavailable, or
    # answering for an enclosing repository — stay conservative and treat every touch
    # as dirty rather than silently passing.
    touched_paths = {entry.get("path", "") for entry in touched}
    tree_changes = working_tree_changes(ctx.repo_root)
    new_files = untracked_files(ctx.repo_root)
    git_answers_here = toplevel(ctx.repo_root) == ctx.repo_root.resolve()
    if not git_answers_here or (tree_changes is None and new_files is None):
        dirty = touched_paths
        records = {path for path in touched_paths if path.startswith("governance/history/changes/")}
    else:
        dirty_now = {*(tree_changes or ()), *(new_files or ())}
        dirty = touched_paths & dirty_now
        records = {path for path in dirty_now if path.startswith("governance/history/changes/")}

    material = sorted(path for path in dirty if not path.startswith("governance/history/"))
    if material and not records:
        try:
            load(ctx)
        except SessionError:
            preview = ", ".join(material[:5])
            blockers.append(
                f"{len(material)} file(s) changed with no change session and no change record ({preview}). "
                "Repair: make governance-change-start SUMMARY=... REASON=...   then, when the work is done,   "
                "make governance-change-finish"
            )
        else:
            annotations.append("a change session is open; finish it with `make governance-change-finish` before commit")

    return blockers, annotations


@hook.command("stop-check")
def stop_check() -> None:
    """Gate turn-end for Stop: exit 2 with instructions while the governed loop is unclosed."""
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}
    if event.get("stop_hook_active"):
        return  # loop guard: never block twice in one stop cycle

    try:
        ctx = Context.discover()
        session_id = str(event.get("session_id") or "unknown")
        blockers, annotations = _stop_evaluation(ctx, session_id)
        unenforced = _unenforced_count(ctx)
    except Exception as error:
        click.echo(json.dumps({"systemMessage": f"governance stop-check degraded ({error}); turn not blocked"}))
        return

    label = (
        f"verified: {VERIFIED_CLASSES} (fast checks). Not verified: {unenforced} declared rule(s) "
        "await enforcement - a clean stop-check is not proof the architectural invariants held."
    )

    if blockers:
        message_lines = [
            "The governance stop-check is blocking turn-end:",
            *[f"  {index}. {text}" for index, text in enumerate(blockers, start=1)],
            *[f"  note: {text}" for text in annotations],
            f"  ({label})",
        ]
        click.echo("\n".join(message_lines), err=True)
        raise SystemExit(2)

    notes = "; ".join(annotations)
    suffix = f" | {notes}" if notes else ""
    click.echo(json.dumps({"systemMessage": f"governance stop-check passed - {label}{suffix}"}))


__all__ = ["hook", "render_session_context"]
