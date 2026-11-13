/**
 * SPD Analysis Engine — SSE Live Streaming & Heartbeat Telemetry
 */
import { DOM } from './dom.js';
import { state, getSelectedProjectInfo } from './state.js';
import { apiGet } from './api.js';
import { showToast } from './toasts.js';
import {
  updateHeaderTelemetry,
  renderLiveTicker,
  loadProjectLogs,
  loadProjectSpecs,
  triggerInstantDebounce,
  stopDebounceHeartbeat,
} from './views/dashboard.js';
import { handleBurstSealedEvent } from './views/timeline.js';

// Setup visibility listener to pause nonessential polling when tab is hidden
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      state.isTabHidden = true;
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } else {
      state.isTabHidden = false;
      if (!state.sseActive && !state.pollTimer) {
        pollLiveStatus();
        state.pollTimer = setInterval(pollLiveStatus, 2000);
      }
    }
  });
}

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
      if (Array.isArray(match.recent_actions) && match.recent_actions.length > 0) {
        activeProject.recent_actions = match.recent_actions.slice(0, 20);
        try {
          sessionStorage.setItem('spd_actions_' + activeProject.name, JSON.stringify(activeProject.recent_actions));
        } catch (_) {}
        renderLiveTicker(activeProject.recent_actions);
      } else if (!activeProject.recent_actions || activeProject.recent_actions.length === 0) {
        try {
          const stored = sessionStorage.getItem('spd_actions_' + activeProject.name);
          if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed) && parsed.length > 0) {
              activeProject.recent_actions = parsed.slice(0, 20);
              renderLiveTicker(activeProject.recent_actions);
            }
          }
        } catch (_) {}
      }
      updateHeaderTelemetry();

      // CRITICAL: handleLivePing must NEVER re-fetch the entire event list!
      if (state.activeTab === 'logs') {
        loadProjectLogs(activeProject.name);
      }
    }
  }
}

export async function pollLiveStatus() {
  if (typeof document !== 'undefined' && document.hidden) {
    return; // Pause nonessential polling when tab is hidden
  }
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
    const streamUrl = state.lastEventId
      ? `/api/stream?last_event_id=${encodeURIComponent(state.lastEventId)}`
      : '/api/stream';
    const es = new EventSource(streamUrl);
    state.eventSource = es;

    es.addEventListener('open', () => {
      state.sseActive = true;
      state.connectGraceUntil = Date.now() + 2000;
      if (DOM.connStatus) {
        const dot = DOM.connStatus.querySelector('.status-dot');
        if (dot) dot.className = 'status-dot connected';
      }
      if (DOM.connText) DOM.connText.textContent = 'SSE Stream Active';
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    });

    es.addEventListener('connected', (e) => {
      state.sseActive = true;
      if (e.lastEventId) state.lastEventId = e.lastEventId;
    });

    es.addEventListener('ping', (e) => {
      if (e.lastEventId) state.lastEventId = e.lastEventId;
      try {
        const payload = JSON.parse(e.data);
        handleLivePing(payload);
      } catch (_) {}
    });

    // "On burst_sealed: fetch only that event"
    es.addEventListener('burst_sealed', (e) => {
      if (e.lastEventId) state.lastEventId = e.lastEventId;
      try {
        const payload = JSON.parse(e.data);
        handleBurstSealedEvent(payload);
        stopDebounceHeartbeat();
        try {
          const proj = payload.project || state.selectedProject;
          if (proj) {
            sessionStorage.removeItem('spd_actions_' + proj);
          }
        } catch (_) {}
        const p = getSelectedProjectInfo();
        if (p) {
          p.recent_actions = [];
          if (p.debounce) {
            p.debounce.active = false;
            p.debounce.remaining_s = 0;
          }
        }
        renderLiveTicker([]);
        if (DOM.debounceProgressFill) {
          DOM.debounceProgressFill.style.width = '0%';
          DOM.debounceProgressFill.classList.remove('active');
        }
        if (DOM.debouncePulse) DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
        if (DOM.debounceStatusBadge) {
          DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
          DOM.debounceStatusBadge.textContent = 'Idle / Listening';
        }
        if (DOM.debounceStatusDesc) DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
        if (DOM.debounceCountdownText) DOM.debounceCountdownText.textContent = '—';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
      } catch (err) {
        console.warn('Failed to parse burst_sealed SSE:', err);
      }
    });

    es.addEventListener('status_changed', (e) => {
      if (e.lastEventId) state.lastEventId = e.lastEventId;
      try {
        const payload = JSON.parse(e.data);
        if (payload.project === state.selectedProject) {
          const p = getSelectedProjectInfo();
          if (p) {
            p.status = payload.state;
            p.is_active = (payload.state === 'ACTIVE' || payload.state === 'STARTING' || payload.state === 'INDEXING');
            updateHeaderTelemetry();
          }
        }
      } catch (_) {}
    });

    es.addEventListener('live_action', (e) => {
      if (e.lastEventId) state.lastEventId = e.lastEventId;
      try {
        const payload = JSON.parse(e.data);
        if (payload.project === state.selectedProject) {
          const p = getSelectedProjectInfo();
          if (p) {
            const action = {
              timestamp: payload.timestamp ? new Date(payload.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
              iso: payload.timestamp || new Date().toISOString(),
              action_type: (payload.action_type || payload.event_type || 'WRITE').toUpperCase(),
              file_path: payload.path || payload.file_path || '',
              details: payload.details || payload.command || '',
            };
            if (!Array.isArray(p.recent_actions)) {
              p.recent_actions = [];
            }
            p.recent_actions.unshift(action);
            if (p.recent_actions.length > 20) {
              p.recent_actions = p.recent_actions.slice(0, 20);
            }
            try {
              sessionStorage.setItem('spd_actions_' + p.name, JSON.stringify(p.recent_actions));
            } catch (_) {}
            const badge = DOM.badgeLiveActions || DOM.liveActionCount;
            if (badge) {
              badge.textContent = `${p.recent_actions.length} action${p.recent_actions.length === 1 ? '' : 's'}`;
            }
            renderLiveTicker(p.recent_actions);
            if (action.action_type === 'WRITE' || action.action_type === 'UPDATE' || action.action_type === 'EXEC') {
              triggerInstantDebounce(action);
            }
          }
        }
      } catch (err) {
        console.warn('Failed to parse live_action SSE:', err);
      }
    });

    es.addEventListener('auto_push_result', (e) => {
      if (e.lastEventId) state.lastEventId = e.lastEventId;
      try {
        const payload = JSON.parse(e.data);
        if (payload.success) {
          showToast(`🚀 Milestone Auto-Pushed: ${payload.commit || 'Committed'}`, 'success');
          if (state.selectedProject) {
            loadProjectSpecs(state.selectedProject);
          }
        } else if (payload.skipped) {
          if (payload.reason && payload.reason !== 'auto_push not enabled') {
            showToast(`⚠️ Auto-push skipped: ${payload.reason}`, 'warning');
          }
        } else {
          showToast(`⚠️ Auto-push skipped: ${payload.error || 'Failed'}`, 'warning');
        }
      } catch (err) {
        console.warn('Failed to parse auto_push_result SSE:', err);
      }
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

      if (!state.pollTimer && !(typeof document !== 'undefined' && document.hidden)) {
        state.pollTimer = setInterval(pollLiveStatus, 2000);
      }
    };
  } catch (_) {
    if (!state.pollTimer && !(typeof document !== 'undefined' && document.hidden)) {
      state.pollTimer = setInterval(pollLiveStatus, 2000);
    }
  }
}
