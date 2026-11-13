/**
 * SPD Analysis Engine — Base API Client
 */

let _cachedToken = null;

export function getApiToken() {
  if (_cachedToken) return _cachedToken;
  if (typeof window !== 'undefined' && window.__SPD_API_TOKEN__) {
    _cachedToken = window.__SPD_API_TOKEN__;
    return _cachedToken;
  }
  if (typeof document !== 'undefined') {
    const meta = document.querySelector('meta[name="spd-api-token"]');
    if (meta && meta.content) {
      _cachedToken = meta.content;
      return _cachedToken;
    }
    const match = document.cookie.match(/(?:^|;\s*)spd_token=([^;]+)/);
    if (match) {
      _cachedToken = decodeURIComponent(match[1]);
      return _cachedToken;
    }
  }
  return null;
}

export async function fetchAPI(endpoint, options = {}) {
  const headers = Object.assign({}, options.headers);
  const token = getApiToken();
  if (token && !headers['X-API-Token'] && !headers['Authorization']) {
    headers['X-API-Token'] = token;
  }
  const res = await fetch(endpoint, { ...options, headers });
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
