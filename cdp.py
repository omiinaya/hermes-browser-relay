#!/usr/bin/env python3
"""
CDP Client — Send CDP commands to Chrome via the relay server.

Usage:
    cdp Page.navigate '{"url": "https://example.com"}'
    cdp Runtime.evaluate '{"expression": "document.title"}'
    cdp Network.getCookies '{}' | jq .
    cdp --watch
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

SERVER = "http://127.0.0.1:9223"


def send_cdp(method, params=None, timeout=60):
    """Send a CDP command and return the response."""
    if params is None:
        params = {}
    
    body = json.dumps({"method": method, "params": params, "timeout": timeout}).encode()
    req = urllib.request.Request(
        f"{SERVER}/cdp",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else "{}"
        return {"error": f"HTTP {e.code}", "detail": json.loads(error_body) if error_body else {}}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}", "hint": "Is the CDP relay server running?"}
    except Exception as e:
        return {"error": str(e)}


def watch_events():
    """Stream CDP events via SSE."""
    import http.client
    
    conn = http.client.HTTPConnection("127.0.0.1", 9223, timeout=300)
    conn.request("GET", "/events")
    resp = conn.getresponse()
    
    print("Watching CDP events... (Ctrl+C to stop)\n", file=sys.stderr)
    
    buffer = ""
    try:
        while True:
            chunk = resp.read(1)
            if not chunk:
                break
            char = chunk.decode()
            if char == '\n':
                if buffer.startswith("data: "):
                    try:
                        event = json.loads(buffer[6:])
                        method = event.get("method", "?")
                        params_summary = json.dumps(event.get("params", {}))[:200]
                        print(f"[{method}] {params_summary}")
                    except json.JSONDecodeError:
                        print(buffer)
                buffer = ""
            else:
                buffer += char
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


def cmd_navigate(url):
    """Navigate to a URL."""
    result = send_cdp("Page.navigate", {"url": url})
    print(json.dumps(result, indent=2))


def cmd_eval(expression):
    """Evaluate JavaScript in the page."""
    result = send_cdp("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True
    })
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
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return {{ error: 'Element not found: {json.dumps(selector)}' }};
        const rect = el.getBoundingClientRect();
        return {{
            x: rect.x + rect.width/2,
            y: rect.y + rect.height/2,
            text: el.textContent.trim().substring(0, 100),
            tag: el.tagName
        }};
    }})()
    """
    result = send_cdp("Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True
    })
    
    try:
        coords = result["result"]["result"]["value"]
        if "error" in coords:
            print(f"Error: {coords['error']}")
            return
        x, y = coords["x"], coords["y"]
        print(f"Clicking '{coords.get('text', '?')}' at ({x:.0f}, {y:.0f})")
        
        # Dispatch mouse click via CDP
        send_cdp("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": round(x),
            "y": round(y),
            "button": "left",
            "clickCount": 1
        })
        send_cdp("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": round(x),
            "y": round(y),
            "button": "left",
            "clickCount": 1
        })
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


def main():
    parser = argparse.ArgumentParser(description="CDP Client — control Chrome via CDP relay")
    parser.add_argument("--server", default=None, help="CDP relay server (default: http://127.0.0.1:9223)")
    
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
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    global SERVER
    if args.server:
        SERVER = args.server
    
    if args.command == "status":
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
        watch_events()
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
