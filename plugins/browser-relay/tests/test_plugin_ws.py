"""
Tests for the browser-relay Hermes plugin's WebSocket transport layer.

The plugin (plugins/browser-relay/__init__.py) was upgraded in v2.5.0 to use a
persistent WebSocket to the relay's /mcp-ws endpoint (mirroring the MCP server),
with HTTP POST fallback. These tests exercise the circuit breaker, WS send/recv,
and the HTTP fallback path in _cdp().

Run:    python3 -m pytest plugins/browser-relay/tests/ -v
"""

import importlib.util
import pathlib
import sys
import unittest.mock

import pytest

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent

# Load the plugin module under a unique name to avoid clobbering sys.modules
_spec = importlib.util.spec_from_file_location("br_plugin_module", _PLUGIN_DIR / "__init__.py")
_plugin = importlib.util.module_from_spec(_spec)
sys.modules["br_plugin_module"] = _plugin
_spec.loader.exec_module(_plugin)


@pytest.fixture
def plugin():
    """Fresh plugin module state per test."""
    _plugin._plugin_ws = None
    _plugin._ws_failures = 0
    _plugin._last_ws_ok = 0.0
    _plugin._ws_circuit_open_until = 0.0
    return _plugin


class TestWsCircuitBreaker:
    def test_ws_skipped_when_library_missing(self, plugin):
        plugin._ws_sync = None
        assert plugin._ensure_plugin_ws_locked() is False
        assert plugin._cdp_via_ws({"method": "Page.navigate"}) is None

    def test_circuit_opens_after_threshold_failures(self, plugin):
        plugin._ws_sync = unittest.mock.MagicMock()
        plugin._ws_sync.connect.side_effect = Exception("conn refused")
        plugin._ws_failures = 0

        for _ in range(plugin._WS_BREAK_THRESHOLD):
            assert plugin._ensure_plugin_ws_locked() is False

        # Circuit now open — next call skips immediately without trying connect
        plugin._ws_sync.connect.reset_mock()
        assert plugin._ensure_plugin_ws_locked() is False
        plugin._ws_sync.connect.assert_not_called()
        assert plugin._ws_circuit_open_until > 0

    def test_circuit_resets_on_success(self, plugin):
        fake_ws = unittest.mock.MagicMock()
        fake_ws.recv.return_value = '{"result": {"ok": true}}'
        plugin._ws_sync = unittest.mock.MagicMock()
        plugin._ws_sync.connect.return_value = fake_ws
        plugin._ws_failures = 3
        plugin._ws_circuit_open_until = 0.0  # not open

        result = plugin._cdp_via_ws({"method": "Runtime.evaluate", "timeout": 5})
        assert result == {"result": {"ok": True}}
        assert plugin._ws_failures == 0

    def test_no_double_connect_under_concurrency(self, plugin):
        """Two threads racing on a cold WS must not both connect (socket leak)."""
        import threading

        fake_ws = unittest.mock.MagicMock()
        fake_ws.recv.return_value = '{"result": {"ok": true}}'
        plugin._ws_sync = unittest.mock.MagicMock()
        plugin._ws_sync.connect.return_value = fake_ws
        plugin._ws_failures = 0
        plugin._ws_circuit_open_until = 0.0

        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()  # release both threads at once
            results.append(plugin._cdp_via_ws({"method": "Runtime.evaluate", "timeout": 5}))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == {"result": {"ok": True}} for r in results)
        # The lock guarantees a single connect — a race would call connect twice
        assert plugin._ws_sync.connect.call_count == 1


class TestCdpWsFirst:
    def test_cdp_uses_ws_when_available(self, plugin):
        fake_ws = unittest.mock.MagicMock()
        fake_ws.recv.return_value = '{"result": {"type": "object"}}'
        plugin._ws_sync = unittest.mock.MagicMock()
        plugin._ws_sync.connect.return_value = fake_ws

        with unittest.mock.patch.object(
            plugin, "_cdp_opener"
        ) as opener:
            result = plugin._cdp("Runtime.evaluate", {"expression": "1+1"}, timeout=5)
            assert result == {"result": {"type": "object"}}
            opener.open.assert_not_called()  # HTTP POST NOT used when WS works

    def test_cdp_falls_back_to_http_when_ws_unavailable(self, plugin):
        plugin._ws_sync = None  # no websockets lib → WS path disabled

        fake_resp = unittest.mock.MagicMock()
        fake_resp.read.return_value = b'{"result": {"ok": true}}'
        fake_ctx = unittest.mock.MagicMock()
        fake_ctx.__enter__.return_value = fake_resp
        fake_ctx.__exit__.return_value = False
        opener = unittest.mock.MagicMock()
        opener.open.return_value = fake_ctx

        with unittest.mock.patch.object(plugin, "_cdp_opener", opener):
            result = plugin._cdp("Page.navigate", {"url": "https://example.com"}, timeout=5)
            assert result == {"result": {"ok": True}}
            opener.open.assert_called_once()
