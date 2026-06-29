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
            name="browser_back",
            description="Navigate back to the previous page in browser history.",
            inputSchema={
                "type": "object",
                "properties": {},
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
                return [TextContent(type="text", text=json.dumps(status, indent=2))]
            except Exception as e:
                return [TextContent(type="text", text=f"CDP relay unreachable: {e}")]

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
                const all = document.querySelectorAll(selectors.join(','));
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
                    const el = document.querySelector({_js_quote(selector)});
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
                const all = document.querySelectorAll('a, button, span, label, div[role=button], [role=link], [role=tab], [role=menuitem], input[type=submit], input[type=button]');
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
                // Try label association
                const labelEl = document.querySelector('label');
                const labels = Array.from(document.querySelectorAll('label'));
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
                // Try placeholder
                if (!input) {{
                    input = document.querySelector('input[placeholder*=' + JSON.stringify(label) + '], textarea[placeholder*=' + JSON.stringify(label) + ']');
                }}
                // Try aria-label
                if (!input) {{
                    input = document.querySelector('[aria-label*=' + JSON.stringify(label) + ']');
                }}
                // Try name
                if (!input) {{
                    input = document.querySelector('input[name=' + JSON.stringify(label) + '], textarea[name=' + JSON.stringify(label) + ']');
                }}
                if (!input) return {{ error: 'Could not find input for: ' + search }};
                input.focus();
                input.value = '';
                // Use execCommand for React compatibility
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
            # First get the snapshot elements with coords, then take screenshot, then overlay numbers
            annotate_js = f"""
            (() => {{
                const selectors = {json.dumps(_INTERACTIVE_SELECTORS)};
                const all = document.querySelectorAll(selectors.join(','));
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
                    items.push({{ ref: idx, text: label.substring(0, 30), x: Math.round(r.x + r.width/2), y: Math.round(r.top + 8) }});
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
            data = shot.get("result", {}).get("data", "")
            if not data:
                return [TextContent(type="text", text="No screenshot data")]
            # Return screenshot + annotation list as text
            lines = ["📸 Annotated screenshot — interactive elements:", ""]
            for a in annotations:
                lines.append(f"  [{a['ref']}] \"{a['text'][:50]}\" @({a['x']},{a['y']})")
            return [
                ImageContent(type="image", data=data, mimeType="image/png"),
                TextContent(type="text", text="\n".join(lines)),
            ]

        elif name == "browser_key":
            key = arguments.get("key", "Escape")
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
            return [TextContent(type="text", text=f"Pressed {key}")]

        elif name == "browser_back":
            result = _cdp("Runtime.evaluate", {"expression": "window.history.back()"})
            return [TextContent(type="text", text="Navigated back")]

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
