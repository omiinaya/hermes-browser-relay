// CDP Bridge — Popup Script

document.addEventListener('DOMContentLoaded', () => {
  const connDot = document.getElementById('connDot');
  const connLabel = document.getElementById('connLabel');
  const connectBtn = document.getElementById('connectBtn');
  const serverUrlInput = document.getElementById('serverUrl');
  const saveUrlBtn = document.getElementById('saveUrlBtn');
  
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
    saveUrlBtn.textContent = 'Saving...';
    chrome.runtime.sendMessage({ action: 'set_server_url', url: url }, (resp) => {
      setTimeout(() => {
        saveUrlBtn.disabled = false;
        saveUrlBtn.textContent = 'Save';
        if (resp && resp.success) serverUrlInput.style.borderColor = '#4caf50';
        setTimeout(() => serverUrlInput.style.borderColor = '', 1000);
      }, 500);
    });
  });
  
  const tabDot = document.getElementById('tabDot');
  const tabLabel = document.getElementById('tabLabel');
  const tabIdDisplay = document.getElementById('tabIdDisplay');
  const attachBtn = document.getElementById('attachBtn');
  const detachBtn = document.getElementById('detachBtn');

  const diagSection = document.getElementById('diagSection');
  const diagLastMsg = document.getElementById('diagLastMsg');
  const diagServerUrl = document.getElementById('diagServerUrl');

  function timeAgo(ts) {
    if (!ts) return 'never';
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 2) return 'just now';
    if (sec < 60) return sec + 's ago';
    const min = Math.floor(sec / 60);
    return min + 'm ' + (sec % 60) + 's ago';
  }

  function updateUI(status) {
    // Connection status
    const connColors = { connected: 'green', connecting: 'yellow', disconnected: 'gray', error: 'red' };
    const connLabels = { connected: 'Connected to server', connecting: 'Connecting...', disconnected: 'Disconnected', error: 'Connection error' };
    connDot.className = `dot ${connColors[status.connectionStatus] || 'gray'}`;
    connLabel.textContent = connLabels[status.connectionStatus] || 'Unknown';
    
    // Tab status
    const tabColors = { attached: 'green', attaching: 'yellow', detached: 'gray' };
    const tabLabels = { attached: `Attached to tab`, attaching: 'Attaching...', detached: 'No tab attached' };
    tabDot.className = `dot ${tabColors[status.tabStatus] || 'gray'}`;
    tabLabel.textContent = tabLabels[status.tabStatus] || 'Unknown';
    
    if (status.attachedTabId) {
      tabIdDisplay.textContent = `Tab ID: ${status.attachedTabId}`;
      tabIdDisplay.style.display = 'block';
    } else {
      tabIdDisplay.style.display = 'none';
    }
    
    // Buttons
    const connected = status.connectionStatus === 'connected';
    attachBtn.disabled = !connected || status.tabStatus === 'attached' || status.tabStatus === 'attaching';
    detachBtn.disabled = status.tabStatus !== 'attached';
    
    if (status.connectionStatus === 'connected') {
      connectBtn.textContent = 'Disconnect';
      connectBtn.className = 'btn btn-outline';
    } else {
      connectBtn.textContent = 'Connect to Server';
      connectBtn.className = 'btn btn-primary';
    }

    // Diagnostics
    if (status.connectionStatus === 'connected') {
      diagSection.style.display = 'block';
      diagLastMsg.textContent = 'Last message: ' + timeAgo(status.lastServerMessageTime);
      diagServerUrl.textContent = 'Server: ' + status.serverUrl;
    } else {
      diagSection.style.display = 'none';
    }
  }
  
  function refreshStatus() {
    chrome.runtime.sendMessage({ action: 'get_status' }, updateUI);
  }
  
  // Poll status
  refreshStatus();
  setInterval(refreshStatus, 2000);
  
  // Connect/Disconnect
  connectBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
      if (status.connectionStatus === 'connected') {
        chrome.runtime.sendMessage({ action: 'disconnect' }, refreshStatus);
      } else {
        chrome.runtime.sendMessage({ action: 'connect' }, refreshStatus);
      }
    });
  });
  
  // Attach
  attachBtn.addEventListener('click', () => {
    attachBtn.disabled = true;
    attachBtn.textContent = 'Attaching...';
    
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs.length === 0) {
        attachBtn.textContent = 'No tab found';
        setTimeout(refreshStatus, 1500);
        return;
      }
      
      chrome.runtime.sendMessage({ action: 'attach', tabId: tabs[0].id }, (resp) => {
        if (resp && resp.success) {
          refreshStatus();
        } else {
          attachBtn.textContent = resp?.error || 'Failed';
          setTimeout(refreshStatus, 2000);
        }
      });
    });
  });
  
  // Detach
  detachBtn.addEventListener('click', () => {
    detachBtn.disabled = true;
    chrome.runtime.sendMessage({ action: 'detach' }, refreshStatus);
  });
});
