"""Turn extractor output into manifest documents.

Kept separate from the extractors so that what is read and what is written stay independent:
an extractor answers "what does this source say", a builder answers "what shape does the
manifest take".
"""

from __future__ import annotations

from pathlib import Path
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


#: The complete front-matter vocabulary of an ADR. Closed on purpose: a key outside this
#: set is a typo or an invention, and either way silently dropping it would let an author
#: believe they had declared something governance never read.
ADR_FRONT_MATTER_KEYS: frozenset[str] = frozenset(
    {"id", "title", "status", "date", "components", "supersedes", "related_paths", "policy_refs"}
)

#: Front-matter keys that must be lists of strings.
ADR_LIST_KEYS: tuple[str, ...] = ("components", "supersedes", "related_paths", "policy_refs")

#: Front-matter keys that must be present.
ADR_REQUIRED_KEYS: tuple[str, ...] = ("id", "title", "status", "date", "components")


def _scalar(value: Any) -> str:
    """Render a front-matter scalar as the string the index stores.

    YAML resolves an unquoted `2026-08-06` to a `datetime.date`. Generated output must be
    byte-reproducible text, so dates are normalized back to ISO here rather than relying on
    `str()` of whatever type the resolver picked.
    """
    from datetime import date as date_type
    from datetime import datetime as datetime_type

    if isinstance(value, datetime_type):
        return value.date().isoformat()
    if isinstance(value, date_type):
        return value.isoformat()
    return str(value)


def read_adr_front_matter(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Parse one ADR's YAML front matter.

    Returns `(fields, problems)`. Problems are human-readable strings describing what is
    wrong with the block; the parser never raises and never silently drops a key. The
    builder ignores problems so that a malformed ADR cannot break `sync`, and
    `checks.documentation` reports them — detection and repair stay separate, the same way
    they are for every other governance surface.
    """
    import re

    import yaml

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}, ["No YAML front-matter block. The file must open with `---`."]

    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        return {}, [f"Front matter is not valid YAML: {error}"]

    if loaded is None:
        return {}, ["Front-matter block is empty."]
    if not isinstance(loaded, dict):
        return {}, ["Front matter must be a mapping of keys to values."]

    problems: list[str] = []
    fields: dict[str, Any] = {}

    for key in sorted(loaded):
        if key not in ADR_FRONT_MATTER_KEYS:
            problems.append(f"Unknown front-matter key {key!r}. Allowed: {', '.join(sorted(ADR_FRONT_MATTER_KEYS))}.")
            continue
        fields[key] = loaded[key]

    for key in ADR_REQUIRED_KEYS:
        if key not in fields or fields[key] in (None, "", []):
            problems.append(f"Required front-matter key {key!r} is missing or empty.")

    for key in ADR_LIST_KEYS:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            fields[key] = []
            continue
        if not isinstance(value, list):
            problems.append(f"Front-matter key {key!r} must be a list.")
            fields[key] = []
            continue
        fields[key] = [_scalar(item) for item in value]

    return fields, problems


def build_decision_index(ctx: Context) -> dict[str, Any]:
    """Index the ADRs from their front matter.

    The front matter is the authoritative ADR-to-component link: a component's
    `decision_refs` is derived from it at sync (see `merge.build_components`), so this index
    is the join table the rest of governance reads instead of opening five documents.

    `superseded_by` is derived here rather than declared: an ADR that supersedes another
    knows it, the superseded one should not have to be edited to agree, and two declarations
    of one fact is exactly the drift this phase removes.
    """
    from repo_governance.config import iter_files
    from repo_governance.io_atomic import relative_posix

    decisions = []
    for path in iter_files(ctx.paths.decisions, suffixes=(".md",)):
        if not path.name.startswith("ADR-"):
            continue
        fields, _problems = read_adr_front_matter(path)
        if not fields:
            continue

        entry = {
            "id": _scalar(fields.get("id", path.name.split("-")[0] + "-" + path.name.split("-")[1])),
            "title": _scalar(fields.get("title", path.stem)),
            "status": _scalar(fields.get("status", "proposed")),
            "date": _scalar(fields.get("date", "1970-01-01")),
            "file": relative_posix(path, ctx.repo_root),
        }
        for key in ADR_LIST_KEYS:
            if fields.get(key):
                entry[key] = sorted(fields[key])
        decisions.append(entry)

    superseded_by: dict[str, list[str]] = {}
    for entry in decisions:
        for target in entry.get("supersedes", []):
            superseded_by.setdefault(target, []).append(entry["id"])
    for entry in decisions:
        successors = superseded_by.get(entry["id"])
        if successors:
            entry["superseded_by"] = sorted(successors)

    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": ["docs/architecture/decisions/ADR-*.md"],
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


def build_read_surface(
    ctx: Context, components: dict[str, Any], generated_paths: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Compile the read-gate surface the PreToolUse hook script consults.

    The hook is stdlib-only and must answer in milliseconds, so everything it needs is
    flattened here at sync time: which paths a read may touch (the repo surface) and which
    parts of the governance corpus are answered by queries rather than bulk reads (the
    corpus surface). The gate is steering, not a security boundary — the script fails open
    on anything this document does not confidently cover.

    `generated_paths` plays the same role as in `build_catalog`: files this render is
    about to write count as present, otherwise the first sync of a clean checkout and the
    second would disagree about the surface and idempotence would break.
    """
    from repo_governance.checks.process import SANCTIONED_UNTRACKED_PREFIXES, SANCTIONED_UNTRACKED_SUFFIXES

    gate = ctx.config.raw.get("gate", {})

    rules: list[str] = []
    for component in components.get("components", []):
        rules.extend(component.get("owns", []))
    # Same honesty rule as build_catalog: a declared path that neither exists nor is
    # about to be written is a future intention, not readable surface — including it
    # would only manufacture ghosts.
    rules.extend(
        spec["path"]
        for spec in ctx.config.catalog_spec
        if spec["path"] in generated_paths or (ctx.repo_root / spec["path"].rstrip("/")).exists()
    )
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
            "breadth": str(gate.get("mode_breadth", "warn")),
            "corpus": str(gate.get("mode_corpus", "warn")),
            "repo": str(gate.get("mode_repo", "warn")),
        },
        "breadth": {
            "roots": sorted(gate.get("breadth_roots", ())),
            "shadow_roots": sorted(gate.get("breadth_shadow_roots", ())),
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


def analyse_read_surface_coverage(ctx: Context) -> dict[str, Any]:
    """Measure how much of the tracked tree the compiled read surface accounts for.

    Returns orphans (tracked on disk, matched by nothing), ghosts (promised by the
    surface, absent on disk), rule-scope drift, and the coverage percentage that decides
    whether a repo-surface deny would mean anything. Paths under the corpus roots count
    as covered — they are governed by the query discipline, not by membership. A `None`
    status means git could not answer for this root, never that coverage is clean.
    """
    import fnmatch

    from repo_governance.gitutil import toplevel, tracked_files
    from repo_governance.merge import build_components
    from repo_governance.renderers.context import RULE_SCOPES

    tracked = tracked_files(ctx.repo_root)
    if tracked is None or toplevel(ctx.repo_root) != ctx.repo_root.resolve():
        return {"status": "unknown", "reason": "git did not answer for this repository root"}

    components, _ = build_components(ctx)
    surface = build_read_surface(ctx, components)
    exact = {item.casefold() for item in surface["repo"]["exact_files"]}
    prefixes = [item.casefold() for item in surface["repo"]["dir_prefixes"]]
    patterns = [item.casefold() for item in surface["repo"]["glob_patterns"]]

    def covered(path: str) -> bool:
        folded = path.casefold()
        if folded.startswith("governance/"):
            return True
        if folded in exact:
            return True
        if any(folded.startswith(prefix) for prefix in prefixes):
            return True
        return any(fnmatch.fnmatchcase(folded, pattern) for pattern in patterns)

    considered = [path for path in tracked if not path.startswith(".claude/worktrees/")]
    orphans = sorted(path for path in considered if not covered(path))

    ghosts = sorted(
        {item for item in surface["repo"]["exact_files"] if not (ctx.repo_root / item).is_file()}
        | {item for item in surface["repo"]["dir_prefixes"] if not (ctx.repo_root / item.rstrip("/")).is_dir()}
    )

    declared_rules = {rule_file for rule_file, _ in RULE_SCOPES}
    on_disk_rules = {
        f".claude/rules/{path.name}" for path in sorted((ctx.repo_root / ".claude" / "rules").glob("*.md"))
    }
    rules_drift = sorted(declared_rules ^ on_disk_rules)

    total = len(considered)
    return {
        "status": "ok",
        "total_tracked": total,
        "covered": total - len(orphans),
        "coverage_percent": round(100 * (total - len(orphans)) / total, 2) if total else 100.0,
        "orphans": orphans,
        "ghosts": ghosts,
        "rules_drift": rules_drift,
    }


def sample_map_claims(ctx: Context, *, count: int, seed: str) -> dict[str, Any]:
    """Spot-check the manifests' mechanically-verifiable claims against sampled files.

    The statistical precursor to exhaustive AST enforcement: N files drawn from the
    governed application surface with a seeded generator (reproducible per seed), each
    checked against the claims that can be verified without a graph — layering imports,
    repository commit discipline, the utcnow ban, and settings references being known to
    the configuration manifest. A mismatch means the map and the territory disagree; a
    parse error means the territory could not be read and is reported, never passed.
    """
    import random

    from repo_governance.extractors.python_ast import extract_settings, scan_invariants
    from repo_governance.gitutil import toplevel, tracked_files
    from repo_governance.io_atomic import read_json

    tracked = tracked_files(ctx.repo_root)
    if tracked is None or toplevel(ctx.repo_root) != ctx.repo_root.resolve():
        return {"status": "unknown", "reason": "git did not answer for this repository root"}

    pool = [path for path in tracked if path.startswith("backend/app/") and path.endswith(".py")]
    chosen = sorted(random.Random(seed).sample(pool, min(count, len(pool))))

    known_settings: set[str] = set()
    manifest_path = ctx.paths.generated_manifests / "configuration.json"
    if manifest_path.is_file():
        known_settings.update(
            variable["name"]
            for variable in read_json(manifest_path).get("variables", [])
            if variable.get("surface") == "backend"
        )
    extraction = extract_settings(ctx.repo_root / "backend" / "app" / "core" / "config.py")
    known_settings.update(item.name for item in extraction.fields)
    known_settings.update(extraction.computed)
    known_settings.update(extraction.constants)

    mismatches: list[dict[str, str]] = []
    unreadable: list[dict[str, str]] = []
    for relative in chosen:
        scan = scan_invariants(ctx.repo_root / relative)
        if scan.parse_error is not None:
            unreadable.append({"path": relative, "reason": scan.parse_error})
            continue
        if relative.startswith("backend/app/api/") and scan.repository_imports:
            mismatches.append(
                {
                    "path": relative,
                    "claim": "Routes call services; they never import or call repositories directly",
                    "evidence": "imports " + ", ".join(scan.repository_imports),
                }
            )
        if relative.startswith("backend/app/repositories/") and scan.commit_calls:
            mismatches.append(
                {
                    "path": relative,
                    "claim": "Repositories flush and refresh; the session auto-commits in get_db_session",
                    "evidence": f"{scan.commit_calls} .commit() call(s)",
                }
            )
        if scan.utcnow_calls:
            mismatches.append(
                {
                    "path": relative,
                    "claim": "datetime.now(UTC), never datetime.utcnow()",
                    "evidence": f"{scan.utcnow_calls} .utcnow() call(s)",
                }
            )
        unknown_refs = sorted(set(scan.settings_refs) - known_settings)
        if unknown_refs:
            mismatches.append(
                {
                    "path": relative,
                    "claim": (
                        "Every settings reference is a field, computed property, or "
                        "constant the configuration manifest knows"
                    ),
                    "evidence": "unknown: " + ", ".join(unknown_refs),
                }
            )

    return {
        "status": "ok",
        "seed": seed,
        "pool_size": len(pool),
        "sampled": len(chosen),
        "files": chosen,
        "mismatches": mismatches,
        "unreadable": unreadable,
    }
