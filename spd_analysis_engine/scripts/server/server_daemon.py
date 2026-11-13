"""
scripts/server/server_daemon.py
===============================
HTTP server daemon lifecycle and socket management for the SPD Analysis Engine.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .request_handler import _EngineRequestHandler
from .db_queries import _count_events_in_db, _read_sessions_from_db, _read_events_from_db
from .batch_runner import run_async_batch
from .security import generate_api_token
from ..orchestrator import Supervisor
from ..storage_rotator import StorageRotator
from ..trash_manager import TrashManager

try:
    from spd_analysis_engine.scripts.ai.controller_db import EngineDB
    from spd_analysis_engine.scripts.ai.controller_executor import ControllerAIExecutor
except (ImportError, ModuleNotFoundError):
    from ..ai.controller_db import EngineDB
    from ..ai.controller_executor import ControllerAIExecutor

logger = logging.getLogger(__name__)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that silences socket disconnects and runs request threads as daemons."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
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
        supervisor: Supervisor | None = None,
        api_token: str | None = None,
        require_token: bool = True,
    ) -> None:
        self.engine_root = Path(engine_root).resolve()
        self.host = host
        self.port = port
        self.web_dir = Path(web_dir).resolve() if web_dir else (self.engine_root / "web")
        self.api_token = api_token if api_token is not None else generate_api_token()
        self.require_token = require_token

        if supervisor is not None:
            self.supervisor = supervisor
        else:
            # Must not silently construct an independent Supervisor or lifecycle thread.
            # Obtain the authoritative Supervisor for this engine_root without starting background reaper.
            self.supervisor = Supervisor.get_authoritative(self.engine_root, enable_reaper=False)

        self.rotator = StorageRotator(self.engine_root)
        self.trash_mgr = TrashManager(self.engine_root)
        self._batch_jobs: dict[str, dict[str, Any]] = {}
        self._batch_jobs_lock = threading.Lock()

        self._controller: Any = None
        self._ai_db: EngineDB | None = None
        self._ai_executor: ControllerAIExecutor | None = None

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

    @property
    def controller(self) -> Any:
        """Authoritative LifecycleController instance."""
        if self._controller is None:
            ctrl = getattr(self.supervisor, "controller", None)
            if ctrl is None:
                from spd_analysis_engine.scripts.controller.controller import LifecycleController
                ctrl = LifecycleController(self.engine_root)
            self._controller = ctrl
        return self._controller

    @property
    def ai_db(self) -> EngineDB:
        """Lazily initialize EngineDB on first access."""
        if self._ai_db is None:
            db_path = getattr(self.controller, "db_path", self.engine_root / "data" / "engine.db")
            self._ai_db = EngineDB(db_path)
        return self._ai_db

    @property
    def ai_executor(self) -> ControllerAIExecutor:
        """Lazily initialize ControllerAIExecutor on first access."""
        if self._ai_executor is None:
            self._ai_executor = ControllerAIExecutor(engine_root=self.engine_root, db=self.ai_db)
        return self._ai_executor


    @classmethod
    def broadcast_sse(cls, event: str, data: dict[str, Any]) -> None:
        """Broadcast an SSE event frame via the authoritative SSEManager."""
        _EngineRequestHandler.broadcast_sse(event, data)

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
                self._server.daemon_threads = True
                self._server.engine_web_server = self  # type: ignore[attr-defined]
                self.port = self._server.server_address[1]
                break
            except OSError as exc:
                logger.warning("Port %d busy: %s; trying %d...", current_port, exc, current_port + 1)
                current_port += 1
                attempts -= 1

        if self._server is None:
            raise RuntimeError(f"Could not bind to any port near {self.port}")

        try:
            (self.engine_root / ".active_token").write_text(self.api_token, encoding="utf-8")
        except Exception:
            pass

        logger.info("EngineWebServer listening on http://%s:%d (web_dir=%s)", self.host, self.port, self.web_dir)

        if hasattr(self, "ai_executor") and self.ai_executor is not None:
            self.ai_executor.start()

        if background:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        else:
            self._server.serve_forever()

        return self.port

    def shutdown(self) -> None:
        """Gracefully stop the HTTP server and all background connections."""
        self._shutdown_event.set()
        try:
            (self.engine_root / ".active_token").unlink(missing_ok=True)
        except Exception:
            pass
        if hasattr(self, "ai_executor") and self.ai_executor is not None:
            try:
                self.ai_executor.stop()
            except Exception as exc:
                logger.warning("Error stopping AI executor: %s", exc)
        if self._server is not None:
            logger.info("Stopping EngineWebServer...")
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
        if hasattr(self, "supervisor") and self.supervisor is not None:
            try:
                self.supervisor.shutdown()
            except Exception as exc:
                logger.warning("Error during supervisor shutdown: %s", exc)
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
        run_async_batch(self, slug, job_id, event_ids, db_path, target_path)

    def _count_events_in_db(self, db_path: Path) -> int:
        """Count prompt burst events (EDIT events) recorded in the database."""
        return _count_events_in_db(db_path)

    def _read_sessions_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all sessions from session.db with burst counts and active state."""
        return _read_sessions_from_db(db_path)

    def _read_events_from_db(self, db_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
        """Read all events and their linked patches from session.db with clean sequential burst indices."""
        return _read_events_from_db(db_path, limit=limit)
