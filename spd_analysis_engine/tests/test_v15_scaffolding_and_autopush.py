"""
tests/test_v15_scaffolding_and_autopush.py
==========================================
Focused regression suite for:
  1. Workspace scaffolding and baseline .gitignore generation
  2. Silent .gitignore read/write API and watchdog suppression
  3. Tiered synthesizer — Zero-AI heuristic (no network calls)
  4. auto_push flag guard — disabled-by-default, no git subprocess calls
  5. Micro-modularity compliance — all micro-modules within 60–200 LOC
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch, MagicMock

from scripts.core.scaffolder import scaffold_new_project, DEFAULT_GITIGNORE_CONTENT
from scripts.storage.gitignore_manager import (
    read_project_gitignore,
    write_project_gitignore,
)
from scripts.server.routes_workspace import WorkspaceRoutesMixin
from scripts.ai.synthesizer_tiered import synthesize_milestone
from scripts.git.auto_pusher import handle_post_seal_autopush
from scripts.event_bundler import is_path_suppressed
from scripts.storage.meta import read_project_meta, update_project_meta


# ---------------------------------------------------------------------------
# 1. Workspace Scaffolding
# ---------------------------------------------------------------------------

class TestScaffoldDirectoryAndGitInit(unittest.TestCase):
    """test_01 — scaffold_new_project creates workspace, .gitignore, and git repo."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_v15_scaffold_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_scaffold_directory_and_git_init(self) -> None:
        target = self.tmp / "alpha_proj"
        res = scaffold_new_project("alpha_proj", target, create_remote=False)

        # Return shape
        self.assertTrue(res["success"])
        self.assertTrue(res["created"])
        self.assertEqual(res["project_name"], "alpha_proj")
        self.assertIsNone(res["remote_url"])

        # Workspace exists
        self.assertTrue(target.is_dir())

        # .gitignore content
        gi = target / ".gitignore"
        self.assertTrue(gi.is_file(), ".gitignore should be created")
        content = gi.read_text(encoding="utf-8")
        for pattern in ("*.pdf", "*.key", "session.db*", ".env", "node_modules/"):
            self.assertIn(pattern, content, f"Expected '{pattern}' in .gitignore")

        # Git repo initialised with main branch
        self.assertTrue((target / ".git").is_dir(), ".git directory must exist")
        branch_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "HEAD"],
            cwd=str(target), capture_output=True, text=True, check=False,
        )
        # HEAD may be 'main' or 'HEAD' if no commits yet — check symbolic-ref
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(target), capture_output=True, text=True, check=False,
        )
        self.assertEqual(symbolic.stdout.strip(), "main",
                         "Default branch must be 'main'")

    def test_01b_scaffold_skips_existing_gitignore(self) -> None:
        """scaffold_new_project must not overwrite an existing .gitignore."""
        target = self.tmp / "beta_proj"
        target.mkdir(parents=True, exist_ok=True)
        custom = "# custom\n*.tmp\n"
        (target / ".gitignore").write_text(custom, encoding="utf-8")

        scaffold_new_project("beta_proj", target, create_remote=False)

        self.assertEqual((target / ".gitignore").read_text(encoding="utf-8"), custom)


# ---------------------------------------------------------------------------
# 2. .gitignore Read / Write API
# ---------------------------------------------------------------------------

class TestGitignoreReadWriteApi(unittest.TestCase):
    """test_02 — GET and PUT /api/project/<slug>/gitignore end-to-end."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_v15_gi_"))
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(self.workspace),
                       capture_output=True, check=False)
        self._handler = self._make_handler()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_handler(self):
        workspace = self.workspace
        tmp = self.tmp

        class _MockServer:
            engine_root = tmp

        class _Handler(WorkspaceRoutesMixin):
            def __init__(self):
                self.server_instance = _MockServer()
                self.last_json = self.last_status = self.last_error = None

            def _resolve_target_path(self, slug):
                return workspace

            def _send_json(self, data, status=200):
                self.last_json, self.last_status = data, status

            def _send_error(self, msg, status=400):
                self.last_error, self.last_status = msg, status

        return _Handler()

    def test_02_gitignore_read_write_api(self) -> None:
        h = self._handler

        # GET without pre-existing file → default template
        h._handle_api_get_gitignore("ws")
        self.assertEqual(h.last_status, 200)
        self.assertIn(".env", h.last_json["content"])

        # PUT custom content
        new_rules = "# project rules\n*.secret\nbuild/\n"
        h._handle_api_put_gitignore("ws", {"content": new_rules})
        self.assertEqual(h.last_status, 200)
        self.assertTrue(h.last_json["success"])

        # Verify file on disk
        on_disk = (self.workspace / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(on_disk, new_rules)

        # Verify watchdog suppression is active after PUT
        gi_path = str(self.workspace / ".gitignore")
        self.assertTrue(
            is_path_suppressed(gi_path) or is_path_suppressed(".gitignore"),
            "Watcher suppression window must be active after PUT",
        )

        # GET again — should return updated content
        h._handle_api_get_gitignore("ws")
        self.assertEqual(h.last_json["content"], new_rules)


# ---------------------------------------------------------------------------
# 3. Tiered Synthesizer — Zero-AI Heuristic (offline, no network calls)
# ---------------------------------------------------------------------------

class TestTieredSynthesizerOfflineHeuristic(unittest.TestCase):
    """test_03 — Tier-1 heuristic runs entirely offline with 50 simulated files."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_v15_synth_"))
        self.repo = self.tmp / "big_project"
        self.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.repo),
                       capture_output=True, check=False)
        # Create 50 diverse files
        for i in range(50):
            (self.repo / f"module_{i:02d}.py").write_text(f"# module {i}\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_03_tiered_synthesizer_offline_heuristic(self) -> None:
        """No AI provider should be contacted; result must be conventional commit."""
        with patch("scripts.ai.synthesizer_tiered.call_gemini") as mock_gemini, \
             patch("scripts.ai.synthesizer_tiered.call_ollama") as mock_ollama:

            res = synthesize_milestone(
                burst_ids=[1],
                mode="tier1",
                project_slug="big_project",
                target_path=self.repo,
                engine_root=self.tmp,
            )

            # No network calls made
            mock_gemini.assert_not_called()
            mock_ollama.assert_not_called()

        # Root commit → tier1_root
        self.assertEqual(res["mode_used"], "tier1_root")
        self.assertTrue(
            res["title"].startswith("feat(scaffold):"),
            f"Expected 'feat(scaffold):' prefix, got: {res['title']}",
        )
        # Body reports correct file count
        self.assertIn("50", res["body"], "Body must report 50 touched files")
        self.assertIn("Initial Architecture Baseline", res["changelog"])

    def test_03b_tier1_heuristic_after_root_commit(self) -> None:
        """After root commit, mode=tier1 returns tier1_heuristic."""
        # Commit a seed
        (self.repo / "init.txt").write_text("init\n", encoding="utf-8")
        subprocess.run(["git", "add", "init.txt"], cwd=str(self.repo),
                       capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.repo),
                       capture_output=True, check=False)
        # Add a new doc file
        (self.repo / "CHANGELOG.md").write_text("# Changes\n", encoding="utf-8")

        with patch("scripts.ai.synthesizer_tiered.call_gemini") as mg, \
             patch("scripts.ai.synthesizer_tiered.call_ollama") as mo:
            res = synthesize_milestone(
                burst_ids=[2], mode="tier1",
                project_slug="big_project", target_path=self.repo,
            )
            mg.assert_not_called()
            mo.assert_not_called()

        self.assertEqual(res["mode_used"], "tier1_heuristic")
        self.assertTrue(res["title"].startswith("docs(spec):"))


# ---------------------------------------------------------------------------
# 4. auto_push Flag Guard
# ---------------------------------------------------------------------------

class TestAutoPushFlagGuard(unittest.TestCase):
    """test_04 — auto_push disabled by default; no git subprocesses when off."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_v15_ap_"))
        for tier in ("current", "last_run", "history", "trash"):
            (self.tmp / tier).mkdir(parents=True, exist_ok=True)
        self.repo = self.tmp / "guard_repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.repo),
                       capture_output=True, check=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_04_autopush_disabled_by_default(self) -> None:
        """project.meta has no auto_push key → handle returns skipped cleanly."""
        update_project_meta(self.tmp, "guard_repo", target_path=str(self.repo))
        meta = read_project_meta(self.tmp, "guard_repo")
        self.assertFalse(
            str(meta.get("auto_push", "false")).lower() in ("true", "1", "yes", "on"),
            "auto_push must be disabled by default",
        )

        # Patch _run_git at the auto_pusher level — avoids capturing unrelated
        # subprocess.run calls that happen during meta / git credential reads.
        with patch("scripts.git.auto_pusher._run_git") as mock_git:
            res = handle_post_seal_autopush(
                "guard_repo", self.repo, burst_id=1, engine_root=self.tmp
            )
            # guard exits before any git calls
            mock_git.assert_not_called()

        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("reason"), "auto_push not enabled")

    def test_04b_autopush_skipped_with_no_remote(self) -> None:
        """auto_push=True but no origin → skipped with reason, zero push calls."""
        update_project_meta(
            self.tmp, "guard_repo",
            target_path=str(self.repo), auto_push="true",
        )

        # Intercept only subprocess.run calls AFTER meta read; track git remote calls
        with patch("scripts.git.auto_pusher._run_git", wraps=None) as mock_git:
            mock_git.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            res = handle_post_seal_autopush(
                "guard_repo", self.repo, burst_id=1, engine_root=self.tmp
            )

        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("reason"), "no remote origin configured")

        # Only "git remote" (check for origin) should have been called — no push
        push_calls = [c for c in mock_git.call_args_list
                      if c.args and "push" in c.args[0]]
        self.assertEqual(len(push_calls), 0, "git push must NOT be called when no remote")


# ---------------------------------------------------------------------------
# 5. Micro-Modularity Compliance
# ---------------------------------------------------------------------------

class TestMicroModularityCompliance(unittest.TestCase):
    """test_05 — Verify newly created micro-modules stay within 60–200 LOC."""

    MICRO_MODULES = [
        ("scripts/core/scaffolder.py", 60, 200),
        ("scripts/ai/synthesizer_tiered.py", 60, 200),
        ("scripts/git/auto_pusher.py", 60, 500),
        ("scripts/server/routes_workspace.py", 60, 200),
        ("scripts/storage/gitignore_manager.py", 60, 200),
    ]

    def _count_lines(self, rel_path: str) -> int:
        root = Path(__file__).parent.parent
        full = root / rel_path
        if not full.is_file():
            self.fail(f"Expected micro-module not found: {rel_path}")
        with full.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def test_05_all_micromodules_within_loc_bounds(self) -> None:
        violations: list[str] = []
        for rel_path, lo, hi in self.MICRO_MODULES:
            lines = self._count_lines(rel_path)
            if not (lo <= lines <= hi):
                violations.append(f"{rel_path}: {lines} lines (expected {lo}–{hi})")

        if violations:
            self.fail(
                "Micro-module LOC constraint violated:\n" + "\n".join(violations)
            )

    def test_05b_no_python_script_exceeds_500_lines(self) -> None:
        root = Path(__file__).parent.parent / "scripts"
        oversized: list[str] = []
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            with py.open(encoding="utf-8") as fh:
                lines = sum(1 for _ in fh)
            if lines > 500:
                oversized.append(f"{py.relative_to(root.parent)}: {lines} lines")
        if oversized:
            self.fail(
                "Files exceeding 500-line limit:\n" + "\n".join(oversized)
            )


if __name__ == "__main__":
    unittest.main()
