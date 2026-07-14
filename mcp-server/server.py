#!/usr/bin/env python3
"""
Browser Relay MCP Server — Exposes Chrome/CDP browser control as MCP tools.

Bridges Hermes Agent to a running CDP relay (:9223) so browser actions
(navigate, eval, click, type, screenshot) are available as first-class tools.

Protocol: stdio MCP — read JSON-RPC from stdin, write to stdout.
"""
import json
import os
import io
import base64
import time
import urllib.request
import urllib.error
from typing import Any

# Ensure the project root is on sys.path for shared.py import
_shared_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _shared_dir not in __import__('sys').path:
    __import__('sys').path.insert(0, _shared_dir)

from PIL import Image, ImageDraw, ImageFont

import websockets.sync.client

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

# Import shared utilities (snapshot cache, JS quoting, result parsing)
from shared import (
    js_quote,
    parse_result,
    cache_snapshot,
    get_cached_snapshot,
    invalidate_cache,
    INTERACTIVE_SELECTORS,
)
# Module-level aliases for backward compat with handler code
_js_quote = js_quote
_parse_result = parse_result
_cache_snapshot = cache_snapshot
_get_cached_snapshot = get_cached_snapshot
_invalidate_cache = invalidate_cache
_INTERACTIVE_SELECTORS = INTERACTIVE_SELECTORS


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def _hermes_call(func_name: str, *args, fallback: str = None, tab_id: int = None) -> dict:
    """Call __hermes.<func_name>(args) via content script fast path.
    
    Attempts content script routing first (no CDP overhead). Falls back to
    Runtime.evaluate if content script is unavailable (e.g. page just navigated).
    
    SMART RETRY: If CS is unavailable, waits 80ms and retries once — the content
    script is usually back by then. Catches 80%+ of navigation races.
    
    Returns the full CDP result dict (use _parse_result to extract the value).
    The result format matches CDP Runtime.evaluate for backward compat.
    """
    # Try content script fast path
    result = _cdp("__hermes", {}, timeout=CDP_TIMEOUT, tab_id=tab_id,
                  _hermes=True, _hermesFunc=func_name, _hermesArgs=list(args))
    
    # Check if content script was available
    error = result.get("error", {})
    if isinstance(error, dict) and error.get("_fallback"):
        # Smart retry: wait 80ms and try CS once more
        # Content script is usually back after navigation by this point
        import time as _retry_tm
        _retry_tm.sleep(0.08)
        result = _cdp("__hermes", {}, timeout=CDP_TIMEOUT, tab_id=tab_id,
                      _hermes=True, _hermesFunc=func_name, _hermesArgs=list(args))
        error = result.get("error", {})
        if isinstance(error, dict) and error.get("_fallback"):
            # Still unavailable — fall back to Runtime.evaluate
            js_args = ', '.join(json.dumps(a) for a in args)
            if fallback:
                full_js = f"(typeof __hermes !== 'undefined' && __hermes.{func_name}({js_args})) || ({fallback})"
            else:
                full_js = f"typeof __hermes !== 'undefined' && __hermes.{func_name}({js_args})"
            result = _cdp("Runtime.evaluate", {"expression": full_js, "returnByValue": True}, tab_id=tab_id)
    elif isinstance(error, dict):
        # Real error from content script
        pass
    
    return result


def _hermes_check() -> bool:
    """Quick check if __hermes is loaded in the current page."""
    result = _cdp("Runtime.evaluate", {"expression": "typeof __hermes !== 'undefined'", "returnByValue": True})
    val = _parse_result(result)
    return bool(val)


# ---------------------------------------------------------------------------
# CDP Relay Client (with connection reuse)
# ---------------------------------------------------------------------------

CDP_SERVER = os.environ.get("CDP_RELAY_URL", "http://127.0.0.1:9223")
CAPTURE_SERVER = os.environ.get("CAPTURE_SERVER_URL", "")  # Legacy, kept for compat
CDP_TIMEOUT = int(os.environ.get("CDP_RELAY_TIMEOUT", "60"))

# Tab targeting — set before CDP calls to route to a specific tab attachment
_shared_tab_id: int | None = None

def _set_tab(arguments: dict | None) -> None:
    """Extract optional tab_id from tool arguments for tab-targeted CDP routing."""
    global _shared_tab_id
    _shared_tab_id = arguments.get("tab_id") if arguments else None

# Shared HTTP connection for keep-alive
_cdp_opener = urllib.request.build_opener()
_cdp_opener.addheaders = [("Content-Type", "application/json")]

# Separate opener for capture server (plain GET requests)
_capture_opener = urllib.request.build_opener()

# ---------------------------------------------------------------------------
# Persistent WebSocket connection to relay (eliminates HTTP POST overhead)
# ---------------------------------------------------------------------------
_mcp_ws = None  # websockets.sync.client connection to ws://host:9223/mcp-ws

# Circuit breaker for WS reconnection
_ws_failures = 0        # consecutive WS failures
_last_ws_ok = 0.0       # timestamp of last successful WS operation
_ws_circuit_open_until = 0.0  # skip WS attempts until this time
_WS_BREAK_THRESHOLD = 3      # consecutive failures before circuit opens
_WS_RETRY_AFTER = 30.0       # seconds to wait before trying again


def _ensure_mcp_ws() -> bool:
    """Connect or reconnect to the relay's /mcp-ws endpoint.
    Uses a circuit breaker — if WS fails 3x in a row, skip attempts
    for 30s to avoid blocking tool calls with dead-connection timeouts.
    Returns True if connected and healthy.
    """
    global _mcp_ws, _ws_failures, _last_ws_ok, _ws_circuit_open_until
    now = time.time()

    # Circuit breaker: skip WS if it's been failing repeatedly
    if _ws_circuit_open_until > now:
        return False

    if _mcp_ws is not None:
        # Only ping if > 10s since last success — avoids 5s timeout on dead conn
        if _last_ws_ok > 0 and now - _last_ws_ok < 10.0:
            return True
        try:
            _mcp_ws.ping(timeout=1)  # short timeout — don't block tool calls
            _last_ws_ok = now
            _ws_failures = 0
            return True
        except Exception:
            _mcp_ws = None

    # Try connecting
    try:
        ws_url = CDP_SERVER.replace("http://", "ws://").replace("https://", "wss://") + "/mcp-ws"
        _mcp_ws = websockets.sync.client.connect(ws_url, open_timeout=3)
        _last_ws_ok = time.time()
        _ws_failures = 0
        _ws_circuit_open_until = 0.0
        return True
    except Exception:
        _mcp_ws = None
        _ws_failures += 1
        if _ws_failures >= _WS_BREAK_THRESHOLD:
            _ws_circuit_open_until = time.time() + _WS_RETRY_AFTER
            print(f"[ws] Circuit opened: {_WS_RETRY_AFTER}s cooldown ({_ws_failures} failures)", flush=True)
        return False


def _cdp_via_ws(body_dict: dict) -> dict | None:
    """Send CDP command via persistent WebSocket.
    Returns parsed response dict on success, None on failure (triggers HTTP fallback).
    """
    global _mcp_ws, _ws_failures, _last_ws_ok
    if not _ensure_mcp_ws():
        return None

    try:
        _mcp_ws.send(json.dumps(body_dict))
        resp = _mcp_ws.recv(timeout=body_dict.get("timeout", CDP_TIMEOUT) + 5)
        result = json.loads(resp)
        _last_ws_ok = time.time()
        _ws_failures = 0
        return result
    except Exception:
        _mcp_ws = None
        _ws_failures += 1
        if _ws_failures >= _WS_BREAK_THRESHOLD:
            _ws_circuit_open_until = time.time() + _WS_RETRY_AFTER
            print(f"[ws] Circuit opened: {_WS_RETRY_AFTER}s cooldown ({_ws_failures} failures)", flush=True)
        return None


def _cdp(method: str, params: dict | None = None, timeout: int = CDP_TIMEOUT, tab_id: int | None = None, **extra) -> dict:
    """Send a CDP command to the relay server. Uses shared connection for keep-alive.
    If tab_id is provided, routes to that specific tab attachment.
    Falls back to _shared_tab_id if set via _set_tab().
    Auto-retries once if relay is temporarily unreachable.
    Extra kwargs (e.g. _hermes=True) are forwarded to the extension for content script routing."""
    body_dict = {"method": method, "params": params or {}, "timeout": timeout}
    # Forward extra fields (content script routing, etc.)
    for k, v in extra.items():
        body_dict[k] = v
    effective_tab_id = tab_id if tab_id is not None else _shared_tab_id
    if effective_tab_id is not None:
        body_dict["tabId"] = effective_tab_id
    
    # Try persistent WebSocket first (no HTTP POST overhead)
    ws_result = _cdp_via_ws(body_dict)
    if ws_result is not None:
        return ws_result
    
    # Fall back to HTTP POST
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/cdp", data=body, method="POST")
    for attempt in range(2):
        try:
            with _cdp_opener.open(req, timeout=timeout + 5) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode() if e.fp else "{}"
            return {"error": f"HTTP {e.code}", "detail": json.loads(detail) if detail else {}}
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if attempt == 0:
                # Relay might be restarting — wait briefly and retry
                import time as _tm
                _tm.sleep(0.5)
                if _wait_for_relay(timeout=3):
                    continue
            return {"error": f"Connection failed: {e}", "hint": "Is the CDP relay running on :9223?"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Connection failed after retry", "hint": "Is the CDP relay running on :9223?"}


def _cdp_async(method: str, params: dict | None = None, timeout: int = CDP_TIMEOUT) -> dict:
    """Same as _cdp but returns immediately for fire-and-forget commands (no response needed)."""
    body = json.dumps({"method": method, "params": params or {}, "timeout": timeout}).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/cdp", data=body, method="POST")
    try:
        with _cdp_opener.open(req, timeout=timeout + 5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _query_capture(path: str, timeout: int = 5) -> dict:
    """Query the relay server's /recent endpoint. Returns parsed JSON or error dict.
    Auto-retries once if relay is temporarily unreachable."""
    for attempt in range(2):
        try:
            req = urllib.request.Request(f"{CDP_SERVER}{path}")
            with _capture_opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            if attempt == 0:
                import time as _tm
                _tm.sleep(0.5)
                if _wait_for_relay(timeout=3):
                    continue
            return {"error": f"Relay server unreachable: {e.reason}", "hint": "Is server.py running on :9223?"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Relay unreachable after retry", "hint": "Is server.py running on :9223?"}


# ---------------------------------------------------------------------------
# MCP Connection Watchdog
# ---------------------------------------------------------------------------

_connection_ok = False
_watchdog_running = False


def _wait_for_relay(timeout: float = 5.0) -> bool:
    """Block until the relay server responds on /status.
    Returns True if relay became reachable, False on timeout.
    Used by _cdp() and _query_capture() to auto-recover from relay restarts."""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        try:
            req = urllib.request.Request(f"{CDP_SERVER}/status")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                _connection_ok = data.get("extension_connected", False) and data.get("tab_id") is not None
                return True
        except Exception:
            _connection_ok = False
        _t.sleep(0.3)
    return False


def _check_connection() -> bool:
    """Ping the relay server /status endpoint. Returns True if reachable."""
    try:
        req = urllib.request.Request(f"{CDP_SERVER}/status")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            ok = data.get("extension_connected", False) and data.get("tab_id") is not None
            return ok
    except Exception:
        return False


def _watchdog_loop():
    """Background thread: periodically check relay health and log status changes."""
    global _connection_ok, _watchdog_running
    import time as _t
    _watchdog_running = True
    was_ok = False
    while _watchdog_running:
        ok = _check_connection()
        if ok and not was_ok:
            print("[watchdog] CDP relay connected (tab attached)", flush=True)
        elif not ok and was_ok:
            print("[watchdog] CDP relay disconnected — will retry...", flush=True)
        _connection_ok = ok
        was_ok = ok
        _t.sleep(15)


def start_watchdog():
    """Start the connection watchdog in a daemon thread."""
    import threading as _thr
    t = _thr.Thread(target=_watchdog_loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("browser-relay")



# ---------------------------------------------------------------------------
# Tool definitions — inline dict replaces 510-line list_tools()
# ---------------------------------------------------------------------------

TOOL_DEFS = {
    "browser_navigate": {
        "description": "Navigate the browser to a URL. Returns after navigation starts.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to navigate to (e.g. https://example.com)"},
                },
                "required": ["url"],
            },
    },
    "browser_eval": {
        "description": "Evaluate JavaScript in the current page context. Returns the result as a string. Prefer this over screenshots for reading page state \u2014 it's 10x faster.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
                },
                "required": ["expression"],
            },
    },
    "browser_hover": {
        "description": "Hover the mouse over an element by CSS selector. Dispatches CDP mouseMoved event to trigger mouseenter/mouseover for dropdowns and tooltips.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector (e.g. '.menu-item', '#tooltip-trigger')"},
                },
                "required": ["selector"],
            },
    },
    "browser_click": {
        "description": "Click an element by CSS selector. Finds coordinates via JS, dispatches CDP mouse events.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector (e.g. '#submit-btn', 'button.primary')"},
                },
                "required": ["selector"],
            },
    },
    "browser_type": {
        "description": "Type text into an input field. Selects the field first, clears it (Ctrl+A, Delete), then inserts text.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input field"},
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["selector", "text"],
            },
    },
    "browser_enter": {
        "description": "Press the Enter key. Use after typing into a search box or form.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_screenshot": {
        "description": "Capture a screenshot of the current page as PNG. Returns base64-encoded image data.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_status": {
        "description": "Check the CDP relay connection status \u2014 is an extension connected and attached to a tab?",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_send_cdp": {
        "description": "Send an arbitrary CDP command. Use for advanced operations not covered by other tools.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "CDP method name (e.g. 'Page.navigate', 'Runtime.evaluate')"},
                    "params": {"type": "object", "description": "CDP parameters as JSON object"},
                },
                "required": ["method"],
            },
    },
    "browser_recent_events": {
        "description": "Fetch recent browser events (navigations, downloads, requests) from the capture buffer.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 200)", "default": 20},
                    "mode": {"type": "string", "description": "'nav' for interesting events only, 'all' for everything (default: 'nav')", "default": "nav", "enum": ["nav", "all"]},
                },
            },
    },
    "browser_snapshot": {
        "description": "Get an accessibility-style snapshot of the current page. Returns compact info for all interactive elements (links, buttons, inputs) with coordinates so you can interact. Faster than taking a screenshot. Auto-cached for 2s \u2014 repeat calls within that window are instant.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "full": {"type": "boolean", "description": "If true, return complete page text content as well. Default: false (compact interactive elements only).", "default": False},
                },
            },
    },
    "browser_scroll": {
        "description": "Scroll the page in a direction or by a specific amount.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "Direction to scroll: 'up', 'down', 'left', 'right' (default: 'down')", "default": "down", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Pixels to scroll (default: viewport height / 2)", "default": 0},
                },
            },
    },
    "browser_wait_selector": {
        "description": "Wait for a CSS selector to appear in the DOM. Polls until the element exists or timeout expires.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to wait for (e.g. '#results', '.loading-done')"},
                    "timeout": {"type": "integer", "description": "Max seconds to wait (default: 15)", "default": 15},
                    "state": {"type": "string", "description": "'visible' = element must be visible (not display:none), 'attached' = element exists in DOM (default: 'visible')", "default": "visible", "enum": ["visible", "attached"]},
                },
                "required": ["selector"],
            },
    },
    "browser_click_text": {
        "description": "Click an element by its visible text content. Finds the first matching element (button, link, or anything) whose text contains the given string. Slower than browser_click (searches all elements) but doesn't require a CSS selector.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to search for (case-insensitive partial match)"},
                    "tag": {"type": "string", "description": "Optional: restrict to a specific tag (e.g. 'button', 'a', 'label')"},
                },
                "required": ["text"],
            },
    },
    "browser_fill_by_label": {
        "description": "Find a form field by its label text, placeholder, or aria-label, then type text into it. Handles React-controlled inputs by dispatching native keyboard events.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Label text, placeholder, or aria-label to search for"},
                    "text": {"type": "string", "description": "Text to type into the field"},
                },
                "required": ["label", "text"],
            },
    },
    "browser_get_text": {
        "description": "Get the rendered text content of the current page. Returns body text stripped of HTML tags. Much faster than taking and analyzing a screenshot.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_screenshot_annotated": {
        "description": "Take a screenshot with numbered labels overlaid on interactive elements. Each [N] corresponds to a clickable element.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_key": {
        "description": "Press a keyboard key (e.g. 'Escape', 'Tab', 'ArrowDown', 'Delete', 'Backspace'). Useful for keyboard navigation and form interactions.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key to press (e.g. 'Escape', 'Tab', 'ArrowDown', 'Enter', 'Delete', 'Backspace')", "default": "Escape"},
                },
            },
    },
    "browser_select_option": {
        "description": "Select an <option> in a <select> dropdown by visible text text match.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the <select> element (e.g. '#country', 'select[name=state]')"},
                    "text": {"type": "string", "description": "Visible text of the option to select (case-insensitive, exact match)"},
                },
                "required": ["selector", "text"],
            },
    },
    "browser_back": {
        "description": "Navigate back to the previous page in browser history.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_console": {
        "description": "Read recent browser console output (console.log/warn/error/debug/info messages). Returns the most recent console messages captured from the page.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max messages to return (default: 20, max: 200)", "default": 20},
                },
            },
    },
    "browser_list_tabs": {
        "description": "List all open browser tabs with their IDs, titles, URLs, and active status. Use this before browser_switch_tab to find the tab you want.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_attach_tab": {
        "description": "Attach the CDP debugger to a specific tab by tabId (additive \u2014 keeps existing attachments). Use browser_list_tabs to see available tabs.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to attach to"},
                    "keyword": {"type": "string", "description": "URL or title keyword to search for (alternative to tab_id)"},
                },
            },
    },
    "browser_detach_tab": {
        "description": "Detach the CDP debugger from a specific tab by tabId. Use browser_list_attachments to see currently attached tabs.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to detach from"},
                },
                "required": ["tab_id"],
            },
    },
    "browser_list_attachments": {
        "description": "List currently attached tab IDs \u2014 shows which tabs have active CDP debugger connections.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_switch_tab": {
        "description": "Switch to a tab by its tab ID (from browser_list_tabs), or by URL/title keyword. Pass tabId directly, or use keyword to match against URL or title.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID from browser_list_tabs (mutually exclusive with keyword)"},
                    "keyword": {"type": "string", "description": "URL or title keyword to search for (mutually exclusive with tab_id)"},
                },
            },
    },
    "browser_get_cookies": {
        "description": "Read all cookies for the current page via CDP Network.getCookies. Returns cookie name, value, domain, path, expiry, and flags.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_clear_cookies": {
        "description": "Clear all cookies for the current page via CDP Network.clearBrowserCookies.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_get_downloads": {
        "description": "List recent downloads captured by the extension. Returns download ID, URL, filename, total bytes, and state (created/changed).",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max downloads to return (default: 20, max: 50)", "default": 20},
                },
            },
    },
    "browser_refresh": {
        "description": "Reload the current page. Optionally bypass cache with hardReload=true.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "hard_reload": {"type": "boolean", "description": "Bypass cache (default: false)", "default": False},
                },
            },
    },
    "browser_forward": {
        "description": "Navigate forward in browser history.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_get_title": {
        "description": "Get the current page title.",
        "inputSchema": {
                "type": "object",
                "properties": {},
            },
    },
    "browser_find": {
        "description": "Search the page for elements matching text or selector. Returns matching elements with coordinates, tag, text content, and ref IDs for clicking. Useful for finding elements without knowing their exact CSS selector.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for (case-insensitive partial match on element text content)"},
                    "selector": {"type": "string", "description": "Optional CSS selector to narrow search scope (e.g. 'div.content', '#main')"},
                    "max_results": {"type": "integer", "description": "Maximum results (default: 20, max: 50)", "default": 20},
                },
                "required": ["query"],
            },
    },
    "browser_block_patterns": {
        "description": "Block or allow URL patterns matching specified strings. Uses Chrome's declarativeNetRequest API (MV3). Pass patterns=[\\\"*.css\\\",\\\"*.jpg\\\",\\\"*analytics*\\\"] to block, or patterns=[] to clear all blocks. Supports glob-style: *.css, *analytics*, https://example.com/*",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URL patterns to block. Empty array to clear all blocks. Supports *.css, *analytics*, /path/* formats",
                    },
                },
                "required": ["patterns"],
            },
    },
    "browser_pdf": {
        "description": "Save the current page as a PDF document. Returns base64-encoded PDF data. Uses CDP Page.printToPDF with A4 paper size, background colors, and system fonts.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "landscape": {"type": "boolean", "description": "Landscape orientation (default: false)", "default": False},
                    "scale": {"type": "number", "description": "Scale factor from 0.1 to 2.0 (default: 1.0)", "default": 1.0},
                },
            },
    },
    "browser_wait_for_navigation": {
        "description": "Wait for a page navigation (tab.navigated event) to complete. Polls the relay's event buffer until a fresh navigation is detected or timeout. Returns the URL, title, and tabId of the new page. Use this after browser_click or browser_navigate when you need the page to finish loading before inspecting it.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to wait for navigation on (optional — defaults to active tab)"},
                    "timeout": {"type": "integer", "description": "Max seconds to wait (default: 30)", "default": 30},
                },
            },
    },
    "browser_network_requests": {
        "description": "Query captured network requests from the relay's event buffer. Returns webRequest events (beforeRequest, beforeSendHeaders, headersReceived, completed, error) filtered by type. Useful for checking if a specific request completed, inspecting response headers, or debugging network issues.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "event": {"type": "string", "description": "Filter by event type: 'completed', 'error', 'beforeRequest', 'beforeSendHeaders', 'headersReceived' (optional — returns all types)"},
                    "since": {"type": "number", "description": "Only include events after this timestamp (epoch ms). Use the _received field from a prior call for polling", "default": 0},
                    "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 100)", "default": 20},
                },
            },
    },
    "browser_response_body": {
        "description": "Query captured response bodies from the relay's event buffer. The extension captures response bodies for JSON/PDF responses and matched URL patterns. Returns the URL, mime type, and body content for each captured response.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "number", "description": "Only include bodies captured after this timestamp (epoch ms)", "default": 0},
                    "limit": {"type": "integer", "description": "Max bodies to return (default: 5, max: 20)", "default": 5},
                    "include_body": {"type": "boolean", "description": "Whether to include full body content (default: false — only metadata)", "default": False},
                },
            },
    },
    "browser_set_cookie": {
        "description": "Set a browser cookie via CDP Network.setCookie. Useful for auth workflows, session injection, or bypassing login screens. Returns success/failure and cookie details.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Cookie name"},
                    "value": {"type": "string", "description": "Cookie value"},
                    "url": {"type": "string", "description": "Cookie URL (e.g. https://example.com). If omitted, uses domain + path"},
                    "domain": {"type": "string", "description": "Cookie domain (e.g. .example.com)"},
                    "path": {"type": "string", "description": "Cookie path (default: /)"},
                    "secure": {"type": "boolean", "description": "Secure flag (default: false)", "default": False},
                    "httpOnly": {"type": "boolean", "description": "HttpOnly flag (default: false)", "default": False},
                    "sameSite": {"type": "string", "description": "SameSite: 'Strict', 'Lax', or 'None' (optional)"},
                },
                "required": ["name", "value"],
            },
    },
    "browser_dismiss_dialog": {
        "description": "Dismiss a JavaScript dialog (alert/confirm/prompt) by accepting or cancelling it. JavaScript dialogs block all CDP commands until handled \u2014 use this tool when screenshots, navigation, or eval commands hang because a dialog is open on the page.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "accept": {"type": "boolean", "description": "True to accept/confirm, False to cancel/dismiss (default: true)"},
                    "prompt_text": {"type": "string", "description": "Text to provide for prompt() dialogs (ignored for alert/confirm)"},
                },
                "required": [],
            },
    },
    "browser_upload_file": {
        "description": "Upload a file by setting the value of an <input type=file> element. Uses CDP DOM.setFileInputFiles to set the file path on the file input without requiring a file picker dialog. \\n\\nIMPORTANT: File paths must exist on the machine where Chrome is running (not the agent's machine).",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the <input type=file> element"},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute file path(s) to upload (on the Chrome host machine)"},
                },
                "required": ["selector", "file_paths"],
            },
    },
    "browser_form_fill": {
        "description": "Fill multiple form fields at once from a dictionary of {field_identifier: value} pairs. Field identifiers can be label text, placeholder, name, or CSS selector. More efficient than calling browser_fill_by_label repeatedly for multi-field forms.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": "Dict of field identifier (label/placeholder/name) to value. E.g. {\"Email\": \"user@example.com\", \"Password\": \"secret123\", \"input[name=remember]\": \"true\"}",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["fields"],
            },
    },
    "browser_create_tab": {
        "description": "Create a new browser tab with an optional URL. Returns the new tab's ID, title, and URL. Uses chrome.tabs.create via the extension.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open (default: about:blank)", "default": "about:blank"},
                    "active": {"type": "boolean", "description": "Whether the tab should be active/focused (default: true)", "default": True},
                },
            },
    },
    "browser_close_tab": {
        "description": "Close a browser tab by its tab ID. Uses chrome.tabs.remove via the extension. Also cleans up any CDP attachments for the closed tab.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to close (from browser_list_tabs)"},
                },
                "required": ["tab_id"],
            },
    },
    "browser_do": {
        "description": "Execute multiple browser actions sequentially in ONE tool call. Eliminates LLM thinking time between steps. Supported actions: navigate, navigate_wait (navigate + auto-ready + optional element wait), click, click_text, hover, type, enter, key, scroll, snapshot, screenshot, get_text, fill_by_label, wait_selector, back, forward, eval, wait.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of action objects, each with an 'action' field. See browser_do handler for supported actions.",
                    },
                },
                "required": ["actions"],
            },
    },
    "browser_navigate_wait": {
        "description": "Navigate to a URL and wait until the page is fully loaded (readyState=complete). Auto-returns snapshot data (interactive elements with coordinates) so you can see the page immediately \u2014 no separate browser_snapshot call needed. Pass snapshot=False to get just the title.",
        "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to navigate to"},
                    "timeout": {"type": "integer", "description": "Max seconds to wait for page load (default: 15, max: 60)", "default": 15},
                },
                "required": ["url"],
            },
    },
}


TAB_ID_PROP = {"tab_id": {"type": "integer", "description": "Optional: target a specific tab attachment by ID (from browser_list_attachments)"}}

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List all browser-relay MCP tools."""
    tools = []
    for name, defn in TOOL_DEFS.items():
        tool = Tool(
            name=name,
            description=defn["description"],
            inputSchema=defn["inputSchema"],
        )
        props = tool.inputSchema.setdefault("properties", {})
        if "tab_id" not in props:
            props["tab_id"] = TAB_ID_PROP["tab_id"]
        tools.append(tool)
    return tools

# ---------------------------------------------------------------------------
# Per-tool handler functions — extracted from monolithic call_tool()
# ---------------------------------------------------------------------------

async def _handle_browser_navigate(arguments: dict) -> list[TextContent | ImageContent]:
    url = arguments["url"]
    result = _cdp("Page.navigate", {"url": url})
    _invalidate_cache()
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    return [TextContent(type="text", text=json.dumps(result.get("result", {}), indent=2))]

async def _handle_browser_eval(arguments: dict) -> list[TextContent | ImageContent]:
    expr = arguments["expression"]
    result = _cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    value = _parse_result(result)
    return [TextContent(type="text", text=str(value))]

async def _handle_browser_hover(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    result = _hermes_call("hover", selector, fallback=f"""
    (() => {{
        const el = document.querySelector({_js_quote(selector)});
        if (!el) return {{ error: 'Element not found: ' + {_js_quote(selector)} }};
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return {{ error: 'Element has zero size' }};
        const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
        el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        return {{ ok: true, x: cx, y: cy, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and value.get("ok"):
        return [TextContent(type="text", text=f"Hovered '{value.get('text', '?')}' ({value.get('tag', '?')}) at ({value.get('x',0)},{value.get('y',0)})")]
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    return [TextContent(type="text", text=f"Hovered {selector}")]

async def _handle_browser_click(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    result = _hermes_call("click", selector, fallback=f"""
    (() => {{
        const el = document.querySelector({_js_quote(selector)});
        if (!el) return {{ error: 'Element not found: ' + {_js_quote(selector)} }};
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return {{ error: 'Element has zero size' }};
        const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
        el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        return {{ ok: true, x: cx, y: cy, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    x = value.get("x", 0)
    y = value.get("y", 0)
    desc = f"Clicked '{value.get('text', '?')}' ({value.get('tag', '?')}) at ({x}, {y})"
    return [TextContent(type="text", text=desc)]

async def _handle_browser_type(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    text = arguments["text"]
    result = _hermes_call("type", selector, text, fallback=f"""
    (() => {{
        const el = document.querySelector({_js_quote(selector)});
        if (!el) return {{ error: 'Element not found: ' + {_js_quote(selector)} }};
        el.focus();
        el.value = '';
        el.select();
        document.execCommand('insertText', false, {_js_quote(text)});
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return {{ ok: true, tag: el.tagName.toLowerCase(), value_len: el.value.length }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    return [TextContent(type="text", text=f"Typed '{text}' into {selector}")]

async def _handle_browser_enter(arguments: dict) -> list[TextContent | ImageContent]:
    _hermes_call("key", "Enter", fallback="""
    (() => {
        const active = document.activeElement;
        if (!active) return { error: 'No active element' };
        active.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 }));
        active.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 }));
        return { ok: true };
    })()
    """)
    return [TextContent(type="text", text="Pressed Enter")]

async def _handle_browser_screenshot(arguments: dict) -> list[TextContent | ImageContent]:
    result = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    data = result.get("result", {}).get("data", "")
    if data:
        return [ImageContent(type="image", data=data, mimeType="image/png")]
    return [TextContent(type="text", text="No screenshot data returned")]

async def _handle_browser_status(arguments: dict) -> list[TextContent | ImageContent]:
    try:
        req = urllib.request.Request(f"{CDP_SERVER}/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = json.loads(resp.read().decode())
        status["watchdog"] = {
            "connected": _connection_ok,
            "running": _watchdog_running,
        }
        return [TextContent(type="text", text=json.dumps(status, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"CDP relay unreachable: {e}\nWatchdog: {'running' if _watchdog_running else 'stopped'} (last check: {_connection_ok})")]

async def _handle_browser_send_cdp(arguments: dict) -> list[TextContent | ImageContent]:
    method = arguments["method"]
    params = arguments.get("params", {})
    result = _cdp(method, params)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]

async def _handle_browser_recent_events(arguments: dict) -> list[TextContent | ImageContent]:
    limit = min(arguments.get("limit", 20), 200)
    mode = arguments.get("mode", "nav")
    result = _query_capture(f"/recent?limit={limit}&mode={mode}")
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    ev_count = result.get("count", 0)
    events = result.get("events", [])
    lines = [f"📊 {ev_count} recent events (of {result.get('total', '?')} total):", ""]
    for e in events:
        etype = e.get("type", e.get("eventType", "?"))
        if etype == "tab":
            lines.append(f"  🏁 NAV: {e.get('url', '')[:120]}")
        elif etype == "download":
            lines.append(f"  📥 DOWNLOAD {e.get('event', '')}: {e.get('filename', e.get('url', ''))[:120]}")
        elif etype == "webrequest":
            ev = e.get("event", "")
            if ev in ("completed", "error"):
                lines.append(f"  🌐 {ev}: {e.get('method','?')} {e.get('statusCode','?')} {e.get('url','')[:100]}")
        elif etype == "response_body":
            lines.append(f"  📄 BODY: {e.get('url', '')[:100]}")
    if len(lines) <= 2:
        lines.append("  (no events yet — the capture server is running but may not have received anything)")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_snapshot(arguments: dict) -> list[TextContent | ImageContent]:
    full = arguments.get("full", False)
    cached = _get_cached_snapshot(_shared_tab_id, full)
    if cached is not None:
        snap = cached
    else:
        # Prefer __hermes.snapshot(full) — single eval vs 50 lines of inline JS
        result = _hermes_call("snapshot", full, fallback=f"""
    (() => {{
        const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
        const _shadowQueryAll = (root, sel) => {{
            let r = [];
            try {{ r = Array.from(root.querySelectorAll(sel)); }} catch(e) {{}}
            if (root.shadowRoot) r = r.concat(_shadowQueryAll(root.shadowRoot, sel));
            for (const c of root.children || []) r = r.concat(_shadowQueryAll(c, sel));
            return r;
        }};
        const all = _shadowQueryAll(document, selectors.join(','));
        const seen = new Set();
        const items = [];
        let idx = 0;
        all.forEach(el => {{
            if (seen.has(el)) return;
            seen.add(el);
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            idx++;
            const tag = el.tagName.toLowerCase();
            const type = el.type || '';
            const txt = (el.textContent||'').trim().substring(0, 100);
            const placeholder = el.placeholder || '';
            const aria = el.getAttribute('aria-label') || '';
            const name = el.getAttribute('name') || '';
            const href = el.getAttribute('href') || '';
            const role = el.getAttribute('role') || '';
            items.push({{
                ref: 'e' + idx,
                tag: tag + (type ? '[type=' + type + ']' : ''),
                text: txt.substring(0, 80),
                placeholder: placeholder,
                aria_label: aria,
                name: name,
                href: href.substring(0, 120),
                role: role,
                x: Math.round(r.x + r.width/2),
                y: Math.round(r.y + r.height/2),
                w: Math.round(r.width),
                h: Math.round(r.height),
                visible: r.top < window.innerHeight && r.bottom > 0,
            }});
        }});
        const info = {{
            title: document.title,
            url: location.href,
            viewport: {{ w: window.innerWidth, h: window.innerHeight }},
            total: items.length,
            visible: items.filter(i => i.visible).length,
        }};
        let textContent = '';
        if ({'true' if full else 'false'}) {{
            textContent = (document.body ? document.body.innerText : '').substring(0, 30000);
        }}
        return {{ info, items, textContent }};
    }})()
    """)
        snap = _parse_result(result)
        _cache_snapshot(_shared_tab_id, full, snap)
    if isinstance(snap, dict) and "error" in snap:
        return [TextContent(type="text", text=f"Snapshot error: {snap['error']}")]
    info = snap.get("info", {})
    items = snap.get("items", [])
    lines = [
        f"📄 {info.get('title', '?')}",
        f"🌐 {info.get('url', '?')}",
        f"📐 {info.get('viewport', {}).get('w','?')}×{info.get('viewport', {}).get('h','?')}  —  {info.get('visible','?')} visible of {info.get('total','?')} interactive elements",
        "",
    ]
    for it in items[:60]:
        vis = "" if it["visible"] else "⬆"
        label = it["text"] or it["placeholder"] or it["aria_label"] or it["name"] or it["href"] or it["role"]
        lines.append(f"  [{it['ref']}] {vis}{it['tag']} \"{label[:70]}\" @({it['x']},{it['y']})")
    if len(items) > 60:
        lines.append(f"  ... and {len(items) - 60} more (use browser_scroll to reveal)")
    if full:
        text_content = snap.get("textContent", "")
        lines.extend(["", "--- Page Text ---", str(text_content)[:20000]])
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_scroll(arguments: dict) -> list[TextContent | ImageContent]:
    direction = arguments.get("direction", "down")
    amount = arguments.get("amount", 0)
    result = _hermes_call("scrollBy", direction, amount, fallback=f"""
    (() => {{
        const dir = {_js_quote(direction)};
        let amt = {amount or 0};
        if (!amt) amt = Math.round(window.innerHeight * 0.6);
        const dx = (dir === 'right' ? amt : (dir === 'left' ? -amt : 0));
        const dy = (dir === 'down' ? amt : (dir === 'up' ? -amt : 0));
        window.scrollBy({{ left: dx, top: dy, behavior: 'instant' }});
        return {{ direction: dir, amount: amt, scrollX: window.scrollX, scrollY: window.scrollY }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and value.get("direction"):
        return [TextContent(type="text", text=f"Scrolled {value.get('direction')} by {value.get('amount', '?')}px (pos: {value.get('scrollX',0)},{value.get('scrollY',0)})")]
    return [TextContent(type="text", text=f"Scrolled {direction}")]

async def _handle_browser_wait_selector(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    timeout = min(arguments.get("timeout", 15), 60)
    state = arguments.get("state", "visible")
    if state == "attached":
        result = _hermes_call("waitSelector", selector, timeout, "attached", fallback=f"""
    (() => {{
        const deadline = Date.now() + {timeout * 1000};
        while (Date.now() < deadline) {{
            const el = document.querySelector({_js_quote(selector)});
            if (el) return {{ ok: true }};
        }}
        return {{ error: 'Timeout waiting for: ' + {_js_quote(selector)}, timeout_seconds: {timeout} }};
    }})()
    """)
    else:
        result = _hermes_call("waitSelector", selector, timeout, "visible", fallback=f"""
    (() => {{
        const deadline = Date.now() + {timeout * 1000};
        while (Date.now() < deadline) {{
            const el = document.querySelector({_js_quote(selector)});
            if (el) {{
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return {{ ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) }};
            }}
        }}
        return {{ error: 'Timeout waiting for: ' + {_js_quote(selector)}, timeout_seconds: {timeout} }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"⏱️ Timed out ({timeout}s) waiting for '{selector}'")]
    return [TextContent(type="text", text=f"✅ Selector '{selector}' matched")]

async def _handle_browser_click_text(arguments: dict) -> list[TextContent | ImageContent]:
    text_query = arguments["text"]
    tag_filter = arguments.get("tag", "")
    result = _hermes_call("clickText", text_query, tag_filter, fallback=f"""
    (() => {{
        const searchText = {_js_quote(text_query)}.toLowerCase();
        const tag = {_js_quote(tag_filter)};
        const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
        const all = _sq(document, 'a, button, span, label, div[role=button], [role=link], [role=tab], [role=menuitem], input[type=submit], input[type=button], select, [onclick], [tabindex]');
        for (const el of all) {{
            if (tag && el.tagName.toLowerCase() !== tag) continue;
            const txt = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
            if (txt.includes(searchText)) {{
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
                el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                return {{ ok: true, x: cx, y: cy, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
            }}
        }}
        return {{ error: 'No element found with text: ' + searchText }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    return [TextContent(type="text", text=f"Clicked '{value.get('text','?')}' ({value.get('tag','?')}) by text")]

async def _handle_browser_fill_by_label(arguments: dict) -> list[TextContent | ImageContent]:
    label = arguments["label"]
    text = arguments["text"]
    result = _hermes_call("fillByLabel", label, text, fallback=f"""
    (() => {{
        const search = {_js_quote(label)}.toLowerCase();
        const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
        const labels = _sq(document, 'label');
        let input = null;
        for (const lbl of labels) {{
            const txt = (lbl.textContent||'').trim().toLowerCase();
            if (txt.includes(search)) {{
                const forId = lbl.getAttribute('for');
                if (forId) {{
                    input = document.getElementById(forId) || document.querySelector('input[name=' + JSON.stringify(forId) + ']');
                }} else {{
                    input = lbl.querySelector('input, textarea, select');
                }}
                if (input) break;
            }}
        }}
        if (!input) {{
            const allInputs = _sq(document, 'input, textarea');
            for (const el of allInputs) {{
                const ph = el.getAttribute('placeholder') || el.getAttribute('aria-label') || '';
                if (ph.toLowerCase().includes(search)) {{ input = el; break; }}
            }}
        }}
        if (!input) {{
            const allInputs = _sq(document, 'input, textarea');
            for (const el of allInputs) {{
                if ((el.getAttribute('name') || '').toLowerCase().includes(search)) {{ input = el; break; }}
            }}
        }}
        if (!input) return {{ error: 'Could not find input for: ' + search }};
        input.focus();
        input.value = '';
        input.select();
        document.execCommand('insertText', false, {_js_quote(text)});
        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return {{ ok: true, tag: input.tagName, type: input.type || '', value_len: input.value.length }};
    }})()
    """)
    fill_result = _parse_result(result)
    if isinstance(fill_result, dict) and "error" in fill_result:
        return [TextContent(type="text", text=f"Error: {fill_result['error']}")]
    return [TextContent(type="text", text=f"Filled '{label}' with '{text}'")]

async def _handle_browser_get_text(arguments: dict) -> list[TextContent | ImageContent]:
    result = _hermes_call("getText", fallback="document.body ? document.body.innerText.substring(0, 50000) : ''")
    page_text = str(_parse_result(result))
    return [TextContent(type="text", text=page_text[:40000])]

async def _handle_browser_screenshot_annotated(arguments: dict) -> list[TextContent | ImageContent]:
    annotate_result = _hermes_call("snapshot", False)
    snap = _parse_result(annotate_result)
    if isinstance(snap, dict) and "error" in snap:
        return [TextContent(type="text", text=f"Snapshot error: {snap['error']}")]
    all_items = snap.get("items", [])
    annotations = [it for it in all_items if it.get("visible") and it.get("x")][:50]
    if not annotations:
        return [TextContent(type="text", text="No visible interactive elements found")]
    shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    if "error" in shot:
        return [TextContent(type="text", text=f"Screenshot error: {shot['error']}")]
    raw_data = shot.get("result", {}).get("data", "")
    if not raw_data:
        return [TextContent(type="text", text="No screenshot data")]
    try:
        img_bytes = base64.b64decode(raw_data)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        draw = ImageDraw.Draw(img)

        font_size = max(14, img.width // 80)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except (IOError, OSError):
            font = ImageFont.load_default()

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        for idx, a in enumerate(annotations):
            ref = str(idx + 1)
            cx, cy = a.get("x", 0), a.get("y", 0)
            label = a.get("text") or a.get("aria_label") or a.get("placeholder") or a.get("name") or a.get("role") or a.get("tag", "?")
            bbox = draw.textbbox((0, 0), f"[{ref}]", font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            label_x = cx - 10
            label_y = cy - th - 4
            overlay_draw.rectangle(
                [label_x - 4, label_y - 4, label_x + tw + 4, label_y + th + 4],
                fill=(255, 200, 0, 220),
            )
            overlay_draw.text((label_x, label_y), f"[{ref}]", fill=(0, 0, 0, 220), font=font)
            overlay_draw.rectangle(
                [cx - a.get("w", 10)//2, cy - a.get("h", 10)//2, cx + a.get("w", 10)//2, cy + a.get("h", 10)//2],
                outline=(255, 200, 0, 220), width=2,
            )

        img = Image.alpha_composite(img, overlay)
        img_rgb = img.convert("RGB")
        buf = io.BytesIO()
        img_rgb.save(buf, format="PNG")
        annotated_b64 = base64.b64encode(buf.getvalue()).decode()

        lines = ["Interactive elements:"]
        for idx, a in enumerate(annotations):
            label = a.get("text") or a.get("aria_label") or a.get("placeholder") or a.get("name") or a.get("role") or a.get("tag", "?")
            lines.append(f"  [{idx+1}] \"{label[:50]}\" @({a['x']},{a['y']})")

        return [
            ImageContent(type="image", data=annotated_b64, mimeType="image/png"),
            TextContent(type="text", text="\n".join(lines)),
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Annotation overlay error: {e}")]

async def _handle_browser_key(arguments: dict) -> list[TextContent | ImageContent]:
    key = arguments.get("key", "Escape")
    result = _hermes_call("key", key, fallback=f"""
    (() => {{
        const k = {_js_quote(key)};
        const active = document.activeElement;
        if (!active) return {{ error: 'No active element', key: k }};
        const keyMap = {{ 'Enter': 13, 'Escape': 27, 'Tab': 9, 'Backspace': 8, 'Delete': 46, 'ArrowDown': 40, 'ArrowUp': 38, 'ArrowLeft': 37, 'ArrowRight': 39, 'Home': 36, 'End': 35, 'PageUp': 33, 'PageDown': 34 }};
        const kc = keyMap[k] || k.charCodeAt(0) || 0;
        active.dispatchEvent(new KeyboardEvent('keydown', {{ bubbles: true, cancelable: true, key: k, code: k, keyCode: kc, which: kc }}));
        active.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true, cancelable: true, key: k, code: k, keyCode: kc, which: kc }}));
        return {{ ok: true, key: k }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and value.get("ok"):
        return [TextContent(type="text", text=f"Pressed {key}")]
    return [TextContent(type="text", text=f"Pressed {key}")]

async def _handle_browser_select_option(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    text = arguments["text"]
    result = _hermes_call("selectOption", selector, text, fallback=f"""
    (() => {{
        const select = document.querySelector({json.dumps(selector)});
        if (!select) return {{ error: 'Select element not found: ' + {json.dumps(selector)} }};
        if (select.tagName !== 'SELECT') return {{ error: 'Element is not a <select>' }};
        const options = Array.from(select.options);
        const q = {json.dumps(text)}.trim().toLowerCase();
        const target = options.find(o => (o.text||'').trim().toLowerCase() === q);
        if (!target) return {{ error: 'Option not found with text: ' + {json.dumps(text)} }};
        select.value = target.value;
        select.dispatchEvent(new Event('change', {{ bubbles: true }}));
        select.dispatchEvent(new Event('input', {{ bubbles: true }}));
        return {{ ok: true, selected: target.text, value: target.value, index: target.index }};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    return [TextContent(type="text", text=f"Selected option '{value.get('selected', '?')}' (value={value.get('value', '?')}) in '{selector}'")]

async def _handle_browser_back(arguments: dict) -> list[TextContent | ImageContent]:
    _hermes_call("historyBack", fallback="window.history.back()")
    _invalidate_cache()
    return [TextContent(type="text", text="Navigated back")]

async def _handle_browser_console(arguments: dict) -> list[TextContent | ImageContent]:
    limit = min(arguments.get("limit", 20), 200)
    _cdp("Runtime.enable", timeout=5)
    result = _query_capture(f"/recent?limit={limit * 2}&mode=all")
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    events = result.get("events", [])
    console_events = [e for e in events if e.get("type") == "event" and e.get("method") == "Runtime.consoleAPICalled"]
    console_events = console_events[:limit]
    if not console_events:
        return [TextContent(type="text", text="No console messages captured yet. Try navigating to a page and running JavaScript that calls console.log().")]
    lines = [f"📋 {len(console_events)} recent console messages:", ""]
    for e in console_events:
        params = e.get("params", {})
        level = params.get("type", "log")
        args = params.get("args", [])
        # Format the message arguments
        text_parts = []
        for arg in args:
            arg_type = arg.get("type", "")
            if arg_type == "string":
                text_parts.append(arg.get("value", ""))
            elif arg_type in ("number", "boolean"):
                text_parts.append(str(arg.get("value", "")))
            elif arg_type == "undefined":
                text_parts.append("undefined")
            elif arg_type == "object":
                preview = arg.get("preview", {})
                if preview and preview.get("description"):
                    text_parts.append(str(preview.get("description", "")))
                else:
                    text_parts.append(str(arg.get("description", "[Object]")))
            else:
                text_parts.append(str(arg.get("description", arg.get("value", str(arg)))))
        msg_text = " ".join(text_parts)
        icon = {"log": "📝", "warning": "⚠️", "error": "❌", "debug": "🔍", "info": "ℹ️"}.get(level, "📝")
        lines.append(f"  {icon} [{level}] {msg_text[:300]}")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_list_tabs(arguments: dict) -> list[TextContent | ImageContent]:
    try:
        req = urllib.request.Request(f"{CDP_SERVER}/tabs")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if "error" in result:
            return [TextContent(type="text", text=f"Error: {result['error']}")]
        tabs = result.get("tabs", [])
        if not tabs:
            return [TextContent(type="text", text="No tabs found.")]
        lines = [f"📑 {len(tabs)} open tabs:", ""]
        for t in tabs:
            active = "🟢" if t.get("active") else "  "
            title = (t.get("title", "") or "").strip()[:60]
            url = (t.get("url", "") or "").strip()[:80]
            lines.append(f"  {active} [{t['id']}] {title}")
            lines.append(f"              {url}")
        return [TextContent(type="text", text="\n".join(lines))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error listing tabs: {e}")]

async def _handle_browser_attach_tab(arguments: dict) -> list[TextContent | ImageContent]:
    tab_id = arguments.get("tab_id")
    keyword = arguments.get("keyword")
    if not tab_id and not keyword:
        return [TextContent(type="text", text="Provide either tab_id or keyword to find the tab.")]
    if keyword and not tab_id:
        try:
            req = urllib.request.Request(f"{CDP_SERVER}/tabs")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            kw = keyword.lower()
            matches = [t for t in result.get("tabs", [])
                       if kw in (t.get("title", "") or "").lower()
                       or kw in (t.get("url", "") or "").lower()]
            if not matches:
                return [TextContent(type="text", text=f"No tab found matching '{keyword}'")]
            tab_id = matches[0]["id"]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    body = json.dumps({"tabId": tab_id}).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/tabs/attach", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("success"):
            return [TextContent(type="text", text=f"🔗 Attached to tab {tab_id} ({result.get('attachmentCount', '?')} total attachments)")]
        return [TextContent(type="text", text=f"Attach failed: {result.get('error', 'unknown')}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_detach_tab(arguments: dict) -> list[TextContent | ImageContent]:
    tab_id = arguments.get("tab_id")
    if not tab_id:
        return [TextContent(type="text", text="Provide tab_id to detach.")]
    body = json.dumps({"tabId": tab_id}).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/tabs/detach", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("success"):
            return [TextContent(type="text", text=f"🔌 Detached from tab {tab_id} ({result.get('attachmentCount', '?')} remaining)")]
        return [TextContent(type="text", text=f"Detach failed: {result.get('error', 'unknown')}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_list_attachments(arguments: dict) -> list[TextContent | ImageContent]:
    try:
        req = urllib.request.Request(f"{CDP_SERVER}/tabs/attachments")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
        attached = result.get("attachments", [])
        if not attached:
            return [TextContent(type="text", text="No tabs currently attached.")]
        lines = [f"🔗 {len(attached)} attached tab(s):", ""]
        for a in attached:
            lines.append(f"  [{a['tabId']}]")
        return [TextContent(type="text", text="\n".join(lines))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_switch_tab(arguments: dict) -> list[TextContent | ImageContent]:
    tab_id = arguments.get("tab_id")
    keyword = arguments.get("keyword")
    if not tab_id and not keyword:
        return [TextContent(type="text", text="Provide either tab_id or keyword to find the tab.")]
    if keyword and not tab_id:
        try:
            req = urllib.request.Request(f"{CDP_SERVER}/tabs")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            kw = keyword.lower()
            matches = [t for t in result.get("tabs", [])
                       if kw in (t.get("title", "") or "").lower()
                       or kw in (t.get("url", "") or "").lower()]
            if not matches:
                return [TextContent(type="text", text=f"No tab found matching '{keyword}'")]
            tab_id = matches[0]["id"]
            if len(matches) > 1:
                return [TextContent(type="text", text=f"Multiple tabs match '{keyword}'. Use browser_list_tabs and specify tab_id:\n" + "\n".join(
                    f"  [{t['id']}] {t.get('title','')[:60]}"
                    for t in matches))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error searching tabs: {e}")]
    body = json.dumps({"tabId": tab_id}).encode()
    switch_req = urllib.request.Request(f"{CDP_SERVER}/tabs/switch", data=body, method="POST")
    switch_req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(switch_req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("success"):
            return [TextContent(type="text", text=f"✅ Switched to tab {tab_id}")]
        return [TextContent(type="text", text=f"Switch failed: {result.get('error', 'unknown')}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error switching tab: {e}")]

async def _handle_browser_get_cookies(arguments: dict) -> list[TextContent | ImageContent]:
    result = _cdp("Network.getCookies")
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    cookies = result.get("result", {}).get("cookies", [])
    if not cookies:
        return [TextContent(type="text", text="No cookies for the current page.")]
    lines = [f"🍪 {len(cookies)} cookies:", ""]
    for c in cookies:
        name = c.get("name", "?")
        value = c.get("value", "")
        domain = c.get("domain", "")
        path = c.get("path", "")
        httponly = "🔒" if c.get("httpOnly") else "  "
        secure = "🔐" if c.get("secure") else "  "
        session = "📄" if c.get("session") else "⏳"
        expiry = ""
        if not c.get("session"):
            import datetime
            exp = c.get("expires", 0)
            if exp:
                try:
                    expiry = f" (expires {datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d')})"
                except Exception:
                    expiry = ""
        lines.append(f"  {httponly}{secure}{session} {name}={value[:80]}{expiry}")
        lines.append(f"         Domain: {domain}{path}")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_clear_cookies(arguments: dict) -> list[TextContent | ImageContent]:
    result = _cdp("Network.clearBrowserCookies")
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    return [TextContent(type="text", text="🧹 All cookies cleared.")]

async def _handle_browser_get_downloads(arguments: dict) -> list[TextContent | ImageContent]:
    limit = min(arguments.get("limit", 20), 50)
    dl_result = _query_capture(f"/recent?limit={limit * 2}&mode=all")
    if "error" in dl_result:
        return [TextContent(type="text", text=f"Error: {dl_result['error']}")]
    events = dl_result.get("events", [])
    dl_events = [e for e in events if e.get("type") == "download"]
    dl_events = dl_events[:limit]
    if not dl_events:
        return [TextContent(type="text", text="No recent downloads captured.")]
    lines = [f"📥 {len(dl_events)} recent downloads:", ""]
    for d in dl_events:
        evt = d.get("event", "?")
        icon = "🆕" if evt == "created" else "✅" if evt == "changed" else "📄"
        filename = d.get("filename", d.get("url", "?")).split("/")[-1][:60]
        url = (d.get("url", "") or "")[:80]
        state = d.get("state", "")
        total = d.get("totalBytes", 0)
        lines.append(f"  {icon} {filename}")
        lines.append(f"     {url}")
        if state:
            lines.append(f"     State: {state}")
        if total:
            lines.append(f"     Size: {total} bytes")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_refresh(arguments: dict) -> list[TextContent | ImageContent]:
    hard = arguments.get("hard_reload", False)
    result = _cdp("Page.reload", {"ignoreCache": hard})
    _invalidate_cache()
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    return [TextContent(type="text", text="🔄 Page reloaded" + (" (hard)" if hard else ""))]

async def _handle_browser_forward(arguments: dict) -> list[TextContent | ImageContent]:
    _hermes_call("historyForward", fallback="window.history.forward()")
    _invalidate_cache()
    return [TextContent(type="text", text="Navigated forward")]

async def _handle_browser_get_title(arguments: dict) -> list[TextContent | ImageContent]:
    result = _hermes_call("docInfo", fallback='({ title: document.title, url: location.href })')
    value = _parse_result(result)
    if isinstance(value, dict):
        title = value.get("title", "?")
        url = value.get("url", "?")
        return [TextContent(type="text", text=f"📄 {title}\n🌐 {url}")]
    return [TextContent(type="text", text=str(value)[:500])]

async def _handle_browser_find(arguments: dict) -> list[TextContent | ImageContent]:
    query = arguments["query"]
    css_selector = arguments.get("selector", "")
    max_results = min(arguments.get("max_results", 20), 50)
    result = _hermes_call("find", query, fallback=f"""
    (() => {{
        const q = {_js_quote(query)}.toLowerCase();
        const scope = {_js_quote(css_selector)};
        const maxR = {max_results};
        const root = scope ? document.querySelector(scope) : document.body;
        if (!root) return {{ error: 'Scope not found: ' + scope }};
        const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
        const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
        const all = _sq(root, selectors.join(','));
        const seen = new Set();
        const results = [];
        let idx = 0;
        for (const el of all) {{
            if (seen.has(el)) continue;
            seen.add(el);
            const txt = (el.textContent||'').trim().toLowerCase();
            if (!txt.includes(q)) continue;
            if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
            idx++;
            if (idx > maxR) break;
            const r = el.getBoundingClientRect();
            results.push({{ ref: 'e' + idx, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 100), x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), w: Math.round(r.width), h: Math.round(r.height), visible: r.top < window.innerHeight && r.bottom > 0 }});
        }}
        return {{ count: results.length, results }};
    }})()
    """)
    find_result = _parse_result(result)
    if isinstance(find_result, dict) and "error" in find_result:
        return [TextContent(type="text", text=f"Find error: {find_result['error']}")]
    count = find_result.get("count", 0)
    items = find_result.get("results", [])
    if count == 0:
        return [TextContent(type="text", text=f"No elements found matching '{query}'")]
    lines = [f"🔍 {count} element(s) matching '{query}':", ""]
    for it in items:
        vis = "" if it["visible"] else "⬆"
        lines.append(f"  [{it['ref']}] {vis}{it['tag']} \"{it['text'][:70]}\" @({it['x']},{it['y']})")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_block_patterns(arguments: dict) -> list[TextContent | ImageContent]:
    patterns = arguments.get("patterns", [])
    if not isinstance(patterns, list):
        return [TextContent(type="text", text="Error: patterns must be an array of strings")]
    body = json.dumps({"patterns": patterns}).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/block/patterns", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("success"):
            if patterns:
                return [TextContent(type="text", text=f"🚫 Blocking {len(patterns)} patterns: {', '.join(patterns[:10])}")]
            return [TextContent(type="text", text="✅ All blocking patterns cleared")]
        return [TextContent(type="text", text=f"Error: {json.dumps(result)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_pdf(arguments: dict) -> list[TextContent | ImageContent]:
    landscape = arguments.get("landscape", False)
    scale = arguments.get("scale", 1.0)
    params = {
        "landscape": landscape,
        "scale": scale,
        "printBackground": True,
        "preferCSSPageSize": True,
        "paperWidth": 8.27,
        "paperHeight": 11.69,
    }
    result = _cdp("Page.printToPDF", params)
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    data = result.get("result", {}).get("data", "")
    if data:
        pdf_bytes = base64.b64decode(data)
        return [TextContent(type="text", text=json.dumps({
            "base64_pdf": data[:100] + "..." if len(data) > 100 else data,
            "size": len(pdf_bytes),
            "mime": "application/pdf",
        }, indent=2))]
    return [TextContent(type="text", text="No PDF data returned")]

async def _handle_browser_wait_for_navigation(arguments: dict) -> list[TextContent | ImageContent]:
    import time as _time_module
    import asyncio as _asyncio
    tab_id = arguments.get("tab_id")
    timeout = min(arguments.get("timeout", 30), 120)
    deadline = _time_module.time() + timeout
    start_ts = _time_module.time() * 1000  # epoch ms
    while _time_module.time() < deadline:
        result = _query_capture(f"/recent?mode=nav&since={start_ts}&limit=5")
        events = result.get("events", []) if "error" not in result else []
        for e in events:
            if e.get("type") == "tab" and e.get("event") == "navigated":
                if tab_id is None or e.get("tabId") == tab_id:
                    return [TextContent(type="text", text=json.dumps({
                        "navigated": True,
                        "url": e.get("url", ""),
                        "title": e.get("title", ""),
                        "tabId": e.get("tabId"),
                        "elapsed": round((_time_module.time() * 1000 - start_ts) / 1000, 2),
                    }, indent=2))]
        await _asyncio.sleep(0.5)
    return [TextContent(type="text", text=json.dumps({
        "navigated": False,
        "error": f"Navigation timeout after {timeout}s",
        "elapsed": timeout,
    }, indent=2))]

async def _handle_browser_network_requests(arguments: dict) -> list[TextContent | ImageContent]:
    ev_filter = arguments.get("event", "")
    since = arguments.get("since", 0)
    limit = min(arguments.get("limit", 20), 100)
    qs = f"/recent?mode=all&type=webrequest&limit={limit}"
    if since:
        qs += f"&since={since}"
    result = _query_capture(qs)
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    events = result.get("events", [])
    if ev_filter:
        events = [e for e in events if e.get("event") == ev_filter]
    lines = [f"🌐 {len(events)} network requests:", ""]
    for e in events[:limit]:
        ev = e.get("event", "?")
        method = e.get("method", "?")
        status = e.get("statusCode", "?")
        url = e.get("url", "")[:100]
        lines.append(f"  {ev.upper()} {method} {status} {url}")
    if len(lines) <= 2:
        lines.append("  (no matching network requests found)")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_response_body(arguments: dict) -> list[TextContent | ImageContent]:
    since = arguments.get("since", 0)
    limit = min(arguments.get("limit", 5), 20)
    qs = f"/recent?mode=all&type=response_body&limit={limit}"
    if since:
        qs += f"&since={since}"
    result = _query_capture(qs)
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    events = result.get("events", [])
    include_body = arguments.get("include_body", False)
    lines = [f"📄 {len(events)} captured response bodies:", ""]
    for e in events[:limit]:
        url = e.get("url", "")[:100]
        mime = e.get("mimeType", "?")
        body = e.get("body", "")
        b64 = e.get("base64Encoded", False)
        size = len(body)
        lines.append(f"  {url}")
        lines.append(f"     MIME: {mime}  Size: {size}  Base64: {b64}")
        if include_body and body:
            snippet = body[:500]
            if b64:
                try:
                    snippet = base64.b64decode(body[:500]).decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
            lines.append(f"     Body: {snippet}")
    if len(lines) <= 2:
        lines.append("  (no response bodies captured yet)")
    return [TextContent(type="text", text="\n".join(lines))]

async def _handle_browser_set_cookie(arguments: dict) -> list[TextContent | ImageContent]:
    params = {
        "name": arguments["name"],
        "value": arguments["value"],
    }
    if "url" in arguments:
        params["url"] = arguments["url"]
    if "domain" in arguments:
        params["domain"] = arguments["domain"]
    if "path" in arguments:
        params["path"] = arguments["path"]
    if "secure" in arguments:
        params["secure"] = arguments["secure"]
    if "httpOnly" in arguments:
        params["httpOnly"] = arguments["httpOnly"]
    if "sameSite" in arguments:
        params["sameSite"] = arguments["sameSite"]
    result = _cdp("Network.setCookie", params)
    if "error" in result:
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    r = result.get("result", {})
    success = r.get("success", False)
    return [TextContent(type="text", text=json.dumps({
        "success": success,
        "cookie": params.get("name"),
        "domain": params.get("domain", params.get("url", "any")),
    }, indent=2))]

async def _handle_browser_dismiss_dialog(arguments: dict) -> list[TextContent | ImageContent]:
    accept = arguments.get("accept", True)
    params = {"accept": accept}
    if "prompt_text" in arguments:
        params["promptText"] = arguments["prompt_text"]
    result = _cdp("Page.handleJavaScriptDialog", params)
    if "error" in result:
        return [TextContent(type="text", text=f"Error dismissing dialog: {result['error']}")]
    return [TextContent(type="text", text=json.dumps({
        "accepted": accept,
        "success": True,
    }, indent=2))]

async def _handle_browser_upload_file(arguments: dict) -> list[TextContent | ImageContent]:
    selector = arguments["selector"]
    file_paths = arguments["file_paths"]
    if not isinstance(file_paths, list):
        file_paths = [file_paths]
    find_js = f"""
    (() => {{
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return {{ error: 'File input not found: ' + {json.dumps(selector)} }};
        if (el.tagName !== 'INPUT' || (el.type !== 'file' && el.getAttribute('type') !== 'file'))
            return {{ error: 'Element is not a file input: ' + el.tagName + ' type=' + (el.type || 'none') }};
        return {{ found: true }};
    }})()
    """
    check = _parse_result(_cdp("Runtime.evaluate", {"expression": find_js, "returnByValue": True}))
    if isinstance(check, dict) and "error" in check:
        return [TextContent(type="text", text=f"Error: {check['error']}")]
    doc_result = _cdp("DOM.getDocument", {"depth": 0})
    if "error" in doc_result:
        return [TextContent(type="text", text=f"Error getting DOM: {doc_result['error']}")]
    node_id_result = _cdp("DOM.querySelector", {"nodeId": doc_result["result"]["root"]["nodeId"], "selector": selector})
    if "error" in node_id_result:
        return [TextContent(type="text", text=f"Error finding node: {node_id_result['error']}")]
    node_id = node_id_result.get("result", {}).get("nodeId")
    if not node_id:
        return [TextContent(type="text", text=f"Error: Could not find node for selector: {selector}")]
    files_result = _cdp("DOM.setFileInputFiles", {
        "nodeId": node_id,
        "files": file_paths,
    })
    if "error" in files_result:
        return [TextContent(type="text", text=f"Error setting files: {files_result['error']}")]
    return [TextContent(type="text", text=json.dumps({
        "success": True,
        "files": file_paths,
        "selector": selector,
    }, indent=2))]

async def _handle_browser_form_fill(arguments: dict) -> list[TextContent | ImageContent]:
    fields = arguments["fields"]
    result = _hermes_call("formFill", fields, fallback=f"""
    (() => {{
        var fields = {json.dumps(fields)};
        var errors = [];
        var completed = 0;
        var keys = Object.keys(fields);
        var _shadowQueryAll = function(r, sel) {{ var z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_shadowQueryAll(r.shadowRoot,sel)); for(var c=0;c<(r.children||[]).length;c++) z=z.concat(_shadowQueryAll(r.children[c],sel)); return z; }};
        for (var i = 0; i < keys.length; i++) {{
            var identifier = keys[i];
            var value = fields[identifier];
            var valueStr = String(value);
            var isSelector = identifier.charCodeAt(0) === 35 || identifier.charCodeAt(0) === 46 || identifier.charCodeAt(0) === 91 || identifier.charCodeAt(0) === 58 || identifier.indexOf(' ') > 0 || identifier.indexOf('input[') === 0;
            var el = null;
            if (isSelector) {{
                el = document.querySelector(identifier);
            }} else {{
                var s = identifier.toLowerCase();
                // Label match
                var labels = _shadowQueryAll(document, 'label');
                for (var li = 0; li < labels.length; li++) {{
                    var lbl = labels[li];
                    if ((lbl.textContent||'').trim().toLowerCase().indexOf(s) !== -1) {{
                        var fid = lbl.getAttribute('for');
                        if (fid) el = document.getElementById(fid);
                        else el = lbl.querySelector('input,textarea,select');
                        if (el) break;
                    }}
                }}
                // Placeholder / aria-label match
                if (!el) {{
                    var allInp = _shadowQueryAll(document, 'input,textarea');
                    for (var pi = 0; pi < allInp.length; pi++) {{
                        var ph = allInp[pi].getAttribute('placeholder') || allInp[pi].getAttribute('aria-label') || '';
                        if (ph.toLowerCase().indexOf(s) !== -1) {{ el = allInp[pi]; break; }}
                    }}
                }}
                // Name match
                if (!el) {{
                    var allInp2 = _shadowQueryAll(document, 'input,textarea');
                    for (var ni = 0; ni < allInp2.length; ni++) {{
                        if ((allInp2[ni].getAttribute('name')||'').toLowerCase().indexOf(s) !== -1) {{ el = allInp2[ni]; break; }}
                    }}
                }}
            }}
            if (!el) {{
                errors.push({{field: identifier, error: 'Field not found'}});
                continue;
            }}
            if (el.tagName === 'SELECT') {{
                var opts = Array.from(el.options);
                var found = false;
                for (var j = 0; j < opts.length; j++) {{
                    if (opts[j].value === valueStr || opts[j].text.indexOf(valueStr) !== -1) {{
                        el.value = opts[j].value;
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        completed++;
                        found = true;
                        break;
                    }}
                }}
                if (!found) errors.push({{field: identifier, error: 'Option not found'}});
                continue;
            }}
            el.focus(); el.value = ''; el.select();
            document.execCommand('insertText', false, valueStr);
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            completed++;
        }}
        return {{completed: completed, errors: errors}};
    }})()
    """)
    value = _parse_result(result)
    if isinstance(value, dict) and "error" in value:
        return [TextContent(type="text", text=f"Error: {value['error']}")]
    if isinstance(value, dict):
        return [TextContent(type="text", text=json.dumps(value, indent=2))]
    return [TextContent(type="text", text=str(value))]

async def _handle_browser_create_tab(arguments: dict) -> list[TextContent | ImageContent]:
    url = arguments.get("url", "about:blank")
    active = arguments.get("active", True)
    try:
        req = urllib.request.Request(
            f"{CDP_SERVER}/tabs/create",
            data=json.dumps({"url": url, "active": active}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _cdp_opener.open(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_close_tab(arguments: dict) -> list[TextContent | ImageContent]:
    tab_id = arguments["tab_id"]
    try:
        req = urllib.request.Request(
            f"{CDP_SERVER}/tabs/close",
            data=json.dumps({"tabId": tab_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _cdp_opener.open(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

async def _handle_browser_do(arguments: dict) -> list[TextContent | ImageContent]:
    """Execute multiple browser actions sequentially in ONE tool call.
    Each action is a dict with an 'action' field and action-specific params.
    Supported actions: navigate, navigate_wait, click, type, snapshot, wait_selector, eval, scroll, enter, back, forward, get_text, wait.
    Eliminates LLM thinking time between steps."""
    actions = arguments.get("actions", [])
    if not actions or not isinstance(actions, list):
        return [TextContent(type="text", text="Error: 'actions' must be a non-empty array")]
    results = []
    for i, action in enumerate(actions):
        act = action.get("action", "")
        try:
            if act == "navigate":
                r = _cdp("Page.navigate", {"url": action["url"]})
                _invalidate_cache()
                results.append(f"[{i+1}] Navigated to {action.get('url', '?')}")
            elif act == "navigate_wait":
                url = action["url"]
                to = min(action.get("timeout", 15), 60)
                r = _cdp("Page.navigate", {"url": url})
                _invalidate_cache()
                import asyncio as _nwt_asyncio
                import time as _nwt_tm
                deadline = _nwt_tm.time() + to
                title = "?"
                while _nwt_tm.time() < deadline:
                    ready = _parse_result(_hermes_call("readyState", fallback="({ readyState: document.readyState, title: document.title })"))
                    if isinstance(ready, dict):
                        title = ready.get("title", "?")
                        if ready.get("readyState") == "complete":
                            break
                    await _nwt_asyncio.sleep(0.3)
                wait_for = action.get("wait_for")
                if wait_for:
                    ws_r = _hermes_call("waitSelector", wait_for, to, "visible", fallback=f"(typeof __hermes !== 'undefined' && __hermes.waitSelector({_js_quote(wait_for)}, {to})) || (() => {{ const d=Date.now()+{to*1000}; while(Date.now()<d){{ const e=document.querySelector({_js_quote(wait_for)}); if(e&&e.offsetWidth>0) return {{ok:true}}; }} return {{error:'timeout'}}; }})()")
                    ws_v = _parse_result(ws_r)
                    if isinstance(ws_v, dict) and ws_v.get("ok"):
                        results.append(f"[{i+1}] Navigate-wait: '{title}' loaded, '{wait_for}' visible")
                    else:
                        results.append(f"[{i+1}] Navigate-wait: '{title}' loaded, waited for '{wait_for}' (timeout)")
                else:
                    results.append(f"[{i+1}] Navigate-wait: '{title}' loaded")
            elif act == "wait_selector":
                sel = action["selector"]
                to = min(action.get("timeout", 10), 60)
                ws_r = _hermes_call("waitSelector", sel, to, "visible", fallback=f"(typeof __hermes !== 'undefined' && __hermes.waitSelector({_js_quote(sel)}, {to})) || (() => {{ const d=Date.now()+{to*1000}; while(Date.now()<d){{ const e=document.querySelector({_js_quote(sel)}); if(e&&e.offsetWidth>0) return {{ok:true}}; }} return {{error:'timeout'}}; }})()")
                r = _parse_result(ws_r)
                if isinstance(r, dict) and r.get("ok"):
                    results.append(f"[{i+1}] Wait: '{sel}' visible")
                else:
                    results.append(f"[{i+1}] Wait timed out: '{sel}'")
            elif act == "click":
                sel = action["selector"]
                click_result = _hermes_call("click", sel)
                v = _parse_result(click_result)
                if isinstance(v, dict) and v.get("ok"):
                    results.append(f"[{i+1}] Clicked '{sel}'")
                else:
                    results.append(f"[{i+1}] Click failed: {v}")
            elif act == "type":
                sel = action["selector"]
                txt = action["text"]
                _hermes_call("type", sel, txt)
                results.append(f"[{i+1}] Typed into '{sel}'")
            elif act == "enter":
                _hermes_call("key", "Enter")
                results.append(f"[{i+1}] Pressed Enter")
            elif act == "hover":
                sel = action["selector"]
                hv_r = _hermes_call("hover", sel)
                hv_v = _parse_result(hv_r)
                if isinstance(hv_v, dict) and hv_v.get("ok"):
                    results.append(f"[{i+1}] Hovered '{sel}'")
                else:
                    results.append(f"[{i+1}] Hover failed: {hv_v}")
            elif act == "click_text":
                text_query = action["text"]
                tag_filter = action.get("tag", "")
                ct_r = _hermes_call("clickText", text_query, tag_filter)
                ct_v = _parse_result(ct_r)
                if isinstance(ct_v, dict) and "error" not in ct_v:
                    results.append(f"[{i+1}] Clicked text '{text_query}'")
                else:
                    results.append(f"[{i+1}] Click-text failed: {ct_v}")
            elif act == "key":
                k = action.get("key", "Escape")
                _hermes_call("key", k)
                results.append(f"[{i+1}] Pressed {k}")
            elif act == "screenshot":
                shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
                if "error" not in shot:
                    results.append(f"[{i+1}] Screenshot captured")
                else:
                    results.append(f"[{i+1}] Screenshot failed")
            elif act == "fill_by_label":
                lbl = action["label"]
                txt = action["text"]
                fl_r = _hermes_call("fillByLabel", lbl, txt)
                fl_v = _parse_result(fl_r)
                if isinstance(fl_v, dict) and "error" not in fl_v:
                    results.append(f"[{i+1}] Filled '{lbl}'")
                else:
                    results.append(f"[{i+1}] Fill failed: {fl_v}")
            elif act == "scroll":
                d = action.get("direction", "down")
                a = action.get("amount", 0)
                _hermes_call("scrollBy", d, a)
                results.append(f"[{i+1}] Scrolled {d}")
            elif act == "back":
                _hermes_call("historyBack", fallback="window.history.back()")
                _invalidate_cache()
                results.append(f"[{i+1}] Navigated back")
            elif act == "forward":
                _hermes_call("historyForward", fallback="window.history.forward()")
                _invalidate_cache()
                results.append(f"[{i+1}] Navigated forward")
            elif act == "eval":
                expr = action["expression"]
                r = _parse_result(_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True}))
                results.append(f"[{i+1}] eval: {str(r)[:200]}")
            elif act == "snapshot":
                full = action.get("full", False)
                sn_r = _hermes_call("snapshot", full)
                sn_v = _parse_result(sn_r)
                if isinstance(sn_v, dict):
                    info = sn_v.get("info", {})
                    results.append(f"[{i+1}] Snapshot: {info.get('visible',0)}/{info.get('total',0)} elements on {info.get('title','?')}")
                else:
                    results.append(f"[{i+1}] Snapshot error")
            elif act == "get_text":
                gt_r = _hermes_call("getText", fallback="document.body ? document.body.innerText.substring(0,10000) : ''")
                txt = str(_parse_result(gt_r))[:300]
                results.append(f"[{i+1}] Text: {txt}")
            elif act == "wait":
                import asyncio
                await asyncio.sleep(min(action.get("seconds", 1), 10))
                results.append(f"[{i+1}] Waited {action.get('seconds', 1)}s")
            else:
                results.append(f"[{i+1}] Unknown action: {act}")
        except Exception as e:
            results.append(f"[{i+1}] Error in '{act}': {e}")
    summary = "\n".join(results)
    return [TextContent(type="text", text=f"✅ browser_do completed {len(actions)} actions:\n{summary}")]

async def _handle_browser_navigate_wait(arguments: dict) -> list[TextContent | ImageContent]:
    """Navigate to a URL and wait until the page is ready.
    Combines navigate + wait_for_navigation + ready-state poll in one tool call."""
    url = arguments["url"]
    timeout = min(arguments.get("timeout", 15), 60)
    nav_result = _cdp("Page.navigate", {"url": url})
    _invalidate_cache()
    if "error" in nav_result:
        return [TextContent(type="text", text=f"Error: {nav_result['error']}")]
    import asyncio as _asyncio
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        ready = _parse_result(_hermes_call("readyState", fallback="({ readyState: document.readyState, title: document.title })"))
        if isinstance(ready, dict):
            rs = ready.get("readyState", "")
            if rs == "complete":
                # Auto-snapshot: return page state inline — saves one LLM round-trip
                do_snap = arguments.get("snapshot", True)
                if do_snap:
                    snap_data = _parse_result(_hermes_call("snapshot", False))
                else:
                    snap_data = None
                title = ready.get("title", "?")
                lines = [f"✅ Loaded: {title}", f"🌐 {url}"]
                if isinstance(snap_data, dict) and "error" not in snap_data:
                    info = snap_data.get("info", {})
                    items = snap_data.get("items", [])
                    lines.append(f"📐 {info.get('viewport', {}).get('w','?')}×{info.get('viewport', {}).get('h','?')}  —  {info.get('visible','?')} visible of {info.get('total','?')} interactive elements")
                    lines.append("")
                    for it in items[:60]:
                        vis = "" if it["visible"] else "⬆"
                        label = it["text"] or it["placeholder"] or it.get("aria_label", "") or it["name"] or it["href"] or it["role"]
                        lines.append(f"  [{it['ref']}] {vis}{it['tag']} \"{label[:70]}\" @({it['x']},{it['y']})")
                    if len(items) > 60:
                        lines.append(f"  ... and {len(items) - 60} more")
                return [TextContent(type="text", text="\n".join(lines))]
        await _asyncio.sleep(0.3)
    return [TextContent(type="text", text=f"⏱️ Page did not finish loading within {timeout}s: {url}")]

async def _handle_unknown(name: str) -> list[TextContent]:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

# ---------------------------------------------------------------------------
# Tool dispatch — replaces 1290-line if/elif chain
# ---------------------------------------------------------------------------

HANDLER_MAP = {
    "browser_navigate": _handle_browser_navigate,
    "browser_eval": _handle_browser_eval,
    "browser_hover": _handle_browser_hover,
    "browser_click": _handle_browser_click,
    "browser_type": _handle_browser_type,
    "browser_enter": _handle_browser_enter,
    "browser_screenshot": _handle_browser_screenshot,
    "browser_status": _handle_browser_status,
    "browser_send_cdp": _handle_browser_send_cdp,
    "browser_recent_events": _handle_browser_recent_events,
    "browser_snapshot": _handle_browser_snapshot,
    "browser_scroll": _handle_browser_scroll,
    "browser_wait_selector": _handle_browser_wait_selector,
    "browser_click_text": _handle_browser_click_text,
    "browser_fill_by_label": _handle_browser_fill_by_label,
    "browser_get_text": _handle_browser_get_text,
    "browser_screenshot_annotated": _handle_browser_screenshot_annotated,
    "browser_key": _handle_browser_key,
    "browser_select_option": _handle_browser_select_option,
    "browser_back": _handle_browser_back,
    "browser_console": _handle_browser_console,
    "browser_list_tabs": _handle_browser_list_tabs,
    "browser_attach_tab": _handle_browser_attach_tab,
    "browser_detach_tab": _handle_browser_detach_tab,
    "browser_list_attachments": _handle_browser_list_attachments,
    "browser_switch_tab": _handle_browser_switch_tab,
    "browser_get_cookies": _handle_browser_get_cookies,
    "browser_clear_cookies": _handle_browser_clear_cookies,
    "browser_get_downloads": _handle_browser_get_downloads,
    "browser_refresh": _handle_browser_refresh,
    "browser_forward": _handle_browser_forward,
    "browser_get_title": _handle_browser_get_title,
    "browser_find": _handle_browser_find,
    "browser_block_patterns": _handle_browser_block_patterns,
    "browser_pdf": _handle_browser_pdf,
    "browser_wait_for_navigation": _handle_browser_wait_for_navigation,
    "browser_network_requests": _handle_browser_network_requests,
    "browser_response_body": _handle_browser_response_body,
    "browser_set_cookie": _handle_browser_set_cookie,
    "browser_dismiss_dialog": _handle_browser_dismiss_dialog,
    "browser_upload_file": _handle_browser_upload_file,
    "browser_form_fill": _handle_browser_form_fill,
    "browser_create_tab": _handle_browser_create_tab,
    "browser_close_tab": _handle_browser_close_tab,
    "browser_do": _handle_browser_do,
    "browser_navigate_wait": _handle_browser_navigate_wait,
}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    try:
        _set_tab(arguments)
        handler = HANDLER_MAP.get(name)
        if handler is None:
            return await _handle_unknown(name)
        return await handler(arguments)
    except Exception as e:
        return [TextContent(type="text", text=f"Error in {name}: {e}")]

def main():
    import asyncio
    start_watchdog()
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    asyncio.run(run())


if __name__ == "__main__":
    main()

