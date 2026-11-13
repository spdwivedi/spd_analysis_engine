"""
tests/test_baseline_gitignore_safety.py
=======================================
Test suite verifying that baseline creation and Shadow Git staging are
completely .gitignore-safe across all project structures and file types.

Covers the full Test Matrix:
A. Normal files
B. Ignored file
C. Ignored directory
D. Nested ignored directory
E. .gitignore itself
F. Newly created ignored files in subsequent bursts
G. Binary files
H. Empty files
I. 2,000 files with a mixture of ignored and non-ignored files
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts.core.changeset import ChangeSet
from scripts.core.worker_lifecycle import WorkerState
from scripts.core.worker_bundle import trigger_initial_ingestion, _handle_event_bundle
from scripts.diff_calc import DiffCalculator, is_binary_file
from scripts.git.shadow_commit import stage_paths_nul, filter_gitignored_paths, execute_burst_commit, GitState
from scripts.git.shadow_repo import ShadowGit
from scripts.storage.db import SessionDB
from scripts.storage.ignore_policy import IgnorePolicy


class TestBaselineGitignoreSafety(unittest.TestCase):
    """Test suite for .gitignore-safe baseline creation and Shadow Git micro-commit staging."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve() / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.engine_root = Path(self.temp_dir.name).resolve() / "engine_data"
        self.engine_root.mkdir(parents=True, exist_ok=True)

        self.proj_dir = self.engine_root / "current" / "test_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir = self.proj_dir / "baseline"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.patches_dir = self.proj_dir / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = self.proj_dir / "shadow_git"
        self.db_path = self.proj_dir / "session.db"

        self.git_cmd = shutil.which("git")
        self.assertIsNotNone(self.git_cmd, "Git executable required for tests")

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _get_shadow_commit_files(self) -> set[str]:
        """Query Shadow Git HEAD tree to list all committed files."""
        res = subprocess.run(
            [self.git_cmd, f"--git-dir={self.shadow_dir}", "ls-tree", "-r", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return {line.strip() for line in res.stdout.splitlines() if line.strip()}

    def _make_changeset(self, burst_uid: str, event_id: int, files: list[str]) -> ChangeSet:
        """Helper to create a ChangeSet with effective_files set to files."""
        return ChangeSet(
            burst_uid=burst_uid,
            event_id=event_id,
            sequence=event_id,
            files=list(files),
            effective_files=list(files),
        )

    # -------------------------------------------------------------------------
    # Test A: Normal files
    # -------------------------------------------------------------------------
    def test_matrix_a_normal_files_captured(self) -> None:
        """Normal text files in workspace must be captured in baseline cache and Shadow Git."""
        (self.workspace / "main.py").write_text("print('hello world')\n", encoding="utf-8")
        (self.workspace / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn("main.py", captured)
        self.assertIn("utils.py", captured)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        changeset = self._make_changeset("b1", 1, list(captured.keys()))
        res = shadow.commit_changeset(changeset, summary="Initial baseline")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("main.py", committed)
        self.assertIn("utils.py", committed)

    # -------------------------------------------------------------------------
    # Test B: Ignored file
    # -------------------------------------------------------------------------
    def test_matrix_b_ignored_file_not_captured_and_not_force_added(self) -> None:
        """Files matching .gitignore must not enter baseline cache and must not fail git add."""
        (self.workspace / ".gitignore").write_text("secret.txt\n*.log\n", encoding="utf-8")
        (self.workspace / "normal.txt").write_text("normal content\n", encoding="utf-8")
        (self.workspace / "secret.txt").write_text("secret content\n", encoding="utf-8")
        (self.workspace / "app.log").write_text("log content\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn("normal.txt", captured)
        self.assertIn(".gitignore", captured)
        self.assertNotIn("secret.txt", captured)
        self.assertNotIn("app.log", captured)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        # Even if ignored files are explicitly listed in changeset, staging must filter them safely
        changeset = self._make_changeset("b1", 1, ["normal.txt", "secret.txt", "app.log", ".gitignore"])
        res = shadow.commit_changeset(changeset, summary="Initial baseline")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("normal.txt", committed)
        self.assertIn(".gitignore", committed)
        self.assertNotIn("secret.txt", committed)
        self.assertNotIn("app.log", committed)

    # -------------------------------------------------------------------------
    # Test C: Ignored directory
    # -------------------------------------------------------------------------
    def test_matrix_c_ignored_directory_not_captured_and_not_force_added(self) -> None:
        """Directories matching .gitignore must be pruned and not staged into Shadow Git."""
        (self.workspace / ".gitignore").write_text("ignored_dir/\nnode_modules/\n", encoding="utf-8")
        (self.workspace / "app.js").write_text("console.log('hi');\n", encoding="utf-8")

        ignored_dir = self.workspace / "ignored_dir"
        ignored_dir.mkdir(parents=True, exist_ok=True)
        (ignored_dir / "config.json").write_text("{}", encoding="utf-8")

        node_dir = self.workspace / "node_modules"
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "pkg.json").write_text("{}", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn("app.js", captured)
        self.assertNotIn("ignored_dir/config.json", captured)
        self.assertNotIn("node_modules/pkg.json", captured)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        # Test staging explicitly including paths inside ignored directory
        changeset = self._make_changeset(
            "b1",
            1,
            ["app.js", ".gitignore", "ignored_dir/config.json", "node_modules/pkg.json"],
        )
        res = shadow.commit_changeset(changeset, summary="Initial baseline")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("app.js", committed)
        self.assertNotIn("ignored_dir/config.json", committed)
        self.assertNotIn("node_modules/pkg.json", committed)

    # -------------------------------------------------------------------------
    # Test D: Nested ignored directory
    # -------------------------------------------------------------------------
    def test_matrix_d_nested_ignored_directory_handled(self) -> None:
        """Deeply nested ignored directories must be pruned and safe from force-add."""
        (self.workspace / ".gitignore").write_text("src/**/build/\n*.tmp\n", encoding="utf-8")
        src_dir = self.workspace / "src" / "pkg" / "sub"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "index.py").write_text("print(1)\n", encoding="utf-8")

        build_dir = src_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "bundle.js").write_text("var x=1;\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn("src/pkg/sub/index.py", captured)
        self.assertNotIn("src/pkg/sub/build/bundle.js", captured)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        changeset = self._make_changeset(
            "b1",
            1,
            ["src/pkg/sub/index.py", "src/pkg/sub/build/bundle.js", ".gitignore"],
        )
        res = shadow.commit_changeset(changeset, summary="Nested test")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("src/pkg/sub/index.py", committed)
        self.assertNotIn("src/pkg/sub/build/bundle.js", committed)

    # -------------------------------------------------------------------------
    # Test E: .gitignore itself
    # -------------------------------------------------------------------------
    def test_matrix_e_gitignore_itself_captured(self) -> None:
        """.gitignore itself must be tracked, diffed, and committed to Shadow Git."""
        (self.workspace / ".gitignore").write_text("*.bak\n*.cache\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("# Test\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn(".gitignore", captured)
        self.assertEqual(captured[".gitignore"].replace("\r\n", "\n"), "*.bak\n*.cache\n")

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        changeset = self._make_changeset("b1", 1, list(captured.keys()))
        res = shadow.commit_changeset(changeset, summary="Commit .gitignore")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn(".gitignore", committed)
        self.assertIn("README.md", committed)

    # -------------------------------------------------------------------------
    # Test F: Newly created ignored files
    # -------------------------------------------------------------------------
    def test_matrix_f_newly_created_ignored_files_do_not_break_staging(self) -> None:
        """A new file matching .gitignore added after baseline must not cause fatal git staging errors."""
        (self.workspace / ".gitignore").write_text("*.log\noutput/\n", encoding="utf-8")
        (self.workspace / "app.py").write_text("print('running')\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        calc.capture_baseline(self.workspace)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())
        res1 = shadow.commit_changeset(
            self._make_changeset("b1", 1, ["app.py", ".gitignore"]),
            summary="Initial baseline",
        )
        self.assertTrue(res1.is_success)

        # Later, user script generates a log file and output directory
        (self.workspace / "debug.log").write_text("debug message\n", encoding="utf-8")
        out_dir = self.workspace / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.csv").write_text("a,b,c\n", encoding="utf-8")
        (self.workspace / "app.py").write_text("print('done')\n", encoding="utf-8")

        # Burst includes modified app.py along with the ignored debug.log and output/result.csv
        changeset = self._make_changeset(
            "b2",
            2,
            ["app.py", "debug.log", "output/result.csv"],
        )
        res2 = shadow.commit_changeset(changeset, summary="Second burst with ignored noise")
        self.assertTrue(res2.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("app.py", committed)
        self.assertNotIn("debug.log", committed)
        self.assertNotIn("output/result.csv", committed)

    # -------------------------------------------------------------------------
    # Test G: Binary files
    # -------------------------------------------------------------------------
    def test_matrix_g_binary_files_handled(self) -> None:
        """Non-ignored binary files must be captured in Shadow Git, while ignored binary files are skipped."""
        (self.workspace / ".gitignore").write_text("*.bin\n", encoding="utf-8")
        (self.workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
        (self.workspace / "dump.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        (self.workspace / "main.py").write_text("print('logo loaded')\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        # Text baseline cache excludes binary files
        self.assertNotIn("logo.png", captured)
        self.assertNotIn("dump.bin", captured)
        self.assertIn("main.py", captured)

        # Non-ignored binary files are tracked in _binary_files
        self.assertIn("logo.png", calc._binary_files)
        # Ignored binary files are NOT in _binary_files
        self.assertNotIn("dump.bin", calc._binary_files)

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        # Stage both text and binary files
        all_targets = list(captured.keys()) + list(calc._binary_files) + ["dump.bin"]
        changeset = self._make_changeset("b1", 1, all_targets)
        res = shadow.commit_changeset(changeset, summary="Commit binary and text")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("main.py", committed)
        self.assertIn("logo.png", committed)
        self.assertNotIn("dump.bin", committed)

    # -------------------------------------------------------------------------
    # Test H: Empty files
    # -------------------------------------------------------------------------
    def test_matrix_h_empty_files_captured(self) -> None:
        """Empty files (0 bytes) must be captured in baseline cache and Shadow Git."""
        (self.workspace / "empty.txt").write_text("", encoding="utf-8")
        (self.workspace / "__init__.py").write_text("", encoding="utf-8")
        (self.workspace / "app.py").write_text("import utils\n", encoding="utf-8")

        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)

        self.assertIn("empty.txt", captured)
        self.assertEqual(captured["empty.txt"], "")
        self.assertIn("__init__.py", captured)
        self.assertEqual(captured["__init__.py"], "")

        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        changeset = self._make_changeset("b1", 1, list(captured.keys()))
        res = shadow.commit_changeset(changeset, summary="Empty files baseline")
        self.assertTrue(res.is_success)

        committed = self._get_shadow_commit_files()
        self.assertIn("empty.txt", committed)
        self.assertIn("__init__.py", committed)
        self.assertIn("app.py", committed)

    # -------------------------------------------------------------------------
    # Test I: 2,000 files with mixture of ignored and non-ignored files
    # -------------------------------------------------------------------------
    def test_matrix_i_2000_mixed_files_scale(self) -> None:
        """2,000 files with a realistic mixture of ignored and non-ignored files must stage smoothly."""
        (self.workspace / ".gitignore").write_text("ignored_dir/\n*.tmp\n*.log\nbuild/\n", encoding="utf-8")

        ignored_dir = self.workspace / "ignored_dir"
        ignored_dir.mkdir(parents=True, exist_ok=True)
        build_dir = self.workspace / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        src_dir = self.workspace / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        # 1,000 non-ignored files
        for i in range(1000):
            (src_dir / f"mod_{i}.py").write_text(f"x_{i} = {i}\n", encoding="utf-8")

        # 500 ignored files in ignored_dir
        for i in range(500):
            (ignored_dir / f"cache_{i}.tmp").write_text(f"cache {i}\n", encoding="utf-8")

        # 300 ignored files in build_dir
        for i in range(300):
            (build_dir / f"artifact_{i}.log").write_text(f"log {i}\n", encoding="utf-8")

        # 200 ignored files matching *.tmp at root
        for i in range(200):
            (self.workspace / f"temp_{i}.tmp").write_text(f"temp {i}\n", encoding="utf-8")

        # Baseline capture
        t0 = time.time()
        calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        captured = calc.capture_baseline(self.workspace)
        t1 = time.time()

        # Must have exactly 1,000 src files + .gitignore = 1,001 captured files
        self.assertEqual(len(captured), 1001)
        self.assertIn(".gitignore", captured)
        self.assertIn("src/mod_0.py", captured)
        self.assertIn("src/mod_999.py", captured)
        self.assertNotIn("ignored_dir/cache_0.tmp", captured)
        self.assertNotIn("build/artifact_0.log", captured)
        self.assertNotIn("temp_0.tmp", captured)

        # Shadow Git initialization & staging
        shadow = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.assertTrue(shadow.init_repo())

        t2 = time.time()
        changeset = self._make_changeset("scale_burst", 1, list(captured.keys()))
        res = shadow.commit_changeset(changeset, summary="Scale baseline commit")
        t3 = time.time()

        self.assertTrue(res.is_success)
        self.assertIsNotNone(res.commit_hash)

        committed = self._get_shadow_commit_files()
        self.assertEqual(len(committed), 1001)
        self.assertIn("src/mod_0.py", committed)
        self.assertIn("src/mod_999.py", committed)

    # -------------------------------------------------------------------------
    # Full Integration: Production Worker Baseline Ingestion
    # -------------------------------------------------------------------------
    def test_production_worker_initial_ingestion_with_gitignore(self) -> None:
        """Full simulation of production worker initial baseline ingestion with .gitignore."""
        (self.workspace / ".gitignore").write_text(
            "node_modules/\n.venv/\n*.log\nsecrets/\n",
            encoding="utf-8",
        )
        (self.workspace / "app.py").write_text("def run(): print('app')\n", encoding="utf-8")
        (self.workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        (self.workspace / "empty.txt").write_text("", encoding="utf-8")

        sec_dir = self.workspace / "secrets"
        sec_dir.mkdir(parents=True, exist_ok=True)
        (sec_dir / "key.pem").write_text("SECRET KEY\n", encoding="utf-8")

        node_dir = self.workspace / "node_modules" / "express"
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "index.js").write_text("module.exports = {};\n", encoding="utf-8")

        state = WorkerState()
        state.target_path_str = str(self.workspace)
        state.session_db = SessionDB(self.db_path)
        state.session_id = state.session_db.create_session("test_proj", str(self.workspace))
        state.patches_dir = self.patches_dir
        state.diff_calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        state.diff_calc.capture_baseline(self.workspace)

        state.shadow_git = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        state.shadow_git.init_repo()

        # Trigger initial baseline ingestion burst
        trigger_initial_ingestion(state=state)

        # Verify SessionDB recorded the event
        events = state.session_db.get_events(state.session_id)
        self.assertTrue(len(events) >= 1)
        first_ev = events[0]
        self.assertEqual(first_ev["event_type"], "EDIT")

        # Verify Shadow Git micro-commit was created cleanly
        committed = self._get_shadow_commit_files()
        self.assertIn("app.py", committed)
        self.assertIn("empty.txt", committed)
        self.assertIn("logo.png", committed)
        self.assertIn(".gitignore", committed)

        # Verify ignored files were not committed
        self.assertNotIn("secrets/key.pem", committed)
        self.assertNotIn("node_modules/express/index.js", committed)


if __name__ == "__main__":
    unittest.main()
