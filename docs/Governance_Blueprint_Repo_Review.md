# Governance Blueprint — Repository Review and Required Amendments

Reviewed against the working tree on 2026-08-05 (branch `main`, HEAD `aaec646 "Semi-finished"`, 38 dirty paths, no git remote). Companion to [Fullstack_Agentic_Governance_Blueprint.md](Fullstack_Agentic_Governance_Blueprint.md) — the verified comparison, issue analysis, and amendment record whose contents are incorporated in the blueprint's final draft. Kept as the evidence trail (code citations, severity reasoning) behind that document.

## 1. Verdict

The blueprint is architecturally sound and its core invariants (Redis map, embedding contract, bucket separation, GitHub MCP read-only) are **verified correct against code**. But it was written as if governance starts from zero, and it models the wrong MCP architecture. The repository already contains roughly a third of the plan's Phase 0/1/5/10 machinery (generator provenance, preflight checks, Compose-matrix validation, CI, pre-commit, an AGENTS.md); the plan must *absorb and extend* that machinery or it creates a second, competing control plane — the exact failure mode ("accidental second source of truth") the blueprint warns against. Second, the plan's MCP/security model assumes container-hosted MCP sidecars; the real privileged-tool surface is mostly **per-user OAuth connections stored in database rows** — some with live credentials embedded in the URL itself — which static extraction cannot see and directory-level `.governance.json` files cannot describe; runtime evidence therefore becomes load-bearing and needs data-governance rules of its own. Third, several concrete example paths and policies reference structures that do not exist. All fixable; amendments in §5.

## 2. Blueprint claims verified correct

| Blueprint claim | Repository evidence | Note |
|---|---|---|
| Redis DB 0 general / 1 Taskiq broker / 2 results / 3 embedding cache | `backend/app/core/config.py:165,184,185,272`; consumed in `app/worker/taskiq_app.py:10,17`, `app/services/rag/embedding_cache.py:84` | Exact match |
| Qwen3 embedding, dimension 1024 | `config.py:263-266` (`docker.io/ai/qwen3-embedding:latest`, `4B-Q4_K_M`, rev `731f733db2ef`, dim 1024) | Already **triple-enforced**: settings, `vectorstore.py:179-182` hard guard + `vector(1024)` DDL + HNSW index, and migration `0027` `CHECK (dimensions = 1024)`. Per-collection `embedding_fingerprint` persisted and validated on open (`vectorstore.py:212-221`) |
| General vs RAG buckets distinct | `S3_BUCKET="fullstack"` (`config.py:191`) vs `S3_RAG_BUCKET="fullstack-rag"` (`config.py:319`), separate credentials/endpoints | boto3 S3-compatible; no `minio` package, no `MINIO_*` app vars |
| GitHub MCP read-only with tool allowlist | Enforced at **three layers**: container flags (`--read-only --tools …`, `GITHUB_READ_ONLY=1` in `docker-compose.yml`), settings validator (`GITHUB_MCP_READ_ONLY_TOOLS` frozenset, `config.py:17,251`), runtime post-probe assert (`app/agents/mcp.py:235-242`) | Governance should *extract and cross-check* this, not re-declare it |
| Pin production images by digest | Every third-party image in all four compose files is SHA256-pinned | Already done |
| Health checks + persistent volumes where expected | Core services all have healthchecks; Taskiq worker/scheduler deliberately disabled | Model the deliberate exceptions |
| Compose profile matrix validation | `make compose-check` / `compose-check-prod` already validate every profile permutation | Extend, don't rebuild |
| IIS / port-80 environment | Documented in `MANUAL_STEPS.md`; prod Traefik uses `${TRAEFIK_HTTP_PORT:-80}` | Real constraint |
| Taskiq explicitness (1 worker, `TASKIQ_MAX_ASYNC_TASKS=1`) | `config.py:186`, compose worker command, MANUAL_STEPS checklist | Confirmed |
| Logfire as runtime evidence source | `app/core/logfire_setup.py` instruments FastAPI/asyncpg/redis/httpx/pydantic-ai | Phase 8 feasible |
| Evaluations via structured scenarios | `backend/evals/google_workspace_tools.py` already uses **pydantic-evals** (10 deterministic routing/approval cases), run in normal CI via `tests/test_google_workspace_evals.py` | The evaluation mechanism has a working beachhead |

## 3. Where the repository contradicts the blueprint

### 3.1 Example paths that don't exist

- `backend/app/rag/` → actually **`backend/app/services/rag/`** (thick subpackage: `vectorstore.py`, `embeddings.py`, `embedding_cache.py`, `ingestion.py`, `documents.py`, `connectors/`, `sources/`). Entry points are `app/api/routes/v1/rag.py` + flat services `rag_sync.py`, `rag_document.py`, `rag_status.py` — not `router.py`/`service.py` inside the package.
- `frontend/src/features/chat/` → **no `src/features/` exists.** The chat feature spans four roots: `src/app/[locale]/(dashboard)/chat/`, `src/components/chat/` (25 files + `tool-results/`), `src/hooks/use-chat.ts`, and five chat-related Zustand stores in `src/stores/`.
- `infra/mcp/github/` → **no `infra/` exists.** MCP sidecar build contexts live at `docker/mcp/docling/` and `docker/mcp/chrome-devtools/`; **`github-mcp` is image-only** (no directory anywhere to hold a `.governance.json`).
- Example `"validation": ["uv run pytest tests/rag"]` → backend tests are flat `tests/test_*.py`; no `tests/rag/` directory.
- Only two thick service domains exist: `services/rag/` and `services/email/`. There is **no `services/channels/` and no `services/billing/`** (billing was generated disabled: schema migrations 0010–0015 exist, models/services do not).

### 3.2 MCP is one connection abstraction with orthogonal dimensions

The blueprint's MCP policy assumes container MCP services. In reality "MCP connection" is a single storage/UI abstraction whose instances vary along independent axes — **provisioning** (deployment-managed vs. per-user), **executor** (generic MCP client vs. native Google REST toolsets), transport, authentication, allowlist source, and approval policy — so the manifest should model these as orthogonal fields rather than mutually exclusive tiers. The concrete configurations today:

1. **Deployment-managed servers** — `MCP_SERVERS` JSON env (`config.py:225`), validated at startup (duplicate names, GitHub allowlist), probed with `tools/list` before every turn (3 s budget, unreachable → skipped), tools name-prefixed and deduped. The compose `mcp`-profile containers (`docling-mcp`, `chrome-devtools-mcp` + `browserless`, `github-mcp`) are just the usual *targets* of these URLs — the backend only ever speaks HTTP; no stdio, no process spawning.
2. **Per-user connections** — `mcp_connections` table (migration 0026): per-user URL, bearer/OAuth 2.1+PKCE with dynamic client registration (`app/agents/mcp_oauth.py`), Fernet-encrypted tokens, per-connection `allowed_tools`, enable flag, health status. These privileged edges **live in database rows, not in any file** — static extraction cannot enumerate them.
3. **Google REST executor** — `build_toolsets_for_user` (`app/services/mcp_connection.py:463-482`) routes recognized Google URLs to local `FunctionToolset`s built from a data registry (`app/agents/google_apis/products.py`, `google_workspace_api.py`): the connection/UI abstraction is deliberately retained while execution goes to Google's REST APIs (the module docstring is explicit that Google's preview MCP servers are intentionally not used). This migration is in flight — compat env aliases (`GOOGLE_WORKSPACE_MCP_*`, `GOOGLE_DRIVE_*`) still exist in `config.py:140-145`.

Four security-model facts the plan must encode explicitly:

- **Tool approval is asymmetric.** Human-in-the-loop gating uses pydantic-ai deferred tools (`agent_session.py:304-378` pauses the run, emits `tool_approval_required` over WebSocket). It covers **Google mutation tools only** (`products.py:269-276`, Gmail send/draft/delete in `google_workspace_api.py:344-346`). **MCP-sourced tools are never approval-gated.** The blueprint's "write-capable tools require explicit human authorization policy" is currently true for Google and false for arbitrary user-added MCP servers — this is precisely the kind of declared-vs-observed gap the governance system exists to surface, so state it as a known finding, not an assumption. Because a connection with `allowed_tools` unset exposes **every** tool the server advertises, write-capable arbitrary servers combine full tool exposure with no approval gate — a combination that warrants an explicit product-security decision (approval parity, default allowlists, or a documented restriction), not just an accepted-risk ADR.
- The catalog is **frontend-side** (`frontend/src/lib/mcp-catalog.ts` + `mcp-logos.generated.ts`); the backend has URL→kind/scope maps (`google_workspace_api.py:38-58`). Catalog-vs-backend-map agreement is a cross-file contract the interfaces manifest should check.
- **URL validation is write-time, not connection-time.** `validate_mcp_url` (SSRF check; resolves DNS) runs at connection create/update (`mcp_connection.py:187,220`), OAuth start (`:314`), and on each OAuth HTTP request (`mcp_oauth.py:89,319`) — but the per-turn probe and toolset attach open the *stored* URL directly (`mcp.py:118-147,190-199`) with no revalidation, leaving a validation-to-connection window (DNS rebinding). Record this as a current security gap, not a protected invariant; closing it means revalidating at connect time and preferably network-level egress controls.
- **Connection URLs can carry live credentials.** The catalog supports `tokenPlacement: "url"` — e.g. Alpha Vantage `https://mcp.alphavantage.co/mcp?apikey={token}` (`mcp-catalog.ts:331`) — and the `url` column is stored unencrypted (unlike `auth_token`, which is Fernet-encrypted) and returned verbatim by `McpConnectionRead.url`. Anything that touches connection URLs — manifests, graph edges, logs, runtime evidence — must strip query strings and userinfo first.

### 3.3 Docling has two distinct paths

Ingestion and chat uploads use a **direct HTTP client** to Docling Serve (`DoclingServeParser`, `app/services/rag/documents.py:362-402`; chat singleton in `file_upload.py:28-41`). The `docling-mcp` sidecar (which does use `DOCLING_CONVERSION_MODE: remote` → Docling Serve, as the blueprint says) is only an **agent tool source** behind the `mcp` compose profile. The Docling policy must cover both paths and require they agree on `DOCLING_SERVE_URL`/timeouts.

### 3.4 Embedding cache is two-level

`EmbeddingCache` is "Redis L1 backed by a durable PostgreSQL pgvector L2" (`embedding_cache.py:66-84`), with versioned keys hashing `{dimensions, input_kind, instruction, normalized_input, model_id, model_version}`. The blueprint's data-store model ("Redis DB 3 embedding cache") captures only L1. The L2 table and the cache-key fingerprint are part of the embedding-dimension contract.

### 3.5 The frontend is a BFF; chat is WebSocket

- **~60 Next.js route handlers under `src/app/api/**` proxy every REST call**; the browser never hits FastAPI directly (`src/lib/api-client.ts` → `/api/...`; server-side `src/lib/server-api.ts` → `BACKEND_URL`). The blueprint's site graph (page → API client → FastAPI route) is missing a mandatory hop: page → client → **proxy route handler** → FastAPI route. Impact analysis that skips the proxy layer will report wrong blast radii. The proxy→backend mapping is string-literal (`backendFetch("/api/v1/…")`) — statically extractable.
- **Chat streaming is WebSocket** (`/api/v1/ws/agent`, `app/api/routes/v1/agent.py:30`), the one place the browser talks to the backend origin directly, with the access token passed as a WS **subprotocol** (deliberate: keeps it out of logs). SSE exists only for RAG ingestion status (`rag.py:386`). WS events have **no formal schema** — they are ad-hoc `{type, data}` dicts produced in `agent_session.py`/`agent.py` and consumed in `use-chat.ts`. "WebSocket event schemas" cannot be *referenced* (blueprint §Interfaces) because none exist; enumerating them is a governance deliverable, not an extraction.
- i18n routing uses next-intl with `[locale]` + `localePrefix: "as-needed"` (`en`, `pl`) — route extraction must normalize the locale segment.

### 3.6 Governance-adjacent machinery already exists

| Blueprint proposes | Already in the repo |
|---|---|
| Phase 0: record generator version/options | `.fastapi-fullstack.json` — template ref `0.2.17`, `generated_at`, `context_hash`, full ~200-key generation context (`enable_billing: false`, `tenancy: single`, …); also `[tool.fastapi-fullstack]` in `backend/pyproject.toml` |
| Phase 10: generator-upgrade workflow | `make upgrade / upgrade-dry-run / upgrade-new-features / upgrade-finalize`, with gitignored `.pending`/`.candidate` transient manifests |
| `governance preflight` | `make preflight-volumes / preflight-model / preflight-ports / preflight-edge-ports / preflight-mcp` (probes DMR embeddings + reranker, checks external volumes, port conflicts) |
| Compose profile/override validation | `make compose-check` / `compose-check-prod` over the canonical stacks `COMPOSE_BASE/DEV/FRONTEND/PROD` |
| Phase 5 CI | `.github/workflows/ci.yml`: ruff + ty, pytest with real Postgres/Redis services + coverage→Codecov, frontend lint/type-check/vitest/Playwright, pip-audit, Trivy (currently `exit-code: 0`, i.e. advisory) |
| Pre-commit | `backend/.pre-commit-config.yaml` (ruff, ty, hygiene hooks) + frontend husky/lint-staged |
| Concise `AGENTS.md` | Exists at root since generation (66 lines, targets "Codex, Copilot, Cursor, Zed, OpenCode") — but it is a **near-duplicate of `CLAUDE.md`**, not a routing entry point, and neither references the other |
| Generated/curated separation precedent | `frontend/src/lib/mcp-logos.generated.ts` + `gen:mcp-logos` script |
| Evaluation records | `backend/evals/` with pydantic-evals (declared dep `pydantic-evals>=2.21.0`) |

### 3.7 Compose is a file-stack × profile matrix

Four files — `docker-compose.yml` (base, staging-ish, **zero published host ports**, everything `expose` + internal networks), `docker-compose.dev.yml` (publishes all ports on `127.0.0.1`), `docker-compose.prod.yml` (Traefik edge, resource limits, `${VAR:?}` secret guards), `docker-compose.frontend.yml` (compat shim) — combined with profiles `frontend`, `mcp`, `db-ui`, `edge`. The canonical combinations are defined in the **Makefile**, which the blueprint's authority table omits entirely. Also invisible to pure Compose extraction:

- **Docker Model Runner is a host-level runtime**, not a service: `EMBEDDING_BASE_URL: http://model-runner.docker.internal/engines/v1` (host: `localhost:12434`). The services manifest needs a "host runtime" node type or the embedding/reranker dependency edge simply vanishes.
- **External preservation volumes** `redis-data` and `docling-models` (`external: true`) — bootstrap must treat them as pre-existing state to protect (the Makefile's `preflight-volumes` already does).
- Network security posture worth encoding as the invariant: `data-internal` is `internal: true` (no egress); base file publishes no host ports at all. The plan's "detect duplicate host ports" check is near-vacuous on the base file; the valuable invariant is *"base/prod publish nothing except Traefik; dev binds only to 127.0.0.1."*

### 3.8 Smaller factual corrections

- **Alembic**: 26 revisions in `backend/alembic/versions/` with **filename numbering gaps** (no 0017, 0019–0021; an interleaved `0004_5`). A "contiguous chain" policy keyed to filenames fails on day one; contiguity must mean *revision-graph linearity* (single head, no orphans via `down_revision` links).
- **Tests**: async via **anyio** (not pytest-asyncio); DB is a mocked `AsyncMock` session + `httpx.AsyncClient(ASGITransport)`; migration tests self-skip without a live DB (CI has one); destructive live Google tests are gated by env vars (`GOOGLE_LIVE_E2E=1`, plus a second gate to actually send email), not markers; **no custom pytest markers are registered**. Coverage is pinned at `fail_under = 100` (backend) and 100% thresholds (frontend vitest) — so the blueprint's "centrality × weak line coverage" risk analysis has almost no signal here.
- **Configuration**: `Settings` has computed fields (`DATABASE_URL`, `REDIS_URL`, the whole `rag` object) — "declared vs consumed" mapping is two-hop through nested config objects, not direct env-name references. Secrets are inconsistently typed (`GOOGLE_API_CLIENT_SECRET` is `SecretStr`, `GOOGLE_CLIENT_SECRET` is plain `str`) — secret classification must be curated, not inferred from types. Deliberate deprecated aliases exist and need an "alias-of" classification.
- **`.env` handling**: `backend/.env` and `frontend/.env.local` exist on disk with real values; both verified gitignored and untracked. But `find_env_file()` (`config.py:40-47`) walks parent directories, and `app/main.py:208` calls `setup_logfire()` at **module import scope** (`send_to_logfire="if-token-present"`).

## 4. Issues that will occur if the plan is implemented as outlined

Ordered by severity.

- **I1 — Secret/telemetry hazard in extraction (high).** Any extractor or OpenAPI generator that *imports* `app.core.config` or `app.main` will load real secrets from `.env` (parent-directory search) and can emit telemetry to Logfire at import time. The blueprint's "no secret values in manifests" is necessary but not sufficient — the extraction *process* itself must be isolated.
- **I2 — Wrong MCP threat model (high).** Policies written for container MCPs miss the actual privileged surface: per-user DB-resident connections, OAuth-discovered endpoints, the Google REST executor path, and the approval-gating asymmetry (§3.2). The agent/tool/permission graph as drafted would show `github-mcp` (already triple-locked) and be blind to arbitrary user-added write-capable servers.
- **I3 — Runtime evidence can leak tenant data and credentials (high).** Phase 8 evidence (per-user connections, Logfire traces, prompts) is environment-specific, nondeterministic, and potentially personal — and because catalog connections can embed API keys in URLs (§3.2), potentially credential-bearing. As drafted, nothing prevents it from landing in committed manifests or change records. Runtime evidence needs its own rules: never committed, credentials/query-strings stripped at capture, environment-scoped TTL-bound storage, read-only aggregated DB access (A16).
- **I4 — Baseline over a mid-flight repo (medium-high).** 38 dirty paths, an in-progress MCP→Google-REST migration with compat aliases, HEAD "Semi-finished". A Phase 0 baseline taken now bakes transitional state into "expected architecture," and drift detection screams from day one — the fastest way to get governance ignored.
- **I5 — Second control plane (medium).** Building `governance preflight`/`check` beside the existing Makefile preflights, `compose-check`, CI, and upgrade machinery creates two overlapping enforcement systems whose *mutual* drift becomes a new failure mode.
- **I6 — No enforcement substrate for PR CI (medium).** There is no git remote; the plan's pull-request gates (Phase 5 items 5, 10) have nothing to attach to and need a local equivalent until one exists. Separately, local-only history is a durability risk for a system whose point is preserving change records — flag it as a decision for the repo owner rather than a governance mandate.
- **I7 — Day-one false-positive policies (medium).** Filename-based migration contiguity (§3.8); orphan detection flagging the deliberately disabled billing schema (migrations exist, code doesn't); duplicate keys already present in `.env.example`; "coverage-weakness" analytics against pinned-100% suites.
- **I8 — Instruction-surface conflict (medium).** Adding governance rules to `AGENTS.md` while `CLAUDE.md` + `.claude/rules/*` independently restate conventions creates three partially-overlapping agent rulebooks. Also note both `AGENTS.md` and `CLAUDE.md` are *generator-owned* — a template upgrade may rewrite them over local governance edits.
- **I9 — CLI packaging undefined for this layout (medium).** There is no root `pyproject.toml`; `uv run governance` resolves against nothing. Adding the tool to `backend/` couples governance to app dependencies and imports (see I1) — wrong direction.
- **I10 — Arbitrary validation commands (medium).** `.governance.json` `validation` lists as drafted are free-form shell strings — an annotation could carry anything from a destructive env-gated suite (`tests/live/` sends real email; an agent following "run required validation" can be induced to set the gate) to an outright malicious command. A written "non-destructive only" rule doesn't verify itself; annotations should carry trusted validator IDs resolved through a reviewed registry instead of executable text (A2).
- **I11 — Boundary schema needs multi-root and non-directory support (medium).** Directory-scoped `.governance.json` cannot by itself describe `github-mcp` (image-only, no directory), the chat feature (spans four frontend roots), Docker Model Runner (host runtime), or per-user connections (DB rows). The curated manifests are the right home for these, but the annotation/component schema must explicitly support repo-root-relative multi-root `owns` and non-directory component declarations (A2) — otherwise those boundaries silently go undescribed.
- **I12 — Windows determinism unstated (low).** Generated-file byte-identity ("two syncs produce identical bytes") needs explicit LF-only + UTF-8-no-BOM writes, forward-slash path normalization, and `os.replace` atomic swaps, or idempotence checks flap between machines/editors.
- **I13 — Hygiene gaps (low).** `artifacts/` is not gitignored (plan assumes excluded); `.uv-cache/` at root and `backend/` is untracked-but-unignored; pre-commit exists only backend-scoped while governance checks are repo-wide.

## 5. Amendments to the blueprint

Keyed to blueprint sections; apply these when revising the draft.

### A1. Authority table (§Design principles) — add missing authoritative sources

| Information | Authoritative source | Governance treatment |
|---|---|---|
| Operational stacks, preflights, upgrade workflow | `Makefile` (PowerShell-based, Windows-first) | Extract targets + canonical compose stacks; governance CLI *wraps* these, never duplicates |
| CI matrix | `.github/workflows/ci.yml` | Extract; process policies reference actual jobs |
| Generator provenance & options | `.fastapi-fullstack.json` (+ `[tool.fastapi-fullstack]`) | Extract; Phase 10 builds on `make upgrade*` and `context_hash` |
| Per-user MCP catalog | `frontend/src/lib/mcp-catalog.ts` | Extract; cross-check against backend URL→kind/scope maps |
| Existing hook layers | `backend/.pre-commit-config.yaml`, frontend husky/lint-staged | Extend, don't add a third framework |
| i18n surface | `frontend/messages/{en,pl}.json`, `src/i18n.ts` | Extract locale list; normalize `[locale]` in route extraction |
| Runtime privileged-tool grants | `mcp_connections` DB table | **Runtime-evidence source** (Phase 8), never statically assumed |

### A2. Child-directory annotations (§Child-directory governance)

- Fix example paths: `backend/app/services/rag/`, `docker/mcp/docling/`, `docker/mcp/chrome-devtools/`.
- **Add a second annotation mechanism**: components without a home directory (image-only compose services like `github-mcp`, host runtimes like Docker Model Runner, DB-resident per-user connections) are declared in `governance/manifests/curated/architectural-intent.json` with the same schema, keyed by stable component ID. Rule: a component is *either* directory-annotated *or* curated-manifest-declared, never both.
- **Allow multi-root `owns`**: the chat component owns `frontend/src/app/[locale]/(dashboard)/chat/**`, `frontend/src/components/chat/**`, `frontend/src/hooks/use-chat.ts`, `frontend/src/stores/chat*.ts` — paths relative to repo root, not the annotation's directory.
- Replace free-form `validation` command strings with **trusted validator IDs** (e.g. `backend-unit`, `compose-matrix`) resolved through a reviewed registry mapping IDs to Make targets or fixed commands; annotations never carry executable shell text. Validators needing live-service env gates (`GOOGLE_LIVE_E2E`, `GOOGLE_LIVE_SEND_E2E`) stay out of the registry and run only via an explicit human-invoked CLI flag.

### A3. MCP policy (§Initial policies → MCPs) — rewrite for the three-tier reality

- Model connections along the orthogonal dimensions per §3.2. Manifest fields: provisioning (`deployment` | `user`), executor (`mcp` | `google-rest`), transport (SSE vs streamable HTTP, inferred from URL), authentication, probe policy, tool prefix, allowlist source (settings validator / per-connection / product registry), approval gating (deferred-tools), encryption at rest.
- Keep the GitHub read-only policy but restate it as *cross-check of three existing enforcement layers* (compose flags ↔ settings frozenset ↔ runtime assert) — drift between the three layers is the finding.
- Add explicit policies: (a) per-user connections are enumerated only from runtime evidence with provenance "database", under the A16 rules; (b) the approval-gating asymmetry (MCP tools bypass deferred-tool approval; `allowed_tools` unset exposes every advertised tool) requires an explicit product-security decision — approval parity, default allowlists, or a documented restriction — before MCP policies are promoted past advisory; an accepted-risk ADR alone is not sufficient for write-capable servers; (c) frontend catalog entries must map to a backend `google_api_kind` or documented generic handling; (d) URL validation is recorded as **write-time only**, and the missing connection-time revalidation (§3.2) is tracked as an open security finding with remediation options (revalidate at probe/attach; network-level egress controls).
- Record the compat-alias envs (`GOOGLE_WORKSPACE_MCP_*`, `GOOGLE_DRIVE_*`) as `deprecated-alias-of: GOOGLE_API_*` with a removal milestone.

### A4. Docling policy (§Initial policies → Docling)

Cover both paths: (1) ingestion/chat-upload via `DoclingServeParser` (retry set, 50 MB cap, page-break sentinel contract); (2) the `docling-mcp` agent sidecar in remote mode. Invariant: both resolve to the same Docling Serve instance and compatible timeout/retry budgets.

### A5. Data stores (§Manifest model → Data stores)

Add: embedding cache **L2** (PostgreSQL pgvector cache table) and the versioned cache-key fingerprint as part of the dimension contract; external volumes (`redis-data`, `docling-models`) classified `external/preserve`; the per-collection `embedding_fingerprint` validation; Redis policy unchanged (verified).

### A6. Interfaces (§Manifest model → Interfaces) and site graph (§Logical graph views)

- Insert the **Next.js proxy route handler** as a first-class node between frontend API client and FastAPI route; extract the mapping from `backendFetch`/`fetch` string literals.
- WebSocket: declare `/api/v1/ws/agent` (direct-to-backend, token-as-subprotocol) as the one non-proxied browser edge; **generate** the WS event inventory from `agent_session.py`/`agent.py` producers and `use-chat.ts` consumers — producer/consumer set difference is the compatibility check. SSE: RAG status stream only.
- OpenAPI extraction must run in an **isolated subprocess with a scrubbed environment** (no `.env` on the search path, `LOGFIRE_TOKEN` unset, `ENVIRONMENT=local`) because settings loading walks parent dirs and Logfire configures at import (I1). No existing code provides this isolation — the OpenAPI tests import `app.main` in-process — so a small dedicated exporter that dumps `app.openapi()` to a file is required.

### A7. Configuration (§Initial policies → Configuration)

- **Never import application settings in-process.** AST extraction (names, types, defaults, validators) is the primary method, cross-checked textually against `.env.example`; where computed fields or validators resist static resolution, run the same scrubbed-subprocess isolation as the OpenAPI exporter (A6) and compare against the AST result. (Direct consequence of I1.)
- Add classifications: `computed` (not env-settable: `DATABASE_URL`, `REDIS_URL`), `deprecated-alias`, `secret` (curated list — type-based inference is unreliable here), `build-arg` (frontend `NEXT_PUBLIC_*`, baked at image build) vs `runtime`.
- Add the frontend configuration surface (`frontend/.env.example`, compose build args, `process.env` references) — the draft is backend-only.
- Duplicate keys in `.env.example` (already present: `RAG_CHUNK_SIZE` block appears twice) → deterministic last-wins parse + a finding, not a crash.
- Make **`ENV_VARS.md` a generated renderer output** — it has already drifted (documents `DATABASE_URL`, `S3_ENDPOINT_URL`, `GOOGLE_OAUTH_*` that aren't template vars; a broken table header; a `jw` placeholder cell). This is the single best early proof of the whole approach.

### A8. PostgreSQL/pgvector (§Initial policies)

Restate dimension policy as a **five-point agreement check**: settings `EMBEDDING_DIMENSION` ↔ vectorstore guard/DDL ↔ migration CHECK ↔ persisted per-collection fingerprint ↔ cache-key version. Migration policy: "contiguous" = an acyclic, connected revision graph whose head set matches an explicitly recorded expectation — branches and merge revisions stay legal, and single-head becomes a rule only if adopted as deliberate policy. Document the existing filename numbering gaps as accepted so the policy doesn't fire on history.

### A9. Compose/networking (§Initial policies)

- Model the **file-stack × profile matrix**, taking canonical stacks from the Makefile; wrap `compose-check` rather than reimplementing.
- Replace "detect duplicate host ports" emphasis with the actual invariants: base/staging publish **zero** host ports; dev binds only `127.0.0.1`; prod publishes only Traefik 80/443 (+ loopback dashboard); `data-internal` stays `internal: true`.
- Add a `host-runtime` service node for Docker Model Runner (embeddings + reranker), with its own preflight (`make preflight-model`) as the check.

### A10. Tests manifest (§Manifest model → Tests)

Line coverage is pinned at 100% on both sides — pivot the risk metric to **invariant/scenario coverage**: which declared invariants have a dedicated test (dimension guard → `test_rag_docker_integration.py`; approval routing → `evals/google_workspace_tools.py`; migrations → `test_migrations.py`, DB-gated). Record test frameworks precisely (anyio, mocked-session unit tests, env-gated live suites, vitest, Playwright) and the gating env vars as metadata so impact analysis never selects a destructive suite.

### A11. CLI packaging and enforcement (§Governance CLI, §Automated enforcement)

- `tools/repo_governance/` becomes a **standalone uv project** (own `pyproject.toml`, `uv.lock`, `.venv`; Python ≥3.12) that never imports `app.*`. Invocation: `uv run --project tools/repo_governance governance …`, wrapped as `make governance-check` / `governance-context` etc. so the Makefile stays the single operational entry point.
- Pre-commit: either promote `backend/.pre-commit-config.yaml` to the repo root (pre-commit supports per-hook `files:` scoping) and add governance hooks there, or run governance fast-checks from husky + Makefile. Pick one; do not introduce a third hook framework.
- Phase 5 CI = a new job in the existing `ci.yml`. Until a git remote exists, "PR CI" gates run as `make governance-check` locally; **recommend establishing a remote (even private)** — for enforcement substrate and durability — as a repo-owner decision recorded during Phase 0.
- Windows determinism (I12): all generated files written LF-only, UTF-8 without BOM, normalized forward-slash paths, `os.replace` atomic swaps; add a `.gitattributes` entry pinning generated governance files to LF.

### A12. AGENTS.md and instruction surfaces (§Governance documents)

Phase 1 changes from "Add concise AGENTS.md" to **"Converge the existing instruction surfaces"**: rewrite `AGENTS.md` as the routing entry point (it currently duplicates `CLAUDE.md`); slim `CLAUDE.md` to Claude-specific deltas + a pointer; keep `.claude/rules/*` as the path-scoped detail layer (it already implements the blueprint's progressive-disclosure principle — reuse it, and have `governance context` return rule-file references rather than restating them). Record in curated ownership that `AGENTS.md`/`CLAUDE.md` are generator-provided with local divergence, so `make upgrade` conflicts are classified instead of clobbering.

### A13. Orphan/dead-code analysis (§Graph analysis, §Scheduled analysis)

Add a `generator-provided, feature-disabled` classification sourced from `.fastapi-fullstack.json` context (e.g. `enable_billing: false`): billing migrations/tables and similar surfaces are expected-present-unused, not orphans. Also register known generated files (`mcp-logos.generated.ts`, lockfiles) in the generated-file registry for direct-edit detection.

### A14. Extraction realism (§Repository graph → Static and runtime evidence)

Add to the known-limits list, with the mitigation for each: pydantic-ai toolsets built from **data registries** (`DIRECT_GOOGLE_PRODUCTS`, 1281-line table — tool nodes are data, extract the registry, not the AST); CLI commands auto-discovered via `pkgutil` (`app/commands/__init__.py`); Taskiq tasks registered by import side-effect (`taskiq_app.py:44-45`); WS events as untyped dicts (§A6); per-user MCP edges (runtime-only, §A3).

### A15. Phasing adjustments (§Simplified implementation strategy)

- **Phase 0 pre-step (new):** land or stash the in-flight MCP→Google-REST work and take the baseline on a clean tree; where that's impractical, baseline a named commit plus an explicitly recorded working-tree patch so drift stays explainable. Flag the missing git remote as a durability decision for the repo owner — recommended, but an ownership call, not a governance mandate. (Addresses I4/I6.)
- **Phase 0 output additions:** the authority-matrix rows from A1; the generated-files registry seed; `.gitignore` additions (`artifacts/`, `.uv-cache/`).
- **Phase 1:** kernel CLI `preflight` *wraps* the Makefile preflights and adds schema/ownership checks on top.
- **Phase 3 quick win:** generated `ENV_VARS.md` + configuration manifest first — it fixes live drift on day one and exercises the whole extract→sync→check loop on the least risky surface.
- **Phase 5:** extend `ci.yml`; unify pre-commit per A11. The existing Trivy job (`exit-code: 0`) is a ready-made example of the advisory→blocking policy-maturity ladder — promote it through the governance lifecycle as the pilot.
- **Phase 8 addition:** per-user MCP connection inventory compared against the frontend catalog and the approval-gating map — collected under the A16 evidence rules (URLs stripped of query strings and credentials, aggregate counts, ephemeral environment-scoped storage, never committed).
- **Phase 10:** build on `make upgrade*` + `.fastapi-fullstack.json` (`context_hash`, `.pending`/`.candidate` files) instead of a parallel mechanism.

### A16. Runtime-evidence data governance (§Static and runtime evidence, Phase 8)

Runtime evidence is environment-specific, nondeterministic, and can contain tenant data — and per-user connection URLs can contain live credentials (§3.2). Rules:

- Runtime-derived records never enter committed manifests, graph reports, or change records; committed artifacts may reference evidence only by ID and summary.
- Strip query strings, userinfo, and known credential patterns from every URL at capture time, before storage or logging.
- Evidence stores are environment-scoped, TTL-bound, and live under `.cache/repo-governance/` (already gitignored).
- Database access for evidence collection is read-only and produces aggregated results (counts, kinds, health states), never row dumps.
- PII, prompt contents, and Logfire-derived payloads are governed explicitly: collected only when a named policy requires them, redacted to the minimum the check needs.

## 6. Repository findings surfaced in passing (worth fixing regardless of governance)

1. **`ENV_VARS.md` drift** — documents variables that don't exist in `.env.example` (`DATABASE_URL`, `DB_POOL_SIZE`, `S3_ENDPOINT_URL`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `BACKEND_URL`, `FRONTEND_URL`, `REFRESH_TOKEN_EXPIRE_MINUTES`), has a table missing its header row, and a `jw` placeholder cell.
2. **`backend/.env.example` duplicate keys** — `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`, `RAG_DEFAULT_COLLECTION`, `RAG_TOP_K`, `PDF_PARSER`, `CHAT_PDF_PARSER`, `LLAMAPARSE_API_KEY` each appear twice.
3. **`TIMEZONE: str = "EDT"`** (`config.py:66`) — the comment demands an IANA zone; `EDT` isn't one (use `America/New_York`).
4. **`.gitignore` gaps** — `artifacts/` and `.uv-cache/` are not ignored; a root-level `node_modules/` would be committed (only `frontend/.gitignore` covers it).
5. **No git remote** — the entire repo (2 commits + 38 dirty paths) lives on one disk.
6. **`AGENTS.md`/`CLAUDE.md` duplication** — neither references the other; conventions are restated in both plus `.claude/rules/*`.
7. **Trivy scan never fails the build** (`exit-code: 0` in `ci.yml`) — fine as advisory, but undocumented as a deliberate choice.
8. **MCP tools bypass the approval dialog**, and a connection with `allowed_tools` unset exposes every tool the server advertises (§3.2) — needs an explicit product-security decision, independent of governance tooling.
9. **No connection-time SSRF revalidation** — `validate_mcp_url` runs at connection create/update and during OAuth flows, but the per-turn probe and toolset attach open stored URLs directly (`app/agents/mcp.py:118-147,190-199`), leaving a validation-to-connection (DNS-rebinding) window.
10. **URL-embedded connection credentials are stored unencrypted and echoed back** — catalog entries with `tokenPlacement: "url"` (e.g. Alpha Vantage `?apikey={token}`) put a live key in the `url` column, which is not encrypted (unlike `auth_token`) and is returned verbatim by `GET /me/mcp-connections`. Consider storing the token separately and substituting it at connect time.
