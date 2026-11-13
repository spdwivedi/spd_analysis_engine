"""
tests/test_v7_ide_profiles.py
=============================
Unit tests for AI IDE Process Isolation, IDE Profile Configuration,
and Human vs. AI Action Discrimination in the SPD Analysis Engine.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.ide_profiles import (
    IDE_PROFILES,
    get_profile,
    _is_native_vscode,
    is_process_whitelisted,
)
from scripts.shell_interceptor import (
    detect_executor,
    ExecutionTracker,
)
from scripts.watcher_proc import ProcessInspector
from scripts.storage_rotator import (
    SessionDB,
    update_project_meta,
    read_project_meta,
    consolidate_project_history,
)
from scripts.git_shadow import ensure_hardened_gitignore
from scripts.event_bundler import is_path_suppressed
from scripts.web_server import EngineWebServer


class MockProc:
    """Mock psutil.Process with name, exe, cmdline, and parents hierarchy."""

    def __init__(
        self,
        name: str = "python.exe",
        exe: str = "C:\\Python\\python.exe",
        cmdline_args: list[str] | None = None,
        parents_list: list[MockProc] | None = None,
        pid: int = 1234,
    ):
        self._name = name
        self._exe = exe
        self._cmdline = cmdline_args or []
        self._parents = parents_list or []
        self.pid = pid
        self.info = {"pid": pid, "name": name}

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return self._cmdline

    def parents(self) -> list[MockProc]:
        return self._parents


class TestIDEProfiles(unittest.TestCase):
    """Test IDE profile lookup and process ancestry whitelisting."""

    def test_get_profile(self) -> None:
        anti = get_profile("antigravity")
        self.assertEqual(anti["label"], "Google Antigravity")
        self.assertIn("antigravity.exe", anti["executables"])

        cursor = get_profile("cursor")
        self.assertEqual(cursor["label"], "Cursor")

        windsurf = get_profile("windsurf")
        self.assertEqual(windsurf["label"], "Windsurf")

        claude = get_profile("claude_code")
        self.assertEqual(claude["label"], "Claude Code")

        # Fallback
        unknown = get_profile("non_existent_ide")
        self.assertEqual(unknown["label"], "Google Antigravity")

    def test_native_vscode_rejection(self) -> None:
        """Native Microsoft VS Code must be detected and rejected."""
        vscode_proc = MockProc(
            name="Code.exe",
            exe="C:\\Users\\User\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
            cmdline_args=["Code.exe", "."],
            parents_list=[MockProc("explorer.exe", "C:\\Windows\\explorer.exe")],
        )
        self.assertTrue(_is_native_vscode(vscode_proc.name(), vscode_proc.exe(), " ".join(vscode_proc.cmdline())))
        # Whitelist should reject native VS Code
        self.assertFalse(is_process_whitelisted(vscode_proc, "antigravity"))
        self.assertFalse(is_process_whitelisted(vscode_proc, "cursor"))

    def test_authorized_antigravity_process(self) -> None:
        """Processes matching the active profile should be whitelisted."""
        anti_proc = MockProc(
            name="antigravity.exe",
            exe="C:\\Program Files\\Google\\Antigravity\\antigravity.exe",
            cmdline_args=["antigravity.exe"],
        )
        self.assertTrue(is_process_whitelisted(anti_proc, "antigravity"))
        # But not under cursor profile
        self.assertFalse(is_process_whitelisted(anti_proc, "cursor"))

    def test_vscode_child_of_authorized_ai_is_accepted(self) -> None:
        """If Code.exe is spawned as an internal child of an authorized AI IDE, accept it."""
        ai_parent = MockProc(
            name="Antigravity.exe",
            exe="C:\\Google\\Antigravity\\Antigravity.exe",
            cmdline_args=["Antigravity.exe"],
        )
        vscode_child = MockProc(
            name="Code.exe",
            exe="C:\\Users\\User\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
            cmdline_args=["Code.exe"],
            parents_list=[ai_parent],
        )
        self.assertTrue(is_process_whitelisted(vscode_child, "antigravity"))

    def test_custom_profile_and_marker(self) -> None:
        custom_proc = MockProc(
            name="my_custom_agent.exe",
            exe="D:\\tools\\my_custom_agent.exe",
            cmdline_args=["my_custom_agent.exe", "--run"],
        )
        # Using custom marker
        self.assertTrue(is_process_whitelisted(custom_proc, "custom", custom_marker="my_custom_agent"))
        self.assertFalse(is_process_whitelisted(custom_proc, "custom", custom_marker="other_agent"))


class TestWatcherProcInspector(unittest.TestCase):
    """Test that ProcessInspector has decommissioned anonymous fs_atime inspection."""

    def test_no_fs_atime_inspector(self) -> None:
        inspector = ProcessInspector(target_path="D:\\test", ide_profile="antigravity")
        # Ensure _inspect_access_times_once does NOT exist
        self.assertFalse(hasattr(inspector, "_inspect_access_times_once"))
        self.assertFalse(hasattr(inspector, "_known_atimes"))

    def test_process_inspector_whitelisting_filter(self) -> None:
        """ProcessInspector only queries open files on whitelisted processes."""
        with tempfile.TemporaryDirectory() as td:
            reads_recorded = []
            inspector = ProcessInspector(
                target_path=td,
                on_file_read=lambda r: reads_recorded.append(r),
                ide_profile="antigravity",
            )

            vscode_proc = MockProc(
                name="Code.exe",
                exe="C:\\Users\\User\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
                cmdline_args=["Code.exe"],
                parents_list=[MockProc("explorer.exe", "C:\\Windows\\explorer.exe")],
                pid=9999,
            )
            # Patch psutil.process_iter to return only native VS Code
            with patch("psutil.process_iter", return_value=[vscode_proc]):
                inspector._inspect_once()
                # Should be empty because VS Code was rejected
                self.assertEqual(len(reads_recorded), 0)
                self.assertEqual(len(inspector._recent_reads), 0)


class TestHumanVsAIExecutor(unittest.TestCase):
    """Test classification of execution commands into AI_AGENT vs HUMAN_DEV."""

    def test_detect_ai_agent_headless_flags(self) -> None:
        ai_commands = [
            'python -c "import os; print(os.getcwd())"',
            'node -e "console.log(process.pid)"',
            'python -m py_compile scripts/watcher_proc.py',
            'python -m unittest discover tests',
            'npx eslint --fix src/app.js',
            'npx prettier --write .',
            'node --check server.js',
            'python scratch/temp_eval.py',
            'python eval_script.py',
            'bash /tmp/agent_test.sh',
        ]
        for cmd in ai_commands:
            self.assertEqual(detect_executor(cmd), "AI_AGENT", f"Expected AI_AGENT for: {cmd}")

    def test_detect_human_dev_commands(self) -> None:
        human_commands = [
            'git status',
            'git commit -m "feat: updates"',
            'npm run dev',
            'npm start',
            'python main.py',
            'dir',
            'ls -la',
            'pytest',
            'pip install -r requirements.txt',
        ]
        for cmd in human_commands:
            self.assertEqual(detect_executor(cmd), "HUMAN_DEV", f"Expected HUMAN_DEV for: {cmd}")


class TestDBExecutorPersistence(unittest.TestCase):
    """Test SQLite events table executor column and persistence."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_db_test_"))
        self.db_path = self.temp_dir / "session.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_and_read_executor(self) -> None:
        db = SessionDB(self.db_path)
        sid = db.create_session("test_proj", str(self.temp_dir))

        # Check column exists
        cur = db.conn.cursor()
        cur.execute("PRAGMA table_info(events)")
        cols = [r["name"] for r in cur.fetchall()]
        self.assertIn("executor", cols)

        # Record AI execution
        ev1 = db.record_event(
            session_id=sid,
            event_type="EXEC",
            first_file="python -c 'print(1)'",
            summary="[AI_AGENT] Automated check",
            executor="AI_AGENT",
        )

        # Record Human execution
        ev2 = db.record_event(
            session_id=sid,
            event_type="EXEC",
            first_file="git status",
            summary="[HUMAN_DEV] Terminal command",
            executor="HUMAN_DEV",
        )

        cur.execute("SELECT id, executor FROM events ORDER BY id ASC")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["executor"], "AI_AGENT")
        self.assertEqual(rows[1]["executor"], "HUMAN_DEV")
        db.close()

    def test_consolidate_preserves_executor(self) -> None:
        """Consolidating project history preserves the executor column."""
        source_dir = self.temp_dir / "history" / "my_proj" / "run_1"
        source_dir.mkdir(parents=True)
        src_db = SessionDB(source_dir / "session.db")
        sid = src_db.create_session("my_proj", str(self.temp_dir))
        src_db.record_event(
            session_id=sid,
            event_type="EXEC",
            first_file="node -e '1'",
            summary="exec",
            executor="AI_AGENT",
        )
        src_db.close()

        consolidated_path = consolidate_project_history(self.temp_dir, "my_proj")
        self.assertIsNotNone(consolidated_path)
        self.assertTrue(consolidated_path.exists())

        c_db = SessionDB(consolidated_path)
        cur = c_db.conn.cursor()
        cur.execute("SELECT executor FROM events WHERE event_type = 'EXEC'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["executor"], "AI_AGENT")
        c_db.close()


class TestScaffoldingSuppression(unittest.TestCase):
    """Test engine scaffolding silence bypass for .gitignore and project.meta."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_suppress_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_gitignore_is_suppressed(self) -> None:
        ensure_hardened_gitignore(self.temp_dir)
        gi_path = str(self.temp_dir / ".gitignore")
        self.assertTrue(is_path_suppressed(gi_path))

    def test_update_project_meta_is_suppressed(self) -> None:
        update_project_meta(self.temp_dir, "test_proj", target_path=str(self.temp_dir), ide_profile="antigravity")
        meta_path = str(self.temp_dir / "current" / "test_proj" / "project.meta")
        self.assertTrue(is_path_suppressed(meta_path))
        meta = read_project_meta(self.temp_dir, "test_proj")
        self.assertEqual(meta.get("ide_profile"), "antigravity")


class TestWebServerTokenScrubbing(unittest.TestCase):
    """Test web server event reader token scrubbing and executor inclusion."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_web_test_"))
        self.db_path = self.temp_dir / "session.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_events_from_db_scrubs_and_includes_executor(self) -> None:
        server = EngineWebServer(self.temp_dir)
        db = SessionDB(self.db_path)
        sid = db.create_session("test_proj", str(self.temp_dir))
        db.record_event(
            session_id=sid,
            event_type="EXEC",
            first_file="curl -H 'Authorization: Bearer ghp_ABC12345678901234567890123456789012' https://example.com",
            summary="[AI_AGENT] Key AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv used",
            executor="AI_AGENT",
        )
        db.close()

        events = server._read_events_from_db(self.db_path)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["executor"], "AI_AGENT")
        self.assertNotIn("ghp_ABC12345678901234567890123456789012", ev["first_file_touched"])
        self.assertIn("ghp_***", ev["first_file_touched"])
        self.assertNotIn("AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv", ev["summary"])
        self.assertIn("AIza***", ev["summary"])


if __name__ == "__main__":
    unittest.main()
