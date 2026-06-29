"""
Browser Relay Plugin — Hermes-native browser control tools.

Registers tools (browser_navigate, browser_eval, etc.) that communicate
with the CDP relay server (:9223) to control the user's browser.

Usage:
    hermes plugins enable browser-relay
    # Then restart — tools appear as: browser_navigate, browser_eval, etc.
"""

import json
import urllib.request
import urllib.error
from typing import Any

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    MemoryProvider = object  # fallback for standalone testing

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CDP_SERVER = "http://127.0.0.1:9223"
TOOL_TIMEOUT = 60

# ---------------------------------------------------------------------------
# CDP Relay Client
# ---------------------------------------------------------------------------


# Shared HTTP connection for keep-alive
_cdp_opener = urllib.request.build_opener()
_cdp_opener.addheaders = [("Content-Type", "application/json")]


def _cdp(method: str, params: dict | None = None, timeout: int = TOOL_TIMEOUT) -> dict:
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


def _parse_result(result: dict) -> Any:
    """Extract the return value from a CDP response."""
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
        "description": "Click an element by CSS selector.",
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
        "description": "Type text into an input field (clicks it first, clears it, then types).",
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
        "description": "Get an accessibility-style snapshot of the current page. Returns compact info for all interactive elements (links, buttons, inputs) with coordinates so you can interact. Faster than taking a screenshot.",
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
        return TOOL_SCHEMAS

    def handle_tool_call(self, name: str, args: dict) -> str:
        try:
            return self._dispatch(name, args)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "browser_navigate":
            result = _cdp("Page.navigate", {"url": args["url"]})
            if "error" in result:
                return json.dumps({"error": result["error"]})
            return json.dumps(result.get("result", {}), indent=2)

        elif name == "browser_eval":
            result = _cdp("Runtime.evaluate", {
                "expression": args["expression"], "returnByValue": True
            })
            return str(_parse_result(result))

        elif name == "browser_hover":
            selector = args["selector"]
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
                return f"Error: {coords['error']}"
            if isinstance(coords, dict) and "x" in coords:
                x, y = round(coords["x"]), round(coords["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
                return f"Hovered '{coords.get('text', '?')}' ({coords.get('tag', '?')}) at ({x}, {y})"
            return f"Unexpected: {json.dumps(coords, indent=2)}"

        elif name == "browser_click":
            selector = args["selector"]
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
                return f"Error: {coords['error']}"
            if isinstance(coords, dict) and "x" in coords:
                x, y = round(coords["x"]), round(coords["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                _cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                return f"Clicked '{coords.get('text', '?')}' ({coords.get('tag', '?')}) at ({x}, {y})"
            return f"Unexpected: {json.dumps(coords, indent=2)}"

        elif name == "browser_type":
            selector = args["selector"]
            text = args["text"]
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
                return json.dumps({"base64_png": data[:100] + "..." if len(data) > 100 else data,
                                   "size": len(data)})
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
            snapshot_js = f"""
            (() => {{
                const selectors = {json.dumps(["a[href]", "button", "input:not([type=hidden])", "select", "textarea", "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])", "summary", "details", "[contenteditable=true]"])};
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
                return {{ info, items }};
            }})()
            """
            snap = _parse_result(_cdp("Runtime.evaluate", {"expression": snapshot_js, "returnByValue": True}))
            if isinstance(snap, dict) and "error" in snap:
                return f"Snapshot error: {snap['error']}"
            info = snap.get("info", {})
            items = snap.get("items", [])
            lines = [
                f"📄 {info.get('title', '?')}",
                f"🌐 {info.get('url', '?')}",
                f"📐 {info.get('viewport', {}).get('w','?')}×{info.get('viewport', {}).get('h','?')} — {info.get('visible','?')} visible of {info.get('total','?')} elements",
                "",
            ]
            for it in items[:60]:
                vis = "⬆ " if not it["visible"] else ""
                label = it["text"] or it.get("placeholder", "") or it.get("aria_label", "") or it["tag"]
                lines.append(f"  [{it['ref']}] {vis}{it['tag']} \"{label[:70]}\" @({it['x']},{it['y']})")
            if len(items) > 60:
                lines.append(f"  ... and {len(items) - 60} more")
            if full:
                text_js = "document.body.innerText.substring(0, 30000)"
                lines.extend(["", "--- Page Text ---", str(_parse_result(_cdp("Runtime.evaluate", {"expression": text_js, "returnByValue": True})))])
            return "\n".join(lines)

        elif name == "browser_scroll":
            direction = args.get("direction", "down")
            amount = args.get("amount", 0)
            if not amount:
                amount = int(_parse_result(_cdp("Runtime.evaluate", {"expression": "Math.round(window.innerHeight * 0.6)", "returnByValue": True})) or 400)
            dx, dy = 0, 0
            if direction == "down": dy = amount
            elif direction == "up": dy = -amount
            elif direction == "right": dx = amount
            elif direction == "left": dx = -amount
            _cdp("Runtime.evaluate", {"expression": f"window.scrollBy({{left:{dx}, top:{dy}, behavior:'smooth'}})"})
            return f"Scrolled {direction} by {abs(dx or dy)}px"

        elif name == "browser_wait_selector":
            selector = args["selector"]
            timeout = min(args.get("timeout", 15), 60)
            state = args.get("state", "visible")
            import time as _t
            deadline = _t.time() + timeout
            while _t.time() < deadline:
                check_js = f"(el = document.querySelector({json.dumps(selector)})) && ({'true' if state == 'visible' else 'true'} ? (r=el.getBoundingClientRect(), r.width>0&&r.height>0) : true)"
                if _parse_result(_cdp("Runtime.evaluate", {"expression": check_js, "returnByValue": True})):
                    return f"Selector '{selector}' matched"
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import asyncio as _a
                        _t.sleep(0.5)  # synchronous sleep since we're not in an async context
                except RuntimeError:
                    _t.sleep(0.5)
            return f"Timed out ({timeout}s) waiting for '{selector}'"

        elif name == "browser_click_text":
            text_query = args["text"]
            tag_filter = args.get("tag", "")
            click_js = f"""
            (() => {{
                const q = {json.dumps(text_query)}.toLowerCase();
                const tag = {json.dumps(tag_filter)};
                const all = document.querySelectorAll('a, button, span, label, div[role=button], [role=link], [role=tab], input[type=submit], input[type=button]');
                for (const el of all) {{
                    if (tag && el.tagName.toLowerCase() !== tag) continue;
                    const txt = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (txt.includes(q)) {{
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        return {{ x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), tag: el.tagName, text: (el.textContent||'').trim().substring(0,80) }};
                    }}
                }}
                return {{ error: 'No element found with text: ' + q }};
            }})()
            """
            ct = _parse_result(_cdp("Runtime.evaluate", {"expression": click_js, "returnByValue": True}))
            if isinstance(ct, dict) and "error" in ct:
                return f"Error: {ct['error']}"
            if isinstance(ct, dict) and "x" in ct:
                x, y = round(ct["x"]), round(ct["y"])
                _cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                _cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                return f"Clicked '{ct.get('text','?')}' ({ct.get('tag','?')}) by text"
            return f"Unexpected: {json.dumps(ct)}"

        elif name == "browser_fill_by_label":
            label = args["label"]
            text = args["text"]
            fill_js = f"""
            (() => {{
                const search = {json.dumps(label)}.toLowerCase();
                let input = null;
                for (const lbl of Array.from(document.querySelectorAll('label'))) {{
                    if ((lbl.textContent||'').trim().toLowerCase().includes(search)) {{
                        const forId = lbl.getAttribute('for');
                        if (forId) input = document.getElementById(forId) || document.querySelector('input[name=' + JSON.stringify(forId) + ']');
                        else input = lbl.querySelector('input, textarea, select');
                        if (input) break;
                    }}
                }}
                if (!input) input = document.querySelector('input[placeholder*=' + JSON.stringify(label) + '], textarea[placeholder*=' + JSON.stringify(label) + ']');
                if (!input) input = document.querySelector('[aria-label*=' + JSON.stringify(label) + ']');
                if (!input) input = document.querySelector('input[name=' + JSON.stringify(label) + ']');
                if (!input) return {{ error: 'Could not find input for: ' + search }};
                input.focus();
                input.value = '';
                input.select();
                document.execCommand('insertText', false, {json.dumps(text)});
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ ok: true, tag: input.tagName, type: input.type || '' }};
            }})()
            """
            fill_result = _parse_result(_cdp("Runtime.evaluate", {"expression": fill_js, "returnByValue": True}))
            if isinstance(fill_result, dict) and "error" in fill_result:
                return f"Error: {fill_result['error']}"
            return f"Filled '{label}' with '{text}'"

        elif name == "browser_get_text":
            text_js = "document.body.innerText.substring(0, 50000)"
            return str(_parse_result(_cdp("Runtime.evaluate", {"expression": text_js, "returnByValue": True})))

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
                    if etype == "tab": lines.append(f"  🏁 NAV: {e.get('url','')[:120]}")
                    elif etype == "download": lines.append(f"  📥 DOWNLOAD: {e.get('filename',e.get('url',''))[:120]}")
                    elif etype == "webrequest" and e.get("event") in ("completed","error"):
                        lines.append(f"  🌐 {e['event']}: {e.get('method','?')} {e.get('statusCode','?')} {e.get('url','')[:100]}")
                    elif etype == "response_body": lines.append(f"  📄 BODY: {e.get('url','')[:100]}")
                if len(lines) <= 2: lines.append("  (no events yet)")
                return "\n".join(lines)
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif name == "browser_key":
            key = args.get("key", "Escape")
            _cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key})
            _cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
            return f"Pressed {key}"

        elif name == "browser_back":
            _cdp("Runtime.evaluate", {"expression": "window.history.back()"})
            return "Navigated back"

        elif name == "browser_screenshot_annotated":
            annotate_js = f"""
            (() => {{
                const selectors = {json.dumps(["a[href]", "button", "input:not([type=hidden])", "select", "textarea", "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[tabindex]:not([tabindex='-1'])", "summary", "details", "[contenteditable=true]"])};
                const all = document.querySelectorAll(selectors.join(','));
                const seen = new Set();
                const items = [];
                let idx = 0;
                all.forEach(el => {{
                    if (seen.has(el)) return;
                    seen.add(el);
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return;
                    if (r.top > window.innerHeight || r.bottom < 0) return;
                    idx++;
                    const txt = (el.textContent||'').trim().substring(0, 40);
                    const label = txt || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('name') || el.tagName;
                    items.push({{ ref: idx, text: label.substring(0, 30), x: Math.round(r.x + r.width/2), y: Math.round(r.top + 8) }});
                }});
                return items.slice(0, 50);
            }})()
            """
            annotations = _parse_result(_cdp("Runtime.evaluate", {"expression": annotate_js, "returnByValue": True}))
            shot = _cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            data = shot.get("result", {}).get("data", "")
            lines = ["📸 Annotated screenshot — interactive elements:", ""]
            for a in (annotations if isinstance(annotations, list) else []):
                lines.append(f"  [{a['ref']}] \"{a['text'][:50]}\" @({a['x']},{a['y']})")
            if data:
                import tempfile, os, base64
                img_dir = os.path.expanduser("~/.hermes/image_cache")
                os.makedirs(img_dir, exist_ok=True)
                img_path = os.path.join(img_dir, f"annotated_{int(__import__('time').time())}.png")
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(data))
                return "MEDIA:" + img_path + "\n" + "\n".join(lines)
            return "\n".join(lines)

        return json.dumps({"error": f"Unknown tool: {name}"})

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Plugin Entrypoint
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the browser-relay memory provider."""
    ctx.register_memory_provider(BrowserRelayProvider())
