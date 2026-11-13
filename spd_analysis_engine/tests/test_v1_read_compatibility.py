"""
tests/test_v1_read_compatibility.py
===================================
Comprehensive test suite verifying read compatibility with legacy v1 databases,
modern databases, migrated databases, empty databases, and malformed databases.

Verifies:
- GET requests against pure legacy v1 databases succeed without 'no such column: e.event_id'
- GET requests against legacy databases produce ZERO filesystem mutations
- GET requests against legacy databases produce ZERO SQLite writes or schema alterations
- Migrated v1 database remains correct with stable canonical event_id
- Second migration remains an idempotent no-op
- Empty legacy database handled gracefully
- Malformed legacy database handled gracefully without unhandled crashes
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from scripts.server.request_handler import _EngineRequestHandler
from scripts.server.server_daemon import EngineWebServer
from scripts.storage.compatibility_reader import (
    get_table_columns,
    parse_diff_stats,
    read_event_detail,
    read_event_file_diff,
    read_events_summary,
    resolve_event_row,
)
from scripts.storage.schema_migration import (
    CURRENT_SCHEMA_VERSION,
    apply_schema_migrations,
    get_user_version,
)


class TestV1ReadCompatibility(unittest.TestCase):
    """Test suite for legacy v1 read compatibility across all storage scenarios."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.engine_root = Path(self.tmp_dir.name).resolve()
        (self.engine_root / "current").mkdir(parents=True, exist_ok=True)
        (self.engine_root / "last_run").mkdir(parents=True, exist_ok=True)
        (self.engine_root / "history").mkdir(parents=True, exist_ok=True)
        (self.engine_root / "data" / "projects").mkdir(parents=True, exist_ok=True)

        self.server = EngineWebServer(self.engine_root, port=0)

    def tearDown(self) -> None:
        try:
            self.server.close()
        except Exception:
            pass
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def _create_mock_handler(self) -> tuple[_EngineRequestHandler, dict[str, Any]]:
        """Instantiate mock request handler capturing JSON responses and errors."""
        handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
        handler.server = MagicMock()
        handler.server.engine_web_server = self.server
        response_capture: dict[str, Any] = {"json": None, "error": None, "status": None}

        def mock_send_json(data: Any, status: int = 200) -> None:
            response_capture["json"] = data
            response_capture["status"] = status

        def mock_send_error(message: str, status: int = 400) -> None:
            response_capture["error"] = message
            response_capture["status"] = status

        handler._send_json = mock_send_json
        handler._send_error = mock_send_error
        return handler, response_capture

    def _create_pure_v1_database(self, db_path: Path, num_events: int = 5) -> None:
        """Create a pure legacy v1 database without event_id, seq, adds, dels columns."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
        CREATE TABLE sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT    NOT NULL,
            target_path  TEXT    NOT NULL,
            start_time   TEXT    NOT NULL,
            end_time     TEXT,
            status       TEXT    NOT NULL DEFAULT 'ACTIVE'
        );
        CREATE TABLE events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id         INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            event_type         TEXT    NOT NULL,
            timestamp          TEXT    NOT NULL,
            first_file_touched TEXT,
            summary            TEXT,
            ai_summary         TEXT,
            executor           TEXT,
            burst_num          INTEGER,
            commit_hash        TEXT,
            is_rolled_back     INTEGER DEFAULT 0
        );
        CREATE TABLE patches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            file_path    TEXT    NOT NULL,
            diff_content TEXT
        );
        """)
        conn.execute(
            "INSERT INTO sessions (id, project_name, target_path, start_time, status) "
            "VALUES (1, 'legacy_proj', '/workspace/legacy', '2026-09-01T10:00:00Z', 'ACTIVE')"
        )
        for i in range(1, num_events + 1):
            conn.execute(
                "INSERT INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, burst_num) "
                "VALUES (?, 1, 'EDIT', ?, ?, ?, ?)",
                (i, f"2026-09-01T10:0{i}:00Z", f"file_{i}.py", f"Burst summary {i}", i),
            )
            # Patch with 2 additions and 1 deletion
            diff = f"--- a/file_{i}.py\n+++ b/file_{i}.py\n@@ -1,2 +1,3 @@\n-old line\n+new line 1\n+new line 2\n"
            conn.execute(
                "INSERT INTO patches (id, event_id, file_path, diff_content) VALUES (?, ?, ?, ?)",
                (i, i, f"file_{i}.py", diff),
            )
        conn.commit()
        conn.close()

    def _file_sha256(self, path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_legacy_v1_database_read_succeeds_and_produces_no_mutations(self) -> None:
        """
        Verify GET /events and GET /events/<id> against legacy v1 databases:
        - Succeeds without raising 'no such column: e.event_id'
        - Produces zero filesystem mutations
        - Produces zero SQLite writes or schema additions
        """
        slug = "legacy_test_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text(
            f"project_name={slug}\ntarget_path=/workspace/{slug}\n",
            encoding="utf-8",
        )
        db_path = proj_dir / "session.db"
        self._create_pure_v1_database(db_path, num_events=4)

        # Confirm user_version is 0 and event_id column is absent
        conn_check = sqlite3.connect(str(db_path))
        self.assertEqual(get_user_version(conn_check), 0)
        cur = conn_check.cursor()
        cur.execute("PRAGMA table_info(events);")
        cols = {r[1] for r in cur.fetchall()}
        self.assertNotIn("event_id", cols)
        self.assertNotIn("seq", cols)
        self.assertNotIn("burst_uid", cols)
        conn_check.close()

        # Capture baseline state
        before_hash = self._file_sha256(db_path)
        before_size = db_path.stat().st_size
        before_files = sorted(p.name for p in proj_dir.iterdir())

        # 1. Execute GET /events?project=legacy_test_proj
        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug], "limit": ["10"]})

        self.assertEqual(res["status"], 200)
        self.assertIsNone(res["error"])
        events_data = res["json"]["events"]
        self.assertEqual(len(events_data), 4)
        first_ev = events_data[0]
        self.assertEqual(first_ev["id"], 1)
        # Verify deterministic stable compatibility ID
        self.assertEqual(first_ev["event_id"], "ev_1")
        self.assertEqual(first_ev["files_count"], 1)
        self.assertEqual(first_ev["additions"], 2)
        self.assertEqual(first_ev["deletions"], 1)
        self.assertNotIn("diff_content", first_ev)

        # 2. Execute GET /events/1?project=legacy_test_proj
        handler2, res2 = self._create_mock_handler()
        handler2._handle_api_event_detail("1", {"project": [slug]})

        self.assertEqual(res2["status"], 200)
        self.assertEqual(res2["json"]["event"]["id"], 1)
        self.assertEqual(res2["json"]["event"]["event_id"], "ev_1")
        self.assertEqual(len(res2["json"]["files"]), 1)
        file_meta = res2["json"]["files"][0]
        self.assertEqual(file_meta["file_path"], "file_1.py")
        self.assertEqual(file_meta["additions"], 2)
        self.assertEqual(file_meta["deletions"], 1)
        self.assertNotIn("diff_content", file_meta)

        # 3. Resolve by canonical legacy identifier evt_legacy_1
        handler3, res3 = self._create_mock_handler()
        handler3._handle_api_event_detail("evt_legacy_1", {"project": [slug]})
        self.assertEqual(res3["status"], 200)
        self.assertEqual(res3["json"]["event"]["id"], 1)

        # 4. Resolve by alias ev_1
        handler4, res4 = self._create_mock_handler()
        handler4._handle_api_event_detail("ev_1", {"project": [slug]})
        self.assertEqual(res4["status"], 200)
        self.assertEqual(res4["json"]["event"]["id"], 1)

        # 5. Execute GET /events/1/files/file_1.py/diff?project=legacy_test_proj
        handler5, res5 = self._create_mock_handler()
        handler5._handle_api_event_file_diff("1", "file_1.py", {"project": [slug]})
        self.assertEqual(res5["status"], 200)
        self.assertEqual(res5["json"]["file_path"], "file_1.py")
        self.assertIn("+new line 1", res5["json"]["diff_content"])
        self.assertEqual(res5["json"]["additions"], 2)
        self.assertEqual(res5["json"]["deletions"], 1)

        # 6. Execute GET /api/projects/legacy_test_proj/events
        handler6, res6 = self._create_mock_handler()
        handler6._handle_api_events(slug)
        self.assertEqual(res6["status"], 200)
        self.assertEqual(len(res6["json"]["events"]), 4)
        self.assertEqual(res6["json"]["events"][0]["event_id"], "ev_1")

        # 7. Execute GET /api/projects/legacy_test_proj/sessions
        handler7, res7 = self._create_mock_handler()
        handler7._handle_api_sessions(slug)
        self.assertEqual(res7["status"], 200)
        self.assertEqual(len(res7["json"]["sessions"]), 1)

        # VERIFY STRICT ZERO-MUTATION INVARIANTS:
        # A. File hash must be byte-for-byte identical
        after_hash = self._file_sha256(db_path)
        self.assertEqual(before_hash, after_hash, "GET request mutated session.db content")

        # B. File size must be identical
        after_size = db_path.stat().st_size
        self.assertEqual(before_size, after_size, "GET request altered session.db size")

        # C. Directory listing must not contain WAL, SHM, temporary files
        after_files = sorted(p.name for p in proj_dir.iterdir() if not p.name.endswith("-shm"))
        self.assertEqual(before_files, after_files, "GET request created unexpected files in project dir")

        # D. Schema must NOT have been migrated
        conn_after = sqlite3.connect(str(db_path))
        self.assertEqual(get_user_version(conn_after), 0, "GET request modified user_version")
        cur_after = conn_after.cursor()
        cur_after.execute("PRAGMA table_info(events);")
        cols_after = {r[1] for r in cur_after.fetchall()}
        self.assertNotIn("event_id", cols_after, "GET request added column to events table")
        conn_after.close()

    def test_current_schema_database_read_succeeds(self) -> None:
        """Verify modern fully-migrated database reads correctly through compatibility layer."""
        slug = "modern_test_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text(f"project_name={slug}\ntarget_path=/workspace/{slug}\n", encoding="utf-8")
        db_path = proj_dir / "session.db"

        # Initialize with modern schema
        conn = sqlite3.connect(str(db_path))
        apply_schema_migrations(conn)
        self.assertEqual(get_user_version(conn), CURRENT_SCHEMA_VERSION)
        conn.execute("INSERT INTO sessions (id, project_name, target_path, start_time) VALUES (1, ?, '/workspace', '2026-10-01T12:00:00Z')", (slug,))
        conn.execute(
            "INSERT INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, event_id, seq, burst_uid) "
            "VALUES (1, 1, 'EDIT', '2026-10-01T12:00:00Z', 'app.py', 'Modern burst', 'evt_mod_001', 1, 'buid_001')"
        )
        conn.execute(
            "INSERT INTO patches (id, event_id, file_path, diff_content, adds, dels, kind, size) "
            "VALUES (1, 1, 'app.py', '@@ -1 +1 @@\n-a\n+b', 1, 1, 'unified', 20)"
        )
        conn.commit()
        conn.close()

        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug]})

        self.assertEqual(res["status"], 200)
        self.assertEqual(len(res["json"]["events"]), 1)
        ev = res["json"]["events"][0]
        self.assertEqual(ev["event_id"], "evt_mod_001")
        self.assertEqual(ev["seq"], 1)
        self.assertEqual(ev["burst_uid"], "buid_001")
        self.assertEqual(ev["additions"], 1)
        self.assertEqual(ev["deletions"], 1)

        # Resolve by canonical ID
        handler2, res2 = self._create_mock_handler()
        handler2._handle_api_event_detail("evt_mod_001", {"project": [slug]})
        self.assertEqual(res2["status"], 200)
        self.assertEqual(res2["json"]["event"]["event_id"], "evt_mod_001")

    def test_migrated_v1_database_remains_correct_and_second_migration_is_noop(self) -> None:
        """
        Verify:
        - Explicit migration of a v1 database brings it to CURRENT_SCHEMA_VERSION
        - Legacy events receive stable evt_legacy_<id>
        - Migrated database reads accurately
        - Second migration is an idempotent no-op
        """
        slug = "migrated_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text(f"project_name={slug}\ntarget_path=/workspace/{slug}\n", encoding="utf-8")
        db_path = proj_dir / "session.db"
        self._create_pure_v1_database(db_path, num_events=3)

        # 1. Explicit transactional migration
        conn = sqlite3.connect(str(db_path))
        report1 = apply_schema_migrations(conn)
        self.assertTrue(report1.success)
        self.assertEqual(report1.current_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(get_user_version(conn), CURRENT_SCHEMA_VERSION)

        # Verify backfilled canonical ID
        cur = conn.cursor()
        cur.execute("SELECT id, event_id FROM events ORDER BY id ASC")
        rows = cur.fetchall()
        self.assertEqual(rows[0][1], "evt_legacy_1")
        self.assertEqual(rows[1][1], "evt_legacy_2")
        self.assertEqual(rows[2][1], "evt_legacy_3")
        conn.close()

        # 2. Verify reading migrated database
        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug]})
        self.assertEqual(res["status"], 200)
        self.assertEqual(len(res["json"]["events"]), 3)
        self.assertEqual(res["json"]["events"][0]["event_id"], "evt_legacy_1")

        # 3. Verify second migration is completely idempotent no-op
        conn2 = sqlite3.connect(str(db_path))
        report2 = apply_schema_migrations(conn2)
        self.assertTrue(report2.success)
        self.assertEqual(len(report2.applied_versions), 0)
        self.assertEqual(report2.current_version, CURRENT_SCHEMA_VERSION)
        conn2.close()

    def test_empty_legacy_database_handled_gracefully(self) -> None:
        """Verify 0-byte file and empty table databases return empty results without crashing."""
        # Scenario A: 0-byte file
        slug_a = "empty_zero_byte"
        proj_dir_a = self.engine_root / "current" / slug_a
        proj_dir_a.mkdir(parents=True, exist_ok=True)
        (proj_dir_a / "project.meta").write_text(f"project_name={slug_a}\ntarget_path=/workspace\n", encoding="utf-8")
        db_a = proj_dir_a / "session.db"
        db_a.write_bytes(b"")

        handler_a, res_a = self._create_mock_handler()
        handler_a._handle_api_events_summary({"project": [slug_a]})
        self.assertEqual(res_a["status"], 200)
        self.assertEqual(res_a["json"]["events"], [])
        self.assertEqual(res_a["json"]["total"], 0)
        self.assertFalse(res_a["json"]["has_more"])

        # Detail lookup on empty DB returns 404
        handler_a2, res_a2 = self._create_mock_handler()
        handler_a2._handle_api_event_detail("1", {"project": [slug_a]})
        self.assertEqual(res_a2["status"], 404)

        # Scenario B: Valid SQLite DB with tables but 0 rows
        slug_b = "empty_with_tables"
        proj_dir_b = self.engine_root / "current" / slug_b
        proj_dir_b.mkdir(parents=True, exist_ok=True)
        (proj_dir_b / "project.meta").write_text(f"project_name={slug_b}\ntarget_path=/workspace\n", encoding="utf-8")
        db_b = proj_dir_b / "session.db"
        self._create_pure_v1_database(db_b, num_events=0)

        handler_b, res_b = self._create_mock_handler()
        handler_b._handle_api_events_summary({"project": [slug_b]})
        self.assertEqual(res_b["status"], 200)
        self.assertEqual(res_b["json"]["events"], [])
        self.assertEqual(res_b["json"]["total"], 0)

    def test_malformed_legacy_database_handled_gracefully(self) -> None:
        """Verify corrupted bytes or partial schemas are handled gracefully without 500 crashes."""
        # Scenario A: Random corrupted bytes (not a SQLite database)
        slug_a = "corrupt_bytes_proj"
        proj_dir_a = self.engine_root / "current" / slug_a
        proj_dir_a.mkdir(parents=True, exist_ok=True)
        (proj_dir_a / "project.meta").write_text(f"project_name={slug_a}\ntarget_path=/workspace\n", encoding="utf-8")
        db_a = proj_dir_a / "session.db"
        db_a.write_bytes(b"CORRUPTED_NOT_SQLITE_DATA_HEADER_1234567890\n")

        handler_a, res_a = self._create_mock_handler()
        handler_a._handle_api_events_summary({"project": [slug_a]})
        # Must return 200 with empty list, not crash with 500
        self.assertEqual(res_a["status"], 200)
        self.assertEqual(res_a["json"]["events"], [])
        self.assertEqual(res_a["json"]["total"], 0)

        handler_a2, res_a2 = self._create_mock_handler()
        handler_a2._handle_api_event_detail("1", {"project": [slug_a]})
        self.assertEqual(res_a2["status"], 404)

        # Scenario B: Partial schema (sessions table exists, events table missing)
        slug_b = "partial_schema_proj"
        proj_dir_b = self.engine_root / "current" / slug_b
        proj_dir_b.mkdir(parents=True, exist_ok=True)
        (proj_dir_b / "project.meta").write_text(f"project_name={slug_b}\ntarget_path=/workspace\n", encoding="utf-8")
        db_b = proj_dir_b / "session.db"
        conn_b = sqlite3.connect(str(db_b))
        conn_b.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, project_name TEXT);")
        conn_b.commit()
        conn_b.close()

        handler_b, res_b = self._create_mock_handler()
        handler_b._handle_api_events_summary({"project": [slug_b]})
        self.assertEqual(res_b["status"], 200)
        self.assertEqual(res_b["json"]["events"], [])

    def test_filters_and_pagination_on_legacy_v1_database(self) -> None:
        """Verify since_id, session_id, types, and limit work seamlessly on v1 database."""
        slug = "filter_test_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text(f"project_name={slug}\ntarget_path=/workspace\n", encoding="utf-8")
        db_path = proj_dir / "session.db"
        self._create_pure_v1_database(db_path, num_events=10)

        # 1. Test limit and pagination has_more
        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug], "limit": ["4"]})
        self.assertEqual(res["status"], 200)
        self.assertEqual(len(res["json"]["events"]), 4)
        self.assertTrue(res["json"]["has_more"])

        # 2. Test since_id pagination
        handler2, res2 = self._create_mock_handler()
        handler2._handle_api_events_summary({"project": [slug], "since_id": ["4"], "limit": ["4"]})
        self.assertEqual(res2["status"], 200)
        events2 = res2["json"]["events"]
        self.assertEqual(len(events2), 4)
        self.assertEqual(events2[0]["id"], 5)
        self.assertTrue(res2["json"]["has_more"])

        # 3. Test types filter
        handler3, res3 = self._create_mock_handler()
        handler3._handle_api_events_summary({"project": [slug], "types": ["NONEXISTENT"]})
        self.assertEqual(res3["status"], 200)
        self.assertEqual(len(res3["json"]["events"]), 0)

    def test_multi_patch_event_and_not_found_on_legacy_v1_database(self) -> None:
        """Verify events with multiple patches aggregate correctly and missing items 404."""
        slug = "multi_patch_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text(f"project_name={slug}\ntarget_path=/workspace\n", encoding="utf-8")
        db_path = proj_dir / "session.db"
        self._create_pure_v1_database(db_path, num_events=1)

        # Add second patch to event 1
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO patches (id, event_id, file_path, diff_content) VALUES (2, 1, 'file_b.py', '@@ -1 +1 @@\n-old\n+new')"
        )
        conn.commit()
        conn.close()

        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug]})
        self.assertEqual(res["status"], 200)
        ev = res["json"]["events"][0]
        self.assertEqual(ev["files_count"], 2)
        # Patch 1: 2 adds, 1 del; Patch 2: 1 add, 1 del -> Total: 3 adds, 2 dels
        self.assertEqual(ev["additions"], 3)
        self.assertEqual(ev["deletions"], 2)

        # Detail query reflects 2 files
        handler2, res2 = self._create_mock_handler()
        handler2._handle_api_event_detail("1", {"project": [slug]})
        self.assertEqual(res2["status"], 200)
        self.assertEqual(len(res2["json"]["files"]), 2)

        # Non-existent event returns 404
        handler3, res3 = self._create_mock_handler()
        handler3._handle_api_event_detail("9999", {"project": [slug]})
        self.assertEqual(res3["status"], 404)

        # Non-existent diff returns 404
        handler4, res4 = self._create_mock_handler()
        handler4._handle_api_event_file_diff("1", "missing_file.py", {"project": [slug]})
        self.assertEqual(res4["status"], 404)

    def test_history_tier_legacy_project_resolution(self) -> None:
        """Verify a legacy database in history/ tier is discovered and queried read-only."""
        slug = "hist_legacy_proj"
        run_dir = self.engine_root / "history" / slug / "run_20260901_100000_000000"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "project.meta").write_text(f"project_name={slug}\ntarget_path=/workspace\n", encoding="utf-8")
        db_path = run_dir / "session.db"
        self._create_pure_v1_database(db_path, num_events=2)

        handler, res = self._create_mock_handler()
        handler._handle_api_events_summary({"project": [slug]})
        self.assertEqual(res["status"], 200)
        self.assertEqual(len(res["json"]["events"]), 2)


if __name__ == "__main__":
    unittest.main()
