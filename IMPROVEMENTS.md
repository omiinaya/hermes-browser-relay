# Browser Relay — Improvement Backlog

---

## Status: ALL COMPLETED 🎉

All 11 items from the initial backlog have been implemented. See Recently Completed below.

---

## Recently Completed

### P11 — Multi-tab attach support
Support attaching to multiple tabs simultaneously. Replaced single `attachedTabId` with `attachments Map`. Additive attach, targeted detach, tab-routed CDP commands, badge shows count.

### P10 — browser_block_patterns
Network request blocking via Chrome's declarativeNetRequest API (MV3 native). Uses `chrome.declarativeNetRequest.updateDynamicRules()` to add/remove URL-filter rules. Supports glob-style patterns: `*.css`, `*analytics*`, `/path/*`. Background.js stores blocked patterns and creates DNR rules; relay server has `POST/GET /block/patterns` endpoints; tools at both MCP and plugin layers.
28|
29|### P8 — browser_get_downloads
30|List completed downloads with local file paths. The extension already captures `downloads.onCreated`/`onChanged` events, but there was no tool to query the download state.
31|
32|### P7 — browser_clear_cookies
33|Clear all cookies for the current page via CDP `Network.clearBrowserCookies`.
34|
35|### P6 — browser_get_cookies
36|Read all cookies for the current page via CDP `Network.getCookies`. Useful for auth workflows.
37|
38|### P5 — browser_switch_tab
39|List open tabs and switch to one by ID or URL/title keyword. Required new extension message types (`tab-list`/`tab-switch`), relay server endpoints (`/tabs`, `/tabs/switch`), and tools at both MCP and plugin layers.
40|
41|### P4 — Annotated screenshot with Pillow overlay ✅
42|Draw `[1]`, `[2]` labels directly onto the PNG screenshot using Pillow, instead of returning them as text next to the image. Would make annotated screenshots instantly visually useful.
43|
44|### P3 — browser_console ✅
