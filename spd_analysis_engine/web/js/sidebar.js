/**
 * SPD Analysis Engine — Collapsible Sidebar & Layout Controller
 */
import { DOM } from './dom.js';

export function setupSidebarToggle() {
  if (!DOM.sidebar) return;

  function applySidebarState(isCollapsed) {
    DOM.sidebar.classList.toggle('collapsed', isCollapsed);
    if (DOM.sidebarToggleIcon) {
      DOM.sidebarToggleIcon.textContent = isCollapsed ? '▶' : '◀';
    }
    try {
      localStorage.setItem('spd_sidebar_collapsed', String(isCollapsed));
    } catch (e) {}
  }

  try {
    const stored = localStorage.getItem('spd_sidebar_collapsed');
    if (stored !== null) {
      applySidebarState(stored === 'true');
    } else if (window.innerWidth <= 780) {
      applySidebarState(true);
    }
  } catch (e) {}

  if (DOM.btnToggleSidebar) {
    DOM.btnToggleSidebar.addEventListener('click', (e) => {
      e.stopPropagation();
      const currentlyCollapsed = DOM.sidebar.classList.contains('collapsed');
      applySidebarState(!currentlyCollapsed);
    });
  }

  // Keyboard shortcut: Ctrl + B or Cmd + B
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'b' || e.key === 'B')) {
      e.preventDefault();
      const currentlyCollapsed = DOM.sidebar.classList.contains('collapsed');
      applySidebarState(!currentlyCollapsed);
    }
  });

  // Auto-collapse on small window resize
  window.addEventListener('resize', () => {
    if (window.innerWidth <= 780 && !DOM.sidebar.classList.contains('collapsed')) {
      applySidebarState(true);
    }
  });
}

/**
 * Atomic view switching with strict pane isolation.
 * Deactivates all panes, resets inline styles, activates target pane,
 * updates navigation links, and triggers view-specific refreshes.
 */
export function switchView(viewName) {
  const allPanes = document.querySelectorAll('.view-pane, .tab-view');
  const allNavLinks = document.querySelectorAll('.nav-link, .nav-item');

  // 1. Remove active state from every pane
  allPanes.forEach((pane) => {
    pane.classList.remove('active');
    pane.style.display = 'none';
  });

  // 2. Remove active state from sidebar navigation links
  allNavLinks.forEach((link) => link.classList.remove('active'));

  // 3. Activate the specific target pane
  let targetPane = document.getElementById(`view-${viewName}`);
  if (!targetPane && (viewName === 'diff' || viewName === 'diffs')) {
    targetPane = document.getElementById('view-diff') || document.getElementById('view-diffs');
  }
  if (targetPane) {
    targetPane.classList.add('active');
    targetPane.style.display = ''; // defer to CSS .view-pane.active
  }

  // 4. Update active sidebar item
  let activeLink = document.querySelector(`[data-view="${viewName}"], [data-tab="${viewName}"]`);
  if (!activeLink && (viewName === 'diff' || viewName === 'diffs')) {
    activeLink = document.querySelector('[data-view="diff"], [data-tab="diffs"], [data-view="diffs"], [data-tab="diff"]');
  }
  if (activeLink) activeLink.classList.add('active');

  // 5. Trigger view-specific data refresh if needed
  if ((viewName === 'diff' || viewName === 'diffs') && typeof window.refreshDiffView === 'function') {
    window.refreshDiffView();
  } else if (viewName === 'logs' && typeof window.refreshWorkerLogs === 'function') {
    window.refreshWorkerLogs();
  }
}

