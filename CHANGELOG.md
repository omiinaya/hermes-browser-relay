# Changelog

## v1.5.0 (2026-06-29)

### Added
- Popup tab selector with attach/detach per-tab buttons, auto-attach toggle, connection status
- `browser_find` — search page text for pattern, return matching elements with coords and ref IDs
- `browser_pdf` — save current page as PDF via CDP `Page.printToPDF` (landscape, scale support)
- `tab_id` auto-injected on all MCP and Plugin tools via programmatic schema injection
- `browser_wait_for_navigation` — poll event buffer for fresh navigation, returns URL/title of new page
- `browser_network_requests` — query captured webRequest events from relay buffer (`?type=webrequest` + event filter)
- `browser_response_body` — query captured response bodies (JSON/PDF/matched patterns) from relay buffer
- `browser_set_cookie` — inject cookies via CDP `Network.setCookie` (name, value, domain, path, secure, httpOnly, sameSite)
- Relay `/recent` endpoint now supports `?type=`, `?since=` filters for targeted event queries

### Changed
- Version bumped to 1.5.0 (1.0.0 → 1.5.0: 5 minor versions of accumulated features)
- Relay `/recent` now captures all buffered events before filtering (was last *N*, now filters full buffer)
- Plugin `base64` import moved to module level (was inline in browser_pdf only)

## v1.4.0 (2026-06-29)

### Added
- Multi-tab attach support — extension now attaches to multiple tabs simultaneously
- `browser_attach_tab` / `browser_detach_tab` / `browser_list_attachments` tools
- Tab-targeted CDP commands via `POST /cdp/{tabId}` and `tabId` field
- `browser_refresh` — reload page with optional hard reload
- `browser_forward` — navigate forward in history
- `browser_get_title` — quick page title getter
- `browser_list_tabs` / `browser_switch_tab` — list all open tabs and switch by ID or keyword
- `browser_get_cookies` / `browser_clear_cookies` — read and clear cookies via CDP
- `browser_get_downloads` — query download events from capture buffer
- `browser_block_patterns` — block URL patterns via Chrome's declarativeNetRequest API
- MCP Connection watchdog — background thread auto-detects relay reconnections
- Relay endpoints: `GET /tabs`, `POST /tabs/switch`, `POST /block/patterns`, `GET /block/patterns`
- Extension: `tab-list`/`tab-switch` message types for tab management
- Extension: `declarativeNetRequest`-based URL blocking with `set_blocked_patterns` command

### Changed
- `browser_status` now includes watchdog connection state
- Extension updated to MV3-compatible declarativeNetRequest for request blocking

## v1.3.0 (2026-06-28)

### Added
- 7 new MCP tools for agent efficiency:
  - `browser_snapshot` — accessibility-style element map with ref IDs and coordinates
  - `browser_scroll` — scroll page by direction or pixel amount
  - `browser_wait_selector` — poll until CSS selector appears (visible or attached)
  - `browser_click_text` — click by visible text content, no CSS selector needed
  - `browser_fill_by_label` — find form fields by label/placeholder/aria-label,
    uses `document.execCommand('insertText')` for React compatibility
  - `browser_get_text` — return rendered page text (10x faster than vision)
  - `browser_screenshot_annotated` — screenshot with numbered element overlay
  - `browser_key` — press arbitrary keyboard keys (Escape, Tab, ArrowDown, etc.)
  - `browser_back` — navigate back in browser history

### Changed
- `browser_type` now uses `document.execCommand('insertText')` for React/Next.js
  controlled input compatibility (was setting `el.value` directly)

### Plugin
- Full Hermes plugin with all 18 tools at `plugins/browser-relay/`

## v1.2.0 (2026-06-28)

### Added
- `/recent` endpoint on relay server (:9223) — returns recent captured events from in-memory buffer
- `/stats` endpoint alias on relay server (mirrors `/status`)
- `?raw=true` support on `/recent` for raw JSONL pipe-friendly output
- SIGTERM/SIGINT buffer dump — in-memory events written to JSONL file on graceful shutdown
- In-memory event buffer (1,000 full + 200 nav) in relay server for instant queries
- Event capture now built into relay server — writes to `/tmp/hermes-captures/capture.jsonl`

### Fixed
- `bodyTargets` Map memory leak — cleared on tab detach
- Console noise — operational logs changed from `console.log` to `console.debug` in extension

### Changed
- Architecture simplified: one WS connection from extension → relay, capture handled in-relay
- `_query_capture` MCP tool now queries relay server `:9223/recent`

### Deprecated
- Standalone `capture_server.py` (:9988) — capture merged into relay server

## v1.1.0

Initial tagged release. CDP relay with heartbeat, reconnect, tab-switch auto-reattach, stale request sweep, graceful shutdown.
