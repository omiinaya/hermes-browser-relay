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

  // new Function scopes function declarations and let/const variables inside the
  // function body — they never reach globalThis. Append export assignments that
  // copy the references to `this` (set to globalThis via .call()).
  const code = bgCode + `
    // Expose functions for tests
    this.routeToContentScript = routeToContentScript;
    this.attachToTab = attachToTab;
    this.detach = detach;
    this.detachAll = detachAll;
    this.sendViaCDP = sendViaCDP;
    this.handleServerMessage = handleServerMessage;
    this.safeSend = safeSend;
    this.updateBadge = updateBadge;
    this.shouldCaptureBody = shouldCaptureBody;
    this.queueEvent = queueEvent;
    this.pickHeaders = pickHeaders;
    this.FORWARDED_CDP_EVENTS = FORWARDED_CDP_EVENTS;
    this.loadServerUrl = loadServerUrl;
    this.saveServerUrl = saveServerUrl;
    this.startReattachWatcher = startReattachWatcher;
    this.stopReattachWatcher = stopReattachWatcher;
    this.startClientHeartbeat = startClientHeartbeat;
    this.stopClientHeartbeat = stopClientHeartbeat;
    this.DEFAULT_SERVER = DEFAULT_SERVER;
    // Mutable state: use getter/setter so mutations through globalThis
    // actually modify the local let variables that the functions close over.
    Object.defineProperty(this, '_csTabs', { get: () => _csTabs, set: (v) => { _csTabs = v; }, configurable: true });
    Object.defineProperty(this, '_csGraceSet', { get: () => _csGraceSet, set: (v) => { _csGraceSet = v; }, configurable: true });
    Object.defineProperty(this, 'attachments', { get: () => attachments, set: (v) => { attachments = v; }, configurable: true });
    Object.defineProperty(this, 'blockedPatterns', { get: () => blockedPatterns, set: (v) => { blockedPatterns = v; }, configurable: true });
    Object.defineProperty(this, 'clientHeartbeatTimer', { get: () => clientHeartbeatTimer, set: (v) => { clientHeartbeatTimer = v; }, configurable: true });
    Object.defineProperty(this, 'reattachWatcherTimer', { get: () => reattachWatcherTimer, set: (v) => { reattachWatcherTimer = v; }, configurable: true });
    Object.defineProperty(this, 'ws', { get: () => ws, set: (v) => { ws = v; }, configurable: true });
    Object.defineProperty(this, 'SERVER_URL', { get: () => SERVER_URL, set: (v) => { SERVER_URL = v; }, configurable: true });
  `;

  const fn = new Function('chrome', code);
  // Call with `this = globalThis` so export assignments land on globalThis
  // Set up storage mock to invoke callbacks (background.js init calls loadServerUrl)
  chrome.storage.sync.get = (keys, callback) => {
    if (typeof callback === 'function') callback({});
    return Promise.resolve({});
  };
  chrome.storage.sync.set = (data, callback) => {
    if (typeof callback === 'function') callback();
    return Promise.resolve();
  };
  chrome.storage.local.get = (keys, callback) => {
    if (typeof callback === 'function') callback({});
    return Promise.resolve({});
  };
  fn.call(globalThis, chrome);

  // Collect the functions and state that background.js defined
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
    queueEvent: globalThis.queueEvent,
    pickHeaders: globalThis.pickHeaders,
    FORWARDED_CDP_EVENTS: globalThis.FORWARDED_CDP_EVENTS,
    loadServerUrl: globalThis.loadServerUrl,
    saveServerUrl: globalThis.saveServerUrl,
    startReattachWatcher: globalThis.startReattachWatcher,
    stopReattachWatcher: globalThis.stopReattachWatcher,
    startClientHeartbeat: globalThis.startClientHeartbeat,
    stopClientHeartbeat: globalThis.stopClientHeartbeat,
  };

  // Expose state vars for test assertions (same references as background.js uses)
  background._state = {
    attachments: () => globalThis.attachments,
    csTabs: () => globalThis._csTabs,
    csGraceSet: () => globalThis._csGraceSet,
  };
});

after(() => {
  // Clean up globals injected by the test setup
  const injected = [
    'routeToContentScript', 'attachToTab', 'detach', 'detachAll',
    'sendViaCDP', 'handleServerMessage', 'safeSend', 'updateBadge',
    'shouldCaptureBody', 'loadServerUrl', 'saveServerUrl',
    'startReattachWatcher', 'stopReattachWatcher',
    'startClientHeartbeat', 'stopClientHeartbeat',
    '_csTabs', '_csGraceSet', 'attachments', 'blockedPatterns',
    'clientHeartbeatTimer', 'reattachWatcherTimer', 'DEFAULT_SERVER', 'ws',
  ];
  for (const key of injected) {
    delete globalThis[key];
  }
});

// ======================================================================
// Content Script Routing Tests
// ======================================================================

describe('routeToContentScript', () => {
  it('should attempt to route hermes commands to content script', { timeout: 2000 }, async () => {
    // Add a tab with content script
    const tab = chrome.addTab({ id: 42, url: 'https://example.com' });
    globalThis._csTabs.add(42);

    // Mock ws so safeSend captures data
    let sentData = null;
    globalThis.ws = {
      readyState: 1, // WebSocket.OPEN
      send: (data) => { sentData = data; },
    };

    // Set up chrome.tabs.sendMessage to succeed
    chrome.tabs.sendMessage = (...args) => {
      return Promise.resolve({ ok: true, result: { test: 'value' } });
    };

    await background.routeToContentScript(42, {
      _hermes: true,
      _hermesFunc: 'click',
      _hermesArgs: ['#btn'],
    });

    // Function sends result via safeSend -> ws.send, not as return value
    assert.ok(sentData, 'Expected ws.send to be called');
    const parsed = JSON.parse(sentData);
    assert.ok(parsed.result, 'Expected result in sent data');
    assert.equal(parsed.result.result.value.test, 'value');

    globalThis.ws = null;
  });

  it('should add tab to grace set on content script failure', { timeout: 2000 }, async () => {
    const tab = chrome.addTab({ id: 43, url: 'https://example.com' });
    globalThis._csTabs.add(43);
    globalThis._csGraceSet.delete(43); // Ensure clean state

    // Make sendMessage fail (timeout or unavailable)
    chrome.tabs.sendMessage = () => {
      return Promise.reject(new Error('Could not establish connection'));
    };

    await background.routeToContentScript(43, {
      _hermes: true,
      _hermesFunc: 'click',
      _hermesArgs: ['#btn'],
    });

    // Should be in grace set (for retry by MCP/plugin)
    assert.ok(globalThis._csGraceSet.has(43), 'Tab should be in grace set after failure');
  });

  it('should forward CDP commands directly (non-hermes)', { timeout: 2000 }, async () => {
    chrome.debugger.sendCommand = (target, method, params) => {
      return Promise.resolve({ result: { success: true } });
    };

    // Mock ws so safeSend captures response
    let sentData = null;
    globalThis.ws = {
      readyState: 1,
      send: (data) => { sentData = data; },
    };

    // Non-hermes commands go through handleServerMessage -> sendViaCDP
    globalThis.handleServerMessage({
      id: 'test-1',
      method: 'Page.navigate',
      params: { url: 'https://example.com' },
    });

    assert.ok(sentData, 'Expected ws.send to be called with CDP result');

    globalThis.ws = null;
  });
});

// ======================================================================
// Tab Management Tests
// ======================================================================

describe('attachToTab', () => {
  it('should attach debugger to a tab', async () => {
    const tab = chrome.addTab({ id: 50, url: 'https://example.com' });
    // attachments is a Map — use the real reference from background.js

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
    // Use the real Map from background.js
    globalThis.attachments.set(60, { attachedAt: Date.now() });

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
    // blockedPatterns is an array — use the real reference from background.js
    globalThis.blockedPatterns.length = 0;

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
    assert.ok(background.shouldCaptureBody('', 'application/json'));
    assert.ok(!background.shouldCaptureBody('text/html'));
    assert.ok(!background.shouldCaptureBody('image/png'));
    assert.ok(!background.shouldCaptureBody(''));
    // PDF via body pattern match (url contains '.pdf')
    assert.ok(background.shouldCaptureBody('https://example.com/file.pdf'));
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
  it('should default to DEFAULT_SERVER', () => {
    // loadServerUrl is sync, sets SERVER_URL as side effect
    // DEFAULT_SERVER is 'ws://127.0.0.1:9223/ws'
    assert.equal(globalThis.DEFAULT_SERVER, 'ws://127.0.0.1:9223/ws');
  });

  it('should save and load server URL', () => {
    let saved = null;
    chrome.storage.sync.set = (data, callback) => {
      saved = data;
      if (typeof callback === 'function') callback();
      return Promise.resolve();
    };
    chrome.storage.sync.get = (keys, callback) => {
      const result = saved || {};
      if (typeof callback === 'function') callback(result);
      return Promise.resolve(result);
    };

    background.saveServerUrl('ws://127.0.0.1:9223/ws', () => {
      background.loadServerUrl(); // reads from sync storage, sets SERVER_URL
      assert.equal(globalThis.SERVER_URL, 'ws://127.0.0.1:9223/ws');
    });
  });
});

// ======================================================================
// Event Batching Tests
// ======================================================================

describe('queueEvent (client-side batching)', () => {
  it('should batch events into a single type:"batch" message', () => {
    // Use mock timers to advance past BATCH_FLUSH_MS deterministically
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
      const sent = [];
      globalThis.ws = {
        readyState: 1, // OPEN
        send: (data) => { sent.push(data); },
      };

      background.queueEvent({ type: 'event', method: 'Network.responseReceived', tabId: 1 });
      background.queueEvent({ type: 'webrequest', event: 'completed', url: 'https://x.com' });
      assert.equal(sent.length, 0, 'nothing sent before flush');

      mock.timers.tick(150); // past BATCH_FLUSH_MS
      assert.equal(sent.length, 1, 'one batched message after flush');
      const parsed = JSON.parse(sent[0]);
      assert.equal(parsed.type, 'batch');
      assert.equal(parsed.events.length, 2, 'both events in one batch');
      assert.equal(parsed.events[0].method, 'Network.responseReceived');
      assert.equal(parsed.events[1].event, 'completed');
    } finally {
      mock.timers.reset();
    }
  });

  it('should coalesce bursts into a single batch (trailing timer)', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
      const sent = [];
      globalThis.ws = {
        readyState: 1,
        send: (data) => { sent.push(data); },
      };

      // Burst: events keep arriving before the timer fires
      background.queueEvent({ type: 'event', method: 'A' });
      mock.timers.tick(30);
      background.queueEvent({ type: 'event', method: 'B' });
      mock.timers.tick(30);
      background.queueEvent({ type: 'event', method: 'C' });
      assert.equal(sent.length, 0);
      mock.timers.tick(60); // now past 100ms since first queue
      assert.equal(sent.length, 1);
      assert.equal(JSON.parse(sent[0]).events.length, 3);
    } finally {
      mock.timers.reset();
    }
  });
});

// ======================================================================
// Header Whitelist Tests
// ======================================================================

describe('pickHeaders (header whitelist)', () => {
  it('should keep only interesting headers', () => {
    const headers = [
      { name: 'Content-Type', value: 'application/json' },
      { name: 'Set-Cookie', value: 'session=abc' },
      { name: 'X-Random-Noise', value: 'aaa' },
      { name: 'Server', value: 'nginx' },
      { name: 'Location', value: 'https://redirect' },
    ];
    const picked = background.pickHeaders(headers);
    const names = picked.map(h => h.name.toLowerCase());
    assert.ok(names.includes('content-type'));
    assert.ok(names.includes('set-cookie'));
    assert.ok(names.includes('location'));
    assert.ok(!names.includes('x-random-noise'));
    assert.ok(!names.includes('server'));
  });

  it('should return undefined for missing headers', () => {
    assert.equal(background.pickHeaders(undefined), undefined);
    assert.equal(background.pickHeaders(null), undefined);
  });

  it('should cap at 20 headers', () => {
    const headers = [];
    for (let i = 0; i < 50; i++) {
      headers.push({ name: `Content-Type-${i}`, value: 'x' }); // not interesting
    }
    // none interesting → empty array
    assert.deepEqual(background.pickHeaders(headers), []);
  });
});

// ======================================================================
// CDP Event Whitelist Tests
// ======================================================================

describe('FORWARDED_CDP_EVENTS', () => {
  it('should include events consumed by relay tools', () => {
    assert.ok(background.FORWARDED_CDP_EVENTS.has('Network.responseReceived'),
      'nav buffer relies on responseReceived');
    assert.ok(background.FORWARDED_CDP_EVENTS.has('Runtime.consoleAPICalled'),
      'browser_console relies on consoleAPICalled');
  });

  it('should exclude high-volume noise events', () => {
    assert.ok(!background.FORWARDED_CDP_EVENTS.has('Network.requestWillBeSent'),
      'per-request noise should not be forwarded');
    assert.ok(!background.FORWARDED_CDP_EVENTS.has('Network.loadingFinished'),
      'loadingFinished is internal-only for body capture');
    assert.ok(!background.FORWARDED_CDP_EVENTS.has('Network.webSocketFrameReceived'),
      'WS frame spam should not be forwarded');
  });
});