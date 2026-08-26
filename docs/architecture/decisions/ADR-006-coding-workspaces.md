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
stack (Decision rule 7). Amended 2026-08-24: rule 5 records the shipped read-only skills
index as the standing interpretation and the backend-rooted skills directory as the
deferred, priced upgrade path. Amended 2026-08-25: rule 7 carries a note recording the
dev-stack local git daemon as an interpretation of the egress already granted, and open
question 1 notes the credential-free `git://` scheme it rides on; rules 8 and 9 resolve
open questions 3 and 4. It deliberately does not
settle private-repository credentials (see Open
questions).

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
5. **Target-repository process** (amended 2026-08-24). Inside a workspace the agent drives
   the target repository's own conventions — its `AGENTS.md`, its Make targets, its
   `.claude/skills` — through `execute` and a **read-only skills index**: the standing
   interpretation, shipped 2026-08-23, is that the attach-time briefing carries the
   repository's entry-point document plus a listing of its skills by name and description,
   and the agent opens the skill files it needs with its own read tools. The original
   phrasing — a `SkillsToolset` rooted at the workspace's `.claude/skills` — could not ship
   against the pinned skills library, whose directory implementation reads only local
   filesystems while the workspace lives in a remote sandbox. That is a fact about the
   pinned library, not an inherent limit: the reference design demonstrates a backend-rooted
   skills directory that discovers skills through the sandbox backend's file protocol and
   executes skill scripts through the sandbox's execute protocol (see evaluation
   `2026-08-24-skillstoolset-limitation-over-scoped`). Adopting it is the priced upgrade
   path, **deferred** because the package breaks compatibility at patch level (a patch
   release moved its required pydantic-ai major version), has a single-maintainer bus
   factor, duck-types its backend dependency and fails silently when it is absent, and does
   synchronous discovery I/O at construction — a poor fit for a per-turn toolkit over an
   HTTP `RemoteSandbox` — while this ADR's Alternatives already reject depending on
   `pydantic-deep` wholesale and its reconsider condition (a server mode) is unmet. Revisit
   when skill *execution* inside a workspace becomes a requirement; that work runs through
   the `agent-tool` workflow, is gated by `agent-evals` and `security-review`, and pins the
   dependency exactly (`==`). This repository's `.claude/settings.json` hooks (read gate,
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
   *Note (2026-08-25):* the dev stack's `git-local` service (`docker-compose.dev.yml`,
   profile `coding`) exports the host's repositories over `git://` on `127.0.0.1:9418`,
   reached from a sandbox as `git://host.docker.internal:9418/<dir>/<repo>` through
   Docker Desktop's host gateway. This is an *interpretation* of the egress this rule
   already grants dev — the runtime has the bridge; the daemon adds a destination on
   the loopback the dev posture already owns — not a new privilege, and it exists so
   the bootstrap loop (clone, work, push `sandbox/<topic>` branches, merge host-side)
   needs no GitHub credential anywhere in the product. It is dev-only by construction:
   the base and production stacks define no such service, and their runtimes have no
   network to reach one with. Two boundaries the security review of this note drew:
   the host gateway is resolvable from *every* local container, so the daemon image
   itself enforces the write surface (an `update` hook admits only new
   `refs/heads/sandbox/*` refs, denies deletes and rewinds, and `core.hooksPath`
   shadows any per-repo hooks, so a push never executes repository-owned code); and
   the anonymous *read* of every repository under the mounted root is accepted
   knowingly as dev-machine posture — a developer who keeps repositories that must
   not be readable this way narrows the bind in a compose override.
8. **Quotas are a per-stack property of the runtime allowlist entry, not a column in
   the workspace model** (added 2026-08-25, resolving Open question 3). Disk, CPU, and
   memory limits ride on the workspace runtime's Compose entry — CPU and memory caps on
   the container, a size-bounded volume or tmpfs for the workspace root — so every limit
   is visible in `compose config` diffs exactly as egress is under rule 7, and no limit
   has to be enforced by code running inside the sandbox it constrains. Wall-clock is
   the one quota enforced in code: sandboxd caps every `execute` at its own 300-second
   ceiling, and the toolkit client applies the matching `SANDBOX_TIMEOUT_SECS` (a
   per-stack setting — dev pins it to the same 300 seconds so one timeout stays
   authoritative; never per-workspace or per-user), because a runaway process must be
   stopped by its caller rather than by the kernel. Sandbox state persists across turns
   of one conversation and is garbage-collected by a taskiq periodic task after an idle
   TTL (7 days default); the `workspaces` row survives collection, and the next turn
   recreates state by re-cloning `repo_url`, so GC loses only derived bytes. Dev ships
   generous defaults; production keeps coding disabled until these entries exist.
9. **`auto_approve` is stored as the user's standing preference, but grants expire with
   the conversation** (added 2026-08-25, resolving Open question 4). The boolean on the
   `workspaces` row says what the user wants; activation happens per conversation through
   the chat-controls toggle rule 4 already requires, and deactivates when the
   conversation ends — a new conversation starts unprivileged even though the preference
   persists. This answers the question's failure mode (a stale grant silently approving
   writes weeks later) without adding statefulness: no TTL column, no expiry scheduler;
   expiry falls out of the conversation lifecycle the toolkit already tears down per
   turn. `ruleset=strict` continues to refuse the combination outright, and the
   `auto_approved=true` span attribute remains the audit trail either way.

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
  image digests (including the pinned uv the runtime carries), the ruleset presets as
  `pydantic-ai-backend` evolves them, the per-stack quota entries rule 8 adds to the
  runtime allowlist, and the sandbox-state GC task's TTL.

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
   must be reachable without credentials. *Note (2026-08-25):* the dev stack satisfies
   this credential-free constraint with the `git://` scheme (added to the `repo_url`
   allowlist; the protocol has no userinfo or auth exchange, so the
   `connection-urls-are-stripped` posture is unchanged) pointed at the rule 7 note's
   local daemon. `ssh://` remains excluded because it carries identity — adopting it is
   this question's decision to make, not the allowlist's.
2. **Sandbox egress.** *Resolved 2026-08-23 by Decision rule 7*: per stack — `none` in the
   base stack, the daemon's bridge for the dev workspace runtime, and production closed
   until an egress allowlist proxy exists. Kept in this list under its number so existing
   citations of "open question 2" keep resolving.
3. **Quotas.** *Resolved 2026-08-25 by Decision rule 8*: disk, CPU, and memory limits
   are per-stack properties of the runtime allowlist entry; wall-clock is a fixed
   toolkit-level timeout on `execute`; sandbox state persists for one conversation and
   is garbage-collected after an idle TTL, the `workspaces` row surviving. Kept in this
   list under its number so existing citations of "open question 3" keep resolving.
4. **Auto-approve lifetime.** *Resolved 2026-08-25 by Decision rule 9*: `auto_approve`
   stays a stored workspace preference, but the grant activates per conversation through
   chat controls and expires when that conversation ends. Kept in this list under its
   number so existing citations of "open question 4" keep resolving.

## References

- Change record proposing this ADR:
  `2026-08-23-propose-adr-006-coding-workspaces-give-the-assistant-sandboxed-r`
- ADR-003 (MCP connection model, open question 1), ADR-005 (exposure posture)
- `governance/policies/security.json`: `privilege-expansion-requires-approval`,
  `mcp-approval-parity`, `connection-urls-are-stripped`, `exposure-posture`
- Open findings: `mcp-approval-gating-asymmetry`, `mcp-no-connection-time-ssrf-revalidation`
- `pydantic-ai-backend` (PyPI, 0.2.29 at time of writing); `pydantic-deep` as reference design
- Evaluation record on rule 5's skills surface:
  `2026-08-24-skillstoolset-limitation-over-scoped`
- Documentation pipeline plan §0.11 / §7.10 (decision reversed here)
