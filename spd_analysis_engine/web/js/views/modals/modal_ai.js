/**
 * SPD Analysis Engine — Modal AI Controller
 * Handles AI provider switching (Gemini / Ollama / Heuristic), Gemini Key Vault,
 * model testing, configuration persistence, and batch session analysis polling.
 */
import { DOM } from '../../dom.js';
import { state } from '../../state.js';
import { escapeHtml, formatIST } from '../../utils.js';
import { showToast, showBatchProgress, hideBatchProgress, updateGlobalTray, hideGlobalTray } from '../../toasts.js';
import { loadProjectEvents } from '../timeline.js';
import { openUnifiedSettingsModal, closeUnifiedSettingsModal } from './modal_base.js';

let batchPollInterval = null;

export function updateAiProviderBoxes(provider) {
  if (!DOM.boxProviderOllama || !DOM.boxProviderGemini || !DOM.boxProviderHeuristic) return;
  DOM.boxProviderOllama.style.display = provider === 'ollama' ? 'flex' : 'none';
  DOM.boxProviderGemini.style.display = provider === 'gemini' ? 'flex' : 'none';
  DOM.boxProviderHeuristic.style.display = provider === 'heuristic' ? 'flex' : 'none';
}

export async function loadAiSettings() {
  if (DOM.ollamaTestStatus) {
    DOM.ollamaTestStatus.className = 'test-status-badge hidden';
    DOM.ollamaTestStatus.textContent = '';
  }
  if (DOM.geminiTestStatus) {
    DOM.geminiTestStatus.className = 'test-status-badge hidden';
    DOM.geminiTestStatus.textContent = '';
  }

  try {
    const projectQuery = state.currentProject ? `?project=${encodeURIComponent(state.currentProject)}` : '';
    const res = await fetch(`/api/ai/config${projectQuery}`);
    const data = await res.json();
    if (data.success && data.config) {
      const c = data.config;
      if (DOM.selAiProvider) DOM.selAiProvider.value = c.preferred_provider || 'heuristic';
      if (DOM.inpOllamaUrl) DOM.inpOllamaUrl.value = c.ollama_base_url || 'http://127.0.0.1:11434';
      if (DOM.inpOllamaModel) DOM.inpOllamaModel.value = c.ollama_model || 'qwen2.5-coder:7b';
      if (DOM.selGeminiModel) DOM.selGeminiModel.value = c.gemini_model || 'gemini-3.1-flash-lite';
      if (DOM.chkCloudAiConsent) DOM.chkCloudAiConsent.checked = Boolean(c.cloud_ai_enabled || c.cloud_consent);

      state.savedGeminiKeys = c.saved_gemini_keys || c.saved_keys || [];
      populateGeminiKeyVaultDropdown(state.savedGeminiKeys, c);
      if (DOM.selAiProvider) updateAiProviderBoxes(DOM.selAiProvider.value);
    }
  } catch (err) {
    console.warn('Failed to load AI config:', err);
  }
}

export function openAiSettingsModal() {
  openUnifiedSettingsModal('ai');
}

export function closeAiSettingsModal() {
  closeUnifiedSettingsModal();
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

  if (DOM.chkCloudAiConsent) {
    payload.cloud_ai_enabled = DOM.chkCloudAiConsent.checked;
    payload.cloud_consent = DOM.chkCloudAiConsent.checked;
  }
  if (state.currentProject) {
    payload.project_slug = state.currentProject;
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

export function initModalAiListeners() {
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

  if (DOM.modalAiSettings) {
    DOM.modalAiSettings.addEventListener('click', (e) => {
      if (e.target === DOM.modalAiSettings) closeAiSettingsModal();
    });
  }
}
