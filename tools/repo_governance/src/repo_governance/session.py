"""Change sessions.

The session exists to capture the reason for a change **before** it can be lost. A diff
shows what changed and never why, and by the time a change is finished the reason has
usually collapsed into "it needed doing". Asking at the start is the only reliable moment.

Session state lives in the gitignored cache, not in committed history: an in-flight change
is not a fact about the repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from repo_governance.config import Context
from repo_governance.gitutil import head_commit, working_tree_changes
from repo_governance.identifiers import change_record_id, slugify
from repo_governance.io_atomic import read_json, write_json_atomic

#: Fields no automation can supply. `change finish` refuses rather than inventing them.
REQUIRED_JUDGEMENT = (
    "security_impact",
    "compatibility_impact",
    "rollback",
)


@dataclass
class Session:
    summary: str
    reason: str
    date: str
    start_commit: str | None
    initiated_by: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def start(ctx: Context, *, summary: str, reason: str, date: str, initiated_by: str = "agent") -> Session:
    if ctx.paths.session_file.is_file():
        existing = read_json(ctx.paths.session_file)
        raise SessionError(
            f"A change session is already open: {existing.get('summary')!r}. "
            "Finish it, or delete .cache/repo-governance/session.json to abandon it."
        )

    session = Session(
        summary=summary.strip(),
        reason=reason.strip(),
        date=date,
        start_commit=head_commit(ctx.repo_root),
        initiated_by=initiated_by,
    )
    write_json_atomic(ctx.paths.session_file, session.to_json())
    return session


def load(ctx: Context) -> Session:
    if not ctx.paths.session_file.is_file():
        raise SessionError(
            "No change session is open. Start one with `make governance-change-start "
            'SUMMARY="..." REASON="..."` so the reason is captured before it is lost.'
        )
    data = read_json(ctx.paths.session_file)
    return Session(**data)


def clear(ctx: Context) -> None:
    ctx.paths.session_file.unlink(missing_ok=True)


class SessionError(RuntimeError):
    """A session problem the caller has to resolve."""


def build_record(
    ctx: Context,
    session: Session,
    *,
    judgement: dict[str, Any],
    validators: list[dict[str, str]],
) -> dict[str, Any]:
    """Assemble a change record from session state, the diff, and supplied judgement.

    Diff-derived facts are collected automatically. Everything a diff cannot show — why,
    what it breaks, how to undo it — has to be supplied, and this refuses to guess.
    """
    from repo_governance.merge import build_components

    changed = working_tree_changes(ctx.repo_root)
    if changed is None:
        raise SessionError("Could not read the working tree; git is unavailable.")

    missing = [field for field in REQUIRED_JUDGEMENT if not judgement.get(field)]
    if missing:
        raise SessionError(
            "These cannot be inferred from a diff and must be supplied: "
            + ", ".join(f"--{field.replace('_', '-')}" for field in missing)
        )

    manifest, _ = build_components(ctx)
    components = _owning_components(manifest["components"], changed)

    manifests_changed = sorted(
        path for path in changed if path.startswith("governance/manifests/") or path == "ENV_VARS.md"
    )

    record: dict[str, Any] = {
        "id": change_record_id(session.date, session.summary),
        "date": session.date,
        "summary": session.summary,
        "reason": session.reason,
        "initiated_by": {"kind": session.initiated_by},
        "affected_components": sorted(components),
        "affected_contracts": sorted(judgement.get("contracts", [])),
        "files_changed": sorted(changed),
        "manifests_changed": manifests_changed,
        "behavioral_effects": list(judgement.get("behavioral_effects", [])),
        "validators_run": validators,
        "security_impact": judgement["security_impact"],
        "compatibility_impact": judgement["compatibility_impact"],
        "risks": list(judgement.get("risks", [])),
        "limitations": list(judgement.get("limitations", [])),
        "follow_up": list(judgement.get("follow_up", [])),
        "rollback": judgement["rollback"],
        "adr_refs": sorted(judgement.get("adr_refs", [])),
        "schema_version": ctx.config.schema_version,
        "tool_version": ctx.config.version,
    }

    if not record["behavioral_effects"]:
        record["behavioral_effects"] = ["None stated. An empty list is a claim that nothing observably changes."]

    return record


def _owning_components(components: list[dict[str, Any]], paths: list[str]) -> set[str]:
    """Which components own the changed paths.

    Longest matching pattern wins, so a specific subsystem beats the application root that
    contains it.
    """
    import fnmatch

    owners: set[str] = set()
    for path in paths:
        best: tuple[int, str] | None = None
        for component in components:
            for pattern in component.get("owns", []):
                if fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*")):
                    score = len(pattern)
                    if best is None or score > best[0]:
                        best = (score, component["id"])
        if best:
            owners.add(best[1])
    return owners


def record_path(ctx: Context, record_id: str) -> Any:
    return ctx.paths.changes / f"{record_id}.json"


def suggest_id(date: str, summary: str) -> str:
    return f"{date}-{slugify(summary)}"
