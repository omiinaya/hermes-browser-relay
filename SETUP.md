# Setup

## Hermes Plugin Install

```bash
# 1. Clone and install deps
git clone https://github.com/omiinaya/hermes-browser-relay.git
cd browser-relay
make install          # Installs Python (aiohttp, websockets, Pillow) + npm deps

# 2. Start the CDP relay server
python3 server.py &

# 3. Load the Chrome extension
# Open chrome://extensions/ → Enable Developer mode → Load unpacked → select extension/

# 4. Install as Hermes plugin
hermes plugins install .
# Restart Hermes — tools (browser_navigate, browser_eval, etc.) appear automatically
```

The plugin auto-starts the relay server on first tool use if it's not already running.

## Prerequisites

- **Python 3.10+** — relay server, MCP server, Hermes plugin
- **Node.js 18+** — extension tests only
- **Chrome/Chromium** — the extension drives Chrome via `chrome.debugger` API
- **Hermes Agent** — the plugin registers tools for Hermes

## Cross-LAN Setup

For controlling a browser on a different machine:

1. Start the relay on the target machine: `python3 server.py`
2. In the extension popup, set the server URL to `ws://<server-lan-ip>:9223/ws`
3. Point the Hermes plugin's `CDP_SERVER` to `http://<server-lan-ip>:9223`

## Manual Start (without Hermes)

If you want to use the CLI tools or HTTP API directly:

```bash
python3 server.py              # Start relay
python3 cdp.py status          # Check it's running
python3 cdp.py navigate https://example.com
python3 -m mcp-server.server   # Optional: MCP stdio server
```

## Development

```bash
make install        # All deps
make test-python    # Relay tests (52)
make test-mcp       # MCP tests (24)
make test-plugin    # Plugin tests (5)
make test-ext       # Extension tests (55)
make test           # All of the above (136 tests)
make test-all       # All including E2E (140 tests, requires Chromium)
make test-integration  # E2E tests (4, requires Chromium)
make lint           # ruff check
npm run lint        # ESLint
make lint-all       # Both
make docker-build   # Build Docker image
make docker-run     # Run container on port 9223
make docs-build     # Build Docusaurus documentation site
make docs-serve     # Serve docs locally (port 3000)

### Pre-commit Hooks

Enable the project's pre-commit hooks:
```bash
git config core.hooksPath .githooks
```
This runs `make lint` on every commit.
