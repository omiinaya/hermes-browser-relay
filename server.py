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
import itertools
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
HEARTBEAT_INTERVAL = 15  # Send ping every 15s
HEARTBEAT_TIMEOUT = 45  # Consider disconnected after 45s of silence
STALE_REQUEST_TIMEOUT = 120  # Auto-cancel pending requests older than 120s

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
_cdp_id_counter = itertools.count()

# Rate limiter state — per-IP token bucket
_rate_limiters = {}  # ip -> {"tokens": float, "last_refill": float}
RATE_LIMIT_PER_SEC = 30
RATE_LIMIT_BURST = 50

# Read version from VERSION file in the same directory as this script
_HERE = Path(__file__).resolve().parent
try:
    VERSION = _HERE.joinpath("VERSION").read_text().strip()
except Exception:
    VERSION = "unknown"


@web.middleware
async def cors_middleware(request, handler):
    """Add CORS headers to all responses and handle preflight OPTIONS requests."""
    if request.method == "OPTIONS":
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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
            log.warning(f"No message from extension in {elapsed:.0f}s (> {HEARTBEAT_TIMEOUT}s) — closing")
            try:
                await ws.close()
            except Exception:
                pass


async def stale_request_sweep():
    """Periodically clean up pending CDP requests that have timed out."""
    while True:
        await asyncio.sleep(30)  # Check every 30s
        now = _now()
        stale_ids = [cdp_id for cdp_id, entry in list(pending_requests.items()) if now - entry["created_at"] > STALE_REQUEST_TIMEOUT]
        for cdp_id in stale_ids:
            entry = pending_requests.pop(cdp_id, None)
            if entry and not entry["future"].done():
                entry["future"].set_exception(Exception(f"Request {cdp_id} timed out after {STALE_REQUEST_TIMEOUT}s"))
                log.warning(f"Swept stale pending request {cdp_id} ({STALE_REQUEST_TIMEOUT}s)")


def _capture_event(data: dict) -> None:
    """Write event to capture file and in-memory buffer."""
    if "ts" not in data:
        data["_received"] = time.time() * 1000
    # Rotate if file exceeds 10MB
    MAX_CAPTURE_SIZE = 10 * 1024 * 1024  # 10MB
    if CAPTURE_FILE.exists() and CAPTURE_FILE.stat().st_size > MAX_CAPTURE_SIZE:
        rotated = CAPTURE_FILE.with_suffix(".jsonl.1")
        rotated.unlink(missing_ok=True)
        CAPTURE_FILE.rename(rotated)
        log.info(f"Rotated capture file: {CAPTURE_FILE.name} -> {rotated.name}")
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

                if msg_type in ("download", "tab", "page_event"):
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


def _http_status_for_error(error_msg: str) -> int:
    """Infer HTTP status code from an error message."""
    if error_msg.startswith("Extension not connected"):
        return 503
    if error_msg.startswith("Missing"):
        return 400
    if error_msg.startswith("Failed to send"):
        return 502
    if error_msg.startswith("CDP command timed out"):
        return 504
    return 400


async def _execute_cdp_raw(body: dict) -> dict:
    """Execute a CDP command and return the raw result dict.
    This is the core execution logic, independent of transport.
    """
    if not extension_ws or extension_ws.closed:
        return {"error": "Extension not connected", "hint": "Open Chrome extension and click Attach"}

    method = body.get("method")
    params = body.get("params", {})

    if not method:
        return {"error": "Missing 'method'"}

    # Generate unique ID
    cdp_id = next(_cdp_id_counter)

    # Create future for response
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    # Send CDP command via extension
    cmd = {"id": cdp_id, "method": method, "params": params}
    # Preserve _hermes routing fields and other extras
    for extra_key in ("_hermes", "_hermesFunc", "_hermesArgs"):
        if extra_key in body:
            cmd[extra_key] = body[extra_key]
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return {"error": "Failed to send CDP command to extension"}

    log.info(f"CDP -> {method} (id={cdp_id})")

    # Wait for response with timeout
    timeout = body.get("timeout", 60)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return {"error": f"CDP command timed out after {timeout}s", "method": method}


async def _process_cdp_command(body):
    """HTTP handler version of CDP command. Wraps result with HTTP status codes."""
    result = await _execute_cdp_raw(body)
    if isinstance(result, dict) and "error" in result:
        return web.json_response(result, status=_http_status_for_error(result.get("error", "")))
    return web.json_response(result)


async def handle_mcp_connection(request):
    """WebSocket handler for MCP server connection (persistent command channel).

    The MCP server connects via WS (ws://host:9223/mcp-ws) and sends CDP commands
    as JSON messages. Each message is processed via _execute_cdp_raw (same logic as
    HTTP POST /cdp) and the response is sent back over the same WS connection.

    This eliminates the HTTP POST overhead for every MCP tool call.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    remote = request.remote
    log.info(f"MCP server connected from {remote}")
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                # Process as CDP command (same logic as HTTP POST /cdp)
                result = await _execute_cdp_raw(data)
                # Send response back via WS
                try:
                    await ws.send_json(result)
                except Exception:
                    log.warning("Failed to send WS response to MCP server")
                    break
            elif msg.type == web.WSMsgType.ERROR:
                log.error(f"MCP WebSocket error: {ws.exception()}")
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("MCP WebSocket handler error")
    finally:
        log.info(f"MCP server disconnected (was from {remote})")


def _check_rate_limit(request) -> bool:
    """Token-bucket rate limiter: returns True if request is allowed, False if rate-limited."""
    ip = request.remote or "unknown"
    now = time.time()
    state = _rate_limiters.get(ip)
    if state is None:
        _rate_limiters[ip] = {"tokens": RATE_LIMIT_BURST - 1, "last_refill": now}
        return True

    # Refill tokens based on elapsed time
    elapsed = now - state["last_refill"]
    state["tokens"] = min(RATE_LIMIT_BURST, state["tokens"] + elapsed * RATE_LIMIT_PER_SEC)
    state["last_refill"] = now

    if state["tokens"] >= 1:
        state["tokens"] -= 1
        return True

    return False


async def handle_cdp_post(request):
    """HTTP endpoint: send a CDP command to Chrome."""
    if not _check_rate_limit(request):
        return web.json_response({"error": "Rate limit exceeded"}, status=429)
    try:
        body = await request.json()
    except (JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    return await _process_cdp_command(body)


async def handle_status(request):
    """HTTP endpoint: server status."""
    now = _now()
    uptime = now - server_start_time
    return web.json_response(
        {
            "version": VERSION,
            "extension_connected": extension_ws is not None and not extension_ws.closed,
            "tab_id": tab_id,
            "extension_info": extension_info,
            "pending_requests": len(pending_requests),
            "uptime": uptime,
            "uptime_str": f"{uptime:.0f}s",
            "last_message_ago": (now - last_message_time) if last_message_time > 0 else None,
        }
    )


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
            log.warning("Event subscriber queue full — dropping event")

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


async def handle_wait_navigation(request):
    """Long-poll endpoint: block until a tab.navigated event occurs.

    Query params:
      since (float): epoch ms filter — only events after this timestamp
      tab_id (int): optional — filter for a specific tab
      timeout (int): max seconds to wait (default 30, max 60)
    """
    since = float(request.query.get("since", 0))
    tab_id = request.query.get("tab_id")
    if tab_id:
        tab_id = int(tab_id)
    timeout = min(int(request.query.get("timeout", 30)), 60)

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    def subscriber(data):
        typ = data.get("type", "")
        evt = data.get("event", "")
        if typ == "tab" and evt == "navigated":
            if tab_id is None or data.get("tabId") == tab_id:
                # Check recency
                received = data.get("_received", 0)
                if received >= since:
                    try:
                        queue.put_nowait(("ok", data))
                    except asyncio.QueueFull:
                        pass

    event_subscribers.append(subscriber)
    try:
        result = None
        try:
            result = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        if result:
            _, data = result
            return web.json_response({
                "navigated": True,
                "url": data.get("url", ""),
                "title": data.get("title", ""),
                "tabId": data.get("tabId"),
            })

        return web.json_response({
            "navigated": False,
            "error": f"Navigation timeout after {timeout}s",
        })
    finally:
        try:
            event_subscribers.remove(subscriber)
        except ValueError:
            pass


async def handle_recent(request):
    """Return recent events from in-memory capture buffer."""
    limit = int(request.query.get("limit", 20))
    limit = min(limit, 500)
    mode = request.query.get("mode", "nav")
    event_type = request.query.get("type")  # Optional: filter by event type
    since = request.query.get("since")  # Optional: filter by timestamp (ms)

    buf = _event_buffer if mode == "all" else _nav_buffer
    events = list(buf) if limit > 0 else []
    events.reverse()

    # Apply filters
    if event_type:
        events = [e for e in events if e.get("type") == event_type]
    if since:
        try:
            since_ts = float(since)
            events = [e for e in events if (e.get("_received") or 0) >= since_ts]
        except ValueError:
            pass

    events = events[:limit]

    # ?raw=true returns raw JSONL lines as text/plain
    if request.query.get("raw") == "true":
        lines = "\n".join(json.dumps(e, default=str) for e in events)
        return web.Response(text=lines, content_type="text/plain")

    return web.json_response(
        {
            "count": len(events),
            "buffer_fill": len(buf),
            "events": events,
        }
    )


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


async def handle_tabs_attach(request):
    """HTTP endpoint: attach debugger to a specific tab (additive)."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

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

    cmd = {"id": cdp_id, "tabCommand": "attach", "tabId": tab_id}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send attach command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Attach timed out"}, status=504)


async def handle_tabs_detach(request):
    """HTTP endpoint: detach debugger from a specific tab."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

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

    cmd = {"id": cdp_id, "tabCommand": "detach", "tabId": tab_id}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send detach command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Detach timed out"}, status=504)


async def handle_tabs_attachments(request):
    """HTTP endpoint: list currently attached tab IDs."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "tabCommand": "list-attachments"}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to list attachments"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=5)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "List attachments timed out"}, status=504)


async def handle_cdp_post_tab(request):
    """HTTP endpoint: send a CDP command to a specific tab.
    POST /cdp/{tabId} with same JSON body as /cdp."""
    tab_id_str = request.match_info.get("tabId")
    try:
        tab_id = int(tab_id_str)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid tabId"}, status=400)

    try:
        body = await request.json()
    except (JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    body["tabId"] = tab_id
    return await _process_cdp_command(body)


async def handle_tabs_create(request):
    """HTTP endpoint: create a new tab. POST /tabs/create with {url, active}."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    cdp_id = int(time.time() * 1000000) % (2**31)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[cdp_id] = {"future": future, "created_at": _now()}

    cmd = {"id": cdp_id, "tabCommand": "create", "url": body.get("url", "about:blank"), "active": body.get("active", True)}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send create tab command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Create tab timed out"}, status=504)


async def handle_tabs_close(request):
    """HTTP endpoint: close a tab. POST /tabs/close with {tabId}."""
    if not extension_ws or extension_ws.closed:
        return web.json_response({"error": "Extension not connected"}, status=503)

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

    cmd = {"id": cdp_id, "tabCommand": "close", "tabId": tab_id}
    try:
        await extension_ws.send_json(cmd)
    except Exception:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Failed to send close tab command"}, status=502)

    try:
        result = await asyncio.wait_for(future, timeout=10)
        return web.json_response(result)
    except asyncio.TimeoutError:
        pending_requests.pop(cdp_id, None)
        return web.json_response({"error": "Close tab timed out"}, status=504)


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

    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/status", handle_status)
    app.router.add_get("/", handle_status)
    app.router.add_get("/cdp", handle_cdp_get)
    app.router.add_post("/cdp", handle_cdp_post)
    app.router.add_get("/ws", handle_extension)
    app.router.add_get("/mcp-ws", handle_mcp_connection)
    app.router.add_get("/events", handle_events_sse)
    app.router.add_get("/wait-nav", handle_wait_navigation)
    app.router.add_get("/recent", handle_recent)
    app.router.add_get("/stats", handle_status)
    app.router.add_get("/tabs", handle_tabs_list)
    app.router.add_post("/tabs/switch", handle_tabs_switch)
    app.router.add_post("/block/patterns", handle_block_patterns)
    app.router.add_get("/block/patterns", handle_block_patterns_get)
    app.router.add_post("/tabs/attach", handle_tabs_attach)
    app.router.add_post("/tabs/detach", handle_tabs_detach)
    app.router.add_get("/tabs/attachments", handle_tabs_attachments)
    app.router.add_post("/cdp/{tabId}", handle_cdp_post_tab)
    app.router.add_post("/tabs/create", handle_tabs_create)
    app.router.add_post("/tabs/close", handle_tabs_close)

    print()
    print("  ╔═══════════════════════════════════════╗")
    print(f"  ║     CDP Relay Server v{VERSION}           ║")
    print("  ╚═══════════════════════════════════════╝")
    print()
    print(f"  Extension WS: ws://{args.host}:{args.port}/ws")
    print(f"  MCP Server:   ws://{args.host}:{args.port}/mcp-ws  (persistent, no HTTP overhead)")
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
    print("  or: MCP server connects to ws://.../mcp-ws (persistent, no HTTP POST overhead)")
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
