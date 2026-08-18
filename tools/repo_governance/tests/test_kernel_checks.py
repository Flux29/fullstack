"""The kernel rules actually fire.

A check that never fires is worse than no check: it reads as an assurance. Each test here
introduces the exact condition its rule exists to catch.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from repo_governance import gitutil
from repo_governance.checks import CheckScope, iter_rules, process, resolvable_check_impls
from repo_governance.checks.references import check_excluded_validators, check_validator_references
from repo_governance.checks.schemas import check_generated_files, check_idempotence
from repo_governance.checks.security import (
    check_no_application_imports,
    check_no_secret_values,
    check_runtime_evidence_not_committed,
)
from repo_governance.config import Context
from repo_governance.io_atomic import read_json, write_json_atomic
from repo_governance.pipeline import sync


def _scope(root: Path) -> CheckScope:
    return CheckScope(ctx=Context.discover(root))


def test_every_declared_rule_resolves_or_is_explicitly_deferred(real_context: Context) -> None:
    resolvable = resolvable_check_impls()
    unresolved = [
        rule.id
        for rule in iter_rules(real_context)
        if rule.check_impl not in resolvable
        and rule.check_impl != "manual"
        and not rule.check_impl.startswith("phase-")
    ]
    assert unresolved == [], f"rules whose check would silently never run: {unresolved}"


def test_all_self_governance_rules_are_blocking(real_context: Context) -> None:
    """The kernel is blocking from day one; everything else earns strictness."""
    kernel = [rule for rule in iter_rules(real_context) if rule.group == "self-governance"]
    assert kernel, "the self-governance policy must declare rules"
    assert all(rule.maturity == "blocking" for rule in kernel)


def test_hand_editing_a_generated_file_is_detected(minimal_repo: Path) -> None:
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    catalog = minimal_repo / "governance" / "catalog.json"
    document = read_json(catalog)
    document["entries"][0]["description"] = "hand-edited by a well-meaning human"
    write_json_atomic(catalog, document)

    issues = check_generated_files(_scope(minimal_repo))
    assert any("hand-edited" in issue.message for issue in issues)
    assert all(issue.repair == "make governance-sync" for issue in issues)


def test_deleting_a_generated_file_is_detected(minimal_repo: Path) -> None:
    ctx = Context.discover(minimal_repo)
    sync(ctx)
    (minimal_repo / "governance" / "catalog.json").unlink()

    issues = check_generated_files(_scope(minimal_repo))
    assert any("missing" in (issue.message + (issue.evidence or "")) for issue in issues)


def test_crlf_in_a_generated_file_is_detected_and_named(minimal_repo: Path) -> None:
    """Windows checkouts are the reason the LF pin exists; the message must say so."""
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    catalog = minimal_repo / "governance" / "catalog.json"
    catalog.write_bytes(catalog.read_bytes().replace(b"\n", b"\r\n"))

    issues = check_generated_files(_scope(minimal_repo))
    assert any("CRLF" in (issue.message + (issue.evidence or "")) for issue in issues)


def test_generated_drift_is_reported_even_when_only_the_source_is_in_scope(minimal_repo: Path) -> None:
    """pre-commit passes the staged source, never the stale derived file. Both must still fail."""
    ctx = Context.discover(minimal_repo)
    sync(ctx)
    catalog = minimal_repo / "governance" / "catalog.json"
    document = read_json(catalog)
    document["entries"][0]["description"] = "stale"
    write_json_atomic(catalog, document)

    scoped = CheckScope(ctx=ctx, files=("backend/.env.example",))
    assert any(issue.path == "governance/catalog.json" for issue in check_generated_files(scoped))


def test_a_record_staged_outside_the_batch_still_counts(monkeypatch: pytest.MonkeyPatch, real_context: Context) -> None:
    """A scoped run sees only its batch of staged files; the record is in the change set regardless."""
    record = "governance/history/changes/2026-01-01-example.json"
    tooling = "tools/repo_governance/src/repo_governance/cli.py"
    monkeypatch.setattr(process, "working_tree_changes", lambda root: [record, tooling])

    scoped = CheckScope(ctx=real_context, files=(tooling,))
    assert process.check_governance_change_records(scoped) == []

    monkeypatch.setattr(process, "working_tree_changes", lambda root: [tooling])
    assert process.check_governance_change_records(scoped), "no record anywhere is still a finding"


def test_shell_text_in_a_validation_list_is_rejected(minimal_repo: Path) -> None:
    """An annotation must not be able to introduce a command."""
    (minimal_repo / "thing").mkdir()
    (minimal_repo / "thing" / ".governance.json").write_text(
        json.dumps(
            {
                "id": "thing",
                "kind": "backend-subsystem",
                "purpose": "x",
                "owns": ["thing/**"],
                "validation": ["uv run pytest tests/live --send-real-email"],
            }
        ),
        encoding="utf-8",
    )

    issues = check_validator_references(_scope(minimal_repo))
    assert any("executable text" in issue.message for issue in issues)


def test_referencing_an_excluded_validator_is_rejected(minimal_repo: Path) -> None:
    """Impact analysis must never be able to reach a destructive suite."""
    (minimal_repo / "thing").mkdir()
    (minimal_repo / "thing" / ".governance.json").write_text(
        json.dumps(
            {
                "id": "thing",
                "kind": "backend-subsystem",
                "purpose": "x",
                "owns": ["thing/**"],
                "validation": ["google-live-e2e"],
            }
        ),
        encoding="utf-8",
    )

    issues = check_validator_references(_scope(minimal_repo))
    assert any("deliberately excluded" in issue.message for issue in issues)


def test_unknown_validator_reference_is_rejected(minimal_repo: Path) -> None:
    (minimal_repo / "thing").mkdir()
    (minimal_repo / "thing" / ".governance.json").write_text(
        json.dumps(
            {
                "id": "thing",
                "kind": "backend-subsystem",
                "purpose": "x",
                "owns": ["thing/**"],
                "validation": ["no-such-validator"],
            }
        ),
        encoding="utf-8",
    )

    issues = check_validator_references(_scope(minimal_repo))
    assert any("not in the registry" in issue.message for issue in issues)


def test_a_destructive_validator_in_the_registry_is_rejected(minimal_repo: Path) -> None:
    registry_path = minimal_repo / "governance" / "validators.json"
    registry = read_json(registry_path)
    registry["validators"].append(
        {
            "id": "wipe-everything",
            "command": "make docker-reset",
            "description": "destroys local data",
            "destructive": True,
        }
    )
    write_json_atomic(registry_path, registry)

    issues = check_excluded_validators(_scope(minimal_repo))
    assert any("destructive" in issue.message for issue in issues)


def test_a_credential_in_a_governance_artifact_is_detected(minimal_repo: Path) -> None:
    (minimal_repo / "governance" / "history" / "changes" / "note.json").write_text(
        json.dumps({"leak": "ghp_" + "a" * 36}),
        encoding="utf-8",
    )

    issues = check_no_secret_values(_scope(minimal_repo))
    assert any("GitHub token" in issue.message for issue in issues)
    assert all("ghp_" not in (issue.evidence or "") for issue in issues), "the scanner must not echo the value"


def test_a_url_with_a_query_string_is_detected(minimal_repo: Path) -> None:
    """A connection URL can carry a live API key in its query string."""
    (minimal_repo / "governance" / "history" / "changes" / "note.json").write_text(
        json.dumps({"url": "https://mcp.example.com/mcp?apikey=REDACTED-BUT-STILL-A-QUERY"}),
        encoding="utf-8",
    )

    issues = check_no_secret_values(_scope(minimal_repo))
    assert any("query string" in issue.message for issue in issues)


def test_a_url_with_userinfo_is_detected(minimal_repo: Path) -> None:
    (minimal_repo / "governance" / "history" / "changes" / "note.json").write_text(
        json.dumps({"dsn": "postgresql://user:hunter2@localhost/db"}),
        encoding="utf-8",
    )

    issues = check_no_secret_values(_scope(minimal_repo))
    assert any("userinfo" in issue.message for issue in issues)


def test_the_real_governance_tree_holds_no_credentials(real_context: Context) -> None:
    assert check_no_secret_values(CheckScope(ctx=real_context)) == []


def test_the_tool_does_not_import_application_code(real_context: Context) -> None:
    assert check_no_application_imports(CheckScope(ctx=real_context)) == []


def test_an_unmarked_application_import_is_detected(real_context: Context, tmp_path: Path) -> None:
    """Only a file that declares it runs in a scrubbed subprocess may import app.*."""
    root = tmp_path / "repo"
    source = root / "tools" / "repo_governance" / "src" / "repo_governance"
    source.mkdir(parents=True)
    (root / "governance").mkdir()
    (root / "governance" / "governance.toml").write_text(
        (real_context.paths.config_file).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (source / "leaky.py").write_text("from app.core.config import settings\n", encoding="utf-8")

    issues = check_no_application_imports(_scope(root))
    assert any("imports application code in-process" in issue.message for issue in issues)

    (source / "leaky.py").write_text(
        "# governance:runs-in-scrubbed-subprocess\nfrom app.core.config import settings\n",
        encoding="utf-8",
    )
    assert check_no_application_imports(_scope(root)) == []


def test_evidence_inside_the_governance_tree_is_detected(minimal_repo: Path) -> None:
    evidence = minimal_repo / "governance" / "evidence"
    evidence.mkdir()
    (evidence / "connections.json").write_text('{"count": 3}', encoding="utf-8")

    issues = check_runtime_evidence_not_committed(_scope(minimal_repo))
    assert any("Runtime evidence" in issue.message for issue in issues)


def test_rendering_is_deterministic_in_the_real_repository(real_context: Context) -> None:
    assert check_idempotence(CheckScope(ctx=real_context)) == []


def test_the_porcelain_status_column_is_read_by_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every status shape keeps its path, and only `??` counts as untracked.

    Splitting on the first space instead of slicing the fixed-width column silently loses
    the path of every unstaged modification, whose index column is blank.
    """
    porcelain = " M AGENTS.md\0A  backend/app/new.py\0R  after.py\0before.py\0?? debug_run.py\0"
    monkeypatch.setattr(gitutil, "_run", lambda args, cwd: porcelain)

    assert gitutil.working_tree_changes(Path(".")) == [
        "AGENTS.md",
        "after.py",
        "backend/app/new.py",
        "before.py",
        "debug_run.py",
    ]
    assert gitutil.untracked_files(Path(".")) == ["debug_run.py"]


def test_hook_installed_follows_git_not_a_guessed_hooks_directory(tmp_path: Path) -> None:
    """A fresh clone reports the hook missing; writing it where git looks reports it present."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert gitutil.hook_installed(tmp_path) is False

    hooks_dir = tmp_path / "custom-hooks"
    hooks_dir.mkdir()
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.hooksPath", "custom-hooks"], check=True)
    assert gitutil.hook_installed(tmp_path) is False, "an empty core.hooksPath directory is still missing"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    assert gitutil.hook_installed(tmp_path) is True


def test_preflight_names_a_missing_precommit_hook_and_its_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from repo_governance import cli

    assert cli._precommit_hook_problem(tmp_path) is None, "no pre-commit config means nothing to install"
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "hook_installed", lambda root, name="pre-commit": False)
    problem = cli._precommit_hook_problem(tmp_path)
    assert problem is not None and "NOT INSTALLED" in problem and cli.PRECOMMIT_INSTALL in problem
    monkeypatch.setattr(cli, "hook_installed", lambda root, name="pre-commit": True)
    assert cli._precommit_hook_problem(tmp_path) is None
    monkeypatch.setattr(cli, "hook_installed", lambda root, name="pre-commit": None)
    assert cli._precommit_hook_problem(tmp_path) is None, "unknown is not reported as missing"


def test_a_scratch_script_is_reported_and_sanctioned_locations_are_not(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        process,
        "untracked_files",
        lambda root: [
            "backend/alembic/versions/9f2c_add_column.py",
            "backend/tests/test_thing.py",
            "docs/notes.md",
            "frontend/e2e/login.spec.ts",
            "frontend/src/lib/thing.test.ts",
            "governance/history/changes/2026-08-06-x.json",
            "verify_fix.py",
        ],
    )

    issues = process.check_untracked_files_are_sanctioned(CheckScope(ctx=real_context))
    assert len(issues) == 1
    assert issues[0].path == "verify_fix.py"
