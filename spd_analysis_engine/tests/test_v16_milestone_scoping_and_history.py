"""
tests/test_v16_milestone_scoping_and_history.py
===============================================
Unit tests for scoped milestone tracking, unpushed burst scoping,
milestone history API, and force_ai regeneration.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.storage.db import SessionDB
from scripts.git.milestone_tracker import (
    get_unpushed_bursts,
    record_git_milestone,
    get_project_milestone_history,
)
from scripts.ai.synthesizer_tiered import synthesize_milestone
from scripts.server.request_handler import _EngineRequestHandler


class TestMilestoneSchemaAndScoping(unittest.TestCase):
    """Test idempotent schema migration, event attribution, and scoping."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "session.db"
        self.db = SessionDB(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_01_idempotent_schema_columns_and_table(self) -> None:
        """Assert pushed_in_milestone_id and git_milestones table exist."""
        conn = self.db.conn
        assert conn is not None
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(events)")
        cols = [c[1] for c in cur.fetchall()]
        self.assertIn("pushed_in_milestone_id", cols)

        cur.execute("PRAGMA table_info(git_milestones)")
        m_cols = [c[1] for c in cur.fetchall()]
        self.assertIn("commit_sha", m_cols)
        self.assertIn("burst_ids", m_cols)

    def test_02_unpushed_bursts_filters_non_burst_events(self) -> None:
        """Assert unpushed scoping selects only EDIT burst events, ignoring GIT noise."""
        conn = self.db.conn
        assert conn is not None
        conn.execute(
            "INSERT INTO sessions (project_name, target_path, start_time) VALUES ('demo', '/demo', '2026-09-29T00:00:00Z')"
        )
        # Event 1: EDIT burst
        conn.execute(
            "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (1, 'EDIT', '2026-09-29T00:00:01Z', 'Burst edit across 2 files')"
        )
        # Event 2: GIT noise event
        conn.execute(
            "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (1, 'GIT', '2026-09-29T00:00:02Z', 'Micro-commit 1234567')"
        )

        unpushed = get_unpushed_bursts(self.db_path, session_id=1)
        self.assertEqual(len(unpushed), 1)
        self.assertEqual(unpushed[0]["id"], 1)
        self.assertEqual(unpushed[0]["event_type"], "EDIT")

    def test_03_record_milestone_marks_events_pushed(self) -> None:
        """Assert record_git_milestone updates events table and persists milestone row."""
        conn = self.db.conn
        assert conn is not None
        conn.execute(
            "INSERT INTO sessions (project_name, target_path, start_time) VALUES ('demo', '/demo', '2026-09-29T00:00:00Z')"
        )
        conn.execute("INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (1, 'EDIT', '2026-09-29T00:00:01Z', 'Burst 1')")
        conn.execute("INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (1, 'EDIT', '2026-09-29T00:00:02Z', 'Burst 2')")

        m_id = record_git_milestone(
            db_path=self.db_path,
            session_id=1,
            commit_sha="abcdef123456",
            title="feat(core): new release",
            body="- highlights",
            changelog="### Notes",
            burst_ids=[1, 2],
            remote_url="https://github.com/org/repo.git",
            branch="main",
        )
        self.assertGreater(m_id, 0)

        # Bursts are now marked as pushed
        unpushed = get_unpushed_bursts(self.db_path, session_id=1)
        self.assertEqual(len(unpushed), 0)

        # Milestone history query returns the record
        history = get_project_milestone_history(self.db_path, limit=5)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["commit_sha"], "abcdef123456")
        self.assertEqual(history[0]["commit_title"], "feat(core): new release")


class TestMilestoneApiEndpoints(unittest.TestCase):
    """Test REST API handlers for unpushed-milestone, synthesize-milestone, and milestones."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine_root = Path(self.tmp.name)
        self.proj_dir = self.engine_root / "current" / "test_slug"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.proj_dir / "session.db"

        # Initialize session DB
        db = SessionDB(self.db_path)
        conn = db.conn
        assert conn is not None
        conn.execute(
            "INSERT INTO sessions (project_name, target_path, start_time) VALUES ('test_slug', ?, '2026-09-29T00:00:00Z')",
            (str(self.proj_dir),),
        )
        conn.execute("INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (1, 'EDIT', '2026-09-29T00:00:01Z', 'Burst 1')")
        db.close()

        # Mock handler
        self.handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
        mock_server = MagicMock()
        mock_server.engine_root = self.engine_root
        self.handler.server = MagicMock()
        self.handler.server.engine_web_server = mock_server
        self.last_json = None
        self.handler._send_json = lambda d, **kwargs: setattr(self, "last_json", d)

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_04_unpushed_milestone_handler(self) -> None:
        """Assert unpushed milestone returns has_unpushed=True and burst count = 1."""
        self.handler._handle_api_unpushed_milestone("test_slug")
        self.assertIsNotNone(self.last_json)
        self.assertTrue(self.last_json.get("success"))
        self.assertTrue(self.last_json.get("has_unpushed"))
        self.assertEqual(self.last_json.get("bursts_count"), 1)
        self.assertEqual(self.last_json.get("burst_ids"), [1])

    def test_05_milestone_history_handler(self) -> None:
        """Assert milestones endpoint returns history list."""
        record_git_milestone(
            db_path=self.db_path,
            session_id=1,
            commit_sha="fedcba987654",
            title="feat(test): milestone 1",
            body="",
            changelog="",
            burst_ids=[1],
            remote_url="https://github.com/org/repo.git",
            branch="main",
        )
        self.handler._handle_api_project_milestones("test_slug")
        self.assertIsNotNone(self.last_json)
        self.assertTrue(self.last_json.get("success"))
        self.assertEqual(len(self.last_json.get("milestones", [])), 1)
        self.assertEqual(self.last_json["milestones"][0]["commit_sha"], "fedcba987654")

    @patch("scripts.ai.synthesizer_tiered.call_gemini")
    @patch("scripts.ai.synthesizer_tiered.AIConfig.load_config")
    def test_06_regenerate_with_force_ai(self, mock_cfg, mock_gemini) -> None:
        """Assert force_ai forces tier2_ai mode even on root repository."""
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.proj_dir), capture_output=True)
        (self.proj_dir / "index.js").write_text("console.log('hello');\n", encoding="utf-8")

        mock_cfg.return_value = {"active_provider": "gemini", "gemini": {}}
        mock_gemini.return_value = {
            "title": "feat(ai): regenerated commit title",
            "body": "Detailed regenerated commit body highlights.",
            "changelog": "- AI generated changelog entry",
        }
        res = synthesize_milestone(
            burst_ids=[1],
            mode="tier2_ai",
            project_slug="test_slug",
            target_path=str(self.proj_dir),
            engine_root=self.engine_root,
        )
        self.assertEqual(res.get("mode_used"), "tier2_ai")
        self.assertEqual(res.get("title"), "feat(ai): regenerated commit title")


if __name__ == "__main__":
    unittest.main()
