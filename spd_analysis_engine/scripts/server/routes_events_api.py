"""
scripts/server/routes_events_api.py
===================================
Canonical Events API endpoints for the SPD Analysis Engine:
- GET /events?since_id=&limit=&types=&session_id=&project= (summaries only)
- GET /events/<id>?project= (event + touched file list)
- GET /events/<id>/files/<path>/diff?project= (diff content on demand)
"""

from __future__ import annotations

import logging
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from spd_analysis_engine.scripts.storage_rotator import (
    _slug,
    read_project_meta,
    resolve_project_context,
)
from .db_queries import _open_ro_db

try:
    from spd_analysis_engine.scripts.storage.compatibility_reader import (
        read_events_summary,
        read_event_detail,
        read_event_file_diff,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from scripts.storage.compatibility_reader import (
            read_events_summary,
            read_event_detail,
            read_event_file_diff,
        )
    except (ImportError, ModuleNotFoundError):
        from ..storage.compatibility_reader import (
            read_events_summary,
            read_event_detail,
            read_event_file_diff,
        )

logger = logging.getLogger(__name__)


class EventsApiRoutesMixin:
    """Mixin implementing canonical Events API endpoints with bounded summaries and on-demand diffs."""

    def _resolve_events_db(self, project_name: str | None = None) -> tuple[Path | None, str]:
        """Resolve session.db path and slug for specified or active project."""
        engine = getattr(self, "server_instance", None)
        if not engine:
            return None, ""

        if project_name and str(project_name).strip():
            slug = _slug(project_name)
            ctx = resolve_project_context(slug, engine.engine_root, auto_synthesize=False)
            db_path = ctx.get("db_path")
            if db_path and Path(db_path).exists():
                return Path(db_path), slug

        # Fallback 1: Scan current/ directory
        curr_dir = engine.engine_root / "current"
        if curr_dir.exists():
            for p in sorted(curr_dir.iterdir()):
                if p.is_dir():
                    db = p / "session.db"
                    if db.is_file():
                        return db, p.name

        # Fallback 2: Scan data/projects/ directory
        proj_dir = engine.engine_root / "data" / "projects"
        if proj_dir.exists():
            for p in sorted(proj_dir.iterdir()):
                if p.is_dir():
                    db = p / "session.db"
                    if db.is_file():
                        return db, p.name

        # Fallback 3: Scan history/ directory
        hist_dir = engine.engine_root / "history"
        if hist_dir.exists():
            for p in sorted(hist_dir.iterdir(), reverse=True):
                if p.is_dir():
                    db = p / "session.db"
                    if db.is_file():
                        return db, p.name

        return None, ""

    def _handle_api_events_summary(self, query: dict[str, list[str]]) -> None:
        """
        GET /events?since_id=&limit=&types=&session_id=&project=
        Returns summaries only (no full diff bodies).
        """
        proj_param = query.get("project", [""])[0]
        db_path, slug = self._resolve_events_db(proj_param)

        if not db_path or not db_path.exists():
            self._send_json({"events": [], "total": 0, "has_more": False, "project": slug})  # type: ignore[attr-defined]
            return

        since_id = None
        if "since_id" in query and query["since_id"][0].isdigit():
            since_id = int(query["since_id"][0])

        limit = 50
        if "limit" in query and query["limit"][0].isdigit():
            limit = min(max(int(query["limit"][0]), 1), 200)

        session_id = None
        if "session_id" in query and query["session_id"][0].isdigit():
            session_id = int(query["session_id"][0])

        event_types = None
        if "types" in query and query["types"][0].strip():
            event_types = [t.strip().upper() for t in query["types"][0].split(",") if t.strip()]

        conn = None
        try:
            conn = _open_ro_db(db_path)
            summaries, has_more = read_events_summary(
                conn,
                since_id=since_id,
                session_id=session_id,
                event_types=event_types,
                limit=limit,
            )
            self._send_json({  # type: ignore[attr-defined]
                "events": summaries,
                "total": len(summaries),
                "has_more": has_more,
                "project": slug,
            })
        except sqlite3.DatabaseError as exc:
            # Handle empty or malformed database files gracefully as empty dataset
            logger.warning("Database read error for %s (malformed/empty): %s", slug, exc)
            self._send_json({  # type: ignore[attr-defined]
                "events": [],
                "total": 0,
                "has_more": False,
                "project": slug,
            })
        except Exception as exc:
            logger.exception("Error in _handle_api_events_summary for %s", slug)
            self._send_error(f"Failed to query events: {exc}", status=500)  # type: ignore[attr-defined]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_api_event_detail(self, item_id: str, query: dict[str, list[str]]) -> None:
        """
        GET /events/<id>?project=
        Returns event detail + touched file list (no diff bodies).
        """
        proj_param = query.get("project", [""])[0]
        db_path, slug = self._resolve_events_db(proj_param)

        if not db_path or not db_path.exists():
            self._send_error("Session database not found", status=404)  # type: ignore[attr-defined]
            return

        conn = None
        try:
            conn = _open_ro_db(db_path)
            ev_dict, files = read_event_detail(conn, item_id)
            if not ev_dict:
                self._send_error(f"Event '{item_id}' not found", status=404)  # type: ignore[attr-defined]
                return

            self._send_json({  # type: ignore[attr-defined]
                "event": ev_dict,
                "files": files,
                "files_count": len(files),
                "project": slug,
            })
        except sqlite3.DatabaseError as exc:
            logger.warning("Database read error for %s / %s (malformed/empty): %s", slug, item_id, exc)
            self._send_error(f"Event '{item_id}' not found", status=404)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Error in _handle_api_event_detail for %s / %s", slug, item_id)
            self._send_error(f"Failed to fetch event details: {exc}", status=500)  # type: ignore[attr-defined]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_api_event_file_diff(self, item_id: str, file_path: str, query: dict[str, list[str]]) -> None:
        """
        GET /events/<id>/files/<path>/diff?project=
        Returns unified diff content for single file on demand.
        """
        proj_param = query.get("project", [""])[0]
        db_path, slug = self._resolve_events_db(proj_param)

        if not db_path or not db_path.exists():
            self._send_error("Session database not found", status=404)  # type: ignore[attr-defined]
            return

        conn = None
        try:
            conn = _open_ro_db(db_path)
            diff_res = read_event_file_diff(conn, item_id, file_path)
            if not diff_res:
                self._send_error(f"Diff not found for file '{file_path}' in event '{item_id}'", status=404)  # type: ignore[attr-defined]
                return

            diff_res["project"] = slug
            self._send_json(diff_res)  # type: ignore[attr-defined]
        except sqlite3.DatabaseError as exc:
            logger.warning("Database read error for %s / %s diff (malformed/empty): %s", slug, item_id, exc)
            self._send_error(f"Diff not found for file '{file_path}' in event '{item_id}'", status=404)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Error in _handle_api_event_file_diff for %s / %s", slug, file_path)
            self._send_error(f"Failed to fetch file diff: {exc}", status=500)  # type: ignore[attr-defined]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_api_internal_live_action(self, body: dict[str, Any] | None = None) -> None:
        """
        POST /api/internal/live-action
        Internal loopback ingestion route for worker real-time live activity stream.
        """
        client_address = getattr(self, "client_address", None)
        client_ip = client_address[0] if client_address else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
            self._send_error("Forbidden: loopback access only", status=403)  # type: ignore[attr-defined]
            return

        data = body or {}
        event_type = str(data.get("event_type") or data.get("action_type") or "UPDATE")
        path = str(data.get("path") or "")
        project = str(data.get("project") or "")
        command = str(data.get("command") or "")
        details = str(data.get("details") or "")

        engine = getattr(self, "server_instance", None)
        sse_mgr = getattr(engine, "sse_manager", None)
        if not sse_mgr:
            try:
                from .sse_stream import get_sse_manager
                sse_mgr = get_sse_manager()
            except Exception:
                sse_mgr = None

        if sse_mgr:
            sse_mgr.broadcast_live_action(
                project=project,
                action_type=event_type,
                path=path,
                command=command,
                details=details,
            )

        self._send_json({"status": "ok"})  # type: ignore[attr-defined]

