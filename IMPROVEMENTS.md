# Browser Relay — Improvement Backlog

---

## Status: ✅ All identified items completed

Every gap from the original P1–P11 backlog, the v1.5.0 additions (P12–P18), and the post-audit items (shadow DOM, create/close tab, CI workflow, dialog dismissal) have been implemented across all 4 layers (Extension, Relay, MCP, Plugin).

---

## Completed

| # | Item | What shipped |
|---|------|-------------|
| P1 | CI workflow | `.github/workflows/test.yml` — 39 tests on push/PR |
| P2 | `browser_snapshot` shadow DOM | Recursive shadow root traversal in snapshot, annotated screenshot, find, wait_selector |
| P3 | `browser_wait_selector` shadow DOM | Shadow-aware waiting for elements inside web components |
| P4 | `browser_create_tab` + `browser_close_tab` | Full tab lifecycle across all 4 layers |
| P5 | `browser_dismiss_dialog` | `Page.handleJavaScriptDialog` via accept/cancel/prompt_text |

## Final Stats

| Metric | Value |
|--------|-------|
| MCP + Plugin tools | **44** each |
| Relay endpoints | 18 |
| Extension actions | 10 |
| Tests | **39** (all passing) |
| Version | **1.5.1** |
| Commits | 40+ |
