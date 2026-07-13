/**
 * Tests for extension/background.js
 *
 * Uses Node.js built-in test runner (node --test).
 * Mocks chrome.* APIs to verify routing logic, tab management, and message handling.
 */
import { describe, it, before, after, mock } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import chrome from './mock-chrome.js';

// Resolve paths relative to this file
const __dirname = dirname(fileURLToPath(import.meta.url));
const EXTENSION_DIR = resolve(__dirname, '..', '..', 'extension');

// -------------------------------------------------------------------------
// We eval background.js in a controlled scope with chrome mock injected
// -------------------------------------------------------------------------

let background;

before(() => {
  // Reset chrome mock state
  chrome._reset();

  // Read background.js and eval it with chrome mock injected
  const bgCode = readFileSync(resolve(EXTENSION_DIR, 'background.js'), 'utf-8');
  
  // Build a set of global definitions that background.js expects
  // background.js uses `function NAME(){}` style declarations at global scope
  // These get collected onto the `background` object
  const fn = new Function('chrome', bgCode);
  fn(chrome);

  // Collect the functions and state that background.js defined globally
  background = {
    routeToContentScript: globalThis.routeToContentScript,
    attachToTab: globalThis.attachToTab,
    detach: globalThis.detach,
    detachAll: globalThis.detachAll,
    sendViaCDP: globalThis.sendViaCDP,
    handleServerMessage: globalThis.handleServerMessage,
    safeSend: globalThis.safeSend,
    updateBadge: globalThis.updateBadge,
    shouldCaptureBody: globalThis.shouldCaptureBody,
    loadServerUrl: globalThis.loadServerUrl,
    saveServerUrl: globalThis.saveServerUrl,
    startReattachWatcher: globalThis.startReattachWatcher,
    stopReattachWatcher: globalThis.stopReattachWatcher,
    startClientHeartbeat: globalThis.startClientHeartbeat,
    stopClientHeartbeat: globalThis.stopClientHeartbeat,
  };

  // Expose state vars for test assertions
  background._state = {
    attachments() { return globalThis.attachments; },
    csTabs() { return globalThis._csTabs; },
    csGraceSet() { return globalThis._csGraceSet; },
  };
});

after(() => {
  // Clean up global functions injected by background.js
  const funcs = Object.keys(background).filter(k => k !== '_state');
  for (const fn of funcs) {
    delete globalThis[fn];
  }
  // Clean up state vars
  ['DEFAULT_SERVER', 'SERVER_URL', 'ws', 'attachments', '_csTabs', '_csGraceSet',
   'connectionStatus', 'reattachWatcherTimer', 'clientHeartbeatTimer',
   'blockedPatterns', 'bodyCapturePatterns', 'bodyTargets'].forEach(v => {
    delete globalThis[v];
  });
});

// ======================================================================
// Content Script Routing Tests
// ======================================================================

describe('routeToContentScript', () => {
  it('should attempt to route hermes commands to content script', { timeout: 2000 }, async () => {
    // Add a tab with content script
    const tab = chrome.addTab({ id: 42, url: 'https://example.com' });
    globalThis._csTabs.add(42);

    // Set up chrome.tabs.sendMessage to succeed
    chrome.tabs.sendMessage = (...args) => {
      const cb = args[args.length - 1];
      if (typeof cb === 'function') {
        cb({ ok: true, result: { test: 'value' } });
      }
      return Promise.resolve({ ok: true, result: { test: 'value' } });
    };

    const result = await background.routeToContentScript(42, {
      _hermes: true,
      _hermesFunc: 'click',
      _hermesArgs: ['#btn'],
    });

    assert.ok(result.ok, 'Expected successful routing');
    assert.equal(result.result.test, 'value');
  });

  it('should add tab to grace set on content script failure', { timeout: 2000 }, async () => {
    const tab = chrome.addTab({ id: 43, url: 'https://example.com' });
    globalThis._csTabs.add(43);
    globalThis._csGraceSet.delete(43); // Ensure clean state

    // Make sendMessage fail
    chrome.tabs.sendMessage = (...args) => {
      const cb = args[args.length - 1];
      if (typeof cb === 'function') {
        chrome.runtime.lastError = { message: 'Could not establish connection' };
        cb();
      }
      return Promise.reject(new Error('Could not establish connection'));
    };

    await background.routeToContentScript(43, {
      _hermes: true,
      _hermesFunc: 'click',
      _hermesArgs: ['#btn'],
    });

    // Should be in grace set (for retry)
    assert.ok(globalThis._csGraceSet.has(43), 'Tab should be in grace set after failure');
  });

  it('should forward CDP commands directly (non-hermes)', { timeout: 2000 }, async () => {
    chrome.debugger.sendCommand = (tabId, method, params) => {
      return Promise.resolve({ result: { success: true } });
    };

    const tab = chrome.addTab({ id: 44 });
    const result = await background.routeToContentScript(44, {
      method: 'Page.navigate',
      params: { url: 'https://example.com' },
    });

    assert.ok(result, 'Expected CDP command to return result');
  });
});

// ======================================================================
// Tab Management Tests
// ======================================================================

describe('attachToTab', () => {
  it('should attach debugger to a tab', async () => {
    const tab = chrome.addTab({ id: 50, url: 'https://example.com' });
    globalThis.attachments = [];

    let attachCalled = false;
    chrome.debugger.attach = (target, version, callback) => {
      attachCalled = true;
      if (callback) callback();
      return Promise.resolve();
    };

    await background.attachToTab(50);
    assert.ok(attachCalled, 'debugger.attach should be called');
  });
});

describe('detach', () => {
  it('should detach from a tab and clean up', async () => {
    const tab = chrome.addTab({ id: 60 });
    globalThis.attachments = [{ tabId: 60, attachedAt: Date.now() }];

    let detachCalled = false;
    chrome.debugger.detach = (target, callback) => {
      detachCalled = true;
      if (callback) callback();
      return Promise.resolve();
    };

    await background.detach(60);
    assert.ok(detachCalled, 'debugger.detach should be called');
  });
});

// ======================================================================
// Message Handling Tests
// ======================================================================

describe('onMessage (from content script)', () => {
  it('should handle page_loaded event', () => {
    const tab = chrome.addTab({ id: 70, url: 'https://example.com' });

    // Clear grace set if present
    globalThis._csGraceSet.delete(70);
    globalThis._csTabs.delete(70);

    chrome.simulateContentScriptMessage(70, {
      type: 'page_loaded',
      url: 'https://example.com',
      title: 'Test Page',
      state: { total: 10, visible: 5, readyState: 'complete' },
    });

    // Should add to _csTabs and remove from grace set
    assert.ok(globalThis._csTabs.has(70), 'Tab should be in _csTabs after page_loaded');
    assert.ok(!globalThis._csGraceSet.has(70), 'Tab should not be in grace set after page_loaded');
  });

  it('should handle page_updated event', () => {
    const tab = chrome.addTab({ id: 71 });
    globalThis._csTabs.add(71);

    chrome.simulateContentScriptMessage(71, {
      type: 'page_updated',
      state: { total: 15, visible: 8, readyState: 'complete' },
    });

    // Should not crash
    assert.ok(true);
  });

  it('should handle blockCommand set/get', () => {
    globalThis.blockedPatterns = [];

    chrome.simulateContentScriptMessage(1, {
      blockCommand: 'set',
      patterns: ['*.css', '*.jpg'],
    });

    chrome.simulateContentScriptMessage(1, {
      blockCommand: 'get',
    });
  });
});

// ======================================================================
// Helper Function Tests
// ======================================================================

describe('safeSend', () => {
  it('should send JSON-stringified data via WebSocket', () => {
    let sentData = null;
    globalThis.ws = {
      readyState: 1, // WebSocket.OPEN
      send: (data) => { sentData = data; },
    };

    background.safeSend({ type: 'test', value: 42 });

    assert.ok(sentData, 'Data should be sent');
    const parsed = JSON.parse(sentData);
    assert.equal(parsed.type, 'test');
    assert.equal(parsed.value, 42);
  });

  it('should not send if WebSocket is not open', () => {
    let sentData = null;
    globalThis.ws = {
      readyState: 3, // CLOSED
      send: (data) => { sentData = data; },
    };

    background.safeSend({ type: 'test' });
    assert.equal(sentData, null, 'Should not send when WS is closed');
  });
});

describe('shouldCaptureBody', () => {
  it('should capture JSON and PDF responses', () => {
    assert.ok(background.shouldCaptureBody('application/json'));
    assert.ok(background.shouldCaptureBody('application/pdf'));
    assert.ok(!background.shouldCaptureBody('text/html'));
    assert.ok(!background.shouldCaptureBody('image/png'));
    assert.ok(!background.shouldCaptureBody(''));
  });
});

// ======================================================================
// Connection Lifecycle Tests
// ======================================================================

describe('connection management', () => {
  it('should start and stop client heartbeat', () => {
    background.startClientHeartbeat();
    assert.ok(globalThis.clientHeartbeatTimer, 'Heartbeat timer should be set');

    background.stopClientHeartbeat();
    assert.equal(globalThis.clientHeartbeatTimer, null, 'Heartbeat timer should be cleared');
  });

  it('should start and stop reattach watcher', () => {
    background.startReattachWatcher();
    assert.ok(globalThis.reattachWatcherTimer, 'Reattach watcher timer should be set');

    background.stopReattachWatcher();
    assert.equal(globalThis.reattachWatcherTimer, null, 'Reattach watcher timer should be cleared');
  });
});

// ======================================================================
// Server URL Tests
// ======================================================================

describe('loadServerUrl / saveServerUrl', () => {
  it('should default to DEFAULT_SERVER', async () => {
    // Mock storage to return empty
    chrome.storage.local.get = (keys) => Promise.resolve({});
    const url = await background.loadServerUrl();
    assert.equal(url, globalThis.DEFAULT_SERVER);
  });

  it('should save and load server URL', async () => {
    let saved = null;
    chrome.storage.local.set = (data) => {
      saved = data;
      return Promise.resolve();
    };
    chrome.storage.local.get = (keys) => {
      return Promise.resolve(saved || {});
    };

    await background.saveServerUrl('http://127.0.0.1:9223');
    const loaded = await background.loadServerUrl();
    assert.equal(loaded, 'http://127.0.0.1:9223');
  });
});
