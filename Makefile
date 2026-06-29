.PHONY: build test lint fmt fix clean help

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build:  ## Build the project
	@echo "See AGENTS.md for build instructions"

test:  ## Run tests
	@echo "See AGENTS.md for test instructions"

lint:  ## Lint code
	@echo "Linting not configured"

fmt:  ## Format code
	@echo "Formatting not configured"

fix: lint fmt  ## Auto-fix issues

clean:  ## Clean build artifacts
	rm -rf node_modules dist target __pycache__
