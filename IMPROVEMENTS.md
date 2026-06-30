# Browser Relay — Improvement Backlog

---

## Status: 20 implemented, 5 remaining 🔄

All 20 backlog items completed. 5 new gaps identified below.

---

## Recently Completed

### P20 — Test suite (33 passing) ✅
Full pytest test suite for relay server with 33 tests covering status, CDP endpoints, /recent with filters, tab endpoints, block patterns, event subscriber system, and capture buffer functionality. Uses aiohttp TestClient for real request/response cycles. Runner at `tests/run.sh`.

### P19 — MCP watchdog auto-reconnect ✅
Added `_wait_for_relay()` — blocks up to 5s polling `/status` every 300ms. `_cdp()` and `_query_capture()` retry once with wait on connection failure. Tools no longer immediately error when relay is restarting.

*(P12–P18 from v1.5.0 remain completed — see prior entries)*
*(P1–P11 from original backlog remain completed — see git history)*

---

## Pending

| Priority | Item | Impact | Notes |
|----------|------|--------|-------|
| P1 | **GitHub CI workflow** | Tests exist but never auto-run — PRs can silently break | `.github/workflows/test.yml` |
| P2 | **`browser_snapshot` shadow DOM** | Lit/Vue/Shoelace components invisible in accessibility tree — same gap we fixed in click_text/fill_by_label | Needs recursive shadow root traversal |
| P3 | **`browser_wait_selector` shadow DOM** | Can't wait for elements inside web components | Same fix as snapshot |
| P4 | **`browser_close_tab` + `browser_create_tab`** | Full tab lifecycle. Can navigate but can't close tabs or create blank ones | Extension needs `tabs.create` + `tabs.remove` message handlers |
| P5 | **`browser_dismiss_dialog`** | No way to handle `alert()`/`confirm()`/`prompt()` dialogs — they block CDP commands silently | CDP `Page.handleJavaScriptDialog` |
