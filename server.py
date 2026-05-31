#!/usr/bin/env python3
"""
CDP Relay Server — Bridges Chrome DevTools Protocol between Chrome extension
and Hermes Agent via WebSocket (extension) + HTTP (agent).

Architecture:
    Hermes Agent ──HTTP POST /cdp──> server ──WS──> Chrome Extension ──CDP──> Chrome tab
    Hermes Agent <──HTTP response── server <──WS── Chrome Extension <──CDP── Chrome tab

Usage:
    python server.py          # Start on port 9223
    python server.py --port 9222
"""

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cdp-relay")

# State
extension_ws = None
extension_info = {}
pending_requests = {}  # id -> asyncio.Future
event_subscribers = []  # list of callbacks for forwarding events
tab_id = None


async def handle_extension(request):
    """WebSocket handler for Chrome extension connection."""
    global extension_ws, extension_info, tab_id

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    extension_ws = ws
    log.info("Extension connected")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                msg_type = data.get("type", "")
                msg_id = data.get("id")

                if msg_type == "hello":
                    # Extension handshake
                    extension_info = data.get("info", {})
                    tab_id = data.get("tabId")
                    log.info(f"Extension attached to tab {tab_id}")
                    continue

                if msg_type == "event":
                    # CDP event from Chrome
                    method = data.get("method", "")
                    log.debug(f"CDP event: {method}")
                    # Forward to any event subscribers
                    for cb in event_subscribers:
                        try:
                            cb(data)
                        except Exception:
                            pass
                    continue
                
                if msg_type == "webrequest":
                    # WebRequest event from Chrome (global, all tabs)
                    url = data.get("url", "")
                    evt = data.get("event", "")
                    log.debug(f"WebRequest {evt}: {url[:100]}")
                    # Forward to event subscribers
                    for cb in event_subscribers:
                        try:
                            cb(data)
                        except Exception:
                            pass
                    continue

                if msg_id and msg_id in pending_requests:
                    # Response to a pending HTTP /cdp request
                    pending_requests[msg_id].set_result(data)
                    continue

                log.warning(f"Unhandled message: {data.get('type', 'unknown')}")

            elif msg.type == web.WSMsgType.ERROR:
                log.error(f"WebSocket error: {ws.exception()}")

    except asyncio.CancelledError:
        pass
    finally:
        log.info("Extension disconnected")
        extension_ws = None
        extension_info = {}
        tab_id = None
        # Cancel all pending requests
        for fut in pending_requests.values():
            if not fut.done():
                fut.set_exception(Exception("Extension disconnected"))
        pending_requests.clear()


async def handle_cdp_post(request):
    """HTTP endpoint: send a CDP command to Chrome."""
    global extension_ws

    if not extension_ws or extension_ws.closed:
        return web.json_response(
            {"error": "Extension not connected", "hint": "Open Chrome extension and click Attach"},
            status=503,
        )

    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    method = body.get("method")
    params = body.get("params", {})

    if not method:
        return web.json_response({"error": "Missing 'method'"}, status=400)

    # Generate unique ID
    cdp_id = int(time.time() * 1000000) % (2**31)

    # Create future for response
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = future

    # Send CDP command via extension
    cmd = {"id": cdp_id, "method": method, "params": params}
    await extension_ws.send_json(cmd)
    log.info(f"CDP -> {method}")

    # Wait for response with timeout
    timeout = body.get("timeout", 60)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": f"CDP command timed out after {timeout}s", "method": method}, status=504)


async def handle_status(request):
    """HTTP endpoint: server status."""
    return web.json_response({
        "extension_connected": extension_ws is not None and not extension_ws.closed,
        "tab_id": tab_id,
        "extension_info": extension_info,
        "pending_requests": len(pending_requests),
        "uptime": time.time() - server_start_time,
    })


async def handle_static(request):
    """Serve the Chrome extension as a downloadable zip."""
    ext_dir = Path(__file__).parent / "extension"
    if not ext_dir.exists():
        return web.json_response({"error": "Extension not found"}, status=404)
    return web.FileResponse(ext_dir / "manifest.json")


async def handle_cdp_get(request):
    """HTTP endpoint: convenience GET with query param method."""
    method = request.query.get("method")
    url = request.query.get("url")
    if method:
        params = {}
        if url:
            params["url"] = url
        body = {"method": method, "params": params}
        # Forward to POST handler
        request._body = json.dumps(body).encode()
        return await handle_cdp_post(request)
    return web.json_response({"error": "Use GET ?method=Page.navigate&url=... or POST /cdp"}, status=400)


async def handle_events_sse(request):
    """SSE endpoint: stream CDP events for real-time monitoring."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    queue = asyncio.Queue()
    
    def subscriber(data):
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            pass

    event_subscribers.append(subscriber)
    try:
        while True:
            data = await queue.get()
            await response.write(f"data: {json.dumps(data)}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        event_subscribers.remove(subscriber)

    return response


server_start_time = time.time()


async def main():
    parser = argparse.ArgumentParser(description="CDP Relay Server")
    parser.add_argument("--port", type=int, default=9223, help="Server port (default: 9223)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get("/status", handle_status)
    app.router.add_get("/", handle_status)
    app.router.add_get("/cdp", handle_cdp_get)
    app.router.add_post("/cdp", handle_cdp_post)
    app.router.add_get("/ws", handle_extension)
    app.router.add_get("/events", handle_events_sse)

    print()
    print(f"  ╔═══════════════════════════════════════╗")
    print(f"  ║     CDP Relay Server v1.0             ║")
    print(f"  ╚═══════════════════════════════════════╝")
    print()
    print(f"  Extension WS: ws://127.0.0.1:{args.port}/ws")
    print(f"  CDP API:      http://127.0.0.1:{args.port}/cdp")
    print(f"  Status:       http://127.0.0.1:{args.port}/status")
    print(f"  Events (SSE): http://127.0.0.1:{args.port}/events")
    print()
    print(f"  Install extension → chrome://extensions/ → Load unpacked")
    print(f"  → {Path(__file__).parent / 'extension'}")
    print()
    print(f"  Then: attach to a tab, then POST commands to /cdp")
    print()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    print(f"  Listening on http://{args.host}:{args.port}")
    print()

    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
