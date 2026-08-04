/**
 * Hermes Content Script — bridges window.__hermes to extension service worker
 *
 * TWO FUNCTIONS:
 * 1. DOM Command Bridge: receives {type:"hermes", func, args} from service worker,
 *    executes directly against window.__hermes (no CDP Runtime.evaluate).
 * 2. Push State Detection: MutationObserver detects page changes, sends
 *    page_updated / page_loaded events to service worker — eliminates polling.
 *
 * Injected via manifest.json content_scripts at "document_start" in MAIN world,
 * so it shares the page's JS context (same window.__hermes).
 */
(function() {
  'use strict';

  // Bail out silently if the extension context is gone — happens when the
  // extension is reloaded/updated while an old content script is still
  // injected in an existing tab. chrome.runtime is undefined there.
  if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.id) {
    return;
  }

  let hermesReady = false;

  // ──────────────────────────────────────────────────────────────────────────
  // Wait for __hermes (injected via Page.addScriptToEvaluateOnNewDocument)
  // ──────────────────────────────────────────────────────────────────────────
  function waitForHermes(callback) {
    if (window.__hermes && window.__hermes.isLoaded && window.__hermes.isLoaded()) {
      hermesReady = true;
      callback();
      return;
    }
    // Poll briefly — __hermes is injected at document creation via CDP
    const poll = setInterval(() => {
      if (window.__hermes && window.__hermes.isLoaded && window.__hermes.isLoaded()) {
        hermesReady = true;
        clearInterval(poll);
        clearTimeout(fallback);
        callback();
      }
    }, 10);
    // Safety timeout — fall back to non-hermes state
    const fallback = setTimeout(() => {
      clearInterval(poll);
      hermesReady = false;
      callback();
    }, 5000);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Message handler — receive DOM commands from service worker
  // ──────────────────────────────────────────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === 'hermes') {
      if (!hermesReady || !window.__hermes) {
        sendResponse({ ok: false, error: '__hermes not ready yet', ready: hermesReady });
        return true;
      }
      try {
        const fn = window.__hermes[msg.func];
        if (msg.func === '__eval') {
          // Backward compat: raw JS expression containing __hermes calls
          const result = eval(msg.args[0]);
          sendResponse({ ok: true, result: result });
          return true;
        }
        if (typeof fn !== 'function') {
          sendResponse({ ok: false, error: `__hermes.${msg.func} is not a function` });
          return true;
        }
        const result = fn.apply(window.__hermes, msg.args || []);
        sendResponse({ ok: true, result: result });
      } catch (e) {
        sendResponse({ ok: false, error: e.message, stack: e.stack ? e.stack.substring(0, 500) : '' });
      }
      return true; // keep channel open for async sendResponse
    }
    if (msg.type === 'hermes_ping') {
      sendResponse({ ok: hermesReady && !!window.__hermes, ready: hermesReady });
      return true;
    }
    return false;
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Push State Detection
  // ──────────────────────────────────────────────────────────────────────────
  let lastStateKey = '';
  let pushTimer = null;

  function getStateSnapshot() {
    const h = window.__hermes;
    // Use the lightweight state snapshot (~10x cheaper than snapshot(false)):
    // only counts interactive elements + checks viewport visibility. The full
    // DOM walk is reserved for actual tool calls, not push-event dedup.
    if (!h || !h.getStateSnapshot) return null;
    try {
      return h.getStateSnapshot();
    } catch (e) {
      return null;
    }
  }

  function sendPushEvent(type, extra) {
    const state = getStateSnapshot();
    if (!state) return;
    const key = `${state.total}:${state.visible}:${state.readyState}`;
    if (key === lastStateKey) return;
    lastStateKey = key;
    try {
      chrome.runtime.sendMessage(Object.assign({ type: type }, extra, { state: state }));
    } catch (e) {
      // SW might be inactive — silently drop
    }
  }

  // MutationObserver — trailing debounce (waits 250ms for mutations to settle
  // before firing). This is NOT a leading-edge throttle: the old pattern fired
  // immediately on the first mutation of a burst (mid-render DOM walk) then
  // blocked 500ms. Debounce delivers the final DOM state, not the first.
  const observer = new MutationObserver(() => {
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(() => {
      pushTimer = null;
      sendPushEvent('page_updated');
    }, 250);
  });

  function startObserver() {
    try {
      observer.observe(document, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: false,
      });
    } catch (e) {
      // Cross-origin frame access can throw — safe to ignore
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Init
  // ──────────────────────────────────────────────────────────────────────────
  waitForHermes(() => {
    // Send page_loaded to service worker
    sendPushEvent('page_loaded', {
      url: location.href,
      title: document.title,
    });
    // Start mutation observer for push updates
    startObserver();
  });
})();
