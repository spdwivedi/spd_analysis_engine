/**
 * SPD Analysis Engine — Notifications, Toast System & Batch Progress Trays
 */
import { DOM } from './dom.js';

export function showToast(message, type = 'success', title = null) {
  const container = DOM.toastContainer;
  if (!container) return;
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

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

export function updateGlobalTray(projectName, analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0, currentFile = '') {
  if (!DOM.globalJobTray) return;
  DOM.globalJobTray.classList.remove('hidden');

  if (DOM.trayProjectName) {
    DOM.trayProjectName.textContent = `Project: ${projectName || 'Workspace'}`;
  }

  const pct = total > 0 ? Math.min(100, Math.round((analyzed / total) * 100)) : 0;
  if (DOM.trayProgressBar) DOM.trayProgressBar.style.width = `${pct}%`;
  if (DOM.trayPercent) DOM.trayPercent.textContent = `${pct}%`;
  if (DOM.trayCounts) {
    if (currentFile) {
      DOM.trayCounts.textContent = `(${analyzed}/${total}) File: ${currentFile}`;
    } else {
      DOM.trayCounts.textContent = `Analyzed ${analyzed} / ${total} bursts`;
    }
  }

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

let _trayCooldownTimer = null;

export function startTrayCooldownCountdown(seconds, onTick = null, onComplete = null) {
  if (_trayCooldownTimer) {
    clearInterval(_trayCooldownTimer);
    _trayCooldownTimer = null;
  }
  let remaining = Math.max(1, Math.round(seconds));
  if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.remove('hidden');
  if (DOM.trayCooldownText) DOM.trayCooldownText.textContent = `Rate-Limit Cooldown: ${remaining}s remaining`;
  if (DOM.trayTitle) DOM.trayTitle.textContent = 'AI Queue Cooling Down ⏳';

  _trayCooldownTimer = setInterval(() => {
    remaining--;
    if (DOM.trayCooldownText) DOM.trayCooldownText.textContent = `Rate-Limit Cooldown: ${Math.max(0, remaining)}s remaining`;
    if (onTick) onTick(remaining);
    if (remaining <= 0) {
      clearInterval(_trayCooldownTimer);
      _trayCooldownTimer = null;
      if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.add('hidden');
      if (DOM.trayTitle) DOM.trayTitle.textContent = 'AI Batch Synthesis Active';
      if (onComplete) onComplete();
    }
  }, 1000);
}

export function hideGlobalTray() {
  if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
}

export function showBatchProgress(analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0) {
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

export function hideBatchProgress() {
  if (DOM.batchProgressContainer) DOM.batchProgressContainer.style.display = 'none';
}

export function showRollbackTray(projectName, status = 'running', message = '') {
  if (!DOM.globalJobTray) return;
  DOM.globalJobTray.classList.remove('hidden');

  if (DOM.trayProjectName) {
    DOM.trayProjectName.textContent = `Project: ${projectName || 'Workspace'}`;
  }
  if (DOM.trayCooldownBadge) {
    DOM.trayCooldownBadge.classList.add('hidden');
  }

  if (status === 'running') {
    if (DOM.trayTitle) DOM.trayTitle.textContent = '[ ↶ Rolling back workspace files via Shadow Git... ]';
    if (DOM.traySpinner) DOM.traySpinner.style.display = 'inline-block';
    if (DOM.trayProgressBar) {
      DOM.trayProgressBar.style.width = '65%';
      DOM.trayProgressBar.style.transition = 'width 0.4s ease';
    }
    if (DOM.trayCounts) DOM.trayCounts.textContent = 'Reverting modified files and cleaning untracked files…';
    if (DOM.trayPercent) DOM.trayPercent.textContent = '65%';
  } else if (status === 'completed') {
    if (DOM.trayTitle) DOM.trayTitle.textContent = '[ ✓ Rollback complete: Workspace reverted to selected burst ]';
    if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
    if (DOM.trayProgressBar) {
      DOM.trayProgressBar.style.width = '100%';
    }
    if (DOM.trayCounts) DOM.trayCounts.textContent = message || 'Workspace files successfully synchronized.';
    if (DOM.trayPercent) DOM.trayPercent.textContent = '100%';

    setTimeout(() => {
      if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
    }, 3000);
  } else if (status === 'failed') {
    if (DOM.trayTitle) DOM.trayTitle.textContent = 'Rollback Failed ⚠️';
    if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
    if (DOM.trayProgressBar) DOM.trayProgressBar.style.width = '100%';
    if (DOM.trayCounts) DOM.trayCounts.textContent = message || 'An error occurred during rollback.';
    setTimeout(() => {
      if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
    }, 4000);
  }
}

