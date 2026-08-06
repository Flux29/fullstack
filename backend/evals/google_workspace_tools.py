"""Deterministic Pydantic Evals for Google Workspace tool routing and approval policy.

Two datasets:

``GOOGLE_WORKSPACE_ROUTING``
    Approval policy — the tool a prompt intends, and whether calling it pauses
    for the user.

``GOOGLE_WORKSPACE_LOADOUT``
    Per-turn tool exposure. Every case asserts the selected loadout is a
    **superset** of the tools the scenario needs, not that the model picked an
    expected first tool. A loadout that happens to include the right first tool
    but is missing the second one still fails the turn, so superset coverage is
    what actually has to hold.
"""

from types import SimpleNamespace
from typing import Any, TypedDict

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected, Evaluator, EvaluatorContext

from app.agents.google_loadout import (
    GOOGLE_PRODUCT_ORDER,
    GoogleLoadout,
    product_tools,
)
from app.agents.google_workspace_api import build_google_api_toolset, google_api_url
from app.agents.tool_schema import function_tools

# --------------------------------------------------------------------------- #
# Approval policy
# --------------------------------------------------------------------------- #


class RoutingInput(TypedDict):
    integration: str
    prompt: str


class RoutingOutput(TypedDict):
    tool: str
    requires_approval: bool


_ROUTES = {
    "calendar": ("list_events", ("calendar", "events")),
    "drive": ("upload_file", ("upload", "drive")),
    "docs": ("append_text", ("append", "document")),
    "sheets": ("append_values", ("append", "row")),
    "slides": ("create_image", ("image", "slide")),
    "chat": ("create_message", ("send", "chat")),
    "contacts": ("update_contact", ("update", "contact")),
}


def evaluate_route(inputs: RoutingInput) -> RoutingOutput:
    """Select the intended tool and verify its real registered approval metadata."""
    integration = inputs["integration"]
    lowered = inputs["prompt"].casefold()
    if integration == "gmail":
        if "send" in lowered and "draft" in lowered:
            tool_name, keywords = "send_draft", ("send", "draft")
        elif "send" in lowered and ("message" in lowered or "email" in lowered):
            tool_name, keywords = "send_message", ("send",)
        else:
            tool_name, keywords = "create_draft", ("draft", "compose")
    else:
        tool_name, keywords = _ROUTES[integration]
    if not all(keyword in lowered for keyword in keywords):
        raise ValueError(f"Prompt does not express the {integration} evaluation intent")
    toolset = build_google_api_toolset(
        name=integration,
        url=google_api_url(integration),
        access_token="eval-token",
        allowed_tools=[tool_name],
        user_id="00000000-0000-0000-0000-000000000001",
    )
    tool = function_tools(toolset)[tool_name]
    return {"tool": tool_name, "requires_approval": tool.requires_approval}


GOOGLE_WORKSPACE_ROUTING = Dataset[RoutingInput, RoutingOutput, None](
    name="google-workspace-tool-routing",
    evaluators=[EqualsExpected()],
    cases=[
        Case(
            name="gmail-draft-pauses",
            inputs={"integration": "gmail", "prompt": "Compose a draft reply in Gmail"},
            expected_output={"tool": "create_draft", "requires_approval": True},
        ),
        Case(
            name="gmail-send-draft-pauses",
            inputs={"integration": "gmail", "prompt": "Send the approved Gmail draft"},
            expected_output={"tool": "send_draft", "requires_approval": True},
        ),
        Case(
            name="gmail-send-message-pauses",
            inputs={"integration": "gmail", "prompt": "Send this email message now"},
            expected_output={"tool": "send_message", "requires_approval": True},
        ),
        Case(
            name="calendar-read-is-immediate",
            inputs={"integration": "calendar", "prompt": "List my calendar events"},
            expected_output={"tool": "list_events", "requires_approval": False},
        ),
        Case(
            name="drive-upload-pauses",
            inputs={"integration": "drive", "prompt": "Upload this file to Drive"},
            expected_output={"tool": "upload_file", "requires_approval": True},
        ),
        Case(
            name="docs-edit-pauses",
            inputs={"integration": "docs", "prompt": "Append this paragraph to the document"},
            expected_output={"tool": "append_text", "requires_approval": True},
        ),
        Case(
            name="sheets-edit-pauses",
            inputs={"integration": "sheets", "prompt": "Append a row to the sheet"},
            expected_output={"tool": "append_values", "requires_approval": True},
        ),
        Case(
            name="slides-edit-pauses",
            inputs={"integration": "slides", "prompt": "Add an image to the slide"},
            expected_output={"tool": "create_image", "requires_approval": True},
        ),
        Case(
            name="chat-send-pauses",
            inputs={"integration": "chat", "prompt": "Send this message in Chat"},
            expected_output={"tool": "create_message", "requires_approval": True},
        ),
        Case(
            name="contacts-edit-pauses",
            inputs={"integration": "contacts", "prompt": "Update this contact"},
            expected_output={"tool": "update_contact", "requires_approval": True},
        ),
    ],
)


# --------------------------------------------------------------------------- #
# Per-turn loadout
# --------------------------------------------------------------------------- #

#: Every product connected, which is the case the whole feature exists for.
ALL_CONNECTED: tuple[str, ...] = GOOGLE_PRODUCT_ORDER


class Turn(TypedDict, total=False):
    prompt: str
    """What the user says on this turn."""

    used: list[str]
    """Unprefixed tool names the turn actually called, e.g. ``["docs:create_document"]``
    written as ``product:tool``. Feeds the sticky loadout for later turns."""

    approval_pending: list[str]
    """``product:tool`` entries awaiting approval, which triggers resume slimming."""


class LoadoutInput(TypedDict, total=False):
    turns: list[Turn]
    connected: list[str]


class LoadoutOutput(TypedDict):
    tools: list[str]
    """Prefixed tool names exposed on the final turn, in serialization order."""

    products: list[str]


def _prefixed(entry: str) -> str:
    product, tool = entry.split(":", 1)
    return f"{product}_{tool}"


def replay(inputs: LoadoutInput) -> GoogleLoadout:
    """Drive a real GoogleLoadout through a scripted conversation.

    Connections are named after their product, so the tool prefix equals the
    product name and expectations stay readable. The final turn is left open —
    ``end_turn`` is not called for it — so callers observe the live selection.
    """
    connected = list(inputs.get("connected") or ALL_CONNECTED)
    loadout = GoogleLoadout()
    turns = inputs["turns"]
    for index, turn in enumerate(turns):
        loadout.begin_turn(turn.get("prompt", ""))
        for product in connected:
            loadout.register(prefix=product, product=product)
        pending = [_prefixed(entry) for entry in turn.get("approval_pending", [])]
        if pending:
            loadout.restrict_for_resume(
                pending_tool_names=pending,
                messages=_synthetic_messages(turns[: index + 1]),
            )
        if index < len(turns) - 1:
            loadout.end_turn([_prefixed(entry) for entry in turn.get("used", [])])
    return loadout


def evaluate_loadout(inputs: LoadoutInput) -> LoadoutOutput:
    """Report which tools the final turn of a scripted conversation exposes."""
    loadout = replay(inputs)
    products = list(loadout.active_products())
    tools = [f"{product}_{tool}" for product in products for tool, _ in product_tools(product)]
    return {"tools": tools, "products": products}


def _synthetic_messages(turns: list[Turn]) -> list[Any]:
    """Rebuild the message history a real run would have at approval time."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        UserPromptPart,
    )

    messages: list[Any] = []
    for turn in turns:
        messages.append(ModelRequest(parts=[UserPromptPart(content=turn.get("prompt", ""))]))
        calls = [
            ToolCallPart(tool_name=_prefixed(entry), args={}) for entry in turn.get("used", [])
        ]
        if calls:
            messages.append(ModelResponse(parts=calls))
    return messages


class LoadoutMatches(Evaluator[LoadoutInput, LoadoutOutput, Any]):
    """Check a case's ``requires`` / ``excludes`` / ``products`` expectations.

    ``requires`` is a SUPERSET assertion, not equality: an exact set would be
    brittle against a deliberately conservative router, and asserting only the
    expected first tool would pass loadouts that strand the turn on its second
    call. Superset is what decides whether the turn can actually complete.

    ``excludes`` is what stops that being vacuous — without a negative
    assertion, a router that returned the entire catalog would pass every
    superset case, which is precisely the behaviour being replaced.

    Emitting one assertion per expectation (rather than one class each) keeps a
    failure legible: the report names which expectation broke.
    """

    def evaluate(self, ctx: EvaluatorContext[LoadoutInput, LoadoutOutput, Any]) -> dict[str, bool]:
        expectations = ctx.metadata or {}
        exposed = set(ctx.output["tools"])
        checks: dict[str, bool] = {}
        if "requires" in expectations:
            checks["covers_required"] = set(expectations["requires"]) <= exposed
        if "excludes" in expectations:
            checks["omits_excluded"] = not set(expectations["excludes"]) & exposed
        if "products" in expectations:
            checks["products"] = ctx.output["products"] == expectations["products"]
        return checks


GOOGLE_WORKSPACE_LOADOUT = Dataset[LoadoutInput, LoadoutOutput, Any](
    name="google-workspace-loadout",
    evaluators=[LoadoutMatches()],
    cases=[
        # -- single product ------------------------------------------------- #
        Case(
            name="single-product-gmail",
            inputs={"turns": [{"prompt": "Check my Gmail inbox for anything unread"}]},
            metadata={
                "requires": ["gmail_search_threads", "gmail_get_thread"],
                "excludes": ["slides_add_slide", "sheets_update_values"],
            },
        ),
        Case(
            name="single-product-calendar",
            inputs={"turns": [{"prompt": "What meetings do I have tomorrow?"}]},
            metadata={
                "requires": ["calendar_list_events", "calendar_list_calendars"],
                "excludes": ["drive_delete_file", "chat_create_message"],
            },
        ),
        Case(
            name="single-product-sheets",
            inputs={"turns": [{"prompt": "Add a row to the budget spreadsheet"}]},
            metadata={
                "requires": ["sheets_append_values", "sheets_get_values"],
                "excludes": ["slides_create_image"],
            },
        ),
        # -- multi-product workflows ---------------------------------------- #
        Case(
            name="multi-product-union",
            inputs={
                "turns": [
                    {
                        "prompt": "Pull the numbers from the Q3 spreadsheet, "
                        "put them in a deck, and email it to the team"
                    }
                ]
            },
            metadata={
                "requires": [
                    "sheets_get_values",
                    "slides_create_presentation",
                    "gmail_send_message",
                ],
            },
        ),
        Case(
            name="multi-product-drive-and-docs",
            inputs={"turns": [{"prompt": "Find the onboarding doc in my Drive folder"}]},
            metadata={"requires": ["drive_search_files", "docs_read_doc"]},
        ),
        # -- implicit requests ---------------------------------------------- #
        Case(
            name="implicit-put-this-in-the-spreadsheet",
            inputs={
                "turns": [
                    {"prompt": "Here are last month's totals"},
                    {"prompt": "Put this in the spreadsheet"},
                ]
            },
            metadata={"requires": ["sheets_append_values", "sheets_update_values"]},
        ),
        Case(
            name="implicit-send-that-inherits-sticky-gmail",
            inputs={
                "turns": [
                    {
                        "prompt": "Draft an email summarising the outage",
                        "used": ["gmail:create_draft"],
                    },
                    {"prompt": "Send that to Nikki"},
                ]
            },
            metadata={"requires": ["gmail_send_draft", "gmail_send_message"]},
        ),
        # -- follow-ups relying on conversation history --------------------- #
        Case(
            name="followup-inherits-used-product",
            inputs={
                "turns": [
                    {
                        "prompt": "Create a doc with the retro notes",
                        "used": ["docs:create_document"],
                    },
                    {"prompt": "Now add a heading at the top"},
                ]
            },
            metadata={"requires": ["docs_insert_text", "docs_format_paragraph"]},
        ),
        Case(
            name="followup-evicts-after-ttl",
            inputs={
                "turns": [
                    {"prompt": "List my Drive folders", "used": ["drive:list_recent_files"]},
                    {"prompt": "Thanks"},
                    {"prompt": "What is the capital of France?"},
                    {"prompt": "And what is its population?"},
                ]
            },
            # Drive aged out: inheritance must not accumulate indefinitely, or the
            # loadout creeps back to the full catalog one idle turn at a time.
            metadata={"excludes": ["drive_search_files", "drive_delete_file"]},
        ),
        # -- ambiguity expands ---------------------------------------------- #
        Case(
            name="ambiguous-google-mention-expands",
            inputs={"turns": [{"prompt": "Help me tidy up my Google account"}]},
            metadata={
                "requires": [
                    "gmail_search_threads",
                    "drive_search_files",
                    "calendar_list_events",
                    "contacts_list_contacts",
                ]
            },
        ),
        Case(
            name="ambiguous-file-word-covers-every-file-product",
            inputs={"turns": [{"prompt": "Rename that file for me"}]},
            metadata={
                "requires": [
                    "drive_update_file",
                    "docs_search_docs",
                    "sheets_search_spreadsheets",
                    "slides_search_presentations",
                ]
            },
        ),
        # -- approval, then a different product ----------------------------- #
        Case(
            name="approval-resume-keeps-planned-other-product",
            inputs={
                "turns": [
                    {
                        "prompt": "Send the approved email to the vendor, "
                        "then record it on my calendar",
                        "used": ["gmail:create_draft"],
                        "approval_pending": ["gmail:send_message"],
                    }
                ]
            },
            # Calendar is referenced but not yet used at approval time — a
            # retention set built from "used" alone would strand the second step.
            metadata={
                "requires": ["gmail_send_message", "calendar_list_events"],
                "excludes": ["slides_add_slide", "chat_create_message"],
            },
        ),
        Case(
            name="approval-resume-drops-unrelated-products",
            inputs={
                "turns": [
                    {
                        "prompt": "Delete that spreadsheet",
                        "used": ["sheets:search_spreadsheets"],
                        "approval_pending": ["sheets:delete_spreadsheet"],
                    }
                ]
            },
            metadata={
                "requires": ["sheets_delete_spreadsheet"],
                "excludes": ["gmail_send_message", "chat_create_message", "contacts_get_contact"],
            },
        ),
        # -- similarly named tools across products -------------------------- #
        Case(
            name="similar-names-docs-not-slides",
            inputs={"turns": [{"prompt": "Replace the placeholder text in the document"}]},
            # docs_replace_text and slides_replace_text differ only by prefix;
            # the loadout must carry the one the prompt named.
            metadata={
                "requires": ["docs_replace_text", "docs_insert_text"],
                "excludes": ["slides_replace_text", "slides_insert_text"],
            },
        ),
        Case(
            name="similar-names-slides-not-docs",
            inputs={"turns": [{"prompt": "Replace the placeholder text on every slide"}]},
            metadata={
                "requires": ["slides_replace_text", "slides_insert_text"],
                "excludes": ["docs_replace_text"],
            },
        ),
    ],
)


#: Turns with no Google intent at all must attach nothing. Kept as its own
#: dataset because "the catalog is empty" is a different claim from "the catalog
#: covers what this scenario needs", and a case landing in the wrong dataset by
#: accident should not be able to pass vacuously.
GOOGLE_WORKSPACE_NEGATIVE = Dataset[LoadoutInput, LoadoutOutput, Any](
    name="google-workspace-loadout-negative",
    evaluators=[LoadoutMatches()],
    cases=[
        Case(
            name=name,
            inputs={"turns": [{"prompt": prompt}]},
            metadata={"products": []},
        )
        for name, prompt in (
            ("negative-arithmetic", "What is 17 times 23?"),
            ("negative-decorators", "Explain how Python decorators work"),
            ("negative-haiku", "Write me a haiku about the sea"),
            ("negative-refactor-request", "Refactor this function to use a generator"),
            ("negative-history-question", "Who was the first person to reach the South Pole?"),
        )
    ],
)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class DeterminismOutput(TypedDict):
    identical: bool


async def evaluate_determinism(inputs: LoadoutInput) -> DeterminismOutput:
    """Serialize the same conversation state twice and compare the bytes.

    Provider prompt caching keys on a stable prefix. A loadout that selects the
    right products but emits them in a different order each turn defeats caching
    just as thoroughly as a loadout that keeps changing size, so byte-identity —
    not set-equality — is the property under test.
    """

    first = await _serialize_loadout(inputs)
    second = await _serialize_loadout(inputs)
    return {"identical": first == second}


async def _serialize_loadout(inputs: LoadoutInput) -> str:
    """The provider payload a scripted conversation's final turn would produce."""
    from app.agents.google_loadout import gate_google_toolset
    from app.agents.tool_schema import serialized_tool_defs, synthetic_run_context

    loadout = replay(inputs)
    # Sorted here the way build_toolsets_for_user sorts, so declaring connections
    # in a different order cannot change the bytes.
    ordered = sorted(inputs.get("connected") or ALL_CONNECTED, key=GOOGLE_PRODUCT_ORDER.index)
    ctx = synthetic_run_context(SimpleNamespace(google_loadout=loadout))
    tool_defs: list[Any] = []
    for product in ordered:
        toolset = gate_google_toolset(
            build_google_api_toolset(
                name=product,
                url=google_api_url(product),
                access_token="eval-token",
                allowed_tools=None,
                user_id="00000000-0000-0000-0000-000000000001",
            ),
            product=product,
        )
        tool_defs.extend(tool.tool_def for tool in (await toolset.get_tools(ctx)).values())
    return serialized_tool_defs(tool_defs)


class SerializationIsByteIdentical(Evaluator[LoadoutInput, DeterminismOutput, Any]):
    def evaluate(self, ctx: EvaluatorContext[LoadoutInput, DeterminismOutput, Any]) -> bool:
        return ctx.output["identical"]


GOOGLE_WORKSPACE_DETERMINISM = Dataset[LoadoutInput, DeterminismOutput, Any](
    name="google-workspace-loadout-determinism",
    evaluators=[SerializationIsByteIdentical()],
    cases=[
        Case(
            name="determinism-multi-product",
            inputs={
                "turns": [
                    {"prompt": "Pull the Q3 numbers into a deck and email the team"},
                ]
            },
        ),
        Case(
            name="determinism-after-sticky-history",
            inputs={
                "turns": [
                    {"prompt": "Create a doc for the retro", "used": ["docs:create_document"]},
                    {"prompt": "Now share it with the team", "used": ["drive:create_permission"]},
                    {"prompt": "Add the action items"},
                ]
            },
        ),
        Case(
            name="determinism-connected-order-does-not-leak",
            # Connections declared in reverse order must still serialize in the
            # canonical product order — otherwise the database row order would
            # decide the cache prefix.
            inputs={
                "turns": [{"prompt": "Help me tidy up my Google account"}],
                "connected": list(reversed(ALL_CONNECTED)),
            },
        ),
    ],
)
