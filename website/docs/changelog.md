# Changelog

## v2.5.0 (2026-08-04)

- **Performance overhaul** — content script uses `getStateSnapshot()` (~10x cheaper) for push events instead of the full DOM walk; real 250ms trailing debounce on MutationObserver
- **Client-side event batching** — CDP + webRequest events flush as one `type:"batch"` WS message every 100ms
- **CDP event whitelist** — only 12 interesting methods forwarded; per-request noise dropped at the source
- **Batched capture writes** — relay flushes `capture.jsonl` every 1s / 200 events instead of per-event open+stat
- **Plugin persistent WS** — Hermes plugin now uses `/mcp-ws` with circuit breaker + HTTP fallback (was HTTP POST per call)
- **Header whitelist** — security-relevant headers only (was 30 arbitrary per request); dropped unused `requestBody` capture
- **Bug fix** — relay now forwards `tabId` so multi-tab targeting works
- **Bug fix** — `make lint-js` no longer scans build artifacts (232 errors eliminated)
- **141 tests** — 82 Python + 55 JS + 4 E2E, 0 failures

## v2.4.2 (2026-07-14)

- **8 polish passes** — 80+ improvements across code quality, testing, documentation, and tooling
- **123 tests** — 71 Python + 48 JS + 4 E2E, 0 failures
- Docker support — multi-stage Dockerfile, health checks, Makefile targets
- CI badges in README — test status, Python version, license, Ruff
- HTTP API docs — expanded from 9 to 20 documented endpoints
- Bug fix: `_ensure_relay()` now respects `CDP_SERVER`/`SERVER` env vars
- Bug fix: plugin `is_available()` now respects `CDP_SERVER` env var
- MCP server `--version` flag and startup banner
- Version sync across 7 files (Makefile `check-version` target)
- Test count sync across all docs
- `.gitignore` extended for coverage artifacts
- Pre-commit hook now checks VERSION drift

## v2.4.0 (2026-07-13)

- **Full Hermes plugin structure** — 46 browser tools, MCP parity, auto-start relay
- Content Script DOM Channel — `window.__hermes` fast path for DOM operations
- Snapshot cache — 2s TTL eliminates redundant DOM walks
- WS circuit breaker — 3 consecutive failures → 30s skip
- Handler dispatch refactored — 1,296-line `call_tool` → `HANDLER_MAP` + 46 handlers
- Tool definitions as data — `TOOL_DEFS` dict replaces 510-line `list_tools`
- Shared module — `browser_relay/shared.py` deduplicates utilities
- Extension test suite — 48 tests with JSDOM + chrome mocks
- MCP server test suite — 24 tests with mocked CDP layer
- E2E integration tests — 4 tests against real Chromium
- Full test suite: 115 tests (67 Python + 48 JS), 0 failures
- ESLint + pre-commit hooks (ruff)
- Dependabot — weekly pip/npm/actions updates
- CI workflow — lint + Python matrix (3.10-3.12) + extension + E2E
- Makefile — 18 targets (build, test, lint, version, docker)
- Issue + PR templates, CONTRIBUTING.md, SECURITY.md, SETUP.md
- `.editorconfig`, `.githooks/pre-commit`, `.gitmessage`
- Version synced across VERSION, manifest.json, plugin.yaml, package.json
- Rate limiting — 30 req/s per IP token bucket on POST /cdp
- Event capture — 10MB rotation, SSE streaming, capture to disk
- Plugin auto-starts relay server — zero manual setup
- Plugin self-contained — no external imports
- Shadow DOM support in snapshot, selector wait, annotated screenshots
- Full tab lifecycle tools (create, close, attach, detach, switch, list)
- `browser_do` — compound action tool (18 action types in one call)
- `browser_dismiss_dialog` — handle JS alerts/confirms/prompts
- `browser_wait_selector` — shadow-DOM-aware element wait
- `browser_wait_for_navigation` — long-poll navigation wait
- Proactive content script health check — 150ms Promise.race timeout
- Debounced mutation observer — 250ms settle window
- Thread safety — `threading.local()` for tab routing, `itertools.count()` for CDP IDs
- `Viewer for page state` (browser_snapshot) — 2s cache, annotated screenshots
- Archived `capture_server.py` and stale `IMPROVEMENTS.md`
