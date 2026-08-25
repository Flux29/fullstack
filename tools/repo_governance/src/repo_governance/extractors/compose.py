"""Compose extraction across the file-stack by profile matrix.

Parses the Compose files as YAML rather than shelling out to `docker compose config`.
Three reasons: the CI governance job must run without a Docker daemon; interpolation stays
symbolic so a variable name is recorded and its value never is; and `make compose-check`
already validates that the matrix resolves, so reimplementing that would create two checks
that can disagree about the same fact. Governance wraps that target instead.

The cost is that this parser implements Compose's merge semantics itself, and only the
subset the repository actually uses. `doctor` can cross-check against a real
`docker compose config` when a daemon is available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from repo_governance.config import Context
from repo_governance.extractors.makefile import ComposeStack, extract_makefile

INTERPOLATION = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")
PORT = re.compile(r"^(?:(?P<host_ip>[\d.]+):)?(?P<host_port>[^:]*):(?P<container_port>\d+)$")


@dataclass
class ServiceFacts:
    name: str
    profiles: list[str] = field(default_factory=list)
    image_ref: str | None = None
    image_digest: str | None = None
    build_context: str | None = None
    build_dockerfile: str | None = None
    build_args: list[str] = field(default_factory=list)
    expose: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    volumes: list[dict[str, str]] = field(default_factory=list)
    healthcheck_present: bool = False
    depends_on: list[str] = field(default_factory=list)
    #: Host-environment variables the file interpolates with ${...}. These are inputs the
    #: operator supplies.
    env_refs: list[str] = field(default_factory=list)
    #: Keys of the service's `environment:` mapping. These are consumed by the container
    #: image, which is a different thing from an input and must not be reported as an
    #: unread variable just because the application settings do not declare it.
    env_keys: list[str] = field(default_factory=list)
    gpu: bool = False
    #: Published ports keyed by stack id.
    ports: dict[str, list[dict[str, str | None]]] = field(default_factory=dict)
    #: Stacks this service appears in at all.
    stacks: list[str] = field(default_factory=list)


@dataclass
class ComposeExtraction:
    services: dict[str, ServiceFacts] = field(default_factory=dict)
    networks: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    stacks: list[ComposeStack] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


def _collect_interpolations(node: Any, into: set[str]) -> None:
    """Collect only `${VAR}` references — the values an operator has to supply."""
    if isinstance(node, str):
        into.update(INTERPOLATION.findall(node))
    elif isinstance(node, dict):
        for value in node.values():
            _collect_interpolations(value, into)
    elif isinstance(node, list):
        for item in node:
            _collect_interpolations(item, into)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Compose override semantics for the subset this repository uses.

    Mappings merge key by key; sequences are replaced wholesale, which is what Compose does
    for `command`, `ports`, and `volumes`.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_volume(entry: Any, external_names: set[str]) -> dict[str, str] | None:
    """Parse a service volume entry in short or long (mapping) syntax.

    None means unparseable — the caller must record it as an unknown, because a silently
    dropped mount is indistinguishable from a service that never declared one.
    """
    if isinstance(entry, str):
        parts = entry.split(":")
        if len(parts) < 2:
            return None
        source, target = parts[0], parts[1]
        is_bind = source.startswith((".", "/"))
    elif isinstance(entry, dict) and (mount_type := entry.get("type")) in ("volume", "bind"):
        source, target = entry.get("source"), entry.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        is_bind = mount_type == "bind"
    else:
        return None
    if is_bind:
        volume_class = "bind"
    elif source in external_names:
        volume_class = "external-preserve"
    else:
        volume_class = "named"
    return {"name": source, "target": target, "class": volume_class}


def extract_compose(ctx: Context) -> ComposeExtraction:
    result = ComposeExtraction()
    makefile = extract_makefile(ctx.repo_root / "Makefile")
    result.stacks = makefile.stacks
    result.unknowns.extend(makefile.unknowns)

    if not result.stacks:
        result.unknowns.append("no canonical Compose stacks found in the Makefile")
        return result

    documents: dict[str, dict[str, Any]] = {}
    for stack in result.stacks:
        for relative_path in stack.compose_files:
            if relative_path in documents:
                continue
            path = ctx.repo_root / relative_path
            if not path.is_file():
                result.unknowns.append(f"{relative_path} referenced by the Makefile does not exist")
                continue
            try:
                documents[relative_path] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                result.unknowns.append(f"could not parse {relative_path}: {exc}")

    base_document = documents.get("docker-compose.yml", {})
    declared_volumes = base_document.get("volumes") or {}
    # Both the compose-file key and any `name:` override count as external.
    external_names = {
        alias
        for name, spec in declared_volumes.items()
        if isinstance(spec, dict) and spec.get("external")
        for alias in (name, spec.get("name") or name)
    }

    for name, spec in sorted(declared_volumes.items()):
        spec = spec or {}
        is_external = bool(isinstance(spec, dict) and spec.get("external"))
        result.volumes.append(
            {
                "name": (spec.get("name") if isinstance(spec, dict) else None) or name,
                "class": "external-preserve" if is_external else "named",
            }
        )

    for name, spec in sorted((base_document.get("networks") or {}).items()):
        result.networks.append({"name": name, "internal": bool((spec or {}).get("internal"))})

    for stack in result.stacks:
        resolved: dict[str, dict[str, Any]] = {}
        for relative_path in stack.compose_files:
            services = (documents.get(relative_path) or {}).get("services") or {}
            for service_name, definition in services.items():
                resolved[service_name] = _merge(resolved.get(service_name, {}), definition or {})

        for service_name in sorted(resolved):
            definition = resolved[service_name]
            facts = result.services.setdefault(service_name, ServiceFacts(name=service_name))
            facts.stacks.append(stack.id)

            profiles = definition.get("profiles") or []
            if profiles:
                facts.profiles = sorted(set(facts.profiles) | set(profiles))

            image = definition.get("image")
            if isinstance(image, str):
                if "@" in image:
                    facts.image_ref, facts.image_digest = image.split("@", 1)
                else:
                    facts.image_ref = image

            build = definition.get("build")
            if isinstance(build, dict):
                facts.build_context = build.get("context", "").lstrip("./") or None
                facts.build_dockerfile = build.get("dockerfile")
                facts.build_args = sorted((build.get("args") or {}).keys())
            elif isinstance(build, str):
                facts.build_context = build.lstrip("./")

            facts.expose = [str(item) for item in (definition.get("expose") or [])]
            facts.networks = sorted(definition.get("networks") or [])
            facts.depends_on = sorted((definition.get("depends_on") or {}).keys())
            facts.gpu = bool(definition.get("gpus") or definition.get("runtime") == "nvidia")

            healthcheck = definition.get("healthcheck")
            facts.healthcheck_present = bool(healthcheck) and not healthcheck.get("disable", False)

            volumes = []
            for entry in definition.get("volumes") or []:
                parsed = _parse_volume(entry, external_names)
                if parsed:
                    volumes.append(parsed)
                else:
                    result.unknowns.append(
                        f"could not parse volume entry {entry!r} on service {service_name} in stack {stack.id}"
                    )
            if volumes:
                facts.volumes = volumes

            published: list[dict[str, str | None]] = []
            for entry in definition.get("ports") or []:
                match = PORT.match(str(entry))
                if match:
                    published.append(
                        {
                            "host_ip": match.group("host_ip"),
                            "host_port": match.group("host_port"),
                            "container_port": match.group("container_port"),
                        }
                    )
                else:
                    result.unknowns.append(
                        f"could not parse port mapping {entry!r} on service {service_name} in stack {stack.id}"
                    )
            if published:
                facts.ports[stack.id] = published

            references: set[str] = set()
            for section in ("environment", "ports", "command", "labels"):
                _collect_interpolations(definition.get(section), references)
            if isinstance(build, dict):
                _collect_interpolations(build.get("args"), references)
            facts.env_refs = sorted(set(facts.env_refs) | references)

            environment = definition.get("environment")
            if isinstance(environment, dict):
                facts.env_keys = sorted(set(facts.env_keys) | {key for key in environment if key.isupper()})

    return result
