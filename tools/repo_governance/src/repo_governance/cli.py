"""The governance command line.

Exit codes are part of the contract:

* ``0`` — the command succeeded, or reported only advisory findings.
* ``1`` — a blocking rule failed, or the command could not run.
* ``2`` — the command exists but is deferred to a later phase, with its growth trigger
  named. Deferred is never silently reported as success.

Invocation goes through the Makefile so it stays the single operational entry point:
``make governance-check`` rather than a bare ``uv run``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from repo_governance import __version__
from repo_governance.checks import CheckScope, run_checks
from repo_governance.config import Context, GovernanceError
from repo_governance.gitutil import head_commit, is_clean, is_repository
from repo_governance.io_atomic import write_text_atomic
from repo_governance.models import CheckReport
from repo_governance.pipeline import compare_generated, render_all, sync as run_sync

DEFERRED_EXIT = 2


def _context() -> Context:
    try:
        return Context.discover()
    except GovernanceError as exc:
        raise click.ClickException(str(exc)) from exc


def _echo_report(report: CheckReport, *, verbose: bool) -> None:
    for finding in report.blocking:
        click.echo(finding.render())
    for finding in report.advisory:
        click.echo(finding.render())

    if verbose and report.checks_deferred:
        click.echo("")
        click.echo("Not yet enforced:")
        for entry in report.checks_deferred:
            click.echo(f"  - {entry}")

    click.echo("")
    click.echo(
        f"{len(report.checks_run)} checks run, "
        f"{len(report.blocking)} blocking, "
        f"{len(report.advisory)} advisory, "
        f"{len(report.checks_deferred)} not yet enforced."
    )


def _run_make(target: str, repo_root: Path, extra: list[str] | None = None) -> tuple[bool, str]:
    """Run a Make target. Governance wraps the Makefile; it never reimplements a target."""
    if shutil.which("make") is None:
        return False, "make is not on PATH"
    try:
        completed = subprocess.run(
            ["make", target, *(extra or [])],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="governance")
def main() -> None:
    """Repository governance control plane."""


@main.command()
def bootstrap() -> None:
    """Inventory the repository and confirm the governance baseline.

    Deliberately non-destructive: it reports state and refuses to normalize anything. The
    external preservation volumes in particular are pre-existing state this repository did
    not create.
    """
    ctx = _context()
    click.echo(f"Repository:          {ctx.repo_root}")
    click.echo(f"Governance contract: {ctx.config.version} (schema {ctx.config.schema_version})")
    click.echo(f"Tool:                {__version__}")

    provenance = ctx.repo_root / ".fastapi-fullstack.json"
    if provenance.is_file():
        from repo_governance.io_atomic import read_json

        data = read_json(provenance)
        click.echo(f"Generator:           {data.get('generator_name')} {data.get('generator_version')}")
        click.echo(f"Context hash:        {data.get('context_hash')}")
    else:
        click.echo("Generator:           no .fastapi-fullstack.json found")

    if is_repository(ctx.repo_root):
        click.echo(f"HEAD:                {head_commit(ctx.repo_root)}")
        clean = is_clean(ctx.repo_root)
        click.echo(f"Working tree:        {'clean' if clean else 'has uncommitted changes'}")
    else:
        click.echo("HEAD:                not a git repository")

    click.echo("")
    click.echo("Protected pre-existing state (never recreated or normalized by governance):")
    click.echo("  - docker volume redis-data")
    click.echo("  - docker volume docling-models")
    click.echo("")
    click.echo("Bootstrap made no changes. Run `make governance-sync` to generate manifests.")


@main.command()
@click.option("--skip-docker", is_flag=True, help="Skip preflights that need a running Docker engine.")
@click.option("--with-model", is_flag=True, help="Also probe Docker Model Runner (slow).")
def preflight(skip_docker: bool, with_model: bool) -> None:
    """Run the operational preflights, then the governance compatibility checks.

    The Makefile owns the operational checks; this wraps them. If a governance check and a
    Make target would test the same thing, the Make target keeps it.
    """
    ctx = _context()
    failures: list[str] = []

    targets = ["preflight-ports"]
    if not skip_docker:
        targets.insert(0, "preflight-volumes")
    if with_model:
        targets.append("preflight-model")

    for target in targets:
        ok, output = _run_make(target, ctx.repo_root)
        status = "ok" if ok else "FAILED"
        click.echo(f"make {target}: {status}")
        if not ok:
            failures.append(target)
            if output:
                click.echo("\n".join(f"    {line}" for line in output.splitlines()[:10]))

    scope = CheckScope(ctx=ctx, fast=True)
    from repo_governance.checks.schemas import check_contract_version

    version_issues = check_contract_version(scope)
    for issue in version_issues:
        click.echo(f"contract: {issue.message}")
        failures.append("contract-version")

    drift = compare_generated(ctx)
    if drift:
        click.echo("")
        click.echo("Unexplained drift before this change begins:")
        for path, reason in drift:
            click.echo(f"  - {path}: {reason}")
        click.echo("  Resolve it first, or the drift you cause will be indistinguishable from it.")

    clean = is_clean(ctx.repo_root)
    if clean is False:
        click.echo("")
        click.echo("Working tree has uncommitted changes; the baseline for this change is not clean.")

    if failures:
        raise SystemExit(1)


@main.command()
def scan() -> None:
    """Parse the authoritative sources and write generated candidates to the cache.

    A preview: it never touches the repository. In this milestone there is no graph cache
    to rebuild, so scan produces exactly what sync would write and reports the difference.
    """
    ctx = _context()
    candidates = ctx.paths.cache / "candidates"

    outputs = render_all(ctx)
    for relative_path, content in sorted(outputs.items()):
        write_text_atomic(candidates / relative_path, content)

    drift = compare_generated(ctx)
    click.echo(f"Rendered {len(outputs)} generated files to {candidates}.")
    if not drift:
        click.echo("Committed output matches the sources; sync would change nothing.")
        return

    click.echo("")
    click.echo("Sync would change:")
    for path, reason in drift:
        click.echo(f"  - {path}: {reason}")


@main.command()
def sync() -> None:
    """Write the generated manifests and derived documentation."""
    ctx = _context()
    changed = run_sync(ctx)
    if not changed:
        click.echo("Already in sync; no bytes changed.")
        return
    click.echo(f"Updated {len(changed)} file(s):")
    for path in changed:
        click.echo(f"  - {path}")
    click.echo("")
    click.echo("Review the diff rather than accepting it blindly.")


@main.command()
@click.option("--fast", "mode", flag_value="fast", help="Cheap checks only, for pre-commit.")
@click.option("--full", "mode", flag_value="full", default=True, help="Everything (default).")
@click.option(
    "--files",
    "files",
    multiple=True,
    help="Limit to these repo-relative paths. Repeatable; implies the affected-file scope.",
)
@click.option("--since", help="Also consider the commit range since this git ref (for CI against a base).")
@click.option("--verbose", is_flag=True, help="List rules that are not yet enforced.")
def check(mode: str, files: tuple[str, ...], since: str | None, verbose: bool) -> None:
    """Read-only validation. Nonzero exit when a blocking rule fails."""
    ctx = _context()
    scope = CheckScope(
        ctx=ctx,
        files=tuple(sorted(files)) if files else None,
        fast=(mode == "fast"),
        since=since,
    )
    report = run_checks(scope)
    _echo_report(report, verbose=verbose or mode == "full")
    raise SystemExit(report.exit_code)


@main.command()
def doctor() -> None:
    """Diagnose the environment and the governance tree."""
    ctx = _context()
    click.echo("Environment")
    for tool in ("git", "make", "uv", "docker"):
        location = shutil.which(tool)
        click.echo(f"  {tool:<8} {location or 'NOT FOUND'}")

    click.echo("")
    click.echo("Governance tree")
    expected = {
        "governance.toml": ctx.paths.config_file,
        "schemas/": ctx.paths.schemas,
        "policies/": ctx.paths.policies,
        "validators.json": ctx.paths.validators,
        "manifests/curated/": ctx.paths.curated_manifests,
        "manifests/generated/": ctx.paths.generated_manifests,
        "manifests/effective/": ctx.paths.effective_manifests,
        "history/changes/": ctx.paths.changes,
        "history/decisions/": ctx.paths.decisions,
    }
    for label, path in expected.items():
        click.echo(f"  {label:<22} {'present' if path.exists() else 'missing'}")

    click.echo("")
    click.echo("Cache")
    click.echo(f"  {ctx.paths.cache}: {'present' if ctx.paths.cache.exists() else 'not created yet'}")

    drift = compare_generated(ctx)
    click.echo("")
    click.echo(f"Generated-file drift: {len(drift)} file(s)")
    for path, reason in drift:
        click.echo(f"  - {path}: {reason}")


@main.command()
@click.option("--to", "target", required=True, help="Governance contract version to migrate to.")
def migrate(target: str) -> None:
    """Migrate governance documents to another contract version."""
    ctx = _context()
    if target == ctx.config.version:
        click.echo(f"Already at governance contract {target}. Nothing to do.")
        return
    raise click.ClickException(
        f"No migration is defined from {ctx.config.version} to {target}. "
        "Contract version 1.0.0 is the initial version; incompatible schema changes must "
        "ship with an explicit migration and golden fixtures for both forms."
    )


def _deferred(name: str, phase: str, trigger: str) -> None:
    click.echo(f"`governance {name}` is deferred to {phase}.")
    click.echo("")
    click.echo(f"Growth trigger: {trigger}")
    click.echo("When that symptom is observed it becomes an evaluation record before it becomes a build task.")
    raise SystemExit(DEFERRED_EXIT)


@main.command()
@click.argument("view")
@click.option("--focus", help="Node or component to centre the view on.")
def visualize(view: str, focus: str | None) -> None:
    """Render a bounded view of the repository graph."""
    _deferred(
        "visualize",
        "Phase 7",
        "reviewers or agents demonstrably misjudge a dependency that a bounded view would have shown.",
    )


@main.command()
@click.argument("scenario", required=False)
def evaluate(scenario: str | None) -> None:
    """Replay governance evaluation scenarios."""
    _deferred(
        "evaluate",
        "Phase 9",
        "enough evaluation records exist that policy changes need shadow-mode replay to be trusted.",
    )


def _phase_four(name: str) -> None:
    click.echo(f"`governance {name}` arrives in Phase 4 (change history and bounded context).")
    raise SystemExit(DEFERRED_EXIT)


@main.command()
@click.option("--paths", multiple=True)
@click.option("--task")
@click.option("--token-budget", type=int, default=6000)
def context(paths: tuple[str, ...], task: str | None, token_budget: int) -> None:
    """Return a token-bounded task briefing."""
    _phase_four("context")


@main.command()
@click.option("--paths", multiple=True)
@click.option("--depth", type=int, default=1)
def impact(paths: tuple[str, ...], depth: int) -> None:
    """Return the blast radius of a change."""
    _phase_four("impact")


@main.command()
@click.argument("component_id")
def explain(component_id: str) -> None:
    """Return a compact component briefing."""
    _phase_four("explain")


@main.command()
def summary() -> None:
    """Regenerate the bounded Summary.md."""
    _phase_four("summary")


@main.group()
def change() -> None:
    """Governed change sessions."""


@change.command("start")
@click.option("--summary", "summary_text", required=True)
@click.option("--reason", required=True)
def change_start(summary_text: str, reason: str) -> None:
    """Open a change session, capturing the reason before it can be lost."""
    _phase_four("change start")


@change.command("finish")
@click.option("--validation", multiple=True)
def change_finish(validation: tuple[str, ...]) -> None:
    """Close the session and write a validated permanent record."""
    _phase_four("change finish")


if __name__ == "__main__":  # pragma: no cover
    main()
