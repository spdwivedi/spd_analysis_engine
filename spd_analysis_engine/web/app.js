/**
 * SPD Analysis Engine — Web Control Portal Client Coordinator
 * Pure Vanilla JavaScript (Zero external framework/CDN dependencies)
 */
import { state } from './js/state.js';
import { DOM } from './js/dom.js';
import { initSSE } from './js/stream.js';
import { refreshProjects, loadProjectLogs, initDashboardListeners } from './js/views/dashboard.js';
import { loadProjectEvents, loadRecentEventSummaries, initTimelineListeners, setTimelineNavigationHooks } from './js/views/timeline.js';
import { initDiffListeners } from './js/views/diff_viewer.js';
import { updateTrashCount, initModalListeners } from './js/views/modals.js';
import { setupSidebarToggle, switchView } from './js/sidebar.js';
import { initEcoMode } from './js/eco_mode.js';

export function selectTab(tabName) {
  state.activeTab = tabName;
  switchView(tabName);

  try {
    if ((tabName === 'timeline' || tabName === 'diffs' || tabName === 'diff' || tabName === 'terminal') && state.selectedProject) {
      loadProjectEvents(state.selectedProject);
    } else if (tabName === 'logs' && state.selectedProject) {
      loadProjectLogs(state.selectedProject);
    }
  } catch (err) {
    console.error(`Error activating tab '${tabName}':`, err);
  }
}

export { switchView };

// Expose global window hooks for atomic view switching and view data refresh
window.switchView = selectTab;
window.selectTab = selectTab;
window.refreshDiffView = () => {
  if (state.selectedProject) {
    loadProjectEvents(state.selectedProject);
  }
};
window.refreshWorkerLogs = () => {
  if (state.selectedProject) {
    loadProjectLogs(state.selectedProject);
  }
};

async function init() {
  try {
    setTimelineNavigationHooks({
      onSelectTab: selectTab,
      onRefreshProjects: refreshProjects,
      onUpdateTrashCount: updateTrashCount,
      onRenderDashboardRecentEvents: (events) => {
        import('./js/views/dashboard.js')
          .then((m) => m.renderDashboardRecentEvents(events))
          .catch((err) => console.warn('Render recent events error:', err));
      },
    });
  } catch (err) {
    console.error('Error setting timeline navigation hooks:', err);
  }

  try {
    setupSidebarToggle();
  } catch (err) {
    console.error('Error initializing sidebar toggle:', err);
  }

  try {
    initDashboardListeners();
  } catch (err) {
    console.error('Error initializing dashboard listeners:', err);
  }

  try {
    initTimelineListeners();
  } catch (err) {
    console.error('Error initializing timeline listeners:', err);
  }

  try {
    initDiffListeners();
  } catch (err) {
    console.error('Error initializing diff listeners:', err);
  }

  try {
    initModalListeners();
  } catch (err) {
    console.error('Error initializing modal listeners:', err);
  }

  try {
    initEcoMode();
  } catch (err) {
    console.error('Error initializing eco mode:', err);
  }

  try {
    if (DOM.navButtons) {
      DOM.navButtons.forEach((btn) => {
        btn.addEventListener('click', () => selectTab(btn.dataset.view || btn.dataset.tab));
      });
    }

    if (DOM.btnViewAllTimeline) {
      DOM.btnViewAllTimeline.addEventListener('click', () => selectTab('timeline'));
    }
  } catch (err) {
    console.error('Error binding navigation button listeners:', err);
  }

  // 1. Initial page load: project summary
  try {
    await refreshProjects();
  } catch (err) {
    console.error('Initial project summary error:', err);
  }

  // 2. Initial page load: recent event summaries
  try {
    if (state.selectedProject) {
      await loadRecentEventSummaries(state.selectedProject);
    }
  } catch (err) {
    console.error('Initial recent event summaries error:', err);
  }

  // 3. Initial page load: SSE connection
  try {
    initSSE();
  } catch (err) {
    console.error('Error initializing SSE connection:', err);
  }

  // Background nonessential metadata
  try {
    updateTrashCount();
  } catch (_) {}
}

document.addEventListener('DOMContentLoaded', init);
