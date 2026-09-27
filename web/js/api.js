/**
 * SPD Analysis Engine — API Client & Network Service
 */

export async function fetchAPI(endpoint, options = {}) {
  const res = await fetch(endpoint, options);
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.error || body.message) errMsg = body.error || body.message;
    } catch (_) {}
    throw new Error(errMsg);
  }
  return await res.json();
}

export async function apiGet(endpoint) {
  return await fetchAPI(endpoint);
}

export async function apiPost(endpoint, payload = {}) {
  return await fetchAPI(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function apiDelete(endpoint) {
  return await fetchAPI(endpoint, { method: 'DELETE' });
}

// Project & System Endpoints
export const fetchStatus = () => apiGet('/api/status');
export const fetchProjects = () => apiGet('/api/projects');
export const fetchProjectSpecs = (p) => apiGet(`/api/project/${encodeURIComponent(p)}/specs`);
export const fetchProjectEvents = (p) => apiGet(`/api/project/${encodeURIComponent(p)}/events`);
export const fetchProjectSessions = (p) => apiGet(`/api/project/${encodeURIComponent(p)}/sessions`);
export const fetchProjectLogs = (p) => apiGet(`/api/project/${encodeURIComponent(p)}/logs`);

export const startProject = (payload) => apiPost('/api/projects/start', payload);
export const stopProject = (projectName) => apiPost('/api/projects/stop', { project: projectName });
export const resumeProject = (projectName, payload = {}) => apiPost(`/api/project/${encodeURIComponent(projectName)}/resume`, payload);
export const sealBurstNow = (projectName) => apiPost(`/api/project/${encodeURIComponent(projectName)}/seal-burst`, {});
export const rollbackBurst = (projectName, eventId) => apiPost(`/api/project/${encodeURIComponent(projectName)}/rollback-burst/${eventId}`, {});
export const deleteProject = (projectName, payload) => apiPost(`/api/project/${encodeURIComponent(projectName)}/delete`, payload);

// AI & Synthesis Endpoints
export const analyzeEvent = (projectName, eventId) => apiPost(`/api/project/${encodeURIComponent(projectName)}/analyze-event/${eventId}`, {});
export const analyzeExec = (projectName, eventId) => apiPost(`/api/project/${encodeURIComponent(projectName)}/analyze-exec/${eventId}`, {});
export const fetchFileAnalysis = (projectName, params = {}) => {
  const q = new URLSearchParams(params).toString();
  return apiGet(`/api/project/${encodeURIComponent(projectName)}/file-analysis?${q}`);
};
export const fetchBurstOverview = (projectName, params = {}) => {
  const q = new URLSearchParams(params).toString();
  return apiGet(`/api/project/${encodeURIComponent(projectName)}/burst-overview?${q}`);
};
export const analyzeFile = (projectName, payload) => apiPost(`/api/project/${encodeURIComponent(projectName)}/analyze-file`, payload);
export const analyzeBurstOverview = (projectName, payload) => apiPost(`/api/project/${encodeURIComponent(projectName)}/analyze-burst-overview`, payload);
export const batchAnalyze = (projectName, forceAll = false) => apiPost(`/api/project/${encodeURIComponent(projectName)}/analyze-batch`, { force_all: forceAll });
export const batchAnalyzeStatus = (projectName) => apiGet(`/api/project/${encodeURIComponent(projectName)}/analyze-batch/status`);
export const getAiConfig = () => apiGet('/api/ai/config');
export const saveAiConfig = (payload) => apiPost('/api/ai/config', payload);
export const testAiConnection = (payload) => apiPost('/api/ai/test', payload);

// Git Micro-versioning Endpoints
export const getGitConfig = () => apiGet('/api/git/config');
export const saveGitConfig = (payload) => apiPost('/api/git/config', payload);
export const testGitConnection = (payload) => apiPost('/api/git/test', payload);
export const createGitRepo = (payload) => apiPost('/api/git/create-repo', payload);
export const prepareGitSync = (projectName) => apiPost(`/api/project/${encodeURIComponent(projectName)}/prepare-sync`, {});
export const pushGitSync = (projectName, payload) => apiPost(`/api/project/${encodeURIComponent(projectName)}/git-push`, payload);

// Trash Bin Endpoints
export const getTrash = () => apiGet('/api/trash');
export const restoreTrash = (trashId) => apiPost(`/api/trash/restore/${encodeURIComponent(trashId)}`, {});
export const purgeTrash = (trashId) => apiDelete(`/api/trash/purge/${encodeURIComponent(trashId)}`);
export const purgeAllTrash = () => apiDelete('/api/trash/purge-all');
