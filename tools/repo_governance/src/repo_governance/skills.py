"""Reference integrity for the agent operating surface (.claude).

Skills, commands, and rules are the highest-leverage agent inputs in this repository,
and they rot silently: a renamed module, a deleted doc, or a retired Make target keeps
being cited long after the territory moved. This module verifies every positively
identifiable citation - repository paths, Make targets, and validator IDs - against the
tree, the Makefile, and the validator registry.

Classification is positive-only: a token is checked when it provably names one of the
reference kinds and ignored otherwise. Precision over recall, because a checker that
false-positives on prose gets switched off and then catches nothing.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from repo_governance.config import Context, iter_files
from repo_governance.io_atomic import read_json

#: The corpus. Everything scanned lives under these repo-relative roots. `docs/` joined the
#: corpus because it cites the same territory the skills do and rots the same way, but with
#: nothing checking it: five sites still named `app/rag/connectors/` two refactors after
#: that package moved, a howto step instructed editing a file that belongs to the generator
#: repository rather than this one, and the command reference listed twenty-four Make
#: targets that do not exist. What is checked differs per root — see `_FENCED_PATH_ROOTS`
#: and `_BARE_NAME_ROOTS`.
SCAN_ROOTS = (".claude/skills", ".claude/commands", ".claude/rules", "docs")

#: Checked-out worktree copies are other branches' content, not this tree's claims.
SKIP_PREFIX = ".claude/worktrees/"

#: Frozen records: documents that describe a past state on purpose, where a citation that
#: no longer resolves is history rather than rot. The governance blueprint is the
#: specification this system was adopted from; it describes the repository as it stood
#: before adoption (pre-commit still under backend/, for one) and proposes the very move
#: that invalidated the path. Editing it to match today would falsify the record ADR-001
#: rests on. It also names MCP methods such as `tools/list`, which are wire protocol, not
#: paths — a shape no positive-classification rule can tell from a deleted directory.
SKIP_FILES = frozenset(
    {
        "docs/Fullstack_Agentic_Governance_Blueprint.md",
        # The adoption baseline, frozen for the same reason as the blueprint it accepts: it
        # records what was true on 2026-08-06, including the hook layout that has since
        # moved, and one passage quotes a wrong path on purpose to contrast it with the
        # right one. Editing it to satisfy the checker would falsify the record. It entered
        # this corpus only because Phase 1 moved the ADRs under docs/; ADRs written from
        # here on describe the present and are checked.
        "docs/architecture/decisions/ADR-001-governance-adoption-baseline.md",
    }
)

#: A token containing any of these is a placeholder or shell fragment, never a citation.
_PLACEHOLDER = re.compile(r"[<>$={}\[\]()\s]|\.\.\.|…")

#: Corpus convention: paths written relative to a well-known interior root.
_RELATIVE_ROOTS: dict[str, tuple[str, ...]] = {
    "app": ("backend",),
    "api": ("backend/app",),
    "services": ("backend/app",),
    "worker": ("backend/app",),
    "src": ("frontend",),
    "components": ("frontend/src",),
    "stores": ("frontend/src",),
    "lib": ("frontend/src",),
    "hooks": ("frontend/src",),
}

#: Bare filenames are only meaningful citations in skills and commands; rules use
#: bare names as naming-convention examples.
_BARE_NAME_ROOTS = (".claude/skills/", ".claude/commands/")
_BARE_NAME = re.compile(r"^[A-Za-z0-9_*.-]+\.(?:py|md|ts|tsx|json|toml|yml|yaml)$")

#: Roots whose fenced blocks hold instructions against files that already exist, so every
#: word in them is a citation. Prose documentation is excluded: a howto's fenced example
#: names the file you are about to create, and demanding that it already exist would force
#: the procedure to be written about some unrelated file that happens to be present.
_FENCED_PATH_ROOTS = (".claude/",)

#: Fence info strings whose contents are commands a reader will actually run. A `make`
#: token inside a python or json fence is prose or sample data — "Never make up
#: information" in a prompt template is not a call to a Makefile target named `up`.
_SHELL_FENCES = frozenset({"", "bash", "sh", "shell", "console", "shell-session", "zsh", "powershell", "ps1"})

#: Suffixes marking a tracked template whose un-suffixed sibling is gitignored by design.
#: Derived rather than listed: a hand-maintained set of absent paths grows a false positive
#: every time a document names a new `.env` variant.
_TEMPLATE_SUFFIXES = (".example", ".template", ".sample")

_BACKTICK = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_MAKE_TARGET = re.compile(r"\bmake\s+([A-Za-z0-9_.][A-Za-z0-9_.*-]*)")
_MAKEFILE_RULE = re.compile(r"^([A-Za-z0-9_.-]+):(?!=)")
_VALIDATOR_MENTION = re.compile(r"validators?\s+\(?`([a-z0-9-]+)`")


def _clean_token(token: str) -> str:
    """Strip quoting both ends but punctuation only from the right.

    A leading dot is load-bearing (`.governance.json`, `.env.example`); a trailing one
    is sentence punctuation.
    """
    cleaned = token.strip().strip("`'\"").rstrip(".,;:!?")
    return cleaned[2:] if cleaned.startswith("./") else cleaned


def _finding(file: str, line: int, kind: str, token: str, detail: str) -> dict[str, Any]:
    return {"file": file, "line": line, "kind": kind, "token": token, "detail": detail}


def _file_index(ctx: Context) -> set[str] | None:
    """Repo-relative POSIX paths of every file the corpus may legitimately cite.

    Git's tracked list where available, always unioned with a live walk of `.claude/`
    so a skill added mid-session can cite its own new neighbours.
    """
    from repo_governance.gitutil import toplevel, tracked_files

    root = ctx.repo_root
    tracked = tracked_files(root)
    if tracked is None or toplevel(root) != root.resolve():
        # No git, or git answering for an enclosing repository (a synthetic repo under
        # the real one's cache): the walk is the only honest index.
        walked = iter_files(root)
        if not walked:
            return None
        index = {path.relative_to(root).as_posix() for path in walked}
    else:
        index = set(tracked)
    for path in iter_files(root / ".claude"):
        index.add(path.relative_to(root).as_posix())
    return index


def _dir_index(files: set[str]) -> set[str]:
    dirs: set[str] = set()
    for path in files:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            dirs.add("/".join(parts[:depth]))
    return dirs


def _makefile_targets(ctx: Context) -> set[str] | None:
    makefile = ctx.repo_root / "Makefile"
    if not makefile.is_file():
        return None
    targets: set[str] = set()
    for line in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _MAKEFILE_RULE.match(line)
        if match and not match.group(1).startswith("."):
            targets.add(match.group(1))
    return targets


def _validator_ids(ctx: Context) -> set[str] | None:
    registry_path = ctx.paths.validators
    if not registry_path.is_file():
        return None
    registry = read_json(registry_path)
    if not isinstance(registry, dict):
        return None
    ids = {entry.get("id") for entry in registry.get("validators", ()) if isinstance(entry, dict)}
    ids |= {entry.get("id") for entry in registry.get("excluded", ()) if isinstance(entry, dict)}
    return {item for item in ids if isinstance(item, str)}


def _validator_commands(ctx: Context) -> dict[str, str]:
    registry_path = ctx.paths.validators
    if not registry_path.is_file():
        return {}
    registry = read_json(registry_path)
    if not isinstance(registry, dict):
        return {}
    return {
        entry["id"]: entry["command"]
        for entry in registry.get("validators", ())
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("command"), str)
    }


class _Resolver:
    """Answers 'does this citation resolve' against a fixed snapshot of the tree."""

    def __init__(self, files: set[str], targets: set[str] | None, validator_ids: set[str] | None) -> None:
        self.files = files
        self.dirs = _dir_index(files)
        self.basenames = {path.rsplit("/", 1)[-1] for path in files}
        self.top_level = {path.split("/", 1)[0] for path in files}
        self.targets = targets
        self.validator_ids = validator_ids

    def path_exists(self, token: str) -> bool:
        cleaned = token.rstrip("/")
        if "*" in token or "?" in token:
            return any(fnmatch.fnmatchcase(path, token) or fnmatch.fnmatchcase(path, cleaned) for path in self.files)
        return cleaned in self.files or cleaned in self.dirs

    def basename_exists(self, token: str) -> bool:
        if "*" in token or "?" in token:
            return any(fnmatch.fnmatchcase(name, token) for name in self.basenames)
        return token in self.basenames

    def target_exists(self, token: str) -> bool:
        if self.targets is None:
            return True
        if "*" in token:
            return any(fnmatch.fnmatchcase(target, token) for target in self.targets)
        return token in self.targets

    def validator_known(self, slug: str) -> bool:
        return self.validator_ids is None or slug in self.validator_ids

    def absent_by_construction(self, token: str) -> bool:
        """True when a tracked template sibling explains the absence.

        `backend/.env.example` is committed precisely so `backend/.env` can be gitignored,
        and any document explaining configuration has to name the file you create from it.
        Absence is the design here, not rot.
        """
        return any(f"{token}{suffix}" in self.files for suffix in _TEMPLATE_SUFFIXES)


def _classify_path(token: str, resolver: _Resolver) -> tuple[str, str] | None:
    """Return (kind, detail) when a path-shaped token fails to resolve, else None."""
    cleaned = _clean_token(token)
    if not cleaned or _PLACEHOLDER.search(cleaned):
        return None
    if cleaned.startswith(("-", "/")) or "\\" in cleaned or "://" in cleaned:
        return None
    if resolver.absent_by_construction(cleaned):
        return None
    if "/" not in cleaned:
        return None
    first = cleaned.split("/", 1)[0]
    if first in resolver.top_level:
        if resolver.path_exists(cleaned):
            return None
        return ("missing-path", f"no such file or directory: {cleaned}")
    roots = _RELATIVE_ROOTS.get(first)
    if roots:
        if any(resolver.path_exists(f"{root}/{cleaned}") for root in roots):
            return None
        return ("missing-path", f"{cleaned} not found under {' or '.join(roots)}")
    return None


def _classify_bare_name(token: str, resolver: _Resolver) -> tuple[str, str] | None:
    cleaned = _clean_token(token)
    if not _BARE_NAME.match(cleaned) or _PLACEHOLDER.search(cleaned):
        return None
    if resolver.basename_exists(cleaned):
        return None
    return ("missing-path", f"no file named {cleaned} anywhere in the tree")


def _expand_table_ids(cell: str, resolver: _Resolver) -> list[tuple[str, bool]]:
    """Slugs claimed by a validator-table ID cell, with suffix shorthand expanded.

    `` `preflight-volumes` / `-model` `` claims preflight-volumes and preflight-model.
    Returns (slug, resolves) pairs; a shorthand resolves when any prefix of the base
    slug joined with the suffix is a registered ID.
    """
    tokens = _BACKTICK.findall(cell)
    if not tokens:
        return []
    results: list[tuple[str, bool]] = []
    base = tokens[0]
    results.append((base, resolver.validator_known(base)))
    parts = base.split("-")
    for token in tokens[1:]:
        if token.startswith("-"):
            candidates = ["-".join(parts[:depth]) + token for depth in range(1, len(parts) + 1)]
            resolved = next((item for item in candidates if resolver.validator_known(item)), None)
            results.append((base + token if resolved is None else resolved, resolved is not None))
        else:
            results.append((token, resolver.validator_known(token)))
    return results


def _check_tables(rel: str, lines: list[str], resolver: _Resolver, commands: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    header_cells: list[str] | None = None
    id_col = command_col = -1
    for lineno, line in enumerate(lines, 1):
        if not line.lstrip().startswith("|"):
            header_cells = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header_cells is None:
            lowered = [cell.strip("` ").lower() for cell in cells]
            if "id" in lowered and any("command" in cell for cell in lowered):
                header_cells = cells
                id_col = lowered.index("id")
                command_col = next(index for index, cell in enumerate(lowered) if "command" in cell)
            continue
        if set("".join(cells)) <= {"-", ":", " "}:
            continue
        if id_col >= len(cells):
            continue
        claimed = _expand_table_ids(cells[id_col], resolver)
        for slug, resolves in claimed:
            if not resolves:
                findings.append(
                    _finding(rel, lineno, "unknown-validator", slug, "not registered in governance/validators.json")
                )
        if len(claimed) == 1 and claimed[0][1] and command_col < len(cells):
            slug = claimed[0][0]
            cell_commands = _BACKTICK.findall(cells[command_col])
            if len(cell_commands) == 1 and slug in commands and cell_commands[0].strip() != commands[slug]:
                findings.append(
                    _finding(
                        rel,
                        lineno,
                        "validator-command-mismatch",
                        slug,
                        f"table says `{cell_commands[0].strip()}`, registry says `{commands[slug]}`",
                    )
                )
    return findings


def _check_skill_integrity(ctx: Context, resolver: _Resolver) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    skills_root = ctx.repo_root / ".claude" / "skills"
    if not skills_root.is_dir():
        return findings
    for directory in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        if directory.name == "worktrees":
            continue
        rel = f".claude/skills/{directory.name}/SKILL.md"
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            findings.append(_finding(rel, 1, "skill-integrity", directory.name, "skill directory has no SKILL.md"))
            continue
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            findings.append(_finding(rel, 1, "skill-integrity", directory.name, "missing YAML frontmatter"))
            continue
        try:
            end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration:
            findings.append(_finding(rel, 1, "skill-integrity", directory.name, "unterminated YAML frontmatter"))
            continue
        front = "\n".join(lines[1:end])
        name = re.search(r"^name:\s*(\S+)\s*$", front, re.MULTILINE)
        description = re.search(r"^description:\s*(\S.*)$", front, re.MULTILINE)
        if name is None or name.group(1) != directory.name:
            found = "missing" if name is None else name.group(1)
            findings.append(
                _finding(
                    rel, 2, "skill-integrity", found, f"frontmatter name must equal directory name {directory.name}"
                )
            )
        if description is None:
            findings.append(_finding(rel, 2, "skill-integrity", directory.name, "frontmatter description is empty"))
    return findings


def _scan_file(rel: str, text: str, resolver: _Resolver) -> list[dict[str, Any]]:
    # Both policies are functions of the file's location, so they are derived here rather
    # than passed in: a caller that forgot the keyword would silently apply skill
    # semantics to a prose document, which is the exact confusion this split exists to end.
    bare_names = rel.startswith(_BARE_NAME_ROOTS)
    fenced_paths = rel.startswith(_FENCED_PATH_ROOTS)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()

    def record(lineno: int, kind: str, token: str, detail: str) -> None:
        key = (lineno, kind, token)
        if key not in seen:
            seen.add(key)
            findings.append(_finding(rel, lineno, kind, token, detail))

    def check_token(lineno: int, token: str) -> None:
        verdict = _classify_path(token, resolver)
        if verdict is None and bare_names:
            verdict = _classify_bare_name(token, resolver)
        if verdict is not None:
            record(lineno, verdict[0], _clean_token(token), verdict[1])

    def check_make(lineno: int, segment: str) -> None:
        for target in _MAKE_TARGET.findall(segment):
            if not resolver.target_exists(target):
                record(lineno, "unknown-make-target", target, "no such Makefile target")

    fenced = False
    shell_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            # A closing fence carries no info string, so this reassignment is only ever
            # read after the next opening fence has overwritten it.
            shell_fence = stripped[3:].strip().lower() in _SHELL_FENCES
            fenced = not fenced
            continue
        if fenced:
            if fenced_paths:
                for word in line.split():
                    check_token(lineno, word)
            if shell_fence:
                check_make(lineno, line)
            continue
        for span in _BACKTICK.findall(line):
            check_token(lineno, span)
            check_make(lineno, span)
        for target in _MD_LINK.findall(line):
            check_token(lineno, target)
        for slug in _VALIDATOR_MENTION.findall(line):
            if not resolver.validator_known(slug):
                record(lineno, "unknown-validator", slug, "not registered in governance/validators.json")
    return findings


def analyse_skill_references(ctx: Context) -> dict[str, Any]:
    """Verify every positively identifiable citation in the .claude corpus.

    Returns findings sorted by (file, line, kind, token); deterministic across runs
    so the report can serve as golden evidence.
    """
    files = _file_index(ctx)
    if files is None:
        return {"status": "unknown", "reason": "neither git nor a filesystem walk could list the tree"}

    resolver = _Resolver(files, _makefile_targets(ctx), _validator_ids(ctx))
    commands = _validator_commands(ctx)

    # iter_files, not rglob: it honours EXCLUDED_DIRS and sorts POSIX-normalized, which
    # matters now that a root is a human-writable tree where a node_modules or .venv could
    # plausibly land.
    corpus: list[Path] = []
    for root in SCAN_ROOTS:
        corpus.extend(iter_files(ctx.repo_root / Path(root), suffixes=(".md",)))

    findings: list[dict[str, Any]] = []
    scanned = 0
    for path in corpus:
        rel = path.relative_to(ctx.repo_root).as_posix()
        if rel.startswith(SKIP_PREFIX) or rel in SKIP_FILES:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(_scan_file(rel, text, resolver))
        findings.extend(_check_tables(rel, text.splitlines(), resolver, commands))
    findings.extend(_check_skill_integrity(ctx, resolver))

    findings.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["token"]))
    return {"status": "ok", "files_scanned": scanned, "findings": findings}
