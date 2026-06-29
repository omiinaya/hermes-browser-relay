#!/usr/bin/env python3
"""
CDP Relay Server — Bridges Chrome DevTools Protocol between Chrome extension
and Hermes Agent via WebSocket (extension) + HTTP (agent).

Architecture:
    Hermes Agent ──HTTP POST /cdp──> server ──WS──> Chrome Extension ──CDP──> Chrome tab
    Hermes Agent <──HTTP response── server <──WS── Chrome Extension <──CDP── Chrome tab

Features:
    - WebSocket heartbeat (15s ping) to detect stale connections fast
    - Stale pending_requests auto-sweep (entries older than 120s)
    - Graceful shutdown on SIGTERM/SIGINT
    - Event subscriber isolation (slow subscribers don't block the relay)

Usage:
    python server.py          # Start on port 9223
    python server.py --port 9222
"""

import argparse
import asyncio
import json
import logging
import signal
import time
from collections import deque
from json import JSONDecodeError
from pathlib import Path

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cdp-relay")

# Heartbeat configuration
HEARTBEAT_INTERVAL = 15       # Send ping every 15s
HEARTBEAT_TIMEOUT = 45        # Consider disconnected after 45s of silence
STALE_REQUEST_TIMEOUT = 120   # Auto-cancel pending requests older than 120s

# Event capture
CAPTURE_DIR = Path("/tmp/hermes-captures")
CAPTURE_FILE = CAPTURE_DIR / "capture.jsonl"
EVENT_BUFFER_SIZE = 1000
NAV_BUFFER_SIZE = 200
_event_buffer = deque(maxlen=EVENT_BUFFER_SIZE)
_nav_buffer = deque(maxlen=NAV_BUFFER_SIZE)

INTERESTING_WEBREQUEST_EVENTS = frozenset({"completed", "error"})
INTERESTING_CDP_EVENTS = frozenset({"Network.responseReceived"})

# State
extension_ws = None
extension_info = {}
pending_requests = {}  # id -> {"future": asyncio.Future, "created_at": float}
event_subscribers = []  # list of callbacks for forwarding events
tab_id = None
last_message_time = 0.0
server_start_time = 0.0
_heartbeat_task = None
_stale_sweep_task = None


def _now():
    return time.time()


async def heartbeat_loop():
    """Periodic ping to detect stale WebSocket connections."""
    global last_message_time
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        ws = extension_ws
        if ws is None or ws.closed:
            continue
        try:
            await ws.send_json({"type": "ping"})
        except Exception:
            log.warning("Heartbeat send failed — closing stale connection")
            try:
                await ws.close()
            except Exception:
                pass
            continue

        # Check if we've heard anything within the timeout window
        elapsed = _now() - last_message_time
        if elapsed > HEARTBEAT_TIMEOUT:
            log.warning(
                f"No message from extension in {elapsed:.0f}s (> {HEARTBEAT_TIMEOUT}s) — closing"
            )
            try:
                await ws.close()
            except Exception:
                pass


async def stale_request_sweep():
    """Periodically clean up pending CDP requests that have timed out."""
    while True:
        await asyncio.sleep(30)  # Check every 30s
        now = _now()
        stale_ids = [
            cdp_id
            for cdp_id, entry in list(pending_requests.items())
            if now - entry["created_at"] > STALE_REQUEST_TIMEOUT
        ]
        for cdp_id in stale_ids:
            entry = pending_requests.pop(cdp_id, None)
            if entry and not entry["future"].done():
                entry["future"].set_exception(
                    Exception(f"Request {cdp_id} timed out after {STALE_REQUEST_TIMEOUT}s")
                )
                log.warning(f"Swept stale pending request {cdp_id} ({STALE_REQUEST_TIMEOUT}s)")


def _capture_event(data: dict) -> None:
    """Write event to capture file and in-memory buffer."""
    if "ts" not in data:
        data["_received"] = time.time() * 1000
    # Write to file
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CAPTURE_FILE, "a") as f:
        f.write(json.dumps(data, default=str) + "\n")
    # Push to buffers
    _event_buffer.append(data)
    msg_type = data.get("type", "")
    if msg_type in ("tab", "download", "response_body"):
        _nav_buffer.append(data)
    elif msg_type == "webrequest" and data.get("event") in INTERESTING_WEBREQUEST_EVENTS:
        _nav_buffer.append(data)
    elif msg_type == "event" and data.get("method") in INTERESTING_CDP_EVENTS:
        _nav_buffer.append(data)


async def handle_extension(request):
    """WebSocket handler for Chrome extension connection."""
    global extension_ws, extension_info, tab_id, last_message_time

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    extension_ws = ws
    last_message_time = _now()
    remote = request.remote
    log.info(f"Extension connected from {remote}")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                last_message_time = _now()
                msg_type = data.get("type", "")
                msg_id = data.get("id")

                if msg_type == "hello":
                    extension_info = data.get("info", {})
                    tab_id = data.get("tabId")
                    log.info(f"Extension attached to tab {tab_id}")
                    continue

                if msg_type in ("pong", "keepalive"):
                    # Heartbeat response / SW keepalive — already tracked via last_message_time
                    continue

                if msg_type == "ping":
                    # Server-side ping from extension (if extension sends proactive pings)
                    try:
                        await ws.send_json({"type": "pong"})
                    except Exception:
                        pass
                    continue

                if msg_type == "event":
                    method = data.get("method", "")
                    log.debug(f"CDP event: {method}")
                    _capture_event(data)
                    # Forward to event subscribers with isolation
                    for cb in list(event_subscribers):
                        try:
                            cb(data)
                        except Exception:
                            log.exception("Event subscriber error")
                    continue

                if msg_type == "webrequest":
                    url = data.get("url", "")
                    evt = data.get("event", "")
                    log.debug(f"WebRequest {evt}: {url[:100]}")
                    _capture_event(data)
                    for cb in list(event_subscribers):
                        try:
                            cb(data)
                        except Exception:
                            log.exception("Event subscriber error")
                    continue

                if msg_type in ("download", "tab"):
                    _capture_event(data)
                    for cb in list(event_subscribers):
                        try:
                            cb(data)
                        except Exception:
                            log.exception("Event subscriber error")
                    continue

                if msg_type == "body":
                    _capture_event(data)
                    continue

                if msg_type == "batch":
                    for evt in data.get("events", []):
                        _capture_event(evt)
                    continue

                if msg_id and msg_id in pending_requests:
                    entry = pending_requests.pop(msg_id, None)
                    if entry and not entry["future"].done():
                        entry["future"].set_result(data)
                    continue

                log.warning(f"Unhandled message type: {msg_type}")

            elif msg.type == web.WSMsgType.ERROR:
                log.error(f"WebSocket error: {ws.exception()}")

    except asyncio.CancelledError:
        pass
    finally:
        log.info(f"Extension disconnected (was from {remote})")
        extension_ws = None
        extension_info = {}
        tab_id = None
        last_message_time = 0.0
        # Cancel all pending requests
        for entry in pending_requests.values():
            if not entry["future"].done():
                entry["future"].set_exception(Exception("Extension disconnected"))
        pending_requests.clear()


async def _process_cdp_command(body):
    """Shared CDP command execution logic (used by both POST and GET handlers)."""
    if not extension_ws or extension_ws.closed:
        return web.json_response(
            {"error": "Extension not connected", "hint": "Open Chrome extension and click Attach"},
            status=503,
        )

    method = body.get("method")
    params = body.get("params", {})

    if not method:
        return web.json_response({"error": "Missing 'method'"}, status=400)

    # Generate unique ID
    cdp_id = int(time.time() * 1000000) % (2**31)

    # Create future for response
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    # Send CDP command via extension
    cmd = {"id": cdp_id, "method": method, "params": params}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response(
            {"error": "Failed to send CDP command to extension"},
            status=502,
        )

    log.info(f"CDP -> {method} (id={cdp_id})")

    # Wait for response with timeout
    timeout = body.get("timeout", 60)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response(
            {"error": f"CDP command timed out after {timeout}s", "method": method},
            status=504,
        )


async def handle_cdp_post(request):
    """HTTP endpoint: send a CDP command to Chrome."""
    try:
        body = await request.json()
    except (JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    return await _process_cdp_command(body)


async def handle_status(request):
    """HTTP endpoint: server status."""
    now = _now()
    uptime = now - server_start_time
    return web.json_response({
        "version": "1.4.0",
        "extension_connected": extension_ws is not None and not extension_ws.closed,
        "tab_id": tab_id,
        "extension_info": extension_info,
        "pending_requests": len(pending_requests),
        "uptime": uptime,
        "uptime_str": f"{uptime:.0f}s",
        "last_message_ago": (now - last_message_time) if last_message_time > 0 else None,
    })


async def handle_cdp_get(request):
    """HTTP endpoint: convenience GET with query param method."""
    method = request.query.get("method")
    url = request.query.get("url")
    if method:
        params = {}
        if url:
            params["url"] = url
        body = {"method": method, "params": params}
        return await _process_cdp_command(body)
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

    queue = asyncio.Queue(maxsize=500)

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
    except (asyncio.CancelledError, ConnectionResetError, ConnectionAbortedError):
        pass
    finally:
        try:
            event_subscribers.remove(subscriber)
        except ValueError:
            pass

    return response


async def handle_recent(request):
    """Return recent events from in-memory capture buffer."""
    # ?raw=true returns raw JSONL lines as text/plain
    if request.query.get("raw") == "true":
        limit = int(request.query.get("limit", 500))
        limit = min(limit, 500)
        mode = request.query.get("mode", "nav")
        buf = _event_buffer if mode == "all" else _nav_buffer
        events = list(buf)[-limit:] if limit > 0 else []
        events.reverse()
        lines = "\n".join(json.dumps(e, default=str) for e in events)
        return web.Response(text=lines, content_type="text/plain")

    limit = int(request.query.get("limit", 20))
    limit = min(limit, 500)
    mode = request.query.get("mode", "nav")

    buf = _event_buffer if mode == "all" else _nav_buffer
    events = list(buf)[-limit:] if limit > 0 else []
    events.reverse()

    return web.json_response({
        "count": len(events),
        "buffer_fill": len(buf),
        "events": events,
    })


async def handle_tabs_list(request):
    """HTTP endpoint: list all open tabs from the extension."""
    if not extension_ws or extension_ws.closed:
        return web.json_response(
            {"error": "Extension not connected", "hint": "Open Chrome extension and click Attach"},
            status=503,
        )

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "tabCommand": "list"}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send tab list command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        tabs = result.get("tabs", [])
        return web.json_response({"count": len(tabs), "tabs": tabs})
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Tab list timed out"}, status=504)


async def handle_tabs_switch(request):
    """HTTP endpoint: switch to a specific tab by tabId."""
    if not extension_ws or extension_ws.closed:
        return web.json_response(
            {"error": "Extension not connected", "hint": "Open Chrome extension and click Attach"},
            status=503,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    tab_id = body.get("tabId")
    if tab_id is None:
        return web.json_response({"error": "Missing 'tabId'"}, status=400)

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "tabCommand": "switch", "tabId": tab_id}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send tab switch command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Tab switch timed out"}, status=504)


async def handle_block_patterns(request):
    """HTTP endpoint: set blocked URL patterns for request blocking via DNR."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    patterns = body.get("patterns", [])
    if not isinstance(patterns, list):
        return web.json_response({"error": "patterns must be an array of strings"}, status=400)

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "blockCommand": "set", "patterns": patterns}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send block patterns"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Block patterns set timed out"}, status=504)


async def handle_block_patterns_get(request):
    """HTTP endpoint: get current blocked URL patterns."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "blockCommand": "get"}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to get block patterns"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=5)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Block patterns get timed out"}, status=504)


async def shutdown():
    """Clean shutdown — dump buffer, close WS, cancel pending, stop background tasks."""
    log.info("Shutting down...")

    # Dump in-memory event buffer to JSONL file before shutdown
    if _event_buffer:
        try:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CAPTURE_FILE, "a") as f:
                for evt in _event_buffer:
                    f.write(json.dumps(evt, default=str) + "\n")
            log.info(f"Dumped {len(_event_buffer)} buffered events to {CAPTURE_FILE}")
        except Exception:
            log.exception("Failed to dump event buffer on shutdown")

    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()

    if _stale_sweep_task and not _stale_sweep_task.done():
        _stale_sweep_task.cancel()

    ws = extension_ws
    if ws and not ws.closed:
        try:
            await ws.close()
        except Exception:
            pass

    for entry in pending_requests.values():
        if not entry["future"].done():
            entry["future"].set_exception(Exception("Server shutting down"))
    pending_requests.clear()

    log.info("Shutdown complete")


async def main():
    global server_start_time, _heartbeat_task, _stale_sweep_task

    parser = argparse.ArgumentParser(description="CDP Relay Server")
    parser.add_argument("--port", type=int, default=9223, help="Server port (default: 9223)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    server_start_time = _now()

    app = web.Application()
    app.router.add_get("/status", handle_status)
    app.router.add_get("/", handle_status)
    app.router.add_get("/cdp", handle_cdp_get)
    app.router.add_post("/cdp", handle_cdp_post)
    app.router.add_get("/ws", handle_extension)
    app.router.add_get("/events", handle_events_sse)
    app.router.add_get("/recent", handle_recent)
    app.router.add_get("/stats", handle_status)
    app.router.add_get("/tabs", handle_tabs_list)
    app.router.add_post("/tabs/switch", handle_tabs_switch)
    app.router.add_post("/block/patterns", handle_block_patterns)
    app.router.add_get("/block/patterns", handle_block_patterns_get)

    print()
    print("  ╔═══════════════════════════════════════╗")
    print("  ║     CDP Relay Server v1.4.0           ║")
    print("  ╚═══════════════════════════════════════╝")
    print()
    print(f"  Extension WS: ws://{args.host}:{args.port}/ws")
    print(f"  CDP API:      http://{args.host}:{args.port}/cdp")
    print(f"  Status:       http://{args.host}:{args.port}/status")
    print(f"  Events (SSE): http://{args.host}:{args.port}/events")
    print(f"  Recent:       http://{args.host}:{args.port}/recent?limit=20&mode=nav")
    print(f"  Tabs:         http://{args.host}:{args.port}/tabs")
    print(f"  Block:        http://{args.host}:{args.port}/block/patterns")
    print()
    print("  Install extension → chrome://extensions/ → Load unpacked")
    print(f"  → {Path(__file__).parent / 'extension'}")
    print()
    print("  Then: attach to a tab, then POST commands to /cdp")
    print(f"  Heartbeat: every {HEARTBEAT_INTERVAL}s, timeout at {HEARTBEAT_TIMEOUT}s")
    print()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    print(f"  Listening on http://{args.host}:{args.port}")
    print()

    # Start background tasks
    _heartbeat_task = asyncio.create_task(heartbeat_loop())
    _stale_sweep_task = asyncio.create_task(stale_request_sweep())

    # Set up signal handlers for graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("Received termination signal")
        asyncio.create_task(shutdown())
        asyncio.create_task(runner.cleanup())
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass
    except Exception:
        pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt")
        await shutdown()
        await runner.cleanup()

    log.info("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())
