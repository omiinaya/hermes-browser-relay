#!/usr/bin/env python3
"""
⚠️ DEPRECATED — Capture has been merged into server.py (the relay).

This standalone capture server is kept for backward compatibility but is no
longer maintained. The relay server at port 9223 handles all capture
functionality internally. See CHANGELOG v1.2.0 for migration details.

---
Hermes Traffic Capture Server (v1.2.0)
Receives browser events via WebSocket from the Chrome extension,
writes to JSONL, and maintains an in-memory event buffer for
real-time querying via the CDP relay server's /recent endpoint.

Handles event types from browser-relay extension:
  - webrequest (all phases: beforeRequest, completed, error, etc.)
  - download (created, changed)
  - tab (navigated)
  - event (CDP protocol events)
  - batch (buffered events from custom extensions)
  - hello / pong / ping / keepalive (connection management)

Usage:
    python capture_server.py                  # 0.0.0.0:9988 (LAN access)
    python capture_server.py --bind 127.0.0.1  # localhost only (safer)
    python capture_server.py --port 9989
"""
import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import websockets

sys.stdout.reconfigure(line_buffering=True)

VERSION = "1.2.0"

CAPTURE_DIR = Path("/tmp/hermes-captures")
CAPTURE_FILE = CAPTURE_DIR / "capture.jsonl"
DEFAULT_PORT = 9988
HEARTBEAT_INTERVAL = 15
HEARTBEAT_TIMEOUT = 45
EVENT_BUFFER_SIZE = 1000    # Full event buffer (feeds /recent?limit=N)
NAV_BUFFER_SIZE = 200       # Slim buffer of interesting events only


class CaptureServer:
    def __init__(self):
        self.clients = {}
        self.event_count = 0
        self.file_handle = None

        # In-memory event buffers for real-time querying
        self.full_buffer = deque(maxlen=EVENT_BUFFER_SIZE)
        self.nav_buffer = deque(maxlen=NAV_BUFFER_SIZE)

        self.stats = {
            'webrequest': 0,
            'downloads': 0,
            'tabs': 0,
            'cdp_events': 0,
            'errors': 0,
            'responses': 0,
        }

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self, host="0.0.0.0", port=DEFAULT_PORT):
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

        old_files = sorted(CAPTURE_DIR.glob("capture*.jsonl"), reverse=True)
        for f in old_files[4:]:
            f.unlink(missing_ok=True)

        self.file_handle = open(CAPTURE_FILE, 'a')

        print(f"📡 Capture server v{VERSION} starting on ws://{host}:{port}")
        print(f"📁 Writing to: {CAPTURE_FILE}")
        print(f"📊 Events buffer: {EVENT_BUFFER_SIZE} (nav: {NAV_BUFFER_SIZE})")
        print()

        log = logging.getLogger('capture')
        log.warning("DEPRECATED: Capture functionality has been merged into the CDP relay server (server.py :9223).")
        log.warning("This standalone capture server is kept for backward compatibility. Use :9223/recent instead.")

        async with websockets.serve(
            self.handle_client, host, port,
            ping_interval=HEARTBEAT_INTERVAL,
            ping_timeout=HEARTBEAT_TIMEOUT,
        ):
            await asyncio.Future()

    # ------------------------------------------------------------------
    # WebSocket client handler
    # ------------------------------------------------------------------

    async def handle_client(self, websocket):
        remote = websocket.remote_address
        self.clients[websocket] = {"remote": remote, "last_message": time.time()}
        print(f"🔌 Client connected: {remote}")

        try:
            async for message in websocket:
                self.clients[websocket]["last_message"] = time.time()
                try:
                    data = json.loads(message)
                    await self.process_message(data)
                except json.JSONDecodeError:
                    print(f"⚠️  Bad JSON from {remote}")
                except Exception as e:
                    print(f"⚠️  Error: {e}")
                    self.stats['errors'] += 1
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)
            print(f"🔌 Client disconnected: {remote}")

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    INTERESTING_EVENTS = frozenset({
        "tab", "download", "response_body",
        "webrequest",
    })

    INTERESTING_WEBREQUEST = frozenset({
        "completed", "error",
    })

    INTERESTING_CDP = frozenset({
        "Network.responseReceived",
    })

    def _is_interesting(self, msg_type: str, msg: dict) -> bool:
        """Determine if an event is 'interesting' enough for the nav buffer."""
        if msg_type in ("tab", "download", "response_body"):
            return True
        if msg_type == "webrequest":
            ev = msg.get("event", "")
            if ev in self.INTERESTING_WEBREQUEST:
                return True
        if msg_type == "event":
            method = msg.get("method", "")
            if method in self.INTERESTING_CDP:
                return True
        return False

    async def process_message(self, msg):
        if 'ts' not in msg:
            msg['_received'] = datetime.now(timezone.utc).timestamp() * 1000

        msg_type = msg.get('type', msg.get('action', 'unknown'))

        # Connection management - don't log these
        if msg_type in ('hello', 'pong', 'ping', 'keepalive'):
            return

        # Response body events
        if msg_type == 'response_body':
            await self.write_event(msg)
            url = msg.get('url', '')
            body = msg.get('body', '') or ''
            b64 = msg.get('base64Encoded', False)
            body_preview = body[:200] if not b64 else f'[base64: {len(body)} chars]'
            print(f"  📄 BODY: {url[:100]}")
            print(f"     Preview: {body_preview}")
            self.stats['responses'] += 1
            return

        # CDP events
        if msg_type == 'event':
            await self.write_event(msg)
            self.stats['cdp_events'] += 1
            method = msg.get('method', '')
            url = msg.get('params', {}).get('response', {}).get('url',
                   msg.get('params', {}).get('documentURL', ''))
            if 'Network.responseReceived' in method:
                print(f"  📡 CDP: {method} {url[:100]}")
                self.stats['responses'] += 1
            elif 'Network.requestWillBeSent' in method:
                pass  # too verbose
            return

        # Batch mode
        if msg.get('action') == 'batch':
            events = msg.get('events', [])
            for event in events:
                await self.write_event(event)
                et = event.get('eventType', '')
                if et == 'request':
                    self.stats['webrequest'] += 1
                elif 'download' in et:
                    self.stats['downloads'] += 1
            return

        # Individual events
        await self.write_event(msg)

        if msg_type == 'webrequest':
            self.stats['webrequest'] += 1
        elif msg_type == 'download':
            self.stats['downloads'] += 1
        elif msg_type == 'tab':
            self.stats['tabs'] += 1

    # ------------------------------------------------------------------
    # Event writing (file + buffers)
    # ------------------------------------------------------------------

    async def write_event(self, event):
        line = json.dumps(event, default=str) + '\n'
        self.file_handle.write(line)
        self.file_handle.flush()
        self.event_count += 1

        # Push to in-memory buffers for real-time querying
        self.full_buffer.append(event)

        msg_type = event.get('type', event.get('action', '?'))
        if self._is_interesting(msg_type, event):
            self.nav_buffer.append(event)

        # Live summary
        etype = event.get('eventType', event.get('type', event.get('action', '?')))
        if etype == 'webrequest':
            ev = event.get('event', '')
            url = event.get('url', '')
            sc = event.get('statusCode', '')
            m = event.get('method', '')
            print(f"  🌐 {ev:18s} {m} {sc} {url[:100]}")
        elif etype == 'download':
            ev = event.get('event', '')
            fn = event.get('filename', event.get('url', ''))
            print(f"  📥 DOWNLOAD {ev}: {fn}")
        elif etype == 'tab':
            print(f"  🏁 NAV: {event.get('url', '')[:100]}")
        elif etype == 'request':
            url = event.get('url', '')
            m = event.get('method', '')
            sc = event.get('statusCode', '')
            print(f"  🌐 {m} {sc} {url[:100]}")
        elif etype == 'navigation':
            print(f"  🏁 NAV: {event.get('url', '')[:100]}")
        elif 'download' in etype:
            fn = event.get('filename', event.get('url', ''))
            print(f"  📥 {etype}: {fn}")


async def main():
    parser = argparse.ArgumentParser(description="Hermes Traffic Capture Server")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0, use 127.0.0.1 for localhost only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--version", action="version", version=f"capture-server v{VERSION}",
                        help="Show version and exit")
    args = parser.parse_args()

    server = CaptureServer()

    print(f"Hermes Traffic Capture Server v{VERSION}")
    print("=" * 50)
    print(f"  Binding: {args.bind}:{args.port}")
    print()

    stop = asyncio.Future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(
                sig, lambda s=sig: stop.set_result(None)
            )
        except NotImplementedError:
            pass

    try:
        await asyncio.wait_for(server.start(host=args.bind, port=args.port), timeout=None)
    except asyncio.CancelledError:
        pass
    finally:
        if server.file_handle:
            server.file_handle.close()
        print(f"\n📊 Stats: {server.stats}")
        print(f"📁 Wrote {server.event_count} events to {CAPTURE_FILE}")
        print("👋 Server stopped")


if __name__ == '__main__':
    asyncio.run(main())
