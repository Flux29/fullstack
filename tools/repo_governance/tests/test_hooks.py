"""The hook surface: the stdlib gate and touched scripts, and the CLI session/stop hooks.

The scripts under `.claude/hooks/` are loaded from their file paths so there is exactly
one source of truth — none of their logic is duplicated into the package for testability.
The gate's contract under test is the decision ladder: a deny requires positive certainty
on every rung, and every failure between the rungs degrades to a visible allow.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from repo_governance.config import Context
from repo_governance.hooks import _stop_evaluation, render_session_context
from repo_governance.io_atomic import canonical_json

SYNTHETIC_SURFACE = {
    "schema_version": 1,
    "provenance": {"method": "extracted", "extractor_version": "1.0.0"},
    "default_modes": {"corpus": "warn", "repo": "warn"},
    "corpus": {
        "gated_roots": ["governance/history/", "governance/manifests/"],
        "bounded_surfaces": ["governance/catalog.json", "governance/schemas/"],
    },
    "repo": {
        "exact_files": ["AGENTS.md"],
        "dir_prefixes": [".claude/", "backend/", "governance/schemas/"],
        "glob_patterns": ["*.test.ts"],
    },
}


def _load_script(repo_root: Path, name: str):
    path = repo_root / ".claude" / "hooks" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate(repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real gate script, pointed at a synthetic repository and surface."""
    module = _load_script(repo_root, "governance_gate.py")
    fake_root = tmp_path / "repo"
    artifact = fake_root / "governance" / "manifests" / "generated" / "read-surface.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(canonical_json(SYNTHETIC_SURFACE), encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", str(fake_root))
    monkeypatch.setattr(module, "ARTIFACT_PATH", str(artifact))
    monkeypatch.setattr(module, "STATE_ROOT", str(tmp_path / "state"))
    return module


def _run_gate(module, monkeypatch, capsys, payload, mode: str | None = None) -> dict:
    if mode is None:
        monkeypatch.delenv("GOVERNANCE_GATE", raising=False)
    else:
        monkeypatch.setenv("GOVERNANCE_GATE", mode)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(sys, "argv", ["governance_gate.py"])
    module.main()
    return json.loads(capsys.readouterr().out)


def _decision(output: dict) -> str:
    return output.get("hookSpecificOutput", {}).get("permissionDecision", "")


def _read(path: str, session: str = "s1") -> dict:
    return {"session_id": session, "tool_name": "Read", "tool_input": {"file_path": path}}


def _grep(path: str, session: str = "s1") -> dict:
    return {"session_id": session, "tool_name": "Grep", "tool_input": {"pattern": "x", "path": path}}


# --- the happy rungs -----------------------------------------------------------------


def test_a_governed_read_is_allowed_silently(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _read("backend/app/main.py"))
    assert _decision(output) == "allow"
    assert "systemMessage" not in output


def test_a_named_file_read_inside_the_corpus_is_allowed(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _read("governance/history/changes/2026-01-01-x.json"))
    assert _decision(output) == "allow"
    assert "systemMessage" not in output


def test_reads_outside_the_repository_are_allowed(gate, monkeypatch, capsys, tmp_path) -> None:
    outside = str(tmp_path.parent / "elsewhere" / "scratch.py")
    output = _run_gate(gate, monkeypatch, capsys, _read(outside))
    assert _decision(output) == "allow"


def test_bounded_surfaces_may_be_enumerated(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance/schemas"))
    assert _decision(output) == "allow"
    assert "systemMessage" not in output


# --- the two gated surfaces ----------------------------------------------------------


def test_corpus_bulk_grep_warns_with_the_query_instruction(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance/history"))
    assert _decision(output) == "allow"
    assert "queried, not bulk-read" in output.get("systemMessage", "")


def test_enumerating_governance_root_is_gated(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance"))
    assert "sweeps the governance corpus" in output.get("systemMessage", "")


def test_glob_static_prefix_reaches_the_corpus(gate, monkeypatch, capsys) -> None:
    event = {
        "session_id": "s1",
        "cwd": gate.REPO_ROOT,
        "tool_name": "Glob",
        "tool_input": {"pattern": "governance/manifests/**/*.json"},
    }
    output = _run_gate(gate, monkeypatch, capsys, event, mode="deny")
    assert _decision(output) == "deny"
    assert "queried, not bulk-read" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_an_ungoverned_read_warns_once_then_stays_quiet(gate, monkeypatch, capsys) -> None:
    first = _run_gate(gate, monkeypatch, capsys, _read("stray/notes.txt"))
    second = _run_gate(gate, monkeypatch, capsys, _read("stray/notes.txt"))
    assert "outside the governed read surface" in first.get("systemMessage", "")
    assert _decision(second) == "allow"
    assert "systemMessage" not in second


def test_deny_mode_denies_with_the_register_instruction(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _read("stray/notes.txt"), mode="deny")
    assert _decision(output) == "deny"
    assert "register it" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_literal_bracket_segments_match_as_prefixes_not_charclasses(gate, monkeypatch, capsys) -> None:
    surface = dict(SYNTHETIC_SURFACE)
    surface["repo"] = dict(surface["repo"], dir_prefixes=["frontend/src/app/[locale]/"])
    Path(gate.ARTIFACT_PATH).write_text(canonical_json(surface), encoding="utf-8")
    output = _run_gate(gate, monkeypatch, capsys, _read("frontend/src/app/[locale]/page.tsx"))
    assert _decision(output) == "allow"
    assert "systemMessage" not in output


# --- fail-open: everything between the rungs degrades to a visible allow --------------


def test_off_mode_stands_down_entirely(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance/history"), mode="off")
    assert _decision(output) == "allow"
    assert "systemMessage" not in output


def test_malformed_stdin_fails_open_with_a_degraded_note(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, "this is not json")
    assert _decision(output) == "allow"
    assert "degraded" in output.get("systemMessage", "")


def test_a_missing_artifact_never_denies_even_in_deny_mode(gate, monkeypatch, capsys) -> None:
    Path(gate.ARTIFACT_PATH).unlink()
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance/history"), mode="deny")
    assert _decision(output) == "allow"
    assert "degraded" in output.get("systemMessage", "")


def test_a_newer_schema_version_fails_open_never_denies(gate, monkeypatch, capsys) -> None:
    surface = dict(SYNTHETIC_SURFACE, schema_version=2)
    Path(gate.ARTIFACT_PATH).write_text(canonical_json(surface), encoding="utf-8")
    output = _run_gate(gate, monkeypatch, capsys, _grep("governance/history"), mode="deny")
    assert _decision(output) == "allow"
    assert "degraded" in output.get("systemMessage", "")


def test_an_unknown_mode_value_degrades_rather_than_guessing(gate, monkeypatch, capsys) -> None:
    output = _run_gate(gate, monkeypatch, capsys, _read("backend/app/main.py"), mode="block")
    assert _decision(output) == "allow"
    assert "degraded" in output.get("systemMessage", "")


def test_degraded_notes_are_deduped_per_reason(gate, monkeypatch, capsys) -> None:
    first = _run_gate(gate, monkeypatch, capsys, "not json")
    second = _run_gate(gate, monkeypatch, capsys, "still not json")
    assert "degraded" in first.get("systemMessage", "")
    assert "systemMessage" not in second


def test_selftest_reports_healthy_then_degraded(gate, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GOVERNANCE_GATE", raising=False)
    gate.selftest()
    healthy = json.loads(capsys.readouterr().out)
    assert healthy["gate"] == "healthy"
    assert healthy["modes"] == {"corpus": "warn", "repo": "warn"}

    Path(gate.ARTIFACT_PATH).unlink()
    with pytest.raises(SystemExit) as excinfo:
        gate.selftest()
    degraded = json.loads(capsys.readouterr().out)
    assert degraded["gate"] == "degraded"
    assert excinfo.value.code == 1  # CI gates on this; session-context ignores it


# --- the touched log -------------------------------------------------------------------


@pytest.fixture
def touched(repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_script(repo_root, "governance_touched.py")
    monkeypatch.setattr(module, "REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.setattr(module, "STATE_ROOT", str(tmp_path / "state"))
    return module


def _run_touched(module, monkeypatch, payload) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()


def test_touched_appends_repo_relative_entries(touched, monkeypatch, tmp_path) -> None:
    event = {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": "backend/app/main.py"}}
    _run_touched(touched, monkeypatch, event)
    _run_touched(touched, monkeypatch, dict(event, tool_name="Write"))
    lines = (tmp_path / "state" / "s1" / "touched.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["tool"] for line in lines] == ["Edit", "Write"]
    assert json.loads(lines[0])["path"] == "backend/app/main.py"


def test_touched_ignores_paths_outside_the_repository(touched, monkeypatch, tmp_path) -> None:
    outside = str(tmp_path.parent / "elsewhere" / "scratch.py")
    event = {"session_id": "s1", "tool_name": "Write", "tool_input": {"file_path": outside}}
    _run_touched(touched, monkeypatch, event)
    assert not (tmp_path / "state" / "s1" / "touched.jsonl").exists()


# --- latency: the reason the gate is a stdlib script -----------------------------------


def test_the_gate_answers_well_inside_the_hook_timeout(repo_root: Path) -> None:
    event = json.dumps({"session_id": "latency", "tool_name": "Read", "tool_input": {"file_path": "AGENTS.md"}})
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(repo_root / ".claude" / "hooks" / "governance_gate.py")],
        input=event,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(repo_root),
    )
    elapsed = time.perf_counter() - started
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert elapsed < 3.0, f"gate took {elapsed:.2f}s; the 5s hook timeout leaves no headroom"


# --- the CLI half: session-context and stop-check ---------------------------------------


def test_session_context_fits_its_budget_and_lists_the_catalog(real_context: Context) -> None:
    text = render_session_context(real_context)
    assert len(text) <= 2000 * 4
    assert "gate:" in text
    assert "governance/catalog.json" in text
    assert "governance-change-start" in text


def test_stop_evaluation_passes_an_untouched_session(real_context: Context) -> None:
    blockers, _ = _stop_evaluation(real_context, "session-that-never-existed")
    assert blockers == []


def test_stop_evaluation_reports_gate_degradations(real_context: Context) -> None:
    directory = real_context.paths.cache / "hooks" / "test-degraded-session"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        (directory / "degraded.json").write_text('{"artifact-missing": "gone"}', encoding="utf-8")
        _, annotations = _stop_evaluation(real_context, "test-degraded-session")
        assert any("degraded" in note for note in annotations)
    finally:
        for item in directory.iterdir():
            item.unlink()
        directory.rmdir()


def test_stop_evaluation_blocks_material_changes_without_a_record(minimal_repo: Path) -> None:
    """minimal_repo is not its own git repository — git answers for whatever encloses the
    temp directory — so the toplevel guard rejects the answer, every touch counts as
    dirty, and the record rule blocks conservatively."""
    ctx = Context.discover(minimal_repo)
    state = ctx.paths.cache / "hooks" / "s1"
    state.mkdir(parents=True)
    (state / "touched.jsonl").write_text('{"path": "backend/app/main.py", "tool": "Edit"}\n', encoding="utf-8")
    blockers, _ = _stop_evaluation(ctx, "s1")
    assert any("no change session and no change record" in blocker for blocker in blockers)


def test_stop_evaluation_does_not_block_touched_paths_that_were_committed(
    minimal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The false positive from the first live firing: session memory outliving commits.

    A path touched earlier in the session but since committed through the governed flow
    must not block turn-end — the touched log is evidence of activity, not of debt.
    """
    monkeypatch.setattr("repo_governance.gitutil.working_tree_changes", lambda root: [])
    monkeypatch.setattr("repo_governance.gitutil.untracked_files", lambda root: [])
    monkeypatch.setattr("repo_governance.gitutil.toplevel", lambda root: minimal_repo.resolve())
    ctx = Context.discover(minimal_repo)
    state = ctx.paths.cache / "hooks" / "s1"
    state.mkdir(parents=True)
    (state / "touched.jsonl").write_text('{"path": "backend/app/main.py", "tool": "Edit"}\n', encoding="utf-8")
    blockers, _ = _stop_evaluation(ctx, "s1")
    assert not any("no change session" in blocker for blocker in blockers)


def test_stop_evaluation_counts_records_the_cli_wrote_outside_the_editor(
    minimal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change records are written by the governance CLI, not the agent's Edit tool, so
    they never appear in the touched log — the working tree is where they must count."""
    monkeypatch.setattr(
        "repo_governance.gitutil.working_tree_changes",
        lambda root: ["backend/app/main.py", "governance/history/changes/2026-08-08-x.json"],
    )
    monkeypatch.setattr("repo_governance.gitutil.untracked_files", lambda root: [])
    monkeypatch.setattr("repo_governance.gitutil.toplevel", lambda root: minimal_repo.resolve())
    ctx = Context.discover(minimal_repo)
    state = ctx.paths.cache / "hooks" / "s1"
    state.mkdir(parents=True)
    (state / "touched.jsonl").write_text('{"path": "backend/app/main.py", "tool": "Edit"}\n', encoding="utf-8")
    blockers, _ = _stop_evaluation(ctx, "s1")
    assert not any("no change session" in blocker for blocker in blockers)


def test_stop_evaluation_notes_an_open_session_instead_of_blocking(minimal_repo: Path) -> None:
    from repo_governance.session import start

    ctx = Context.discover(minimal_repo)
    start(ctx, summary="test", reason="test", date="2026-08-08")
    state = ctx.paths.cache / "hooks" / "s1"
    state.mkdir(parents=True, exist_ok=True)
    (state / "touched.jsonl").write_text('{"path": "backend/app/main.py", "tool": "Edit"}\n', encoding="utf-8")
    blockers, annotations = _stop_evaluation(ctx, "s1")
    assert not any("no change session" in blocker for blocker in blockers)
    assert any("change session is open" in note for note in annotations)


def test_stop_hook_active_short_circuits_the_loop_guard(repo_root: Path) -> None:
    from click.testing import CliRunner

    from repo_governance.hooks import hook

    result = CliRunner().invoke(hook, ["stop-check"], input='{"stop_hook_active": true}')
    assert result.exit_code == 0
    assert result.output == ""


# --- the artifact builder ----------------------------------------------------------------


def test_bracketed_ownership_globs_compile_to_prefixes(real_context: Context) -> None:
    from repo_governance.builders import build_read_surface
    from repo_governance.merge import build_components

    components, _ = build_components(real_context)
    surface = build_read_surface(real_context, components)
    prefixes = surface["repo"]["dir_prefixes"]
    assert "frontend/src/app/[locale]/(dashboard)/chat/" in prefixes
    assert all(not prefix.endswith("**") for prefix in prefixes)
    assert surface["default_modes"] == {"corpus": "deny", "repo": "warn"}


def test_declared_but_absent_catalog_paths_stay_out_of_the_surface(real_context: Context) -> None:
    """Same honesty rule as the catalog: a declared future path is not readable surface.

    governance/history/evaluations/ is declared in the catalog spec but does not exist
    yet; including it would manufacture a permanent ghost in the coverage report."""
    from repo_governance.builders import build_read_surface
    from repo_governance.merge import build_components

    components, _ = build_components(real_context)
    surface = build_read_surface(real_context, components)
    assert "governance/history/evaluations/" not in surface["repo"]["dir_prefixes"]


# --- coverage: the fail-closed CI layer -------------------------------------------------


def test_read_surface_coverage_is_clean_in_the_real_repository(real_context: Context) -> None:
    """The same bar CI holds: every tracked path governed, nothing promised but absent."""
    from repo_governance.builders import analyse_read_surface_coverage

    report = analyse_read_surface_coverage(real_context)
    assert report["status"] == "ok"
    assert report["orphans"] == []
    assert report["ghosts"] == []
    assert report["rules_drift"] == []
    assert report["coverage_percent"] == 100.0


def test_coverage_reports_unknown_when_git_answers_for_an_enclosing_repository(minimal_repo: Path) -> None:
    from repo_governance.builders import analyse_read_surface_coverage

    report = analyse_read_surface_coverage(Context.discover(minimal_repo))
    assert report["status"] == "unknown"


def test_coverage_names_orphans_and_ghosts(real_context: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    from repo_governance.builders import analyse_read_surface_coverage

    monkeypatch.setattr(
        "repo_governance.gitutil.tracked_files",
        lambda root: ["backend/app/main.py", "stray/orphan.txt", ".claude/worktrees/w1/ignored.py"],
    )
    report = analyse_read_surface_coverage(real_context)
    assert report["status"] == "ok"
    assert report["orphans"] == ["stray/orphan.txt"]
    assert report["total_tracked"] == 2  # the worktree path is excluded from consideration
