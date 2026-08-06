"""Measure the provider-facing size of the Google Workspace tool catalog.

Serializes every Google toolset exactly as ``OpenAIChatModel._map_tool_definition``
does before a request, so the numbers here are the ones the provider bills for.
Run it before and after a schema change:

    uv run --directory backend python scripts/measure_google_tool_schemas.py
    uv run --directory backend python scripts/measure_google_tool_schemas.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.google_loadout import GOOGLE_PRODUCT_ORDER
from app.agents.google_workspace_api import build_google_api_toolset, google_api_url
from app.agents.tool_schema import provider_payload, synthetic_run_context

PRODUCT_URLS: dict[str, str] = {kind: google_api_url(kind) for kind in GOOGLE_PRODUCT_ORDER}


async def measure() -> dict[str, Any]:
    ctx = synthetic_run_context()
    products: list[dict[str, Any]] = []
    total_bytes = 0
    total_tools = 0
    description_bytes = 0
    parameter_bytes = 0

    for kind, url in PRODUCT_URLS.items():
        toolset = build_google_api_toolset(
            name=kind,
            url=url,
            access_token="measurement-token",
            allowed_tools=None,
            user_id="00000000-0000-0000-0000-000000000001",
        )
        tools = await toolset.get_tools(ctx)
        payloads = [provider_payload(tool.tool_def) for tool in tools.values()]
        serialized = json.dumps(payloads, ensure_ascii=False, separators=(",", ":"))
        desc = sum(len(p["function"]["description"]) for p in payloads)
        params = sum(
            len(json.dumps(p["function"]["parameters"], ensure_ascii=False, separators=(",", ":")))
            for p in payloads
        )
        products.append(
            {
                "product": kind,
                "tools": len(payloads),
                "bytes": len(serialized),
                "description_bytes": desc,
                "parameter_bytes": params,
                "largest_tools": sorted(
                    (
                        {
                            "name": p["function"]["name"],
                            "bytes": len(json.dumps(p, ensure_ascii=False, separators=(",", ":"))),
                        }
                        for p in payloads
                    ),
                    key=lambda item: item["bytes"],
                    reverse=True,
                )[:3],
            }
        )
        total_bytes += len(serialized)
        total_tools += len(payloads)
        description_bytes += desc
        parameter_bytes += params

    return {
        "products": products,
        "total_tools": total_tools,
        "total_bytes": total_bytes,
        "description_bytes": description_bytes,
        "parameter_bytes": parameter_bytes,
        # ~4 chars/token is the standard rough conversion for JSON payloads.
        "approx_tokens": round(total_bytes / 4),
    }


# Representative turns, weighted the way a real chat session runs: most turns
# touch nothing, some touch one product, a few span several.
SCENARIOS: tuple[tuple[str, str], ...] = (
    ("no Google intent", "What is 17 times 23?"),
    ("no Google intent", "Explain how Python decorators work"),
    ("no Google intent", "Summarise the tradeoffs of optimistic locking"),
    ("single product", "Check my Gmail inbox for anything unread"),
    ("single product", "What meetings do I have tomorrow?"),
    ("single product", "Add a row to the budget spreadsheet"),
    ("two products", "Find the onboarding doc in my Drive folder"),
    ("multi product", "Pull the Q3 numbers into a deck and email the team"),
    ("ambiguous, widened", "Help me tidy up my Google account"),
)


async def measure_turns() -> list[dict[str, Any]]:
    """Serialized catalog size for each representative turn, after routing."""
    from app.agents.google_loadout import GOOGLE_PRODUCT_ORDER, GoogleLoadout, gate_google_toolset
    from app.agents.tool_schema import serialized_tool_defs

    rows: list[dict[str, Any]] = []
    for label, prompt in SCENARIOS:
        loadout = GoogleLoadout()
        loadout.begin_turn(prompt)
        toolsets = []
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)
            toolsets.append(
                gate_google_toolset(
                    build_google_api_toolset(
                        name=product,
                        url=PRODUCT_URLS[product],
                        access_token="measurement-token",
                        allowed_tools=None,
                        user_id="00000000-0000-0000-0000-000000000001",
                    ),
                    product=product,
                )
            )

        ctx = synthetic_run_context(SimpleNamespace(google_loadout=loadout))
        tool_defs: list[Any] = []
        for toolset in toolsets:
            tool_defs.extend(tool.tool_def for tool in (await toolset.get_tools(ctx)).values())
        serialized = serialized_tool_defs(tool_defs)
        rows.append(
            {
                "label": label,
                "prompt": prompt,
                "products": list(loadout.active_products()),
                "tools": len(tool_defs),
                "bytes": len(serialized) if tool_defs else 0,
            }
        )
    return rows


def cache_status() -> dict[str, Any]:
    """Whether anything on this OpenRouter path can earn a prompt-cache credit.

    Worth checking before celebrating a size reduction: a stable large tool block
    that gets cached can beat a smaller one that churns. Anthropic caching is
    *explicit* — it only happens where a ``cache_control`` breakpoint is sent —
    so for an Anthropic model the answer is decided entirely by whether these
    settings are present, and needs no live traffic to determine.
    """
    from app.agents.assistant import AssistantAgent
    from app.core.config import settings

    agent = AssistantAgent()
    model_settings = agent.agent.model_settings or {}
    breakpoints = [
        key
        for key in (
            "openrouter_cache_instructions",
            "openrouter_cache_messages",
            "openrouter_cache_tool_definitions",
        )
        if model_settings.get(key)
    ]
    model = settings.AI_MODEL
    explicit_only = model.startswith(("anthropic/", "google/"))
    return {
        "model": model,
        "cache_breakpoints_sent": breakpoints,
        "requires_explicit_breakpoints": explicit_only,
        "caching_active": bool(breakpoints) or not explicit_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    report = asyncio.run(measure())
    turns = asyncio.run(measure_turns())
    report["turns"] = turns
    report["cache"] = cache_status()
    baseline = report["total_bytes"]
    report["mean_turn_bytes"] = round(sum(row["bytes"] for row in turns) / len(turns))
    report["mean_reduction_pct"] = round(100 * (1 - report["mean_turn_bytes"] / baseline), 1)
    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"{'product':<10} {'tools':>6} {'bytes':>8} {'desc':>8} {'params':>8}")
    print("-" * 44)
    for entry in report["products"]:
        print(
            f"{entry['product']:<10} {entry['tools']:>6} {entry['bytes']:>8} "
            f"{entry['description_bytes']:>8} {entry['parameter_bytes']:>8}"
        )
    print("-" * 44)
    print(
        f"{'TOTAL':<10} {report['total_tools']:>6} {report['total_bytes']:>8} "
        f"{report['description_bytes']:>8} {report['parameter_bytes']:>8}"
    )
    print(f"\napprox tokens: {report['approx_tokens']}")
    print("\nlargest tools per product:")
    for entry in report["products"]:
        biggest = ", ".join(f"{t['name']} ({t['bytes']}B)" for t in entry["largest_tools"])
        print(f"  {entry['product']:<10} {biggest}")

    print("\nper-turn exposure after routing (all 8 products connected):")
    print(f"  {'scenario':<20} {'tools':>6} {'bytes':>8} {'vs full':>8}  prompt")
    for row in report["turns"]:
        share = 100 * row["bytes"] / report["total_bytes"]
        print(
            f"  {row['label']:<20} {row['tools']:>6} {row['bytes']:>8} "
            f"{share:>7.1f}%  {row['prompt'][:44]}"
        )
    print(
        f"\n  mean turn: {report['mean_turn_bytes']} bytes "
        f"({report['mean_reduction_pct']}% below the full catalog of "
        f"{report['total_bytes']})"
    )

    cache = report["cache"]
    print("\nprompt caching on this path:")
    print(f"  model:                {cache['model']}")
    print(f"  explicit breakpoints: {cache['cache_breakpoints_sent'] or 'none sent'}")
    print(f"  caching active:       {cache['caching_active']}")
    if not cache["caching_active"]:
        print(
            "  -> tool definitions are re-billed in full on every request, so raw\n"
            "     reduction is what pays here. Canonical ordering is what would let\n"
            "     caching work if openrouter_cache_tool_definitions is enabled later."
        )


if __name__ == "__main__":
    main()
