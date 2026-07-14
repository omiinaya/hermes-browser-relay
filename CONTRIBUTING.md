# Contributing

Thanks for your interest in browser-relay! This guide will help you get started.

## Quick Start

```bash
git clone https://github.com/omiinaya/browser-relay.git
cd browser-relay
make install
make test
```

## Code Standards

- **Python:** ruff lint + format (configured in `pyproject.toml`). Run `make lint` before committing.
- **JavaScript:** ESLint (flat config in `eslint.config.js`). Run `npm run lint`.
- **Tests:** All PRs must pass `make test` (115+ tests). Add tests for new features.

## Architecture Overview

```
plugins/browser-relay/   → Hermes plugin (self-contained)
server.py                → CDP relay (HTTP + WS, aiohttp)
mcp-server/server.py     → MCP stdio server
extension/               → Chrome extension (background.js + content-script + popup)
browser_relay/           → Shared Python utilities
cdp.py                   → CLI client
tests/                   → pytest (relay) + node:test (extension)
```

## Adding a New Tool

1. Add the handler function in `plugins/browser-relay/__init__.py`
2. Register it in `HANDLER_MAP`
3. Add its schema to `TOOL_SCHEMAS`
4. Add the same handler + schema in `mcp-server/server.py`
5. Add tests in `tests/test_server.py` and/or `mcp-server/tests/test_mcp_server.py`
6. Update the tool table in `README.md` and the tool count

## Versioning

The canonical version is in `VERSION`. Before release, run `make version` (when available) or update `VERSION` and sync `manifest.json`, `plugin.yaml`, `package.json`, and any hardcoded version strings in source.

## Pull Requests

- One feature/fix per PR
- Include tests
- Verify with `make test && make lint`
- Commits should be `chore:`, `fix:`, `feat:`, `docs:`, or `refactor:`

## Questions

Open an issue or ask in the Hermes community.
