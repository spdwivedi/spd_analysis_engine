"""
scripts/server/routes_workspace.py
===================================
WorkspaceRoutesMixin: workspace endpoints for .gitignore reading and updating.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..storage.gitignore_manager import read_project_gitignore, write_project_gitignore
from ..storage.utils import _slug
from ..storage.meta import read_project_meta

logger = logging.getLogger(__name__)


class WorkspaceRoutesMixin:
    """
    Mixin providing workspace configuration routes.
    Expects self.server_instance, self._resolve_target_path, self._send_json, self._send_error.
    """

    def _resolve_workspace_target(self, project_name: str) -> Path | None:
        """Resolve workspace target directory from active worker, project.meta, or candidates."""
        slug = _slug(project_name)
        if hasattr(self, "_resolve_target_path"):
            resolved = self._resolve_target_path(slug)  # type: ignore[attr-defined]
            if resolved:
                return Path(resolved)

        engine = self.server_instance  # type: ignore[attr-defined]
        meta = read_project_meta(engine.engine_root, slug)
        if meta.get("target_path"):
            target_p = Path(meta["target_path"])
            if target_p.exists():
                return target_p

        # Fallback to current project dir
        cand = engine.engine_root / "current" / slug
        if cand.exists():
            return cand
        return None

    def _handle_api_get_gitignore(self, project_name: str) -> None:
        """GET /api/project/<slug>/gitignore - read current or default .gitignore rules."""
        slug = _slug(project_name)
        target = self._resolve_workspace_target(slug)
        if not target or not target.exists():
            self._send_error(f"Target workspace for project '{slug}' not found", status=404)  # type: ignore[attr-defined]
            return

        content = read_project_gitignore(target)
        self._send_json({"success": True, "project": slug, "content": content})  # type: ignore[attr-defined]

    def _handle_api_put_gitignore(self, project_name: str, body: dict[str, Any] | None = None) -> None:
        """PUT /api/project/<slug>/gitignore - update .gitignore with silent watchdog bypass."""
        slug = _slug(project_name)
        target = self._resolve_workspace_target(slug)
        if not target or not target.exists():
            self._send_error(f"Target workspace for project '{slug}' not found", status=404)  # type: ignore[attr-defined]
            return

        body_dict = body if isinstance(body, dict) else {}
        content = body_dict.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        success = write_project_gitignore(target, content, project_slug=slug)
        if success:
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "message": "Successfully updated .gitignore rules with silent watcher bypass.",
            })
        else:
            self._send_error("Failed to write .gitignore to workspace", status=500)  # type: ignore[attr-defined]


__all__ = ["WorkspaceRoutesMixin"]
