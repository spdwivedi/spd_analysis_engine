/**
 * SPD Analysis Engine — GPU Optimization ("Eco Mode") Controller
 */
import { DOM } from './dom.js';

export function isEcoModeActive() {
  return document.body.classList.contains('eco-mode');
}

export function setEcoMode(enable) {
  const active = Boolean(enable);
  document.body.classList.toggle('eco-mode', active);
  try {
    localStorage.setItem('spd_eco_mode', String(active));
  } catch (_) {}

  // Update button state in header
  if (DOM.btnHeaderEcoMode) {
    DOM.btnHeaderEcoMode.classList.toggle('active', active);
    DOM.btnHeaderEcoMode.title = active
      ? 'Eco Mode Active (GPU effects & animations paused)'
      : 'Toggle GPU Optimization (Eco Mode)';
  }

  // Update settings modal checkbox if present
  if (DOM.chkEcoModeSetting) {
    DOM.chkEcoModeSetting.checked = active;
  }
}

export function toggleEcoMode() {
  setEcoMode(!isEcoModeActive());
}

export function initEcoMode() {
  let saved = false;
  try {
    saved = localStorage.getItem('spd_eco_mode') === 'true';
  } catch (_) {}
  setEcoMode(saved);

  if (DOM.btnHeaderEcoMode) {
    DOM.btnHeaderEcoMode.addEventListener('click', () => {
      toggleEcoMode();
    });
  }

  if (DOM.chkEcoModeSetting) {
    DOM.chkEcoModeSetting.addEventListener('change', (e) => {
      setEcoMode(e.target.checked);
    });
  }
}
