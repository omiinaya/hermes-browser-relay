.PHONY: test test-python test-mcp test-ext install lint fmt fix clean node_modules

PYTEST_FLAGS = -v --tb=short

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-python test-mcp test-ext  ## Run all tests

test-python:  ## Run relay server tests (excluding integration/E2E)
	.venv/bin/python -m pytest tests/ --ignore=tests/integration $(PYTEST_FLAGS)

test-mcp:  ## Run MCP server tests
	.venv/bin/python -m pytest mcp-server/tests/ $(PYTEST_FLAGS)

test-integration:  ## Run integration/E2E tests (requires Chromium)
	.venv/bin/python -m pytest tests/integration/ -v --tb=long

test-ext: node_modules  ## Run extension tests (all 3 test files)
	node --test --test-force-exit tests/extensions/*.test.js

lint:  ## Lint Python code with ruff
	ruff check .
lint-js:  ## Lint JavaScript with ESLint
	npm run lint
lint-all: lint lint-js  ## Run all linters

update-version:  ## Update version in all files (usage: make update-version VERSION=2.4.3)
	test -n "$(VERSION)" && echo $(VERSION) > VERSION
	test -n "$(VERSION)" && sed -i 's/"version": ".*"/"version": "$(VERSION)"/' extension/manifest.json
	test -n "$(VERSION)" && sed -i 's/version: .*/version: $(VERSION)/' plugins/browser-relay/plugin.yaml
	test -n "$(VERSION)" && sed -i 's/"version": ".*"/"version": "$(VERSION)"/' package.json
	test -n "$(VERSION)" && echo "Version updated to $(VERSION)" || echo "Usage: make update-version VERSION=x.y.z"

fmt-js:  ## Auto-fix JS with ESLint
	npm run lint:fix

minify-ext:  ## Minify content-script.js using terser (via npx)
	npx terser extension/content-script.js -o extension/content-script.js --compress --mangle --comments false 2>/dev/null || \
	  echo "Note: content-script.js is maintained in minified form; terser is optional."

minify-helper:  ## Regenerate hermes-helper.min.js from source
	npx terser extension/hermes-helper.js -o extension/hermes-helper.min.js --compress --mangle --comments false

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
