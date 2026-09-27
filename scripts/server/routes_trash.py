"""
scripts/server/routes_trash.py
==============================
Trash management and project deletion routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import gc
import logging
import os
import stat
import time
import urllib.parse
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import _slug
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import _slug

logger = logging.getLogger(__name__)


class TrashRoutesMixin:
    """
    Mixin containing trash bin inspection, restore, purge, and project deletion route handlers.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _handle_api_delete_project(self, project_name: str, body: dict[str, Any]) -> None:
        """Handle granular deletion of project data according to 5 scopes."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        scope = body.get("scope", "local_session")
        burst_ids = body.get("burst_ids")
        if burst_ids and isinstance(burst_ids, list):
            burst_ids = [int(b) for b in burst_ids]
        session_id = body.get("session_id")
        if session_id is not None:
            try:
                session_id = int(session_id)
            except (ValueError, TypeError):
                session_id = None
        delete_remote = bool(body.get("delete_remote", False) or body.get("delete_github_repo", False))

        try:
            engine.supervisor.stop_project(slug)
            time.sleep(0.3)
        except Exception:
            pass

        try:
            result = engine.trash_mgr.delete_project(
                project_name=slug,
                scope=scope,
                burst_ids=burst_ids,
                delete_remote=delete_remote,
                session_id=session_id,
            )
            self._send_json({"success": True, "project": slug, "result": result})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to delete project %s with scope %s", slug, scope)
            self._send_error(f"Deletion failed: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_get_trash(self) -> None:
        """List all items currently in the trash bin."""
        engine = self.server_instance  # type: ignore[attr-defined]
        try:
            items = engine.trash_mgr.list_trash()
            self._send_json({"success": True, "items": items, "count": len(items)})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to list trash")
            self._send_error(f"Failed to list trash: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_restore_trash(self, trash_id: str) -> None:
        """Restore an item from the trash bin."""
        engine = self.server_instance  # type: ignore[attr-defined]
        try:
            res = engine.trash_mgr.restore(trash_id)
            self._send_json({"success": True, "restored": res})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to restore trash item %s", trash_id)
            self._send_error(f"Restore failed: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_purge_trash(self, trash_id: str) -> None:
        """Permanently purge an item from the trash bin with URL unquoting, retry on PermissionError, and remaining count."""
        engine = self.server_instance  # type: ignore[attr-defined]
        clean_id = urllib.parse.unquote(str(trash_id)).strip()
        try:
            try:
                res = engine.trash_mgr.purge(clean_id)
            except PermissionError:
                gc.collect()
                time.sleep(0.2)
                target = engine.trash_mgr.trash_dir / clean_id
                if target.exists():
                    try:
                        for root, dirs, files in os.walk(target):
                            for f in files:
                                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IWUSR)
                            for d in dirs:
                                os.chmod(os.path.join(root, d), stat.S_IWRITE | stat.S_IWUSR)
                    except Exception:
                        pass
                res = engine.trash_mgr.purge(clean_id)

            remaining_items = engine.trash_mgr.list_trash()
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "trash_id": clean_id,
                "remaining_count": len(remaining_items),
                "message": res.get("message", f"Permanently purged '{clean_id}' from disk."),
            })
        except Exception as exc:
            logger.exception("Failed to purge trash item %s", clean_id)
            self._send_error(f"Purge failed: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_purge_all_trash(self) -> None:
        """Empty the entire trash bin, unlocking all files."""
        engine = self.server_instance  # type: ignore[attr-defined]
        try:
            try:
                res = engine.trash_mgr.purge_all()
            except PermissionError:
                gc.collect()
                time.sleep(0.2)
                res = engine.trash_mgr.purge_all()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "purged_count": res.get("purged_count", 0),
                "remaining_count": 0,
                "message": "Trash bin successfully emptied.",
            })
        except Exception as exc:
            logger.exception("Failed to purge all trash")
            self._send_error(f"Purge all failed: {exc}", status=500)  # type: ignore[attr-defined]
