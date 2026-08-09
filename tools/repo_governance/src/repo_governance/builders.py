"""Turn extractor output into manifest documents.

Kept separate from the extractors so that what is read and what is written stay independent:
an extractor answers "what does this source say", a builder answers "what shape does the
manifest take".
"""

from __future__ import annotations

from typing import Any

from repo_governance.config import Context
from repo_governance.extractors.compose import extract_compose


def build_services(ctx: Context) -> dict[str, Any]:
    """Build the services manifest from the Compose files and the Makefile.

    Pure extraction. The link from a Compose service to a component lives on the component,
    in the curated manifest, because that link is a judgment about intent rather than a fact
    any Compose file states.
    """
    compose = extract_compose(ctx)

    stacks = [
        {
            "id": stack.id,
            "make_variable": stack.make_variable,
            "compose_files": list(stack.compose_files),
            "profiles_available": sorted(
                {profile for service in compose.services.values() for profile in service.profiles}
            ),
            "profiles_always_on": list(stack.profiles_always_on),
        }
        for stack in compose.stacks
    ]

    services = []
    for name in sorted(compose.services):
        facts = compose.services[name]
        record: dict[str, Any] = {
            "name": name,
            "presence": [
                {
                    "stack": stack_id,
                    "profiles": sorted(facts.profiles),
                    "active_without_profile": not facts.profiles,
                }
                for stack_id in sorted(set(facts.stacks))
            ],
            "expose": facts.expose,
            "networks": facts.networks,
            "volumes": facts.volumes,
            "healthcheck": {"present": facts.healthcheck_present},
            "depends_on": facts.depends_on,
            "env_refs": facts.env_refs,
            "gpu": facts.gpu,
        }

        if facts.image_ref:
            image: dict[str, str] = {"ref": facts.image_ref}
            if facts.image_digest:
                image["digest"] = facts.image_digest
            record["image"] = image

        if facts.build_context:
            build: dict[str, Any] = {"context": facts.build_context}
            if facts.build_dockerfile:
                build["dockerfile"] = facts.build_dockerfile
            build["args"] = facts.build_args
            record["build"] = build

        ports = []
        for stack_id in sorted(facts.ports):
            for entry in facts.ports[stack_id]:
                ports.append({"stack": stack_id, **entry})
        if ports:
            record["ports"] = ports

        services.append(record)

    host_runtimes = [
        {
            "id": "model-runner-host",
            "purpose": (
                "Docker Model Runner on the host, serving embeddings and the reranker. No Compose "
                "file declares it, so without this node the embedding and reranker dependency edges "
                "are invisible to every graph query."
            ),
            "endpoints": sorted(
                {
                    reference
                    for service in compose.services.values()
                    for reference in service.env_refs
                    if "MODEL" in reference
                }
                | {"http://model-runner.docker.internal/engines/v1", "http://localhost:12434"}
            ),
            "healthcheck_validator": "preflight-model",
        }
    ]

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": sorted({"Makefile", *(f for stack in compose.stacks for f in stack.compose_files)}),
            "extractor_version": ctx.config.version,
        },
        "stacks": stacks,
        "services": services,
        "host_runtimes": host_runtimes,
        "networks": compose.networks,
        "volumes": compose.volumes,
    }


def build_interfaces(ctx: Context) -> dict[str, Any]:
    """Build the interfaces manifest.

    Almost everything here is extracted. The WebSocket event inventory is not: those events
    are ad-hoc dicts with no schema on either side, so enumerating them is a governance
    deliverable rather than a reading of something that already exists. It comes from the
    curated manifest and is merged in.
    """
    import re

    from repo_governance.config import iter_files
    from repo_governance.extractors.openapi import extract_openapi
    from repo_governance.extractors.typescript_ast import extract_frontend
    from repo_governance.io_atomic import read_json, relative_posix

    frontend = extract_frontend(ctx)
    openapi = extract_openapi(ctx)

    openapi_section: dict[str, Any] = {"status": openapi.status}
    if openapi.status == "extracted":
        openapi_section["route_count"] = openapi.route_count
        openapi_section["paths"] = openapi.paths
    else:
        openapi_section["unknown_reason"] = openapi.unknown_reason or "the exporter did not run"
        openapi_section["paths"] = []

    routes_dir = ctx.repo_root / "backend" / "app" / "api" / "routes"
    websocket_endpoint = None
    sse: list[dict[str, str]] = []
    ws_pattern = re.compile(r"@router\.websocket\(\s*[\"']([^\"']+)[\"']")
    sse_pattern = re.compile(r"@router\.get\(\s*[\"']([^\"']+)[\"'][^)]*EventSourceResponse", re.DOTALL)

    # A route module's prefix is applied where its router is included, not where it is
    # constructed. Without resolving it here, a path reads as router-relative and matches
    # nothing a proxy handler or an OpenAPI entry names.
    include_pattern = re.compile(r"include_router\(\s*(\w+)\.router[^)]*?prefix\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)
    own_prefix_pattern = re.compile(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)

    prefixes: dict[str, str] = {}
    registry = routes_dir / "v1" / "__init__.py"
    if registry.is_file():
        prefixes = dict(include_pattern.findall(registry.read_text(encoding="utf-8")))

    for path in iter_files(routes_dir, suffixes=(".py",)):
        text = path.read_text(encoding="utf-8")
        own = own_prefix_pattern.search(text)
        prefix = prefixes.get(path.stem, own.group(1) if own else "")

        match = ws_pattern.search(text)
        if match:
            websocket_endpoint = f"/api/v1{prefix}{match.group(1)}"
        for sse_match in sse_pattern.finditer(text):
            sse.append(
                {
                    "path": f"/api/v1{prefix}{sse_match.group(1)}",
                    "location": relative_posix(path, ctx.repo_root),
                    "purpose": "Server-sent events stream",
                }
            )

    curated_path = ctx.paths.curated_manifests / "architectural-intent.json"
    events = []
    if curated_path.is_file():
        for event in read_json(curated_path).get("websocket_events", []):
            events.append({**event, "provenance": "reviewed-baseline"})

    locales_file = ctx.repo_root / "frontend" / "src" / "i18n.ts"
    locales: list[str] = []
    if locales_file.is_file():
        match = re.search(r"locales\s*=\s*\[([^\]]*)\]", locales_file.read_text(encoding="utf-8"))
        if match:
            locales = sorted(re.findall(r"[\"']([a-z-]+)[\"']", match.group(1)))

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": sorted(
                {
                    "backend/app/api/routes/",
                    "frontend/src/app/api/",
                    "frontend/src/app/[locale]/",
                    "frontend/src/i18n.ts",
                    "governance/manifests/curated/architectural-intent.json",
                }
            ),
            "extractor_version": ctx.config.version,
        },
        "openapi": openapi_section,
        "websocket": {
            "endpoint": websocket_endpoint or "/api/v1/ws/agent",
            "auth": "token-subprotocol",
            "proxy_exception_ref": "ws-chat-bypasses-proxy",
            "events": events,
        },
        "sse": sorted(sse, key=lambda item: item["path"]),
        "proxy_routes": [
            {
                "frontend_path": route.frontend_path,
                "file": route.file,
                "mechanism": route.mechanism,
                "methods": route.methods,
                "backend_targets": route.backend_targets,
            }
            for route in frontend.proxy_routes
        ],
        "frontend_pages": frontend.pages,
        "locales": locales,
    }


def build_decision_index(ctx: Context) -> dict[str, Any]:
    """Index the ADRs from their front matter.

    Deliberately a small hand-rolled parser rather than a YAML dependency for the whole
    document: the front matter here is a fixed set of scalars and one string list, and an
    ADR whose front matter cannot be read is reported rather than skipped.
    """
    import re

    from repo_governance.config import iter_files
    from repo_governance.io_atomic import relative_posix

    decisions = []
    for path in iter_files(ctx.paths.decisions, suffixes=(".md",)):
        if not path.name.startswith("ADR-"):
            continue
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not match:
            continue

        fields: dict[str, Any] = {}
        current_list: str | None = None
        for line in match.group(1).splitlines():
            if line.startswith("  - ") and current_list:
                fields.setdefault(current_list, []).append(line[4:].strip())
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if not key:
                continue
            if value:
                fields[key] = value
                current_list = None
            else:
                current_list = key
                fields.setdefault(key, [])

        entry = {
            "id": str(fields.get("id", path.name.split("-title")[0][:7])),
            "title": str(fields.get("title", path.stem)),
            "status": str(fields.get("status", "proposed")),
            "date": str(fields.get("date", "1970-01-01")),
            "file": relative_posix(path, ctx.repo_root),
        }
        if fields.get("components"):
            entry["components"] = sorted(fields["components"])
        if fields.get("supersedes"):
            entry["supersedes"] = sorted(fields["supersedes"])
        decisions.append(entry)

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": ["governance/history/decisions/ADR-*.md"],
            "extractor_version": ctx.config.version,
        },
        "decisions": sorted(decisions, key=lambda item: item["id"]),
    }


#: Characters fnmatch treats as pattern syntax. A rule containing none of them is a
#: literal path, which matters because ownership globs contain literal `[locale]` segments.
_WILDCARD_CHARS = ("*", "?", "[")


def _classify_path_rules(rules: list[str]) -> tuple[set[str], set[str], set[str]]:
    """Split path rules into exact files, directory prefixes, and fnmatch patterns.

    Prefixes are matched by `startswith`, never fnmatch, so a literal bracketed segment
    like `frontend/src/app/[locale]/` cannot be misread as a character class.
    """
    exact: set[str] = set()
    prefixes: set[str] = set()
    patterns: set[str] = set()
    for rule in rules:
        if rule.endswith("/**"):
            prefixes.add(rule[:-2])
        elif rule.endswith("/"):
            prefixes.add(rule)
        elif any(char in rule for char in _WILDCARD_CHARS):
            patterns.add(rule)
        else:
            exact.add(rule)
    return exact, prefixes, patterns


def build_read_surface(ctx: Context, components: dict[str, Any]) -> dict[str, Any]:
    """Compile the read-gate surface the PreToolUse hook script consults.

    The hook is stdlib-only and must answer in milliseconds, so everything it needs is
    flattened here at sync time: which paths a read may touch (the repo surface) and which
    parts of the governance corpus are answered by queries rather than bulk reads (the
    corpus surface). The gate is steering, not a security boundary — the script fails open
    on anything this document does not confidently cover.
    """
    from repo_governance.checks.process import SANCTIONED_UNTRACKED_PREFIXES, SANCTIONED_UNTRACKED_SUFFIXES

    gate = ctx.config.raw.get("gate", {})

    rules: list[str] = []
    for component in components.get("components", []):
        rules.extend(component.get("owns", []))
    rules.extend(spec["path"] for spec in ctx.config.catalog_spec)
    rules.extend(SANCTIONED_UNTRACKED_PREFIXES)
    rules.extend(gate.get("always_allow", ()))

    exact, prefixes, patterns = _classify_path_rules(rules)
    patterns.update(f"*{suffix}" for suffix in SANCTIONED_UNTRACKED_SUFFIXES)

    return {
        "schema_version": 1,
        "provenance": {
            "method": "extracted",
            "sources": [
                "governance/governance.toml",
                "governance/manifests/generated/components.json",
                "tools/repo_governance/src/repo_governance/checks/process.py",
            ],
            "extractor_version": ctx.config.version,
        },
        "default_modes": {
            "corpus": str(gate.get("mode_corpus", "warn")),
            "repo": str(gate.get("mode_repo", "warn")),
        },
        "corpus": {
            "gated_roots": sorted(gate.get("corpus_gated_roots", ())),
            "bounded_surfaces": sorted(gate.get("corpus_bounded_surfaces", ())),
        },
        "repo": {
            "exact_files": sorted(exact),
            "dir_prefixes": sorted(prefixes),
            "glob_patterns": sorted(patterns),
        },
    }
