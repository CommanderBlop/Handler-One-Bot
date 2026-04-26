# Handler-One Discord Bot — local dev targets.
#
# Quickstart:
#   make venv        Create a local virtualenv and install deps
#   make run         Run the bot from the venv (reads .env)
#   make docker-up   Build and run the containerized bot
#   make docker-logs Tail container logs

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help venv install run lint test docker-build docker-up docker-down docker-logs clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

venv: ## Create venv and install the package in editable mode
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

install: ## Reinstall (after dependency changes)
	$(PIP) install -e ".[dev]"

run: ## Run the bot locally
	$(PY) -m scripts.run

lint: ## Run ruff
	$(VENV)/bin/ruff check .

test: ## Run pytest
	$(VENV)/bin/pytest

docker-build: ## Build the container image
	docker compose build

docker-up: ## Build and start the container in the background
	docker compose up -d --build

docker-down: ## Stop and remove the container
	docker compose down

docker-logs: ## Tail container logs (Ctrl+C to stop)
	docker compose logs -f handler

clean: ## Remove build artifacts and the venv
	rm -rf $(VENV) build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
