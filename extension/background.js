// CDP Bridge — Background Service Worker (v2.4.0)
// Connects chrome.debugger to Hermes Agent via WebSocket
// Features: heartbeat keepalive, exponential backoff reconnect,
// tab-switch auto-reattach, robust error handling

const DEFAULT_SERVER = 'ws://127.0.0.1:9223/ws';
let SERVER_URL = DEFAULT_SERVER;
let ws = null;
let wsReconnectTimer = null;
let attachments = new Map();  // tabId -> true (multi-tab support)
let reconnectDelay = 1000;
let autoAttach = true;  // User preference: auto-attach on tab activation

// Connection status: 'disconnected', 'connecting', 'connected'
let connectionStatus = 'disconnected';
// Track last time we heard from the server
let lastServerMessageTime = null;

// Client-side heartbeat fallback (if server doesn't ping)
let clientHeartbeatTimer = null;
const CLIENT_HEARTBEAT_INTERVAL = 20000; // 20s

// Reattach watcher — keeps trying until attached or disconnected
const REATTACH_INTERVAL = 5000; // 5s
let reattachWatcherTimer = null;

// ====== Safe Send Helper ======

function safeSend(data) {
  try {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
      return true;
    }
  } catch (e) {
    console.warn('[CDP] safeSend failed:', e.message);
  }
  return false;
}

// ====== CDP Event Listeners ======

const bodyCapturePatterns = [
  '/technology',
  '/benchmarks/latest',
  '/download',
  '.pdf',
  'application/json',
];

function shouldCaptureBody(url, mimeType) {
  if (mimeType && (mimeType.includes('json') || mimeType.includes('pdf'))) return true;
  for (const p of bodyCapturePatterns) {
    if (url.includes(p)) return true;
  }
  return false;
}

// Track requests we want response bodies for
const bodyTargets = new Map();
const BODY_TARGET_TTL = 30000; // 30s — sweep orphaned entries that never got loadingFinished

// Periodically sweep stale bodyTargets entries (memory leak guard)
setInterval(() => {
  const cutoff = Date.now() - BODY_TARGET_TTL;
  for (const [reqId, entry] of bodyTargets) {
    if (entry.timestamp < cutoff) {
      bodyTargets.delete(reqId);
    }
  }
}, BODY_TARGET_TTL / 2);

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  // Forward event as-is
  safeSend({
    type: 'event',
    method: method,
    params: params,
    tabId: source.tabId,
  });

  if (method === 'Network.responseReceived' && params && params.requestId) {
    const resp = params.response || {};
    const url = resp.url || '';
    const mime = resp.mimeType || '';

    if (shouldCaptureBody(url, mime)) {
      bodyTargets.set(params.requestId, { url, mime, timestamp: Date.now() });
    }
  }

  if (method === 'Network.loadingFinished') {
    const target = bodyTargets.get(params.requestId);
    if (target && attachments.size > 0) {
      bodyTargets.delete(params.requestId);
      for (const [tid] of attachments) {
        chrome.debugger.sendCommand(
          { tabId: tid },
          'Network.getResponseBody',
          { requestId: params.requestId },
          (result) => {
            const lastError = chrome.runtime.lastError;
            if (lastError) return;
            if (result && result.body !== undefined && ws && ws.readyState === WebSocket.OPEN) {
              safeSend({
                type: 'response_body',
                requestId: params.requestId,
                tabId: tid,
                url: target.url,
                mimeType: target.mime,
                body: result.body,
                base64Encoded: result.base64Encoded || false,
              });
            }
          }
        );
      }
    }
  }
});

chrome.debugger.onDetach.addListener((source, reason) => {
  console.debug('[CDP] Detached from tab', source.tabId, '- reason:', reason);
  attachments.delete(source.tabId);
  updateBadge();

  // If no more attachments and WS connected, start reattach watcher
  if (attachments.size === 0 && connectionStatus === 'connected') {
    startReattachWatcher();
  }
});

// ====== URL Blocking via declarativeNetRequest ======

let blockedPatterns = [];
async function updateBlockingRules(patterns) {
  blockedPatterns = patterns || [];

  // Remove all existing dynamic rules
  const existing = await chrome.declarativeNetRequest.getDynamicRules();
  const existingIds = existing.map(r => r.id);

  // Build new rules
  const newRules = [];
  for (let i = 0; i < blockedPatterns.length; i++) {
    const p = blockedPatterns[i];
    let urlFilter = p;

    // Convert common glob patterns to DNR urlFilter
    if (p.startsWith('*.')) {
      // *.css → ends with ".css"
      urlFilter = p.slice(1); // ".css"
    } else if (p.startsWith('*')) {
      // *analytics* → contains "analytics"
      urlFilter = p.replace(/\*/g, '');
    } else if (p.endsWith('*')) {
      // https://example.com/* → starts with "https://example.com/"
      urlFilter = p.slice(0, -1);
    }

    newRules.push({
      id: 1000 + i,
      priority: 1,
      action: { type: "block" },
      condition: {
        urlFilter: urlFilter,
        resourceTypes: [
          "main_frame", "sub_frame", "stylesheet", "script",
          "image", "font", "object", "xmlhttprequest", "ping",
          "csp_report", "media", "websocket", "webtransport",
          "webbundle", "other"
        ],
      },
    });
  }

  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: existingIds,
    addRules: newRules,
  });
}

async function getBlockingRules() {
  const rules = await chrome.declarativeNetRequest.getDynamicRules();
  return rules.map(r => ({
    id: r.id,
    urlFilter: r.condition.urlFilter,
  }));
}

// ====== Global Web Request Capture (all tabs) ======

function sendWebRequest(type, details) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  safeSend({
    type: 'webrequest',
    event: type,
    tabId: details.tabId,
    requestId: details.requestId,
    url: details.url,
    method: details.method || details.request?.method,
    statusCode: details.statusCode,
    statusLine: details.statusLine,
    resourceType: details.type,
    ip: details.ip,
    timeStamp: details.timeStamp,
    responseHeaders: details.responseHeaders ? details.responseHeaders.slice(0, 30) : undefined,
    requestHeaders: details.requestHeaders ? details.requestHeaders.slice(0, 30) : undefined,
  });
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => { sendWebRequest('beforeRequest', details); return; },
  { urls: ['<all_urls>'] },
  ['requestBody']
);

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => { sendWebRequest('beforeSendHeaders', details); return; },
  { urls: ['<all_urls>'] },
  ['requestHeaders']
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => { sendWebRequest('headersReceived', details); return; },
  { urls: ['<all_urls>'] },
  ['responseHeaders']
);

chrome.webRequest.onCompleted.addListener(
  (details) => { sendWebRequest('completed', details); return; },
  { urls: ['<all_urls>'] },
  ['responseHeaders']
);

chrome.webRequest.onErrorOccurred.addListener(
  (details) => { sendWebRequest('error', details); return; },
  { urls: ['<all_urls>'] }
);

// ====== Server URL ======

function loadServerUrl() {
  chrome.storage.sync.get('serverUrl', (data) => {
    const newUrl = (data.serverUrl || DEFAULT_SERVER).trim();
    if (!newUrl.startsWith('ws://') && !newUrl.startsWith('wss://')) {
      SERVER_URL = DEFAULT_SERVER;
      return;
    }
    SERVER_URL = newUrl;
  });
}

function saveServerUrl(url, callback) {
  chrome.storage.sync.set({ serverUrl: url }, () => {
    SERVER_URL = url;
    // Reconnect with new URL
    if (ws) {
      ws.onclose = null;
      try { ws.close(); } catch (e) { /* ignore */ }
      ws = null;
    }
    if (callback) callback();
    reconnectDelay = 1000; // Reset backoff on URL change
    setTimeout(connectWS, 100);
  });
}

// ====== WebSocket ======

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  ensureKeepalive(); // Re-arm the keepalive alarm on every connect attempt
  connectionStatus = 'connecting';
  updateBadge();

  console.debug('[CDP] Connecting to', SERVER_URL);
  ws = new WebSocket(SERVER_URL);

  ws.onopen = () => {
    console.debug('[CDP] WebSocket connected to ' + SERVER_URL);
    connectionStatus = 'connected';
    reconnectDelay = 1000; // Reset backoff on successful connect
    updateBadge();

    // Send hello for all currently attached tabs
    for (const [tid] of attachments) {
      safeSend({
        type: 'hello',
        tabId: tid,
        info: { attached: true },
      });
    }

    // Start client-side heartbeat as fallback
    startClientHeartbeat();

    // If WS reconnected but no attachments, start reattach watcher
    if (attachments.size === 0) {
      startReattachWatcher();
    }
  };

  ws.onmessage = (event) => {
    lastServerMessageTime = Date.now();
    try {
      const msg = JSON.parse(event.data);
      handleServerMessage(msg);
    } catch (e) {
      console.error('[CDP] Failed to parse server message:', e);
    }
  };

  ws.onclose = (event) => {
    console.debug('[CDP] WebSocket disconnected (code:', event.code, 'reason:', event.reason || 'none', ')');
    connectionStatus = 'disconnected';
    lastServerMessageTime = null;
    updateBadge();
    ws = null;
    stopClientHeartbeat();

    // Exponential backoff reconnect
    scheduleReconnect();
  };

  ws.onerror = (_err) => {
    console.warn('[CDP] WebSocket connection failed — will retry');
    // onclose will fire after this, triggering reconnect
  };
}

function scheduleReconnect() {
  if (wsReconnectTimer) clearTimeout(wsReconnectTimer);

  const delay = reconnectDelay;
  console.debug('[CDP] Reconnecting in', delay, 'ms');
  wsReconnectTimer = setTimeout(connectWS, delay);

  // Exponential backoff: 1s → 3s → 9s → 27s → 60s (cap)
  reconnectDelay = Math.min(reconnectDelay * 3, 60000);
}

function disconnectWS() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    try { ws.close(); } catch (e) { /* ignore */ }
    ws = null;
  }
  reconnectDelay = 1000;
  connectionStatus = 'disconnected';
  stopClientHeartbeat();
  stopReattachWatcher();
  cancelKeepalive();
  updateBadge();
}

// ====== Heartbeat ======

function startClientHeartbeat() {
  stopClientHeartbeat();
  clientHeartbeatTimer = setInterval(() => {
    // Send client-side ping as fallback heartbeat
    // The server also pings us — this ensures bidirectional detection
    if (ws && ws.readyState === WebSocket.OPEN) {
      safeSend({ type: 'ping' });
    }
  }, CLIENT_HEARTBEAT_INTERVAL);
}

function stopClientHeartbeat() {
  if (clientHeartbeatTimer) {
    clearInterval(clientHeartbeatTimer);
    clientHeartbeatTimer = null;
  }
}

// ====== Tab List / Switch (from Server) ======

function handleTabCommand(msg) {
  const id = msg.id;
  const command = msg.tabCommand; // 'list', 'switch', 'attach', 'detach', 'list-attachments'
  const tabId = msg.tabId;

  if (command === 'list') {
    chrome.tabs.query({}, (tabs) => {
      const tabList = tabs.map(t => ({
        id: t.id,
        index: t.index,
        title: t.title || '',
        url: t.url || '',
        active: t.active,
        windowId: t.windowId,
        pinned: t.pinned,
      }));
      safeSend({ id, type: 'tab-list', tabs: tabList });
    });
    return true;
  }

  if (command === 'switch') {
    if (tabId == null) {
      safeSend({ id, type: 'tab-switch', success: false, error: 'No tabId' });
      return true;
    }
    attachToTab(tabId).then((result) => {
      safeSend({ id, type: 'tab-switch', ...result });
    });
    return true;
  }

  if (command === 'attach') {
    if (tabId == null) {
      safeSend({ id, type: 'tab-attach', success: false, error: 'No tabId' });
      return true;
    }
    attachToTab(tabId).then((result) => {
      safeSend({ id, type: 'tab-attach', ...result, attachmentCount: attachments.size });
    });
    return true;
  }

  if (command === 'detach') {
    if (tabId == null) {
      safeSend({ id, type: 'tab-detach', success: false, error: 'No tabId' });
      return true;
    }
    detach(tabId).then((result) => {
      safeSend({ id, type: 'tab-detach', ...result, attachmentCount: attachments.size });
    });
    return true;
  }

  if (command === 'list-attachments') {
    const attached = Array.from(attachments.keys()).map(tid => ({ tabId: tid }));
    safeSend({ id, type: 'tab-attachment-list', attachments: attached });
    return true;
  }

  if (command === 'create') {
    const url = msg.url || 'about:blank';
    const active = msg.active !== false;
    chrome.tabs.create({ url, active }, (tab) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        safeSend({ id, type: 'tab-create', success: false, error: lastError.message });
        return;
      }
      safeSend({ id, type: 'tab-create', success: true, tabId: tab.id, title: tab.title || '', url: tab.url || '' });
    });
    return true;
  }

  if (command === 'close') {
    const tabId = msg.tabId;
    if (tabId == null) {
      safeSend({ id, type: 'tab-close', success: false, error: 'No tabId' });
      return true;
    }
    chrome.tabs.remove(tabId, () => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        safeSend({ id, type: 'tab-close', success: false, error: lastError.message });
        return;
      }
      // Auto-clean attachments
      attachments.delete(tabId);
      updateBadge();
      safeSend({ id, type: 'tab-close', success: true, tabId });
    });
    return true;
  }

  return false;
}

// ====== Reattach Watcher ======

function startReattachWatcher() {
  stopReattachWatcher();
  console.debug('[CDP] Starting reattach watcher (every 5s)');
  reattachWatcherTimer = setInterval(() => {
    if (attachments.size > 0 || connectionStatus !== 'connected') {
      stopReattachWatcher();
      return;
    }
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0] && tabs[0].id) {
        attachToTab(tabs[0].id);
      }
    });
  }, REATTACH_INTERVAL);
}

function stopReattachWatcher() {
  if (reattachWatcherTimer) {
    clearInterval(reattachWatcherTimer);
    reattachWatcherTimer = null;
  }
}

// ====== Content Script Routing ======

// Cache which tabs have a content script available (avoids failed sendMessage per call)
let _csTabs = new Set();  // Set<tabId> with known content script
let _csGraceSet = new Set(); // Set<tabId> that had CS but might have navigated; try once

async function routeToContentScript(tabId, msg) {
  const id = msg.id;
  try {
    // Proactive CS health check: wrap sendMessage with a short timeout
    // If CS doesn't respond within 150ms, skip the 80ms retry and go straight to CDP
    const response = await Promise.race([
      chrome.tabs.sendMessage(tabId, {
        type: 'hermes',
        func: msg._hermesFunc || msg.params?._hermesFunc,
        args: msg._hermesArgs || msg.params?._hermesArgs,
      }),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('CS health check timed out')), 150)
      ),
    ]);
    _csTabs.add(tabId);
    _csGraceSet.delete(tabId);
    if (response && response.ok) {
      safeSend({
        id: id,
        result: {
          result: { type: 'object', value: response.result },
        },
      });
    } else {
      safeSend({
        id: id,
        error: { code: -32000, message: response?.error || 'Content script command failed' },
      });
    }
  } catch (e) {
    // CS unavailable (navigating, not injected yet, or timeout)
    // Skip retry — proactive check already determined CS isn't responding
    // The MCP server's _hermes_call has its own 80ms smart retry
    _csTabs.delete(tabId);
    _csGraceSet.add(tabId);
    safeSend({
      id: id,
      error: { code: -32999, message: 'Content script unavailable', _fallback: true },
    });
  }
}

// ====== Push Events from Content Script ======

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === 'page_loaded' || msg.type === 'page_updated') {
    // Forward to relay so MCP/plugin can hear about page state changes
    safeSend({
      type: 'page_event',
      event: msg.type,
      tabId: sender.tab?.id,
      url: msg.url || msg.state?.url,
      title: msg.title || msg.state?.title,
      state: msg.state,
    });
    // Clear grace set on page_loaded — content script is back
    if (msg.type === 'page_loaded' && sender.tab?.id) {
      _csGraceSet.delete(sender.tab.id);
      _csTabs.add(sender.tab.id);
    }
    return false;
  }
  return false;
});

// ====== Handle CDP Commands from Server ======

function handleServerMessage(msg) {
  const msgType = msg.type;

  // Handle heartbeats from server
  if (msgType === 'ping') {
    safeSend({ type: 'pong' });
    return;
  }
  if (msgType === 'pong') {
    return;
  }

  // Handle tab list / switch commands
  if (msg.tabCommand) {
    handleTabCommand(msg);
    return;
  }

  // Handle block-patterns commands
  if (msg.blockCommand === 'set') {
    (async () => {
      blockedPatterns = msg.patterns || [];
      await updateBlockingRules(blockedPatterns);
      safeSend({ id: msg.id, type: 'block-patterns', success: true, count: blockedPatterns.length });
    })();
    return;
  }
  if (msg.blockCommand === 'get') {
    (async () => {
      const rules = await getBlockingRules();
      safeSend({ id: msg.id, type: 'block-patterns', patterns: blockedPatterns, rules: rules });
    })();
    return;
  }

  const id = msg.id;
  const method = msg.method;
  const params = msg.params || {};
  const targetTabId = msg.tabId;

  if (attachments.size === 0) {
    if (id) {
      safeSend({
        id: id,
        error: { code: -32000, message: 'No tabs attached' },
      });
    }
    return;
  }

  // Determine which tab to route the command to
  let targetId = targetTabId;
  if (!targetId || !attachments.has(targetId)) {
    targetId = attachments.keys().next().value;
  }

  // ── Content Script Fast Path ───────────────────────────────────────
  // If this command has the _hermes flag, route directly to the content
  // script in the page, bypassing CDP Runtime.evaluate entirely.
  if (msg._hermes) {
    routeToContentScript(targetId, msg);
    return;
  }
  // Also route Runtime.evaluate that contains __hermes calls directly
  // (backward compat with non-flagged messages from older MCP servers)
  if (method === 'Runtime.evaluate') {
    const expr = (params.expression || '').toString();
    if (expr.includes('__hermes.') && _csTabs.has(targetId)) {
      // Inline hermes call — best-effort route to content script
      // Fallback is handled by content script error path
      try {
        chrome.tabs.sendMessage(targetId, {
          type: 'hermes',
          func: '__eval',
          args: [expr],
        }).then(response => {
          if (response && response.ok) {
            safeSend({
              id: id,
              result: { result: { type: 'object', value: response.result } },
            });
          } else {
            // Fall through to CDP path below
            sendViaCDP(targetId, id, method, params);
          }
        }).catch(() => {
          sendViaCDP(targetId, id, method, params);
        });
        return;
      } catch (e) {
        // Fall through to CDP
      }
    }
  }

  // ── Standard CDP Path ──────────────────────────────────────────────
  sendViaCDP(targetId, id, method, params);
}

function sendViaCDP(targetId, id, method, params) {
  chrome.debugger.sendCommand(
    { tabId: targetId },
    method,
    params,
    (result) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        if (id) {
          safeSend({
            id: id,
            error: { code: -32000, message: lastError.message },
          });
        }
        return;
      }

      if (id) {
        safeSend({
          id: id,
          result: result || {},
        });
      }
    }
  );
}

// ====== Hermes Helper Injection ======

// Cache the injected script source to avoid repeated fetch() calls
let _cachedHelperSource = null;

async function injectHermesHelper(tabId) {
  try {
    // Load helper source (cached after first fetch)
    if (!_cachedHelperSource) {
      const resp = await fetch(chrome.runtime.getURL('hermes-helper.min.js'));
      _cachedHelperSource = await resp.text();
    }
    // Inject via CDP — runs in every future document in this target
    await new Promise((resolve) => {
      chrome.debugger.sendCommand(
        { tabId },
        'Page.addScriptToEvaluateOnNewDocument',
        { scriptSource: _cachedHelperSource },
        () => resolve()
      );
    });
    console.debug('[CDP] __hermes helper injected into tab', tabId);
  } catch (e) {
    console.warn('[CDP] Failed to inject __hermes helper:', e.message);
  }
}

// ====== Attach / Detach (Multi-Tab) ======

async function attachToTab(tabId) {
  // Already attached to this tab — nothing to do
  if (attachments.has(tabId)) {
    return { success: true, tabId, alreadyAttached: true, attachmentCount: attachments.size };
  }

  return new Promise((resolve) => {
    chrome.debugger.attach({ tabId: tabId }, '1.3', () => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        resolve({ success: false, error: lastError.message });
        return;
      }

      attachments.set(tabId, true);
      updateBadge();

      // Enable Network + Page domains (fire-and-forget)
      chrome.debugger.sendCommand({ tabId }, 'Network.enable', {}, () => {
        if (chrome.runtime.lastError) {
          console.warn('[CDP] Network.enable failed:', chrome.runtime.lastError.message);
        }
      });
      chrome.debugger.sendCommand({ tabId }, 'Page.enable', {}, () => {
        if (chrome.runtime.lastError) {
          console.warn('[CDP] Page.enable failed:', chrome.runtime.lastError.message);
        }
      });

      // Inject __hermes helper for all future page loads in this tab
      injectHermesHelper(tabId);

      // Notify server
      if (ws && ws.readyState === WebSocket.OPEN) {
        safeSend({
          type: 'hello',
          tabId: tabId,
          info: { attached: true },
        });
      }

      // Stop reattach watcher — at least one attachment exists
      stopReattachWatcher();

      resolve({ success: true, tabId, attachmentCount: attachments.size });
    });
  });
}

async function detach(tabId) {
  if (!attachments.has(tabId)) {
    return { success: true, alreadyDetached: true, attachmentCount: attachments.size };
  }

  return new Promise((resolve) => {
    chrome.debugger.detach({ tabId: tabId }, () => {
      attachments.delete(tabId);
      updateBadge();
      resolve({ success: true, tabId, attachmentCount: attachments.size });
    });
  });
}

async function detachAll() {
  const ids = Array.from(attachments.keys());
  for (const tid of ids) {
    try {
      await new Promise((resolve) => {
        chrome.debugger.detach({ tabId: tid }, () => resolve());
      });
    } catch (e) {
      // Ignore individual detach errors
    }
    attachments.delete(tid);
  }
  updateBadge();
  return { success: true, detachedCount: ids.length };
}

// ====== Badge ======

function updateBadge() {
  let color, text;

  if (connectionStatus === 'connected' && attachments.size > 0) {
    color = '#4CAF50';
    text = String(attachments.size);
  } else if (connectionStatus === 'connected') {
    color = '#2196F3';
    text = 'WS';
  } else if (connectionStatus === 'connecting') {
    color = '#FF9800';
    text = '...';
  } else {
    color = '#9E9E9E';
    text = '';
  }

  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
}

// ====== Tab Switch Auto-Attach ======

chrome.tabs.onActivated.addListener((activeInfo) => {
  const newTabId = activeInfo.tabId;
  if (connectionStatus !== 'connected') return;
  if (!autoAttach) return;  // User disabled auto-attach

  // Ignore chrome:// URLs
  chrome.tabs.get(newTabId, (tab) => {
    if (chrome.runtime.lastError) return;
    if (!tab || !tab.url) return;
    if (tab.url.startsWith('chrome://') || tab.url.startsWith('edge://') || tab.url.startsWith('about:')) {
      return;
    }

    console.debug('[CDP] Tab activated:', newTabId, '-', tab.url);
    attachToTab(newTabId);
  });
});

// ====== Message from Popup ======

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'connect':
      reconnectDelay = 1000;
      connectWS();
      setTimeout(() => sendResponse({
        connectionStatus,
        attachmentCount: attachments.size,
        attachedTabIds: Array.from(attachments.keys()),
      }), 500);
      return true;

    case 'disconnect':
      disconnectWS();
      sendResponse({ connectionStatus, attachmentCount: 0 });
      break;

    case 'attach':
      if (!msg.tabId) {
        sendResponse({ success: false, error: 'No tab ID' });
        break;
      }
      attachToTab(msg.tabId).then(sendResponse);
      return true;

    case 'detach':
      if (msg.tabId) {
        detach(msg.tabId).then(sendResponse);
      } else {
        detachAll().then(sendResponse);
      }
      return true;

    case 'get_status':
      sendResponse({
        connectionStatus,
        attachmentCount: attachments.size,
        attachedTabIds: Array.from(attachments.keys()),
        serverUrl: SERVER_URL,
        lastServerMessageTime,
        autoAttach,
      });
      break;

    case 'get_server_url':
      chrome.storage.sync.get('serverUrl', (data) => {
        sendResponse({ url: data.serverUrl || DEFAULT_SERVER });
      });
      return true;

    case 'set_server_url':
      saveServerUrl(msg.url, () => {
        sendResponse({ success: true, url: SERVER_URL });
      });
      return true;

    case 'set_auto_attach':
      autoAttach = !!msg.enabled;
      chrome.storage.sync.set({ autoAttach });
      sendResponse({ success: true, autoAttach });
      break;

    case 'create_tab':
      chrome.tabs.create({ url: msg.url || 'about:blank', active: msg.active !== false }, (tab) => {
        sendResponse({ success: true, tabId: tab.id, url: tab.url });
      });
      return true;

    case 'close_tab':
      if (!msg.tabId) {
        sendResponse({ success: false, error: 'No tabId' });
        break;
      }
      chrome.tabs.remove(msg.tabId, () => {
        const lastError = chrome.runtime.lastError;
        if (lastError) {
          sendResponse({ success: false, error: lastError.message });
          return;
        }
        attachments.delete(msg.tabId);
        updateBadge();
        sendResponse({ success: true });
      });
      return true;
  }
  return false;
});

// ====== Downloads Capture ======

chrome.downloads.onCreated.addListener((item) => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  safeSend({
    type: 'download',
    event: 'created',
    id: item.id,
    url: item.url,
    filename: item.filename,
    totalBytes: item.totalBytes,
    mime: item.mime,
    danger: item.danger,
  });
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!delta.state) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  safeSend({
    type: 'download',
    event: 'changed',
    id: delta.id,
    state: delta.state.current,
    filename: delta.filename?.current,
    error: delta.error?.current,
  });
});

// ====== Tab Navigation ======

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (!tab.url || tab.url.startsWith('chrome')) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  safeSend({
    type: 'tab',
    event: 'navigated',
    tabId: tabId,
    url: tab.url,
    title: tab.title,
  });
});

// ====== Chrome Alarms Keepalive (prevents MV3 SW termination) ======

function ensureKeepalive() {
  chrome.alarms.create('cdp-keepalive', { periodInMinutes: 0.5 });
}

function cancelKeepalive() {
  chrome.alarms.clear('cdp-keepalive');
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'cdp-keepalive') {
    // Send a keepalive ping if connected
    if (ws && ws.readyState === WebSocket.OPEN) {
      safeSend({ type: 'keepalive' });
    }
  }
});

// ====== Init ======
loadServerUrl();
ensureKeepalive();
connectWS();

// Load persisted autoAttach setting, then auto-attach if enabled
chrome.storage.sync.get('autoAttach', (data) => {
  if (data.autoAttach !== undefined) autoAttach = data.autoAttach;
  if (autoAttach) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0] && tabs[0].id) {
        attachToTab(tabs[0].id);
      }
    });
  }
});
