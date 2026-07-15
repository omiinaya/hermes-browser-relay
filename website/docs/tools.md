# Tools

browser-relay exposes **46 browser automation tools**. All tools accept an optional `tab_id` parameter for multi-tab routing. Tools are registered in the Hermes plugin and duplicated in the MCP server for parity.

## Navigation

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL. Returns after navigation starts. |
| `browser_navigate_wait` | Navigate and wait for page to fully load (`readyState=complete`). |
| `browser_back` | Navigate to the previous page in history. |
| `browser_forward` | Navigate forward in browser history. |
| `browser_refresh` | Reload the current page. Optional `hard_reload` bypasses cache. |

## Interaction

| Tool | Description |
|------|-------------|
| `browser_click` | Click an element by CSS selector. Dispatches CDP mouse events. |
| `browser_click_text` | Click an element by visible text content. No selector needed. |
| `browser_type` | Type text into an input field (clears first, then types). |
| `browser_hover` | Hover over an element by CSS selector (triggers mouseenter/mouseover). |
| `browser_key` | Press a keyboard key (Escape, Tab, Enter, ArrowDown, Delete, Backspace). |
| `browser_enter` | Press the Enter key. |
| `browser_select_option` | Select an `<option>` in a `<select>` dropdown by visible text. |

## Inspection

| Tool | Description |
|------|-------------|
| `browser_snapshot` | Get an accessibility-style snapshot of the page with interactive elements and ref IDs. Auto-cached for 2s. |
| `browser_eval` | Evaluate JavaScript in the page context. Returns the result as a string. |
| `browser_get_text` | Get rendered text content of the page (stripped of HTML tags). |
| `browser_get_title` | Get the current page title. |
| `browser_find` | Search the page for elements matching text or selector. Returns coordinates, tag, content. |
| `browser_get_cookies` | Read all cookies for the current page via CDP. |

## Forms

| Tool | Description |
|------|-------------|
| `browser_fill_by_label` | Find a form field by label text, placeholder, or aria-label, then type text. |
| `browser_form_fill` | Fill multiple form fields at once from a `{field: value}` dictionary. |
| `browser_upload_file` | Upload a file by setting the value of an `<input type=file>` element. |

## Tabs

| Tool | Description |
|------|-------------|
| `browser_list_tabs` | List all open browser tabs with IDs, titles, and URLs. |
| `browser_switch_tab` | Switch to a tab by ID, or by URL/title keyword. |
| `browser_create_tab` | Create a new tab with an optional URL. |
| `browser_close_tab` | Close a browser tab by ID. |
| `browser_attach_tab` | Attach the CDP debugger to a specific tab by ID. |
| `browser_detach_tab` | Detach the CDP debugger from a specific tab. |
| `browser_list_attachments` | List currently attached tab IDs. |

## Network

| Tool | Description |
|------|-------------|
| `browser_network_requests` | Query captured network requests from the event buffer. Filter by event type. |
| `browser_response_body` | Query captured response bodies (JSON/PDF responses). |
| `browser_block_patterns` | Block or allow URL patterns using Chrome's `declarativeNetRequest` API. |
| `browser_set_cookie` | Set a browser cookie (useful for auth session injection). |
| `browser_clear_cookies` | Clear all cookies for the current page. |

## Media

| Tool | Description |
|------|-------------|
| `browser_screenshot` | Capture a screenshot of the current page as PNG. |
| `browser_screenshot_annotated` | Take a screenshot with numbered labels overlaid on interactive elements. |
| `browser_scroll` | Scroll the page by direction (up/down/left/right) or specific pixel amount. |
| `browser_pdf` | Save the current page as a PDF document (A4, with background colors). |

## Compound

| Tool | Description |
|------|-------------|
| `browser_do` | Execute multiple browser actions sequentially in one tool call. Supports 18 action types: navigate, click, type, scroll, screenshot, eval, and more. |

## Utility

| Tool | Description |
|------|-------------|
| `browser_status` | Check CDP relay connection status. |
| `browser_console` | Read recent browser console output (log/warn/error/debug/info messages). |
| `browser_send_cdp` | Send an arbitrary CDP command — for advanced operations not covered by other tools. |
| `browser_wait_selector` | Wait for a CSS selector to appear in the DOM (visible or attached). |
| `browser_wait_for_navigation` | Wait for a page navigation to complete. Returns the new URL and title. |
| `browser_dismiss_dialog` | Dismiss a JavaScript dialog (alert/confirm/prompt) by accepting or cancelling it. |
| `browser_get_downloads` | List recent downloads captured by the extension. |
| `browser_recent_events` | Fetch recent browser events (navigations, downloads, requests) from the capture buffer. |
