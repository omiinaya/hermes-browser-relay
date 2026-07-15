# HTTP API

The relay server exposes a RESTful HTTP API on port `9223` (configurable). All endpoints return JSON.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdp` | POST | Send a CDP command |
| `/cdp` | GET | Quick command via query params |
| `/cdp/{tabId}` | POST | Send CDP command to a specific tab |
| `/status` | GET | Connection health, attached tab, uptime |
| `/stats` | GET | Same as `/status` |
| `/events` | GET | Server-Sent Events stream |
| `/ws` | WS | WebSocket for the Chrome extension |
| `/mcp-ws` | WS | Persistent WebSocket for MCP clients |
| `/wait-nav` | GET | Long-poll waiting for page navigation |
| `/recent` | GET | Recent captured events |
| `/tabs` | GET | List all open browser tabs |
| `/tabs` | POST | Create a new tab |
| `/tabs/switch` | POST | Switch to a tab by ID |
| `/tabs/attach` | POST | Attach debugger to a tab |
| `/tabs/detach` | POST | Detach debugger from a tab |
| `/tabs/attachments` | GET | List currently attached tabs |
| `/tabs/create` | POST | Create a new blank tab |
| `/tabs/close` | POST | Close a tab by ID |
| `/block/patterns` | GET | Get current URL block patterns |
| `/block/patterns` | POST | Update URL block patterns |

## `/cdp` — Send CDP Command

**POST /cdp** with JSON body:

```json
{"method": "Page.navigate", "params": {"url": "https://example.com"}}
```

**GET /cdp** via query parameters:

```
GET /cdp?method=Page.navigate&url=https://example.com
```

**Response:**

```json
{
  "id": 42,
  "result": {"frameId": "...", "loaderId": "..."},
  "tabId": 3
}
```

## `/cdp/{tabId}` — Tab-Targeted Command

**POST /cdp/3** — same as `/cdp` but targets tab ID 3 directly.

## `/status` — Health Check

**GET /status**

```json
{
  "status": "connected",
  "tabId": 3,
  "uptime": 847.2,
  "capture_size": 4096,
  "attachments": [3, 5]
}
```

## `/events` — Server-Sent Events

**GET /events** — Streams CDP events and WebRequest events as SSE:

```
data: {"type": "CDPevent", "method": "Page.frameNavigated", "params": {...}}

data: {"type": "WebRequest", "event": "completed", "url": "https://..."}
```

## `/wait-nav` — Navigation Wait

**GET /wait-nav?since=1234567890.0&timeout=30**

Long-poll endpoint that blocks until a new navigation event occurs. Returns immediately if an event happened since the timestamp.

## `/recent` — Recent Events

**GET /recent?limit=20&mode=nav**

Returns recently captured events from the in-memory buffer. `mode=nav` filters to interesting events (navigations, downloads). `mode=all` returns everything.

## `/tabs` — Tab Management

**GET /tabs** — List all open browser tabs.

**POST /tabs** with `{"url": "https://..."}` — Create a new tab.

**POST /tabs/switch** with `{"tabId": 3}` — Switch to tab 3.

## `/tabs/attach` — Debugger Management

**POST /tabs/attach** with `{"tabId": 3}` — Attach the CDP debugger to tab 3.

**POST /tabs/detach** with `{"tabId": 3}` — Detach the debugger from tab 3.

**GET /tabs/attachments** — List tab IDs with active CDP debugger attachments.

## `/block/patterns` — URL Blocking

**GET /block/patterns** — Get the current list of blocked URL patterns.

**POST /block/patterns** with:

```json
{"patterns": ["*.css", "*analytics*", "https://tracker.example.com/*"]}
```

Pass an empty array to clear all blocks.

## Rate Limiting

POST `/cdp` and `/cdp/{tabId}` are rate-limited to **30 requests per second** per IP address using a token bucket algorithm.
