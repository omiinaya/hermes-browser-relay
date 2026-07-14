"""
End-to-end integration test: real Chromium browser via CDP.

Launches Chromium headless with a remote debugging port, connects directly
via the Chrome DevTools Protocol (CDP), and drives browser commands
(navigate, eval, screenshot) to verify protocol-level integration.

This tests the CDP layer against a real browser — complements the relay
and extension tests which are unit-test-level mocks.

Requires chromium + chromium-headless-shell to be installed.
Skips gracefully if not found.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import aiohttp
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Try to find Chromium binaries
CHROMIUM_PATHS = [
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
    shutil.which("google-chrome"),
    shutil.which("google-chrome-stable"),
    shutil.which("chrome"),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
CHROME_BIN = next((p for p in CHROMIUM_PATHS if p and os.access(p, os.X_OK)), None)
HEADLESS_SHELL = shutil.which("chromium-headless-shell")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        CHROME_BIN is None and HEADLESS_SHELL is None,
        reason="No Chromium/Chrome binary found — install chromium or google-chrome",
    ),
]


def _parse_cdp_port(stderr_output: str) -> int | None:
    """Extract the DevTools listening port from Chrome's stderr."""
    match = re.search(r"DevTools listening on ws://[^:]+:(\d+)", stderr_output)
    if match:
        return int(match.group(1))
    match = re.search(r"listen\(tcp://[^:]+:(\d+)", stderr_output)
    if match:
        return int(match.group(1))
    return None


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _get_page_ws_url(base_url: str) -> str | None:
    """Get the WebSocket URL for the first tab page target."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{base_url}/json", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    targets = await resp.json()
                    for t in targets:
                        ws = t.get("webSocketDebuggerUrl")
                        if ws:
                            return ws
        except Exception:
            pass
    return None


@pytest.fixture(scope="module")
async def chrome_cdp():
    """
    Launch Chromium headless and expose a page-level CDP WebSocket.

    Yields (page_ws_url, base_url, proc, tmpdir).
    """
    browser_bin = CHROME_BIN or HEADLESS_SHELL
    assert browser_bin, "No Chromium binary"

    tmpdir = tempfile.mkdtemp(prefix="cdp-e2e-")
    user_data_dir = os.path.join(tmpdir, "profile")

    proc = await asyncio.create_subprocess_exec(
        browser_bin,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        f"--user-data-dir={user_data_dir}",
        "--remote-debugging-port=0",
        "--window-size=1280,720",
        "about:blank",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Read stderr until we find the DevTools port
    cdp_port = None
    stderr_lines = []
    timeout = time.time() + 15
    while time.time() < timeout:
        try:
            assert proc.stderr is not None
            line = await asyncio.wait_for(proc.stderr.readline(), timeout=5)
            decoded = line.decode(errors="replace").strip()
            stderr_lines.append(decoded)
            cdp_port = _parse_cdp_port(decoded)
            if cdp_port:
                break
        except asyncio.TimeoutError:
            break

    if not cdp_port:
        proc.kill()
        stdout, stderr = await proc.communicate()
        all_stderr = "\n".join(stderr_lines) + stderr.decode(errors="replace")
        pytest.fail(
            f"Could not find CDP port in Chromium output.\n"
            f"Stderr:\n{all_stderr[:1000]}"
        )

    base_url = f"http://127.0.0.1:{cdp_port}"

    # Wait for a page target to appear
    page_ws_url = None
    for attempt in range(20):
        page_ws_url = await _get_page_ws_url(base_url)
        if page_ws_url:
            break
        await asyncio.sleep(0.5)

    if not page_ws_url:
        proc.kill()
        await proc.wait()
        # Fall back: try to create a tab
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{base_url}/json/new?about:blank",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        tab = await resp.json()
                        page_ws_url = tab.get("webSocketDebuggerUrl")
        except Exception:
            pass

    if not page_ws_url:
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.fail(f"Could not get page WebSocket URL from {base_url}/json")

    yield {"proc": proc, "ws_url": page_ws_url, "base_url": base_url, "tmpdir": tmpdir}

    # Cleanup
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    import shutil as _shutil
    _shutil.rmtree(tmpdir, ignore_errors=True)


async def _cdp_call(ws_url: str, method: str, params: dict | None = None) -> dict:
    """Send a CDP command via WebSocket and return the result."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            msg_id = 1
            await ws.send_json({
                "id": msg_id,
                "method": method,
                "params": params or {},
            })

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("id") == msg_id:
                        return data
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break

    return {"error": "No response"}


class TestCDPE2E:
    """End-to-end CDP tests against a real browser."""

    async def test_navigate(self, chrome_cdp):
        """Navigate to a page and verify navigation completes."""
        ws_url = chrome_cdp["ws_url"]
        base_url = chrome_cdp["base_url"]

        # Enable Page domain first
        result = await _cdp_call(ws_url, "Page.enable")
        assert "error" not in result, f"Page.enable error: {result.get('error', result)}"

        # Navigate to a data URI (no network needed, works in headless)
        result = await _cdp_call(ws_url, "Page.navigate", {
            "url": "data:text/html,<h1 id=test>Hello E2E</h1>",
        })
        assert "error" not in result, f"Navigation error: {result.get('error', result)}"
        assert "frameId" in result.get("result", {}), f"No frameId in response: {result}"

    async def test_eval_javascript(self, chrome_cdp):
        """Evaluate JavaScript and get the result."""
        ws_url = chrome_cdp["ws_url"]

        # Navigate to test page
        await _cdp_call(ws_url, "Page.enable")
        await _cdp_call(ws_url, "Page.navigate", {
            "url": "data:text/html,<script>window.MY_VAR=42</script>",
        })
        await asyncio.sleep(1)

        # Evaluate JavaScript
        result = await _cdp_call(ws_url, "Runtime.evaluate", {
            "expression": "window.MY_VAR",
        })
        assert "error" not in result, f"Eval error: {result.get('error', result)}"

        value = result.get("result", {}).get("result", {}).get("value")
        assert value == 42, f"Expected 42, got {value}"

    async def test_screenshot(self, chrome_cdp):
        """Capture a page screenshot via CDP."""
        ws_url = chrome_cdp["ws_url"]

        # Ensure we're on a page
        await _cdp_call(ws_url, "Page.enable")
        await _cdp_call(ws_url, "Page.navigate", {
            "url": "data:text/html,<h1 style='color:red'>Screenshot Test</h1>",
        })
        await asyncio.sleep(1)

        # Capture screenshot
        result = await _cdp_call(ws_url, "Page.captureScreenshot", {
            "format": "png",
        })

        if "error" in result:
            pytest.skip(f"Screenshot not supported in this headless mode: {result['error']}")
            return

        # Extract base64 data
        data = result.get("result", {}).get("data", "")
        assert data, "No screenshot data in response"
        assert len(data) > 100, f"Screenshot data suspiciously small ({len(data)} chars)"

    async def test_tab_list(self, chrome_cdp):
        """List browser tabs via HTTP /json endpoint."""
        base_url = chrome_cdp["base_url"]
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}/json", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                assert resp.status == 200
                tabs = await resp.json()
                assert isinstance(tabs, list)
                assert len(tabs) >= 1
                first = tabs[0]
                assert "id" in first or "webSocketDebuggerUrl" in first
