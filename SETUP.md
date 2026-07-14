# Setup

## Prerequisites

- Python 3.10+
- Node.js 18+ (for extension tests)
- Chrome/Chromium browser

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/omiinaya/browser-relay.git
cd browser-relay

# 2. Install Python dependencies
pip install aiohttp websockets Pillow 'mcp>=1.0.0' pytest pytest-asyncio

# 3. Install Node.js dependencies (for extension tests)
npm install

# 4. Start the CDP relay server
python3 server.py

# 5. Load the Chrome extension
# Open chrome://extensions/ → Enable Developer mode → Load unpacked → select extension/

# 6. Verify it works
python3 cdp.py status
# Should show the extension connected and a tab attached

# 7. (Optional) Start the MCP server for agent use
python3 -m mcp-server.server
```

## Cross-LAN Setup

For remote browser control across machines:
1. Start the relay on the target machine: `python3 server.py`
2. In the extension popup, set the server URL to `ws://<server-lan-ip>:9223/ws`
3. Use `cdp.py --server http://<server-lan-ip>:9223` from the agent machine

## Running Tests

```bash
# Relay server tests (39)
python3 -m pytest tests/ -v

# MCP server tests (24)
python3 -m pytest mcp-server/tests/ -v

# Extension tests (28)
node --test tests/extensions/hermes-helper.test.js

# All Python tests
python3 -m pytest tests/ mcp-server/tests/ -v --tb=short
```

## Plugin Setup (Hermes Agent)

```bash
hermes plugins enable browser-relay
# Restart Hermes — tools appear as browser_navigate, browser_eval, etc.
# The plugin communicates with the relay server at localhost:9223
```
