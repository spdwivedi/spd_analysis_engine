/**
 * tests/test_windowed_list.js
 * Unit test for WindowedList component verifying 1,000-item windowing.
 */

import assert from 'node:assert';
import { WindowedList } from '../web/js/components/windowed_list.js';

// Minimal mock DOM for headless testing
class MockElement {
  constructor(tag = 'div') {
    this.tag = tag;
    this.style = {};
    this.children = [];
    this.scrollTop = 0;
    this.clientHeight = 400;
    this.listeners = {};
  }

  set innerHTML(val) {
    if (val === '') this.children = [];
  }

  appendChild(child) {
    if (child._isFragment) {
      this.children.push(...child.children);
    } else {
      this.children.push(child);
    }
  }

  addEventListener(event, fn) {
    this.listeners[event] = fn;
  }

  removeEventListener(event, fn) {
    delete this.listeners[event];
  }
}

globalThis.document = {
  createElement(tag) {
    return new MockElement(tag);
  },
  createDocumentFragment() {
    const frag = new MockElement('fragment');
    frag._isFragment = true;
    return frag;
  },
};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

async function runTests() {
  console.log('Testing WindowedList with 1,000 files...');
  const container = new MockElement('div');
  const list = new WindowedList(container, {
    itemHeight: 40,
    buffer: 5,
    renderItem: (item, idx) => {
      const el = new MockElement('div');
      el.textContent = `Item ${idx}: ${item.name}`;
      return el;
    },
  });

  // Create 1,000 mock file items
  const items = Array.from({ length: 1000 }, (_, i) => ({ name: `file_${i}.py` }));
  list.setItems(items);

  // Check structure: container must have topSpacer, contentBox, bottomSpacer
  assert.strictEqual(container.children.length, 3, 'Container should hold topSpacer, contentBox, bottomSpacer');
  const [topSpacer, contentBox, bottomSpacer] = container.children;

  // Viewport at top (scrollTop = 0): visible = 400 / 40 = 10 items.
  // With buffer = 5, startIndex = 0, endIndex = 0 + 10 + 5 = 15.
  assert.strictEqual(contentBox.children.length, 15, 'Only visible window of items should be in DOM');
  assert.strictEqual(topSpacer.style.height, '0px', 'Top spacer at top should be 0px');
  assert.strictEqual(bottomSpacer.style.height, `${(1000 - 15) * 40}px`, 'Bottom spacer should absorb rest of height');

  // Scroll to middle (scrollTop = 20,000px, roughly item 500)
  container.scrollTop = 20000;
  list.renderWindow();

  // startIndex = max(0, 500 - 5) = 495
  // endIndex = min(1000, 500 + 10 + 5) = 515 (20 items rendered)
  assert.strictEqual(contentBox.children.length, 20, 'Rendered window should remain exactly 20 items in middle');
  assert.strictEqual(topSpacer.style.height, `${495 * 40}px`, 'Top spacer height should equal 495 * 40');
  assert.strictEqual(bottomSpacer.style.height, `${(1000 - 515) * 40}px`, 'Bottom spacer height should equal 485 * 40');

  // Memory check: total DOM nodes in container is 3 + 20 = 23 nodes, NOT 1,000!
  assert.ok(contentBox.children.length < 50, 'DOM elements remain strictly bounded for 1,000 items');
  console.log('✔ WindowedList 1,000 items verified: bounded DOM size (20 items rendered, constant memory)');
}

runTests();
