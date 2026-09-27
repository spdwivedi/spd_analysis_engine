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

try:
    from .request_handler import _EngineRequestHandler
    from .db_queries import _count_events_in_db, _read_sessions_from_db, _read_events_from_db
    from .batch_runner import run_async_batch
    from ..orchestrator import Supervisor
    from ..storage_rotator import StorageRotator
    from ..trash_manager import TrashManager
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.request_handler import _EngineRequestHandler
        from spd_analysis_engine.scripts.server.db_queries import _count_events_in_db, _read_sessions_from_db, _read_events_from_db
        from spd_analysis_engine.scripts.server.batch_runner import run_async_batch
        from spd_analysis_engine.scripts.orchestrator import Supervisor
        from spd_analysis_engine.scripts.storage_rotator import StorageRotator
        from spd_analysis_engine.scripts.trash_manager import TrashManager
    except (ImportError, ModuleNotFoundError):
        from scripts.server.request_handler import _EngineRequestHandler
        from scripts.server.db_queries import _count_events_in_db, _read_sessions_from_db, _read_events_from_db
        from scripts.server.batch_runner import run_async_batch
        from scripts.orchestrator import Supervisor
        from scripts.storage_rotator import StorageRotator
        from scripts.trash_manager import TrashManager

logger = logging.getLogger(__name__)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that silences socket disconnects and aborts on Windows."""

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
        run_async_batch(self, slug, job_id, event_ids, db_path, target_path)

    def _count_events_in_db(self, db_path: Path) -> int:
        """Count prompt burst events (EDIT events) recorded in the database."""
        return _count_events_in_db(db_path)

    def _read_sessions_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all sessions from session.db with burst counts and active state."""
        return _read_sessions_from_db(db_path)

    def _read_events_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all events and their linked patches from session.db with clean sequential burst indices."""
        return _read_events_from_db(db_path)
