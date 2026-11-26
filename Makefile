.DEFAULT_GOAL := help
.PHONY: help install lint typecheck format test check extract clean

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-11s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install locked dependencies
	uv sync --locked

lint: ## Check style and formatting without changing files
	uv run ruff check .
	uv run ruff format --check .

format: ## Fix what can be fixed automatically and format the code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type check of the package
	uv run mypy

test: ## Run the test suite with coverage (never touches the network)
	uv run pytest --cov --cov-report=term-missing:skip-covered

check: lint typecheck test ## Everything CI runs

extract: ## Collect what is new into the raw layer (SINCE=YYYY-MM sets the first month)
	uv run fuel-lakehouse extract $(if $(SINCE),--since $(SINCE)) $(ARGS)

clean: ## Remove caches and build output (never touches data/)
	rm -rf .pytest_cache .ruff_cache dist build
	find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +
