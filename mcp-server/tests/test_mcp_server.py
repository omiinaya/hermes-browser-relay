"""
MCP Server tests.

Tests the refactored MCP server (mcp-server/server.py) — tool definitions,
handler dispatch, and individual handler logic with mocked CDP layer.

Run:    python3 -m pytest mcp-server/tests/ -v
"""
import json
import os
import sys
import unittest.mock

import importlib
import pathlib

# Import mcp-server/server.py without colliding with root server.py
_mcp_path = pathlib.Path(__file__).resolve().parent.parent / "server.py"
_spec = importlib.util.spec_from_file_location("mcp_server_module", str(_mcp_path))
mcp = importlib.util.module_from_spec(_spec)
sys.modules["mcp_server_module"] = mcp
_spec.loader.exec_module(mcp)

import pytest
from mcp.types import Tool, TextContent, ImageContent


# =========================================================================
# Tool Definitions Tests
# =========================================================================

class TestToolDefinitions:
    """Verify TOOL_DEFS and list_tools work correctly."""

    def test_tool_defs_count(self):
        assert len(mcp.TOOL_DEFS) >= 40

    def test_all_tools_have_required_fields(self):
        for name, defn in mcp.TOOL_DEFS.items():
            assert "description" in defn, f"{name} missing description"
            assert "inputSchema" in defn, f"{name} missing inputSchema"
            schema = defn["inputSchema"]
            assert "type" in schema, f"{name} inputSchema missing type"
            assert "properties" in schema, f"{name} inputSchema missing properties"

    def test_essential_tools_exist(self):
        essential = [
            "browser_navigate", "browser_click", "browser_type",
            "browser_snapshot", "browser_eval", "browser_screenshot",
            "browser_do", "browser_navigate_wait",
        ]
        for tool in essential:
            assert tool in mcp.TOOL_DEFS

    def test_all_tools_get_tab_id(self):
        import asyncio
        tools = asyncio.run(mcp.list_tools())
        for t in tools:
            assert "tab_id" in t.inputSchema["properties"], f"{t.name} missing tab_id"

    def test_list_tools_count(self):
        import asyncio
        tools = asyncio.run(mcp.list_tools())
        assert len(tools) == len(mcp.TOOL_DEFS)


# =========================================================================
# Handler Dispatch Tests
# =========================================================================

class TestHandlerDispatch:
    """Verify HANDLER_MAP and the dispatch function work."""

    def test_all_tools_have_handlers(self):
        for name in mcp.TOOL_DEFS:
            assert name in mcp.HANDLER_MAP

    def test_no_extra_handlers(self):
        for name in mcp.HANDLER_MAP:
            assert name in mcp.TOOL_DEFS

    def test_handler_map_size(self):
        assert len(mcp.HANDLER_MAP) == len(mcp.TOOL_DEFS)

    def test_unknown_tool(self):
        import asyncio
        result = asyncio.run(mcp._handle_unknown("nope"))
        assert "Unknown" in result[0].text

    def test_call_tool_unknown(self):
        import asyncio
        result = asyncio.run(mcp.call_tool("nope", {}))
        assert "Unknown" in result[0].text

    def test_call_tool_catches_exception(self):
        import asyncio
        result = asyncio.run(mcp.call_tool("browser_navigate", {}))
        assert "Error" in result[0].text


# =========================================================================
# Handler Unit Tests (with mocks)
# =========================================================================

class TestNavigate:
    def test_calls_cdp(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_cdp', return_value={"result": {"frameId": "abc"}}):
            with unittest.mock.patch.object(mcp, '_invalidate_cache'):
                asyncio.run(mcp._handle_browser_navigate({"url": "https://ex.com"}))
                mcp._cdp.assert_called_once_with("Page.navigate", {"url": "https://ex.com"})

    def test_cdp_error(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_cdp', return_value={"error": "Failed"}):
            r = asyncio.run(mcp._handle_browser_navigate({"url": "https://ex.com"}))
            assert "Error" in r[0].text


class TestEval:
    def test_calls_runtime_evaluate(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_cdp'):
            with unittest.mock.patch.object(mcp, '_parse_result', return_value="42"):
                asyncio.run(mcp._handle_browser_eval({"expression": "1+1"}))
                mcp._cdp.assert_called_once_with("Runtime.evaluate", {"expression": "1+1", "returnByValue": True})


class TestSnapshot:
    def test_uses_cache(self):
        import asyncio
        cached = {"info": {"title": "cached"}, "items": []}
        with unittest.mock.patch.object(mcp, '_get_cached_snapshot', return_value=cached):
            with unittest.mock.patch.object(mcp, '_hermes_call') as h:
                r = asyncio.run(mcp._handle_browser_snapshot({"full": False}))
                h.assert_not_called()
                assert "cached" in r[0].text

    def test_cache_miss(self):
        import asyncio
        snap = {"info": {"title": "fresh"}, "items": []}
        with unittest.mock.patch.object(mcp, '_get_cached_snapshot', return_value=None):
            with unittest.mock.patch.object(mcp, '_hermes_call', return_value={}):
                with unittest.mock.patch.object(mcp, '_parse_result', return_value=snap):
                    with unittest.mock.patch.object(mcp, '_cache_snapshot'):
                        r = asyncio.run(mcp._handle_browser_snapshot({"full": False}))
                        assert "fresh" in r[0].text


class TestDo:
    def test_empty_actions(self):
        import asyncio
        r = asyncio.run(mcp._handle_browser_do({}))
        assert "non-empty" in r[0].text

    def test_click(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_hermes_call', return_value={}):
            with unittest.mock.patch.object(mcp, '_parse_result', return_value={"ok": True}):
                r = asyncio.run(mcp._handle_browser_do({
                    "actions": [{"action": "click", "selector": "#btn"}]
                }))
                assert "1 actions" in r[0].text

    def test_multiple(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_hermes_call', return_value={}):
            with unittest.mock.patch.object(mcp, '_parse_result', return_value={"ok": True}):
                r = asyncio.run(mcp._handle_browser_do({
                    "actions": [
                        {"action": "click", "selector": "#b"},
                        {"action": "type", "selector": "#i", "text": "hi"},
                    ]
                }))
                assert "2 actions" in r[0].text

    def test_unknown_action(self):
        import asyncio
        r = asyncio.run(mcp._handle_browser_do({
            "actions": [{"action": "wobble"}]
        }))
        assert "Unknown" in r[0].text

    def test_screenshot(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_cdp', return_value={"result": {"data": "b64"}}):
            r = asyncio.run(mcp._handle_browser_do({
                "actions": [{"action": "screenshot"}]
            }))
            assert "Screenshot" in r[0].text


class TestScreenshot:
    def test_error_path(self):
        import asyncio
        with unittest.mock.patch.object(mcp, '_cdp', return_value={"error": "fail"}):
            r = asyncio.run(mcp._handle_browser_screenshot({}))
            assert "Error" in r[0].text


# =========================================================================
# Helper Integrity Tests
# =========================================================================

class TestHelpers:
    def test_js_quote(self):
        assert mcp._js_quote("test") == '"test"'
        assert mcp._js_quote("it's") == '"it\'s"'

    def test_invalidate_cache(self):
        mcp._snapshot_cache[("t", False)] = {"data": {}, "ts": 1000.0}
        mcp._invalidate_cache()
        assert len(mcp._snapshot_cache) == 0
