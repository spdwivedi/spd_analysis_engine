"""
scripts/server/routes_ai_analysis.py
=====================================
AIAnalysisRoutesMixin: heavy analysis handlers extracted from AIRoutesMixin.
Provides batch, file-analysis, burst-overview, and cache query endpoints.
"""

from __future__ import annotations

import logging
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
    from .routes_ai_batch import AIBatchRoutesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.routes_ai_batch import AIBatchRoutesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.server.routes_ai_batch import AIBatchRoutesMixin

logger = logging.getLogger(__name__)


class AIAnalysisRoutesMixin(AIBatchRoutesMixin):
    """
    Mixin providing heavy AI analysis route handlers extracted from AIRoutesMixin.
    Requires self.server_instance, self._resolve_db_path, self._resolve_target_path,
    self._send_json, self._send_error from the base handler class.
    """

    def _resolve_burst_identifiers(
        self, db_path: Any, session_id: Any, burst_id: Any, event_id: Any = None, burst_number: Any = None
    ) -> tuple[Any, Any, Any]:
        resolved_eid = None
        resolved_bnum = None
        resolved_sid = None
        if db_path and db_path.exists() and resolve_burst_event:
            resolved_eid, resolved_bnum, resolved_sid = resolve_burst_event(
                db_path, session_id, burst_id, event_id=event_id, burst_number=burst_number
            )
        sid = resolved_sid or session_id
        bnum = resolved_bnum or burst_number or burst_id or 0
        eid = resolved_eid or event_id or burst_id
        return sid, bnum, eid

    def _handle_api_analyze_file(self, project_name: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer.analyze_file on a specific file diff in a burst."""
        file_path = body.get("file_path")
        if not file_path:
            self._send_error("Missing 'file_path' in request body", status=400)  # type: ignore[attr-defined]
            return

        burst_id = body.get("burst_id", 0)
        event_id = body.get("event_id")
        burst_number = body.get("burst_number") or body.get("burst_num")
        session_id = body.get("session_id")
        mode = str(body.get("mode", "deep"))
        diff_text = body.get("diff_text")
        provider = body.get("provider")
        force_refresh = bool(body.get("force_refresh", False) or body.get("bypass_cache", False))

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        session_id, target_bnum, target_eid = self._resolve_burst_identifiers(
            db_path, session_id, burst_id, event_id=event_id, burst_number=burst_number
        )

        if not diff_text and db_path and db_path.exists():
            try:
                db = SessionDB(db_path)
                conn = db.conn
                if conn is not None:
                    cur = conn.cursor()
                    prow = None
                    if target_eid:
                        try:
                            cur.execute(
                                "SELECT diff_content FROM patches WHERE event_id = ? AND file_path = ?",
                                (int(target_eid), file_path),
                            )
                            prow = cur.fetchone()
                        except Exception:
                            pass
                    if not prow or not prow["diff_content"]:
                        cur.execute(
                            "SELECT diff_content FROM patches WHERE file_path = ? ORDER BY id DESC LIMIT 1",
                            (file_path,),
                        )
                        prow = cur.fetchone()
                    if prow and prow["diff_content"]:
                        diff_text = prow["diff_content"]
                db.close()
            except Exception as db_exc:
                logger.debug("Failed reading patch from DB: %s", db_exc)

        if session_id is None:
            session_id = 1

        if not diff_text:
            diff_text = f"--- a/{file_path}\n+++ b/{file_path}\n@@ -1 +1 @@\n# File {file_path} modified in burst #{target_bnum}"

        try:
            syn = AISynthesizer(engine.engine_root)
            analysis = syn.analyze_file(
                project_name=slug,
                file_path=file_path,
                diff_text=diff_text,
                burst_id=target_bnum,
                session_id=session_id,
                mode=mode,
                provider=provider,
                force_refresh=force_refresh,
                burst_number=target_bnum,
                event_id=target_eid,
            )

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "file_path": file_path,
                "analysis": analysis,
            })
        except Exception as exc:
            logger.exception("Failed to analyze file %s for %s", file_path, slug)
            self._send_error(f"Error during file AI analysis: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_get_file_analysis(self, project_name: str, query: dict[str, list[str]]) -> None:
        """Query AICacheManager for cached file analysis without invoking AI providers."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        session_id_list = query.get("session_id") or ["1"]
        session_id = session_id_list[0] if session_id_list else "1"

        burst_id_list = query.get("burst_id") or query.get("event_id") or ["0"]
        burst_id = burst_id_list[0] if burst_id_list else "0"

        event_id = (query.get("event_id") or [None])[0]
        burst_number = (query.get("burst_number") or query.get("burst_num") or [None])[0]

        file_path_list = query.get("file_path") or [""]
        file_path = file_path_list[0] if file_path_list else ""

        diff_hash_list = query.get("diff_hash") or [""]
        diff_hash = diff_hash_list[0] if diff_hash_list else ""

        if not file_path:
            self._send_error("Missing 'file_path' query parameter", status=400)  # type: ignore[attr-defined]
            return

        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        session_id, target_bnum, target_eid = self._resolve_burst_identifiers(
            db_path, session_id, burst_id, event_id=event_id, burst_number=burst_number
        )

        syn = AISynthesizer(engine.engine_root)
        cached = syn.cache_mgr.find_file_analysis(
            burst_id=target_bnum,
            file_path=file_path,
            session_id=session_id,
            project_name=slug,
            diff_hash=diff_hash or None,
            burst_number=target_bnum,
            event_id=target_eid,
        )

        if cached:
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "file_path": file_path,
                "cached": True,
                "analysis": cached,
            })
        else:
            self._send_json({  # type: ignore[attr-defined]
                "success": False,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "file_path": file_path,
                "cached": False,
                "analysis": None,
                "message": "No cached analysis found",
            })

    def _handle_api_get_burst_overview(self, project_name: str, query: dict[str, list[str]]) -> None:
        """Query AICacheManager for cached burst overview without invoking AI providers."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        session_id_list = query.get("session_id") or ["1"]
        session_id = session_id_list[0] if session_id_list else "1"

        burst_id_list = query.get("burst_id") or query.get("event_id") or ["0"]
        burst_id = burst_id_list[0] if burst_id_list else "0"

        event_id = (query.get("event_id") or [None])[0]
        burst_number = (query.get("burst_number") or query.get("burst_num") or [None])[0]

        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        session_id, target_bnum, target_eid = self._resolve_burst_identifiers(
            db_path, session_id, burst_id, event_id=event_id, burst_number=burst_number
        )

        syn = AISynthesizer(engine.engine_root)
        cached = syn.cache_mgr.find_burst_overview(
            burst_id=target_bnum,
            session_id=session_id,
            project_name=slug,
            burst_number=target_bnum,
            event_id=target_eid,
        )

        if cached:
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "cached": True,
                "file_count": cached.get("file_count", len(cached.get("overview", {}))),
                "overview": cached.get("overview", {}),
                "burst_summary": cached.get("burst_summary", ""),
                "provider": cached.get("provider", "heuristic"),
            })
        else:
            self._send_json({  # type: ignore[attr-defined]
                "success": False,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "cached": False,
                "file_count": 0,
                "overview": {},
                "burst_summary": "",
                "message": "No cached burst overview found",
            })

    def _handle_api_analyze_burst_overview(self, project_name: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer.analyze_burst_overview on all touched files in a burst."""
        burst_id = body.get("burst_id")
        event_id = body.get("event_id")
        burst_number = body.get("burst_number") or body.get("burst_num")
        if not burst_id and event_id is None and burst_number is None:
            self._send_error("Missing 'burst_id' in request body", status=400)  # type: ignore[attr-defined]
            return

        session_id = body.get("session_id")
        provider = body.get("provider")
        force_refresh = bool(body.get("force_refresh", False) or body.get("bypass_cache", False))
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        patches = []
        session_id, target_bnum, target_eid = self._resolve_burst_identifiers(
            db_path, session_id, burst_id, event_id=event_id, burst_number=burst_number
        )

        if db_path and db_path.exists():
            try:
                db = SessionDB(db_path)
                conn = db.conn
                if conn is not None:
                    cur = conn.cursor()
                    cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (int(target_eid),))
                    patches = [dict(r) for r in cur.fetchall()]
                    if not patches:
                        b_index = None
                        try:
                            b_index = int(burst_number if burst_number is not None else burst_id)
                        except (ValueError, TypeError):
                            pass
                        if b_index and b_index > 0:
                            cur.execute(
                                "SELECT id FROM events WHERE session_id = ? AND (event_type = 'EDIT' OR event_type IS NULL) ORDER BY id ASC",
                                (int(session_id or 1),),
                            )
                            edit_events = [int(r["id"]) for r in cur.fetchall()]
                            if 1 <= b_index <= len(edit_events):
                                mapped_eid = edit_events[b_index - 1]
                                cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (mapped_eid,))
                                patches = [dict(r) for r in cur.fetchall()]
                                if patches:
                                    target_eid = mapped_eid
                                    target_bnum = b_index
                db.close()
            except Exception as exc:
                logger.debug("Failed querying patches for burst %s (event #%s): %s", burst_id, target_eid, exc)

        if session_id is None:
            session_id = 1

        try:
            syn = AISynthesizer(engine.engine_root)
            result = syn.analyze_burst_overview(
                project_name=slug,
                burst_id=target_bnum,
                patches=patches,
                session_id=session_id,
                provider=provider,
                force_refresh=force_refresh,
                burst_number=target_bnum,
                event_id=target_eid,
            )
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "session_id": session_id,
                "burst_id": target_bnum,
                "burst_number": target_bnum,
                "event_id": target_eid,
                "file_count": result.get("file_count", len(result.get("overview", {}))),
                "overview": result.get("overview", {}),
                "burst_summary": result.get("burst_summary", ""),
                "provider": result.get("provider", "heuristic"),
                "cached": result.get("cached", False),
            })
        except Exception as exc:
            logger.exception("Failed to generate burst overview for %s (burst #%s, event #%s)", slug, target_bnum, target_eid)
            self._send_error(f"Error during burst overview analysis: {exc}", status=500)  # type: ignore[attr-defined]
