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


def check_exposure_posture(scope: CheckScope) -> list[Issue]:
    """Per-stack exposure is the real security posture, and it differs by stack.

    Base publishes nothing, dev binds to loopback only, production publishes only the edge.
    A generic port-collision check is near-vacuous against a base file that publishes
    nothing, while a dev port moved from loopback to 0.0.0.0 would sail past it.
    """
    from repo_governance.builders import build_services

    manifest = build_services(scope.ctx)
    issues: list[Issue] = []

    for service in manifest["services"]:
        for port in service.get("ports", []):
            stack, host_ip = port["stack"], port.get("host_ip")
            published = f"{port.get('host_port')}:{port['container_port']}"

            if stack == "base":
                issues.append(
                    Issue(
                        message=f"The base stack publishes a host port for {service['name']}.",
                        path="docker-compose.yml",
                        evidence=f"{published}. The base stack is expected to publish none.",
                        repair="Move the port mapping into an override file.",
                    )
                )
            elif stack in {"dev", "frontend"} and host_ip != "127.0.0.1":
                issues.append(
                    Issue(
                        message=f"The dev stack publishes {service['name']} on a non-loopback address.",
                        path="docker-compose.dev.yml",
                        evidence=f"{published} bound to {host_ip or 'all interfaces'}.",
                        repair="Bind to 127.0.0.1 so the service is not reachable from the network.",
                    )
                )
            elif stack == "prod" and service["name"] != "traefik" and host_ip != "127.0.0.1":
                issues.append(
                    Issue(
                        message=f"The production stack publishes {service['name']} directly.",
                        path="docker-compose.prod.yml",
                        evidence=f"{published}. Only Traefik should be publicly reachable.",
                        repair="Route it through Traefik instead of publishing it.",
                    )
                )

    for network in manifest["networks"]:
        if network["name"] == "data-internal" and not network["internal"]:
            issues.append(
                Issue(
                    message="data-internal is no longer an internal network.",
                    path="docker-compose.yml",
                    evidence="Making it routable silently gives the database and object storage egress.",
                    repair="Restore internal: true.",
                )
            )

    return issues


def check_images_pinned(scope: CheckScope) -> list[Issue]:
    """Third-party images stay pinned by digest."""
    from repo_governance.builders import build_services

    manifest = build_services(scope.ctx)
    issues: list[Issue] = []

    # Images built in this repository are referenced by tag from several services — the
    # worker and scheduler reuse the image the app service builds — and have no digest to
    # pin. Only third-party images are in scope.
    locally_built = {
        service["image"]["ref"]
        for service in manifest["services"]
        if service.get("build") and service.get("image", {}).get("ref")
    }

    for service in manifest["services"]:
        image = service.get("image")
        if not image or service.get("build"):
            continue
        if image.get("ref") in locally_built:
            continue
        if not image.get("digest"):
            issues.append(
                Issue(
                    message=f"Image for {service['name']} is not pinned by digest.",
                    path="docker-compose.yml",
                    evidence=f"ref {image.get('ref')!r} carries no @sha256 digest.",
                    repair="Pin it. Every other third-party image here already is.",
                )
            )
    return issues


def check_github_readonly_layers(scope: CheckScope) -> list[Issue]:
    """The three GitHub MCP read-only enforcement layers agree."""
    from repo_governance.extractors.mcp import extract_mcp

    extraction = extract_mcp(scope.ctx)
    issues: list[Issue] = []

    for layer in extraction.layers:
        if not layer.present:
            issues.append(
                Issue(
                    message=f"GitHub MCP read-only enforcement layer {layer.layer!r} is missing.",
                    path=layer.location,
                    evidence="Three independent layers are expected; losing one removes defence without any visible failure.",
                    repair="Restore the layer, or record its removal as a reviewed governance change.",
                )
            )

    for problem in extraction.disagreements():
        issues.append(
            Issue(
                message="GitHub MCP read-only allowlists disagree between enforcement layers.",
                path="docker-compose.yml",
                evidence=problem,
                repair="Make the layers identical. Widening one layer only is exactly what this cross-check exists to catch.",
            )
        )

    for unknown in extraction.unknowns:
        issues.append(
            Issue(
                message="A GitHub MCP enforcement layer could not be read.",
                path="backend/app/core/config.py",
                evidence=unknown,
                repair="An unreadable allowlist is reported as unknown, never assumed empty. Restore a literal collection.",
            )
        )

    return issues
