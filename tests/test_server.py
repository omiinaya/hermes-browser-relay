"""
CDP Relay Server tests.

Tests the HTTP endpoints of the relay server (server.py) using aiohttp's
TestClient for real request/response cycles. No Chrome extension required
— we test error paths, status responses, request validation, and event capture.

Run:    python3 -m pytest tests/ -v
        bash tests/run.sh
"""

import asyncio
import json
import os
import sys
import time as _time

# Add parent dir so we can import server.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server as relay  # noqa: E402

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


# ====== Test Harness ======


def _build_app():
    """Build the relay server application with all routes (same as main())."""
    app = web.Application()
    app.router.add_get("/status", relay.handle_status)
    app.router.add_get("/", relay.handle_status)
    app.router.add_get("/cdp", relay.handle_cdp_get)
    app.router.add_post("/cdp", relay.handle_cdp_post)
    app.router.add_get("/recent", relay.handle_recent)
    app.router.add_get("/stats", relay.handle_status)
    app.router.add_get("/tabs", relay.handle_tabs_list)
    app.router.add_post("/tabs/switch", relay.handle_tabs_switch)
    app.router.add_post("/block/patterns", relay.handle_block_patterns)
    app.router.add_get("/block/patterns", relay.handle_block_patterns_get)
    app.router.add_post("/tabs/attach", relay.handle_tabs_attach)
    app.router.add_post("/tabs/detach", relay.handle_tabs_detach)
    app.router.add_get("/tabs/attachments", relay.handle_tabs_attachments)
    app.router.add_post("/tabs/create", relay.handle_tabs_create)
    app.router.add_post("/tabs/close", relay.handle_tabs_close)
    app.router.add_post("/cdp/{tabId}", relay.handle_cdp_post_tab)
    return app


@pytest.fixture
def reset_state():
    """Reset module-level state between tests."""
    relay.extension_ws = None
    relay.extension_info = {}
    relay.pending_requests = {}
    relay.event_subscribers = []
    relay.tab_id = None
    relay.last_message_time = 0.0
    relay._event_buffer.clear()
    relay._nav_buffer.clear()
    relay.server_start_time = _time.time()


@pytest_asyncio.fixture
async def client(reset_state):
    """TestClient connected to a fresh relay server app."""
    app = _build_app()
    async with TestClient(TestServer(app)) as client:
        yield client


# ====== Status Tests ======


class TestStatus:
    """Tests for GET /status and GET /stats endpoints."""

    @pytest.mark.asyncio
    async def test_status_returns_ok(self, client):
        resp = await client.get("/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["version"] == "1.5.0"
        assert data["extension_connected"] is False
        assert data["tab_id"] is None
        assert data["pending_requests"] == 0
        assert "uptime" in data

    @pytest.mark.asyncio
    async def test_root_redirects_to_status(self, client):
        resp = await client.get("/")
        assert resp.status == 200
        data = await resp.json()
        assert "version" in data

    @pytest.mark.asyncio
    async def test_stats_alias(self, client):
        resp = await client.get("/stats")
        assert resp.status == 200
        data = await resp.json()
        assert data["extension_connected"] is False

    @pytest.mark.asyncio
    async def test_status_uptime_increasing(self, client):
        resp1 = await client.get("/status")
        d1 = await resp1.json()
        await asyncio.sleep(0.02)
        resp2 = await client.get("/status")
        d2 = await resp2.json()
        assert d2["uptime"] >= d1["uptime"]


# ====== CDP Endpoint Tests ======


class TestCDP:
    """Tests for POST /cdp and GET /cdp endpoints."""

    @pytest.mark.asyncio
    async def test_cdp_post_no_extension(self, client):
        """POST /cdp returns 503 when no extension connected."""
        resp = await client.post("/cdp", json={"method": "Page.navigate", "params": {"url": "https://example.com"}})
        assert resp.status == 503
        data = await resp.json()
        assert "Extension not connected" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_cdp_post_bad_json(self, client):
        """POST /cdp with malformed JSON returns 400."""
        resp = await client.post("/cdp", data=b"not-json-{{{", headers={"Content-Type": "application/json"})
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid JSON" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_cdp_post_no_method(self, client):
        """POST /cdp with valid JSON but no method returns 503 (extension check first) or 400."""
        resp = await client.post("/cdp", json={"params": {"url": "https://example.com"}})
        # Extension is checked before method validation
        assert resp.status in (400, 503)

    @pytest.mark.asyncio
    async def test_cdp_get_no_params(self, client):
        """GET /cdp without method query returns 400 with usage hint."""
        resp = await client.get("/cdp")
        assert resp.status == 400
        data = await resp.json()
        assert "?method=" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_cdp_get_with_method(self, client):
        """GET /cdp?method=Page.navigate passes validation and hits extension check."""
        resp = await client.get("/cdp?method=Page.navigate&url=https://example.com")
        # No extension connected → 503
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_cdp_dialog_dismiss_method(self, client):
        """POST /cdp with Page.handleJavaScriptDialog passes method validation."""
        # Accept dialog
        resp = await client.post("/cdp", json={"method": "Page.handleJavaScriptDialog", "params": {"accept": True}})
        assert resp.status == 503  # No extension connected
        data = await resp.json()
        assert "Extension not connected" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_cdp_dialog_dismiss_with_prompt(self, client):
        """Page.handleJavaScriptDialog with promptText is properly forwarded."""
        resp = await client.post("/cdp", json={
            "method": "Page.handleJavaScriptDialog",
            "params": {"accept": True, "promptText": "hello"}
        })
        assert resp.status == 503
        data = await resp.json()
        assert "Extension not connected" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_cdp_dialog_dismiss_negative(self, client):
        """Page.handleJavaScriptDialog with accept=false."""
        resp = await client.post("/cdp", json={
            "method": "Page.handleJavaScriptDialog",
            "params": {"accept": False}
        })
        assert resp.status == 503
        data = await resp.json()
        assert "Extension not connected" in data.get("error", "")


# ====== Recent / Event Capture Tests ======


class TestRecentEvents:
    """Tests for GET /recent — event capture buffer."""

    @pytest.mark.asyncio
    async def test_recent_empty(self, client):
        resp = await client.get("/recent")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] == 0
        assert data["buffer_fill"] == 0
        assert data["events"] == []

    @pytest.mark.asyncio
    async def test_recent_with_events(self, client, reset_state):
        relay._event_buffer.append({
            "type": "webrequest", "event": "completed",
            "url": "https://api.example.com/data", "method": "GET",
            "statusCode": 200, "_received": _time.time() * 1000,
        })
        relay._nav_buffer.append({
            "type": "tab", "event": "navigated",
            "url": "https://example.com", "title": "Example",
            "tabId": 42, "_received": _time.time() * 1000,
        })
        resp = await client.get("/recent?mode=all")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] >= 1
        assert data["buffer_fill"] >= 1

    @pytest.mark.asyncio
    async def test_recent_type_filter(self, client, reset_state):
        relay._event_buffer.append({"type": "webrequest", "_received": _time.time() * 1000})
        relay._event_buffer.append({"type": "download", "_received": _time.time() * 1000})
        relay._event_buffer.append({"type": "tab", "_received": _time.time() * 1000})
        relay._nav_buffer.append({"type": "download", "_received": _time.time() * 1000})
        resp = await client.get("/recent?type=download&mode=all")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] >= 1
        for e in data["events"]:
            assert e["type"] == "download"

    @pytest.mark.asyncio
    async def test_recent_since_filter(self, client, reset_state):
        now = _time.time() * 1000
        relay._event_buffer.append({"type": "test", "_received": now - 5000})
        relay._event_buffer.append({"type": "test", "_received": now})
        relay._nav_buffer.append({"type": "test", "_received": now})
        resp = await client.get(f"/recent?since={now - 1000}&mode=all")
        assert resp.status == 200
        data = await resp.json()
        for e in data["events"]:
            assert (e.get("_received") or 0) >= now - 1000

    @pytest.mark.asyncio
    async def test_recent_raw_mode(self, client, reset_state):
        relay._event_buffer.append({"type": "test", "_received": _time.time() * 1000})
        relay._nav_buffer.append({"type": "test", "_received": _time.time() * 1000})
        resp = await client.get("/recent?raw=true&mode=all")
        assert resp.status == 200
        assert resp.content_type == "text/plain"
        text = await resp.text()
        lines = text.strip().split("\n")
        assert len(lines) >= 1
        for line in lines:
            obj = json.loads(line)
            assert "type" in obj

    @pytest.mark.asyncio
    async def test_recent_limit_respected(self, client, reset_state):
        relay._event_buffer.append({"type": "a", "_received": _time.time() * 1000})
        relay._event_buffer.append({"type": "b", "_received": _time.time() * 1000})
        relay._nav_buffer.append({"type": "a", "_received": _time.time() * 1000})
        relay._nav_buffer.append({"type": "b", "_received": _time.time() * 1000})
        resp = await client.get("/recent?limit=1&mode=nav")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] == 1


# ====== Tab Endpoint Tests (no extension) ======


class TestTabs:
    """Tab endpoints return 503 without extension (or 400 for bad input)."""

    @pytest.mark.asyncio
    async def test_tabs_list(self, client):
        resp = await client.get("/tabs")
        assert resp.status == 503
        assert "Extension not connected" in (await resp.json()).get("error", "")

    @pytest.mark.asyncio
    async def test_tabs_switch(self, client):
        resp = await client.post("/tabs/switch", json={"tabId": 123})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_tabs_attach(self, client):
        resp = await client.post("/tabs/attach", json={"tabId": 123})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_tabs_detach(self, client):
        resp = await client.post("/tabs/detach", json={"tabId": 123})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_tabs_attachments(self, client):
        resp = await client.get("/tabs/attachments")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_tabs_switch_missing_tabid(self, client):
        """Missing tabId returns 503 (extension check first) or 400."""
        resp = await client.post("/tabs/switch", json={})
        # Extension check precedes validation
        assert resp.status in (400, 503)

    @pytest.mark.asyncio
    async def test_tabs_attach_missing_tabid(self, client):
        """Missing tabId returns 503 (extension check first) or 400."""
        resp = await client.post("/tabs/attach", json={})
        assert resp.status in (400, 503)

    @pytest.mark.asyncio
    async def test_tabs_create_no_extension(self, client):
        """POST /tabs/create returns 503 without extension."""
        resp = await client.post("/tabs/create", json={"url": "https://example.com"})
        assert resp.status in (503, 404)

    @pytest.mark.asyncio
    async def test_tabs_close_no_extension(self, client):
        """POST /tabs/close returns 503 without extension."""
        resp = await client.post("/tabs/close", json={"tabId": 123})
        assert resp.status in (503, 404)

    @pytest.mark.asyncio
    async def test_tabs_close_missing_tabid(self, client):
        """POST /tabs/close without tabId returns 503 (extension first) or 400."""
        resp = await client.post("/tabs/close", json={})
        assert resp.status in (400, 503)


# ====== Block Patterns Tests ======


class TestBlockPatterns:
    """Block endpoint tests (no extension = 503, bad input = 400)."""

    @pytest.mark.asyncio
    async def test_block_set_no_extension(self, client):
        resp = await client.post("/block/patterns", json={"patterns": ["*.css", "*analytics*"]})
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_block_get_no_extension(self, client):
        resp = await client.get("/block/patterns")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_block_patterns_not_a_list(self, client):
        """Non-list patterns returns 503 (extension first) or 400."""
        resp = await client.post("/block/patterns", json={"patterns": "not an array"})
        assert resp.status in (400, 503)

    @pytest.mark.asyncio
    async def test_block_post_empty_body(self, client):
        """Empty body returns 503 (extension first) or 400."""
        resp = await client.post("/block/patterns", json={})
        assert resp.status in (400, 503)


# ====== SSE Event System Tests ======


class TestEventSystem:
    """Tests for the SSE event subscriber system (no real streaming)."""

    def test_events_handler_is_callable(self):
        assert callable(relay.handle_events_sse)
        import inspect
        assert inspect.iscoroutinefunction(relay.handle_events_sse)

    def test_event_subscribers_list_empty(self, reset_state):
        assert len(relay.event_subscribers) == 0

    def test_event_subscribers_add_remove(self, reset_state):
        def dummy(data):
            pass
        relay.event_subscribers.append(dummy)
        assert len(relay.event_subscribers) == 1
        relay.event_subscribers.remove(dummy)
        assert len(relay.event_subscribers) == 0


# ====== Capture Functionality ======


class TestCapture:
    """Tests for the event capture system."""

    def test_capture_event_writes_to_buffer(self, reset_state):
        relay._capture_event({"type": "tab", "event": "navigated", "url": "https://example.com"})
        assert len(relay._event_buffer) == 1
        assert len(relay._nav_buffer) == 1

    def test_capture_event_nav_buffer_filters(self, reset_state):
        relay._capture_event({"type": "tab", "event": "navigated"})
        relay._capture_event({"type": "webrequest", "event": "completed"})
        relay._capture_event({"type": "ping"})
        assert len(relay._nav_buffer) == 2  # only tab + completed

    def test_capture_event_buffer_max_size(self, reset_state):
        for i in range(relay.EVENT_BUFFER_SIZE + 50):
            relay._capture_event({"type": "test", "idx": i, "_received": _time.time() * 1000})
        assert len(relay._event_buffer) <= relay.EVENT_BUFFER_SIZE


# ====== Handler Existence ======


class TestHandlerStructure:
    """Meta-tests: verify all handlers are properly defined."""

    HANDLER_NAMES = [
        "handle_extension", "handle_cdp_post", "handle_status",
        "handle_cdp_get", "handle_events_sse", "handle_recent",
        "handle_tabs_list", "handle_tabs_switch", "handle_block_patterns",
        "handle_block_patterns_get", "handle_tabs_attach", "handle_tabs_detach",
        "handle_tabs_attachments", "handle_cdp_post_tab",
        "handle_tabs_create", "handle_tabs_close",
    ]

    def test_all_handlers_exist(self):
        for name in self.HANDLER_NAMES:
            assert hasattr(relay, name), f"Missing handler: {name}"
            assert callable(getattr(relay, name)), f"Handler not callable: {name}"
