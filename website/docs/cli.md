# CLI

`cdp.py` is a full-featured CLI client for the CDP relay server. Use it for manual debugging, scripting, and automation outside of Hermes.

## Basic Usage

```bash
python3 cdp.py status              # Check relay health
python3 cdp.py navigate https://example.com   # Navigate to URL
python3 cdp.py eval "document.title"          # Execute JavaScript
python3 cdp.py click "#submit-btn"            # Click element
python3 cdp.py type "#search" "hello world"   # Type into field
python3 cdp.py screenshot                     # Take screenshot
python3 cdp.py send Network.getCookies '{}'   # Raw CDP command
```

## Commands

| Command | Description |
|---------|-------------|
| `status` | Check relay server health and connection status |
| `navigate <url>` | Navigate the current tab to a URL |
| `eval <js>` | Evaluate JavaScript in the page context |
| `click <selector>` | Click an element by CSS selector |
| `type <selector> <text>` | Type text into an input field |
| `screenshot [path]` | Take a screenshot (default: `screenshot.png`) |
| `send <method> [params_json]` | Send a raw CDP command |
| `watch` | Stream events in real-time from the relay |
| `tabs` | List all open browser tabs |
| `switch <tab_id>` | Switch to a specific tab |

## Configuration

The CLI uses the `CDP_SERVER` environment variable to locate the relay:

```bash
export CDP_SERVER=http://127.0.0.1:9223
python3 cdp.py status
```

Default: `http://127.0.0.1:9223`
