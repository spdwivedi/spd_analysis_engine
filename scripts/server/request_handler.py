"""
scripts/server/request_handler.py
=================================
HTTP request dispatcher and SSE streaming handler for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

try:
    from .routes_projects import ProjectRoutesMixin
    from .routes_events import EventRoutesMixin
    from .routes_ai import AIRoutesMixin
    from .routes_trash import TrashRoutesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.routes_projects import ProjectRoutesMixin
        from spd_analysis_engine.scripts.server.routes_events import EventRoutesMixin
        from spd_analysis_engine.scripts.server.routes_ai import AIRoutesMixin
        from spd_analysis_engine.scripts.server.routes_trash import TrashRoutesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.server.routes_projects import ProjectRoutesMixin
        from scripts.server.routes_events import EventRoutesMixin
        from scripts.server.routes_ai import AIRoutesMixin
        from scripts.server.routes_trash import TrashRoutesMixin

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class _EngineRequestHandler(ProjectRoutesMixin, EventRoutesMixin, AIRoutesMixin, TrashRoutesMixin, BaseHTTPRequestHandler):
    """Custom request handler with REST API and SSE support."""

    # Disable default noisy request logging on every SSE ping
    def log_message(self, format: str, *args: Any) -> None:
        if args and str(args[0]).startswith("GET /api/stream"):
            return
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    @property
    def server_instance(self) -> Any:
        return self.server.engine_web_server  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # HTTP Options (CORS preflight)
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message, "status": "error"}, status=status)

    # ------------------------------------------------------------------
    # GET Handlers
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 1. API: /api/status
        if path == "/api/status":
            self._handle_api_status()
            return

        # 2. API: /api/projects
        if path == "/api/projects":
            self._handle_api_projects()
            return

        # 3. API: /api/stream (SSE)
        if path == "/api/stream":
            self._handle_api_stream()
            return

        # 4. API: /api/project/<name>/events
        if path.startswith("/api/project/") and path.endswith("/events"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_events(proj_name)
                return

        # 5. API: /api/project/<name>/logs
        if path.startswith("/api/project/") and path.endswith("/logs"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_logs(proj_name)
                return

        # 6. API: /api/project/<name>/export
        if path.startswith("/api/project/") and path.endswith("/export"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_export(proj_name)
                return

        # 7. API: /api/project/<name>/shadow-git
        if path.startswith("/api/project/") and path.endswith("/shadow-git"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_shadow_git(proj_name)
                return

        # 8. API: /api/git/config
        if path == "/api/git/config":
            self._handle_api_get_git_config()
            return

        # 9. API: /api/ai/config
        if path == "/api/ai/config":
            self._handle_api_get_ai_config()
            return

        # 10. API: /api/project/<name>/specs
        if path.startswith("/api/project/") and path.endswith("/specs"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_project_specs(proj_name)
                return

        # 11. API: /api/project/<name>/analyze-batch/status
        if path.startswith("/api/project/") and path.endswith("/analyze-batch/status"):
            parts = path.split("/")
            if len(parts) == 6:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_batch_status(proj_name)
                return

        # 12. API: /api/trash
        if path == "/api/trash":
            self._handle_api_get_trash()
            return

        # 13. API: /api/project/<name>/sessions
        if path.startswith("/api/project/") and path.endswith("/sessions"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_sessions(proj_name)
                return

        # 14. API: /api/project/<name>/file-analysis or /api/projects/<name>/file-analysis
        if (path.startswith("/api/project/") or path.startswith("/api/projects/")) and path.endswith("/file-analysis"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                query = urllib.parse.parse_qs(parsed.query)
                self._handle_api_get_file_analysis(proj_name, query)
                return

        # 15. API: /api/project/<name>/burst-overview or /api/projects/<name>/burst-overview
        if (path.startswith("/api/project/") or path.startswith("/api/projects/")) and path.endswith("/burst-overview"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                query = urllib.parse.parse_qs(parsed.query)
                self._handle_api_get_burst_overview(proj_name, query)
                return

        # 16. Static file serving from web/
        self._handle_static_file(parsed.path)

    # ------------------------------------------------------------------
    # POST Handlers
    # ------------------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Read JSON body
        content_len = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_len > 0:
            try:
                raw_body = self.rfile.read(content_len).decode("utf-8")
                body = json.loads(raw_body)
            except Exception as exc:
                self._send_error(f"Malformed JSON body: {exc}", status=400)
                return

        if path == "/api/projects/start":
            self._handle_api_start_project(body)
            return

        if path == "/api/projects/stop":
            self._handle_api_stop_project(body)
            return

        if path == "/api/git/config":
            self._handle_api_save_git_config(body)
            return

        if path == "/api/git/test":
            self._handle_api_test_git_connection(body)
            return

        if path == "/api/git/create-repo":
            self._handle_api_create_git_repo(body)
            return

        if path == "/api/ai/config":
            self._handle_api_save_ai_config(body)
            return

        if path == "/api/ai/test":
            self._handle_api_test_ai_connection(body)
            return

        # API: /api/project/<name>/prepare-sync
        if path.startswith("/api/project/") and path.endswith("/prepare-sync"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_prepare_sync(proj_name, body)
                return

        # API: /api/project/<name>/analyze-batch
        if path.startswith("/api/project/") and path.endswith("/analyze-batch"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_batch(proj_name, body)
                return

        # API: /api/project/<name>/analyze-file or /api/projects/<name>/analyze-file
        if (path.startswith("/api/project/") or path.startswith("/api/projects/")) and path.endswith("/analyze-file"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_file(proj_name, body)
                return

        # API: /api/project/<name>/analyze-burst-overview or /api/projects/<name>/analyze-burst-overview
        if (path.startswith("/api/project/") or path.startswith("/api/projects/")) and path.endswith("/analyze-burst-overview"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_burst_overview(proj_name, body)
                return



        # API: /api/project/<name>/resume or /api/projects/<name>/resume
        if (path.startswith("/api/project/") or path.startswith("/api/projects/")) and path.endswith("/resume"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_resume_project(proj_name, body)
                return

        # API: /api/project/<name>/exec
        if path.startswith("/api/project/") and path.endswith("/exec"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_record_exec(proj_name, body)
                return

        # API: /api/project/<name>/git-push
        if path.startswith("/api/project/") and path.endswith("/git-push"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_git_push(proj_name, body)
                return

        # API: /api/project/<name>/analyze-event/<event_id>
        if path.startswith("/api/project/") and "/analyze-event/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "analyze-event":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                self._handle_api_analyze_event(proj_name, event_id_str, body)
                return

        # API: /api/project/<name>/analyze-exec/<event_id>
        if path.startswith("/api/project/") and "/analyze-exec/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "analyze-exec":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                self._handle_api_analyze_exec(proj_name, event_id_str, body)

        # API: /api/project/<name>/rollback-burst/<event_id>
        if path.startswith("/api/project/") and "/rollback-burst/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "rollback-burst":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                try:
                    eid = int(event_id_str)
                    self._handle_api_rollback_burst(proj_name, eid)
                except ValueError:
                    self._send_error("Invalid event_id; must be an integer", status=400)
                return

        # API: /api/project/<name>/seal-burst
        if path.startswith("/api/project/") and path.endswith("/seal-burst"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_seal_burst(proj_name)
                return

        # API: /api/project/<name>/delete
        if path.startswith("/api/project/") and path.endswith("/delete"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_delete_project(proj_name, body)
                return

        # Trash REST endpoints (POST fallback)
        if path.startswith("/api/trash/restore/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/restore/"):])
            self._handle_api_restore_trash(trash_id)
            return

        if path.startswith("/api/trash/purge/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/purge/"):])
            self._handle_api_purge_trash(trash_id)
            return

        if path == "/api/trash/purge-all":
            self._handle_api_purge_all_trash()
            return

        self._send_error(f"Endpoint not found: {path}", status=404)

    # ------------------------------------------------------------------
    # DELETE Handlers
    # ------------------------------------------------------------------

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/trash/purge/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/purge/"):])
            self._handle_api_purge_trash(trash_id)
            return

        if path == "/api/trash/purge-all":
            self._handle_api_purge_all_trash()
            return

        self._send_error(f"Endpoint not found: {path}", status=404)

    # ------------------------------------------------------------------
    # SSE Stream & Static Assets
    # ------------------------------------------------------------------

    def _handle_api_stream(self) -> None:
        """Server-Sent Events (SSE) streaming endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        engine = self.server_instance

        # Initial connect message
        try:
            init_frame = f"event: connected\ndata: {json.dumps({'time': _utcnow_iso()})}\n\n"
            self.wfile.write(init_frame.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

        while not engine._shutdown_event.is_set():
            try:
                sup = engine.supervisor
                statuses = sup.get_status()
                payload = {
                    "server_time": _utcnow_iso(),
                    "workers": statuses,
                    "active_count": sum(1 for s in statuses if s.get("alive")),
                }
                msg = f"event: ping\ndata: {json.dumps(payload)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.5)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                break
            except Exception as exc:
                logger.debug("Error streaming SSE data: %s", exc)
                break

    # ------------------------------------------------------------------
    # Static Assets Serving
    # ------------------------------------------------------------------

    def _handle_static_file(self, raw_path: str) -> None:
        engine = self.server_instance
        clean_path = raw_path.lstrip("/")
        if not clean_path or clean_path == "/":
            clean_path = "index.html"

        # Prevent directory traversal attacks
        file_path = (engine.web_dir / clean_path).resolve()
        try:
            file_path.relative_to(engine.web_dir)
        except ValueError:
            self._send_error("Forbidden", status=403)
            return

        if not file_path.is_file():
            self._send_error(f"File not found: {clean_path}", status=404)
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
            mime_type += "; charset=utf-8"

        try:
            content = file_path.read_bytes()
            if clean_path == "style.css" or file_path.name == "style.css":
                text = content.decode("utf-8", errors="replace")
                if "@import" in text:
                    def _replace_import(match: re.Match) -> str:
                        import_path = (match.group(1) or match.group(2) or "").strip("'\"")
                        target_file = (file_path.parent / import_path).resolve()
                        if target_file.is_file():
                            return target_file.read_text(encoding="utf-8", errors="replace")
                        return match.group(0)

                    bundled = re.sub(
                        r"""@import\s+(?:url\(['"]?([^'"]+)['"]?\)|['"]([^'"]+)['"]);?""",
                        _replace_import,
                        text,
                    )
                    content = bundled.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self._send_error(f"Error reading file: {exc}", status=500)
