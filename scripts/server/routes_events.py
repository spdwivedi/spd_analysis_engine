"""
scripts/server/routes_events.py
================================
Data retrieval, git operations, burst rollback/sealing, and record-exec
routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager
    from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
    from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from scripts.git_shadow import ShadowGit, GitCredentialManager
    from scripts.shell_interceptor import ExecutionTracker
    from scripts.ai_analyzer import AISynthesizer

try:
    from .routes_events_export import EventExportRoutesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.routes_events_export import EventExportRoutesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.server.routes_events_export import EventExportRoutesMixin

logger = logging.getLogger(__name__)
_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class EventRoutesMixin(EventExportRoutesMixin):
    """Mixin containing data retrieval, git, and rollback handlers."""

    def _handle_api_events(self, project_name: str) -> None:
        """Fetch all recorded events and patches from the project session database across all sessions."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        # Consolidate historical runs so all prompt bursts (#1 through #N) are in the canonical database
        consolidated_path = consolidate_project_history(engine.engine_root, slug)

        # Search current, last_run, history
        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"
        if not db_path.exists() and consolidated_path and consolidated_path.exists():
            db_path = consolidated_path
            tier = "history"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"
                    tier = "history"

        if not db_path.exists():
            self._send_json({"project": slug, "tier": None, "events": []})  # type: ignore[attr-defined]
            return

        try:
            events_data = engine._read_events_from_db(db_path)
            sessions_data = engine._read_sessions_from_db(db_path)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "tier": tier,
                "db_path": str(db_path),
                "events": events_data,
                "sessions": sessions_data,
            })
        except Exception as exc:
            logger.exception("Failed to query events for %s", slug)
            self._send_error(f"Error querying session database: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_sessions(self, project_name: str) -> None:
        """Fetch all sessions and burst metrics for a project."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"
                    tier = "history"

        if not db_path.exists():
            self._send_json({"project": slug, "tier": None, "sessions": []})  # type: ignore[attr-defined]
            return

        try:
            sessions = engine._read_sessions_from_db(db_path)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "tier": tier,
                "db_path": str(db_path),
                "sessions": sessions,
            })
        except Exception as exc:
            logger.exception("Failed to query sessions for %s", slug)
            self._send_error(f"Error querying sessions: {exc}", status=500)  # type: ignore[attr-defined]

    _handle_api_project_sessions = _handle_api_sessions

    def _handle_api_get_git_config(self) -> None:
        """Return stored GitHub credential settings with token masked."""
        engine = self.server_instance  # type: ignore[attr-defined]
        cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)
        masked_cfg = GitCredentialManager.mask_token(cfg)
        self._send_json({"success": True, "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_save_git_config(self, body: dict[str, Any]) -> None:
        """Save updated GitHub credential settings."""
        engine = self.server_instance  # type: ignore[attr-defined]
        current_cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)

        new_cfg = dict(current_cfg)
        if "github_username" in body:
            new_cfg["github_username"] = str(body["github_username"]).strip()

        raw_token = str(body.get("github_token", "")).strip()
        if raw_token and "****" not in raw_token:
            new_cfg["github_token"] = raw_token
        elif raw_token == "" and "github_token" in body:
            new_cfg["github_token"] = ""

        if "default_remote" in body:
            new_cfg["default_remote"] = str(body["default_remote"]).strip() or "origin"
        if "default_branch" in body:
            new_cfg["default_branch"] = str(body["default_branch"]).strip() or "main"
        if "auto_generate_changelog" in body:
            new_cfg["auto_generate_changelog"] = bool(body["auto_generate_changelog"])

        saved = GitCredentialManager.save_config(new_cfg, engine_root=engine.engine_root)
        masked_cfg = GitCredentialManager.mask_token(saved)
        self._send_json({"success": True, "message": "GitHub settings saved successfully.", "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_test_git_connection(self, body: dict[str, Any]) -> None:
        """Test connection to a remote git repository."""
        engine = self.server_instance  # type: ignore[attr-defined]
        repo_url = body.get("repo_url")
        override_token = body.get("github_token")
        override_user = body.get("github_username")

        if override_token and "****" in override_token:
            override_token = None

        res = GitCredentialManager.test_connection(
            repo_url=repo_url,
            engine_root=engine.engine_root,
            token=override_token,
            username=override_user,
        )
        self._send_json(res)  # type: ignore[attr-defined]

    def _handle_api_create_git_repo(self, body: dict[str, Any]) -> None:
        """Create a remote repository on GitHub and optionally configure origin on the workspace."""
        engine = self.server_instance  # type: ignore[attr-defined]
        repo_name = str(body.get("repo_name", "")).strip()
        project_name = str(body.get("project_name", "")).strip()
        private = bool(body.get("private", False))
        description = body.get("description")

        if not repo_name:
            if project_name:
                repo_name = _slug(project_name)
            else:
                self._send_error("Parameter 'repo_name' is required.", status=400)  # type: ignore[attr-defined]
                return

        res = GitCredentialManager.create_remote_repo(
            repo_name=repo_name,
            private=private,
            engine_root=engine.engine_root,
            description=description,
        )

        if not res.get("success"):
            self._send_json(res, status=400)  # type: ignore[attr-defined]
            return

        # If project_name is provided, configure origin remote on the workspace
        remote_configured = False
        clone_url = res.get("clone_url")
        if project_name and clone_url:
            slug = _slug(project_name)
            target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
            if target_path and target_path.exists():
                git_bin = shutil.which("git")
                if git_bin:
                    try:
                        # Ensure repo initialized
                        if not (target_path / ".git").exists():
                            subprocess.run([git_bin, "init", "-b", "main"], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)

                        # Check if origin remote exists
                        chk = subprocess.run([git_bin, "remote", "get-url", "origin"], cwd=str(target_path), capture_output=True, text=True, check=False, creationflags=_NO_WINDOW_FLAG, shell=False)
                        if chk.returncode == 0:
                            # Update origin
                            subprocess.run([git_bin, "remote", "set-url", "origin", clone_url], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)
                        else:
                            # Add origin
                            subprocess.run([git_bin, "remote", "add", "origin", clone_url], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)
                        remote_configured = True
                        update_project_meta(engine.engine_root, slug, remote_url=clone_url, remote_branch="main")
                    except Exception as exc:
                        logger.warning("Failed to configure remote 'origin' for %s: %s", slug, exc)

        res["remote_configured"] = remote_configured
        self._send_json(res)  # type: ignore[attr-defined]

    def _handle_api_prepare_sync(self, project_name: str, body: dict[str, Any]) -> None:
        """
        Synthesize session bursts into Conventional Commit title/body and CHANGELOG entry
        in preparation for remote push.
        """
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
        if not target_path or not target_path.exists():
            self._send_error(f"Target workspace for project '{slug}' could not be resolved or does not exist.", status=404)  # type: ignore[attr-defined]
            return

        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        events: list[dict[str, Any]] = []
        if db_path and db_path.exists():
            events = engine._read_events_from_db(db_path)

        synthesizer = AISynthesizer(engine_root=engine.engine_root)
        changelog_res = synthesizer.generate_session_changelog(events)

        cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)
        default_remote = cfg.get("default_remote", "origin")
        default_branch = cfg.get("default_branch", "main")
        auto_changelog = cfg.get("auto_generate_changelog", True)

        detected_remote_url = ""
        if (target_path / ".git").exists():
            try:
                cmd_res = subprocess.run(
                    ["git", "remote", "get-url", default_remote],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=_NO_WINDOW_FLAG,
                    shell=False,
                )
                if cmd_res.returncode == 0:
                    detected_remote_url = GitCredentialManager.scrub_text(cmd_res.stdout.strip())
            except Exception:
                pass

        # Check project.meta for persistent remote configuration
        meta = read_project_meta(engine.engine_root, slug)
        saved_remote = meta.get("remote_url", "")
        saved_branch = meta.get("remote_branch", "")
        if saved_remote:
            detected_remote_url = saved_remote
        if saved_branch:
            default_branch = saved_branch

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "project": slug,
            "target_path": str(target_path),
            "commit_title": changelog_res.get("commit_title", "feat: session updates"),
            "commit_body": changelog_res.get("commit_body", "- Code modifications applied."),
            "changelog_entry": changelog_res.get("changelog_entry", ""),
            "provider": changelog_res.get("provider", "offline"),
            "remote_url": detected_remote_url or "",
            "remote": default_remote,
            "branch": default_branch,
            "auto_generate_changelog": auto_changelog,
            "events_count": len(events),
        })

    def _handle_api_git_push(self, project_name: str, body: dict[str, Any]) -> None:
        """Push workspace changes to remote git repository."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        remote_name = body.get("remote", "origin")
        remote_url = body.get("remote_url")
        commit_message = body.get("commit_message")
        changelog_content = body.get("changelog_content")
        branch = body.get("branch", "main")

        target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
        if not target_path or not target_path.exists():
            self._send_error(f"Target workspace for project '{slug}' could not be resolved or does not exist.", status=404)  # type: ignore[attr-defined]
            return

        # If remote_url not provided, check project.meta
        meta = read_project_meta(engine.engine_root, slug)
        if not remote_url and meta.get("remote_url"):
            remote_url = meta["remote_url"]
        if not branch and meta.get("remote_branch"):
            branch = meta["remote_branch"]

        try:
            result = ShadowGit.push_to_remote(
                target_path,
                remote_url=remote_url,
                commit_message=commit_message,
                changelog_content=changelog_content,
                branch=branch,
                remote_name=remote_name,
                engine_root=engine.engine_root,
            )

            if result.get("success"):
                effective_remote = remote_url or result.get("remote", "")
                effective_branch = result.get("branch", branch)
                update_project_meta(
                    engine.engine_root,
                    slug,
                    remote_url=effective_remote,
                    remote_branch=effective_branch,
                    last_push_status="SUCCESS",
                    last_push_time=datetime.now(_IST).strftime("%Y-%m-%d %I:%M:%S %p IST"),
                    last_push_commit=result.get("commit", ""),
                )

            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "target_path": str(target_path),
                **result,
            })
        except Exception as exc:
            logger.exception("Failed git push for %s", slug)
            self._send_error(f"Error during git push: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_rollback_burst(self, project_name: str, event_id: int) -> None:
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
        try:
            try:
                from spd_analysis_engine.scripts.storage_rotator import rollback_burst
            except (ImportError, ModuleNotFoundError):
                from scripts.storage_rotator import rollback_burst
            res = rollback_burst(engine.engine_root, project_name, event_id, target_dir=target_path)
            if res.get("success"):
                self._send_json(res)  # type: ignore[attr-defined]
            else:
                self._send_error(res.get("message", "Rollback failed"), status=400)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Error rolling back burst #%d for project %s", event_id, project_name)
            self._send_error(str(exc), status=500)  # type: ignore[attr-defined]

    def _handle_api_seal_burst(self, project_name: str) -> None:
        """Trigger immediate burst sealing (Lap button) for an active project."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        curr_dir = engine.engine_root / "current" / slug
        if not curr_dir.exists():
            self._send_error(f"Project '{slug}' is not active in current/.", status=400)  # type: ignore[attr-defined]
            return

        cmd_file = curr_dir / "seal_burst.cmd"
        try:
            cmd_file.write_text("SEAL\n", encoding="utf-8")
            # Poll up to 300ms for worker to process and delete the trigger file
            for _ in range(6):
                time.sleep(0.05)
                if not cmd_file.exists():
                    break
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "message": "Burst sealed successfully.",
            })
        except Exception as exc:
            logger.exception("Error writing seal_burst.cmd for %s", slug)
            self._send_error(str(exc), status=500)  # type: ignore[attr-defined]
