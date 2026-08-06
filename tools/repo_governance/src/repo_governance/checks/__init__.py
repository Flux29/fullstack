"""Check dispatch.

A check never decides its own severity. Policy files declare rules — statement, maturity,
and the `check_impl` that implements them — and this module resolves each `check_impl` to a
function, runs it, and stamps the resulting issues with the rule's identity and maturity.
That inversion is what makes the policy files load-bearing rather than documentation: to
promote a rule from advisory to blocking you edit the policy, not the code.

Rules whose `check_impl` is a phase marker or `manual` are reported as deferred. They are
never silently treated as passing: a rule that cannot be evaluated yet is uncertainty, and
the blueprint requires policies to distinguish uncertainty from violations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.io_atomic import read_json
from repo_governance.models import CheckReport, Finding, Issue, Maturity


@dataclass(frozen=True)
class CheckScope:
    """What a check run is allowed to look at."""

    ctx: Context
    #: Repo-relative POSIX paths under consideration, or None for the whole repository.
    files: tuple[str, ...] | None = None
    fast: bool = False
    #: Optional git ref. When set, process checks consider the commit range since that ref
    #: in addition to the working tree, so CI can enforce against a pull-request base.
    since: str | None = None

    def selects(self, relative_path: str) -> bool:
        if self.files is None:
            return True
        return relative_path in self.files


CheckFunction = Callable[[CheckScope], list[Issue]]


@dataclass(frozen=True)
class Rule:
    id: str
    group: str
    statement: str
    maturity: Maturity
    check_impl: str


def iter_rules(ctx: Context) -> list[Rule]:
    """Load every declared rule from every policy file, in stable order."""
    rules: list[Rule] = []
    for path in iter_files(ctx.paths.policies, suffixes=(".json",)):
        document = read_json(path)
        group = document.get("policy_group", path.stem)
        for raw in document.get("rules", []):
            rules.append(
                Rule(
                    id=raw["id"],
                    group=group,
                    statement=raw["statement"],
                    maturity=raw["maturity"],
                    check_impl=raw["check_impl"],
                )
            )
    return sorted(rules, key=lambda rule: (rule.group, rule.id))


def _registry() -> dict[str, tuple[CheckFunction, bool]]:
    """Map `check_impl` to its function and whether it is cheap enough for pre-commit.

    Imported lazily so a syntax error in one check module surfaces as a real traceback
    rather than an import-time failure of the whole CLI.
    """
    from repo_governance.checks import architecture, compatibility, process, references, schemas, security

    return {
        "checks.architecture.check_single_declaration": (architecture.check_single_declaration, False),
        "checks.architecture.check_dependency_declarations": (architecture.check_dependency_declarations, False),
        "checks.architecture.check_healthchecks": (architecture.check_healthchecks, False),
        "checks.architecture.check_proxy_coverage": (architecture.check_proxy_coverage, False),
        "checks.compatibility.check_alias_milestones": (compatibility.check_alias_milestones, False),
        "checks.compatibility.check_build_arg_classification": (compatibility.check_build_arg_classification, False),
        "checks.compatibility.check_migration_graph": (compatibility.check_migration_graph, False),
        "checks.compatibility.check_redis_allocations": (compatibility.check_redis_allocations, False),
        "checks.security.check_exposure_posture": (security.check_exposure_posture, False),
        "checks.security.check_images_pinned": (security.check_images_pinned, False),
        "checks.security.check_github_readonly_layers": (security.check_github_readonly_layers, False),
        "checks.process.check_exception_hygiene": (process.check_exception_hygiene, False),
        "checks.process.check_suite_validator_coverage": (process.check_suite_validator_coverage, False),
        "checks.process.check_validator_ci_jobs": (process.check_validator_ci_jobs, False),
        "checks.schemas.check_document_schemas": (schemas.check_document_schemas, True),
        "checks.schemas.check_generated_files": (schemas.check_generated_files, True),
        "checks.schemas.check_idempotence": (schemas.check_idempotence, False),
        "checks.schemas.check_curated_not_generated": (schemas.check_curated_not_generated, False),
        "checks.schemas.check_contract_version": (schemas.check_contract_version, True),
        "checks.references.check_validator_references": (references.check_validator_references, True),
        "checks.references.check_excluded_validators": (references.check_excluded_validators, True),
        "checks.references.check_document_references": (references.check_document_references, False),
        "checks.references.check_check_impl_resolves": (references.check_check_impl_resolves, False),
        "checks.security.check_no_secret_values": (security.check_no_secret_values, True),
        "checks.security.check_no_application_imports": (security.check_no_application_imports, False),
        "checks.security.check_runtime_evidence_not_committed": (
            security.check_runtime_evidence_not_committed,
            True,
        ),
        "checks.process.check_governance_change_records": (process.check_governance_change_records, True),
        "checks.process.check_policy_weakening_recorded": (process.check_policy_weakening_recorded, False),
    }


def resolvable_check_impls() -> set[str]:
    return set(_registry())


def run_checks(scope: CheckScope) -> CheckReport:
    """Evaluate every declared rule against the repository."""
    report = CheckReport()
    registry = _registry()

    for rule in iter_rules(scope.ctx):
        if rule.maturity in {"retired", "deprecated"}:
            continue

        if rule.check_impl == "manual" or rule.check_impl.startswith("phase-"):
            report.checks_deferred.append(f"{rule.id}: not yet enforced ({rule.check_impl})")
            continue

        entry = registry.get(rule.check_impl)
        if entry is None:
            report.add(
                Finding(
                    rule=rule.id,
                    maturity="blocking",
                    message=f"Policy rule declares check_impl {rule.check_impl!r}, which does not resolve to any check.",
                    path=f"governance/policies/{rule.group}.json",
                    repair="Fix the check_impl value, or implement the check and register it in checks/__init__.py.",
                )
            )
            continue

        function, is_fast = entry
        if scope.fast and not is_fast:
            report.checks_deferred.append(f"{rule.id}: skipped in fast mode")
            continue

        report.checks_run.append(rule.id)
        for issue in function(scope):
            report.add(
                Finding(
                    rule=rule.id,
                    maturity=rule.maturity,
                    message=issue.message,
                    path=issue.path,
                    evidence=issue.evidence,
                    repair=issue.repair,
                )
            )

    return report


def relative(ctx: Context, path: Path) -> str:
    """Repo-relative POSIX form, for use in issue paths."""
    from repo_governance.io_atomic import relative_posix

    return relative_posix(path, ctx.repo_root)
