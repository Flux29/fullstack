"""The coverage-floor ratchet fires on a lowered floor and stays quiet otherwise.

Floors are ratchets against measured coverage; the one way to make a red build green
without a test is to lower one. The check compares each floor source with its content at
the base commit. Git is stubbed here so the tests exercise the comparison, not the plumbing
`test_kernel_checks` already covers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_governance.checks import CheckScope, process
from repo_governance.config import Context

BACKEND_PYPROJECT = """[tool.coverage.report]
fail_under = {floor}
show_missing = true
"""

VITEST_CONFIG = """export default defineConfig({{
  test: {{
    coverage: {{
      thresholds: {{
        statements: {statements},
        branches: 17,
        functions: 6,
        lines: 3,
      }},
    }},
  }},
}});
"""

RECORD_NAMING_THE_RULE = (
    '{"id": "2026-08-18-lower-the-floor", "effects": ["Deliberately lowered under coverage-floor-does-not-regress"]}'
)


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def floors_repo(minimal_repo: Path) -> Path:
    _write(minimal_repo, "backend/pyproject.toml", BACKEND_PYPROJECT.format(floor=52))
    _write(minimal_repo, "tools/repo_governance/pyproject.toml", BACKEND_PYPROJECT.format(floor=73))
    _write(minimal_repo, "frontend/vitest.config.ts", VITEST_CONFIG.format(statements=3))
    return minimal_repo


def _stub_git(monkeypatch: pytest.MonkeyPatch, base: dict[str, str], records: list[str] = ()) -> None:
    monkeypatch.setattr(process, "file_at_ref", lambda root, ref, rel: base.get(rel))
    monkeypatch.setattr(process, "_change_set", lambda scope: ([], list(records), True))


def _base(floor: int = 52, tool_floor: int = 73, statements: int = 3) -> dict[str, str]:
    return {
        "backend/pyproject.toml": BACKEND_PYPROJECT.format(floor=floor),
        "tools/repo_governance/pyproject.toml": BACKEND_PYPROJECT.format(floor=tool_floor),
        "frontend/vitest.config.ts": VITEST_CONFIG.format(statements=statements),
    }


def test_floors_are_read_from_pyproject_and_vitest_config() -> None:
    assert process._coverage_floors("pyproject", BACKEND_PYPROJECT.format(floor=54)) == {"fail_under": 54.0}
    assert process._coverage_floors("vitest", VITEST_CONFIG.format(statements=3)) == {
        "statements": 3.0,
        "branches": 17.0,
        "functions": 6.0,
        "lines": 3.0,
    }
    assert process._coverage_floors("pyproject", "not = [valid toml") == {}
    assert process._coverage_floors("vitest", "export default {}") == {}


def test_unchanged_floors_raise_no_issue(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, _base())
    assert process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo))) == []


def test_raising_a_floor_is_not_a_regression(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, _base(floor=50, statements=2))
    assert process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo))) == []


def test_lowering_the_backend_floor_is_reported(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, _base(floor=54))
    issues = process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo)))
    assert [issue.path for issue in issues] == ["backend/pyproject.toml"]
    assert issues[0].evidence == "fail_under 54 -> 52"
    assert process.COVERAGE_RATCHET_RULE in issues[0].message


def test_lowering_a_vitest_threshold_names_the_metric(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, _base(statements=4))
    issues = process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo)))
    assert [(issue.path, issue.evidence) for issue in issues] == [("frontend/vitest.config.ts", "statements 4 -> 3")]


def test_removing_a_floor_counts_as_lowering_it(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(floors_repo, "tools/repo_governance/pyproject.toml", "[tool.coverage.report]\nshow_missing = true\n")
    _stub_git(monkeypatch, _base())
    issues = process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo)))
    assert [issue.evidence for issue in issues] == ["fail_under removed (was 73)"]


def test_a_change_record_naming_the_rule_permits_the_lowering(
    floors_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = "governance/history/changes/2026-08-18-lower-the-floor.json"
    _write(floors_repo, record, RECORD_NAMING_THE_RULE)
    _stub_git(monkeypatch, _base(floor=54), records=[record])
    assert process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo))) == []


def test_a_new_floor_source_has_nothing_to_regress_from(floors_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, {})
    assert process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo))) == []


def test_unknown_git_state_is_left_to_the_change_record_check(
    floors_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process, "_change_set", lambda scope: ([], [], False))
    assert process.check_coverage_floor_ratchet(CheckScope(ctx=Context.discover(floors_repo))) == []


def test_the_rule_is_wired_to_the_check(real_context: Context) -> None:
    from repo_governance.checks import iter_rules

    rule = next(rule for rule in iter_rules(real_context) if rule.id == process.COVERAGE_RATCHET_RULE)
    assert rule.check_impl == "checks.process.check_coverage_floor_ratchet"


def test_the_real_repository_floors_have_not_been_lowered_against_head(real_context: Context) -> None:
    """Against HEAD with a clean tree this is trivially quiet; against a dirty tree it is the gate."""
    issues = process.check_coverage_floor_ratchet(CheckScope(ctx=real_context))
    assert issues == [], [issue.evidence for issue in issues]
