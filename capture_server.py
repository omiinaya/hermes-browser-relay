#!/usr/bin/env python3
"""
Hermes Traffic Capture Server (v1.1.0)
Receives browser events via WebSocket from the Chrome extension
and writes them to a JSONL file.

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
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

sys.stdout.reconfigure(line_buffering=True)

CAPTURE_DIR = Path("/tmp/hermes-captures")
CAPTURE_FILE = CAPTURE_DIR / "capture.jsonl"
DEFAULT_PORT = 9988
HEARTBEAT_INTERVAL = 15       # Send ping every 15s
HEARTBEAT_TIMEOUT = 45        # Disconnect if no message for 45s


class CaptureServer:
    def __init__(self):
        self.clients = {}  # websocket -> {"remote": addr, "last_message": float}
        self.event_count = 0
        self.file_handle = None
        self.stats = {
            'webrequest': 0,
            'downloads': 0,
            'tabs': 0,
            'cdp_events': 0,
            'errors': 0,
            'responses': 0,
        }

    async def start(self, host="0.0.0.0", port=DEFAULT_PORT):
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

        # Rotate old capture files (keep last 5)
        old_files = sorted(CAPTURE_DIR.glob("capture*.jsonl"), reverse=True)
        for f in old_files[4:]:
            f.unlink(missing_ok=True)

        self.file_handle = open(CAPTURE_FILE, 'a')

        print(f"📡 Capture server starting on ws://{host}:{port}")
        print(f"📁 Writing to: {CAPTURE_FILE}")
        local = "localhost" if host == "0.0.0.0" else host
        print(f"🌐 Accessible at ws://{local}:{port} (local)")

        async with websockets.serve(self.handle_client, host, port,
                                    ping_interval=HEARTBEAT_INTERVAL,
                                    ping_timeout=HEARTBEAT_TIMEOUT):
            await asyncio.Future()

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

    async def process_message(self, msg):
        # Add timestamp if not present
        if 'ts' not in msg:
            msg['_received'] = datetime.now(timezone.utc).timestamp() * 1000

        msg_type = msg.get('type', msg.get('action', 'unknown'))

        # Connection management - don't log these
        if msg_type in ('hello', 'pong', 'ping', 'keepalive'):
            return

        # Response body events - write and print preview
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

        # CDP events - write but be selective about printing
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
                # Too verbose - skip printing
                self.stats['webrequest'] += 1
            return

        # Batch mode (from custom extensions)
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

    async def write_event(self, event):
        line = json.dumps(event, default=str) + '\n'
        self.file_handle.write(line)
        self.file_handle.flush()
        self.event_count += 1

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
    parser.add_argument("--version", action="version", version="1.1.0",
                        help="Show version and exit")
    args = parser.parse_args()

    server = CaptureServer()

    print("Hermes Traffic Capture Server v1.1.0")
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
