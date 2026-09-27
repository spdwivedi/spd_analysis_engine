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
