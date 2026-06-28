# CDP Bridge — Remote Browser Control

A Chrome DevTools Protocol relay server that bridges remote browser control between a Chrome extension and automation agents (Hermes, custom scripts) via WebSocket + HTTP.

**Version 1.1.0** — Features heartbeat keepalive, exponential backoff reconnect, tab-switch auto-reattach, stale request sweep, and graceful shutdown.

## Architecture

```
┌──────────────┐   POST /cdp    ┌──────────────┐   WebSocket    ┌──────────────┐   CDP    ┌────────┐
│              │ ─────────────> │              │ ─────────────> │              │ ───────> │        │
│  Hermes      │                │  Relay       │                │  Extension   │          │ Chrome │
│  Agent       │ <───────────── │  Server      │ <───────────── │  (Service    │ <─────── │  Tab   │
│              │   HTTP resp    │  :9223       │   WS resp      │   Worker)    │   CDP    │        │
└──────────────┘                └──────────────┘                └──────────────┘          └────────┘
                                        │                              │
                                        │ WebSocket                    │ WebSocket
                                        │ :9988                        │ :9988
                                        └──────────┬───────────────────┘
                                                   │
                                          ┌────────┴────────┐
                                          │  Capture Server  │
                                          │  :9988           │
                                          │  → capture.jsonl │
                                          └─────────────────┘
```

Two servers:
- **CDP Relay** (`server.py`, port `:9223`) — sends CDP commands to Chrome, responds via HTTP
- **Capture Server** (`capture_server.py`, port `:9988`) — receives browser events (web requests, downloads, navigation) and writes to JSONL

## What's New in v1.1.0

- ✅ **Heartbeat keepalive** — bidirectional ping/pong every 15s; detects stale connections in 45s
- ✅ **Exponential backoff reconnect** — 1s → 3s → 9s → 27s → 60s (max); resets on successful connect
- ✅ **Tab-switch auto-reattach** — debugger follows you when you switch tabs
- ✅ **Stale request sweep** — pending CDP commands older than 120s are auto-cancelled
- ✅ **Graceful shutdown** — SIGTERM/SIGINT cleanly close WS, cancel pending, stop background tasks
- ✅ **Safe send** — all `ws.send()` calls wrapped in try/catch; silent failures never crash the service worker

## Quick Start

```bash
# Start the CDP relay server
python3 server.py

# Start the capture server (optional, for event logging)
python3 capture_server.py

# Load the Chrome extension
# chrome://extensions/ → Load unpacked → extension/
```

The extension auto-connects to `ws://127.0.0.1:9223/ws` and auto-attaches to the active tab.

## Usage

### CLI (cdp.py)

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

# Use a different server
python3 cdp.py --server http://127.0.0.1:9223 status
```

### HTTP API (Relay Server)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdp` | POST | Send a CDP command `{"method": "Page.navigate", "params": {"url": "..."}}` |
| `/cdp` | GET | Quick command via query params `?method=Page.navigate&url=...` |
| `/status` | GET | Connection health, attached tab ID, uptime, pending requests |
| `/events` | GET | Server-Sent Events stream of CDP events |
| `/ws` | WS | WebSocket endpoint for the Chrome extension |

## Extension Features

### Badge Status
- **CDP** (green) — connected + debugger attached to a tab
- **WS** (blue) — connected but no tab attached
- **...** (yellow) — connecting
- empty (gray) — disconnected

### Auto Features
- **Auto-connect** — extension connects to the server on load
- **Auto-attach** — debugger attaches to the active tab on startup
- **Auto-reconnect** — exponential backoff (1s–60s) on disconnect
- **Auto-reattach** — follows you when you switch browser tabs
- **Heartbeat** — bidirectional ping/pong keeps the connection alive

### Configuration
The extension popup has a URL input field. The URL persists in `chrome.storage.sync`:

```
ws://127.0.0.1:9223/ws   ← for cross-LAN connections
ws://127.0.0.1:9223/ws       ← for same-machine connections
```

## Capture Server

The capture server logs all browser traffic (web requests, downloads, CDP events) to timestamped JSONL files:

```bash
python3 capture_server.py
```

Output goes to `/tmp/hermes-captures/capture.jsonl`. Files rotate (keep last 5).

### Reading Captures

```bash
# Count events
wc -l /tmp/hermes-captures/capture.jsonl

# Find download events
grep '"download"' /tmp/hermes-captures/capture.jsonl

# Find response bodies
grep '"response_body"' /tmp/hermes-captures/capture.jsonl

# Stream live
tail -f /tmp/hermes-captures/capture.jsonl | jq .
```

## Network Topology

For cross-LAN setups, specify the server's LAN IP in the extension popup:

```
ws://<server-lan-ip>:9223/ws
```

**Never** expose ports 9223 or 9988 to the public internet. There is no built-in auth.

## CDP Command Reference

See [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) for the full API.

### Common Commands
| Method | Params | Use |
|--------|--------|-----|
| `Page.navigate` | `{url}` | Navigate to URL |
| `Runtime.evaluate` | `{expression, returnByValue}` | Execute JS |
| `Page.captureScreenshot` | `{format, fromSurface}` | Capture viewport |
| `Input.dispatchMouseEvent` | `{type, x, y, button}` | Click coordinates |
| `Input.insertText` | `{text}` | Type text |
| `DOM.getDocument` | `{}` | Get full DOM |
| `Network.getCookies` | `{urls}` | Get cookies |

### Fast DOM Queries (Prefer Over Screenshots)
```python
# Get page text (200ms vs 5s for screenshot+vision)
cdp eval "document.body.innerText"

# Find interactive elements
cdp eval 'Array.from(document.querySelectorAll("a, button, input")).map(e => ({tag: e.tagName, text: (e.textContent||"").trim()}))'
```

## License

MIT
