---
id: ADR-006
title: Coding workspaces give the assistant sandboxed repository tools, and every write or execute is approval-gated unless the workspace ruleset auto-approves it
status: proposed
date: 2026-08-23
components:
  - agents
  - backend-api
  - chat-frontend
  - mcp-user-connections
supersedes: []
related_paths:
  - backend/app/agents/tools/*.py
  - backend/app/services/agent_session.py
  - backend/app/services/research.py
  - frontend/src/components/chat/tool-approval-dialog.tsx
  - docker-compose*.yml
policy_refs:
  - privilege-expansion-requires-approval
  - mcp-approval-parity
  - exposure-posture
---

# ADR-006 — Coding workspaces give the assistant sandboxed repository tools, and every write or execute is approval-gated unless the workspace ruleset auto-approves it

## Status

Proposed, 2026-08-23. This settles *where* repository tools execute, *what* a workspace is,
*how* mutations are gated, and — amended 2026-08-23 — *what network* a sandbox gets, per
stack (Decision rule 7). It deliberately does not settle private-repository credentials or
per-workspace resource quotas (see Open questions).

## Context

The product was envisioned as a Claude-Code-like coding assistant with a RAG system
attached: chat, plus the ability to perform coding tasks on repositories the user names.
The generated tree does not deliver the second half. `backend/app/agents/assistant.py`
registers RAG search, web search and fetch, chart creation, `run_python`, ask-user, the
Google Workspace toolsets, MCP servers, and an optional `SkillsToolset` rooted at a
`skills` directory beside `app/` that has never been created. There is no file-read, file-write,
shell, or git tool, and `backend/app/db/models/` has no notion of a workspace or a target
repository. `run_python` (`backend/app/agents/tools/code_execution.py`) is a Monty sandbox
with a restricted stdlib and no filesystem, so it cannot substitute.

The generator that produced this repository (`fastapi-fullstack` 0.2.17, recorded in
`backend/pyproject.toml` under `[tool.fastapi-fullstack]`) offers a `pydantic_deep`
framework variant. That variant is composed from packages this repository already locks —
`pydantic-ai-todo`, `subagents-pydantic-ai`, `pydantic-ai-skills`,
`summarization-pydantic-ai` — plus one it does not: `pydantic-ai-backend`, which supplies
the `ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `execute` console
toolset over `StateBackend`, `LocalBackend`, `DockerSandbox`, and `RemoteSandbox`
backends, with `require_write_approval` / `require_execute_approval` rulesets. The
"Deep Research" mode already wired in `backend/app/services/research.py` is that variant
minus this one package.

Three forces constrain how the package may be attached:

1. **The approval model already exists and is the only gate.** `AgentSession._approve_tools`
   (`backend/app/services/agent_session.py`) pauses a run on `DeferredToolRequests`, sends
   `tool_approval_required` over the chat WebSocket, and resumes on the browser's decision;
   `frontend/src/components/chat/tool-approval-dialog.tsx` renders it with `allow_edit`.
   `security.json` rule `mcp-approval-parity` states that a write-capable tool is
   approval-gated regardless of its source, and `privilege-expansion-requires-approval`
   states that adding a write-capable tool source requires explicit human approval recorded
   in a change record. ADR-003's first open question — MCP-sourced tools bypassing approval —
   is still a registered finding (`mcp-approval-gating-asymmetry`). A new write-capable
   surface must therefore not be introduced through MCP, and must not widen that gap.
2. **The API container must not become an execution host.** ADR-005 fixes the exposure
   posture per stack: the base stack publishes nothing, `data-internal` is `internal: true`,
   and the edge is the only public surface. A `LocalBackend` rooted inside the `backend`
   container would let a model-driven `execute` reach the application's own filesystem,
   its environment (every secret in `ENV_VARS.md` is present there), and the
   `data-internal` network. A `DockerSandbox` driven from inside the API container would
   require mounting the Docker socket, which is host root.
3. **Subagents cannot approve.** `Deps.clone_for_subagent` in `assistant.py` sets
   `approve_tools=None`, so any write-capable tool reaching a subagent would either fail
   on every call or, worse, be configured approval-free to make it work.

A fourth fact shapes the workspace model rather than constraining it: per-turn model
selection already exists (`data.get("model")` in `agent_session.py`, list from
`AI_AVAILABLE_MODELS`), and every agent run is traced to Logfire with `gen_ai` usage and
tool schemas. A coding mode inherits both — a cheaper model can be chosen per turn, and
coding runs produce telemetry without new instrumentation.

## Decision

**A coding workspace is a user-owned, persistent, sandboxed filesystem that a chat turn may
name; repository tools attach to that turn only, execute only inside the sandbox, and every
mutation is a deferred approval unless the workspace's ruleset auto-approves it.**

Rules an agent can check a change against:

1. **Execution boundary.** File and shell tools run through a `RemoteSandbox` talking to a
   sandbox service that is its own Compose service on its own network, or through a
   `DockerSandbox` only when the backend itself runs as a host process outside Compose
   (`make dev` on a developer machine). `LocalBackend` rooted anywhere inside the `backend`
   container is forbidden in every Compose stack. The `backend` service never mounts the
   Docker socket. Unit tests use `StateBackend` (in-memory) or a `LocalBackend` rooted at a
   pytest `tmp_path`. Because this is an execution surface, the sandbox service follows
   ADR-005: no published host ports in the base stack, loopback-only in dev, and it joins
   neither `data-internal` nor any network that reaches PostgreSQL, Redis, or MinIO.
2. **Workspace model.** A `workspaces` table owned by the user (`user_id` FK, cascade
   delete) carries `name`, `backend_kind` (`remote` | `docker`), `root` (the sandbox
   workspace name or container root; never a host path), `repo_url` (nullable; stored
   stripped of credentials per `connection-urls-are-stripped`), `ruleset`
   (`readonly` | `default` | `strict`), and `auto_approve` (boolean, default `false`). A
   conversation turn names at most one workspace. Workspace CRUD is a normal
   `/api/v1/me/workspaces` REST surface with its Next.js proxy handler; the chat WebSocket
   only references a workspace id it does not create.
3. **Toolkit attachment.** Repository tools are a per-turn toolkit built the way
   `ResearchToolkit` is built in `research.py`: constructed in `AgentSession` when the
   turn payload carries a `workspace_id` and `ENABLE_CODING` is on, attached to the
   top-level agent only, torn down with the turn. They are never attached to subagents
   (`max_nesting_depth` stays `0` for the coding toolkit) and never exposed through an MCP
   server.
4. **Approval gating.** `write_file`, `edit_file`, and `execute` are deferred-approval tools
   resolved by the existing `_approve_tools` path and the existing dialog. `ls`,
   `read_file`, `glob`, and `grep` are not gated. A `readonly` ruleset does not register the
   write or execute tools at all — there is nothing to approve. `auto_approve=true` resolves
   write and execute approvals without a round-trip **for that workspace only**; it is
   refused in combination with `ruleset=strict`, it is displayed in the chat controls as an
   active override for the whole conversation, and every auto-approved call carries an
   `auto_approved=true` attribute on its tool span so telemetry can distinguish them.
   Auto-approval never extends to a tool outside the workspace toolkit (Google mutations
   and MCP tools keep their own gating).
5. **Target-repository process.** Inside a workspace the agent drives the target
   repository's own conventions — its `AGENTS.md`, its Make targets, its `.claude/skills`
   — through `execute` and, when present, a `SkillsToolset` rooted at the workspace's
   `.claude/skills`. This repository's `.claude/settings.json` hooks (read gate,
   touched-file log, `Stop` check) are Claude Code hooks and do not apply inside a
   workspace; the target repository's own gates (pre-commit, CI, change records) are what
   hold. When the target is this repository, that is the same guarantee every non-Claude
   harness already gets (`AGENTS.md`: "steering, not a security boundary").
6. **This ADR is the recorded approval** that `privilege-expansion-requires-approval`
   demands for adding a write-capable tool source. Each implementing session cites it with
   `--adr ADR-006`; the first one that lands write-capable code also runs
   `security-review` before its change record is written.
7. **Egress is a per-stack property of the runtime allowlist entry** (added 2026-08-23,
   resolving Open question 2). A sandbox's network is set by its allowlist entry's
   `network_mode`, which overrides the service-wide default — the mechanism note lives
   beside the allowlist in `docker-compose.yml`. The workspace runtime is a
   repository-owned, digest-pinned image (`docker/sandbox-runtime/`) carrying the
   baseline toolchain a repository process needs — without git, make, and uv a coding
   sandbox dead-ends before any documented entry point can run. The base stack pins
   `network_mode=none`: no egress. The dev stack grants the workspace runtime the
   daemon's default bridge, so an agent can clone the repository a workspace names and
   install what that project declares; the sandbox still joins no Compose network, so
   PostgreSQL, Redis, and MinIO stay unreachable. A production stack keeps `none` until
   an egress allowlist — a filtering proxy the runtime entry points at — exists;
   blanket egress in production is not an option this ADR grants. Every widening is a
   per-stack edit to the runtime entry, visible in `compose config` diffs.

## Consequences

- Easier: the product gains coding capability with one new dependency
  (`pydantic-ai-backend`) and one new pattern instance (a toolkit beside
  `ResearchToolkit`), reusing the approval loop, the dialog, per-turn model selection, the
  skills toolset slot, and existing telemetry.
- Harder: a sandbox service becomes part of every Compose stack that enables coding, with
  its own image pin, network, volume, and exposure rules under ADR-005; `compose-check`,
  `preflight-volumes`, and `compose-check-prod` gain a service to validate.
- New invariants for the `agents` component annotation, proved by `backend-unit` and
  `agent-evals`: *coding tools execute only through a sandbox backend, never
  `LocalBackend` in the API container*; *write and execute tools are deferred-approval
  unless the workspace auto-approves*; *coding tools are absent from subagent deps*;
  *`readonly` workspaces register no write or execute tool*. For `backend-api`:
  *`repo_url` is stored stripped of credentials*. For `chat-frontend`: *an active
  auto-approve workspace is visible in the chat controls*.
- The Documentation plan's decision that "backend PydanticAI agents stay out" of
  repository work (its §0.11 / §7.10) is reversed by this ADR. Plans stay out of the
  agents' scope; repository *tools* no longer do.
- To maintain: the `workspaces` migration, the sandbox service's and workspace runtime's
  image digests (including the pinned uv the runtime carries), and the ruleset presets as
  `pydantic-ai-backend` evolves them.

## Alternatives considered

**Depend on `pydantic-deep` wholesale.** It is the same author's "self-hosted Claude Code"
and the reference design for this ADR, but it is CLI/TUI-oriented and re-bundles the todo,
subagent, skills, and summarization packages already composed here. Rejected to avoid two
copies of the same capabilities; reconsidered if `pydantic-deep` grows a server mode that
replaces `AgentSession` rather than duplicating it.

**Expose a filesystem or shell MCP server.** This is exactly the surface ADR-003 warns
about and the one `mcp-approval-gating-asymmetry` leaves ungated. Rejected until that
finding is closed; even then, an MCP server gives no per-workspace scoping or ruleset.

**`LocalBackend` inside the API container.** The smallest code change and the largest
blast radius: model-driven `execute` with the application's secrets and `data-internal`
reachable. Rejected outright; no condition reconsiders it.

**Regenerate or upgrade to the generator's `pydantic_deep` variant.** Possibly the least
typing, but `template-upgrade` is this repository's largest-blast-radius operation and the
variant would still need the sandbox boundary, workspace model, and ruleset decided here.
Reconsidered if a dry-run shows the variant lands as an additive feature rather than a
framework swap.

**Use an external coding harness (Codex, OpenCode) against the repository instead.** It
works today with zero product change — `AGENTS.md` is written for it — but it is not the
product, produces none of the product's telemetry, and cannot use the RAG knowledge base
in the loop. Kept as the fallback for work on this repository; not a substitute for the
capability.

## Open questions

1. **Private repositories.** How clone credentials are supplied to the sandbox — a
   per-workspace Fernet-encrypted token as `mcp_connections` does, the user's existing
   GitHub connection, or SSH agent forwarding — is not decided. Until it is, `repo_url`
   must be reachable without credentials.
2. **Sandbox egress.** *Resolved 2026-08-23 by Decision rule 7*: per stack — `none` in the
   base stack, the daemon's bridge for the dev workspace runtime, and production closed
   until an egress allowlist proxy exists. Kept in this list under its number so existing
   citations of "open question 2" keep resolving.
3. **Quotas.** Per-workspace disk, CPU, and wall-clock limits for `execute`; whether a
   workspace's sandbox state persists across turns indefinitely or is garbage-collected.
4. **Auto-approve lifetime.** Whether `auto_approve` should expire or require
   re-assertion per conversation rather than being a stored workspace property.

## References

- Change record proposing this ADR:
  `2026-08-23-propose-adr-006-coding-workspaces-give-the-assistant-sandboxed-r`
- ADR-003 (MCP connection model, open question 1), ADR-005 (exposure posture)
- `governance/policies/security.json`: `privilege-expansion-requires-approval`,
  `mcp-approval-parity`, `connection-urls-are-stripped`, `exposure-posture`
- Open findings: `mcp-approval-gating-asymmetry`, `mcp-no-connection-time-ssrf-revalidation`
- `pydantic-ai-backend` (PyPI, 0.2.29 at time of writing); `pydantic-deep` as reference design
- Documentation pipeline plan §0.11 / §7.10 (decision reversed here)
