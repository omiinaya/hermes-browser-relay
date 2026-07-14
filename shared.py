"""
Shared utilities for browser-relay MCP server and Hermes plugin.

Contains code that was duplicated between mcp-server/server.py and
plugins/browser-relay/__init__.py. Both import from here to keep
implementations in sync.
"""
import time
from typing import Any

# ---------------------------------------------------------------------------
# JS string quoting
# ---------------------------------------------------------------------------

def js_quote(s: str) -> str:
    """JSON-encode a string for safe JS interpolation."""
    return json.dumps(s)

# ---------------------------------------------------------------------------
# Snapshot cache — removes redundant DOM walks within 2s TTL
# ---------------------------------------------------------------------------

import json  # noqa: E402 (used by js_quote)

_snapshot_cache = {}  # key: (tab_id or 0, full) -> {"data": dict, "ts": float}
_last_nav_time = 0.0  # time of last navigation — cache is invalidated after
_SNAPSHOT_TTL = 2.0   # seconds before cache entry is considered stale


def cache_snapshot(tab_id: int | None, full: bool, data: dict) -> None:
    """Cache a snapshot result with current timestamp."""
    if not data or (isinstance(data, dict) and "error" in data):
        return
    global _last_nav_time
    now = time.time()
    if now - _last_nav_time < 0.5:
        return  # Don't cache during navigation
    _snapshot_cache[(tab_id or 0, full)] = {"data": data, "ts": now}


def get_cached_snapshot(tab_id: int | None, full: bool) -> dict | None:
    """Return cached snapshot if fresh (< 2s), else None."""
    entry = _snapshot_cache.get((tab_id or 0, full))
    if entry and time.time() - entry["ts"] < _SNAPSHOT_TTL:
        return entry["data"]
    return None


def invalidate_cache() -> None:
    """Clear all snapshot caches — call after navigation."""
    global _last_nav_time
    _snapshot_cache.clear()
    _last_nav_time = time.time()


# ---------------------------------------------------------------------------
# CDP response parsing
# ---------------------------------------------------------------------------

def parse_result(result: dict) -> Any:
    """Extract the actual return value from a CDP response."""
    if "error" in result:
        return result
    if "result" in result and "result" in result["result"]:
        val = result["result"]["result"].get("value")
        if val is not None:
            return val
        exc = result["result"].get("exceptionDetails")
        if exc:
            return {"error": f"JS exception: {exc.get('text', str(exc))}"}
        return result["result"]["result"]
    return result.get("result", result)


# ---------------------------------------------------------------------------
# Interactive element CSS selectors for snapshot
# ---------------------------------------------------------------------------

INTERACTIVE_SELECTORS = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]",
    "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])",
    "summary", "details", "[contenteditable=true]",
]
