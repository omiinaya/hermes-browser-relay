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
import urllib.request
import urllib.error
import threading
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Interactive element CSS selectors for snapshot
# ---------------------------------------------------------------------------
_INTERACTIVE_SELECTORS = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]",
    "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])",
    "summary", "details", "[contenteditable=true]",
]


def _js_quote(s: str) -> str:
    """JSON-encode a string for safe JS interpolation."""
    return json.dumps(s)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

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


def _cdp(method: str, params: dict | None = None, timeout: int = CDP_TIMEOUT, tab_id: int | None = None) -> dict:
    """Send a CDP command to the relay server. Uses shared connection for keep-alive.
    If tab_id is provided, routes to that specific tab attachment.
    Falls back to _shared_tab_id if set via _set_tab().
    Auto-retries once if relay is temporarily unreachable."""
    body_dict = {"method": method, "params": params or {}, "timeout": timeout}
    effective_tab_id = tab_id if tab_id is not None else _shared_tab_id
    if effective_tab_id is not None:
        body_dict["tabId"] = effective_tab_id
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
        return {}


def _parse_result(result: dict) -> Any:
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
            print(f"[watchdog] CDP relay connected (tab attached)", flush=True)
        elif not ok and was_ok:
            print(f"[watchdog] CDP relay disconnected — will retry...", flush=True)
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


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="browser_navigate",
            description="Navigate the browser to a URL. Returns after navigation starts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to navigate to (e.g. https://example.com)"},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="browser_eval",
            description="Evaluate JavaScript in the current page context. Returns the result as a string. Prefer this over screenshots for reading page state — it's 10x faster.",
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
                },
                "required": ["expression"],
            },
        ),
        Tool(
            name="browser_hover",
            description="Hover the mouse over an element by CSS selector. Dispatches CDP mouseMoved event to trigger mouseenter/mouseover for dropdowns and tooltips.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector (e.g. '.menu-item', '#tooltip-trigger')"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_click",
            description="Click an element by CSS selector. Finds coordinates via JS, dispatches CDP mouse events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector (e.g. '#submit-btn', 'button.primary')"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_type",
            description="Type text into an input field. Selects the field first, clears it (Ctrl+A, Delete), then inserts text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input field"},
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["selector", "text"],
            },
        ),
        Tool(
            name="browser_enter",
            description="Press the Enter key. Use after typing into a search box or form.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_screenshot",
            description="Capture a screenshot of the current page as PNG. Returns base64-encoded image data.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_status",
            description="Check the CDP relay connection status — is an extension connected and attached to a tab?",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_send_cdp",
            description="Send an arbitrary CDP command. Use for advanced operations not covered by other tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "CDP method name (e.g. 'Page.navigate', 'Runtime.evaluate')"},
                    "params": {"type": "object", "description": "CDP parameters as JSON object"},
                },
                "required": ["method"],
            },
        ),
        Tool(
            name="browser_recent_events",
            description="Fetch recent browser events (navigations, downloads, requests) from the capture buffer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 200)", "default": 20},
                    "mode": {"type": "string", "description": "'nav' for interesting events only, 'all' for everything (default: 'nav')", "default": "nav", "enum": ["nav", "all"]},
                },
            },
        ),
        Tool(
            name="browser_snapshot",
            description="Get an accessibility-style snapshot of the current page. Returns compact info for all interactive elements (links, buttons, inputs) with coordinates so you can interact by coordinate or selector. Faster than taking a screenshot.",
            inputSchema={
                "type": "object",
                "properties": {
                    "full": {"type": "boolean", "description": "If true, return complete page text content as well. Default: false (compact interactive elements only).", "default": False},
                },
            },
        ),
        Tool(
            name="browser_scroll",
            description="Scroll the page in a direction or by a specific amount.",
            inputSchema={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "Direction to scroll: 'up', 'down', 'left', 'right' (default: 'down')", "default": "down", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Pixels to scroll (default: viewport height / 2)", "default": 0},
                },
            },
        ),
        Tool(
            name="browser_wait_selector",
            description="Wait for a CSS selector to appear in the DOM. Polls until the element exists or timeout expires.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to wait for (e.g. '#results', '.loading-done')"},
                    "timeout": {"type": "integer", "description": "Max seconds to wait (default: 15)", "default": 15},
                    "state": {"type": "string", "description": "'visible' = element must be visible (not display:none), 'attached' = element exists in DOM (default: 'visible')", "default": "visible", "enum": ["visible", "attached"]},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_click_text",
            description="Click an element by its visible text content. Finds the first matching element (button, link, or anything) whose text contains the given string. Slower than browser_click (searches all elements) but doesn't require a CSS selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to search for (case-insensitive partial match)"},
                    "tag": {"type": "string", "description": "Optional: restrict to a specific tag (e.g. 'button', 'a', 'label')"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="browser_fill_by_label",
            description="Find a form field by its label text, placeholder, or aria-label, then type text into it. Handles React-controlled inputs by dispatching native keyboard events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Label text, placeholder, or aria-label to search for"},
                    "text": {"type": "string", "description": "Text to type into the field"},
                },
                "required": ["label", "text"],
            },
        ),
        Tool(
            name="browser_get_text",
            description="Get the rendered text content of the current page. Returns body text stripped of HTML tags. Much faster than taking and analyzing a screenshot.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_screenshot_annotated",
            description="Take a screenshot with numbered labels overlaid on interactive elements. Each [N] corresponds to a clickable element.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_key",
            description="Press a keyboard key (e.g. 'Escape', 'Tab', 'ArrowDown', 'Delete', 'Backspace'). Useful for keyboard navigation and form interactions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key to press (e.g. 'Escape', 'Tab', 'ArrowDown', 'Enter', 'Delete', 'Backspace')", "default": "Escape"},
                },
            },
        ),
        Tool(
            name="browser_select_option",
            description="Select an <option> in a <select> dropdown by visible text text match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the <select> element (e.g. '#country', 'select[name=state]')"},
                    "text": {"type": "string", "description": "Visible text of the option to select (case-insensitive, exact match)"},
                },
                "required": ["selector", "text"],
            },
        ),
        Tool(
            name="browser_back",
            description="Navigate back to the previous page in browser history.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_console",
            description="Read recent browser console output (console.log/warn/error/debug/info messages). Returns the most recent console messages captured from the page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max messages to return (default: 20, max: 200)", "default": 20},
                },
            },
        ),
        Tool(
            name="browser_list_tabs",
            description="List all open browser tabs with their IDs, titles, URLs, and active status. Use this before browser_switch_tab to find the tab you want.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_attach_tab",
            description="Attach the CDP debugger to a specific tab by tabId (additive — keeps existing attachments). Use browser_list_tabs to see available tabs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to attach to"},
                    "keyword": {"type": "string", "description": "URL or title keyword to search for (alternative to tab_id)"},
                },
            },
        ),
        Tool(
            name="browser_detach_tab",
            description="Detach the CDP debugger from a specific tab by tabId. Use browser_list_attachments to see currently attached tabs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to detach from"},
                },
                "required": ["tab_id"],
            },
        ),
        Tool(
            name="browser_list_attachments",
            description="List currently attached tab IDs — shows which tabs have active CDP debugger connections.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_switch_tab",
            description="Switch to a tab by its tab ID (from browser_list_tabs), or by URL/title keyword. Pass tabId directly, or use keyword to match against URL or title.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID from browser_list_tabs (mutually exclusive with keyword)"},
                    "keyword": {"type": "string", "description": "URL or title keyword to search for (mutually exclusive with tab_id)"},
                },
            },
        ),
        Tool(
            name="browser_get_cookies",
            description="Read all cookies for the current page via CDP Network.getCookies. Returns cookie name, value, domain, path, expiry, and flags.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_clear_cookies",
            description="Clear all cookies for the current page via CDP Network.clearBrowserCookies.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_get_downloads",
            description="List recent downloads captured by the extension. Returns download ID, URL, filename, total bytes, and state (created/changed).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max downloads to return (default: 20, max: 50)", "default": 20},
                },
            },
        ),
        Tool(
            name="browser_refresh",
            description="Reload the current page. Optionally bypass cache with hardReload=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hard_reload": {"type": "boolean", "description": "Bypass cache (default: false)", "default": False},
                },
            },
        ),
        Tool(
            name="browser_forward",
            description="Navigate forward in browser history.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_get_title",
            description="Get the current page title.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_find",
            description="Search the page for elements matching text or selector. Returns matching elements with coordinates, tag, text content, and ref IDs for clicking. Useful for finding elements without knowing their exact CSS selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for (case-insensitive partial match on element text content)"},
                    "selector": {"type": "string", "description": "Optional CSS selector to narrow search scope (e.g. 'div.content', '#main')"},
                    "max_results": {"type": "integer", "description": "Maximum results (default: 20, max: 50)", "default": 20},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="browser_block_patterns",
            description="Block or allow URL patterns matching specified strings. Uses Chrome's declarativeNetRequest API (MV3). Pass patterns=[\"*.css\",\"*.jpg\",\"*analytics*\"] to block, or patterns=[] to clear all blocks. Supports glob-style: *.css, *analytics*, https://example.com/*",
            inputSchema={
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
        ),
        Tool(
            name="browser_pdf",
            description="Save the current page as a PDF document. Returns base64-encoded PDF data. Uses CDP Page.printToPDF with A4 paper size, background colors, and system fonts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "landscape": {"type": "boolean", "description": "Landscape orientation (default: false)", "default": False},
                    "scale": {"type": "number", "description": "Scale factor from 0.1 to 2.0 (default: 1.0)", "default": 1.0},
                },
            },
        ),
        Tool(
            name="browser_wait_for_navigation",
            description="Wait for a page navigation (tab.navigated event) to complete. Polls the relay's event buffer until a fresh navigation is detected or timeout. Returns the URL, title, and tabId of the new page. Use this after browser_click or browser_navigate when you need the page to finish loading before inspecting it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to wait for navigation on (optional — defaults to active tab)"},
                    "timeout": {"type": "integer", "description": "Max seconds to wait (default: 30)", "default": 30},
                },
            },
        ),
        Tool(
            name="browser_network_requests",
            description="Query captured network requests from the relay's event buffer. Returns webRequest events (beforeRequest, beforeSendHeaders, headersReceived, completed, error) filtered by type. Useful for checking if a specific request completed, inspecting response headers, or debugging network issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event": {"type": "string", "description": "Filter by event type: 'completed', 'error', 'beforeRequest', 'beforeSendHeaders', 'headersReceived' (optional — returns all types)"},
                    "since": {"type": "number", "description": "Only include events after this timestamp (epoch ms). Use the _received field from a prior call for polling", "default": 0},
                    "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 100)", "default": 20},
                },
            },
        ),
        Tool(
            name="browser_response_body",
            description="Query captured response bodies from the relay's event buffer. The extension captures response bodies for JSON/PDF responses and matched URL patterns. Returns the URL, mime type, and body content for each captured response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "number", "description": "Only include bodies captured after this timestamp (epoch ms)", "default": 0},
                    "limit": {"type": "integer", "description": "Max bodies to return (default: 5, max: 20)", "default": 5},
                    "include_body": {"type": "boolean", "description": "Whether to include full body content (default: false — only metadata)", "default": False},
                },
            },
        ),
        Tool(
            name="browser_set_cookie",
            description="Set a browser cookie via CDP Network.setCookie. Useful for auth workflows, session injection, or bypassing login screens. Returns success/failure and cookie details.",
            inputSchema={
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
        ),
        Tool(
            name="browser_dismiss_dialog",
            description="Dismiss a JavaScript dialog (alert/confirm/prompt) by accepting or cancelling it. JavaScript dialogs block all CDP commands until handled — use this tool when screenshots, navigation, or eval commands hang because a dialog is open on the page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accept": {"type": "boolean", "description": "True to accept/confirm, False to cancel/dismiss (default: true)"},
                    "prompt_text": {"type": "string", "description": "Text to provide for prompt() dialogs (ignored for alert/confirm)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="browser_upload_file",
            description="Upload a file by setting the value of an <input type=file> element. Uses CDP DOM.setFileInputFiles to set the file path on the file input without requiring a file picker dialog. \n\nIMPORTANT: File paths must exist on the machine where Chrome is running (not the agent's machine).",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the <input type=file> element"},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute file path(s) to upload (on the Chrome host machine)"},
                },
                "required": ["selector", "file_paths"],
            },
        ),
        Tool(
            name="browser_form_fill",
            description="Fill multiple form fields at once from a dictionary of {field_identifier: value} pairs. Field identifiers can be label text, placeholder, name, or CSS selector. More efficient than calling browser_fill_by_label repeatedly for multi-field forms.",
            inputSchema={
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
        ),
        Tool(
            name="browser_create_tab",
            description="Create a new browser tab with an optional URL. Returns the new tab's ID, title, and URL. Uses chrome.tabs.create via the extension.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open (default: about:blank)", "default": "about:blank"},
                    "active": {"type": "boolean", "description": "Whether the tab should be active/focused (default: true)", "default": True},
                },
            },
        ),
        Tool(
            name="browser_close_tab",
            description="Close a browser tab by its tab ID. Uses chrome.tabs.remove via the extension. Also cleans up any CDP attachments for the closed tab.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_id": {"type": "integer", "description": "Tab ID to close (from browser_list_tabs)"},
                },
                "required": ["tab_id"],
            },
        ),
    ]

    # Auto-inject tab_id into all tools for multi-tab targeting
    TAB_ID_PROP = {"tab_id": {"type": "integer", "description": "Optional: target a specific tab attachment by ID (from browser_list_attachments)"}}
    for tool in tools:
        props = tool.inputSchema.setdefault("properties", {})
        if "tab_id" not in props:
            props["tab_id"] = TAB_ID_PROP["tab_id"]

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    try:
        _set_tab(arguments)
        if name == "browser_navigate":
            url = arguments["url"]
            result = _cdp("Page.navigate", {"url": url})
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text=json.dumps(result.get("result", {}), indent=2))]

        elif name == "browser_eval":
            expr = arguments["expression"]
            result = _cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            value = _parse_result(result)
            return [TextContent(type="text", text=str(value))]

        elif name == "browser_hover":
            selector = arguments["selector"]
            # Find element coordinates via JS (same as click)
            find_expr = f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                const r = el.getBoundingClientRect();
                return {{ x: r.x + r.width/2, y: r.y + r.height/2, tag: el.tagName, text: (el.textContent||'').trim().substring(0,80) }};
            }})()
            """
            find_result = _cdp("Runtime.evaluate", {"expression": find_expr, "returnByValue": True})
            coords = _parse_result(find_result)
            if isinstance(coords, dict) and "error" in coords:
                return [TextContent(type="text", text=f"Error: {coords['error']}")]
            if isinstance(coords, dict) and "x" in coords:
                x, y = round(coords["x"]), round(coords["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
                desc = f"Hovered '{coords.get('text', '?')}' ({coords.get('tag', '?')}) at ({x}, {y})"
                return [TextContent(type="text", text=desc)]
            return [TextContent(type="text", text=f"Unexpected: {json.dumps(coords, indent=2)}")]

        elif name == "browser_click":
            selector = arguments["selector"]
            # Find element coordinates via JS
            find_expr = f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                const r = el.getBoundingClientRect();
                return {{ x: r.x + r.width/2, y: r.y + r.height/2, tag: el.tagName, text: (el.textContent||'').trim().substring(0,80) }};
            }})()
            """
            find_result = _cdp("Runtime.evaluate", {"expression": find_expr, "returnByValue": True})
            coords = _parse_result(find_result)
            if isinstance(coords, dict) and "error" in coords:
                return [TextContent(type="text", text=f"Error: {coords['error']}")]
            if isinstance(coords, dict) and "x" in coords:
                x, y = round(coords["x"]), round(coords["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                _cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                desc = f"Clicked '{coords.get('text', '?')}' ({coords.get('tag', '?')}) at ({x}, {y})"
                return [TextContent(type="text", text=desc)]
            return [TextContent(type="text", text=f"Unexpected: {json.dumps(coords, indent=2)}")]

        elif name == "browser_type":
            selector = arguments["selector"]
            text = arguments["text"]
            # Use execCommand('insertText') for full React/Next.js compatibility
            type_expr = f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                el.focus();
                el.value = '';
                el.select();
                document.execCommand('insertText', false, {json.dumps(text)});
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ ok: true, tag: el.tagName, value_len: el.value.length }};
            }})()
            """
            result = _cdp("Runtime.evaluate", {"expression": type_expr, "returnByValue": True})
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return [TextContent(type="text", text=f"Error: {value['error']}")]
            return [TextContent(type="text", text=f"Typed '{text}' into {selector}")]

        elif name == "browser_enter":
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "windowsVirtualKeyCode": 13})
            _cdp("Input.dispatchKeyEvent", {"type": "char", "text": "\r"})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "windowsVirtualKeyCode": 13})
            return [TextContent(type="text", text="Pressed Enter")]

        elif name == "browser_screenshot":
            result = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            data = result.get("result", {}).get("data", "")
            if data:
                return [ImageContent(type="image", data=data, mimeType="image/png")]
            return [TextContent(type="text", text="No screenshot data returned")]

        elif name == "browser_status":
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

        elif name == "browser_send_cdp":
            method = arguments["method"]
            params = arguments.get("params", {})
            result = _cdp(method, params)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "browser_snapshot":
            full = arguments.get("full", False)
            snapshot_js = f"""
            (() => {{
                const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
                // Shadow DOM-aware queryAll — traverses light DOM + shadow roots recursively
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
                return {{ info, items }};
            }})()
            """
            snap = _parse_result(_cdp("Runtime.evaluate", {"expression": snapshot_js, "returnByValue": True, "awaitPromise": True}))
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
                text_js = "document.body.innerText.substring(0, 30000)"
                text_content = _parse_result(_cdp("Runtime.evaluate", {"expression": text_js, "returnByValue": True}))
                lines.extend(["", "--- Page Text ---", str(text_content)[:20000]])
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "browser_scroll":
            direction = arguments.get("direction", "down")
            amount = arguments.get("amount", 0)
            if not amount:
                amount_js = "Math.round(window.innerHeight * 0.6)"
                amount = int(_parse_result(_cdp("Runtime.evaluate", {"expression": amount_js, "returnByValue": True})) or 400)
            dx, dy = 0, 0
            if direction == "down": dy = amount
            elif direction == "up": dy = -amount
            elif direction == "right": dx = amount
            elif direction == "left": dx = -amount
            _cdp("Runtime.evaluate", {"expression": f"window.scrollBy({{left:{dx}, top:{dy}, behavior:'smooth'}})", "returnByValue": True})
            return [TextContent(type="text", text=f"Scrolled {direction} by {abs(dx or dy)}px")]

        elif name == "browser_wait_selector":
            selector = arguments["selector"]
            timeout = min(arguments.get("timeout", 15), 60)
            state = arguments.get("state", "visible")
            import asyncio, time as ttime
            deadline = ttime.time() + timeout
            while ttime.time() < deadline:
                check_js = f"""
                (() => {{
                    // Shadow DOM-aware query — traverses light DOM + shadow roots recursively
                    const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
                    const el = _sq(document, {_js_quote(selector)})[0] || null;
                    if (!el) return false;
                    if ({'true' if state == 'visible' else 'false'}) {{
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }}
                    return true;
                }})()
                """
                found = _parse_result(_cdp("Runtime.evaluate", {"expression": check_js, "returnByValue": True}))
                if found:
                    return [TextContent(type="text", text=f"✅ Selector '{selector}' matched after {timeout - (deadline - ttime.time()):.1f}s")]
                await asyncio.sleep(0.5)
            return [TextContent(type="text", text=f"⏱️ Timed out ({timeout}s) waiting for '{selector}'")]

        elif name == "browser_click_text":
            text_query = arguments["text"]
            tag_filter = arguments.get("tag", "")
            click_js = f"""
            (() => {{
                const searchText = {_js_quote(text_query)}.toLowerCase();
                const tag = {_js_quote(tag_filter)};
                // Shadow DOM-aware queryAll — traverses light DOM + shadow roots recursively
                const _shadowQueryAll = (root, sel) => {{
                    let r = [];
                    try {{ r = Array.from(root.querySelectorAll(sel)); }} catch(e) {{}}
                    if (root.shadowRoot) r = r.concat(_shadowQueryAll(root.shadowRoot, sel));
                    for (const c of root.children || []) r = r.concat(_shadowQueryAll(c, sel));
                    return r;
                }};
                const all = _shadowQueryAll(document, 'a, button, span, label, div[role=button], [role=link], [role=tab], [role=menuitem], input[type=submit], input[type=button], select, [onclick], [tabindex]');
                for (const el of all) {{
                    if (tag && el.tagName.toLowerCase() !== tag) continue;
                    const txt = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (txt.includes(searchText)) {{
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        return {{ x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), tag: el.tagName, text: (el.textContent||'').trim().substring(0,80) }};
                    }}
                }}
                return {{ error: 'No element found with text: ' + searchText }};
            }})()
            """
            ct = _parse_result(_cdp("Runtime.evaluate", {"expression": click_js, "returnByValue": True}))
            if isinstance(ct, dict) and "error" in ct:
                return [TextContent(type="text", text=f"Error: {ct['error']}")]
            if isinstance(ct, dict) and "x" in ct:
                x, y = round(ct["x"]), round(ct["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                _cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                return [TextContent(type="text", text=f"Clicked '{ct.get('text','?')}' ({ct.get('tag','?')}) by text")]
            return [TextContent(type="text", text=f"Unexpected: {json.dumps(ct, indent=2)}")]

        elif name == "browser_fill_by_label":
            label = arguments["label"]
            text = arguments["text"]
            fill_js = f"""
            (() => {{
                const search = {_js_quote(label)}.toLowerCase();
                // Shadow DOM-aware helpers
                const _shadowQueryAll = (root, sel) => {{
                    let r = [];
                    try {{ r = Array.from(root.querySelectorAll(sel)); }} catch(e) {{}}
                    if (root.shadowRoot) r = r.concat(_shadowQueryAll(root.shadowRoot, sel));
                    for (const c of root.children || []) r = r.concat(_shadowQueryAll(c, sel));
                    return r;
                }};
                const _shadowQuery = (root, sel) => _shadowQueryAll(root, sel)[0] || null;
                // Try label association (light + shadow DOM)
                const labels = _shadowQueryAll(document, 'label');
                let input = null;
                for (const lbl of labels) {{
                    const txt = (lbl.textContent||'').trim().toLowerCase();
                    if (txt.includes(search)) {{
                        const forId = lbl.getAttribute('for');
                        if (forId) {{
                            input = document.getElementById(forId) || _shadowQuery(document, '#' + CSS.escape(forId)) || document.querySelector('input[name=' + JSON.stringify(forId) + ']');
                        }} else {{
                            input = lbl.querySelector('input, textarea, select') || _shadowQuery(lbl, 'input, textarea, select');
                        }}
                        if (input) break;
                    }}
                }}
                // Try placeholder (shadow-aware)
                if (!input) {{
                    const allInputs = _shadowQueryAll(document, 'input, textarea');
                    for (const el of allInputs) {{
                        const ph = el.getAttribute('placeholder') || el.getAttribute('aria-label') || '';
                        if (ph.toLowerCase().includes(search)) {{ input = el; break; }}
                    }}
                }}
                // Try name attribute (shadow-aware)
                if (!input) {{
                    const allInputs = _shadowQueryAll(document, 'input, textarea');
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
            """
            fill_result = _parse_result(_cdp("Runtime.evaluate", {"expression": fill_js, "returnByValue": True}))
            if isinstance(fill_result, dict) and "error" in fill_result:
                return [TextContent(type="text", text=f"Error: {fill_result['error']}")]
            return [TextContent(type="text", text=f"Filled '{label}' with '{text}'")]

        elif name == "browser_get_text":
            text_js = "document.body.innerText.substring(0, 50000)"
            page_text = str(_parse_result(_cdp("Runtime.evaluate", {"expression": text_js, "returnByValue": True})))
            return [TextContent(type="text", text=page_text[:40000])]

        elif name == "browser_screenshot_annotated":
            # Get interactive elements with their center coordinates
            annotate_js = f"""
            (() => {{
                const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
                // Shadow DOM-aware queryAll — traverses light DOM + shadow roots recursively
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
                    if (r.top > window.innerHeight || r.bottom < 0) return;  // off-screen
                    idx++;
                    const txt = (el.textContent||'').trim().substring(0, 40);
                    const role = el.getAttribute('role') || '';
                    const label = txt || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('name') || role || el.tagName;
                    items.push({{ ref: idx, text: label.substring(0, 30), x: Math.round(r.x + r.width/2), y: Math.round(r.top + 8), left: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }});
                }});
                return items.slice(0, 50);
            }})()
            """
            annotations = _parse_result(_cdp("Runtime.evaluate", {"expression": annotate_js, "returnByValue": True}))
            if not isinstance(annotations, list) or len(annotations) == 0:
                return [TextContent(type="text", text="No interactive elements found on screen")]

            # Take screenshot
            shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            if "error" in shot:
                return [TextContent(type="text", text=f"Screenshot error: {shot['error']}")]
            raw_data = shot.get("result", {}).get("data", "")
            if not raw_data:
                return [TextContent(type="text", text="No screenshot data")]

            # Decode screenshot and overlay labels using Pillow
            try:
                img_bytes = base64.b64decode(raw_data)
                img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                draw = ImageDraw.Draw(img)

                # Try to load a nicer font, fall back to default
                font_size = max(14, img.width // 80)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
                except (IOError, OSError):
                    font = ImageFont.load_default()

                # Draw a semi-transparent overlay layer for label backgrounds
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)

                for a in annotations:
                    ref = str(a.get("ref", "?"))
                    cx, cy = a.get("x", 0), a.get("y", 0)
                    left = a.get("left", cx - 10)
                    top = a.get("top", cy - 10)

                    # Measure text
                    bbox = draw.textbbox((0, 0), f"[{ref}]", font=font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]

                    # Label background
                    label_x = left
                    label_y = top - th - 4
                    padding = 4
                    overlay_draw.rectangle(
                        [label_x - padding, label_y - padding, label_x + tw + padding, label_y + th + padding],
                        fill=(255, 200, 0, 220),
                    )
                    # Draw text label in black
                    overlay_draw.text(
                        (label_x, label_y),
                        f"[{ref}]",
                        fill=(0, 0, 0, 220),
                        font=font,
                    )
                    # Highlight rectangle around element
                    if a.get("w") and a.get("h"):
                        overlay_draw.rectangle(
                            [left, top, left + a["w"], top + a["h"]],
                            outline=(255, 200, 0, 220),
                            width=2,
                        )

                img = Image.alpha_composite(img, overlay)

                # Convert back to RGB for PNG encoding
                img_rgb = img.convert("RGB")

                buf = io.BytesIO()
                img_rgb.save(buf, format="PNG")
                annotated_b64 = base64.b64encode(buf.getvalue()).decode()

                # Build minimal element listing for context
                lines = ["Interactive elements:"]
                for a in annotations:
                    lines.append(f"  [{a['ref']}] \"{a['text'][:50]}\" @({a['x']},{a['y']})")

                return [
                    ImageContent(type="image", data=annotated_b64, mimeType="image/png"),
                    TextContent(type="text", text="\n".join(lines)),
                ]
            except Exception as e:
                return [TextContent(type="text", text=f"Annotation overlay error: {e}")]

        elif name == "browser_key":
            key = arguments.get("key", "Escape")
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
            return [TextContent(type="text", text=f"Pressed {key}")]

        elif name == "browser_select_option":
            selector = arguments["selector"]
            text = arguments["text"]
            select_js = f"""
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
            """
            result = _cdp("Runtime.evaluate", {"expression": select_js, "returnByValue": True})
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return [TextContent(type="text", text=f"Error: {value['error']}")]
            return [TextContent(type="text", text=f"Selected option '{value.get('selected', '?')}' (value={value.get('value', '?')}, index={value.get('index', '?')}) in '{selector}'")]

        elif name == "browser_back":
            result = _cdp("Runtime.evaluate", {"expression": "window.history.back()"})
            return [TextContent(type="text", text="Navigated back")]

        elif name == "browser_console":
            limit = min(arguments.get("limit", 20), 200)
            # Ensure Runtime domain is enabled (safe to send repeatedly)
            _cdp("Runtime.enable", timeout=5)
            # Query capture buffer for console events
            result = _query_capture(f"/recent?limit={limit * 2}&mode=all")
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            events = result.get("events", [])
            # Filter for Runtime.consoleAPICalled events
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

        elif name == "browser_list_tabs":
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

        elif name == "browser_attach_tab":
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

        elif name == "browser_detach_tab":
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

        elif name == "browser_list_attachments":
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

        elif name == "browser_switch_tab":
            tab_id = arguments.get("tab_id")
            keyword = arguments.get("keyword")
            if not tab_id and not keyword:
                return [TextContent(type="text", text="Provide either tab_id or keyword to find the tab.")]

            # If keyword given, find matching tab first
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

            # Switch to the tab
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

        elif name == "browser_get_cookies":
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

        elif name == "browser_clear_cookies":
            result = _cdp("Network.clearBrowserCookies")
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text="🧹 All cookies cleared.")]

        elif name == "browser_get_downloads":
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
                if state: lines.append(f"     State: {state}")
                if total: lines.append(f"     Size: {total} bytes")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "browser_refresh":
            hard = arguments.get("hard_reload", False)
            result = _cdp("Page.reload", {"ignoreCache": hard})
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text="🔄 Page reloaded" + (" (hard)" if hard else ""))]

        elif name == "browser_forward":
            _cdp("Runtime.evaluate", {"expression": "window.history.forward()"})
            return [TextContent(type="text", text="Navigated forward")]

        elif name == "browser_get_title":
            title = _parse_result(_cdp("Runtime.evaluate", {"expression": "document.title", "returnByValue": True}))
            return [TextContent(type="text", text=f"📄 {title}")]

        elif name == "browser_find":
            query = arguments["query"]
            css_selector = arguments.get("selector", "")
            max_results = min(arguments.get("max_results", 20), 50)
            find_js = f"""
            (() => {{
                const query = {_js_quote(query)}.toLowerCase();
                const scope = {_js_quote(css_selector)};
                const maxR = {max_results};
                const root = scope ? document.querySelector(scope) : document.body;
                if (!root) return {{ error: 'Scope selector not found: ' + scope }};
                const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
                // Shadow DOM-aware queryAll
                const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
                const all = _sq(root, selectors.join(','));
                const seen = new Set();
                const results = [];
                let idx = 0;
                all.forEach(el => {{
                    if (seen.has(el)) return;
                    seen.add(el);
                    const txt = (el.textContent||'').trim().toLowerCase();
                    if (!txt.includes(query)) return;
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
                    idx++;
                    const r = el.getBoundingClientRect();
                    results.push({{
                        ref: 'e' + idx,
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent||'').trim().substring(0, 100),
                        x: Math.round(r.x + r.width/2),
                        y: Math.round(r.y + r.height/2),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.top < window.innerHeight && r.bottom > 0,
                    }});
                }});
                return {{ count: results.length, results: results.slice(0, maxR) }};
            }})()
            """
            find_result = _parse_result(_cdp("Runtime.evaluate", {"expression": find_js, "returnByValue": True, "awaitPromise": True}))
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

        elif name == "browser_block_patterns":
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

        elif name == "browser_recent_events":
            limit = min(arguments.get("limit", 20), 200)
            mode = arguments.get("mode", "nav")
            result = _query_capture(f"/recent?limit={limit}&mode={mode}")
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            ev_count = result.get("count", 0)
            events = result.get("events", [])
            lines = [f"📊 {ev_count} recent events (of {result.get('total', '?')} total):", ""]
            for e in events:
                ts = e.get("_received", "")
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

        elif name == "browser_pdf":
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

        elif name == "browser_wait_for_navigation":
            import time as _time_module
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
                import asyncio as _asyncio
                try:
                    _asyncio.sleep(0.5)
                except RuntimeError:
                    pass
            return [TextContent(type="text", text=json.dumps({
                "navigated": False,
                "error": f"Navigation timeout after {timeout}s",
                "elapsed": timeout,
            }, indent=2))]

        elif name == "browser_network_requests":
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

        elif name == "browser_response_body":
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

        elif name == "browser_set_cookie":
            params = {
                "name": arguments["name"],
                "value": arguments["value"],
            }
            if "url" in arguments: params["url"] = arguments["url"]
            if "domain" in arguments: params["domain"] = arguments["domain"]
            if "path" in arguments: params["path"] = arguments["path"]
            if "secure" in arguments: params["secure"] = arguments["secure"]
            if "httpOnly" in arguments: params["httpOnly"] = arguments["httpOnly"]
            if "sameSite" in arguments: params["sameSite"] = arguments["sameSite"]
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

        elif name == "browser_upload_file":
            selector = arguments["selector"]
            file_paths = arguments["file_paths"]
            if not isinstance(file_paths, list):
                file_paths = [file_paths]
            # First find the input element node ID via JS
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
            # Get the backend node ID via CDP
            doc_result = _cdp("DOM.getDocument", {"depth": 0})
            if "error" in doc_result:
                return [TextContent(type="text", text=f"Error getting DOM: {doc_result['error']}")]
            node_id_result = _cdp("DOM.querySelector", {"nodeId": doc_result["result"]["root"]["nodeId"], "selector": selector})
            if "error" in node_id_result:
                return [TextContent(type="text", text=f"Error finding node: {node_id_result['error']}")]
            node_id = node_id_result.get("result", {}).get("nodeId")
            if not node_id:
                return [TextContent(type="text", text=f"Error: Could not find node for selector: {selector}")]
            # Set the file input files
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

        elif name == "browser_dismiss_dialog":
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

        elif name == "browser_form_fill":
            fields = arguments["fields"]
            results = []
            import time as _tf_mod
            for identifier, value in fields.items():
                value_str = str(value)
                # Try as CSS selector first (if it looks like one)
                if identifier.startswith("input[") or identifier.startswith("#") or identifier.startswith(".") or identifier.startswith("[") or identifier.startswith(":") or identifier.count(" ") > 0:
                    fill_js = f"""
                    (() => {{
                        const el = document.querySelector({json.dumps(identifier)});
                        if (!el) return {{ error: 'Not found: ' + {json.dumps(identifier)} }};
                        if (el.tagName === 'SELECT') {{
                            const opt = Array.from(el.options).find(o => o.value === {json.dumps(value_str)} || o.text.includes({json.dumps(value_str)}));
                            if (opt) {{ el.value = opt.value; el.dispatchEvent(new Event('change', {{bubbles:true}})); return {{ok:true, tag:'SELECT'}}; }}
                            return {{error: 'Option not found'}};
                        }}
                        el.focus(); el.value = ''; el.select();
                        document.execCommand('insertText', false, {json.dumps(value_str)});
                        el.dispatchEvent(new Event('input', {{bubbles:true}}));
                        el.dispatchEvent(new Event('change', {{bubbles:true}}));
                        return {{ok:true, tag: el.tagName}};
                    }})()
                    """
                else:
                    fill_js = f"""
                    (() => {{
                        const s = {json.dumps(identifier)}.toLowerCase();
                        const _shadowQueryAll = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_shadowQueryAll(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_shadowQueryAll(c,sel)); return z; }};
                        let el = null;
                        for (const lbl of _shadowQueryAll(document, 'label')) {{
                            if ((lbl.textContent||'').trim().toLowerCase().includes(s)) {{
                                const fid = lbl.getAttribute('for');
                                if (fid) el = document.getElementById(fid);
                                else el = lbl.querySelector('input,textarea,select');
                                if (el) break;
                            }}
                        }}
                        if (!el) {{ for (const e of _shadowQueryAll(document, 'input,textarea')) {{ const ph = e.getAttribute('placeholder')||e.getAttribute('aria-label')||''; if(ph.toLowerCase().includes(s)){{el=e;break;}} }} }}
                        if (!el) {{ for (const e of _shadowQueryAll(document, 'input,textarea')) {{ if((e.getAttribute('name')||'').toLowerCase().includes(s)){{el=e;break;}} }} }}
                        if (!el) return {{error:'Not found: '+s}};
                        if (el.tagName === 'SELECT') {{
                            const opt = Array.from(el.options).find(o => o.value === {json.dumps(value_str)} || o.text.includes({json.dumps(value_str)}));
                            if (opt) {{ el.value = opt.value; el.dispatchEvent(new Event('change',{{bubbles:true}})); return {{ok:true,tag:'SELECT'}}; }}
                            return {{error:'Option not found'}};
                        }}
                        el.focus(); el.value = ''; el.select();
                        document.execCommand('insertText', false, {json.dumps(value_str)});
                        el.dispatchEvent(new Event('input',{{bubbles:true}}));
                        el.dispatchEvent(new Event('change',{{bubbles:true}}));
                        return {{ok:true, tag:el.tagName}};
                    }})()
                    """
                r = _parse_result(_cdp("Runtime.evaluate", {"expression": fill_js, "returnByValue": True}))
                if isinstance(r, dict) and "error" in r:
                    results.append({"field": identifier, "error": r["error"]})
                else:
                    results.append({"field": identifier, "set": True})
                _tf_mod.sleep(0.05)  # small delay between fills
            return [TextContent(type="text", text=json.dumps({"results": results, "filled": sum(1 for r in results if r.get("set")), "failed": sum(1 for r in results if "error" in r)}, indent=2))]

        elif name == "browser_create_tab":
            url = arguments.get("url", "about:blank")
            active = arguments.get("active", True)
            cdp_id = int(time.time() * 1000000) % (2**31)
            import time as _ct_tm
            cmd = {"id": cdp_id, "tabCommand": "create", "url": url, "active": active}
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

        elif name == "browser_close_tab":
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

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error in {name}: {e}")]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    import asyncio
    start_watchdog()
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
