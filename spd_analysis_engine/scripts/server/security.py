"""
scripts/server/security.py
==========================
Security validations, host allowlists, CORS policy, origin verification,
API token management, and error message sanitization for EngineWebServer.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

# Security Bounds
MAX_REQUEST_BODY_SIZE: int = 10 * 1024 * 1024  # 10 MB limit
ALLOWED_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def generate_api_token() -> str:
    """Generate a cryptographically secure URL-safe API token."""
    return secrets.token_urlsafe(32)


def is_valid_host(host_header: str | None, server_port: int, bound_host: str = "127.0.0.1") -> bool:
    """
    Validate that the Host header belongs strictly to the localhost / loopback allowlist.
    Rejects unrecognized domains, public IPs, and mismatched port values.
    """
    if not host_header or not isinstance(host_header, str):
        return False

    host_str = host_header.strip()
    if not host_str:
        return False

    # Extract host and port taking IPv6 bracket notation into account
    if host_str.startswith("["):
        bracket_end = host_str.find("]")
        if bracket_end == -1:
            return False
        host_part = host_str[: bracket_end + 1]
        remainder = host_str[bracket_end + 1 :]
        if remainder.startswith(":"):
            port_part: str | None = remainder[1:]
        elif not remainder:
            port_part = None
        else:
            return False
    elif ":" in host_str:
        parts = host_str.split(":")
        if len(parts) != 2:
            return False
        host_part, port_part = parts[0], parts[1]
    else:
        host_part, port_part = host_str, None

    host_lower = host_part.lower()
    allowed = set(ALLOWED_LOOPBACK_HOSTS)
    if bound_host:
        allowed.add(bound_host.lower().strip("[]"))
        allowed.add(f"[{bound_host.lower().strip('[]')}]")

    raw_host = host_lower.strip("[]")
    if host_lower not in allowed and raw_host not in allowed:
        return False

    if port_part is not None:
        try:
            port_int = int(port_part)
            if port_int <= 0 or port_int > 65535:
                return False
            if server_port and server_port > 0 and port_int != server_port:
                return False
        except ValueError:
            return False

    return True


def is_trusted_origin(origin_header: str | None, server_port: int, bound_host: str = "127.0.0.1") -> bool:
    """
    Validate that the Origin header strictly matches localhost / loopback on the current server port.
    Rejects external domains, rogue ports, null, and public schemes.
    """
    if not origin_header or not isinstance(origin_header, str):
        return False

    origin_str = origin_header.strip()
    if not origin_str or origin_str.lower() == "null":
        return False

    try:
        parsed = urllib.parse.urlparse(origin_str)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False
    hostname = hostname.lower()

    allowed = set(ALLOWED_LOOPBACK_HOSTS)
    if bound_host:
        allowed.add(bound_host.lower().strip("[]"))
        allowed.add(f"[{bound_host.lower().strip('[]')}]")

    raw_host = hostname.strip("[]")
    if hostname not in allowed and raw_host not in allowed:
        return False

    origin_port = parsed.port
    if origin_port is not None and server_port and server_port > 0 and origin_port != server_port:
        return False

    return True


def get_cors_headers(origin_header: str | None, server_port: int, bound_host: str = "127.0.0.1") -> dict[str, str]:
    """
    Build CORS response headers. Never outputs wildcard '*'.
    Only echoes the origin if it matches the trusted localhost allowlist.
    """
    headers: dict[str, str] = {
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Token",
    }
    if origin_header and is_trusted_origin(origin_header, server_port, bound_host):
        headers["Access-Control-Allow-Origin"] = origin_header.strip()
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers


def extract_auth_token(headers: Any) -> str | None:
    """
    Extract API token from request headers or same-origin cookie.
    Never accepts token from query string or URL parameters.
    """
    # 1. Header: X-API-Token
    token = headers.get("X-API-Token")
    if token and isinstance(token, str) and token.strip():
        return token.strip()

    # 2. Header: Authorization: Bearer <token>
    auth = headers.get("Authorization")
    if auth and isinstance(auth, str) and auth.strip():
        parts = auth.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    # 3. Cookie: spd_token=<token>
    cookie_str = headers.get("Cookie")
    if cookie_str and isinstance(cookie_str, str):
        for cookie in cookie_str.split(";"):
            cookie = cookie.strip()
            if cookie.startswith("spd_token="):
                val = cookie[len("spd_token=") :].strip()
                if val:
                    return val

    return None


def verify_auth_token(provided_token: str | None, expected_token: str | None) -> bool:
    """Compare provided token against expected token using constant-time digest comparison."""
    if not provided_token or not expected_token:
        return False
    return hmac.compare_digest(str(provided_token).strip(), str(expected_token).strip())


def sanitize_error_message(message: str) -> str:
    """
    Strip filesystem paths, secrets, API keys, and sensitive internal paths from error messages
    so they cannot be leaked in client responses.
    """
    if not isinstance(message, str):
        message = str(message)

    # Windows drive paths (e.g. C:\Users\... or D:\Project\...)
    message = re.sub(r"[A-Za-z]:\\[^\s\"'<>]+", "[path]", message)
    # Unix paths (/home/..., /Users/..., /tmp/..., etc.)
    message = re.sub(r"/(?:Users|home|tmp|var|etc|opt)/[^\s\"'<>]+", "[path]", message)
    # Gemini / Google API keys
    message = re.sub(r"AIzaSy[A-Za-z0-9_-]{10,}", "[secret]", message)
    # Generic token / key patterns
    message = re.sub(r"(?i)(token|key|secret|password|bearer)[=:\s]+[A-Za-z0-9_-]{16,}", r"\1=[secret]", message)
    return message


class SecurityHandlerMixin:
    """Mixin for HTTP request handlers providing host validation, CORS, auth, and bounded body reading."""

    @property
    def server_instance(self) -> Any:
        return self.server.engine_web_server  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:
        port = getattr(self.server_instance, "port", 0)
        host = getattr(self.server_instance, "host", "127.0.0.1")
        if not is_valid_host(self.headers.get("Host"), port, host):
            self._send_error("Invalid or disallowed Host header", status=400)
            return

        origin = self.headers.get("Origin")
        if origin and not is_trusted_origin(origin, port, host):
            self._send_error("Forbidden: untrusted origin", status=403)
            return

        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        port = getattr(self.server_instance, "port", 0)
        host = getattr(self.server_instance, "host", "127.0.0.1")
        for k, v in get_cors_headers(origin, port, host).items():
            self.send_header(k, v)

    def _send_json(self, data: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, message: str, status: int = 400) -> None:
        clean = sanitize_error_message(message)
        self._send_json({"error": clean, "status": "error"}, status=status)

    def _validate_request(
        self,
        is_mutation: bool = False,
        requires_json: bool = False,
        auth_required: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """Validate Host, Origin, Token, Content-Type and read bounded body."""
        port = getattr(self.server_instance, "port", 0)
        host = getattr(self.server_instance, "host", "127.0.0.1")

        if not is_valid_host(self.headers.get("Host"), port, host):
            self._send_error("Invalid or disallowed Host header", status=400)
            return False, {}

        if not is_mutation:
            return True, {}

        clen_str = self.headers.get("Content-Length")
        body: dict[str, Any] = {}
        if clen_str is not None:
            try:
                content_len = int(clen_str)
                if content_len < 0:
                    self._send_error("Invalid Content-Length header", status=400)
                    return False, {}
                if content_len > MAX_REQUEST_BODY_SIZE:
                    self._send_error("Payload Too Large: body exceeds maximum limit", status=413)
                    return False, {}
            except ValueError:
                self._send_error("Invalid Content-Length header", status=400)
                return False, {}

            if content_len > 0:
                if requires_json:
                    ct = self.headers.get("Content-Type", "")
                    media_type = ct.split(";")[0].strip().lower()
                    if media_type != "application/json":
                        try:
                            self.rfile.read(content_len)
                        except Exception:
                            pass
                        self._send_error("Unsupported Media Type: Content-Type must be application/json", status=415)
                        return False, {}
                try:
                    raw_body = self.rfile.read(content_len).decode("utf-8")
                    body = json.loads(raw_body)
                except Exception as exc:
                    self._send_error(f"Malformed JSON body: {exc}", status=400)
                    return False, {}

        origin = self.headers.get("Origin")
        if origin and not is_trusted_origin(origin, port, host):
            self._send_error("Forbidden: untrusted origin", status=403)
            return False, {}

        if auth_required and getattr(self.server_instance, "require_token", True):
            token = extract_auth_token(self.headers)
            if not token:
                self._send_error("Unauthorized: missing API token", status=401)
                return False, {}
            expected = getattr(self.server_instance, "api_token", "")
            if not verify_auth_token(token, expected):
                self._send_error("Unauthorized: invalid API token", status=401)
                return False, {}

        return True, body
