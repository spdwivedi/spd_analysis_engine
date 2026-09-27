"""
scripts/server/batch_runner.py
==============================
Asynchronous batch AI synthesis job coordinator for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ..ai_analyzer import AISynthesizer
    from ..storage_rotator import SessionDB
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
        from spd_analysis_engine.scripts.storage_rotator import SessionDB
    except (ImportError, ModuleNotFoundError):
        from scripts.ai_analyzer import AISynthesizer
        from scripts.storage_rotator import SessionDB

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def run_async_batch(
    server_instance: Any,
    slug: str,
    job_id: str,
    event_ids: list[int],
    db_path: Path,
    target_path: Path | None,
) -> None:
    """Background worker executing batch AI synthesis sequentially with deep context."""
    logger.info("Starting async batch AI analysis for %s (job %s, %d events)", slug, job_id, len(event_ids))
    try:
        syn = AISynthesizer(server_instance.engine_root)
        db = SessionDB(db_path)
        conn = db.conn
        assert conn is not None
        cur = conn.cursor()

        previous_intent = None
        analyzed = 0

        for idx, eid in enumerate(event_ids, 1):
            # Check if job was superseded
            with server_instance._batch_jobs_lock:
                curr_job = server_instance._batch_jobs.get(slug, {})
                if curr_job.get("job_id") != job_id:
                    logger.info("Batch job %s for %s was superseded. Halting.", job_id, slug)
                    db.close()
                    return
                server_instance._batch_jobs[slug]["current_event_id"] = eid
                server_instance._batch_jobs[slug]["current_index"] = idx

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
                    with server_instance._batch_jobs_lock:
                        if server_instance._batch_jobs.get(slug, {}).get("job_id") == job_id:
                            server_instance._batch_jobs[slug]["cooling_down"] = True
                            server_instance._batch_jobs[slug]["cooldown_remaining_s"] = round(remaining, 1)

                syn.rate_limiter.wait_if_needed(progress_callback=_on_cooldown)

                with server_instance._batch_jobs_lock:
                    if server_instance._batch_jobs.get(slug, {}).get("job_id") == job_id:
                        server_instance._batch_jobs[slug]["cooling_down"] = False
                        server_instance._batch_jobs[slug]["cooldown_remaining_s"] = 0.0

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

            with server_instance._batch_jobs_lock:
                if server_instance._batch_jobs.get(slug, {}).get("job_id") == job_id:
                    server_instance._batch_jobs[slug]["analyzed_count"] = analyzed

        db.close()
        with server_instance._batch_jobs_lock:
            if server_instance._batch_jobs.get(slug, {}).get("job_id") == job_id:
                server_instance._batch_jobs[slug]["status"] = "completed"
                server_instance._batch_jobs[slug]["completed_at"] = _now_ist_iso()
        logger.info("Completed async batch AI analysis for %s (job %s)", slug, job_id)
    except Exception as exc:
        logger.exception("Fatal error in async batch job %s for %s", job_id, slug)
        with server_instance._batch_jobs_lock:
            if server_instance._batch_jobs.get(slug, {}).get("job_id") == job_id:
                server_instance._batch_jobs[slug]["status"] = "failed"
                server_instance._batch_jobs[slug]["error"] = str(exc)
