"""
tests/test_v14_micromodules_scaffold_gitignore_autopush.py
==========================================================
Unit tests for the new micro-modules:
1. scripts/core/scaffolder.py (workspace scaffold & remote handshake)
2. scripts/storage/gitignore_manager.py & scripts/server/routes_workspace.py (.gitignore API & silent bypass)
3. scripts/ai/synthesizer_tiered.py (Tier 1 heuristic & Tier 2 progressive AI)
4. scripts/git/auto_pusher.py (event-driven auto-commit & push upon burst seal)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.core.scaffolder import scaffold_new_project, DEFAULT_GITIGNORE_CONTENT
from scripts.storage.gitignore_manager import read_project_gitignore, write_project_gitignore
from scripts.server.routes_workspace import WorkspaceRoutesMixin
from scripts.ai.synthesizer_tiered import synthesize_milestone
from scripts.git.auto_pusher import handle_post_seal_autopush
from scripts.event_bundler import is_path_suppressed
from scripts.storage.meta import update_project_meta


class TestScaffolder(unittest.TestCase):
    """Test smart workspace scaffolding and remote handshake."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_scaffold_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scaffold_creates_workspace_and_baseline_gitignore(self) -> None:
        target = self.temp_dir / "new_project"
        res = scaffold_new_project("new_project", target, create_remote=False)

        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertTrue(target.is_dir())

        gi = target / ".gitignore"
        self.assertTrue(gi.is_file())
        gi_content = gi.read_text(encoding="utf-8")
        self.assertIn(".env", gi_content)
        self.assertIn("node_modules/", gi_content)
        self.assertIn("*.pdf", gi_content)
        self.assertIn("session.db*", gi_content)
        self.assertIn("worker.log", gi_content)

        # Check git repo initialized
        self.assertTrue((target / ".git").is_dir())

    def test_scaffold_with_remote_creation(self) -> None:
        target = self.temp_dir / "remote_proj"
        mock_create = MagicMock(return_value={
            "success": True,
            "clone_url": "https://github.com/mockuser/remote_proj.git",
        })

        with patch("scripts.core.scaffolder.GitCredentialManager.create_remote_repo", mock_create), \
             patch("scripts.core.scaffolder.GitCredentialManager.get_authenticated_url", return_value="https://token@github.com/mockuser/remote_proj.git"), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="origin", stderr="")
            res = scaffold_new_project("remote_proj", target, create_remote=True, pat="mock_pat_123")
            self.assertTrue(res["success"])
            self.assertEqual(res["remote_url"], "https://github.com/mockuser/remote_proj.git")


class TestGitignoreManager(unittest.TestCase):
    """Test .gitignore reading, updating, and silent watchdog bypass."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_gi_"))
        self.target = self.temp_dir / "repo"
        self.target.mkdir(parents=True, exist_ok=True)
        # Init git repo
        subprocess.run(["git", "init"], cwd=str(self.target), capture_output=True, check=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_project_gitignore_default_when_missing(self) -> None:
        content = read_project_gitignore(self.target)
        self.assertEqual(content, DEFAULT_GITIGNORE_CONTENT)

    def test_read_project_gitignore_existing(self) -> None:
        (self.target / ".gitignore").write_text("*.log\ncustom_build/\n", encoding="utf-8")
        content = read_project_gitignore(self.target)
        self.assertEqual(content, "*.log\ncustom_build/\n")

    def test_write_project_gitignore_with_silent_bypass(self) -> None:
        new_rules = "# Custom Rules\nsecrets/\n*.secret\n"
        success = write_project_gitignore(self.target, new_rules, project_slug="repo")
        self.assertTrue(success)

        # Check content on disk
        gi_file = self.target / ".gitignore"
        self.assertEqual(gi_file.read_text(encoding="utf-8"), new_rules)

        # Check path suppression window is active
        self.assertTrue(is_path_suppressed(str(gi_file)))
        self.assertTrue(is_path_suppressed(".gitignore"))


class TestWorkspaceRoutesMixin(unittest.TestCase):
    """Test GET and PUT /api/project/<slug>/gitignore handlers."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_ws_route_"))
        self.workspace = self.temp_dir / "target_workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        class MockServer:
            engine_root = self.temp_dir

        class MockHandler(WorkspaceRoutesMixin):
            def __init__(self, srv, ws_path):
                self.server_instance = srv
                self.ws_path = ws_path
                self.last_json = None
                self.last_status = None
                self.last_error = None

            def _resolve_target_path(self, slug):
                return self.ws_path

            def _send_json(self, data, status=200):
                self.last_json = data
                self.last_status = status

            def _send_error(self, message, status=400):
                self.last_error = message
                self.last_status = status

        self.handler = MockHandler(MockServer(), self.workspace)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_and_put_gitignore_endpoint(self) -> None:
        # GET
        self.handler._handle_api_get_gitignore("target_workspace")
        self.assertEqual(self.handler.last_status, 200)
        self.assertEqual(self.handler.last_json["content"], DEFAULT_GITIGNORE_CONTENT)

        # PUT
        updated = "# Updated Rules\n*.bak\n"
        self.handler._handle_api_put_gitignore("target_workspace", {"content": updated})
        self.assertEqual(self.handler.last_status, 200)
        self.assertTrue(self.handler.last_json["success"])

        # GET again
        self.handler._handle_api_get_gitignore("target_workspace")
        self.assertEqual(self.handler.last_json["content"], updated)


class TestTieredSynthesizer(unittest.TestCase):
    """Test progressive and tiered commit synthesis."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_synth_"))
        self.repo = self.temp_dir / "test_repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.repo), capture_output=True, check=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tier1_root_commit_synthesis(self) -> None:
        # Create uncommitted files
        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        src_dir = self.repo / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")

        res = synthesize_milestone(
            burst_ids=[1],
            mode="tier1",
            project_slug="test_repo",
            target_path=self.repo,
            engine_root=self.temp_dir,
        )

        self.assertEqual(res["mode_used"], "tier1_root")
        self.assertTrue(res["title"].startswith("feat(scaffold): initialize test_repo architecture"))
        self.assertIn("Total files: 2", res["body"])
        self.assertIn("Initial Architecture Baseline", res["changelog"])

    def test_tier1_heuristic_conventional_tag(self) -> None:
        # Commit root first
        (self.repo / "init.txt").write_text("init\n", encoding="utf-8")
        subprocess.run(["git", "add", "init.txt"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.repo), capture_output=True, check=False)

        # Create documentation burst
        (self.repo / "ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")

        res = synthesize_milestone(
            burst_ids=[2],
            mode="tier1",
            project_slug="test_repo",
            target_path=self.repo,
            engine_root=self.temp_dir,
        )

        self.assertEqual(res["mode_used"], "tier1_heuristic")
        self.assertTrue(res["title"].startswith("docs(spec):"))
        self.assertIn("ARCHITECTURE.md", res["body"])


class TestAutoPusher(unittest.TestCase):
    """Test event-driven auto-pusher upon burst seal."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_autopush_"))
        for tier in ("current", "last_run", "history", "trash"):
            (self.temp_dir / tier).mkdir(parents=True, exist_ok=True)
        self.repo = self.temp_dir / "target_repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.repo), capture_output=True, check=False)
        (self.repo / "test.txt").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.repo), capture_output=True, check=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_autopush_skipped_when_disabled(self) -> None:
        # auto_push not set in meta
        update_project_meta(self.temp_dir, "my_project", target_path=str(self.repo))
        res = handle_post_seal_autopush("my_project", self.repo, burst_id=1, engine_root=self.temp_dir)
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("reason"), "auto_push not enabled")

    def test_autopush_skipped_when_no_remote(self) -> None:
        # Enable auto_push but no origin
        update_project_meta(self.temp_dir, "my_project", target_path=str(self.repo), auto_push=True)
        res = handle_post_seal_autopush("my_project", self.repo, burst_id=1, engine_root=self.temp_dir)
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("reason"), "no remote origin configured")

    def test_autopush_success(self) -> None:
        # Configure a local bare repo as remote origin
        bare_remote = self.temp_dir / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=False)
        subprocess.run(["git", "remote", "add", "origin", str(bare_remote)], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(self.repo), capture_output=True, check=False)

        # Make a change
        (self.repo / "new_feature.py").write_text("x = 1\n", encoding="utf-8")
        update_project_meta(self.temp_dir, "my_project", target_path=str(self.repo), auto_push=True, allow_default_branch=True)

        res = handle_post_seal_autopush("my_project", self.repo, burst_id=2, engine_root=self.temp_dir)
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("committed"))
        self.assertTrue(res.get("pushed"))


class TestGitConfigAutoPush(unittest.TestCase):
    """Test auto_push persistence in Git config and project.meta via endpoints."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_git_cfg_"))
        for tier in ("current", "last_run", "history", "trash"):
            (self.temp_dir / tier).mkdir(parents=True, exist_ok=True)

        from scripts.server.routes_events import EventRoutesMixin
        from scripts.server.routes_projects import ProjectRoutesMixin

        class MockServer:
            engine_root = self.temp_dir
            supervisor = MagicMock()
            supervisor.get_status = MagicMock(return_value=[])

        class CombinedHandler(EventRoutesMixin, ProjectRoutesMixin):
            def __init__(self, srv):
                self.server_instance = srv
                self.path = "/api/git/config"
                self.last_json = None
                self.last_status = None
                self.last_error = None

            def _send_json(self, data, status=200):
                self.last_json = data
                self.last_status = status

            def _send_error(self, message, status=400):
                self.last_error = message
                self.last_status = status

        self.handler = CombinedHandler(MockServer())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_get_autopush_git_config(self) -> None:
        from scripts.storage.meta import read_project_meta

        # Initially save with auto_push=True for specific project
        self.handler._handle_api_save_git_config({
            "auto_push": True,
            "project": "autopush_proj",
            "default_remote": "origin",
            "default_branch": "main",
        })
        self.assertEqual(self.handler.last_status, 200)
        self.assertTrue(self.handler.last_json["config"]["auto_push"])

        # Check project.meta
        meta = read_project_meta(self.temp_dir, "autopush_proj")
        self.assertEqual(meta.get("auto_push"), "true")

        # GET git config with project query
        self.handler.path = "/api/git/config?project=autopush_proj"
        self.handler._handle_api_get_git_config()
        self.assertEqual(self.handler.last_status, 200)
        self.assertTrue(self.handler.last_json["config"]["auto_push"])

        # GET project specs
        self.handler._handle_api_project_specs("autopush_proj")
        self.assertEqual(self.handler.last_status, 200)
        self.assertTrue(self.handler.last_json["auto_push"])

        # Save with auto_push=False
        self.handler._handle_api_save_git_config({
            "auto_push": False,
            "project": "autopush_proj",
        })
        meta = read_project_meta(self.temp_dir, "autopush_proj")
        self.assertEqual(meta.get("auto_push"), "false")

        self.handler._handle_api_project_specs("autopush_proj")
        self.assertFalse(self.handler.last_json["auto_push"])


if __name__ == "__main__":
    unittest.main()

