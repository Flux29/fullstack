"""Repository discovery, path registry, and `governance.toml` loading.

Every path the CLI touches is derived here, relative to a discovered repository root, so
the tool behaves identically whether it is invoked from the repo root, from `backend/`, or
from a temporary directory during a regeneration comparison.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

GOVERNANCE_DIRNAME = "governance"
CONFIG_FILENAME = "governance.toml"
ANNOTATION_FILENAME = ".governance.json"

#: Directories never walked when searching for annotations or source files. Keeping this
#: list explicit stops the scanners from wandering into dependency trees, build output, or
#: the governance cache — all of which would make output non-deterministic.
EXCLUDED_DIRS = frozenset(
    {
        ".cache",
        ".git",
        ".next",
        ".playwright",
        ".pytest_cache",
        ".ruff_cache",
        ".ty_cache",
        ".uv-cache",
        ".venv",
        "__pycache__",
        "artifacts",
        "node_modules",
        "playwright-report",
        "test-results",
        "venv",
    }
)


class GovernanceError(RuntimeError):
    """A condition the operator must fix before governance can run at all."""


@dataclass(frozen=True)
class Paths:
    """The governance path registry, rooted at a repository."""

    repo_root: Path

    @property
    def governance(self) -> Path:
        return self.repo_root / GOVERNANCE_DIRNAME

    @property
    def config_file(self) -> Path:
        return self.governance / CONFIG_FILENAME

    @property
    def catalog(self) -> Path:
        return self.governance / "catalog.json"

    @property
    def summary(self) -> Path:
        return self.governance / "Summary.md"

    @property
    def validators(self) -> Path:
        return self.governance / "validators.json"

    @property
    def schemas(self) -> Path:
        return self.governance / "schemas"

    @property
    def manifests(self) -> Path:
        return self.governance / "manifests"

    @property
    def generated_manifests(self) -> Path:
        return self.manifests / "generated"

    @property
    def curated_manifests(self) -> Path:
        return self.manifests / "curated"

    @property
    def effective_manifests(self) -> Path:
        return self.manifests / "effective"

    @property
    def effective_repository(self) -> Path:
        return self.effective_manifests / "repository.json"

    @property
    def policies(self) -> Path:
        return self.governance / "policies"

    @property
    def history(self) -> Path:
        return self.governance / "history"

    @property
    def changes(self) -> Path:
        return self.history / "changes"

    @property
    def decisions(self) -> Path:
        """Where the ADR prose lives.

        Under `docs/` rather than `governance/history/`: an ADR is architecture
        documentation a human or a non-Claude agent goes looking for, and burying it in the
        corpus-gated history made it reachable only through a governance query. The
        generated index stays in `governance/history/` — see `decision_index`.
        """
        return self.repo_root / "docs" / "architecture" / "decisions"

    @property
    def decision_index(self) -> Path:
        """The generated ADR index. Deliberately not derived from `decisions`.

        The prose moved to `docs/`; the index is a generated governance artefact and stays
        where sync owns it.
        """
        return self.history / "decisions" / "index.json"

    @property
    def evaluations(self) -> Path:
        return self.history / "evaluations"

    @property
    def cache(self) -> Path:
        return self.repo_root / ".cache" / "repo-governance"

    @property
    def session_file(self) -> Path:
        return self.cache / "session.json"

    @property
    def sync_state(self) -> Path:
        return self.cache / "sync-state.json"

    @property
    def evidence(self) -> Path:
        return self.cache / "evidence"

    @property
    def env_vars_doc(self) -> Path:
        return self.repo_root / "ENV_VARS.md"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward for the repository root.

    A governance directory wins over a `.git` directory so the tool still works inside a
    worktree or an exported copy that has no git metadata.
    """
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]

    for candidate in candidates:
        if (candidate / GOVERNANCE_DIRNAME / CONFIG_FILENAME).is_file():
            return candidate
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate

    raise GovernanceError(
        "Could not locate the repository root: no governance/governance.toml and no .git "
        f"directory found at or above {current}."
    )


@dataclass(frozen=True)
class GovernanceConfig:
    """Parsed `governance.toml`."""

    version: str
    schema_version: int
    protected_paths: tuple[str, ...]
    catalog_spec: tuple[dict[str, str], ...]
    raw: dict

    @classmethod
    def load(cls, path: Path) -> GovernanceConfig:
        if not path.is_file():
            raise GovernanceError(
                f"Missing {path}. Governance is not initialized in this repository; "
                "run `make governance-bootstrap` or restore the file from version control."
            )
        with path.open("rb") as handle:
            raw = tomllib.load(handle)

        try:
            governance = raw["governance"]
            version = str(governance["version"])
            schema_version = int(governance["schema_version"])
        except KeyError as exc:  # pragma: no cover - configuration error path
            raise GovernanceError(f"{path} is missing required key {exc}.") from exc

        protected = tuple(raw.get("kernel", {}).get("protected_paths", ()))
        catalog_spec = tuple(raw.get("catalog", ()))
        return cls(
            version=version,
            schema_version=schema_version,
            protected_paths=protected,
            catalog_spec=catalog_spec,
            raw=raw,
        )


@dataclass(frozen=True)
class Context:
    """Everything a command needs to locate and interpret the repository."""

    paths: Paths
    config: GovernanceConfig

    @classmethod
    def discover(cls, start: Path | None = None) -> Context:
        paths = Paths(find_repo_root(start))
        return cls(paths=paths, config=GovernanceConfig.load(paths.config_file))

    @property
    def repo_root(self) -> Path:
        return self.paths.repo_root


def iter_files(root: Path, *, suffixes: tuple[str, ...] | None = None) -> list[Path]:
    """Deterministically list files under `root`, skipping excluded directories.

    Results are sorted by their POSIX-normalized relative path so a Windows walk and a
    Linux walk agree.
    """
    if not root.exists():
        return []

    collected: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        collected.append(path)

    return sorted(collected, key=lambda item: item.relative_to(root).as_posix())
