/**
 * SPD Analysis Engine — Web Control Portal Client Controller
 * Pure Vanilla JavaScript (Zero external framework/CDN dependencies)
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Global State
  // ---------------------------------------------------------------------------
  const state = {
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
  };

  // ---------------------------------------------------------------------------
  // DOM Elements Cache
  // ---------------------------------------------------------------------------
  const DOM = {
    // Navigation & Sidebar
    btnToggleSidebar: document.getElementById('btn-toggle-sidebar'),
    sidebar: document.querySelector('.sidebar'),
    sidebarToggleIcon: document.getElementById('sidebar-toggle-icon'),
    navButtons: document.querySelectorAll('.nav-item'),
    tabViews: document.querySelectorAll('.tab-view'),
    navEventCount: document.getElementById('nav-event-count'),
    navExecCount: document.getElementById('nav-exec-count'),

    // Powers Ribbon
    powerPillEdits: document.getElementById('power-pill-edits'),
    powerPillReads: document.getElementById('power-pill-reads'),
    powerPillExec: document.getElementById('power-pill-exec'),
    powerPillGit: document.getElementById('power-pill-git'),

    // Header
    headerProjectName: document.getElementById('header-project-name'),
    headerTierPill: document.getElementById('header-tier-pill'),
    headerPathVal: document.getElementById('header-path-val'),
    headerPid: document.getElementById('header-pid'),
    headerUptime: document.getElementById('header-uptime'),
    headerMem: document.getElementById('header-mem'),
    headerHb: document.getElementById('header-hb'),
    headerCompactTel: document.getElementById('header-compact-tel'),
    btnHeaderResume: document.getElementById('btn-header-resume'),
    btnHeaderStop: document.getElementById('btn-header-stop'),
    btnHeaderAiSettings: document.getElementById('btn-header-ai-settings'),
    btnHeaderGitSettings: document.getElementById('btn-header-git-settings'),
    btnHeaderPushGit: document.getElementById('btn-header-push-git'),
    btnHeaderTrash: document.getElementById('btn-header-trash'),
    headerTrashCount: document.getElementById('header-trash-count') || document.getElementById('badge-trash-count'),
    badgeTrashCount: document.getElementById('badge-trash-count') || document.getElementById('header-trash-count'),
    btnHeaderDeleteProj: document.getElementById('btn-header-delete-proj'),

    // Sidebar Projects & Accordions
    projectFilterInput: document.getElementById('project-filter-input'),
    btnRefreshProjects: document.getElementById('btn-refresh-projects'),
    countCurrent: document.getElementById('count-current'),
    countLastRun: document.getElementById('count-last-run'),
    countHistory: document.getElementById('count-history'),
    tierCurrentItems: document.getElementById('tier-current-items'),
    tierLastRunItems: document.getElementById('tier-last-run-items'),
    tierHistoryItems: document.getElementById('tier-history-items'),
    accordionCurrent: document.getElementById('accordion-current'),
    accordionLastRun: document.getElementById('accordion-last-run'),
    accordionHistory: document.getElementById('accordion-history'),

    // Dashboard View
    dashActiveCount: document.getElementById('dash-active-count'),
    dashEventCount: document.getElementById('dash-event-count'),
    dashPatchCount: document.getElementById('dash-patch-count'),
    detailSlug: document.getElementById('detail-slug'),
    detailTier: document.getElementById('detail-tier'),
    detailPath: document.getElementById('detail-path'),
    detailSessionId: document.getElementById('detail-session-id'),
    detailPid: document.getElementById('detail-pid'),
    detailMemory: document.getElementById('detail-memory'),
    dashRecentEvents: document.getElementById('dash-recent-events'),
    btnTelemetryResume: document.getElementById('btn-telemetry-resume'),
    btnExportSession: document.getElementById('btn-export-session'),
    btnViewAllTimeline: document.getElementById('btn-view-all-timeline'),
    btnBatchAnalyzeSession: document.getElementById('btn-batch-analyze-session'),

    // Project Configuration & Specs Card
    cardProjectSpecs: document.getElementById('card-project-specs'),
    specMonitoredPath: document.getElementById('spec-monitored-path'),
    specAiProfile: document.getElementById('spec-ai-profile'),
    specDebounceWindow: document.getElementById('spec-debounce-window'),
    specPowersChecklist: document.getElementById('spec-powers-checklist'),
    specCheckEdits: document.getElementById('spec-check-edits'),
    specCheckReads: document.getElementById('spec-check-reads'),
    specCheckExec: document.getElementById('spec-check-exec'),
    specCheckGit: document.getElementById('spec-check-git'),
    specGithubRemote: document.getElementById('spec-github-remote'),
    specLastPushStatus: document.getElementById('spec-last-push-status'),
    btnSpecsPushGh: document.getElementById('btn-specs-push-gh'),

    // Real-Time Live Activity Stream
    cardLiveActivity: document.getElementById('card-live-activity'),
    liveTickerFeed: document.getElementById('live-ticker-feed'),
    liveActionCount: document.getElementById('live-action-count'),

    // Debounce Burst Monitor
    cardDebounceMonitor: document.getElementById('card-debounce-monitor'),
    debounceMonitorTitle: document.getElementById('debounce-monitor-title'),
    debouncePulse: document.getElementById('debounce-pulse'),
    debounceStatusBadge: document.getElementById('debounce-status-badge'),
    btnSealBurstNow: document.getElementById('btn-seal-burst-now'),
    debounceProgressFill: document.getElementById('debounce-progress-fill'),
    debounceStatusDesc: document.getElementById('debounce-status-desc'),
    debounceCountdownText: document.getElementById('debounce-countdown-text'),

    // Timeline View
    timelineContainer: document.getElementById('timeline-container'),
    timelineFilterGroup: document.getElementById('timeline-filter-group'),
    timelineFilterPills: document.querySelectorAll('.filter-pill'),
    btnRefreshTimeline: document.getElementById('btn-refresh-timeline'),
    timelineReadsList: document.getElementById('timeline-reads-list'),
    timelineReadCount: document.getElementById('timeline-read-count'),

    // Terminal View
    terminalFeedContainer: document.getElementById('terminal-feed-container'),
    btnRefreshTerminal: document.getElementById('btn-refresh-terminal'),

    // Diff Viewer View
    diffSessionSelect: document.getElementById('diff-session-select'),
    diffBurstSelect: document.getElementById('diff-burst-select'),
    diffBurstCount: document.getElementById('diff-burst-count'),
    diffEventSelect: document.getElementById('diff-burst-select'),
    diffFileCount: document.getElementById('diff-file-count'),
    diffFilesList: document.getElementById('diff-files-list'),
    diffCurrentFileName: document.getElementById('diff-current-file-name'),
    diffCodeContainer: document.getElementById('diff-code-container'),
    statAdditions: document.getElementById('stat-additions'),
    statDeletions: document.getElementById('stat-deletions'),
    diffAiSlot: document.getElementById('diff-ai-slot'),


    // Logs View
    logsTerminal: document.getElementById('logs-terminal'),
    chkAutoscroll: document.getElementById('chk-autoscroll'),
    btnCopyLogs: document.getElementById('btn-copy-logs'),
    btnRefreshLogs: document.getElementById('btn-refresh-logs'),

    // Modal: New Session
    btnNewSession: document.getElementById('btn-new-session'),
    modalNewSession: document.getElementById('modal-new-session'),
    modalCloseBtn: document.getElementById('modal-close-btn'),
    modalCancelBtn: document.getElementById('modal-cancel-btn'),
    formNewSession: document.getElementById('form-new-session'),
    inpProjectName: document.getElementById('inp-project-name'),
    slugPreview: document.getElementById('slug-preview'),
    inpTargetPath: document.getElementById('inp-target-path'),
    chkScaffold: document.getElementById('chk-scaffold'),
    rngDebounce: document.getElementById('rng-debounce'),
    debounceValDisplay: document.getElementById('debounce-val-display'),
    selIdeProfile: document.getElementById('sel-ide-profile'),
    groupCustomIdeMarker: document.getElementById('group-custom-ide-marker'),
    inpCustomIdeMarker: document.getElementById('inp-custom-ide-marker'),
    modalError: document.getElementById('modal-error'),
    modalSpinner: document.getElementById('modal-spinner'),
    modalSubmitBtn: document.getElementById('modal-submit-btn'),
    chkPowerEdits: document.getElementById('chk-power-edits'),
    chkPowerReads: document.getElementById('chk-power-reads'),
    chkPowerExec: document.getElementById('chk-power-exec'),
    chkPowerShadowGit: document.getElementById('chk-power-shadow-git'),
    chkPowerGitInit: document.getElementById('chk-power-git-init'),

    // Modal: Git Settings
    modalGitSettings: document.getElementById('modal-git-settings'),
    modalGitSettingsClose: document.getElementById('modal-git-settings-close'),
    modalGitSettingsCancel: document.getElementById('modal-git-settings-cancel'),
    formGitSettings: document.getElementById('form-git-settings'),
    inpGitUsername: document.getElementById('inp-git-username'),
    inpGitToken: document.getElementById('inp-git-token'),
    inpGitDefaultRemote: document.getElementById('inp-git-default-remote'),
    inpGitDefaultBranch: document.getElementById('inp-git-default-branch'),
    chkGitAutoChangelog: document.getElementById('chk-git-auto-changelog'),
    inpGitTestUrl: document.getElementById('inp-git-test-url'),
    btnTestGitConnection: document.getElementById('btn-test-git-connection'),
    gitTestStatus: document.getElementById('git-test-status'),
    btnSaveGitConfig: document.getElementById('btn-save-git-config'),

    // Modal: Milestone Sync & Changelog Review
    modalSyncReview: document.getElementById('modal-sync-review'),
    modalSyncReviewClose: document.getElementById('modal-sync-review-close'),
    modalSyncCancel: document.getElementById('modal-sync-cancel'),
    btnConfirmSyncPush: document.getElementById('btn-confirm-sync-push'),
    syncEventsCount: document.getElementById('sync-events-count'),
    syncProviderBadge: document.getElementById('sync-provider-badge'),
    inpSyncCommitTitle: document.getElementById('inp-sync-commit-title'),
    inpSyncCommitBody: document.getElementById('inp-sync-commit-body'),
    inpSyncChangelogContent: document.getElementById('inp-sync-changelog-content'),
    inpSyncRemote: document.getElementById('inp-sync-remote'),
    inpSyncBranch: document.getElementById('inp-sync-branch'),
    chkSyncAutoCreateRepo: document.getElementById('chk-sync-auto-create-repo'),
    syncError: document.getElementById('sync-error'),
    syncSpinner: document.getElementById('sync-spinner'),

    // Push Stepper Progress Bar
    syncStepperContainer: document.getElementById('sync-stepper-container'),
    syncStepperFill: document.getElementById('sync-stepper-fill'),
    syncStepperMsg: document.getElementById('sync-stepper-msg'),
    step1: document.getElementById('step-1'),
    step2: document.getElementById('step-2'),
    step3: document.getElementById('step-3'),

    // Modal: AI Settings
    modalAiSettings: document.getElementById('modal-ai-settings'),
    modalAiSettingsClose: document.getElementById('modal-ai-settings-close'),
    modalAiSettingsCancel: document.getElementById('modal-ai-settings-cancel'),
    formAiSettings: document.getElementById('form-ai-settings'),
    selAiProvider: document.getElementById('sel-ai-provider'),
    boxProviderOllama: document.getElementById('box-provider-ollama'),
    boxProviderGemini: document.getElementById('box-provider-gemini'),
    boxProviderHeuristic: document.getElementById('box-provider-heuristic'),
    inpOllamaUrl: document.getElementById('inp-ollama-url'),
    inpOllamaModel: document.getElementById('inp-ollama-model'),
    btnTestOllama: document.getElementById('btn-test-ollama'),
    ollamaTestStatus: document.getElementById('ollama-test-status'),
    selGeminiKeyVault: document.getElementById('sel-gemini-key-vault'),
    btnDeleteGeminiKey: document.getElementById('btn-delete-gemini-key'),
    inpGeminiKey: document.getElementById('inp-gemini-key'),
    lblGeminiKey: document.getElementById('lbl-gemini-key'),
    helpGeminiKey: document.getElementById('help-gemini-key'),
    geminiActiveKeyInfo: document.getElementById('gemini-active-key-info'),
    selGeminiModel: document.getElementById('sel-gemini-model'),
    btnTestGemini: document.getElementById('btn-test-gemini'),
    geminiTestStatus: document.getElementById('gemini-test-status'),
    btnSaveAiConfig: document.getElementById('btn-save-ai-config'),

    // Batch Progress Box
    batchProgressContainer: document.getElementById('batch-progress-container'),
    batchSpinner: document.getElementById('batch-spinner'),
    batchProgressTitle: document.getElementById('batch-progress-title'),
    batchProgressPct: document.getElementById('batch-progress-pct'),
    btnBatchRetry: document.getElementById('btn-batch-retry'),
    batchProgressFill: document.getElementById('batch-progress-fill'),
    batchProgressDetail: document.getElementById('batch-progress-detail'),
    batchProgressTime: document.getElementById('batch-progress-time'),

    // Modal: Delete Project
    modalDeleteProject: document.getElementById('modal-delete-project'),
    modalDeleteProjectClose: document.getElementById('modal-delete-project-close'),
    modalDeleteProjectCancel: document.getElementById('modal-delete-project-cancel'),
    delModalProjectName: document.getElementById('del-modal-project-name'),
    deleteBurstsContainer: document.getElementById('delete-bursts-container'),
    deleteBurstsList: document.getElementById('delete-bursts-list'),
    btnSelectAllBursts: document.getElementById('btn-select-all-bursts'),
    deleteRemoteOption: document.getElementById('delete-remote-option'),
    chkDeleteRemoteGithub: document.getElementById('chk-delete-remote-github'),
    deleteProjectError: document.getElementById('delete-project-error'),
    deleteProjectSpinner: document.getElementById('delete-project-spinner'),
    btnConfirmDeleteProject: document.getElementById('btn-confirm-delete-project'),

    // Modal: Trash Bin
    modalTrashBin: document.getElementById('modal-trash-bin'),
    modalTrashBinClose: document.getElementById('modal-trash-bin-close'),
    btnEmptyTrash: document.getElementById('btn-empty-trash'),
    trashBinItemsContainer: document.getElementById('trash-bin-items-container'),
    trashBinError: document.getElementById('trash-bin-error'),

    // Toasts & Status
    toastContainer: document.getElementById('toast-container'),
    connStatus: document.getElementById('connection-status'),
    connText: document.getElementById('conn-text'),

    // Floating Global Job Tray
    globalJobTray: document.getElementById('global-job-tray'),
    traySpinner: document.getElementById('tray-spinner'),
    trayTitle: document.getElementById('tray-title'),
    trayCloseBtn: document.getElementById('tray-close-btn'),
    trayProjectName: document.getElementById('tray-project-name'),
    trayProgressBar: document.getElementById('tray-progress-bar'),
    trayCounts: document.getElementById('tray-counts'),
    trayPercent: document.getElementById('tray-percent'),
    trayCooldownBadge: document.getElementById('tray-cooldown-badge'),
    trayCooldownText: document.getElementById('tray-cooldown-text'),
  };

  // ---------------------------------------------------------------------------
  // Utilities & Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function scrubSensitiveText(text) {
    if (!text || typeof text !== 'string') return text;
    return text
      // GitHub PATs
      .replace(/gh[pousr]_[A-Za-z0-9_]{16,255}/g, '[REDACTED_GH_TOKEN]')
      // Google / Gemini API Keys
      .replace(/AIza[0-9A-Za-z\-_]{35}/g, '[REDACTED_API_KEY]')
      // Generic Bearer tokens
      .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, 'Bearer [REDACTED_TOKEN]')
      // Basic Auth / embedded URLs
      .replace(/:\/\/([^:\s]+):([^@\s]+)@/g, '://$1:[REDACTED_SECRET]@')
      // Private key headers
      .replace(/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, '[REDACTED_PRIVATE_KEY]')
      // High-entropy generic tokens or secrets in key-value pairs
      .replace(/(?:api_key|apikey|secret|token|password|auth_token)\s*[:=]\s*['"][^\s'"]{8,}['"]/gi, (m) => {
        const parts = m.split(/[:=]/);
        return `${parts[0]}: "[REDACTED_CREDENTIAL]"`;
      });
  }

  function slugify(name) {
    return name
      .trim()
      .toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .replace(/[\s-]+/g, '_')
      .slice(0, 64) || 'unnamed';
  }

  function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h > 0) return `${h}h ${m % 60}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function formatIST(timestampStr, includeDate = true) {
    if (!timestampStr) return '—';
    try {
      const d = new Date(timestampStr);
      if (isNaN(d.getTime())) return timestampStr;
      const options = {
        timeZone: 'Asia/Kolkata',
        hour12: true,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      };
      if (includeDate) {
        options.year = 'numeric';
        options.month = 'short';
        options.day = '2-digit';
      }
      return `${new Intl.DateTimeFormat('en-IN', options).format(d)} IST`;
    } catch (e) {
      return timestampStr;
    }
  }

  function formatISTTime(timestampStr) {
    if (!timestampStr) return '';
    try {
      if (typeof timestampStr === 'string' && timestampStr.endsWith('IST')) {
        return timestampStr;
      }
      const d = new Date(timestampStr);
      if (isNaN(d.getTime())) {
        if (typeof timestampStr === 'string' && timestampStr.includes(':')) {
          return `${timestampStr} IST`;
        }
        return timestampStr;
      }
      const formatted = new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour12: true,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(d);
      return `${formatted} IST`;
    } catch (e) {
      return timestampStr;
    }
  }

  function showToast(message, type = 'success', title = null) {
    if (!DOM.toastContainer) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let iconChar = '✓';
    let defaultTitle = 'Success';
    if (type === 'error') {
      iconChar = '✕';
      defaultTitle = 'Error';
    } else if (type === 'info') {
      iconChar = 'ℹ';
      defaultTitle = 'Information';
    } else if (type === 'warning') {
      iconChar = '⚠';
      defaultTitle = 'Notice';
    }

    const toastIcon = document.createElement('span');
    toastIcon.className = 'toast-icon';
    toastIcon.textContent = iconChar;

    const toastBody = document.createElement('div');
    toastBody.className = 'toast-body';

    const toastTitle = document.createElement('div');
    toastTitle.className = 'toast-title';
    toastTitle.textContent = title || defaultTitle;

    const toastMsg = document.createElement('div');
    toastMsg.className = 'toast-msg';
    toastMsg.textContent = message;

    toastBody.appendChild(toastTitle);
    toastBody.appendChild(toastMsg);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.title = 'Dismiss';
    closeBtn.onclick = () => toast.remove();

    toast.appendChild(toastIcon);
    toast.appendChild(toastBody);
    toast.appendChild(closeBtn);

    DOM.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 4500);
  }

  // ---------------------------------------------------------------------------
  // API Calls
  // ---------------------------------------------------------------------------

  async function apiGet(endpoint) {
    const res = await fetch(endpoint);
    if (!res.ok) {
      let errMsg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body.error) errMsg = body.error;
      } catch (_) {}
      throw new Error(errMsg);
    }
    return await res.json();
  }

  async function apiPost(endpoint, payload) {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let errMsg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body.error) errMsg = body.error;
      } catch (_) {}
      throw new Error(errMsg);
    }
    return await res.json();
  }

  // ---------------------------------------------------------------------------
  // Projects Management
  // ---------------------------------------------------------------------------

  async function refreshProjects() {
    try {
      const data = await apiGet('/api/projects');
      state.projectsData = data;

      // Update counters
      DOM.countCurrent.textContent = data.current.length;
      DOM.countLastRun.textContent = data.last_run.length;
      DOM.countHistory.textContent = data.history.length;

      // If no project selected yet, pick first active current or first last_run
      if (!state.selectedProject) {
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

  async function loadProjectSpecs(projectName) {
    if (!projectName || !DOM.cardProjectSpecs) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/specs`);
      if (DOM.specMonitoredPath) {
        DOM.specMonitoredPath.textContent = data.target_path || '—';
        DOM.specMonitoredPath.title = data.target_path || '';
      }
      if (DOM.specAiProfile) {
        const prof = data.ide_profile || 'antigravity';
        let profLabel = 'Google Antigravity';
        if (prof === 'cursor') profLabel = 'Cursor AI';
        else if (prof === 'windsurf') profLabel = 'Windsurf / Codeium';
        else if (prof === 'claude_code') profLabel = 'Claude Code CLI';
        else if (prof === 'custom') {
          profLabel = data.ide_custom_marker ? `Custom (${data.ide_custom_marker})` : 'Custom AI';
        }
        DOM.specAiProfile.textContent = `${profLabel} (Native VS Code Ignored)`;
      }
      if (DOM.specDebounceWindow) {
        const dbVal = data.debounce != null ? data.debounce : 3.5;
        const dbBadge = dbVal === 3.5 ? ' (Standard Recommended)' : ' (Custom)';
        DOM.specDebounceWindow.textContent = `${dbVal}s${dbBadge}`;
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

  function selectProject(projectName, tier) {
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

  function renderProjectsList() {
    const filter = DOM.projectFilterInput.value.toLowerCase().trim();

    function renderGroup(items, container, tier) {
      container.innerHTML = '';
      const filtered = items.filter((p) => p.name.toLowerCase().includes(filter));
      if (filtered.length === 0) {
        container.innerHTML = `<div class="empty-hint">${filter ? 'No matching projects' : 'No entries'}</div>`;
        return;
      }

      filtered.forEach((p) => {
        const btn = document.createElement('button');
        const isSelected = state.selectedProject === p.name && state.selectedTier === tier;
        btn.className = `project-item ${isSelected ? 'active' : ''}`;

        const dot = p.is_active
          ? '<span class="pulse-dot"></span>'
          : (p.is_interrupted ? '<span class="static-dot" style="background: #ef4444; box-shadow: 0 0 6px #ef4444;"></span>' : '<span class="static-dot"></span>');
        const burstCount = p.events_count || 0;
        const evCount = burstCount > 0 ? `<span class="badge badge-subtle">${burstCount} ${burstCount === 1 ? 'burst' : 'bursts'}</span>` : '';
        const interruptedBadge = p.is_interrupted ? `<span class="badge badge-interrupted" title="${escapeHtml(p.interrupted_reason || 'Interrupted')}">⚠️ Interrupted</span>` : '';

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

  function getSelectedProjectInfo() {
    if (!state.selectedProject) return null;
    const tierList = state.projectsData[state.selectedTier] || [];
    return tierList.find((p) => p.name === state.selectedProject) || null;
  }

  function formatPreciseCountdown(seconds) {
    if (seconds <= 0) return '00:00.0';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 10);
    const minStr = String(mins).padStart(2, '0');
    const secStr = String(secs).padStart(2, '0');
    return `${minStr}:${secStr}.${ms}`;
  }

  function updateHeaderTelemetry() {
    const p = getSelectedProjectInfo();
    if (!p) {
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

    if (p.is_active) {
      DOM.headerTierPill.textContent = 'ACTIVE';
      DOM.headerTierPill.className = 'project-tier-pill';
      DOM.btnHeaderStop.disabled = false;
      DOM.btnHeaderStop.style.display = '';
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = 'none';
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = 'none';
    } else if (p.is_interrupted || p.status === 'INTERRUPTED') {
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

    DOM.headerPid.textContent = p.pid != null ? p.pid : '—';
    DOM.headerUptime.textContent = p.uptime_s != null ? formatUptime(p.uptime_s) : '—';
    const memStr = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';
    DOM.headerMem.textContent = memStr;
    DOM.headerHb.textContent = p.is_active ? 'Live' : (p.is_interrupted ? 'Interrupted' : 'Stopped');
    if (DOM.headerCompactTel) {
      DOM.headerCompactTel.textContent = `${p.is_active ? '🟢 Live' : (p.is_interrupted ? '⚠️ Interrupted' : '⚪ Stopped')} • ${memStr}`;
    }

    // Powers Ribbon
    const powers = p.powers || {};
    setPowerPillState(DOM.powerPillEdits, powers.track_edits ?? true);
    setPowerPillState(DOM.powerPillReads, powers.track_reads ?? true);
    setPowerPillState(DOM.powerPillExec, powers.track_exec ?? true);
    setPowerPillState(DOM.powerPillGit, powers.shadow_git ?? true);

    // Dashboard Telemetry Card
    DOM.detailSlug.textContent = p.name;
    DOM.detailTier.textContent = p.is_interrupted ? 'INTERRUPTED (Power Loss / Terminated)' : state.selectedTier;
    DOM.detailPath.textContent = p.target_path || '—';
    DOM.detailSessionId.textContent = p.session_id != null ? `#${p.session_id}` : '—';
    DOM.detailPid.textContent = p.pid != null ? p.pid : '—';
    DOM.detailMemory.textContent = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';

    // Debounce Burst Monitor
    if (DOM.cardDebounceMonitor) {
      const db = p.debounce;
      if (p.is_active && db) {
        if (db.active) {
          const quiet = db.quiet_period || 3.5;
          const elapsed = db.elapsed_s || 0;
          const remaining = db.remaining_s || 0;
          const pct = Math.min(100, Math.max(8, (elapsed / quiet) * 100));

          DOM.debounceProgressFill.style.width = `${pct.toFixed(0)}%`;
          DOM.debounceProgressFill.classList.add('active');
          DOM.debouncePulse.className = 'debounce-pulse-indicator active';
          DOM.debounceStatusBadge.className = 'debounce-status-badge active';
          DOM.debounceStatusBadge.textContent = 'Debouncing Burst';
          if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = '';
          DOM.debounceStatusDesc.textContent = `Coalescing edits in ${db.files_count || 1} file(s) (${db.first_file || ''})…`;
          DOM.debounceCountdownText.textContent = `${formatPreciseCountdown(remaining)} remaining…`;
        } else {
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
        DOM.debounceProgressFill.style.width = '0%';
        DOM.debounceProgressFill.classList.remove('active');
        DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
        DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
        DOM.debounceStatusBadge.textContent = 'Idle / Listening';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
        DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
        DOM.debounceCountdownText.textContent = '—';
      } else {
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

  function setPowerPillState(el, isActive) {
    if (!el) return;
    if (isActive) {
      el.classList.add('active');
      el.classList.remove('disabled');
    } else {
      el.classList.remove('active');
      el.classList.add('disabled');
    }
  }

  // ---------------------------------------------------------------------------
  // Events & Timeline Management
  // ---------------------------------------------------------------------------

  async function loadProjectEvents(projectName) {
    if (!projectName) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/events`);
      state.eventsData = data.events || [];
      state.sessionsData = data.sessions || [];

      // Filter events by type
      const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
      const readEvents = state.eventsData.filter((ev) => ev.event_type === 'READ');
      const execEvents = state.eventsData.filter((ev) => ev.event_type === 'EXEC');
      const gitEvents = state.eventsData.filter((ev) => ev.event_type === 'GIT');

      DOM.navEventCount.textContent = editEvents.length;
      if (DOM.navExecCount) DOM.navExecCount.textContent = execEvents.length;
      if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = readEvents.length;
      DOM.dashEventCount.textContent = editEvents.length;
      const curProj = getSelectedProjectInfo();
      if (curProj && curProj.events_count !== editEvents.length) {
        curProj.events_count = editEvents.length;
        renderProjectsList();
      }

      // Count total patches
      let patchCount = 0;
      editEvents.forEach((ev) => {
        patchCount += ev.patches ? ev.patches.length : 0;
      });
      DOM.dashPatchCount.textContent = patchCount;

      renderTimelineFromState();
      renderAnalyzedFiles(readEvents);
      renderTerminal(execEvents);
      renderDashboardRecentEvents(editEvents);
      setupDiffViewerHierarchy(editEvents);
    } catch (err) {
      console.warn(`Could not load events for ${projectName}:`, err);
      state.eventsData = [];
      state.sessionsData = [];
      DOM.navEventCount.textContent = '0';
      if (DOM.navExecCount) DOM.navExecCount.textContent = '0';
      if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = '0';
      DOM.dashEventCount.textContent = '0';
      DOM.dashPatchCount.textContent = '0';
      renderTimelineFromState();
      renderAnalyzedFiles([]);
      renderTerminal([]);
      renderDashboardRecentEvents([]);
      setupDiffViewerHierarchy([]);
    }
  }

  async function loadProjectSessions(projectName) {
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

  function setupTimelineFilters() {
    if (!DOM.timelineFilterGroup) return;
    if (!DOM.timelineFilterPills) return;
    DOM.timelineFilterPills.forEach((pill) => {
      pill.addEventListener('click', () => {
        DOM.timelineFilterPills.forEach((p) => p.classList.remove('active'));
        pill.classList.add('active');
        state.timelineFilter = pill.dataset.filter || 'all';
        renderTimelineFromState();
      });
    });
  }

  function renderTimelineFromState() {
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
      // 'all' - exclude standalone GIT events since they are badged onto bursts
      eventsToRender = state.eventsData.filter((ev) => ev.event_type !== 'GIT');
    }

    renderTimeline(eventsToRender, gitEvents);
  }

  function createAiCardHtml(ai) {
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
      <div class="ai-analysis-card">
        <div class="ai-card-header">
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Deep Architectural Synthesis</span>
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
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
    `;
  }

  function createExecAiCardHtml(ai) {
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
      <div class="ai-analysis-card exec-ai-card">
        <div class="ai-card-header">
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Command Intent & Execution Analysis</span>
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
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
    `;
  }

  async function triggerExplainCommand(ev, slotEl, buttonEl) {
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

  function renderTimeline(events, gitEvents = []) {
    if (!DOM.timelineContainer) return;
    if (!events || events.length === 0) {
      DOM.timelineContainer.dataset.signature = 'empty';
      DOM.timelineContainer.innerHTML = '<div class="empty-state">No events recorded for this project yet.</div>';
      return;
    }

    // Anti-flicker signature check: compare filter, count, event IDs, types, and whether ai_summary exists
    const signature = `${state.timelineFilter}_${events.length}_` + events.map((e) => `${e.id}_${e.event_type || 'EDIT'}_${Boolean(e.ai_summary)}_${state.analyzingEventIds.has(e.id)}`).join('|');
    if (DOM.timelineContainer.dataset.signature === signature) {
      return;
    }
    DOM.timelineContainer.dataset.signature = signature;

    const fragment = document.createDocumentFragment();

    events.forEach((ev) => {
      const type = ev.event_type || 'EDIT';

      if (type === 'EDIT') {
        const card = document.createElement('div');
        card.className = 'timeline-card';

        const entrypointFile = ev.first_file_touched || (ev.patches && ev.patches[0] ? ev.patches[0].file_path : null);
        const patchList = ev.patches || [];

        // Check for associated micro-commit
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

        card.innerHTML = `
          <div class="timeline-card-header">
            <div class="timeline-badge-group">
              <span class="burst-badge">${escapeHtml(burstLabelText)}</span>
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
              <button class="btn btn-danger-outline btn-sm btn-rollback-burst" data-event-id="${ev.id}" title="Rollback this burst: undo git micro-commit and move burst to trash">
                ↩ Rollback Burst
              </button>
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
            btnRollback.innerHTML = '<span class="spinner-sm"></span> Rolling back…';
            try {
              const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/rollback-burst/${ev.id}`, {
                method: 'POST',
              });
              const data = await res.json();
              if (!res.ok || !data.success) {
                throw new Error(data.message || data.error || 'Rollback failed');
              }
              btnRollback.disabled = false;
              btnRollback.innerHTML = '↩ Rollback Burst';
              showToast('✓ Burst rolled back and moved to Trash.', 'success');
              await updateTrashCount();
              await loadProjectEvents(state.selectedProject);
              await loadProjectSessions(state.selectedProject);
              await refreshProjects();
            } catch (err) {
              console.error('Rollback error:', err);
              showToast(`Rollback failed: ${err.message}`, 'error');
              btnRollback.disabled = false;
              btnRollback.innerHTML = '↩ Rollback Burst';
            }
          });
        }

        card.querySelector('.btn-inspect-diff').addEventListener('click', () => {
          selectTab('diffs');
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

  function renderAnalyzedFiles(readEvents) {
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

  function renderTerminal(execEvents) {
    if (!DOM.terminalFeedContainer) return;
    DOM.terminalFeedContainer.innerHTML = '';
    if (!execEvents || execEvents.length === 0) {
      DOM.terminalFeedContainer.innerHTML = '<div class="empty-state">No terminal commands recorded yet.</div>';
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

  function renderDashboardRecentEvents(recentEvents) {
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

  // ---------------------------------------------------------------------------
  // Diff Viewer Management (3-Tier Hierarchical Navigation)
  // ---------------------------------------------------------------------------

  function renderSessionDropdown(sessions) {
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

  function setupDiffViewerHierarchy(editEvents) {
    if (!DOM.diffSessionSelect) return;
    DOM.diffSessionSelect.innerHTML = '';
    if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '';

    let events = editEvents;
    if (!events || events.length === 0) {
      events = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    }

    if (!events || events.length === 0) {
      renderSessionDropdown([]);
      if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '<option value="">(No edit bursts available)</option>';
      if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = '0';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No edit events available to inspect for this project.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    // Determine sessions: use state.sessionsData if available, otherwise aggregate from events
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

    // Filter out empty sessions: do not return sessions where burst_count == 0 unless active
    sessions = sessions.filter((s) => s.is_active || (s.burst_count ?? s.bursts_count ?? 0) > 0);

    if (sessions.length === 0) {
      renderSessionDropdown([]);
      if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '<option value="">(No edit bursts available)</option>';
      if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = '0';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No edit events available to inspect for this project.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    // Sort sessions ascending by session_id
    sessions.sort((a, b) => ((a.session_id ?? a.id ?? 1) - (b.session_id ?? b.id ?? 1)));

    // Populate Level 1 (Session Selector) using safe label resolution
    renderSessionDropdown(sessions);

    // Select target session: prioritize current state.selectedSessionId if valid, otherwise latest
    let targetSessionId = null;
    if (state.selectedSessionId && sessions.some((s) => (s.session_id ?? s.id) === state.selectedSessionId)) {
      targetSessionId = state.selectedSessionId;
    } else {
      const lastS = sessions[sessions.length - 1];
      targetSessionId = lastS.session_id ?? lastS.id;
    }

    DOM.diffSessionSelect.value = String(targetSessionId);
    onDiffSessionSelected(targetSessionId);
  }

  function onDiffSessionSelected(sessionId, preferredEventId) {
    const sid = parseInt(sessionId, 10);
    state.selectedSessionId = isNaN(sid) ? null : sid;

    const allEditEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    const sessionBursts = allEditEvents.filter((ev) => (ev.session_id || 1) === (state.selectedSessionId || 1));

    if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = sessionBursts.length;

    if (!DOM.diffBurstSelect) return;
    DOM.diffBurstSelect.innerHTML = '';

    if (sessionBursts.length === 0) {
      DOM.diffBurstSelect.innerHTML = '<option value="">(No bursts in this session)</option>';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this session.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    sessionBursts.forEach((ev) => {
      const opt = document.createElement('option');
      opt.value = ev.id;
      const patchCount = ev.patches ? ev.patches.length : 0;
      const timeStr = formatISTTime(ev.timestamp);
      const commitTag = ev.commit_hash ? ` [${ev.commit_hash.slice(0, 7)}]` : '';
      const file = ev.first_file_touched || 'edit';
      const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
      opt.textContent = `${burstLabel} • ${timeStr} [${patchCount} file(s)]${commitTag} — ${file}`;
      DOM.diffBurstSelect.appendChild(opt);
    });

    let targetEventId = null;
    if (preferredEventId && sessionBursts.some((ev) => ev.id === preferredEventId)) {
      targetEventId = preferredEventId;
    } else if (state.selectedEventId && sessionBursts.some((ev) => ev.id === state.selectedEventId)) {
      targetEventId = state.selectedEventId;
    } else {
      targetEventId = sessionBursts[sessionBursts.length - 1].id;
    }

    DOM.diffBurstSelect.value = String(targetEventId);
    onDiffBurstSelected(targetEventId);
  }

  function onDiffBurstSelected(eventId) {
    state.selectedEventId = parseInt(eventId, 10);
    const ev = state.eventsData.find((x) => x.id === state.selectedEventId);

    // Sync Level 1 session dropdown if event has a different session_id
    if (ev && ev.session_id && ev.session_id !== state.selectedSessionId) {
      state.selectedSessionId = ev.session_id;
      if (DOM.diffSessionSelect) DOM.diffSessionSelect.value = String(ev.session_id);
    }

    // Step 3 Header: Render AI Intent & Summary Banner
    if (DOM.diffAiSlot) {
      if (ev && ev.ai_summary) {
        DOM.diffAiSlot.innerHTML = createAiCardHtml(ev.ai_summary);
      } else if (ev) {
        const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
        DOM.diffAiSlot.innerHTML = `
          <div class="diff-ai-banner-empty">
            <div class="diff-ai-banner-left">
              <span class="ai-sparkle">✨</span>
              <span>No AI architectural synthesis generated for ${escapeHtml(burstLabel)} yet.</span>
            </div>
            <button class="btn btn-secondary btn-xs btn-diff-analyze" id="btn-diff-analyze-${ev.id}">
              Analyze ${escapeHtml(burstLabel)}
            </button>
          </div>
        `;
        const analyzeBtn = document.getElementById(`btn-diff-analyze-${ev.id}`);
        if (analyzeBtn) {
          analyzeBtn.addEventListener('click', async () => {
            analyzeBtn.disabled = true;
            analyzeBtn.textContent = 'Analyzing…';
            try {
              const res = await apiPost(`/api/project/${encodeURIComponent(state.selectedProject)}/events/${ev.id}/analyze`, {});
              if (res.ai_summary) {
                ev.ai_summary = res.ai_summary;
                DOM.diffAiSlot.innerHTML = createAiCardHtml(ev.ai_summary);
                renderTimelineFromState();
              }
            } catch (err) {
              showToast(`Analysis failed: ${err.message}`, 'error');
              analyzeBtn.disabled = false;
              analyzeBtn.textContent = `Analyze ${burstLabel}`;
            }
          });
        }
      } else {
        DOM.diffAiSlot.innerHTML = '';
      }
    }

    if (!ev || !ev.patches || ev.patches.length === 0) {
      state.selectedFilePath = null;
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      DOM.diffFilesList.innerHTML = '<div class="empty-hint">No patches for this event</div>';
      DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this burst.</div>';
      DOM.diffCurrentFileName.textContent = 'None';
      DOM.statAdditions.textContent = '+0';
      DOM.statDeletions.textContent = '-0';
      return;
    }

    if (DOM.diffFileCount) DOM.diffFileCount.textContent = ev.patches.length;
    DOM.diffFilesList.innerHTML = '';

    // Check if previously selected file exists in this event's patches
    let targetPatch = ev.patches[0];
    if (state.selectedFilePath) {
      const found = ev.patches.find((p) => p.file_path === state.selectedFilePath);
      if (found) targetPatch = found;
    }

    ev.patches.forEach((p) => {
      const btn = document.createElement('button');
      btn.className = `diff-file-item ${p === targetPatch ? 'active' : ''}`;

      // Calculate lines added/deleted if not already present
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

      btn.innerHTML = `
        <span class="diff-file-name" title="${escapeHtml(p.file_path)}">📄 ${escapeHtml(p.file_path)}</span>
        <span class="diff-file-stats">
          ${adds > 0 ? `<span class="stat-badge-add">+${adds}</span>` : ''}
          ${dels > 0 ? `<span class="stat-badge-del">-${dels}</span>` : ''}
        </span>
      `;

      btn.addEventListener('click', () => {
        state.selectedFilePath = p.file_path;
        document.querySelectorAll('.diff-file-item').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        renderPatchDiff(p);
      });
      DOM.diffFilesList.appendChild(btn);
    });

    state.selectedFilePath = targetPatch.file_path;
    // Render the target patch
    renderPatchDiff(targetPatch);
  }

  // Backwards compatibility aliases
  function setupDiffEventDropdown(editEvents) {
    setupDiffViewerHierarchy(editEvents);
  }

  function onDiffEventSelected(eventId) {
    onDiffBurstSelected(eventId);
  }

  function renderPatchDiff(patch) {
    if (!patch) return;
    DOM.diffCurrentFileName.textContent = patch.file_path;
    const diffContent = patch.diff_content || '';

    if (!diffContent.trim()) {
      DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No text diff content recorded for this file.</div>';
      DOM.statAdditions.textContent = '+0';
      DOM.statDeletions.textContent = '-0';
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

    DOM.statAdditions.textContent = `+${additions}`;
    DOM.statDeletions.textContent = `-${deletions}`;

    DOM.diffCodeContainer.innerHTML = '';
    DOM.diffCodeContainer.appendChild(fragment);
  }

  // ---------------------------------------------------------------------------
  // Logs Management
  // ---------------------------------------------------------------------------

  async function loadProjectLogs(projectName) {
    if (!projectName) return;
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

  // ---------------------------------------------------------------------------
  // Real-time Streaming (SSE with Fallback Polling)
  // ---------------------------------------------------------------------------

  function initSSE() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }

    try {
      const es = new EventSource('/api/stream');
      state.eventSource = es;

      es.addEventListener('open', () => {
        state.sseActive = true;
        DOM.connStatus.querySelector('.status-dot').className = 'status-dot connected';
        DOM.connText.textContent = 'SSE Stream Active';
      });

      es.addEventListener('connected', (e) => {
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
        DOM.connStatus.querySelector('.status-dot').className = 'status-dot disconnected';
        DOM.connText.textContent = 'Reconnecting / Polling…';
        es.close();
        state.eventSource = null;

        // Fallback to polling
        if (!state.pollTimer) {
          state.pollTimer = setInterval(pollLiveStatus, 1500);
        }
      };
    } catch (_) {
      // Direct fallback to polling if EventSource is not supported
      if (!state.pollTimer) {
        state.pollTimer = setInterval(pollLiveStatus, 1500);
      }
    }
  }

  function renderLiveTicker(actions) {
    if (!DOM.liveTickerFeed) return;
    if (!actions || actions.length === 0) {
      if (DOM.liveActionCount) DOM.liveActionCount.textContent = '0 actions';
      DOM.liveTickerFeed.innerHTML = '<div class="ticker-empty">Listening for file modifications, file inspections, and executions…</div>';
      return;
    }

    if (DOM.liveActionCount) {
      DOM.liveActionCount.textContent = `${actions.length} action${actions.length === 1 ? '' : 's'}`;
    }

    // Check signature to avoid DOM churn
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

  async function handleResumeProject() {
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
      const res = await fetch(`/api/project/${encodeURIComponent(p.name)}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to resume project');
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

  function handleLivePing(payload) {
    if (!payload || !payload.workers) return;
    const workers = payload.workers;

    // Check if active project status changed or if any worker heartbeat updated
    const activeProject = getSelectedProjectInfo();
    if (activeProject && state.selectedTier === 'current') {
      const match = workers.find((w) => w.project === activeProject.name);
      if (match) {
        activeProject.is_active = match.alive;
        activeProject.uptime_s = match.uptime_s;
        activeProject.memory_mb = match.memory_mb;
        activeProject.pid = match.pid;
        activeProject.session_id = match.session_id;
        if (match.debounce) activeProject.debounce = match.debounce;
        if (Array.isArray(match.recent_actions)) {
          activeProject.recent_actions = match.recent_actions;
          renderLiveTicker(match.recent_actions);
        }
        updateHeaderTelemetry();

        // Refresh events if count might have changed
        if (state.activeTab === 'dashboard' || state.activeTab === 'timeline' || state.activeTab === 'diffs') {
          loadProjectEvents(activeProject.name);
        }
        if (state.activeTab === 'logs') {
          loadProjectLogs(activeProject.name);
        }
      }
    }
  }

  async function pollLiveStatus() {
    try {
      const statusData = await apiGet('/api/status');
      handleLivePing(statusData);
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Modal & Project Actions
  // ---------------------------------------------------------------------------

  function openNewSessionModal() {
    DOM.formNewSession.reset();
    DOM.slugPreview.textContent = 'my_project';
    DOM.debounceValDisplay.innerHTML = '3.5s <span class="badge badge-recommended">Recommended</span>';
    DOM.rngDebounce.value = '3.5';
    if (DOM.selIdeProfile) DOM.selIdeProfile.value = 'antigravity';
    if (DOM.groupCustomIdeMarker) DOM.groupCustomIdeMarker.classList.add('hidden');
    if (DOM.inpCustomIdeMarker) DOM.inpCustomIdeMarker.value = '';
    DOM.modalError.classList.add('hidden');
    DOM.modalSpinner.classList.add('hidden');
    DOM.modalSubmitBtn.disabled = false;
    DOM.modalNewSession.classList.add('open');
  }

  function closeNewSessionModal() {
    DOM.modalNewSession.classList.remove('open');
  }

  async function handleLaunchNewSession(e) {
    e.preventDefault();
    const name = DOM.inpProjectName.value.trim();
    const targetPath = DOM.inpTargetPath.value.trim();
    const scaffold = DOM.chkScaffold.checked;
    const debounce = parseFloat(DOM.rngDebounce.value);

    if (!name || !targetPath) {
      DOM.modalError.textContent = 'Please fill in both project name and workspace path.';
      DOM.modalError.classList.remove('hidden');
      return;
    }

    DOM.modalError.classList.add('hidden');
    DOM.modalSpinner.classList.remove('hidden');
    DOM.modalSubmitBtn.disabled = true;

    try {
      const trackReads = DOM.chkPowerReads ? DOM.chkPowerReads.checked : true;
      const trackExec = DOM.chkPowerExec ? DOM.chkPowerExec.checked : true;
      const shadowGit = DOM.chkPowerShadowGit ? DOM.chkPowerShadowGit.checked : true;
      const gitInitPrimary = DOM.chkPowerGitInit ? DOM.chkPowerGitInit.checked : false;
      const ideProfile = DOM.selIdeProfile ? DOM.selIdeProfile.value : 'antigravity';
      const ideCustomMarker = (DOM.selIdeProfile && DOM.selIdeProfile.value === 'custom' && DOM.inpCustomIdeMarker)
        ? DOM.inpCustomIdeMarker.value.trim()
        : null;

      const res = await apiPost('/api/projects/start', {
        project: name,
        path: targetPath,
        scaffold: scaffold,
        debounce: debounce,
        track_reads: trackReads,
        track_exec: trackExec,
        shadow_git: shadowGit,
        git_init_primary: gitInitPrimary,
        ide_profile: ideProfile,
        ide_custom_marker: ideCustomMarker,
      });

      closeNewSessionModal();
      showToast(`Worker started for '${name}' (PID ${res.result ? res.result.pid : ''})`, 'success');
      await refreshProjects();
      selectProject(name, 'current');
    } catch (err) {
      DOM.modalError.textContent = `Launch failed: ${err.message}`;
      DOM.modalError.classList.remove('hidden');
    } finally {
      DOM.modalSpinner.classList.add('hidden');
      DOM.modalSubmitBtn.disabled = false;
    }
  }

  async function handleStopActiveProject() {
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

  function handleExportSession() {
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

  // ---------------------------------------------------------------------------
  // GitHub Settings & Milestone Sync Review
  // ---------------------------------------------------------------------------

  async function openGitSettingsModal() {
    if (!DOM.modalGitSettings) return;
    if (DOM.gitTestStatus) {
      DOM.gitTestStatus.className = 'git-test-badge hidden';
      DOM.gitTestStatus.textContent = '';
    }
    DOM.modalGitSettings.classList.add('open');

    try {
      const res = await fetch('/api/git/config');
      const data = await res.json();
      if (data.success && data.config) {
        const c = data.config;
        DOM.inpGitUsername.value = c.github_username || '';
        DOM.inpGitToken.value = c.github_token || '';
        DOM.inpGitDefaultRemote.value = c.default_remote || 'origin';
        DOM.inpGitDefaultBranch.value = c.default_branch || 'main';
        DOM.chkGitAutoChangelog.checked = c.auto_generate_changelog !== false;
        if (!DOM.inpGitTestUrl.value) {
          DOM.inpGitTestUrl.value = c.default_remote && c.default_remote.startsWith('http') ? c.default_remote : '';
        }
      }
    } catch (err) {
      console.warn('Failed to load git config:', err);
    }
  }

  function closeGitSettingsModal() {
    if (DOM.modalGitSettings) DOM.modalGitSettings.classList.remove('open');
  }

  async function handleSaveGitSettings(e) {
    if (e) e.preventDefault();
    const payload = {
      github_username: DOM.inpGitUsername.value.trim(),
      github_token: DOM.inpGitToken.value.trim(),
      default_remote: DOM.inpGitDefaultRemote.value.trim() || 'origin',
      default_branch: DOM.inpGitDefaultBranch.value.trim() || 'main',
      auto_generate_changelog: DOM.chkGitAutoChangelog.checked,
    };

    try {
      const res = await fetch('/api/git/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to save settings');
      }
      showToast('GitHub settings saved successfully.', 'success');
      closeGitSettingsModal();
    } catch (err) {
      showToast(`Save failed: ${err.message}`, 'error');
    }
  }

  async function handleTestGitConnection() {
    const testUrl = DOM.inpGitTestUrl.value.trim() || DOM.inpGitDefaultRemote.value.trim();
    const user = DOM.inpGitUsername.value.trim();
    const token = DOM.inpGitToken.value.trim();

    DOM.gitTestStatus.className = 'git-test-badge testing';
    DOM.gitTestStatus.textContent = 'Testing connection via git ls-remote…';
    DOM.btnTestGitConnection.disabled = true;

    try {
      const res = await fetch('/api/git/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: testUrl,
          github_username: user,
          github_token: token,
        }),
      });
      const data = await res.json();
      if (data.success) {
        DOM.gitTestStatus.className = 'git-test-badge success';
        DOM.gitTestStatus.textContent = `✔ ${data.message || 'Connection successful!'}`;
      } else {
        DOM.gitTestStatus.className = 'git-test-badge error';
        DOM.gitTestStatus.textContent = `✖ ${data.message || 'Connection failed.'}`;
      }
    } catch (err) {
      DOM.gitTestStatus.className = 'git-test-badge error';
      DOM.gitTestStatus.textContent = `✖ Error: ${err.message}`;
    } finally {
      DOM.btnTestGitConnection.disabled = false;
    }
  }

  async function handleOpenSyncReview() {
    if (!state.selectedProject) {
      showToast('Please select a project first.', 'error');
      return;
    }

    if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = true;
    showToast('Synthesizing Conventional Commit & Changelog…', 'info');

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/prepare-sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to prepare sync');
      }

      DOM.inpSyncCommitTitle.value = data.commit_title || 'feat: milestone sync';
      DOM.inpSyncCommitBody.value = data.commit_body || '';
      DOM.inpSyncChangelogContent.value = data.changelog_entry || '';
      DOM.inpSyncRemote.value = data.remote_url || data.remote || 'origin';
      DOM.inpSyncBranch.value = data.branch || 'main';
      DOM.syncEventsCount.textContent = data.events_count || 0;
      DOM.syncProviderBadge.textContent = (data.provider || 'AI').toUpperCase();

      DOM.syncError.classList.add('hidden');
      DOM.syncError.textContent = '';
      DOM.modalSyncReview.classList.add('open');
    } catch (err) {
      console.error('Prepare sync error:', err);
      showToast(`Prepare sync failed: ${err.message}`, 'error');
    } finally {
      if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = false;
    }
  }

  function closeSyncReviewModal() {
    if (DOM.modalSyncReview) DOM.modalSyncReview.classList.remove('open');
  }

  async function handleConfirmSyncPush() {
    if (!state.selectedProject) return;

    const commitTitle = DOM.inpSyncCommitTitle.value.trim();
    if (!commitTitle) {
      DOM.syncError.textContent = 'Commit title is required.';
      DOM.syncError.classList.remove('hidden');
      return;
    }

    const commitBody = DOM.inpSyncCommitBody.value.trim();
    const fullCommitMsg = commitBody ? `${commitTitle}\n\n${commitBody}` : commitTitle;
    const changelogContent = DOM.inpSyncChangelogContent.value.trim();
    let remote = DOM.inpSyncRemote.value.trim() || 'origin';
    const branch = DOM.inpSyncBranch.value.trim() || 'main';
    const autoCreate = DOM.chkSyncAutoCreateRepo ? DOM.chkSyncAutoCreateRepo.checked : false;

    DOM.syncError.classList.add('hidden');
    DOM.btnConfirmSyncPush.disabled = true;
    DOM.syncSpinner.classList.remove('hidden');

    function updateStepper(step, pct, msg) {
      if (DOM.syncStepperContainer) DOM.syncStepperContainer.classList.remove('hidden');
      if (DOM.syncStepperFill) DOM.syncStepperFill.style.width = `${pct}%`;
      if (DOM.syncStepperMsg) DOM.syncStepperMsg.textContent = msg;
      [DOM.step1, DOM.step2, DOM.step3].forEach((el, idx) => {
        if (!el) return;
        const num = idx + 1;
        el.classList.remove('active', 'done');
        if (num < step) el.classList.add('done');
        else if (num === step) el.classList.add('active');
      });
    }

    try {
      // Step 1: Synthesize & verify changelog
      updateStepper(1, 33, '1/3 Generating semantic changelog & commit structure…');
      await new Promise((r) => setTimeout(r, 350));

      if (autoCreate) {
        // Step 2: Remote verification / creation
        updateStepper(2, 66, '2/3 Verifying remote repository on GitHub…');

        let repoName = state.selectedProject;
        if (remote.startsWith('http://') || remote.startsWith('https://')) {
          const parts = remote.replace(/\.git$/, '').split('/');
          repoName = parts[parts.length - 1] || state.selectedProject;
        }

        const createRes = await fetch('/api/git/create-repo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_name: state.selectedProject,
            repo_name: repoName,
            private: false,
          }),
        });
        const createData = await createRes.json();
        if (!createRes.ok || !createData.success) {
          throw new Error(createData.message || createData.error || 'Failed to auto-create GitHub repository');
        }
        if (createData.clone_url) {
          remote = createData.clone_url;
          DOM.inpSyncRemote.value = createData.clone_url;
        }
        if (createData.created) {
          showToast(`Repository '${repoName}' created on GitHub!`, 'info');
        }
      } else {
        updateStepper(2, 66, '2/3 Remote repository verified…');
        await new Promise((r) => setTimeout(r, 250));
      }

      // Step 3: Staging, committing & pushing
      updateStepper(3, 90, '3/3 Staging, committing & pushing to GitHub…');

      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/git-push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remote_url: remote,
          remote: remote,
          branch: branch,
          commit_message: fullCommitMsg,
          changelog_content: changelogContent,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Git push failed');
      }

      updateStepper(3, 100, '✔ Pushed to GitHub successfully!');
      showToast(data.message || `Successfully pushed to ${remote}/${branch}!`, 'success');
      setTimeout(async () => {
        closeSyncReviewModal();
        await refreshProjects();
        loadProjectSpecs(state.selectedProject);
      }, 700);
    } catch (err) {
      console.error('Git push error:', err);
      DOM.syncError.textContent = `Git push failed: ${err.message}`;
      DOM.syncError.classList.remove('hidden');
      showToast(`Git push failed: ${err.message}`, 'error');
    } finally {
      DOM.btnConfirmSyncPush.disabled = false;
      DOM.syncSpinner.classList.add('hidden');
    }
  }

  // ---------------------------------------------------------------------------
  // Batch AI Analysis
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // Async Batch AI Analysis & Global Floating Job Tray
  // ---------------------------------------------------------------------------

  let batchPollInterval = null;

  function updateGlobalTray(projectName, analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0) {
    if (!DOM.globalJobTray) return;
    DOM.globalJobTray.classList.remove('hidden');

    if (DOM.trayProjectName) {
      DOM.trayProjectName.textContent = `Project: ${projectName || state.selectedProject || 'Workspace'}`;
    }

    const pct = total > 0 ? Math.min(100, Math.round((analyzed / total) * 100)) : 0;
    if (DOM.trayProgressBar) DOM.trayProgressBar.style.width = `${pct}%`;
    if (DOM.trayPercent) DOM.trayPercent.textContent = `${pct}%`;
    if (DOM.trayCounts) DOM.trayCounts.textContent = `Analyzed ${analyzed} / ${total} bursts`;

    if (isCoolingDown && cooldownRemaining > 0) {
      if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.remove('hidden');
      if (DOM.trayCooldownText) DOM.trayCooldownText.textContent = `Rate-Limit Cooldown: ${Math.round(cooldownRemaining)}s remaining`;
    } else {
      if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.add('hidden');
    }

    if (status === 'completed') {
      if (DOM.trayTitle) DOM.trayTitle.textContent = 'Batch Analysis Complete ✔';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
      setTimeout(() => {
        if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
      }, 6000);
    } else if (status === 'failed') {
      if (DOM.trayTitle) DOM.trayTitle.textContent = 'Batch Analysis Error ⚠️';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
    } else {
      if (DOM.trayTitle) DOM.trayTitle.textContent = isCoolingDown ? 'AI Queue Cooling Down ⏳' : 'AI Batch Synthesis Active';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'inline-block';
    }
  }

  function hideGlobalTray() {
    if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
  }

  if (DOM.trayCloseBtn) {
    DOM.trayCloseBtn.onclick = hideGlobalTray;
  }

  function showBatchProgress(analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0) {
    if (!DOM.batchProgressContainer) return;
    DOM.batchProgressContainer.style.display = 'block';

    const pct = total > 0 ? Math.min(100, Math.round((analyzed / total) * 100)) : 0;
    if (DOM.batchProgressFill) DOM.batchProgressFill.style.width = `${pct}%`;
    if (DOM.batchProgressPct) DOM.batchProgressPct.textContent = `${pct}%`;

    if (DOM.batchSpinner) {
      DOM.batchSpinner.style.display = status === 'running' ? 'inline-block' : 'none';
    }

    if (status === 'running') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = isCoolingDown ? 'AI Queue Rate-Limit Cooldown ⏳' : 'Synthesizing AI Intelligence (Background)…';
      if (DOM.batchProgressDetail) {
        if (isCoolingDown && cooldownRemaining > 0) {
          DOM.batchProgressDetail.textContent = `Rate-limit cooldown active (${Math.round(cooldownRemaining)}s remaining). Resuming automatically…`;
        } else {
          DOM.batchProgressDetail.textContent = `Analyzing Burst ${analyzed} of ${total} (${pct}%)…`;
        }
      }
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'none';
    } else if (status === 'completed') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = 'Batch AI Analysis Complete ✔';
      if (DOM.batchProgressDetail) DOM.batchProgressDetail.textContent = `Successfully synthesized ${total} bursts!`;
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'none';
      setTimeout(() => {
        hideBatchProgress();
      }, 5000);
    } else if (status === 'failed') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = 'Batch Analysis Halted / Error ⚠️';
      if (DOM.batchProgressDetail) DOM.batchProgressDetail.textContent = `Error at burst ${analyzed} of ${total}: ${errorMsg || 'Failed'}`;
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'inline-block';
    }
  }

  function hideBatchProgress() {
    if (DOM.batchProgressContainer) DOM.batchProgressContainer.style.display = 'none';
  }

  function startBatchProgressPolling(projectName) {
    if (batchPollInterval) clearInterval(batchPollInterval);

    showBatchProgress(0, 1, 'running', null, false, 0);
    updateGlobalTray(projectName, 0, 1, 'running', null, false, 0);

    batchPollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/project/${encodeURIComponent(projectName)}/analyze-batch/status`);
        const data = await res.json();
        const cooling = !!data.cooling_down;
        const cooldownS = Number(data.cooldown_remaining_s || 0);

        if (data.status === 'running') {
          showBatchProgress(data.analyzed_count || 0, data.total_events || 1, 'running', null, cooling, cooldownS);
          updateGlobalTray(projectName, data.analyzed_count || 0, data.total_events || 1, 'running', null, cooling, cooldownS);
        } else if (data.status === 'completed') {
          clearInterval(batchPollInterval);
          batchPollInterval = null;
          showBatchProgress(data.total_events || 0, data.total_events || 0, 'completed', null, false, 0);
          updateGlobalTray(projectName, data.total_events || 0, data.total_events || 0, 'completed', null, false, 0);
          if (DOM.btnBatchAnalyzeSession) {
            DOM.btnBatchAnalyzeSession.disabled = false;
            DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
          }
          showToast(`Batch AI synthesis completed successfully for ${projectName}!`, 'success', 'Batch Analysis');
          await loadProjectEvents(projectName);
        } else if (data.status === 'failed') {
          clearInterval(batchPollInterval);
          batchPollInterval = null;
          showBatchProgress(data.analyzed_count || 0, data.total_events || 0, 'failed', data.error, false, 0);
          updateGlobalTray(projectName, data.analyzed_count || 0, data.total_events || 0, 'failed', data.error, false, 0);
          if (DOM.btnBatchAnalyzeSession) {
            DOM.btnBatchAnalyzeSession.disabled = false;
            DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
          }
          showToast(`Batch AI synthesis halted: ${data.error || 'Failed'}`, 'error', 'Batch Analysis Failed');
        }
      } catch (err) {
        console.warn('Batch status polling error:', err);
      }
    }, 1200);
  }

  async function handleBatchAnalyzeSession(forceAll = false) {
    if (!state.selectedProject) {
      showToast('Please select a project first.', 'error');
      return;
    }

    if (DOM.btnBatchAnalyzeSession) {
      DOM.btnBatchAnalyzeSession.disabled = true;
      DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Analyzing…';
    }

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_all: forceAll }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Batch analysis request failed');
      }

      if (data.total_events === 0) {
        showToast('All prompt bursts already analyzed!', 'success');
        hideBatchProgress();
        if (DOM.btnBatchAnalyzeSession) {
          DOM.btnBatchAnalyzeSession.disabled = false;
          DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
        }
        return;
      }

      showToast(`Batch analysis started for ${data.total_events} bursts in background.`, 'info');
      startBatchProgressPolling(state.selectedProject);
    } catch (err) {
      console.error('Batch analyze error:', err);
      showToast(`Batch analyze failed: ${err.message}`, 'error');
      if (DOM.btnBatchAnalyzeSession) {
        DOM.btnBatchAnalyzeSession.disabled = false;
        DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
      }
    }
  }

  function handleBatchRetry() {
    handleBatchAnalyzeSession(false);
  }

  // ---------------------------------------------------------------------------
  // Project Deletion (5 Scopes) & Trash Bin Management
  // ---------------------------------------------------------------------------

  async function updateTrashCount() {
    try {
      const res = await fetch('/api/trash');
      const data = await res.json();
      const count = (data && typeof data.count === 'number') ? data.count : 0;
      if (DOM.headerTrashCount) DOM.headerTrashCount.textContent = count;
      if (DOM.badgeTrashCount) DOM.badgeTrashCount.textContent = count;
      const b1 = document.getElementById('badge-trash-count');
      if (b1) b1.textContent = count;
      const b2 = document.getElementById('header-trash-count');
      if (b2) b2.textContent = count;
      document.querySelectorAll('.badge-trash-count').forEach((el) => {
        el.textContent = count;
      });
    } catch (_) {}
  }

  function openDeleteProjectModal() {
    if (!state.selectedProject) {
      showToast('Please select a project to delete.', 'error');
      return;
    }

    if (DOM.delModalProjectName) {
      DOM.delModalProjectName.textContent = state.selectedProject;
    }
    if (DOM.deleteProjectError) {
      DOM.deleteProjectError.classList.add('hidden');
      DOM.deleteProjectError.textContent = '';
    }
    if (DOM.chkDeleteRemoteGithub) {
      DOM.chkDeleteRemoteGithub.checked = false;
    }

    // Default to local_session
    const radios = document.querySelectorAll('input[name="delete-scope"]');
    radios.forEach((r) => {
      r.checked = r.value === 'local_session';
      const opt = r.closest('.scope-option');
      if (opt) opt.classList.toggle('selected', r.checked);
    });

    if (DOM.deleteBurstsContainer) DOM.deleteBurstsContainer.style.display = 'none';
    if (DOM.deleteRemoteOption) DOM.deleteRemoteOption.style.display = 'none';

    // Populate selective bursts list
    if (DOM.deleteBurstsList) {
      DOM.deleteBurstsList.innerHTML = '';
      const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
      if (editEvents.length === 0) {
        DOM.deleteBurstsList.innerHTML = '<div class="empty-hint">No edit bursts recorded in session.db</div>';
      } else {
        editEvents.forEach((ev) => {
          const item = document.createElement('label');
          item.className = 'burst-check-item';
          const file = ev.first_file_touched || 'edit';
          const summary = ev.summary ? ` — ${ev.summary}` : '';
          const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
          item.innerHTML = `
            <input type="checkbox" value="${ev.id}" class="burst-del-chk">
            <span><strong>${escapeHtml(burstLabel)}</strong> • <span class="mono">${escapeHtml(file)}</span>${escapeHtml(summary)}</span>
          `;
          DOM.deleteBurstsList.appendChild(item);
        });
      }
    }

    if (DOM.modalDeleteProject) DOM.modalDeleteProject.classList.add('open');
  }

  function closeDeleteProjectModal() {
    if (DOM.modalDeleteProject) DOM.modalDeleteProject.classList.remove('open');
  }

  function setupDeleteScopeOptions() {
    const radios = document.querySelectorAll('input[name="delete-scope"]');
    radios.forEach((r) => {
      r.addEventListener('change', () => {
        radios.forEach((radio) => {
          const opt = radio.closest('.scope-option');
          if (opt) opt.classList.toggle('selected', radio.checked);
        });

        const val = r.value;
        if (DOM.deleteBurstsContainer) {
          DOM.deleteBurstsContainer.style.display = val === 'selective_bursts' ? 'block' : 'none';
        }
        if (DOM.deleteRemoteOption) {
          DOM.deleteRemoteOption.style.display = val === 'complete_erase' ? 'block' : 'none';
        }
      });
    });

    if (DOM.btnSelectAllBursts) {
      DOM.btnSelectAllBursts.addEventListener('click', () => {
        const chks = document.querySelectorAll('.burst-del-chk');
        const allChecked = Array.from(chks).every((c) => c.checked);
        chks.forEach((c) => { c.checked = !allChecked; });
        DOM.btnSelectAllBursts.textContent = allChecked ? 'Select All' : 'Deselect All';
      });
    }
  }

  async function handleConfirmDeleteProject() {
    if (!state.selectedProject) return;

    const checkedRadio = document.querySelector('input[name="delete-scope"]:checked');
    const scope = checkedRadio ? checkedRadio.value : 'local_session';
    const deleteRemote = DOM.chkDeleteRemoteGithub ? DOM.chkDeleteRemoteGithub.checked : false;

    let burstIds = [];
    if (scope === 'selective_bursts') {
      const chks = document.querySelectorAll('.burst-del-chk:checked');
      burstIds = Array.from(chks).map((c) => parseInt(c.value, 10));
      if (burstIds.length === 0) {
        if (DOM.deleteProjectError) {
          DOM.deleteProjectError.textContent = 'Please select at least one burst to delete.';
          DOM.deleteProjectError.classList.remove('hidden');
        }
        return;
      }
    }

    if (scope === 'complete_erase' && deleteRemote) {
      if (!confirm(`CAUTION: You have chosen to PERMANENTLY DELETE the remote GitHub repository for '${state.selectedProject}'. This cannot be undone from the Trash Bin. Continue?`)) {
        return;
      }
    }

    if (DOM.deleteProjectSpinner) DOM.deleteProjectSpinner.classList.remove('hidden');
    if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.disabled = true;

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: scope,
          burst_ids: burstIds,
          delete_remote: deleteRemote,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Deletion failed');
      }

      showToast(`Project data moved to Trash successfully (${scope}).`, 'success');
      closeDeleteProjectModal();
      await updateTrashCount();

      if (scope === 'selective_bursts') {
        await loadProjectEvents(state.selectedProject);
      } else {
        await refreshProjects();
      }
    } catch (err) {
      console.error('Delete project error:', err);
      if (DOM.deleteProjectError) {
        DOM.deleteProjectError.textContent = `Deletion error: ${err.message}`;
        DOM.deleteProjectError.classList.remove('hidden');
      }
      showToast(`Delete failed: ${err.message}`, 'error');
    } finally {
      if (DOM.deleteProjectSpinner) DOM.deleteProjectSpinner.classList.add('hidden');
      if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Trash Bin Modal Operations
  // ---------------------------------------------------------------------------

  function openTrashBinModal() {
    if (DOM.modalTrashBin) DOM.modalTrashBin.classList.add('open');
    loadTrashBinItems();
  }

  function closeTrashBinModal() {
    if (DOM.modalTrashBin) DOM.modalTrashBin.classList.remove('open');
  }

  async function loadTrashBinItems() {
    if (!DOM.trashBinItemsContainer) return;
    DOM.trashBinItemsContainer.innerHTML = '<div class="empty-state">Loading trash bin…</div>';

    try {
      const res = await fetch('/api/trash');
      const data = await res.json();
      if (!data.success) throw new Error(data.message || data.error || 'Failed to list trash');

      const items = data.items || [];
      if (DOM.headerTrashCount) DOM.headerTrashCount.textContent = items.length;

      if (items.length === 0) {
        DOM.trashBinItemsContainer.innerHTML = '<div class="empty-state">Trash Bin is empty. No soft-deleted items.</div>';
        return;
      }

      const fragment = document.createDocumentFragment();
      items.forEach((item) => {
        const card = document.createElement('div');
        card.className = 'trash-item-card';

        const scopeLabel = (item.scope || 'Item').replace(/_/g, ' ');
        const timeStr = formatISTTime(item.timestamp);
        const sizeKb = item.folder_size_bytes ? `${Math.round(item.folder_size_bytes / 1024)} KB` : '';
        const details = item.details || {};
        let detailText = '';
        if (details.burst_ids) {
          detailText = `Bursts: #${details.burst_ids.join(', #')}`;
        } else if (details.moved_items) {
          detailText = `Moved: ${details.moved_items.join(', ')}`;
        }

        card.innerHTML = `
          <div class="trash-item-main">
            <div class="trash-item-top">
              <span class="trash-item-title">${escapeHtml(item.project_name || 'Unknown')}</span>
              <span class="trash-scope-badge">${escapeHtml(scopeLabel)}</span>
            </div>
            <div class="trash-item-meta">
              <span>Deleted: ${escapeHtml(timeStr)}</span>
              ${detailText ? `<span>${escapeHtml(detailText)}</span>` : ''}
              ${sizeKb ? `<span>Size: ${sizeKb}</span>` : ''}
            </div>
          </div>
          <div class="trash-item-actions">
            <button type="button" class="btn btn-secondary btn-xs btn-restore-trash" data-trash-id="${escapeHtml(item.trash_id)}">
              ⟲ Restore
            </button>
            <button type="button" class="btn btn-danger-outline btn-xs btn-purge-trash" data-trash-id="${escapeHtml(item.trash_id)}">
              ✕ Purge
            </button>
          </div>
        `;

        card.querySelector('.btn-restore-trash').addEventListener('click', (e) => handleRestoreTrash(item.trash_id, e.currentTarget));
        card.querySelector('.btn-purge-trash').addEventListener('click', (e) => handlePurgeTrashItem(item.trash_id, e.currentTarget));

        fragment.appendChild(card);
      });

      DOM.trashBinItemsContainer.innerHTML = '';
      DOM.trashBinItemsContainer.appendChild(fragment);
    } catch (err) {
      DOM.trashBinItemsContainer.innerHTML = `<div class="empty-state" style="color: #ef4444;">Failed to load trash bin: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function handleRestoreTrash(trashId, btnEl) {
    const originalText = btnEl ? btnEl.innerHTML : null;
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = 'Restoring…';
    }
    try {
      showToast(`Restoring '${trashId}'…`, 'info');
      const res = await fetch(`/api/trash/restore/${encodeURIComponent(trashId)}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Restore failed');
      }
      showToast(`Restored successfully!`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
      await refreshProjects();
    } catch (err) {
      console.error('Restore error:', err);
      showToast(`Restore failed: ${err.message}`, 'error');
      if (btnEl && originalText) {
        btnEl.disabled = false;
        btnEl.innerHTML = originalText;
      }
    }
  }

  async function handlePurgeTrashItem(trashId, btnEl) {
    if (!confirm(`Are you sure you want to PERMANENTLY PURGE '${trashId}' from disk? This cannot be restored.`)) {
      return;
    }
    const originalText = btnEl ? btnEl.innerHTML : null;
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = '<span class="spinner-sm"></span> Purging…';
    }
    try {
      const res = await fetch(`/api/trash/purge/${encodeURIComponent(trashId)}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Purge failed');
      }
      showToast(`Permanently purged '${trashId}'.`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
    } catch (err) {
      console.error('Purge error:', err);
      showToast(`Purge failed: ${err.message}`, 'error');
      if (btnEl && originalText) {
        btnEl.disabled = false;
        btnEl.innerHTML = originalText;
      }
    }
  }

  const handlePurgeTrash = handlePurgeTrashItem;

  async function handleEmptyTrashBin() {
    if (!confirm('Are you sure you want to PERMANENTLY EMPTY the entire Trash Bin? All soft-deleted items will be wiped from disk.')) {
      return;
    }
    const btn = DOM.btnEmptyTrash;
    const originalText = btn ? btn.innerHTML : null;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-sm"></span> Purging…';
    }
    try {
      const res = await fetch('/api/trash/purge-all', { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Empty trash failed');
      }
      showToast(`Trash Bin emptied (${data.purged_count || 0} items purged).`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
    } catch (err) {
      console.error('Empty trash error:', err);
      showToast(`Empty trash failed: ${err.message}`, 'error');
    } finally {
      if (btn && originalText) {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }
  }

  // ---------------------------------------------------------------------------
  // AI Settings Modal & Providers
  // ---------------------------------------------------------------------------

  function updateAiProviderBoxes(provider) {
    if (!DOM.boxProviderOllama || !DOM.boxProviderGemini || !DOM.boxProviderHeuristic) return;
    DOM.boxProviderOllama.style.display = provider === 'ollama' ? 'flex' : 'none';
    DOM.boxProviderGemini.style.display = provider === 'gemini' ? 'flex' : 'none';
    DOM.boxProviderHeuristic.style.display = provider === 'heuristic' ? 'flex' : 'none';
  }

  async function openAiSettingsModal() {
    if (!DOM.modalAiSettings) return;
    if (DOM.ollamaTestStatus) {
      DOM.ollamaTestStatus.className = 'test-status-badge hidden';
      DOM.ollamaTestStatus.textContent = '';
    }
    if (DOM.geminiTestStatus) {
      DOM.geminiTestStatus.className = 'test-status-badge hidden';
      DOM.geminiTestStatus.textContent = '';
    }
    DOM.modalAiSettings.classList.add('open');

    try {
      const res = await fetch('/api/ai/config');
      const data = await res.json();
      if (data.success && data.config) {
        const c = data.config;
        DOM.selAiProvider.value = c.preferred_provider || 'heuristic';
        DOM.inpOllamaUrl.value = c.ollama_base_url || 'http://127.0.0.1:11434';
        DOM.inpOllamaModel.value = c.ollama_model || 'qwen2.5-coder:7b';
        DOM.selGeminiModel.value = c.gemini_model || 'gemini-3.1-flash-lite';

        state.savedGeminiKeys = c.saved_gemini_keys || c.saved_keys || [];
        populateGeminiKeyVaultDropdown(state.savedGeminiKeys, c);

        updateAiProviderBoxes(DOM.selAiProvider.value);
      }
    } catch (err) {
      console.warn('Failed to load AI config:', err);
    }
  }

  function populateGeminiKeyVaultDropdown(keys, config) {
    if (!DOM.selGeminiKeyVault) return;
    DOM.selGeminiKeyVault.innerHTML = '';

    let activeKeyId = null;
    keys.forEach((k) => {
      const opt = document.createElement('option');
      opt.value = k.id;
      opt.textContent = `${k.label || k.suffix} ${k.active ? '★ Active' : ''}`;
      if (k.active) {
        opt.selected = true;
        activeKeyId = k.id;
      }
      DOM.selGeminiKeyVault.appendChild(opt);
    });

    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '+ Add / Paste New Key';
    DOM.selGeminiKeyVault.appendChild(newOpt);

    if (!activeKeyId && keys.length === 0) {
      newOpt.selected = true;
    }

    onGeminiKeyVaultSelectionChanged();
  }

  function onGeminiKeyVaultSelectionChanged() {
    if (!DOM.selGeminiKeyVault) return;
    const selectedVal = DOM.selGeminiKeyVault.value;

    if (selectedVal === '__new__') {
      DOM.inpGeminiKey.value = '';
      DOM.inpGeminiKey.disabled = false;
      DOM.inpGeminiKey.placeholder = 'AIzaSy•••••••••••••••••••••••••••••••';
      if (DOM.btnDeleteGeminiKey) DOM.btnDeleteGeminiKey.style.display = 'none';
      if (DOM.geminiActiveKeyInfo) {
        DOM.geminiActiveKeyInfo.textContent = 'New Key Entry';
        DOM.geminiActiveKeyInfo.style.color = '#a855f7';
      }
      if (DOM.helpGeminiKey) {
        DOM.helpGeminiKey.innerHTML = 'Pasting a new key saves it permanently to the vault and activates it upon save.';
      }
    } else {
      const selectedKey = (state.savedGeminiKeys || []).find((k) => k.id === selectedVal);
      if (selectedKey) {
        DOM.inpGeminiKey.value = selectedKey.masked_key || '••••••••••••••••';
        DOM.inpGeminiKey.disabled = true;
        if (DOM.btnDeleteGeminiKey) DOM.btnDeleteGeminiKey.style.display = 'inline-flex';
        if (DOM.geminiActiveKeyInfo) {
          DOM.geminiActiveKeyInfo.textContent = selectedKey.active ? '★ Active Key' : 'Saved in Vault';
          DOM.geminiActiveKeyInfo.style.color = selectedKey.active ? '#4ade80' : '#94a3b8';
        }
        if (DOM.helpGeminiKey) {
          DOM.helpGeminiKey.innerHTML = `Added: ${escapeHtml(formatIST(selectedKey.added_at))} &bull; Last used: ${escapeHtml(formatIST(selectedKey.last_used))}`;
        }
      }
    }
  }

  async function handleDeleteGeminiKey() {
    if (!DOM.selGeminiKeyVault) return;
    const selectedVal = DOM.selGeminiKeyVault.value;
    if (selectedVal === '__new__') return;

    if (!confirm('Are you sure you want to remove this API key from the vault?')) {
      return;
    }

    DOM.btnDeleteGeminiKey.disabled = true;
    try {
      const res = await fetch('/api/ai/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delete_key_id: selectedVal }),
      });
      const data = await res.json();
      if (data.success && data.config) {
        showToast('Key removed from vault.', 'success');
        state.savedGeminiKeys = data.config.saved_gemini_keys || data.config.saved_keys || [];
        populateGeminiKeyVaultDropdown(state.savedGeminiKeys, data.config);
      } else {
        showToast(`Failed to remove key: ${data.message || data.error}`, 'error');
      }
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, 'error');
    } finally {
      DOM.btnDeleteGeminiKey.disabled = false;
    }
  }

  function closeAiSettingsModal() {
    if (DOM.modalAiSettings) DOM.modalAiSettings.classList.remove('open');
  }

  async function handleTestAiConnection(provider) {
    const badge = provider === 'ollama' ? DOM.ollamaTestStatus : DOM.geminiTestStatus;
    const btn = provider === 'ollama' ? DOM.btnTestOllama : DOM.btnTestGemini;
    if (!badge || !btn) return;

    btn.disabled = true;
    badge.className = 'test-status-badge testing';
    badge.textContent = 'Connecting…';

    const payload = { provider: provider };
    if (provider === 'ollama') {
      payload.base_url = DOM.inpOllamaUrl.value.trim();
      payload.model = DOM.inpOllamaModel.value.trim();
    } else if (provider === 'gemini') {
      const vaultVal = DOM.selGeminiKeyVault ? DOM.selGeminiKeyVault.value : '__new__';
      if (vaultVal !== '__new__') {
        payload.key_id = vaultVal;
      } else {
        payload.api_key = DOM.inpGeminiKey.value.trim();
      }
      payload.model = DOM.selGeminiModel.value;
    }

    try {
      const res = await fetch('/api/ai/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        badge.className = 'test-status-badge success';
        badge.textContent = `✔ ${data.message}`;
      } else {
        badge.className = 'test-status-badge error';
        badge.textContent = `✖ ${data.message || data.error}`;
      }
    } catch (err) {
      badge.className = 'test-status-badge error';
      badge.textContent = `✖ Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  }

  async function handleSaveAiSettings(e) {
    if (e) e.preventDefault();
    const vaultVal = DOM.selGeminiKeyVault ? DOM.selGeminiKeyVault.value : '__new__';
    const payload = {
      preferred_provider: DOM.selAiProvider.value,
      ollama_base_url: DOM.inpOllamaUrl.value.trim(),
      ollama_model: DOM.inpOllamaModel.value.trim(),
      gemini_model: DOM.selGeminiModel.value,
    };

    if (vaultVal !== '__new__') {
      payload.select_key_id = vaultVal;
    } else {
      payload.gemini_api_key = DOM.inpGeminiKey.value.trim();
    }

    DOM.btnSaveAiConfig.disabled = true;
    try {
      const res = await fetch('/api/ai/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        showToast('AI settings saved successfully!', 'success');
        closeAiSettingsModal();
      } else {
        showToast(`Save failed: ${data.message || data.error}`, 'error');
      }
    } catch (err) {
      showToast(`Save failed: ${err.message}`, 'error');
    } finally {
      DOM.btnSaveAiConfig.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab Switching
  // ---------------------------------------------------------------------------

  function selectTab(tabName) {
    state.activeTab = tabName;
    DOM.navButtons.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    DOM.tabViews.forEach((view) => {
      view.classList.toggle('active', view.id === `view-${tabName}`);
    });

    if ((tabName === 'timeline' || tabName === 'diffs' || tabName === 'terminal') && state.selectedProject) {
      loadProjectEvents(state.selectedProject);
    } else if (tabName === 'logs' && state.selectedProject) {
      loadProjectLogs(state.selectedProject);
    }
  }

  // ---------------------------------------------------------------------------
  // Event Listeners Setup
  // ---------------------------------------------------------------------------

  function setupEventListeners() {
    // Nav tabs
    DOM.navButtons.forEach((btn) => {
      btn.addEventListener('click', () => selectTab(btn.dataset.tab));
    });

    // Dashboard quick link
    DOM.btnViewAllTimeline.addEventListener('click', () => selectTab('timeline'));

    // Project search
    DOM.projectFilterInput.addEventListener('input', renderProjectsList);
    DOM.btnRefreshProjects.addEventListener('click', refreshProjects);

    // Header buttons
    DOM.btnHeaderStop.addEventListener('click', handleStopActiveProject);
    if (DOM.btnHeaderResume) {
      DOM.btnHeaderResume.addEventListener('click', handleResumeProject);
    }
    if (DOM.btnTelemetryResume) {
      DOM.btnTelemetryResume.addEventListener('click', handleResumeProject);
    }
    if (DOM.btnHeaderGitSettings) {
      DOM.btnHeaderGitSettings.addEventListener('click', openGitSettingsModal);
    }
    if (DOM.btnHeaderPushGit) {
      DOM.btnHeaderPushGit.addEventListener('click', handleOpenSyncReview);
    }
    if (DOM.btnSpecsPushGh) {
      DOM.btnSpecsPushGh.addEventListener('click', handleOpenSyncReview);
    }
    DOM.btnExportSession.addEventListener('click', handleExportSession);

    // Timeline filters
    setupTimelineFilters();

    // Timeline & Diffs & Terminal
    DOM.btnRefreshTimeline.addEventListener('click', () => loadProjectEvents(state.selectedProject));
    if (DOM.btnRefreshTerminal) {
      DOM.btnRefreshTerminal.addEventListener('click', () => loadProjectEvents(state.selectedProject));
    }
    if (DOM.diffSessionSelect) {
      DOM.diffSessionSelect.addEventListener('change', (e) => onDiffSessionSelected(e.target.value));
    }
    if (DOM.diffBurstSelect) {
      DOM.diffBurstSelect.addEventListener('change', (e) => onDiffBurstSelected(e.target.value));
    }

    // Logs controls
    DOM.chkAutoscroll.addEventListener('change', (e) => {
      state.autoScrollLogs = e.target.checked;
    });
    DOM.btnRefreshLogs.addEventListener('click', () => loadProjectLogs(state.selectedProject));
    DOM.btnCopyLogs.addEventListener('click', () => {
      navigator.clipboard.writeText(DOM.logsTerminal.innerText);
      showToast('Logs copied to clipboard', 'success');
    });

    // Sidebar Accordions
    document.querySelectorAll('.accordion-header').forEach((hdr) => {
      hdr.addEventListener('click', () => {
        const section = hdr.closest('.accordion-section');
        if (section) section.classList.toggle('open');
      });
    });

    // Batch analyze session button
    if (DOM.btnBatchAnalyzeSession) {
      DOM.btnBatchAnalyzeSession.addEventListener('click', handleBatchAnalyzeSession);
    }

    // Modal controls: New Session
    DOM.btnNewSession.addEventListener('click', openNewSessionModal);
    DOM.modalCloseBtn.addEventListener('click', closeNewSessionModal);
    DOM.modalCancelBtn.addEventListener('click', closeNewSessionModal);
    DOM.formNewSession.addEventListener('submit', handleLaunchNewSession);
    if (DOM.selIdeProfile) {
      DOM.selIdeProfile.addEventListener('change', (e) => {
        if (DOM.groupCustomIdeMarker) {
          DOM.groupCustomIdeMarker.classList.toggle('hidden', e.target.value !== 'custom');
        }
      });
    }

    // Modal controls: Git Settings
    if (DOM.modalGitSettingsClose) DOM.modalGitSettingsClose.addEventListener('click', closeGitSettingsModal);
    if (DOM.modalGitSettingsCancel) DOM.modalGitSettingsCancel.addEventListener('click', closeGitSettingsModal);
    if (DOM.formGitSettings) DOM.formGitSettings.addEventListener('submit', handleSaveGitSettings);
    if (DOM.btnTestGitConnection) DOM.btnTestGitConnection.addEventListener('click', handleTestGitConnection);

    // Modal controls: Milestone Sync Review
    if (DOM.modalSyncReviewClose) DOM.modalSyncReviewClose.addEventListener('click', closeSyncReviewModal);
    if (DOM.modalSyncCancel) DOM.modalSyncCancel.addEventListener('click', closeSyncReviewModal);
    if (DOM.btnConfirmSyncPush) DOM.btnConfirmSyncPush.addEventListener('click', handleConfirmSyncPush);

    // Modal controls: AI Model Settings
    if (DOM.btnHeaderAiSettings) DOM.btnHeaderAiSettings.addEventListener('click', openAiSettingsModal);
    if (DOM.modalAiSettingsClose) DOM.modalAiSettingsClose.addEventListener('click', closeAiSettingsModal);
    if (DOM.modalAiSettingsCancel) DOM.modalAiSettingsCancel.addEventListener('click', closeAiSettingsModal);
    if (DOM.formAiSettings) DOM.formAiSettings.addEventListener('submit', handleSaveAiSettings);
    if (DOM.selAiProvider) {
      DOM.selAiProvider.addEventListener('change', (e) => updateAiProviderBoxes(e.target.value));
    }
    if (DOM.btnTestOllama) {
      DOM.btnTestOllama.addEventListener('click', () => handleTestAiConnection('ollama'));
    }
    if (DOM.btnTestGemini) {
      DOM.btnTestGemini.addEventListener('click', () => handleTestAiConnection('gemini'));
    }
    if (DOM.selGeminiKeyVault) {
      DOM.selGeminiKeyVault.addEventListener('change', onGeminiKeyVaultSelectionChanged);
    }
    if (DOM.btnDeleteGeminiKey) {
      DOM.btnDeleteGeminiKey.addEventListener('click', handleDeleteGeminiKey);
    }

    // Live slug preview
    DOM.inpProjectName.addEventListener('input', (e) => {
      DOM.slugPreview.textContent = slugify(e.target.value);
    });

    // Debounce slider display & presets
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
      DOM.debounceValDisplay.innerHTML = `${timeLabel} ${badge}`;
    });

    // Preset buttons for debounce window
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
          DOM.debounceProgressFill.style.width = '0%';
          DOM.debounceProgressFill.classList.remove('active');
          DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
          DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
          DOM.debounceStatusBadge.textContent = 'Idle / Listening';
          DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
          DOM.debounceCountdownText.textContent = '—';
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

    // Trash Bin Header & Modal
    if (DOM.btnHeaderTrash) {
      DOM.btnHeaderTrash.addEventListener('click', openTrashBinModal);
    }
    if (DOM.modalTrashBinClose) {
      DOM.modalTrashBinClose.addEventListener('click', closeTrashBinModal);
    }
    if (DOM.btnEmptyTrash) {
      DOM.btnEmptyTrash.addEventListener('click', handleEmptyTrashBin);
    }

    // Delete Project Header & Modal
    if (DOM.btnHeaderDeleteProj) {
      DOM.btnHeaderDeleteProj.addEventListener('click', openDeleteProjectModal);
    }
    if (DOM.modalDeleteProjectClose) {
      DOM.modalDeleteProjectClose.addEventListener('click', closeDeleteProjectModal);
    }
    if (DOM.modalDeleteProjectCancel) {
      DOM.modalDeleteProjectCancel.addEventListener('click', closeDeleteProjectModal);
    }
    if (DOM.btnConfirmDeleteProject) {
      DOM.btnConfirmDeleteProject.addEventListener('click', handleConfirmDeleteProject);
    }
    setupDeleteScopeOptions();

    // Batch retry
    if (DOM.btnBatchRetry) {
      DOM.btnBatchRetry.addEventListener('click', handleBatchRetry);
    }

    // Close modals on backdrop click
    DOM.modalNewSession.addEventListener('click', (e) => {
      if (e.target === DOM.modalNewSession) closeNewSessionModal();
    });
    if (DOM.modalGitSettings) {
      DOM.modalGitSettings.addEventListener('click', (e) => {
        if (e.target === DOM.modalGitSettings) closeGitSettingsModal();
      });
    }
    if (DOM.modalSyncReview) {
      DOM.modalSyncReview.addEventListener('click', (e) => {
        if (e.target === DOM.modalSyncReview) closeSyncReviewModal();
      });
    }
    if (DOM.modalAiSettings) {
      DOM.modalAiSettings.addEventListener('click', (e) => {
        if (e.target === DOM.modalAiSettings) closeAiSettingsModal();
      });
    }
    if (DOM.modalDeleteProject) {
      DOM.modalDeleteProject.addEventListener('click', (e) => {
        if (e.target === DOM.modalDeleteProject) closeDeleteProjectModal();
      });
    }
    if (DOM.modalTrashBin) {
      DOM.modalTrashBin.addEventListener('click', (e) => {
        if (e.target === DOM.modalTrashBin) closeTrashBinModal();
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Collapsible Sidebar Management
  // ---------------------------------------------------------------------------

  function setupSidebarToggle() {
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

    // Read stored preference or auto-collapse if narrow viewport
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
        // Prevent default browser bookmark shortcut
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

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  async function init() {
    setupSidebarToggle();
    setupEventListeners();
    await updateTrashCount();
    await refreshProjects();
    initSSE();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
