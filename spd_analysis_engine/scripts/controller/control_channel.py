"""
scripts/controller/control_channel.py
=====================================
Loopback authenticated control channel for worker communication.

Protocol (Version 1):
Request:
{
  "protocol": 1,
  "request_id": "...",
  "command": "STOP|SEAL|PAUSE|RESUME|RELOAD_CONFIG|SUPPRESS|PING",
  "project_id": "...",
  "worker_id": "...",
  "deadline_ms": 20000
}

Response:
{
  "protocol": 1,
  "request_id": "...",
  "ok": true,
  "state": "ACTIVE",
  "error": null
}

Invariants:
- Loopback only (127.0.0.1).
- Per-instance secret passed via environment/config, never command line.
- Commands are idempotent.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
VALID_COMMANDS = {"STOP", "SEAL", "PAUSE", "RESUME", "RELOAD_CONFIG", "SUPPRESS", "PING"}


class ControlChannelHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for worker loopback control channel."""

    server: "WorkerControlServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP request logging to stderr."""
        pass

    def do_POST(self) -> None:
        """Process incoming control command."""
        req_id = ""

        # 1. Enforce Host allowlist
        host_header = (self.headers.get("Host") or "").strip()
        host_name = host_header.split(":")[0].strip("[]").lower() if host_header else ""
        if not host_name or host_name not in ("127.0.0.1", "localhost", "::1"):
            self._send_response(
                request_id="unknown",
                ok=False,
                state=self.server.get_state(),
                error="Invalid or disallowed Host header",
                status=400,
            )
            return

        # 2. Enforce Origin validation if present
        origin = self.headers.get("Origin")
        if origin:
            try:
                p_orig = urllib.parse.urlparse(origin)
                o_host = (p_orig.hostname or "").strip("[]").lower()
                if p_orig.scheme not in ("http", "https") or o_host not in ("127.0.0.1", "localhost", "::1"):
                    self._send_response(
                        request_id="unknown",
                        ok=False,
                        state=self.server.get_state(),
                        error="Forbidden: untrusted Origin",
                        status=403,
                    )
                    return
            except Exception:
                self._send_response(
                    request_id="unknown",
                    ok=False,
                    state=self.server.get_state(),
                    error="Forbidden: malformed Origin",
                    status=403,
                )
                return

        # 3. Enforce application/json Content-Type
        ct = self.headers.get("Content-Type", "")
        media_type = ct.split(";")[0].strip().lower()
        if media_type != "application/json":
            self._send_response(
                request_id="unknown",
                ok=False,
                state=self.server.get_state(),
                error="Unsupported Media Type: Content-Type must be application/json",
                status=415,
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)
            data = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self._send_response(
                request_id="unknown",
                ok=False,
                state="UNKNOWN",
                error=f"Malformed request payload: {exc}",
                status=400,
            )
            return

        req_id = str(data.get("request_id") or uuid.uuid4().hex[:8])

        # 4. Verify per-instance authentication token
        auth_header = (self.headers.get("X-Worker-Token") or data.get("auth_token") or data.get("secret") or "").strip()
        if not auth_header or not self.server.verify_secret(str(auth_header)):
            logger.warning("Unauthorized control channel request (request_id=%s)", req_id)
            self._send_response(
                request_id=req_id,
                ok=False,
                state=self.server.get_state(),
                error="Unauthorized: invalid or missing worker secret",
                status=401,
            )
            return

        # 2. Validate protocol version
        if data.get("protocol") != PROTOCOL_VERSION:
            self._send_response(
                request_id=req_id,
                ok=False,
                state=self.server.get_state(),
                error=f"Unsupported protocol version {data.get('protocol')}; expected {PROTOCOL_VERSION}",
                status=400,
            )
            return

        command = str(data.get("command", "")).strip().upper()
        if command not in VALID_COMMANDS:
            self._send_response(
                request_id=req_id,
                ok=False,
                state=self.server.get_state(),
                error=f"Unknown command '{command}'. Valid: {sorted(VALID_COMMANDS)}",
                status=400,
            )
            return

        # 3. Dispatch command to worker state handler (idempotent)
        try:
            ok, current_state, err = self.server.execute_command(command, data)
            self._send_response(
                request_id=req_id,
                ok=ok,
                state=current_state,
                error=err,
                status=200 if ok else 500,
            )
        except Exception as exc:
            logger.exception("Error executing command '%s' (request_id=%s)", command, req_id)
            self._send_response(
                request_id=req_id,
                ok=False,
                state=self.server.get_state(),
                error=str(exc),
                status=500,
            )

    def _send_response(
        self,
        request_id: str,
        ok: bool,
        state: str,
        error: str | None,
        status: int = 200,
    ) -> None:
        payload = {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": ok,
            "state": state,
            "error": error,
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


class WorkerControlServer(http.server.HTTPServer):
    """
    Loopback-only HTTP server embedded in the worker process.
    Bound to 127.0.0.1 on an ephemeral port.
    """

    def __init__(
        self,
        secret: str,
        project_dir: Path | str,
        project_id: str = "",
        worker_id: str = "",
        command_handler: Callable[[str, dict[str, Any]], tuple[bool, str, str | None]] | None = None,
        get_state_fn: Callable[[], str] | None = None,
    ) -> None:
        # Bind to 127.0.0.1 on OS-allocated ephemeral port 0
        super().__init__(("127.0.0.1", 0), ControlChannelHandler)
        if not secret or not str(secret).strip():
            import secrets
            secret = secrets.token_urlsafe(32)
        self.secret = str(secret).strip()
        self.project_dir = Path(project_dir).resolve()
        self.project_id = project_id
        self.worker_id = worker_id
        self._command_handler = command_handler
        self._get_state_fn = get_state_fn or (lambda: "ACTIVE")
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.port: int = self.server_address[1]

        # Write endpoint info to worker.control.json (without secret!)
        self._write_control_info()

    def _write_control_info(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        info_file = self.project_dir / "worker.control.json"
        payload = {
            "host": "127.0.0.1",
            "port": self.port,
            "pid": os.getpid(),
            "worker_id": self.worker_id,
            "project_id": self.project_id,
            "protocol": PROTOCOL_VERSION,
        }
        tmp = info_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(info_file)

    def remove_control_info(self) -> None:
        info_file = self.project_dir / "worker.control.json"
        if info_file.exists():
            try:
                info_file.unlink()
            except OSError:
                pass

    def verify_secret(self, candidate: str) -> bool:
        """Constant-time token verification."""
        import hmac
        if not candidate or not candidate.strip() or not self.secret or not self.secret.strip():
            return False
        return hmac.compare_digest(candidate.strip(), self.secret.strip())

    def get_state(self) -> str:
        return self._get_state_fn()

    def execute_command(self, command: str, data: dict[str, Any]) -> tuple[bool, str, str | None]:
        if self._command_handler is not None:
            return self._command_handler(command, data)
        # Default fallback responses for standard commands (idempotent)
        if command == "PING":
            return True, self.get_state(), None
        if command == "STOP":
            return True, "STOPPING", None
        return True, self.get_state(), None

    def start(self) -> None:
        """Start the control server on a background daemon thread."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True, name="WorkerControlServer")
        self._thread.start()
        logger.info("Worker control channel listening on 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        """Shutdown the control server and remove control info file."""
        self.shutdown()
        self.server_close()
        self.remove_control_info()


def send_worker_command(
    project_dir: Path | str,
    secret: str = "",
    command: str = "PING",
    project_id: str = "",
    worker_id: str = "",
    deadline_ms: int = 20000,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """
    Send an authenticated command to a running worker over the loopback control channel.
    Returns response dict matching protocol: {"protocol": 1, "request_id": ..., "ok": bool, "state": ..., "error": ...}
    """
    pdir = Path(project_dir).resolve()
    effective_secret = secret or os.environ.get("SPD_WORKER_SECRET", "")
    if not effective_secret:
        secret_file = pdir / ".worker_secret"
        if secret_file.exists():
            try:
                effective_secret = secret_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    info_file = pdir / "worker.control.json"
    if not info_file.exists():
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": "none",
            "ok": False,
            "state": "UNKNOWN",
            "error": f"worker.control.json not found in {pdir}",
        }

    try:
        data = json.loads(info_file.read_text(encoding="utf-8"))
        host = data.get("host", "127.0.0.1")
        port = int(data.get("port", 0))
    except Exception as exc:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": "none",
            "ok": False,
            "state": "UNKNOWN",
            "error": f"Could not parse worker.control.json: {exc}",
        }

    req_id = f"req_{uuid.uuid4().hex[:8]}"
    payload = {
        "protocol": PROTOCOL_VERSION,
        "request_id": req_id,
        "command": command.strip().upper(),
        "project_id": project_id or data.get("project_id", ""),
        "worker_id": worker_id or data.get("worker_id", ""),
        "deadline_ms": deadline_ms,
        "auth_token": effective_secret,
    }

    url = f"http://{host}:{port}/"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Worker-Token": effective_secret,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return resp_data
    except urllib.error.HTTPError as http_err:
        try:
            err_data = json.loads(http_err.read().decode("utf-8"))
            return err_data
        except Exception:
            return {
                "protocol": PROTOCOL_VERSION,
                "request_id": req_id,
                "ok": False,
                "state": "UNKNOWN",
                "error": f"HTTP {http_err.code}: {http_err.reason}",
            }
    except Exception as exc:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": req_id,
            "ok": False,
            "state": "UNKNOWN",
            "error": str(exc),
        }


def send_control_command(
    engine_root: Path | str | None,
    slug: str,
    command: str,
    timeout_s: float = 2.0,
) -> dict[str, Any]:
    """
    Convenience wrapper resolving project_dir from engine_root + slug,
    then dispatching command via send_worker_command.
    """
    if engine_root is None:
        pdir = Path(__file__).resolve().parent.parent.parent / "current" / slug
    else:
        pdir = Path(engine_root).resolve() / "current" / slug
    if not pdir.exists() and engine_root:
        cand = Path(engine_root).resolve() / "projects" / slug
        if cand.exists():
            pdir = cand
    return send_worker_command(pdir, command=command, timeout_s=timeout_s)

