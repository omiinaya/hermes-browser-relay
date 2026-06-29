1|# Browser Relay — Improvement Backlog
2|
3|---
4|
5|## Status: PENDING
6|
7|### P9 — Automatic MCP reconnect watchdog
8|When the relay restarts, MCP tools break until someone runs `hermes mcp test`. A watchdog that auto-refreshes the MCP connection would eliminate the manual step.
9|Files: server.py (heartbeat), mcp-server/server.py
10|Difficulty: Medium
11|Est: 20 min
12|
13|### P10 — browser_block_patterns
14|Network request blocking via CDP `Network.setRequestInterception`. Block CSS, images, fonts to speed up scraping.
15|Files: mcp-server/server.py, plugins/browser-relay/__init__.py
16|Difficulty: Hard
17|Est: 30 min
18|
19|### P11 — Multi-tab attach support
20|Support attaching to tab N while listing all open tabs. Currently one tab at a time.
21|Files: server.py, mcp-server/server.py, plugins/browser-relay/__init__.py, extension/background.js
22|Difficulty: Hard
23|Est: 45 min
24|
25|---
26|
27|## Recently Completed
28|
29|### P8 — browser_get_downloads
30|List completed downloads with local file paths. The extension already captures `downloads.onCreated`/`onChanged` events, but there was no tool to query the download state.
31|
32|### P7 — browser_clear_cookies
33|Clear all cookies for the current page via CDP `Network.clearBrowserCookies`.
34|
35|### P6 — browser_get_cookies
36|Read all cookies for the current page via CDP `Network.getCookies`. Useful for auth workflows.
37|
38|### P5 — browser_switch_tab
39|List open tabs and switch to one by ID or URL/title keyword. Required new extension message types (`tab-list`/`tab-switch`), relay server endpoints (`/tabs`, `/tabs/switch`), and tools at both MCP and plugin layers.
40|
41|### P4 — Annotated screenshot with Pillow overlay ✅
42|Draw `[1]`, `[2]` labels directly onto the PNG screenshot using Pillow, instead of returning them as text next to the image. Would make annotated screenshots instantly visually useful.
43|
44|### P3 — browser_console ✅
