"""skills-check: reference integrity for the .claude corpus.

Synthetic cases prove each classification rule on inputs the real repository must never
contain; the real-repo case is the ratchet - the committed corpus must scan with zero
findings, so this suite is a second enforcement layer beside the strict CLI default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_governance.config import Context
from repo_governance.skills import SKIP_FILES, analyse_skill_references

FRONTMATTER = "---\nname: {name}\ndescription: A test skill.\n---\n"

MAKEFILE_STUB = """\
lint:
\techo lint
test:
\techo test
preflight-model:
\techo probe
preflight-volumes:
\techo probe
GOVERNANCE := placeholder
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(root: Path, name: str, body: str) -> None:
    _write(root, f".claude/skills/{name}/SKILL.md", FRONTMATTER.format(name=name) + body)


@pytest.fixture
def skills_repo(minimal_repo: Path) -> Path:
    """The minimal kernel repo plus the anchors the corpus legitimately cites."""
    _write(minimal_repo, "Makefile", MAKEFILE_STUB)
    _write(minimal_repo, "backend/app/real.py", "x = 1\n")
    _write(minimal_repo, "docs/index.md", "# docs\n")
    return minimal_repo


def _run(root: Path) -> dict:
    return analyse_skill_references(Context.discover(root))


def _kinds(report: dict) -> list[tuple[str, str]]:
    return [(item["kind"], item["token"]) for item in report["findings"]]


def test_existing_path_passes_and_missing_path_is_flagged(skills_repo: Path):
    _skill(skills_repo, "demo", "Read `backend/app/real.py` and `docs/rag.md`.\n")
    report = _run(skills_repo)
    assert report["status"] == "ok"
    assert _kinds(report) == [("missing-path", "docs/rag.md")]


def test_glob_needs_at_least_one_match(skills_repo: Path):
    _skill(skills_repo, "demo", "Own `backend/app/**` but never `backend/nothing/**`.\n")
    report = _run(skills_repo)
    assert _kinds(report) == [("missing-path", "backend/nothing/**")]


def test_placeholder_tokens_are_ignored(skills_repo: Path):
    _skill(
        skills_repo,
        "demo",
        'Scaffold `backend/app/<entity>.py`, pass `PATHS="..."`, then `backend/app/{name}.py`.\n'
        "Route `/api/v1/things` and flag `--collection` stay untouched.\n",
    )
    report = _run(skills_repo)
    assert report["findings"] == []


def test_make_targets_resolve_against_the_makefile(skills_repo: Path):
    _skill(
        skills_repo,
        "demo",
        "```bash\nmake lint\nmake nope\nmake preflight-*\n```\nInline `make test` too.\n",
    )
    report = _run(skills_repo)
    assert _kinds(report) == [("unknown-make-target", "nope")]


def test_validator_table_ids_and_commands_are_cross_checked(skills_repo: Path):
    _skill(
        skills_repo,
        "demo",
        "| ID | Command |\n"
        "| --- | --- |\n"
        "| `backend-lint` | `make lint` |\n"
        "| `backend-unit` | `make wrong` |\n"
        "| `bogus-validator` | `make lint` |\n",
    )
    report = _run(skills_repo)
    assert ("unknown-validator", "bogus-validator") in _kinds(report)
    assert ("validator-command-mismatch", "backend-unit") in _kinds(report)
    assert all(token != "backend-lint" for _, token in _kinds(report))


def test_validator_mention_in_prose_is_checked(skills_repo: Path):
    _skill(skills_repo, "demo", "Run validator `backend-lint`, never validator `bogus-thing`.\n")
    report = _run(skills_repo)
    assert _kinds(report) == [("unknown-validator", "bogus-thing")]


def test_bare_filenames_checked_in_skills_but_not_rules(skills_repo: Path):
    _skill(skills_repo, "demo", "See `real.py` and `ghost_tasks.py`.\n")
    _write(skills_repo, ".claude/rules/naming.md", "Name files like `ghost_tasks.py`.\n")
    report = _run(skills_repo)
    assert _kinds(report) == [("missing-path", "ghost_tasks.py")]
    assert report["findings"][0]["file"] == ".claude/skills/demo/SKILL.md"


def test_paths_are_still_checked_in_rules(skills_repo: Path):
    _write(skills_repo, ".claude/rules/layout.md", "Docs live in `docs/rag.md`.\n")
    report = _run(skills_repo)
    assert _kinds(report) == [("missing-path", "docs/rag.md")]


def test_frontmatter_name_must_match_directory(skills_repo: Path):
    _write(
        skills_repo,
        ".claude/skills/demo/SKILL.md",
        "---\nname: other\ndescription: Mismatched.\n---\nBody.\n",
    )
    report = _run(skills_repo)
    assert ("skill-integrity", "other") in _kinds(report)


def test_skill_directory_without_skill_md_is_flagged(skills_repo: Path):
    _write(skills_repo, ".claude/skills/empty/notes.md", "No SKILL.md here.\n")
    report = _run(skills_repo)
    assert ("skill-integrity", "empty") in _kinds(report)


def test_report_is_deterministic(skills_repo: Path):
    _skill(skills_repo, "demo", "Cite `docs/rag.md`, `ghost_tasks.py`, and `make nope`.\n")
    first = json.dumps(_run(skills_repo), sort_keys=True)
    second = json.dumps(_run(skills_repo), sort_keys=True)
    assert first == second


def test_docs_prose_citations_are_checked(skills_repo: Path):
    """The regression this root was added for: a path that moved, still named in prose."""
    _write(
        skills_repo,
        "docs/howto/sync.md",
        "The registry lives in `app/rag/connectors/__init__.py` today.\n",
    )
    report = _run(skills_repo)
    assert _kinds(report) == [("missing-path", "app/rag/connectors/__init__.py")]
    assert report["findings"][0]["file"] == "docs/howto/sync.md"


def test_docs_relative_paths_resolve_under_backend(skills_repo: Path):
    _write(skills_repo, "docs/howto/sync.md", "Edit `app/real.py` to add the field.\n")
    assert _run(skills_repo)["findings"] == []


def test_docs_fenced_examples_are_not_citations(skills_repo: Path):
    """Howtos scaffold files that do not exist yet; fences keep them illustrative."""
    _write(
        skills_repo,
        "docs/howto/endpoint.md",
        "Real file: `backend/app/real.py`.\n\n"
        "```python\n# app/schemas/notification.py\nfrom app.db.models.notification import Note\n```\n\n"
        "```bash\ncat backend/app/invented.py\n```\n",
    )
    assert _run(skills_repo)["findings"] == []


def test_bare_filenames_are_not_checked_in_docs(skills_repo: Path):
    """Same rule as rules/: only skills and commands treat a bare name as a citation."""
    _write(skills_repo, "docs/howto/tasks.md", "Create `ghost_tasks.py` next to the worker.\n")
    assert _run(skills_repo)["findings"] == []


def test_make_token_in_a_non_shell_fence_is_prose(skills_repo: Path):
    """`Never make up information` inside a prompt template is not a Makefile target."""
    _skill(
        skills_repo,
        "demo",
        '```python\nPROMPT = """\n- Never make up information\n"""\n```\n\n```bash\nmake lint\n```\n',
    )
    assert _run(skills_repo)["findings"] == []


def test_a_tracked_template_sibling_explains_absence(skills_repo: Path):
    """A doc explaining configuration must name backend/.env; it never exists in a checkout."""
    _write(
        skills_repo,
        "docs/config.md",
        "Copy `backend/.env.example` to `backend/.env`, then edit `backend/.env.local`.\n",
    )
    _write(skills_repo, "backend/.env.example", "KEY=\n")
    _write(skills_repo, "backend/.env.local.example", "KEY=\n")
    assert _run(skills_repo)["findings"] == []


def test_absence_without_a_template_sibling_is_still_flagged(skills_repo: Path):
    _write(skills_repo, "docs/config.md", "Edit `backend/.secrets` before starting.\n")
    assert _kinds(_run(skills_repo)) == [("missing-path", "backend/.secrets")]


def test_frozen_specifications_are_skipped(skills_repo: Path):
    """A record of a past state is history, not rot — see SKIP_FILES."""
    for rel in SKIP_FILES:
        _write(skills_repo, rel, "Hooks live in `backend/.pre-commit-config.yaml`.\n")
    report = _run(skills_repo)
    assert report["findings"] == []


def test_real_repository_corpus_has_zero_findings(real_context: Context):
    """The ratchet: the committed corpus stays citation-clean from here on.

    A finding here means a skill, command, or rule cites something that does not
    exist. Fix the text or the territory - never this assertion.
    """
    report = analyse_skill_references(real_context)
    assert report["status"] == "ok"
    assert report["files_scanned"] >= 15
    assert report["findings"] == []
