"""
tests/test_github_sync.py
==========================
Comprehensive Integration Test Suite for GitHub Credential Persistence,
AI Semantic Changelog Generation, and Milestone Auto-Push:
1. GitCredentialManager: local config persistence, token masking, token scrubbing,
   authenticated URL synthesis, and connection testing.
2. AISynthesizer: session-wide changelog synthesis and conventional commit generation.
3. ShadowGit Milestone Push: updating CHANGELOG.md, committing dirty workspaces, and pushing to upstream.
4. Web Server Endpoints:
   - GET  /api/git/config
   - POST /api/git/config
   - POST /api/git/test
   - POST /api/project/<name>/prepare-sync
   - POST /api/project/<name>/git-push
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_analyzer import AISynthesizer
from scripts.git_shadow import ShadowGit, GitCredentialManager
from scripts.storage_rotator import SessionDB, StorageRotator
from scripts.web_server import EngineWebServer


class TestGitCredentialManager(unittest.TestCase):
    """Unit tests for GitCredentialManager."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_cred_test_"))
        self.ai_data_dir = self.temp_dir / "ai_data"
        self.ai_data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_config_and_persistence(self) -> None:
        """Verify default config loading and saving."""
        cfg = GitCredentialManager.load_config(engine_root=self.temp_dir)
        self.assertEqual(cfg["github_username"], "")
        self.assertEqual(cfg["github_token"], "")
        self.assertEqual(cfg["default_remote"], "origin")
        self.assertEqual(cfg["default_branch"], "main")
        self.assertTrue(cfg["auto_generate_changelog"])

        # Save new values
        update_cfg = {
            "github_username": "octocat",
            "github_token": "ghp_1234567890abcdef1234567890",
            "default_remote": "origin",
            "default_branch": "master",
            "auto_generate_changelog": False,
        }
        saved = GitCredentialManager.save_config(update_cfg, engine_root=self.temp_dir)
        self.assertEqual(saved["github_username"], "octocat")
        self.assertEqual(saved["github_token"], "ghp_1234567890abcdef1234567890")

        # Reload
        reloaded = GitCredentialManager.load_config(engine_root=self.temp_dir)
        self.assertEqual(reloaded["github_username"], "octocat")
        self.assertEqual(reloaded["github_token"], "ghp_1234567890abcdef1234567890")
        self.assertFalse(reloaded["auto_generate_changelog"])

    def test_token_masking(self) -> None:
        """Verify that token masking uses safe ASCII asterisks and protects tokens."""
        cfg = {
            "github_username": "octocat",
            "github_token": "ghp_1234567890abcdef1234567890",
        }
        masked = GitCredentialManager.mask_token(cfg)
        self.assertEqual(masked["github_username"], "octocat")
        self.assertTrue(masked["github_token"].startswith("ghp_"))
        self.assertTrue(masked["github_token"].endswith("90"))
        self.assertIn("****", masked["github_token"])
        self.assertNotIn("abcdef", masked["github_token"])

        # Short tokens
        short_masked = GitCredentialManager.mask_token({"github_token": "abc"})
        self.assertEqual(short_masked["github_token"], "****")

        # Empty token
        empty_masked = GitCredentialManager.mask_token({"github_token": ""})
        self.assertEqual(empty_masked["github_token"], "")

    def test_scrub_text(self) -> None:
        """Verify credentials in text / logs are completely scrubbed."""
        raw = "fatal: repository 'https://octocat:ghp_secretToken12345@github.com/my-org/my-repo.git' not found"
        clean = GitCredentialManager.scrub_text(raw)
        self.assertNotIn("ghp_secretToken12345", clean)
        self.assertNotIn("octocat:", clean)
        self.assertIn("https://***:***@github.com/my-org/my-repo.git", clean)

    def test_get_authenticated_url(self) -> None:
        """Verify authenticated URL formation."""
        GitCredentialManager.save_config({
            "github_username": "testuser",
            "github_token": "testtoken123",
            "default_remote": "origin",
            "default_branch": "main",
            "auto_generate_changelog": True,
        }, engine_root=self.temp_dir)

        url = "https://github.com/testuser/repo.git"
        auth_url = GitCredentialManager.get_authenticated_url(url, engine_root=self.temp_dir)
        self.assertEqual(auth_url, "https://testuser:testtoken123@github.com/testuser/repo.git")

        # Local directory path should remain untouched
        local_path = str(self.temp_dir / "bare.git")
        self.assertEqual(GitCredentialManager.get_authenticated_url(local_path, engine_root=self.temp_dir), local_path)

    def test_connection_testing(self) -> None:
        """Verify test_connection with a local bare git repo."""
        bare_repo = self.temp_dir / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

        res = GitCredentialManager.test_connection(str(bare_repo), engine_root=self.temp_dir)
        self.assertTrue(res["success"])
        self.assertIn("Connection successful", res["message"])

        # Non-existent repository URL
        bad_res = GitCredentialManager.test_connection(str(self.temp_dir / "nonexistent.git"), engine_root=self.temp_dir)
        self.assertFalse(bad_res["success"])
        self.assertIn("failed", bad_res["message"].lower())

    def test_create_remote_repo_missing_token(self) -> None:
        """Verify error when PAT token is missing."""
        res = GitCredentialManager.create_remote_repo("my-repo", engine_root=self.temp_dir)
        self.assertFalse(res["success"])
        self.assertIn("Personal Access Token", res["message"])

    @patch("urllib.request.urlopen")
    def test_create_remote_repo_success(self, mock_urlopen: MagicMock) -> None:
        """Verify successful remote repository creation (HTTP 201)."""
        GitCredentialManager.save_config({
            "github_username": "octocat",
            "github_token": "ghp_validToken1234567890",
        }, engine_root=self.temp_dir)

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "name": "new-test-repo",
            "full_name": "octocat/new-test-repo",
            "clone_url": "https://github.com/octocat/new-test-repo.git",
            "html_url": "https://github.com/octocat/new-test-repo",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = GitCredentialManager.create_remote_repo("new-test-repo", engine_root=self.temp_dir)
        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertFalse(res["already_exists"])
        self.assertEqual(res["clone_url"], "https://github.com/octocat/new-test-repo.git")

    @patch("urllib.request.urlopen")
    def test_create_remote_repo_already_exists(self, mock_urlopen: MagicMock) -> None:
        """Verify HTTP 422 triggers graceful existing repo link."""
        GitCredentialManager.save_config({
            "github_username": "octocat",
            "github_token": "ghp_validToken1234567890",
        }, engine_root=self.temp_dir)

        # Mock HTTPError 422
        fp = MagicMock()
        fp.read.return_value = b'{"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}'
        err = urllib.error.HTTPError("https://api.github.com/user/repos", 422, "Unprocessable Entity", {}, fp)
        mock_urlopen.side_effect = err

        res = GitCredentialManager.create_remote_repo("existing-test-repo", engine_root=self.temp_dir)
        self.assertTrue(res["success"])
        self.assertFalse(res["created"])
        self.assertTrue(res["already_exists"])
        self.assertEqual(res["clone_url"], "https://github.com/octocat/existing-test-repo.git")


class TestAISemanticChangelog(unittest.TestCase):
    """Unit tests for AI Semantic Changelog Generation in AISynthesizer."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ai_changelog_"))
        self.syn = AISynthesizer(engine_root=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_generate_session_changelog_heuristics(self) -> None:
        """Verify fallback/offline heuristics generate Conventional Commits and changelog entries."""
        events = [
            {
                "id": 1,
                "event_type": "EDIT",
                "summary": "Modified core server routes and supervisor",
                "patches": [
                    {
                        "file_path": "scripts/web_server.py",
                        "diff_content": "@@ -10,3 +10,4 @@\n+def new_route(): pass\n",
                    },
                    {
                        "file_path": "scripts/orchestrator.py",
                        "diff_content": "@@ -5,2 +5,3 @@\n+class NewWorker: pass\n",
                    },
                ],
            },
            {
                "id": 2,
                "event_type": "EDIT",
                "summary": "Updated frontend styles",
                "patches": [
                    {
                        "file_path": "web/style.css",
                        "diff_content": "@@ -20,2 +20,3 @@\n+.modal-new { max-height: 88vh; }\n",
                    },
                ],
            },
        ]

        result = self.syn.generate_session_changelog(events)
        self.assertIn("commit_title", result)
        self.assertIn("commit_body", result)
        self.assertIn("changelog_entry", result)
        self.assertTrue(result["commit_title"].startswith("feat") or result["commit_title"].startswith("refactor"))
        self.assertTrue(result.get("diff_hash", "").startswith("session_"))
        self.assertIn("### [", result["changelog_entry"])
        self.assertIn("scripts/web_server.py", result["changelog_entry"])


class TestMilestoneAutoPush(unittest.TestCase):
    """Integration test for ShadowGit milestone auto-push and CHANGELOG.md prepending."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_git_push_test_"))
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.bare_upstream = self.temp_dir / "upstream.git"

        # Init bare upstream
        subprocess.run(["git", "init", "--bare", str(self.bare_upstream)], check=True, capture_output=True)

        # Init workspace with git and commit initial file
        subprocess.run(["git", "init", "-b", "main", str(self.workspace)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.name", "Test User"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.email", "test@example.com"], check=True)

        (self.workspace / "README.md").write_text("# My Test App\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workspace), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "-m", "Initial commit"], check=True)

        # Add origin remote
        subprocess.run(["git", "-C", str(self.workspace), "remote", "add", "origin", str(self.bare_upstream)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "push", "-u", "origin", "main"], check=True, capture_output=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_milestone_push_with_changelog(self) -> None:
        """Verify pushing with changelog content creates and prepends CHANGELOG.md correctly."""
        # 1. First push
        (self.workspace / "app.py").write_text("print('version 1.0')\n", encoding="utf-8")
        changelog_1 = "### [2026-09-24 04:00 UTC] feat(core): implement core engine v1\n\n- Added app.py\n- Initial release"
        commit_msg_1 = "feat(core): implement core engine v1\n\n- Added app.py"

        res1 = ShadowGit.push_to_remote(
            self.workspace,
            remote_name="origin",
            commit_message=commit_msg_1,
            changelog_content=changelog_1,
            branch="main",
            engine_root=self.temp_dir,
        )
        self.assertTrue(res1["success"], msg=f"Push 1 failed: {res1.get('message')}")

        changelog_file = self.workspace / "CHANGELOG.md"
        self.assertTrue(changelog_file.exists())
        content_1 = changelog_file.read_text(encoding="utf-8")
        self.assertTrue(content_1.startswith("# Changelog\n\n"))
        self.assertIn("feat(core): implement core engine v1", content_1)

        # 2. Second push - verify prepending
        (self.workspace / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")
        changelog_2 = "### [2026-09-24 04:30 UTC] feat(utils): add helper utilities\n\n- Added helper function"
        commit_msg_2 = "feat(utils): add helper utilities"

        res2 = ShadowGit.push_to_remote(
            self.workspace,
            remote_name="origin",
            commit_message=commit_msg_2,
            changelog_content=changelog_2,
            branch="main",
            engine_root=self.temp_dir,
        )
        self.assertTrue(res2["success"], msg=f"Push 2 failed: {res2.get('message')}")

        content_2 = changelog_file.read_text(encoding="utf-8")
        self.assertTrue(content_2.startswith("# Changelog\n\n"))
        idx_feat_utils = content_2.find("feat(utils): add helper utilities")
        idx_feat_core = content_2.find("feat(core): implement core engine v1")
        self.assertGreater(idx_feat_core, idx_feat_utils, "Newest changelog entry should be prepended before older entries")

        # Verify commits in upstream bare repo
        log_res = subprocess.run(
            ["git", "-C", str(self.bare_upstream), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("feat(utils): add helper utilities", log_res.stdout)
        self.assertIn("feat(core): implement core engine v1", log_res.stdout)


class TestWebServerGitEndpoints(unittest.TestCase):
    """Integration test for web server Git configuration, connection test, prepare-sync, and git-push APIs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_web_git_test_"))
        cls.rotator = StorageRotator(engine_root=cls.temp_dir)
        cls.project_name = "sync_demo_project"
        cls.workspace = cls.temp_dir / "sync_demo_workspace"
        cls.workspace.mkdir(parents=True, exist_ok=True)
        cls.bare_upstream = cls.temp_dir / "sync_upstream.git"

        # Init bare upstream
        subprocess.run(["git", "init", "--bare", str(cls.bare_upstream)], check=True, capture_output=True)

        # Init workspace
        subprocess.run(["git", "init", "-b", "main", str(cls.workspace)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(cls.workspace), "config", "user.name", "Sync Bot"], check=True)
        subprocess.run(["git", "-C", str(cls.workspace), "config", "user.email", "bot@example.com"], check=True)
        (cls.workspace / "index.js").write_text("console.log('init');\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(cls.workspace), "add", "index.js"], check=True)
        subprocess.run(["git", "-C", str(cls.workspace), "commit", "-m", "Initial setup"], check=True)
        subprocess.run(["git", "-C", str(cls.workspace), "remote", "add", "origin", str(cls.bare_upstream)], check=True)
        subprocess.run(["git", "-C", str(cls.workspace), "push", "-u", "origin", "main"], check=True, capture_output=True)

        # Create session in rotator
        session_dir = cls.rotator.init_project(cls.project_name, str(cls.workspace))
        db = SessionDB(session_dir / "session.db")
        session_id = db.create_session(
            project_name=cls.project_name,
            target_path=str(cls.workspace),
        )
        eid = db.record_event(
            session_id=session_id,
            first_file="index.js",
            summary="Added greeting and logger",
            event_type="EDIT",
        )
        db.record_patch(
            event_id=eid,
            file_path="index.js",
            diff_content="@@ -1,1 +1,2 @@\n console.log('init');\n+console.log('hello world');\n",
        )
        db.close()

        # Update worker.status.json
        status_file = session_dir / "worker.status.json"
        status_file.write_text(json.dumps({
            "project_name": cls.project_name,
            "project_slug": "sync_demo_project",
            "target_path": str(cls.workspace),
            "pid": os.getpid(),
            "alive": True,
        }), encoding="utf-8")

        # Set up offline heuristic mode for AI in the test engine
        ai_data_dir = cls.temp_dir / "ai_data"
        ai_data_dir.mkdir(parents=True, exist_ok=True)
        (ai_data_dir / "models_config.json").write_text(json.dumps({
            "preferred_provider": "heuristic",
            "gemini_api_key": "",
        }), encoding="utf-8")

        # Start web server
        cls.server = EngineWebServer(
            engine_root=cls.temp_dir,
            host="127.0.0.1",
            port=18940,
            web_dir=cls.temp_dir / "web",
        )
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_01_git_config_get_and_post(self) -> None:
        """Verify GET & POST /api/git/config."""
        # 1. Initial GET
        res = self._get("/api/git/config")
        self.assertTrue(res["success"])
        self.assertIn("config", res)
        self.assertEqual(res["config"]["default_remote"], "origin")

        # 2. Save credentials via POST
        save_res = self._post("/api/git/config", {
            "github_username": "synctester",
            "github_token": "ghp_secureToken998877665544",
            "default_remote": "origin",
            "default_branch": "main",
            "auto_generate_changelog": True,
        })
        self.assertTrue(save_res["success"])
        self.assertEqual(save_res["config"]["github_username"], "synctester")
        self.assertIn("****", save_res["config"]["github_token"])
        self.assertNotIn("secureToken", save_res["config"]["github_token"])

        # 3. Save again with masked placeholder; verify token not clobbered
        save_res2 = self._post("/api/git/config", {
            "github_username": "synctester_renamed",
            "github_token": save_res["config"]["github_token"],  # masked token
        })
        self.assertTrue(save_res2["success"])
        self.assertEqual(save_res2["config"]["github_username"], "synctester_renamed")
        # Check actual stored token on disk
        stored = GitCredentialManager.load_config(engine_root=self.temp_dir)
        self.assertEqual(stored["github_token"], "ghp_secureToken998877665544")

    def test_02_git_test_endpoint(self) -> None:
        """Verify POST /api/git/test."""
        test_res = self._post("/api/git/test", {
            "repo_url": str(self.bare_upstream),
        })
        self.assertTrue(test_res["success"])
        self.assertIn("Connection successful", test_res["message"])

    def test_03_prepare_sync_endpoint(self) -> None:
        """Verify POST /api/project/<name>/prepare-sync."""
        prep_res = self._post(f"/api/project/{self.project_name}/prepare-sync", {})
        self.assertTrue(prep_res["success"])
        self.assertEqual(prep_res["project"], "sync_demo_project")
        self.assertIn("commit_title", prep_res)
        self.assertIn("commit_body", prep_res)
        self.assertIn("changelog_entry", prep_res)
        self.assertEqual(prep_res["events_count"], 1)
        self.assertEqual(prep_res["branch"], "main")

    def test_04_git_push_endpoint_with_milestone_sync(self) -> None:
        """Verify POST /api/project/<name>/git-push executes full push with changelog and commit msg."""
        (self.workspace / "index.js").write_text("console.log('init');\nconsole.log('hello world');\n", encoding="utf-8")

        push_res = self._post(f"/api/project/{self.project_name}/git-push", {
            "remote": "origin",
            "branch": "main",
            "commit_message": "feat(demo): release milestone v1.1.0",
            "changelog_content": "### [2026-09-24] feat(demo): release milestone v1.1.0\n\n- Added hello world",
        })
        self.assertTrue(push_res["success"], msg=f"Push failed: {push_res.get('message')}")
        self.assertIn("commit", push_res)

        # Verify CHANGELOG.md was created in workspace
        cl_file = self.workspace / "CHANGELOG.md"
        self.assertTrue(cl_file.exists())
        self.assertIn("feat(demo): release milestone v1.1.0", cl_file.read_text(encoding="utf-8"))

        # Verify upstream bare repo received the commit
        log_res = subprocess.run(
            ["git", "-C", str(self.bare_upstream), "log", "-n", "1", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(log_res.stdout.strip(), "feat(demo): release milestone v1.1.0")

    @patch("scripts.git_shadow.GitCredentialManager.create_remote_repo")
    def test_05_create_git_repo_endpoint(self, mock_create: MagicMock) -> None:
        """Verify POST /api/git/create-repo creates repo and configures origin remote on workspace."""
        mock_create.return_value = {
            "success": True,
            "created": True,
            "already_exists": False,
            "clone_url": "https://github.com/octocat/sync_demo_project.git",
            "html_url": "https://github.com/octocat/sync_demo_project",
            "full_name": "octocat/sync_demo_project",
        }

        res = self._post("/api/git/create-repo", {
            "project_name": self.project_name,
            "repo_name": self.project_name,
            "private": False,
        })
        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertTrue(res.get("remote_configured"))

        # Verify that workspace origin was updated to the clone_url
        chk = subprocess.run(
            ["git", "-C", str(self.workspace), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(chk.stdout.strip(), "https://github.com/octocat/sync_demo_project.git")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SPD ANALYSIS ENGINE — GITHUB SYNC & CHANGELOG TEST SUITE")
    print("=" * 70 + "\n")
    unittest.main(verbosity=2)
