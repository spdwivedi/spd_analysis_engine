"""
scripts/web_server.py
=====================
Zero-dependency backend HTTP server & SSE streamer for the SPD Analysis Engine.

Implements ``EngineWebServer`` using Python standard library ``http.server``
and ``threading`` (no Flask or FastAPI required).

Routes
------
Static assets:
    GET /                 -> Serves web/index.html
    GET /style.css        -> Serves web/style.css
    GET /app.js           -> Serves web/app.js
    GET /*                -> Serves requested file from web/

REST API:
    GET  /api/status               -> Live worker processes, uptime, memory, CPU
    GET  /api/projects             -> All projects grouped by tier (current, last_run, history)
    POST /api/projects/start        -> Launch worker {project, path, scaffold, debounce}
    POST /api/projects/stop         -> Stop worker and trigger rotation {project}
    GET  /api/project/<name>/events -> All events & patches from session.db
    GET  /api/project/<name>/logs   -> Last 150 lines from worker.log
    GET  /api/project/<name>/export -> Full session JSON export package
    GET  /api/stream               -> Server-Sent Events (SSE) live updates (1.5s keep-alive)
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .server import (
        ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin, EventRoutesMixin,
    )
    from .orchestrator import Supervisor
    from .storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from .git_shadow import ShadowGit, GitCredentialManager
    from .shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
    from .ai_analyzer import AISynthesizer
    from .trash_manager import TrashManager
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server import (
            ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin, EventRoutesMixin,
        )
        from spd_analysis_engine.scripts.orchestrator import Supervisor
        from spd_analysis_engine.scripts.storage_rotator import (
            StorageRotator, SessionDB, _slug,
            read_project_meta, update_project_meta, consolidate_project_history,
        )
        from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager
        from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
        from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
        from spd_analysis_engine.scripts.trash_manager import TrashManager
    except (ImportError, ModuleNotFoundError):
        from scripts.server import (
            ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin, EventRoutesMixin,
        )
        from scripts.orchestrator import Supervisor
        from scripts.storage_rotator import (
            StorageRotator, SessionDB, _slug,
            read_project_meta, update_project_meta, consolidate_project_history,
        )
        from scripts.git_shadow import ShadowGit, GitCredentialManager
        from scripts.shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
        from scripts.ai_analyzer import AISynthesizer
        from scripts.trash_manager import TrashManager


logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

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
    def server_instance(self) -> EngineWebServer:
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

        # 14. Static file serving from web/
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

        # API: /api/project/<name>/resume
        if path.startswith("/api/project/") and path.endswith("/resume"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_resume_project(proj_name)
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
    # SSE Stream & Static Assets (Handlers in server/ mixins)
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
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self._send_error(f"Error reading file: {exc}", status=500)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that silences socket disconnects and aborts on Windows."""

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return  # Silently ignore client tab reloads/browser disconnects on Windows
        super().handle_error(request, client_address)


class EngineWebServer:
    """
    Standard-library based HTTP server for the SPD Analysis Engine.

    Parameters
    ----------
    engine_root : Path | str
        Root directory of the SPD Analysis Engine.
    host : str
        Host IP to bind to (default: ``127.0.0.1``).
    port : int
        Port to bind to (default: ``8765``).
    web_dir : Path | str | None
        Directory containing static web files (defaults to ``<engine_root>/web``).
    """

    def __init__(
        self,
        engine_root: Path | str,
        host: str = "127.0.0.1",
        port: int = 8765,
        web_dir: Path | str | None = None,
    ) -> None:
        self.engine_root = Path(engine_root).resolve()
        self.host = host
        self.port = port
        self.web_dir = Path(web_dir).resolve() if web_dir else (self.engine_root / "web")

        self.supervisor = Supervisor(self.engine_root)
        self.rotator = StorageRotator(self.engine_root)
        self.trash_mgr = TrashManager(self.engine_root)
        self._batch_jobs: dict[str, dict[str, Any]] = {}
        self._batch_jobs_lock = threading.Lock()

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

    def start(self, background: bool = True) -> int:
        """
        Start the HTTP server. If *port* is taken, tries successive ports.
        Returns the bound port.
        """
        self._shutdown_event.clear()

        attempts = 10
        current_port = self.port
        while attempts > 0:
            try:
                self._server = _QuietThreadingHTTPServer((self.host, current_port), _EngineRequestHandler)
                self._server.engine_web_server = self  # type: ignore[attr-defined]
                self.port = self._server.server_address[1]
                break
            except OSError as exc:
                logger.warning("Port %d busy: %s; trying %d...", current_port, exc, current_port + 1)
                current_port += 1
                attempts -= 1

        if self._server is None:
            raise RuntimeError(f"Could not bind to any port near {self.port}")

        logger.info("EngineWebServer listening on http://%s:%d (web_dir=%s)", self.host, self.port, self.web_dir)

        if background:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        else:
            self._server.serve_forever()

        return self.port

    def shutdown(self) -> None:
        """Gracefully stop the HTTP server and all background connections."""
        self._shutdown_event.set()
        if self._server is not None:
            logger.info("Stopping EngineWebServer...")
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("EngineWebServer stopped.")

    def _run_async_batch(
        self,
        slug: str,
        job_id: str,
        event_ids: list[int],
        db_path: Path,
        target_path: Path | None,
    ) -> None:
        """Background worker executing batch AI synthesis sequentially with deep context."""
        logger.info("Starting async batch AI analysis for %s (job %s, %d events)", slug, job_id, len(event_ids))
        try:
            syn = AISynthesizer(self.engine_root)
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()

            previous_intent = None
            analyzed = 0

            for idx, eid in enumerate(event_ids, 1):
                # Check if job was superseded
                with self._batch_jobs_lock:
                    curr_job = self._batch_jobs.get(slug, {})
                    if curr_job.get("job_id") != job_id:
                        logger.info("Batch job %s for %s was superseded. Halting.", job_id, slug)
                        db.close()
                        return
                    self._batch_jobs[slug]["current_event_id"] = eid
                    self._batch_jobs[slug]["current_index"] = idx

                cur.execute("SELECT * FROM events WHERE id = ?", (eid,))
                erow = cur.fetchone()
                if not erow:
                    continue
                event_dict = dict(erow)

                cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (eid,))
                prows = cur.fetchall()
                patches = [dict(p) for p in prows]

                # Check rate limiter only for live external Gemini calls
                cfg = syn.load_config(syn.engine_root)
                pref = (cfg.get("preferred_provider") or "gemini").lower()
                if pref == "gemini":
                    def _on_cooldown(remaining: float) -> None:
                        with self._batch_jobs_lock:
                            if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                                self._batch_jobs[slug]["cooling_down"] = True
                                self._batch_jobs[slug]["cooldown_remaining_s"] = round(remaining, 1)

                    syn.rate_limiter.wait_if_needed(progress_callback=_on_cooldown)

                    with self._batch_jobs_lock:
                        if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                            self._batch_jobs[slug]["cooling_down"] = False
                            self._batch_jobs[slug]["cooldown_remaining_s"] = 0.0

                try:
                    analysis = syn.analyze_burst(
                        project_name=slug,
                        workspace_path=str(target_path) if target_path else "",
                        event=event_dict,
                        patches=patches,
                        previous_summary=previous_intent,
                    )
                    previous_intent = analysis.get("intent", "")
                    db.update_event_ai_summary(eid, json.dumps(analysis))
                    analyzed += 1
                except Exception as inner_exc:
                    logger.warning("Failed analyzing event #%d for %s: %s", eid, slug, inner_exc)
                    db.update_event_ai_summary(eid, json.dumps({
                        "intent": f"Burst #{eid} - Analysis pending/failed",
                        "architecture_impact": str(inner_exc),
                        "key_modifications": [],
                        "functionality_gained": "N/A",
                        "summary": [str(inner_exc)],
                    }))
                    analyzed += 1

                with self._batch_jobs_lock:
                    if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                        self._batch_jobs[slug]["analyzed_count"] = analyzed

            db.close()
            with self._batch_jobs_lock:
                if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                    self._batch_jobs[slug]["status"] = "completed"
                    self._batch_jobs[slug]["completed_at"] = _now_ist_iso()
            logger.info("Completed async batch AI analysis for %s (job %s)", slug, job_id)
        except Exception as exc:
            logger.exception("Fatal error in async batch job %s for %s", job_id, slug)
            with self._batch_jobs_lock:
                if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                    self._batch_jobs[slug]["status"] = "failed"
                    self._batch_jobs[slug]["error"] = str(exc)

    # ------------------------------------------------------------------
    # Internal SQLite query helpers
    # ------------------------------------------------------------------

    def _count_events_in_db(self, db_path: Path) -> int:
        """Count prompt burst events (EDIT events) recorded in the database."""
        if not db_path.exists():
            return 0
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT' OR event_type IS NULL OR event_type = ''")
            row = cur.fetchone()
            count = row[0] if row else 0
            conn.close()
            return count
        except Exception:
            return 0

    def _read_sessions_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all sessions from session.db with burst counts and active state."""
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT id, project_name, target_path, start_time, end_time, status FROM sessions ORDER BY id ASC")
            s_rows = cur.fetchall()

            cur.execute("SELECT session_id, COUNT(*) as c FROM events WHERE event_type = 'EDIT' GROUP BY session_id")
            burst_counts = {r["session_id"]: r["c"] for r in cur.fetchall()}
            conn.close()

            sessions = []
            for r in s_rows:
                sid = r["id"]
                is_active = (r["status"] == "ACTIVE")
                b_count = burst_counts.get(sid, 0)
                if b_count == 0 and not is_active:
                    continue
                sessions.append({
                    "id": sid,
                    "session_id": sid,
                    "project_name": r["project_name"],
                    "target_path": r["target_path"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "status": r["status"],
                    "is_active": is_active,
                    "burst_count": b_count,
                    "bursts_count": b_count,
                    "label": f"Session {sid} ({'Active' if is_active else str(b_count) + ' bursts'})",
                })
            return sessions
        except Exception as exc:
            logger.debug("Error reading sessions from %s: %s", db_path, exc)
            return []

    def _read_events_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all events and their linked patches from session.db with clean sequential burst indices."""
        if not db_path.exists():
            return []

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(events)")
        cols = [r["name"] for r in cur.fetchall()]
        has_ai_summary = "ai_summary" in cols
        has_executor = "executor" in cols

        select_cols = ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary"]
        if has_ai_summary:
            select_cols.append("ai_summary")
        if has_executor:
            select_cols.append("executor")
        cur.execute(f"SELECT {', '.join(select_cols)} FROM events ORDER BY id ASC")
        event_rows = cur.fetchall()

        events: list[dict[str, Any]] = []
        burst_by_session: dict[int, int] = {}

        for er in event_rows:
            raw_first_file = er["first_file_touched"] or ""
            raw_summary = er["summary"] or ""

            # 1. Filter out internal IDE EXEC noise rows
            if er["event_type"] == "EXEC":
                if is_ignored_command(raw_first_file) or is_ignored_command(raw_summary):
                    continue

            eid = er["id"]
            sid = er["session_id"]
            first_file_scrubbed = scrub_tokens(raw_first_file) if raw_first_file else None
            summary_scrubbed = scrub_tokens(raw_summary) if raw_summary else None
            executor_val = er["executor"] if has_executor else None

            cur.execute(
                "SELECT id, file_path, diff_content FROM patches WHERE event_id = ? ORDER BY id ASC",
                (eid,),
            )
            patch_rows = cur.fetchall()

            patches = []
            total_adds = 0
            total_dels = 0
            for pr in patch_rows:
                diff_text = pr["diff_content"] or ""
                adds = 0
                dels = 0
                for line in diff_text.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        adds += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        dels += 1
                total_adds += adds
                total_dels += dels
                patches.append({
                    "id": pr["id"],
                    "file_path": pr["file_path"],
                    "diff_content": pr["diff_content"],
                    "additions": adds,
                    "deletions": dels,
                    "lines_added": adds,
                    "lines_deleted": dels,
                })

            # 2. Sequential burst numbering per session
            is_burst = (er["event_type"] == "EDIT") or (len(patches) > 0)
            if is_burst:
                burst_by_session[sid] = burst_by_session.get(sid, 0) + 1
                burst_num = burst_by_session[sid]
                session_burst_label = f"Session {sid} • Burst #{burst_num}"
            else:
                burst_num = None
                session_burst_label = f"Session {sid} • {er['event_type']}"

            ai_summary_parsed = None
            if has_ai_summary and er["ai_summary"]:
                try:
                    ai_summary_parsed = json.loads(er["ai_summary"])
                except Exception:
                    ai_summary_parsed = {
                        "intent": str(er["ai_summary"]),
                        "summary": [],
                        "functionality_gained": "",
                    }

            events.append(
                {
                    "id": eid,
                    "session_id": sid,
                    "burst_num": burst_num,
                    "session_burst_label": session_burst_label,
                    "event_type": er["event_type"],
                    "timestamp": er["timestamp"],
                    "first_file_touched": first_file_scrubbed,
                    "summary": summary_scrubbed,
                    "executor": executor_val,
                    "ai_summary": ai_summary_parsed,
                    "patches_count": len(patches),
                    "patches": patches,
                    "additions": total_adds,
                    "deletions": total_dels,
                }
            )

        conn.close()
        return events



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SPD Analysis Engine Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()

    engine_root = Path(__file__).resolve().parent.parent
    server = EngineWebServer(engine_root, host=args.host, port=args.port)
    actual_port = server.start(background=True)
    print(f"EngineWebServer listening on http://{args.host}:{actual_port}")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.shutdown()

