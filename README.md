# browser-relay — Hermes Plugin for Remote Browser Control

A **Hermes plugin** that gives your AI agent full control of Chrome/Chromium — navigate, click, type, scrape, screenshot, fill forms, and manage tabs. All through a CDP relay server + companion Chrome extension.

```bash
hermes plugins install https://github.com/omiinaya/browser-relay
# Start the relay:
python3 server.py
# Load the Chrome extension:
# chrome://extensions/ → Load unpacked → extension/
# Now use: browser_navigate, browser_eval, browser_click, ...
```

**Version 2.4.0** — **115 tests** (63 Python + 48 JS + 4 E2E), 0 failures. ESLint + ruff clean.

---

## Quick Start (Hermes)

```bash
# 1. Install the plugin
git clone https://github.com/omiinaya/browser-relay.git
cd browser-relay
make install           # Python deps + npm

# 2. Start the relay (or let the plugin auto-start it)
python3 server.py &

# 3. Load the Chrome extension
# chrome://extensions/ → Enable Developer mode → Load unpacked → select extension/

# 4. Install the Hermes plugin
hermes plugins install .
# Then restart Hermes — 46 browser tools appear automatically
```

> **Tip:** The plugin auto-starts the relay server on first tool call if it's not already running. You just need the Chrome extension loaded.

## Architecture

```
┌──────────────┐   POST /cdp   ┌──────────────┐  WebSocket  ┌──────────────┐   CDP   ┌──────────┐
│  Hermes      │ ─────────────> │  Relay       │ ───────────> │  Extension   │ ───────> │  Browser │
│  Agent       │ <───────────── │  Server      │ <─────────── │  (Service    │ <─────── │  Tabs    │
│  (plugin)    │   HTTP resp    │  :9223       │   WS resp   │   Worker)    │   CDP    │          │
└──────────────┘                └──────────────┘              └──────────────┘          └──────────┘
                                        │                            │
                                    in-memory buffer                 ├── Content Script (MAIN world)
                                    /tmp/hermes-captures/            │   window.__hermes — DOM fast path
                                    WS circuit breaker (3×30s)       │   MutationObserver — push events
                                                                     │   250ms debounced page_updated
```

### Components

| Component | What it does |
|-----------|-------------|
| **Hermes Plugin** (`plugins/browser-relay/`) | Registers 46 `browser_*` tools with Hermes. Auto-starts relay. Communicates via HTTP to relay server. |
| **Relay Server** (`server.py`, port `:9223`) | Bridges CDP commands between Hermes and the Chrome extension over WebSocket |
| **Chrome Extension** (`extension/`) | Service worker that connects to relay, drives `chrome.debugger` API, injects `__hermes` helper into pages |
| **Content Script** (`extension/content-script.js`) | Direct DOM execution in MAIN world — no CDP overhead for click/type/snapshot |
| **MCP Server** (`mcp-server/server.py`) | Alternative interface: exposes the same 46 tools via stdio MCP protocol |

---

## Tools (46)

All tools accept an optional `tab_id` parameter for multi-tab routing.

| Category | Tools |
|----------|-------|
| **Navigation** | `browser_navigate`, `browser_navigate_wait`, `browser_back`, `browser_forward`, `browser_refresh` |
| **Interaction** | `browser_click`, `browser_click_text`, `browser_type`, `browser_hover`, `browser_key`, `browser_enter`, `browser_select_option` |
| **Inspection** | `browser_snapshot`, `browser_eval`, `browser_get_text`, `browser_get_title`, `browser_find`, `browser_get_cookies` |
| **Forms** | `browser_fill_by_label`, `browser_form_fill`, `browser_upload_file` |
| **Tabs** | `browser_list_tabs`, `browser_switch_tab`, `browser_create_tab`, `browser_close_tab`, `browser_attach_tab`, `browser_detach_tab`, `browser_list_attachments` |
| **Network** | `browser_network_requests`, `browser_response_body`, `browser_block_patterns`, `browser_set_cookie`, `browser_clear_cookies` |
| **Media** | `browser_screenshot`, `browser_screenshot_annotated`, `browser_scroll`, `browser_pdf` |
| **Compound** | `browser_do` — execute 18 action types in one call |
| **Utility** | `browser_status`, `browser_console`, `browser_send_cdp`, `browser_wait_selector`, `browser_wait_for_navigation`, `browser_dismiss_dialog`, `browser_get_downloads` |

---

## CLI (`cdp.py`)

For manual debugging or scripting outside of Hermes:

```bash
python3 cdp.py status
python3 cdp.py navigate https://example.com
python3 cdp.py eval "document.title"
python3 cdp.py click "#submit-btn"
python3 cdp.py type "#search" "hello world"
python3 cdp.py screenshot
python3 cdp.py send Network.getCookies '{}'
python3 cdp.py watch
```

---

## HTTP API (Relay Server)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdp` | POST | Send a CDP command `{"method": "Page.navigate", "params": {"url": "..."}}` |
| `/cdp` | GET | Quick command via query params `?method=Page.navigate&url=...` |
| `/cdp/{tabId}` | POST | Send CDP command to a specific tab |
| `/status` | GET | Connection health, attached tab, uptime |
| `/events` | GET | Server-Sent Events stream |
| `/ws` | WS | WebSocket for the Chrome extension |
| `/recent` | GET | Recent captured events |
| `/tabs` | GET/POST | Tab lifecycle management |
| `/block/patterns` | GET/POST | URL blocking (declarativeNetRequest) |

---

## Test Suite

| Suite | Command | Count | What it tests |
|-------|---------|-------|---------------|
| Relay server | `make test-python` | 39 | HTTP endpoints, WS, CDP dispatch, event capture |
| MCP server | `make test-mcp` | 24 | Tool definitions, handler dispatch, snapshots |
| Extension | `make test-ext` | 48 | background.js, content-script, hermes-helper |
| E2E | `make test-integration` | 4 | Real Chromium via CDP (requires chromium installed) |

**115 tests, 0 failures.** Ruff lint clean, ESLint clean (source files).

---

## License

MIT
