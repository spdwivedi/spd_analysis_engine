/**
 * SPD Analysis Engine — Modal Base Controller
 * Handles modal show/hide utilities, escape key listener, new session modal,
 * and unified settings modal tab navigation (AI / Git / Trash / Engine).
 */
import { DOM } from '../../dom.js';
import { state } from '../../state.js';
import { slugify } from '../../utils.js';
import { showToast } from '../../toasts.js';
import { apiPost } from '../../api.js';
import { refreshProjects, selectProject } from '../dashboard.js';
import { isEcoModeActive, setEcoMode } from '../../eco_mode.js';
import { loadAiSettings } from './modal_ai.js';
import { loadGitSettings } from './modal_git.js';
import { loadTrashBinItems, populateDeleteProjectInfo } from './modal_trash.js';
import { loadEngineHealth, loadWorkerLogRaw } from './modal_health.js';
import { loadWorkspaceGitignore } from './modal_workspace.js';

export function getSettingsTabButtons() {
  return typeof DOM.settingsTabButtons === 'function'
    ? DOM.settingsTabButtons()
    : (DOM.settingsTabButtons || document.querySelectorAll('.settings-tab-btn'));
}

export function getSettingsTabPanes() {
  return typeof DOM.settingsTabPanes === 'function'
    ? DOM.settingsTabPanes()
    : (DOM.settingsTabPanes || document.querySelectorAll('.settings-tab-pane'));
}

export function openModal(modalEl) {
  if (modalEl) modalEl.classList.add('open');
}

export function closeModal(modalEl) {
  if (modalEl) modalEl.classList.remove('open');
}

// =============================================================================
// New Session Modal
// =============================================================================

export function openNewSessionModal() {
  if (DOM.formNewSession) DOM.formNewSession.reset();
  if (DOM.slugPreview) DOM.slugPreview.textContent = 'my_project';
  if (DOM.debounceValDisplay) DOM.debounceValDisplay.innerHTML = '3.5s <span class="badge badge-recommended">Recommended</span>';
  if (DOM.rngDebounce) DOM.rngDebounce.value = '3.5';
  if (DOM.selIdeProfile) DOM.selIdeProfile.value = 'antigravity';
  if (DOM.groupCustomIdeMarker) DOM.groupCustomIdeMarker.classList.add('hidden');
  if (DOM.inpCustomIdeMarker) DOM.inpCustomIdeMarker.value = '';
  if (DOM.modalError) DOM.modalError.classList.add('hidden');
  if (DOM.modalSpinner) DOM.modalSpinner.classList.add('hidden');
  if (DOM.modalSubmitBtn) DOM.modalSubmitBtn.disabled = false;
  if (DOM.modalNewSession) DOM.modalNewSession.classList.add('open');
}

export function closeNewSessionModal() {
  if (DOM.modalNewSession) DOM.modalNewSession.classList.remove('open');
}

export async function handleLaunchNewSession(e) {
  if (e) e.preventDefault();
  const name = DOM.inpProjectName ? DOM.inpProjectName.value.trim() : '';
  const targetPath = DOM.inpTargetPath ? DOM.inpTargetPath.value.trim() : '';
  const scaffold = DOM.chkScaffold ? DOM.chkScaffold.checked : false;
  const debounce = DOM.rngDebounce ? parseFloat(DOM.rngDebounce.value) : 3.5;

  if (!name || !targetPath) {
    if (DOM.modalError) {
      DOM.modalError.textContent = 'Please fill in both project name and workspace path.';
      DOM.modalError.classList.remove('hidden');
    }
    return;
  }

  if (DOM.modalError) DOM.modalError.classList.add('hidden');
  if (DOM.modalSpinner) DOM.modalSpinner.classList.remove('hidden');
  if (DOM.modalSubmitBtn) DOM.modalSubmitBtn.disabled = true;

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
    if (DOM.modalError) {
      DOM.modalError.textContent = `Launch failed: ${err.message}`;
      DOM.modalError.classList.remove('hidden');
    }
  } finally {
    if (DOM.modalSpinner) DOM.modalSpinner.classList.add('hidden');
    if (DOM.modalSubmitBtn) DOM.modalSubmitBtn.disabled = false;
  }
}

// =============================================================================
// Engine Settings
// =============================================================================

export function loadEngineSettings() {
  let savedDebounce = '3.5';
  try {
    savedDebounce = localStorage.getItem('spd_default_debounce') || '3.5';
  } catch (_) {}
  if (DOM.selDefaultDebounce) {
    DOM.selDefaultDebounce.value = savedDebounce;
  }
  if (DOM.chkEcoModeSetting) {
    DOM.chkEcoModeSetting.checked = isEcoModeActive();
  }
}

export function handleSaveEngineSettings() {
  if (DOM.selDefaultDebounce) {
    const val = DOM.selDefaultDebounce.value;
    try {
      localStorage.setItem('spd_default_debounce', val);
    } catch (_) {}
  }
  if (DOM.chkEcoModeSetting) {
    setEcoMode(DOM.chkEcoModeSetting.checked);
  }
  showToast('Engine settings saved.', 'success');
  closeUnifiedSettingsModal();
}

// =============================================================================
// Unified Settings Modal
// =============================================================================

export function switchSettingsTab(tabName) {
  const tabs = ['ai', 'git', 'trash', 'engine', 'health', 'workspace'];
  const activeTab = tabs.includes(tabName) ? tabName : 'ai';

  const tabButtons = getSettingsTabButtons();
  if (tabButtons) {
    tabButtons.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === activeTab);
    });
  }

  const tabPanes = getSettingsTabPanes();
  if (tabPanes) {
    tabPanes.forEach((pane) => {
      pane.classList.toggle('hidden', pane.id !== `pane-settings-${activeTab}`);
    });
  }

  if (activeTab === 'ai') {
    loadAiSettings();
  } else if (activeTab === 'git') {
    loadGitSettings();
  } else if (activeTab === 'trash') {
    loadTrashBinItems();
    if (state.selectedProject) {
      populateDeleteProjectInfo();
    }
  } else if (activeTab === 'engine') {
    loadEngineSettings();
  } else if (activeTab === 'health') {
    loadEngineHealth();
    loadWorkerLogRaw();
  } else if (activeTab === 'workspace') {
    loadWorkspaceGitignore();
  }
}

export function openUnifiedSettingsModal(initialTab = 'ai') {
  if (DOM.modalUnifiedSettings) {
    DOM.modalUnifiedSettings.classList.add('open');
  }
  switchSettingsTab(initialTab);
}

export function closeUnifiedSettingsModal() {
  if (DOM.modalUnifiedSettings) {
    DOM.modalUnifiedSettings.classList.remove('open');
  }
}

export function initModalBaseListeners() {
  // Unified Settings Modal
  if (DOM.btnHeaderSettings) {
    DOM.btnHeaderSettings.addEventListener('click', () => openUnifiedSettingsModal('ai'));
  }
  if (DOM.modalUnifiedSettingsClose) {
    DOM.modalUnifiedSettingsClose.addEventListener('click', closeUnifiedSettingsModal);
  }
  if (DOM.modalUnifiedSettingsCancel) {
    DOM.modalUnifiedSettingsCancel.addEventListener('click', closeUnifiedSettingsModal);
  }
  document.querySelectorAll('.modal-cancel-unified').forEach((btn) => {
    btn.addEventListener('click', closeUnifiedSettingsModal);
  });

  const tabButtons = getSettingsTabButtons();
  if (tabButtons) {
    tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => switchSettingsTab(btn.dataset.tab));
    });
  }

  if (DOM.btnSaveEngineSettings) {
    DOM.btnSaveEngineSettings.addEventListener('click', handleSaveEngineSettings);
  }
  if (DOM.modalUnifiedSettings) {
    DOM.modalUnifiedSettings.addEventListener('click', (e) => {
      if (e.target === DOM.modalUnifiedSettings) closeUnifiedSettingsModal();
    });
  }

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
  if (DOM.modalNewSession) {
    DOM.modalNewSession.addEventListener('click', (e) => {
      if (e.target === DOM.modalNewSession) closeNewSessionModal();
    });
  }

  // Escape key global listener to close open modals
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.keyCode === 27) {
      const openModals = document.querySelectorAll('.modal.open, .modal-backdrop.open');
      openModals.forEach((m) => m.classList.remove('open'));
    }
  });
}
