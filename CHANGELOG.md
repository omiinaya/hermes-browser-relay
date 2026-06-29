# Changelog

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
