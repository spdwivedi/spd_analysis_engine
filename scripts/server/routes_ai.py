"""
scripts/server/routes_ai.py
===========================
AI configuration and analysis routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB, _slug
    from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import SessionDB, _slug
    from scripts.ai_analyzer import AISynthesizer

try:
    from .db_queries import resolve_burst_event
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.db_queries import resolve_burst_event
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.server.db_queries import resolve_burst_event
        except (ImportError, ModuleNotFoundError):
            resolve_burst_event = None

try:
    from .routes_ai_analysis import AIAnalysisRoutesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.routes_ai_analysis import AIAnalysisRoutesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.server.routes_ai_analysis import AIAnalysisRoutesMixin


logger = logging.getLogger(__name__)


def _now_ist_iso() -> str:
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S+05:30")


class AIRoutesMixin(AIAnalysisRoutesMixin):
    """
    Mixin containing AI configuration, event analysis, command explanation,
    and batch synthesis route handlers.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _handle_api_get_ai_config(self) -> None:
        """Return stored AI configuration settings with secrets masked."""
        engine = self.server_instance  # type: ignore[attr-defined]
        cfg = AISynthesizer.load_config(engine_root=engine.engine_root)
        masked_cfg = AISynthesizer.mask_config(cfg)
        self._send_json({"success": True, "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_save_ai_config(self, body: dict[str, Any]) -> None:
        """Save updated AI configuration settings."""
        engine = self.server_instance  # type: ignore[attr-defined]
        current_cfg = AISynthesizer.load_config(engine_root=engine.engine_root)
        new_cfg = dict(current_cfg)

        if "preferred_provider" in body:
            new_cfg["preferred_provider"] = str(body["preferred_provider"]).strip() or "heuristic"
        if "ollama_model" in body:
            new_cfg["ollama_model"] = str(body["ollama_model"]).strip() or "qwen2.5-coder:7b"
        if "gemini_model" in body:
            new_cfg["gemini_model"] = str(body["gemini_model"]).strip() or "gemini-3.1-flash-lite"
        if "ollama_base_url" in body:
            new_cfg["ollama_base_url"] = str(body["ollama_base_url"]).strip() or "http://127.0.0.1:11434"

        if "select_key_id" in body:
            new_cfg["select_key_id"] = str(body["select_key_id"]).strip()
        if "delete_key_id" in body:
            new_cfg["delete_key_id"] = str(body["delete_key_id"]).strip()
        if "key_label" in body:
            new_cfg["key_label"] = str(body["key_label"]).strip()

        raw_key = str(body.get("gemini_api_key", "")).strip()
        if raw_key and "****" not in raw_key:
            new_cfg["gemini_api_key"] = raw_key
            new_cfg.setdefault("gemini", {})["api_key"] = raw_key
        elif raw_key == "" and "gemini_api_key" in body and not body.get("select_key_id"):
            new_cfg["gemini_api_key"] = ""
            new_cfg.setdefault("gemini", {})["api_key"] = ""

        saved = AISynthesizer.save_config(new_cfg, engine_root=engine.engine_root)
        masked_cfg = AISynthesizer.mask_config(saved)
        self._send_json({"success": True, "message": "AI settings saved successfully.", "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_test_ai_connection(self, body: dict[str, Any]) -> None:
        """Test connection to an AI provider (Ollama, Gemini, Heuristic)."""
        engine = self.server_instance  # type: ignore[attr-defined]
        provider = body.get("provider", "ollama")

        if provider == "ollama":
            base_url = body.get("base_url") or None
            model = body.get("model") or None
            res = AISynthesizer.test_ollama_connection(
                base_url=base_url,
                model=model,
                engine_root=engine.engine_root,
            )
            self._send_json(res)  # type: ignore[attr-defined]
        elif provider == "gemini":
            api_key = body.get("api_key") or None
            if api_key and "****" in api_key:
                api_key = None
            key_id = body.get("key_id") or None
            model = body.get("model") or None
            res = AISynthesizer.test_gemini_connection(
                api_key=api_key,
                key_id=key_id,
                model=model,
                engine_root=engine.engine_root,
            )
            self._send_json(res)  # type: ignore[attr-defined]
        elif provider in ("heuristic", "none"):
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "provider": "heuristic",
                "message": "Zero-AI Heuristic Mode is ready (offline rule-based, zero network or GPU load).",
            })
        else:
            self._send_error(f"Unknown AI provider '{provider}'.", status=400)  # type: ignore[attr-defined]

    def _handle_api_analyze_event(self, project_name: str, event_id_str: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer on an event and store the result in session.db."""
        try:
            event_id = int(event_id_str)
        except ValueError:
            self._send_error(f"Invalid event ID: {event_id_str}", status=400)  # type: ignore[attr-defined]
            return

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        # Search current, last_run, history
        db_path = engine.engine_root / "current" / slug / "session.db"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"

        if not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            erow = cur.fetchone()
            if not erow:
                db.close()
                self._send_error(f"Event #{event_id} not found in database", status=404)  # type: ignore[attr-defined]
                return

            event_dict = dict(erow)
            cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (event_id,))
            prows = cur.fetchall()
            patches = [dict(p) for p in prows]

            syn = AISynthesizer(engine.engine_root)
            analysis = syn.analyze_event(event_dict, patches)

            # Persist to database
            db.update_event_ai_summary(event_id, json.dumps(analysis))
            db.close()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "event_id": event_id,
                "ai_summary": analysis,
            })
        except Exception as exc:
            logger.exception("Failed to analyze event #%s for %s", event_id, slug)
            self._send_error(f"Error during AI analysis: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_analyze_exec(self, project_name: str, event_id_str: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer.explain_command on an EXEC event and store the result in session.db."""
        try:
            event_id = int(event_id_str)
        except ValueError:
            self._send_error(f"Invalid event ID: {event_id_str}", status=400)  # type: ignore[attr-defined]
            return

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]

        if not db_path or not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            erow = cur.fetchone()
            if not erow:
                db.close()
                self._send_error(f"Event #{event_id} not found in database", status=404)  # type: ignore[attr-defined]
                return

            event_dict = dict(erow)
            command_str = event_dict.get("first_file_touched") or event_dict.get("summary") or ""

            # Extract exit_code and duration_s from body or parse from summary string
            exit_code = body.get("exit_code")
            duration_s = body.get("duration_s")
            summary_str = event_dict.get("summary") or ""

            if exit_code is None:
                m_exit = re.search(r"exit=(\d+)", summary_str)
                exit_code = int(m_exit.group(1)) if m_exit else 0
            if duration_s is None:
                m_dur = re.search(r"duration=([\d\.]+)s", summary_str)
                duration_s = float(m_dur.group(1)) if m_dur else 0.0

            # Gather surrounding events for context
            cur.execute(
                "SELECT id, event_type, first_file_touched, summary FROM events "
                "WHERE session_id = ? AND id != ? AND id BETWEEN ? AND ? ORDER BY id ASC",
                (event_dict.get("session_id", 1), event_id, max(1, event_id - 3), event_id + 3),
            )
            surrounding = [dict(r) for r in cur.fetchall()]

            syn = AISynthesizer(engine.engine_root)
            explanation = syn.explain_command(
                project_name=slug,
                command_str=command_str,
                exit_code=int(exit_code),
                duration_s=float(duration_s),
                surrounding_burst_context=surrounding,
                provider=body.get("provider"),
            )

            # Persist to database
            db.update_event_ai_summary(event_id, json.dumps(explanation))
            db.close()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "event_id": event_id,
                "ai_summary": explanation,
            })
        except Exception as exc:
            logger.exception("Failed to explain EXEC event #%s for %s", event_id, slug)
            self._send_error(f"Error during command explanation: {exc}", status=500)  # type: ignore[attr-defined]

