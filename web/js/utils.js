/**
 * SPD Analysis Engine — Web Portal Utilities & Formatters
 */

export function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export function scrubSensitiveText(text) {
  if (!text || typeof text !== 'string') return text;
  return text
    // GitHub PATs
    .replace(/gh[pousr]_[A-Za-z0-9_]{16,255}/g, '[REDACTED_GH_TOKEN]')
    // Google / Gemini API Keys
    .replace(/AIza[0-9A-Za-z\-_]{35}/g, '[REDACTED_API_KEY]')
    // Generic Bearer tokens
    .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, 'Bearer [REDACTED_TOKEN]')
    // Basic Auth / embedded URLs
    .replace(/:\/\/([^:\s]+):([^@\s]+)@/g, '://$1:[REDACTED_SECRET]@')
    // Private key headers
    .replace(/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, '[REDACTED_PRIVATE_KEY]')
    // High-entropy generic tokens or secrets in key-value pairs
    .replace(/(?:api_key|apikey|secret|token|password|auth_token)\s*[:=]\s*['"][^\s'"]{8,}['"]/gi, (m) => {
      const parts = m.split(/[:=]/);
      return `${parts[0]}: "[REDACTED_CREDENTIAL]"`;
    });
}

export function slugify(name) {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s-]+/g, '_')
    .slice(0, 64) || 'unnamed';
}

export function formatUptime(seconds) {
  if (seconds == null || isNaN(seconds)) return '—';
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function formatIST(timestampStr, includeDate = true) {
  if (!timestampStr) return '—';
  try {
    const d = new Date(timestampStr);
    if (isNaN(d.getTime())) return timestampStr;
    const options = {
      timeZone: 'Asia/Kolkata',
      hour12: true,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    };
    if (includeDate) {
      options.year = 'numeric';
      options.month = 'short';
      options.day = '2-digit';
    }
    return `${new Intl.DateTimeFormat('en-IN', options).format(d)} IST`;
  } catch (e) {
    return timestampStr;
  }
}

export function formatISTTime(timestampStr) {
  if (!timestampStr) return '';
  try {
    if (typeof timestampStr === 'string' && timestampStr.endsWith('IST')) {
      return timestampStr;
    }
    const d = new Date(timestampStr);
    if (isNaN(d.getTime())) {
      if (typeof timestampStr === 'string' && timestampStr.includes(':')) {
        return `${timestampStr} IST`;
      }
      return timestampStr;
    }
    const formatted = new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour12: true,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(d);
    return `${formatted} IST`;
  } catch (e) {
    return timestampStr;
  }
}

export function formatPreciseCountdown(seconds) {
  if (seconds <= 0) return '00:00.0';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 10);
  const minStr = String(mins).padStart(2, '0');
  const secStr = String(secs).padStart(2, '0');
  return `${minStr}:${secStr}.${ms}`;
}
