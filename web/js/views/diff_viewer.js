/**
 * SPD Analysis Engine — 3-Tier Hierarchical Diff Viewer (Session -> Burst -> File)
 * With Anti-Flicker DOM Preservation & Per-File AI Code Inspection
 */
import { DOM } from '../dom.js';
import { state } from '../state.js';
import { escapeHtml, formatISTTime } from '../utils.js';
import { analyzeEvent, analyzeFile, analyzeBurstOverview, fetchFileAnalysis, fetchBurstOverview } from '../api.js';
import { showToast, updateGlobalTray, startTrayCooldownCountdown, hideGlobalTray } from '../toasts.js';

// Anti-flicker & cache tracking
let currentRenderedBurstId = null;
let currentRenderedFile = null;
let currentRenderedAiSummaryHash = null;
export let currentActiveAiMode = 'file'; // 'file' | 'batch' | 'overview' | 'none'
export const cachedFileAnalyses = new Map();
export const fileAnalysesCache = cachedFileAnalyses;
export const cachedBurstOverviews = new Map();
export const burstOverviewsCache = cachedBurstOverviews;
const checkedFilePaths = new Set();

export function isFileAnalysisCached(sessId, burstNum, eventId, filePath) {
  if (!filePath) return false;
  const keys = [
    `${sessId}::${burstNum}::${filePath}`,
    `${sessId}::${eventId}::${filePath}`,
    `${burstNum}::${filePath}`,
    `${eventId}::${filePath}`,
  ];
  return keys.some((k) => !!(cachedFileAnalyses.get(k) || cachedFileAnalyses[k]));
}

export function isBurstOverviewCached(sessId, burstNum, eventId) {
  const keys = [
    `${sessId}::${burstNum}`,
    `${sessId}::${eventId}`,
    String(burstNum),
    String(eventId),
  ];
  return keys.some((k) => {
    const d = cachedBurstOverviews.get(k) || cachedBurstOverviews[k];
    return !!(d && (d.overview || d.burst_summary));
  });
}

export function cacheFileAnalysisRecord(sessId, burstNum, eventId, filePath, analysis) {
  if (!filePath || !analysis) return;
  const keys = [
    `${sessId}::${burstNum}::${filePath}`,
    `${sessId}::${eventId}::${filePath}`,
    `${burstNum}::${filePath}`,
    `${eventId}::${filePath}`,
  ];
  keys.forEach((k) => {
    cachedFileAnalyses.set(k, analysis);
    cachedFileAnalyses[k] = analysis;
  });
}

export function cacheBurstOverviewRecord(sessId, burstNum, eventId, overviewData) {
  if (!overviewData) return;
  const keys = [
    `${sessId}::${burstNum}`,
    `${sessId}::${eventId}`,
    String(burstNum),
    String(eventId),
  ];
  keys.forEach((k) => {
    cachedBurstOverviews.set(k, overviewData);
    cachedBurstOverviews[k] = overviewData;
  });
}

/**
 * Reconcile triple button "Re-" toggles and labels based on cache state and checked files.
 * Enforces strict mutual exclusivity: only the button matching currentActiveAiMode may show [ 🔄 Re-... ].
 */
export function updateDiffActionButtonsState() {
  const selectedSessionId = state.selectedSessionId || 1;
  const ev = state.eventsData?.find((x) => x.id === state.selectedEventId);
  const selectedEventId = state.selectedEventId || 0;
  const selectedBurstNum = ev?.burst_num || selectedEventId;
  const selectedFilePath = state.selectedFilePath;

  // 1. btnAnalyzeActiveFile: [ 📄 Analyze Active File (Deep) ] <-> [ 🔄 Re-Analyze Active File ]
  const btnActive = DOM.btnAnalyzeActiveFile;
  if (btnActive && !btnActive.disabled) {
    const isCached = selectedFilePath && isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedEventId, selectedFilePath);
    if (currentActiveAiMode === 'file' && isCached) {
      btnActive.innerHTML = '<span>🔄</span> Re-Analyze Active File';
      btnActive.title = 'Re-analyze this file diff with AI (bypasses cache)';
      btnActive.dataset.action = 'reanalyze';
    } else {
      btnActive.innerHTML = '<span>📄</span> Analyze Active File (Deep)';
      btnActive.title = 'Run deep AI code inspection for this file diff';
      btnActive.dataset.action = 'analyze';
    }
  }

  // 2. btnAnalyzeCheckedFiles / btnBatchAnalyzeFiles: [ 📑 Analyze Checked Files (Deep Batch) ] <-> [ 🔄 Re-Analyze Checked Files ]
  const btnBatch = DOM.btnAnalyzeCheckedFiles || DOM.btnBatchAnalyzeFiles;
  if (btnBatch && !btnBatch.disabled) {
    const hasChecked = checkedFilePaths.size > 0;
    const allCheckedCached = hasChecked && Array.from(checkedFilePaths).every((fp) =>
      isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedEventId, fp)
    );
    if (currentActiveAiMode === 'batch' && allCheckedCached) {
      btnBatch.innerHTML = '<span>🔄</span> Re-Analyze Checked Files';
      btnBatch.title = 'Re-analyze checked files with AI (bypasses cache)';
      btnBatch.dataset.action = 'reanalyze';
    } else {
      btnBatch.innerHTML = '<span>📑</span> Analyze Checked Files (Deep Batch)';
      btnBatch.title = 'Sequentially inspects checked files, retrieving cached files with 0 API calls';
      btnBatch.dataset.action = 'analyze';
    }
  }

  // 3. btnOverviewBurstFiles / btnOverviewFiles: [ 🌐 Overview Burst Files ] <-> [ 🔄 Re-Overview Burst Files ]
  const btnOverview = DOM.btnOverviewBurstFiles || DOM.btnOverviewFiles;
  if (btnOverview && !btnOverview.disabled) {
    const isBurstCached = isBurstOverviewCached(selectedSessionId, selectedBurstNum, selectedEventId);
    if (currentActiveAiMode === 'overview' && isBurstCached) {
      btnOverview.innerHTML = '<span>🔄</span> Re-Overview Burst Files';
      btnOverview.title = 'Re-generate high-level burst overview with AI (bypasses cache)';
      btnOverview.dataset.action = 'reoverview';
    } else {
      btnOverview.innerHTML = '<span>🌐</span> Overview Burst Files';
      btnOverview.title = 'Generates a compact, high-level overview list summarizing the purpose of every changed file in the burst';
      btnOverview.dataset.action = 'overview';
    }
  }
}

/**
 * Render Macro Burst AI Synthesis Card HTML (Collapsible).
 */
export function createAiCardHtml(ai) {
  if (!ai) return '';
  const intent = ai.intent || 'No intent description provided.';
  const archImpact = ai.architecture_impact || '';
  const gains = ai.functionality_gained || 'No functional gains specified.';
  const provider = ai.provider ? `Provider: ${ai.provider}` : 'AI Intelligence';
  const cachedBadge = ai.cached ? ' • (Cached)' : '';

  let archHtml = '';
  if (archImpact) {
    archHtml = `
      <div class="ai-section">
        <div class="ai-section-title">Architectural Impact:</div>
        <div class="ai-gains-text" style="color: #38bdf8;">${escapeHtml(archImpact)}</div>
      </div>
    `;
  }

  let summaryHtml = '';
  const modifications = (Array.isArray(ai.key_modifications) && ai.key_modifications.length > 0)
    ? ai.key_modifications
    : (Array.isArray(ai.summary) && ai.summary.length > 0 ? ai.summary : []);

  if (modifications.length > 0) {
    summaryHtml = `
      <div class="ai-section">
        <div class="ai-section-title">Key Modifications:</div>
        <ul class="ai-summary-list">
          ${modifications.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}
        </ul>
      </div>
    `;
  }

  return `
    <div class="ai-analysis-card collapsible-card">
      <div class="ai-card-header card-toggle-header" onclick="this.closest('.collapsible-card').classList.toggle('card-collapsed')">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="card-toggle-btn">▼</span>
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Deep Architectural Synthesis</span>
        </div>
        <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
      </div>
      <div class="card-content-body">
        <div class="ai-section">
          <div class="ai-section-title">Architectural Intent & Rationale:</div>
          <div class="ai-intent-text">${escapeHtml(intent)}</div>
        </div>
        ${archHtml}
        <div class="ai-section">
          <div class="ai-section-title">Functionality Gained:</div>
          <div class="ai-gains-text">${escapeHtml(gains)}</div>
        </div>
        ${summaryHtml}
      </div>
    </div>
  `;
}

/**
 * Render Dedicated Per-File AI Code Inspection Card HTML (Collapsible).
 */
export function createFileAiCardHtml(analysis) {
  if (!analysis) return '';
  const summary = analysis.summary || 'File diff analyzed.';
  const whyModified = analysis.why_modified || '';
  const risk = (analysis.risk_level || 'LOW').toUpperCase();
  const riskClass = risk === 'HIGH' ? 'risk-high' : (risk === 'MEDIUM' ? 'risk-medium' : 'risk-low');
  const provider = analysis.provider ? `Provider: ${analysis.provider}` : 'AI Intelligence';
  const cachedBadge = analysis.cached ? ' • (Cached)' : '';
  const modeBadge = analysis.mode ? `<span class="badge" style="font-size:0.68rem; background:rgba(255,255,255,0.08);">${escapeHtml(analysis.mode.toUpperCase())}</span>` : '';

  const symbols = Array.isArray(analysis.changed_symbols) ? analysis.changed_symbols : [];
  const breaking = Array.isArray(analysis.breaking_changes) ? analysis.breaking_changes : [];

  let symbolsHtml = '';
  if (symbols.length > 0) {
    symbolsHtml = `
      <div class="file-ai-section">
        <div class="file-ai-label">Changed Symbols & Interfaces:</div>
        <div class="file-ai-symbols">
          ${symbols.map((s) => `<span class="symbol-tag">${escapeHtml(s)}</span>`).join('')}
        </div>
      </div>
    `;
  }

  let breakingHtml = '';
  if (breaking.length > 0 && !(breaking.length === 1 && breaking[0].toLowerCase().includes('none'))) {
    breakingHtml = `
      <div class="file-ai-section">
        <div class="file-ai-label" style="color: #f87171;">Potential Breaking Changes / Regressions:</div>
        <div class="file-ai-symbols">
          ${breaking.map((b) => `<span class="breaking-tag">⚠️ ${escapeHtml(b)}</span>`).join('')}
        </div>
      </div>
    `;
  } else if (breaking.length > 0) {
    breakingHtml = `
      <div class="file-ai-section">
        <div class="file-ai-label">Breaking Changes:</div>
        <div class="file-ai-text" style="color: #34d399; font-size: 0.76rem;">✓ ${escapeHtml(breaking[0])}</div>
      </div>
    `;
  }

  return `
    <div class="file-ai-card collapsible-card">
      <div class="file-ai-header card-toggle-header" onclick="this.closest('.collapsible-card').classList.toggle('card-collapsed')">
        <div class="file-ai-title">
          <span class="card-toggle-btn">▼</span>
          <span>📄</span>
          <span>Per-File Code Inspection: <code>${escapeHtml(analysis.file_path || '')}</code></span>
          ${modeBadge}
        </div>
        <div class="file-ai-meta">
          <span class="risk-badge ${riskClass}">Risk: ${escapeHtml(risk)}</span>
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
      </div>
      <div class="card-content-body">
        <div class="file-ai-section">
          <div class="file-ai-label">Modification Rationale:</div>
          <div class="file-ai-text">${escapeHtml(whyModified || summary)}</div>
        </div>
        ${symbolsHtml}
        ${breakingHtml}
      </div>
    </div>
  `;
}

/**
 * Render Burst Multi-File Overview Card HTML (Collapsible).
 */
export function createBurstOverviewCardHtml(overviewData) {
  if (!overviewData || !overviewData.overview) return '';
  const fileCount = overviewData.file_count || Object.keys(overviewData.overview).length || 0;
  const provider = overviewData.provider ? `Provider: ${overviewData.provider}` : 'AI Intelligence';
  const cachedBadge = overviewData.cached ? ' • (Cached)' : '';
  const entries = Object.entries(overviewData.overview);

  const filesHtml = entries.map(([file, desc]) => `
    <div class="overview-file-item">
      <span class="overview-file-name">📄 ${escapeHtml(file)}</span>
      <span class="overview-file-desc">${escapeHtml(desc)}</span>
    </div>
  `).join('');

  return `
    <div class="file-ai-card burst-overview-card collapsible-card" style="border-left-color: #38bdf8;">
      <div class="file-ai-header card-toggle-header" onclick="this.closest('.collapsible-card').classList.toggle('card-collapsed')">
        <div class="file-ai-title">
          <span class="card-toggle-btn">▼</span>
          <span>🌐</span>
          <span>Burst Multi-File Overview • ${fileCount} File(s)</span>
        </div>
        <div class="file-ai-meta">
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
      </div>
      <div class="card-content-body">
        <div class="overview-files-list">
          ${filesHtml || '<div class="empty-hint">No file summaries available.</div>'}
        </div>
      </div>
    </div>
  `;
}

/**
 * Macro synthesis card stub for backward compatibility.
 * Diff Viewer now relies purely on file-level and overview AI inspection;
 * macro synthesis is presented exclusively on the Event Timeline.
 */
export function renderAISummaryCard(ev, force = false) {
  if (DOM.diffAiSlot) {
    DOM.diffAiSlot.innerHTML = '';
  }
}

/**
 * Immediately populate the session select dropdown and default to the latest session.
 */
export function populateSessionsDropdown(sessions) {
  if (!DOM.diffSessionSelect) return;
  DOM.diffSessionSelect.innerHTML = '';
  if (!sessions || sessions.length === 0) {
    DOM.diffSessionSelect.innerHTML = '<option value="">(No sessions recorded)</option>';
    return;
  }
  sessions.forEach((s, idx) => {
    const sessId = s.session_id ?? s.id ?? (idx + 1);
    const bCount = s.burst_count ?? s.bursts_count ?? 0;
    const opt = document.createElement('option');
    opt.value = String(sessId);
    const label = `Session ${sessId}` + (s.is_active ? ' (Active)' : ` (${bCount} bursts)`);
    opt.textContent = label;
    DOM.diffSessionSelect.appendChild(opt);
  });
}

export const renderSessionDropdown = populateSessionsDropdown;

export function setupDiffViewerHierarchy(editEvents, force = false) {
  if (!DOM.diffSessionSelect) return;

  let events = editEvents;
  if (!events || events.length === 0) {
    events = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
  }

  // Find or reconstruct sessions
  let sessions = (state.sessionsData && state.sessionsData.length > 0) ? state.sessionsData.slice() : null;
  if (!sessions || sessions.length === 0) {
    const sMap = new Map();
    events.forEach((ev) => {
      const sid = ev.session_id || 1;
      if (!sMap.has(sid)) {
        sMap.set(sid, { session_id: sid, id: sid, is_active: false, burst_count: 0 });
      }
      sMap.get(sid).burst_count += 1;
    });
    sessions = Array.from(sMap.values());
  }

  // Filter out phantom sessions
  sessions = sessions.filter((s) => s.is_active || (s.burst_count ?? s.bursts_count ?? 0) > 0);

  if (sessions.length === 0) {
    populateSessionsDropdown([]);
    if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '<option value="">(No edit bursts available)</option>';
    if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = '0';
    if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
    if (DOM.fileSelectAllRow) DOM.fileSelectAllRow.style.display = 'none';
    if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
    if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No edit events available to inspect for this project.</div>';
    if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
    if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
    if (DOM.btnAnalyzeActiveFile) DOM.btnAnalyzeActiveFile.style.display = 'none';
    if (DOM.btnAnalyzeCheckedFiles) DOM.btnAnalyzeCheckedFiles.style.display = 'none';
    if (DOM.btnOverviewBurstFiles) DOM.btnOverviewBurstFiles.style.display = 'none';
    currentRenderedBurstId = null;
    currentRenderedFile = null;
    currentRenderedAiSummaryHash = null;
    return;
  }

  sessions.sort((a, b) => ((a.session_id ?? a.id ?? 1) - (b.session_id ?? b.id ?? 1)));

  // Always immediately populate session dropdown so it never hangs on (Loading sessions...)
  if (force || DOM.diffSessionSelect.options.length !== sessions.length || DOM.diffSessionSelect.querySelector('option[value=""]')) {
    populateSessionsDropdown(sessions);
  }

  let targetSessionId = null;
  if (state.selectedSessionId && sessions.some((s) => (s.session_id ?? s.id) === state.selectedSessionId)) {
    targetSessionId = state.selectedSessionId;
  } else {
    const lastS = sessions[sessions.length - 1];
    targetSessionId = lastS.session_id ?? lastS.id;
  }

  DOM.diffSessionSelect.value = String(targetSessionId);
  onDiffSessionSelected(targetSessionId, null, force);
}

export function onDiffSessionSelected(sessionId, preferredEventId = null, force = false) {
  const sid = parseInt(sessionId, 10);
  state.selectedSessionId = isNaN(sid) ? null : sid;

  // On Session Switch: Clear rendered file analysis card slot and update buttons
  currentActiveAiMode = 'file';
  if (DOM.diffFileAiSlot) {
    DOM.diffFileAiSlot.innerHTML = '';
  }
  updateDiffActionButtonsState();

  const allEditEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
  const sessionBursts = allEditEvents.filter((ev) => (ev.session_id || 1) === (state.selectedSessionId || 1));

  if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = sessionBursts.length;

  if (!DOM.diffBurstSelect) return;

  if (sessionBursts.length === 0) {
    DOM.diffBurstSelect.innerHTML = '<option value="">(No bursts in this session)</option>';
    if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
    if (DOM.fileSelectAllRow) DOM.fileSelectAllRow.style.display = 'none';
    if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
    if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this session.</div>';
    if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
    if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
    if (DOM.btnAnalyzeActiveFile) DOM.btnAnalyzeActiveFile.style.display = 'none';
    if (DOM.btnAnalyzeCheckedFiles) DOM.btnAnalyzeCheckedFiles.style.display = 'none';
    if (DOM.btnOverviewBurstFiles) DOM.btnOverviewBurstFiles.style.display = 'none';
    currentRenderedBurstId = null;
    currentRenderedFile = null;
    currentRenderedAiSummaryHash = null;
    return;
  }

  const optionsMatch = DOM.diffBurstSelect.options.length === sessionBursts.length &&
    sessionBursts.length > 0 &&
    DOM.diffBurstSelect.options[sessionBursts.length - 1]?.value === String(sessionBursts[sessionBursts.length - 1].id);

  if (force || !optionsMatch) {
    DOM.diffBurstSelect.innerHTML = '';
    sessionBursts.forEach((ev) => {
      const opt = document.createElement('option');
      opt.value = String(ev.id);
      const patchCount = ev.patches ? ev.patches.length : 0;
      const timeStr = formatISTTime(ev.timestamp);
      const commitTag = ev.commit_hash ? ` [${ev.commit_hash.slice(0, 7)}]` : '';
      const file = ev.first_file_touched || 'edit';
      const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
      opt.textContent = `${burstLabel} • ${timeStr} [${patchCount} file(s)]${commitTag} — ${file}`;
      DOM.diffBurstSelect.appendChild(opt);
    });
  }

  let targetEventId = null;
  if (preferredEventId && sessionBursts.some((ev) => ev.id === preferredEventId)) {
    targetEventId = preferredEventId;
  } else if (state.selectedEventId && sessionBursts.some((ev) => ev.id === state.selectedEventId)) {
    targetEventId = state.selectedEventId;
  } else {
    targetEventId = sessionBursts[sessionBursts.length - 1].id;
  }

  DOM.diffBurstSelect.value = String(targetEventId);
  onDiffBurstSelected(targetEventId, null, force);
}

function updateCheckedFilesState(totalCount) {
  if (DOM.checkedFilesCount) {
    DOM.checkedFilesCount.textContent = `${checkedFilePaths.size} selected`;
  }
  if (DOM.chkSelectAllFiles) {
    DOM.chkSelectAllFiles.checked = totalCount > 0 && checkedFilePaths.size === totalCount;
    DOM.chkSelectAllFiles.indeterminate = checkedFilePaths.size > 0 && checkedFilePaths.size < totalCount;
  }
  updateDiffActionButtonsState();
}

export function onDiffBurstSelected(eventId, preferredFilePath = null, force = false) {
  state.selectedEventId = parseInt(eventId, 10);
  const ev = state.eventsData.find((x) => x.id === state.selectedEventId);

  if (ev && ev.session_id && ev.session_id !== state.selectedSessionId) {
    state.selectedSessionId = ev.session_id;
    if (DOM.diffSessionSelect) DOM.diffSessionSelect.value = String(ev.session_id);
  }

  // On Burst Switch: Clear rendered file analysis card slot and reset mode
  currentActiveAiMode = 'file';
  if (DOM.diffFileAiSlot) {
    DOM.diffFileAiSlot.innerHTML = '';
  }

  const selectedSessionId = state.selectedSessionId || ev?.session_id || 1;
  const selectedEventId = state.selectedEventId;
  const selectedBurstNum = ev?.burst_num || selectedEventId;

  // Check burst overview cache in background if not already in memory
  if (!isBurstOverviewCached(selectedSessionId, selectedBurstNum, selectedEventId)) {
    const proj = state.selectedProject || state.activeProject;
    if (proj && selectedEventId) {
      fetchBurstOverview(proj, {
        session_id: selectedSessionId,
        burst_id: selectedBurstNum,
        burst_number: selectedBurstNum,
        event_id: selectedEventId,
      }).then((res) => {
        if (res && res.success && res.overview && Object.keys(res.overview).length > 0) {
          cacheBurstOverviewRecord(selectedSessionId, selectedBurstNum, selectedEventId, res);
          updateDiffActionButtonsState();
        }
      }).catch(() => {});
    }
  }

  updateDiffActionButtonsState();

  if (!ev || !ev.patches || ev.patches.length === 0) {
    state.selectedFilePath = null;
    currentRenderedFile = null;
    currentRenderedBurstId = state.selectedEventId;
    if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
    if (DOM.fileSelectAllRow) DOM.fileSelectAllRow.style.display = 'none';
    if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No patches for this event</div>';
    if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this burst.</div>';
    if (DOM.diffCurrentFileName) DOM.diffCurrentFileName.textContent = 'None';
    if (DOM.statAdditions) DOM.statAdditions.textContent = '+0';
    if (DOM.statDeletions) DOM.statDeletions.textContent = '-0';
    if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
    if (DOM.btnAnalyzeActiveFile) DOM.btnAnalyzeActiveFile.style.display = 'none';
    if (DOM.btnAnalyzeCheckedFiles) DOM.btnAnalyzeCheckedFiles.style.display = 'none';
    if (DOM.btnOverviewBurstFiles) DOM.btnOverviewBurstFiles.style.display = 'none';
    return;
  }

  if (DOM.diffFileCount) DOM.diffFileCount.textContent = ev.patches.length;

  let targetPatch = ev.patches[0];
  const desiredPath = preferredFilePath || state.selectedFilePath;
  if (desiredPath) {
    const found = ev.patches.find((p) => p.file_path === desiredPath);
    if (found) targetPatch = found;
  }

  // Show multi-selection header
  if (DOM.fileSelectAllRow) {
    DOM.fileSelectAllRow.style.display = 'flex';
  }
  // Clear checked files for newly selected burst if burst changed
  if (currentRenderedBurstId !== state.selectedEventId) {
    checkedFilePaths.clear();
    updateCheckedFilesState(ev.patches.length);
  }

  // Anti-flicker check: if already viewing this burst and this file, and not forcing, skip rebuilding DOM
  if (!force && currentRenderedBurstId === state.selectedEventId && currentRenderedFile === targetPatch.file_path) {
    const fileItems = DOM.diffFilesList ? DOM.diffFilesList.querySelectorAll('.diff-file-item') : [];
    fileItems.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.filePath === targetPatch.file_path);
    });
    return;
  }

  currentRenderedBurstId = state.selectedEventId;

  // Re-render file list items with checkboxes
  if (DOM.diffFilesList) {
    DOM.diffFilesList.innerHTML = '';
    ev.patches.forEach((p) => {
      const row = document.createElement('div');
      row.className = `diff-file-item ${p.file_path === targetPatch.file_path ? 'active' : ''}`;
      row.dataset.filePath = p.file_path;

      let adds = typeof p.lines_added === 'number' ? p.lines_added : 0;
      let dels = typeof p.lines_deleted === 'number' ? p.lines_deleted : 0;
      if (!p.lines_added && !p.lines_deleted && p.diff_content) {
        p.diff_content.split('\n').forEach((l) => {
          if (l.startsWith('+') && !l.startsWith('+++')) adds++;
          else if (l.startsWith('-') && !l.startsWith('---')) dels++;
        });
        p.lines_added = adds;
        p.lines_deleted = dels;
      }

      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.className = 'file-select-checkbox';
      chk.dataset.filePath = p.file_path;
      chk.checked = checkedFilePaths.has(p.file_path);
      chk.addEventListener('click', (e) => e.stopPropagation());
      chk.addEventListener('change', (e) => {
        e.stopPropagation();
        if (chk.checked) {
          checkedFilePaths.add(p.file_path);
        } else {
          checkedFilePaths.delete(p.file_path);
        }
        updateCheckedFilesState(ev.patches.length);
      });

      const nameSpan = document.createElement('span');
      nameSpan.className = 'diff-file-name';
      nameSpan.title = p.file_path;
      nameSpan.innerHTML = `<span>📄</span> <span>${escapeHtml(p.file_path)}</span>`;

      const statsSpan = document.createElement('span');
      statsSpan.className = 'diff-file-stats';
      statsSpan.innerHTML = `
        ${adds > 0 ? `<span class="stat-badge-add">+${adds}</span>` : ''}
        ${dels > 0 ? `<span class="stat-badge-del">-${dels}</span>` : ''}
      `;

      row.appendChild(chk);
      row.appendChild(nameSpan);
      row.appendChild(statsSpan);

      row.addEventListener('click', (e) => {
        if (e.target === chk) return;
        state.selectedFilePath = p.file_path;
        DOM.diffFilesList.querySelectorAll('.diff-file-item').forEach((b) => b.classList.remove('active'));
        row.classList.add('active');
        renderPatchDiff(p, true);
      });
      DOM.diffFilesList.appendChild(row);
    });
  }

  state.selectedFilePath = targetPatch.file_path;
  renderPatchDiff(targetPatch, force);
}

export async function renderPatchDiff(patch, force = false) {
  if (!patch) return;

  currentActiveAiMode = 'file';

  if (!force && currentRenderedBurstId === state.selectedEventId && currentRenderedFile === patch.file_path) {
    return;
  }
  currentRenderedFile = patch.file_path;

  if (DOM.diffCurrentFileName) DOM.diffCurrentFileName.textContent = patch.file_path;

  // Show all 3 file & overview action buttons
  if (DOM.btnAnalyzeActiveFile) DOM.btnAnalyzeActiveFile.style.display = 'inline-flex';
  if (DOM.btnAnalyzeCheckedFiles) DOM.btnAnalyzeCheckedFiles.style.display = 'inline-flex';
  if (DOM.btnOverviewBurstFiles) DOM.btnOverviewBurstFiles.style.display = 'inline-flex';

  const ev = state.eventsData.find((x) => x.id === state.selectedEventId);
  const selectedSessionId = state.selectedSessionId || ev?.session_id || 1;
  const selectedBurstId = state.selectedEventId || 0;
  const selectedBurstNum = ev?.burst_num || selectedBurstId;
  const selectedFilePath = patch.file_path;

  let cachedAnalysis = null;
  if (isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedBurstId, selectedFilePath)) {
    const keys = [
      `${selectedSessionId}::${selectedBurstNum}::${selectedFilePath}`,
      `${selectedSessionId}::${selectedBurstId}::${selectedFilePath}`,
      `${selectedBurstNum}::${selectedFilePath}`,
      `${selectedBurstId}::${selectedFilePath}`,
    ];
    for (const k of keys) {
      const found = cachedFileAnalyses.get(k) || cachedFileAnalyses[k];
      if (found) {
        cachedAnalysis = found;
        break;
      }
    }
  }

  if (!cachedAnalysis) {
    const proj = state.selectedProject || state.activeProject;
    if (proj) {
      try {
        const res = await fetchFileAnalysis(proj, {
          session_id: selectedSessionId,
          burst_id: selectedBurstNum,
          burst_number: selectedBurstNum,
          event_id: selectedBurstId,
          file_path: selectedFilePath,
        });
        if (res && res.success && res.analysis) {
          cachedAnalysis = res.analysis;
          cacheFileAnalysisRecord(selectedSessionId, selectedBurstNum, selectedBurstId, selectedFilePath, cachedAnalysis);
        }
      } catch (_) {}
    }
  }

  // Update AI slot and action buttons
  if (currentRenderedFile === selectedFilePath && currentRenderedBurstId === selectedBurstId) {
    if (cachedAnalysis) {
      if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(cachedAnalysis);
    } else {
      if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
    }
  }

  updateDiffActionButtonsState();

  const diffContent = patch.diff_content || '';
  if (!diffContent.trim()) {
    if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No text diff content recorded for this file.</div>';
    if (DOM.statAdditions) DOM.statAdditions.textContent = '+0';
    if (DOM.statDeletions) DOM.statDeletions.textContent = '-0';
    return;
  }

  let additions = 0;
  let deletions = 0;
  const lines = diffContent.split('\n');
  const fragment = document.createDocumentFragment();

  lines.forEach((line) => {
    const row = document.createElement('div');
    row.className = 'diff-line';

    if (line.startsWith('---') || line.startsWith('+++')) {
      row.className += ' diff-file-header';
      row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
    } else if (line.startsWith('@@')) {
      row.className += ' diff-hunk';
      row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
    } else if (line.startsWith('+')) {
      additions++;
      row.className += ' diff-add';
      row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
    } else if (line.startsWith('-')) {
      deletions++;
      row.className += ' diff-del';
      row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
    } else {
      row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
    }

    fragment.appendChild(row);
  });

  if (DOM.statAdditions) DOM.statAdditions.textContent = `+${additions}`;
  if (DOM.statDeletions) DOM.statDeletions.textContent = `-${deletions}`;

  if (DOM.diffCodeContainer) {
    DOM.diffCodeContainer.innerHTML = '';
    DOM.diffCodeContainer.appendChild(fragment);
  }
}

/**
 * Button 1: [ 📄 Analyze Active File (Deep) ] / [ 🔄 Re-Analyze Active File ]
 */
export async function handleAnalyzeActiveFile() {
  const proj = state.selectedProject || state.activeProject;
  if (!proj) {
    showToast('No project selected.', 'warning');
    return;
  }
  if (!state.selectedEventId) {
    showToast('No burst selected to analyze.', 'warning');
    return;
  }
  if (!state.selectedFilePath) {
    showToast('No active file selected.', 'warning');
    return;
  }

  currentActiveAiMode = 'file';

  const ev = state.eventsData.find((x) => x.id === state.selectedEventId);
  const patch = ev?.patches?.find((p) => p.file_path === state.selectedFilePath);

  const btn = DOM.btnAnalyzeActiveFile;
  const isReanalyze = btn?.dataset.action === 'reanalyze' || (btn?.innerHTML && btn.innerHTML.includes('Re-Analyze'));

  const selectedSessionId = state.selectedSessionId || ev?.session_id || 1;
  const selectedBurstId = state.selectedEventId;
  const selectedBurstNum = ev?.burst_num || selectedBurstId;
  const selectedFilePath = state.selectedFilePath;

  // Base state cache check: if not forcing reanalyze and already cached, render immediately (0 API calls)
  if (!isReanalyze && isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedBurstId, selectedFilePath)) {
    const keys = [
      `${selectedSessionId}::${selectedBurstNum}::${selectedFilePath}`,
      `${selectedSessionId}::${selectedBurstId}::${selectedFilePath}`,
      `${selectedBurstNum}::${selectedFilePath}`,
      `${selectedBurstId}::${selectedFilePath}`,
    ];
    for (const k of keys) {
      const found = cachedFileAnalyses.get(k) || cachedFileAnalyses[k];
      if (found) {
        if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(found);
        updateDiffActionButtonsState();
        return;
      }
    }
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> ${isReanalyze ? 'Re-Analyzing' : 'Analyzing'} Active File...`;
  }

  if (DOM.diffFileAiSlot) {
    DOM.diffFileAiSlot.innerHTML = `
      <div class="file-ai-card" style="border-left-color: #a855f7;">
        <div class="ai-progress-track">
          <div class="ai-progress-fill"></div>
        </div>
        <div class="ai-progress-status">${isReanalyze ? 'Re-analyzing' : 'Deep inspecting'} <code>${escapeHtml(state.selectedFilePath)}</code> diff via AI...</div>
      </div>
    `;
  }

  try {
    const res = await analyzeFile(proj, {
      session_id: selectedSessionId,
      burst_id: selectedBurstNum,
      burst_number: selectedBurstNum,
      event_id: selectedBurstId,
      file_path: selectedFilePath,
      diff_text: patch?.diff_content || '',
      mode: 'deep',
      force_refresh: isReanalyze,
    });

    if (res && res.analysis) {
      cacheFileAnalysisRecord(selectedSessionId, selectedBurstNum, selectedBurstId, selectedFilePath, res.analysis);
      if (DOM.diffFileAiSlot) {
        DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(res.analysis);
      }
      showToast(`Deep analysis complete for ${selectedFilePath}`, 'success');
    } else {
      showToast('No analysis returned.', 'warning');
    }
  } catch (err) {
    showToast(`File analysis failed: ${err.message}`, 'error');
    if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
  } finally {
    if (btn) {
      btn.disabled = false;
    }
    updateDiffActionButtonsState();
  }
}

/**
 * Button 2: [ 📑 Analyze Checked Files (Deep Batch) ] / [ 🔄 Re-Analyze Checked Files ]
 */
export async function handleAnalyzeCheckedFiles() {
  const proj = state.selectedProject || state.activeProject;
  if (!proj) {
    showToast('No project selected.', 'warning');
    return;
  }
  if (!state.selectedEventId) {
    showToast('No burst selected to analyze.', 'warning');
    return;
  }
  if (checkedFilePaths.size === 0) {
    showToast('Please check at least one file in the Touched Files sidebar.', 'warning');
    return;
  }

  currentActiveAiMode = 'batch';

  const ev = state.eventsData.find((x) => x.id === state.selectedEventId);
  const patches = ev?.patches || [];
  const targetFiles = Array.from(checkedFilePaths);
  const selectedSessionId = state.selectedSessionId || ev?.session_id || 1;
  const selectedBurstId = state.selectedEventId;
  const selectedBurstNum = ev?.burst_num || selectedBurstId;

  const btn = DOM.btnAnalyzeCheckedFiles || DOM.btnBatchAnalyzeFiles;
  const isReanalyze = btn?.dataset.action === 'reanalyze' || (btn?.innerHTML && btn.innerHTML.includes('Re-Analyze'));

  if (btn) {
    btn.disabled = true;
  }

  // Display floating global job tray
  updateGlobalTray(proj, 0, targetFiles.length, 'running', null, false, 0, targetFiles[0]);

  if (DOM.diffFileAiSlot) {
    DOM.diffFileAiSlot.innerHTML = `
      <div class="file-ai-card" style="border-left-color: #a855f7;">
        <div class="ai-progress-track">
          <div class="ai-progress-fill"></div>
        </div>
        <div class="ai-progress-status" id="batch-checked-progress-text">${isReanalyze ? 'Re-analyzing' : 'Deep inspecting'} ${targetFiles.length} checked file(s)...</div>
      </div>
    `;
  }

  let successCount = 0;
  for (let i = 0; i < targetFiles.length; i++) {
    const fPath = targetFiles[i];
    const patch = patches.find((p) => p.file_path === fPath);
    if (btn) {
      btn.innerHTML = `<span>⏳</span> ${isReanalyze ? 'Re-Analyzing' : 'Analyzing'} (${i + 1}/${targetFiles.length})...`;
    }
    const progText = document.getElementById('batch-checked-progress-text');
    if (progText) {
      progText.textContent = `${isReanalyze ? 'Re-analyzing' : 'Deep inspecting'} (${i + 1}/${targetFiles.length}): ${fPath}...`;
    }

    // Update global tray with active file
    updateGlobalTray(proj, i, targetFiles.length, 'running', null, false, 0, fPath);

    // If NOT re-analyzing, retrieve from cache if available (0 API calls!)
    if (!isReanalyze && isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedBurstId, fPath)) {
      const keys = [
        `${selectedSessionId}::${selectedBurstNum}::${fPath}`,
        `${selectedSessionId}::${selectedBurstId}::${fPath}`,
        `${selectedBurstNum}::${fPath}`,
        `${selectedBurstId}::${fPath}`,
      ];
      for (const k of keys) {
        const found = cachedFileAnalyses.get(k) || cachedFileAnalyses[k];
        if (found) {
          successCount++;
          if (state.selectedFilePath === fPath && DOM.diffFileAiSlot) {
            DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(found);
          }
          break;
        }
      }
      continue;
    }

    try {
      const res = await analyzeFile(proj, {
        session_id: selectedSessionId,
        burst_id: selectedBurstNum,
        burst_number: selectedBurstNum,
        event_id: selectedBurstId,
        file_path: fPath,
        diff_text: patch?.diff_content || '',
        mode: 'deep',
        force_refresh: isReanalyze,
      });

      if (res && res.analysis) {
        cacheFileAnalysisRecord(selectedSessionId, selectedBurstNum, selectedBurstId, fPath, res.analysis);
        successCount++;
        if (state.selectedFilePath === fPath && DOM.diffFileAiSlot) {
          DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(res.analysis);
        }
      }
    } catch (err) {
      console.warn(`Failed batch analysis for ${fPath}:`, err);
      const errMsg = (err.message || '').toLowerCase();
      if (errMsg.includes('429') || errMsg.includes('rate limit') || errMsg.includes('resourceexhausted') || errMsg.includes('quota')) {
        updateGlobalTray(proj, i, targetFiles.length, 'running', null, true, 15, fPath);
        if (progText) {
          progText.textContent = `⏳ API Rate Limit encountered for ${fPath}. Cooling down 15s...`;
        }
        await new Promise((resolve) => startTrayCooldownCountdown(15, (rem) => {
          if (progText) progText.textContent = `⏳ API Rate Limit: Cooling down (${rem}s remaining)...`;
        }, resolve));
        // Retry the current file
        i--;
        continue;
      }
    }
  }

  if (btn) {
    btn.disabled = false;
  }

  // Update tray to completed state
  updateGlobalTray(proj, targetFiles.length, targetFiles.length, 'completed', null, false, 0, '');

  if (isFileAnalysisCached(selectedSessionId, selectedBurstNum, selectedBurstId, state.selectedFilePath)) {
    const keys = [
      `${selectedSessionId}::${selectedBurstNum}::${state.selectedFilePath}`,
      `${selectedSessionId}::${selectedBurstId}::${state.selectedFilePath}`,
      `${selectedBurstNum}::${state.selectedFilePath}`,
      `${selectedBurstId}::${state.selectedFilePath}`,
    ];
    for (const k of keys) {
      const found = cachedFileAnalyses.get(k) || cachedFileAnalyses[k];
      if (found && DOM.diffFileAiSlot) {
        DOM.diffFileAiSlot.innerHTML = createFileAiCardHtml(found);
        break;
      }
    }
  }

  updateDiffActionButtonsState();
  showToast(`Deep batch analysis completed: ${successCount}/${targetFiles.length} file(s) analyzed.`, 'success');
}

/**
 * Button 3: [ 🌐 Overview All Touched Files ] / [ 🔄 Re-Overview All Touched Files ]
 */
export async function handleOverviewBurstFiles() {
  const proj = state.selectedProject || state.activeProject;
  if (!proj) {
    showToast('No project selected.', 'warning');
    return;
  }
  if (!state.selectedEventId) {
    showToast('No burst selected to analyze.', 'warning');
    return;
  }

  currentActiveAiMode = 'overview';

  const ev = state.eventsData.find((x) => x.id === state.selectedEventId);
  const selectedSessionId = state.selectedSessionId || ev?.session_id || 1;
  const selectedBurstId = state.selectedEventId;
  const selectedBurstNum = ev?.burst_num || selectedBurstId;

  const btn = DOM.btnOverviewBurstFiles || DOM.btnOverviewFiles;
  const isReoverview = btn?.dataset.action === 'reoverview' || (btn?.innerHTML && btn.innerHTML.includes('Re-Overview'));

  // If not forcing reoverview, check if already cached
  if (!isReoverview) {
    if (isBurstOverviewCached(selectedSessionId, selectedBurstNum, selectedBurstId)) {
      const keys = [
        `${selectedSessionId}::${selectedBurstNum}`,
        `${selectedSessionId}::${selectedBurstId}`,
        String(selectedBurstNum),
        String(selectedBurstId),
      ];
      for (const k of keys) {
        const found = cachedBurstOverviews.get(k) || cachedBurstOverviews[k];
        if (found && (found.overview || found.burst_summary)) {
          if (DOM.diffFileAiSlot) {
            DOM.diffFileAiSlot.innerHTML = createBurstOverviewCardHtml(found);
          }
          currentRenderedFile = null;
          updateDiffActionButtonsState();
          return;
        }
      }
    }
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> ${isReoverview ? 'Re-Generating Overview...' : 'Generating Overview...'}`;
  }

  if (DOM.diffFileAiSlot) {
    DOM.diffFileAiSlot.innerHTML = `
      <div class="file-ai-card" style="border-left-color: #38bdf8;">
        <div class="ai-progress-track">
          <div class="ai-progress-fill"></div>
        </div>
        <div class="ai-progress-status">${isReoverview ? 'Re-generating' : 'Generating'} compact architectural overview for burst #${selectedBurstNum}...</div>
      </div>
    `;
  }

  try {
    const res = await analyzeBurstOverview(proj, {
      burst_id: selectedBurstNum,
      burst_number: selectedBurstNum,
      event_id: selectedBurstId,
      session_id: selectedSessionId,
      force_refresh: isReoverview,
    });

    if (res && res.overview) {
      cacheBurstOverviewRecord(selectedSessionId, selectedBurstNum, selectedBurstId, res);
      if (DOM.diffFileAiSlot) {
        DOM.diffFileAiSlot.innerHTML = createBurstOverviewCardHtml(res);
      }
      currentRenderedFile = null;
      updateDiffActionButtonsState();
      showToast(`Burst overview generated (${res.file_count || Object.keys(res.overview).length} files)`, 'success');
    } else {
      showToast('No overview returned.', 'warning');
    }
  } catch (err) {
    showToast(`Burst overview failed: ${err.message}`, 'error');
    if (DOM.diffFileAiSlot) DOM.diffFileAiSlot.innerHTML = '';
  } finally {
    if (btn) {
      btn.disabled = false;
    }
    updateDiffActionButtonsState();
  }
}

// Backwards compatibility aliases
export const setupDiffEventDropdown = setupDiffViewerHierarchy;
export const onDiffEventSelected = onDiffBurstSelected;
export const handleAnalyzeSelectedFile = handleAnalyzeActiveFile;
export const handleBatchAnalyzeFiles = handleAnalyzeCheckedFiles;
export const handleOverviewFiles = handleOverviewBurstFiles;
export const updateDiffButtons = updateDiffActionButtonsState;

export function initDiffListeners() {
  if (DOM.diffSessionSelect) {
    DOM.diffSessionSelect.addEventListener('change', (e) => onDiffSessionSelected(e.target.value));
  }
  if (DOM.diffBurstSelect) {
    DOM.diffBurstSelect.addEventListener('change', (e) => onDiffBurstSelected(e.target.value));
  }
  if (DOM.chkSelectAllFiles) {
    DOM.chkSelectAllFiles.addEventListener('change', () => {
      const isChecked = DOM.chkSelectAllFiles.checked;
      const ev = state.eventsData.find((x) => x.id === state.selectedEventId);
      const patches = ev?.patches || [];
      const checkboxes = DOM.diffFilesList ? DOM.diffFilesList.querySelectorAll('.file-select-checkbox') : [];
      checkboxes.forEach((cb) => {
        cb.checked = isChecked;
        if (isChecked) {
          checkedFilePaths.add(cb.dataset.filePath);
        } else {
          checkedFilePaths.delete(cb.dataset.filePath);
        }
      });
      updateCheckedFilesState(patches.length);
    });
  }
  if (DOM.btnAnalyzeActiveFile) {
    DOM.btnAnalyzeActiveFile.addEventListener('click', handleAnalyzeActiveFile);
  }
  if (DOM.btnAnalyzeCheckedFiles) {
    DOM.btnAnalyzeCheckedFiles.addEventListener('click', handleAnalyzeCheckedFiles);
  }
  if (DOM.btnOverviewBurstFiles) {
    DOM.btnOverviewBurstFiles.addEventListener('click', handleOverviewBurstFiles);
  }
}
