# Architecture

browser-relay bridges AI agents with Chrome/Chromium through four layers.

```md
┌──────────────┐   POST /cdp   ┌──────────────┐  WebSocket  ┌──────────────┐   CDP   ┌──────────┐
│  Hermes      │ ─────────────> │  Relay       │ ───────────> │  Extension   │ ───────> │  Browser │
│  Agent       │ <───────────── │  Server      │ <─────────── │  (Service    │ <─────── │  Tabs    │
│  (plugin)    │   WS (/mcp-ws) │  :9223       │   WS resp   │   Worker)    │   CDP    │          │
└──────────────┘                └──────────────┘              └──────────────┘          └──────────┘
                                        │                            │
                                    in-memory buffer                 ├── Content Script (MAIN world)
                                    /tmp/hermes-captures/            │   window.__hermes — DOM fast path
                                    WS circuit breaker (3×30s)       │   MutationObserver — 250ms debounced
                                    batched capture writes (1s)      │   push events (getStateSnapshot)
                                    CDP event whitelist (12 methods) │   client-side batch (100ms)
```

## Components

| Component | What it does |
|-----------|-------------|
| **Hermes Plugin** (`plugins/browser-relay/`) | Registers 46 `browser_*` tools with Hermes. Auto-starts relay. Uses a persistent WebSocket to `/mcp-ws` (circuit breaker + HTTP POST fallback) since v2.5.0. |
| **Relay Server** (`server.py`, port `:9223`) | Bridges CDP commands between Hermes and the Chrome extension over WebSocket. Handles event capture (batched disk writes), tab lifecycle, URL blocking. |
| **Chrome Extension** (`extension/`) | Service worker that connects to relay, drives `chrome.debugger` API, injects `__hermes` helper into pages. |
| **Content Script** (`extension/content-script.js`) | Direct DOM execution in MAIN world — no CDP overhead for click/type/snapshot. 250ms debounced mutation observer for push events. |
| **MCP Server** (`mcp-server/server.py`) | Alternative interface: exposes the same 46 tools via stdio MCP protocol. Uses the same relay server under the hood. |
| **CLI** (`cdp.py`) | Python CLI for manual debugging, scripting, and status checks. |

## Data Flow

1. **Tool invocation** — The agent (Hermes or MCP client) calls a `browser_*` tool
2. **WebSocket** — The tool handler sends the CDP command to the relay over a persistent WebSocket (`/mcp-ws`), falling back to `POST /cdp` if the WS circuit is open
3. **WebSocket** — The relay forwards the command to the Chrome extension over a persistent WebSocket connection
4. **CDP** — The extension uses `chrome.debugger.sendCommand()` to execute the command in the target tab
5. **Response** — The result flows back: extension → WebSocket → relay → WS response → tool handler

### Content Script Fast Path

For DOM operations (click, type, snapshot, find, eval), the extension routes commands to the **content script** injected in MAIN world. This avoids CDP `Runtime.evaluate` overhead — direct function calls on `window.__hermes` are ~10x faster.

## Key Design Decisions

- **No authentication** — The relay is designed for local use. Bind to `127.0.0.1` for safety.
- **In-memory event buffer** — Recent network events and CDP responses are kept in memory, rotated at 10MB. No database dependency.
- **WS circuit breaker** — 3 consecutive WebSocket failures → skip WS for 30s. Prevents cascading failures.
- **Snapshot cache** — 2s TTL on DOM snapshots, invalidated on navigation. Eliminates redundant full DOM walks.
- **Multi-tab routing** — All tools accept an optional `tab_id` parameter. The relay maintains separate CDP connections per tab.
