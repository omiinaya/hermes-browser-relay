# Configuration

browser-relay is configured through environment variables. Copy `.env.example` to `.env` and adjust for your setup.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CDP_SERVER` | `http://127.0.0.1:9223` | CDP relay server address (used by plugin and MCP server) |
| `RELAY_TIMEOUT` | `60` | Tool call timeout in seconds |
| `RELAY_SERVER_PATH` | `server.py` | Path to `server.py` (for auto-start) |
| `RELAY_HOST` | `0.0.0.0` | Relay server bind address |
| `RELAY_PORT` | `9223` | Relay server port |
| `CDP_RELAY_URL` | `http://127.0.0.1:9223` | Alias for `CDP_SERVER` (MCP server) |

## Cross-LAN Setup

To control a browser on a different machine:

1. Start the relay on the target machine:
   ```bash
   RELAY_HOST=0.0.0.0 python3 server.py
   ```

2. In the extension popup, set the server URL to:
   ```
   ws://<target-lan-ip>:9223/ws
   ```

3. On the Hermes side:
   ```bash
   export CDP_SERVER=http://<target-lan-ip>:9223
   ```

## Security

> **⚠️ The relay has no authentication layer.** Bind to `127.0.0.1` unless you need LAN access. For production-like setups, use SSH port forwarding or a firewall.

## Built-in Configuration

The `--log-level` flag controls verbosity:

```bash
python3 server.py --log-level DEBUG    # Verbose logging
python3 server.py --log-level WARNING  # Errors only
```
