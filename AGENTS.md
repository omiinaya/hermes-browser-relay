---
name: browser-relay
description: "Hermes plugin for remote Chrome/Chromium browser control via CDP"
stack: [python, javascript]
deps: [python3, node, chromium]
---

# browser-relay — Hermes Plugin

Controls Chrome/Chromium through CDP (Chrome DevTools Protocol). 
46 browser automation tools available as Hermes agent tools.

## First-Time Setup

```bash
# Install deps
pip install aiohttp websockets Pillow 'mcp>=1.0.0'
npm install

# Start relay
python3 server.py &

# Load Chrome extension
# chrome://extensions/ → Load unpacked → extension/

# Install Hermes plugin
hermes plugins install /path/to/browser-relay
```

## Key Commands

| Action | Command |
|--------|---------|
| Start relay | `python3 server.py` |
| Check status | `python3 cdp.py status` |
| Run tests | `make test` |
| Lint | `make lint` |
| Install deps | `make install` |

## Structure

- `plugins/browser-relay/` — **Hermes plugin** (self-contained, no external imports)
- `server.py` — CDP relay (HTTP + WS)
- `extension/` — Chrome extension (background, content-script, hermes-helper)
- `mcp-server/server.py` — MCP stdio server (alternative to plugin)
- `cdp.py` — CLI client for manual debugging
- `browser_relay/` — shared utilities (for MCP server + dev)
- `tests/` — relay server, extension, and E2E tests

## Architecture

Hermes Agent → Plugin (POST /cdp) → Relay Server → WS → Extension → chrome.debugger → Browser Tabs
```
