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

    @classmethod
    def from_row(cls, workspace: Any) -> WorkspacePolicy:
        return cls(
            name=workspace.name,
            backend_kind=workspace.backend_kind,
            root=workspace.root,
            ruleset=workspace.ruleset,
            auto_approve=workspace.auto_approve,
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
