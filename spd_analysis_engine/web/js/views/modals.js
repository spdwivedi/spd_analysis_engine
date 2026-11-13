/**
 * SPD Analysis Engine — Modal Views Aggregator
 * Re-exports modular controllers: modal_base, modal_ai, modal_git, modal_trash.
 */

export * from './modals/modal_base.js';
export * from './modals/modal_ai.js';
export * from './modals/modal_git.js';
export * from './modals/modal_trash.js';
export * from './modals/modal_health.js';
export * from './modals/modal_workspace.js';

import { initModalBaseListeners } from './modals/modal_base.js';
import { initModalAiListeners } from './modals/modal_ai.js';
import { initModalGitListeners } from './modals/modal_git.js';
import { initModalTrashListeners } from './modals/modal_trash.js';
import { initModalHealthListeners } from './modals/modal_health.js';
import { initModalWorkspaceListeners } from './modals/modal_workspace.js';

/**
 * Initialize all modal event listeners across base, AI, Git, Trash, Health, and Workspace subsystems.
 */
export function initModalListeners() {
  initModalBaseListeners();
  initModalAiListeners();
  initModalGitListeners();
  initModalTrashListeners();
  initModalHealthListeners();
  initModalWorkspaceListeners();
}
