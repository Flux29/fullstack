"""Sandboxed repository tools for a chat turn that names a workspace (ADR-006).

The toolkit is built per turn, attached to the top-level agent only, and torn
down with the turn — the same shape as
:class:`app.services.research.ResearchToolkit`.

**Where code runs.** ADR-006 forbids ``LocalBackend`` rooted inside the API
container: a model-driven ``execute`` there would reach the application's own
filesystem, its environment (every secret in ``ENV_VARS.md``), and the
``data-internal`` network. So a workspace resolves to either

* ``RemoteSandbox`` — HTTP to a ``sandboxd`` service that owns the Docker
  socket. The API container holds a token, never the socket. This is the only
  kind that may run in a Compose stack.
* ``DockerSandbox`` — the backend drives Docker itself. Refused unless
  ``SANDBOX_ALLOW_DOCKER`` is set, which is for a developer running the backend
  as a host process outside Compose.

Nothing here imports ``LocalBackend``, so ``grep -rn LocalBackend app/`` staying
empty is a checkable statement of that rule.

**How mutations are gated.** ``write_file``, ``edit_file``, and ``execute`` are
registered with ``requires_approval``, so pydantic-ai surfaces them as
``DeferredToolRequests.approvals`` and
:meth:`app.services.agent_session.AgentSession._approve_tools` — the existing
browser dialog — resolves them. Nothing new asks the user.

**Why approvals and denials are configured separately, verified against 0.2.29.**
Handing a preset ruleset to ``create_console_toolset(permissions=...)`` looks
equivalent and is not, in both directions:

* A ruleset whose operation default is ``"ask"`` registers the tool as needing
  approval *and* re-checks it in the backend guard, which cannot learn that the
  browser already approved. The call then fails **after** the user approves it,
  returning ``Error: Permission denied for write on '/x' (approval required)``
  to the model — dialog shown, user says yes, nothing written.
* A ruleset whose default is ``"allow"`` makes ``requires_approval`` read
  ``"allow"`` and silently turns the approval booleans off entirely.

So the toolkit passes ``permissions=None`` with the explicit approval booleans,
and enforces the denials by wrapping the backend with ``guarding()`` and the
library's own ``PERMISSIVE_RULESET`` (allow-by-default, with the secret-file,
system-path, and destructive-command denials). Secrets stay unreadable and
``rm -rf /`` stays blocked whatever the user approved. ``readonly`` is the one
preset safe to pass directly, because it *unregisters* the mutating tools
rather than asking about them, and leaves reads on ``allow``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic_ai_backends import DockerSandbox, RemoteSandbox
from pydantic_ai_backends.backends._guard import guarding
from pydantic_ai_backends.permissions import PERMISSIVE_RULESET, READONLY_RULESET
from pydantic_ai_backends.toolsets.console import create_console_toolset

from app.core.config import settings
from app.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

Sandbox = RemoteSandbox | DockerSandbox

#: The mutating tools this toolkit registers — exactly the three ADR-006 §4
#: enumerates. Approval for one of these is what an auto-approving workspace
#: may resolve without a round trip; anything else is never auto-resolved.
#:
#: Background shells (``run_in_background`` and friends) are deliberately not
#: registered: only ``LocalBackend`` implements them, so on the two sandbox
#: kinds ADR-006 permits they would show the user an approval dialog for a call
#: that always fails — and they bypass the backend guard's denials.
CODING_WRITE_TOOLS = frozenset({"write_file", "edit_file", "execute"})


@dataclass(frozen=True)
class WorkspacePolicy:
    """What the toolkit needs from a workspace row, read while the session is open.

    A value rather than the ORM entity: this module is in the agent layer and
    must not hold a row whose loading state depends on a database session that
    has already closed.
    """

    name: str
    backend_kind: str
    root: str
    ruleset: str
    auto_approve: bool
    #: Already normalized at write time (scheme allowlist, credentials
    #: stripped), so it is safe to hand to the model verbatim.
    repo_url: str | None = None

    @classmethod
    def from_row(cls, workspace: Any) -> WorkspacePolicy:
        return cls(
            name=workspace.name,
            backend_kind=workspace.backend_kind,
            root=workspace.root,
            ruleset=workspace.ruleset,
            auto_approve=workspace.auto_approve,
            repo_url=workspace.repo_url,
        )


@dataclass
class CodingTools:
    """What a turn needs to run and tear down the coding toolset."""

    toolset: Any
    backend: Sandbox
    #: Tool names whose approval this workspace resolves without asking.
    auto_approved_tools: frozenset[str]


class CodingToolkit:
    """Builds the console toolset for one workspace, for one turn."""

    def __init__(self, policy: WorkspacePolicy) -> None:
        self.policy = policy
        self._backend: Sandbox | None = None

    def _build_backend(self) -> Sandbox:
        kind = self.policy.backend_kind
        if kind == "remote":
            return RemoteSandbox(
                service_url=settings.SANDBOX_SERVICE_URL,
                token=settings.SANDBOX_SERVICE_TOKEN.get_secret_value(),
                session_id=self.policy.root,
                reuse=True,
                timeout=settings.SANDBOX_TIMEOUT_SECS,
            )
        if kind == "docker":
            if not settings.SANDBOX_ALLOW_DOCKER:
                raise ValidationError(
                    message=(
                        "This deployment does not run Docker sandboxes directly. "
                        "Use a workspace with the sandbox service instead."
                    ),
                    details={"backend_kind": kind},
                )
            return DockerSandbox(
                image=settings.SANDBOX_DOCKER_IMAGE,
                container_name=self.policy.root,
                network_mode=settings.SANDBOX_NETWORK_MODE,
                mem_limit=settings.SANDBOX_MEM_LIMIT,
                cpus=settings.SANDBOX_CPUS,
            )
        raise ValidationError(message="Unknown workspace backend", details={"backend_kind": kind})

    def build(self) -> CodingTools:
        """Create the backend and the console toolset for this turn.

        The sandbox is *not* started here — both sandbox classes open their
        session lazily on the first operation, so a turn that never calls a
        coding tool costs nothing.
        """
        readonly = self.policy.ruleset == "readonly"
        self._backend = self._build_backend()
        if readonly:
            # READONLY_RULESET unregisters write/edit/execute outright and keeps
            # reads on "allow", so there is no ask verdict to strand.
            toolset = create_console_toolset(
                id="coding",
                backend=self._backend,
                permissions=READONLY_RULESET,
                include_background=False,
                ask_fallback="deny",
            )
        else:
            # Denials through the guard, approvals through the deferred-tool
            # loop — see the module docstring for why these cannot be merged.
            guarded = guarding(self._backend, PERMISSIVE_RULESET, ask_fallback="deny")
            toolset = create_console_toolset(
                id="coding",
                backend=guarded,
                require_write_approval=True,
                require_execute_approval=True,
                include_background=False,
                ask_fallback="deny",
            )
        return CodingTools(
            toolset=toolset,
            backend=self._backend,
            auto_approved_tools=self._auto_approved_tools(),
        )

    def _auto_approved_tools(self) -> frozenset[str]:
        """ADR-006 §4, enforced here and not only at write time.

        ``strict`` never auto-approves and ``readonly`` has nothing to approve,
        so only a ``default`` workspace that opted in resolves its own writes.
        The service refuses the strict combination on create and update; this is
        the coding surface's own defence against a row that arrived some other
        way.
        """
        if self.policy.ruleset != "default" or not self.policy.auto_approve:
            return frozenset()
        return CODING_WRITE_TOOLS

    async def aclose(self) -> None:
        """Release the sandbox handle. The sandbox itself is the service's to reap."""
        backend, self._backend = self._backend, None
        if backend is None:
            return
        try:
            backend.stop()
        except Exception as exc:  # a dead sandbox must not fail the turn
            logger.warning("Releasing sandbox for workspace %s failed: %s", self.policy.name, exc)


# --- the target repository's own conventions (ADR-006 section 5) -------------

#: Files a repository uses to tell an agent how to work in it, most specific
#: first. Only the first one found is read — AGENTS.md is the vendor-neutral
#: name and CLAUDE.md the Claude-specific one, and a repository carrying both
#: means the first to point at the other.
CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")

#: Enough of the conventions file to carry its routing rules without spending
#: the turn's context on it. This repository's own AGENTS.md is ~8.8 KB.
MAX_CONVENTION_CHARS = 8000

#: A skills index is a listing, not a payload: the agent reads the SKILL.md it
#: actually needs with read_file, the same way a human would.
MAX_SKILLS_LISTED = 40

#: A repository may be checked out at the sandbox work dir or at the root.
WORKSPACE_PREFIXES = ("/workspace/", "/")


def _front_matter_field(text: str, field: str) -> str:
    """Pull one scalar out of a leading YAML front-matter block, without a parser.

    The skills format is fixed (``---``, ``name:``/``description:``, ``---``) and
    this runs on untrusted repository content, so it stays a line scan rather
    than a YAML load.
    """
    if not text.startswith("---"):
        return ""
    _, _, rest = text.partition("\n")
    body, _, _ = rest.partition("\n---")
    prefix = f"{field}:"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip("\"'")
    return ""


def _text_of(data: object) -> str:
    """Decode what a backend hands back, whatever shape it uses.

    ``read()`` is the model-facing tool and returns a line-numbered gutter
    (``     1\tcontent``) on some backends and plain text on others — right for
    a model, wrong for parsing, and the difference is invisible until a real
    backend is swapped in. ``read_bytes()`` is the raw accessor: it never adds
    a gutter and returns ``b""`` for anything it cannot read, so there is no
    error-string to sniff for either.
    """
    if not data:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _path_of(info: object) -> str:
    """A glob result's path.

    ``FileInfo`` is a ``TypedDict``, so most backends hand back a mapping;
    accept an attribute-style object too rather than depending on which.
    """
    if isinstance(info, dict):
        return str(info.get("path") or info.get("name") or "")
    return str(getattr(info, "path", None) or getattr(info, "name", "") or "")


async def _read_conventions(async_backend: object) -> str:
    """The first conventions file the workspace actually has."""
    for name in CONVENTION_FILES:
        for prefix in WORKSPACE_PREFIXES:
            content = _text_of(await async_backend.read_bytes(f"{prefix}{name}"))
            if not content.strip():
                continue
            truncated = content[:MAX_CONVENTION_CHARS]
            note = " (truncated)" if len(content) > MAX_CONVENTION_CHARS else ""
            return f"### {name}{note}\n\n{truncated}"
    return ""


async def _read_skills_index(async_backend: object) -> str:
    """List the target repository's skills by name and description.

    A listing, not a payload: the agent reads the SKILL.md it actually needs
    with ``read_file``, the same way a human would.
    """
    matches: list[object] = []
    for prefix in WORKSPACE_PREFIXES:
        root = prefix.rstrip("/") or "/"
        found = await async_backend.glob_info("**/.claude/skills/*/SKILL.md", root)
        if found and not isinstance(found, str):
            matches = list(found)
            break
    if not matches:
        return ""

    lines: list[str] = []
    unreadable = 0
    for info in matches[:MAX_SKILLS_LISTED]:
        path = _path_of(info)
        head = _text_of(await async_backend.read_bytes(path)) if path else ""
        if not head:
            unreadable += 1
            continue
        # The directory name is the skill's name when front matter omits one.
        parts = [p for p in path.split("/") if p]
        fallback = parts[-2] if len(parts) >= 2 else path
        name = _front_matter_field(head, "name") or fallback
        description = _front_matter_field(head, "description")
        lines.append(f"- `{name}` — {description}" if description else f"- `{name}`")

    remaining = len(matches) - MAX_SKILLS_LISTED
    if remaining > 0:
        lines.append(f"- …and {remaining} more under `.claude/skills/`")
    if unreadable and not lines:
        return ""
    return "\n".join(lines)


async def read_repository_briefing(backend: object, *, repo_url: str | None = None) -> str:
    """Read the target repository's conventions out of the sandbox (ADR-006 §5).

    Returns a block to append to the turn's instructions, or an empty string
    when the workspace has nothing to report — an empty workspace with no
    configured repository, a repository that documents nothing, or a sandbox
    that cannot be reached. A briefing is a convenience, so every failure
    degrades to a smaller briefing rather than failing the turn.

    ``repo_url`` is the workspace row's normalized repository URL. It goes
    first and survives sandbox failures deliberately: the turn that needs it
    most is the very first one, when the sandbox is empty (nothing to clone
    the URL *from*) — without it every conversation would have to be told the
    URL by hand before the initial clone.
    """
    from pydantic_ai_backends.adapter import ensure_async

    sections: list[str] = []
    if repo_url:
        sections.append(
            "### Repository\n\n"
            f"This workspace names `{repo_url}` as its repository. If the working "
            "directory does not hold a checkout yet, cloning that URL is the first step."
        )
    try:
        async_backend = ensure_async(backend)
        conventions = await _read_conventions(async_backend)
        if conventions:
            sections.append(conventions)
        skills = await _read_skills_index(async_backend)
        if skills:
            sections.append("### Available skills\n\n" + skills)
    except Exception as exc:  # never fail a turn over a briefing
        logger.warning("Could not read the workspace briefing: %s", exc)
    if not sections:
        return ""
    return (
        "\n\n## The repository you are working in\n\n"
        "This workspace has its own conventions. Follow what it documents over "
        "your general habits, and prefer a command or procedure it documents over "
        "improvising one. Read any file named below with `read_file` before acting "
        "on it.\n\n" + "\n\n".join(sections)
    )
