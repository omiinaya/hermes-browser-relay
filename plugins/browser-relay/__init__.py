"""
Browser Relay Plugin — Hermes-native browser control tools.

Registers tools (browser_navigate, browser_eval, etc.) that communicate
with the CDP relay server (:9223) to control the user's browser.

Usage:
    hermes plugins enable browser-relay
    # Then restart — tools appear as: browser_navigate, browser_eval, etc.
"""

import json
import time
import base64
import urllib.request
import urllib.error
import sys as _sys
import os as _os

# Ensure project root is on sys.path for shared.py import
_plugin_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _plugin_root not in _sys.path:
    _sys.path.insert(0, _plugin_root)

# Import shared utilities
from shared import (  # noqa: E402
    js_quote,
    parse_result,
    cache_snapshot,
    get_cached_snapshot,
    invalidate_cache,
)

# Keep backward-compatible aliases for existing code
_js_quote = js_quote
_parse_result = parse_result
_cache_snapshot = cache_snapshot
_get_cached_snapshot = get_cached_snapshot
_invalidate_cache = invalidate_cache

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    MemoryProvider = object  # fallback for standalone testing

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CDP_SERVER = "http://127.0.0.1:9223"
TOOL_TIMEOUT = 60

# Tab targeting — set before CDP calls to route to a specific tab attachment
_shared_tab_id: int | None = None


def _set_tab(arguments: dict | None) -> None:
    """Extract optional tab_id from tool arguments for tab-targeted CDP routing."""
    global _shared_tab_id
    _shared_tab_id = arguments.get("tab_id") if arguments else None


def _hermes_call(func_name: str, *args, fallback: str = None) -> dict:
    """Call __hermes.<func_name>(args) via content script fast path.

    Attempts content script routing first (no CDP overhead). Falls back to
    Runtime.evaluate if content script is unavailable (e.g. page just navigated)."""
    result = _cdp("__hermes", {}, _hermes=True, _hermesFunc=func_name, _hermesArgs=list(args))
    error = result.get("error", {})
    if isinstance(error, dict) and error.get("_fallback"):
        time.sleep(0.08)
        result = _cdp("__hermes", {}, _hermes=True, _hermesFunc=func_name, _hermesArgs=list(args))
        error = result.get("error", {})
        if isinstance(error, dict) and error.get("_fallback"):
            js_args = ", ".join(json.dumps(a) for a in args)
            if fallback:
                full_js = f"(typeof __hermes !== 'undefined' && __hermes.{func_name}({js_args})) || ({fallback})"
            else:
                full_js = f"typeof __hermes !== 'undefined' && __hermes.{func_name}({js_args})"
            result = _cdp("Runtime.evaluate", {"expression": full_js, "returnByValue": True})
    return result


# ---------------------------------------------------------------------------
# CDP Relay Client
# ---------------------------------------------------------------------------


# Shared HTTP connection for keep-alive
_cdp_opener = urllib.request.build_opener()
_cdp_opener.addheaders = [("Content-Type", "application/json")]


def _cdp(method: str, params: dict | None = None, timeout: int = TOOL_TIMEOUT, **extra) -> dict:
    """Send a CDP command to the relay server. Uses shared connection for keep-alive.
    Routes to _shared_tab_id if set via _set_tab()."""
    body_dict = {"method": method, "params": params or {}, "timeout": timeout}
    for k, v in extra.items():
        body_dict[k] = v
    if _shared_tab_id is not None:
        body_dict["tabId"] = _shared_tab_id
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(f"{CDP_SERVER}/cdp", data=body, method="POST")
    try:
        with _cdp_opener.open(req, timeout=timeout + 5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else "{}"
        return {"error": f"HTTP {e.code}", "detail": json.loads(detail) if detail else {}}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}", "hint": "Is the CDP relay running on :9223?"}
    except Exception as e:
        return {"error": str(e)}


# Tool Schemas (OpenAI format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "browser_navigate",
        "description": "Navigate the browser to a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_eval",
        "description": "Evaluate JavaScript in the current page. 10x faster than screenshots for reading page state.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "browser_hover",
        "description": "Hover the mouse over an element by CSS selector. Dispatches mouseMoved to trigger mouseenter/mouseover for dropdowns and tooltips.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (e.g. '.menu-item', '#tooltip-trigger')"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_click",
        "description": "Click an element by CSS selector. Uses content script fast path — finds coordinates and dispatches mouse events in one shot.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (e.g. '#submit-btn')"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field. Selects the field first, clears it (Ctrl+A, Delete), then inserts text via content script fast path.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input"},
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_enter",
        "description": "Press the Enter key.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a screenshot of the current page as base64 PNG.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_status",
        "description": "Check CDP relay connection status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_send_cdp",
        "description": "Send an arbitrary CDP command for advanced operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "CDP method name"},
                "params": {"type": "object", "description": "CDP parameters"},
            },
            "required": ["method"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Get an accessibility-style snapshot of the current page. Returns compact info for all interactive elements (links, buttons, inputs) with coordinates so you can interact. Uses content script fast path. Auto-cached for 2s — repeat calls within that window are instant.",
        "parameters": {
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "description": "Return full page text too", "default": False},
            },
        },
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page in a direction.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
                "amount": {"type": "integer", "description": "Pixels to scroll (0 = viewport/2)", "default": 0},
            },
        },
    },
    {
        "name": "browser_wait_selector",
        "description": "Wait for a CSS selector to appear in the DOM. Polls until the element exists or timeout expires.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to wait for"},
                "timeout": {"type": "integer", "description": "Max seconds to wait", "default": 15},
                "state": {"type": "string", "enum": ["visible", "attached"], "default": "visible"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_click_text",
        "description": "Click an element by its visible text content. No CSS selector needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to search for (case-insensitive)"},
                "tag": {"type": "string", "description": "Restrict to tag (button, a, etc)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_fill_by_label",
        "description": "Find a form field by its label text, placeholder, or aria-label, then type. Handles React inputs.",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label/placeholder/aria-label to find"},
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["label", "text"],
        },
    },
    {
        "name": "browser_get_text",
        "description": "Get the rendered text of the current page. Much faster than vision.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_recent_events",
        "description": "Fetch recent browser events (navigations, requests, downloads) from the capture buffer.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max events", "default": 20},
                "mode": {"type": "string", "enum": ["nav", "all"], "default": "nav"},
            },
        },
    },
    {
        "name": "browser_key",
        "description": "Press a keyboard key (e.g. 'Escape', 'Tab', 'ArrowDown', 'Delete', 'Backspace').",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to press", "default": "Escape"},
            },
        },
    },
    {
        "name": "browser_select_option",
        "description": "Select an <option> in a <select> dropdown by visible text (case-insensitive).",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <select> element"},
                "text": {"type": "string", "description": "Visible text of the option to select"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_back",
        "description": "Navigate back to the previous page in browser history.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_screenshot_annotated",
        "description": "Take a screenshot with numbered labels overlaid on interactive elements.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_console",
        "description": "Read recent browser console output (console.log/warn/error/debug/info messages). Returns the most recent console messages captured from the page.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max messages to return (default: 20, max: 200)", "default": 20},
            },
        },
    },
    {
        "name": "browser_list_tabs",
        "description": "List all open browser tabs with their IDs, titles, URLs, and active status. Use before browser_switch_tab.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_attach_tab",
        "description": "Attach the CDP debugger to a specific tab by tabId (additive — keeps existing attachments).",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer", "description": "Tab ID to attach to"},
                "keyword": {"type": "string", "description": "URL or title keyword to search for"},
            },
        },
    },
    {
        "name": "browser_detach_tab",
        "description": "Detach the CDP debugger from a specific tab by tabId.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer", "description": "Tab ID to detach from"},
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "browser_list_attachments",
        "description": "List currently attached tab IDs with active CDP debugger connections.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch to a tab by its tab ID (from browser_list_tabs), or by URL/title keyword.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer", "description": "Tab ID from browser_list_tabs"},
                "keyword": {"type": "string", "description": "URL or title keyword to search for"},
            },
        },
    },
    {
        "name": "browser_get_cookies",
        "description": "Read all cookies for the current page via CDP. Returns name, value, domain, path, expiry.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_clear_cookies",
        "description": "Clear all cookies for the current page via CDP.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_get_downloads",
        "description": "List recent downloads captured by the extension. Returns filename, URL, state, size.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max downloads (default: 20, max: 50)", "default": 20},
            },
        },
    },
    {
        "name": "browser_block_patterns",
        "description": 'Block or allow URL patterns matching specified strings. Uses Chrome\'s declarativeNetRequest API. Pass patterns=["*.css","*analytics*"] to block, or patterns=[] to clear.',
        "parameters": {
            "type": "object",
            "properties": {
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URL patterns to block. Empty array to clear all. Supports *.css, *analytics*, /path/*",
                },
            },
            "required": ["patterns"],
        },
    },
    {
        "name": "browser_refresh",
        "description": "Reload the current page. Optionally bypass cache with hardReload=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "hard_reload": {"type": "boolean", "description": "Bypass cache (default: false)", "default": False},
            },
        },
    },
    {
        "name": "browser_forward",
        "description": "Navigate forward in browser history.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_get_title",
        "description": "Get the current page title.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_find",
        "description": "Search the page for elements matching text. Returns elements with coordinates, tag, text, ref IDs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for (case-insensitive, partial match)"},
                "selector": {"type": "string", "description": "Optional CSS selector to narrow search scope"},
                "max_results": {"type": "integer", "description": "Maximum results (default: 20)", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browser_pdf",
        "description": "Save the current page as a PDF document. Returns base64-encoded PDF data. Uses CDP Page.printToPDF with A4 paper size, background colors, and system fonts.",
        "parameters": {
            "type": "object",
            "properties": {
                "landscape": {"type": "boolean", "description": "Landscape orientation (default: false)", "default": False},
                "scale": {"type": "number", "description": "Scale factor from 0.1 to 2.0 (default: 1.0)", "default": 1.0},
            },
        },
    },
    {
        "name": "browser_wait_for_navigation",
        "description": "Wait for a page navigation (tab.navigated event) to complete. Polls the relay's event buffer until a fresh navigation is detected or timeout. Returns the URL, title, and tabId of the new page. Use this after browser_click or browser_navigate when you need the page to finish loading before inspecting it.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer", "description": "Tab ID to wait for navigation on (optional — defaults to active tab)"},
                "timeout": {"type": "integer", "description": "Max seconds to wait (default: 30)", "default": 30},
            },
        },
    },
    {
        "name": "browser_network_requests",
        "description": "Query captured network requests from the relay's event buffer. Returns webRequest events (beforeRequest, beforeSendHeaders, headersReceived, completed, error) filtered by type. Useful for checking if a specific request completed, inspecting response headers, or debugging network issues.",
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": "Filter by event type: 'completed', 'error', 'beforeRequest', 'beforeSendHeaders', 'headersReceived' (optional — returns all types)",
                },
                "since": {
                    "type": "number",
                    "description": "Only include events after this timestamp (epoch ms). Use the _received field from a prior call for polling",
                    "default": 0,
                },
                "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 100)", "default": 20},
            },
        },
    },
    {
        "name": "browser_response_body",
        "description": "Query captured response bodies from the relay's event buffer. The extension captures response bodies for JSON/PDF responses and matched URL patterns. Returns the URL, mime type, and body content for each captured response.",
        "parameters": {
            "type": "object",
            "properties": {
                "since": {"type": "number", "description": "Only include bodies captured after this timestamp (epoch ms)", "default": 0},
                "limit": {"type": "integer", "description": "Max bodies to return (default: 5, max: 20)", "default": 5},
                "include_body": {"type": "boolean", "description": "Whether to include full body content (default: false — only metadata)", "default": False},
            },
        },
    },
    {
        "name": "browser_set_cookie",
        "description": "Set a browser cookie via CDP Network.setCookie. Useful for auth workflows, session injection, or bypassing login screens. Returns success/failure and cookie details.",
        "parameters": {
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
    {
        "name": "browser_dismiss_dialog",
        "description": "Dismiss a JavaScript dialog (alert/confirm/prompt) by accepting or cancelling it. JavaScript dialogs block all CDP commands until handled — use this tool when screenshots, navigation, or eval commands hang because a dialog is open on the page.",
        "parameters": {
            "type": "object",
            "properties": {
                "accept": {"type": "boolean", "description": "True to accept/confirm, False to cancel/dismiss (default: true)"},
                "prompt_text": {"type": "string", "description": "Text to provide for prompt() dialogs (ignored for alert/confirm)"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_upload_file",
        "description": "Upload a file by setting the value of an <input type=file> element. Uses CDP DOM.setFileInputFiles to set the file path on the file input without requiring a file picker dialog. \n\nIMPORTANT: File paths must exist on the machine where Chrome is running (not the agent's machine).",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <input type=file> element"},
                "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute file path(s) to upload (on the Chrome host machine)"},
            },
            "required": ["selector", "file_paths"],
        },
    },
    {
        "name": "browser_form_fill",
        "description": "Fill multiple form fields at once from a dictionary of {field_identifier: value} pairs. Field identifiers can be label text, placeholder, name, or CSS selector. More efficient than calling browser_fill_by_label repeatedly for multi-field forms.",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": 'Dict of field identifier (label/placeholder/name) to value. E.g. {"Email": "user@example.com", "Password": "secret123", "input[name=remember]": "true"}',
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["fields"],
        },
    },
    {
        "name": "browser_create_tab",
        "description": "Create a new browser tab with an optional URL. Returns the new tab's ID, title, and URL. Uses chrome.tabs.create via the extension.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open (default: about:blank)", "default": "about:blank"},
                "active": {"type": "boolean", "description": "Whether the tab should be active/focused (default: true)", "default": True},
            },
        },
    },
    {
        "name": "browser_close_tab",
        "description": "Close a browser tab by its tab ID. Uses chrome.tabs.remove via the extension. Also cleans up any CDP attachments for the closed tab.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer", "description": "Tab ID to close (from browser_list_tabs)"},
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "browser_do",
        "description": "Execute multiple browser actions sequentially in ONE tool call. Eliminates LLM thinking time between steps. Supported actions: navigate, navigate_wait (navigate + auto-ready + optional element wait), click, click_text, hover, type, enter, key, scroll, snapshot, screenshot, get_text, fill_by_label, wait_selector, back, forward, eval, wait.",
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of action objects, each with an 'action' field.",
                },
            },
            "required": ["actions"],
        },
    },
    {
        "name": "browser_navigate_wait",
        "description": "Navigate to a URL and wait until the page is fully loaded (readyState=complete).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to navigate to"},
                "timeout": {"type": "integer", "description": "Max seconds to wait (default: 15, max: 60)", "default": 15},
            },
            "required": ["url"],
        },
    },
]


# ---------------------------------------------------------------------------
# MemoryProvider Implementation
# ---------------------------------------------------------------------------


class BrowserRelayProvider(MemoryProvider if MemoryProvider is not object else object):
    """Hermes memory provider that exposes browser control tools."""

    @property
    def name(self) -> str:
        return "browser-relay"

    def is_available(self) -> bool:
        """Check if CDP relay is reachable (lightweight)."""
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", 9223))
            s.close()
            return True
        except Exception:
            return False

    def initialize(self, session_id: str = "", **kwargs) -> None:
        pass

    def get_tool_schemas(self) -> list[dict]:
        # Inject tab_id into all tools for multi-tab targeting
        TAB_ID_PARAM = {"tab_id": {"type": "integer", "description": "Optional: target a specific tab attachment by ID (from browser_list_attachments)"}}
        schemas = []
        for s in TOOL_SCHEMAS:
            params = s.get("parameters", {})
            props = params.setdefault("properties", {})
            if "tab_id" not in props:
                props["tab_id"] = TAB_ID_PARAM["tab_id"]
            schemas.append(s)
        return schemas

    def handle_tool_call(self, name: str, args: dict) -> str:
        try:
            _set_tab(args)
            return self._dispatch(name, args)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "browser_navigate":
            result = _cdp("Page.navigate", {"url": args["url"]})
            _invalidate_cache()
            if "error" in result:
                return json.dumps({"error": result["error"]})
            return json.dumps(result.get("result", {}), indent=2)

        elif name == "browser_eval":
            result = _cdp("Runtime.evaluate", {"expression": args["expression"], "returnByValue": True})
            return str(_parse_result(result))

        elif name == "browser_hover":
            selector = args["selector"]
            result = _hermes_call(
                "hover",
                selector,
                fallback=f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                const r = el.getBoundingClientRect();
                el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true, cancelable: true, clientX: r.x + r.width/2, clientY: r.y + r.height/2 }}));
                el.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true, cancelable: true, clientX: r.x + r.width/2, clientY: r.y + r.height/2 }}));
                el.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, cancelable: true, clientX: r.x + r.width/2, clientY: r.y + r.height/2 }}));
                return {{ ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
            }})()
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return f"Error: {value['error']}"
            return f"Hovered '{value.get('text', '?')}' ({value.get('tag', '?')})"

        elif name == "browser_click":
            selector = args["selector"]
            result = _hermes_call(
                "click",
                selector,
                fallback=f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                const r = el.getBoundingClientRect();
                const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
                el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                return {{ ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
            }})()
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return f"Error: {value['error']}"
            return f"Clicked '{value.get('text', '?')}' ({value.get('tag', '?')})"

        elif name == "browser_type":
            selector = args["selector"]
            text = args["text"]
            result = _hermes_call(
                "type",
                selector,
                text,
                fallback=f"""
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
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return f"Error: {value['error']}"
            return f"Typed '{text}' into {selector}"

        elif name == "browser_enter":
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "windowsVirtualKeyCode": 13})
            _cdp("Input.dispatchKeyEvent", {"type": "char", "text": "\r"})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "windowsVirtualKeyCode": 13})
            return "Pressed Enter"

        elif name == "browser_screenshot":
            result = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            if "error" in result:
                return json.dumps({"error": result["error"]})
            data = result.get("result", {}).get("data", "")
            if data:
                return json.dumps({"base64_png": data[:100] + "..." if len(data) > 100 else data, "size": len(data)})
            return "No screenshot data"

        elif name == "browser_status":
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/status")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.dumps(json.loads(resp.read().decode()), indent=2)
            except Exception as e:
                return f"CDP relay unreachable: {e}"

        elif name == "browser_send_cdp":
            result = _cdp(args["method"], args.get("params", {}))
            return json.dumps(result, indent=2)

        elif name == "browser_snapshot":
            full = args.get("full", False)
            cached = _get_cached_snapshot(_shared_tab_id, full)
            if cached is not None:
                snap = cached
            else:
                result = _hermes_call(
                    "snapshot",
                    full,
                    fallback=f"""
            (() => {{
                const selectors = {json.dumps(["a[href]", "button", "input:not([type=hidden])", "select", "textarea", "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])", "summary", "details", "[contenteditable=true]"])};
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
                    items.push({{
                        ref: 'e' + idx,
                        tag: tag + (type ? '[type=' + type + ']' : ''),
                        text: txt.substring(0, 80),
                        x: Math.round(r.x + r.width/2),
                        y: Math.round(r.y + r.height/2),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.top < window.innerHeight && r.bottom > 0,
                    }});
                }});
                const info = {{ title: document.title, url: location.href, viewport: {{ w: window.innerWidth, h: window.innerHeight }}, total: items.length, visible: items.filter(i => i.visible).length }};
                let textContent = '';
                if ({json.dumps(full)} === true) {{
                    textContent = (document.body ? document.body.innerText : '').substring(0, 30000);
                }}
                return {{ info, items, textContent }};
            }})()
            """,
                )
                snap = _parse_result(result)
                if isinstance(snap, dict) and "error" in snap:
                    return f"Snapshot error: {snap['error']}"
                _cache_snapshot(_shared_tab_id, full, snap)
            info = snap.get("info", {})
            items = snap.get("items", [])
            lines = [
                f"📄 {info.get('title', '?')}",
                f"🌐 {info.get('url', '?')}",
                f"📐 {info.get('viewport', {}).get('w', '?')}×{info.get('viewport', {}).get('h', '?')} — {info.get('visible', '?')} visible of {info.get('total', '?')} elements",
                "",
            ]
            for it in items[:60]:
                vis = "⬆ " if not it["visible"] else ""
                label = it["text"] or it.get("placeholder", "") or it.get("aria_label", "") or it["tag"]
                lines.append(f'  [{it["ref"]}] {vis}{it["tag"]} "{label[:70]}" @({it["x"]},{it["y"]})')
            if len(items) > 60:
                lines.append(f"  ... and {len(items) - 60} more")
            if full:
                text_content = snap.get("textContent", "")
                lines.extend(["", "--- Page Text ---", str(text_content)[:20000]])
            return "\n".join(lines)

        elif name == "browser_scroll":
            direction = args.get("direction", "down")
            amount = args.get("amount", 0)
            result = _hermes_call(
                "scrollBy",
                direction,
                amount,
                fallback=f"""
            (() => {{
                const dir = {json.dumps(direction)};
                let amt = {amount or 0};
                if (!amt) {{ amt = Math.round(window.innerHeight / 2); }}
                const dx = dir === 'right' ? amt : (dir === 'left' ? -amt : 0);
                const dy = dir === 'down' ? amt : (dir === 'up' ? -amt : 0);
                window.scrollBy({{ left: dx, top: dy, behavior: 'instant' }});
                return {{ ok: true, direction: dir, amount: amt }};
            }})()
            """,
            )
            return f"Scrolled {direction} by {abs(amount or 400)}px"

        elif name == "browser_wait_selector":
            selector = args["selector"]
            timeout = min(args.get("timeout", 15), 60)
            state = args.get("state", "visible")
            result = _hermes_call(
                "waitSelector",
                selector,
                timeout,
                state,
                fallback=f"""
            (() => {{
                const deadline = Date.now() + {timeout * 1000};
                while (Date.now() < deadline) {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (el) {{
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return {{ ok: true }};
                    }}
                }}
                return {{ error: 'Timeout' }};
            }})()
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and value.get("ok"):
                return f"Selector '{selector}' matched"
            return f"Timed out ({timeout}s) waiting for '{selector}'"

        elif name == "browser_click_text":
            text_query = args["text"]
            tag_filter = args.get("tag", "")
            result = _hermes_call(
                "clickText",
                text_query,
                tag_filter,
                fallback=f"""
            (() => {{
                const q = {json.dumps(text_query)}.toLowerCase();
                const tag = {json.dumps(tag_filter)};
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
                    if (txt.includes(q)) {{
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
                        el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                        el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                        el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
                        return {{ ok: true, x: cx, y: cy, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
                    }}
                }}
                return {{ error: 'No element found with text: ' + q }};
            }})()
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return f"Error: {value['error']}"
            return f"Clicked '{value.get('text', '?')}' ({value.get('tag', '?')}) by text"

        elif name == "browser_fill_by_label":
            label = args["label"]
            text = args["text"]
            result = _hermes_call(
                "fillByLabel",
                label,
                text,
                fallback=f"""
            (() => {{
                const search = {json.dumps(label)}.toLowerCase();
                const _shadowQueryAll = (root, sel) => {{
                    let r = [];
                    try {{ r = Array.from(root.querySelectorAll(sel)); }} catch(e) {{}}
                    if (root.shadowRoot) r = r.concat(_shadowQueryAll(root.shadowRoot, sel));
                    for (const c of root.children || []) r = r.concat(_shadowQueryAll(c, sel));
                    return r;
                }};
                let input = null;
                for (const lbl of _shadowQueryAll(document, 'label')) {{
                    if ((lbl.textContent||'').trim().toLowerCase().includes(search)) {{
                        const forId = lbl.getAttribute('for');
                        if (forId) input = document.getElementById(forId) || document.querySelector('input[name=' + JSON.stringify(forId) + ']');
                        else input = lbl.querySelector('input, textarea, select');
                        if (input) break;
                    }}
                }}
                if (!input) {{
                    for (const el of _shadowQueryAll(document, 'input, textarea')) {{
                        const ph = el.getAttribute('placeholder') || el.getAttribute('aria-label') || '';
                        if (ph.toLowerCase().includes(search)) {{ input = el; break; }}
                    }}
                }}
                if (!input) {{
                    for (const el of _shadowQueryAll(document, 'input, textarea')) {{
                        if ((el.getAttribute('name') || '').toLowerCase().includes(search)) {{ input = el; break; }}
                    }}
                }}
                if (!input) return {{ error: 'Could not find input for: ' + search }};
                input.focus();
                input.value = '';
                input.select();
                document.execCommand('insertText', false, {json.dumps(text)});
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ ok: true, tag: input.tagName, type: input.type || '' }};
            }})()
            """,
            )
            fill_result = _parse_result(result)
            if isinstance(fill_result, dict) and "error" in fill_result:
                return f"Error: {fill_result['error']}"
            return f"Filled '{label}' with '{text}'"

        elif name == "browser_get_text":
            result = _hermes_call("getText", fallback="document.body ? document.body.innerText.substring(0, 50000) : ''")
            return str(_parse_result(result))

        elif name == "browser_recent_events":
            limit = min(args.get("limit", 20), 200)
            mode = args.get("mode", "nav")
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/recent?limit={limit}&mode={mode}")
                with urllib.request.urlopen(req, timeout=5) as r:
                    result = json.loads(r.read().decode())
                ev_count = result.get("count", 0)
                events = result.get("events", [])
                lines = [f"📊 {ev_count} recent events:", ""]
                for e in events:
                    etype = e.get("type", "?")
                    if etype == "tab":
                        lines.append(f"  🏁 NAV: {e.get('url', '')[:120]}")
                    elif etype == "download":
                        lines.append(f"  📥 DOWNLOAD: {e.get('filename', e.get('url', ''))[:120]}")
                    elif etype == "webrequest" and e.get("event") in ("completed", "error"):
                        lines.append(f"  🌐 {e['event']}: {e.get('method', '?')} {e.get('statusCode', '?')} {e.get('url', '')[:100]}")
                    elif etype == "response_body":
                        lines.append(f"  📄 BODY: {e.get('url', '')[:100]}")
                if len(lines) <= 2:
                    lines.append("  (no events yet)")
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_key":
            key = args.get("key", "Escape")
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
            return f"Pressed {key}"

        elif name == "browser_back":
            _hermes_call("historyBack", fallback="window.history.back()")
            _invalidate_cache()
            return "Navigated back"

        elif name == "browser_console":
            limit = min(args.get("limit", 20), 200)
            # Ensure Runtime domain is enabled (safe to send repeatedly)
            _cdp("Runtime.enable", timeout=5)
            # Query capture buffer for console events
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/recent?limit={limit * 2}&mode=all")
                with urllib.request.urlopen(req, timeout=5) as r:
                    result = json.loads(r.read().decode())
            except Exception as e:
                return json.dumps({"error": str(e)})
            events = result.get("events", [])
            # Filter for Runtime.consoleAPICalled events
            console_events = [e for e in events if e.get("type") == "event" and e.get("method") == "Runtime.consoleAPICalled"]
            console_events = console_events[:limit]
            if not console_events:
                return "No console messages captured yet. Try navigating to a page and running JavaScript that calls console.log()."
            lines = [f"📋 {len(console_events)} recent console messages:", ""]
            for e in console_events:
                params = e.get("params", {})
                level = params.get("type", "log")
                args_list = params.get("args", [])
                text_parts = []
                for arg in args_list:
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
            return "\n".join(lines)

        elif name == "browser_list_tabs":
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/tabs")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                if "error" in result:
                    return json.dumps({"error": result["error"]})
                tabs = result.get("tabs", [])
                if not tabs:
                    return "No tabs found."
                lines = [f"📑 {len(tabs)} open tabs:", ""]
                for t in tabs:
                    active = "🟢" if t.get("active") else "  "
                    title = (t.get("title", "") or "").strip()[:60]
                    url = (t.get("url", "") or "").strip()[:80]
                    lines.append(f"  {active} [{t['id']}] {title}")
                    lines.append(f"              {url}")
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": f"Error listing tabs: {e}"})

        elif name == "browser_attach_tab":
            tab_id = args.get("tab_id")
            keyword = args.get("keyword")
            if not tab_id and not keyword:
                return "Provide either tab_id or keyword to find the tab."
            if keyword and not tab_id:
                try:
                    req = urllib.request.Request(f"{CDP_SERVER}/tabs")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = json.loads(resp.read().decode())
                    kw = keyword.lower()
                    matches = [t for t in result.get("tabs", []) if kw in (t.get("title", "") or "").lower() or kw in (t.get("url", "") or "").lower()]
                    if not matches:
                        return f"No tab found matching '{keyword}'"
                    tab_id = matches[0]["id"]
                except Exception as e:
                    return json.dumps({"error": str(e)})
            body = json.dumps({"tabId": tab_id}).encode()
            req = urllib.request.Request(f"{CDP_SERVER}/tabs/attach", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                if result.get("success"):
                    return f"🔗 Attached to tab {tab_id} ({result.get('attachmentCount', '?')} total)"
                return json.dumps(result)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_detach_tab":
            tab_id = args.get("tab_id")
            if not tab_id:
                return "Provide tab_id to detach."
            body = json.dumps({"tabId": tab_id}).encode()
            req = urllib.request.Request(f"{CDP_SERVER}/tabs/detach", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                if result.get("success"):
                    return f"🔌 Detached from tab {tab_id} ({result.get('attachmentCount', '?')} remaining)"
                return json.dumps(result)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_list_attachments":
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/tabs/attachments")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    result = json.loads(resp.read().decode())
                attached = result.get("attachments", [])
                if not attached:
                    return "No tabs currently attached."
                lines = [f"🔗 {len(attached)} attached tab(s):", ""]
                for a in attached:
                    lines.append(f"  [{a['tabId']}]")
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_switch_tab":
            tab_id = args.get("tab_id")
            keyword = args.get("keyword")
            if not tab_id and not keyword:
                return "Provide either tab_id or keyword to find the tab."

            # If keyword given, find matching tab first
            if keyword and not tab_id:
                try:
                    req = urllib.request.Request(f"{CDP_SERVER}/tabs")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = json.loads(resp.read().decode())
                    if "error" in result:
                        return json.dumps({"error": result["error"]})
                    kw = keyword.lower()
                    matches = [t for t in result.get("tabs", []) if kw in (t.get("title", "") or "").lower() or kw in (t.get("url", "") or "").lower()]
                    if not matches:
                        return f"No tab found matching '{keyword}'"
                    tab_id = matches[0]["id"]
                    if len(matches) > 1:
                        return f"Multiple tabs match '{keyword}'. Use browser_list_tabs and specify tab_id:\n" + "\n".join(
                            f"  [{t['id']}] {t.get('title', '')[:60]}" for t in matches
                        )
                except Exception as e:
                    return json.dumps({"error": f"Error searching tabs: {e}"})

            body = json.dumps({"tabId": tab_id}).encode()
            switch_req = urllib.request.Request(f"{CDP_SERVER}/tabs/switch", data=body, method="POST")
            switch_req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(switch_req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                if result.get("success"):
                    return f"✅ Switched to tab {tab_id}"
                return f"Switch failed: {result.get('error', 'unknown')}"
            except Exception as e:
                return json.dumps({"error": f"Error switching tab: {e}"})

        elif name == "browser_get_cookies":
            result = _cdp("Network.getCookies")
            if "error" in result:
                return json.dumps({"error": result["error"]})
            cookies = result.get("result", {}).get("cookies", [])
            if not cookies:
                return "No cookies for the current page."
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
            return "\n".join(lines)

        elif name == "browser_clear_cookies":
            result = _cdp("Network.clearBrowserCookies")
            if "error" in result:
                return json.dumps({"error": result["error"]})
            return "🧹 All cookies cleared."

        elif name == "browser_get_downloads":
            limit = min(args.get("limit", 20), 50)
            try:
                req = urllib.request.Request(f"{CDP_SERVER}/recent?limit={limit * 2}&mode=all")
                with urllib.request.urlopen(req, timeout=5) as r:
                    dl_result = json.loads(r.read().decode())
            except Exception as e:
                return json.dumps({"error": str(e)})
            events = dl_result.get("events", [])
            dl_events = [e for e in events if e.get("type") == "download"]
            dl_events = dl_events[:limit]
            if not dl_events:
                return "No recent downloads captured."
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
            return "\n".join(lines)

        elif name == "browser_refresh":
            hard = args.get("hard_reload", False)
            result = _cdp("Page.reload", {"ignoreCache": hard})
            _invalidate_cache()
            if "error" in result:
                return json.dumps({"error": result["error"]})
            return "🔄 Page reloaded" + (" (hard)" if hard else "")

        elif name == "browser_forward":
            _hermes_call("historyForward", fallback="window.history.forward()")
            _invalidate_cache()
            return "Navigated forward"

        elif name == "browser_get_title":
            result = _hermes_call("docInfo", fallback="({ title: document.title, url: location.href })")
            value = _parse_result(result)
            if isinstance(value, dict):
                return f"📄 {value.get('title', '?')}\n🌐 {value.get('url', '?')}"
            return f"📄 {value}"

        elif name == "browser_find":
            query = args["query"]
            result = _hermes_call(
                "find",
                query,
                fallback=f"""
            (() => {{
                const q = {json.dumps(query)}.toLowerCase();
                const selectors = {json.dumps(["a[href]", "button", "input:not([type=hidden])", "select", "textarea", "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])", "summary", "details", "[contenteditable=true]"])};
                const _sq = (r, sel) => {{ let z=[]; try{{z=Array.from(r.querySelectorAll(sel))}}catch(e){{}} if(r.shadowRoot) z=z.concat(_sq(r.shadowRoot,sel)); for(const c of r.children||[]) z=z.concat(_sq(c,sel)); return z; }};
                const all = _sq(document, selectors.join(','));
                const seen = new Set();
                const results = [];
                let idx = 0;
                all.forEach(el => {{
                    if (seen.has(el)) return;
                    seen.add(el);
                    const txt = (el.textContent||'').trim();
                    const aria = el.getAttribute('aria-label') || '';
                    const placeholder = el.placeholder || '';
                    const name = el.getAttribute('name') || '';
                    const val = el.value || '';
                    const textSources = [txt, aria, placeholder, name, val];
                    let matched = false;
                    for (const s of textSources) {{ if (s.toLowerCase().includes(q)) {{ matched = true; break; }} }}
                    if (!matched) return;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return;
                    idx++;
                    results.push({{ ref: 'e'+idx, tag: el.tagName.toLowerCase(), text: txt.substring(0,120), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), w: Math.round(r.width), h: Math.round(r.height), visible: r.top < window.innerHeight && r.bottom > 0 }});
                }});
                return {{ count: results.length, results: results }};
            }})()
            """,
            )
            find_result = _parse_result(result)
            if isinstance(find_result, dict) and "error" in find_result:
                return json.dumps({"error": find_result["error"]})
            count = find_result.get("count", 0)
            items = find_result.get("results", [])
            if count == 0:
                return f"No elements found matching '{query}'"
            lines = [f"🔍 {count} element(s) matching '{query}':", ""]
            for it in items:
                vis = "" if it["visible"] else "⬆"
                lines.append(f'  [{it["ref"]}] {vis}{it["tag"]} "{it["text"][:70]}" @({it["x"]},{it["y"]})')
            return "\n".join(lines)

        elif name == "browser_block_patterns":
            patterns = args.get("patterns", [])
            if not isinstance(patterns, list):
                return json.dumps({"error": "patterns must be an array of strings"})
            body = json.dumps({"patterns": patterns}).encode()
            req = urllib.request.Request(f"{CDP_SERVER}/block/patterns", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                if result.get("success"):
                    if patterns:
                        return f"🚫 Blocking {len(patterns)} patterns: {', '.join(patterns[:10])}"
                    return "✅ All blocking patterns cleared"
                return json.dumps(result)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_select_option":
            selector = args["selector"]
            text = args["text"]
            result = _hermes_call(
                "selectOption",
                selector,
                text,
                fallback=f"""
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
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return f"Error: {value['error']}"
            return f"Selected option '{value.get('selected', '?')}' (value={value.get('value', '?')}, index={value.get('index', '?')}) in '{selector}'"

        elif name == "browser_screenshot_annotated":
            # Get interactive elements via __hermes.snapshot (avoids inline JS through CDP)
            annotate_result = _hermes_call(
                "snapshot",
                False,
                fallback=f"""
            (() => {{
                const selectors = {json.dumps(["a[href]", "button", "input:not([type=hidden])", "select", "textarea", "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])", "summary", "details", "[contenteditable=true]"])};
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
                    const txt = (el.textContent||'').trim().substring(0, 100);
                    const placeholder = el.placeholder || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const name = el.getAttribute('name') || '';
                    items.push({{ ref: 'e' + idx, tag: el.tagName.toLowerCase(), text: txt, placeholder: placeholder, aria_label: aria, name: name, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), w: Math.round(r.width), h: Math.round(r.height), visible: r.top < window.innerHeight && r.bottom > 0 }});
                }});
                return {{ info: {{ total: items.length, visible: items.filter(i => i.visible).length }}, items: items }};
            }})()
            """,
            )
            snap = _parse_result(annotate_result)
            all_items = snap.get("items", [])
            annotations = [it for it in all_items if it.get("visible") and it.get("x")][:50]
            shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            data = shot.get("result", {}).get("data", "")
            lines = ["📸 Annotated screenshot — interactive elements:", ""]
            for a in annotations if isinstance(annotations, list) else []:
                lines.append(f'  [{a["ref"]}] "{a["text"][:50]}" @({a["x"]},{a["y"]})')
            if data:
                import os

                img_dir = os.path.expanduser("~/.hermes/image_cache")
                os.makedirs(img_dir, exist_ok=True)
                img_path = os.path.join(img_dir, f"annotated_{int(__import__('time').time())}.png")
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(data))
                return "MEDIA:" + img_path + "\n" + "\n".join(lines)
            return "\n".join(lines)

        elif name == "browser_pdf":
            landscape = args.get("landscape", False)
            scale = args.get("scale", 1.0)
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
                return json.dumps({"error": result["error"]})
            data = result.get("result", {}).get("data", "")
            if data:
                pdf_bytes = base64.b64decode(data)
                return json.dumps(
                    {
                        "base64_pdf": data[:100] + "..." if len(data) > 100 else data,
                        "size": len(pdf_bytes),
                        "mime": "application/pdf",
                    },
                    indent=2,
                )
            return "No PDF data returned"

        elif name == "browser_wait_for_navigation":
            import time as _time_module

            tab_id = args.get("tab_id")
            timeout = min(args.get("timeout", 30), 120)
            deadline = _time_module.time() + timeout
            start_ts = _time_module.time() * 1000
            while _time_module.time() < deadline:
                try:
                    req = urllib.request.Request(f"{CDP_SERVER}/recent?mode=nav&since={start_ts}&limit=5")
                    with urllib.request.urlopen(req, timeout=5) as r:
                        result = json.loads(r.read().decode())
                    events = result.get("events", [])
                    for e in events:
                        if e.get("type") == "tab" and e.get("event") == "navigated":
                            if tab_id is None or e.get("tabId") == tab_id:
                                return json.dumps(
                                    {
                                        "navigated": True,
                                        "url": e.get("url", ""),
                                        "title": e.get("title", ""),
                                        "tabId": e.get("tabId"),
                                        "elapsed": round((_time_module.time() * 1000 - start_ts) / 1000, 2),
                                    },
                                    indent=2,
                                )
                except Exception:
                    pass
                _time_module.sleep(0.5)
            return json.dumps(
                {
                    "navigated": False,
                    "error": f"Navigation timeout after {timeout}s",
                    "elapsed": timeout,
                },
                indent=2,
            )

        elif name == "browser_network_requests":
            ev_filter = args.get("event", "")
            since = args.get("since", 0)
            limit = min(args.get("limit", 20), 100)
            try:
                qs = f"{CDP_SERVER}/recent?mode=all&type=webrequest&limit={limit}"
                if since:
                    qs += f"&since={since}"
                req = urllib.request.Request(qs)
                with urllib.request.urlopen(req, timeout=5) as r:
                    result = json.loads(r.read().decode())
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
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_response_body":
            since = args.get("since", 0)
            limit = min(args.get("limit", 5), 20)
            try:
                qs = f"{CDP_SERVER}/recent?mode=all&type=response_body&limit={limit}"
                if since:
                    qs += f"&since={since}"
                req = urllib.request.Request(qs)
                with urllib.request.urlopen(req, timeout=5) as r:
                    result = json.loads(r.read().decode())
                events = result.get("events", [])
                include_body = args.get("include_body", False)
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
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_set_cookie":
            params = {
                "name": args["name"],
                "value": args["value"],
            }
            if "url" in args:
                params["url"] = args["url"]
            if "domain" in args:
                params["domain"] = args["domain"]
            if "path" in args:
                params["path"] = args["path"]
            if args.get("secure"):
                params["secure"] = True
            if args.get("httpOnly"):
                params["httpOnly"] = True
            if "sameSite" in args:
                params["sameSite"] = args["sameSite"]
            result = _cdp("Network.setCookie", params)
            if "error" in result:
                return json.dumps({"error": result["error"]})
            r = result.get("result", {})
            success = r.get("success", False)
            return json.dumps(
                {
                    "success": success,
                    "cookie": params.get("name"),
                    "domain": params.get("domain", params.get("url", "any")),
                },
                indent=2,
            )

        elif name == "browser_upload_file":
            selector = args["selector"]
            file_paths = args["file_paths"]
            if not isinstance(file_paths, list):
                file_paths = [file_paths]
            # Validate the file input exists via JS
            find_js = f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'File input not found: ' + {json.dumps(selector)} }};
                if (el.tagName !== 'INPUT' || (el.type !== 'file' && el.getAttribute('type') !== 'file'))
                    return {{ error: 'Not a file input: ' + el.tagName + ' type=' + (el.type || 'none') }};
                return {{ found: true }};
            }})()
            """
            check = _parse_result(_cdp("Runtime.evaluate", {"expression": find_js, "returnByValue": True}))
            if isinstance(check, dict) and "error" in check:
                return f"Error: {check['error']}"
            # Get the backend node ID
            doc_result = _cdp("DOM.getDocument", {"depth": 0})
            if "error" in doc_result:
                return f"Error getting DOM: {doc_result['error']}"
            node_id_result = _cdp("DOM.querySelector", {"nodeId": doc_result["result"]["root"]["nodeId"], "selector": selector})
            if "error" in node_id_result:
                return f"Error finding node: {node_id_result['error']}"
            node_id = node_id_result.get("result", {}).get("nodeId")
            if not node_id:
                return f"Error: Could not find node for selector: {selector}"
            files_result = _cdp("DOM.setFileInputFiles", {"nodeId": node_id, "files": file_paths})
            if "error" in files_result:
                return f"Error setting files: {files_result['error']}"
            return json.dumps({"success": True, "files": file_paths}, indent=2)

        elif name == "browser_dismiss_dialog":
            accept = args.get("accept", True)
            params = {"accept": accept}
            if "prompt_text" in args:
                params["promptText"] = args["prompt_text"]
            result = _cdp("Page.handleJavaScriptDialog", params)
            if "error" in result:
                return f"Error dismissing dialog: {result['error']}"
            return json.dumps({"accepted": accept, "success": True}, indent=2)

        elif name == "browser_form_fill":
            fields = args["fields"]
            result = _hermes_call(
                "formFill",
                fields,
                fallback=f"""
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
                        if (!el) {{
                            var allInp = _shadowQueryAll(document, 'input,textarea');
                            for (var pi = 0; pi < allInp.length; pi++) {{
                                var ph = allInp[pi].getAttribute('placeholder') || allInp[pi].getAttribute('aria-label') || '';
                                if (ph.toLowerCase().indexOf(s) !== -1) {{ el = allInp[pi]; break; }}
                            }}
                        }}
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
            """,
            )
            value = _parse_result(result)
            if isinstance(value, dict) and "error" in value:
                return json.dumps(value, indent=2)
            if isinstance(value, dict):
                return json.dumps(value, indent=2)
            return str(value)

        elif name == "browser_create_tab":
            url = args.get("url", "about:blank")
            active = args.get("active", True)
            try:
                req = urllib.request.Request(
                    f"{CDP_SERVER}/tabs/create",
                    data=json.dumps({"url": url, "active": active}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                return json.dumps(result, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_close_tab":
            tab_id = args["tab_id"]
            try:
                req = urllib.request.Request(
                    f"{CDP_SERVER}/tabs/close",
                    data=json.dumps({"tabId": tab_id}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                return json.dumps(result, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_do":
            actions = args.get("actions", [])
            if not actions or not isinstance(actions, list):
                return json.dumps({"error": "actions must be a non-empty array"})
            results = []
            for act in actions:
                action = act.get("action", "")
                try:
                    if action == "navigate":
                        _cdp("Page.navigate", {"url": act["url"]})
                        results.append(f"Navigated to {act.get('url', '?')}")
                    elif action == "navigate_wait":
                        url = act["url"]
                        to = min(act.get("timeout", 15), 60)
                        _cdp("Page.navigate", {"url": url})
                        import time as _nwt

                        deadline = _nwt.time() + to
                        title = "?"
                        while _nwt.time() < deadline:
                            ready = _parse_result(_hermes_call("readyState", fallback="({ readyState: document.readyState, title: document.title })"))
                            if isinstance(ready, dict):
                                title = ready.get("title", "?")
                                if ready.get("readyState") == "complete":
                                    break
                            _nwt.sleep(0.3)
                        wait_for = act.get("wait_for")
                        if wait_for:
                            ws_v = _parse_result(_hermes_call("waitSelector", wait_for, to, "visible"))
                            if isinstance(ws_v, dict) and ws_v.get("ok"):
                                results.append(f"Navigate-wait: '{title}' loaded, '{wait_for}' visible")
                            else:
                                results.append(f"Navigate-wait: '{title}' loaded, waited for '{wait_for}' (timeout)")
                        else:
                            results.append(f"Navigate-wait: '{title}' loaded")
                    elif action == "wait_selector":
                        sel = act["selector"]
                        to = min(act.get("timeout", 10), 60)
                        ws_r = _hermes_call(
                            "waitSelector",
                            sel,
                            to,
                            "visible",
                            fallback=f"(typeof __hermes !== 'undefined' && __hermes.waitSelector({json.dumps(sel)}, {to})) || (() => {{ const d=Date.now()+{to * 1000}; while(Date.now()<d){{ const e=document.querySelector({json.dumps(sel)}); if(e&&e.offsetWidth>0) return {{ok:true}}; }} return {{error:'timeout'}}; }})()",
                        )
                        r = _parse_result(ws_r)
                        results.append(f"Wait: '{sel}' {'visible' if isinstance(r, dict) and r.get('ok') else 'timeout'}")
                    elif action == "click":
                        sel = act["selector"]
                        cr = _hermes_call("click", sel)
                        v = _parse_result(cr)
                        results.append(f"Clicked '{sel}'" if isinstance(v, dict) and v.get("ok") else f"Click failed: {v}")
                    elif action == "type":
                        sel = act["selector"]
                        txt = act["text"]
                        _hermes_call("type", sel, txt)
                        results.append(f"Typed into '{sel}'")
                    elif action == "enter":
                        _hermes_call("key", "Enter")
                        results.append("Pressed Enter")
                    elif action == "hover":
                        sel = act["selector"]
                        hv_r = _hermes_call("hover", sel)
                        hv_v = _parse_result(hv_r)
                        results.append(f"Hovered '{sel}'" if isinstance(hv_v, dict) and hv_v.get("ok") else f"Hover failed: {hv_v}")
                    elif action == "click_text":
                        text_query = act["text"]
                        tag_filter = act.get("tag", "")
                        ct_r = _hermes_call("clickText", text_query, tag_filter)
                        ct_v = _parse_result(ct_r)
                        results.append(f"Clicked text '{text_query}'" if isinstance(ct_v, dict) and "error" not in ct_v else f"Click-text failed: {ct_v}")
                    elif action == "key":
                        k = act.get("key", "Escape")
                        _hermes_call("key", k)
                        results.append(f"Pressed {k}")
                    elif action == "screenshot":
                        shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
                        results.append("Screenshot captured" if "error" not in shot else "Screenshot failed")
                    elif action == "fill_by_label":
                        lbl = act["label"]
                        txt = act["text"]
                        fl_r = _hermes_call("fillByLabel", lbl, txt)
                        fl_v = _parse_result(fl_r)
                        results.append(f"Filled '{lbl}'" if isinstance(fl_v, dict) and "error" not in fl_v else f"Fill failed: {fl_v}")
                    elif action == "scroll":
                        d = act.get("direction", "down")
                        a = act.get("amount", 0)
                        _hermes_call("scrollBy", d, a)
                        results.append(f"Scrolled {d}")
                    elif action == "back":
                        _hermes_call("historyBack", fallback="window.history.back()")
                        _invalidate_cache()
                        results.append("Navigated back")
                    elif action == "forward":
                        _hermes_call("historyForward", fallback="window.history.forward()")
                        _invalidate_cache()
                        results.append("Navigated forward")
                    elif action == "eval":
                        expr = act["expression"]
                        r = _parse_result(_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True}))
                        results.append(f"eval: {str(r)[:200]}")
                    elif action == "snapshot":
                        full = act.get("full", False)
                        sn_r = _hermes_call("snapshot", full)
                        sn_v = _parse_result(sn_r)
                        if isinstance(sn_v, dict):
                            info = sn_v.get("info", {})
                            results.append(f"Snapshot: {info.get('visible', 0)}/{info.get('total', 0)} elements")
                        else:
                            results.append("Snapshot error")
                    elif action == "get_text":
                        gt_r = _hermes_call("getText", fallback="document.body ? document.body.innerText.substring(0,10000) : ''")
                        txt = str(_parse_result(gt_r))[:300]
                        results.append(f"Text: {txt}")
                    elif action == "wait":
                        import time

                        time.sleep(min(act.get("seconds", 1), 10))
                        results.append(f"Waited {act.get('seconds', 1)}s")
                    else:
                        results.append(f"Unknown action: {action}")
                except Exception as e:
                    results.append(f"Error in '{action}': {e}")
            return json.dumps({"completed": len(actions), "steps": results})

        elif name == "browser_navigate_wait":
            url = args["url"]
            timeout = min(args.get("timeout", 15), 60)
            nav_result = _cdp("Page.navigate", {"url": url})
            _invalidate_cache()
            if "error" in nav_result:
                return json.dumps({"error": nav_result["error"]})
            import time as _t

            deadline = _t.time() + timeout
            while _t.time() < deadline:
                ready = _parse_result(_hermes_call("readyState", fallback="({ readyState: document.readyState, title: document.title })"))
                if isinstance(ready, dict) and ready.get("readyState") == "complete":
                    title = ready.get("title", "?")
                    do_snap = args.get("snapshot", True)
                    if do_snap:
                        snap_data = _parse_result(_hermes_call("snapshot", False))
                    else:
                        snap_data = None
                    lines = [f"✅ Loaded: {title}", f"🌐 {url}"]
                    if isinstance(snap_data, dict) and "error" not in snap_data:
                        info = snap_data.get("info", {})
                        items = snap_data.get("items", [])
                        lines.append(
                            f"📐 {info.get('viewport', {}).get('w', '?')}×{info.get('viewport', {}).get('h', '?')} — {info.get('visible', '?')} visible of {info.get('total', '?')} elements"
                        )
                        lines.append("")
                        for it in items[:60]:
                            vis = "" if it["visible"] else "⬆"
                            label = it["text"] or it.get("placeholder", "") or it.get("aria_label", "") or it.get("name", "") or it.get("href", "") or it["tag"]
                            lines.append(f'  [{it["ref"]}] {vis}{it["tag"]} "{label[:70]}" @({it["x"]},{it["y"]})')
                        if len(items) > 50:
                            lines.append(f"  ... and {len(items) - 50} more")
                    return "\n".join(lines)
                _t.sleep(0.3)
            return f"Timed out loading: {url}"

        return json.dumps({"error": f"Unknown tool: {name}"})

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Plugin Entrypoint
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the browser-relay memory provider."""
    ctx.register_memory_provider(BrowserRelayProvider())
