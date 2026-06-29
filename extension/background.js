// CDP Bridge — Background Service Worker (v1.1.0)
// Connects chrome.debugger to Hermes Agent via WebSocket
// Features: heartbeat keepalive, exponential backoff reconnect,
// tab-switch auto-reattach, robust error handling

const DEFAULT_SERVER = 'ws://127.0.0.1:9223/ws';
let SERVER_URL = DEFAULT_SERVER;
let ws = null;
let wsReconnectTimer = null;
let attachedTabId = null;
let attached = false;
let reconnectDelay = 1000; // Starts at 1s, backs off to max 60s

// Connection status: 'disconnected', 'connecting', 'connected'
let connectionStatus = 'disconnected';
// Tab status: 'detached', 'attaching', 'attached'
let tabStatus = 'detached';
// Track last time we heard from the server
let lastServerMessageTime = null;

// Client-side heartbeat fallback (if server doesn't ping)
let clientHeartbeatTimer = null;
const CLIENT_HEARTBEAT_INTERVAL = 20000; // 20s

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
    if (target && attachedTabId) {
      bodyTargets.delete(params.requestId);
      chrome.debugger.sendCommand(
        { tabId: attachedTabId },
        'Network.getResponseBody',
        { requestId: params.requestId },
        (result) => {
          const lastError = chrome.runtime.lastError;
          if (lastError) {
            console.warn('[CDP] getResponseBody failed:', lastError.message, 'url:', target.url);
            return;
          }
          if (result && result.body !== undefined && ws && ws.readyState === WebSocket.OPEN) {
            safeSend({
              type: 'response_body',
              requestId: params.requestId,
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
});

chrome.debugger.onDetach.addListener((source, reason) => {
  console.log('[CDP] Detached from tab', source.tabId, '- reason:', reason);
  attachedTabId = null;
  attached = false;
  tabStatus = 'detached';
  updateBadge();

  // Auto-reattempt with exponential backoff
  if (connectionStatus === 'connected') {
    const reattachDelay = Math.min((reconnectDelay || 1000), 30000);
    console.log(`[CDP] Will attempt reattach in ${reattachDelay}ms`);
    setTimeout(() => {
      if (!attached && connectionStatus === 'connected') {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs && tabs[0] && tabs[0].id && tabs[0].url &&
              !tabs[0].url.startsWith('chrome://') && !tabs[0].url.startsWith('edge://') && !tabs[0].url.startsWith('about:')) {
            attachToTab(tabs[0].id);
          }
        });
      }
    }, reattachDelay);
  }
});

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

  console.log('[CDP] Connecting to', SERVER_URL);
  ws = new WebSocket(SERVER_URL);

  ws.onopen = () => {
    console.log('[CDP] WebSocket connected to ' + SERVER_URL);
    connectionStatus = 'connected';
    reconnectDelay = 1000; // Reset backoff on successful connect
    updateBadge();

    // Send hello with tab info if attached
    if (attachedTabId) {
      safeSend({
        type: 'hello',
        tabId: attachedTabId,
        info: { attached: true },
      });
    }

    // Start client-side heartbeat as fallback
    startClientHeartbeat();
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
    console.log('[CDP] WebSocket disconnected (code:', event.code, 'reason:', event.reason || 'none', ')');
    connectionStatus = 'disconnected';
    lastServerMessageTime = null;
    updateBadge();
    ws = null;
    stopClientHeartbeat();

    // Exponential backoff reconnect
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.warn('[CDP] WebSocket connection failed — will retry');
    // onclose will fire after this, triggering reconnect
  };
}

function scheduleReconnect() {
  if (wsReconnectTimer) clearTimeout(wsReconnectTimer);

  const delay = reconnectDelay;
  console.log('[CDP] Reconnecting in', delay, 'ms');
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

// ====== Handle CDP Commands from Server ======

function handleServerMessage(msg) {
  const msgType = msg.type;

  // Handle heartbeats from server
  if (msgType === 'ping') {
    safeSend({ type: 'pong' });
    return;
  }
  if (msgType === 'pong') {
    // Server responded to our ping — connection is alive
    return;
  }

  const id = msg.id;
  const method = msg.method;
  const params = msg.params || {};

  if (!attachedTabId || !attached) {
    if (id) {
      safeSend({
        id: id,
        error: { code: -32000, message: 'No tab attached' },
      });
    }
    return;
  }

  chrome.debugger.sendCommand(
    { tabId: attachedTabId },
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

// ====== Attach / Detach ======

async function attachToTab(tabId) {
  if (attached && attachedTabId === tabId) {
    return { success: true, tabId };
  }

  // Detach from current first
  if (attachedTabId) {
    try {
      await new Promise((resolve) => {
        chrome.debugger.detach({ tabId: attachedTabId }, () => resolve());
      });
    } catch (e) {
      // Ignore detach errors
    }
  }

  tabStatus = 'attaching';
  updateBadge();

  return new Promise((resolve) => {
    chrome.debugger.attach({ tabId: tabId }, '1.3', () => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        tabStatus = 'detached';
        updateBadge();
        resolve({ success: false, error: lastError.message });
        return;
      }

      attachedTabId = tabId;
      attached = true;
      tabStatus = 'attached';
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

      // Notify server
      if (ws && ws.readyState === WebSocket.OPEN) {
        safeSend({
          type: 'hello',
          tabId: tabId,
          info: { attached: true },
        });
      }

      resolve({ success: true, tabId });
    });
  });
}

async function detach() {
  if (!attachedTabId) {
    tabStatus = 'detached';
    return { success: true };
  }

  return new Promise((resolve) => {
    chrome.debugger.detach({ tabId: attachedTabId }, () => {
      attachedTabId = null;
      attached = false;
      tabStatus = 'detached';
      updateBadge();
      resolve({ success: true });
    });
  });
}

// ====== Badge ======

function updateBadge() {
  let color, text;

  if (connectionStatus === 'connected' && tabStatus === 'attached') {
    color = '#4CAF50';
    text = 'CDP';
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

// ====== Tab Switch Auto-Reattach ======

chrome.tabs.onActivated.addListener((activeInfo) => {
  const newTabId = activeInfo.tabId;

  // Skip if same tab or no connection
  if (newTabId === attachedTabId) return;
  if (connectionStatus !== 'connected') return;

  // Ignore chrome:// URLs
  chrome.tabs.get(newTabId, (tab) => {
    if (chrome.runtime.lastError) return;
    if (!tab || !tab.url) return;
    if (tab.url.startsWith('chrome://') || tab.url.startsWith('edge://') || tab.url.startsWith('about:')) {
      return;
    }

    console.log('[CDP] Tab switched to', newTabId, '-', tab.url);
    attachToTab(newTabId);
  });
});

// ====== Message from Popup ======

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'connect':
      reconnectDelay = 1000; // Reset backoff on manual connect
      connectWS();
      setTimeout(() => sendResponse({ connectionStatus, tabStatus, attachedTabId }), 500);
      return true;

    case 'disconnect':
      disconnectWS();
      sendResponse({ connectionStatus, tabStatus });
      break;

    case 'attach':
      if (!msg.tabId) {
        sendResponse({ success: false, error: 'No tab ID' });
        break;
      }
      attachToTab(msg.tabId).then(sendResponse);
      return true;

    case 'detach':
      detach().then(sendResponse);
      return true;

    case 'get_status':
      sendResponse({ connectionStatus, tabStatus, attachedTabId, serverUrl: SERVER_URL, lastServerMessageTime });
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

// Auto-attach to the active tab on startup
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs[0] && tabs[0].id) {
    attachToTab(tabs[0].id);
  }
});
