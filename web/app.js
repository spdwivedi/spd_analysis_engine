/**
 * SPD Analysis Engine — Web Control Portal Client Coordinator
 * Pure Vanilla JavaScript (Zero external framework/CDN dependencies)
 */
import { state } from './js/state.js';
import { DOM } from './js/dom.js';
import { initSSE } from './js/stream.js';
import { refreshProjects, loadProjectLogs, initDashboardListeners } from './js/views/dashboard.js';
import { loadProjectEvents, initTimelineListeners, setTimelineNavigationHooks } from './js/views/timeline.js';
import { initDiffListeners } from './js/views/diff_viewer.js';
import { updateTrashCount, initModalListeners } from './js/views/modals.js';
import { setupSidebarToggle } from './js/sidebar.js';

export function selectTab(tabName) {
  state.activeTab = tabName;
  DOM.navButtons.forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tabName));
  DOM.tabViews.forEach((view) => view.classList.toggle('active', view.id === `view-${tabName}`));

  if ((tabName === 'timeline' || tabName === 'diffs' || tabName === 'terminal') && state.selectedProject) {
    loadProjectEvents(state.selectedProject);
  } else if (tabName === 'logs' && state.selectedProject) {
    loadProjectLogs(state.selectedProject);
  }
}

async function init() {
  setTimelineNavigationHooks({
    onSelectTab: selectTab,
    onRefreshProjects: refreshProjects,
    onUpdateTrashCount: updateTrashCount,
    onRenderDashboardRecentEvents: (events) => {
      import('./js/views/dashboard.js').then((m) => m.renderDashboardRecentEvents(events));
    },
  });

  setupSidebarToggle();
  initDashboardListeners();
  initTimelineListeners();
  initDiffListeners();
  initModalListeners();

  DOM.navButtons.forEach((btn) => {
    btn.addEventListener('click', () => selectTab(btn.dataset.tab));
  });

  if (DOM.btnViewAllTimeline) {
    DOM.btnViewAllTimeline.addEventListener('click', () => selectTab('timeline'));
  }

  await updateTrashCount();
  await refreshProjects();
  initSSE();
}

document.addEventListener('DOMContentLoaded', init);
