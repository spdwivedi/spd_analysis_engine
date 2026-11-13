"""
scripts/ai/sanitizer.py
=======================
Sensitive data redactor and secret sanitizer for the SPD Analysis Engine.
Redacts API keys, credentials, tokens, JWTs, PEM private keys, and sensitive files
prior to transmitting any content to AI providers.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import PurePath
from typing import Any

logger = logging.getLogger(__name__)

# Canonical sanitizer version tracked in AI cache key
SANITIZER_VERSION = "v2.0"

# Sensitive file patterns (matches IgnorePolicy sensitive-file blocklist)
SENSITIVE_FILE_PATTERNS = (
    ".env*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "credentials*",
    "*.pfx",
    "*.p12",
    "*.keystore",
    "*.jks",
)

# -----------------------------------------------------------------------------
# Regex Patterns for In-Text Redaction
# -----------------------------------------------------------------------------

_AWS_KEY_PATTERN = re.compile(r"\b(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b")
_GITHUB_TOKEN_PATTERN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{82})\b")
_GOOGLE_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z\-_]{32,45}\b")
_OPENAI_KEY_PATTERN = re.compile(r"\b(sk-[a-zA-Z0-9]{20,}|sk-proj-[a-zA-Z0-9_\-]{20,})\b")
_SLACK_TOKEN_PATTERN = re.compile(r"\bxox[baprs]-[0-9a-zA-Z\-]{10,80}\b")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
_PEM_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")

# Key-value assignment patterns: password=, secret=, token=, api_key=
_KV_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api_key)\s*([:=])\s*(['\"]?)([^'\"\s,;&\r\n]{4,})\3",
)

# Credential-bearing URLs (e.g. https://user:pass@host)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(https?://)([^:\s/@]+):([^@\s/]+)@",
)


def is_sensitive_file(file_path: str) -> bool:
    """Return True if file matches sensitive blocklist (metadata-only)."""
    if not file_path:
        return False
    norm = file_path.replace("\\", "/").strip().lstrip("/")
    name = PurePath(norm).name
    for pat in SENSITIVE_FILE_PATTERNS:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(norm, pat):
            return True
    return False


def redact_sensitive_text(text: str) -> str:
    """
    Redact all known secrets and credentials from a text string.
    Replaces tokens with standard redaction placeholders.
    """
    if not text:
        return text

    # 1. PEM Private Keys
    sanitized = _PEM_KEY_PATTERN.sub("[REDACTED_PEM_PRIVATE_KEY]", text)

    # 2. AWS Credentials
    sanitized = _AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", sanitized)

    # 3. GitHub Tokens
    sanitized = _GITHUB_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", sanitized)

    # 4. Google AI Keys
    sanitized = _GOOGLE_KEY_PATTERN.sub("[REDACTED_GOOGLE_API_KEY]", sanitized)

    # 5. OpenAI-style Keys
    sanitized = _OPENAI_KEY_PATTERN.sub("[REDACTED_OPENAI_KEY]", sanitized)

    # 6. Slack Tokens
    sanitized = _SLACK_TOKEN_PATTERN.sub("[REDACTED_SLACK_TOKEN]", sanitized)

    # 7. JWTs
    sanitized = _JWT_PATTERN.sub("[REDACTED_JWT]", sanitized)

    # 8. Credential-bearing URLs
    sanitized = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED_USER]:[REDACTED_PASSWORD]@", sanitized)

    # 9. Key-value secrets (password=, secret=, token=, api_key=)
    sanitized = _KV_SECRET_PATTERN.sub(r"\1\2\3[REDACTED_SECRET]\3", sanitized)

    return sanitized


def sanitize_diff_content(diff_text: str, file_path: str = "") -> str:
    """
    Sanitize patch content.
    If the file itself is in the sensitive file blocklist, replace entire content
    with '[REDACTED FILE: <path>]'. Otherwise, redact in-text secrets.
    """
    if file_path and is_sensitive_file(file_path):
        return f"[REDACTED FILE: {file_path}]"

    # Also detect diff headers referencing sensitive files (e.g. --- a/.env)
    for line in diff_text.splitlines()[:5]:
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            ref_path = line[6:].strip()
            if is_sensitive_file(ref_path):
                return f"[REDACTED FILE: {ref_path}]"

    return redact_sensitive_text(diff_text)


def sanitize_context_dict(context: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact secrets across dictionary context values."""
    cleaned: dict[str, Any] = {}
    for k, v in context.items():
        if isinstance(v, str):
            cleaned[k] = redact_sensitive_text(v)
        elif isinstance(v, dict):
            cleaned[k] = sanitize_context_dict(v)
        elif isinstance(v, list):
            cleaned[k] = [
                redact_sensitive_text(x) if isinstance(x, str) else (
                    sanitize_context_dict(x) if isinstance(x, dict) else x
                )
                for x in v
            ]
        else:
            cleaned[k] = v
    return cleaned
