# Full-Stack Agentic Harness Governance Blueprint

## Executive summary

Build the governance system as a small repository control plane, not as a parallel copy of the application. Existing project files remain authoritative: Docker Compose describes services, Pydantic settings describe backend configuration, Alembic describes database evolution, OpenAPI describes HTTP contracts, and package lockfiles describe dependencies. Governance extracts and connects those facts, adds human intent that code cannot express, validates cross-file invariants, and gives AI agents a compact task-specific view.

This repository is not a blank slate. It already carries a meaningful fraction of the machinery this plan needs: generator provenance in `.fastapi-fullstack.json`, operational preflights and Compose-matrix validation in the `Makefile`, CI in `.github/workflows/ci.yml`, pre-commit and husky/lint-staged hooks, a generated `AGENTS.md`, and working pydantic-evals in `backend/evals/`. Governance **wraps and extends** that machinery; it never builds a second, competing control plane whose drift against the first would itself become a failure mode.

The system has seven cooperating parts:

1. A converged `AGENTS.md` entry point (rewritten from the existing generated file, with `CLAUDE.md` and `.claude/rules/*` layered under it rather than duplicating it).
2. JSON Schemas that define governance document structure.
3. Generated, curated, and merged manifests.
4. Component boundaries: optional child-directory annotations, plus curated declarations for components that have no home directory (image-only services, host runtimes, database-resident connections).
5. A Python governance CLI — a standalone uv project under `tools/` that scans, synchronizes, validates, queries, and records changes, and never imports application code.
6. A typed repository graph built from ASTs and structured configuration, stored as a generated query cache rather than injected into agent context.
7. Automated local and CI workflows — extensions of the existing Makefile, pre-commit, and CI — enforcing drift detection, architecture, security, compatibility, history, and self-governance.

The manifests are the repository's machine-readable index, not the source of truth for facts already owned elsewhere. The graph is derived intelligence, not policy. `Summary.md` is generated from structured change records, while ADRs preserve durable architectural decisions. Runtime evidence is collected under explicit data-protection rules and never committed. AI agents retrieve bounded context through the CLI instead of traversing the entire governance directory.

## Design principles

### 1. Preserve authoritative sources

Do not maintain the same fact manually in multiple places. Extract or reference it from its owner.

| Information | Authoritative source | Governance treatment |
| --- | --- | --- |
| Containers, networks, ports, profiles, volumes | Compose file-stack × profile matrix (`docker-compose.yml` + `dev`/`prod`/`frontend` overrides; canonical stacks defined in the `Makefile`) | Extract and normalize per stack |
| Operational stacks, preflights, upgrade workflow | `Makefile` (PowerShell-based, Windows-first) | Extract targets; the governance CLI wraps these, never duplicates them |
| CI matrix | `.github/workflows/ci.yml` | Extract; process policies reference actual jobs |
| Generator provenance and options | `.fastapi-fullstack.json` (+ `[tool.fastapi-fullstack]` in `backend/pyproject.toml`) | Extract; the upgrade workflow builds on `make upgrade*` and `context_hash` |
| Backend configuration types and defaults | Pydantic settings (`backend/app/core/config.py`) | Extract via AST and cross-check; never import in-process |
| Configuration availability | `backend/.env.example`, `frontend/.env.example`, Compose build args | Extract names only; never values |
| Python dependencies | `backend/pyproject.toml` and `backend/uv.lock` | Extract |
| Frontend dependencies | `frontend/package.json` and `frontend/bun.lock` | Extract |
| HTTP contracts | FastAPI/Pydantic and generated OpenAPI (via sanitized exporter) | Reference or extract |
| Database evolution | Alembic revisions (`backend/alembic/versions/`) | Extract revision graph |
| Current ORM model shape | SQLAlchemy models | Extract and compare with migrations |
| Frontend routes | Next.js route structure (normalize the `[locale]` segment) | Extract |
| Frontend→backend mapping | Next.js API proxy route handlers (`frontend/src/app/api/**`) and their `backendFetch`/`fetch` string literals | Extract; the proxy hop is a first-class graph node |
| Background processing | Taskiq configuration and task registration | Extract |
| Agent and tool registration | Agent source, tool data registries, and MCP discovery | Extract statically and verify dynamically |
| Per-user MCP catalog | `frontend/src/lib/mcp-catalog.ts` | Extract; cross-check against backend URL→kind/scope maps |
| Runtime privileged-tool grants | `mcp_connections` database table | Runtime-evidence source only, under the data-protection rules; never statically assumed |
| i18n surface | `frontend/messages/{en,pl}.json`, `frontend/src/i18n.ts` | Extract locale list |
| Existing hook layers | `backend/.pre-commit-config.yaml`, frontend husky/lint-staged | Extend; do not add a third hook framework |
| Runtime behavior | Tests and Logfire traces | Treat as observed evidence, never the only declaration |
| Architectural intent and exceptions | Local annotations, curated manifests, and ADRs | Human/agent authored and reviewed |
| Modification rationale | Structured change record | Human/agent supplied; never inferred solely from a diff |

### 2. Separate three kinds of truth

- **Observed architecture:** extracted statically or seen at runtime.
- **Declared architecture:** component manifests and local annotations.
- **Expected architecture:** policies, invariants, and ADRs.

Differences between them become findings. For example, a runtime MCP edge not declared in the agent-tool manifest is a security finding; a declared dependency never found statically or dynamically is a possible stale annotation.

### 3. Separate generated and curated content

Generated files may be replaced deterministically. Curated intent must never be silently overwritten. The effective repository model is produced by a documented merge with explicit conflicts and exceptions. The repository already has this precedent (`frontend/src/lib/mcp-logos.generated.ts` + its `gen:mcp-logos` script); governance formalizes it with a generated-file registry.

### 4. Use progressive disclosure

Agents read a small entry point, request an impact/context slice, and open only the returned manifests, ADRs, and change records. They never recursively load the governance tree or the full code graph. The existing `.claude/rules/*` path-scoped files already implement this principle for conventions — governance context returns references to them rather than restating their content.

### 5. Make outputs deterministic

Use stable IDs, stable ordering, normalized paths, atomic writes, and no volatile timestamps in ordinary generated manifests. Running synchronization twice without source changes must produce no diff. Because this repository is developed on Windows, byte-identity is specified explicitly: generated files are written LF-only, UTF-8 without BOM, with forward-slash-normalized paths and `os.replace` atomic swaps, and a `.gitattributes` entry pins generated governance files to LF.

### 6. Treat governance itself as governed code

Schemas, policies, extraction logic, ranking weights, and the AI workflow can improve, but changes to them require compatibility checks, evaluation evidence, a governance change record, and an available rollback.

### 7. Absorb, don't duplicate, existing machinery

`make preflight-volumes/-model/-ports/-edge-ports/-mcp`, `make compose-check`/`compose-check-prod`, `make upgrade*`, the CI workflow, and the existing hook layers are authoritative operational checks. Governance commands wrap them and add schema/ownership/policy validation on top. If a governance check and a Makefile check would test the same thing, the Makefile keeps it and governance calls it.

### 8. Isolate extraction from the application runtime

Extractors never import `app.*` in-process. Settings loading walks parent directories for `.env` (`find_env_file()`), and `app/main.py` configures Logfire at module import scope — so importing application modules from tooling can load real secrets and emit telemetry. AST extraction is the primary method; where computed fields, validators, or OpenAPI generation resist static resolution, run an isolated subprocess with a scrubbed environment (no `.env` on the search path, `LOGFIRE_TOKEN` unset, `ENVIRONMENT=local`) and compare its output against the AST result.

## Final repository-root structure

```text
repo-root/
├── AGENTS.md                      # converged entry point (rewritten, not added)
├── governance/
│   ├── catalog.json
│   ├── Summary.md
│   ├── governance.toml
│   ├── validators.json            # trusted validator ID → Make target/command registry
│   ├── schemas/
│   │   ├── catalog.schema.json
│   │   ├── component.schema.json
│   │   ├── service.schema.json
│   │   ├── configuration.schema.json
│   │   ├── data-store.schema.json
│   │   ├── ai-runtime.schema.json
│   │   ├── interface.schema.json
│   │   ├── local-governance.schema.json
│   │   ├── policy.schema.json
│   │   ├── change-record.schema.json
│   │   ├── decision-index.schema.json
│   │   └── graph-model.schema.json
│   ├── manifests/
│   │   ├── generated/
│   │   │   ├── components.json
│   │   │   ├── services.json
│   │   │   ├── configuration.json
│   │   │   ├── data-stores.json
│   │   │   ├── ai-runtime.json
│   │   │   ├── interfaces.json
│   │   │   └── tests.json
│   │   ├── curated/
│   │   │   ├── architectural-intent.json   # includes non-directory component declarations
│   │   │   ├── ownership.json
│   │   │   └── exceptions.json
│   │   └── effective/
│   │       └── repository.json
│   ├── policies/
│   │   ├── architecture.json
│   │   ├── security.json
│   │   ├── compatibility.json
│   │   ├── process.json
│   │   └── self-governance.json
│   ├── history/
│   │   ├── changes/
│   │   │   └── YYYY-MM-DD-change-name.json
│   │   ├── decisions/
│   │   │   └── ADR-NNN-decision-name.md
│   │   └── evaluations/
│   │       └── YYYY-MM-DD-evaluation-name.json
│   ├── graph/
│   │   ├── node-types.json
│   │   ├── edge-types.json
│   │   ├── analysis-policy.json
│   │   └── reports/
│   │       ├── hotspots.json
│   │       ├── cycles.json
│   │       ├── orphans.json
│   │       └── boundaries.json
│   └── views/
│       └── definitions.json
├── tools/
│   └── repo_governance/           # standalone uv project: own pyproject.toml, uv.lock, .venv
│       ├── pyproject.toml
│       ├── uv.lock
│       ├── src/repo_governance/
│       │   ├── __main__.py
│       │   ├── cli.py
│       │   ├── config.py
│       │   ├── models.py
│       │   ├── identifiers.py
│       │   ├── extractors/
│       │   │   ├── python_ast.py
│       │   │   ├── typescript_ast.py
│       │   │   ├── compose.py
│       │   │   ├── configuration.py
│       │   │   ├── openapi.py     # drives the sanitized subprocess exporter
│       │   │   ├── alembic.py
│       │   │   ├── dependencies.py
│       │   │   ├── makefile.py
│       │   │   └── mcp.py
│       │   ├── graph/
│       │   │   ├── builder.py
│       │   │   ├── storage.py
│       │   │   ├── queries.py
│       │   │   └── algorithms.py
│       │   ├── checks/
│       │   │   ├── schemas.py
│       │   │   ├── references.py
│       │   │   ├── architecture.py
│       │   │   ├── security.py
│       │   │   ├── compatibility.py
│       │   │   └── process.py
│       │   └── renderers/
│       │       ├── summary.py
│       │       ├── env_vars.py    # regenerates ENV_VARS.md
│       │       ├── context.py
│       │       └── visualizations.py
│       └── tests/
│           ├── fixtures/
│           ├── golden/
│           └── scenarios/
├── .cache/
│   └── repo-governance/
│       ├── repository.sqlite
│       └── evidence/              # runtime evidence: environment-scoped, TTL-bound
└── artifacts/
    └── governance/
        ├── repository-map.html
        ├── site-map.html
        ├── impact-map.html
        └── security-map.html
```

Commit `governance/` and `tools/repo_governance/` (excluding its `.venv`). Treat `.cache/repo-governance/` and rendered `artifacts/governance/` as reproducible local or CI artifacts and exclude them from version control. Commit only the small graph reports whose diffs are useful during review. Prerequisite hygiene: add `artifacts/` and `.uv-cache/` to the root `.gitignore` (`.cache/` is already covered; `.env` files are already ignored and untracked).

The governance CLI never imports `app.*` — it depends only on its own project. Invocation is `uv run --project tools/repo_governance governance …`, wrapped as `make governance-check`, `make governance-context`, etc., so the Makefile stays the single operational entry point.

The full graph should use SQLite initially. JSON remains appropriate for schemas, manifests, policies, annotations, change records, and small graph reports; it is inefficient for a large symbol-edge graph.

## Component boundaries: annotations and curated declarations

A component is declared in exactly one of two places:

1. **A `.governance.json` child annotation** in its home directory — for components that live in one directory subtree. Do not place one in every directory; only at architectural boundaries: backend subsystems, frontend features, infrastructure groups, MCP sidecar build contexts, persistent-data components, and security-sensitive modules.
2. **A curated declaration** in `governance/manifests/curated/architectural-intent.json` — for components with no home directory: image-only Compose services (`github-mcp`), host runtimes (Docker Model Runner), database-resident surfaces (per-user MCP connections), and multi-root frontend features.

Never both. Real boundary locations in this repository:

```text
backend/app/services/rag/.governance.json      # RAG thick subpackage
backend/app/services/email/.governance.json    # email thick subpackage
backend/app/agents/.governance.json            # agent + toolsets + MCP client
docker/mcp/docling/.governance.json            # Docling MCP sidecar build context
docker/mcp/chrome-devtools/.governance.json    # Chrome DevTools MCP sidecar build context
```

Curated declarations (same schema, keyed by stable component ID): `github-mcp` (image-only service), `model-runner-host` (host runtime serving embeddings + reranker), `mcp-user-connections` (DB-resident), and `chat-frontend`, whose ownership spans four roots — `frontend/src/app/[locale]/(dashboard)/chat/**`, `frontend/src/components/chat/**`, `frontend/src/hooks/use-chat.ts`, `frontend/src/stores/chat*.ts`. All `owns`/`entrypoints` paths are repo-root-relative so multi-root ownership is expressible everywhere.

Recommended local annotation shape (`backend/app/services/rag/.governance.json`):

```json
{
  "$schema": "../../../../governance/schemas/local-governance.schema.json",
  "id": "rag",
  "kind": "backend-subsystem",
  "purpose": "Convert, chunk, embed, persist, retrieve, and rerank documents",
  "entrypoints": [
    "backend/app/api/routes/v1/rag.py",
    "backend/app/services/rag/ingestion.py",
    "backend/app/services/rag/retrieval.py"
  ],
  "owns": [
    "backend/app/services/rag/**",
    "backend/app/services/rag_sync.py",
    "backend/app/services/rag_document.py",
    "backend/app/services/rag_status.py"
  ],
  "public_interfaces": [
    "GET /api/v1/rag/search",
    "POST /api/v1/rag/sync/sources/{source_id}/trigger"
  ],
  "invariants": [
    "Embedding dimension is 1024 end to end: settings, vectorstore DDL, migration CHECK, per-collection fingerprint, cache key",
    "Re-ingestion must preserve stable document identity"
  ],
  "allowed_dependencies": ["docling-serve", "minio", "model-runner-host", "postgres-pgvector", "redis"],
  "forbidden_dependencies": ["frontend"],
  "configuration_refs": ["S3_RAG_ENDPOINT", "S3_RAG_BUCKET", "EMBEDDING_MODEL", "EMBEDDING_DIMENSION"],
  "decision_refs": ["ADR-002"],
  "validation": ["backend-unit", "rag-docker-integration"],
  "graph_hints": {
    "entry_symbols": ["ingest_document_task", "sync_single_source_task"],
    "exclude": []
  }
}
```

Rules for annotations and curated declarations:

- Inherit defaults from the nearest governed ancestor (annotations) or from the manifest defaults (curated declarations).
- Allow only documented fields to override inherited behavior.
- Prohibit a component from weakening security or compatibility policy without an explicit exception record.
- Keep intent and invariants local; do not duplicate facts extractable from source.
- Use stable component IDs independent of directory names.
- Treat file moves as graph identity updates, not new unrelated components.
- `validation` entries are **trusted validator IDs**, never executable shell text. IDs resolve through `governance/validators.json`, a reviewed registry mapping each ID to a Make target or fixed command. Validators that require live-service env gates (`GOOGLE_LIVE_E2E`, `GOOGLE_LIVE_SEND_E2E`) are excluded from the registry and run only via an explicit human-invoked CLI flag.
- Compile all annotations and curated declarations into the effective repository manifest.

## Governance documents and responsibilities

### `AGENTS.md` and the instruction surfaces

An `AGENTS.md` already exists at the repository root (generator-provided, targeting Codex, Copilot, Cursor, Zed, and OpenCode) — but it is a near-duplicate of `CLAUDE.md` rather than an entry point, and neither file references the other. Phase 1 therefore **converges** the instruction surfaces instead of adding a new one:

- Rewrite `AGENTS.md` as the routing entry point — concise, ideally below 150 lines, containing the non-negotiable entry rules below and pointers to everything else.
- Slim `CLAUDE.md` to Claude-specific deltas plus a pointer to `AGENTS.md`.
- Keep `.claude/rules/*` as the path-scoped convention layer; `governance context` returns references to those files rather than restating them.
- Record in curated ownership that `AGENTS.md` and `CLAUDE.md` are generator-provided with local divergence, so `make upgrade` conflicts are classified instead of clobbered.

`AGENTS.md` entry rules:

1. Run the governance preflight.
2. Start a governed change session with a stated reason.
3. Retrieve task-specific context instead of recursively reading governance files.
4. Make the smallest coherent change.
5. Synchronize manifests and graph evidence.
6. Run the registered validators the impact analysis selects.
7. Finalize the change record and regenerate `Summary.md`.
8. Run the read-only full check.

### `catalog.json`

The compact navigation index. For every manifest, policy, graph report, and history class, record its path, description, authority, update method, and normal read conditions. Agents inspect this file before selecting deeper material.

### `Summary.md`

Generate it from structured records. Keep it bounded to:

- current architectural state;
- unresolved warnings and accepted exceptions;
- active compatibility contracts;
- the latest 15–25 material changes;
- links to relevant change records and ADRs.

Do not use it as the permanent complete history. Old records remain queryable under `history/changes/`.

### Change records

Each material governed change should record:

- stable ID and date;
- summary and reason;
- initiating request or issue reference;
- affected components and contracts;
- files and manifests changed;
- behavioral effects;
- validators run and their results;
- security and compatibility impact;
- risks, limitations, and follow-up work;
- rollback procedure;
- ADR references;
- governance schema/tool versions.

Automation can populate changed files, manifests, and test results. It cannot reliably infer the reason; that must be supplied at the beginning or completion of the change.

### ADRs

Use ADRs for decisions that should remain understandable after individual modifications disappear from the recent summary: the remote Docling Serve architecture, the embedding dimension contract, the MCP privilege and approval-gating policy, the Google direct-REST executor decision, service exposure strategy, migration guarantees, and similar durable choices.

### Evaluations

Store evidence about governance behavior rather than ordinary application changes: false-positive reviews, missed dependency edges, context retrieval quality, policy promotion results, ranking calibration, and schema migration tests. Use pydantic-evals — the mechanism already runs in this repository (`backend/evals/` with deterministic cases executed by the normal test suite).

## Manifest model

Begin with seven manifests.

### Components

Logical backend, frontend, agent, RAG, infrastructure, MCP, data, and observability components; owned paths (repo-root-relative, multi-root allowed); entry points; dependencies (including host runtimes); configuration references; contracts; and validator IDs. Components carry a `declared_in` marker (`annotation` or `curated`) and, where sourced from the generator, a `generator_feature` flag (e.g. billing: schema present, feature disabled).

### Services

Resolved across the Compose file-stack × profile matrix (canonical stacks from the Makefile): images and digests, build contexts, profiles, health checks (including deliberate exceptions — Taskiq worker/scheduler healthchecks are disabled by design), dependency ordering, ports per stack, networks, volumes (with `external/preserve` classification for `redis-data` and `docling-models`), GPU requirements, secrets references, and internal versus host exposure. Includes a `host-runtime` node type for Docker Model Runner (embeddings + reranker at `model-runner.docker.internal`), which no Compose file declares.

### Configuration

Environment-variable names, type, requiredness, default classification, secret status, declaring source, consuming components, applicable profiles, and validation rules — for **both** backend and frontend surfaces (`backend/.env.example`, `frontend/.env.example`, Compose build args, `process.env` references). Classifications include: `computed` (settings properties like `DATABASE_URL`/`REDIS_URL` that are not env-settable), `deprecated-alias` (e.g. `GOOGLE_WORKSPACE_MCP_*` and `GOOGLE_DRIVE_*` → alias-of `GOOGLE_API_*`, with a removal milestone), `secret` (a curated list — type-based inference is unreliable because secret fields are inconsistently typed), and `build-arg` (`NEXT_PUBLIC_*`, baked at image build) versus `runtime`. Duplicate keys in an env template parse deterministically last-wins and raise a finding, not a crash. `ENV_VARS.md` becomes a generated renderer output. Never store actual secret values.

### Data stores

PostgreSQL/pgvector objects, the Alembic revision graph, MinIO buckets (`fullstack` general vs `fullstack-rag`, addressed S3-compatibly via boto3), Redis logical databases (0 general cache, 1 Taskiq broker, 2 Taskiq results, 3 embedding cache L1), the **embedding cache L2** (durable PostgreSQL pgvector cache table) and its versioned cache-key fingerprint, per-collection `embedding_fingerprint` validation, vector dimensions, persistence volumes (including the external preservation volumes), and backup/retention classification.

### AI runtime

Agents, models, providers, prompts, tools, and connections modeled along **orthogonal dimensions**: provisioning (`deployment` — from the `MCP_SERVERS` setting — or `user` — rows in `mcp_connections`), executor (`mcp` client or `google-rest` toolsets built from the product data registry), transport (streamable HTTP vs SSE, inferred from URL), authentication (none / bearer / OAuth 2.1 + PKCE with dynamic client registration), allowlist source (settings validator / per-connection `allowed_tools` / product registry), approval gating (pydantic-ai deferred tools), and encryption at rest. Also: embedding dimensions, cache fingerprints, observability, fallback behavior (probe-and-skip semantics, tool prefixing and dedup), and human-confirmation requirements. Per-user connections are enumerated only from runtime evidence with provenance `database`, under the runtime-evidence rules.

### Interfaces

References to OpenAPI (produced by the sanitized exporter), the **generated WebSocket event inventory**, frontend API clients, the Next.js proxy route handlers, background-task payloads, MCP endpoints, and external integration contracts. Two repository facts shape this manifest: every REST call from the browser goes through the Next.js proxy layer (`frontend/src/app/api/**` — the proxy hop is mandatory in the site graph), and chat streaming is a WebSocket (`/api/v1/ws/agent`) that bypasses the proxy — the one direct browser→backend edge, authenticated by token-as-subprotocol. WS events currently have no formal schema (ad-hoc `{type, data}` dicts), so the inventory is generated from backend producers and `use-chat.ts` consumers; the producer/consumer set difference is the compatibility check. SSE exists only for the RAG ingestion status stream. Reuse existing schemas rather than manually reproducing them.

### Tests

Mappings among tests, components, symbols, contracts, policies, and scenario coverage. Because line coverage is pinned at 100% on both sides (backend `fail_under = 100`, frontend vitest thresholds), the risk metric is **invariant/scenario coverage** — which declared invariants have a dedicated test (dimension guard → `test_rag_docker_integration.py`; tool routing/approval → `evals/google_workspace_tools.py`; migrations → `test_migrations.py`, DB-gated) — not line-coverage weakness. Record frameworks precisely (anyio async tests with mocked sessions, DB-gated migration tests, env-gated destructive live suites, vitest, Playwright) and the gating env vars as metadata so impact analysis never selects a destructive suite.

## Repository graph and visualizers

### Extraction

Use Python AST or LibCST/tree-sitter for Python, the TypeScript compiler API/ts-morph or tree-sitter for TypeScript, and dedicated parsers for TOML, YAML, Compose, OpenAPI, Alembic, Makefile targets, and package metadata.

AST extraction should identify modules, classes, functions, imports, calls, inheritance, decorators, routes, models, tasks, tools, and tests. Structured extractors add services, configuration, migrations, endpoints, storage, and deployment relationships. The proxy→backend route mapping is extracted from `backendFetch`/`fetch` string literals in the route handlers.

Known extraction limits in this repository, each with its mitigation:

- Google tool nodes are **data, not AST-visible functions** — toolsets are built from the `DIRECT_GOOGLE_PRODUCTS` registry; extract the registry table, not the call graph.
- CLI commands are auto-discovered via `pkgutil` (`app/commands/__init__.py`); enumerate the package modules.
- Taskiq tasks register by import side-effect (`app/worker/taskiq_app.py`); extract the `@broker.task` decorators plus the explicit imports.
- WS events are untyped dicts; use the generated event inventory (Interfaces manifest).
- Per-user MCP edges exist only in database rows; runtime evidence only, never static assumption.

### Typed graph

Representative node types:

- directory, file, module, class, function;
- frontend page, component, API client, **proxy route handler**;
- API route and WebSocket event;
- agent, prompt, model, MCP server, MCP user connection, tool;
- background task;
- database model, migration, table, vector index;
- Compose service, **host runtime**, network, volume;
- environment variable;
- validator, test, policy, ADR, and change record.

Representative edges:

- `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`;
- `DEPENDS_ON`, `CONFIGURES`, `EXPOSES`, `IMPLEMENTS`;
- `READS_FROM`, `WRITES_TO`, `MIGRATES`;
- `PROXIES_TO`, `INVOKES_TOOL`, `ROUTES_TO`, `TESTS`;
- `OWNED_BY`, `GOVERNED_BY`, `CHANGED_BY`.

Preserve edge provenance, extraction method, and confidence. A runtime-observed edge and a statically inferred edge may coexist.

### Logical graph views

Maintain separate views over shared identifiers rather than one undifferentiated graph:

1. Code dependency graph.
2. Component architecture graph.
3. Runtime service graph.
4. Data-flow graph.
5. Configuration graph.
6. Agent/tool/permission graph.
7. Site and user-journey graph.
8. Governance and ownership graph.
9. Test coverage graph.

The site graph connects a Next.js page or action to its component, API client, **proxy route handler**, FastAPI route, service, agent/RAG operation, data store, MCP, or external provider. The WebSocket chat edge is modeled as the explicit exception that bypasses the proxy layer. Impact analysis that skips the proxy hop reports wrong blast radii.

### Graph analysis

| Algorithm | Primary use |
| --- | --- |
| Reverse reachability | Change blast radius |
| Strongly connected components | Dependency cycles |
| Personalized PageRank | Importance relative to a task or subsystem |
| Betweenness centrality | Bridges and architectural bottlenecks |
| Community detection | Candidate component boundaries |
| In/out degree analysis | Coupling anomalies |
| Shortest paths | Explain relationships |
| Orphan detection | Unused routes, configuration, tools, or files |
| Churn × centrality | Maintenance hotspots |
| Centrality × weak invariant coverage | High-risk insufficiently protected code |

PageRank is a relevance and dependency-centrality signal, not a quality score or automatic merge decision. Compute rankings within typed graph views or use configurable edge weights; generic utilities otherwise dominate misleadingly.

Orphan detection uses a `generator-provided, feature-disabled` classification sourced from the `.fastapi-fullstack.json` context (e.g. `enable_billing: false`): billing migrations and tables are expected-present-unused, not orphans. Known generated files (`mcp-logos.generated.ts`, lockfiles, governance-generated manifests) live in the generated-file registry for direct-edit detection.

### Visualizations

Generate bounded, task-focused views rather than rendering the whole graph:

- architecture map;
- Next.js-to-FastAPI site map (including the proxy layer);
- proposed-change impact map;
- environment-variable flow map;
- database migration and data-flow map;
- agent/MCP permission map;
- dependency-cycle and hotspot reports.

Visual outputs are generated on demand or attached to CI runs. The AI receives the small subgraph used to generate the view, not the entire SQLite graph.

### Static and runtime evidence

Static analysis cannot fully resolve dynamic imports, decorators, dependency injection, reflection, framework registration, runtime Compose profiles, dynamically constructed routes, or database-resident configuration. Supplement it with:

- generated OpenAPI (sanitized exporter) and runtime route enumeration;
- resolved Compose configuration per canonical stack;
- MCP initialization and `tools/list` results;
- per-user connection inventory from the `mcp_connections` table;
- test instrumentation;
- carefully scoped Logfire traces;
- runtime dependency-registration inspection.

Compare declared, static, and observed edges. Treat runtime-only privileged edges as high-priority findings.

### Runtime-evidence data governance

Runtime evidence is environment-specific, nondeterministic, and can contain tenant data — and per-user connection URLs can contain live credentials (the catalog supports token-in-URL placement, and the `url` column is stored unencrypted). Rules:

- Runtime-derived records never enter committed manifests, graph reports, or change records; committed artifacts may reference evidence only by ID and summary.
- Strip query strings, userinfo, and known credential patterns from every URL at capture time, before storage or logging.
- Evidence stores are environment-scoped, TTL-bound, and live under `.cache/repo-governance/evidence/` (gitignored).
- Database access for evidence collection is read-only and produces aggregated results (counts, kinds, health states), never row dumps.
- PII, prompt contents, and Logfire-derived payloads are governed explicitly: collected only when a named policy requires them, redacted to the minimum the check needs.

## Governance CLI

The CLI is a standalone uv project at `tools/repo_governance/` (own `pyproject.toml`, `uv.lock`, `.venv`; Python ≥ 3.12) that never imports `app.*`. Expose it as `uv run --project tools/repo_governance governance …`, wrapped by Make targets so the Makefile remains the single operational entry point.

### Core commands

```text
governance bootstrap
governance preflight
governance scan
governance sync
governance check [--fast|--full]
governance context --paths ... --task ... --token-budget N
governance impact --paths ... [--depth N]
governance explain COMPONENT_ID
governance change start --summary ... --reason ...
governance change finish --validation ...
governance summary
governance visualize VIEW [--focus ID]
governance doctor
governance migrate --to VERSION
governance evaluate [SCENARIO]
```

### Command behavior

- `bootstrap`: inventory the repository, record generator provenance (from `.fastapi-fullstack.json`), establish a reviewed baseline, protect external volumes and pre-existing state, and refuse destructive normalization.
- `preflight`: wrap the existing `make preflight-*` targets, then confirm schema/tool compatibility and report unexplained drift before a change begins.
- `scan`: parse authoritative sources and rebuild the graph cache and generated candidates.
- `sync`: atomically update generated/effective manifests, small graph reports, and derived documentation (including `ENV_VARS.md`).
- `check`: perform a read-only regeneration comparison plus schema, reference, policy, security, compatibility, process, and idempotence validation.
- `context`: select a token-bounded task briefing using path proximity, graph reachability, policy risk, architectural centrality, history relevance, and recency; return references to `.claude/rules/*` files rather than restating them.
- `impact`: return affected symbols, components, contracts, configuration, data, validators, ADRs, and likely blast radius.
- `explain`: return a compact component briefing.
- `change start`: capture rationale before it can be lost and open a temporary change session outside committed history.
- `change finish`: collect diff-derived facts and validator evidence, ask for any missing human judgment, then create a validated permanent record.
- `summary`: regenerate the bounded `Summary.md`.
- `visualize`: generate a bounded subgraph and corresponding view.
- `doctor`: diagnose missing parsers, malformed configuration, stale caches, unsupported syntax, and repository inconsistencies.
- `migrate`: upgrade governance schemas and documents explicitly.
- `evaluate`: run historical and synthetic governance scenarios (pydantic-evals).

### Determinism and safety requirements

- Stable keys, forward-slash path normalization, and stable list ordering.
- Generated files written LF-only, UTF-8 without BOM; atomic writes through temporary files followed by `os.replace`; `.gitattributes` pins generated governance files to LF.
- Read-only check mode with nonzero exit codes.
- Idempotence test: two sync operations produce identical bytes.
- No secret values in manifests, graph metadata, logs, or change records; runtime evidence follows the data-governance rules above.
- Never import application modules in-process; subprocess isolation with a scrubbed environment for anything that must execute application code.
- Generated/curated ownership enforced at the field or document boundary.
- Provenance and confidence for extracted graph edges.
- Parser failures reported as unknowns, never silently interpreted as absence.
- Bounded traversal depth and token budgets for context queries.
- Validators execute only through the reviewed registry; destructive validators require an explicit human-invoked flag.

## AI modification workflow

### Before modification

1. Run `governance preflight`.
2. Start a change session with the task summary and actual reason.
3. Run `governance context` for the proposed paths and task.
4. Read only the returned manifest slices, local annotations, ADRs, recent related records, and policies.
5. State affected components, contracts, invariants, security boundaries, and planned validators.

### During modification

1. Make the smallest coherent change.
2. Query impact again if new paths or components enter scope.
3. Do not manually edit generated manifests, `Summary.md`, or `ENV_VARS.md`.
4. Add or update curated intent only when architectural meaning changes.
5. Create an ADR only for durable decisions, not ordinary implementation detail.

### After modification

1. Run `governance scan` and inspect discovered relationship changes.
2. Run `governance sync`.
3. Review the generated diff rather than accepting it blindly.
4. Run the graph-selected registered validators and any policy-mandated validators.
5. Run relevant Compose, environment, migration, MCP, persistence, GPU, and security checks (via their Make targets).
6. Finish the change record with behavioral effects, evidence, risks, limitations, and rollback.
7. Regenerate `Summary.md`.
8. Run `governance check --full`.
9. Report remaining uncertainty rather than inventing missing facts.

If a generated manifest disagrees with an authoritative source, treat the manifest as stale. Never change application behavior merely to make a stale generated file pass.

## Automated enforcement

### Editor and manual feedback

- Associate JSON files with their `$schema` for immediate validation.
- Provide short Make targets such as `make governance-check` and `make governance-context`.
- Keep error messages actionable: source, violated policy, evidence, and repair command.

### Pre-commit

The repository already has two hook layers: `backend/.pre-commit-config.yaml` (ruff, ty, hygiene) and frontend husky/lint-staged. Governance adds fast checks to **one** of them — either promote the pre-commit config to the repository root (pre-commit supports per-hook `files:` scoping) and add governance hooks there, or run governance fast-checks from husky plus a Make target. Pick one; do not introduce a third hook framework. The fast affected-file check covers:

- JSON and schema validity;
- local annotation and curated-declaration validity;
- secret-value scanning;
- generated-file direct-edit detection (via the generated-file registry);
- fast drift detection for touched components;
- required change-session or change-record presence;
- `Summary.md` synchronization when history changes.

Keep this fast enough that developers do not bypass it.

### Continuous integration

Governance CI is a new job in the existing `.github/workflows/ci.yml`, not a separate pipeline:

1. Rebuild generated manifests and graph cache in a clean environment.
2. Fail on uncommitted generated diffs.
3. Validate schemas and cross-file references.
4. Run architecture, security, compatibility, and process policies.
5. Confirm a change record exists when governed paths changed.
6. Run idempotence and golden-fixture tests.
7. Select and run affected application tests via registered validators, then required broad suites.
8. Compare OpenAPI, migrations, configuration, Compose stacks/profiles, MCP permissions, and persistence contracts.
9. Generate impact and visualization artifacts for review.
10. Require explicit review for breaking contracts, privilege expansion, data migrations, policy weakening, or governance-kernel changes.

Until a git remote exists, the "pull-request" gates run locally as `make governance-check` before merge-equivalent events. Establishing a remote (even private) is recommended — for enforcement substrate and history durability — and is recorded during Phase 0 as a repo-owner decision. The existing Trivy job (`exit-code: 0`, advisory-only) is the ready-made pilot for the advisory→blocking policy-maturity ladder.

### Scheduled analysis

Run deeper non-blocking analysis periodically or manually:

- orphan and dead-route detection (with the `generator-provided, feature-disabled` whitelist);
- centrality/hotspot recalculation;
- weak-invariant-coverage critical-node analysis;
- runtime-versus-static graph comparison;
- stale exception detection;
- generator/upstream divergence assessment;
- governance performance and false-positive review.

Do not let scheduled automation silently modify application code or activate new blocking policy.

### Generator-upgrade workflow

Generator provenance is already recorded: `.fastapi-fullstack.json` carries the template reference (`0.2.17`), generation timestamp, `context_hash`, and the full generation context; the Makefile provides `upgrade`, `upgrade-dry-run`, `upgrade-new-features`, and `upgrade-finalize` with gitignored `.pending`/`.candidate` transient manifests. Governance builds on this mechanism rather than duplicating it. For an upgrade:

1. Build a clean project from the proposed generator version using the preserved context.
2. Compare its manifests and graph with the local repository.
3. Classify upstream changes, local changes, and conflicts (using `context_hash` and the recorded divergence, including the deliberately customized `AGENTS.md`/`CLAUDE.md`).
4. Apply upgrades through the existing `upgrade*` targets or reviewed patches.
5. Run the full governance and application validation matrix.
6. Record the upgrade decision, compatibility impact, and rollback.

## Initial policies for this harness

### Configuration

- Active configuration variables must be declared, typed, and consumed; the declared-vs-consumed mapping accounts for computed settings properties and nested config objects.
- Extraction never imports settings in-process (AST primary; scrubbed subprocess for cross-checking).
- `.env` template parsing must reject malformed concatenated lines; duplicate keys parse last-wins and raise a finding.
- Required variables may not be empty in stacks/profiles where their component is active.
- Secret classification is a curated list; secret variables may be named and classified but never copied into governance output.
- Deprecated aliases (`GOOGLE_WORKSPACE_MCP_*`, `GOOGLE_DRIVE_*` → `GOOGLE_API_*`) carry an `alias-of` classification and a removal milestone.
- The frontend configuration surface (env template, `NEXT_PUBLIC_*` build args, `process.env` references) is part of the manifest, with build-time vs runtime classification.
- `ENV_VARS.md` is generated output; hand edits are drift.

### PostgreSQL and pgvector

- Qwen3 embedding dimensionality remains `1024` unless an explicit migration changes the complete contract.
- The dimension contract is a **five-point agreement check**: settings `EMBEDDING_DIMENSION` ↔ vectorstore guard/DDL ↔ migration CHECK constraint ↔ persisted per-collection `embedding_fingerprint` ↔ embedding-cache key version. All five exist in code today; drift between any two is the finding.
- ORM schema changes require an Alembic revision.
- Migration-chain policy: the revision graph must be acyclic and connected with a head set matching an explicitly recorded expectation; branches and merge revisions are legal, and single-head becomes a rule only if adopted as deliberate policy. Filename numbering is not the contiguity signal — the existing numbering gaps (no 0017, 0019–0021, an interleaved 0004_5) are recorded as accepted history.
- Migrations reversible where feasible.

### Redis

- Logical DB allocations remain explicit: DB 0 general cache, DB 1 Taskiq broker, DB 2 Taskiq results, DB 3 embedding cache L1.
- The embedding cache is two-level — Redis L1 backed by a durable PostgreSQL pgvector L2 with versioned cache keys; the L2 table is part of the data-store manifest and the dimension contract.
- New consumers cannot silently reuse an allocated logical DB.

### MinIO / S3

- General (`fullstack`) and RAG (`fullstack-rag`) bucket purposes remain distinct, with separate credentials and endpoints (S3-compatible via boto3).
- Bucket names, endpoints, credentials references, and persistence declarations must align across settings and Compose.

### Docling

Docling has two distinct execution paths, and policy covers both:

- **Ingestion and chat uploads** use the direct HTTP client (`DoclingServeParser`) to Docling Serve — retry set, upload size cap, and page-break sentinel contract are part of the interface manifest.
- **The `docling-mcp` sidecar** (agent tool source, `mcp` Compose profile) runs in remote conversion mode against the same Docling Serve instance.
- Invariant: both paths resolve to the same `DOCLING_SERVE_URL` with compatible timeout/retry budgets.
- Conversion health and a harmless conversion test are required after configuration changes.
- GPU behavior, model cache (external `docling-models` volume), worker count, and OOM/fallback expectations remain explicit.

### MCP and agent tools

Connections are modeled along orthogonal dimensions (provisioning, executor, transport, authentication, allowlist source, approval gating, encryption at rest) rather than as server types. Policies:

- **GitHub MCP read-only** is a cross-check of the three existing enforcement layers — container flags (`--read-only`, `--tools` allowlist), the settings-validator frozenset, and the runtime post-probe assert. Drift between any two layers is the finding. Privilege expansion requires explicit approval.
- MCP services must pass initialize, `tools/list`, and a harmless-tool check.
- Tool annotations, authorization level, reachable resources, transport, and exposure must be mapped; per-user connections are enumerated only from runtime evidence with provenance `database`.
- **Approval-gating asymmetry is a standing finding**: deferred-tool approval currently covers Google mutation tools only, MCP-sourced tools bypass it entirely, and a connection with `allowed_tools` unset exposes every tool the server advertises. Write-capable arbitrary servers therefore combine full tool exposure with no approval gate. This requires an explicit product-security decision (approval parity, default allowlists, or a documented restriction) before MCP policies are promoted past advisory; an accepted-risk ADR alone is not sufficient.
- **SSRF validation is write-time only** (connection create/update and OAuth flows); the per-turn probe and toolset attach open stored URLs without revalidation. This is tracked as an open security finding with remediation options (connection-time revalidation; network-level egress controls) — never described as a protected invariant until the connection path is fixed.
- **Connection URLs can carry live credentials** (catalog token-in-URL placement; the `url` column is unencrypted and returned by the API). Every governance surface that touches connection URLs strips query strings and userinfo first; remediating the storage design is tracked as a finding.
- Frontend catalog entries must map to a backend `google_api_kind` or documented generic handling; catalog-vs-backend-map agreement is a checked contract.

### Compose and networking

- Validate the full file-stack × profile matrix using the Makefile's canonical stacks; wrap `make compose-check` / `compose-check-prod` rather than reimplementing them.
- Exposure invariants (the real security posture, not generic port-conflict checks): the base/staging stack publishes **zero** host ports; the dev stack binds only to `127.0.0.1`; prod publishes only Traefik 80/443 plus the loopback dashboard; `data-internal` remains `internal: true`.
- Model Docker Model Runner as a `host-runtime` node (embeddings + reranker); its health check is `make preflight-model`.
- External preservation volumes (`redis-data`, `docling-models`) are protected pre-existing state; bootstrap and checks must never recreate or normalize them.
- Require health checks and persistent volumes where expected; record the deliberate exceptions (Taskiq worker/scheduler healthchecks disabled).
- Pin production images by digest (already the norm — every third-party image is SHA256-pinned; policy prevents regression).
- Preserve internal-versus-external service boundaries and account for the existing IIS/port-80 environment (prod Traefik uses `TRAEFIK_HTTP_PORT`).

### Taskiq

- Broker/result allocations, worker concurrency (currently a deliberate single worker with `TASKIQ_MAX_ASYNC_TASKS=1`), scheduler behavior, retries, and restart behavior must remain testable and explicit.

### Interfaces and frontend

- API-contract changes must identify affected proxy route handlers, frontend clients, and pages — the proxy hop is part of every REST impact chain.
- Frontend-to-backend build/version provenance must be checkable (including `NEXT_PUBLIC_*` build args baked at image build).
- WebSocket event changes require producer/consumer compatibility validation against the generated event inventory; the WS chat edge is the documented exception to the proxy rule.

### Validation registry

- All validation referenced by annotations, manifests, or impact analysis resolves through `governance/validators.json` — a reviewed registry mapping trusted validator IDs to Make targets or fixed commands.
- Annotations and manifests never contain executable shell text.
- Destructive or live-service validators (env-gated suites such as the live Google tests) are excluded from the registry and run only via an explicit human-invoked CLI flag.
- Agent behavior is verified in two registered tiers:
  - **`agent-evals`** — deterministic tool-routing and approval-policy evals (pydantic-evals, no live LLM), extending the existing `backend/evals/` harness to every tool. Cheap enough to run on every change; feeds the invariant-coverage metric in the Tests manifest.
  - **`agent-smoke`** — a live but harmless smoke sweep: a fixed prompt list driven through the WebSocket agent API against a running stack, exercising each read-only capability (RAG search, web search, chart, code execution, MCP probes, Google reads). Verification is **structural pass/fail over the Logfire trace** — expected tool spans present, zero error spans, each tool succeeded — never raw-output or raw-telemetry diffing. Selected by impact analysis when a change touches the agent/tool/MCP/configuration surface, plus a nightly scheduled run; never per-commit and never UI-driven. Its traces and any captured records follow the runtime-evidence data-governance rules. Destructive mutation flows stay in the excluded, human-invoked tier.

### Runtime evidence

- Runtime evidence follows the data-governance rules (never committed; URLs stripped of query strings/credentials at capture; environment-scoped TTL-bound storage; read-only aggregated database access; PII/prompt/Logfire payloads collected only under a named policy, minimally).

## Known findings at adoption

Register these as findings on day one so checks start honest instead of discovering them as "drift." Each carries a disposition:

1. `ENV_VARS.md` has drifted from the actual configuration surface (documents non-existent variables; formatting defects) — resolved by making it generated output (Phase 3 quick win).
2. `backend/.env.example` contains duplicated keys — fix once; the duplicate-key parse rule prevents recurrence.
3. `TIMEZONE` default (`"EDT"`) is not a valid IANA zone — application fix, tracked as a finding until landed.
4. `.gitignore` gaps: `artifacts/` and `.uv-cache/` unignored — fix during Phase 0.
5. No git remote — durability and enforcement-substrate decision for the repo owner, recorded in Phase 0.
6. `AGENTS.md`/`CLAUDE.md` duplication — resolved by the Phase 1 instruction-surface convergence.
7. Trivy scan is advisory-only (`exit-code: 0`) — acceptable, but recorded as a deliberate choice; pilot for policy-maturity promotion.
8. MCP approval-gating asymmetry (see MCP policies) — requires a product-security decision.
9. No connection-time SSRF revalidation (see MCP policies) — open security finding.
10. URL-embedded connection credentials stored unencrypted and echoed by the API (see MCP policies) — open security finding.

## Context management rules

1. Never instruct an agent to recursively read `governance/`.
2. Keep `AGENTS.md` concise and stable.
3. Keep `catalog.json` small enough to read routinely.
4. Bound `Summary.md` to current state and recent material history.
5. Query older records and ADRs by component, path, policy, or change relationship.
6. Execute schemas and policies without loading their complete contents into model context.
7. Keep the full graph in SQLite outside normal context.
8. Return bounded graph slices with explicit token budgets.
9. Put `.governance.json` only at architectural boundaries; declare non-directory and multi-root components in the curated manifest instead of inventing directories for them.
10. Reuse `.claude/rules/*` as the path-scoped convention layer; context results reference those files rather than restating them.
11. Exclude generated graph databases, evidence stores, and large rendered views from generic semantic indexing.
12. Prefer exact structured queries over repository-wide text search.
13. Include provenance and uncertainty in returned context so agents know what requires source verification.

## Recursive improvement and self-governance

The governance system should improve from actual use, but it must not be able to silently rewrite the rules that judge it.

### Minimal governance kernel

Treat these as the protected kernel:

- governance version and migration rules in `governance.toml`;
- change-record schema;
- generated-versus-curated ownership rules;
- secret-handling and runtime-evidence data-governance rules;
- the validator registry and its review requirement;
- self-governance policy;
- full-check command and idempotence test;
- requirement that policy weakening be explicit and reviewed.

Everything else can evolve through the governed process.

### Improvement loop

```text
Observe a miss, false positive, or inefficient context result
    -> create an evaluation record
    -> propose extractor/schema/policy/ranking change
    -> run in advisory shadow mode
    -> replay historical and synthetic scenarios
    -> compare precision, recall, context size, and runtime
    -> review compatibility and security impact
    -> activate at warning level
    -> promote to blocking only after demonstrated reliability
    -> monitor and retain rollback
```

### Policy maturity

Every policy has a lifecycle:

- `experimental`: development only;
- `advisory`: reported but non-blocking;
- `warning`: visible and requires acknowledgment;
- `blocking`: fails governed checks;
- `deprecated`: retained temporarily for migration;
- `retired`: removed after compatibility review.

### Versioning and migration

- Version the governance contract independently from the application.
- Include schema and tool versions in manifests and change records.
- Require explicit migrations for incompatible schema changes.
- Validate old and new forms during a bounded transition when practical.
- Maintain golden fixtures for every supported governance version.
- Refuse to interpret a newer unsupported manifest as though it were valid.

### Evaluation scenarios

Build small reproducible scenarios representing real changes:

- rename an environment variable;
- add a Compose service (and a profile);
- alter an embedding dimension;
- add or remove a FastAPI route (and its proxy route handler);
- change a Next.js API consumer;
- add an Alembic migration (including a branch + merge revision);
- expand an MCP tool allowlist;
- add a per-user MCP connection (runtime-evidence path);
- introduce a dependency cycle;
- move a governed subsystem;
- change a governance schema or ranking weight.

Each scenario defines expected affected components, policies, validators, context documents, and findings. Historical change records can seed new scenarios.

### Feedback metrics

Track only metrics that lead to decisions:

- false-positive and missed-finding counts;
- percentage of generated drift explained;
- context output size and relevance;
- impact-set accuracy against post-change reality;
- policy execution time;
- parser unsupported-syntax rate;
- stale exception count;
- critical-node invariant coverage;
- governance changes rolled back.

Do not optimize PageRank scores themselves. Optimize the usefulness and accuracy of decisions supported by the graph.

### Safe automation boundary

Automation may regenerate derived files, propose policy changes, produce evaluation reports, and draft patches. It may not automatically weaken a policy, broaden an MCP permission, accept a security exception, migrate production data, or approve its own governance change.

## Simplified implementation strategy

Avoid building the complete graph platform before the basic governance workflow provides value. Use a narrow vertical slice and expand it.

### Milestone 1: the compact kernel

Phases 0–4, trimmed, form the first deliverable: a reviewed baseline, the 10–20 critical cross-file invariants (enumerated in the policy sections above), converged agent instructions wrapping the existing Makefile/CI checks, the generated configuration inventory with drift detection, the validator registry, and lightweight material-change records. Policies stay advisory until their accuracy is demonstrated. Determinism (two byte-identical syncs) is kernel scope, never deferred — noisy drift detection in week one would sink the system's credibility.

Milestone 1 is accepted when governance answers three questions reliably:

1. **What owns this behavior?** — from annotations and curated declarations.
2. **What else can this change break?** — manifest-level blast radius: component dependencies, configuration references, and the proxy-route mapping. Conservative (over-inclusive) answers are acceptable at this stage.
3. **Which existing checks prove it still works?** — validator IDs resolving to Make targets and CI jobs.

One deliberate exception to deferring runtime evidence: a one-off, manually run, sanitized snapshot of the per-user connection inventory (aggregate counts, URLs stripped) is taken during Milestone 1, because the product-security decision on MCP approval gating needs it as input.

### Growth triggers for the deferred phases

Everything from Phase 6 onward is built in response to a named symptom, not in anticipation of one:

- **Phase 6 (AST graph):** kernel impact sets are measurably too coarse — validators over-selected on repeated changes, or context token budgets blown by over-inclusive slices.
- **Phase 7 (site maps, ranking, visualizers):** reviewers or agents demonstrably misjudge a dependency that a bounded view would have shown.
- **Phase 8 (runtime-evidence pipeline):** static-vs-declared comparisons repeatedly miss relationships only runtime can see, or the one-off connection snapshot needs refreshing often enough that manual runs become the risk.
- **Phase 9 (recursive improvement):** enough evaluation records exist that policy changes need shadow-mode replay to be trusted.
- **Telemetry baselining for `agent-smoke`** (numeric latency/token tolerance bands, baseline capture and re-baseline workflow): a regression ships that the smoke's structural pass/fail assertions missed — e.g., a performance degradation while every tool span still succeeded.

Each trigger, when hit, becomes an evaluation record before it becomes a build task.

### Phase 0 — Local repository audit and stabilization

0. Pre-step: land or stash the in-flight work and take the baseline on a clean tree; where that is impractical, baseline a named commit plus an explicitly recorded working-tree patch so drift stays explainable. Record the repo owner's decision on establishing a git remote (recommended for durability and enforcement substrate).
1. Inventory the exact repository tree and working state.
2. Confirm generator provenance (`.fastapi-fullstack.json`) and the Compose stack/profile matrix (from the Makefile).
3. Produce the reviewed authority matrix (the table in Design Principles §1).
4. Catalog existing migrations, settings, RAG, Taskiq, MCP, Docker, frontend, and observability files; seed the generated-file registry.
5. Define protected data and secret boundaries; register the known findings at adoption.
6. Hygiene: add `artifacts/` and `.uv-cache/` to `.gitignore`.

**Exit criterion:** reviewed authority matrix and repository provenance; known findings registered; no files or volumes changed during discovery beyond the listed hygiene items.

### Phase 1 — Minimal governance kernel

1. Converge the instruction surfaces: rewrite `AGENTS.md` as the routing entry point, slim `CLAUDE.md`, keep `.claude/rules/*` as the detail layer.
2. Add `governance.toml`, `catalog.json`, and schema versioning.
3. Create schemas for components, configuration, services, and change records.
4. Add `self-governance.json`, generated/curated ownership rules, and the seed `validators.json` registry.
5. Add a minimal deterministic CLI (standalone uv project) with `preflight` (wrapping the Make preflights), `sync`, and `check`.

**Exit criterion:** invalid governance documents and direct edits to generated files are detected; two syncs are byte-identical on Windows.

### Phase 2 — Reviewed baseline manifests

1. Manually establish components and architectural boundaries (directory annotations at the real boundaries; curated declarations for `github-mcp`, `model-runner-host`, `mcp-user-connections`, and multi-root frontend features).
2. Create reviewed baseline service and configuration manifests.
3. Add current critical invariants for pgvector (five-point dimension check), Redis (including cache L2), MinIO, Docling (both paths), Taskiq, MCP, and Compose exposure.

**Exit criterion:** the effective manifest accurately describes the current system without attempting complete symbol mapping.

### Phase 3 — Deterministic extraction

1. Quick win first: extract the configuration surface (backend + frontend) and generate `ENV_VARS.md` — this fixes live drift on day one and exercises the full extract→sync→check loop on the least risky surface.
2. Extract Compose services across the canonical stacks and profiles.
3. Extract Pydantic settings via AST; add the sanitized subprocess exporter for OpenAPI and computed-field cross-checks.
4. Extract dependencies, Alembic revision graph, and frontend routes (locale-normalized) plus proxy-route mappings.
5. Reproduce the manually reviewed baseline.
6. Add golden fixtures, malformed-input tests (including duplicate env keys), and atomic writes.

**Exit criterion:** generated manifests reproduce the reviewed baseline and correctly surface deliberate fixture changes.

### Phase 4 — Change history and bounded context

1. Implement change start/finish sessions.
2. Generate structured permanent change records.
3. Generate bounded `Summary.md`.
4. Add ADR indexing.
5. Implement component/path-based `context`, `impact`, and `explain` using manifests before the full AST graph exists.

**Exit criterion:** an AI agent can complete a normal configuration or component change without recursively inspecting governance files.

### Phase 5 — Local and CI enforcement

1. Add fast affected-file checks to the chosen single hook layer (root pre-commit or husky + Make).
2. Add the governance job to the existing `ci.yml` with clean-environment full checks.
3. Require records for governed changes.
4. Add schema, reference, architecture, security, compatibility, and process checks.
5. Generate review artifacts without automatically changing the branch.
6. Pilot the policy-maturity ladder by promoting the Trivy scan from advisory through warning toward blocking.

**Exit criterion:** stale manifests, unrecorded governed changes, malformed configuration, secret leakage, and critical invariant violations block review appropriately (locally via `make governance-check` until a remote exists).

### Phase 6 — AST graph and impact analysis

1. Define stable node IDs and typed edges.
2. Implement Python and TypeScript import/module graphs.
3. Add routes, proxy mappings, tasks, models, tests, tools (including data-registry extraction), and configuration relationships.
4. Store the full graph in SQLite.
5. Add reverse reachability, cycle detection, orphan detection (with the feature-disabled whitelist), and shortest-path explanations.
6. Feed bounded graph slices into `context` and `impact`.

**Exit criterion:** impact analysis improves validator and context selection on the evaluation scenarios without unacceptable false positives.

### Phase 7 — Site maps, algorithms, and visualizers

1. Connect Next.js pages/actions to API clients, proxy route handlers, and FastAPI routes (with the WS chat edge as the modeled exception).
2. Connect routes to services, agents, RAG, data stores, MCP, and external providers.
3. Add personalized PageRank, betweenness, community detection, and churn/invariant-coverage risk analysis.
4. Generate focused architecture, site, impact, configuration, migration, and security views.
5. Keep all algorithmic findings advisory initially.

**Exit criterion:** visualizations and rankings explain real dependencies and review risk better than a directory tree without becoming merge gates by default.

### Phase 8 — Runtime evidence

1. Import sanitized-exporter OpenAPI and runtime route enumeration.
2. Inspect resolved Compose stacks and profiles.
3. Record MCP discovery and harmless-tool tests.
4. Collect the per-user MCP connection inventory and compare it against the frontend catalog and the approval-gating map — under the runtime-evidence data-governance rules (URLs stripped of query strings and credentials, aggregate counts, ephemeral environment-scoped storage, never committed).
5. Integrate selected test instrumentation and Logfire relationship evidence.
6. Compare declared, static, and observed graphs.

**Exit criterion:** dynamic relationships and privileged runtime-only edges are visible with provenance and confidence, with no tenant data or credentials in any committed artifact.

### Phase 9 — Recursive governance improvement

1. Add policy lifecycle states and shadow mode.
2. Add evaluation records and scenario replay (pydantic-evals).
3. Measure false positives, misses, context size, unsupported syntax, and runtime.
4. Add explicit governance migrations and compatibility testing.
5. Require governance-change records and protected review for kernel changes.
6. Promote only demonstrated reliable policies from advisory to blocking.

**Exit criterion:** the system can propose and validate improvements to itself while remaining unable to silently weaken its own protections.

### Phase 10 — Generator upgrade intelligence

1. Reconstruct clean generated baselines from the preserved context in `.fastapi-fullstack.json`.
2. Compare upstream and local manifests/graphs, driven through the existing `make upgrade*` machinery.
3. Classify additions, removals, conflicts, and local divergence (via `context_hash` and the recorded customized files).
4. Produce reviewed upgrade plans with validation and rollback.

**Exit criterion:** generator upgrades become controlled reconciliations rather than manual repository-wide guesswork.

## Definition of success

The governance system is successful when:

- an AI agent can orient itself through one small entry point;
- task context is bounded, relevant, and source-backed;
- generated manifests never become an accidental second source of truth;
- governance checks and the pre-existing Makefile/CI machinery never disagree about the same fact;
- architectural intent survives refactors, agent changes, and generator upgrades;
- modification reasons and validation evidence remain discoverable;
- cross-file errors are caught before services start;
- impact analysis selects appropriate validators and reviewers;
- visual maps explain the system without requiring the whole graph in context;
- runtime evidence informs findings without tenant data or credentials ever reaching a committed artifact;
- policies distinguish uncertainty from violations;
- the governance system can evolve through evaluation, migration, and rollback;
- no automation can silently relax the rules that govern it.

The practical starting point is Phase 0 followed by the Phase 1 kernel. The AST graph, PageRank, and visualizers should build on a correct authority model and deterministic manifests, not precede them.

## Reference standards and project context

- JSON Schema Draft 2020-12: <https://json-schema.org/draft/2020-12>
- Pydantic JSON Schema generation: <https://docs.pydantic.dev/latest/concepts/json_schema/>
- Pydantic Evals: <https://ai.pydantic.dev/evals/>
- Pre-commit framework: <https://pre-commit.com/>
- Full-Stack AI Agent Template: <https://github.com/vstorm-co/full-stack-ai-agent-template>
- Template agent guidance: <https://github.com/vstorm-co/full-stack-ai-agent-template/blob/main/AGENTS.md>
