---
name: browser-relay
description: "Chrome DevTools Protocol relay — control browser remotely via chrome.debugger API + WebSocket bridge"
stack: [python, javascript]
deps: [python3, node]
---

# browser-relay

Chrome DevTools Protocol relay — control browser remotely via chrome.debugger API + WebSocket bridge.

## Stack
- **Python 3.10+** — Relay server (`server.py`), MCP server (`mcp-server/server.py`), Hermes plugin (`plugins/`)
- **JavaScript** — Chrome extension (`extension/background.js`, `extension/content-script.js`, `extension/hermes-helper.js`)

## Quick Reference

```bash
# Relay server
python3 server.py

# MCP server
python3 -m mcp-server.server

# Python tests
python3 -m pytest tests/ mcp-server/tests/ -v

# Extension tests
node --test tests/extensions/hermes-helper.test.js

# Install Node dependencies
npm install
```

## Documentation
| Document | Purpose |
|---|---|
| [README.md](./README.md) | Full human-readable docs |
| [CHANGELOG.md](./CHANGELOG.md) | Version history |
| [SETUP.md](./SETUP.md) | Step-by-step setup |
| [Makefile](./Makefile) | Build/test/lint targets |
