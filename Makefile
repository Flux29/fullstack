.PHONY: help install format lint test test-cov frontend-test playwright run run-prod \
	preflight preflight-volumes preflight-model preflight-ports preflight-edge-ports preflight-mcp compose-check \
	dev dev-frontend dev-mcp dev-db-ui dev-all dev-down dev-logs dev-rebuild stage stage-down \
	prod prod-down prod-logs quickstart seed bootstrap docker-clean docker-reset \
	db-migrate db-upgrade db-downgrade db-current db-history taskiq-worker taskiq-scheduler \
	upgrade upgrade-dry-run upgrade-new-features upgrade-finalize

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
	@powershell.exe -NoProfile -Command "$$ports=8100,5432,6379,9000,9001,5001,3000,8081,8201,8202,8203; $$busy=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in $$ports; if($$busy){Write-Error ('Occupied development ports: ' + (($$busy.LocalPort | Sort-Object -Unique) -join ', ')); exit 1}"

preflight-edge-ports:
	@powershell.exe -NoProfile -Command "$$ports=80,443,8080; $$busy=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in $$ports; if($$busy){Write-Error ('Occupied edge ports: ' + (($$busy.LocalPort | Sort-Object -Unique) -join ', ')); exit 1}"

preflight-mcp:
	@powershell.exe -NoProfile -Command "if ('$(MCP)') { if (-not $$env:BROWSERLESS_TOKEN -or $$env:BROWSERLESS_TOKEN -eq 'change-me-before-enabling-mcp') { throw 'Set a strong BROWSERLESS_TOKEN before enabling MCP.' }; if (-not $$env:GITHUB_MCP_TOKEN) { throw 'Set GITHUB_MCP_TOKEN before enabling MCP.' } }"

compose-check:
	@$(COMPOSE_BASE) config --quiet
	@$(COMPOSE_DEV) config --quiet
	@$(COMPOSE_FRONTEND) config --quiet
	@$(COMPOSE_DEV) --profile frontend config --quiet
	@$(COMPOSE_DEV) --profile mcp config --quiet
	@$(COMPOSE_DEV) --profile db-ui config --quiet
	@$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui config --quiet
	@$(MAKE) --no-print-directory compose-check-prod DOMAIN=example.invalid \
		ACME_EMAIL=ops@example.invalid POSTGRES_PASSWORD=validation-only \
		REDIS_PASSWORD=validation-only MINIO_ROOT_USER=validation \
		MINIO_ROOT_PASSWORD=validation-only BROWSERLESS_TOKEN=validation-only \
		GITHUB_MCP_TOKEN=validation-only

compose-check-prod:
	@$(COMPOSE_PROD) --profile frontend --profile edge --profile mcp config --quiet

preflight: preflight-volumes preflight-model preflight-ports preflight-mcp compose-check

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

dev-all: MCP=1
dev-all: preflight
	$(COMPOSE_FRONTEND) --profile mcp --profile db-ui up -d --build

dev-down:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui down --remove-orphans

dev-logs:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui logs -f

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

prod-down:
	$(COMPOSE_PROD) --profile frontend --profile edge down --remove-orphans

prod-logs:
	$(COMPOSE_PROD) --profile frontend --profile edge logs -f

# The authoritative source-plan audit is a deliberate human gate.
quickstart:
	@powershell.exe -NoProfile -Command "if ('$(SOURCE_PLAN_AUDITED)' -ne '1') { throw 'Set SOURCE_PLAN_AUDITED=1 only after the Sections 1-15 audit.' }"
	$(MAKE) bootstrap

seed:
	@powershell.exe -NoProfile -Command "$$email=$$env:ADMIN_EMAIL; $$password=$$env:ADMIN_PASSWORD; if (-not $$email -or -not $$password) { throw 'Set ADMIN_EMAIL and ADMIN_PASSWORD before seeding.' }; $$users=docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T app fullstack user list; if (-not ($$users | Select-String -SimpleMatch $$email -Quiet)) { docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T app fullstack user create --email $$email --password $$password --superuser; if ($$LASTEXITCODE -ne 0) { exit $$LASTEXITCODE } } else { Write-Output ('Admin already exists: ' + $$email) }"

bootstrap: preflight dev seed

# Safe cleanup: never passes -v, so all named and external data survives.
docker-clean:
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui down --remove-orphans

# Explicit opt-in removes only project-managed volumes; external preservation volumes survive.
docker-reset:
	@powershell.exe -NoProfile -Command "if ('$(CONFIRM_DESTROY_LOCAL_DATA)' -ne '1') { throw 'Set CONFIRM_DESTROY_LOCAL_DATA=1 to remove project-managed data volumes.' }"
	$(COMPOSE_DEV) --profile frontend --profile mcp --profile db-ui down -v --remove-orphans

install:
	uv sync --directory backend --dev
	uv run --directory backend pre-commit install

format:
	uv run --directory backend ruff format app tests cli
	uv run --directory backend ruff check app tests cli --fix

lint:
	uv run --directory backend ruff check app tests cli
	uv run --directory backend ruff format app tests cli --check
	uv run --directory backend ty check

test:
	uv run --directory backend pytest tests/ -v

test-cov:
	uv run --directory backend pytest tests/ -v --cov=app --cov-report=term-missing

frontend-test:
	cd frontend && npm run lint && npm run type-check && npm test -- --run

playwright:
	cd frontend && npm run test:e2e

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

upgrade:
	uvx fastapi-fullstack@latest upgrade $(ARGS)

upgrade-dry-run:
	uvx fastapi-fullstack@latest upgrade --dry-run $(ARGS)

upgrade-new-features:
	uvx fastapi-fullstack@latest upgrade --with-new-features $(ARGS)

upgrade-finalize:
	uvx fastapi-fullstack@latest upgrade finalize $(ARGS)

help:
	@echo "Core: preflight compose-check dev dev-frontend dev-mcp dev-db-ui dev-all"
	@echo "Lifecycle: dev-down dev-logs stage prod docker-clean (preserves data)"
	@echo "Validation: lint test frontend-test playwright"
	@echo "First bootstrap: set ADMIN_EMAIL/ADMIN_PASSWORD, audit source-plan Sections 1-15, then make quickstart SOURCE_PLAN_AUDITED=1"
	@echo "Local API: make run (port 8100); Taskiq concurrency defaults to 1"
	@echo "External redis-data and docling-models volumes are never removed by normal targets."
