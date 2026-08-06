"""Secret handling, extraction isolation, and runtime-evidence containment."""

from __future__ import annotations

import re

from repo_governance.checks import CheckScope, relative
from repo_governance.config import iter_files
from repo_governance.models import Issue

#: Credential shapes. Each requires enough entropy after the prefix that a placeholder or a
#: prose mention ("pylf_v2_us_...") cannot match — a scanner that cries wolf on its own
#: documentation gets disabled, which is worse than not having one.
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Logfire token", re.compile(r"pylf_v2_[a-z]{2}_[0-9a-f]{8}-[0-9a-f-]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[a-f0-9]{40,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{40,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b")),
)

#: A URL carrying userinfo or a query string. Connection URLs can embed live API keys, so
#: every governance surface strips both before anything is stored or logged.
URL_WITH_USERINFO = re.compile(r"://[^/\s\"']+:[^/\s@\"']+@")
URL_WITH_QUERY = re.compile(r"https?://[^\s\"'<>]+\?[^\s\"'<>]+")

#: A file that legitimately executes application code must say so. The marker is checked,
#: not assumed, so the isolation rule cannot be bypassed by an unannotated import.
SUBPROCESS_MARKER = "governance:runs-in-scrubbed-subprocess"
APPLICATION_IMPORT = re.compile(r"^\s*(?:from\s+app[\s.]|import\s+app\b)", re.MULTILINE)


def _governance_outputs(scope: CheckScope) -> list:
    ctx = scope.ctx
    files = iter_files(ctx.paths.governance)
    if ctx.paths.env_vars_doc.is_file():
        files.append(ctx.paths.env_vars_doc)
    return files


def check_no_secret_values(scope: CheckScope) -> list[Issue]:
    """No governance artifact contains a credential value or a credential-bearing URL."""
    ctx = scope.ctx
    issues: list[Issue] = []

    for path in _governance_outputs(scope):
        rel = relative(ctx, path)
        if not scope.selects(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for label, pattern in CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append(
                    Issue(
                        message=f"{label} appears in a governance artifact.",
                        path=rel,
                        evidence=f"Matched at offset {match.start()}; the value is not reproduced here.",
                        repair="Remove the value. Governance records names and classifications, never values.",
                    )
                )

        userinfo = URL_WITH_USERINFO.search(text)
        if userinfo:
            issues.append(
                Issue(
                    message="URL with embedded userinfo appears in a governance artifact.",
                    path=rel,
                    evidence=f"Matched at offset {userinfo.start()}.",
                    repair="Strip userinfo at capture time, before storage or logging.",
                )
            )

        query = URL_WITH_QUERY.search(text)
        if query:
            issues.append(
                Issue(
                    message="URL with a query string appears in a governance artifact.",
                    path=rel,
                    evidence=f"Matched at offset {query.start()}.",
                    repair="Strip query strings: a connection URL can carry a live API key.",
                )
            )

    return issues


def check_no_application_imports(scope: CheckScope) -> list[Issue]:
    """The governance tool never imports application code in-process."""
    ctx = scope.ctx
    tool_source = ctx.repo_root / "tools" / "repo_governance" / "src"
    issues: list[Issue] = []

    for path in iter_files(tool_source, suffixes=(".py",)):
        rel = relative(ctx, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if not APPLICATION_IMPORT.search(text):
            continue
        if SUBPROCESS_MARKER in text:
            continue

        issues.append(
            Issue(
                message="Governance module imports application code in-process.",
                path=rel,
                evidence=(
                    "Settings loading walks parent directories for .env and main.py configures "
                    "Logfire at import scope, so this can load real secrets and emit telemetry."
                ),
                repair=(
                    "Extract via AST instead, or move the code into a script that runs in a scrubbed "
                    f"subprocess and mark it with `{SUBPROCESS_MARKER}`."
                ),
            )
        )

    return issues


def check_runtime_evidence_not_committed(scope: CheckScope) -> list[Issue]:
    """Runtime evidence stays out of version control and out of committed artifacts."""
    ctx = scope.ctx
    issues: list[Issue] = []

    evidence_in_governance = [
        path for path in iter_files(ctx.paths.governance) if "evidence" in path.parts and path.suffix == ".json"
    ]
    for path in evidence_in_governance:
        issues.append(
            Issue(
                message="Runtime evidence found inside the committed governance tree.",
                path=relative(ctx, path),
                evidence="Evidence is environment-specific and can contain tenant data or credentials.",
                repair="Move it under .cache/repo-governance/evidence/ and reference it by ID and summary.",
            )
        )

    from repo_governance.gitutil import _run

    if (ctx.paths.cache).exists():
        tracked = _run(["ls-files", "--error-unmatch", ".cache/repo-governance"], ctx.repo_root)
        if tracked:
            issues.append(
                Issue(
                    message="The governance cache is tracked by git.",
                    path=".cache/repo-governance",
                    evidence="Tracked files here would commit runtime evidence.",
                    repair="Ensure .cache is gitignored and untrack the directory.",
                )
            )

    return issues
