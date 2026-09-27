/**
 * SPD Analysis Engine — SSE Live Streaming & Heartbeat Telemetry
 */
import { DOM } from './dom.js';
import { state, getSelectedProjectInfo } from './state.js';
import { apiGet } from './api.js';
import { updateHeaderTelemetry, renderLiveTicker, loadProjectLogs } from './views/dashboard.js';
import { loadProjectEvents } from './views/timeline.js';

export function handleLivePing(payload) {
  if (!payload || !payload.workers) return;
  const workers = payload.workers;

  workers.forEach((w) => {
    if (w && w.project) {
      state.lastKnownActive.set(w.project, {
        alive: Boolean(w.alive),
        timestamp: Date.now(),
        pid: w.pid,
      });
    }
  });

  const activeProject = getSelectedProjectInfo();
  if (activeProject && state.selectedTier === 'current') {
    const match = workers.find((w) => w.project === activeProject.name);
    if (match) {
      activeProject.is_active = match.alive;
      activeProject.uptime_s = match.uptime_s;
      activeProject.memory_mb = match.memory_mb;
      activeProject.pid = match.pid;
      activeProject.session_id = match.session_id;
      if (match.debounce) {
        activeProject.debounce = match.debounce;
      } else if (!match.alive && activeProject.debounce) {
        activeProject.debounce.active = false;
      }
      if (Array.isArray(match.recent_actions)) {
        activeProject.recent_actions = match.recent_actions;
        renderLiveTicker(match.recent_actions);
      }
      updateHeaderTelemetry();

      if (state.activeTab === 'dashboard' || state.activeTab === 'timeline' || state.activeTab === 'diffs') {
        loadProjectEvents(activeProject.name);
      }
      if (state.activeTab === 'logs') {
        loadProjectLogs(activeProject.name);
      }
    }
  }
}

export async function pollLiveStatus() {
  try {
    const statusData = await apiGet('/api/status');
    handleLivePing(statusData);
  } catch (_) {}
}

export function initSSE() {
  state.connectGraceUntil = Date.now() + 2000;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  try {
    const es = new EventSource('/api/stream');
    state.eventSource = es;

    es.addEventListener('open', () => {
      state.sseActive = true;
      state.connectGraceUntil = Date.now() + 2000;
      if (DOM.connStatus) {
        const dot = DOM.connStatus.querySelector('.status-dot');
        if (dot) dot.className = 'status-dot connected';
      }
      if (DOM.connText) DOM.connText.textContent = 'SSE Stream Active';
    });

    es.addEventListener('connected', () => {
      state.sseActive = true;
    });

    es.addEventListener('ping', (e) => {
      try {
        const payload = JSON.parse(e.data);
        handleLivePing(payload);
      } catch (_) {}
    });

    es.onerror = () => {
      state.sseActive = false;
      if (DOM.connStatus) {
        const dot = DOM.connStatus.querySelector('.status-dot');
        if (dot) dot.className = 'status-dot disconnected';
      }
      if (DOM.connText) DOM.connText.textContent = 'Reconnecting / Polling…';
      es.close();
      state.eventSource = null;

      if (!state.pollTimer) {
        state.pollTimer = setInterval(pollLiveStatus, 1500);
      }
    };
  } catch (_) {
    if (!state.pollTimer) {
      state.pollTimer = setInterval(pollLiveStatus, 1500);
    }
  }
}
