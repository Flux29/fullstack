.PHONY: help install format lint test test-cov frontend-test frontend-format-check frontend-build playwright \
	audit audit-python audit-js run run-prod \
	preflight preflight-volumes preflight-model preflight-ports preflight-edge-ports preflight-mcp \
	preflight-codecov compose-check \
	dev dev-frontend dev-mcp dev-db-ui dev-codecov dev-all dev-down dev-logs dev-rebuild stage stage-down \
	prod prod-codecov prod-down prod-logs quickstart seed bootstrap docker-clean docker-reset \
	db-migrate db-upgrade db-downgrade db-current db-history taskiq-worker taskiq-scheduler \
	upgrade upgrade-dry-run upgrade-new-features upgrade-finalize \
	governance-install governance-preflight governance-scan governance-sync governance-check \
	governance-check-fast governance-doctor governance-selftest governance-lint governance-sample governance-views governance-context \
	governance-impact governance-explain governance-summary governance-gate-metrics \
	governance-visualize governance-skills-check governance-change-start governance-change-finish

COMPOSE_BASE := docker compose -f docker-compose.yml
COMPOSE_DEV := $(COMPOSE_BASE) -f docker-compose.dev.yml
COMPOSE_FRONTEND := $(COMPOSE_DEV) -f docker-compose.frontend.yml --profile frontend
COMPOSE_PROD := docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.prod.yml
TASKIQ_MAX_ASYNC_TASKS ?= 1
EMBEDDING_MODEL ?= docker.io/ai/qwen3-embedding:latest
RERANKER_MODEL ?= huggingface.co/keisuke-miyako/gte-reranker-modernbert-base-gguf-q8_0:Q8_0

preflight-volumes:
	@powershell.exe -NoProfile -Command "docker volume inspect redis-data | Out-Null; if ($$LASTEXITCODE -ne 0) { exit $$LASTEXITCODE }; docker volume inspect docling-models | Out-Null; if ($$LASTEXITCODE -ne 0) { exit $$LASTEXITCODE }; Write-Output 'External volumes redis-data and docling-models are present and protected.'"

preflight-model:
	@powershell.exe -NoProfile -Command "$$models=Invoke-RestMethod -Uri 'http://localhost:12434/engines/v1/models' -TimeoutSec 15; if (($$models | ConvertTo-Json -Depth 10) -notmatch 'qwen3-embedding') { throw 'Docker Model Runner qwen3-embedding model is unavailable.' }; $$body=@{model='$(EMBEDDING_MODEL)'; input=@('docker integration preflight'); dimensions=1024} | ConvertTo-Json -Depth 5; $$response=Invoke-RestMethod -Method Post -Uri 'http://localhost:12434/engines/v1/embeddings' -ContentType 'application/json' -Body $$body -TimeoutSec 120; $$vector=@($$response.data[0].embedding); if ($$vector.Count -lt 1024) { throw ('Embedding vector is too short: ' + $$vector.Count) }; foreach ($$value in $$vector) { if ([double]::IsNaN([double]$$value) -or [double]::IsInfinity([double]$$value)) { throw 'Embedding vector contains a non-finite value.' } }; Write-Output ('Docker Model Runner ready; native vector length=' + $$vector.Count + ', application target=1024.')"
	@powershell.exe -NoProfile -Command "$$body=@{model='$(RERANKER_MODEL)'; query='Which passage is about Docker?'; documents=@('Docker runs containers.','Bananas are fruit.'); top_n=1} | ConvertTo-Json -Depth 5; $$response=Invoke-RestMethod -Method Post -Uri 'http://localhost:12434/rerank' -ContentType 'application/json' -Body $$body -TimeoutSec 120; if (@($$response.results).Count -ne 1 -or $$response.results[0].index -ne 0) { throw 'Docker Model Runner reranker returned an unexpected result.' }; $$score=[double]$$response.results[0].relevance_score; if ([double]::IsNaN($$score) -or [double]::IsInfinity($$score)) { throw 'Reranker returned a non-finite score.' }; Write-Output ('Docker Model Runner reranker ready: $(RERANKER_MODEL)')"

preflight-ports:
	@powershell.exe -NoProfile -Command "$$ports=8100,5432,6379,9000,9001,5001,3000,8081,8090,8201,8202,8203; $$busy=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in $$ports; if($$busy){Write-Error ('Occupied development ports: ' + (($$busy.LocalPort | Sort-Object -Unique) -join ', ')); exit 1}"

preflight-edge-ports:
	@powershell.exe -NoProfile -Command "$$ports=80,443,8080; $$busy=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in $$ports; if($$busy){Write-Error ('Occupied edge ports: ' + (($$busy.LocalPort | Sort-Object -Unique) -join ', ')); exit 1}"

preflight-mcp:
	@powershell.exe -NoProfile -Command "if ('$(MCP)') { if (-not $$env:BROWSERLESS_TOKEN -or $$env:BROWSERLESS_TOKEN -eq 'change-me-before-enabling-mcp') { throw 'Set a strong BROWSERLESS_TOKEN before enabling MCP.' }; if (-not $$env:GITHUB_MCP_TOKEN) { throw 'Set GITHUB_MCP_TOKEN before enabling MCP.' } }"

# Refuses to start the codecov profile while any credential still holds its placeholder.
# Checks the shell environment first, then CODECOV_ENV_FILE — the same precedence Compose
# applies when it interpolates: dev-codecov points this at the root .env Compose reads by
# default, prod-codecov at backend/.env which the prod stack passes via --env-file.
preflight-codecov:
	@powershell.exe -NoProfile -Command "if ('$(CODECOV)') { $$file=@{}; if ('$(CODECOV_ENV_FILE)' -and (Test-Path '$(CODECOV_ENV_FILE)')) { Get-Content '$(CODECOV_ENV_FILE)' | ForEach-Object { if ($$_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$$') { $$file[$$Matches[1]] = $$Matches[2].Trim().Trim([char]34).Trim([char]39) } } }; foreach ($$name in 'CODECOV_COOKIE_SECRET','CODECOV_GITHUB_CLIENT_ID','CODECOV_GITHUB_CLIENT_SECRET','CODECOV_POSTGRES_PASSWORD','CODECOV_MINIO_ROOT_PASSWORD') { $$value=[Environment]::GetEnvironmentVariable($$name); if (-not $$value) { $$value=$$file[$$name] }; if (-not $$value -or $$value -like '*change-me*') { throw ('Set a real ' + $$name + ' before enabling the codecov profile.') } } }"

compose-check:
	@$(COMPOSE_BASE) config --quiet
	@$(COMPOSE_DEV) config --quiet
	@$(COMPOSE_FRONTEND) config --quiet
	@$(COMPOSE_DEV) --profile frontend config --quiet
	@$(COMPOSE_DEV) --profile mcp config --quiet
	@$(COMPOSE_DEV) --profile db-ui config --quiet
	@$(COMPOSE_DEV) --profile codecov config --quiet
	@$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui --profile codecov config --quiet
	@$(MAKE) --no-print-directory compose-check-prod DOMAIN=example.invalid \
		ACME_EMAIL=ops@example.invalid POSTGRES_PASSWORD=validation-only \
		REDIS_PASSWORD=validation-only MINIO_ROOT_USER=validation \
		MINIO_ROOT_PASSWORD=validation-only BROWSERLESS_TOKEN=validation-only \
		GITHUB_MCP_TOKEN=validation-only

compose-check-prod:
	@$(COMPOSE_PROD) --profile frontend --profile edge --profile mcp --profile codecov config --quiet

preflight: preflight-volumes preflight-model preflight-ports preflight-mcp preflight-codecov compose-check

dev: preflight-volumes preflight-model compose-check
	$(COMPOSE_DEV) up -d --build --wait --wait-timeout 180
	$(COMPOSE_DEV) exec -T app fullstack db upgrade
	@echo "API: http://localhost:8100  Docs: http://localhost:8100/docs"

dev-frontend:
	$(COMPOSE_FRONTEND) up -d frontend

dev-mcp: MCP=1
dev-mcp: preflight-mcp
	$(COMPOSE_DEV) --profile mcp up -d --build docling-mcp browserless chrome-devtools-mcp github-mcp

dev-db-ui:
	$(COMPOSE_DEV) --profile db-ui up -d pgweb

# Self-hosted Codecov on http://localhost:8090. Put the CODECOV_* variables from
# backend/.env.example in a gitignored root .env (Compose reads it for interpolation
# automatically) or export them in the shell; the shell wins where both are set.
dev-codecov: CODECOV=1
dev-codecov: CODECOV_ENV_FILE=.env
dev-codecov: preflight-codecov
	$(COMPOSE_DEV) --profile codecov up -d --wait --wait-timeout 300 codecov-gateway codecov-worker
	@echo "Codecov: http://localhost:8090"

dev-all: MCP=1
dev-all: preflight
	$(COMPOSE_FRONTEND) --profile mcp --profile db-ui up -d --build

dev-down:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui --profile codecov down --remove-orphans

dev-logs:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui --profile codecov logs -f

dev-rebuild:
	$(COMPOSE_DEV) build --no-cache app
	$(COMPOSE_DEV) up -d --force-recreate app taskiq-worker taskiq-scheduler

stage: preflight-volumes preflight-model compose-check
	$(COMPOSE_BASE) up -d --build --wait --wait-timeout 180
	$(COMPOSE_BASE) exec -T app fullstack db upgrade

stage-down:
	$(COMPOSE_BASE) down --remove-orphans

prod: preflight-volumes preflight-edge-ports compose-check
	@powershell.exe -NoProfile -Command "if (-not (Test-Path 'backend/.env')) { throw 'backend/.env is required' }"
	$(COMPOSE_PROD) --profile frontend --profile edge up -d --build --wait --wait-timeout 180
	$(COMPOSE_PROD) exec -T app fullstack db upgrade

# Self-hosted Codecov behind Traefik at https://codecov.$(DOMAIN). Standalone: it starts
# Traefik itself, so a host dedicated to Codecov never needs `make prod` or the application
# stack. Credentials come from the shell or backend/.env. On a Linux host, where the
# PowerShell preflight cannot run, run the recipe's Compose command directly (MANUAL_STEPS).
prod-codecov: CODECOV=1
prod-codecov: CODECOV_ENV_FILE=backend/.env
prod-codecov: preflight-codecov
	$(COMPOSE_PROD) --profile edge --profile codecov up -d --wait --wait-timeout 300 traefik codecov-gateway codecov-worker

prod-down:
	$(COMPOSE_PROD) --profile frontend --profile edge --profile codecov down --remove-orphans

prod-logs:
	$(COMPOSE_PROD) --profile frontend --profile edge --profile codecov logs -f

# The authoritative source-plan audit is a deliberate human gate.
quickstart:
	@powershell.exe -NoProfile -Command "if ('$(SOURCE_PLAN_AUDITED)' -ne '1') { throw 'Set SOURCE_PLAN_AUDITED=1 only after the Sections 1-15 audit.' }"
	$(MAKE) bootstrap

seed:
	@powershell.exe -NoProfile -Command "$$email=$$env:ADMIN_EMAIL; $$password=$$env:ADMIN_PASSWORD; if (-not $$email -or -not $$password) { throw 'Set ADMIN_EMAIL and ADMIN_PASSWORD before seeding.' }; $$users=docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T app fullstack user list; if (-not ($$users | Select-String -SimpleMatch $$email -Quiet)) { docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T app fullstack user create --email $$email --password $$password --superuser; if ($$LASTEXITCODE -ne 0) { exit $$LASTEXITCODE } } else { Write-Output ('Admin already exists: ' + $$email) }"

bootstrap: preflight dev seed

# Safe cleanup: never passes -v, so all named and external data survives.
docker-clean:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui --profile codecov down --remove-orphans

# Explicit opt-in removes only project-managed volumes; external preservation volumes survive.
docker-reset:
	@powershell.exe -NoProfile -Command "if ('$(CONFIRM_DESTROY_LOCAL_DATA)' -ne '1') { throw 'Set CONFIRM_DESTROY_LOCAL_DATA=1 to remove project-managed data volumes.' }"
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui --profile codecov down -v --remove-orphans

install:
	uv sync --directory backend --dev
	uv sync --project tools/repo_governance
	uv run --directory backend pre-commit install --config .pre-commit-config.yaml

format:
	uv run --directory backend ruff format app tests cli
	uv run --directory backend ruff check app tests cli --fix

lint:
	uv run --directory backend ruff check app tests cli
	uv run --directory backend ruff format app tests cli --check
	uv run --directory backend ty check

test:
	uv run --directory backend pytest tests/ -v

# The XML report is what CI uploads to Codecov; emitting it here keeps `make test-cov`
# and the CI `test` job the same command.
test-cov:
	uv run --directory backend pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=xml

# bun, not npm: the repository has only bun.lock, and CI, the Dockerfile, and vercel.json
# all use bun. `test:coverage` (not plain vitest) so the ratcheted thresholds in
# vitest.config.ts are enforced by the registered validator, not only by CI.
frontend-test:
	cd frontend && bun run lint && bun run type-check && bun run test:coverage

frontend-format-check:
	cd frontend && bun run format:check

frontend-build:
	cd frontend && NEXT_TELEMETRY_DISABLED=1 bun run build

playwright:
	cd frontend && bun run test:e2e $(ARGS)

# Every dependency set the repository ships, not only the backend: the governance tool's
# lock, the Docling sidecar's requirements.lock, the frontend's bun.lock, and the
# chrome-devtools sidecar's package-lock.json. Exports go under the gitignored .cache/ so
# they never appear as untracked files. pip-audit runs through uvx in its own ephemeral env.
#
# Two halves so CI can gate on one while measuring the other: audit-python is clean today
# and gates; audit-js currently reports known high findings in the frontend's transitive
# dependencies and runs non-gating in CI until those are bumped in their own session.
AUDIT_TMP ?= .cache/audit
audit: audit-python audit-js

audit-python:
	mkdir -p $(AUDIT_TMP)
	uv export --directory backend --no-hashes --no-emit-project -o ../$(AUDIT_TMP)/backend.txt
	uvx pip-audit -r $(AUDIT_TMP)/backend.txt --disable-pip --no-deps --progress-spinner=off
	uv export --project tools/repo_governance --no-hashes --no-emit-project -o $(AUDIT_TMP)/governance.txt
	uvx pip-audit -r $(AUDIT_TMP)/governance.txt --disable-pip --no-deps --progress-spinner=off
	uvx pip-audit -r docker/mcp/docling/requirements.lock --disable-pip --no-deps --progress-spinner=off

audit-js:
	cd frontend && bun audit --audit-level=high
	cd docker/mcp/chrome-devtools && npm audit --omit=dev --audit-level=high

run:
	uv run --directory backend fullstack server run --reload --port 8100

run-prod:
	uv run --directory backend fullstack server run --host 0.0.0.0 --port 8100

db-migrate:
	@read -p "Migration message: " msg; uv run --directory backend fullstack db migrate -m "$$msg"

db-upgrade:
	uv run --directory backend fullstack db upgrade

db-downgrade:
	uv run --directory backend fullstack db downgrade

db-current:
	uv run --directory backend fullstack db current

db-history:
	uv run --directory backend fullstack db history

taskiq-worker:
	uv run --directory backend taskiq worker app.worker.taskiq_app:broker --workers 1 --max-async-tasks $(TASKIQ_MAX_ASYNC_TASKS)

taskiq-scheduler:
	uv run --directory backend taskiq scheduler app.worker.taskiq_app:scheduler

# --- Governance -------------------------------------------------------------
# The governance CLI is a standalone uv project under tools/ that never imports app.*.
# These targets keep the Makefile the single operational entry point; nothing else should
# invoke `uv run --project tools/repo_governance` directly.
GOVERNANCE := uv run --project tools/repo_governance governance

governance-install:
	uv sync --project tools/repo_governance

governance-preflight:
	$(GOVERNANCE) preflight $(ARGS)

governance-scan:
	$(GOVERNANCE) scan

governance-sync:
	$(GOVERNANCE) sync

governance-check:
	$(GOVERNANCE) check --full $(ARGS)

governance-check-fast:
	$(GOVERNANCE) check --fast $(ARGS)

governance-doctor:
	$(GOVERNANCE) doctor

# The tool's own suite with coverage. Run from the repository root, so --cov-config has to
# name the tool's pyproject explicitly; the ratcheted fail_under lives there.
governance-selftest:
	uv run --project tools/repo_governance pytest tools/repo_governance/tests \
		--cov --cov-config=tools/repo_governance/pyproject.toml \
		--cov-report=term-missing --cov-report=xml:tools/repo_governance/coverage.xml

governance-lint:
	uv run --project tools/repo_governance ruff check tools/repo_governance
	uv run --project tools/repo_governance ruff format --check tools/repo_governance

# Map-vs-territory spot check: seeded by HEAD, so a run is reproducible.
governance-sample:
	$(GOVERNANCE) sample $(ARGS)

# Render every focus-free view. A smoke test that the renderers still produce a page from
# the current graph; artifacts/ is gitignored, so CI uploads the result instead of diffing.
governance-views:
	$(GOVERNANCE) visualize architecture
	$(GOVERNANCE) visualize site
	$(GOVERNANCE) visualize configuration
	$(GOVERNANCE) visualize migration
	$(GOVERNANCE) visualize security

governance-context:
	$(GOVERNANCE) context --paths "$(PATHS)" --task "$(TASK)" $(ARGS)

governance-impact:
	$(GOVERNANCE) impact --paths "$(PATHS)" $(ARGS)

governance-explain:
	$(GOVERNANCE) explain $(ID)

governance-summary:
	$(GOVERNANCE) summary

governance-gate-metrics:
	$(GOVERNANCE) gate-metrics $(ARGS)

governance-visualize:
	$(GOVERNANCE) visualize $(VIEW) $(ARGS)

governance-skills-check:
	$(GOVERNANCE) skills-check $(ARGS)

governance-change-start:
	$(GOVERNANCE) change start --summary "$(SUMMARY)" --reason "$(REASON)"

governance-change-finish:
	$(GOVERNANCE) change finish $(ARGS)

upgrade:
	uvx fastapi-fullstack@latest upgrade $(ARGS)

upgrade-dry-run:
	uvx fastapi-fullstack@latest upgrade --dry-run $(ARGS)

upgrade-new-features:
	uvx fastapi-fullstack@latest upgrade --with-new-features $(ARGS)

upgrade-finalize:
	uvx fastapi-fullstack@latest upgrade finalize $(ARGS)

help:
	@echo "Core: preflight compose-check dev dev-frontend dev-mcp dev-db-ui dev-codecov dev-all"
	@echo "Lifecycle: dev-down dev-logs stage prod prod-codecov docker-clean (preserves data)"
	@echo "Validation: lint test test-cov frontend-test frontend-format-check frontend-build playwright audit compose-check"
	@echo "Governance: governance-preflight governance-context governance-sync governance-check (see AGENTS.md)"
	@echo "First bootstrap: set ADMIN_EMAIL/ADMIN_PASSWORD, audit source-plan Sections 1-15, then make quickstart SOURCE_PLAN_AUDITED=1"
	@echo "Local API: make run (port 8100); Taskiq concurrency defaults to 1"
	@echo "External redis-data and docling-models volumes are never removed by normal targets."
