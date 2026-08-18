"""The configuration surface: backend settings, both environment templates, Compose, frontend.

Enumeration is extracted; classification is curated. Nothing here infers whether a variable
is a secret — secret fields are inconsistently typed in this codebase, so an inference rule
would be confidently wrong about at least one of them. The curated list in
`architectural-intent.json` is the authority, and this module merges it in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_governance.config import Context, iter_files
from repo_governance.extractors.python_ast import extract_settings
from repo_governance.io_atomic import read_json, relative_posix

ASSIGNMENT = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
PROCESS_ENV = re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)")
INLINE_COMMENT = re.compile(r"\s+#\s.*$")


@dataclass
class TemplateEntry:
    name: str
    value: str
    description: str
    line: int


@dataclass
class TemplateExtraction:
    entries: dict[str, TemplateEntry] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, list[int]]] = field(default_factory=list)
    malformed: list[tuple[int, str]] = field(default_factory=list)


def parse_env_template(path: Path) -> TemplateExtraction:
    """Parse an environment template.

    Duplicate keys resolve last-wins — matching how a real dotenv loader behaves — and raise
    a finding rather than crashing. A line that is neither blank, a comment, nor an
    assignment is recorded as malformed rather than skipped: a concatenated line is exactly
    the kind of defect that otherwise silently removes a variable from the surface.
    """
    result = TemplateExtraction()
    if not path.is_file():
        return result

    seen: dict[str, list[int]] = {}
    comment_block: list[str] = []

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            comment_block.clear()
            continue
        if line.startswith("#"):
            stripped = line.lstrip("#").strip()
            # Section banners are structure, not description. Without this, the first
            # variable under each banner inherits the section name as its description.
            is_banner = stripped.startswith("=") or stripped.endswith("=") or set(stripped) <= {"=", "-"}
            if stripped and not is_banner:
                comment_block.append(stripped)
            continue

        match = ASSIGNMENT.match(line)
        if not match:
            result.malformed.append((number, raw))
            comment_block.clear()
            continue

        name = match.group("key")
        value = INLINE_COMMENT.sub("", match.group("value")).strip()
        seen.setdefault(name, []).append(number)
        if name not in result.order:
            result.order.append(name)
        result.entries[name] = TemplateEntry(
            name=name,
            value=value,
            description=" ".join(comment_block),
            line=number,
        )
        comment_block.clear()

    result.duplicates = sorted((name, lines) for name, lines in seen.items() if len(lines) > 1)
    return result


def scan_process_env(root: Path, repo_root: Path) -> dict[str, list[str]]:
    """Find `process.env.NAME` references in the frontend source.

    Paths are recorded relative to the repository root. An absolute path would embed the
    checkout location in a committed manifest, so the file would differ between a developer
    machine and a CI runner and the drift gate would fail on every run for no reason.
    """
    found: dict[str, set[str]] = {}
    for path in iter_files(root, suffixes=(".ts", ".tsx", ".mjs", ".js")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in PROCESS_ENV.findall(text):
            found.setdefault(name, set()).add(relative_posix(path, repo_root))
    return {name: sorted(paths) for name, paths in sorted(found.items())}


def _curated_classifications(ctx: Context) -> dict[str, dict[str, Any]]:
    path = ctx.paths.curated_manifests / "architectural-intent.json"
    if not path.is_file():
        return {}
    document = read_json(path)
    return {entry["name"]: entry for entry in document.get("configuration_classifications", [])}


def _component_config_refs(ctx: Context) -> dict[str, list[str]]:
    """Which components reference which variables, from their declarations."""
    from repo_governance.merge import build_components

    manifest, _ = build_components(ctx)
    consumers: dict[str, set[str]] = {}
    for component in manifest["components"]:
        for name in component.get("configuration_refs", []):
            consumers.setdefault(name, set()).add(component["id"])
    return {name: sorted(ids) for name, ids in consumers.items()}


@dataclass
class ComposeConfigRefs:
    """Which Compose services reference a variable, and in what capacity.

    The distinction matters: an interpolated `${VAR}` is something an operator has to
    supply, while a literal `environment:` key is consumed by the container image. Treating
    the second as an unread variable — merely because the application settings do not
    declare it — would report two dozen container-internal knobs as defects.
    """

    interpolated: dict[str, list[str]] = field(default_factory=dict)
    container_consumed: dict[str, list[str]] = field(default_factory=dict)
    build_args: dict[str, list[str]] = field(default_factory=dict)

    def names(self) -> set[str]:
        return {*self.interpolated, *self.container_consumed, *self.build_args}


def _compose_refs(ctx: Context) -> ComposeConfigRefs:
    from repo_governance.extractors.compose import extract_compose

    compose = extract_compose(ctx)
    refs = ComposeConfigRefs()
    interpolated: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    build: dict[str, set[str]] = {}

    for service in compose.services.values():
        for name in service.env_refs:
            interpolated.setdefault(name, set()).add(service.name)
        for name in service.env_keys:
            consumed.setdefault(name, set()).add(service.name)
        for name in service.build_args:
            build.setdefault(name, set()).add(service.name)

    refs.interpolated = {name: sorted(items) for name, items in sorted(interpolated.items())}
    refs.container_consumed = {name: sorted(items) for name, items in sorted(consumed.items())}
    refs.build_args = {name: sorted(items) for name, items in sorted(build.items())}
    return refs


def extract_configuration(ctx: Context) -> dict[str, Any]:
    """Build the configuration manifest from every declaring source."""
    settings_path = ctx.repo_root / "backend" / "app" / "core" / "config.py"
    settings = extract_settings(settings_path)

    backend_template = parse_env_template(ctx.repo_root / "backend" / ".env.example")
    frontend_template = parse_env_template(ctx.repo_root / "frontend" / ".env.example")
    process_env = scan_process_env(ctx.repo_root / "frontend" / "src", ctx.repo_root)
    curated = _curated_classifications(ctx)
    consumers = _component_config_refs(ctx)
    compose_refs = _compose_refs(ctx)

    settings_by_name = {item.name: item for item in settings.fields}
    names = sorted(
        {
            *settings_by_name,
            *backend_template.entries,
            *frontend_template.entries,
            *process_env,
            *compose_refs.names(),
            *curated,
        }
    )

    variables: list[dict[str, Any]] = []
    for name in names:
        setting = settings_by_name.get(name)
        curated_entry = curated.get(name, {})
        classification = list(curated_entry.get("classification", []))

        declared_in: list[str] = []
        if setting:
            declared_in.append("backend/app/core/config.py")
        if name in backend_template.entries:
            declared_in.append("backend/.env.example")
        if name in frontend_template.entries:
            declared_in.append("frontend/.env.example")
        if name in compose_refs.names():
            declared_in.append("docker-compose.yml")

        if (
            name.startswith("NEXT_PUBLIC_")
            or name in frontend_template.entries
            or (name in process_env and not setting)
        ):
            surface = "frontend"
        elif setting or name in backend_template.entries:
            surface = "backend"
        else:
            surface = "compose"

        if not classification:
            if setting:
                classification = ["runtime"]
            elif name in compose_refs.build_args:
                classification = ["build-arg"]
            elif name in compose_refs.names():
                # Interpolated by, or consumed by, a container. Either way something reads
                # it; it is simply not an application settings field.
                classification = ["compose-only"]
            else:
                classification = ["unknown-consumer"]

        template = backend_template.entries.get(name) or frontend_template.entries.get(name)
        default = None
        default_class = "none"
        if setting and setting.default is not None:
            default, default_class = setting.default, "literal"
        elif template and template.value:
            default, default_class = template.value, "literal"
        if "secret" in classification:
            default, default_class = None, "none"
        if "computed" in classification:
            default_class = "generated"

        consumed = list(consumers.get(name, []))
        consumed.extend(f"frontend:{path}" for path in process_env.get(name, [])[:3])
        consumed.extend(f"compose:{service}" for service in compose_refs.container_consumed.get(name, []))

        record: dict[str, Any] = {
            "name": name,
            "surface": surface,
            "type": setting.annotation if setting else None,
            "default": default,
            "default_class": default_class,
            "required": bool(setting and not setting.has_default),
            "classification": sorted(set(classification)),
            "declared_in": declared_in,
            "consumed_by": sorted(set(consumed)),
        }
        if curated_entry.get("alias_of"):
            record["alias_of"] = curated_entry["alias_of"]
        if curated_entry.get("removal_milestone"):
            record["removal_milestone"] = curated_entry["removal_milestone"]
        if template and template.description:
            record["description"] = template.description
        variables.append(record)

    findings: list[dict[str, str]] = []
    for label, extraction in (("backend", backend_template), ("frontend", frontend_template)):
        for name, lines in extraction.duplicates:
            findings.append(
                {
                    "id": f"duplicate-{label}-{name.lower().replace('_', '-')}",
                    "kind": "duplicate-key",
                    "detail": (
                        f"{name} is assigned more than once in the {label} template, at lines "
                        f"{', '.join(str(line) for line in lines)}. Parsed last-wins."
                    ),
                    "status": "open",
                }
            )
        for number, text in extraction.malformed:
            findings.append(
                {
                    "id": f"malformed-{label}-line-{number}",
                    "kind": "malformed-line",
                    "detail": f"Line {number} of the {label} template is neither blank, a comment, nor an assignment: {text[:80]!r}",
                    "status": "open",
                }
            )

    for variable in variables:
        name = variable["name"]
        if "unknown-consumer" not in variable["classification"]:
            continue
        if not any(source.endswith(".env.example") for source in variable["declared_in"]):
            continue
        findings.append(
            {
                "id": f"unconsumed-{name.lower().replace('_', '-')}",
                "kind": "unconsumed",
                "detail": (
                    f"{name} appears in an environment template but has no field in Settings and no "
                    'Compose consumer. Because Settings uses extra="ignore", setting it has no effect '
                    "and produces no warning."
                ),
                "status": "open",
            }
        )

    for unknown in settings.unknowns:
        findings.append(
            {
                "id": "settings-extraction-unknown",
                "kind": "undeclared",
                "detail": f"Settings extraction reported an unknown: {unknown}",
                "status": "open",
            }
        )

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": sorted(
                {
                    "backend/app/core/config.py",
                    "backend/.env.example",
                    "frontend/.env.example",
                    "docker-compose.yml",
                    "frontend/src/**",
                    "governance/manifests/curated/architectural-intent.json",
                }
            ),
            "extractor_version": ctx.config.version,
        },
        "secret_list_source": "curated",
        "variables": variables,
        "findings": sorted(findings, key=lambda item: item["id"]),
    }


def template_sections(path: Path) -> list[tuple[str, list[str]]]:
    """Group a template's variables under its section banners, preserving file order.

    Used by the ENV_VARS renderer so the generated document reads in the same order as the
    template a developer is actually editing.
    """
    if not path.is_file():
        return []

    sections: list[tuple[str, list[str]]] = []
    current = "General"
    members: list[str] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            banner = line.lstrip("#").strip().strip("= ").strip()
            if banner and (line.count("=") >= 2 or line.startswith("# ===")):
                if members:
                    sections.append((current, members))
                    members = []
                current = banner
            continue
        match = ASSIGNMENT.match(line)
        if match:
            name = match.group("key")
            if name not in members:
                members.append(name)

    if members:
        sections.append((current, members))
    return sections


def relative(ctx: Context, path: Path) -> str:
    return relative_posix(path, ctx.repo_root)
