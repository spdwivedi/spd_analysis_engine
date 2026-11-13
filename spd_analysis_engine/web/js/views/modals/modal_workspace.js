/**
 * SPD Analysis Engine — Modal Workspace Controller
 * Handles in-dashboard .gitignore editing, saving with silent watchdog suppression,
 * and resetting to the standard hardened template.
 */
import { DOM } from '../../dom.js';
import { state, getSelectedProjectInfo } from '../../state.js';
import { showToast } from '../../toasts.js';

export const DEFAULT_GITIGNORE_TEMPLATE = `# Environments & Secrets
.env
*.env
*.key
*.pem
*.cert

# Dependencies & Build Artifacts
node_modules/
__pycache__/
*.pyc
*.pyo
dist/
build/
.venv/
.venv*/

# Binary & Documents
*.pdf
pdf/

# SPD Analysis Engine & Runtime
.spd/
session.db*
worker.log
worker.pid*
worker.status.json
*.patch
*.tmp
`;

export async function loadWorkspaceGitignore(projectSlug) {
  const slug = projectSlug || state.selectedProject || getSelectedProjectInfo()?.name;
  if (!slug) {
    if (DOM.txtWorkspaceGitignore) {
      DOM.txtWorkspaceGitignore.value = '# Please select a project from the sidebar to view or edit its .gitignore';
    }
    return;
  }

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(slug)}/gitignore`);
    const data = await res.json();
    if (res.ok && data.success) {
      if (DOM.txtWorkspaceGitignore) {
        DOM.txtWorkspaceGitignore.value = data.content || DEFAULT_GITIGNORE_TEMPLATE;
      }
    } else {
      showToast(data.error || 'Failed to load .gitignore rules', 'warning');
    }
  } catch (err) {
    showToast(`Error loading .gitignore: ${err.message}`, 'error');
  }
}

export async function handleSaveGitignore(e) {
  if (e) e.preventDefault();
  const slug = state.selectedProject || getSelectedProjectInfo()?.name;
  if (!slug) {
    showToast('Please select a project first.', 'error');
    return;
  }

  const content = DOM.txtWorkspaceGitignore ? DOM.txtWorkspaceGitignore.value : '';
  if (DOM.btnSaveGitignore) DOM.btnSaveGitignore.disabled = true;

  try {
    const res = await fetch(`/api/project/${encodeURIComponent(slug)}/gitignore`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('✔ .gitignore updated without triggering burst', 'success');
    } else {
      throw new Error(data.message || data.error || 'Failed to update .gitignore');
    }
  } catch (err) {
    showToast(`Save failed: ${err.message}`, 'error');
  } finally {
    if (DOM.btnSaveGitignore) DOM.btnSaveGitignore.disabled = false;
  }
}

export function handleResetGitignore() {
  if (DOM.txtWorkspaceGitignore) {
    DOM.txtWorkspaceGitignore.value = DEFAULT_GITIGNORE_TEMPLATE;
  }
  showToast('Reset to standard template. Click Save to apply.', 'info');
}

export function initModalWorkspaceListeners() {
  if (DOM.btnSaveGitignore) {
    DOM.btnSaveGitignore.addEventListener('click', handleSaveGitignore);
  }
  if (DOM.btnResetGitignore) {
    DOM.btnResetGitignore.addEventListener('click', handleResetGitignore);
  }
}

export const DEFAULT_WORKSPACE_DEBOUNCE = 3.5;
export const MAX_STANDARD_DEBOUNCE = 3600.0;

export function clampWorkspaceDebounce(val, extendedSoak = true) {
  const num = parseFloat(val);
  if (isNaN(num) || num < 0.5) return DEFAULT_WORKSPACE_DEBOUNCE;
  if (num > 3600.0) return 3600.0;
  return num;
}
