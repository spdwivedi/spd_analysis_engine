/**
 * SPD Analysis Engine — Windowed / Virtualized List Component
 * Renders large collections (e.g. 1,000-file bursts) efficiently with bounded DOM elements.
 */

export class WindowedList {
  /**
   * @param {HTMLElement} container - Scrollable container element
   * @param {Object} options
   * @param {number} [options.itemHeight=42] - Approximate height of each item in pixels
   * @param {number} [options.buffer=10] - Number of offscreen items to pre-render above and below
   * @param {function(any, number): HTMLElement} options.renderItem - Callback returning an item DOM node
   */
  constructor(container, options = {}) {
    this.container = container;
    this.itemHeight = options.itemHeight || 42;
    this.buffer = options.buffer || 10;
    this.renderItem = options.renderItem || (() => document.createElement('div'));
    this.items = [];
    this._rafId = null;
    this._onScroll = this._onScroll.bind(this);

    this.topSpacer = document.createElement('div');
    this.topSpacer.className = 'windowed-spacer-top';
    this.topSpacer.style.height = '0px';

    this.bottomSpacer = document.createElement('div');
    this.bottomSpacer.className = 'windowed-spacer-bottom';
    this.bottomSpacer.style.height = '0px';

    this.contentBox = document.createElement('div');
    this.contentBox.className = 'windowed-content';
  }

  setItems(items) {
    this.items = Array.isArray(items) ? items : [];
    this.container.removeEventListener('scroll', this._onScroll);

    // If small collection, render normally without windowing overhead
    if (this.items.length <= 60) {
      this.container.innerHTML = '';
      const fragment = document.createDocumentFragment();
      for (let i = 0; i < this.items.length; i++) {
        fragment.appendChild(this.renderItem(this.items[i], i));
      }
      this.container.appendChild(fragment);
      return;
    }

    // Set up windowed viewport structure
    this.container.innerHTML = '';
    this.container.appendChild(this.topSpacer);
    this.container.appendChild(this.contentBox);
    this.container.appendChild(this.bottomSpacer);

    this.container.addEventListener('scroll', this._onScroll, { passive: true });
    this.renderWindow();
  }

  _onScroll() {
    if (this._rafId) return;
    this._rafId = requestAnimationFrame(() => {
      this._rafId = null;
      this.renderWindow();
    });
  }

  renderWindow() {
    const total = this.items.length;
    if (total === 0) {
      this.contentBox.innerHTML = '';
      this.topSpacer.style.height = '0px';
      this.bottomSpacer.style.height = '0px';
      return;
    }

    const scrollTop = this.container.scrollTop || 0;
    const viewHeight = this.container.clientHeight || 400;

    const visibleCount = Math.ceil(viewHeight / this.itemHeight);
    const rawStart = Math.floor(scrollTop / this.itemHeight);

    const startIndex = Math.max(0, rawStart - this.buffer);
    const endIndex = Math.min(total, rawStart + visibleCount + this.buffer);

    this.topSpacer.style.height = `${startIndex * this.itemHeight}px`;
    this.bottomSpacer.style.height = `${Math.max(0, (total - endIndex) * this.itemHeight)}px`;

    const fragment = document.createDocumentFragment();
    for (let i = startIndex; i < endIndex; i++) {
      fragment.appendChild(this.renderItem(this.items[i], i));
    }

    this.contentBox.innerHTML = '';
    this.contentBox.appendChild(fragment);
  }

  destroy() {
    this.container.removeEventListener('scroll', this._onScroll);
    if (this._rafId) cancelAnimationFrame(this._rafId);
  }
}
