# Browser Relay — Improvement Backlog

---

## Status: ✅ All identified items completed

Every gap from the original P1–P11 backlog, the v1.5.0 additions (P12–P18), and the post-v2.0 additions (handler refactor, shared module, CS health check, test suites, lint) have been implemented.

---

## Completed

| # | Item | What shipped |
|---|------|-------------|
| P1 | CI workflow | `.github/workflows/test.yml` — 3 job matrix (Python 3.10–3.12 relay tests, MCP tests, extension tests, lint) |
| P2 | `browser_snapshot` shadow DOM | Recursive shadow root traversal in snapshot, annotated screenshot, find, wait_selector |
| P3 | `browser_wait_selector` shadow DOM | Shadow-aware waiting for elements inside web components |
| P4 | `browser_create_tab` + `browser_close_tab` | Full tab lifecycle across all 4 layers |
| P5 | `browser_dismiss_dialog` | `Page.handleJavaScriptDialog` via accept/cancel/prompt_text |
| P6 | Content Script DOM Channel | `window.__hermes` — zero-CDP DOM operations, push-based page state |
| P7 | Snapshot cache | 2s TTL invalidated on navigation — eliminates redundant DOM walks |
| P8 | WS circuit breaker | 3 consecutive failures → 30s skip — prevents dead connections blocking calls |
| P9 | Handler dispatch pattern | 1,296-line `call_tool` → 46 `_handle_*` functions + `HANDLER_MAP` |
| P10 | Tool definitions as data | 510-line `list_tools` → `TOOL_DEFS` dict + 8-line wrapper |
| P11 | Shared module | `browser_relay/shared.py` — deduplicated `_js_quote`, `_parse_result`, snapshot cache |
| P12 | Proactive CS health check | 150ms Promise.race timeout in background.js routing |
| P13 | Extension test suite | 28 tests for hermes-helper.js using JSDOM + chrome mocks |
| P14 | MCP server test suite | 24 tests with mocked CDP layer |
| P15 | Debounced mutation observer | 250ms settle window replaces 500ms throttle |
| P16 | Full test coverage | 111 tests (63 Python + 48 JS), all passing |
| P17 | CS fast-path parity | 46 MCP tools ↔ 46 plugin tools, exactly matching |
| P18 | CI lint | Ruff check + ESLint against all Python and JS source |
| P19 | Makefile targets fixed | `lint` (truff→ruff), `test-ext` (48 tests + `--force-exit`), `test-mcp` (`.venv`), `make install` |
| P20 | Extension source restored | `content-script.src.js` — editable 6KB source alongside minified version |
| P21 | ESLint + pre-commit | ESLint flat config for all JS, pre-commit hooks (ruff) |
| P22 | Version sync | `VERSION`, `manifest.json`, `plugin.yaml`, `package.json` all at 2.3.1 |
| P23 | Dependabot | Weekly pip/npm/actions updates |
| P24 | Dead code archived | `capture_server.py` → `archive/` |

## Final Stats

| Metric | Value |
|--------|-------|
| MCP + Plugin tools | **46** each |
| Relay endpoints | 18 |
| Extension actions | 10 |
| Tests | **111** (63 Python + 48 JS, all passing) |
| Python lint | Clean (ruff, 0 violations) |
| JS lint | Clean (ESLint, 0 source warnings, 21 test-file warnings accepted) |
| Pre-commit hooks | ruff check + ruff-format |
| Dependabot | pip (weekly), npm (weekly), actions (weekly) |
| Version | **2.4.0** |
| Commits | 75+ |
