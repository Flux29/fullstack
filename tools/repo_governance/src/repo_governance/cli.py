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


def _split_paths(values: tuple[str, ...]) -> list[str]:
    """Accept repeated options and comma- or space-separated lists.

    The Make targets pass a single quoted string, so PATHS="a b" has to work as well as
    --paths a --paths b.
    """
    collected: list[str] = []
    for value in values:
        for part in value.replace(",", " ").split():
            normalized = part.strip().replace("\\", "/")
            if normalized:
                collected.append(normalized)
    return collected



@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="governance")
def main() -> None:
    """Repository governance control plane."""


def _register_hook_group() -> None:
    """The hook endpoints live in their own module; a syntax error there must not take
    down the rest of the CLI, so the import failure is reported as a stub command."""
    try:
        from repo_governance.hooks import hook

        main.add_command(hook, name="hook")
    except Exception as error:  # pragma: no cover - import-failure guard
        message = str(error)

        @main.command("hook")
        def broken_hook() -> None:
            """Hook endpoints failed to import."""
            raise click.ClickException(f"repo_governance.hooks failed to import: {message}")


_register_hook_group()


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
    """Parse the authoritative sources, rebuild the graph cache, and write generated
    candidates to the cache. A preview: it never touches the repository."""
    ctx = _context()
    candidates = ctx.paths.cache / "candidates"

    outputs = render_all(ctx)
    for relative_path, content in sorted(outputs.items()):
        write_text_atomic(candidates / relative_path, content)

    from repo_governance.graph.queries import assemble
    from repo_governance.graph.storage import build_sqlite

    data = assemble(ctx)
    cache = build_sqlite(ctx, data)
    click.echo(f"Graph cache: {len(data.nodes)} nodes, {len(data.edges)} edges -> {cache}")

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
@click.argument("file_arguments", nargs=-1, type=click.UNPROCESSED)
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
def check(
    file_arguments: tuple[str, ...],
    mode: str,
    files: tuple[str, ...],
    since: str | None,
    verbose: bool,
) -> None:
    """Read-only validation. Nonzero exit when a blocking rule fails.

    Paths may be given positionally as well as with --files, because pre-commit passes the
    staged filenames as positional arguments and a hook that cannot accept them is a hook
    that does not run.
    """
    ctx = _context()
    selected = _split_paths(files) + _split_paths(file_arguments)
    scope = CheckScope(
        ctx=ctx,
        files=tuple(sorted(set(selected))) if selected else None,
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


@main.command("snapshot-mcp-connections")
@click.option("--dsn", help="Read-only PostgreSQL DSN. Never read from .env.")
@click.option("--via-docker", "container", help="Container to run psql in, when the database publishes no host port.")
@click.option("--db-user", default="postgres", show_default=True)
@click.option("--db-name", default="fullstack", show_default=True)
@click.option("--date", "date_text", help="ISO date for the evidence ID; defaults to today.")
def snapshot_mcp_connections(
    dsn: str | None,
    container: str | None,
    db_user: str,
    db_name: str,
    date_text: str | None,
) -> None:
    """Take a sanitized aggregate snapshot of the per-user MCP connections.

    Human-invoked only. The per-user tool surface exists solely as database rows, so a
    static analysis of it is confidently incomplete — but the rows themselves contain live
    credentials, so the snapshot is aggregate, URL-stripped, and written only to the
    gitignored cache.
    """
    from datetime import date as date_type

    from repo_governance.evidence import EvidenceError, snapshot_mcp_connections as collect

    ctx = _context()
    try:
        result = collect(
            ctx,
            dsn,
            date=date_text or date_type.today().isoformat(),
            container=container,
            user=db_user,
            database=db_name,
        )
    except EvidenceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Evidence ID: {result.evidence_id}")
    click.echo(f"Written to:  {result.path}")
    click.echo(f"Summary:     {result.summary}")
    click.echo("")
    click.echo("Not committed. Reference it from a change record by ID and summary only.")


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


@main.command()
@click.option("--paths", multiple=True, help="Repo-relative paths the task will touch.")
@click.option("--task", help="What you are about to do.")
@click.option("--token-budget", type=int, default=6000, help="Approximate ceiling for the briefing.")
def context(paths: tuple[str, ...], task: str | None, token_budget: int) -> None:
    """Return a token-bounded task briefing.

    Returns references to the convention files rather than their contents: they already
    load themselves when you edit a matching file, and restating them here would spend the
    budget on something you already have.
    """
    from repo_governance.renderers.context import render_context

    ctx = _context()
    selected = _split_paths(paths)
    if not selected:
        raise click.ClickException('Give at least one path: --paths "backend/app/..."')
    click.echo(render_context(ctx, selected, task, token_budget))


@main.command()
@click.option("--paths", multiple=True, help="Repo-relative paths the change touches.")
@click.option("--depth", type=int, default=1, help="Reverse-dependency closure depth.")
def impact(paths: tuple[str, ...], depth: int) -> None:
    """Return the blast radius of a change."""
    from repo_governance.renderers.context import analyse_impact

    ctx = _context()
    selected = _split_paths(paths)
    if not selected:
        raise click.ClickException('Give at least one path: --paths "backend/app/..."')

    result = analyse_impact(ctx, selected, depth)

    click.echo(f"Seed components:  {', '.join(result.seeds) or 'none'}")
    click.echo(f"Blast radius:     {', '.join(result.components) or 'none'}")
    if result.unassigned:
        click.echo(f"Unassigned paths: {', '.join(result.unassigned)}")
        click.echo("  No component owns these, so the radius may be incomplete.")
    if result.contracts:
        click.echo(f"Contracts:        {', '.join(result.contracts)}")
    if result.configuration:
        click.echo(f"Configuration:    {', '.join(result.configuration)}")
    if result.proxy_routes:
        click.echo("Proxy handlers in the chain:")
        for route in result.proxy_routes:
            click.echo(f"  - {route}")
    if result.decisions:
        click.echo(f"Decisions:        {', '.join(result.decisions)}")
    if result.findings:
        click.echo(f"Open findings:    {', '.join(result.findings)}")
    if result.graph_files:
        click.echo("Reverse importers (import graph):")
        for file in result.graph_files:
            click.echo(f"  - {file}")
    for note in result.notes:
        click.echo(f"Note: {note}")
    click.echo(f"Validators:       {', '.join(result.validators) or 'none selected'}")


@main.command()
@click.argument("component_id")
def explain(component_id: str) -> None:
    """Return a compact component briefing."""
    from repo_governance.renderers.context import render_explain

    click.echo(render_explain(_context(), component_id))


@main.command()
@click.option("--count", default=25, show_default=True, help="How many files to sample.")
@click.option("--seed", default=None, help="Sampling seed. Defaults to the HEAD commit, so a run is reproducible.")
def sample(count: int, seed: str | None) -> None:
    """Map-vs-territory spot check: verify manifest claims about sampled files. Read-only.

    The statistical stand-in for exhaustive AST enforcement until the graph exists.
    Nonzero exit on any mismatch; unreadable files are reported, never counted as passing.
    """
    from repo_governance.builders import sample_map_claims
    from repo_governance.gitutil import head_commit

    ctx = _context()
    resolved_seed = seed or head_commit(ctx.repo_root)
    if resolved_seed is None:
        click.echo("No --seed given and git HEAD is unavailable; a reproducible sample needs one.")
        raise SystemExit(1)

    report = sample_map_claims(ctx, count=count, seed=resolved_seed)
    if report["status"] != "ok":
        click.echo(f"Sample unknown: {report['reason']}")
        raise SystemExit(1)

    click.echo(
        f"Sampled {report['sampled']} of {report['pool_size']} governed application files "
        f"(seed {report['seed'][:12]})"
    )
    for item in report["unreadable"]:
        click.echo(f"  unreadable: {item['path']} - {item['reason']}")
    for item in report["mismatches"]:
        click.echo(f"  MISMATCH {item['path']}")
        click.echo(f"    claim:    {item['claim']}")
        click.echo(f"    evidence: {item['evidence']}")

    if report["mismatches"]:
        click.echo(f"{len(report['mismatches'])} claim(s) disagree with the territory - each is an evaluation-record candidate.")
        raise SystemExit(1)
    click.echo("Every sampled file agrees with the claims the manifests make about it.")


@main.command()
def coverage() -> None:
    """Read-surface coverage: orphans, ghosts, and rule-scope drift. Read-only.

    Nonzero exit on any finding, so CI can hold the line the local warn-mode gate
    deliberately does not.
    """
    from repo_governance.builders import analyse_read_surface_coverage

    report = analyse_read_surface_coverage(_context())
    if report["status"] != "ok":
        click.echo(f"Coverage unknown: {report['reason']}")
        raise SystemExit(1)

    click.echo(
        f"Read-surface coverage: {report['covered']}/{report['total_tracked']} tracked paths "
        f"({report['coverage_percent']}%)"
    )
    for label, items, repair in (
        ("Orphans (tracked, matched by nothing)", report["orphans"], "annotate the directory or add to [gate] always_allow"),
        ("Ghosts (promised, absent on disk)", report["ghosts"], "fix the ownership glob or catalog entry"),
        ("Rule-scope drift (RULE_SCOPES vs .claude/rules/)", report["rules_drift"], "align renderers/context.py with the files on disk"),
    ):
        if items:
            click.echo(f"{label} — {repair}:")
            for item in items:
                click.echo(f"  - {item}")

    if report["orphans"] or report["ghosts"] or report["rules_drift"]:
        raise SystemExit(1)
    click.echo("No orphans, no ghosts, no rule-scope drift.")


@main.command("skills-check")
@click.option(
    "--strict/--advisory",
    "strict",
    default=True,
    help="Strict (the default) exits nonzero on findings; --advisory reports and exits 0 for exploratory runs.",
)
def skills_check(strict: bool) -> None:
    """Reference integrity for .claude skills, commands, and rules. Read-only.

    Every positively identifiable citation - repository paths, Make targets, and
    validator IDs - is verified against the tree, the Makefile, and the validator
    registry. Unclassifiable tokens are ignored, never flagged: a checker that
    false-positives on prose gets switched off and then catches nothing.
    """
    from repo_governance.skills import analyse_skill_references

    report = analyse_skill_references(_context())
    if report["status"] != "ok":
        click.echo(f"Skills check unknown: {report['reason']}")
        raise SystemExit(1)

    click.echo(f"Scanned {report['files_scanned']} file(s) under .claude/")
    for finding in report["findings"]:
        click.echo(f"  {finding['file']}:{finding['line']} [{finding['kind']}] {finding['token']}")
        click.echo(f"    {finding['detail']}")

    count = len(report["findings"])
    if not count:
        click.echo("Every citation resolves: paths, Make targets, and validator IDs all exist.")
        return
    if strict:
        click.echo(f"{count} finding(s) - each is a citation to something that does not exist. Fix the text or the territory.")
        raise SystemExit(1)
    click.echo(f"{count} finding(s), advisory run - the default is strict and CI will fail on these.")


@main.command()
def summary() -> None:
    """Report whether the bounded Summary.md is current. Read-only.

    This used to run a full sync under a read-only-sounding name, which is exactly the
    kind of blurred write path the hook workflow cannot afford: hooks instruct agents to
    run commands, and an instruction chain landing here must never silently write.
    """
    from repo_governance.pipeline import SUMMARY_PATH

    ctx = _context()
    stale = [reason for path, reason in compare_generated(ctx) if path == SUMMARY_PATH]
    if stale:
        click.echo(f"governance/Summary.md is stale: {stale[0]}")
        click.echo("Regenerate with `make governance-sync`.")
        raise SystemExit(1)
    click.echo("governance/Summary.md is current.")


@main.command("gate-metrics")
@click.option("--json", "as_json", is_flag=True, help="Emit the raw report for evaluation records.")
def gate_metrics(as_json: bool) -> None:
    """Aggregate read-gate telemetry into the evidence that tunes [gate]. Read-only.

    Interpretation: high deny counts with high compliance and no decomposition means the
    gate is redirecting cheaply; decomposed sessions mean an agent simulated a denied
    sweep with scoped searches and tokens went up, which argues for demoting that root or
    improving the substitute query; shadow would-fire counts are the promotion case for a
    candidate root. Rising map-first sessions are the convergence signal — the map became
    the habit before the gate had to speak.
    """
    import json as json_module

    from repo_governance.hooks import gate_metrics_report

    report = gate_metrics_report(_context())
    if as_json:
        click.echo(json_module.dumps(report, indent=2, sort_keys=True))
        return
    sessions = report["sessions"]
    breadth = report["breadth"]
    click.echo(
        f"sessions: {sessions['with_telemetry']} with telemetry, "
        f"{sessions['without_telemetry']} without (pre-telemetry), {sessions['degraded']} degraded"
    )
    denies = ", ".join(f"{root}: {count}" for root, count in sorted(breadth["deny_counts_by_root"].items()))
    click.echo(
        f"breadth denials: {sum(breadth['deny_counts_by_root'].values())} "
        f"across {breadth['sessions_with_denials']} session(s)" + (f"  [{denies}]" if denies else "")
    )
    if breadth["sessions_with_denials"]:
        latency = breadth["median_events_to_unlock"]
        click.echo(
            f"  complied after denial: {breadth['complied_after_denial']}/{breadth['sessions_with_denials']}"
            + (f"; median events to unlock: {latency}" if latency is not None else "")
            + (f"; never unlocked: {breadth['never_unlocked']}" if breadth["never_unlocked"] else "")
        )
        click.echo(
            f"  sweep decomposition (>=3 scoped searches under a denied root pre-unlock): "
            f"{breadth['decomposed_sessions']} session(s)"
        )
    click.echo(f"map-first sessions (scoped query before any denial): {breadth['map_first_sessions']}")
    shadows = ", ".join(f"{root}: {count}" for root, count in sorted(report["shadow_would_fire_by_root"].items()))
    click.echo(f"shadow would-fire: {shadows if shadows else 'none'}")
    click.echo(
        f"repo-surface verdicts: {report['repo_surface']['read_verdicts']} read(s), "
        f"{report['repo_surface']['enum_verdicts']} enumeration(s); "
        f"corpus verdicts: {report['corpus_surface']['enum_verdicts']}"
    )
    click.echo("Promotion/demotion of roots is a governed change to [gate] in governance/governance.toml.")


@main.group()
def change() -> None:
    """Governed change sessions."""


@change.command("start")
@click.option("--summary", "summary_text", required=True, help="What the change does.")
@click.option("--reason", required=True, help="Why. A diff can never show this.")
@click.option("--date", "date_text", help="ISO date; defaults to today.")
def change_start(summary_text: str, reason: str, date_text: str | None) -> None:
    """Open a change session, capturing the reason before it can be lost."""
    from datetime import date as date_type

    from repo_governance.session import SessionError, start

    ctx = _context()
    try:
        session = start(
            ctx,
            summary=summary_text,
            reason=reason,
            date=date_text or date_type.today().isoformat(),
        )
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Change session open: {session.summary}")
    click.echo(f"Reason captured:     {session.reason}")
    click.echo(f"Baseline commit:     {session.start_commit or 'unknown'}")
    click.echo("")
    click.echo("Make the smallest coherent change, then `make governance-sync` and finish the record.")


@change.command("finish")
@click.option("--validation", multiple=True, help="id=result or id=result:evidence. Repeatable.")
@click.option("--security-impact", required=False, help="Required. 'None' is acceptable if stated deliberately.")
@click.option("--compatibility-impact", required=False, help="Required.")
@click.option("--rollback", required=False, help="Required. Include anything a revert would not restore.")
@click.option("--effect", "effects", multiple=True, help="Behavioural effect. Repeatable.")
@click.option("--risk", "risks", multiple=True, help="Repeatable.")
@click.option("--limitation", "limitations", multiple=True, help="What this did not do. Repeatable.")
@click.option("--follow-up", "follow_ups", multiple=True, help="Repeatable.")
@click.option("--contract", "contracts", multiple=True, help="Affected contract. Repeatable.")
@click.option("--adr", "adrs", multiple=True, help="ADR reference. Repeatable.")
def change_finish(
    validation: tuple[str, ...],
    security_impact: str | None,
    compatibility_impact: str | None,
    rollback: str | None,
    effects: tuple[str, ...],
    risks: tuple[str, ...],
    limitations: tuple[str, ...],
    follow_ups: tuple[str, ...],
    contracts: tuple[str, ...],
    adrs: tuple[str, ...],
) -> None:
    """Close the session and write a validated permanent record.

    Diff-derived facts are collected automatically. Everything a diff cannot show is
    required as an option: this refuses to invent a security impact or a rollback
    procedure, because a fabricated one is worse than an absent one.
    """
    from repo_governance.io_atomic import write_json_atomic
    from repo_governance.session import SessionError, build_record, clear, load, record_path

    ctx = _context()
    try:
        session = load(ctx)
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    validators = []
    for entry in validation:
        identifier, _, rest = entry.partition("=")
        result, _, evidence = rest.partition(":")
        if result not in {"pass", "fail", "skipped"}:
            raise click.ClickException(
                f"Validator result must be pass, fail, or skipped; got {result!r} for {identifier!r}."
            )
        record_entry = {"id": identifier.strip(), "result": result.strip()}
        if evidence:
            record_entry["evidence_ref"] = evidence.strip()
        validators.append(record_entry)

    judgement = {
        "security_impact": security_impact,
        "compatibility_impact": compatibility_impact,
        "rollback": rollback,
        "behavioral_effects": list(effects),
        "risks": list(risks),
        "limitations": list(limitations),
        "follow_up": list(follow_ups),
        "contracts": list(contracts),
        "adr_refs": list(adrs),
    }

    try:
        record = build_record(ctx, session, judgement=judgement, validators=validators)
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    target = record_path(ctx, record["id"])
    if target.exists():
        raise click.ClickException(f"{target} already exists. Give the change a distinct summary.")

    write_json_atomic(target, record)
    clear(ctx)

    click.echo(f"Wrote {target.relative_to(ctx.repo_root).as_posix()}")
    click.echo(f"  components: {', '.join(record['affected_components']) or 'none matched'}")
    click.echo(f"  files:      {len(record['files_changed'])}")
    click.echo("")
    click.echo("Now run `make governance-sync` to fold it into Summary.md, then `make governance-check`.")


if __name__ == "__main__":  # pragma: no cover
    main()
