"""
tests/test_v8_adaptive_polling_and_command_ai.py
================================================
Unit tests for Adaptive Fast Process Polling, PowerShell Headless Flag Detection,
and Command-Level AI Explanation in the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.shell_interceptor import (
    detect_executor,
    ExecutionTracker,
)
from scripts.event_bundler import EventBundler
from scripts.ai_analyzer import AISynthesizer
from scripts.storage_rotator import SessionDB
from scripts.web_server import EngineWebServer, _EngineRequestHandler


class MockProc:
    """Mock process object for psutil tree inspection."""

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
        self.info = {"pid": pid, "name": name, "cmdline": self._cmdline}

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return self._cmdline

    def parents(self) -> list[MockProc]:
        return self._parents


class TestAdaptiveFastPolling(unittest.TestCase):
    """Test ExecutionTracker adaptive fast polling rates and debounce triggers."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_poll_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_intervals(self) -> None:
        tracker = ExecutionTracker(target_dir=self.temp_dir)
        self.assertEqual(tracker.idle_interval, 1.2)
        self.assertEqual(tracker.fast_interval, 0.18)
        self.assertFalse(tracker.is_fast_polling)

    def test_trigger_fast_poll_duration(self) -> None:
        tracker = ExecutionTracker(target_dir=self.temp_dir)
        self.assertFalse(tracker.is_fast_polling)

        tracker.trigger_fast_poll(0.2)
        self.assertTrue(tracker.is_fast_polling)
        time.sleep(0.25)
        self.assertFalse(tracker.is_fast_polling)

    def test_is_active_callback(self) -> None:
        active_state = False
        tracker = ExecutionTracker(
            target_dir=self.temp_dir,
            is_active_callback=lambda: active_state,
        )
        self.assertFalse(tracker.is_fast_polling)
        active_state = True
        self.assertTrue(tracker.is_fast_polling)
        active_state = False
        self.assertFalse(tracker.is_fast_polling)

    def test_event_bundler_activity_accelerates_tracker(self) -> None:
        tracker = ExecutionTracker(target_dir=self.temp_dir)
        self.assertFalse(tracker.is_fast_polling)

        bundler = EventBundler(
            quiet_period=1.0,
            on_activity=lambda: tracker.trigger_fast_poll(0.3),
        )
        # Add event to trigger bundler on_activity
        bundler.add_event("modified", "test.py")
        self.assertTrue(tracker.is_fast_polling)
        bundler.stop()


class TestPowerShellHeadlessFlagDetection(unittest.TestCase):
    """Test PowerShell headless flag detection and AI IDE parent process classification."""

    def test_powershell_and_cmd_headless_flags(self) -> None:
        ai_commands = [
            'powershell -Command "Get-Process"',
            'powershell.exe -command "Get-Service"',
            'pwsh -Command "dir"',
            'pwsh.exe -c "ls"',
            'powershell -encodedcommand VwByAGkAdABlAC0ASABvAHMAdAA=',
            'powershell.exe -file ./build.ps1',
            'pwsh -file scripts/deploy.ps1',
            'cmd.exe /c dir',
            '/c echo test',
            'cmd.exe /c "npm run test"',
        ]
        for cmd in ai_commands:
            self.assertEqual(
                detect_executor(cmd),
                "AI_AGENT",
                f"Expected AI_AGENT for command: {cmd}",
            )

    def test_human_interactive_commands(self) -> None:
        human_commands = [
            'git status',
            'git commit -m "feat: implement feature"',
            'git diff HEAD~1',
            'npm test',
            'npm run build',
            'python main.py',
            'dir',
            'pytest tests/',
        ]
        for cmd in human_commands:
            self.assertEqual(
                detect_executor(cmd),
                "HUMAN_DEV",
                f"Expected HUMAN_DEV for command: {cmd}",
            )

    def test_ai_ide_parent_classification(self) -> None:
        """Commands executed under an Antigravity parent process must be classified as AI_AGENT."""
        antigravity_parent = MockProc(
            name="antigravity.exe",
            exe="C:\\Users\\User\\AppData\\Local\\Programs\\Antigravity\\antigravity.exe",
            cmdline_args=["antigravity.exe"],
        )
        child_cmd_proc = MockProc(
            name="powershell.exe",
            exe="C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            cmdline_args=["powershell.exe"],
            parents_list=[antigravity_parent],
        )

        # Even with interactive command text, parent process tree marks it as AI_AGENT
        res = detect_executor("git status", proc=child_cmd_proc, ide_profile="antigravity")
        self.assertEqual(res, "AI_AGENT")

        res_npm = detect_executor("npm test", proc=child_cmd_proc, ide_profile="antigravity")
        self.assertEqual(res_npm, "AI_AGENT")


class TestAISynthesizerExplainCommand(unittest.TestCase):
    """Test AISynthesizer explain_command and heuristic fallback."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_ai_"))
        self.syn = AISynthesizer(engine_root=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_heuristic_fallback_git_status(self) -> None:
        result = self.syn._fallback_command_explanation(
            command_str="git status",
            exit_code=0,
            duration_s=0.15,
        )
        self.assertIn("intent", result)
        self.assertIn("technical_purpose", result)
        self.assertIn("outcome_analysis", result)
        self.assertIn("summary", result)
        self.assertIn("Inspect", result["intent"])
        self.assertIn("exit code 0", result["outcome_analysis"])
        self.assertTrue(len(result["summary"]) >= 1)

    def test_heuristic_fallback_failure_exit(self) -> None:
        result = self.syn._fallback_command_explanation(
            command_str="pytest",
            exit_code=1,
            duration_s=1.45,
        )
        self.assertIn("non-zero", result["outcome_analysis"])
        self.assertIn("exit code 1", result["outcome_analysis"])

    def test_explain_command_heuristic_mode(self) -> None:
        res = self.syn.explain_command(
            project_name="test_proj",
            command_str="python -c 'import sys; print(sys.version)'",
            exit_code=0,
            duration_s=0.12,
            provider="heuristic",
        )
        self.assertEqual(res["provider"], "heuristic")
        self.assertIn("Python", res["intent"])
        self.assertIn("technical_purpose", res)
        self.assertIn("outcome_analysis", res)

        # Test caching on second call
        res_cached = self.syn.explain_command(
            project_name="test_proj",
            command_str="python -c 'import sys; print(sys.version)'",
            exit_code=0,
            duration_s=0.12,
            provider="heuristic",
        )
        self.assertEqual(res_cached["diff_hash"], res["diff_hash"])


class TestWebServerAnalyzeExecEndpoint(unittest.TestCase):
    """Test REST API endpoint POST /api/project/<name>/analyze-exec/<event_id>."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_web_exec_test_"))
        self.engine_root = self.temp_dir / ".spd"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.proj_dir = self.engine_root / "current" / "demo_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.proj_dir / "session.db"
        self.db = SessionDB(self.db_path)
        self.sid = self.db.create_session("demo_proj", str(self.temp_dir))
        self.ev_id = self.db.record_event(
            session_id=self.sid,
            event_type="EXEC",
            first_file="git commit -m 'Initial commit'",
            summary="[AI_AGENT] cmd: 'git commit -m Initial commit' | exit=0 | duration=0.20s",
            executor="AI_AGENT",
        )

        self.server = EngineWebServer(engine_root=self.engine_root, port=0)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_handle_api_analyze_exec(self) -> None:
        # Mock request handler
        handler = MagicMock(spec=_EngineRequestHandler)
        handler.server_instance = self.server
        handler._resolve_db_path = lambda slug: self.db_path

        sent_json = {}
        def mock_send_json(data: dict) -> None:
            nonlocal sent_json
            sent_json = data

        handler._send_json = mock_send_json

        # Invoke handler with provider='heuristic'
        _EngineRequestHandler._handle_api_analyze_exec(
            handler,
            project_name="demo_proj",
            event_id_str=str(self.ev_id),
            body={"provider": "heuristic"},
        )

        self.assertTrue(sent_json.get("success"))
        self.assertEqual(sent_json.get("event_id"), self.ev_id)
        ai_summary = sent_json.get("ai_summary", {})
        self.assertIn("intent", ai_summary)
        self.assertIn("technical_purpose", ai_summary)
        self.assertIn("outcome_analysis", ai_summary)

        # Verify persisted in database
        cur = self.db.conn.cursor()
        cur.execute("SELECT ai_summary FROM events WHERE id = ?", (self.ev_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row["ai_summary"])
        db_ai = json.loads(row["ai_summary"])
        self.assertIn("intent", db_ai)


if __name__ == "__main__":
    unittest.main()
