/**
 * Hermes Browser Helper — injected via Page.addScriptToEvaluateOnNewDocument
 *
 * Provides window.__hermes with optimized utility functions for CDP-based
 * browser automation. Injected once per document at creation time so every
 * tool call invokes a pre-parsed function instead of sending inline JS.
 *
 * All functions return serializable objects — no DOM nodes, no closures
 * with circular references. Designed for Runtime.evaluate round-trips.
 */
(function() {
  // Guard — only inject once per document
  if (window.__hermes) return;

  const H = {};

  // ──────────────────────────────────────────────────────────────────────────
  // Snapshot — accessibility-style interactive element map
  // ──────────────────────────────────────────────────────────────────────────
  const SELECTORS = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]',
    '[role=tab]', '[role=menuitem]', '[role=option]',
    "[tabindex]:not([tabindex='-1'])",
    'summary', 'details', '[contenteditable=true]',
  ];
  const SELECTOR_STR = SELECTORS.join(',');

  // Shadow DOM recursive query
  function shadowQueryAll(root, sel) {
    let r = [];
    try { r = Array.from(root.querySelectorAll(sel)); } catch(e) { /* shadow root may not support querySelectorAll */ }
    if (root.shadowRoot) r = r.concat(shadowQueryAll(root.shadowRoot, sel));
    for (const c of root.children || []) r = r.concat(shadowQueryAll(c, sel));
    return r;
  }

  H.snapshot = function(full) {
    const all = shadowQueryAll(document, SELECTOR_STR);
    const seen = new Set();
    const items = [];
    let idx = 0;
    for (const el of all) {
      if (seen.has(el)) continue;
      seen.add(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      idx++;
      const tag = el.tagName.toLowerCase();
      const type = el.type || '';
      const txt = (el.textContent||'').trim().substring(0, 120);
      const placeholder = el.placeholder || '';
      const aria = el.getAttribute('aria-label') || '';
      const name = el.getAttribute('name') || '';
      const href = el.getAttribute('href') || '';
      const role = el.getAttribute('role') || '';
      items.push({
        ref: 'e' + idx,
        tag: tag + (type ? '[type=' + type + ']' : ''),
        text: txt.substring(0, 100),
        placeholder: placeholder,
        aria_label: aria,
        name: name,
        href: href.substring(0, 120),
        role: role,
        x: Math.round(r.x + r.width/2),
        y: Math.round(r.y + r.height/2),
        w: Math.round(r.width),
        h: Math.round(r.height),
        visible: r.top < window.innerHeight && r.bottom > 0,
      });
    }
    const info = {
      title: document.title,
      url: location.href,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      total: items.length,
      visible: items.filter(i => i.visible).length,
    };
    let textContent = '';
    if (full) {
      textContent = (document.body ? document.body.innerText : '').substring(0, 30000);
    }
    return { info, items, textContent: textContent };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Get rendered text
  // ──────────────────────────────────────────────────────────────────────────
  H.getText = function() {
    return document.body ? document.body.innerText : '';
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Find element position by CSS selector — returns {x, y, tag, text, visible}
  // ──────────────────────────────────────────────────────────────────────────
  H.findPosition = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return { error: 'Element not found: ' + selector };
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return { error: 'Element has zero size' };
    return {
      x: Math.round(r.x + r.width/2),
      y: Math.round(r.y + r.height/2),
      tag: el.tagName.toLowerCase(),
      text: (el.textContent||'').trim().substring(0, 80),
      visible: r.top < window.innerHeight && r.bottom > 0,
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Click — find element + dispatch mouse events in ONE call
  // ──────────────────────────────────────────────────────────────────────────
  H.click = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return { error: 'Element not found: ' + selector };
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return { error: 'Element has zero size' };
    const x = Math.round(r.x + r.width/2);
    const y = Math.round(r.y + r.height/2);

    // Dispatch CDP-compatible mouse events
    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x, clientY: y }));

    return {
      ok: true,
      x: x,
      y: y,
      tag: el.tagName.toLowerCase(),
      text: (el.textContent||'').trim().substring(0, 80),
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Hover — dispatch mouseMoved event
  // ──────────────────────────────────────────────────────────────────────────
  H.hover = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return { error: 'Element not found: ' + selector };
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return { error: 'Element has zero size' };
    const x = Math.round(r.x + r.width/2);
    const y = Math.round(r.y + r.height/2);

    el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    el.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: x, clientY: y }));

    return {
      ok: true,
      x: x,
      y: y,
      tag: el.tagName.toLowerCase(),
      text: (el.textContent||'').trim().substring(0, 80),
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Type — fill input using execCommand('insertText') for React compat
  // ──────────────────────────────────────────────────────────────────────────
  H.type = function(selector, text) {
    const el = document.querySelector(selector);
    if (!el) return { error: 'Element not found: ' + selector };
    el.focus();
    el.value = '';
    el.select();
    document.execCommand('insertText', false, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, tag: el.tagName.toLowerCase(), value_len: el.value.length };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Fill by label — find input by label/placeholder/aria-label/name, then type
  // ──────────────────────────────────────────────────────────────────────────
  H.fillByLabel = function(label, text) {
    const el = H._findFieldByLabel(label);
    if (!el) return { error: 'No field found for label: ' + label };
    if (el.error) return el;
    el.focus();
    el.value = '';
    el.select();
    document.execCommand('insertText', false, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, tag: el.tagName.toLowerCase(), name: el.name, value_len: el.value.length };
  };

  // Internal: find form field by label, placeholder, aria-label, or name
  H._findFieldByLabel = function(label) {
    const lc = label.toLowerCase();

    // 1. aria-label
    let el = document.querySelector('[aria-label="' + label.replace(/"/g,'&quot;') + '"], [aria-label="' + lc.replace(/"/g,'&quot;') + '"]');
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return el;

    // 2. placeholder
    el = document.querySelector('[placeholder="' + label.replace(/"/g,'&quot;') + '"], [placeholder="' + lc.replace(/"/g,'&quot;') + '"]');
    if (el) return el;

    // 3. name attribute
    el = document.querySelector('[name="' + label.replace(/"/g,'&quot;') + '"], [name="' + lc.replace(/"/g,'&quot;') + '"]');
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return el;

    // 4. id → label[for]
    el = document.querySelector('label[for="' + label.replace(/"/g,'&quot;') + '"], label[for="' + lc.replace(/"/g,'&quot;') + '"]');
    if (el) {
      const input = document.getElementById(el.getAttribute('for'));
      if (input) return input;
    }

    // 5. label text contains → find associated input
    const labels = document.querySelectorAll('label');
    for (const lb of labels) {
      if ((lb.textContent||'').toLowerCase().includes(lc)) {
        const forId = lb.getAttribute('for');
        if (forId) {
          const input = document.getElementById(forId);
          if (input) return input;
        }
        // Label wrapping an input
        const input = lb.querySelector('input, textarea, select');
        if (input) return input;
      }
    }

    // 6. Partial placeholder match (last resort)
    const inputs = document.querySelectorAll('input, textarea');
    for (const inp of inputs) {
      const ph = (inp.placeholder || '').toLowerCase();
      const nm = (inp.name || '').toLowerCase();
      const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
      if (ph.includes(lc) || nm.includes(lc) || aria.includes(lc)) return inp;
    }

    return null;
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Find elements by text content — returns {ref, tag, text, x, y, w, h, visible}[]
  // ──────────────────────────────────────────────────────────────────────────
  H.find = function(query) {
    const q = query.toLowerCase();
    const all = shadowQueryAll(document, 'a, button, input, textarea, select, [role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], label, span, div, h1, h2, h3, h4, h5, h6, p, li, td, th');
    const seen = new Set();
    const results = [];
    let idx = 0;
    for (const el of all) {
      if (seen.has(el)) continue;
      seen.add(el);
      const txt = (el.textContent||'').trim();
      const aria = el.getAttribute('aria-label') || '';
      const placeholder = el.placeholder || '';
      const name = el.getAttribute('name') || '';
      const val = el.value || '';

      // Check all text sources
      const textSources = [txt, aria, placeholder, name, val];
      let matched = false;
      for (const s of textSources) {
        if (s.toLowerCase().includes(q)) { matched = true; break; }
      }
      if (!matched) continue;

      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      idx++;
      results.push({
        ref: 'm' + idx,
        tag: el.tagName.toLowerCase(),
        text: txt.substring(0, 120),
        aria_label: aria,
        placeholder: placeholder,
        name: name,
        value: val.substring(0, 80),
        x: Math.round(r.x + r.width/2),
        y: Math.round(r.y + r.height/2),
        w: Math.round(r.width),
        h: Math.round(r.height),
        visible: r.top < window.innerHeight && r.bottom > 0,
      });
    }
    return results;
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Wait for selector — poll until element exists (or timeout)
  // Uses rAF-based async polling to avoid locking the browser main thread.
  // Returns a Promise when called with async dispatch; OK for sync calls too.
  // ──────────────────────────────────────────────────────────────────────────
  H.waitSelector = function(selector, timeout, state) {
    timeout = timeout || 10;
    state = state || 'visible';
    const deadline = Date.now() + timeout * 1000;
    const poll = function(resolve) {
      const el = document.querySelector(selector);
      if (el) {
        const r = el.getBoundingClientRect();
        if (state === 'attached') {
          resolve({
            ok: true,
            tag: el.tagName.toLowerCase(),
            x: Math.round(r.x + r.width/2),
            y: Math.round(r.y + r.height/2),
          });
          return;
        }
        const visible = r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
        if (visible) {
          resolve({
            ok: true,
            tag: el.tagName.toLowerCase(),
            x: Math.round(r.x + r.width/2),
            y: Math.round(r.y + r.height/2),
          });
          return;
        }
      }
      if (Date.now() >= deadline) {
        resolve({ error: 'Timeout waiting for: ' + selector, timeout_seconds: timeout });
        return;
      }
      requestAnimationFrame(function() { poll(resolve); });
    };
    return new Promise(function(resolve) { poll(resolve); });
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Get page ready state — "loading" | "interactive" | "complete"
  // ──────────────────────────────────────────────────────────────────────────
  H.readyState = function() {
    return {
      readyState: document.readyState,
      title: document.title,
      url: location.href,
      interactiveElements: document.querySelectorAll(SELECTOR_STR).length,
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Network quiet check — true if no active XHR/fetch in last 500ms
  // ──────────────────────────────────────────────────────────────────────────
  H.networkQuiet = function() {
    // Note: can't track fetch/XHR from injected script directly.
    // This is best-effort: check if performance has recent activity
    if (!performance || !performance.getEntriesByType) return { quiet: true, note: 'no performance API' };
    const entries = performance.getEntriesByType('resource');
    const now = performance.now();
    const recent = entries.filter(e => (now - e.startTime) < 2000);
    const activeCount = recent.filter(e => !e.responseEnd).length;
    return { quiet: activeCount === 0, recentResources: recent.length, activeRequests: activeCount };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Press key via dispatched events
  // ──────────────────────────────────────────────────────────────────────────
  H.key = function(key) {
    const keyMap = {
      'Enter': { key: 'Enter', code: 'Enter', keyCode: 13, which: 13 },
      'Escape': { key: 'Escape', code: 'Escape', keyCode: 27, which: 27 },
      'Tab': { key: 'Tab', code: 'Tab', keyCode: 9, which: 9 },
      'ArrowDown': { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, which: 40 },
      'ArrowUp': { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38, which: 38 },
      'ArrowLeft': { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37, which: 37 },
      'ArrowRight': { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39, which: 39 },
      'Backspace': { key: 'Backspace', code: 'Backspace', keyCode: 8, which: 8 },
      'Delete': { key: 'Delete', code: 'Delete', keyCode: 46, which: 46 },
      'Home': { key: 'Home', code: 'Home', keyCode: 36, which: 36 },
      'End': { key: 'End', code: 'End', keyCode: 35, which: 35 },
      'PageUp': { key: 'PageUp', code: 'PageUp', keyCode: 33, which: 33 },
      'PageDown': { key: 'PageDown', code: 'PageDown', keyCode: 34, which: 34 },
    };
    const k = keyMap[key] || { key: key, code: key, keyCode: key.charCodeAt(0) || 0, which: key.charCodeAt(0) || 0 };
    const active = document.activeElement;
    if (active) {
      active.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: k.key, code: k.code, keyCode: k.keyCode, which: k.which }));
      active.dispatchEvent(new KeyboardEvent('keypress', { bubbles: true, cancelable: true, key: k.key, code: k.code, keyCode: k.keyCode, which: k.which }));
      active.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: k.key, code: k.code, keyCode: k.keyCode, which: k.which }));
    }
    return { ok: true, key: key, activeElement: active ? active.tagName : null };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // History back / forward — navigate browser history
  // ──────────────────────────────────────────────────────────────────────────
  H.historyBack = function() {
    window.history.back();
    return { ok: true };
  };

  H.historyForward = function() {
    window.history.forward();
    return { ok: true };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Scroll
  // ──────────────────────────────────────────────────────────────────────────
  H.scrollBy = function(direction, amount) {
    amount = amount || Math.round(window.innerHeight / 2);
    const x = direction === 'right' ? amount : (direction === 'left' ? -amount : 0);
    const y = direction === 'down' ? amount : (direction === 'up' ? -amount : 0);
    window.scrollBy({ left: x, top: y, behavior: 'instant' });
    return {
      ok: true,
      direction: direction || (y ? 'down' : 'right'),
      amount: amount,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      maxScrollX: document.documentElement.scrollWidth - window.innerWidth,
      maxScrollY: document.documentElement.scrollHeight - window.innerHeight,
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Get document info (title, url, metadata)
  // ──────────────────────────────────────────────────────────────────────────
  H.docInfo = function() {
    const meta = {};
    document.querySelectorAll('meta').forEach(m => {
      const name = m.getAttribute('name') || m.getAttribute('property') || '';
      const content = m.getAttribute('content') || '';
      if (name && content) meta[name] = content.substring(0, 200);
    });
    return {
      title: document.title,
      url: location.href,
      charset: document.characterSet,
      lang: document.documentElement.lang,
      meta: meta,
      bodySize: document.body ? document.body.innerText.length : 0,
      linkCount: document.querySelectorAll('a[href]').length,
      imgCount: document.querySelectorAll('img').length,
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // GET form field values by selector
  // ──────────────────────────────────────────────────────────────────────────
  H.getFieldValue = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return { error: 'Element not found: ' + selector };
    return {
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      name: el.name || '',
      value: el.value || '',
      checked: el.checked !== undefined ? el.checked : undefined,
      selectedIndex: el.selectedIndex !== undefined ? el.selectedIndex : undefined,
    };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Check if helper is loaded — used by tools to detect injection
  // ──────────────────────────────────────────────────────────────────────────
  H.isLoaded = function() {
    return true;
  };

  H.version = '2.4.2';

  // ──────────────────────────────────────────────────────────────────────────
  // Click by text — find first element containing text, then click it
  // ──────────────────────────────────────────────────────────────────────────
  H.clickText = function(text, tag) {
    tag = tag || '';
    const q = text.toLowerCase();
    const all = shadowQueryAll(document, 'a, button, span, label, div[role=button], [role=link], [role=tab], [role=menuitem], input[type=submit], input[type=button], select, [onclick], [tabindex]');
    for (const el of all) {
      if (tag && el.tagName.toLowerCase() !== tag) continue;
      const txt = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
      if (txt.includes(q)) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const x = Math.round(r.x + r.width/2), y = Math.round(r.y + r.height/2);
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
        return { ok: true, x: x, y: y, tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0, 80) };
      }
    }
    return { error: 'No element found with text: ' + text };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Select option in <select> by visible text
  // ──────────────────────────────────────────────────────────────────────────
  H.selectOption = function(selector, text) {
    const select = document.querySelector(selector);
    if (!select) return { error: 'Select element not found: ' + selector };
    if (select.tagName !== 'SELECT') return { error: 'Element is not a <select>' };
    const options = Array.from(select.options);
    const q = text.trim().toLowerCase();
    const target = options.find(o => (o.text||'').trim().toLowerCase() === q);
    if (!target) return { error: 'Option not found with text: ' + text };
    select.value = target.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    select.dispatchEvent(new Event('input', { bubbles: true }));
    return { ok: true, selected: target.text, value: target.value, index: target.index };
  };

  // ──────────────────────────────────────────────────────────────────────────
  // formFill — fill multiple form fields from a {identifier: value} object
  // Identifiers can be CSS selectors or label/placeholder/aria-label/name text
  // ──────────────────────────────────────────────────────────────────────────
  H.formFill = function(fields) {
    var errors = [];
    var completed = 0;
    var keys = Object.keys(fields);
    for (var i = 0; i < keys.length; i++) {
      var identifier = keys[i];
      var value = fields[identifier];
      var valueStr = String(value);
      // Determine if identifier looks like a CSS selector
      var isSelector = identifier.charCodeAt(0) === 35 ||  // #
                        identifier.charCodeAt(0) === 46 ||  // .
                        identifier.charCodeAt(0) === 91 ||  // [
                        identifier.charCodeAt(0) === 58 ||  // :
                        identifier.indexOf(' ') > 0 ||
                        identifier.indexOf('input[') === 0;
      var el = null;
      if (isSelector) {
        el = document.querySelector(identifier);
      }
      if (!el) {
        el = H._findFieldByLabel(identifier);
      }
      if (!el) {
        errors.push({field: identifier, error: 'Field not found'});
        continue;
      }
      // Handle <select> elements
      if (el.tagName === 'SELECT') {
        var opts = Array.from(el.options);
        var found = false;
        for (var j = 0; j < opts.length; j++) {
          if (opts[j].value === valueStr || opts[j].text.indexOf(valueStr) !== -1) {
            el.value = opts[j].value;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            completed++;
            found = true;
            break;
          }
        }
        if (!found) errors.push({field: identifier, error: 'Option not found'});
        continue;
      }
      // Regular input / textarea
      el.focus();
      el.value = '';
      el.select();
      document.execCommand('insertText', false, valueStr);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      completed++;
    }
    return {completed: completed, errors: errors};
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Lightweight state snapshot — for push event dedup (avoids full DOM walk)
  // Only counts interactive elements and checks viewport visibility.
  // ~10x cheaper than snapshot(false) for push event use case.
  // ──────────────────────────────────────────────────────────────────────────
  H.getStateSnapshot = function() {
    var els = document.querySelectorAll('button, a, input, textarea, select, [tabindex]');
    var total = 0, visible = 0;
    var wh = window.innerHeight;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      total++;
      try {
        var r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.top < wh && r.bottom > 0) visible++;
      } catch(e) { /* detached element, skip */ }
    }
    return {
      total: total,
      visible: visible,
      title: document.title,
      readyState: document.readyState,
      url: location.href,
    };
  };

  // Export
  window.__hermes = H;
})();
