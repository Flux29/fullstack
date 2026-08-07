"""Tests for lazy Google Workspace tool exposure.

The evals in ``evals/google_workspace_tools.py`` cover routing decisions. These
cover the wiring those decisions depend on: that gating actually removes schemas
from what the model is offered, that the loader puts them back within the same
run, and that the approval resume narrows rather than re-sending the catalog.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents import google_workspace_api as google_api
from app.agents.assistant import Deps, _handle_deferred_tools, _prepare_toolkit_loader
from app.agents.google_loadout import (
    GOOGLE_PRODUCT_ORDER,
    LOADER_TOOL_NAME,
    STICKY_TTL_TURNS,
    GoogleLoadout,
    describe_activation,
    gate_google_toolset,
    resolve_product,
    route_products,
)
from app.agents.tool_schema import (
    compact_json_schema,
    serialized_tool_defs,
    synthetic_run_context,
)


def _toolset(product: str, loadout: GoogleLoadout | None = None) -> Any:
    toolset = google_api.build_google_api_toolset(
        name=product,
        url=google_api.google_api_url(product),
        access_token="AT",
        allowed_tools=None,
        user_id="00000000-0000-0000-0000-000000000001",
    )
    if loadout is None:
        return toolset
    loadout.register(prefix=product, product=product)
    return gate_google_toolset(toolset, product=product)


def _deps(loadout: GoogleLoadout | None) -> Any:
    return SimpleNamespace(google_loadout=loadout)


async def _exposed(toolset: Any, deps: Any) -> set[str]:
    return set(await toolset.get_tools(synthetic_run_context(deps)))


class TestRouter:
    def test_explicit_product_selects_only_that_product(self):
        routing = route_products("Check my Gmail inbox")
        assert routing.products == {"gmail"}
        assert routing.widen is False

    def test_multi_product_request_takes_the_union(self):
        routing = route_products("Copy the spreadsheet numbers into a deck and email it")
        assert {"sheets", "slides", "gmail"} <= routing.products

    def test_ambiguous_term_expands_rather_than_guessing(self):
        # "file" could be any of four products; picking one would strand the turn.
        assert route_products("rename that file").products == {
            "drive",
            "docs",
            "sheets",
            "slides",
        }

    def test_generic_google_mention_asks_to_widen(self):
        routing = route_products("help me sort out my Google account")
        assert routing.widen is True
        assert routing.products == frozenset()

    def test_deictic_action_asks_to_widen(self):
        assert route_products("send that to Nikki").widen is True

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is 17 times 23?",
            "Explain how Python decorators work",
            "Write me a haiku about the sea",
            "Refactor this function to use a generator",
        ],
    )
    def test_ordinary_conversation_selects_nothing(self, prompt):
        routing = route_products(prompt)
        assert routing.products == frozenset()
        assert routing.widen is False

    def test_ambiguous_terms_are_not_treated_as_explicit_mentions(self):
        # "send" spans Gmail and Chat, so it names neither. This is what keeps
        # the approval resume from retaining Chat behind a Gmail send.
        routing = route_products("send the approved email, then note it on my calendar")
        assert "chat" in routing.products
        assert routing.explicit == {"gmail", "calendar"}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("sheets", "sheets"),
            ("Google Sheets", "sheets"),
            ("  SPREADSHEET ", "sheets"),
            ("email", "gmail"),
            ("presentations", "slides"),
            ("people", "contacts"),
            ("nonsense", None),
        ],
    )
    def test_loader_accepts_every_spelling_the_router_understands(self, text, expected):
        """The loader's aliases are derived from the router's own tables, so the
        two cannot drift into disagreeing about what a product is called."""
        assert resolve_product(text) == expected


class TestStickyLoadout:
    def test_followup_inherits_the_product_it_just_used(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("Draft an email about the outage")
        loadout.register(prefix="gmail", product="gmail")
        loadout.end_turn(["gmail_create_draft"])

        loadout.begin_turn("Send that to Nikki")
        loadout.register(prefix="gmail", product="gmail")
        assert "gmail" in loadout.active_products()

    def test_unused_product_ages_out(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("List my Drive folders")
        loadout.register(prefix="drive", product="drive")
        loadout.end_turn(["drive_list_recent_files"])

        for _ in range(STICKY_TTL_TURNS):
            loadout.begin_turn("thanks")
            loadout.register(prefix="drive", product="drive")
            loadout.end_turn([])

        assert loadout.active_products() == ()

    def test_selected_but_unused_products_do_not_become_sticky(self):
        """Otherwise one over-wide routing decision pins itself in forever."""
        loadout = GoogleLoadout()
        loadout.begin_turn("help me sort out my Google account")
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)
        assert len(loadout.active_products()) == len(GOOGLE_PRODUCT_ORDER)
        loadout.end_turn(["sheets_get_values"])

        loadout.begin_turn("and what about the totals?")
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)
        assert loadout.active_products() == ("sheets",)

    def test_inheritance_is_capped(self):
        loadout = GoogleLoadout()
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.begin_turn(f"work with {product}")
            for name in GOOGLE_PRODUCT_ORDER:
                loadout.register(prefix=name, product=name)
            loadout.end_turn([f"{product}_x"])
        assert len(loadout.sticky) <= 4


class TestGating:
    @pytest.mark.anyio
    async def test_inactive_product_contributes_no_schemas(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("What meetings do I have tomorrow?")
        slides = _toolset("slides", loadout)
        calendar = _toolset("calendar", loadout)

        deps = _deps(loadout)
        assert await _exposed(slides, deps) == set()
        assert "calendar_list_events" in await _exposed(calendar, deps)

    @pytest.mark.anyio
    async def test_no_loadout_means_no_gating(self):
        """Channel traffic and direct callers keep the pre-loadout behaviour."""
        slides = gate_google_toolset(_toolset("slides"), product="slides")
        assert "slides_add_slide" in await _exposed(slides, _deps(None))

    @pytest.mark.anyio
    async def test_loader_activation_exposes_tools_on_the_next_request(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("What meetings do I have tomorrow?")
        slides = _toolset("slides", loadout)
        deps = _deps(loadout)
        assert await _exposed(slides, deps) == set()

        activated = loadout.activate("slides")

        # get_tools runs again before every model request, so the very next one
        # carries the schemas — no new run, no re-attached agent.
        assert "slides_add_slide" in await _exposed(slides, deps)
        assert "slides_add_slide" in dict(activated)

    def test_activation_message_names_the_tools(self):
        """A bare "loaded" leaves the model nothing to call, and it loops."""
        loadout = GoogleLoadout()
        loadout.begin_turn("hello")
        loadout.register(prefix="sheets", product="sheets")
        message = describe_activation("sheets", loadout.activate("sheets"))
        assert "sheets_append_values" in message
        assert "Append rows to an A1 range." in message


class TestLoaderVisibility:
    def _ctx(self, loadout: GoogleLoadout | None) -> Any:
        return SimpleNamespace(deps=Deps(google_loadout=loadout))

    def _tool_def(self) -> Any:
        from pydantic_ai.tools import ToolDefinition

        return ToolDefinition(name=LOADER_TOOL_NAME, description="fallback")

    def test_hidden_without_a_loadout(self):
        assert _prepare_toolkit_loader(self._ctx(None), self._tool_def()) is None

    def test_hidden_when_nothing_is_gated_off(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("check my gmail")
        loadout.register(prefix="gmail", product="gmail")
        assert _prepare_toolkit_loader(self._ctx(loadout), self._tool_def()) is None

    def test_visible_while_a_connected_product_is_gated_off(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("check my gmail")
        loadout.register(prefix="gmail", product="gmail")
        loadout.register(prefix="slides", product="slides")
        assert _prepare_toolkit_loader(self._ctx(loadout), self._tool_def()) is not None

    def test_description_indexes_the_gated_tools_by_name(self):
        """The index is what keeps gating lossless — without it the model cannot
        know a withheld tool exists, and gating becomes a capability cut."""
        loadout = GoogleLoadout()
        loadout.begin_turn("check my gmail")
        loadout.register(prefix="gmail", product="gmail")
        loadout.register(
            prefix="github",
            product="github",
            tools=(("search_code", "Search code."), ("issue_read", "Read an issue.")),
        )
        tool_def = _prepare_toolkit_loader(self._ctx(loadout), self._tool_def())

        assert "github_search_code" in tool_def.description
        assert "github_issue_read" in tool_def.description
        # Names only: a schema would defeat the point of withholding it.
        assert "Search code." not in tool_def.description
        # Gmail is active this turn, so it is not something to load.
        assert "gmail_" not in tool_def.description


class TestApprovalResume:
    def _requests(self, tool_name: str) -> Any:
        from pydantic_ai.messages import ToolCallPart

        return SimpleNamespace(
            approvals=[ToolCallPart(tool_name=tool_name, args={})],
            calls=[],
        )

    def _messages(self, prompt: str, called: list[str]) -> list[Any]:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            ToolCallPart,
            UserPromptPart,
        )

        return [
            ModelRequest(parts=[UserPromptPart(content=prompt)]),
            ModelResponse(parts=[ToolCallPart(tool_name=name, args={}) for name in called]),
        ]

    @pytest.mark.anyio
    async def test_resume_keeps_a_referenced_but_unused_product(self):
        """Calendar is planned, not yet touched — "used" alone would strand it."""
        loadout = GoogleLoadout()
        loadout.begin_turn("Send the approved email, then record it on my calendar")
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)

        deps = Deps(google_loadout=loadout, approve_tools=_accepting_approver())
        ctx = SimpleNamespace(
            deps=deps,
            messages=self._messages(
                "Send the approved email, then record it on my calendar",
                ["gmail_create_draft"],
            ),
        )
        await _handle_deferred_tools(ctx, self._requests("gmail_send_message"))

        active = set(loadout.active_products())
        assert {"gmail", "calendar"} <= active
        assert "slides" not in active
        assert "chat" not in active

    @pytest.mark.anyio
    async def test_resume_drops_products_the_run_never_touched(self):
        loadout = GoogleLoadout()
        loadout.begin_turn("Delete that spreadsheet")
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)

        deps = Deps(google_loadout=loadout, approve_tools=_accepting_approver())
        ctx = SimpleNamespace(
            deps=deps,
            messages=self._messages("Delete that spreadsheet", ["sheets_search_spreadsheets"]),
        )
        await _handle_deferred_tools(ctx, self._requests("sheets_delete_spreadsheet"))

        assert loadout.active_products() == ("sheets",)

    @pytest.mark.anyio
    async def test_loader_stays_available_after_the_resume_narrows(self):
        """The escape hatch has to survive the slimming that motivates it."""
        loadout = GoogleLoadout()
        loadout.begin_turn("Delete that spreadsheet")
        for product in GOOGLE_PRODUCT_ORDER:
            loadout.register(prefix=product, product=product)

        deps = Deps(google_loadout=loadout, approve_tools=_accepting_approver())
        ctx = SimpleNamespace(
            deps=deps,
            messages=self._messages("Delete that spreadsheet", []),
        )
        await _handle_deferred_tools(ctx, self._requests("sheets_delete_spreadsheet"))

        from pydantic_ai.tools import ToolDefinition

        assert _prepare_toolkit_loader(ctx, ToolDefinition(name=LOADER_TOOL_NAME)) is not None
        loadout.activate("gmail")
        assert "gmail" in loadout.active_products()


def _accepting_approver():
    async def approve(_requests: Any) -> None:
        return None

    return approve


class TestSchemaCompaction:
    def test_optional_parameter_boilerplate_is_collapsed(self):
        compacted = compact_json_schema(
            {
                "type": "object",
                "properties": {
                    "bold": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "default": None,
                    }
                },
                "required": [],
            }
        )
        assert compacted["properties"]["bold"] == {"type": ["boolean", "null"]}

    def test_a_parameter_actually_named_title_is_left_alone(self):
        """Several Google tools take a literal ``title`` argument."""
        compacted = compact_json_schema(
            {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            }
        )
        assert compacted["properties"] == {"title": {"type": "string"}}

    def test_constraints_and_descriptions_survive(self):
        compacted = compact_json_schema(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["reader", "writer"],
                        "description": "Permission role.",
                    },
                    "ranges": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["role"],
            }
        )
        assert compacted["additionalProperties"] is False
        assert compacted["properties"]["role"]["enum"] == ["reader", "writer"]
        assert compacted["properties"]["role"]["description"] == "Permission role."
        assert compacted["properties"]["ranges"]["items"] == {"type": "string"}
        assert compacted["required"] == ["role"]

    def test_required_parameter_keeps_its_default(self):
        compacted = compact_json_schema(
            {
                "type": "object",
                "properties": {"mode": {"type": ["string", "null"], "default": None}},
                "required": ["mode"],
            }
        )
        assert compacted["properties"]["mode"]["default"] is None

    def test_composite_branches_are_left_alone(self):
        schema = {
            "anyOf": [
                {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                {"type": "null"},
            ]
        }
        assert compact_json_schema(schema) == schema


class TestWhatTheModelIsActuallyOffered:
    """The end-to-end check: what lands in the provider request, not in a wrapper.

    Everything else here tests a layer. This drives a real agent run against
    TestModel and reads the tool list the model was handed, which is the thing
    the whole change is about.
    """

    async def _offered(self, prompt: str, activate: str | None = None) -> list[str]:
        from unittest.mock import patch

        from pydantic_ai.models.test import TestModel

        from app.agents.assistant import AssistantAgent

        loadout = GoogleLoadout()
        loadout.begin_turn(prompt)
        toolsets = [_toolset(product, loadout) for product in ("gmail", "sheets")]
        model = TestModel(call_tools=[])
        # AssistantAgent builds its OpenRouter provider eagerly, which needs a
        # real OPENROUTER_API_KEY — patch the builder rather than override the
        # model, because override only applies once the agent already exists.
        with patch("app.agents.assistant._build_model", return_value=model):
            agent = AssistantAgent(extra_toolsets=toolsets).agent
            if activate:
                loadout.activate(activate)
            await agent.run(prompt, deps=Deps(google_loadout=loadout))
        return sorted(t.name for t in model.last_model_request_parameters.function_tools)

    @pytest.mark.anyio
    async def test_only_the_routed_product_reaches_the_model(self):
        offered = await self._offered("Check my Gmail inbox")
        assert sum(name.startswith("gmail_") for name in offered) == 10
        assert not any(name.startswith("sheets_") for name in offered)

    @pytest.mark.anyio
    async def test_the_model_is_handed_names_for_every_withheld_tool(self):
        """Closes the loop the rest of this class opens.

        Gating is only lossless if the index reaches the provider request — a
        withheld tool the model is never told about is a removed capability, not
        a deferred one.
        """
        from unittest.mock import patch

        from pydantic_ai.models.test import TestModel

        from app.agents.assistant import AssistantAgent

        loadout = GoogleLoadout()
        loadout.begin_turn("Check my Gmail inbox")
        toolsets = [_toolset(product, loadout) for product in ("gmail", "sheets")]
        loadout.register(
            prefix="github", product="github", tools=(("search_code", "Search code."),)
        )
        model = TestModel(call_tools=[])
        with patch("app.agents.assistant._build_model", return_value=model):
            agent = AssistantAgent(extra_toolsets=toolsets).agent
            await agent.run("Check my Gmail inbox", deps=Deps(google_loadout=loadout))

        loader = next(
            t
            for t in model.last_model_request_parameters.function_tools
            if t.name == LOADER_TOOL_NAME
        )
        # Gmail routed in, so its schemas are present and it is not in the index.
        assert "github_search_code" in loader.description
        assert "sheets_append_values" in loader.description
        assert "gmail_" not in loader.description

    @pytest.mark.anyio
    async def test_loader_is_offered_while_a_product_is_gated_off(self):
        assert LOADER_TOOL_NAME in await self._offered("Check my Gmail inbox")

    @pytest.mark.anyio
    async def test_activation_reaches_the_next_request_and_retires_the_loader(self):
        offered = await self._offered("Check my Gmail inbox", activate="sheets")
        assert sum(name.startswith("sheets_") for name in offered) == 13
        # Nothing left to load, so the loader stops costing schema bytes.
        assert LOADER_TOOL_NAME not in offered


class TestSerializationOrder:
    @pytest.mark.anyio
    async def test_identical_state_serializes_byte_identically(self):
        async def once() -> str:
            loadout = GoogleLoadout()
            loadout.begin_turn("Put the spreadsheet numbers into a deck")
            toolsets = [_toolset(p, loadout) for p in GOOGLE_PRODUCT_ORDER]
            ctx = synthetic_run_context(_deps(loadout))
            defs = []
            for toolset in toolsets:
                defs.extend(t.tool_def for t in (await toolset.get_tools(ctx)).values())
            return serialized_tool_defs(defs)

        assert await once() == await once()
