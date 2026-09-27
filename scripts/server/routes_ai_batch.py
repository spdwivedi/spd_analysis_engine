"""
scripts/server/routes_ai_batch.py
==================================
AIBatchRoutesMixin: batch AI analysis dispatch and status handlers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB, _slug
    from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import SessionDB, _slug
    from scripts.ai_analyzer import AISynthesizer

logger = logging.getLogger(__name__)


def _now_ist_iso() -> str:
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S+05:30")


class AIBatchRoutesMixin:
    """
    Mixin providing asynchronous batch AI analysis route handlers.
    Requires self.server_instance, self._resolve_db_path, self._resolve_target_path,
    self._send_json, self._send_error from the base handler class.
    """

    def _handle_api_analyze_batch(self, project_name: str, body: dict[str, Any]) -> None:
        """Asynchronously batch analyze un-analyzed events in a project session."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        if not db_path or not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        with engine._batch_jobs_lock:
            existing_job = engine._batch_jobs.get(slug)
            if existing_job and existing_job.get("status") == "running":
                self._send_json({  # type: ignore[attr-defined]
                    "success": True,
                    "job_id": existing_job.get("job_id"),
                    "project": slug,
                    "total_events": existing_job.get("total_events", 0),
                    "analyzed_count": existing_job.get("analyzed_count", 0),
                    "current_index": existing_job.get("current_index", 0),
                    "current_event_id": existing_job.get("current_event_id"),
                    "status": "running",
                    "message": "Batch analysis already running in background.",
                })
                return

        force_all = bool(body.get("force_all", False))
        requested_ids = body.get("event_ids")

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()

            # Ensure ai_summary column exists
            cur.execute("PRAGMA table_info(events)")
            cols = [r["name"] for r in cur.fetchall()]
            if "ai_summary" not in cols:
                cur.execute("ALTER TABLE events ADD COLUMN ai_summary TEXT")
                conn.commit()

            if requested_ids and isinstance(requested_ids, list):
                placeholders = ",".join("?" for _ in requested_ids)
                cur.execute(f"SELECT id FROM events WHERE id IN ({placeholders}) ORDER BY id ASC", [int(x) for x in requested_ids])
            elif force_all:
                cur.execute("SELECT id FROM events ORDER BY id ASC")
            else:
                cur.execute("SELECT id FROM events WHERE ai_summary IS NULL OR ai_summary = '' ORDER BY id ASC")
            unresolved = cur.fetchall()
            event_ids = [int(r["id"]) for r in unresolved]
            db.close()

            job_id = f"batch_{slug}_{int(time.time())}"
            if not event_ids:
                job_info = {
                    "job_id": job_id,
                    "project": slug,
                    "status": "completed",
                    "total_events": 0,
                    "analyzed_count": 0,
                    "current_index": 0,
                    "current_event_id": None,
                    "started_at": _now_ist_iso(),
                    "completed_at": _now_ist_iso(),
                    "error": None,
                }
                with engine._batch_jobs_lock:
                    engine._batch_jobs[slug] = job_info
                self._send_json({  # type: ignore[attr-defined]
                    "success": True,
                    "job_id": job_id,
                    "project": slug,
                    "total_events": 0,
                    "analyzed_count": 0,
                    "status": "completed",
                    "message": "All events have already been analyzed.",
                })
                return

            job_info = {
                "job_id": job_id,
                "project": slug,
                "status": "running",
                "total_events": len(event_ids),
                "analyzed_count": 0,
                "current_index": 0,
                "current_event_id": event_ids[0],
                "started_at": _now_ist_iso(),
                "completed_at": None,
                "error": None,
            }
            with engine._batch_jobs_lock:
                engine._batch_jobs[slug] = job_info

            target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
            t = threading.Thread(
                target=engine._run_async_batch,
                args=(slug, job_id, event_ids, db_path, target_path),
                daemon=True,
            )
            t.start()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "job_id": job_id,
                "project": slug,
                "total_events": len(event_ids),
                "status": "running",
                "message": f"Started async analysis for {len(event_ids)} bursts in the background.",
            })
        except Exception as exc:
            logger.exception("Failed to start async batch analysis for %s", slug)
            self._send_error(f"Error starting batch AI analysis: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_analyze_batch_status(self, project_name: str) -> None:
        """Return the current progress and status of async batch analysis."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        with engine._batch_jobs_lock:
            job = engine._batch_jobs.get(slug)
            rate_status = AISynthesizer.rate_limiter.get_status()
            if not job:
                self._send_json({  # type: ignore[attr-defined]
                    "project": slug,
                    "status": "idle",
                    "analyzed_count": 0,
                    "total_events": 0,
                    "cooling_down": rate_status.get("cooling_down", False),
                    "cooldown_remaining_s": rate_status.get("cooldown_remaining_s", 0.0),
                    "message": "No active batch analysis job.",
                })
                return
            res_dict = {
                "project": slug,
                **job,
            }
            if rate_status.get("cooling_down"):
                res_dict["cooling_down"] = True
                res_dict["cooldown_remaining_s"] = rate_status.get("cooldown_remaining_s", 0.0)
            elif "cooling_down" not in res_dict:
                res_dict["cooling_down"] = False
                res_dict["cooldown_remaining_s"] = 0.0
            self._send_json(res_dict)  # type: ignore[attr-defined]


__all__ = ["AIBatchRoutesMixin"]
