#!/usr/bin/env python3
"""
Browser Relay MCP Server — Exposes Chrome/CDP browser control as MCP tools.

Bridges Hermes Agent to a running CDP relay (:9223) so browser actions
(navigate, eval, click, type, screenshot) are available as first-class tools.

Protocol: stdio MCP — read JSON-RPC from stdin, write to stdout.
"""
import json
import os
import urllib.request
import urllib.error
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

# ---------------------------------------------------------------------------
# CDP Relay Client (with connection reuse)
# ---------------------------------------------------------------------------

CDP_SERVER = os.environ.get("CDP_RELAY_URL", "http://127.0.0.1:9223")
CAPTURE_SERVER = os.environ.get("CAPTURE_SERVER_URL", "")  # Legacy, kept for compat
CDP_TIMEOUT = int(os.environ.get("CDP_RELAY_TIMEOUT", "60"))

# Shared HTTP connection for keep-alive
_cdp_opener = urllib.request.build_opener()
_cdp_opener.addheaders = [("Content-Type", "application/json")]

# Separate opener for capture server (plain GET requests)
_capture_opener = urllib.request.build_opener()


def _cdp(method: str, params: dict | None = None, timeout: int = CDP_TIMEOUT) -> dict:
    """Send a CDP command to the relay server. Uses shared connection for keep-alive."""
    body = json.dumps({"method": method, "params": params or {}, "timeout": timeout}).encode()
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
    """Query the relay server's /recent endpoint. Returns parsed JSON or error dict."""
    try:
        req = urllib.request.Request(f"{CDP_SERVER}{path}")
        with _capture_opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        return {"error": f"Relay server unreachable: {e.reason}", "hint": "Is server.py running on :9223?"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("browser-relay")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
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
            description="Fetch recent browser events (navigations, downloads, requests) from the capture server buffer. Use this to check what the browser has been doing — no need to navigate to find out. mode='nav' returns only important events (navigations, downloads, errors), mode='all' returns everything including CDP noise.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max events to return (default: 20, max: 200)", "default": 20},
                    "mode": {"type": "string", "description": "'nav' for interesting events only, 'all' for everything (default: 'nav')", "default": "nav", "enum": ["nav", "all"]},
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    try:
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
            # One JS eval: focus the element, set its value, dispatch input/change events
            type_expr = f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
                el.focus();
                el.value = {json.dumps(text)};
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
                return [TextContent(type="text", text=json.dumps(status, indent=2))]
            except Exception as e:
                return [TextContent(type="text", text=f"CDP relay unreachable: {e}")]

        elif name == "browser_send_cdp":
            method = arguments["method"]
            params = arguments.get("params", {})
            result = _cdp(method, params)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

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

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error in {name}: {e}")]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
