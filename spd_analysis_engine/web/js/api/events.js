/**
 * SPD Analysis Engine — Events API Service
 * Canonical endpoints for bounded summaries, event detail, and on-demand file diffs.
 */
import { apiGet } from './client.js';

export async function fetchEventsSummary(params = {}) {
  const q = new URLSearchParams();
  if (params.since_id != null) q.set('since_id', params.since_id);
  if (params.limit != null) q.set('limit', params.limit);
  if (params.types) q.set('types', params.types);
  if (params.session_id != null) q.set('session_id', params.session_id);
  if (params.project) q.set('project', params.project);

  const qs = q.toString();
  return await apiGet(`/events${qs ? `?${qs}` : ''}`);
}

export async function fetchEventDetail(eventId, project = null) {
  const q = project ? `?project=${encodeURIComponent(project)}` : '';
  return await apiGet(`/events/${encodeURIComponent(eventId)}${q}`);
}

export async function fetchFileDiff(eventId, filePath, project = null) {
  const q = project ? `?project=${encodeURIComponent(project)}` : '';
  const cleanPath = String(filePath).replace(/^\/+/, '');
  return await apiGet(`/events/${encodeURIComponent(eventId)}/files/${encodeURIComponent(cleanPath)}/diff${q}`);
}

export async function fetchLegacyProjectEvents(project, limit = 500) {
  const q = limit ? `?limit=${encodeURIComponent(limit)}` : '';
  return await apiGet(`/api/project/${encodeURIComponent(project)}/events${q}`);
}
