# Browser Relay — Improvement Backlog

---

## Status: PENDING

### P4 — Annotated screenshot with Pillow overlay ✅
Draw `[1]`, `[2]` labels directly onto the PNG screenshot using Pillow, instead of returning them as text next to the image. Would make annotated screenshots instantly visually useful.
Files: mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Medium
Est: 20 min
*Implemented: Added `io`, `base64` imports and PIL-based overlay rendering to `browser_screenshot_annotated` in both mcp-server/server.py and plugins/browser-relay/__init__.py. Decodes the PNG, draws yellow-background `[ref]` labels with element highlight rectangles, composites onto the original, re-encodes, and returns as ImageContent with a text element listing. Plugin also saves to MEDIA path for Hermes display.*

### P5 — browser_switch_tab
List open tabs and switch to one by index, URL pattern, or title. The extension follows tab-switch events but there's no programmatic way to say "switch to the tab with Stripe in the URL".
Files: server.py, mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Medium
Est: 25 min

### P6 — browser_get_cookies
Read all cookies for the current page via CDP `Network.getCookies`. Useful for auth workflows ("log in once, then reuse cookies").
Files: mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Easy
Est: 10 min

### P7 — browser_clear_cookies
Clear all cookies for the current page via CDP `Network.clearBrowserCookies`.
Files: mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Easy
Est: 5 min

### P8 — browser_get_downloads
List completed downloads with local file paths. The extension already captures `downloads.onCreated`/`onChanged` events, but there's no tool to query the download state.
Files: mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Medium
Est: 15 min

### P9 — Automatic MCP reconnect watchdog
When the relay restarts, MCP tools break until someone runs `hermes mcp test`. A watchdog that auto-refreshes the MCP connection would eliminate the manual step.
Files: server.py (heartbeat), mcp-server/server.py
Difficulty: Medium
Est: 20 min

### P10 — browser_block_patterns
Network request blocking via CDP `Network.setRequestInterception`. Block CSS, images, fonts to speed up scraping.
Files: mcp-server/server.py, plugins/browser-relay/__init__.py
Difficulty: Hard
Est: 30 min

### P11 — Multi-tab attach support
Support attaching to tab N while listing all open tabs. Currently one tab at a time.
Files: server.py, mcp-server/server.py, plugins/browser-relay/__init__.py, extension/background.js
Difficulty: Hard
Est: 45 min

---

## Recently Completed

### P3 — browser_console
Read browser console output (console.log/warn/error messages) via CDP `Runtime.consoleAPICalled`. These events are already flowing through the capture server but there's no way to query them.
*Implemented: Added browser_console tool to mcp-server/server.py and plugins/browser-relay/__init__.py. Sends Runtime.enable to ensure console domain, queries /recent?mode=all for Runtime.consoleAPICalled events, parses argument types (string/number/boolean/object/undefined), and returns formatted output with level icons.*

### P2 — browser_select_option
Select an `<option>` in a `<select>` dropdown by visible text. Currently requires `browser_eval(\"...\")` to set each dropdown.
*Implemented: Added browser_select_option tool to mcp-server/server.py and plugins/browser-relay/__init__.py. Uses JS to find <select> by CSS selector, match option text case-insensitively, set .value, and dispatch change + input events.*

### P1 — browser_hover
Hover the mouse over an element by CSS selector.
*Implemented: Added browser_hover tool definition + handler to mcp-server/server.py and plugins/browser-relay/__init__.py. Uses Input.dispatchMouseEvent with type 'mouseMoved' at element center coordinates.*
