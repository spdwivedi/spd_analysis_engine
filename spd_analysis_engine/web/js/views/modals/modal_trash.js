/**
 * SPD Analysis Engine — Modal Trash Controller
 * Handles Trash Bin management, soft-delete scopes, item restore, permanent purge, and project deletion.
 */
import { DOM } from '../../dom.js';
import { state } from '../../state.js';
import { escapeHtml, formatIST, formatISTTime, setButtonLoading } from '../../utils.js';
import { showToast } from '../../toasts.js';
import { refreshProjects } from '../dashboard.js';
import { loadProjectEvents } from '../timeline.js';
import { openUnifiedSettingsModal, closeUnifiedSettingsModal } from './modal_base.js';

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
  openUnifiedSettingsModal('trash');
}

export function closeTrashBinModal() {
  closeUnifiedSettingsModal();
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

export function populateDeleteProjectInfo() {
  if (DOM.delModalProjectName) {
    DOM.delModalProjectName.textContent = state.selectedProject || '—';
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
    const editEvents = (state.eventsData || []).filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
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
}

export function openDeleteProjectModal() {
  if (!state.selectedProject) {
    showToast('Please select a project to delete.', 'error');
    return;
  }
  populateDeleteProjectInfo();
  openUnifiedSettingsModal('trash');
}

export function closeDeleteProjectModal() {
  closeUnifiedSettingsModal();
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
  if (DOM.btnConfirmDeleteProject) setButtonLoading(DOM.btnConfirmDeleteProject, true, 'Deleting...');

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
    if (DOM.btnConfirmDeleteProject) setButtonLoading(DOM.btnConfirmDeleteProject, false);
  }
}

export function initModalTrashListeners() {
  if (DOM.btnHeaderTrash) DOM.btnHeaderTrash.addEventListener('click', openTrashBinModal);
  if (DOM.modalTrashBinClose) DOM.modalTrashBinClose.addEventListener('click', closeTrashBinModal);
  if (DOM.btnEmptyTrash) DOM.btnEmptyTrash.addEventListener('click', handleEmptyTrashBin);

  if (DOM.btnHeaderDeleteProj) DOM.btnHeaderDeleteProj.addEventListener('click', openDeleteProjectModal);
  if (DOM.modalDeleteProjectClose) DOM.modalDeleteProjectClose.addEventListener('click', closeDeleteProjectModal);
  if (DOM.modalDeleteProjectCancel) DOM.modalDeleteProjectCancel.addEventListener('click', closeDeleteProjectModal);
  if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.addEventListener('click', handleConfirmDeleteProject);
  setupDeleteScopeOptions();

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
