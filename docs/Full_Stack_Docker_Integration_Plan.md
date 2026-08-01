# Full Stack Local Docker Integration Plan

Date: 2026-08-01

Scope: plan the changes to the generated `full_stack` repository before running its Makefile/bootstrap flow. This is an implementation plan only; none of the uploaded project files have been changed.

## 1. Recommended end state

Use the current project as a Compose application with four layers:

1. **Application:** FastAPI backend on internal port 8100, Next.js frontend on 3000, and Taskiq worker/scheduler processes built from the same backend image.
2. **State:** `pgvector/pgvector:pg17`, Redis 8.10.0, MinIO, and shared named volumes.
3. **AI/document services:** Docker Model Runner on the Windows host for Qwen embeddings and one CUDA Docling Serve container for all document conversion.
4. **Agent tools:** Streamable-HTTP MCP services for Docling and GitHub, plus Browserless Chromium behind a Chrome DevTools MCP HTTP gateway. Traefik supplies optional HTTPS ingress; backend-to-sidecar traffic stays HTTP over a private Compose network.

The critical design distinction is:

- The RAG ingestion pipeline calls **Docling Serve's REST API directly**. It must not ask an LLM to invoke an MCP tool for routine parsing.
- The agent connects to **Docling MCP** when it needs interactive document tools. Docling MCP runs in remote mode and delegates conversion to the same Docling Serve container, so models are not loaded twice.

## 2. Corrections and gating decisions

### 2.1 The installed Qwen model is 4B, not 8B

The provided `docker model list` shows `qwen3-embedding` with 4.02B parameters and a 2.32 GiB artifact. That matches Docker's `ai/qwen3-embedding:4B-Q4_K_M`/`latest` variant, whose native vector size is up to 2,560. It is not Qwen3-Embedding-8B, whose native vector size is 4,096. Docker's published model page currently lists a 4B Q4 variant, not an 8B embedding artifact.

Target model order:

1. Existing Qwen: exact ID returned by `GET /engines/v1/models`, expected to resolve to `ai/qwen3-embedding:4B-Q4_K_M` or an equivalent local alias.
2. `mxbai-embed-large` as the first fallback because it natively emits 1,024 dimensions and is small.
3. `snowflake-arctic-embed-l-v2-vllm` only after its Docker Model Runner endpoint and engine compatibility are tested.

Do not write the model ID into a migration from the display name alone. Resolve and test the exact request ID first.

### 2.2 Dimension handling must be proven before the first RAG table is created

The generated backend's OpenAI provider currently calls `embeddings.create(model=..., input=...)` without a `dimensions` argument, and its model-to-dimension map does not know Qwen. Its pgvector implementation creates `vector(dim)` and an HNSW index dynamically. That means changing only `.env` will not work.

The implementation must:

- add `EMBEDDING_DIMENSION=1024` to application settings;
- send `dimensions=1024` to Docker Model Runner;
- verify the returned vector length;
- if Docker Model Runner returns the Qwen 4B native 2,560-vector, truncate to the first 1,024 Matryoshka dimensions and L2-normalize in application code;
- fail closed if the returned vector contains fewer than 1,024 elements or non-finite values.

At 1,024 dimensions, the existing `vector` HNSW index is within pgvector's 2,000-dimension `vector` limit. Do not create a 2,560-dimensional `vector` HNSW index. A possible alternative is `halfvec`, but it is unnecessary if the 1,024-dimensional Qwen path is implemented and tested.

### 2.3 MCP is not globally HTTP-only, but this generated project is URL-only

MCP officially supports local stdio and remote Streamable HTTP. The generated backend's MCP configuration accepts `name`, `url`, `headers`, and `allowed_tools`; it does not accept a local command/args process specification. Its client infers SSE only for an `/sse` path and uses Streamable HTTP for other URLs.

Therefore, MCP servers attached to this application must expose HTTP/SSE. This affects Chrome DevTools because its official MCP server still supports only stdio and needs a gateway. Docling MCP natively supports `--transport streamable-http`. The current GitHub MCP image natively supports its `http` subcommand.

### 2.4 Internal HTTP and edge HTTPS have different jobs

Use these internal URLs from the backend:

- `http://docling-mcp:8000/mcp`
- `http://chrome-devtools-mcp:8000/mcp`
- `http://github-mcp:8082`
- `http://docling-serve:5001`

Do not send container-to-container traffic out through Traefik merely to make it HTTPS. Optional HTTPS routes are for host or remote MCP clients. Chrome/Browserless and unauthenticated Docling MCP must not be publicly exposed by default.

### 2.5 Do not run `make quickstart` yet

The uploaded files are not currently coherent enough for a safe first bootstrap:

- `backend/.env` has several variables concatenated onto the same physical line.
- Compose uses plain PostgreSQL 16, not pgvector 17.
- Compose uses Redis 7, not Redis 8.10.0.
- no Docling, MinIO, Taskiq worker, Taskiq scheduler, or MCP services exist in the attached Compose files;
- the Dockerfile installs Node and LiteParse, which must be removed;
- the Makefile claims a Traefik production stack while the production Compose file says external Nginx and defines no Traefik service;
- `run-prod` uses port 8000 while the rest of this project uses 8100;
- the production network is `internal: true`, which would prevent the app from reaching OpenRouter, Tavily, Docker Model Runner, and other external APIs unless a separate egress network is added.

## 3. Docker asset disposition

| Local asset | Decision | Role |
|---|---|---|
| `pgvector/pgvector:pg17` | Use | Replaces both plain Postgres images; application DB and pgvector store share one server. |
| `postgres:17` | Do not use | Redundant once the pgvector PG17 image is selected. |
| `redis:8.10.0` | Use | App cache/state, Taskiq broker/results, and embedding L1 cache in separate logical DBs. |
| `minio-local:RELEASE.2025-10-15T17-29-55Z` | Use | S3-compatible application file storage and RAG source storage. |
| `ghcr.io/docling-project/docling-serve-cu128:v1.29.0` | Use | Sole document-conversion engine; one GPU worker initially. |
| `ghcr.io/docling-project/docling-serve-cpu:v1.29.0` | Keep as manual fallback | Selected only with an override if CUDA fails; not started alongside CUDA. |
| `ghcr.io/browserless/chromium:latest` | Use, but pin digest | Long-lived Chromium/CDP service; internal port 3000 only. |
| `ghcr.io/github/github-mcp-server:latest` | Use, but pin digest | Run `http` transport on internal port 8082, preferably read-only at first. |
| `traefik:v3.7.10` | Use | Optional local HTTPS and production ingress. |
| `dpage/pgadmin4:latest` | Optional profile | Use only if the richer database UI is needed. Pin digest before use. |
| `sosedoff/pgweb:latest` | Prefer for lightweight dev profile | Simple DB inspection on host port 8081. Pin digest. Do not run simultaneously with pgAdmin by default. |
| `edoburu/pgbouncer:latest` | Defer | Add only after measuring connection pressure. Asyncpg/prepared-statement compatibility and migration routing must be configured deliberately. |
| `nvidia/cuda:12.8.0-base-ubuntu24.04` | Do not run | Base/debug image, not an application service. |
| Docker Model Runner models | Use outside Compose | Model Runner is a Docker Desktop/Engine host service, not a normal Compose container or volume. |

Mutable `latest` tags should be replaced by repository digests obtained from `docker image inspect` or locally retagged with a stable project tag before the Compose files are committed.

## 4. Network and port plan

The service name and internal port are the stable contract. Host bindings are development conveniences and should bind to `127.0.0.1`, not every interface.

| Service | Compose DNS / internal port | Development host binding | Edge exposure |
|---|---:|---:|---|
| Backend | `app:8100` | `127.0.0.1:8100` | `https://api.<domain>` |
| Frontend | `frontend:3000` | `127.0.0.1:3000` | `https://<domain>` |
| PostgreSQL/pgvector | `db:5432` | `127.0.0.1:5432` | Never |
| Redis | `redis:6379` | `127.0.0.1:6379` | Never |
| MinIO API | `minio:9000` | `127.0.0.1:9000` | Optional/private |
| MinIO console | `minio:9001` | `127.0.0.1:9001` | Optional/private |
| Docling Serve | `docling-serve:5001` | `127.0.0.1:5001` | Optional/private |
| Browserless | `browserless:3000` | None by default; optional `127.0.0.1:3001` | Never public |
| Docling MCP | `docling-mcp:8000/mcp` | `127.0.0.1:8201` | Optional authenticated HTTPS |
| Chrome DevTools MCP gateway | `chrome-devtools-mcp:8000/mcp` | `127.0.0.1:8202` | Never public by default |
| GitHub MCP | `github-mcp:8082` | `127.0.0.1:8203` | Optional authenticated HTTPS |
| pgweb | `pgweb:8081` | `127.0.0.1:8081` | Never |
| Traefik dashboard | `traefik:8080` | `127.0.0.1:8080` | Never public without auth |
| Docker Model Runner | Host service | `localhost:12434` | Never public |

Browserless and the frontend both use container port 3000, which is harmless on the Docker network but prevents publishing both as host port 3000. Browserless therefore remains internal or uses host port 3001 only in a diagnostic profile.

Use two production networks:

- `data-internal` with `internal: true`: DB, Redis, MinIO, app, Taskiq processes, and services that need data access.
- `service-egress`: app, Taskiq, Docling Serve, MCP services, frontend, and Traefik as required. This non-internal network permits OpenRouter/Tavily/GitHub/Model Runner access.

DB and Redis join only `data-internal`. Traefik never joins `data-internal`.

## 5. Compose organization

Refactor the current duplicated Compose files into a base-plus-overrides model:

- `docker-compose.yml`: canonical services, networks, volumes, health checks, and internal ports.
- `docker-compose.dev.yml`: bind-mounted backend source, reload command, localhost port publications, developer tools, and optional profiles.
- `docker-compose.prod.yml`: Traefik, production commands/resource limits, no direct DB/Redis publications, secrets, and dual-network rules.
- `docker-compose.frontend.yml`: either remove after folding the frontend into the base file under a `frontend` profile, or reduce it to a true override that joins the same named network. Do not maintain a second standalone network topology.
- optional `docker-compose.engine.yml`: only for Docker Engine hosts that require `model-runner.docker.internal:host-gateway` and `http://model-runner.docker.internal:12434/engines/v1`. Docker Desktop uses `http://model-runner.docker.internal/engines/v1` without port 12434 from containers.

Suggested profiles:

- default/core: `app`, `db`, `redis`, `minio`, `docling-serve`, `taskiq-worker`, `taskiq-scheduler`;
- `frontend`;
- `mcp`: `docling-mcp`, `browserless`, `chrome-devtools-mcp`, `github-mcp`;
- `db-ui`: pgweb (or pgAdmin, but not both by default);
- `edge`: Traefik and HTTPS routes.

Compose dependencies must use health conditions where supported:

- app/Taskiq depend on healthy DB and Redis;
- app/Taskiq ingestion depends on healthy Docling Serve;
- Docling MCP depends on healthy Docling Serve;
- Chrome MCP depends on Browserless;
- nothing can use `depends_on` for Docker Model Runner because it is outside the Compose project. A Makefile preflight owns that check.

## 6. Shared volumes and cache ownership

Recommended volumes:

| Volume | Consumers | Notes |
|---|---|---|
| `postgres_data` | DB | Project-owned persistent database volume. |
| existing `redis-data` | Redis | Declare with explicit name. Mark external if `docker-clean` must never delete it. Inspect it before reuse. |
| `minio_data` | MinIO | New project-owned volume. |
| `media_data` | app + Taskiq worker | Required so jobs can process files uploaded by the API. |
| `models_cache` | app + Taskiq worker | Retains the cross-encoder reranker or other local HF artifacts; not Docker Model Runner models. |
| existing `docling-models` | Docling Serve | Mount at the Docling image's cache root only after inspecting contents. Avoid hiding pre-baked model files with an incorrectly mounted empty volume. |
| `docling_mcp_cache` | Docling MCP | Generated files/cache directory; note that Docling MCP's converted-document dictionary is still process memory. |

For Docling, first inspect the current image and volume paths. The CUDA image normally contains pre-fetched artifacts under its application cache. A named volume mounted over an empty target can preserve downloaded artifacts, but mounting an empty pre-existing volume over the image's populated model directory can also hide those artifacts. The implementation gate is: inspect both, copy/prefetch deliberately if needed, then set `DOCLING_ARTIFACTS_PATH`, `HF_HOME`, and `HUGGINGFACE_HUB_CACHE` consistently.

Docker Model Runner owns its own model storage. Do not try to attach a Compose volume to it. Embedding **result caching**, described next, replaces the proposed prompt-cache volume.

## 7. Embedding provider and two-level result cache

### 7.1 Environment contract

After the exact model ID preflight, the host-run backend should resemble:

```dotenv
OPENAI_API_KEY=local-docker-model-runner
EMBEDDING_BASE_URL=http://localhost:12434/engines/v1
EMBEDDING_MODEL=ai/qwen3-embedding:4B-Q4_K_M
EMBEDDING_MODEL_VERSION=4B-Q4_K_M
EMBEDDING_DIMENSION=1024
EMBEDDING_QUERY_INSTRUCTION=Given a user query, retrieve relevant passages, documentation, or code that answer the query.
EMBEDDING_DOCUMENT_INSTRUCTION=
EMBEDDING_CACHE_URL=redis://localhost:6379/3
EMBEDDING_CACHE_TTL_SECONDS=604800
```

Compose overrides only the addresses:

```yaml
EMBEDDING_BASE_URL: http://model-runner.docker.internal/engines/v1
EMBEDDING_CACHE_URL: redis://redis:6379/3
```

The exact Qwen query text should be formatted consistently, for example `Instruct: <instruction>\nQuery: <normalized query>`. Documents should not accidentally receive the query prefix.

### 7.2 Canonical cache key

Do not hash ambiguous string concatenation. Serialize a canonical, versioned JSON object with sorted keys and UTF-8 encoding, then SHA-256 it:

```json
{
  "schema": 1,
  "model_id": "ai/qwen3-embedding",
  "model_version": "4B-Q4_K_M",
  "dimensions": 1024,
  "input_kind": "query|document",
  "instruction": "exact instruction",
  "normalized_input": "exact normalized text"
}
```

Normalization should be explicitly versioned: Unicode NFC, CRLF/CR to LF, and removal of only agreed leading/trailing whitespace. Do not collapse internal whitespace because it can change code semantics.

### 7.3 Cache lookup order

1. Deduplicate keys within the request.
2. Redis L1 lookup using `emb:v1:<sha256>` in logical DB 3.
3. PostgreSQL L2 lookup in `embedding_cache` by SHA-256 primary key.
4. Batch all misses into one Docker Model Runner embeddings call when possible.
5. Validate/truncate/normalize the vectors.
6. Upsert PostgreSQL, then populate Redis with a TTL.

Add a short Redis `SET NX EX` lock per missing key to reduce duplicate work when Taskiq jobs ingest the same content concurrently. PostgreSQL remains the durable owner; Redis is an acceleration layer.

Use Redis logical DBs deliberately:

- DB 0: general application cache/session state;
- DB 1: Taskiq broker;
- DB 2: Taskiq result backend (prefer separate from broker if supported by the generated Taskiq configuration);
- DB 3: embedding L1 cache.

Redis logical DBs are namespaces, not memory isolation. Start without an eviction policy that could delete Taskiq data. If embedding volume later becomes large, split embedding Redis into a second instance rather than enabling a global eviction policy on the queue server.

Enable AOF persistence:

```yaml
command: ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec"]
```

### 7.4 PostgreSQL schema

Create an explicit Alembic migration (not an ad hoc startup table) for:

- `CREATE EXTENSION IF NOT EXISTS vector`;
- `embedding_cache` with SHA-256 key, model ID/version, dimensions, input kind, instruction hash, normalized-input hash, `vector(1024)`, timestamps, and optional hit count;
- a normal B-tree primary-key lookup only—no vector index is needed for cache retrieval;
- a `rag_collection_metadata` registry containing collection name, embedding fingerprint, dimension, and creation/update timestamps.

The collection registry prevents a query generated by a new model/instruction from being compared with document vectors generated by an older configuration. Refuse search/ingest on fingerprint mismatch and require an explicit re-embedding operation.

## 8. Replace LiteParse with Docling Serve

### 8.1 Backend behavior

Add a `DoclingServeParser` in the RAG document-processing layer. It should:

- use one reusable `httpx.AsyncClient` or the Docling service client;
- upload local files to Docling Serve's stable v1 file-conversion endpoint;
- request Markdown and/or Docling JSON;
- convert the response into the project's `Document`/`DocumentPage` objects while retaining page/table metadata;
- use explicit timeouts, retry only safe transient failures, and enforce `MAX_UPLOAD_SIZE_MB`;
- propagate a clean conversion error to the Taskiq job/API;
- expose a health/warm-up check.

Docling Serve handles PDF, DOCX, PPTX, XLSX, images/OCR, and related formats. Keep native TXT/Markdown parsing locally because sending plain text through a GPU service adds no value.

Set:

```dotenv
DOCLING_SERVE_URL=http://localhost:5001
DOCLING_SERVE_TIMEOUT_SECONDS=600
PDF_PARSER=docling
CHAT_PDF_PARSER=docling
RAG_ENABLE_OCR=true
```

Compose overrides `DOCLING_SERVE_URL=http://docling-serve:5001`.

### 8.2 Complete LiteParse removal checklist

- Dockerfile: remove NodeSource, Node.js, and `npm install -g @llamaindex/liteparse`.
- `backend/pyproject.toml`: remove `liteparse`; add only the lightweight Docling service client if chosen.
- regenerate `backend/uv.lock` with `uv lock`/`uv sync` after dependency changes.
- remove LiteParse imports, parser classes, factory branches, formats, and timeouts from `app/services/rag/documents.py` and `config.py`.
- replace parser literals/comments in `app/core/config.py`.
- remove `LITEPARSE_OCR_SERVER_URL`, `LITEPARSE_OCR_LANGUAGE`, and `LITEPARSE_TIMEOUT_SECONDS` from `.env`, `.env.example`, `ENV_VARS.md`, README, tests, and agent skill documentation.
- update supported-format API/UI tests and RAG ingestion tests for Docling.
- search the entire repository with `rg -ni 'liteparse|llamaindex/liteparse'` and require zero intentional references.

## 9. Docling deployment and MCP sidecar

### 9.1 CUDA Docling Serve

Run exactly one CUDA Docling Serve instance initially:

- image `ghcr.io/docling-project/docling-serve-cu128:v1.29.0`;
- internal/host port 5001;
- GPU reservation (`gpus: all` on current Compose, with `NVIDIA_VISIBLE_DEVICES=all` as needed);
- one Uvicorn/local-engine worker initially to limit 8 GB GPU pressure;
- health check against the service's health endpoint;
- shared Docling model/cache volume after path inspection;
- `DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true` only after confirming cold-start memory fits.

Qwen 4B Q4 is estimated around 3.75 GiB VRAM at runtime; Docling can consume the remainder. Serialize Docling work initially and use Taskiq concurrency 1 for document ingestion. Docker Model Runner can unload idle models, but concurrency/OOM behavior must be load-tested.

### 9.2 Lightweight Docling MCP image

Create `docker/mcp/docling/Dockerfile` based on Python 3.13 slim and install a pinned `docling-mcp` version without the `[local]` extra. Configure:

```dotenv
DOCLING_MCP_CONVERSION_MODE=remote
DOCLING_MCP_SERVICE_URL=http://docling-serve:5001
DOCLING_MCP_SERVICE_TIMEOUT=600
DOCLING_MCP_FALLBACK_TO_LOCAL=false
CACHE_DIR=/cache
```

Run:

```text
docling-mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

The MCP endpoint is `/mcp`. Do not install local Docling models in this sidecar.

## 10. Chrome DevTools, Browserless, and other MCPs

### 10.1 Browserless

Run the existing Browserless Chromium image only on the MCP/service network:

- internal URL `http://browserless:3000` / CDP WebSocket endpoint;
- strong `BROWSERLESS_TOKEN`;
- low concurrency (1–2) and bounded timeout;
- no host port by default;
- no persistent browser profile by default, so credentials/cookies disappear with sessions;
- Browserless must share a network with frontend/app so it can inspect `http://frontend:3000` and `http://app:8100`.

### 10.2 Chrome DevTools MCP HTTP gateway

The official Chrome DevTools MCP server remains stdio-only. Create a pinned Node sidecar that contains:

- `chrome-devtools-mcp` at an explicit version;
- `supergateway` at an explicit version (or a small project-owned MCP SDK bridge if the gateway compatibility test fails);
- Chrome MCP configured with Browserless's browser discovery/CDP endpoint;
- stdio translated to stateful Streamable HTTP on port 8000 path `/mcp`.

Statefulness matters because a debugging session spans multiple tool calls. Validate initialize, `tools/list`, page creation, navigation, console inspection, and reconnect behavior. If Supergateway's session behavior is incompatible with the backend's current MCP client, replace it with a small pinned MCP SDK adapter rather than weakening the client.

### 10.3 GitHub MCP

Run the existing GitHub image directly:

```text
github-mcp-server http --port 8082 --listen-host 0.0.0.0 --read-only
```

The HTTP mode expects the caller's bearer token. Do not bake a PAT into the image. Add `GITHUB_MCP_TOKEN` as a secret application setting and inject `Authorization: Bearer ...` while constructing the deployment-managed MCP spec. Start with `X-MCP-Readonly: true` and a narrow toolset/allowed-tool list.

### 10.4 MCPs deliberately excluded from phase one

- PostgreSQL MCP: redundant for normal RAG operation and dangerous in unrestricted mode. Add later with a separate least-privilege role and read-only tool policy.
- Filesystem MCP: redundant with native project tools and risky if the repository is broadly mounted.
- Fetch MCP: redundant with the agent's existing fetch/search tooling.
- Playwright MCP: overlaps Browserless + Chrome DevTools MCP.
- Docker MCP: would require Docker socket authority; do not add as a convenience.

## 11. File-by-file change plan

### `backend/.env` and `backend/.env.example`

1. Repair every concatenated line first. Current examples include the RAG header/model/chunk size, `RAG_TOP_K`/`HF_TOKEN`, cross-encoder/parser header, LlamaParse tier/image comment, image model/GDrive/S3 variables, and the AI Agent/OpenRouter/model/temperature group.
2. Add the embedding, cache, Docling, Taskiq, MCP, Browserless, and MinIO values described above.
3. Keep host-run addresses (`localhost`) here; Compose overrides service DNS addresses.
4. Remove LiteParse variables.
5. Do not commit real Logfire, OpenRouter, GitHub, Google, MinIO production, Browserless, or Traefik secrets.

### `frontend/.env.local` and `.env.example`

- Keep the direct development URLs at 3000/8100.
- Keep `NEXT_PUBLIC_RAG_ENABLED=true`.
- For Traefik/production, set public API, WebSocket, site, and OAuth origins to HTTPS.
- No Docling, database, Redis, MinIO, Model Runner, or MCP secret belongs in frontend variables.

### `backend/Dockerfile`

- remove the complete LiteParse/Node installation block;
- keep the uv multi-stage install;
- pin the uv source image instead of `latest`;
- keep non-root runtime;
- create only needed writable paths and make `/app/media` and `/app/models_cache` volume-compatible;
- use the existing Python/httpx health check on port 8100;
- do not add CUDA or embedding models to the backend image.

### `docker-compose.yml`

- make it the canonical base;
- replace DB and Redis images;
- add MinIO, CUDA Docling Serve, Taskiq worker/scheduler, optional MCP services, networks, health checks, and volumes;
- share the backend image/env/media/model-cache mounts with Taskiq processes;
- use map-style environment blocks;
- avoid `container_name` unless there is a specific external requirement, because fixed names prevent parallel project instances;
- make service DNS names the contract.

### `docker-compose.dev.yml`

- convert from a duplicate to an override;
- preserve the current `--ws websockets-sansio` option, which is missing from the attached duplicate dev file;
- add read-only source bind mounts for app/CLI only if reload works with them;
- publish all development ports to `127.0.0.1`;
- activate core/Taskiq/Docling by default and leave MCP/frontend/DB UI as profiles.

### `docker-compose.frontend.yml`

- preferably fold into the base `frontend` profile and delete this file after references are updated;
- if retained, make it an override on the same project network instead of a separate host-network workaround;
- preserve the distinction between build-time `NEXT_PUBLIC_*` variables and server-only runtime variables.

### `docker-compose.prod.yml`

- replace the contradictory external-Nginx configuration with Traefik 3.7.10, Traefik is the recommended decision;
- remove direct app/frontend host publications except Traefik entrypoints;
- add data-internal plus egress/service networks;
- do not expose DB, Redis, Browserless, or Chrome MCP;
- use secrets rather than dev defaults;
- increase the backend memory limit above the current 512 MiB after measuring—the current limit is likely too small for the full RAG/agent dependency set even though Docling is remote;
- add TLS/ACME for a real domain; use a locally trusted certificate for `*.localhost` development because public ACME will not issue localhost certificates.

### `Makefile`

- define common Compose file/profile variables so every target uses the same topology;
- add `preflight`, `model-check`, `compose-check`, `infra-up`, `docling-up`, `mcp-up/down/logs`, `taskiq-up/logs`, and targeted health commands;
- check all required image tags/digests and external volumes;
- test Docker Model Runner model ID and vector length before migrations;
- use container `$POSTGRES_USER`/`$POSTGRES_DB` in `_wait_for_db`, not hard-coded `postgres`;
- replace production's fixed `sleep 5` with the polling helper;
- fix `run-prod` from port 8000 to 8100;
- update help text so it no longer alternates between Traefik and external Nginx;
- ensure `docker-clean` cannot delete external `redis-data` or `docling-models`; add a separately named, explicit destructive data-wipe target if desired;
- add Taskiq worker/scheduler targets and do not add Celery services or commands.

### Additional backend files not attached but required

- `app/core/config.py`: new settings and validation; MCP secret-header injection; parser literals.
- `app/services/rag/config.py`: Qwen/custom dimension support and Docling format registry.
- `app/services/rag/embeddings.py`: Docker Model Runner base URL, query/document separation, dimensions, normalization, async/batching, and cache integration.
- `app/services/rag/embedding_cache.py` (new): canonical keys, Redis/Postgres lookup, locks, serialization.
- `app/services/rag/documents.py`: Docling Serve parser and complete LiteParse removal.
- `app/services/rag/vectorstore.py`: await async embeddings; enforce collection fingerprint; retain `vector(1024)` HNSW.
- `app/services/rag/ingestion.py` and retrieval code: await/cache-aware embedding service.
- Alembic migration: pgvector extension, durable embedding cache, and collection metadata registry.
- Taskiq configuration/commands: Redis DB separation and ingestion job concurrency.
- `backend/pyproject.toml`, `backend/uv.lock`, tests, README, `ENV_VARS.md`, `MANUAL_STEPS.md`, and relevant agent skills.
- `docker/mcp/docling/Dockerfile` and `docker/mcp/chrome-devtools/Dockerfile` plus pinned dependency manifests.

## 12. Implementation sequence and acceptance gates

### Phase 0 — Preserve and inspect

1. Record `git status`, generator version, Docker Desktop version, GPU driver, and current Compose projects.
2. Inspect the contents/labels/mountpoints of `docling-models` and `redis-data`; do not assume they are safe to reuse.
3. Inspect image repository digests and retag/pin mutable assets.
4. Confirm ports 80, 443, 3000, 5001, 5432, 6379, 8080, 8081, 8100, 8201–8203, 9000, 9001, and 12434 are free or intentionally assigned.

Gate: no files changed and no existing volume deleted.

### Phase 1 — Repair configuration and normalize Compose

1. Repair `.env` newlines and validate with Pydantic settings.
2. Convert Compose to base + overrides/profiles.
3. Switch to pgvector PG17 and Redis 8.10.0; add MinIO and Taskiq.
4. Run `docker compose ... config` for dev, stage, prod, frontend, MCP, and DB UI combinations.

Gate: every resolved Compose configuration validates with no unexpected port, secret, or network expansion.

### Phase 2 — Embedding preflight and provider

1. Enable Docker Model Runner TCP host access and query `/engines/v1/models`.
2. Send one embedding request with the exact model ID and `dimensions: 1024`.
3. Record native/returned dimension and confirm finite values.
4. Implement Qwen query/document formatting, truncation/normalization fallback, and dimension tests.
5. Implement Redis/Postgres cache and migration.

Gate: query and document embeddings are exactly 1,024 floats; repeated requests hit cache; a model/instruction fingerprint mismatch is rejected.

### Phase 3 — Docling substitution

1. Start CUDA Docling Serve only; validate GPU visibility and a warm conversion.
2. Implement and test the backend Docling parser.
3. Remove LiteParse from code, dependencies, image, environment, tests, and docs.
4. Build the lightweight remote-mode Docling MCP sidecar and test `/mcp` with MCP Inspector/backend probe.

Gate: PDF, scanned PDF, DOCX, table-heavy PDF, and image inputs ingest successfully; repository-wide LiteParse search is clean.

### Phase 4 — MCP services

1. Start Browserless without a host port; test CDP from the gateway network.
2. Build the Chrome DevTools gateway and run multi-call state/reconnect tests.
3. Start GitHub MCP HTTP in read-only/narrow-tool mode.
4. Add deployment-managed MCP specs with internal URLs and injected secrets.

Gate: backend `tools/list` discovers each intended server; a dead optional MCP is skipped without killing chat; tool-name prefixes do not collide.

### Phase 5 — Taskiq and concurrency

1. Start Taskiq worker and scheduler from the shared backend image.
2. Put ingestion through Taskiq, with shared media/model-cache mounts.
3. Set document/GPU concurrency to 1 initially.
4. Exercise duplicate simultaneous ingestion and cache-lock behavior.

Gate: no Celery process exists; jobs survive app restart; Redis broker/results/cache namespaces behave as designed.

### Phase 6 — Traefik/HTTPS and production hardening

1. Add Traefik routes for frontend/API and only approved MCP endpoints.
2. Create trusted local certificates or real-domain ACME.
3. Add authentication/rate limits to any exposed MCP/Docling route.
4. verify that DB, Redis, Browserless, Chrome MCP, and the Traefik dashboard are not public.
5. run prod Compose config, health tests, backup/restore test, and resource/OOM test.

Gate: HTTPS works without routing internal application traffic through the public edge; sensitive services remain unreachable externally.

### Phase 7 — First bootstrap

After all preceding gates:

1. Install/update dependencies and lockfiles.
2. Apply the explicit new Alembic migration.
3. Start core infrastructure and services.
4. Run `db-upgrade`.
5. Seed the admin only once.
6. Start the frontend.

Do not blindly create an "initial" migration if the generated repository already contains Alembic revisions. Inspect `backend/alembic/versions` first. `db-migrate` is needed for the new cache/metadata models or when revisions are absent; `db-upgrade` applies committed revisions.

## 13. Required test matrix

| Area | Minimum acceptance test |
|---|---|
| Env | Pydantic Settings loads every intended variable; no concatenated/ignored keys. |
| Compose | `config` succeeds for every profile/override combination. |
| DB | `vector` extension present; 1,024-dimensional insert/search; HNSW index created. |
| Model Runner | exact model ID accepted from host and app container; returned vector normalized and length 1,024. |
| Cache | Redis hit, Postgres fallback after Redis flush, DMR miss path, key stability, query/document separation, stampede lock. |
| Collection safety | changed model/version/instruction blocks mixed-space search until re-embed. |
| Docling Serve | health, CUDA detected, warm PDF conversion, OCR, tables, timeout/error behavior. |
| Docling parser | PDF/DOCX/PPTX/XLSX/image supported as promised; TXT/MD remain local. |
| Taskiq | worker/scheduler start, retry policy, restart survival, concurrency 1 for Docling jobs. |
| MCP | initialize + tools/list + one harmless tool per server; unreachable optional server skipped. |
| Chrome | create/navigate/inspect console/network across multiple tool calls. |
| Security | host ports bind only localhost in dev; prod data ports closed; secrets absent from rendered frontend and Git. |
| Persistence | restart stack and verify PostgreSQL, Redis AOF, MinIO objects, Docling cache, and media survive. |
| GPU | simultaneous Docling + Qwen request does not OOM; otherwise serialize or use CPU Docling override. |

## 14. Source verification

- [Docker Model Runner API and container/host base URLs](https://docs.docker.com/ai/model-runner/api-reference/)
- [Docker's Qwen3 Embedding model variants](https://hub.docker.com/r/ai/qwen3-embedding)
- [Qwen3-Embedding-4B dimensions and Matryoshka support](https://huggingface.co/Qwen/Qwen3-Embedding-4B)
- [MCP transports specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)
- [Generated full-stack template repository](https://github.com/vstorm-co/full-stack-ai-agent-template)
- [Docling Serve image/API documentation](https://github.com/docling-project/docling-serve)
- [Docling MCP remote mode and Streamable HTTP](https://github.com/docling-project/docling-mcp)
- [Chrome DevTools MCP repository](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [Chrome DevTools MCP Docker/stdio-only maintainer answer](https://github.com/ChromeDevTools/chrome-devtools-mcp/discussions/749)
- [Supergateway stdio to Streamable HTTP](https://github.com/supercorp-ai/supergateway)
- [Browserless Docker/CDP deployment](https://docs.browserless.io/enterprise/open-source)
- [GitHub MCP Streamable HTTP server mode](https://github.com/github/github-mcp-server/blob/main/docs/streamable-http.md)

## 15. Recommended immediate next action

The next implementation turn should begin with Phase 0 and a complete repository tree, especially `backend/app/core/config.py`, `backend/app/services/rag/`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/alembic/versions/`, Taskiq files, `.env.example`, README, and generated project metadata. The eight uploaded files establish the infrastructure problems, but those backend files are required to implement and test the embedding/Docling changes safely.
