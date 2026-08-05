# Full-Stack Agentic Harness Governance Blueprint

## Executive summary

Build the governance system as a small repository control plane, not as a parallel copy of the application. Existing project files remain authoritative: Docker Compose describes services, Pydantic settings describe backend configuration, Alembic describes database evolution, OpenAPI describes HTTP contracts, and package lockfiles describe dependencies. Governance extracts and connects those facts, adds human intent that code cannot express, validates cross-file invariants, and gives AI agents a compact task-specific view.

The system has seven cooperating parts:

1. A concise `AGENTS.md` entry point.
2. JSON Schemas that define governance document structure.
3. Generated, curated, and merged manifests.
4. Optional child-directory annotations at architectural boundaries.
5. A Python governance CLI that scans, synchronizes, validates, queries, and records changes.
6. A typed repository graph built from ASTs and structured configuration, stored as a generated query cache rather than injected into agent context.
7. Automated local and CI workflows enforcing drift detection, architecture, security, compatibility, history, and self-governance.

The manifests are the repository's machine-readable index, not the source of truth for facts already owned elsewhere. The graph is derived intelligence, not policy. `Summary.md` is generated from structured change records, while ADRs preserve durable architectural decisions. AI agents retrieve bounded context through the CLI instead of traversing the entire governance directory.

## Design principles

### 1. Preserve authoritative sources

Do not maintain the same fact manually in multiple places. Extract or reference it from its owner.

| Information | Authoritative source | Governance treatment |
| --- | --- | --- |
| Containers, networks, ports, profiles, volumes | Docker Compose variants | Extract and normalize |
| Backend configuration types and defaults | Pydantic settings | Extract and cross-check |
| Configuration availability | `.env.example` and environment templates | Extract names only; never values |
| Python dependencies | `pyproject.toml` and `uv.lock` | Extract |
| Frontend dependencies | `package.json` and Bun lockfile | Extract |
| HTTP contracts | FastAPI/Pydantic and generated OpenAPI | Reference or extract |
| Database evolution | Alembic revisions | Extract revision graph |
| Current ORM model shape | SQLAlchemy models | Extract and compare with migrations |
| Frontend routes | Next.js route structure | Extract |
| Background processing | Taskiq configuration and task registration | Extract |
| Agent and tool registration | Agent source/configuration and MCP discovery | Extract statically and verify dynamically |
| Runtime behavior | Tests and Logfire traces | Treat as observed evidence, never the only declaration |
| Architectural intent and exceptions | Local annotations, curated manifests, and ADRs | Human/agent authored and reviewed |
| Modification rationale | Structured change record | Human/agent supplied; never inferred solely from a diff |

### 2. Separate three kinds of truth

- **Observed architecture:** extracted statically or seen at runtime.
- **Declared architecture:** component manifests and local annotations.
- **Expected architecture:** policies, invariants, and ADRs.

Differences between them become findings. For example, a runtime MCP edge not declared in the agent-tool manifest is a security finding; a declared dependency never found statically or dynamically is a possible stale annotation.

### 3. Separate generated and curated content

Generated files may be replaced deterministically. Curated intent must never be silently overwritten. The effective repository model is produced by a documented merge with explicit conflicts and exceptions.

### 4. Use progressive disclosure

Agents read a small entry point, request an impact/context slice, and open only the returned manifests, ADRs, and change records. They never recursively load the governance tree or the full code graph.

### 5. Make outputs deterministic

Use stable IDs, stable ordering, normalized paths, atomic writes, and no volatile timestamps in ordinary generated manifests. Running synchronization twice without source changes must produce no diff.

### 6. Treat governance itself as governed code

Schemas, policies, extraction logic, ranking weights, and the AI workflow can improve, but changes to them require compatibility checks, evaluation evidence, a governance change record, and an available rollback.

## Final repository-root structure

```text
repo-root/
├── AGENTS.md
├── governance/
│   ├── catalog.json
│   ├── Summary.md
│   ├── governance.toml
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
│   │   │   ├── architectural-intent.json
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
│   └── repo_governance/
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── identifiers.py
│       ├── extractors/
│       │   ├── python_ast.py
│       │   ├── typescript_ast.py
│       │   ├── compose.py
│       │   ├── configuration.py
│       │   ├── openapi.py
│       │   ├── alembic.py
│       │   ├── dependencies.py
│       │   └── mcp.py
│       ├── graph/
│       │   ├── builder.py
│       │   ├── storage.py
│       │   ├── queries.py
│       │   └── algorithms.py
│       ├── checks/
│       │   ├── schemas.py
│       │   ├── references.py
│       │   ├── architecture.py
│       │   ├── security.py
│       │   ├── compatibility.py
│       │   └── process.py
│       ├── renderers/
│       │   ├── summary.py
│       │   ├── context.py
│       │   └── visualizations.py
│       └── tests/
│           ├── fixtures/
│           ├── golden/
│           └── scenarios/
├── .cache/
│   └── repo-governance/
│       └── repository.sqlite
└── artifacts/
    └── governance/
        ├── repository-map.html
        ├── site-map.html
        ├── impact-map.html
        └── security-map.html
```

Commit `governance/` and `tools/repo_governance/`. Treat `.cache/repo-governance/` and rendered `artifacts/governance/` as reproducible local or CI artifacts and normally exclude them from version control. Commit only the small graph reports whose diffs are useful during review.

The full graph should use SQLite initially. JSON remains appropriate for schemas, manifests, policies, annotations, change records, and small graph reports; it is inefficient for a large symbol-edge graph.

## Child-directory governance structure

Do not place a governance file in every directory. Put `.governance.json` only at architectural boundaries: backend subsystems, frontend features, infrastructure groups, MCP services, persistent-data components, and security-sensitive modules.

```text
backend/app/rag/
├── .governance.json
├── router.py
├── service.py
├── ingestion.py
└── tests/

backend/app/agents/
├── .governance.json
└── ...

frontend/src/features/chat/
├── .governance.json
└── ...

infra/mcp/github/
├── .governance.json
└── ...
```

Recommended local annotation shape:

```json
{
  "$schema": "../../../governance/schemas/local-governance.schema.json",
  "id": "rag-ingestion",
  "kind": "backend-subsystem",
  "purpose": "Convert, chunk, embed, and persist documents",
  "entrypoints": ["router.py", "service.py"],
  "owns": ["**/*.py"],
  "public_interfaces": ["POST /rag/sources/{id}/sync"],
  "invariants": [
    "Embedding dimensions must match the pgvector schema",
    "Re-ingestion must preserve stable document identity"
  ],
  "allowed_dependencies": ["docling-serve", "minio", "embedding-runtime", "postgres-pgvector"],
  "forbidden_dependencies": ["frontend"],
  "configuration_refs": ["S3_RAG_ENDPOINT", "S3_RAG_BUCKET", "EMBEDDING_MODEL"],
  "decision_refs": ["ADR-004", "ADR-007"],
  "validation": ["uv run pytest tests/rag"],
  "graph_hints": {
    "entry_symbols": ["rag_ingest", "sync_source"],
    "exclude": ["tests/fixtures/**"]
  }
}
```

Rules for child annotations:

- Inherit defaults from the nearest governed ancestor.
- Allow only documented fields to override inherited behavior.
- Prohibit a child from weakening security or compatibility policy without an explicit exception record.
- Keep intent and invariants local; do not duplicate facts extractable from source.
- Use stable component IDs independent of directory names.
- Treat file moves as graph identity updates, not new unrelated components.
- Compile all child annotations into the effective repository manifest.

## Governance documents and responsibilities

### `AGENTS.md`

Keep it concise, ideally below 150 lines. It should point to the workflow and contain the non-negotiable entry rules:

1. Run the governance preflight.
2. Start a governed change session with a stated reason.
3. Retrieve task-specific context instead of recursively reading governance files.
4. Make the smallest coherent change.
5. Synchronize manifests and graph evidence.
6. Run required validation.
7. Finalize the change record and regenerate `Summary.md`.
8. Run the read-only full check.

Agent-specific configuration files should point to `AGENTS.md`; they should not duplicate the entire policy.

### `catalog.json`

This is the compact navigation index. For every manifest, policy, graph report, and history class, record its path, description, authority, update method, and normal read conditions. Agents inspect this file before selecting deeper material.

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
- validations performed and their results;
- security and compatibility impact;
- risks, limitations, and follow-up work;
- rollback procedure;
- ADR references;
- governance schema/tool versions.

Automation can populate changed files, manifests, and test results. It cannot reliably infer the reason; that must be supplied at the beginning or completion of the change.

### ADRs

Use ADRs for decisions that should remain understandable after individual modifications disappear from the recent summary: remote Docling architecture, embedding dimension contracts, MCP privilege policy, service exposure strategy, migration guarantees, and similar durable choices.

### Evaluations

Store evidence about governance behavior rather than ordinary application changes: false-positive reviews, missed dependency edges, context retrieval quality, policy promotion results, ranking calibration, and schema migration tests.

## Manifest model

Begin with seven manifests.

### Components

Logical backend, frontend, agent, RAG, infrastructure, MCP, data, and observability components; owned paths; entry points; dependencies; configuration references; contracts; and validation commands.

### Services

Resolved across Compose variants: images and digests, build contexts, profiles, health checks, dependency ordering, ports, networks, volumes, GPU requirements, secrets references, and internal versus host exposure.

### Configuration

Environment-variable names, type, requiredness, default classification, secret status, declaring source, consuming components, applicable profiles, and validation rules. Never store actual secret values.

### Data stores

PostgreSQL/pgvector objects, Alembic chains, MinIO buckets, Redis logical databases, vector dimensions, persistence volumes, and backup/retention classification.

### AI runtime

Agents, models, providers, prompts, tools, MCP servers, permissions, embedding dimensions, cache fingerprints, observability, fallback behavior, and human-confirmation requirements.

### Interfaces

References to OpenAPI, WebSocket event schemas, frontend API clients, background-task payloads, MCP endpoints, and external integration contracts. Reuse existing schemas rather than manually reproducing them.

### Tests

Mappings among tests, components, symbols, contracts, policies, and scenario coverage. This enables impact analysis to propose the smallest adequate validation set.

## Repository graph and visualizers

### Extraction

Use Python AST or LibCST/tree-sitter for Python, the TypeScript compiler API/ts-morph or tree-sitter for TypeScript, and dedicated parsers for TOML, YAML, Compose, OpenAPI, Alembic, and package metadata.

AST extraction should identify modules, classes, functions, imports, calls, inheritance, decorators, routes, models, tasks, tools, and tests. Structured extractors add services, configuration, migrations, endpoints, storage, and deployment relationships.

### Typed graph

Representative node types:

- directory, file, module, class, function;
- frontend page, component, API client;
- API route and WebSocket event;
- agent, prompt, model, MCP server, tool;
- background task;
- database model, migration, table, vector index;
- Compose service, network, volume;
- environment variable;
- test, policy, ADR, and change record.

Representative edges:

- `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`;
- `DEPENDS_ON`, `CONFIGURES`, `EXPOSES`, `IMPLEMENTS`;
- `READS_FROM`, `WRITES_TO`, `MIGRATES`;
- `INVOKES_TOOL`, `ROUTES_TO`, `TESTS`;
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

The site graph should connect a Next.js page or action to its component, API client, FastAPI route, service, agent/RAG operation, data store, MCP, or external provider.

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
| Centrality × weak test coverage | High-risk insufficiently tested code |

PageRank is a relevance and dependency-centrality signal, not a quality score or automatic merge decision. Compute rankings within typed graph views or use configurable edge weights; generic utilities otherwise dominate misleadingly.

### Visualizations

Generate bounded, task-focused views rather than rendering the whole graph:

- architecture map;
- Next.js-to-FastAPI site map;
- proposed-change impact map;
- environment-variable flow map;
- database migration and data-flow map;
- agent/MCP permission map;
- dependency-cycle and hotspot reports.

Visual outputs are generated on demand or attached to CI runs. The AI receives the small subgraph used to generate the view, not the entire SQLite graph.

### Static and runtime evidence

Static analysis cannot fully resolve dynamic imports, decorators, dependency injection, reflection, framework registration, runtime Compose profiles, or dynamically constructed routes. Supplement it with:

- generated OpenAPI and runtime route enumeration;
- resolved Compose configuration;
- MCP initialization and `tools/list` results;
- test instrumentation;
- carefully scoped Logfire traces;
- runtime dependency-registration inspection.

Compare declared, static, and observed edges. Treat runtime-only privileged edges as high-priority findings.

## Governance CLI

Expose one Python module through `uv run python -m repo_governance` or a short project script such as `uv run governance`.

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

- `bootstrap`: inventory the local generated repository, record generator provenance, establish a reviewed baseline, and refuse destructive normalization.
- `preflight`: confirm schema/tool compatibility and report unexplained drift before a change begins.
- `scan`: parse authoritative sources and rebuild the graph cache and generated candidates.
- `sync`: atomically update generated/effective manifests, small graph reports, and derived documentation.
- `check`: perform a read-only regeneration comparison plus schema, reference, policy, security, compatibility, process, and idempotence validation.
- `context`: select a token-bounded task briefing using path proximity, graph reachability, policy risk, architectural centrality, history relevance, and recency.
- `impact`: return affected symbols, components, contracts, configuration, data, tests, ADRs, and likely blast radius.
- `explain`: return a compact component briefing.
- `change start`: capture rationale before it can be lost and open a temporary change session outside committed history.
- `change finish`: collect diff-derived facts and validation evidence, ask for any missing human judgment, then create a validated permanent record.
- `summary`: regenerate the bounded `Summary.md`.
- `visualize`: generate a bounded subgraph and corresponding view.
- `doctor`: diagnose missing parsers, malformed configuration, stale caches, unsupported syntax, and repository inconsistencies.
- `migrate`: upgrade governance schemas and documents explicitly.
- `evaluate`: run historical and synthetic governance scenarios.

### Determinism and safety requirements

- Stable keys, path normalization, and list ordering.
- Atomic writes through temporary files followed by replacement.
- Read-only check mode with nonzero exit codes.
- Idempotence test: two sync operations produce identical bytes.
- No secret values in manifests, graph metadata, logs, or change records.
- Generated/curated ownership enforced at the field or document boundary.
- Provenance and confidence for extracted graph edges.
- Parser failures reported as unknowns, never silently interpreted as absence.
- Bounded traversal depth and token budgets for context queries.

## AI modification workflow

### Before modification

1. Run `governance preflight`.
2. Start a change session with the task summary and actual reason.
3. Run `governance context` for the proposed paths and task.
4. Read only the returned manifest slices, local annotations, ADRs, recent related records, and policies.
5. State affected components, contracts, invariants, security boundaries, and planned validation.

### During modification

1. Make the smallest coherent change.
2. Query impact again if new paths or components enter scope.
3. Do not manually edit generated manifests or `Summary.md`.
4. Add or update curated intent only when architectural meaning changes.
5. Create an ADR only for durable decisions, not ordinary implementation detail.

### After modification

1. Run `governance scan` and inspect discovered relationship changes.
2. Run `governance sync`.
3. Review the generated diff rather than accepting it blindly.
4. Run graph-selected targeted tests and any policy-mandated tests.
5. Run relevant Compose, environment, migration, MCP, persistence, GPU, and security checks.
6. Finish the change record with behavioral effects, evidence, risks, limitations, and rollback.
7. Regenerate `Summary.md`.
8. Run `governance check --full`.
9. Report remaining uncertainty rather than inventing missing facts.

If a generated manifest disagrees with an authoritative source, treat the manifest as stale. Never change application behavior merely to make a stale generated file pass.

## Automated enforcement

### Editor and manual feedback

- Associate JSON files with their `$schema` for immediate validation.
- Provide short Makefile/project commands such as `make governance-check` and `make governance-context`.
- Keep error messages actionable: source, violated policy, evidence, and repair command.

### Pre-commit

Run a fast affected-file check:

- JSON and schema validity;
- local annotation validity;
- secret-value scanning;
- generated-file direct-edit detection;
- fast drift detection for touched components;
- required change-session or change-record presence;
- `Summary.md` synchronization when history changes.

Keep this fast enough that developers do not bypass it.

### Pull-request CI

1. Rebuild generated manifests and graph cache in a clean environment.
2. Fail on uncommitted generated diffs.
3. Validate schemas and cross-file references.
4. Run architecture, security, compatibility, and process policies.
5. Confirm a change record exists when governed paths changed.
6. Run idempotence and golden-fixture tests.
7. Select and run affected application tests, then required broad suites.
8. Compare OpenAPI, migrations, configuration, Compose profiles, MCP permissions, and persistence contracts.
9. Generate impact and visualization artifacts for review.
10. Require explicit review for breaking contracts, privilege expansion, data migrations, policy weakening, or governance-kernel changes.

### Scheduled analysis

Run deeper non-blocking analysis periodically or manually:

- orphan and dead-route detection;
- centrality/hotspot recalculation;
- low-coverage critical-node analysis;
- runtime-versus-static graph comparison;
- stale exception detection;
- generator/upstream divergence assessment;
- governance performance and false-positive review.

Do not let scheduled automation silently modify application code or activate new blocking policy.

### Generator-upgrade workflow

Because the project originates from a moving generator, record generator version, selected generation options, known upstream commit/release when available, and local divergence. For an upgrade:

1. Build a clean project from the proposed generator version using preserved options.
2. compare its manifests and graph with the local repository;
3. classify upstream changes, local changes, and conflicts;
4. apply upgrades through explicit migrations or reviewed patches;
5. run the full governance and application validation matrix;
6. record the upgrade decision, compatibility impact, and rollback.

## Initial policies for this harness

### Configuration

- Active configuration variables must be declared, typed, and consumed.
- `.env` parsing must reject malformed concatenated lines.
- Required variables may not be empty in profiles where their component is active.
- Secret variables may be named and classified but never copied into governance output.

### PostgreSQL and pgvector

- Qwen3 embedding dimensionality remains `1024` unless an explicit migration changes the complete contract.
- Model, cache fingerprint, vector column, index, ingestion code, and query code must agree on dimension.
- ORM schema changes require an Alembic revision.
- Migration chains must be contiguous and reversible where feasible.

### Redis

- Logical DB allocations remain explicit: DB 0 general cache, DB 1 Taskiq broker, DB 2 Taskiq results, and DB 3 embedding cache.
- New consumers cannot silently reuse an allocated logical DB.

### MinIO

- General and RAG bucket purposes remain distinct.
- Bucket names, endpoints, credentials references, and persistence declarations must align across settings and Compose.

### Docling

- Docling MCP uses the intended remote Docling Serve connection.
- Conversion health and a harmless conversion test are required after configuration changes.
- GPU behavior, model cache, worker count, and OOM/fallback expectations remain explicit.

### MCPs

- GitHub MCP remains read-only with a limited tool allowlist unless privilege expansion is explicitly approved.
- MCP services must pass initialize, `tools/list`, and a harmless-tool check.
- Tool annotations, authorization level, reachable resources, transport, and exposure must be mapped.
- Write-capable or externally consequential tools require explicit human authorization policy.

### Compose and networking

- Validate every supported Compose profile/override matrix.
- Detect duplicate host ports and unintended host publication.
- Require health checks and persistent volumes where expected.
- Pin production images according to the chosen digest policy.
- Preserve internal-versus-external service boundaries and account for the existing IIS/port-80 environment where relevant.

### Taskiq

- Broker/result allocations, worker concurrency, scheduler behavior, retries, and restart behavior must remain testable and explicit.

### Interfaces and frontend

- API-contract changes must identify affected frontend clients and pages.
- Frontend-to-backend build/version provenance must be checkable.
- WebSocket event changes require producer/consumer compatibility validation.

## Context management rules

1. Never instruct an agent to recursively read `governance/`.
2. Keep `AGENTS.md` concise and stable.
3. Keep `catalog.json` small enough to read routinely.
4. Bound `Summary.md` to current state and recent material history.
5. Query older records and ADRs by component, path, policy, or change relationship.
6. Execute schemas and policies without loading their complete contents into model context.
7. Keep the full graph in SQLite outside normal context.
8. Return bounded graph slices with explicit token budgets.
9. Put `.governance.json` only at architectural boundaries.
10. Exclude generated graph databases and large rendered views from generic semantic indexing.
11. Prefer exact structured queries over repository-wide text search.
12. Include provenance and uncertainty in returned context so agents know what requires source verification.

## Recursive improvement and self-governance

The governance system should improve from actual use, but it must not be able to silently rewrite the rules that judge it.

### Minimal governance kernel

Treat these as the protected kernel:

- governance version and migration rules in `governance.toml`;
- change-record schema;
- generated-versus-curated ownership rules;
- secret-handling rules;
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
- add a Compose service;
- alter an embedding dimension;
- add or remove a FastAPI route;
- change a Next.js API consumer;
- add an Alembic migration;
- expand an MCP tool allowlist;
- introduce a dependency cycle;
- move a governed subsystem;
- change a governance schema or ranking weight.

Each scenario defines expected affected components, policies, tests, context documents, and findings. Historical change records can seed new scenarios.

### Feedback metrics

Track only metrics that lead to decisions:

- false-positive and missed-finding counts;
- percentage of generated drift explained;
- context output size and relevance;
- impact-set accuracy against post-change reality;
- policy execution time;
- parser unsupported-syntax rate;
- stale exception count;
- critical-node test coverage;
- governance changes rolled back.

Do not optimize PageRank scores themselves. Optimize the usefulness and accuracy of decisions supported by the graph.

### Safe automation boundary

Automation may regenerate derived files, propose policy changes, produce evaluation reports, and draft patches. It may not automatically weaken a policy, broaden an MCP permission, accept a security exception, migrate production data, or approve its own governance change.

## Simplified implementation strategy

Avoid building the complete graph platform before the basic governance workflow provides value. Use a narrow vertical slice and expand it.

### Phase 0 — Local repository audit

1. Inventory the exact generated repository tree and working state.
2. Record generator version/options and current Compose variants.
3. Identify actual authoritative sources and generated metadata.
4. Catalog existing migrations, settings, RAG, Taskiq, MCP, Docker, frontend, and observability files.
5. Define protected data and secret boundaries.

**Exit criterion:** reviewed authority matrix and repository provenance; no files or volumes changed during discovery.

### Phase 1 — Minimal governance kernel

1. Add concise `AGENTS.md` routing instructions.
2. Add `governance.toml`, `catalog.json`, and schema versioning.
3. Create schemas for components, configuration, services, and change records.
4. Add `self-governance.json` and generated/curated ownership rules.
5. Add a minimal deterministic CLI with `preflight`, `sync`, and `check`.

**Exit criterion:** invalid governance documents and direct edits to generated files are detected; two syncs are byte-identical.

### Phase 2 — Reviewed baseline manifests

1. Manually establish components and architectural boundaries.
2. Add child `.governance.json` files only at those boundaries.
3. Create reviewed baseline service and configuration manifests.
4. Add current critical invariants for pgvector, Redis, MinIO, Docling, Taskiq, MCPs, and Compose.

**Exit criterion:** the effective manifest accurately describes the current system without attempting complete symbol mapping.

### Phase 3 — Deterministic extraction

1. Extract Compose services and profile matrices.
2. Extract Pydantic settings and `.env.example` declarations.
3. Extract dependencies, Alembic revisions, OpenAPI references, and frontend routes.
4. Reproduce the manually reviewed baseline.
5. Add golden fixtures, malformed-input tests, and atomic writes.

**Exit criterion:** generated manifests reproduce the reviewed baseline and correctly surface deliberate fixture changes.

### Phase 4 — Change history and bounded context

1. Implement change start/finish sessions.
2. Generate structured permanent change records.
3. Generate bounded `Summary.md`.
4. Add ADR indexing.
5. Implement component/path-based `context`, `impact`, and `explain` using manifests before the full AST graph exists.

**Exit criterion:** an AI agent can complete a normal configuration or component change without recursively inspecting governance files.

### Phase 5 — Local and CI enforcement

1. Add fast pre-commit checks.
2. Add clean-environment full checks in CI.
3. Require records for governed changes.
4. Add schema, reference, architecture, security, compatibility, and process checks.
5. Generate review artifacts without automatically changing the branch.

**Exit criterion:** stale manifests, unrecorded governed changes, malformed configuration, secret leakage, and critical invariant violations block review appropriately.

### Phase 6 — AST graph and impact analysis

1. Define stable node IDs and typed edges.
2. Implement Python and TypeScript import/module graphs.
3. Add routes, tasks, models, tests, tools, and configuration relationships.
4. Store the full graph in SQLite.
5. Add reverse reachability, cycle detection, orphan detection, and shortest-path explanations.
6. Feed bounded graph slices into `context` and `impact`.

**Exit criterion:** impact analysis improves test and context selection on the evaluation scenarios without unacceptable false positives.

### Phase 7 — Site maps, algorithms, and visualizers

1. Connect Next.js pages/actions to API clients and FastAPI routes.
2. Connect routes to services, agents, RAG, data stores, MCPs, and external providers.
3. Add personalized PageRank, betweenness, community detection, and churn/coverage risk analysis.
4. Generate focused architecture, site, impact, configuration, migration, and security views.
5. Keep all algorithmic findings advisory initially.

**Exit criterion:** visualizations and rankings explain real dependencies and review risk better than a directory tree without becoming merge gates by default.

### Phase 8 — Runtime evidence

1. Import generated OpenAPI and runtime route enumeration.
2. Inspect resolved Compose profiles.
3. Record MCP discovery and harmless-tool tests.
4. Integrate selected test instrumentation and Logfire relationship evidence.
5. Compare declared, static, and observed graphs.

**Exit criterion:** dynamic relationships and privileged runtime-only edges are visible with provenance and confidence.

### Phase 9 — Recursive governance improvement

1. Add policy lifecycle states and shadow mode.
2. Add evaluation records and scenario replay.
3. Measure false positives, misses, context size, unsupported syntax, and runtime.
4. Add explicit governance migrations and compatibility testing.
5. Require governance-change records and protected review for kernel changes.
6. Promote only demonstrated reliable policies from advisory to blocking.

**Exit criterion:** the system can propose and validate improvements to itself while remaining unable to silently weaken its own protections.

### Phase 10 — Generator upgrade intelligence

1. Reconstruct clean generated baselines from preserved options.
2. Compare upstream and local manifests/graphs.
3. Classify additions, removals, conflicts, and local divergence.
4. Produce reviewed upgrade plans with validation and rollback.

**Exit criterion:** generator upgrades become controlled reconciliations rather than manual repository-wide guesswork.

## Definition of success

The governance system is successful when:

- an AI agent can orient itself through one small entry point;
- task context is bounded, relevant, and source-backed;
- generated manifests never become an accidental second source of truth;
- architectural intent survives refactors and agent changes;
- modification reasons and validation evidence remain discoverable;
- cross-file errors are caught before services start;
- impact analysis selects appropriate tests and reviewers;
- visual maps explain the system without requiring the whole graph in context;
- policies distinguish uncertainty from violations;
- the governance system can evolve through evaluation, migration, and rollback;
- no automation can silently relax the rules that govern it.

The practical starting point is Phase 0 followed by the Phase 1 kernel. The AST graph, PageRank, and visualizers should build on a correct authority model and deterministic manifests, not precede them.

## Reference standards and project context

- JSON Schema Draft 2020-12: <https://json-schema.org/draft/2020-12>
- Pydantic JSON Schema generation: <https://docs.pydantic.dev/latest/concepts/json_schema/>
- Pre-commit framework: <https://pre-commit.com/>
- Full-Stack AI Agent Template: <https://github.com/vstorm-co/full-stack-ai-agent-template>
- Template agent guidance: <https://github.com/vstorm-co/full-stack-ai-agent-template/blob/main/AGENTS.md>
