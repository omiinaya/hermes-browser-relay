.PHONY: test test-python test-mcp test-ext lint fmt fix clean

PYTEST_FLAGS = -v --tb=short

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-python test-mcp test-ext  ## Run all tests

test-python:  ## Run relay server tests
	python3 -m pytest tests/ $(PYTEST_FLAGS)

test-mcp:  ## Run MCP server tests
	python3 -m pytest mcp-server/tests/ $(PYTEST_FLAGS)

test-ext:  ## Run extension tests
	node --test tests/extensions/hermes-helper.test.js

lint:  ## Lint Python code with ruff
	ruff check .

fmt:  ## Format Python code with ruff
	ruff format --check .

fix:  ## Auto-fix lint issues
	ruff check --fix .
	ruff format .

clean:  ## Clean build artifacts
	rm -rf node_modules dist target __pycache__ .pytest_cache .ruff_cache
