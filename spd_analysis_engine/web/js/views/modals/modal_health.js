/**
 * SPD Analysis Engine — Modal Health & Diagnostics Controller
 * Handles system diagnostics health grid, active path manifest copying,
 * and emergency raw worker log streaming.
 */
import { DOM } from '../../dom.js';
import { state } from '../../state.js';
import { escapeHtml, setButtonLoading } from '../../utils.js';
import { showToast } from '../../toasts.js';
import { refreshDashboard } from '../dashboard.js';

let logTailInterval = null;

export async function copyPathToClipboard(text, btnEl) {
  if (!text) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    if (btnEl) {
      const origHtml = btnEl.innerHTML;
      btnEl.innerHTML = '✔ Copied';
      btnEl.disabled = true;
      setTimeout(() => {
        btnEl.innerHTML = origHtml;
        btnEl.disabled = false;
      }, 1500);
    }
    showToast('Path copied to clipboard.', 'success');
  } catch (err) {
    showToast(`Failed to copy path: ${err.message}`, 'error');
  }
}

export async function loadEngineHealth() {
  if (DOM.btnRefreshHealth) {
    DOM.btnRefreshHealth.disabled = true;
  }

  try {
    const res = await fetch('/api/engine/health');
    const data = await res.json();

    // 1. Overall Badge
    if (DOM.healthOverallBadge) {
      const status = (data.overall_status || 'healthy').toLowerCase();
      DOM.healthOverallBadge.textContent = status.toUpperCase();
      DOM.healthOverallBadge.className = 'badge';
      if (status === 'healthy') {
        DOM.healthOverallBadge.classList.add('badge-success');
      } else if (status === 'degraded') {
        DOM.healthOverallBadge.classList.add('badge-warning');
      } else {
        DOM.healthOverallBadge.classList.add('badge-danger');
      }
    }

    // 2. Render Pipeline Grid
    renderHealthGrid(data.pipelines || {});

    // 3. Render Active Paths
    renderActivePaths(data.active_paths || {});
  } catch (err) {
    console.error('Failed to load engine health:', err);
    if (DOM.healthOverallBadge) {
      DOM.healthOverallBadge.textContent = 'DISCONNECTED';
      DOM.healthOverallBadge.className = 'badge badge-danger';
    }
    showToast(`Health check error: ${err.message}`, 'error');
  } finally {
    if (DOM.btnRefreshHealth) {
      DOM.btnRefreshHealth.disabled = false;
    }
  }
}

function renderHealthGrid(pipelines) {
  if (!DOM.healthPipelineGrid) return;

  const db = pipelines.database || {};
  const storage = pipelines.storage_tiers || {};
  const git = pipelines.shadow_git || {};
  const worker = pipelines.worker_daemon || {};
  const config = pipelines.configurations || {};

  const cards = [
    {
      title: 'SQLite WAL & Integrity',
      status: db.status === 'healthy' ? 'healthy' : (db.status === 'error' ? 'error' : 'degraded'),
      tag: db.wal_mode ? 'WAL ACTIVE' : 'NO WAL',
      desc: `Integrity: ${escapeHtml(db.integrity || 'ok')} • Databases: ${db.active_databases?.length || 0} • Stale locks: ${db.stale_locks?.length || 0}`,
    },
    {
      title: 'Filesystem Storage Tiers',
      status: storage.status === 'healthy' ? 'healthy' : 'error',
      tag: storage.status === 'healthy' ? 'ALL TIERS R/W' : 'TIER ERROR',
      desc: 'current/, last_run/, history/, trash/ verified readable & writable',
    },
    {
      title: 'Shadow Git Mirror',
      status: git.status === 'healthy' ? 'healthy' : 'degraded',
      tag: (git.dangling_locks?.length || 0) === 0 ? 'LOCKS CLEAN' : 'LOCK DETECTED',
      desc: `Repositories: ${git.repositories?.length || 0} active • Dangling locks: ${git.dangling_locks?.length || 0}`,
    },
    {
      title: 'Worker Process Daemon',
      status: worker.status === 'healthy' ? 'healthy' : (worker.status === 'idle' ? 'idle' : 'degraded'),
      tag: worker.status === 'idle' ? 'IDLE' : (worker.status === 'healthy' ? 'LIVE HEARTBEAT' : 'STALE HEARTBEAT'),
      desc: `Active workers: ${worker.workers?.length || 0} • Heartbeat timeout: 15s`,
    },
    {
      title: 'AI & Git Configurations',
      status: config.status === 'healthy' ? 'healthy' : 'degraded',
      tag: config.status === 'healthy' ? 'VALID JSON' : 'SCHEMA WARN',
      desc: 'models_config.json, git_config.json syntax & schema validated',
    },
  ];

  DOM.healthPipelineGrid.innerHTML = cards.map((c) => `
    <div class="health-card ${c.status}">
      <div class="health-card-header">
        <span class="health-card-title">${escapeHtml(c.title)}</span>
        <span class="health-status-tag ${c.status}">${escapeHtml(c.tag)}</span>
      </div>
      <div class="health-card-desc">${c.desc}</div>
    </div>
  `).join('');
}

function renderActivePaths(paths) {
  if (!DOM.healthActivePaths) return;

  const entries = [
    { label: 'Session DB', path: paths.session_db || '—' },
    { label: 'Shadow Git', path: paths.shadow_git || '—' },
    { label: 'Worker Log', path: paths.worker_log || '—' },
  ];

  DOM.healthActivePaths.innerHTML = entries.map((item) => `
    <div class="active-path-row">
      <span class="active-path-label">${escapeHtml(item.label)}</span>
      <span class="active-path-value" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</span>
      <button type="button" class="btn btn-secondary btn-xs btn-copy-active-path" data-path="${escapeHtml(item.path)}">
        Copy Path
      </button>
    </div>
  `).join('');

  DOM.healthActivePaths.querySelectorAll('.btn-copy-active-path').forEach((btn) => {
    btn.addEventListener('click', () => copyPathToClipboard(btn.dataset.path, btn));
  });
}

export async function loadWorkerLogRaw(projectName = null, lines = 100) {
  const targetProj = projectName || state.selectedProject || state.activeProject || '';

  if (DOM.rawLogTargetFilename) {
    DOM.rawLogTargetFilename.textContent = targetProj ? `${targetProj}/worker.log` : 'worker.log';
  }

  if (DOM.btnRefreshRawLogs) {
    DOM.btnRefreshRawLogs.disabled = true;
  }

  try {
    const slug = targetProj ? encodeURIComponent(targetProj) : '_';
    const res = await fetch(`/api/project/${slug}/worker-log-raw?lines=${lines}`);
    const data = await res.json();

    if (DOM.emergencyLogTerminal) {
      if (data.content && data.content.trim()) {
        DOM.emergencyLogTerminal.textContent = data.content;
      } else {
        DOM.emergencyLogTerminal.textContent = `[RAW LOG] No active log entries recorded for '${targetProj || 'engine'}'.`;
      }
      DOM.emergencyLogTerminal.scrollTop = DOM.emergencyLogTerminal.scrollHeight;
    }
  } catch (err) {
    if (DOM.emergencyLogTerminal) {
      DOM.emergencyLogTerminal.textContent = `[ERROR] Failed to fetch raw worker logs: ${err.message}`;
    }
  } finally {
    if (DOM.btnRefreshRawLogs) {
      DOM.btnRefreshRawLogs.disabled = false;
    }
  }
}

export function startLogAutoTail() {
  stopLogAutoTail();
  logTailInterval = setInterval(() => {
    const activeTab = document.querySelector('.settings-tab-btn.active');
    if (activeTab && activeTab.dataset.tab === 'health') {
      loadWorkerLogRaw(null, 100);
    } else {
      stopLogAutoTail();
      if (DOM.chkAutoTailRawLogs) DOM.chkAutoTailRawLogs.checked = false;
    }
  }, 3000);
}

export function stopLogAutoTail() {
  if (logTailInterval) {
    clearInterval(logTailInterval);
    logTailInterval = null;
  }
}

export function initModalHealthListeners() {
  if (DOM.btnRefreshHealth) {
    DOM.btnRefreshHealth.addEventListener('click', () => loadEngineHealth());
  }

  if (DOM.btnRefreshRawLogs) {
    DOM.btnRefreshRawLogs.addEventListener('click', () => loadWorkerLogRaw());
  }

  if (DOM.chkAutoTailRawLogs) {
    DOM.chkAutoTailRawLogs.addEventListener('change', (e) => {
      if (e.target.checked) {
        startLogAutoTail();
      } else {
        stopLogAutoTail();
      }
    });
  }

  const purgeBtn = DOM.btnEmergencyPurge || document.getElementById('btn-emergency-purge');
  if (purgeBtn) {
    purgeBtn.addEventListener('click', () => handleEmergencyPurgeZombies());
  }
}

export async function handleEmergencyPurgeZombies() {
  const btn = DOM.btnEmergencyPurge || document.getElementById('btn-emergency-purge');
  if (!btn) return;

  setButtonLoading(btn, true, 'Purging...');

  try {
    const res = await fetch('/api/system/recovery/purge-zombies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Terminated ${data.killed_count || 0} zombie processes.`, 'success');
      await refreshDashboard();
      await loadEngineHealth();
    } else {
      showToast(`Purge failed: ${data.error || 'Unknown error'}`, 'error');
    }
  } catch (err) {
    showToast(`Purge failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}
