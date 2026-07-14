# Browser Relay — Improvement Backlog

> **Status:** ✅ All identified items completed (v2.4.0)
> 
> This file records completed improvements. New ideas should go in GitHub Issues.

---

## Completed

| Phase | Item | What shipped |
|-------|------|-------------|
| P1 | CI workflow | `.github/workflows/test.yml` — lint + python (3.10-3.12 matrix) + extension + E2E jobs |
| P2 | `browser_snapshot` shadow DOM | Recursive shadow root traversal in snapshot, annotated screenshot, find, wait_selector |
| P3 | `browser_wait_selector` shadow DOM | Shadow-aware waiting for elements inside web components |
| P4 | `browser_create_tab` + `browser_close_tab` | Full tab lifecycle across all 4 layers |
| P5 | `browser_dismiss_dialog` | `Page.handleJavaScriptDialog` via accept/cancel/prompt_text |
| P6 | Content Script DOM Channel | `window.__hermes` — zero-CDP DOM operations, push-based page state |
| P7 | Snapshot cache | 2s TTL invalidated on navigation — eliminates redundant DOM walks |
| P8 | WS circuit breaker | 3 consecutive failures → 30s skip — prevents dead connections blocking calls |
| P9 | Handler dispatch pattern | 1,296-line `call_tool` → `_handle_*` functions + `HANDLER_MAP` |
| P10 | Tool definitions as data | 510-line `list_tools` → `TOOL_DEFS` dict + 8-line wrapper |
| P11 | Shared module | `browser_relay/shared.py` — deduplicated utilities |
| P12 | Proactive CS health check | 150ms Promise.race timeout in background.js routing |
| P13 | Extension test suite | 28 tests for hermes-helper.js using JSDOM + chrome mocks |
| P14 | MCP server test suite | 24 tests with mocked CDP layer |
| P15 | Debounced mutation observer | 250ms settle window replaces 500ms throttle |
| P16 | Full test coverage | 111 tests (63 Python + 48 JS), all passing |
| P17 | CS fast-path parity | 46 MCP tools ↔ 46 plugin tools, exactly matching |
| P18 | CI lint | Ruff check + ESLint against all Python and JS source |
| P19 | Makefile targets fixed | `lint` (truff→ruff), `test-ext` (48 tests), `test-mcp`, `make install` |
| P20 | Extension source restored | `content-script.src.js` — editable 6KB source |
| P21 | ESLint + pre-commit | ESLint flat config for all JS, pre-commit hooks (ruff) |
| P22 | Version sync | Single `VERSION` file, synced across manifest/plugin/package |
| P23 | Dependabot | Weekly pip/npm/actions updates |
| P24 | Dead code archived | `capture_server.py` → `archive/` |
| P25 | Plugin auto-start relay | `_ensure_relay()` — no manual `python3 server.py` needed |
| P26 | Plugin self-contained | Inlines all utilities, zero external imports |
| P27 | E2E integration tests | 4 tests against real Chromium via CDP |
| P28 | `browser_relay/` package | `shared.py` properly packaged |
| P29 | CI uses `make` targets | All jobs use Makefile for consistency |
| P30 | `pyproject.toml` complete | `[project]` section for pip-installability |
| P31 | SSE navigation wait | `GET /wait-nav` long-poll replaces 500ms `/recent` polling |
| P32 | Env var configuration | `CDP_SERVER`, `RELAY_TIMEOUT` env vars override hardcoded defaults |
| P33 | `.env.example` | Documented env vars for cross-LAN setup |
| P34 | Plugin dispatch refactored | `_dispatch` if/elif → `HANDLER_MAP` pattern |
| P35 | Fallback JS extracted | Duplicated JS fallback snippets → `browser_relay/shared.py` |

## Final Stats (v2.4.0)

| Metric | Value |
|--------|-------|
| MCP + Plugin tools | **46** each |
| Relay endpoints | 18 |
| Extension actions | 10 |
| Tests | **115** (67 Python + 48 JS, all passing) |
| Python lint | Clean (ruff, 0 violations) |
| JS lint | Clean (ESLint, 0 source warnings) |
| Pre-commit hooks | ruff check + ruff-format |
| Dependabot | pip (weekly), npm (weekly), actions (weekly) |
| Version | **2.4.0** |
| Commits | 85+ |
