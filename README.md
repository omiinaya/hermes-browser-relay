# CDP Bridge

A Chrome DevTools Protocol relay server that bridges remote browser control between a Chrome extension and automation agents (Hermes, custom scripts) via WebSocket + HTTP.

## Architecture

```
Agent ──HTTP POST /cdp──> relay ──WebSocket──> Chrome Extension ──CDP──> Chrome tab
Agent <──HTTP response── relay <──WebSocket── Chrome Extension <──CDP── Chrome tab
```

Two servers:
- **CDP Relay** (`server.py`, port 9223) — sends CDP commands to Chrome, responds via HTTP
- **Capture Server** (`capture_server.py`, port 9988) — receives browser events (web requests, downloads, navigation) and writes to JSONL

## Quick Start

```bash
# Start the CDP relay server
python3 server.py

# Start the capture server (optional, for event logging)
python3 capture_server.py

# Load the Chrome extension
# chrome://extensions/ → Load unpacked → extension/
```

The extension auto-connects to `ws://127.0.0.1:9223/ws`. Click **Attach Tab** to start receiving CDP commands.

## Usage

```bash
# Check server and extension status
python3 cdp.py status

# Navigate to a URL
python3 cdp.py navigate https://example.com

# Evaluate JavaScript
python3 cdp.py eval "document.title"

# Click an element
python3 cdp.py click "#submit-btn"

# Type into a field
python3 cdp.py type "#search" "hello world"

# Capture a screenshot
python3 cdp.py screenshot

# Send arbitrary CDP command
python3 cdp.py send Network.getCookies '{}'

# Stream CDP events in real-time
python3 cdp.py watch
```

## API

The relay server provides:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdp` | POST | Send a CDP command `{"method": "Page.navigate", "params": {"url": "..."}}` |
| `/cdp` | GET | Quick command via query params `?method=Page.navigate&url=...` |
| `/status` | GET | Connection health, attached tab ID, uptime |
| `/events` | GET | Server-Sent Events stream of CDP events |
| `/ws` | WS | WebSocket endpoint for the Chrome extension |

## License

MIT
