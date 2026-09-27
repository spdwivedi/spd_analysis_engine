/**
 * SPD Analysis Engine — Timeline, Terminal Feed, and Inspection Views
 */
import { DOM } from '../dom.js';
import { state, getSelectedProjectInfo } from '../state.js';
import { escapeHtml, scrubSensitiveText, formatIST } from '../utils.js';
import { showToast, showRollbackTray } from '../toasts.js';
import { apiGet, apiPost, rollbackBurst as apiRollbackBurst } from '../api.js';
import {
  createAiCardHtml,
  setupDiffViewerHierarchy,
  onDiffSessionSelected,
  onDiffBurstSelected,
} from './diff_viewer.js';

let _onSelectTab = null;
let _onRefreshProjects = null;
let _onUpdateTrashCount = null;
let _onRenderDashboardRecentEvents = null;

export function setTimelineNavigationHooks({ onSelectTab, onRefreshProjects, onUpdateTrashCount, onRenderDashboardRecentEvents }) {
  if (onSelectTab) _onSelectTab = onSelectTab;
  if (onRefreshProjects) _onRefreshProjects = onRefreshProjects;
  if (onUpdateTrashCount) _onUpdateTrashCount = onUpdateTrashCount;
  if (onRenderDashboardRecentEvents) _onRenderDashboardRecentEvents = onRenderDashboardRecentEvents;
}

export function createExecAiCardHtml(ai) {
  if (!ai) return '';
  if (typeof ai === 'string') {
    try {
      ai = JSON.parse(ai);
    } catch (e) {
      return `<div class="ai-analysis-card exec-ai-card"><div class="ai-intent-text">${escapeHtml(ai)}</div></div>`;
    }
  }
  const intent = ai.intent || 'No intent description provided.';
  const techPurpose = ai.technical_purpose || 'Standard command-line execution.';
  const outcome = ai.outcome_analysis || 'Executed without errors.';
  const provider = ai.provider ? `Provider: ${ai.provider}` : 'AI Intelligence';
  const cachedBadge = ai.cached ? ' • (Cached)' : '';
  const summary = Array.isArray(ai.summary) ? ai.summary : [];

  let summaryHtml = '';
  if (summary.length > 0) {
    summaryHtml = `
      <div class="ai-section">
        <div class="ai-section-title">Key Highlights:</div>
        <ul class="ai-summary-list">
          ${summary.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}
        </ul>
      </div>
    `;
  }

  return `
    <div class="ai-analysis-card exec-ai-card collapsible-card">
      <div class="ai-card-header card-toggle-header" onclick="this.closest('.collapsible-card').classList.toggle('card-collapsed')">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="card-toggle-btn">▼</span>
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Command Intent & Execution Analysis</span>
        </div>
        <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
      </div>
      <div class="card-content-body">
        <div class="ai-section">
          <div class="ai-section-title">Intent & Objective:</div>
          <div class="ai-intent-text">${escapeHtml(intent)}</div>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Technical Purpose:</div>
          <div class="ai-gains-text" style="color: #38bdf8;">${escapeHtml(techPurpose)}</div>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Outcome Analysis:</div>
          <div class="ai-gains-text" style="color: #a7f3d0;">${escapeHtml(outcome)}</div>
        </div>
        ${summaryHtml}
      </div>
    </div>
  `;
}

export async function triggerExplainCommand(ev, slotEl, buttonEl) {
  state.analyzingEventIds.add(ev.id);
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> Explaining…';
  }
  if (slotEl) {
    slotEl.innerHTML = `
      <div class="ai-progress-track">
        <div class="ai-progress-fill"></div>
      </div>
      <div class="ai-progress-status">Explaining command intent & execution analysis…</div>
    `;
  }

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-exec/${ev.id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.error || 'Command explanation failed');
    }

    ev.ai_summary = data.ai_summary;
    if (slotEl) {
      slotEl.innerHTML = createExecAiCardHtml(data.ai_summary);
    }
    if (buttonEl) {
      buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> Re-Explain with AI';
    }
    showToast(`Command explanation complete for #${ev.id}!`, 'success');
  } catch (err) {
    console.error('Command explanation error:', err);
    showToast(`Explanation failed: ${err.message}`, 'error');
    if (slotEl) {
      slotEl.innerHTML = createExecAiCardHtml(ev.ai_summary);
    }
    if (buttonEl) {
      buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> ' + (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI');
    }
  } finally {
    state.analyzingEventIds.delete(ev.id);
    if (buttonEl) buttonEl.disabled = false;
  }
}

export function renderTimeline(events, gitEvents = []) {
  if (!DOM.timelineContainer) return;
  const query = (DOM.timelineSearchInput?.value || '').trim().toLowerCase();
  if (!events || events.length === 0) {
    DOM.timelineContainer.dataset.signature = 'empty_' + query;
    DOM.timelineContainer.innerHTML = query
      ? '<div class="empty-state">No events match your search query.</div>'
      : '<div class="empty-state">No events recorded for this project yet.</div>';
    return;
  }

  const signature = `${state.timelineFilter}_${query}_${events.length}_` + events.map((e) => `${e.id}_${e.event_type || 'EDIT'}_${Boolean(e.ai_summary)}_${state.analyzingEventIds.has(e.id)}`).join('|');
  if (DOM.timelineContainer.dataset.signature === signature) {
    return;
  }
  DOM.timelineContainer.dataset.signature = signature;

  const fragment = document.createDocumentFragment();
  const orderedEvents = events.slice().reverse();

  orderedEvents.forEach((ev) => {
    const type = ev.event_type || 'EDIT';

    if (type === 'EDIT') {
      const card = document.createElement('div');
      const isRolledBack = (ev.is_rolled_back === 1 || ev.is_rolled_back === true || ev.is_rolled_back === '1');
      card.className = 'timeline-card' + (isRolledBack ? ' rolled-back' : '');

      const entrypointFile = ev.first_file_touched || (ev.patches && ev.patches[0] ? ev.patches[0].file_path : null);
      const patchList = ev.patches || [];

      const gitMatch = gitEvents.find((g) => g.id === ev.id + 1 || (g.summary && g.summary.includes(ev.summary)));
      let gitTagHtml = '';
      if (gitMatch && gitMatch.summary) {
        const m = gitMatch.summary.match(/Micro-commit ([a-f0-9]{7})/);
        if (m) {
          gitTagHtml = `<span class="badge" title="${escapeHtml(gitMatch.summary)}">📦 Git ${m[1]}</span>`;
        }
      }

      const fileChipsHtml = patchList
        .map((p) => {
          const isEntry = p.file_path === entrypointFile;
          return `<span class="file-chip ${isEntry ? 'entrypoint' : ''}">${escapeHtml(p.file_path)}</span>`;
        })
        .join('');

      const isAnalyzing = state.analyzingEventIds.has(ev.id);
      const aiSlotContent = isAnalyzing
        ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Synthesizing patch diffs & analyzing intent…</div>`
        : createAiCardHtml(ev.ai_summary);

      const burstLabelText = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
      const summaryClean = scrubSensitiveText(ev.summary || 'Multi-file edit burst');

      const rollbackButtonHtml = isRolledBack
        ? `<button class="btn btn-secondary btn-sm" disabled style="opacity: 0.6; cursor: not-allowed;">Rolled Back</button>`
        : `<button class="btn btn-danger-outline btn-sm btn-rollback-burst" data-event-id="${ev.id}" title="Rollback this burst: undo git micro-commit and move burst to trash">
             [ ↶ Rollback Burst ]
           </button>`;

      card.innerHTML = `
        <div class="timeline-card-header">
          <div class="timeline-badge-group">
            <span class="burst-badge">${escapeHtml(burstLabelText)}</span>
            ${isRolledBack ? '<span class="badge badge-rolledback">Rolled Back</span>' : ''}
            ${gitTagHtml}
            ${entrypointFile ? `<span class="entrypoint-badge">Entrypoint: ${escapeHtml(entrypointFile)}</span>` : ''}
          </div>
          <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
        </div>
        <div class="timeline-summary">${escapeHtml(summaryClean)}</div>
        <div class="timeline-files-list">
          ${fileChipsHtml || '<span class="empty-hint">No files touched</span>'}
        </div>
        <div class="ai-card-slot" id="ai-card-slot-${ev.id}">
          ${aiSlotContent}
        </div>
        <div class="timeline-footer">
          <span class="timeline-meta">${patchList.length} file(s) patched in this burst</span>
          <div style="display: flex; gap: 8px;">
            ${rollbackButtonHtml}
            <button class="btn btn-ai btn-sm btn-analyze-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
              <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Analyzing…' : (ev.ai_summary ? 'Re-Analyze with AI' : 'Analyze with AI')}
            </button>
            <button class="btn btn-secondary btn-sm btn-inspect-diff" data-event-id="${ev.id}">
              Inspect Diffs &rarr;
            </button>
          </div>
        </div>
      `;

      const btnRollback = card.querySelector('.btn-rollback-burst');
      if (btnRollback) {
        btnRollback.addEventListener('click', async () => {
          if (!confirm(`Are you sure you want to rollback and discard ${burstLabelText}? Any micro-commit will be undone and changes moved to Trash.`)) {
            return;
          }
          btnRollback.disabled = true;
          const origHtml = btnRollback.innerHTML;
          btnRollback.innerHTML = '<span class="spinner-sm"></span> Rolling back…';
          showRollbackTray(state.selectedProject, 'running');
          try {
            const data = await apiRollbackBurst(state.selectedProject, ev.id);
            if (!data.success) {
              throw new Error(data.message || data.error || 'Rollback failed');
            }
            showRollbackTray(state.selectedProject, 'completed', 'Workspace reverted to selected burst');
            showToast('✓ Burst rolled back and moved to Trash.', 'success');
            if (_onUpdateTrashCount) await _onUpdateTrashCount();
            await loadProjectEvents(state.selectedProject);
            await loadProjectSessions(state.selectedProject);
            if (_onRefreshProjects) await _onRefreshProjects();
          } catch (err) {
            console.error('Rollback error:', err);
            showRollbackTray(state.selectedProject, 'failed', err.message);
            showToast(`Rollback failed: ${err.message}`, 'error');
          } finally {
            btnRollback.disabled = false;
            btnRollback.innerHTML = origHtml;
          }
        });
      }

      card.querySelector('.btn-inspect-diff').addEventListener('click', () => {
        if (_onSelectTab) _onSelectTab('diffs');
        if (ev.session_id) {
          state.selectedSessionId = ev.session_id;
          if (DOM.diffSessionSelect) DOM.diffSessionSelect.value = String(ev.session_id);
          onDiffSessionSelected(ev.session_id, ev.id);
        } else {
          onDiffBurstSelected(ev.id);
        }
      });

      const btnAi = card.querySelector('.btn-analyze-ai');
      btnAi.addEventListener('click', async () => {
        state.analyzingEventIds.add(ev.id);
        btnAi.disabled = true;
        const originalText = btnAi.innerHTML;
        btnAi.innerHTML = '<span class="ai-sparkle">✨</span> Analyzing…';

        const slot = card.querySelector(`#ai-card-slot-${ev.id}`);
        if (slot) {
          slot.innerHTML = `
            <div class="ai-progress-track">
              <div class="ai-progress-fill"></div>
            </div>
            <div class="ai-progress-status">Synthesizing patch diffs & analyzing intent…</div>
          `;
        }

        try {
          const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-event/${ev.id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          const data = await res.json();
          if (!res.ok || !data.success) {
            throw new Error(data.message || data.error || 'AI analysis failed');
          }

          ev.ai_summary = data.ai_summary;
          if (slot) {
            slot.innerHTML = createAiCardHtml(data.ai_summary);
          }
          btnAi.innerHTML = '<span class="ai-sparkle">✨</span> Re-Analyze with AI';
          showToast(`AI Analysis complete for Burst #${ev.id}!`, 'success');
        } catch (err) {
          console.error('AI Analysis error:', err);
          showToast(`AI Analysis failed: ${err.message}`, 'error');
          if (slot) {
            slot.innerHTML = createAiCardHtml(ev.ai_summary);
          }
          btnAi.innerHTML = originalText;
        } finally {
          state.analyzingEventIds.delete(ev.id);
          btnAi.disabled = false;
        }
      });

      fragment.appendChild(card);
    } else if (type === 'READ') {
      const card = document.createElement('div');
      card.className = 'timeline-card read-event-card';
      const fileClean = scrubSensitiveText(ev.first_file_touched || 'unknown');
      const summaryClean = scrubSensitiveText(ev.summary || 'Process inspected file handle');
      card.innerHTML = `
        <div class="timeline-card-header">
          <div class="timeline-badge-group">
            <span class="badge badge-read" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">👁️ READ #${ev.id}</span>
          </div>
          <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
        </div>
        <div class="timeline-summary">Inspected file: <code class="mono" style="color: var(--accent);">${escapeHtml(fileClean)}</code></div>
        <div class="timeline-footer">
          <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
        </div>
      `;
      fragment.appendChild(card);
    } else if (type === 'EXEC') {
      const isSuccess = !ev.summary.includes('failed') && !ev.summary.includes('exit: 1') && !ev.summary.includes('exit: 2');
      const badgeClass = isSuccess ? 'success' : 'failed';
      const badgeText = isSuccess ? 'SUCCESS' : 'FAILED';
      let executorBadge = '';
      if (ev.executor === 'AI_AGENT') {
        executorBadge = '<span class="badge-ai-exec" title="Automated AI Agent Execution">🤖 AI Action</span>';
      } else if (ev.executor === 'HUMAN_DEV') {
        executorBadge = '<span class="badge-human-exec" title="Interactive Developer Terminal Execution">👤 Human Dev</span>';
      }

      const cmdClean = scrubSensitiveText(ev.first_file_touched || ev.summary || '');
      const summaryClean = scrubSensitiveText(ev.summary || 'Terminal process executed');

      const isAnalyzing = state.analyzingEventIds.has(ev.id);
      const aiSlotContent = isAnalyzing
        ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Explaining command intent & execution analysis…</div>`
        : createExecAiCardHtml(ev.ai_summary);

      const card = document.createElement('div');
      card.className = 'timeline-card exec-event-card';
      card.innerHTML = `
        <div class="timeline-card-header">
          <div class="timeline-badge-group">
            <span class="badge badge-exec" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3);">💻 EXEC #${ev.id}</span>
            <span class="badge badge-session">Session ${ev.session_id ?? '1'}</span>
            <span class="badge badge-burst">Burst #${ev.associated_burst_id || 'Active'}</span>
            ${executorBadge}
            <span class="exec-badge ${badgeClass}">${badgeText}</span>
          </div>
          <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
        </div>
        <div class="exec-cmd-box" style="margin: 8px 0;">$ ${escapeHtml(cmdClean)}</div>
        <div class="ai-card-slot" id="ai-exec-slot-${ev.id}">
          ${aiSlotContent}
        </div>
        <div class="timeline-footer">
          <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
          <button class="btn btn-ai btn-sm btn-analyze-exec-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
            <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Explaining…' : (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI')}
          </button>
        </div>
      `;

      const btnAi = card.querySelector('.btn-analyze-exec-ai');
      btnAi.addEventListener('click', async () => {
        await triggerExplainCommand(ev, card.querySelector(`#ai-exec-slot-${ev.id}`), btnAi);
      });

      fragment.appendChild(card);
    }
  });

  DOM.timelineContainer.innerHTML = '';
  DOM.timelineContainer.appendChild(fragment);
}

export function renderTimelineFromState() {
  if (!DOM.timelineContainer) return;
  const gitEvents = state.eventsData.filter((ev) => ev.event_type === 'GIT');
  let eventsToRender = state.eventsData;

  if (state.timelineFilter === 'EDIT') {
    eventsToRender = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
  } else if (state.timelineFilter === 'READ') {
    eventsToRender = state.eventsData.filter((ev) => ev.event_type === 'READ');
  } else if (state.timelineFilter === 'EXEC') {
    eventsToRender = state.eventsData.filter((ev) => ev.event_type === 'EXEC');
  } else {
    eventsToRender = state.eventsData.filter((ev) => ev.event_type !== 'GIT');
  }

  const query = (DOM.timelineSearchInput?.value || '').trim().toLowerCase();
  if (query) {
    eventsToRender = eventsToRender.filter((ev) => {
      const summary = (ev.summary || '').toLowerCase();
      const firstFile = (ev.first_file_touched || '').toLowerCase();
      const burstLabel = (ev.session_burst_label || '').toLowerCase();
      const burstNum = String(ev.burst_num || ev.id || '');
      const executor = (ev.executor || '').toLowerCase();
      const patches = (ev.patches || []).map((p) => (p.file_path || '').toLowerCase()).join(' ');

      const gitMatch = gitEvents.find((g) => g.id === ev.id + 1 || (g.summary && g.summary.includes(ev.summary)));
      const gitSummary = (gitMatch?.summary || '').toLowerCase();

      return summary.includes(query) ||
        firstFile.includes(query) ||
        burstLabel.includes(query) ||
        burstNum.includes(query) ||
        executor.includes(query) ||
        patches.includes(query) ||
        gitSummary.includes(query);
    });
  }

  renderTimeline(eventsToRender, gitEvents);
}

export function setupTimelineFilters() {
  if (!DOM.timelineFilterGroup || !DOM.timelineFilterPills) return;
  DOM.timelineFilterPills.forEach((pill) => {
    pill.addEventListener('click', () => {
      DOM.timelineFilterPills.forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      state.timelineFilter = pill.dataset.filter || 'all';
      renderTimelineFromState();
    });
  });
}

export function renderAnalyzedFiles(readEvents) {
  if (!DOM.timelineReadsList) return;
  DOM.timelineReadsList.innerHTML = '';
  if (!readEvents || readEvents.length === 0) {
    DOM.timelineReadsList.innerHTML = '<div class="empty-hint">No file inspection events recorded yet</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  readEvents.slice(-50).reverse().forEach((ev) => {
    const chip = document.createElement('div');
    chip.className = 'read-chip';
    const file = scrubSensitiveText(ev.first_file_touched || 'unknown');
    const summary = scrubSensitiveText(ev.summary || '');
    chip.innerHTML = `<span>${escapeHtml(file)}</span> <span class="proc-tag">${escapeHtml(summary)}</span>`;
    fragment.appendChild(chip);
  });
  DOM.timelineReadsList.appendChild(fragment);
}

export function renderTerminalFromState() {
  const execEvents = state.eventsData.filter((ev) => ev.event_type === 'EXEC');
  const query = (DOM.terminalSearchInput?.value || '').trim().toLowerCase();
  let eventsToRender = execEvents;
  if (query) {
    eventsToRender = execEvents.filter((ev) => {
      const cmd = (ev.first_file_touched || ev.summary || '').toLowerCase();
      const summary = (ev.summary || '').toLowerCase();
      const idStr = String(ev.id);
      const sessStr = String(ev.session_id || '');
      const burstStr = String(ev.associated_burst_id || '');
      const execStr = (ev.executor || '').toLowerCase();
      return cmd.includes(query) || summary.includes(query) || idStr.includes(query) || sessStr.includes(query) || burstStr.includes(query) || execStr.includes(query);
    });
  }
  renderTerminal(eventsToRender);
}

export function renderTerminal(execEvents) {
  if (!DOM.terminalFeedContainer) return;
  DOM.terminalFeedContainer.innerHTML = '';
  const query = (DOM.terminalSearchInput?.value || '').trim().toLowerCase();
  if (!execEvents || execEvents.length === 0) {
    DOM.terminalFeedContainer.innerHTML = query
      ? '<div class="empty-state">No terminal commands match your search.</div>'
      : '<div class="empty-state">No terminal commands recorded yet.</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  execEvents.slice().reverse().forEach((ev) => {
    const card = document.createElement('div');
    card.className = 'exec-card';

    const isSuccess = !ev.summary.includes('failed') && !ev.summary.includes('exit: 1') && !ev.summary.includes('exit: 2');
    const badgeClass = isSuccess ? 'success' : 'failed';
    const badgeText = isSuccess ? 'SUCCESS' : 'FAILED';
    let executorBadge = '';
    if (ev.executor === 'AI_AGENT') {
      executorBadge = '<span class="badge-ai-exec" title="Automated AI Agent Execution">🤖 AI Action</span>';
    } else if (ev.executor === 'HUMAN_DEV') {
      executorBadge = '<span class="badge-human-exec" title="Interactive Developer Terminal Execution">👤 Human Dev</span>';
    }

    const cmdClean = scrubSensitiveText(ev.first_file_touched || ev.summary || '');
    const summaryClean = scrubSensitiveText(ev.summary || '');

    const isAnalyzing = state.analyzingEventIds.has(ev.id);
    const aiSlotContent = isAnalyzing
      ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Explaining command intent & execution analysis…</div>`
      : createExecAiCardHtml(ev.ai_summary);

    card.innerHTML = `
      <div class="exec-header">
        <div class="exec-meta-group">
          <span class="exec-badge ${badgeClass}">${badgeText}</span>
          ${executorBadge}
          <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
        </div>
        <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
      </div>
      <div class="exec-cmd-box">$ ${escapeHtml(cmdClean)}</div>
      <div class="ai-card-slot" id="ai-term-slot-${ev.id}">
        ${aiSlotContent}
      </div>
      <div class="exec-footer">
        <span>Command Execution #${ev.id} • Logged in session.db</span>
        <button class="btn btn-ai btn-sm btn-analyze-term-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
          <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Explaining…' : (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI')}
        </button>
      </div>
    `;

    const btnAi = card.querySelector('.btn-analyze-term-ai');
    btnAi.addEventListener('click', async () => {
      await triggerExplainCommand(ev, card.querySelector(`#ai-term-slot-${ev.id}`), btnAi);
    });

    fragment.appendChild(card);
  });
  DOM.terminalFeedContainer.appendChild(fragment);
}

export async function loadProjectEvents(projectName) {
  if (!projectName) return;
  try {
    const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/events`);
    state.eventsData = data.events || [];
    state.sessionsData = data.sessions || [];

    const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    const readEvents = state.eventsData.filter((ev) => ev.event_type === 'READ');
    const execEvents = state.eventsData.filter((ev) => ev.event_type === 'EXEC');

    if (DOM.navEventCount) DOM.navEventCount.textContent = editEvents.length;
    if (DOM.navExecCount) DOM.navExecCount.textContent = execEvents.length;
    if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = readEvents.length;
    if (DOM.dashEventCount) DOM.dashEventCount.textContent = editEvents.length;

    const curProj = getSelectedProjectInfo();
    if (curProj && curProj.events_count !== editEvents.length) {
      curProj.events_count = editEvents.length;
      if (_onRefreshProjects) _onRefreshProjects(false);
    }

    let patchCount = 0;
    editEvents.forEach((ev) => {
      patchCount += ev.patches ? ev.patches.length : 0;
    });
    if (DOM.dashPatchCount) DOM.dashPatchCount.textContent = patchCount;

    renderTimelineFromState();
    renderAnalyzedFiles(readEvents);
    renderTerminalFromState();
    if (_onRenderDashboardRecentEvents) _onRenderDashboardRecentEvents(editEvents);
    setupDiffViewerHierarchy(editEvents);
  } catch (err) {
    console.warn(`Could not load events for ${projectName}:`, err);
    state.eventsData = [];
    state.sessionsData = [];
    if (DOM.navEventCount) DOM.navEventCount.textContent = '0';
    if (DOM.navExecCount) DOM.navExecCount.textContent = '0';
    if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = '0';
    if (DOM.dashEventCount) DOM.dashEventCount.textContent = '0';
    if (DOM.dashPatchCount) DOM.dashPatchCount.textContent = '0';
    renderTimelineFromState();
    renderAnalyzedFiles([]);
    renderTerminal([]);
    if (_onRenderDashboardRecentEvents) _onRenderDashboardRecentEvents([]);
    setupDiffViewerHierarchy([]);
  }
}

export async function loadProjectSessions(projectName) {
  if (!projectName) return;
  try {
    const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/sessions`);
    state.sessionsData = data.sessions || [];
    const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    setupDiffViewerHierarchy(editEvents);
  } catch (err) {
    console.warn(`Could not load sessions for ${projectName}:`, err);
  }
}

export function initTimelineListeners() {
  setupTimelineFilters();
  if (DOM.timelineSearchInput) {
    DOM.timelineSearchInput.addEventListener('input', () => {
      renderTimelineFromState();
    });
  }
  if (DOM.terminalSearchInput) {
    DOM.terminalSearchInput.addEventListener('input', () => {
      renderTerminalFromState();
    });
  }
  if (DOM.btnRefreshTimeline) {
    DOM.btnRefreshTimeline.addEventListener('click', () => loadProjectEvents(state.selectedProject));
  }
  if (DOM.btnRefreshTerminal) {
    DOM.btnRefreshTerminal.addEventListener('click', () => loadProjectEvents(state.selectedProject));
  }
}
