"""Tests for the per-turn coding toolkit (ADR-006).

The invariants under test are the ones the ADR names: tools execute only
through a sandbox backend, a readonly workspace registers nothing that
mutates, write and execute are deferred approvals, and auto-approval covers
this workspace's coding tools and nothing else.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved
from pydantic_ai_backends import StateBackend

from app.agents.tools.coding import (
    CODING_WRITE_TOOLS,
    MAX_CONVENTION_CHARS,
    CodingToolkit,
    WorkspacePolicy,
    _front_matter_field,
    read_repository_briefing,
)
from app.core.config import settings
from app.core.exceptions import ValidationError

pytestmark = pytest.mark.anyio


def _workspace(**overrides) -> WorkspacePolicy:
    defaults: dict = {
        "name": "app",
        "backend_kind": "remote",
        "root": "ws-abc123def456",
        "ruleset": "default",
        "auto_approve": False,
    }
    defaults.update(overrides)
    return WorkspacePolicy(**defaults)


def _tool_names(toolset) -> set[str]:
    registered = getattr(toolset, "tools", None) or getattr(toolset, "_tools", None)
    assert isinstance(registered, dict), "console toolset exposes its tools as a mapping"
    return set(registered)


# --- what gets registered ---------------------------------------------------


def test_default_workspace_registers_read_and_write_tools():
    tools = CodingToolkit(_workspace()).build()
    names = _tool_names(tools.toolset)
    assert {"ls", "read_file", "glob", "grep"} <= names
    assert {"write_file", "edit_file", "execute"} <= names


def test_readonly_workspace_registers_no_write_or_execute_tool():
    """ADR-006 §4: there is nothing to approve on a readonly workspace."""
    tools = CodingToolkit(_workspace(ruleset="readonly")).build()
    names = _tool_names(tools.toolset)
    assert {"ls", "read_file", "glob", "grep"} <= names
    assert not (names & CODING_WRITE_TOOLS)


def test_readonly_workspace_never_auto_approves():
    tools = CodingToolkit(_workspace(ruleset="readonly", auto_approve=True)).build()
    assert tools.auto_approved_tools == frozenset()


def test_auto_approving_workspace_covers_only_its_own_write_tools():
    tools = CodingToolkit(_workspace(auto_approve=True)).build()
    assert tools.auto_approved_tools == CODING_WRITE_TOOLS
    assert "rag_search" not in tools.auto_approved_tools
    assert "read_file" not in tools.auto_approved_tools


def test_strict_workspace_never_auto_approves_even_if_the_row_says_so():
    """ADR-006 §4. The service refuses this combination on create and update;
    the coding surface refuses it again for a row that arrived another way."""
    tools = CodingToolkit(_workspace(ruleset="strict", auto_approve=True)).build()
    assert tools.auto_approved_tools == frozenset()


def test_background_shell_tools_are_not_registered():
    """They are unimplemented on both sandbox kinds ADR-006 permits, so they
    would prompt for approval and then always fail — and they bypass the guard."""
    names = _tool_names(CodingToolkit(_workspace()).build().toolset)
    assert not (names & {"run_in_background", "read_output", "kill_shell", "list_shells"})


def test_workspace_without_auto_approve_resolves_nothing_silently():
    assert CodingToolkit(_workspace()).build().auto_approved_tools == frozenset()


# --- where code runs --------------------------------------------------------


def test_remote_workspace_talks_to_the_sandbox_service_not_the_filesystem():
    """ADR-006 §1: never a LocalBackend rooted in the API container."""
    tools = CodingToolkit(_workspace(backend_kind="remote")).build()
    assert type(tools.backend).__name__ == "RemoteSandbox"
    assert not hasattr(tools.backend, "root_dir")


def test_docker_workspace_is_refused_unless_the_deployment_allows_it(monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_ALLOW_DOCKER", False)
    with pytest.raises(ValidationError):
        CodingToolkit(_workspace(backend_kind="docker")).build()


def test_unknown_backend_kind_is_refused():
    with pytest.raises(ValidationError):
        CodingToolkit(_workspace(backend_kind="nonsense")).build()


def test_remote_sandbox_reattaches_to_the_workspaces_own_session():
    tools = CodingToolkit(_workspace(root="ws-0123456789ab")).build()
    assert tools.backend.id == "ws-0123456789ab"


async def test_aclose_survives_a_dead_sandbox():
    toolkit = CodingToolkit(_workspace())
    toolkit.build()
    toolkit._backend = MagicMock(stop=MagicMock(side_effect=RuntimeError("gone")))
    await toolkit.aclose()  # must not raise — a dead sandbox cannot fail the turn


# --- approval actually defers (against the installed pydantic-ai) -----------


async def test_write_file_surfaces_as_a_deferred_approval_and_runs_once_approved():
    """The whole design rests on this: the console toolset's approvals arrive
    through the same DeferredToolRequests loop the browser dialog already
    answers, so no second approval mechanism is introduced."""
    backend = StateBackend({})
    toolkit = CodingToolkit(_workspace())
    # Swap the sandbox for an in-memory backend; everything else is the real
    # toolkit, so this exercises the gating the product actually builds.
    toolkit._build_backend = lambda: backend  # type: ignore[method-assign]
    calls = {"n": 0}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "/a.txt", "content": "hi"})]
            )
        return ModelResponse(parts=[TextPart("done")])

    toolset = toolkit.build().toolset
    agent = Agent[None, str](
        model=FunctionModel(model_fn), toolsets=[toolset], output_type=[str, DeferredToolRequests]
    )

    result = await agent.run("write a file")

    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_name for call in result.output.approvals] == ["write_file"]
    assert backend.files.get("/a.txt") is None, "nothing is written before approval"

    approved = DeferredToolResults(
        approvals={call.tool_call_id: ToolApproved() for call in result.output.approvals}
    )
    after = await agent.run(message_history=result.all_messages(), deferred_tool_results=approved)

    assert after.output == "done"
    assert backend.files.get("/a.txt") is not None, "the approved write reached the sandbox"


async def test_an_approved_write_to_a_secret_file_is_still_refused():
    """The guard's denials outrank the user's approval — approving a write does
    not approve writing to .env."""
    backend = StateBackend({})
    toolkit = CodingToolkit(_workspace())
    toolkit._build_backend = lambda: backend  # type: ignore[method-assign]
    calls = {"n": 0}
    returned: list[str] = []

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "/.env", "content": "KEY=1"})]
            )
        for part in getattr(messages[-1], "parts", []):
            if getattr(part, "tool_name", None) == "write_file":
                returned.append(str(part.content))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent[None, str](
        model=FunctionModel(model_fn),
        toolsets=[toolkit.build().toolset],
        output_type=[str, DeferredToolRequests],
    )
    result = await agent.run("write the env file")
    approved = DeferredToolResults(
        approvals={call.tool_call_id: ToolApproved() for call in result.output.approvals}
    )
    await agent.run(message_history=result.all_messages(), deferred_tool_results=approved)

    assert backend.files.get("/.env") is None, "the secret file was written despite the denial"
    assert returned and "denied" in returned[0].lower()


async def test_readonly_workspace_reads_without_asking_anyone():
    """A readonly workspace must not strand reads on an approval nobody answers."""
    backend = StateBackend({})
    backend.write("/README.md", "hello")
    toolkit = CodingToolkit(_workspace(ruleset="readonly"))
    toolkit._build_backend = lambda: backend  # type: ignore[method-assign]
    calls = {"n": 0}
    returned: list[str] = []

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("read_file", {"path": "/README.md"})])
        for part in getattr(messages[-1], "parts", []):
            if getattr(part, "tool_name", None) == "read_file":
                returned.append(str(part.content))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent[None, str](
        model=FunctionModel(model_fn),
        toolsets=[toolkit.build().toolset],
        output_type=[str, DeferredToolRequests],
    )
    result = await agent.run("read the readme")

    assert result.output == "done", "a readonly read should not defer"
    assert returned and "hello" in returned[0]
    assert "denied" not in returned[0].lower()


async def test_coding_tools_never_reach_a_subagent():
    """ADR-006 §3: the toolkit attaches to the top-level agent only.

    Subagents cannot approve — Deps.clone_for_subagent sets approve_tools=None
    — so a write tool reaching one would either fail on every call or have to be
    configured approval-free. The delegation capability builds its own agents
    and never reads the parent's toolsets; this pins that, so adding a
    toolsets_factory later fails here instead of silently handing a subagent
    file and shell access.
    """
    from app.agents.assistant import Deps
    from app.services.research import _subagent_configs

    # A subagent's deps carry no approval callback, which is why it must carry
    # no tool that needs one.
    parent = Deps(user_id="u1", approve_tools=lambda requests: None)
    child = parent.clone_for_subagent()
    assert child.approve_tools is None

    # No subagent config smuggles the coding tools in by name.
    for config in _subagent_configs():
        declared = getattr(config, "toolsets", None) or []
        for toolset in declared:
            names = getattr(toolset, "tools", {})
            assert not (set(names) & CODING_WRITE_TOOLS), (
                f"subagent {getattr(config, 'name', config)!r} declares a coding tool"
            )


# --- the session's auto-approval branch -------------------------------------


def _session_with_auto_approved(tool_names: frozenset[str]):
    """An AgentSession shell with just the state _approve_tools reads."""
    from app.services.agent_session import AgentSession

    session = AgentSession.__new__(AgentSession)
    session.websocket = MagicMock()
    session._auto_approved_tools = tool_names
    session._approval_future = None
    return session


def _requests(*tool_names: str):
    calls = [
        MagicMock(
            tool_call_id=f"call-{i}",
            tool_name=name,
            args_as_dict=MagicMock(return_value={}),
        )
        for i, name in enumerate(tool_names)
    ]
    requests = MagicMock(approvals=calls, calls=[], metadata={})
    requests.build_results = MagicMock(side_effect=lambda **kw: kw)
    return requests, calls


async def test_auto_approved_write_never_reaches_the_browser(monkeypatch):
    import app.services.agent_session as module

    sent: list[tuple[str, dict]] = []

    async def _send_event(_ws, event_type, payload):
        sent.append((event_type, payload))
        return True

    monkeypatch.setattr(module, "send_event", _send_event)
    session = _session_with_auto_approved(CODING_WRITE_TOOLS)
    requests, calls = _requests("write_file")

    results = await session._approve_tools(requests)

    assert isinstance(results["approvals"][calls[0].tool_call_id], ToolApproved)
    assert [event for event, _ in sent] == ["tool_auto_approved"]
    assert "tool_approval_required" not in [event for event, _ in sent]


async def test_auto_approval_does_not_cover_a_google_or_mcp_tool(monkeypatch):
    """ADR-006 §4: the override is scoped to the workspace's coding tools."""
    import app.services.agent_session as module

    sent: list[str] = []

    async def _send_event(_ws, event_type, payload):
        sent.append(event_type)
        return True

    monkeypatch.setattr(module, "send_event", _send_event)
    session = _session_with_auto_approved(CODING_WRITE_TOOLS)
    requests, calls = _requests("write_file", "gmail_send_message")

    async def _resolve():
        # The browser answers only the non-coding call.
        while session._approval_future is None:
            await __import__("asyncio").sleep(0)
        session._approval_future.set_result([{"id": calls[1].tool_call_id, "decision": "approve"}])

    import asyncio

    task = asyncio.create_task(_resolve())
    results = await session._approve_tools(requests)
    await task

    assert "tool_approval_required" in sent, "the Google mutation still asks the user"
    assert isinstance(results["approvals"][calls[0].tool_call_id], ToolApproved)
    assert isinstance(results["approvals"][calls[1].tool_call_id], ToolApproved)


async def test_without_a_workspace_every_approval_reaches_the_browser(monkeypatch):
    import app.services.agent_session as module

    sent: list[str] = []

    async def _send_event(_ws, event_type, payload):
        sent.append(event_type)
        return True

    monkeypatch.setattr(module, "send_event", _send_event)
    session = _session_with_auto_approved(frozenset())
    requests, calls = _requests("write_file")

    import asyncio

    async def _resolve():
        while session._approval_future is None:
            await asyncio.sleep(0)
        session._approval_future.set_result([{"id": calls[0].tool_call_id, "decision": "reject"}])

    task = asyncio.create_task(_resolve())
    results = await session._approve_tools(requests)
    await task

    assert sent == ["tool_approval_required"]
    assert type(results["approvals"][calls[0].tool_call_id]).__name__ == "ToolDenied"


# --- attaching the toolkit to a turn ----------------------------------------


async def test_no_workspace_named_attaches_no_tools(monkeypatch):
    from app.services.agent_session import AgentSession

    session = AgentSession.__new__(AgentSession)
    monkeypatch.setattr(settings, "ENABLE_CODING", True)
    assert await session._build_coding_toolsets(None) == []


async def test_coding_disabled_attaches_no_tools(monkeypatch):
    from app.services.agent_session import AgentSession

    session = AgentSession.__new__(AgentSession)
    monkeypatch.setattr(settings, "ENABLE_CODING", False)
    assert await session._build_coding_toolsets(str(uuid4())) == []


async def test_malformed_workspace_id_attaches_no_tools(monkeypatch):
    from app.services.agent_session import AgentSession

    session = AgentSession.__new__(AgentSession)
    monkeypatch.setattr(settings, "ENABLE_CODING", True)
    assert await session._build_coding_toolsets("not-a-uuid") == []


async def test_another_users_workspace_attaches_no_tools_and_does_not_error(monkeypatch):
    """A chat turn must not become a probe for other users' workspace ids."""
    import app.services.agent_session as module
    from app.core.exceptions import NotFoundError
    from app.services.agent_session import AgentSession

    session = AgentSession.__new__(AgentSession)
    session.user = MagicMock(id=uuid4())
    monkeypatch.setattr(settings, "ENABLE_CODING", True)

    class _Ctx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(module, "get_db_context", lambda: _Ctx())
    monkeypatch.setattr(
        module.WorkspaceService,
        "get_for_user",
        AsyncMock(side_effect=NotFoundError(message="Workspace not found", details={})),
    )

    assert await session._build_coding_toolsets(str(uuid4())) == []


# --- the target repository's conventions (ADR-006 section 5) -----------------
#
# These drive the real StateBackend rather than a hand-written fake. A fake got
# this wrong once: the real backend returns FileInfo mappings from glob_info and
# a line-numbered gutter from read(), and a fake that returned objects and plain
# text made a briefing that produced nothing look like it worked.


def _seed(files: dict[str, str]) -> StateBackend:
    backend = StateBackend({})
    for path, content in files.items():
        backend.write(path, content)
    return backend


SKILL_MD = (
    "---\nname: gov-change\ndescription: Wrap a change in the governance envelope.\n---\n# Body"
)


async def test_briefing_reads_agents_md_from_the_workspace():
    briefing = await read_repository_briefing(
        _seed({"/workspace/AGENTS.md": "# Entry point\nRun make check."})
    )

    assert "The repository you are working in" in briefing
    assert "Run make check." in briefing
    assert "### AGENTS.md" in briefing


async def test_briefing_carries_no_line_number_gutter():
    """read() adds one for the model; parsing must not inherit it."""
    briefing = await read_repository_briefing(_seed({"/workspace/AGENTS.md": "alpha\nbeta"}))

    assert "alpha\nbeta" in briefing
    assert "\t" not in briefing


async def test_briefing_prefers_agents_md_over_claude_md():
    briefing = await read_repository_briefing(
        _seed({"/workspace/AGENTS.md": "agents entry", "/workspace/CLAUDE.md": "claude entry"})
    )

    assert "agents entry" in briefing
    assert "claude entry" not in briefing


async def test_briefing_falls_back_to_a_repository_checked_out_at_the_root():
    briefing = await read_repository_briefing(_seed({"/AGENTS.md": "root entry"}))
    assert "root entry" in briefing


async def test_briefing_lists_skills_by_name_and_description():
    briefing = await read_repository_briefing(
        _seed(
            {
                "/workspace/AGENTS.md": "entry",
                "/workspace/.claude/skills/gov-change/SKILL.md": SKILL_MD,
            }
        )
    )

    assert "Available skills" in briefing
    assert "`gov-change`" in briefing
    assert "Wrap a change in the governance envelope." in briefing


async def test_briefing_names_a_skill_by_its_directory_when_front_matter_omits_one():
    briefing = await read_repository_briefing(
        _seed({"/workspace/.claude/skills/my-skill/SKILL.md": "# No front matter here"})
    )
    assert "`my-skill`" in briefing


async def test_briefing_is_empty_when_the_workspace_documents_nothing():
    assert await read_repository_briefing(_seed({})) == ""


REPO_URL = "git://host.docker.internal:9418/FullStack/fullstack"


async def test_briefing_names_the_workspace_repo_url():
    briefing = await read_repository_briefing(
        _seed({"/workspace/AGENTS.md": "entry"}), repo_url=REPO_URL
    )
    assert REPO_URL in briefing


async def test_briefing_carries_the_repo_url_even_when_the_workspace_is_empty():
    """The first turn — nothing cloned yet — is exactly when the URL is needed."""
    briefing = await read_repository_briefing(_seed({}), repo_url=REPO_URL)
    assert REPO_URL in briefing
    assert "cloning that URL is the first step" in briefing


async def test_briefing_truncates_a_huge_conventions_file():
    briefing = await read_repository_briefing(
        _seed({"/workspace/AGENTS.md": "x" * (MAX_CONVENTION_CHARS + 5000)})
    )

    assert "(truncated)" in briefing
    assert len(briefing) < MAX_CONVENTION_CHARS + 2000


async def test_briefing_degrades_to_nothing_when_the_sandbox_is_unreachable():
    """A briefing is a convenience; losing it must not fail the turn."""

    class _Dead:
        def read_bytes(self, *a, **k):
            raise RuntimeError("sandbox gone")

        def glob_info(self, *a, **k):
            raise RuntimeError("sandbox gone")

    assert await read_repository_briefing(_Dead()) == ""


async def test_briefing_keeps_the_repo_url_when_the_sandbox_is_unreachable():
    """A dead sandbox costs the conventions, never the URL the row already holds."""

    class _Dead:
        def read_bytes(self, *a, **k):
            raise RuntimeError("sandbox gone")

        def glob_info(self, *a, **k):
            raise RuntimeError("sandbox gone")

    briefing = await read_repository_briefing(_Dead(), repo_url=REPO_URL)
    assert REPO_URL in briefing


def test_front_matter_scan_ignores_a_body_that_mimics_front_matter():
    text = "---\nname: real\n---\n\nname: fake\ndescription: fake\n"
    assert _front_matter_field(text, "name") == "real"
    assert _front_matter_field(text, "description") == ""


def test_a_workspace_briefing_is_appended_never_a_replacement():
    """A target repository must not displace the product's own instructions."""
    from app.agents.assistant import AssistantAgent
    from app.agents.prompts import get_system_prompt_with_rag

    agent = AssistantAgent(extra_instructions="\n\n## The repository\n\nfollow these")

    assert agent.system_prompt.startswith(get_system_prompt_with_rag())
    assert agent.system_prompt.endswith("follow these")


def test_no_briefing_leaves_the_default_prompt_untouched():
    from app.agents.assistant import AssistantAgent
    from app.agents.prompts import get_system_prompt_with_rag

    assert AssistantAgent().system_prompt == get_system_prompt_with_rag()
