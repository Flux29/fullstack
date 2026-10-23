"""ADR front matter is read as data, so it is tested as data.

`merge.build_components` derives every component's `decision_refs` from the `components`
list in each ADR. That makes the front matter a manifest input, not prose: a key governance
silently ignores, a date the YAML resolver turns into an object, or a supersession only one
side declares are all data corruption, and each one has a test here.

Delivery plans are read the same way, by the phase gate rather than by the merge: their
`Status:` line and `## Phase N` headings decide what the pipeline runs next, and sync indexes
them into Summary.md with the gate's own patterns. Those tests close the module.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from repo_governance.builders import (
    _PLAN_PHASE_PATTERN,
    _PLAN_STATUS_PATTERN,
    build_decision_index,
    build_plan_index,
    read_adr_front_matter,
)
from repo_governance.checks import CheckScope
from repo_governance.checks.documentation import (
    check_adr_consistency,
    check_adr_front_matter,
    check_decision_refs_are_derived,
)
from repo_governance.config import Context
from repo_governance.merge import build_components
from repo_governance.pipeline import sync


def write_adr(
    root: Path,
    identifier: str,
    *,
    title: str = "A decision",
    status: str = "accepted",
    components: tuple[str, ...] = ("kernel-thing",),
    supersedes: tuple[str, ...] = (),
    related_paths: tuple[str, ...] = (),
    policy_refs: tuple[str, ...] = (),
    extra: str = "",
    filename: str | None = None,
) -> Path:
    lines = ["---", f"id: {identifier}", f"title: {title}", f"status: {status}", "date: 2026-08-06"]

    def block(name: str, values: tuple[str, ...]) -> None:
        if values:
            lines.append(f"{name}:")
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append(f"{name}: []")

    block("components", components)
    block("supersedes", supersedes)
    block("related_paths", related_paths)
    block("policy_refs", policy_refs)
    if extra:
        lines.append(extra)
    lines += ["---", "", f"# {identifier} — {title}", "", "## Status", "", f"{status}.", ""]

    target = root / "docs" / "architecture" / "decisions" / (filename or f"{identifier}-a-decision.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def declare_component(root: Path, component_id: str, directory: str = "thing") -> None:
    path = root / directory / ".governance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": component_id, "kind": "governance", "purpose": "Test fixture.", "owns": [f"{directory}/**"]}),
        encoding="utf-8",
    )


def _scope(root: Path) -> CheckScope:
    return CheckScope(ctx=Context.discover(root))


# --- front matter parsing -------------------------------------------------------------


def test_front_matter_parses_lists_and_keeps_the_date_a_string(minimal_repo: Path) -> None:
    """YAML resolves an unquoted date to `datetime.date`; generated output must stay text."""
    path = write_adr(minimal_repo, "ADR-001", components=("alpha", "beta"))

    fields, problems = read_adr_front_matter(path)

    assert problems == []
    assert fields["components"] == ["alpha", "beta"]
    assert fields["date"].__class__.__name__ == "date"

    index = build_decision_index(Context.discover(minimal_repo))
    assert index["decisions"][0]["date"] == "2026-08-06"
    assert isinstance(index["decisions"][0]["date"], str)


def test_unknown_front_matter_key_is_reported_not_skipped(minimal_repo: Path) -> None:
    path = write_adr(minimal_repo, "ADR-001", extra="owner: someone")

    _, problems = read_adr_front_matter(path)

    assert any("owner" in problem for problem in problems)


def test_a_missing_required_key_is_reported(minimal_repo: Path) -> None:
    path = write_adr(minimal_repo, "ADR-001")
    path.write_text(path.read_text(encoding="utf-8").replace("title: A decision\n", ""), encoding="utf-8")

    _, problems = read_adr_front_matter(path)

    assert any("'title'" in problem for problem in problems)


def test_a_file_without_front_matter_is_reported_rather_than_crashing(minimal_repo: Path) -> None:
    target = minimal_repo / "docs" / "architecture" / "decisions" / "ADR-009-no-front-matter.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# ADR-009 — no front matter\n", encoding="utf-8")

    fields, problems = read_adr_front_matter(target)

    assert fields == {}
    assert problems and "front-matter" in problems[0].lower()


# --- index shape ----------------------------------------------------------------------


def test_superseded_by_is_derived_from_the_superseding_adr(minimal_repo: Path) -> None:
    """Only the successor declares the link; the loser is not edited to agree."""
    write_adr(minimal_repo, "ADR-001", status="superseded", filename="ADR-001-old.md")
    write_adr(minimal_repo, "ADR-002", status="accepted", supersedes=("ADR-001",), filename="ADR-002-new.md")

    index = build_decision_index(Context.discover(minimal_repo))
    by_id = {entry["id"]: entry for entry in index["decisions"]}

    assert by_id["ADR-001"]["superseded_by"] == ["ADR-002"]
    assert "superseded_by" not in by_id["ADR-002"]


def test_the_index_validates_including_the_deprecated_status(minimal_repo: Path, repo_root: Path) -> None:
    write_adr(minimal_repo, "ADR-001", status="deprecated", related_paths=("backend/**",), policy_refs=("some-rule",))

    index = build_decision_index(Context.discover(minimal_repo))
    schema = json.loads(
        (repo_root / "governance" / "schemas" / "decision-index.schema.json").read_text(encoding="utf-8")
    )

    jsonschema.validate(index, schema)


def test_the_index_points_at_the_docs_tree(real_context: Context) -> None:
    index = json.loads(real_context.paths.decision_index.read_text(encoding="utf-8"))

    assert index["decisions"], "the real repository must carry ADRs"
    for entry in index["decisions"]:
        assert entry["file"].startswith("docs/architecture/decisions/"), entry["file"]


# --- derivation -----------------------------------------------------------------------


def test_decision_refs_are_derived_from_adr_front_matter(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", components=("kernel-thing",))

    manifest, _ = build_components(Context.discover(minimal_repo))
    component = next(item for item in manifest["components"] if item["id"] == "kernel-thing")

    assert component["decision_refs"] == ["ADR-001"]


def test_a_superseded_decision_stops_binding_its_components(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", status="superseded", components=("kernel-thing",), filename="ADR-001-old.md")
    write_adr(minimal_repo, "ADR-002", components=("kernel-thing",), supersedes=("ADR-001",), filename="ADR-002-new.md")

    manifest, _ = build_components(Context.discover(minimal_repo))
    component = next(item for item in manifest["components"] if item["id"] == "kernel-thing")

    assert component["decision_refs"] == ["ADR-002"]


def test_the_real_repository_derives_every_declared_link(real_context: Context) -> None:
    """Every ADR-declared link appears on the component, and nothing else does."""
    index = json.loads(real_context.paths.decision_index.read_text(encoding="utf-8"))
    declared: set[tuple[str, str]] = {
        (component, entry["id"])
        for entry in index["decisions"]
        if entry["status"] in {"accepted", "proposed"}
        for component in entry.get("components", [])
    }

    manifest, _ = build_components(real_context)
    derived = {
        (component["id"], ref) for component in manifest["components"] for ref in component.get("decision_refs", [])
    }

    assert derived == declared


def test_a_hand_declared_decision_ref_is_reported(minimal_repo: Path) -> None:
    path = minimal_repo / "thing" / ".governance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": "kernel-thing",
                "kind": "governance",
                "purpose": "Test fixture.",
                "owns": ["thing/**"],
                "decision_refs": ["ADR-001"],
            }
        ),
        encoding="utf-8",
    )

    issues = check_decision_refs_are_derived(_scope(minimal_repo))

    assert any("decision_refs by hand" in issue.message for issue in issues)


def test_annotations_inside_a_nested_checkout_are_not_compiled(minimal_repo: Path) -> None:
    """A git worktree under this tree is another commit's repository, not this one's."""
    declare_component(minimal_repo, "kernel-thing")
    nested = minimal_repo / ".claude" / "worktrees" / "other"
    nested.mkdir(parents=True)
    (nested / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    declare_component(nested, "kernel-thing", directory="backend")

    manifest, conflicts = build_components(Context.discover(minimal_repo))

    assert [item["id"] for item in manifest["components"]] == ["kernel-thing"]
    assert conflicts == [], "the nested copy must not read as a second declaration"
    assert manifest["components"][0]["annotation_path"] == "thing/.governance.json"


# --- consistency checks ---------------------------------------------------------------


def test_an_unknown_component_in_an_adr_is_reported(minimal_repo: Path) -> None:
    write_adr(minimal_repo, "ADR-001", components=("nobody-declares-this",))

    issues = check_adr_consistency(_scope(minimal_repo))

    assert any("no component declares" in issue.message for issue in issues)


def test_superseding_a_live_adr_is_reported(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", status="accepted", filename="ADR-001-old.md")
    write_adr(minimal_repo, "ADR-002", supersedes=("ADR-001",), filename="ADR-002-new.md")

    issues = check_adr_consistency(_scope(minimal_repo))

    assert any("still has status 'accepted'" in issue.message for issue in issues)


def test_a_superseded_adr_with_no_successor_is_reported(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", status="superseded")

    issues = check_adr_consistency(_scope(minimal_repo))

    assert any("no ADR lists it in `supersedes`" in issue.message for issue in issues)


def test_superseding_a_missing_adr_is_reported(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", supersedes=("ADR-404",))

    issues = check_adr_consistency(_scope(minimal_repo))

    assert any("ADR-404" in issue.message and "does not exist" in issue.message for issue in issues)


def test_a_policy_ref_that_does_not_resolve_is_reported(minimal_repo: Path) -> None:
    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", policy_refs=("no-such-rule",))

    issues = check_adr_consistency(_scope(minimal_repo))

    assert any("no-such-rule" in issue.message for issue in issues)


def test_a_declared_policy_ref_resolves(real_context: Context) -> None:
    """The real tree's ADRs carry no unresolved policy or component reference."""
    issues = check_adr_consistency(CheckScope(ctx=real_context))

    assert issues == [], "\n".join(f"{issue.path}: {issue.message}" for issue in issues)


def test_an_id_that_disagrees_with_the_filename_is_reported(minimal_repo: Path) -> None:
    write_adr(minimal_repo, "ADR-001", filename="ADR-007-mislabelled.md")

    issues = check_adr_front_matter(_scope(minimal_repo))

    assert any("filename says" in issue.message for issue in issues)


def test_two_files_declaring_one_id_are_reported(minimal_repo: Path) -> None:
    write_adr(minimal_repo, "ADR-001", filename="ADR-001-first.md")
    write_adr(minimal_repo, "ADR-001", filename="ADR-001-second.md")

    issues = check_adr_front_matter(_scope(minimal_repo))

    assert any("declared by two files" in issue.message for issue in issues)


def test_the_real_repository_front_matter_is_clean(real_context: Context) -> None:
    issues = check_adr_front_matter(CheckScope(ctx=real_context))

    assert issues == [], "\n".join(f"{issue.path}: {issue.message}" for issue in issues)


# --- context rendering ----------------------------------------------------------------


def test_context_names_decisions_with_title_status_and_path(minimal_repo: Path) -> None:
    from repo_governance.renderers.context import render_context

    declare_component(minimal_repo, "kernel-thing")
    write_adr(minimal_repo, "ADR-001", title="Something binding", components=("kernel-thing",))
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    rendered = render_context(ctx, ["thing/file.py"], task="x", token_budget=10_000)

    assert "ADR-001" in rendered
    assert "Something binding" in rendered
    assert "(accepted)" in rendered
    assert "docs/architecture/decisions/" in rendered


def test_related_paths_reach_context_without_a_component(minimal_repo: Path) -> None:
    """A cross-cutting decision has no owning component; the glob is its only route."""
    from repo_governance.renderers.context import render_context

    write_adr(minimal_repo, "ADR-001", title="Cross cutting", components=(), related_paths=("orphan/**",))
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    rendered = render_context(ctx, ["orphan/thing.py"], task="x", token_budget=10_000)

    assert "ADR-001" in rendered


# --- decision propose -----------------------------------------------------------------

PROPOSAL_DATE = "2026-08-22"


@pytest.fixture
def proposal_repo(minimal_repo: Path) -> Path:
    """The minimal kernel repo with the ADR scaffold present and a change session open.

    `decision propose` refuses to run outside a session and copies the template to produce
    the body, so every test of a successful proposal needs both. Pinning the date keeps the
    written front matter comparable.
    """
    from repo_governance.session import start

    ctx = Context.discover(minimal_repo)
    template = ctx.paths.governance / "templates" / "adr.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("---\nid: ADR-NNN\n---\n\n# ADR-NNN — <title>\n", encoding="utf-8")
    start(ctx, summary="s", reason="r", date=PROPOSAL_DATE)
    return minimal_repo


def test_decision_propose_refuses_outside_a_change_session(minimal_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from repo_governance.cli import main

    monkeypatch.chdir(minimal_repo)
    result = CliRunner().invoke(main, ["decision", "propose", "--title", "T", "--components", "kernel-thing"])

    assert result.exit_code != 0
    assert "change session" in result.output


def test_decision_propose_allocates_the_next_free_id(proposal_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from repo_governance.cli import main

    for number in (1, 2, 3, 4, 5):
        write_adr(proposal_repo, f"ADR-00{number}", filename=f"ADR-00{number}-a-decision.md")

    monkeypatch.chdir(proposal_repo)
    result = CliRunner().invoke(
        main,
        ["decision", "propose", "--title", "Next one", "--components", "kernel-thing", "--date", PROPOSAL_DATE],
    )

    assert result.exit_code == 0, result.output
    written = proposal_repo / "docs" / "architecture" / "decisions" / "ADR-006-next-one.md"
    assert written.is_file()
    fields, problems = read_adr_front_matter(written)
    assert problems == []
    assert fields["id"] == "ADR-006"
    assert fields["status"] == "proposed"
    assert fields["components"] == ["kernel-thing"]


def test_decision_propose_keeps_a_matching_related_path_glob_verbatim(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `related_paths` glob must survive argument parsing as written.

    Click simulates Unix shell expansion whenever it reads `sys.argv` itself on Windows, so
    a pattern matching tracked files was replaced by the files it matched and then rejected
    as unexpected positional arguments — while a pattern matching nothing came through
    intact, which disguised a systematic failure as a shell quirk.

    Two details matter for this to be a regression test rather than decoration. `CliRunner`
    passes `args` explicitly and click only expands when `args is None`, so the runner used
    by the tests above cannot see the bug; this drives `sys.argv` instead. And the expansion
    is keyed on `os.name`, so the Windows branch is forced here — otherwise the guard would
    only ever be exercised on a Windows runner.
    """
    import os as os_module
    import sys

    import click.core

    from repo_governance.cli import main

    # `**` expands to the directory plus everything under it, so the tree has to exist.
    tools = proposal_repo / "backend" / "app" / "agents" / "tools"
    tools.mkdir(parents=True)
    (tools / "ask_user_tool.py").write_text("", encoding="utf-8")

    pattern = "backend/app/agents/tools/**"
    monkeypatch.chdir(proposal_repo)

    class _WindowsOs:
        """Report Windows to `click.core` alone; every other attribute is the real module."""

        name = "nt"

        def __getattr__(self, item: str) -> object:
            return getattr(os_module, item)

    argv = ["governance", "decision", "propose", "--title", "Verbatim", "--components", "kernel-thing"]
    argv += ["--date", PROPOSAL_DATE, "--related-path", pattern]
    monkeypatch.setattr(click.core, "os", _WindowsOs())
    monkeypatch.setattr(sys, "argv", argv)

    main.main(standalone_mode=False)

    written = proposal_repo / "docs" / "architecture" / "decisions" / "ADR-001-verbatim.md"
    assert written.is_file()
    fields, problems = read_adr_front_matter(written)
    assert problems == []
    assert fields["related_paths"] == [pattern]


# --- delivery plans -------------------------------------------------------------------


def write_plan(
    root: Path,
    number: str = "001",
    *,
    adr: str = "ADR-001 (this delivery implements it)",
    status: str | None = "phase 0 of 2 complete",
    phases: int = 2,
) -> Path:
    """The lines of a plan that the gate and the index read."""
    lines = [f"# Plan {number} — A delivery", f"ADR: {adr}"]
    if status is not None:
        lines.append(f"Status: {status}")
    lines += [f"## Phase {phase}: Part {phase}" for phase in range(1, phases + 1)]
    target = root / "docs" / "plans" / f"{number}-a-delivery.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _only_plan(root: Path) -> dict:
    (plan,) = build_plan_index(Context.discover(root))
    return plan


def test_a_plan_is_indexed_with_topic_status_phases_and_adr(minimal_repo: Path) -> None:
    write_plan(minimal_repo)
    readme = minimal_repo / "docs" / "plans" / "README.md"
    readme.write_text("# Delivery plans\n\nStatus: the README is not a plan\n", encoding="utf-8")

    assert _only_plan(minimal_repo) == {
        "id": "Plan 001",
        "topic": "A delivery",
        "status": "phase 0 of 2 complete",
        "adr_refs": ["ADR-001"],
        "state": "in-flight",
        "problems": [],
        "file": "docs/plans/001-a-delivery.md",
    }


@pytest.mark.parametrize(
    ("status", "phases", "state"),
    [
        ("phase 1 of 2 complete", 2, "in-flight"),
        ("phase 2 of 2 complete", 2, "complete"),
        # The gate recomputes the total from the headings, so a phase added mid-delivery reopens the plan.
        ("phase 2 of 2 complete", 3, "in-flight"),
        # A form the gate has not written yet; its first bump overwrites it.
        ("drafting", 2, "in-flight"),
    ],
)
def test_state_follows_the_completed_phase_count(minimal_repo: Path, status: str, phases: int, state: str) -> None:
    write_plan(minimal_repo, status=status, phases=phases)

    assert _only_plan(minimal_repo)["state"] == state


def test_a_plan_the_gate_cannot_run_is_unreadable_and_says_why(minimal_repo: Path) -> None:
    write_plan(minimal_repo, status=None, phases=0)

    plan = _only_plan(minimal_repo)

    assert plan["state"] == "unreadable"
    assert plan["problems"] == [
        "no `Status:` line, so the gate cannot record progress",
        "no `## Phase N` headings, so the gate finds nothing to run",
    ]


def test_a_plan_without_an_adr_has_no_refs(minimal_repo: Path) -> None:
    write_plan(minimal_repo, adr="none — no architectural decision in this work")

    assert _only_plan(minimal_repo)["adr_refs"] == []


def test_summary_lists_deliveries_by_state_with_their_adr_status(minimal_repo: Path) -> None:
    write_adr(minimal_repo, "ADR-001", status="proposed", components=())
    write_plan(minimal_repo, "001", adr="ADR-001, then ADR-009")
    write_plan(minimal_repo, "002", status="phase 2 of 2 complete")
    write_plan(minimal_repo, "003", status=None)
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    deliveries = ctx.paths.summary.read_text(encoding="utf-8").split("\n## Deliveries (3)\n", 1)[1]

    assert "- **Plan 001** — A delivery\n  - Status: phase 0 of 2 complete\n" in deliveries
    assert "  - Implements: ADR-001 (proposed), ADR-009 (not in the decision index)\n" in deliveries
    assert "### Not readable by the phase gate\n\n- **Plan 003**" in deliveries
    assert "### Complete (1)" in deliveries
    assert "Plan 002" not in deliveries


def test_a_repository_without_plans_renders_no_deliveries_section(minimal_repo: Path) -> None:
    ctx = Context.discover(minimal_repo)
    sync(ctx)

    assert "## Deliveries" not in ctx.paths.summary.read_text(encoding="utf-8")


def test_the_plan_patterns_are_the_phase_gate_s_own(repo_root: Path) -> None:
    """Summary.md must report what the gate sees, so a change to the gate's contract fails here first."""
    gate = (repo_root / ".claude" / "skills" / "phased-delivery" / "scripts" / "watch-phase-gate.ps1").read_text(
        encoding="utf-8"
    )

    assert f"'(?m){_PLAN_PHASE_PATTERN.pattern}'" in gate
    assert f"'(?m){_PLAN_STATUS_PATTERN.pattern.replace('(.*)', '.*')}'" in gate
    assert '"Status: phase $Phase of $total complete"' in gate


def test_the_real_repository_plans_are_readable_by_the_gate(real_context: Context) -> None:
    plans = build_plan_index(real_context)

    assert plans, "the real repository must carry a delivery plan"
    for plan in plans:
        assert plan["problems"] == [], f"{plan['file']}: {plan['problems']}"
