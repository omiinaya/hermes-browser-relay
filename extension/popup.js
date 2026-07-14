// CDP Bridge — Popup with Tab Selection

let cachedTabs = [];
let cachedAttachments = [];
let cachedStatus = {};

document.addEventListener('DOMContentLoaded', () => {
  const connDot = document.getElementById('connDot');
  const connLabel = document.getElementById('connLabel');
  const connectBtn = document.getElementById('connectBtn');
  const serverUrlInput = document.getElementById('serverUrl');
  const saveUrlBtn = document.getElementById('saveUrlBtn');
  const tabList = document.getElementById('tabList');
  const attachCount = document.getElementById('attachCount');
  const autoAttachToggle = document.getElementById('autoAttachToggle');
  const footerInfo = document.getElementById('footerInfo');

  // Load saved server URL
  chrome.runtime.sendMessage({ action: 'get_server_url' }, (resp) => {
    if (resp && resp.url) serverUrlInput.value = resp.url;
  });

  // Save server URL
  saveUrlBtn.addEventListener('click', () => {
    const url = serverUrlInput.value.trim();
    if (!url.startsWith('ws://') && !url.startsWith('wss://')) {
      serverUrlInput.style.borderColor = '#f44336';
      setTimeout(() => serverUrlInput.style.borderColor = '', 2000);
      return;
    }
    saveUrlBtn.disabled = true;
    saveUrlBtn.textContent = '...';
    chrome.runtime.sendMessage({ action: 'set_server_url', url }, (resp) => {
      setTimeout(() => {
        saveUrlBtn.disabled = false;
        saveUrlBtn.textContent = 'Save';
        if (resp && resp.success) serverUrlInput.style.borderColor = '#4caf50';
        setTimeout(() => serverUrlInput.style.borderColor = '', 1000);
      }, 500);
    });
  });

  function timeAgo(ts) {
    if (!ts) return 'never';
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 2) return 'just now';
    if (sec < 60) return sec + 's ago';
    const min = Math.floor(sec / 60);
    return min + 'm ' + (sec % 60) + 's ago';
  }

  function renderTabList() {
    const attachedSet = new Set(cachedAttachments.map(a => a.tabId));
    const attachedIds = Array.from(attachedSet);

    // Update count badge
    attachCount.textContent = attachedIds.length;
    attachCount.style.color = attachedIds.length > 0 ? '#4caf50' : '#555';

    // Build tab list
    let html = '';
    if (cachedTabs.length === 0) {
      html = '<div class="label-sm" style="padding:8px 4px;text-align:center;color:#555;">No tabs found</div>';
    } else {
      for (const t of cachedTabs) {
        const tid = t.id;
        const isAttached = attachedSet.has(tid);
        const title = (t.title || '').trim() || '(untitled)';
        const url = (t.url || '').substring(0, 80);

        html += `<div class="tab-item${isAttached ? ' attached' : ''}">`;
        html += `  <span class="tab-id-badge">${tid}</span>`;
        html += `  <div class="tab-info">`;
        html += `    <div class="tab-title">${escapeHtml(title)}</div>`;
        html += `    <div class="tab-url">${escapeHtml(url)}</div>`;
        html += `  </div>`;
        if (isAttached) {
          html += `  <button class="btn btn-detach" data-action="detach" data-tab-id="${tid}">−</button>`;
        } else {
          html += `  <button class="btn btn-attach btn-sm" data-action="attach" data-tab-id="${tid}">+ Attach</button>`;
        }
        html += `</div>`;
      }
    }
    tabList.innerHTML = html;

    // Bind button clicks
    tabList.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', (_e) => {
        const action = btn.dataset.action;
        const tabId = parseInt(btn.dataset.tabId);
        btn.disabled = true;

        if (action === 'attach') {
          chrome.runtime.sendMessage({ action: 'attach', tabId }, () => {
            refreshAll();
          });
        } else if (action === 'detach') {
          chrome.runtime.sendMessage({ action: 'detach', tabId }, () => {
            refreshAll();
          });
        }
      });
    });
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function updateUI(status) {
    cachedStatus = status;

    // Connection
    const connColors = { connected: 'green', connecting: 'yellow', disconnected: 'gray', error: 'red' };
    const connLabels = { connected: 'Connected to server', connecting: 'Connecting...', disconnected: 'Disconnected', error: 'Connection error' };
    connDot.className = `dot ${connColors[status.connectionStatus] || 'gray'}`;
    connLabel.textContent = connLabels[status.connectionStatus] || 'Unknown';
    connLabel.style.color = status.connectionStatus === 'connected' ? '#4caf50' : '#888';

    // Connect button
    if (status.connectionStatus === 'connected') {
      connectBtn.textContent = 'Disconnect';
      connectBtn.className = 'btn btn-outline btn-sm';
    } else {
      connectBtn.textContent = 'Connect';
      connectBtn.className = 'btn btn-primary btn-sm';
    }

    // Auto-attach toggle
    if (status.autoAttach !== undefined) {
      autoAttachToggle.className = 'toggle' + (status.autoAttach ? ' on' : '');
    }

    // Footer
    if (status.connectionStatus === 'connected') {
      const msg = status.lastServerMessageTime ? 'Last message: ' + timeAgo(status.lastServerMessageTime) : 'Connected';
      footerInfo.textContent = `${msg} · ${status.attachmentCount || 0} attached`;
    } else {
      footerInfo.textContent = 'Disconnected';
    }
  }

  function refreshAll() {
    // Get status
    chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
      updateUI(status);
      cachedAttachments = (status.attachedTabIds || []).map(id => ({ tabId: id }));
    });

    // Get all tabs
    chrome.tabs.query({}, (tabs) => {
      cachedTabs = tabs || [];
      renderTabList();
    });
  }

  // Connect/Disconnect
  connectBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
      if (status.connectionStatus === 'connected') {
        chrome.runtime.sendMessage({ action: 'disconnect' }, refreshAll);
      } else {
        chrome.runtime.sendMessage({ action: 'connect' }, refreshAll);
      }
    });
  });

  // Auto-attach toggle
  autoAttachToggle.addEventListener('click', () => {
    const newVal = !cachedStatus.autoAttach;
    chrome.runtime.sendMessage({ action: 'set_auto_attach', enabled: newVal }, (resp) => {
      if (resp && resp.success) {
        cachedStatus.autoAttach = resp.autoAttach;
        autoAttachToggle.className = 'toggle' + (resp.autoAttach ? ' on' : '');
      }
    });
  });

  // Initial load + poll
  refreshAll();
  setInterval(refreshAll, 3000);
});
