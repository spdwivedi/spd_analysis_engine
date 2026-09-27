"""
scripts/server/routes_events_export.py
======================================
EventExportRoutesMixin: session event bundle export, logs, shadow git,
and execution recording handlers.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB, _slug
    from spd_analysis_engine.scripts.git_shadow import ShadowGit
    from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import SessionDB, _slug
    from scripts.git_shadow import ShadowGit
    from scripts.shell_interceptor import ExecutionTracker

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class EventExportRoutesMixin:
    """
    Mixin providing session event bundle exports, logs, and shadow git inspection.
    Requires self.server_instance, self._send_json, self._send_error from the base handler class.
    """

    def _handle_api_logs(self, project_name: str) -> None:
        """Read the last 150 lines from worker.log."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        log_path = engine.engine_root / "current" / slug / "worker.log"
        if not log_path.exists():
            log_path = engine.engine_root / "last_run" / slug / "worker.log"

        if not log_path.exists():
            self._send_json({"project": slug, "lines": [], "total_lines": 0})  # type: ignore[attr-defined]
            return

        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            all_lines = content.splitlines()
            recent_lines = all_lines[-150:] if len(all_lines) > 150 else all_lines
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "lines": recent_lines,
                "total_lines": len(all_lines),
            })
        except Exception as exc:
            self._send_error(f"Error reading log file: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_export(self, project_name: str) -> None:
        """Generate an exportable JSON payload of all session events and patches."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"

        if not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            events = engine._read_events_from_db(db_path)
            export_payload = {
                "engine": "SPD Analysis Engine",
                "version": "1.0.0",
                "project": slug,
                "tier": tier,
                "exported_at": _utcnow_iso(),
                "total_events": len(events),
                "events": events,
            }
            self._send_json(export_payload)  # type: ignore[attr-defined]
        except Exception as exc:
            self._send_error(f"Failed to export session: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_events_export(self, project_name: str) -> None:
        """Alias for _handle_api_export for event bundle export."""
        self._handle_api_export(project_name)

    def _handle_api_shadow_git(self, project_name: str) -> None:
        """Return micro-commit history from the project's shadow git repo."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        shadow_dir = engine.engine_root / "current" / slug / "shadow_git"
        if not shadow_dir.exists():
            shadow_dir = engine.engine_root / "last_run" / slug / "shadow_git"

        if not shadow_dir.exists():
            self._send_json({"project": slug, "commits": [], "has_shadow_git": False})  # type: ignore[attr-defined]
            return

        try:
            sg = ShadowGit(shadow_dir=shadow_dir, target_dir=engine.engine_root)
            commits = sg.get_commit_history(max_count=50)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "commits": commits,
                "has_shadow_git": True,
                "total_commits": len(commits),
            })
        except Exception as exc:
            logger.exception("Failed to get shadow git history for %s", slug)
            self._send_error(f"Error querying shadow git: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_record_exec(self, project_name: str, body: dict[str, Any]) -> None:
        """Record an execution event in session.db."""
        command = str(body.get("command", "")).strip()
        if not command:
            self._send_error("Parameter 'command' is required.", status=400)  # type: ignore[attr-defined]
            return

        duration_s = float(body.get("duration_s", 0.0))
        exit_code = int(body.get("exit_code", 0))

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = engine.engine_root / "current" / slug / "session.db"
        if not db_path.exists():
            self._send_error(f"Active session database not found for '{slug}'.", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM sessions WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            session_id = row[0] if row else 1
            tracker = ExecutionTracker(target_dir=engine.engine_root, db=db, session_id=session_id)
            event_id = tracker.record_execution(command, duration_s=duration_s, exit_code=exit_code)
            db.close()
            self._send_json({"status": "recorded", "event_id": event_id})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to record exec event for %s", slug)
            self._send_error(f"Error recording exec event: {exc}", status=500)  # type: ignore[attr-defined]


__all__ = ["EventExportRoutesMixin"]
