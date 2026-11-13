"""
scripts/server/routes_ai_jobs.py
================================
AI Jobs and progressive analysis route handlers for the controller API.
Enables workers and frontend clients to enqueue, inspect, and retry analysis jobs.
"""

from __future__ import annotations

import logging
from typing import Any

from spd_analysis_engine.scripts.ai.identity import ProgressiveLevel

logger = logging.getLogger(__name__)


class AIJobsRoutesMixin:
    """
    Mixin containing controller-owned AI job execution and progressive analysis endpoints.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _handle_api_ai_job_enqueue(self, body: dict[str, Any]) -> None:
        """Enqueue an AI analysis job in the controller's durable engine.db queue."""
        engine = self.server_instance  # type: ignore[attr-defined]
        project_slug = body.get("project_slug") or body.get("project", "default")
        target_kind = body.get("target_kind", "burst")
        target_id = str(body.get("target_id", "0"))
        diff_text = body.get("diff_text", "")
        level = body.get("level", ProgressiveLevel.L1)
        profile = body.get("profile", "default")
        context = body.get("context", {})
        provider = body.get("provider")
        model = body.get("model")

        if not hasattr(engine, "ai_executor") or engine.ai_executor is None:
            self._send_error("Controller AI executor not initialized", status=503)  # type: ignore[attr-defined]
            return

        job_id = engine.ai_executor.enqueue_analysis(
            project_slug=project_slug,
            target_kind=target_kind,
            target_id=target_id,
            diff_text=diff_text,
            level=level,
            profile=profile,
            context=context,
            provider=provider,
            model=model,
        )

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "project_slug": project_slug,
            "level": level,
        })

    def _handle_api_ai_job_get(self, job_id: str) -> None:
        """Fetch status and details of an enqueued or completed AI job."""
        engine = self.server_instance  # type: ignore[attr-defined]
        if not hasattr(engine, "ai_db") or engine.ai_db is None:
            self._send_error("Controller database not available", status=503)  # type: ignore[attr-defined]
            return

        job = engine.ai_db.get_job(job_id)
        if not job:
            self._send_error(f"AI job '{job_id}' not found", status=404)  # type: ignore[attr-defined]
            return

        resp: dict[str, Any] = {"success": True, "job": job}
        if job.get("analysis_id"):
            ana = engine.ai_db.get_analysis(job["analysis_id"])
            if ana:
                resp["analysis"] = ana

        self._send_json(resp)  # type: ignore[attr-defined]

    def _handle_api_ai_job_retry(self, job_id: str) -> None:
        """Retry action: reset a failed or superseded job back to 'queued'."""
        engine = self.server_instance  # type: ignore[attr-defined]
        if not hasattr(engine, "ai_db") or engine.ai_db is None:
            self._send_error("Controller database not available", status=503)  # type: ignore[attr-defined]
            return

        job = engine.ai_db.get_job(job_id)
        if not job:
            self._send_error(f"AI job '{job_id}' not found", status=404)  # type: ignore[attr-defined]
            return

        ok = engine.ai_db.retry_job(job_id)
        if not ok:
            self._send_error(f"Failed to retry AI job '{job_id}'", status=500)  # type: ignore[attr-defined]
            return

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "message": f"Job '{job_id}' has been re-queued for execution.",
        })

    def _handle_api_ai_analysis_get(self, analysis_id: str) -> None:
        """Fetch a specific durable AI analysis by ID."""
        engine = self.server_instance  # type: ignore[attr-defined]
        if not hasattr(engine, "ai_db") or engine.ai_db is None:
            self._send_error("Controller database not available", status=503)  # type: ignore[attr-defined]
            return

        analysis = engine.ai_db.get_analysis(analysis_id)
        if not analysis:
            self._send_error(f"AI analysis '{analysis_id}' not found", status=404)  # type: ignore[attr-defined]
            return

        self._send_json({"success": True, "analysis": analysis})  # type: ignore[attr-defined]

    def _handle_api_ai_target_analyses_get(self, kind: str, target_id: str) -> None:
        """Fetch all progressive analyses stored for a specific target."""
        engine = self.server_instance  # type: ignore[attr-defined]
        if not hasattr(engine, "ai_db") or engine.ai_db is None:
            self._send_error("Controller database not available", status=503)  # type: ignore[attr-defined]
            return

        analyses = engine.ai_db.get_analyses_for_target(kind, target_id)
        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "target_kind": kind,
            "target_id": target_id,
            "count": len(analyses),
            "analyses": analyses,
        })
