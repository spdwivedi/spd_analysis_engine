/**
 * SPD Analysis Engine — Modal Git Controller
 * Handles GitHub credentials, remote repository settings, connection testing,
 * unpushed milestone scoping, AI regeneration, and milestone history.
 */
import { DOM } from '../../dom.js';
import { state } from '../../state.js';
import { showToast } from '../../toasts.js';
import { escapeHtml, setButtonLoading } from '../../utils.js';
import { refreshProjects, loadProjectSpecs, refreshDashboard } from '../dashboard.js';
import { openUnifiedSettingsModal, closeUnifiedSettingsModal } from './modal_base.js';

export async function loadGitSettings() {
  if (DOM.gitTestStatus) {
    DOM.gitTestStatus.className = 'git-test-badge hidden';
    DOM.gitTestStatus.textContent = '';
  }
  const proj = state.selectedProject;
  loadMilestoneHistory(proj);
  try {
    const url = proj ? `/api/git/config?project=${encodeURIComponent(proj)}` : '/api/git/config';
    const res = await fetch(url);
    const data = await res.json();
    if (data.success && data.config) {
      const c = data.config;
      if (DOM.inpGitUsername) DOM.inpGitUsername.value = c.github_username || '';
      if (DOM.inpGitToken) DOM.inpGitToken.value = c.github_token || '';
      if (DOM.inpGitDefaultRemote) DOM.inpGitDefaultRemote.value = c.default_remote || 'origin';
      if (DOM.inpGitDefaultBranch) DOM.inpGitDefaultBranch.value = c.default_branch || 'main';
      if (DOM.chkGitAutoChangelog) DOM.chkGitAutoChangelog.checked = c.auto_generate_changelog !== false;
      if (DOM.chkGitAutopush) DOM.chkGitAutopush.checked = Boolean(c.auto_push);
      if (DOM.inpGitTestUrl && !DOM.inpGitTestUrl.value) {
        DOM.inpGitTestUrl.value = c.default_remote && c.default_remote.startsWith('http') ? c.default_remote : '';
      }
    }
  } catch (err) {
    console.warn('Failed to load git config:', err);
  }
}

export function openGitSettingsModal() {
  openUnifiedSettingsModal('git');
}

export function closeGitSettingsModal() {
  closeUnifiedSettingsModal();
}

export async function handleSaveGitSettings(e) {
  if (e) e.preventDefault();
  const payload = {
    project: state.selectedProject,
    github_username: DOM.inpGitUsername?.value.trim() || '',
    github_token: DOM.inpGitToken?.value.trim() || '',
    default_remote: DOM.inpGitDefaultRemote?.value.trim() || 'origin',
    default_branch: DOM.inpGitDefaultBranch?.value.trim() || 'main',
    auto_generate_changelog: DOM.chkGitAutoChangelog?.checked ?? true,
    auto_push: DOM.chkGitAutopush ? DOM.chkGitAutopush.checked : false,
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
    if (state.selectedProject) {
      loadProjectSpecs(state.selectedProject);
    }
    showToast('GitHub settings saved successfully.', 'success');
    closeGitSettingsModal();
  } catch (err) {
    showToast(`Save failed: ${err.message}`, 'error');
  }
}

export async function handleTestGitConnection() {
  const testUrl = DOM.inpGitTestUrl?.value.trim() || DOM.inpGitDefaultRemote?.value.trim() || '';
  const user = DOM.inpGitUsername?.value.trim() || '';
  const token = DOM.inpGitToken?.value.trim() || '';

  if (DOM.gitTestStatus) {
    DOM.gitTestStatus.className = 'git-test-badge testing';
    DOM.gitTestStatus.textContent = 'Testing connection via git ls-remote…';
  }
  if (DOM.btnTestGitConnection) DOM.btnTestGitConnection.disabled = true;

  try {
    const res = await fetch('/api/git/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: testUrl, github_username: user, github_token: token }),
    });
    const data = await res.json();
    if (DOM.gitTestStatus) {
      if (data.success) {
        DOM.gitTestStatus.className = 'git-test-badge success';
        DOM.gitTestStatus.textContent = `✔ ${data.message || 'Connection successful!'}`;
      } else {
        DOM.gitTestStatus.className = 'git-test-badge error';
        DOM.gitTestStatus.textContent = `✖ ${data.message || 'Connection failed.'}`;
      }
    }
  } catch (err) {
    if (DOM.gitTestStatus) {
      DOM.gitTestStatus.className = 'git-test-badge error';
      DOM.gitTestStatus.textContent = `✖ Error: ${err.message}`;
    }
  } finally {
    if (DOM.btnTestGitConnection) DOM.btnTestGitConnection.disabled = false;
  }
}

export async function loadMilestoneHistory(slug) {
  const container = DOM.gitMilestoneHistoryList;
  if (!container) return;

  const projectSlug = slug || state.selectedProject;
  if (!projectSlug) {
    container.innerHTML = '<div class="mono" style="font-size: 0.76rem; color: var(--text-dim); text-align: center; padding: 12px 0;">Select a project to view milestones.</div>';
    return;
  }

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(projectSlug)}/milestones`);
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.error || 'Failed to load milestones');
    }

    const milestones = data.milestones || [];
    if (DOM.gitMilestonesCount) {
      DOM.gitMilestonesCount.textContent = `Last ${Math.min(5, milestones.length)} push${milestones.length === 1 ? '' : 'es'}`;
    }

    if (milestones.length === 0) {
      container.innerHTML = '<div class="mono" style="font-size: 0.76rem; color: var(--text-dim); text-align: center; padding: 12px 0;">No milestones pushed yet.</div>';
      return;
    }

    const recent = milestones.slice(0, 5);
    container.innerHTML = recent.map((m) => {
      const shortSha = m.commit_sha ? m.commit_sha.substring(0, 7) : '-------';
      let bursts = [];
      try {
        bursts = typeof m.burst_ids === 'string' ? JSON.parse(m.burst_ids) : (m.burst_ids || []);
      } catch {
        bursts = [];
      }
      const burstInfo = bursts.length ? `${bursts.length} burst${bursts.length === 1 ? '' : 's'}` : 'Baseline';
      const timeStr = m.pushed_at || 'Recent';

      return `
        <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid var(--border-card); border-radius: var(--radius-sm); padding: 8px 10px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
            <strong style="font-size: 0.78rem; color: #f1f5f9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 70%;" title="${escapeHtml(m.commit_title || '')}">
              ${escapeHtml(m.commit_title || 'Milestone Commit')}
            </strong>
            <span class="badge mono" style="font-size: 0.7rem; background: rgba(56, 189, 248, 0.15); color: #38bdf8;">${escapeHtml(shortSha)}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.72rem; color: var(--text-dim);">
            <span>📦 ${escapeHtml(burstInfo)}</span>
            <span>🕒 ${escapeHtml(timeStr)}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.warn('Failed to load milestone history:', err);
    container.innerHTML = `<div style="font-size: 0.74rem; color: var(--color-danger); padding: 8px;">Failed to load milestones: ${err.message}</div>`;
  }
}

export async function openPushGitModal(projectSlug) {
  const slug = projectSlug || state.selectedProject;
  if (!slug) {
    showToast('Please select a project first.', 'error');
    return;
  }

  // Reset form fields and hide error banners
  if (DOM.gitPushErrorBanner) {
    DOM.gitPushErrorBanner.classList.add('hidden');
    DOM.gitPushErrorBanner.textContent = '';
  }
  if (DOM.syncError) {
    DOM.syncError.classList.add('hidden');
    DOM.syncError.textContent = '';
  }
  if (DOM.gitNoUnpushedBanner) {
    DOM.gitNoUnpushedBanner.classList.add('hidden');
  }

  // Set loading state on title and body
  if (DOM.txtCommitTitle) DOM.txtCommitTitle.value = 'Analyzing unpushed bursts...';
  if (DOM.txtCommitBody) DOM.txtCommitBody.value = 'Analyzing unpushed bursts...';
  if (DOM.txtChangelogEntry) DOM.txtChangelogEntry.value = '';
  if (DOM.badgeGitBurstCount) DOM.badgeGitBurstCount.textContent = 'Analyzing...';
  if (DOM.badgeGitAiProvider) DOM.badgeGitAiProvider.textContent = 'AI SYNTHESIZER';

  if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = true;
  if (DOM.btnConfirmPushGit) DOM.btnConfirmPushGit.disabled = true;
  if (DOM.modalPushGit) DOM.modalPushGit.classList.add('open');

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(slug)}/unpushed-milestone`);
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.error || 'Failed to synthesize unpushed milestone');
    }

    if (data.has_unpushed === false) {
      if (DOM.gitPushFormContainer) DOM.gitPushFormContainer.classList.add('hidden');
      if (DOM.btnConfirmPushGit) {
        DOM.btnConfirmPushGit.classList.add('hidden');
        DOM.btnConfirmPushGit.disabled = true;
      }
      if (DOM.gitNoUnpushedBanner) DOM.gitNoUnpushedBanner.classList.remove('hidden');
      if (DOM.badgeGitBurstCount) DOM.badgeGitBurstCount.textContent = '0 unpushed bursts';
      if (DOM.syncEventsCount) DOM.syncEventsCount.textContent = '0';
    } else {
      if (DOM.gitPushFormContainer) DOM.gitPushFormContainer.classList.remove('hidden');
      if (DOM.btnConfirmPushGit) {
        DOM.btnConfirmPushGit.classList.remove('hidden');
        DOM.btnConfirmPushGit.disabled = false;
      }
      if (DOM.gitNoUnpushedBanner) DOM.gitNoUnpushedBanner.classList.add('hidden');
      const count = data.bursts_count || 1;
      const countText = `${count} unpushed burst${count === 1 ? '' : 's'}`;
      if (DOM.badgeGitBurstCount) DOM.badgeGitBurstCount.textContent = countText;
      if (DOM.syncEventsCount) DOM.syncEventsCount.textContent = String(count);

      if (DOM.badgeGitAiProvider) {
        DOM.badgeGitAiProvider.textContent = (data.provider || data.mode_used || 'AI').toUpperCase();
      }

      if (DOM.txtCommitTitle) DOM.txtCommitTitle.value = data.title || 'feat: milestone sync';
      if (DOM.txtCommitBody) DOM.txtCommitBody.value = data.body || '';
      if (DOM.txtChangelogEntry) DOM.txtChangelogEntry.value = data.changelog || '';

      const spec = state.projectSpecs[slug] || {};
      if (DOM.inpSyncRemote && !DOM.inpSyncRemote.value) {
        DOM.inpSyncRemote.value = spec.remote_url || 'origin';
      }
      if (DOM.inpSyncBranch && !DOM.inpSyncBranch.value) {
        DOM.inpSyncBranch.value = spec.remote_branch || 'main';
      }
    }
  } catch (err) {
    console.error('Unpushed milestone error:', err);
    if (DOM.gitPushErrorBanner) {
      DOM.gitPushErrorBanner.textContent = `Error: ${err.message}`;
      DOM.gitPushErrorBanner.classList.remove('hidden');
    }
    showToast(`Failed to load milestone: ${err.message}`, 'error');
  } finally {
    if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = false;
  }
}

export const handleOpenSyncReview = openPushGitModal;

export async function handleRegenerateMilestoneAi() {
  const slug = state.selectedProject;
  if (!slug) return;

  const btn = DOM.btnGitRegenerateAi;
  if (btn) setButtonLoading(btn, true, 'Generating...');

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(slug)}/synthesize-milestone?force_ai=true`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_ai: true }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.error || 'Failed to regenerate with AI');
    }

    if (DOM.txtCommitTitle && data.title) DOM.txtCommitTitle.value = data.title;
    if (DOM.txtCommitBody && data.body) DOM.txtCommitBody.value = data.body;
    if (DOM.txtChangelogEntry && data.changelog) DOM.txtChangelogEntry.value = data.changelog;
    if (DOM.badgeGitAiProvider) {
      DOM.badgeGitAiProvider.textContent = (data.provider || data.mode_used || 'AI').toUpperCase();
    }
    showToast('Commit details regenerated with AI.', 'success');
  } catch (err) {
    console.error('Regenerate AI error:', err);
    showToast(`AI regeneration failed: ${err.message}`, 'error');
  } finally {
    if (btn) setButtonLoading(btn, false);
  }
}

export function closeSyncReviewModal() {
  if (DOM.modalPushGit) DOM.modalPushGit.classList.remove('open');
  if (DOM.modalSyncReview) DOM.modalSyncReview.classList.remove('open');
}

export async function handleConfirmSyncPush() {
  if (!state.selectedProject) return;

  const commitTitle = DOM.txtCommitTitle?.value.trim() || '';
  if (!commitTitle) {
    if (DOM.gitPushErrorBanner) {
      DOM.gitPushErrorBanner.textContent = 'Commit title is required.';
      DOM.gitPushErrorBanner.classList.remove('hidden');
    }
    return;
  }

  const commitBody = DOM.txtCommitBody?.value.trim() || '';
  const fullCommitMsg = commitBody ? `${commitTitle}\n\n${commitBody}` : commitTitle;
  const changelogContent = DOM.txtChangelogEntry?.value.trim() || '';
  let remote = DOM.inpSyncRemote?.value.trim() || 'origin';
  const branch = DOM.inpSyncBranch?.value.trim() || 'main';
  const autoCreate = DOM.chkSyncAutoCreateRepo ? DOM.chkSyncAutoCreateRepo.checked : false;

  if (DOM.gitPushErrorBanner) DOM.gitPushErrorBanner.classList.add('hidden');
  if (DOM.btnConfirmPushGit) setButtonLoading(DOM.btnConfirmPushGit, true, 'Pushing...');
  if (DOM.syncSpinner) DOM.syncSpinner.classList.remove('hidden');

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
    await new Promise((r) => setTimeout(r, 250));

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
        if (DOM.inpSyncRemote) DOM.inpSyncRemote.value = createData.clone_url;
      }
      if (createData.created) {
        showToast(`Repository '${repoName}' created on GitHub!`, 'info');
      }
    } else {
      updateStepper(2, 66, '2/3 Remote repository verified…');
      await new Promise((r) => setTimeout(r, 200));
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
    showToast('🚀 Milestone pushed and recorded in session database', 'success');
    setTimeout(async () => {
      closeSyncReviewModal();
      await refreshDashboard();
    }, 600);
  } catch (err) {
    console.error('Git push error:', err);
    if (DOM.gitPushErrorBanner) {
      DOM.gitPushErrorBanner.textContent = `Git push failed: ${err.message}`;
      DOM.gitPushErrorBanner.classList.remove('hidden');
    }
    showToast(`Git push failed: ${err.message}`, 'error');
  } finally {
    if (DOM.btnConfirmPushGit) setButtonLoading(DOM.btnConfirmPushGit, false);
    if (DOM.syncSpinner) DOM.syncSpinner.classList.add('hidden');
  }
}

export function initModalGitListeners() {
  DOM.btnHeaderGitSettings?.addEventListener('click', openGitSettingsModal);
  DOM.modalGitSettingsClose?.addEventListener('click', closeGitSettingsModal);
  DOM.modalGitSettingsCancel?.addEventListener('click', closeGitSettingsModal);
  DOM.formGitSettings?.addEventListener('submit', handleSaveGitSettings);
  DOM.btnTestGitConnection?.addEventListener('click', handleTestGitConnection);

  DOM.btnHeaderPushGit?.addEventListener('click', () => openPushGitModal());
  DOM.btnSpecsPushGh?.addEventListener('click', () => openPushGitModal());
  DOM.btnGitRegenerateAi?.addEventListener('click', handleRegenerateMilestoneAi);
  DOM.modalSyncReviewClose?.addEventListener('click', closeSyncReviewModal);
  DOM.modalSyncCancel?.addEventListener('click', closeSyncReviewModal);
  DOM.btnConfirmPushGit?.addEventListener('click', handleConfirmSyncPush);

  DOM.modalGitSettings?.addEventListener('click', (e) => {
    if (e.target === DOM.modalGitSettings) closeGitSettingsModal();
  });
  DOM.modalPushGit?.addEventListener('click', (e) => {
    if (e.target === DOM.modalPushGit) closeSyncReviewModal();
  });
}
