"""Provider-facing tool schema helpers: synthetic contexts and JSON Schema compaction.

Measurement drove what is compacted here. Across the 103 Google Workspace tools
the serialized catalog is ~34 KB, of which parameter schemas are ~21 KB and
descriptions only ~3.5 KB — so shortening prose would have moved almost nothing,
while the repeated optional-parameter boilerplate is worth real tokens.

Nothing here changes what a schema accepts. Descriptions are never touched: the
pairs that are easy to confuse (search vs list, draft vs send, trash vs
permanent delete, metadata vs contents, update vs replace, spreadsheet range vs
sheet structure) are told apart by exactly those words. ``title`` is not stripped
either — pydantic-ai's ``GenerateToolJsonSchema`` already pops it from every
property before these schemas exist, so a stripper here would be dead code that
also had to recurse into ``default`` and ``enum`` values to find nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.tools import ToolDefinition

_COMPOSITE_KEYS = frozenset({"anyOf", "oneOf", "allOf", "$ref", "not"})


def synthetic_run_context(deps: Any = None) -> RunContext[Any]:
    """A RunContext detached from a real run, for schema inspection and tests.

    ``get_tools`` needs a context to resolve tool definitions, but the Google
    toolsets never read the model or usage off it. Building one here keeps
    measurement scripts and eval cases from standing up a whole agent run.
    """
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def function_tools(toolset: Any) -> dict[str, Any]:
    """The registered tools of the ``FunctionToolset`` at the base of a wrapper chain.

    A built Google toolset is wrapped for prefixing and loadout gating, so the
    depth is not fixed. ``apply`` is pydantic-ai's own leaf visitor, which beats
    counting ``.wrapped`` hops against a chain we do not control.
    """
    leaves: list[Any] = []
    toolset.apply(leaves.append)
    return leaves[0].tools


def _collapse_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Fold ``X | None`` from a two-branch ``anyOf`` into a type union.

    Pydantic renders every optional parameter as
    ``{"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": null}``.
    The equivalent ``{"type": ["boolean", "null"]}`` is the same JSON Schema and
    about 40% of the bytes. Only the two-branch null case is folded, and only
    when the non-null branch carries a plain string ``type`` — anything with its
    own composition keywords is left exactly as it was.
    """
    branches = schema.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2:
        return schema
    if not all(isinstance(branch, dict) for branch in branches):
        return schema
    nulls = [b for b in branches if b.get("type") == "null" and len(b) == 1]
    others = [b for b in branches if b.get("type") != "null" or len(b) != 1]
    if len(nulls) != 1 or len(others) != 1:
        return schema
    other = others[0]
    if not isinstance(other.get("type"), str) or _COMPOSITE_KEYS & other.keys():
        return schema
    rest = {key: value for key, value in schema.items() if key != "anyOf"}
    return {**other, **rest, "type": [other["type"], "null"]}


def compact_json_schema(schema: Any, *, is_required: bool = True) -> Any:
    """Strip generator boilerplate from a JSON Schema without changing what it accepts.

    Every constraint survives: ``type``, ``enum``, ``items``, ``required``,
    ``additionalProperties`` and all descriptions are preserved. What goes is
    ``default: null`` on optional parameters, where "may be omitted" is already
    carried by the absence from ``required`` and by ``null`` remaining in the
    type union.

    ``is_required`` says whether the schema being compacted is a property its
    parent lists as required; a required property keeps its default, since
    dropping one there would change what omission means.
    """
    if isinstance(schema, list):
        return [compact_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    working = _collapse_nullable(schema)
    own_required = working.get("required")
    child_required = frozenset(own_required) if isinstance(own_required, list) else frozenset()

    result: dict[str, Any] = {}
    for key, value in working.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                name: compact_json_schema(prop, is_required=name in child_required)
                for name, prop in value.items()
            }
            continue
        result[key] = compact_json_schema(value)

    # Only drop the null default where omitting the property is already legal.
    if not is_required and "default" in result and result["default"] is None:
        result.pop("default")
    return result


def compact_toolset_schemas(toolset: Any) -> None:
    """Compact every registered tool's parameter schema, once, in place.

    Done at build time rather than through a ``.prepared()`` wrapper because the
    schemas are constant for the toolset's life: a prepare function would re-run
    the whole recursive rewrite before every model request (~1.1ms per request
    across the Google catalog) to produce the same bytes each time.

    ``Tool.tool_def`` is a property that rebuilds from ``function_schema.json_schema``
    on every access, so rewriting that once propagates to every later request.
    """
    for tool in function_tools(toolset).values():
        schema = tool.function_schema.json_schema
        tool.function_schema.json_schema = compact_json_schema(schema)


def provider_payload(tool_def: ToolDefinition) -> dict[str, Any]:
    """The exact JSON an OpenAI-compatible provider receives for one tool.

    Mirrors ``OpenAIChatModel._map_tool_definition``, so anything measured or
    compared here is what the provider actually bills for.
    """
    return {
        "type": "function",
        "function": {
            "name": tool_def.name,
            "description": tool_def.description or "",
            "parameters": tool_def.parameters_json_schema,
        },
    }


def serialized_tool_defs(tool_defs: list[ToolDefinition]) -> str:
    """The provider payload for *tool_defs*, matching what OpenRouter receives.

    Used by the determinism eval (identical state must produce identical bytes)
    and by the measurement script, so both judge the same bytes.
    """
    return json.dumps(
        [provider_payload(tool_def) for tool_def in tool_defs],
        ensure_ascii=False,
        separators=(",", ":"),
    )
