"""TypeScript extraction.

**Strategy: targeted regex, not a parser.** The three surfaces governance needs from
TypeScript are narrow and highly regular — the backend path literal inside each proxy route
handler, the entries of the MCP catalog, and the file layout of the App Router. A real
parser (tree-sitter or the TypeScript compiler API) would add a native dependency and a
grammar to maintain for no gain on these three shapes, and Phase 6 is the point at which a
real import graph becomes necessary and the investment pays for itself.

The safeguard that makes this acceptable: **a non-match is recorded, never skipped.** A
route handler with no recognizable backend target becomes an entry with `mechanism:
"unknown"`, not an absent entry. A parser failure that silently returns nothing is
indistinguishable from a source that genuinely contains nothing, and the second is far rarer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from repo_governance.config import Context, iter_files
from repo_governance.io_atomic import relative_posix

#: Which call mechanism a handler uses.
USES_BACKEND_FETCH = re.compile(r"\bbackendFetch\s*(?:<[^>]*>)?\s*\(")
USES_PLAIN_FETCH = re.compile(r"\bfetch\s*\(")
#: The backend path literal itself. Handlers commonly assemble the URL into a local
#: variable before calling — `const url = `/api/v1/x?${qs}`` — so the literal is found
#: wherever it appears in the file rather than only inside the call expression.
BACKEND_PATH = re.compile(r"[`\"'](?:\$\{BACKEND_URL\})?(/api/[^`\"'\s]*)")
#: Exported HTTP method handlers.
METHOD = re.compile(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
#: Template interpolations inside a path, normalized to a parameter placeholder.
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
#: A catalog entry's identifying fields.
CATALOG_ENTRY = re.compile(
    r"\{\s*id:\s*\"(?P<id>[^\"]+)\"(?P<body>.*?)\n\s{2}\}",
    re.DOTALL,
)
CATALOG_FIELD = re.compile(r"(?P<key>title|domain|url|auth|tokenPlacement|category):\s*\"(?P<value>[^\"]*)\"")


@dataclass
class ProxyRoute:
    frontend_path: str
    file: str
    mechanism: str
    methods: list[str] = field(default_factory=list)
    backend_targets: list[dict[str, object]] = field(default_factory=list)


@dataclass
class FrontendExtraction:
    proxy_routes: list[ProxyRoute] = field(default_factory=list)
    pages: list[dict[str, str]] = field(default_factory=list)
    catalog: list[dict[str, str | None]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


def _normalize_segment(segment: str) -> str:
    """Turn an App Router directory name into a path segment."""
    if segment.startswith("(") and segment.endswith(")"):
        return ""  # Route group: organizational only, contributes no URL segment.
    if segment.startswith("[") and segment.endswith("]"):
        inner = segment.strip("[]")
        if inner.startswith("..."):
            inner = inner[3:]
        return "{" + inner + "}"
    return segment


def _route_from_directory(parts: tuple[str, ...]) -> str:
    segments = [_normalize_segment(part) for part in parts]
    return "/" + "/".join(segment for segment in segments if segment)


def _normalize_backend_path(raw: str) -> str:
    # A nested template (`${qs ? `?${qs}` : ""}`) truncates the capture mid-interpolation;
    # the unterminated `${...` tail is query assembly, never a path segment.
    path = INTERPOLATION.sub("{param}", raw).split("?")[0].split("${")[0].rstrip("/")
    return path or "/"


def extract_frontend(ctx: Context) -> FrontendExtraction:
    result = FrontendExtraction()
    app_dir = ctx.repo_root / "frontend" / "src" / "app"
    if not app_dir.is_dir():
        result.unknowns.append("frontend/src/app does not exist")
        return result

    for path in iter_files(app_dir, suffixes=(".ts", ".tsx")):
        relative = path.relative_to(app_dir)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            result.unknowns.append(f"could not read {relative.as_posix()}: {exc}")
            continue

        if path.name == "route.ts" and relative.parts and relative.parts[0] == "api":
            targets = [_normalize_backend_path(match) for match in BACKEND_PATH.findall(text)]
            if not targets:
                mechanism = "unknown"
            elif USES_BACKEND_FETCH.search(text):
                mechanism = "backendFetch"
            elif USES_PLAIN_FETCH.search(text):
                mechanism = "fetch"
            else:
                mechanism = "unknown"

            result.proxy_routes.append(
                ProxyRoute(
                    frontend_path=_route_from_directory(relative.parts[:-1]),
                    file=relative_posix(path, ctx.repo_root),
                    mechanism=mechanism,
                    methods=sorted(set(METHOD.findall(text))),
                    backend_targets=[{"path_template": target} for target in sorted(set(targets))],
                )
            )
            if mechanism == "unknown":
                result.unknowns.append(
                    f"no backend target found in {relative.as_posix()}; recorded as unknown rather than as none"
                )
            continue

        if path.name in {"page.tsx", "page.ts"}:
            parts = tuple(part for part in relative.parts[:-1] if part != "[locale]")
            result.pages.append({"route": _route_from_directory(parts), "file": relative_posix(path, ctx.repo_root)})

    result.proxy_routes.sort(key=lambda item: item.file)
    result.pages.sort(key=lambda item: item["file"])
    result.catalog = extract_mcp_catalog(ctx.repo_root / "frontend" / "src" / "lib" / "mcp-catalog.ts", result)
    return result


def extract_mcp_catalog(path: Path, result: FrontendExtraction) -> list[dict[str, str | None]]:
    """Read the per-user MCP catalog entries.

    URLs are reduced to scheme and host before anything is recorded: catalog entries support
    placing a token in the URL, so a full URL is treated as credential-bearing.
    """
    if not path.is_file():
        result.unknowns.append("frontend/src/lib/mcp-catalog.ts does not exist")
        return []

    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, str | None]] = []

    for match in CATALOG_ENTRY.finditer(text):
        fields = {item.group("key"): item.group("value") for item in CATALOG_FIELD.finditer(match.group("body"))}
        url = fields.get("url") or ""
        host = None
        if "://" in url:
            scheme, _, rest = url.partition("://")
            host = f"{scheme}://{rest.split('/')[0]}"
        entries.append(
            {
                "id": match.group("id"),
                "name": fields.get("title"),
                "category": fields.get("category"),
                "auth_kind": fields.get("auth") or "none",
                "token_placement": fields.get("tokenPlacement") or "none",
                "host": host,
            }
        )

    if not entries:
        result.unknowns.append("no catalog entries matched in mcp-catalog.ts; the file shape may have changed")

    entries.sort(key=lambda item: str(item["id"]))
    return entries
