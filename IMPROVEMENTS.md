# Browser Relay — Improvement Backlog

---

## Status: 15 implemented, scanning for more 🔄

All 15 items completed (11 from initial backlog + 4 new). See Recently Completed below.

---

## Recently Completed

### P15 — browser_set_cookie, browser_network_requests, browser_response_body, browser_wait_for_navigation ✅
**v1.5.0** — 4 new tools that surface previously captured data and enable new workflows:
- `browser_wait_for_navigation` — polls relay event buffer until a fresh tab.navigated event appears
- `browser_network_requests` — queries captured webRequest events with `?type=webrequest` and event filter
- `browser_response_body` — queries captured response bodies (JSON/PDF patterns)
- `browser_set_cookie` — injects cookies via CDP `Network.setCookie` (name, value, domain, path, secure, httpOnly, sameSite)
- Relay `/recent` enhanced with `?type=` and `?since=` query filters

### P14 — Popup tab selector + auto-attach toggle ✅
Rewrite of extension popup with per-tab attach/detach buttons, connection status indicators, auto-attach toggle, and server URL config. Background.js now persists `autoAttach` in chrome.storage, starts auto-attach after storage load.

### P13 — tab_id auto-injection + browser_find ✅
Programmatic `tab_id` injection into all 39 tool schemas via post-processing loop in `list_tools()`. `browser_find` searches page text and returns matching element info.

### P12 — browser_pdf ✅
Save the current page as a PDF document using CDP `Page.printToPDF`. Supports landscape orientation and scale factor. Returns base64-encoded PDF data. Added tool to MCP server and plugin.

### P11 — Multi-tab attach support
Support attaching to multiple tabs simultaneously. Replaced single `attachedTabId` with `attachments Map`. Additive attach, targeted detach, tab-routed CDP commands, badge shows count.

### P10 — browser_block_patterns
Network request blocking via Chrome's declarativeNetRequest API (MV3 native). Uses `chrome.declarativeNetRequest.updateDynamicRules()` to add/remove URL-filter rules. Supports glob-style patterns: `*.css`, `*analytics*`, `/path/*`. Background.js stores blocked patterns and creates DNR rules; relay server has `POST/GET /block/patterns` endpoints; tools at both MCP and plugin layers.

### P8 — browser_get_downloads
List completed downloads with local file paths. The extension already captures `downloads.onCreated`/`onChanged` events, but there was no tool to query the download state.

### P7 — browser_clear_cookies
Clear all cookies for the current page via CDP `Network.clearBrowserCookies`.

### P6 — browser_get_cookies
Read all cookies for the current page via CDP `Network.getCookies`. Useful for auth workflows.

### P5 — browser_switch_tab
List open tabs and switch to one by ID or URL/title keyword. Required new extension message types (`tab-list`/`tab-switch`), relay server endpoints (`/tabs`, `/tabs/switch`), and tools at both MCP and plugin layers.

### P4 — Annotated screenshot with Pillow overlay ✅
Draw `[1]`, `[2]` labels directly onto the PNG screenshot using Pillow, instead of returning them as text next to the image. Would make annotated screenshots instantly visually useful.

### P3 — browser_console ✅

---

## Pending

*(No items — all 11 from initial backlog completed. Review mode: scanning for new gaps.)*
