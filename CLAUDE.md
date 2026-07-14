# browser-relay — Agent Quickstart

This is a CDP bridge for remote Chrome browser control.

## First-Time Setup

```bash
# Install deps
pip install aiohttp websockets Pillow 'mcp>=1.0.0'

# Start relay
python3 server.py
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

- `server.py` — CDP relay (HTTP + WS)
- `mcp-server/server.py` — MCP tool server (stdio)
- `extension/` — Chrome extension (background.js, content-script.js, hermes-helper.js)
- `plugins/browser-relay/` — Hermes plugin (self-contained, inherits shared utilities)
- `browser_relay/shared.py` — Shared utilities between MCP server and plugin
- `cdp.py` — CLI client

## Architecture

Extension connects via WebSocket to relay. Relay forwards CDP commands. Content script (injected into all pages) handles DOM operations directly without CDP overhead. Automatic fallback to CDP when CS is mid-navigation.
