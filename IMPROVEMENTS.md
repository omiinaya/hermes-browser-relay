# Browser Relay — Improvement Backlog

---

## Status: 18 implemented, scanning for more 🔄

All 18 items completed (11 from initial backlog + 7 new). See Recently Completed below.

---

## Recently Completed

### P18 — browser_form_fill ✅
Bulk fill multiple form fields from a dict. Smart dispatch: CSS-selector-looking keys (e.g. `input[name=email]`) go directly to `document.querySelector()`, text keys use shadow-aware label/placeholder/name matching. 50ms delay between fills.

### P17 — browser_upload_file ✅
Upload files via CDP `DOM.setFileInputFiles`. Sets the file path on `<input type=file>` without requiring the browser file picker dialog. Validates the element is a file input first via JS, then uses CDP's DOM node ID to set files.

### P16 — Shadow DOM support ✅
`browser_click_text` and `browser_fill_by_label` now recursively traverse shadow DOM roots. Labels, inputs, buttons, and clickable elements inside Lit/Vue/Shoelace/web components are now findable. Added `[onclick]`, `[tabindex]`, `select` to click-target selectors.

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
Programmatic `tab_id` injection into all 41 tool schemas via post-processing loop in `list_tools()`. `browser_find` searches page text and returns matching element info.

### P12 — browser_pdf ✅
Save the current page as a PDF document using CDP `Page.printToPDF`. Supports landscape orientation and scale factor. Returns base64-encoded PDF data. Added tool to MCP server and plugin.

*(P1–P11 from original backlog remain completed — see git history for details)*

---

## Pending

| Priority | Item | Impact | Notes |
|----------|------|--------|-------|
| P1 | **Test suite** | Regression safety across 41 tools × 4 layers | pytest + aiohttp test client for relay server |
| P2 | **MCP watchdog auto-reconnect** | Tools don't hang when relay restarts | Currently just logs — should retry `_cdp()` when relay is down |
| P3 | **Test CI runner** | Automated regression on push | `.github/workflows/test.yml` or shell script |
