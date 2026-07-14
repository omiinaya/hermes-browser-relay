.PHONY: test test-python test-mcp test-ext install lint fmt fix clean node_modules

PYTEST_FLAGS = -v --tb=short

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-python test-mcp test-ext  ## Run all tests

test-python:  ## Run relay server tests
	.venv/bin/python -m pytest tests/ $(PYTEST_FLAGS)

test-mcp:  ## Run MCP server tests
	.venv/bin/python -m pytest mcp-server/tests/ $(PYTEST_FLAGS)

test-ext: node_modules  ## Run extension tests (all 3 test files)
	node --test --test-force-exit tests/extensions/*.test.js

lint:  ## Lint Python code with ruff
	ruff check .

minify-ext:  ## Minify content-script.js using terser (via npx)
	npx terser extension/content-script.js -o extension/content-script.js --compress --mangle --comments false 2>/dev/null || \
	  echo "Note: content-script.js is maintained in minified form; terser is optional."

fmt:  ## Format Python code with ruff
	ruff format --check .

fix:  ## Auto-fix lint issues
	ruff check --fix .
	ruff format .

clean:  ## Clean build artifacts
	rm -rf node_modules dist target __pycache__ .pytest_cache .ruff_cache

install:  ## Install all dependencies
	.venv/bin/pip install -e ".[dev]" 2>/dev/null || \
	  .venv/bin/pip install aiohttp websockets Pillow 'mcp>=1.0.0'
	npm install

node_modules:
	npm install
