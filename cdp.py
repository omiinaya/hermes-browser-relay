#!/usr/bin/env python3
"""
CDP Client — Send CDP commands to Chrome via the relay server.

Subcommands:
    status              Check server & extension status
    navigate <url>      Navigate to a URL
    eval <expression>   Evaluate JavaScript in the page
    screenshot          Capture a page screenshot
    click <selector>    Click an element by CSS selector
    type <sel> <text>   Type text into an input field
    enter               Press Enter key
    send <method> [params]   Send arbitrary CDP command
    watch               Stream CDP events in real-time
    snapshot [--full]   Get accessibility snapshot of page
    find <query>        Search page for elements matching text
    pdf [--landscape] [--scale N]   Save page as PDF
    dismiss_dialog [--accept] [--prompt-text]   Dismiss JS dialog
    set_cookie <name> <value>   Set a browser cookie
    block_patterns <patterns>   Block URL patterns
    form_fill <fields>   Fill multiple form fields
    upload_file <selector> <path>   Upload file via file input
    console [--limit N]   Read browser console output
    back                Navigate back
    forward             Navigate forward
    refresh [--hard]    Reload page
    get_cookies         Read all cookies
    clear_cookies       Clear all cookies
    wait_selector <sel> [--timeout N]   Wait for CSS selector
    wait_nav [--timeout N]   Wait for page navigation
    list_tabs           List all open tabs
    switch_tab <tab_id> Switch to a tab
    create_tab [--url]  Create new tab
    close_tab <tab_id>  Close a tab
    get_text            Get page text content
    get_title           Get page title
    get_downloads       List recent downloads
    hover <selector>    Hover over element
    select_option <sel> <text>   Select option in dropdown
    fill_by_label <label> <text>   Fill form field by label
    click_text <text>   Click element by text content
    key <key>           Press keyboard key
    scroll [--dir] [--amount]   Scroll page
    response_body       Query captured response bodies
    network_requests    Query captured network requests

Usage examples:
    cdp.py navigate '{"url": "https://example.com"}'
    cdp.py eval 'document.title'
    cdp.py click '#submit-btn'
    cdp.py snapshot
    cdp.py find 'login'
    cdp.py pdf --landscape
    cdp.py list_tabs
    cdp.py switch_tab 42
    cdp.py wait_selector '#results' --timeout 10
    cdp.py send Page.navigate '{"url": "https://example.com"}'
    cdp.py watch
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
VERSION = open(_VERSION_FILE).read().strip() if os.path.exists(_VERSION_FILE) else "0.0.0"
SERVER = "http://127.0.0.1:9223"


def _http_get(path, timeout=10):
    """Make a GET request to the relay server."""
    try:
        req = urllib.request.Request(f"{SERVER}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else "{}"
        return {"error": f"HTTP {e.code}", "detail": json.loads(detail) if detail else {}}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}", "hint": "Is the CDP relay server running?"}
    except Exception as e:
        return {"error": str(e)}


def _http_post(path, body, timeout=60):
    """Make a POST request to the relay server."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{SERVER}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else "{}"
        return {"error": f"HTTP {e.code}", "detail": json.loads(detail) if detail else {}}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}", "hint": "Is the CDP relay server running?"}
    except Exception as e:
        return {"error": str(e)}


def send_cdp(method, params=None, timeout=60):
    """Send a CDP command and return the response."""
    if params is None:
        params = {}
    return _http_post("/cdp", {"method": method, "params": params, "timeout": timeout}, timeout=timeout)


def _js_quote(s):
    """JSON-escape a string for embedding in JS."""
    return json.dumps(s)


def _parse_js_result(result):
    """Extract the value from a Runtime.evaluate result."""
    if "error" in result:
        return {"error": result["error"]}
    r = result.get("result", {})
    if "result" in r and "value" in r["result"]:
        return r["result"]["value"]
    if "exceptionDetails" in r:
        return {"error": str(r["exceptionDetails"])}
    return r


def _eval_js(expression):
    """Evaluate JavaScript and return the parsed result."""
    result = send_cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return _parse_js_result(result)


def watch_events(server_url=None):
    """Stream CDP events via SSE using buffered reads."""
    import http.client
    from urllib.parse import urlparse

    if server_url is None:
        server_url = SERVER
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9223

    conn = http.client.HTTPConnection(host, port, timeout=300)
    conn.request("GET", "/events")
    resp = conn.getresponse()

    print("Watching CDP events... (Ctrl+C to stop)\n", file=sys.stderr)

    buffer = ""
    try:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        method = event.get("method", "?")
                        params_summary = json.dumps(event.get("params", {}))[:200]
                        print(f"[{method}] {params_summary}")
                    except json.JSONDecodeError:
                        print(line)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_navigate(url):
    """Navigate to a URL."""
    result = send_cdp("Page.navigate", {"url": url})
    print(json.dumps(result, indent=2))


def cmd_eval(expression):
    """Evaluate JavaScript in the page."""
    result = send_cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if "result" in result:
        r = result["result"]
        if "result" in r and "value" in r["result"]:
            print(r["result"]["value"])
        elif "exceptionDetails" in r:
            print(f"Error: {r['exceptionDetails']}")
        else:
            print(json.dumps(r, indent=2))
    else:
        print(json.dumps(result, indent=2))


def cmd_screenshot():
    """Capture a page screenshot (base64 PNG)."""
    result = send_cdp("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    if "result" in result and "data" in result["result"]:
        import base64

        png_data = base64.b64decode(result["result"]["data"])
        path = f"/tmp/cdp_screenshot_{int(time.time())}.png"
        with open(path, "wb") as f:
            f.write(png_data)
        print(f"Screenshot saved: {path}")
    else:
        print(json.dumps(result, indent=2))


def cmd_click(selector):
    """Click an element by CSS selector using JavaScript."""
    expr = f"""
    (() => {{
        const el = document.querySelector({_js_quote(selector)});
        if (!el) return {{ error: 'Element not found: ' + {_js_quote(selector)} }};
        const rect = el.getBoundingClientRect();
        return {{
            x: rect.x + rect.width/2,
            y: rect.y + rect.height/2,
            text: el.textContent.trim().substring(0, 100),
            tag: el.tagName
        }};
    }})()
    """
    result = send_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})

    try:
        coords = result["result"]["result"]["value"]
        if "error" in coords:
            print(f"Error: {coords['error']}")
            return
        x, y = coords["x"], coords["y"]
        print(f"Clicking '{coords.get('text', '?')}' at ({x:.0f}, {y:.0f})")

        # Dispatch mouse click via CDP
        send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": round(x), "y": round(y), "button": "left", "clickCount": 1})
        send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": round(x), "y": round(y), "button": "left", "clickCount": 1})
    except (KeyError, TypeError) as e:
        print(f"Click failed: {e}")
        print(json.dumps(result, indent=2))


def cmd_type(selector, text):
    """Type text into an input field."""
    # Click the field first
    cmd_click(selector)
    time.sleep(0.2)

    # Clear and type
    send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Control"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Control"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Delete"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Delete"})
    time.sleep(0.1)

    send_cdp("Input.insertText", {"text": text})
    print(f"Typed '{text}'")


def cmd_type_enter():
    """Press Enter key."""
    send_cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "windowsVirtualKeyCode": 13})
    send_cdp("Input.dispatchKeyEvent", {"type": "char", "text": "\r"})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "windowsVirtualKeyCode": 13})
    print("Pressed Enter")


# ---------------------------------------------------------------------------
# New command implementations
# ---------------------------------------------------------------------------


def cmd_snapshot(full=False):
    """Get an accessibility-style snapshot of the current page."""
    selectors = [
        "a[href]",
        "button",
        "input:not([type=hidden])",
        "select",
        "textarea",
        "[role=button]",
        "[role=link]",
        "[role=checkbox]",
        "[role=radio]",
        "[role=tab]",
        "[role=menuitem]",
        "[role=option]",
        "[tabindex]:not([tabindex='-1'])",
        "summary",
        "details",
        "[contenteditable=true]",
    ]
    expr = f"""
    (() => {{
        const selectors = {json.dumps(selectors)};
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
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and "error" in result:
        print(f"Snapshot error: {result['error']}")
        return

    info = result.get("info", {})
    items = result.get("items", [])
    print(f"📄 {info.get('title', '?')}")
    print(f"🌐 {info.get('url', '?')}")
    print(
        f"📐 {info.get('viewport', {}).get('w', '?')}×{info.get('viewport', {}).get('h', '?')} — {info.get('visible', '?')} visible of {info.get('total', '?')} elements"
    )
    print()
    for it in items[:60]:
        vis = "⬆ " if not it.get("visible") else ""
        label = it.get("text", "") or it.get("tag", "")
        print(f'  [{it["ref"]}] {vis}{it["tag"]} "{label[:70]}" @({it["x"]},{it["y"]})')
    if len(items) > 60:
        print(f"  ... and {len(items) - 60} more")
    if full:
        text_content = result.get("textContent", "")
        print(f"\n--- Page Text ---\n{text_content[:20000]}")


def cmd_find(query, selector=None, max_results=20):
    """Search the page for elements matching text."""
    js_sel = json.dumps(selector) if selector else "null"
    expr = f"""
    (() => {{
        const q = {json.dumps(query)}.toLowerCase();
        const scopeSel = {js_sel};
        const root = scopeSel ? document.querySelector(scopeSel) : document;
        if (!root) return {{ error: 'Scope selector not found' }};
        const all = root.querySelectorAll('a, button, input, select, textarea, span, label, p, h1, h2, h3, h4, h5, h6, li, td, th, div, [role], [tabindex]');
        const results = [];
        let idx = 0;
        all.forEach(el => {{
            const txt = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
            if (txt.toLowerCase().includes(q)) {{
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return;
                idx++;
                results.push({{
                    ref: 'f' + idx,
                    tag: el.tagName.toLowerCase(),
                    text: txt.substring(0, 120),
                    x: Math.round(r.x + r.width/2),
                    y: Math.round(r.y + r.height/2),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                }});
            }}
        }});
        return {{ count: results.length, results: results.slice(0, {max_results}) }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and "error" in result:
        print(f"Find error: {result['error']}")
        return
    results = result.get("results", [])
    print(f"Found {result.get('count', 0)} elements matching '{query}':")
    for it in results:
        print(f'  [{it["ref"]}] <{it["tag"]}> "{it.get("text", "")[:70]}" @({it["x"]},{it["y"]})')


def cmd_pdf(landscape=False, scale=1.0):
    """Save the current page as a PDF document."""
    result = send_cdp(
        "Page.printToPDF",
        {
            "landscape": landscape,
            "scale": scale,
            "paperWidth": 8.27,
            "paperHeight": 11.69,
            "printBackground": True,
            "preferCSSPageSize": True,
        },
    )
    if "result" in result and "data" in result["result"]:
        import base64

        pdf_data = base64.b64decode(result["result"]["data"])
        path = f"/tmp/cdp_pdf_{int(time.time())}.pdf"
        with open(path, "wb") as f:
            f.write(pdf_data)
        print(f"PDF saved: {path} ({len(pdf_data)} bytes)")
    else:
        print(json.dumps(result, indent=2))


def cmd_dismiss_dialog(accept=True, prompt_text=""):
    """Dismiss a JavaScript dialog (alert/confirm/prompt)."""
    params = {"accept": accept}
    if prompt_text:
        params["promptText"] = prompt_text
    result = send_cdp("Page.handleJavaScriptDialog", params)
    if "error" in result:
        print(f"Dialog dismiss error: {result['error']}")
    else:
        action = "Accepted" if accept else "Cancelled"
        print(f"{action} dialog")


def cmd_set_cookie(name, value, url=None, domain=None, path="/", secure=False, http_only=False, same_site=None):
    """Set a browser cookie."""
    params = {"name": name, "value": value}
    if url:
        params["url"] = url
    if domain:
        params["domain"] = domain
    if path:
        params["path"] = path
    if secure:
        params["secure"] = True
    if http_only:
        params["httpOnly"] = True
    if same_site:
        params["sameSite"] = same_site
    result = send_cdp("Network.setCookie", params)
    if "error" in result:
        print(f"Set cookie error: {result['error']}")
    else:
        r = result.get("result", {})
        if r.get("success"):
            print(f"Cookie '{name}' set successfully")
        else:
            print(f"Cookie '{name}' set failed: {json.dumps(r)}")


def cmd_block_patterns(patterns):
    """Block or allow URL patterns."""
    result = _http_post("/block/patterns", {"patterns": patterns})
    if "error" in result:
        print(f"Block patterns error: {result['error']}")
    else:
        print(f"Block patterns updated: {json.dumps(result, indent=2)}")


def cmd_form_fill(fields):
    """Fill multiple form fields at once from a dict of {identifier: value}."""
    for identifier, value in fields.items():
        # Try fill_by_label approach for each field
        expr = f"""
        (() => {{
            const label = {json.dumps(identifier)};
            const val = {json.dumps(value)};
            // Try by label text
            const labels = document.querySelectorAll('label');
            for (const lb of labels) {{
                if (lb.textContent.trim().toLowerCase().includes(label.toLowerCase())) {{
                    const forId = lb.getAttribute('for');
                    if (forId) {{
                        const el = document.getElementById(forId);
                        if (el) {{ el.focus(); el.value = val; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'label-for', field: forId }}; }}
                    }}
                }}
            }}
            // Try by placeholder
            const byPlaceholder = document.querySelector('[placeholder*="{json.dumps(identifier)}" i]');
            if (byPlaceholder) {{ byPlaceholder.focus(); byPlaceholder.value = val; byPlaceholder.dispatchEvent(new Event('input', {{ bubbles: true }})); byPlaceholder.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'placeholder' }}; }}
            // Try by name
            const byName = document.querySelector('[name*="{json.dumps(identifier)}" i]');
            if (byName) {{ byName.focus(); byName.value = val; byName.dispatchEvent(new Event('input', {{ bubbles: true }})); byName.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'name' }}; }}
            // Try by aria-label
            const byAria = document.querySelector('[aria-label*="{json.dumps(identifier)}" i]');
            if (byAria) {{ byAria.focus(); byAria.value = val; byAria.dispatchEvent(new Event('input', {{ bubbles: true }})); byAria.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'aria-label' }}; }}
            // Try as CSS selector directly
            try {{
                const bySel = document.querySelector({json.dumps(identifier)});
                if (bySel) {{ bySel.focus(); bySel.value = val; bySel.dispatchEvent(new Event('input', {{ bubbles: true }})); bySel.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'selector' }}; }}
            }} catch(e) {{}}
            return {{ error: 'Field not found: ' + label }};
        }})()
        """
        result = _eval_js(expr)
        if isinstance(result, dict) and result.get("ok"):
            print(f"  {identifier}: ✓ ({result.get('method', '?')})")
        else:
            print(f"  {identifier}: ✗ {result.get('error', 'failed')}")


def cmd_upload_file(selector, file_paths):
    """Upload a file by setting the value of an <input type=file> element."""
    # First find the file input's node ID via CDP
    expr = f"""
    (() => {{
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return {{ error: 'File input not found: ' + {json.dumps(selector)} }};
        if (el.tagName !== 'INPUT' || el.type !== 'file') return {{ error: 'Element is not a file input' }};
        // Get the backend node ID for DOM.setFileInputFiles
        return {{ nodeId: true, tag: el.tagName, found: true }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("error"):
        print(f"Upload error: {result['error']}")
        return

    # Use CDP DOM.setFileInputFiles
    if not isinstance(file_paths, list):
        file_paths = [file_paths]
    result = send_cdp(
        "DOM.setFileInputFiles",
        {
            "files": file_paths,
        },
    )
    if "error" in result:
        print(f"Upload error: {result['error']}")
    else:
        print(f"Uploaded {len(file_paths)} file(s) to '{selector}'")


def cmd_console(limit=20):
    """Read recent browser console output."""
    result = _http_get(f"/recent?limit={limit}&type=page_event")
    if "error" in result:
        print(f"Console error: {result['error']}")
        return
    events = result.get("events", [])
    console_msgs = [e for e in events if e.get("event") == "console"]
    if not console_msgs:
        print("No console messages")
        return
    for msg in console_msgs[:limit]:
        level = msg.get("level", "log")
        text = msg.get("text", msg.get("message", ""))
        print(f"[{level}] {text}")


def cmd_back():
    """Navigate back in browser history."""
    result = send_cdp("Page.navigateToHistoryEntry", {"entryId": -1})
    # Fallback: use JS history.back()
    if "error" in result:
        _eval_js("window.history.back()")
        print("Navigated back (via JS)")
    else:
        print("Navigated back")


def cmd_forward():
    """Navigate forward in browser history."""
    _eval_js("window.history.forward()")
    print("Navigated forward")


def cmd_refresh(hard_reload=False):
    """Reload the current page."""
    result = send_cdp("Page.reload", {"ignoreCache": hard_reload})
    if "error" in result:
        print(f"Refresh error: {result['error']}")
    else:
        print("Page reloaded" + (" (hard)" if hard_reload else ""))


def cmd_get_cookies():
    """Read all cookies for the current page."""
    result = send_cdp("Network.getCookies")
    if "error" in result:
        print(f"Get cookies error: {result['error']}")
        return
    cookies = result.get("result", {}).get("cookies", [])
    if not cookies:
        print("No cookies found")
        return
    print(f"Cookies ({len(cookies)}):")
    for c in cookies:
        flags = []
        if c.get("secure"):
            flags.append("Secure")
        if c.get("httpOnly"):
            flags.append("HttpOnly")
        if c.get("sameSite"):
            flags.append(f"SameSite={c['sameSite']}")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {c['name']} = {c['value'][:60]}{flag_str}")
        print(f"    Domain: {c.get('domain', '?')}  Path: {c.get('path', '/')}  Expires: {c.get('expires', 'session')}")


def cmd_clear_cookies():
    """Clear all cookies for the current page."""
    result = send_cdp("Network.clearBrowserCookies")
    if "error" in result:
        print(f"Clear cookies error: {result['error']}")
    else:
        print("All cookies cleared")


def cmd_wait_selector(selector, timeout=15, state="visible"):
    """Wait for a CSS selector to appear in the DOM."""
    expr = f"""
    (() => {{
        const deadline = Date.now() + {timeout * 1000};
        const state = {json.dumps(state)};
        while (Date.now() < deadline) {{
            const el = document.querySelector({json.dumps(selector)});
            if (el) {{
                if (state === 'attached') return {{ ok: true }};
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return {{ ok: true }};
            }}
        }}
        return {{ error: 'Timeout waiting for ' + {json.dumps(selector)} }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Selector '{selector}' matched")
    else:
        print(f"Timed out ({timeout}s) waiting for '{selector}'")


def cmd_wait_for_navigation(tab_id=None, timeout=30):
    """Wait for a page navigation to complete by polling the relay's event buffer."""
    import time as _time

    deadline = _time.time() + timeout
    since = 0
    while _time.time() < deadline:
        path = "/recent?limit=10&mode=nav"
        if since:
            path += f"&since={since}"
        result = _http_get(path)
        events = result.get("events", [])
        for evt in events:
            if evt.get("type") == "tab" and evt.get("event") == "navigated":
                url = evt.get("url", "")
                title = evt.get("title", "")
                tab = evt.get("tabId", tab_id)
                print(f"Navigation detected: {title}")
                print(f"  URL: {url}")
                print(f"  Tab: {tab}")
                return
            since = max(since, evt.get("_received", 0))
        _time.sleep(0.5)
    print(f"Timed out ({timeout}s) waiting for navigation")


def cmd_list_tabs():
    """List all open browser tabs."""
    result = _http_get("/tabs")
    if "error" in result:
        print(f"List tabs error: {result['error']}")
        return
    tabs = result.get("tabs", [])
    if not tabs:
        print("No tabs found")
        return
    print(f"Tabs ({result.get('count', len(tabs))}):")
    for t in tabs:
        active = " ◀ ACTIVE" if t.get("active") else ""
        print(f"  [{t['id']}] {t.get('title', '?')}{active}")
        print(f"         {t.get('url', '?')}")


def cmd_switch_tab(tab_id):
    """Switch to a tab by its tab ID."""
    result = _http_post("/tabs/switch", {"tabId": tab_id})
    if "error" in result:
        print(f"Switch tab error: {result['error']}")
    else:
        print(f"Switched to tab {tab_id}: {result.get('title', '?')}")


def cmd_create_tab(url="about:blank", active=True):
    """Create a new browser tab."""
    result = _http_post("/tabs/create", {"url": url, "active": active})
    if "error" in result:
        print(f"Create tab error: {result['error']}")
    else:
        print(f"Created tab {result.get('tabId', '?')}: {result.get('title', '?')}")
        print(f"  URL: {result.get('url', url)}")


def cmd_close_tab(tab_id):
    """Close a browser tab."""
    result = _http_post("/tabs/close", {"tabId": tab_id})
    if "error" in result:
        print(f"Close tab error: {result['error']}")
    else:
        print(f"Closed tab {tab_id}")


def cmd_get_text():
    """Get the rendered text content of the current page."""
    result = _eval_js("document.body ? document.body.innerText : ''")
    if isinstance(result, str):
        print(result[:50000])
    elif isinstance(result, dict) and "error" in result:
        print(f"Get text error: {result['error']}")
    else:
        print(str(result)[:50000])


def cmd_get_title():
    """Get the current page title."""
    result = _eval_js("document.title")
    if isinstance(result, str):
        print(result)
    elif isinstance(result, dict) and "error" in result:
        print(f"Get title error: {result['error']}")
    else:
        print(str(result))


def cmd_get_downloads(limit=20):
    """List recent downloads."""
    result = _http_get(f"/recent?limit={limit}&type=download")
    if "error" in result:
        print(f"Get downloads error: {result['error']}")
        return
    events = result.get("events", [])
    downloads = [e for e in events if e.get("type") == "download"]
    if not downloads:
        print("No recent downloads")
        return
    print(f"Recent downloads ({len(downloads)}):")
    for d in downloads[:limit]:
        filename = d.get("filename", d.get("url", "?"))
        state = d.get("state", "?")
        total = d.get("totalBytes", "?")
        print(f"  {filename} [{state}] ({total} bytes)")


def cmd_hover(selector):
    """Hover the mouse over an element by CSS selector."""
    expr = f"""
    (() => {{
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return {{ error: 'Element not found: ' + {json.dumps(selector)} }};
        const r = el.getBoundingClientRect();
        const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
        el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        el.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy }}));
        return {{ ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Hovered '{result.get('text', '?')}' ({result.get('tag', '?')})")
    elif isinstance(result, dict) and "error" in result:
        print(f"Hover error: {result['error']}")
    else:
        print(f"Hovered '{selector}'")


def cmd_select_option(selector, text):
    """Select an <option> in a <select> dropdown by visible text."""
    expr = f"""
    (() => {{
        const sel = document.querySelector({json.dumps(selector)});
        if (!sel) return {{ error: 'Select element not found: ' + {json.dumps(selector)} }};
        if (sel.tagName !== 'SELECT') return {{ error: 'Element is not a <select>' }};
        const options = sel.options;
        for (let i = 0; i < options.length; i++) {{
            if (options[i].text.trim().toLowerCase() === {json.dumps(text)}.toLowerCase()) {{
                sel.selectedIndex = i;
                sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ ok: true, value: options[i].value, text: options[i].text }};
            }}
        }}
        return {{ error: 'Option not found: ' + {json.dumps(text)} }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Selected '{result.get('text', '?')}' (value: {result.get('value', '?')})")
    elif isinstance(result, dict) and "error" in result:
        print(f"Select error: {result['error']}")
    else:
        print(f"Selected option in '{selector}'")


def cmd_fill_by_label(label, text):
    """Find a form field by its label text, placeholder, or aria-label, then type text."""
    expr = f"""
    (() => {{
        const label = {json.dumps(label)};
        const val = {json.dumps(text)};
        const lc = label.toLowerCase();
        // Try by label text
        const labels = document.querySelectorAll('label');
        for (const lb of labels) {{
            if (lb.textContent.trim().toLowerCase().includes(lc)) {{
                const forId = lb.getAttribute('for');
                if (forId) {{
                    const el = document.getElementById(forId);
                    if (el) {{ el.focus(); el.value = val; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'label-for' }}; }}
                }}
                // Try finding input inside label
                const input = lb.querySelector('input, textarea, select');
                if (input) {{ input.focus(); input.value = val; input.dispatchEvent(new Event('input', {{ bubbles: true }})); input.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'label-child' }}; }}
            }}
        }}
        // Try by placeholder
        const byPlaceholder = document.querySelector('[placeholder*="' + label + '" i]');
        if (byPlaceholder) {{ byPlaceholder.focus(); byPlaceholder.value = val; byPlaceholder.dispatchEvent(new Event('input', {{ bubbles: true }})); byPlaceholder.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'placeholder' }}; }}
        // Try by aria-label
        const byAria = document.querySelector('[aria-label*="' + label + '" i]');
        if (byAria) {{ byAria.focus(); byAria.value = val; byAria.dispatchEvent(new Event('input', {{ bubbles: true }})); byAria.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'aria-label' }}; }}
        // Try by name
        const byName = document.querySelector('[name*="' + label + '" i]');
        if (byName) {{ byName.focus(); byName.value = val; byName.dispatchEvent(new Event('input', {{ bubbles: true }})); byName.dispatchEvent(new Event('change', {{ bubbles: true }})); return {{ ok: true, method: 'name' }}; }}
        return {{ error: 'Field not found: ' + label }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Filled '{label}' with '{text}' ({result.get('method', '?')})")
    elif isinstance(result, dict) and "error" in result:
        print(f"Fill error: {result['error']}")
    else:
        print(f"Filled '{label}' with '{text}'")


def cmd_click_text(text, tag=None):
    """Click an element by its visible text content."""
    tag_filter = json.dumps(tag) if tag else "''"
    expr = f"""
    (() => {{
        const q = {json.dumps(text)}.toLowerCase();
        const tag = {tag_filter};
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
                return {{ ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) }};
            }}
        }}
        return {{ error: 'No element found with text: ' + {json.dumps(text)} }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Clicked '{result.get('text', '?')}' ({result.get('tag', '?')})")
    elif isinstance(result, dict) and "error" in result:
        print(f"Click text error: {result['error']}")
    else:
        print(f"Clicked text '{text}'")


def cmd_key(key_name):
    """Press a keyboard key."""
    send_cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key_name, "windowsVirtualKeyCode": _key_to_vk(key_name)})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key_name, "windowsVirtualKeyCode": _key_to_vk(key_name)})
    print(f"Pressed '{key_name}'")


def _key_to_vk(key):
    """Map common key names to Windows virtual key codes."""
    mapping = {
        "Escape": 27,
        "Enter": 13,
        "Tab": 9,
        "Backspace": 8,
        "Delete": 46,
        "ArrowUp": 38,
        "ArrowDown": 40,
        "ArrowLeft": 37,
        "ArrowRight": 39,
        "Home": 36,
        "End": 35,
        "PageUp": 33,
        "PageDown": 34,
        "F1": 112,
        "F2": 113,
        "F3": 114,
        "F4": 115,
        "F5": 116,
        "F6": 117,
        "F7": 118,
        "F8": 119,
        "F9": 120,
        "F10": 121,
        "F11": 122,
        "F12": 123,
        "Space": 32,
        "Control": 17,
        "Shift": 16,
        "Alt": 18,
        "Meta": 91,
    }
    return mapping.get(key, 0)


def cmd_scroll(direction="down", amount=0):
    """Scroll the page in a direction."""
    expr = f"""
    (() => {{
        const dir = {json.dumps(direction)};
        let amt = {amount};
        if (!amt) {{ amt = Math.round(window.innerHeight / 2); }}
        const dx = dir === 'right' ? amt : (dir === 'left' ? -amt : 0);
        const dy = dir === 'down' ? amt : (dir === 'up' ? -amt : 0);
        window.scrollBy({{ left: dx, top: dy, behavior: 'instant' }});
        return {{ ok: true, direction: dir, amount: amt }};
    }})()
    """
    result = _eval_js(expr)
    if isinstance(result, dict) and result.get("ok"):
        print(f"Scrolled {direction} by {abs(result.get('amount', 0))}px")
    else:
        print(f"Scrolled {direction}")


def cmd_response_body(limit=5, include_body=False, since=0):
    """Query captured response bodies from the relay's event buffer."""
    path = f"/recent?limit={limit}&type=body"
    if since:
        path += f"&since={since}"
    result = _http_get(path)
    if "error" in result:
        print(f"Response body error: {result['error']}")
        return
    events = result.get("events", [])
    bodies = [e for e in events if e.get("type") == "body"]
    if not bodies:
        print("No captured response bodies")
        return
    print(f"Response bodies ({len(bodies)}):")
    for b in bodies[:limit]:
        url = b.get("url", "?")
        mime = b.get("mimeType", "?")
        body = b.get("body", "")
        print(f"  {url}")
        print(f"    Type: {mime}  Size: {len(body)} bytes")
        if include_body and body:
            print(f"    Body: {body[:500]}")


def cmd_network_requests(event=None, since=0, limit=20):
    """Query captured network requests from the relay's event buffer."""
    path = f"/recent?limit={limit}&mode=all"
    if since:
        path += f"&since={since}"
    result = _http_get(path)
    if "error" in result:
        print(f"Network requests error: {result['error']}")
        return
    events = result.get("events", [])
    requests = [e for e in events if e.get("type") == "webrequest"]
    if event:
        requests = [e for e in requests if e.get("event") == event]
    if not requests:
        print("No network requests found")
        return
    print(f"Network requests ({len(requests)}):")
    for r in requests[:limit]:
        evt = r.get("event", "?")
        url = r.get("url", "?")
        status = r.get("statusCode", "")
        method = r.get("method", "")
        status_str = f" [{status}]" if status else ""
        print(f"  [{evt}]{status_str} {method} {url[:120]}")


# ---------------------------------------------------------------------------
# Auto-start relay
# ---------------------------------------------------------------------------


def _ensure_relay() -> str | None:
    """Start the CDP relay server if not already running."""
    import subprocess as _sub

    # First check if relay is already running
    try:
        req = urllib.request.Request("http://127.0.0.1:9223/status", method="GET")
        with urllib.request.urlopen(req, timeout=3) as _resp:
            if _resp.status == 200:
                return None
    except Exception:
        pass

    # Find server.py
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _candidates = [
        os.path.normpath(os.path.join(_script_dir, "server.py")),
        os.path.normpath(os.path.join(_script_dir, "..", "server.py")),
    ]
    _server_script = None
    for _c in _candidates:
        if os.path.exists(_c):
            _server_script = _c
            break

    if not _server_script:
        return "Cannot find server.py"

    try:
        _sub.Popen([_server_script], stdout=_sub.DEVNULL, stderr=_sub.DEVNULL, cwd=os.path.dirname(_server_script))
    except Exception as e:
        return f"Failed to start relay: {e}"

    # Wait for it
    for _ in range(20):
        import time as _t
        _t.sleep(0.3)
        try:
            req = urllib.request.Request("http://127.0.0.1:9223/status", method="GET")
            with urllib.request.urlopen(req, timeout=2) as _resp:
                if _resp.status == 200:
                    return None
        except Exception:
            pass
    return "Relay started but not responding"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    global SERVER

    # Auto-start relay if not running
    _relay_err = _ensure_relay()
    if _relay_err:
        print(f"Warning: {_relay_err}", file=sys.stderr)

    parser = argparse.ArgumentParser(description="CDP Client — Send CDP commands to Chrome via the relay server")
    parser.add_argument("--version", action="version", version=f"browser-relay {VERSION}")
    parser.add_argument("--server", default=None, help=f"CDP relay server (default: {SERVER})")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Check server & extension status")

    p = sub.add_parser("navigate", help="Navigate to URL")
    p.add_argument("url", help="URL to navigate to")

    p = sub.add_parser("eval", help="Evaluate JavaScript")
    p.add_argument("expression", help="JS expression")

    p = sub.add_parser("screenshot", help="Capture screenshot")

    p = sub.add_parser("click", help="Click element by CSS selector")
    p.add_argument("selector", help="CSS selector")

    p = sub.add_parser("type", help="Type text into field")
    p.add_argument("selector", help="CSS selector for input field")
    p.add_argument("text", help="Text to type")

    sub.add_parser("enter", help="Press Enter key")

    p = sub.add_parser("send", help="Send arbitrary CDP command")
    p.add_argument("method", help="CDP method (e.g., Page.navigate)")
    p.add_argument("params", nargs="?", default="{}", help="JSON params")

    sub.add_parser("watch", help="Stream CDP events in real-time")

    # --- New commands ---

    p = sub.add_parser("snapshot", help="Get accessibility snapshot of page")
    p.add_argument("--full", action="store_true", help="Include full page text content")

    p = sub.add_parser("find", help="Search page for elements matching text")
    p.add_argument("query", help="Text to search for (case-insensitive)")
    p.add_argument("--selector", help="Optional CSS selector to narrow search scope")
    p.add_argument("--max", type=int, default=20, help="Max results (default: 20)")

    p = sub.add_parser("pdf", help="Save page as PDF")
    p.add_argument("--landscape", action="store_true", help="Landscape orientation")
    p.add_argument("--scale", type=float, default=1.0, help="Scale factor (0.1-2.0)")

    p = sub.add_parser("dismiss_dialog", help="Dismiss JavaScript dialog")
    p.add_argument("--accept", action="store_true", default=True, help="Accept dialog (default)")
    p.add_argument("--cancel", dest="accept", action="store_false", help="Cancel/dismiss dialog")
    p.add_argument("--prompt-text", default="", help="Text for prompt() dialogs")

    p = sub.add_parser("set_cookie", help="Set a browser cookie")
    p.add_argument("name", help="Cookie name")
    p.add_argument("value", help="Cookie value")
    p.add_argument("--url", help="Cookie URL")
    p.add_argument("--domain", help="Cookie domain")
    p.add_argument("--path", default="/", help="Cookie path")
    p.add_argument("--secure", action="store_true", help="Secure flag")
    p.add_argument("--http-only", action="store_true", help="HttpOnly flag")
    p.add_argument("--same-site", choices=["Strict", "Lax", "None"], help="SameSite policy")

    p = sub.add_parser("block_patterns", help="Block URL patterns")
    p.add_argument("patterns", nargs="*", default=[], help="URL patterns to block (e.g., *.css *analytics*)")

    p = sub.add_parser("form_fill", help="Fill multiple form fields")
    p.add_argument("fields", nargs="+", help="field=value pairs (e.g., email=user@example.com)")

    p = sub.add_parser("upload_file", help="Upload file via file input")
    p.add_argument("selector", help="CSS selector for <input type=file>")
    p.add_argument("file_paths", nargs="+", help="File path(s) to upload")

    p = sub.add_parser("console", help="Read browser console output")
    p.add_argument("--limit", type=int, default=20, help="Max messages (default: 20)")

    sub.add_parser("back", help="Navigate back in history")

    sub.add_parser("forward", help="Navigate forward in history")

    p = sub.add_parser("refresh", help="Reload page")
    p.add_argument("--hard", action="store_true", help="Bypass cache")

    sub.add_parser("get_cookies", help="Read all cookies")

    sub.add_parser("clear_cookies", help="Clear all cookies")

    p = sub.add_parser("wait_selector", help="Wait for CSS selector to appear")
    p.add_argument("selector", help="CSS selector to wait for")
    p.add_argument("--timeout", type=int, default=15, help="Max seconds to wait")
    p.add_argument("--state", choices=["visible", "attached"], default="visible", help="'visible' (default) or 'attached' (exists in DOM)")

    p = sub.add_parser("wait_nav", help="Wait for page navigation")
    p.add_argument("--timeout", type=int, default=30, help="Max seconds to wait")

    sub.add_parser("list_tabs", help="List all open tabs")

    p = sub.add_parser("switch_tab", help="Switch to a tab")
    p.add_argument("tab_id", type=int, help="Tab ID to switch to")

    p = sub.add_parser("create_tab", help="Create new tab")
    p.add_argument("--url", default="about:blank", help="URL to open")
    p.add_argument("--no-active", dest="active", action="store_false", default=True, help="Don't make the tab active")

    p = sub.add_parser("close_tab", help="Close a tab")
    p.add_argument("tab_id", type=int, help="Tab ID to close")

    sub.add_parser("get_text", help="Get page text content")

    sub.add_parser("get_title", help="Get page title")

    p = sub.add_parser("get_downloads", help="List recent downloads")
    p.add_argument("--limit", type=int, default=20, help="Max downloads (default: 20)")

    p = sub.add_parser("hover", help="Hover over element by CSS selector")
    p.add_argument("selector", help="CSS selector")

    p = sub.add_parser("select_option", help="Select option in dropdown")
    p.add_argument("selector", help="CSS selector for <select> element")
    p.add_argument("text", help="Visible text of option to select")

    p = sub.add_parser("fill_by_label", help="Fill form field by label/placeholder")
    p.add_argument("label", help="Label text, placeholder, or aria-label")
    p.add_argument("text", help="Text to type")

    p = sub.add_parser("click_text", help="Click element by visible text")
    p.add_argument("text", help="Text to search for (case-insensitive)")
    p.add_argument("--tag", help="Restrict to tag (e.g., button, a)")

    p = sub.add_parser("key", help="Press keyboard key")
    p.add_argument("key", help="Key name (e.g., Escape, Tab, ArrowDown)")

    p = sub.add_parser("scroll", help="Scroll page")
    p.add_argument("--dir", choices=["up", "down", "left", "right"], default="down", help="Scroll direction")
    p.add_argument("--amount", type=int, default=0, help="Pixels to scroll (0 = viewport/2)")

    p = sub.add_parser("response_body", help="Query captured response bodies")
    p.add_argument("--limit", type=int, default=5, help="Max bodies (default: 5)")
    p.add_argument("--include-body", action="store_true", help="Include full body content")
    p.add_argument("--since", type=float, default=0, help="Only after timestamp (epoch ms)")

    p = sub.add_parser("network_requests", help="Query captured network requests")
    p.add_argument("--event", choices=["completed", "error", "beforeRequest", "beforeSendHeaders", "headersReceived"], help="Filter by event type")
    p.add_argument("--since", type=float, default=0, help="Only after timestamp (epoch ms)")
    p.add_argument("--limit", type=int, default=20, help="Max events (default: 20)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.server:
        SERVER = args.server

    if args.command == "status":
        print(f"CDP Bridge CLI v{VERSION}")
        print(f"Server: {SERVER}")
        print()
        try:
            with urllib.request.urlopen(f"{SERVER}/status", timeout=5) as resp:
                print(json.dumps(json.loads(resp.read().decode()), indent=2))
        except Exception as e:
            print(f"Error: {e}")

    elif args.command == "navigate":
        cmd_navigate(args.url)

    elif args.command == "eval":
        cmd_eval(args.expression)

    elif args.command == "screenshot":
        cmd_screenshot()

    elif args.command == "click":
        cmd_click(args.selector)

    elif args.command == "type":
        cmd_type(args.selector, args.text)

    elif args.command == "enter":
        cmd_type_enter()

    elif args.command == "send":
        params = json.loads(args.params) if args.params else {}
        result = send_cdp(args.method, params)
        print(json.dumps(result, indent=2))

    elif args.command == "watch":
        watch_events(SERVER)

    # --- New command dispatch ---

    elif args.command == "snapshot":
        cmd_snapshot(full=args.full)

    elif args.command == "find":
        cmd_find(args.query, args.selector, args.max)

    elif args.command == "pdf":
        cmd_pdf(landscape=args.landscape, scale=args.scale)

    elif args.command == "dismiss_dialog":
        cmd_dismiss_dialog(accept=args.accept, prompt_text=getattr(args, "prompt_text", ""))

    elif args.command == "set_cookie":
        cmd_set_cookie(
            args.name, args.value, url=args.url, domain=args.domain, path=args.path, secure=args.secure, http_only=args.http_only, same_site=args.same_site
        )

    elif args.command == "block_patterns":
        cmd_block_patterns(args.patterns)

    elif args.command == "form_fill":
        fields = {}
        for f in args.fields:
            if "=" in f:
                k, v = f.split("=", 1)
                fields[k] = v
            else:
                print(f"Skipping invalid field spec: {f}")
        if fields:
            cmd_form_fill(fields)

    elif args.command == "upload_file":
        cmd_upload_file(args.selector, args.file_paths)

    elif args.command == "console":
        cmd_console(limit=args.limit)

    elif args.command == "back":
        cmd_back()

    elif args.command == "forward":
        cmd_forward()

    elif args.command == "refresh":
        cmd_refresh(hard_reload=args.hard)

    elif args.command == "get_cookies":
        cmd_get_cookies()

    elif args.command == "clear_cookies":
        cmd_clear_cookies()

    elif args.command == "wait_selector":
        cmd_wait_selector(args.selector, timeout=args.timeout, state=args.state)

    elif args.command == "wait_nav":
        cmd_wait_for_navigation(timeout=args.timeout)

    elif args.command == "list_tabs":
        cmd_list_tabs()

    elif args.command == "switch_tab":
        cmd_switch_tab(args.tab_id)

    elif args.command == "create_tab":
        cmd_create_tab(url=args.url, active=args.active)

    elif args.command == "close_tab":
        cmd_close_tab(args.tab_id)

    elif args.command == "get_text":
        cmd_get_text()

    elif args.command == "get_title":
        cmd_get_title()

    elif args.command == "get_downloads":
        cmd_get_downloads(limit=args.limit)

    elif args.command == "hover":
        cmd_hover(args.selector)

    elif args.command == "select_option":
        cmd_select_option(args.selector, args.text)

    elif args.command == "fill_by_label":
        cmd_fill_by_label(args.label, args.text)

    elif args.command == "click_text":
        cmd_click_text(args.text, tag=getattr(args, "tag", None))

    elif args.command == "key":
        cmd_key(args.key)

    elif args.command == "scroll":
        cmd_scroll(direction=args.dir, amount=args.amount)

    elif args.command == "response_body":
        cmd_response_body(limit=args.limit, include_body=args.include_body, since=args.since)

    elif args.command == "network_requests":
        cmd_network_requests(event=args.event, since=args.since, limit=args.limit)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
