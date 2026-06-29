# CDP Bridge — Remote Browser Control

A Chrome DevTools Protocol relay server that bridges remote browser control between a Chrome extension and automation agents (Hermes, custom scripts) via WebSocket + HTTP.

**Version 1.4.0** — 33 tools across MCP server + Hermes plugin. Multi-tab attach support, URL blocking via declarativeNetRequest, MCP watchdog, tab manager, cookie management, downloads query, find, refresh/forward/get_title, and more.

## Architecture

```
┌──────────────┐   POST /cdp    ┌──────────────┐   WebSocket    ┌──────────────┐   CDP    ┌────────────────┐
│              │ ─────────────> │              │ ─────────────> │              │ ───────> │                │
│  Hermes      │                │  Relay       │                │  Extension   │          │  Chrome Tabs   │
│  Agent       │ <───────────── │  Server      │ <───────────── │  (Service    │ <─────── │  (Multi-Attach)│
│  / MCP       │   HTTP resp    │  :9223       │   WS resp      │   Worker)    │   CDP    │                │
│  / Plugin    │   + /recent    └──────┬───────┘                └──────────────┘          └────────────────┘
│              │   + /tabs      │
│              │   + /block     │ in-memory buffer + MCP watchdog (15s health check)
│              │   + /cdp/{id}  │ /tmp/hermes-captures/capture.jsonl
```

**CDP Relay** (`server.py`, port `:9223`) — sends CDP commands to Chrome tabs (single or multi-tab), responds via HTTP, and captures browser events.
- **`GET /recent?limit=20&mode=nav`** — recent captured events (navigations, downloads, requests)
- **`GET /tabs`** — list all open browser tabs
- **`POST /tabs/switch`** — switch active attachment to a tab
- **`POST /tabs/attach`** — add a tab attachment (multi-tab)
- **`POST /tabs/detach`** — remove a tab attachment
- **`GET /tabs/attachments`** — list currently attached tab IDs
- **`POST /block/patterns`** — set URL blocking patterns (declarativeNetRequest)
- **`GET /block/patterns`** — get current blocking patterns
- **`POST /cdp/{tabId}`** — send CDP command to a specific tab
- **`GET /events`** — SSE streaming for real-time event monitoring
- **`GET /status` / `GET /stats`** — server status

## What's New in v1.4.0

- ✅ **Multi-tab attach support** — attach debugger to multiple tabs simultaneously
- ✅ **Tab management** — `browser_list_tabs`, `browser_switch_tab`, `browser_attach_tab`, `browser_detach_tab`, `browser_list_attachments`
- ✅ **URL blocking** — `browser_block_patterns` via declarativeNetRequest (`*.css`, `*analytics*`, `/path/*`)
- ✅ **Cookie tools** — `browser_get_cookies`, `browser_clear_cookies` (name, value, domain, expiry, httpOnly, secure)
- ✅ **Downloads** — `browser_get_downloads` — query capture buffer for download events
- ✅ **Page tools** — `browser_refresh`, `browser_forward`, `browser_get_title`, `browser_find`
- ✅ **MCP watchdog** — background daemon thread, 15s health check, auto-reconnect detection
- ✅ **Tab-targeted CDP** — all tools accept optional `tab_id` parameter for multi-tab routing
- ✅ **`tab_id` on all tools** — every browser tool can target a specific tab attachment

## Quick Start

```bash
# Start the CDP relay server (handles CDP commands + event capture)
python3 server.py

# Load the Chrome extension
# chrome://extensions/ → Load unpacked → extension/

# Optional: legacy capture server (capture merged into relay)
# python3 capture_server.py
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
python3 cdp.py --server http://192.168.1.100:9223 status
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
- **Number** (green) — connected + N tabs attached (e.g., `3` = 3 tabs)
- **WS** (blue) — connected but no tab attached
- **...** (yellow) — connecting
- empty (gray) — disconnected

### Auto Features
- **Auto-connect** — extension connects to the server on load
- **Auto-attach** — debugger attaches to newly activated tabs (additive)
- **Multi-tab** — maintains debugger connections to multiple tabs simultaneously
- **Auto-reconnect** — exponential backoff (1s–60s) on disconnect
- **Auto-reattach** — reattaches to active tab if all tabs get detached
- **Heartbeat** — bidirectional ping/pong keeps the connection alive

### Configuration
The extension popup has a URL input field. The URL persists in `chrome.storage.sync`:

```
ws://192.168.1.200:9223/ws   ← for cross-LAN connections
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
