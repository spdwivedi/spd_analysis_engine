import { DOM } from '../dom.js';
import { state } from '../state.js';
import { escapeHtml, formatIST, formatISTTime, slugify } from '../utils.js';
import { showToast, showBatchProgress, hideBatchProgress, updateGlobalTray, hideGlobalTray } from '../toasts.js';
import { apiPost } from '../api.js';
import { refreshProjects, selectProject, loadProjectSpecs } from './dashboard.js';
import { loadProjectEvents } from './timeline.js';

let batchPollInterval = null;

// =============================================================================
// Trash Bin Controller
// =============================================================================

export async function updateTrashCount() {
  try {
    const res = await fetch('/api/trash');
    const data = await res.json();
    const count = (data && typeof data.count === 'number') ? data.count : 0;
    if (DOM.headerTrashCount) DOM.headerTrashCount.textContent = count;
    if (DOM.badgeTrashCount) DOM.badgeTrashCount.textContent = count;
    document.querySelectorAll('.badge-trash-count').forEach((el) => {
      el.textContent = count;
    });
  } catch (_) {}
}

export function openTrashBinModal() {
  if (DOM.modalTrashBin) DOM.modalTrashBin.classList.add('open');
  loadTrashBinItems();
}

export function closeTrashBinModal() {
  if (DOM.modalTrashBin) DOM.modalTrashBin.classList.remove('open');
}

export async function loadTrashBinItems() {
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

export async function handleRestoreTrash(trashId, btnEl) {
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

export async function handlePurgeTrashItem(trashId, btnEl) {
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

export async function handleEmptyTrashBin() {
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

// =============================================================================
// New Session Modal
// =============================================================================

export function openNewSessionModal() {
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

export function closeNewSessionModal() {
  DOM.modalNewSession.classList.remove('open');
}

export async function handleLaunchNewSession(e) {
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

// =============================================================================
// Git Settings Modal
// =============================================================================

export async function openGitSettingsModal() {
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

export function closeGitSettingsModal() {
  if (DOM.modalGitSettings) DOM.modalGitSettings.classList.remove('open');
}

export async function handleSaveGitSettings(e) {
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

export async function handleTestGitConnection() {
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

// =============================================================================
// Milestone Sync Review Modal
// =============================================================================

export async function handleOpenSyncReview() {
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

export function closeSyncReviewModal() {
  if (DOM.modalSyncReview) DOM.modalSyncReview.classList.remove('open');
}

export async function handleConfirmSyncPush() {
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
    updateStepper(1, 33, '1/3 Generating semantic changelog & commit structure…');
    await new Promise((r) => setTimeout(r, 350));

    if (autoCreate) {
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

// =============================================================================
// Batch AI Analysis
// =============================================================================

export function startBatchProgressPolling(projectName) {
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

export async function handleBatchAnalyzeSession(forceAll = false) {
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

export function handleBatchRetry() {
  handleBatchAnalyzeSession(false);
}

// =============================================================================
// Delete Project Modal
// =============================================================================

export function openDeleteProjectModal() {
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

  const radios = document.querySelectorAll('input[name="delete-scope"]');
  radios.forEach((r) => {
    r.checked = r.value === 'local_session';
    const opt = r.closest('.scope-option');
    if (opt) opt.classList.toggle('selected', r.checked);
  });

  if (DOM.deleteBurstsContainer) DOM.deleteBurstsContainer.style.display = 'none';
  if (DOM.deleteSessionContainer) DOM.deleteSessionContainer.style.display = 'block';
  if (DOM.deleteRemoteOption) DOM.deleteRemoteOption.style.display = 'none';

  if (DOM.selDeleteSessionTarget) {
    DOM.selDeleteSessionTarget.innerHTML = '<option value="all">Entire Local Session (All Runs &amp; session.db)</option>';
    if (state.sessionsData && state.sessionsData.length > 0) {
      state.sessionsData.forEach((s) => {
        const sid = s.session_id ?? s.id;
        const bCount = s.burst_count ?? s.bursts_count ?? 0;
        const timeStr = s.start_time ? ` (${formatIST(s.start_time)})` : '';
        const opt = document.createElement('option');
        opt.value = String(sid);
        opt.textContent = `Session #${sid} [${bCount} burst${bCount === 1 ? '' : 's'}]${timeStr}`;
        if (state.selectedSessionId && (s.session_id ?? s.id) === state.selectedSessionId) {
          opt.selected = true;
        }
        DOM.selDeleteSessionTarget.appendChild(opt);
      });
    }
  }

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

export function closeDeleteProjectModal() {
  if (DOM.modalDeleteProject) DOM.modalDeleteProject.classList.remove('open');
}

export function setupDeleteScopeOptions() {
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
      if (DOM.deleteSessionContainer) {
        DOM.deleteSessionContainer.style.display = val === 'local_session' ? 'block' : 'none';
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

export async function handleConfirmDeleteProject() {
  if (!state.selectedProject) return;

  const checkedRadio = document.querySelector('input[name="delete-scope"]:checked');
  const scope = checkedRadio ? checkedRadio.value : 'local_session';
  const deleteRemote = DOM.chkDeleteRemoteGithub ? DOM.chkDeleteRemoteGithub.checked : false;

  let burstIds = [];
  let sessionId = null;
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
  } else if (scope === 'local_session') {
    const targetSessionVal = DOM.selDeleteSessionTarget ? DOM.selDeleteSessionTarget.value : 'all';
    if (targetSessionVal && targetSessionVal !== 'all') {
      const parsedSid = parseInt(targetSessionVal, 10);
      if (!isNaN(parsedSid)) {
        sessionId = parsedSid;
      }
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
        session_id: sessionId,
        delete_remote: deleteRemote,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.error || 'Deletion failed');
    }

    const targetDesc = sessionId != null ? `Session #${sessionId}` : scope;
    showToast(`Project data moved to Trash successfully (${targetDesc}).`, 'success');
    closeDeleteProjectModal();
    await updateTrashCount();

    if (scope === 'selective_bursts' || (scope === 'local_session' && sessionId != null)) {
      await loadProjectEvents(state.selectedProject);
      await refreshProjects();
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

// =============================================================================
// AI Settings Modal
// =============================================================================

export function updateAiProviderBoxes(provider) {
  if (!DOM.boxProviderOllama || !DOM.boxProviderGemini || !DOM.boxProviderHeuristic) return;
  DOM.boxProviderOllama.style.display = provider === 'ollama' ? 'flex' : 'none';
  DOM.boxProviderGemini.style.display = provider === 'gemini' ? 'flex' : 'none';
  DOM.boxProviderHeuristic.style.display = provider === 'heuristic' ? 'flex' : 'none';
}

export async function openAiSettingsModal() {
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

export function populateGeminiKeyVaultDropdown(keys, config) {
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

export function onGeminiKeyVaultSelectionChanged() {
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

export async function handleDeleteGeminiKey() {
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

export function closeAiSettingsModal() {
  if (DOM.modalAiSettings) DOM.modalAiSettings.classList.remove('open');
}

export async function handleTestAiConnection(provider) {
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

export async function handleSaveAiSettings(e) {
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

export function initModalListeners() {
  // New Session
  if (DOM.btnNewSession) DOM.btnNewSession.addEventListener('click', openNewSessionModal);
  if (DOM.modalCloseBtn) DOM.modalCloseBtn.addEventListener('click', closeNewSessionModal);
  if (DOM.modalCancelBtn) DOM.modalCancelBtn.addEventListener('click', closeNewSessionModal);
  if (DOM.formNewSession) DOM.formNewSession.addEventListener('submit', handleLaunchNewSession);
  if (DOM.inpProjectName) {
    DOM.inpProjectName.addEventListener('input', (e) => {
      if (DOM.slugPreview) DOM.slugPreview.textContent = slugify(e.target.value);
    });
  }
  if (DOM.selIdeProfile) {
    DOM.selIdeProfile.addEventListener('change', (e) => {
      if (DOM.groupCustomIdeMarker) {
        DOM.groupCustomIdeMarker.classList.toggle('hidden', e.target.value !== 'custom');
      }
    });
  }

  // Git Settings
  if (DOM.btnHeaderGitSettings) DOM.btnHeaderGitSettings.addEventListener('click', openGitSettingsModal);
  if (DOM.modalGitSettingsClose) DOM.modalGitSettingsClose.addEventListener('click', closeGitSettingsModal);
  if (DOM.modalGitSettingsCancel) DOM.modalGitSettingsCancel.addEventListener('click', closeGitSettingsModal);
  if (DOM.formGitSettings) DOM.formGitSettings.addEventListener('submit', handleSaveGitSettings);
  if (DOM.btnTestGitConnection) DOM.btnTestGitConnection.addEventListener('click', handleTestGitConnection);

  // Sync Review
  if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.addEventListener('click', handleOpenSyncReview);
  if (DOM.btnSpecsPushGh) DOM.btnSpecsPushGh.addEventListener('click', handleOpenSyncReview);
  if (DOM.modalSyncReviewClose) DOM.modalSyncReviewClose.addEventListener('click', closeSyncReviewModal);
  if (DOM.modalSyncCancel) DOM.modalSyncCancel.addEventListener('click', closeSyncReviewModal);
  if (DOM.btnConfirmSyncPush) DOM.btnConfirmSyncPush.addEventListener('click', handleConfirmSyncPush);

  // AI Settings
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

  // Batch Analyze Session
  if (DOM.btnBatchAnalyzeSession) {
    DOM.btnBatchAnalyzeSession.addEventListener('click', () => handleBatchAnalyzeSession(false));
  }
  if (DOM.btnBatchRetry) {
    DOM.btnBatchRetry.addEventListener('click', handleBatchRetry);
  }
  if (DOM.trayCloseBtn) {
    DOM.trayCloseBtn.onclick = hideGlobalTray;
  }

  // Trash Bin
  if (DOM.btnHeaderTrash) DOM.btnHeaderTrash.addEventListener('click', openTrashBinModal);
  if (DOM.modalTrashBinClose) DOM.modalTrashBinClose.addEventListener('click', closeTrashBinModal);
  if (DOM.btnEmptyTrash) DOM.btnEmptyTrash.addEventListener('click', handleEmptyTrashBin);

  // Delete Project
  if (DOM.btnHeaderDeleteProj) DOM.btnHeaderDeleteProj.addEventListener('click', openDeleteProjectModal);
  if (DOM.modalDeleteProjectClose) DOM.modalDeleteProjectClose.addEventListener('click', closeDeleteProjectModal);
  if (DOM.modalDeleteProjectCancel) DOM.modalDeleteProjectCancel.addEventListener('click', closeDeleteProjectModal);
  if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.addEventListener('click', handleConfirmDeleteProject);
  setupDeleteScopeOptions();

  // Close modals on backdrop click
  if (DOM.modalNewSession) {
    DOM.modalNewSession.addEventListener('click', (e) => {
      if (e.target === DOM.modalNewSession) closeNewSessionModal();
    });
  }
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
