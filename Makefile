# Convenience wrappers around the documented commands. Each target runs exactly the command
# shown in README.md, so nothing here is required to use Tracehollow.

PNPM ?= npx --yes pnpm@12.4.1
WEB := apps/web
API := services/api

.PHONY: help setup up down ps logs config \
        api-lint api-typecheck api-test \
        web-install web-lint web-typecheck web-test web-build \
        check verify verify-phase1 verify-phase3 ai-eval backup restore-verify

help: ## List available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup: ## Create .env and generate missing local secrets (idempotent)
	scripts/setup.sh

up: ## Build and start the core services, waiting until healthy
	docker compose up --build --detach --wait

down: ## Stop services (volumes and data are kept)
	docker compose down

ps: ## Show service status
	docker compose ps

logs: ## Follow service logs
	docker compose logs --follow

config: ## Validate the Compose configuration
	docker compose config --quiet

api-lint: ## Backend lint and format check
	cd $(API) && uv run ruff check . && uv run ruff format --check .

api-typecheck: ## Backend static type check
	cd $(API) && uv run mypy

api-test: ## Backend tests against ephemeral PostgreSQL and Redis containers
	scripts/test-backend.sh

web-install: ## Install frontend dependencies from the lockfile
	cd $(WEB) && $(PNPM) install --frozen-lockfile

web-lint: ## Frontend lint
	cd $(WEB) && $(PNPM) lint

web-typecheck: ## Frontend type check
	cd $(WEB) && $(PNPM) typecheck

web-test: ## Frontend unit tests
	cd $(WEB) && $(PNPM) test

web-build: ## Frontend production build
	cd $(WEB) && $(PNPM) build

check: config api-lint api-typecheck api-test web-lint web-typecheck web-test web-build ## Run all static checks, tests and builds

verify: ## Full Phase 0 stack verification in an isolated Compose project
	scripts/verify-phase0.sh

verify-phase1: ## Phase 1 stack verification (cases, evidence, executions, recovery, deletion)
	scripts/verify-phase1.sh

verify-phase3: ## Phase 3 stack verification with the synthetic fixture AI provider
	scripts/verify-phase3.sh

ai-eval: ## Model-backed AI evaluation against the configured local models (needs Ollama)
	scripts/ai-eval.sh --providers configured

backup: ## Back up the database and evidence volume of the running stack
	scripts/backup.sh

restore-verify: ## Non-destructive restore drill: make restore-verify BACKUP=backups/<timestamp>
	@test -n "$(BACKUP)" || (echo "usage: make restore-verify BACKUP=backups/<timestamp>" && exit 2)
	scripts/restore.sh "$(BACKUP)" --verify-only
