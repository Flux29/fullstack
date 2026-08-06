"""Shared fixtures.

Tests run against two things: the real repository (to prove the committed governance tree
is valid) and a synthetic minimal repository in a temp directory (to prove behaviour on
inputs the real repository must never contain, such as malformed documents).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repo_governance.config import Context

FIXTURES = Path(__file__).parent / "fixtures"


def _repo_root() -> Path:
    """The real repository root, located from this file rather than the cwd."""
    return Path(__file__).resolve().parents[3]


def pytest_configure(config: pytest.Config) -> None:
    """Keep temporary directories inside the repository's gitignored cache.

    pytest defaults to a per-user directory under the system temp root, which is not
    writable in every environment this suite has to run in (a locked-down Windows profile,
    a hardened CI image, an agent sandbox). Anchoring it to `.cache/` makes the suite
    location-independent, keeps scratch files with the rest of the governance cache, and
    means a failed run leaves its artifacts somewhere already excluded from version control.
    """
    if config.option.basetemp:
        return
    basetemp = _repo_root() / ".cache" / "pytest-governance"
    # pytest creates the basetemp itself but not its parents.
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(basetemp)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    root = _repo_root()
    assert (root / "governance" / "governance.toml").is_file(), f"governance tree not found under {root}"
    return root


@pytest.fixture(scope="session")
def real_context(repo_root: Path) -> Context:
    return Context.discover(repo_root)


@pytest.fixture
def minimal_repo(tmp_path: Path) -> Path:
    """A synthetic repository carrying only what the kernel needs to run.

    Deliberately not a copy of the real repository: a test that mutates a copy of the real
    tree is slow, and its failures are hard to read because everything else is present too.
    """
    root = tmp_path / "repo"
    (root / "governance" / "schemas").mkdir(parents=True)
    (root / "governance" / "policies").mkdir(parents=True)
    (root / "governance" / "manifests" / "curated").mkdir(parents=True)
    (root / "governance" / "history" / "changes").mkdir(parents=True)
    (root / "governance" / "history" / "decisions").mkdir(parents=True)

    source = _repo_root() / "governance"
    for name in ("governance.toml", "validators.json"):
        shutil.copy(source / name, root / "governance" / name)
    shutil.copy(
        source / "manifests" / "curated" / "ownership.json",
        root / "governance" / "manifests" / "curated" / "ownership.json",
    )
    shutil.copy(
        source / "policies" / "self-governance.json",
        root / "governance" / "policies" / "self-governance.json",
    )
    for schema in (source / "schemas").glob("*.json"):
        shutil.copy(schema, root / "governance" / "schemas" / schema.name)

    return root
