/**
 * Mock chrome.* API for Node.js extension tests.
 * Provides complete stubs for all chrome APIs used by background.js.
 */
import { EventEmitter } from 'node:events';

class ChromeMock {
  constructor() {
    this._reset();
  }

  _reset() {
    this.tabs = {
      _tabs: new Map(),
      _nextId: 1,
      sendMessage: this._stub(),
      onActivated: new EventEmitterProxy(),
      onUpdated: new EventEmitterProxy(),
      create: this._stub(),
      remove: this._stub(),
      query: this._stub(),
    };

    this.runtime = {
      lastError: null,
      id: 'test-extension-id',
      onMessage: new EventEmitterProxy(),
      sendMessage: this._stub(),
      getURL: (path) => `chrome-extension://test/${path}`,
    };

    this.debugger = {
      onEvent: new EventEmitterProxy(),
      onDetach: new EventEmitterProxy(),
      attach: this._stub(),
      detach: this._stub(),
      sendCommand: this._stub(),
      getTargets: this._stub(),
    };

    this.webRequest = {
      onBeforeRequest: new EventEmitterProxy(),
      onBeforeSendHeaders: new EventEmitterProxy(),
      onHeadersReceived: new EventEmitterProxy(),
      onCompleted: new EventEmitterProxy(),
      onErrorOccurred: new EventEmitterProxy(),
      handlerBehaviorChanged: this._stub(),
    };

    this.downloads = {
      onCreated: new EventEmitterProxy(),
      onChanged: new EventEmitterProxy(),
    };

    this.alarms = {
      onAlarm: new EventEmitterProxy(),
      create: this._stub(),
      clear: this._stub(),
    };

    this.storage = {
      local: {
        get: this._stub(Promise.resolve({})),
        set: this._stub(Promise.resolve()),
      },
      sync: {
        get: this._stub(Promise.resolve({})),
        set: this._stub(Promise.resolve()),
      },
    };

    this.action = {
      setBadgeText: this._stub(),
      setBadgeBackgroundColor: this._stub(),
    };

    this.declarativeNetRequest = {
      updateDynamicRules: this._stub(Promise.resolve()),
      getDynamicRules: this._stub(Promise.resolve([])),
      // MV3 rule limit
      MAX_NUMBER_OF_DYNAMIC_AND_SESSION_RULES: 5000,
    };
  }

  /** Add a tab to the mock tab list */
  addTab(opts = {}) {
    const id = opts.id || this.tabs._nextId++;
    const tab = {
      id,
      url: opts.url || 'about:blank',
      title: opts.title || '',
      active: opts.active ?? false,
      windowId: opts.windowId || 1,
      status: opts.status || 'complete',
    };
    this.tabs._tabs.set(id, tab);
    return tab;
  }

  /** Get a tab by id */
  getTab(id) {
    return this.tabs._tabs.get(id);
  }

  /** Simulate a message from content script */
  simulateContentScriptMessage(tabId, msg) {
    // The background.js onMessage listener handles messages from CS
    // Find all registered listeners and call them
    const listeners = this.runtime.onMessage._listeners;
    for (const listener of listeners) {
      // CS messages come as {type, ...} through chrome.runtime.onMessage
      listener(msg, { tab: { id: tabId } }, () => {});
    }
  }

  /** Simulate a CDP debugger event */
  simulateDebuggerEvent(source, method, params) {
    this.debugger.onEvent._emit(source, method, params);
  }

  _stub(returnValue) {
    const fn = (...args) => {
      if (typeof returnValue === 'function') return returnValue(...args);
      return returnValue;
    };
    fn.mock = { calls: [] };
    const wrapped = (...args) => {
      fn.mock.calls.push(args);
      if (typeof returnValue === 'function') return returnValue(...args);
      return returnValue;
    };
    // Add mockReset
    wrapped.mockReset = () => { wrapped.mock.calls = []; };
    return wrapped;
  }
}

class EventEmitterProxy {
  constructor() {
    this._listeners = [];
  }

  addListener(fn) {
    this._listeners.push(fn);
  }

  removeListener(fn) {
    const idx = this._listeners.indexOf(fn);
    if (idx >= 0) this._listeners.splice(idx, 1);
  }

  hasListener(fn) {
    return this._listeners.includes(fn);
  }

  /** Emit an event to all registered listeners */
  _emit(...args) {
    for (const fn of this._listeners) {
      fn(...args);
    }
  }
}

// Create and export singleton
const chrome = new ChromeMock();
export default chrome;
export { ChromeMock, EventEmitterProxy };
