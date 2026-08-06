"""MCP enforcement-layer extraction.

The GitHub MCP server is read-only by three independent mechanisms. Governance does not
re-declare that policy — a fourth declaration would just be a fourth thing to drift. It
reads all three and compares them, because a widening applied to one layer only is exactly
the failure a single declaration could never catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from repo_governance.config import Context
from repo_governance.extractors.python_ast import extract_frozenset_members, module_contains

TOOLS_FLAG = re.compile(r"^--tools$")
GOOGLE_KIND = re.compile(r"[\"'](?P<url>https?://[^\"']+)[\"']\s*:\s*[\"'](?P<kind>[a-z_]+)[\"']")


@dataclass
class ReadOnlyLayer:
    layer: str
    location: str
    tools: list[str] | None
    present: bool


@dataclass
class McpExtraction:
    layers: list[ReadOnlyLayer] = field(default_factory=list)
    google_url_kinds: dict[str, str] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)

    def disagreements(self) -> list[str]:
        """Pairs of layers whose allowlists differ.

        A layer whose list could not be read is reported as unknown by the caller, not
        silently treated as agreeing.
        """
        known = [layer for layer in self.layers if layer.tools is not None]
        problems: list[str] = []
        for index, first in enumerate(known):
            for second in known[index + 1 :]:
                if first.tools != second.tools:
                    only_first = sorted(set(first.tools or []) - set(second.tools or []))
                    only_second = sorted(set(second.tools or []) - set(first.tools or []))
                    problems.append(
                        f"{first.layer} and {second.layer} disagree: "
                        f"only in {first.layer}: {only_first or 'none'}; "
                        f"only in {second.layer}: {only_second or 'none'}"
                    )
        return problems


def _container_tools(ctx: Context) -> ReadOnlyLayer:
    path = ctx.repo_root / "docker-compose.yml"
    location = "docker-compose.yml service github-mcp command"
    if not path.is_file():
        return ReadOnlyLayer("container-flags", location, None, False)

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ReadOnlyLayer("container-flags", location, None, False)

    command = ((document.get("services") or {}).get("github-mcp") or {}).get("command") or []
    if not isinstance(command, list):
        return ReadOnlyLayer("container-flags", location, None, False)

    read_only = "--read-only" in command
    tools: list[str] | None = None
    for index, item in enumerate(command):
        if TOOLS_FLAG.match(str(item)) and index + 1 < len(command):
            tools = sorted(str(command[index + 1]).split(","))
            break

    return ReadOnlyLayer("container-flags", location, tools, read_only)


def extract_mcp(ctx: Context) -> McpExtraction:
    result = McpExtraction()

    result.layers.append(_container_tools(ctx))

    config_path = ctx.repo_root / "backend" / "app" / "core" / "config.py"
    settings_tools = extract_frozenset_members(config_path, "GITHUB_MCP_READ_ONLY_TOOLS")
    result.layers.append(
        ReadOnlyLayer(
            "settings-validator",
            "backend/app/core/config.py GITHUB_MCP_READ_ONLY_TOOLS",
            settings_tools,
            settings_tools is not None,
        )
    )
    if settings_tools is None:
        result.unknowns.append(
            "GITHUB_MCP_READ_ONLY_TOOLS could not be read as a literal collection; "
            "reported as unknown rather than as an empty allowlist"
        )

    mcp_path = ctx.repo_root / "backend" / "app" / "agents" / "mcp.py"
    assert_present = module_contains(mcp_path, ("GITHUB_MCP_READ_ONLY_TOOLS",))
    result.layers.append(
        ReadOnlyLayer(
            "runtime-assert",
            "backend/app/agents/mcp.py post-probe assert",
            settings_tools if assert_present else None,
            assert_present,
        )
    )
    if not assert_present:
        result.unknowns.append(
            "no reference to the read-only allowlist found in backend/app/agents/mcp.py; "
            "the runtime post-probe layer may have been removed"
        )

    api_path = ctx.repo_root / "backend" / "app" / "agents" / "google_workspace_api.py"
    if api_path.is_file():
        text = api_path.read_text(encoding="utf-8")
        for match in GOOGLE_KIND.finditer(text):
            url = match.group("url")
            host = url.split("://", 1)[1].split("/")[0]
            result.google_url_kinds[host] = match.group("kind")
    else:
        result.unknowns.append("backend/app/agents/google_workspace_api.py does not exist")

    return result
