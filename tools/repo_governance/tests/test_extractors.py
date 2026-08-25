"""Extractor behaviour, especially on inputs the real repository must never contain.

The recurring theme: a parser failure is reported as an unknown, never as an absence. An
extractor that returns an empty list on malformed input is indistinguishable from one
reading a source that genuinely contains nothing, and the second is far rarer.
"""

from __future__ import annotations

from pathlib import Path

from repo_governance.config import Context
from repo_governance.extractors.alembic import extract_revisions
from repo_governance.extractors.compose import extract_compose
from repo_governance.extractors.configuration import parse_env_template, template_sections
from repo_governance.extractors.makefile import extract_makefile
from repo_governance.extractors.python_ast import extract_frozenset_members, extract_settings

FIXTURES = Path(__file__).parent / "fixtures"


# ----------------------------------------------------------------- backend path templates


def test_nested_template_query_fragment_never_reaches_the_path_template() -> None:
    """`${qs ? `?${qs}` : ""}` truncates the regex capture mid-interpolation; the
    unterminated `${qs` tail is query assembly and must not survive normalization —
    it broke the site-chain join for every parameterized files route."""
    from repo_governance.extractors.typescript_ast import _normalize_backend_path

    assert _normalize_backend_path("/api/v1/files/${fileId}${qs") == "/api/v1/files/{param}"
    assert _normalize_backend_path("/api/v1/files/${fileId}?${qs}") == "/api/v1/files/{param}"
    assert _normalize_backend_path("/api/v1/rag/search") == "/api/v1/rag/search"


# --------------------------------------------------------------------------- env templates


def test_duplicate_keys_parse_last_wins_and_raise_a_finding(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text("A=first\nB=other\nA=second\n", encoding="utf-8")

    result = parse_env_template(template)

    assert result.entries["A"].value == "second", "a real dotenv loader takes the last assignment"
    assert result.duplicates == [("A", [1, 3])]


def test_a_concatenated_line_is_reported_not_skipped(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text("GOOD=1\nthis is not an assignment\nALSO_GOOD=2\n", encoding="utf-8")

    result = parse_env_template(template)

    assert [number for number, _ in result.malformed] == [2]
    assert set(result.entries) == {"GOOD", "ALSO_GOOD"}


def test_section_banners_do_not_become_descriptions(tmp_path: Path) -> None:
    """The first variable under a banner must not inherit the section name as its description."""
    template = tmp_path / ".env.example"
    template.write_text("# === Project ===\nNAME=x\n# real description\nOTHER=y\n", encoding="utf-8")

    result = parse_env_template(template)

    assert result.entries["NAME"].description == ""
    assert result.entries["OTHER"].description == "real description"


def test_inline_comments_are_stripped_from_values(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text("MODE=recursive  # recursive, markdown, or fixed\n", encoding="utf-8")

    assert parse_env_template(template).entries["MODE"].value == "recursive"


def test_template_sections_preserve_file_order(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text("# === One ===\nA=1\n\n# === Two ===\nB=2\nC=3\n", encoding="utf-8")

    assert template_sections(template) == [("One", ["A"]), ("Two", ["B", "C"])]


def test_a_missing_template_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    assert parse_env_template(tmp_path / "absent").entries == {}


# ------------------------------------------------------------------------------ settings


def test_settings_fields_and_computed_properties_are_distinguished(tmp_path: Path) -> None:
    module = tmp_path / "config.py"
    module.write_text(
        "GITHUB_MCP_READ_ONLY_TOOLS = frozenset({'b', 'a'})\n"
        "class Settings(BaseSettings):\n"
        "    NAME: str = 'x'\n"
        "    REQUIRED_ONE: str\n"
        "    COUNT: int = 5\n"
        "    lowercase_ignored: str = 'y'\n"
        "    @computed_field\n"
        "    def DATABASE_URL(self) -> str:\n"
        "        return 'derived'\n",
        encoding="utf-8",
    )

    result = extract_settings(module)
    names = {field.name for field in result.fields}

    assert names == {"NAME", "REQUIRED_ONE", "COUNT"}
    assert "DATABASE_URL" in result.computed
    assert not any(field.name == "lowercase_ignored" for field in result.fields)
    assert next(f for f in result.fields if f.name == "REQUIRED_ONE").has_default is False
    assert next(f for f in result.fields if f.name == "COUNT").default == "5"


def test_a_syntax_error_is_an_unknown_not_an_empty_result(tmp_path: Path) -> None:
    module = tmp_path / "config.py"
    module.write_text("class Settings(BaseSettings:\n", encoding="utf-8")

    result = extract_settings(module)

    assert result.fields == []
    assert result.unknowns, "a parse failure must be reported, not read as 'no settings'"


def test_a_non_literal_allowlist_reads_as_unknown_not_empty(tmp_path: Path) -> None:
    """An unreadable allowlist must never be mistaken for an empty one."""
    module = tmp_path / "config.py"
    module.write_text("TOOLS = frozenset(load_from_somewhere())\n", encoding="utf-8")

    assert extract_frozenset_members(module, "TOOLS") is None


def test_a_literal_allowlist_is_read(tmp_path: Path) -> None:
    module = tmp_path / "config.py"
    module.write_text("TOOLS = frozenset({'read', 'list'})\n", encoding="utf-8")

    assert extract_frozenset_members(module, "TOOLS") == ["list", "read"]


# ------------------------------------------------------------------------------- alembic


def _revision(directory: Path, name: str, revision: str, down: str | None) -> None:
    rendered = "None" if down is None else repr(down)
    (directory / f"{name}.py").write_text(f"revision = {revision!r}\ndown_revision = {rendered}\n", encoding="utf-8")


def _alembic_repo(root: Path) -> Context:
    (root / "governance").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "governance.toml").write_text(
        '[governance]\nversion = "1.0.0"\nschema_version = 1\n', encoding="utf-8"
    )
    (root / "backend" / "alembic" / "versions").mkdir(parents=True, exist_ok=True)
    return Context.discover(root)


def test_a_linear_chain_has_one_head(tmp_path: Path) -> None:
    ctx = _alembic_repo(tmp_path / "repo")
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    _revision(versions, "0001_start", "0001", None)
    _revision(versions, "0002_next", "0002", "0001")

    graph = extract_revisions(ctx)

    assert graph.heads == ["0002"]
    assert graph.roots == ["0001"]
    assert graph.orphans == []
    assert graph.cycles == []


def test_filename_numbering_gaps_are_not_a_problem(tmp_path: Path) -> None:
    """Contiguity is graph connectivity, never filename numbering."""
    ctx = _alembic_repo(tmp_path / "repo")
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    _revision(versions, "0001_start", "0001", None)
    _revision(versions, "0009_jump", "0009", "0001")
    _revision(versions, "0004_5_interleaved", "0004_5", "0009")

    graph = extract_revisions(ctx)

    assert graph.heads == ["0004_5"]
    assert graph.orphans == []


def test_a_branch_produces_two_heads(tmp_path: Path) -> None:
    ctx = _alembic_repo(tmp_path / "repo")
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    _revision(versions, "0001_start", "0001", None)
    _revision(versions, "0002_a", "0002a", "0001")
    _revision(versions, "0002_b", "0002b", "0001")

    graph = extract_revisions(ctx)

    assert graph.heads == ["0002a", "0002b"], "branches are legal; an unexpected head is what is not"


def test_a_merge_revision_collapses_the_branch(tmp_path: Path) -> None:
    ctx = _alembic_repo(tmp_path / "repo")
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    _revision(versions, "0001_start", "0001", None)
    _revision(versions, "0002_a", "0002a", "0001")
    _revision(versions, "0002_b", "0002b", "0001")
    (versions / "0003_merge.py").write_text("revision = '0003'\ndown_revision = ('0002a', '0002b')\n", encoding="utf-8")

    graph = extract_revisions(ctx)

    assert graph.heads == ["0003"]


def test_a_missing_parent_is_reported_as_disconnected(tmp_path: Path) -> None:
    ctx = _alembic_repo(tmp_path / "repo")
    versions = ctx.repo_root / "backend" / "alembic" / "versions"
    _revision(versions, "0002_orphan", "0002", "0001-which-was-deleted")

    graph = extract_revisions(ctx)

    assert graph.orphans, "a dangling down_revision must surface"


def test_the_real_migration_chain_is_connected(real_context: Context) -> None:
    graph = extract_revisions(real_context)

    assert graph.revisions, "the repository has migrations"
    assert graph.orphans == []
    assert graph.cycles == []
    assert len(graph.heads) == 1


# ------------------------------------------------------------------------------ makefile


def test_canonical_stacks_resolve_nested_variable_references(tmp_path: Path) -> None:
    """COMPOSE_FRONTEND is defined in terms of COMPOSE_DEV, which is defined in terms of BASE."""
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "COMPOSE_BASE := docker compose -f docker-compose.yml\n"
        "COMPOSE_DEV := $(COMPOSE_BASE) -f docker-compose.dev.yml\n"
        "COMPOSE_FRONTEND := $(COMPOSE_DEV) -f docker-compose.frontend.yml --profile frontend\n"
        "some-target:\n"
        "\techo hi\n",
        encoding="utf-8",
    )

    result = extract_makefile(makefile)
    by_id = {stack.id: stack for stack in result.stacks}

    assert by_id["frontend"].compose_files == (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.frontend.yml",
    )
    assert by_id["frontend"].profiles_always_on == ("frontend",)
    assert "some-target" in result.targets


def test_an_unnamed_compose_stack_is_reported(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("COMPOSE_EXPERIMENT := docker compose -f other.yml\n", encoding="utf-8")

    assert extract_makefile(makefile).unknowns


def test_the_real_makefile_defines_four_canonical_stacks(real_context: Context) -> None:
    result = extract_makefile(real_context.repo_root / "Makefile")

    assert [stack.id for stack in result.stacks] == ["base", "dev", "frontend", "prod"]
    assert result.unknowns == []


# ------------------------------------------------------------------- compose volume mounts


def _compose_repo(minimal_repo: Path, compose_yaml: str) -> Context:
    (minimal_repo / "Makefile").write_text(
        "COMPOSE_BASE := docker compose -f docker-compose.yml\n",
        encoding="utf-8",
    )
    (minimal_repo / "docker-compose.yml").write_text(compose_yaml, encoding="utf-8")
    return Context.discover(minimal_repo)


def test_long_syntax_volume_mounts_are_parsed_not_dropped(minimal_repo: Path) -> None:
    """A long-syntax mount vanished from services.json while `docker compose config` still
    rendered it — the 2026-08-25 sandboxd change had to keep short syntax because of this.
    The anchor alias in the target is part of the regression: resolving it is what makes
    long syntax worth using there."""
    ctx = _compose_repo(
        minimal_repo,
        "x-workspace-root: &workspace-root /var/lib/docker/volumes/example/_data\n"
        "services:\n"
        "  app:\n"
        "    image: example:latest\n"
        "    volumes:\n"
        "      - ./src:/app/src\n"
        "      - type: volume\n"
        "        source: workspaces\n"
        "        target: *workspace-root\n"
        "      - type: bind\n"
        "        source: /var/run/docker.sock\n"
        "        target: /var/run/docker.sock\n"
        "volumes:\n"
        "  workspaces: {}\n",
    )

    result = extract_compose(ctx)

    assert result.services["app"].volumes == [
        {"name": "./src", "target": "/app/src", "class": "bind"},
        {"name": "workspaces", "target": "/var/lib/docker/volumes/example/_data", "class": "named"},
        {"name": "/var/run/docker.sock", "target": "/var/run/docker.sock", "class": "bind"},
    ]
    assert result.unknowns == []


def test_an_unparseable_volume_entry_is_an_unknown_not_an_absence(minimal_repo: Path) -> None:
    ctx = _compose_repo(
        minimal_repo,
        "services:\n"
        "  app:\n"
        "    image: example:latest\n"
        "    volumes:\n"
        "      - type: tmpfs\n"
        "        target: /scratch\n",
    )

    result = extract_compose(ctx)

    assert result.services["app"].volumes == []
    assert any("could not parse volume entry" in u and "app" in u for u in result.unknowns)


# --- the invariant scanner and the map-vs-territory sampler ---------------------------


def test_scan_finds_repository_imports_commits_and_utcnow(tmp_path: Path) -> None:
    from repo_governance.extractors.python_ast import scan_invariants

    source = tmp_path / "route.py"
    source.write_text(
        "from app.repositories import user_repo\n"
        "import app.repositories.conversation\n"
        "async def handler(db):\n"
        "    await db.commit()\n"
        "    return datetime.utcnow()\n",
        encoding="utf-8",
    )

    scan = scan_invariants(source)

    assert scan.repository_imports == ["app.repositories", "app.repositories.conversation"]
    assert scan.commit_calls == 1
    assert scan.utcnow_calls == 1
    assert scan.parse_error is None


def test_scan_collects_uppercase_settings_refs_only(tmp_path: Path) -> None:
    from repo_governance.extractors.python_ast import scan_invariants

    source = tmp_path / "service.py"
    source.write_text(
        "def f():\n"
        "    key = settings.CHANNEL_ENCRYPTION_KEY\n"
        "    other = settings.API_KEY\n"
        "    method = settings.model_dump()\n",
        encoding="utf-8",
    )

    assert scan_invariants(source).settings_refs == ["API_KEY", "CHANNEL_ENCRYPTION_KEY"]


def test_scan_reports_a_syntax_error_never_an_empty_result(tmp_path: Path) -> None:
    from repo_governance.extractors.python_ast import scan_invariants

    source = tmp_path / "broken.py"
    source.write_text("def f(:\n", encoding="utf-8")

    assert scan_invariants(source).parse_error is not None


def test_sample_is_reproducible_per_seed_in_the_real_repository(real_context: Context) -> None:
    from repo_governance.builders import sample_map_claims

    first = sample_map_claims(real_context, count=10, seed="fixed-seed")
    second = sample_map_claims(real_context, count=10, seed="fixed-seed")
    other = sample_map_claims(real_context, count=10, seed="another-seed")

    assert first["status"] == "ok"
    assert first["sampled"] == 10
    assert first["files"] == second["files"]
    assert first["files"] != other["files"]
    assert first["unreadable"] == []


def test_sample_reports_unknown_when_git_answers_for_an_enclosing_repository(tmp_path: Path) -> None:
    import shutil

    from repo_governance.builders import sample_map_claims

    root = tmp_path / "repo"
    (root / "governance").mkdir(parents=True)
    shutil.copy(
        Path(__file__).resolve().parents[3] / "governance" / "governance.toml",
        root / "governance" / "governance.toml",
    )

    report = sample_map_claims(Context.discover(root), count=5, seed="s")

    assert report["status"] == "unknown"
