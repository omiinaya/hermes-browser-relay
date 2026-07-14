/**
 * Tests for extension/hermes-helper.js using jsdom.
 *
 * Tests the core DOM interaction functions: snapshot, click, type, find,
 * fillByLabel, waitSelector, getText, and state helpers.
 */
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { JSDOM } from 'jsdom';

const __dirname = dirname(fileURLToPath(import.meta.url));
const EXTENSION_DIR = resolve(__dirname, '..', '..', 'extension');

let dom;
let helperCode;

before(() => {
  // Read hermes-helper.js source
  helperCode = readFileSync(resolve(EXTENSION_DIR, 'hermes-helper.js'), 'utf-8');
});

/**
 * Create a fresh DOM with hermes-helper.js injected.
 * Returns { window, document, __hermes }
 */
function createPage(html = '<html><body></body></html>') {
  dom = new JSDOM(html, {
    url: 'https://example.com',
    runScripts: 'dangerously',
  });
  const win = dom.window;
  const doc = win.document;

  // Mock document.execCommand for type/fill operations
  doc.execCommand = function(cmd, _, value) {
    if (cmd === 'insertText') {
      const el = doc.activeElement;
      if (el) {
        el.value = value;
        el.dispatchEvent(new win.Event('input', { bubbles: true }));
        el.dispatchEvent(new win.Event('change', { bubbles: true }));
      }
      return true;
    }
    return false;
  };

  // Set readyState to complete (jsdom defaults to 'loading')
  Object.defineProperty(doc, 'readyState', { value: 'complete', configurable: true });

  // ----- Mock getBoundingClientRect -----
  // jsdom has no layout engine, so all elements report width=0, height=0.
  // hermes-helper.js filters zero-size elements, breaking snapshot/click/find/clickText tests.
  // We patch the prototype to return dimensions based on inline style, with sensible defaults.
  const origGetBoundingClientRect = win.Element.prototype.getBoundingClientRect;
  win.Element.prototype.getBoundingClientRect = function() {
    // Respect display:none, hidden attribute, and hidden input types
    if (this.style.display === 'none' || this.hidden || this.type === 'hidden') {
      return { x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0 };
    }
    const rect = origGetBoundingClientRect.call(this);
    const style = this.style;
    const w = parseInt(style.width, 10) || rect.width || 100;
    const h = parseInt(style.height, 10) || rect.height || 20;
    return {
      x: rect.x || 0, y: rect.y || 0,
      width: w, height: h,
      top: rect.top || 0, right: w, bottom: h, left: rect.left || 0,
    };
  };

  // ----- Mock innerText -----
  // jsdom doesn't support innerText (only textContent), but hermes-helper.js uses
  // body.innerText in docInfo(). Provide a fallback getter.
  if (doc.body && typeof doc.body.innerText === 'undefined') {
    Object.defineProperty(doc.body, 'innerText', {
      get() { return this.textContent || ''; },
      configurable: true,
    });
  }

  // Run hermes-helper.js
  win.eval(helperCode);

  // __hermes should be on the window after eval
  const hermes = win.__hermes;
  return { win, doc, hermes };
}

after(() => {
  dom = null;
});

// ======================================================================
// getStateSnapshot
// ======================================================================

describe('getStateSnapshot', () => {
  it('should return page state for empty page', () => {
    const { hermes } = createPage();
    const state = hermes.getStateSnapshot();
    assert.ok(state, 'Should return state');
    assert.equal(typeof state.total, 'number');
    assert.equal(typeof state.visible, 'number');
    assert.equal(state.readyState, 'complete');
    assert.ok(state.title !== undefined);
  });

  it('should count interactive elements', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button>Click me</button>
        <a href="/test">Link</a>
        <input type="text" placeholder="Name" />
        <span>Not interactive</span>
        <div>Also not</div>
      </body></html>
    `);
    const state = hermes.getStateSnapshot();
    assert.equal(state.total, 3, 'Should find 3 interactive elements');
    // button, a, input are interactive — span and div are not
  });

  it('should handle pages with no interactive elements', () => {
    const { hermes } = createPage('<html><body><p>Just text</p></body></html>');
    const state = hermes.getStateSnapshot();
    assert.equal(state.total, 0);
    assert.equal(state.visible, 0);
  });
});

// ======================================================================
// snapshot (full DOM walk)
// ======================================================================

describe('snapshot', () => {
  it('should return page info with metadata', () => {
    const { hermes } = createPage('<html><head><title>Test</title></head><body></body></html>');
    const snap = hermes.snapshot(false);
    assert.ok(snap.info, 'Should have info');
    assert.equal(snap.info.title, 'Test');
    assert.equal(snap.info.url, 'https://example.com/');
    assert.ok(snap.info.viewport, 'Should have viewport');
    assert.ok(Array.isArray(snap.items), 'Should have items array');
  });

  it('should list interactive elements with coordinates', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button id="btn1" style="width:100px;height:40px">Submit</button>
        <a href="/page" style="width:200px;height:20px">Click here</a>
      </body></html>
    `);
    const snap = hermes.snapshot(false);
    assert.equal(snap.items.length, 2, 'Should find 2 interactive elements');
    
    const btn = snap.items.find(i => i.tag.startsWith('button'));
    assert.ok(btn, 'Should find button');
    assert.ok(btn.ref.startsWith('e'), 'Should have ref ID');
    assert.equal(btn.text, 'Submit');
    assert.ok(btn.x > 0, 'Should have x coordinate');
    assert.ok(btn.y > 0, 'Should have y coordinate');

    const link = snap.items.find(i => i.tag === 'a');
    assert.ok(link, 'Should find link');
    assert.equal(link.href, '/page');
  });

  it('should skip hidden elements', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button style="display:none">Hidden</button>
        <button>Visible</button>
      </body></html>
    `);
    const snap = hermes.snapshot(false);
    // The hidden element has 0 width/height, so it should be skipped
    assert.equal(snap.items.length, 1, 'Should only find visible button');
    assert.equal(snap.items[0].text, 'Visible');
  });
});

// ======================================================================
// click
// ======================================================================

describe('click', () => {
  it('should dispatch click events on matching element', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button id="test-btn">Click Test</button>
      </body></html>
    `);

    let clicked = false;
    doc.getElementById('test-btn').addEventListener('click', () => { clicked = true; });

    const result = hermes.click('#test-btn');
    assert.ok(result.ok);
    assert.ok(clicked, 'Click event should have been dispatched');
  });

  it('should return error for non-existent selector', () => {
    const { hermes } = createPage();
    const result = hermes.click('#nonexistent');
    assert.ok(result.error);
    assert.ok(result.error.includes('not found'));
  });

  it('should handle complex CSS selectors', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <div class="container">
          <form>
            <input type="submit" value="Go" />
          </form>
        </div>
      </body></html>
    `);

    let submitted = false;
    doc.querySelector('input').addEventListener('click', () => { submitted = true; });

    const result = hermes.click('input[type="submit"]');
    assert.ok(result.ok);
    assert.ok(submitted);
  });
});

// ======================================================================
// type
// ======================================================================

describe('type', () => {
  it('should insert text into input field', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <input id="name-input" type="text" />
      </body></html>
    `);

    hermes.type('#name-input', 'Hello World');
    assert.equal(doc.getElementById('name-input').value, 'Hello World');
  });

  it('should fire input and change events', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <input id="event-test" type="text" />
      </body></html>
    `);

    let inputFired = false;
    let changeFired = false;
    const el = doc.getElementById('event-test');
    el.addEventListener('input', () => { inputFired = true; });
    el.addEventListener('change', () => { changeFired = true; });

    hermes.type('#event-test', 'test');
    assert.ok(inputFired, 'Input event should fire');
    assert.ok(changeFired, 'Change event should fire');
  });

  it('should clear existing value before typing', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <input id="clear-test" type="text" value="old value" />
      </body></html>
    `);

    hermes.type('#clear-test', 'new value');
    assert.equal(doc.getElementById('clear-test').value, 'new value');
  });
});

// ======================================================================
// find (search page elements)
// ======================================================================

describe('find', () => {
  it('should find elements by text content', () => {
    const { hermes } = createPage(`
      <html><body>
        <button>Save Changes</button>
        <button>Cancel</button>
        <a href="/">Home</a>
      </body></html>
    `);
    const result = hermes.find('Save');
    assert.equal(result.length, 1);
    assert.equal(result[0].text, 'Save Changes');
  });

  it('should handle multiple matches', () => {
    const { hermes } = createPage(`
      <html><body>
        <button>Submit</button>
        <input type="submit" value="Submit" />
      </body></html>
    `);
    const result = hermes.find('Submit');
    assert.ok(result.length >= 1, 'Should find at least 1 matching element');
  });

  it('should return empty for no matches', () => {
    const { hermes } = createPage();
    const result = hermes.find('zzz_nonexistent');
    assert.equal(result.length, 0);
  });
});

// ======================================================================
// fillByLabel
// ======================================================================

describe('fillByLabel', () => {
  it('should fill input by label text', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <label for="email">Email Address</label>
        <input id="email" type="email" />
      </body></html>
    `);
    const result = hermes.fillByLabel('Email', 'test@example.com');
    assert.ok(result.ok);
    assert.equal(doc.getElementById('email').value, 'test@example.com');
  });

  it('should fill input by placeholder', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <input placeholder="Your name" />
      </body></html>
    `);
    const result = hermes.fillByLabel('Your name', 'Alice');
    assert.ok(result.ok);
    assert.equal(doc.querySelector('input').value, 'Alice');
  });
});

// ======================================================================
// scrollBy
// ======================================================================

describe('scrollBy', () => {
  it('should scroll page in given direction', () => {
    const { hermes, win } = createPage('<html><body style="height:5000px"></body></html>');
    const result = hermes.scrollBy('down', 500);
    assert.equal(result.direction, 'down');
    assert.equal(result.amount, 500);
    assert.ok(result.scrollY >= 0);
  });

  it('should default to 60% of viewport height', () => {
    const { hermes, win } = createPage('<html><body style="height:5000px"></body></html>');
    const result = hermes.scrollBy('down', 0);
    // Default amount = window.innerHeight * 0.6
    assert.ok(result.amount > 0);
  });
});

// ======================================================================
// key
// ======================================================================

describe('key', () => {
  it('should dispatch keyboard events on active element', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <input id="key-test" type="text" />
      </body></html>
    `);

    const input = doc.getElementById('key-test');
    input.focus();

    let keydown = false;
    input.addEventListener('keydown', () => { keydown = true; });

    const result = hermes.key('Enter');
    assert.ok(result.ok);
    assert.ok(keydown, 'Keydown should fire');
  });
});

// ======================================================================
// selectOption
// ======================================================================

describe('selectOption', () => {
  it('should select option by visible text', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <select id="country">
          <option value="us">United States</option>
          <option value="ca">Canada</option>
          <option value="mx">Mexico</option>
        </select>
      </body></html>
    `);

    const result = hermes.selectOption('#country', 'Canada');
    assert.ok(result.ok);
    assert.equal(result.selected, 'Canada');
    assert.equal(result.value, 'ca');
    assert.equal(doc.getElementById('country').value, 'ca');
  });

  it('should return error for non-existent option', () => {
    const { hermes } = createPage(`
      <html><body>
        <select id="test">
          <option value="a">Option A</option>
        </select>
      </body></html>
    `);
    const result = hermes.selectOption('#test', 'Option Z');
    assert.ok(result.error);
  });
});

// ======================================================================
// docInfo + getTitle
// ======================================================================

describe('docInfo', () => {
  it('should return page title and url', () => {
    const { hermes } = createPage('<html><head><title>My Page</title></head><body>Hello</body></html>');
    const info = hermes.docInfo();
    assert.equal(info.title, 'My Page');
    assert.equal(info.url, 'https://example.com/');
    assert.ok(info.bodySize > 0);
    assert.ok(info.linkCount >= 0);
  });
});

// ======================================================================
// isLoaded + isReady
// ======================================================================

describe('isLoaded / needsTouch', () => {
  it('should report loaded state', () => {
    const { hermes } = createPage();
    assert.ok(hermes.isLoaded());
  });
});

// ======================================================================
// clickText
// ======================================================================

describe('clickText', () => {
  it('should click element by visible text', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button>Submit Form</button>
      </body></html>
    `);

    let clicked = false;
    doc.querySelector('button').addEventListener('click', () => { clicked = true; });

    const result = hermes.clickText('Submit Form');
    assert.ok(result.ok);
    assert.ok(clicked, 'Button should have been clicked');
  });

  it('should filter by tag', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <button>Click Me</button>
        <a href="#">Click Me</a>
      </body></html>
    `);

    // Only match buttons
    const result = hermes.clickText('Click Me', 'button');
    assert.ok(result.ok);
    assert.equal(result.tag, 'button', 'Should have clicked the button, not the link');
  });
});

// ======================================================================
// formFill
// ======================================================================

describe('formFill', () => {
  it('should fill multiple fields at once', () => {
    const { hermes, doc } = createPage(`
      <html><body>
        <label for="fname">First Name</label>
        <input id="fname" type="text" />
        <label for="lname">Last Name</label>
        <input id="lname" type="text" />
      </body></html>
    `);

    const result = hermes.formFill({
      'First Name': 'John',
      'Last Name': 'Doe',
    });

    assert.equal(result.completed, 2);
    assert.equal(result.errors.length, 0);
    assert.equal(doc.getElementById('fname').value, 'John');
    assert.equal(doc.getElementById('lname').value, 'Doe');
  });
});

// ======================================================================
// history back/forward
// ======================================================================

describe('history navigation', () => {
  it('should support historyBack and historyForward', () => {
    const { hermes } = createPage();
    // These shouldn't throw
    hermes.historyBack();
    hermes.historyForward();
    assert.ok(true, 'history functions called without error');
  });
});
