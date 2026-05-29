// CDP Bridge — Background Service Worker
// Connects chrome.debugger to Hermes Agent via WebSocket

const DEFAULT_SERVER = 'ws://192.168.1.200:9988';
let SERVER_URL = DEFAULT_SERVER;
let ws = null;
let wsReconnectTimer = null;
let attachedTabId = null;
let attached = false;

// Connection status: 'disconnected', 'connecting', 'connected'
let connectionStatus = 'disconnected';
// Tab status: 'detached', 'attaching', 'attached'
let tabStatus = 'detached';

// ====== CDP Event Listeners ======

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  
  ws.send(JSON.stringify({
    type: 'event',
    method: method,
    params: params,
    tabId: source.tabId
  }));
});

chrome.debugger.onDetach.addListener((source, reason) => {
  console.log('[CDP] Detached from tab', source.tabId, '- reason:', reason);
  attachedTabId = null;
  attached = false;
  tabStatus = 'detached';
  updateBadge();
});

// ====== Global Web Request Capture (all tabs) ======

function sendWebRequest(type, details) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  // Trim down to essential fields to avoid flooding
  ws.send(JSON.stringify({
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
    // Response headers (if available)
    responseHeaders: details.responseHeaders ? details.responseHeaders.slice(0, 30) : undefined,
    requestHeaders: details.requestHeaders ? details.requestHeaders.slice(0, 30) : undefined,
  }));
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
      ws.close();
      ws = null;
    }
    if (callback) callback();
    setTimeout(connectWS, 100);
  });
}

// ====== WebSocket ======

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  
  connectionStatus = 'connecting';
  updateBadge();
  
  ws = new WebSocket(SERVER_URL);
  
  ws.onopen = () => {
    console.log('[CDP] WebSocket connected to ' + SERVER_URL);
    connectionStatus = 'connected';
    updateBadge();
    
    // If we were attached, send hello with tab info
    if (attachedTabId) {
      ws.send(JSON.stringify({
        type: 'hello',
        tabId: attachedTabId,
        info: { attached: true }
      }));
    }
  };
  
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleServerMessage(msg);
    } catch (e) {
      console.error('[CDP] Failed to parse server message:', e);
    }
  };
  
  ws.onclose = () => {
    console.log('[CDP] WebSocket disconnected');
    connectionStatus = 'disconnected';
    updateBadge();
    ws = null;
    
    // Auto-reconnect after 3s
    if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(connectWS, 3000);
  };
  
  ws.onerror = (err) => {
    console.error('[CDP] WebSocket error:', err);
    connectionStatus = 'error';
    updateBadge();
  };
}

function disconnectWS() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  connectionStatus = 'disconnected';
  updateBadge();
}

// ====== Handle CDP Commands from Server ======

function handleServerMessage(msg) {
  const id = msg.id;
  const method = msg.method;
  const params = msg.params || {};
  
  if (!attachedTabId || !attached) {
    if (id && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        id: id,
        error: { code: -32000, message: 'No tab attached' }
      }));
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
        if (id && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            id: id,
            error: { code: -32000, message: lastError.message }
          }));
        }
        return;
      }
      
      if (id && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          id: id,
          result: result || {}
        }));
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
      
      // Enable Network + Page domains
      chrome.debugger.sendCommand({ tabId }, 'Network.enable', {}, () => {});
      chrome.debugger.sendCommand({ tabId }, 'Page.enable', {}, () => {});
      
      // Notify server
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'hello',
          tabId: tabId,
          info: { attached: true }
        }));
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

// ====== Message from Popup ======

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'connect':
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
      sendResponse({ connectionStatus, tabStatus, attachedTabId, serverUrl: SERVER_URL });
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
  ws.send(JSON.stringify({
    type: 'download',
    event: 'created',
    id: item.id,
    url: item.url,
    filename: item.filename,
    totalBytes: item.totalBytes,
    mime: item.mime,
    danger: item.danger,
  }));
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!delta.state) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: 'download',
    event: 'changed',
    id: delta.id,
    state: delta.state.current,
    filename: delta.filename?.current,
    error: delta.error?.current,
  }));
});

// ====== Tab Navigation ======

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (!tab.url || tab.url.startsWith('chrome')) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: 'tab',
    event: 'navigated',
    tabId: tabId,
    url: tab.url,
    title: tab.title,
  }));
});

// ====== Init ======
loadServerUrl();
connectWS();
