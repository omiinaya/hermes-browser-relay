.PHONY: test test-python test-mcp test-ext test-all install lint fmt fix clean check-version node_modules docker-build docker-run docker-stop docs-build docs-serve

PYTEST_FLAGS = -v --tb=short

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-python test-mcp test-ext  ## Run all non-E2E tests (119 tests)

test-all: test test-integration  ## Run all tests including E2E (123 tests, requires Chromium)

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
	test -n "$(VERSION)" && sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	test -n "$(VERSION)" && sed -i 's|<span>[0-9.]*</span>|<span>$(VERSION)</span>|' extension/popup.html
	test -n "$(VERSION)" && sed -i 's/^version: .*/version: $(VERSION)/' AGENTS.md
	test -n "$(VERSION)" && echo "Version updated to $(VERSION)" || echo "Usage: make update-version VERSION=x.y.z"

fmt-js:  ## Auto-fix JS with ESLint
	npm run lint:fix

minify-ext:  ## Minify content-script.js using terser (via npx)
	npx terser extension/content-script.js -o extension/content-script.js --compress --mangle --comments false 2>/dev/null || \
	  echo "Note: content-script.js is maintained in minified form; terser is optional."

minify-helper:  ## Regenerate hermes-helper.min.js from source
	npx terser extension/hermes-helper.js -o extension/hermes-helper.min.js --compress --mangle --comments false

fmt:  ## Check Python code formatting without modifying
	ruff format --check .

check-version:  ## Verify VERSION file matches all versioned files
	@v=$$(cat VERSION); \
	fail=0; \
	for f in extension/manifest.json package.json plugins/browser-relay/plugin.yaml AGENTS.md; do \
		if ! grep -q "$$v" "$$f"; then \
			echo "❌ VERSION mismatch in $$f"; fail=1; \
		fi; \
	done; \
	if ! grep -q "^version = \"$$v\"" pyproject.toml; then \
		echo "❌ VERSION mismatch in pyproject.toml"; fail=1; \
	fi; \
	if ! grep -q "<span>$$v</span>" extension/popup.html; then \
		echo "❌ VERSION mismatch in extension/popup.html"; fail=1; \
	fi; \
	if [ $$fail -eq 0 ]; then echo "✅ All version files match ($$v)"; fi

fix:  ## Auto-fix lint issues
	ruff check --fix .
	ruff format .

clean:  ## Clean build artifacts
	rm -rf node_modules dist target __pycache__ .pytest_cache .ruff_cache .coverage .coverage.* htmlcov/

docker-build:  ## Build Docker image
	docker build -t browser-relay .

docker-run:  ## Run Docker container (port 9223)
	docker run -d -p 9223:9223 --name browser-relay browser-relay

docker-stop:  ## Stop Docker container
	docker stop browser-relay 2>/dev/null; docker rm browser-relay 2>/dev/null; true

docs-build:  ## Build Docusaurus documentation site
	cd website && npm run build

docs-serve:  ## Serve built documentation locally (port 3000)
	cd website && npm run serve

docs-dev:  ## Start Docusaurus dev server with hot reload (port 3000)
	cd website && npm run start

install:  ## Install all dependencies
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]" 2>/dev/null || \
	  .venv/bin/pip install aiohttp websockets Pillow 'mcp>=1.0.0'
	npm install

node_modules:
	npm install
