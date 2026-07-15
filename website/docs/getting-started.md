# Getting Started

Get browser-relay running in minutes.

## Prerequisites

- **Python 3.10+** — relay server, MCP server, Hermes plugin
- **Node.js 18+** — extension tests only  
- **Chrome/Chromium** — the extension drives Chrome via `chrome.debugger`
- **Hermes Agent** — the plugin registers tools for Hermes (optional — you can use the MCP server or CLI directly)

## Quick Install

```bash
# Clone the repo
git clone https://github.com/omiinaya/browser-relay.git
cd browser-relay

# Install dependencies
make install         # Python deps + npm

# Start the relay server
python3 server.py &

# Load the Chrome extension
# chrome://extensions/ → Enable Developer mode → Load unpacked → select extension/

# Install the Hermes plugin
hermes plugins install .
# Restart Hermes — 46 browser tools appear automatically
```

## Quick Start (3 commands)

```bash
python3 server.py &                  # 1. Start the relay
# chrome://extensions/ → Load unpacked → extension/  # 2. Load extension
python3 cdp.py navigate https://example.com          # 3. Navigate to a URL
```

## What's Next?

- [Architecture](./architecture) — understand how the components fit together
- [Tools](./tools) — full list of 46 browser automation tools
- [Docker](./docker) — run the relay in a container
- [CLI](./cli) — use `cdp.py` for manual debugging
