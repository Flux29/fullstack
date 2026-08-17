# Manual setup steps for fullstack

The generator created the code. These are the **one-time external setup steps**
that can't be automated — accounts to create, keys to copy, services to provision.

> Skip ahead to "After every deploy" at the bottom for things you'll re-do
> regularly. Items above are one-time per environment.

---

## Secrets

```bash
cp backend/.env.example backend/.env
```

Then in `backend/.env`:

- [ ] **`SECRET_KEY`** — replace with a fresh value: `openssl rand -hex 32`
- [ ] **`API_KEY`** — replace with a fresh value: `openssl rand -hex 32`

These are used to sign JWTs and authenticate service-to-service calls. Rotate at every environment promotion (dev → staging → prod each get their own).

## PostgreSQL

- [ ] Provision a PostgreSQL ≥ 14 instance (local: `docker compose up -d db`; managed: Neon / Supabase / RDS / Cloud SQL).
- [ ] Set `DATABASE_URL` in `.env` to the **async** connection string: `postgresql+asyncpg://user:pass@host:5432/dbname`.
- [ ] Run migrations: `cd backend && uv run alembic upgrade head`.

## OpenRouter

- [ ] Create API key at https://openrouter.ai/keys.
- [ ] Set `OPENROUTER_API_KEY` in `.env`.

## Google OAuth

- [ ] Go to https://console.cloud.google.com/ → APIs & Services → Credentials → Create OAuth client ID.
- [ ] Application type: **Web application**.
- [ ] Authorized redirect URIs: `http://localhost:3000/auth/callback`. Add prod URL when deploying.
- [ ] Copy **Client ID** + **Client secret** → set `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`.

## RAG, Docker Model Runner, and Docling

- [ ] Apply Alembic through `0027_embedding_cache`; it creates pgvector, the durable embedding cache, and collection fingerprint registry.
- [ ] Confirm `GET http://localhost:12434/engines/v1/models` contains the inspected Qwen 4B artifact and that a `dimensions: 1024` request succeeds.
- [ ] Confirm Docker Model Runner contains `huggingface.co/keisuke-miyako/gte-reranker-modernbert-base-gguf-q8_0:Q8_0` and its native `/rerank` endpoint succeeds.
- [ ] Preserve the external `redis-data` and `docling-models` volumes. Normal Make targets never delete them.
- [ ] Verify CUDA Docling Serve `/health`, then perform one warm PDF conversion before ingestion.
- [ ] Keep Taskiq at one worker and `TASKIQ_MAX_ASYNC_TASKS=1` until simultaneous Qwen/Docling GPU load is measured.

- [ ] Set `ADMIN_EMAIL` and a strong `ADMIN_PASSWORD` before the one-time `make seed`/`make quickstart` step.
- [ ] (Optional) Ingest seed documents: `uv run fullstack cmd rag-ingest /path/to/file.pdf --collection docs`.

### Google Drive sync source

- [ ] Create a service account at https://console.cloud.google.com/iam-admin/serviceaccounts.
- [ ] Download the JSON credentials → save to `secrets/gdrive-service-account.json`.
- [ ] Share the target Drive folder with the service-account email.
- [ ] Set `GOOGLE_DRIVE_CREDENTIALS_FILE` in `.env`.

### S3 / MinIO sync source

- [ ] Provision an S3 bucket (or run MinIO locally: `docker compose up -d minio`).
- [ ] Create an IAM user with `s3:GetObject` + `s3:ListBucket` on the source bucket.
- [ ] Set `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `RAG_S3_BUCKET` / `RAG_S3_PREFIX` in `.env`.

## Redis

- [ ] Local: `docker compose up -d redis` (already in compose file).
- [ ] Managed: Upstash / Redis Cloud / ElastiCache. Set `REDIS_URL` in `.env`.
- [ ] Preserve logical DB assignments: application 0, Taskiq broker 1, Taskiq results 2, embedding L1 3; enable AOF without a global eviction policy.

## MCP sidecars

- [ ] Generate a strong `BROWSERLESS_TOKEN`; Browserless and Chrome DevTools MCP stay private.
- [ ] Create a fine-grained read-only GitHub token and set `GITHUB_MCP_TOKEN`. Never place it inside `MCP_SERVERS` JSON.
- [ ] Confirm the GitHub deployment allowlist contains read-only tools only and the startup probe exposes no mutation tool.

### Google Workspace standard APIs

- [ ] Enable the generally available Gmail, Drive, Docs, Sheets, Slides, Calendar, Chat, and People APIs in the Google Cloud project.
- [ ] Create a Google OAuth **Web application** client and add this exact authorized redirect URI: `http://localhost:3000/api/me/mcp-connections/oauth/callback`.
- [ ] Add the required scopes on Google Auth Platform → Data Access and add your account under Audience → Test users while the app is in testing.
- [ ] Set `GOOGLE_API_CLIENT_ID` and `GOOGLE_API_CLIENT_SECRET` in `backend/.env` from the existing **Full Stack** Web OAuth client, then recreate the backend app.
- [ ] In Google Auth Platform → Data Access, add the full scope URLs from the conversion plan: `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.compose`, `https://www.googleapis.com/auth/calendar.readonly`, `https://www.googleapis.com/auth/drive`, `https://www.googleapis.com/auth/documents`, `https://www.googleapis.com/auth/spreadsheets`, `https://www.googleapis.com/auth/presentations`, `https://www.googleapis.com/auth/chat.spaces`, `https://www.googleapis.com/auth/chat.messages`, `https://www.googleapis.com/auth/chat.memberships`, `https://www.googleapis.com/auth/chat.messages.reactions`, `https://www.googleapis.com/auth/chat.delete`, `https://www.googleapis.com/auth/contacts`, `https://www.googleapis.com/auth/contacts.other.readonly`, `https://www.googleapis.com/auth/directory.readonly`, and `https://www.googleapis.com/auth/userinfo.profile`; add every controlled test account under Audience → Test users while the app is in Testing.
- [ ] Sign in to each desired product from Settings → Integrations. Access and refresh tokens are stored per user, encrypted in PostgreSQL; no `GOOGLE_DRIVE_REFRESH_TOKEN` environment variable is needed.
- [ ] Reauthorize each existing preview connection using **Upgrade to standard API**. The old URL/token remains active until the callback succeeds.
- [ ] Confirm every mutation pauses at the approval dialog. Gmail exposes approved draft creation/deletion, not send; use Gmail to review/send the draft.

Google Workspace per-user OAuth is separate from the Google Drive RAG sync source,
which continues to use `GOOGLE_DRIVE_CREDENTIALS_FILE` and a service account.

## Traefik and production TLS

- [ ] Set `DOMAIN` and `ACME_EMAIL`, point public DNS at the deployment host, and choose the intended ACME resolver/certificate policy.
- [ ] Free or deliberately remap host ports 80/443 before `make prod`; this workstation currently has IIS/HTTP.sys on port 80, and the edge preflight intentionally blocks while it is occupied.
- [ ] Keep the dashboard loopback-only and verify DB, Redis, MinIO, Browserless, and Chrome MCP have no production host publications.
- [ ] Complete a real-domain HTTPS request after DNS and production secrets are provisioned; Compose validation alone cannot prove certificate issuance.

## Self-hosted Codecov (Compose profile `codecov`)

Coverage reports from CI land on a Codecov instance this deployment owns
(`docker/codecov/`, images pinned to the latest-calver release set), reachable at
`http://localhost:8090` in dev and `https://codecov.<DOMAIN>` behind Traefik in prod.
Reference: https://docs.codecov.com/docs/configuration.

- [ ] Create a GitHub **OAuth App** (Settings → Developer settings) with authorization
      callback URL `http://localhost:8090/login/github` (dev) or
      `https://codecov.<DOMAIN>/login/github` (prod). Set `CODECOV_GITHUB_CLIENT_ID` and
      `CODECOV_GITHUB_CLIENT_SECRET`.
- [ ] Generate long random values for `CODECOV_COOKIE_SECRET`, `CODECOV_POSTGRES_PASSWORD`,
      and `CODECOV_MINIO_ROOT_PASSWORD`; `make preflight-codecov` refuses placeholders.
- [ ] Put the GitHub username that owns the deployment under `setup.admins` in
      `docker/codecov/config/codecov.yml` (currently the repository owner).
- [ ] Dev: copy the `CODECOV_*` block (only that block) into a gitignored `.env` at the
      repository root — Compose interpolates that file automatically and `make dev-codecov`
      checks the same file — then run `make dev-codecov`. Shell exports also work and take
      precedence. Prod: add them to `backend/.env`, run `make prod` then `make prod-codecov`,
      and point `codecov.<DOMAIN>` DNS at the host.
- [ ] Log in once at the instance with the admin account so Codecov syncs the organisation
      and repositories; copy the repository upload token from the repo settings page (or set
      `CODECOV_GLOBAL_UPLOAD_TOKEN` and use that with the repo slug).
- [ ] In the GitHub repository: variable `CODECOV_URL` = the public instance URL (no trailing
      slash) and secret `CODECOV_TOKEN` = the upload token. Both CI upload steps
      (`backend` and `frontend` flags) are skipped until `CODECOV_URL` is set, then fail
      the job loudly if the upload fails. GitHub-hosted runners must be able to reach the URL.
- [ ] Webhooks — only once GitHub can reach the instance; a localhost-only instance receives
      no deliveries. Set `CODECOV_GITHUB_WEBHOOK_SECRET` to a long random value **before**
      activating a repository: Codecov creates the hook itself at activation (events `push`,
      `pull_request`, `status`, `delete`, `public`, `repository` → `<codecov_url>/webhooks/github`)
      and stores this secret in it, then rejects deliveries whose HMAC does not match. Empty is
      not "verification off" — unsigned deliveries are rejected too, so an empty value yields a
      hook that 403s every time. Changing the value later means editing or recreating the
      `Codecov Webhook` entry under the repository's GitHub webhook settings; confirm 200s under
      its Recent Deliveries. Coverage uploads work without any of this — webhooks only keep PR
      comments and commit statuses prompt.
- [ ] Optional: a GitHub App for PR comments and status checks
      (`integration_id_enabled: true` plus `GITHUB__INTEGRATION__ID` and the private key —
      see the comment block in `codecov.yml`).
- [ ] Never move a secret into `codecov.yml`; it is committed. Secrets stay in the
      environment as `CATEGORY__KEY` overrides.

## Transactional email

- [ ] No external provider — emails written to stdout (`log` provider). Useful for dev only.
- [ ] Switch to `resend` or `smtp` for staging/prod.

## Logfire (Pydantic observability)

- [ ] Create account at https://logfire.pydantic.dev.
- [ ] Run `uv run logfire auth` once locally to bootstrap.
- [ ] Get write token → set `LOGFIRE_TOKEN` in `.env` for non-local environments.

---

## After every deploy

- [ ] Run database migrations: `alembic upgrade head` (CI step or post-deploy job).
- [ ] Smoke test `/api/v1/health` returns `{"status": "ok"}`.
- [ ] Frontend loads, login → dashboard flow works.
- [ ] Logs flowing to your aggregator.

---

## Where to find more

- `ENV_VARS.md` — exhaustive env var reference
- `docs/deploy.md` — platform-specific deployment recipes
- `SECURITY.md` — security model + production hardening checklist
- `CONTRIBUTING.md` — dev environment setup
- `docs/architecture.md` — codebase layered architecture rules
