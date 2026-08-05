# Environment variables

Reference for `fullstack` runtime configuration. The
authoritative source is `backend/.env.example` — this doc explains what each
group is for and which are required vs optional.

> Quick start: copy `backend/.env.example` to `backend/.env` and fill in the
> blanks marked **Required**. Defaults are sensible for local development.

## Project

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROJECT_NAME` | optional | `fullstack` | Used in logs, OpenAPI title, email templates |
| `DEBUG` | optional | `true` | When `true`, FastAPI returns full tracebacks |
| `ENVIRONMENT` | optional | `local` | Free-form tag: `local` / `staging` / `production` |
| `TIMEZONE` | optional | `EDT` | IANA TZ name (e.g. `Europe/Warsaw`) |
| `BACKEND_URL` | optional | `http://localhost:8100` | Used by frontend BFF + email link generation |
| `FRONTEND_URL` | optional | `http://localhost:3000` | Used by password-reset / magic-link emails |

## Auth & secrets

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | **required in prod** | (generated) | JWT signing key. Rotating invalidates all tokens |
| `API_KEY` | **required in prod** | (generated) | Static admin/service-to-service key for `X-API-Key` header |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | optional | `30` | JWT access token lifetime |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | optional | `10080` | JWT refresh token lifetime (7 days) |
| `GOOGLE_OAUTH_CLIENT_ID` | required | — | From Google Cloud Console → OAuth credentials |
| `GOOGLE_OAUTH_CLIENT_SECRET` | required | — | jw |

## Database
| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **required** | `postgresql+asyncpg://...` | Full async connection string |
| `DB_POOL_SIZE` | optional | `5` | Number of long-lived connections |
| `DB_MAX_OVERFLOW` | optional | `10` | Burst capacity above pool size |

## LLM / AI

| `OPENROUTER_API_KEY` | **required** | — | From openrouter.ai |
| `LOGFIRE_TOKEN` | optional | — | When set, ships traces to Logfire (logfire.pydantic.dev) |

## RAG (pgvector)

| Variable | Required | Default | Description |
|---|---|---|---|
| `EMBEDDING_BASE_URL` | optional | `http://localhost:12434/engines/v1` | Docker Model Runner OpenAI-compatible endpoint |
| `EMBEDDING_MODEL` | optional | `docker.io/ai/qwen3-embedding:latest` | Exact Qwen 4B request ID |
| `EMBEDDING_MODEL_VERSION` | optional | `4B-Q4_K_M` | Artifact variant in cache/collection identity |
| `EMBEDDING_MODEL_REVISION` | optional | `731f733db2ef` | Inspected local artifact revision |
| `EMBEDDING_DIMENSION` | optional | `1024` | Required pgvector dimension |
| `EMBEDDING_CACHE_URL` | optional | `redis://localhost:6379/3` | Redis L1; PostgreSQL is durable L2 |
| `RERANKER_PROVIDER` | optional | `docker_model_runner` | Use DMR native `/rerank`, or `disabled` |
| `CROSS_ENCODER_MODEL` | optional | `huggingface.co/keisuke-miyako/gte-reranker-modernbert-base-gguf-q8_0:Q8_0` | Exact DMR reranker request ID; legacy variable name retained for compatibility |
| `RERANKER_BASE_URL` | optional | `http://localhost:12434` | Docker Model Runner native API root |
| `RERANKER_TIMEOUT_SECONDS` | optional | `120` | Rerank request and warmup timeout |
| `RERANKER_MAX_RETRIES` | optional | `3` | Retries transient DMR load/routing responses |
| `PDF_PARSER` | optional | `docling` | RAG parser (`docling`, `pymupdf`, `llamaparse`) |
| `CHAT_PDF_PARSER` | optional | `docling` | Chat PDF parser; falls back to PyMuPDF |
| `DOCLING_SERVE_URL` | optional | `http://localhost:5001` | Shared Docling Serve URL |
| `DOCLING_SERVE_TIMEOUT_SECONDS` | optional | `600` | Conversion timeout |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` | connector-specific | — | Path to service-account JSON |
| `S3_RAG_BUCKET` | connector-specific | `fullstack-rag` | Source bucket for ingestion |

## Redis

| Variable | Required | Default | Description |
|---|---|---|---|
| `REDIS_DB` | optional | `0` | Application cache/session namespace |
| `REDIS_PASSWORD` | **required in prod** | — | Strong secret propagated to app, Taskiq, and embedding-cache clients |
| `TASKIQ_BROKER_URL` | optional | `redis://localhost:6379/1` | Taskiq broker namespace |
| `TASKIQ_RESULT_BACKEND` | optional | `redis://localhost:6379/2` | Taskiq result namespace |
| `TASKIQ_MAX_ASYNC_TASKS` | optional | `1` | Initial serialized ingestion concurrency |
| `EMBEDDING_CACHE_URL` | optional | `redis://localhost:6379/3` | Embedding L1 namespace |

## Deployment-managed MCP

| Variable | Required | Default | Description |
|---|---|---|---|
| `MCP_SERVERS` | optional | `[]` | Non-secret runtime server configuration JSON |
| `GITHUB_MCP_TOKEN` | with GitHub MCP | — | Fine-grained read-only token injected at runtime |
| `BROWSERLESS_TOKEN` | with browser MCP | — | Strong internal Browserless token |

## Per-user Google Workspace MCP

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_WORKSPACE_MCP_CLIENT_ID` | for Google Workspace MCP | — | Google OAuth Web client ID used only for exact official Workspace MCP endpoints |
| `GOOGLE_WORKSPACE_MCP_CLIENT_SECRET` | for Google Workspace MCP | — | Google OAuth Web client secret; never sent to custom MCP URLs |

The MCP OAuth callback obtains each user's access and refresh tokens and stores
them encrypted in PostgreSQL. Do not put a Google refresh token in `.env`.
These credentials are independent of `GOOGLE_DRIVE_CREDENTIALS_FILE`, which is
the service-account credential used by the RAG Google Drive sync source.

## Email (log)

| Variable | Required | Default | Description |
|---|---|---|---|
| (log provider — no env vars; emails written to stdout) | — | — | — |

## File storage (S3/MinIO)

| Variable | Required | Default | Description |
|---|---|---|---|
| `S3_ENDPOINT_URL` | optional | (AWS default) | Set for MinIO/Backblaze/etc. |
| `S3_ACCESS_KEY` | **required** | — | Access key ID |
| `S3_SECRET_KEY` | **required** | — | Secret key |
| `S3_BUCKET` | **required** | — | Default bucket for uploads |
| `S3_REGION` | optional | `us-east-1` | AWS region |

## Validation

```bash
# Confirm settings load without errors:
cd backend && uv run python -c "from app.core.config import settings; print(settings.ENVIRONMENT, settings.PROJECT_NAME)"
```

If any **Required** var is missing, FastAPI raises `pydantic_settings.SettingsError` on startup — check the message for which field.
