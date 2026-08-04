# Development

## Setup

```bash
git clone https://github.com/omiinaya/hermes-browser-relay.git
cd browser-relay
make install            # All dependencies (Python + npm)
```

## Available Make Targets

| Target | Description |
|--------|-------------|
| `make test` | Run all non-E2E tests (135 tests) |
| `make test-all` | Run all tests including E2E (139 tests, requires Chromium) |
| `make test-python` | Relay server tests (51) |
| `make test-mcp` | MCP server tests (24) |
| `make test-plugin` | Hermes plugin tests (5) |
| `make test-ext` | Extension tests (55) |
| `make test-integration` | E2E tests (4, requires Chromium) |
| `make lint` | Ruff check (Python) |
| `make lint-js` | ESLint (JavaScript) |
| `make lint-all` | All linters |
| `make fmt` | Check Python formatting (no changes) |
| `make fix` | Auto-fix lint issues + format |
| `make clean` | Remove build artifacts |
| `make docker-build` | Build Docker image |
| `make docker-run` | Run Docker container (port 9223) |
| `make update-version VERSION=x.y.z` | Sync version across all files |
| `make check-version` | Verify all version files match `VERSION` |

## Project Structure

```
browser-relay/
├── plugins/browser-relay/   → Hermes plugin (self-contained)
├── server.py                → CDP relay (HTTP + WS, aiohttp)
├── mcp-server/server.py     → MCP stdio server
├── extension/               → Chrome extension
│   ├── background.js        → Service worker (CDP ↔ WebSocket bridge)
│   ├── content-script.js    → MAIN world DOM fast path (minified)
│   ├── content-script.src.js → Editable source for content-script
│   ├── hermes-helper.js     → window.__hermes DOM library
│   ├── hermes-helper.min.js → Minified helper
│   ├── manifest.json        → Extension manifest (MV3)
│   └── popup.html/js        → Extension popup UI
├── browser_relay/           → Shared Python utilities
├── cdp.py                   → CLI client
├── tests/                   → pytest (relay) + node:test (extension) + E2E
└── website/                 → Docusaurus documentation site
```

## Git Conventions

```bash
git config commit.template .gitmessage
git config core.hooksPath .githooks
```

- Commits use conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
- Pre-commit hooks run `make lint` and `make check-version`

## Adding a New Tool

1. Add the handler function in `plugins/browser-relay/__init__.py`
2. Register it in `HANDLER_MAP`
3. Add its schema to `TOOL_SCHEMAS`
4. Add the same handler + schema in `mcp-server/server.py`
5. Add tests in `tests/test_server.py` and/or `mcp-server/tests/test_mcp_server.py`
6. Update the tool table in README.md and the tool count
7. Update `website/docs/tools.md` with the new tool
