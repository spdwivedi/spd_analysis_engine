/**
 * SPD Analysis Engine — Dashboard, Specs, Projects Sidebar & Telemetry
 */
import { DOM } from '../dom.js';
import { state, getSelectedProjectInfo } from '../state.js';
import {
  escapeHtml,
  scrubSensitiveText,
  formatUptime,
  formatIST,
  formatISTTime,
} from '../utils.js';
import { showToast } from '../toasts.js';
import { apiGet, apiPost, resumeProject } from '../api.js';
import { loadProjectEvents } from './timeline.js';

export function setPowerPillState(el, isActive) {
  if (!el) return;
  if (isActive) {
    el.classList.add('active');
    el.classList.remove('disabled');
  } else {
    el.classList.remove('active');
    el.classList.add('disabled');
  }
}

let debounceHeartbeatInterval = null;
let localDebounceDeadline = null;
let debounceTotalWindow = 3.5;
let lastReportedRemainingS = null;

export function stopDebounceHeartbeat() {
  if (debounceHeartbeatInterval !== null) {
    clearInterval(debounceHeartbeatInterval);
    debounceHeartbeatInterval = null;
  }
  localDebounceDeadline = null;
  lastReportedRemainingS = null;
}

export function tickDebounceHeartbeat() {
  const p = getSelectedProjectInfo();
  if (!p || !p.is_active || !p.debounce || !p.debounce.active) {
    stopDebounceHeartbeat();
    return;
  }
  if (localDebounceDeadline === null) {
    return;
  }

  const remainingMs = Math.max(0, localDebounceDeadline - Date.now());
  const rawRemainingS = Math.ceil(remainingMs / 1000);

  // Convert to strictly non-increasing seconds without jumping backwards or skipping numbers
  let remainingS = rawRemainingS;
  if (lastReportedRemainingS !== null && remainingS > lastReportedRemainingS) {
    remainingS = lastReportedRemainingS;
  }
  lastReportedRemainingS = remainingS;

  const totalWindow = debounceTotalWindow || 3.5;
  const pct = Math.max(0, Math.min(100, ((totalWindow - remainingS) / totalWindow) * 100));

  if (DOM.debounceProgressFill) {
    DOM.debounceProgressFill.style.width = pct + '%';
  }

  if (remainingS <= 0 || remainingMs <= 0) {
    if (debounceHeartbeatInterval !== null) {
      clearInterval(debounceHeartbeatInterval);
      debounceHeartbeatInterval = null;
    }
    localDebounceDeadline = null;
    lastReportedRemainingS = 0;
    if (DOM.debounceProgressFill) {
      DOM.debounceProgressFill.style.width = '100%';
    }
    if (DOM.debounceCountdownText) {
      DOM.debounceCountdownText.textContent = 'Sealing burst...';
    }
    return;
  }

  // Format string strictly as MM:SS remaining (e.g. 04:59, 04:58, 04:57)
  const mins = Math.floor(remainingS / 60);
  const secs = remainingS % 60;
  const mm = String(mins).padStart(2, '0');
  const ss = String(secs).padStart(2, '0');
  if (DOM.debounceCountdownText) {
    DOM.debounceCountdownText.textContent = `${mm}:${ss} remaining`;
  }
}

export function startDebounceHeartbeat() {
  // Always execute clearInterval(debounceHeartbeatInterval) before creating a new interval.
  // Never allow multiple interval loops to run concurrently.
  if (debounceHeartbeatInterval !== null) {
    clearInterval(debounceHeartbeatInterval);
    debounceHeartbeatInterval = null;
  }
  tickDebounceHeartbeat();
  debounceHeartbeatInterval = setInterval(tickDebounceHeartbeat, 1000);
}


export function updateHeaderTelemetry() {
  const p = getSelectedProjectInfo();
  if (!p) {
    stopDebounceHeartbeat();
    DOM.headerProjectName.textContent = 'No Project Selected';
    DOM.headerTierPill.textContent = 'NONE';
    DOM.headerTierPill.className = 'project-tier-pill stopped';
    DOM.headerPathVal.textContent = '—';
    DOM.headerPid.textContent = '—';
    DOM.headerUptime.textContent = '—';
    DOM.headerMem.textContent = '—';
    DOM.headerHb.textContent = '—';
    if (DOM.headerCompactTel) DOM.headerCompactTel.textContent = '⚪ Inactive';
    DOM.btnHeaderStop.disabled = true;
    DOM.btnHeaderStop.style.display = '';
    if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = 'none';
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = 'none';
    return;
  }

  DOM.headerProjectName.textContent = p.name;
  DOM.headerPathVal.textContent = p.target_path || '—';

  const now = Date.now();
  const inGrace = Boolean(state.connectGraceUntil && now < state.connectGraceUntil);
  const lastActive = state.lastKnownActive ? state.lastKnownActive.get(p.name) : null;
  const effectiveIsActive = Boolean(p.is_active || (inGrace && lastActive && lastActive.alive));
  const effectiveIsInterrupted = Boolean(!effectiveIsActive && (p.is_interrupted || p.status === 'INTERRUPTED') && (!inGrace || !lastActive || !lastActive.alive));

  if (effectiveIsActive) {
    DOM.headerTierPill.textContent = 'ACTIVE';
    DOM.headerTierPill.className = 'project-tier-pill';
    DOM.btnHeaderStop.disabled = false;
    DOM.btnHeaderStop.style.display = '';
    if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = 'none';
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = 'none';
  } else if (effectiveIsInterrupted) {
    DOM.headerTierPill.textContent = 'INTERRUPTED';
    DOM.headerTierPill.className = 'project-tier-pill interrupted';
    DOM.headerTierPill.title = 'Session was terminated unexpectedly (e.g. system reboot, task kill, power outage)';
    DOM.btnHeaderStop.disabled = true;
    DOM.btnHeaderStop.style.display = 'none';
    if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = '';
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = '';
  } else {
    DOM.headerTierPill.textContent = state.selectedTier.toUpperCase();
    DOM.headerTierPill.className = 'project-tier-pill stopped';
    DOM.btnHeaderStop.disabled = true;
    DOM.btnHeaderStop.style.display = 'none';
    if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = '';
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = '';
  }

  DOM.headerPid.textContent = p.pid != null ? p.pid : (effectiveIsActive && lastActive && lastActive.pid != null ? lastActive.pid : '—');
  DOM.headerUptime.textContent = p.uptime_s != null ? formatUptime(p.uptime_s) : '—';
  const memStr = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';
  DOM.headerMem.textContent = memStr;
  DOM.headerHb.textContent = effectiveIsActive ? 'Live' : (effectiveIsInterrupted ? 'Interrupted' : 'Stopped');
  if (DOM.headerCompactTel) {
    DOM.headerCompactTel.textContent = `${effectiveIsActive ? '🟢 Live' : (effectiveIsInterrupted ? '⚠️ Interrupted' : '⚪ Stopped')} • ${memStr}`;
  }

  // Powers Ribbon
  const powers = p.powers || {};
  setPowerPillState(DOM.powerPillEdits, powers.track_edits ?? true);
  setPowerPillState(DOM.powerPillReads, powers.track_reads ?? true);
  setPowerPillState(DOM.powerPillExec, powers.track_exec ?? true);
  setPowerPillState(DOM.powerPillGit, powers.shadow_git ?? true);

  // Dashboard Telemetry Card
  DOM.detailSlug.textContent = p.name;
  DOM.detailTier.textContent = effectiveIsInterrupted ? 'INTERRUPTED (Power Loss / Terminated)' : state.selectedTier;
  DOM.detailPath.textContent = p.target_path || '—';
  DOM.detailSessionId.textContent = p.session_id != null ? `#${p.session_id}` : '—';
  DOM.detailPid.textContent = p.pid != null ? p.pid : '—';
  DOM.detailMemory.textContent = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';

  // Debounce Burst Monitor
  if (DOM.cardDebounceMonitor) {
    const db = p.debounce;
    if (p.is_active && db) {
      if (db.active) {
        const serverRemainingS = Number(db.remaining_s ?? db.debounce_remaining_s ?? 0);
        debounceTotalWindow = Number(db.quiet_period || 3.5);

        const now = Date.now();
        if (localDebounceDeadline === null) {
          localDebounceDeadline = now + (serverRemainingS * 1000);
          lastReportedRemainingS = Math.ceil(serverRemainingS);
          startDebounceHeartbeat();
        } else {
          const clientRemainingS = Math.max(0, (localDebounceDeadline - now) / 1000);
          if (serverRemainingS - clientRemainingS > 1.5) {
            // Genuine new file modification reset the debounce window
            localDebounceDeadline = now + (serverRemainingS * 1000);
            lastReportedRemainingS = Math.ceil(serverRemainingS);
            startDebounceHeartbeat();
          } else if (debounceHeartbeatInterval === null && serverRemainingS > 0) {
            startDebounceHeartbeat();
          }
          // If incoming server remaining time is within ±1.5 seconds of the current client countdown,
          // ignore the network jitter and continue the smooth client countdown.
        }

        DOM.debounceProgressFill.classList.add('active');
        DOM.debouncePulse.className = 'debounce-pulse-indicator active';
        DOM.debounceStatusBadge.className = 'debounce-status-badge active';
        DOM.debounceStatusBadge.textContent = 'Debouncing Burst';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = '';
        DOM.debounceStatusDesc.textContent = `Coalescing edits in ${db.files_count || 1} file(s) (${db.first_file || ''})…`;
      } else {
        stopDebounceHeartbeat();
        DOM.debounceProgressFill.style.width = '0%';
        DOM.debounceProgressFill.classList.remove('active');
        DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
        DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
        DOM.debounceStatusBadge.textContent = 'Idle / Listening';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
        DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
        DOM.debounceCountdownText.textContent = '—';
      }
    } else if (p.is_active) {
      stopDebounceHeartbeat();
      DOM.debounceProgressFill.style.width = '0%';
      DOM.debounceProgressFill.classList.remove('active');
      DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
      DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
      DOM.debounceStatusBadge.textContent = 'Idle / Listening';
      if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
      DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
      DOM.debounceCountdownText.textContent = '—';
    } else {
      stopDebounceHeartbeat();
      DOM.debounceProgressFill.style.width = '0%';
      DOM.debounceProgressFill.classList.remove('active');
      DOM.debouncePulse.className = 'debounce-pulse-indicator stopped';
      DOM.debounceStatusBadge.className = 'debounce-status-badge stopped';
      DOM.debounceStatusBadge.textContent = p.is_interrupted ? '⚠️ Interrupted' : 'Stopped';
      if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
      DOM.debounceStatusDesc.textContent = p.is_interrupted
        ? 'Session was terminated unexpectedly (e.g. power loss, external kill)'
        : 'Session not running';
      DOM.debounceCountdownText.textContent = '—';
    }
  }

  // Metric cards
  DOM.dashActiveCount.textContent = state.projectsData.current.filter((x) => x.is_active).length;
  DOM.dashEventCount.textContent = p.events_count || 0;
}

export function renderProjectsList() {
  const filter = (DOM.projectFilterInput ? DOM.projectFilterInput.value : '').toLowerCase().trim();

  function renderGroup(items, container, tier) {
    if (!container) return;
    container.innerHTML = '';
    const filtered = (items || []).filter((p) => p.name.toLowerCase().includes(filter));
    if (filtered.length === 0) {
      container.innerHTML = `<div class="empty-hint">${filter ? 'No matching projects' : 'No entries'}</div>`;
      return;
    }

    filtered.forEach((p) => {
      const btn = document.createElement('button');
      const isSelected = state.selectedProject === p.name && state.selectedTier === tier;
      btn.className = `project-item ${isSelected ? 'active' : ''}`;

      const now = Date.now();
      const inGrace = Boolean(state.connectGraceUntil && now < state.connectGraceUntil);
      const lastActive = state.lastKnownActive ? state.lastKnownActive.get(p.name) : null;
      const effectiveIsActive = Boolean(p.is_active || (inGrace && lastActive && lastActive.alive));
      const effectiveIsInterrupted = Boolean(!effectiveIsActive && (p.is_interrupted || p.status === 'INTERRUPTED') && (!inGrace || !lastActive || !lastActive.alive));

      const dot = effectiveIsActive
        ? '<span class="pulse-dot"></span>'
        : (effectiveIsInterrupted ? '<span class="static-dot" style="background: #ef4444; box-shadow: 0 0 6px #ef4444;"></span>' : '<span class="static-dot"></span>');
      const burstCount = p.events_count || 0;
      const evCount = burstCount > 0 ? `<span class="badge badge-subtle">${burstCount} ${burstCount === 1 ? 'burst' : 'bursts'}</span>` : '';
      const interruptedBadge = effectiveIsInterrupted ? `<span class="badge badge-interrupted" title="${escapeHtml(p.interrupted_reason || 'Interrupted')}">⚠️ Interrupted</span>` : '';

      btn.innerHTML = `
        <div class="project-item-left">
          ${dot}
          <span>${escapeHtml(p.name)}</span>
        </div>
        <div style="display: flex; gap: 4px; align-items: center;">
          ${interruptedBadge}
          ${evCount}
        </div>
      `;

      btn.addEventListener('click', () => selectProject(p.name, tier));
      container.appendChild(btn);
    });
  }

  renderGroup(state.projectsData.current, DOM.tierCurrentItems, 'current');
  renderGroup(state.projectsData.last_run, DOM.tierLastRunItems, 'last_run');
  renderGroup(state.projectsData.history, DOM.tierHistoryItems, 'history');
}

export async function refreshProjects(autoSelect = true) {
  try {
    const data = await apiGet('/api/projects');
    state.projectsData = data;

    if (DOM.countCurrent) DOM.countCurrent.textContent = data.current.length;
    if (DOM.countLastRun) DOM.countLastRun.textContent = data.last_run.length;
    if (DOM.countHistory) DOM.countHistory.textContent = data.history.length;

    if (autoSelect && !state.selectedProject) {
      if (data.current.length > 0) {
        selectProject(data.current[0].name, 'current');
      } else if (data.last_run.length > 0) {
        selectProject(data.last_run[0].name, 'last_run');
      } else if (data.history.length > 0) {
        selectProject(data.history[0].name, 'history');
      }
    }

    renderProjectsList();
    updateHeaderTelemetry();
  } catch (err) {
    console.error('Failed to load projects:', err);
  }
}

export async function loadProjectSpecs(projectName) {
  if (!projectName || !DOM.cardProjectSpecs) return;
  try {
    const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/specs`);
    state.projectSpecs = data;
    if (DOM.specMonitoredPath) {
      DOM.specMonitoredPath.textContent = data.target_path || '—';
      DOM.specMonitoredPath.title = data.target_path || '';
    }
    if (DOM.specAiProfile) {
      const prof = data.ide_profile || 'antigravity';
      let profLabel = data.monitored_ai_env || 'Google Antigravity';
      if (!data.monitored_ai_env) {
        if (prof === 'cursor') profLabel = 'Cursor AI';
        else if (prof === 'windsurf') profLabel = 'Windsurf / Codeium';
        else if (prof === 'claude_code') profLabel = 'Claude Code CLI';
        else if (prof === 'custom') {
          profLabel = data.ide_custom_marker ? `Custom (${data.ide_custom_marker})` : 'Custom AI';
        }
      }
      DOM.specAiProfile.textContent = `${profLabel} (Native VS Code Ignored)`;
    }
    if (DOM.specDebounceWindow) {
      const dbVal = data.debounce != null ? data.debounce : (data.debounce_window != null ? data.debounce_window : 3.5);
      const isCustom = dbVal !== 3.5;
      let label = `${dbVal}s`;
      if (dbVal >= 60) {
        const mins = Math.floor(dbVal / 60);
        const secs = Math.round(dbVal % 60);
        label = `${mins}m ${secs > 0 ? secs + 's ' : ''}(${dbVal}s)`;
      }
      const dbBadge = isCustom ? ' (Custom)' : ' (Standard Recommended)';
      DOM.specDebounceWindow.textContent = `${label}${dbBadge}`;
    }
    const powers = data.powers || {};
    setPowerPillState(DOM.specCheckEdits, powers.track_edits ?? true);
    setPowerPillState(DOM.specCheckReads, powers.track_reads ?? true);
    setPowerPillState(DOM.specCheckExec, powers.track_exec ?? true);
    setPowerPillState(DOM.specCheckGit, powers.shadow_git ?? true);

    if (DOM.specGithubRemote) {
      if (data.remote_url && data.remote_url !== 'Not Linked') {
        const branchSuffix = data.remote_branch ? ` [${data.remote_branch}]` : '';
        DOM.specGithubRemote.innerHTML = `<span class="badge badge-success" style="font-weight: 600; margin-right: 6px; background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); padding: 1px 6px; border-radius: 4px;">✓ Linked</span><span>${escapeHtml(data.remote_url)}${escapeHtml(branchSuffix)}</span>`;
        DOM.specGithubRemote.title = `${data.remote_url}${branchSuffix}`;
      } else {
        DOM.specGithubRemote.textContent = 'Not Linked';
        DOM.specGithubRemote.title = '';
      }
    }

    if (DOM.specLastPushStatus) {
      if (data.last_push_time) {
        const timeFormatted = formatIST(data.last_push_time);
        const commitSuffix = data.last_push_commit ? ` • commit ${data.last_push_commit.slice(0, 7)}` : '';
        DOM.specLastPushStatus.textContent = `${data.last_push_status} at ${timeFormatted}${commitSuffix}`;
      } else {
        DOM.specLastPushStatus.textContent = data.last_push_status || 'Never pushed';
      }
    }
  } catch (err) {
    console.warn(`Could not load specs for ${projectName}:`, err);
  }
}

export function selectProject(projectName, tier) {
  if (state.selectedProject !== projectName) {
    state.selectedEventId = null;
    state.selectedFilePath = null;
  }
  state.selectedProject = projectName;
  state.selectedTier = tier;
  renderProjectsList();
  updateHeaderTelemetry();
  loadProjectSpecs(projectName);
  loadProjectEvents(projectName);
  loadProjectLogs(projectName);
}

export async function loadProjectLogs(projectName) {
  if (!projectName || !DOM.logsTerminal) return;
  try {
    const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/logs`);
    const lines = data.lines || [];

    if (lines.length === 0) {
      DOM.logsTerminal.innerHTML = '<div class="log-line">No log lines recorded yet.</div>';
      return;
    }

    const fragment = document.createDocumentFragment();
    lines.forEach((raw) => {
      const lineDiv = document.createElement('div');
      lineDiv.className = 'log-line';

      if (raw.includes('ERROR') || raw.includes('CRITICAL')) {
        lineDiv.className += ' err';
      } else if (raw.includes('WARNING') || raw.includes('WARN')) {
        lineDiv.className += ' warn';
      } else if (raw.startsWith('{') && raw.endsWith('}')) {
        lineDiv.className += ' json';
      } else if (raw.includes('INFO')) {
        lineDiv.className += ' info';
      }

      lineDiv.textContent = raw;
      fragment.appendChild(lineDiv);
    });

    DOM.logsTerminal.innerHTML = '';
    DOM.logsTerminal.appendChild(fragment);

    if (state.autoScrollLogs) {
      DOM.logsTerminal.scrollTop = DOM.logsTerminal.scrollHeight;
    }
  } catch (err) {
    DOM.logsTerminal.innerHTML = `<div class="log-line err">Error loading logs: ${escapeHtml(err.message)}</div>`;
  }
}

export function renderLiveTicker(actions) {
  if (!DOM.liveTickerFeed) return;
  if (!actions || actions.length === 0) {
    if (DOM.liveActionCount) DOM.liveActionCount.textContent = '0 actions';
    DOM.liveTickerFeed.innerHTML = '<div class="ticker-empty">Listening for file modifications, file inspections, and executions…</div>';
    return;
  }

  if (DOM.liveActionCount) {
    DOM.liveActionCount.textContent = `${actions.length} action${actions.length === 1 ? '' : 's'}`;
  }

  const sig = actions.map((a) => `${a.timestamp || a.iso || a.time}_${a.action_type || a.type}_${a.file_path || a.file}`).join('|');
  if (DOM.liveTickerFeed.dataset.signature === sig) {
    return;
  }
  DOM.liveTickerFeed.dataset.signature = sig;

  const fragment = document.createDocumentFragment();
  actions.slice().reverse().forEach((action) => {
    const row = document.createElement('div');
    row.className = 'ticker-row';

    const type = action.action_type || action.type || 'WRITE';
    let typeClass = 'write';
    let icon = '✏️';
    if (type === 'READ') {
      typeClass = 'read';
      icon = '👁️';
    } else if (type === 'EXEC') {
      typeClass = 'exec';
      icon = '💻';
    } else if (type === 'BURST') {
      typeClass = 'burst';
      icon = '⚡';
    }

    const rawTime = action.timestamp || action.iso || action.time || '';
    const timeStr = formatISTTime(rawTime);
    const file = action.file_path || action.file || '';
    const details = action.details ? ` (${action.details})` : '';

    row.innerHTML = `
      <span class="ticker-badge ${typeClass}">${icon} ${escapeHtml(type)}</span>
      <span class="ticker-time">[${escapeHtml(timeStr)}]</span>
      <span class="ticker-path mono" title="${escapeHtml(file)}">${escapeHtml(file)}${escapeHtml(details)}</span>
    `;
    fragment.appendChild(row);
  });

  DOM.liveTickerFeed.innerHTML = '';
  DOM.liveTickerFeed.appendChild(fragment);
}

export function renderDashboardRecentEvents(recentEvents) {
  if (!DOM.dashRecentEvents) return;
  DOM.dashRecentEvents.innerHTML = '';
  const events = recentEvents || state.eventsData;
  if (!events || events.length === 0) {
    DOM.dashRecentEvents.innerHTML = '<div class="empty-state">No events recorded yet.</div>';
    return;
  }

  const recent = events.slice(-3).reverse();
  recent.forEach((ev) => {
    const row = document.createElement('div');
    row.className = 'compact-event-row';
    const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
    const summaryClean = scrubSensitiveText(ev.summary || '');
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(burstLabel)}</strong> — ${escapeHtml(summaryClean)}
      </div>
      <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
    `;
    DOM.dashRecentEvents.appendChild(row);
  });
}

export async function handleResumeProject() {
  const p = getSelectedProjectInfo();
  if (!p) {
    showToast('Please select a project to resume.', 'error');
    return;
  }

  if (p.is_active) {
    showToast(`Project '${p.name}' is already actively monitored!`, 'info');
    return;
  }

  if (DOM.btnHeaderResume) DOM.btnHeaderResume.disabled = true;
  if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.disabled = true;
  showToast(`Resuming monitoring for '${p.name}'…`, 'info');

  try {
    const currentDebounceWindow = state.projectSpecs?.debounce ?? state.projectSpecs?.debounce_window ?? p.debounce?.quiet_period ?? p.debounce?.debounce_window;
    const payload = { resume: true };
    if (currentDebounceWindow != null) {
      payload.debounce_window = parseFloat(currentDebounceWindow);
    }

    const data = await resumeProject(p.name, payload);
    if (!data || !data.success) {
      throw new Error(data?.message || data?.error || 'Failed to resume project');
    }

    showToast(`Resumed monitoring for '${p.name}' (PID: ${data.result ? data.result.pid : ''})`, 'success');
    await refreshProjects();
    selectProject(p.name, 'current');
  } catch (err) {
    console.error('Resume project error:', err);
    showToast(`Failed to resume: ${err.message}`, 'error');
  } finally {
    if (DOM.btnHeaderResume) DOM.btnHeaderResume.disabled = false;
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.disabled = false;
  }
}

export async function handleStopActiveProject() {
  const p = getSelectedProjectInfo();
  if (!p || !p.is_active) return;

  if (!confirm(`Are you sure you want to stop monitoring '${p.name}' and archive this session?`)) {
    return;
  }

  try {
    await apiPost('/api/projects/stop', { project: p.name });
    showToast(`Project '${p.name}' stopped and archived.`, 'success');
    await refreshProjects();
    selectProject(p.name, 'last_run');
  } catch (err) {
    showToast(`Stop failed: ${err.message}`, 'error');
  }
}

export function handleExportSession() {
  if (!state.selectedProject) return;
  const url = `/api/project/${encodeURIComponent(state.selectedProject)}/export`;
  const a = document.createElement('a');
  a.href = url;
  a.download = `spd_session_${state.selectedProject}_${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  showToast('Session export JSON downloaded.', 'success');
}

export function initDashboardListeners() {
  if (DOM.projectFilterInput) {
    DOM.projectFilterInput.addEventListener('input', renderProjectsList);
  }
  if (DOM.btnRefreshProjects) {
    DOM.btnRefreshProjects.addEventListener('click', () => refreshProjects(false));
  }
  if (DOM.btnHeaderStop) {
    DOM.btnHeaderStop.addEventListener('click', handleStopActiveProject);
  }
  if (DOM.btnHeaderResume) {
    DOM.btnHeaderResume.addEventListener('click', handleResumeProject);
  }
  if (DOM.btnTelemetryResume) {
    DOM.btnTelemetryResume.addEventListener('click', handleResumeProject);
  }
  if (DOM.btnExportSession) {
    DOM.btnExportSession.addEventListener('click', handleExportSession);
  }

  // Logs controls
  if (DOM.chkAutoscroll) {
    DOM.chkAutoscroll.addEventListener('change', (e) => {
      state.autoScrollLogs = e.target.checked;
    });
  }
  if (DOM.btnRefreshLogs) {
    DOM.btnRefreshLogs.addEventListener('click', () => loadProjectLogs(state.selectedProject));
  }
  if (DOM.btnCopyLogs) {
    DOM.btnCopyLogs.addEventListener('click', () => {
      if (DOM.logsTerminal) {
        navigator.clipboard.writeText(DOM.logsTerminal.innerText);
        showToast('Logs copied to clipboard', 'success');
      }
    });
  }

  // Sidebar Accordions
  document.querySelectorAll('.accordion-header').forEach((hdr) => {
    hdr.addEventListener('click', () => {
      const section = hdr.closest('.accordion-section');
      if (section) section.classList.toggle('open');
    });
  });

  // Debounce slider & presets
  if (DOM.rngDebounce) {
    DOM.rngDebounce.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      const valFixed = val.toFixed(1);
      let badge = '';
      if (valFixed === '3.5') {
        badge = '<span class="badge badge-recommended">Fast (Recommended)</span>';
      } else if (val >= 9.5 && val <= 10.5) {
        badge = '<span class="badge badge-subtle">Medium</span>';
      } else if (val >= 58 && val <= 62) {
        badge = '<span class="badge badge-subtle">1m</span>';
      } else if (val >= 295 && val <= 305) {
        badge = '<span class="badge badge-subtle">5m</span>';
      } else if (val >= 595 && val <= 605) {
        badge = '<span class="badge badge-subtle">10m</span>';
      } else if (val >= 1795 && val <= 1805) {
        badge = '<span class="badge badge-subtle">30m</span>';
      }

      let timeLabel = `${valFixed}s`;
      if (val >= 60) {
        const mins = Math.floor(val / 60);
        const secs = Math.round(val % 60);
        timeLabel = `${mins}m ${secs > 0 ? secs + 's ' : ''}(${valFixed}s)`;
      }
      if (DOM.debounceValDisplay) {
        DOM.debounceValDisplay.innerHTML = `${timeLabel} ${badge}`;
      }
    });
  }

  document.querySelectorAll('.btn-debounce-preset').forEach((btn) => {
    btn.addEventListener('click', () => {
      const val = parseFloat(btn.dataset.val);
      if (!isNaN(val) && DOM.rngDebounce) {
        DOM.rngDebounce.value = val;
        DOM.rngDebounce.dispatchEvent(new Event('input'));
      }
    });
  });

  // Lap Button: Manual Burst Sealing
  if (DOM.btnSealBurstNow) {
    DOM.btnSealBurstNow.addEventListener('click', async () => {
      if (!state.selectedProject) return;
      DOM.btnSealBurstNow.disabled = true;
      DOM.btnSealBurstNow.innerHTML = '<span class="spinner-sm"></span> Sealing…';
      try {
        const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/seal-burst`, {
          method: 'POST',
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.message || data.error || 'Seal burst failed');
        }
        showToast('Burst sealed immediately (Lap triggered)!', 'success');
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
        DOM.btnSealBurstNow.style.display = 'none';
        await loadProjectEvents(state.selectedProject);
      } catch (err) {
        console.error('Seal burst error:', err);
        showToast(`Seal burst failed: ${err.message}`, 'error');
      } finally {
        DOM.btnSealBurstNow.disabled = false;
        DOM.btnSealBurstNow.innerHTML = '⚡ Seal Burst Now (Lap)';
      }
    });
  }
}
