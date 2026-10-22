# Forge — common tasks.
#
# The local stack needs no container runtime: PostgreSQL is an embedded server and
# Redis is built from source on first use. See docs/development.md.

SHELL := /bin/bash
PYTHON ?= .venv/bin/python
WEB := apps/web

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1;34m%-18s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------- setup

.PHONY: venv
venv: ## Create the virtualenv
	python3.12 -m venv .venv

.PHONY: install
install: ## Install Python dependencies and verify workspace imports
	bash scripts/dev_install.sh

.PHONY: install-web
install-web: ## Install frontend dependencies
	cd $(WEB) && npm install

.PHONY: setup
setup: install install-web ## Full first-time setup

# ------------------------------------------------------------------ database

.PHONY: migrate
migrate: ## Apply database migrations
	source scripts/env.sh && cd packages/forge-core && ../../$(PYTHON) -m alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="description"
	source scripts/env.sh && cd packages/forge-core && \
	  ../../$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

.PHONY: migrate-down
migrate-down: ## Roll back one migration
	source scripts/env.sh && cd packages/forge-core && ../../$(PYTHON) -m alembic downgrade -1

# --------------------------------------------------------------------- stack

.PHONY: up
up: ## Start the full local stack
	bash scripts/run_stack.sh start

.PHONY: down
down: ## Stop the stack, keeping all data
	bash scripts/run_stack.sh stop

.PHONY: restart
restart: ## Restart the stack
	bash scripts/run_stack.sh restart

.PHONY: status
status: ## Show which services are running
	bash scripts/run_stack.sh status

.PHONY: logs
logs: ## Tail every service log
	bash scripts/run_stack.sh logs

.PHONY: clean
clean: ## Stop the stack and DELETE all local data (destructive)
	bash scripts/run_stack.sh clean --yes

.PHONY: web
web: ## Run the dashboard dev server
	cd $(WEB) && npm run dev

# ----------------------------------------------------------------- operations

.PHONY: seed
seed: ## Create and register the bundled demo repository
	source scripts/env.sh && $(PYTHON) -m forge_api.cli seed-demo

.PHONY: create-user
create-user: ## Create a user: make create-user email=you@example.com role=ADMIN
	source scripts/env.sh && $(PYTHON) -m forge_api.cli create-user \
	  --email "$(email)" --role "$(or $(role),DEVELOPER)"

.PHONY: reset-runtime
reset-runtime: ## Delete pipeline/deployment history, keep users and repositories
	source scripts/env.sh && $(PYTHON) -m forge_api.cli reset-runtime --yes

.PHONY: demo
demo: ## Run the full demonstration (healthy release, then rollback)
	source scripts/env.sh && $(PYTHON) scripts/demo.py

.PHONY: demo-healthy
demo-healthy: ## Run only the healthy release scenario
	source scripts/env.sh && $(PYTHON) scripts/demo.py --healthy

.PHONY: demo-rollback
demo-rollback: ## Run only the faulty release / rollback scenario
	source scripts/env.sh && $(PYTHON) scripts/demo.py --faulty

# ---------------------------------------------------------------------- tests

.PHONY: test
test: ## Run the Python test suite
	source scripts/env.sh && $(PYTHON) -m pytest tests apps/sample-service/tests -q

.PHONY: test-unit
test-unit: ## Run unit tests only (no database required)
	source scripts/env.sh && $(PYTHON) -m pytest tests/unit -q

.PHONY: test-integration
test-integration: ## Run integration tests (requires PostgreSQL)
	source scripts/env.sh && $(PYTHON) -m pytest tests/integration -q

.PHONY: test-reliability
test-reliability: ## Run crash, duplicate-delivery and recovery tests
	source scripts/env.sh && $(PYTHON) -m pytest tests/reliability -q

.PHONY: test-web
test-web: ## Run frontend unit tests
	cd $(WEB) && npm test

.PHONY: e2e
e2e: ## Run Playwright end-to-end tests (requires a running stack)
	cd $(WEB) && npx playwright test

.PHONY: test-all
test-all: test test-web ## Run every non-browser test suite

# --------------------------------------------------------------------- checks

.PHONY: lint
lint: ## Lint Python
	source scripts/env.sh && .venv/bin/ruff check apps packages tests scripts

.PHONY: format
format: ## Auto-fix Python lint issues
	source scripts/env.sh && .venv/bin/ruff check --fix apps packages tests scripts
	source scripts/env.sh && .venv/bin/ruff format apps packages tests scripts

.PHONY: typecheck
typecheck: ## Type-check Python (strict)
	source scripts/env.sh && .venv/bin/mypy apps packages

.PHONY: lint-web
lint-web: ## Lint and type-check the frontend
	cd $(WEB) && npm run lint && npm run typecheck

.PHONY: build-web
build-web: ## Production build of the dashboard
	cd $(WEB) && npm run build

.PHONY: check
check: lint typecheck test lint-web ## Everything CI runs on a pull request

# ---------------------------------------------------------------- terraform

.PHONY: tf-fmt
tf-fmt: ## Check Terraform formatting
	terraform -chdir=infra/terraform fmt -check -recursive

.PHONY: tf-validate
tf-validate: ## Validate the Terraform configuration
	terraform -chdir=infra/terraform init -backend=false && \
	  terraform -chdir=infra/terraform validate
