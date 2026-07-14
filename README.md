# CDP Bridge — Remote Browser Control

A Chrome DevTools Protocol relay that bridges remote browser control between a Chrome extension and automation agents (Hermes, custom scripts) via WebSocket + HTTP.

**Version 2.3.0** — 46 tools across MCP server + Hermes plugin, content script fast path, snapshot caching, multi-tab attach, WS circuit breaker, smart CS retry, mutation observer debouncing, and 91 passing tests.

## Architecture

```
┌──────────────┐   POST /cdp    ┌──────────────┐   WebSocket    ┌──────────────┐   CDP    ┌────────────────┐
│              │ ─────────────> │              │ ─────────────> │              │ ───────> │                │
│  Hermes      │                │  Relay       │                │  Extension   │          │  Chrome Tabs   │
│  Agent       │ <───────────── │  Server      │ <───────────── │  (Service    │ <─────── │  (Multi-Attach)│
│  / MCP       │   HTTP resp    │  :9223       │   WS resp      │   Worker)    │   CDP    │                │
│  / Plugin    │   + /recent    └──────┬───────┘                └──────┼───────┘          └────────────────┘
│              │   + /tabs             │                              │
│              │   + /block            │                              ├── Content Script (MAIN world)
│              │   + /cdp/{id}         │                              │   window.__hermes — DOM fast path
│              │                      │                              │   MutationObserver — push events
│              │                      │  in-memory buffer             │   250ms debounced page_updated
│              │                      │  /tmp/hermes-captures/        │
│              │                      │  WS circuit breaker (3×30s)   └── CDP fallback (nav race)
```

**CDP Relay** (`server.py`, port `:9223`) — sends CDP commands to Chrome tabs (single or multi-tab), responds via HTTP, and captures browser events.

**Content Script** (`content-script.js`, injected `@document_start` in MAIN world) — receives DOM commands directly via `chrome.tabs.sendMessage` and executes against `window.__hermes`. **No CDP Runtime.evaluate overhead.** Most DOM operations run as direct function calls.

**Content Script Fast Path** — 13 of 13 DOM interaction actions (click, type, snapshot, hover, find, etc.) route through CS by default. Automatic CDP fallback on navigation race with 80ms smart retry.

**Snapshot Cache** — 2s TTL with invalidation on Page.navigate/reload/back/forward. Eliminates redundant DOM walks during multi-step flows.

**WS Circuit Breaker** — 3 consecutive WS failures → skip WS attempts for 30s. Prevents 5s ping() timeouts on dead connections blocking tool calls.

## What's New in v2.3.0

- ✅ **Handler dispatch pattern** — `call_tool` refactored from 1,296-line if/elif chain into 46 per-tool handler functions with `HANDLER_MAP` dispatch
- ✅ **Tool definitions as data** — `list_tools` collapsed from 510-line function into `TOOL_DEFS` dict + 8-line wrapper
- ✅ **Shared module** (`browser_relay/shared.py`) — deduplicated `_js_quote`, `_parse_result`, snapshot cache, and interactive element selectors between MCP server and plugin
- ✅ **Proactive CS health check** — 150ms Promise.race timeout in background.js routing; saves 80ms per call when CS is mid-navigation
- ✅ **Extension test suite** — 28 tests for hermes-helper.js (JSDOM + chrome mock framework)
- ✅ **MCP server test suite** — 24 tests with mocked CDP layer
- ✅ **10 correctness fixes** — blocking `time.sleep()` → `await asyncio.sleep()`, snapshot overflow message mismatch, `jsmin` template literal bug, content-script Promise rejection, and more

### Highlights from v2.x

| v2.2.0 | Debounced mutation observer (250ms settle), lightweight `getStateSnapshot()`, rAF-based waitSelector, minified CS |
|--------|------|
| v2.1.1 | WS circuit breaker, snapshot cache, smart CS retry, CS fast path for all plugin handlers |
| v2.1.0 | Content Script DOM Channel — CS fast path, push-based page state, auto CDP fallback |
| v2.0.0 | `__hermes` helper injection, `browser_do` compound tool, single-CDP-click, async wait_selector |

## Quick Start

```bash
# Install dependencies
pip install aiohttp websockets Pillow 'mcp>=1.0.0'
npm install

# Start the CDP relay server
python3 server.py

# In a separate terminal, start the MCP server
python3 -m mcp-server.server

# Load the Chrome extension
# chrome://extensions/ → Load unpacked → extension/

# Or use the Hermes plugin
# hermes plugins enable browser-relay
```

The extension auto-connects to `ws://127.0.0.1:9223/ws` and auto-attaches to the active tab.

## Test Suite

```bash
# Python tests (relay server)
python3 -m pytest tests/ -v

# Python tests (MCP server)
python3 -m pytest mcp-server/tests/ -v

# Extension tests (hermes-helper.js)
node --test tests/extensions/hermes-helper.test.js

# All at once (from .venv)
.venv/bin/python -m pytest tests/ mcp-server/tests/ -v
node --test tests/extensions/hermes-helper.test.js
```

**Current: 91 tests** (39 relay + 24 MCP + 28 extension), all passing.

## CLI (cdp.py)

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

## HTTP API (Relay Server)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdp` | POST | Send a CDP command `{"method": "Page.navigate", "params": {"url": "..."}}` |
| `/cdp` | GET | Quick command via query params `?method=Page.navigate&url=...` |
| `/cdp/{tabId}` | POST | Send CDP command to a specific tab |
| `/status` | GET | Connection health, attached tab ID, uptime, pending requests |
| `/stats` | GET | Alias for `/status` |
| `/events` | GET | Server-Sent Events stream of CDP events |
| `/ws` | WS | WebSocket endpoint for the Chrome extension |
| `/recent` | GET | Recent captured events (supports `?type=`, `?since=`, `?limit=`, `?mode=`) |
| `/tabs` | GET | List all open browser tabs |
| `/tabs/switch` | POST | Switch active attachment to a tab |
| `/tabs/attach` | POST | Add a tab attachment (multi-tab) |
| `/tabs/detach` | POST | Remove a tab attachment |
| `/tabs/create` | POST | Create a new browser tab |
| `/tabs/close` | POST | Close a browser tab |
| `/tabs/attachments` | GET | List currently attached tab IDs |
| `/block/patterns` | GET/POST | Get/set URL blocking patterns (declarativeNetRequest) |

## MCP Tools (44 + 2 = 46 total)

The MCP server exposes 46 browser control tools. Every tool has a `tab_id` parameter for multi-tab targeting. Key tool categories:

- **Navigation**: `browser_navigate`, `browser_navigate_wait`, `browser_back`, `browser_forward`, `browser_refresh`
- **Interaction**: `browser_click`, `browser_click_text`, `browser_type`, `browser_hover`, `browser_key`, `browser_enter`, `browser_select_option`
- **Inspection**: `browser_snapshot`, `browser_eval`, `browser_get_text`, `browser_get_title`, `browser_find`, `browser_get_cookies`
- **Form**: `browser_fill_by_label`, `browser_form_fill`, `browser_upload_file`
- **Tab management**: `browser_list_tabs`, `browser_switch_tab`, `browser_create_tab`, `browser_close_tab`, `browser_attach_tab`, `browser_detach_tab`, `browser_list_attachments`
- **Network**: `browser_network_requests`, `browser_response_body`, `browser_block_patterns`, `browser_set_cookie`, `browser_clear_cookies`
- **Media**: `browser_screenshot`, `browser_screenshot_annotated`, `browser_scroll`, `browser_pdf`
- **Compound**: `browser_do` (execute 18 action types in one call)
- **Utility**: `browser_status`, `browser_console`, `browser_send_cdp`, `browser_wait_selector`, `browser_wait_for_navigation`, `browser_dismiss_dialog`, `browser_get_downloads`

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
ws://<server-lan-ip>:9223/ws   ← for cross-LAN connections
ws://127.0.0.1:9223/ws         ← for same-machine connections
```

## Network Topology

For cross-LAN setups, specify the server's LAN IP in the extension popup. **Never** expose port 9223 to the public internet. There is no built-in auth.

## License

MIT
