# Security Policy

## ⚠️ Security Warning

**browser-relay gives full Chrome remote-control access to anyone who can reach the relay server.** This is by design — it's a browser automation tool for AI agents — but it means:

- **No authentication:** The relay server (port 9223) has no auth layer. Any process on the same network can send CDP commands.
- **No encryption:** All traffic is plain HTTP/WebSocket. Do not expose the relay to the internet or untrusted networks.
- **Full browser access:** CDP commands can read cookies, credentials, local storage, execute arbitrary JavaScript, upload/download files, and control all browser state.

## Recommended Practices

1. **Run locally only** — Bind the relay to `127.0.0.1` (the default).
2. **Use a firewall** — If cross-LAN access is needed, restrict by IP or use SSH port forwarding.
3. **Don't share the relay** — Only the Hermes Agent / the user running the browser should have access.
4. **Monitor usage** — Check `/status` periodically to see open connections.

## If You Find a Security Issue

This project is experimental. If you discover a vulnerability, please open an issue at https://github.com/omiinaya/browser-relay/issues.

## Responsible Use

This tool is intended for browser automation — web scraping, testing, AI agent workflows. Do not use it for unauthorized access, data exfiltration, or any purpose that violates the Chrome Web Store policies or applicable laws.
