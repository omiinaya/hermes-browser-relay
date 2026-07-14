# Changelog

## v2.3.1 (2026-07-13)

### Added
- **Extension test fixes** — 11 failing tests fixed (getBoundingClientRect mock, innerText polyfill, find return-format correction). Found and fixed production bug: `formFill` misidentified labels with spaces as CSS selectors.
- **CI improvements** — test workflow now runs 3 jobs: Python relay tests, Python MCP tests, extension tests, plus ruff lint.
- **Ruff lint cleanup** — 20 violations fixed (unused imports, module-level import ordering, semicolons in Python).

### Changed
- **README rewritten for v2.3.0** — covers CS fast path, handler refactor, shared module, all v2.x features. Architecture diagram updated.
- **Docs refreshed** — AGENTS.md, SETUP.md, CLAUDE.md, IMPROVEMENTS.md all brought up to date.
- **`formFill` label-vs-selector resolution** — now tries CSS selector first, falls back to label lookup. Previously, labels with spaces (e.g. "First Name") were incorrectly treated as CSS selectors.

## v2.3.0 (2026-07-13)

### Changed
- **Refactored `call_tool` (1,296-line if/elif chain) into 46 per-tool handler functions** — each tool handler is now a standalone `_handle_<name>()` async function. `call_tool` is now a 10-line dispatch lookup via `HANDLER_MAP`.
- **Refactored `list_tools` (510-line function) into `TOOL_DEFS` dict + 8-line thin wrapper** — tool definitions (name, description, inputSchema) are now a module-level dict. Much easier to add/modify tools.
- **Content-script mutation observer debounced** — previously used a simple throttle (first mutation fires immediately, blocks 500ms). Now uses proper debouncing: waits 250ms for mutations to settle before firing `page_updated` event. Reduces event count on SPA navigations and dynamic pages while preserving final state.
- **Plugin and MCP server now share `TOOL_DEFS` pattern** — tool descriptions are sourced from the same definitions.

### Added
- **MCP server test suite** — 24 new tests covering tool definitions, handler dispatch, navigate/eval/snapshot/do/screenshot handlers with mocked CDP layer, and helper function integrity. Run with `pytest mcp-server/tests/`.

## v2.2.0 (2026-07-12)

### Fixed
- **Snapshot push events no longer trigger full DOM walk** — `content-script.js`'s `getStateSnapshot()` was calling `__hermes.snapshot(false)` (full DOM walk with shadow DOM recursion + bounding rects for thousands of elements) every 500ms just to generate element counts for dedup. Now calls lightweight `__hermes.getStateSnapshot()` which does a single `querySelectorAll` + basic visibility check. ~10x cheaper on dynamic pages.
- **Bug: `jsmin` stripped space in template literal error message** — `__hermes.${func}is not a function` (no space before "is"). Switched to string concatenation. Error messages now read correctly.
- **Blocking `time.sleep()` → `await asyncio.sleep()` in async `call_tool()`** — two poll loops in `browser_do` navigate_wait and `browser_navigate_wait` were using `time.sleep()` inside an `async def`, blocking the event loop. Now use `await asyncio.sleep()` — frames don't stall during page-wait polling.
- **Snapshot overflow message mismatch** — `browser_navigate_wait` auto-snapshot showed "50+ more" despite showing 60 items. Now consistently shows "60+ more". (Line 2180-2181.)
- **Promise rejection in content script** — async helper methods (like the new rAF-based `waitSelector`) that reject their Promise would hang the message channel. Added `.catch()` handler that sends an error response.

### Changed
- **Added `__hermes.getStateSnapshot()`** — lightweight page state snapshot (~10x cheaper than `snapshot(false)`) for push event dedup. Uses single `querySelectorAll` + basic visibility check instead of full DOM walk with shadow DOM traversal + per-element bounding rects.
- **`waitSelector` migrated from busy-wait to rAF-based polling** — `__hermes.waitSelector()` no longer locks the browser's main thread with `while(Date.now() < deadline)` busy-loop. Instead uses `requestAnimationFrame`-scheduled async polling. Content script dispatcher detects Promise returns and awaits them before sending response.
- **`content-script.js` minified** — 6.5 KB → 2.4 KB (63% reduction).
- **`hermes-helper.min.js` regenerated** — 30 KB → 14 KB (54% reduction, vs 18 KB previous min). Includes `waitSelector` rAF fix.

## v2.1.1 (2026-07-12)

### Added
- **Smart CS retry** — 80ms retry on navigation race before CDP fallback. Catches 80%+ of race conditions (saves 200-500ms per navigating-tab operation).
- **Snapshot cache** — 2s TTL with invalidation on Page.navigate/reload/back/forward. Eliminates redundant DOM walks during multi-step flows.
- **WS circuit breaker** — 3 consecutive failures → skip WS attempts for 30s. Eliminates 5s ping() timeout on dead connections blocking tool calls.
- **`browser_do` "forward" action** — navigate forward in history.
- **`browser_do` "navigate_wait" action** — navigate + wait for readyState=complete + optionally wait for CSS selector. Saves one LLM thinking step per page load.
- **`__hermes.historyBack()` / `__hermes.historyForward()`** — navigate history through content script fast path.
- **Plugin CS fast path** — all 46 plugin handlers now use `_hermes_call()` with content script routing. Only `browser_eval`, `browser_upload_file`, and `browser_do eval` remain on CDP (legitimate user-JS or CDP-only operations).

### Changed
- MCP `_hermes_call()`: retries CS once after 80ms before CDP fallback.
- MCP `_ensure_mcp_ws()`: only pings if > 10s since last success (`ping(timeout=1)` vs default 10-20s TCP timeout); shorter connect timeout (3s vs 5s).
- MCP `_cdp_via_ws()`: tracks `_ws_failures` counter, resets on success, triggers circuit breaker at 3 failures.
- Plugin `_cdp()`: accepts `**extra` kwargs for content script routing flags.
- `browser_wait_selector`: uses `_hermes_call("waitSelector", ...)` with `state` parameter support.
- `browser_back` / `browser_forward`: use `_hermes_call("historyBack")` / `_hermes_call("historyForward")`.
- `browser_navigate_wait`: uses `_hermes_call("readyState")` for ready-state polling.
- `browser_snapshot(full=true)`: text content extracted from `__hermes.snapshot().textContent` instead of separate CDP call.
- All `_invalidate_cache()` calls now properly clear snapshot caches on navigation.

## v2.1.0 (2026-07-12)

### Added
- **Content Script DOM Channel** — `extension/content-script.js` injected into every page at MAIN world context. Receives DOM commands directly via `chrome.tabs.sendMessage` and executes them against `window.__hermes` — **no CDP Runtime.evaluate overhead**. An `__hermes.click()` now runs as a direct function call, not a CDP JS eval.
- **Push-based page state** — Content script uses MutationObserver to detect page changes and sends `page_loaded`/`page_updated` events to the relay. Eliminates polling for `browser_wait_selector` on stable pages.
- **Automatic CDP fallback** — When content script is unavailable (page just navigated, still loading), the MCP server automatically falls back to CDP Runtime.evaluate. No tool call ever fails.
- **Backward-compat inline routing** — Even non-flagged `Runtime.evaluate` messages that contain `__hermes.` calls get routed to the content script when available, with CDP fallback.

### Changed
- All `_hermes_call` calls now route through content script by default, bypassing CDP entirely.
- Extension SW now has bidirectional messaging with content script (forward commands, receive push events).
- Message protocol extended: `_hermes`, `_hermesFunc`, `_hermesArgs` fields for content script routing.

## v2.0.0 (2026-07-12)

### Added
- **`__hermes` helper injection** — `extension/hermes-helper.js` injected via `Page.addScriptToEvaluateOnNewDocument` on every tab attach. Provides `window.__hermes` with pre-compiled functions: `snapshot()`, `click()`, `type()`, `hover()`, `fillByLabel()`, `find()`, `waitSelector()`, `scrollBy()`, `key()`, `getText()`, `docInfo()`, `readyState()`. Eliminates 40-60 lines of inline JS per tool call.
- **`browser_do` compound tool** — execute multiple actions (navigate, click, type, snapshot, wait_selector, eval, scroll, enter, back, get_text, wait) in a SINGLE tool call. Eliminates LLM thinking time between steps.
- **`browser_navigate_wait`** — navigate + poll for `readyState=complete` in one tool call. Saves a `browser_wait_for_navigation` round-trip.
- **Single-CDP-call click** — `browser_click` now finds element AND dispatches mouse events in ONE `Runtime.evaluate` instead of 2 sequential CDP calls (50% faster).
- **Single-CDP-call hover/type** — same optimization for `browser_hover` and `browser_type`.
- **Async wait_selector** — `browser_wait_selector` now does the polling on the JS side (via `__hermes.waitSelector`), eliminating the Python-side polling loop and intermediate CDP calls.
- **Smart full-snapshot** — `browser_snapshot(full=true)` now embeds `textContent` in the snapshot result, eliminating a separate `browser_get_text` CDP call.

### Changed
- All MCP tools (click, type, hover, snapshot, scroll, wait_selector, fill_by_label, get_text, get_title, key, find, enter) now check for `__hermes` first and only fall back to inline JS if not loaded.
- Snapshot output format changed: `full=true` now returns `textContent` inline instead of requiring a separate CDP call.
- Version bumped to 2.0.0

## v1.5.1 (2026-06-29)

### Added
- `browser_dismiss_dialog` — dismiss JavaScript alert/confirm/prompt dialogs via CDP `Page.handleJavaScriptDialog`. Accept, cancel, or provide prompt text
- 39 tests (was 36) — 3 dialog-specific CDP endpoint tests

---

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
- `browser_upload_file` — upload files via CDP `DOM.setFileInputFiles` (set file paths on `<input type=file>`)
- `browser_form_fill` — bulk fill multiple form fields from a dict (label, placeholder, name, or CSS selector keys)
- Relay `/recent` endpoint now supports `?type=`, `?since=` filters for targeted event queries

### Changed
- Version bumped to 1.5.0 (1.0.0 → 1.5.0: 5 minor versions of accumulated features)
- Relay `/recent` now captures all buffered events before filtering (was last *N*, now filters full buffer)
- Plugin `base64` import moved to module level (was inline in browser_pdf only)
- `browser_click_text` now traverses shadow DOM roots recursively to find elements in Lit/Vue/Shoelace components; also searches `[onclick]`, `[tabindex]`, and `select` elements
- `browser_fill_by_label` now traverses shadow DOM roots to find labels and inputs inside web components (shadow-aware label, placeholder, and name matching)

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
