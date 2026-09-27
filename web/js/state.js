/**
 * SPD Analysis Engine — Reactive Client State Store
 */

export const state = {
  selectedProject: null,
  selectedTier: 'current',
  activeTab: 'dashboard',
  projectsData: { current: [], last_run: [], history: [] },
  eventsData: [],
  sessionsData: [],
  selectedSessionId: null,
  selectedEventId: null,
  selectedFilePath: null,

  autoScrollLogs: true,
  sseActive: false,
  eventSource: null,
  pollTimer: null,
  lastHbSeconds: 0,
  timelineFilter: 'all',
  expandedAiEventIds: new Set(),
  analyzingEventIds: new Set(),
  savedGeminiKeys: [],
  projectSpecs: null,
  connectGraceUntil: Date.now() + 2000,
  lastKnownActive: new Map(),
};

const subscribers = new Set();

export function subscribeState(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function notifyState(key, value) {
  subscribers.forEach((fn) => {
    try {
      fn(key, value, state);
    } catch (err) {
      console.error('State subscriber error:', err);
    }
  });
}

export function getSelectedProjectInfo() {
  if (!state.selectedProject) return null;
  const tierList = state.projectsData[state.selectedTier] || [];
  return tierList.find((p) => p.name === state.selectedProject) || null;
}

export function resetProjectSelection() {
  state.selectedProject = null;
  state.selectedEventId = null;
  state.selectedFilePath = null;
  state.eventsData = [];
  state.sessionsData = [];
  notifyState('selectedProject', null);
}
