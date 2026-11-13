"""
tests/test_read_idempotency.py
==============================
Tests verifying that all GET routes and read helper methods are strictly
idempotent and non-destructive.

Verifies:
- No auto-archiving on GET /api/projects
- No consolidation on GET /api/project/<slug>/events
- auto_synthesize=False on read operations
- resolve_project_context does not create current/<slug>/
- Repeated GET queries on history-only projects leave them history-only
- No DDL, ALTER TABLE, file writes, or project.meta creation on GET
- No alteration of session.db or session.db-wal
- No implicit WAL checkpoints occur during read operations
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from scripts.core.supervisor import Supervisor
from scripts.server.server_daemon import EngineWebServer
from scripts.server.request_handler import _EngineRequestHandler
from scripts.storage.db import SessionDB
from scripts.storage.meta import resolve_project_context, read_project_meta


class TestReadIdempotency(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        (self.root / "current").mkdir(parents=True, exist_ok=True)
        (self.root / "last_run").mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(parents=True, exist_ok=True)
        (self.root / "trash").mkdir(parents=True, exist_ok=True)
        (self.root / "ai_data").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _take_snapshot(self, path: Path) -> dict[str, Any]:
        """
        Capture a deep state snapshot of a directory tree:
        - relative file and directory paths
        - file modification times (st_mtime_ns)
        - file sizes
        - SQLite database row counts and event IDs for any session.db found
        """
        snapshot: dict[str, Any] = {
            "entries": {},
            "db_states": {},
        }
        if not path.exists():
            return snapshot

        for root, dirs, files in os.walk(path):
            rel_root = os.path.relpath(root, path)
            for d in sorted(dirs):
                rel_d = os.path.normpath(os.path.join(rel_root, d))
                snapshot["entries"][rel_d] = {"type": "dir"}
            for f in sorted(files):
                full_f = Path(root) / f
                rel_f = os.path.normpath(os.path.join(rel_root, f))
                try:
                    stat = full_f.stat()
                    if f.endswith("-shm"):
                        # Shared memory file is an OS IPC mapping index; track existence and size
                        snapshot["entries"][rel_f] = {
                            "type": "file",
                            "size": stat.st_size,
                        }
                    else:
                        # Relevant project files, DB files, WAL files, and meta files track exact mtimes
                        snapshot["entries"][rel_f] = {
                            "type": "file",
                            "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        }
                except FileNotFoundError:
                    continue

                if f == "session.db":
                    # Capture DB internal state via strictly read-only connection
                    try:
                        uri = full_f.resolve().as_uri() + "?mode=ro"
                        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
                        conn.row_factory = sqlite3.Row
                        conn.execute("PRAGMA query_only = ON;")
                        conn.execute("PRAGMA wal_autocheckpoint = 0;")
                        cur = conn.cursor()

                        # Check tables
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = sorted(r["name"] for r in cur.fetchall())

                        row_counts = {}
                        event_ids = []
                        for t in tables:
                            if t.startswith("sqlite_"):
                                continue
                            cur.execute(f"SELECT COUNT(*) FROM {t}")
                            row_counts[t] = cur.fetchone()[0]

                        if "events" in tables:
                            cur.execute("SELECT id FROM events ORDER BY id ASC")
                            event_ids = [r["id"] for r in cur.fetchall()]

                        conn.close()
                        snapshot["db_states"][rel_f] = {
                            "tables": tables,
                            "row_counts": row_counts,
                            "event_ids": event_ids,
                        }
                    except Exception as exc:
                        snapshot["db_states"][rel_f] = {"error": str(exc)}

        return snapshot

    def _create_mock_handler(self, server: EngineWebServer) -> tuple[_EngineRequestHandler, dict[str, Any]]:
        handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
        handler.server = MagicMock()
        handler.server.engine_web_server = server
        response_capture: dict[str, Any] = {"json": None, "error": None}

        def mock_send_json(data: Any, status: int = 200) -> None:
            response_capture["json"] = data
            response_capture["status"] = status

        def mock_send_error(message: str, status: int = 400) -> None:
            response_capture["error"] = message
            response_capture["status"] = status

        handler._send_json = mock_send_json
        handler._send_error = mock_send_error
        return handler, response_capture

    def _prime_wal_db(self, db_path: Path) -> None:
        """Prime read-only connection so WAL/SHM file headers are stabilized prior to baseline snapshot."""
        try:
            from scripts.server.db_queries import _open_ro_db
            conn = _open_ro_db(db_path)
            conn.execute("SELECT COUNT(*) FROM sqlite_master;")
            conn.close()
        except Exception:
            pass

    def test_get_api_projects_idempotency(self) -> None:
        """GET /api/projects must not auto-archive, mutate DBs, or alter mtimes."""
        # 1. Setup inactive project in current/
        inactive_slug = "inactive_proj"
        inact_dir = self.root / "current" / inactive_slug
        inact_dir.mkdir(parents=True, exist_ok=True)
        (inact_dir / "project.meta").write_text("project_name=inactive_proj\ntarget_path=/tmp\n", encoding="utf-8")
        sdb = SessionDB(inact_dir / "session.db")
        sid = sdb.create_session("inactive_proj", "/tmp")
        eid = sdb.record_event(sid, "EDIT", "foo.py", "diff")
        sdb.record_patch(eid, "foo.py", "+test")
        sdb.close()
        self._prime_wal_db(inact_dir / "session.db")

        # 2. Setup history-only project
        hist_slug = "hist_proj"
        run_dir = self.root / "history" / hist_slug / "run_20260901_120000_123456"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "project.meta").write_text("project_name=hist_proj\ntarget_path=/tmp/hist\n", encoding="utf-8")
        sdb2 = SessionDB(run_dir / "session.db")
        sid2 = sdb2.create_session("hist_proj", "/tmp/hist")
        sdb2.record_event(sid2, "EDIT", "hist.py", "diff")
        sdb2.close()
        self._prime_wal_db(run_dir / "session.db")

        # Let timestamps settle
        time.sleep(0.05)
        snapshot_before = self._take_snapshot(self.root)

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, res = self._create_mock_handler(server)
            handler._handle_api_projects()

            self.assertIsNotNone(res["json"])
            # Inactive project must remain in current/ (not auto-archived to last_run/)
            self.assertTrue((self.root / "current" / inactive_slug).exists())
            self.assertFalse((self.root / "last_run" / inactive_slug).exists())

            # History project must NOT have created current/hist_proj
            self.assertFalse((self.root / "current" / hist_slug).exists())

            snapshot_after = self._take_snapshot(self.root)

            # Assert directory tree and files are unchanged
            self.assertEqual(
                snapshot_before["entries"],
                snapshot_after["entries"],
                "GET /api/projects mutated files, mtimes, or directory tree",
            )
            # Assert DB row counts and event IDs are unchanged
            self.assertEqual(
                snapshot_before["db_states"],
                snapshot_after["db_states"],
                "GET /api/projects mutated SQLite database internal states",
            )
        finally:
            server.shutdown()

    def test_get_api_events_idempotency(self) -> None:
        """GET /api/project/<slug>/events must not consolidate, synthesize meta, or alter DBs."""
        slug = "events_test_proj"
        run_dir = self.root / "history" / slug / "run_20260901_100000_000001"
        run_dir.mkdir(parents=True, exist_ok=True)
        sdb = SessionDB(run_dir / "session.db")
        sid = sdb.create_session(slug, "/tmp/workspace")
        eid1 = sdb.record_event(sid, "EDIT", "main.py", "edit 1")
        sdb.record_patch(eid1, "main.py", "+line1")
        eid2 = sdb.record_event(sid, "EDIT", "main.py", "edit 2")
        sdb.record_patch(eid2, "main.py", "+line2")
        sdb.close()
        self._prime_wal_db(run_dir / "session.db")

        time.sleep(0.05)
        snapshot_before = self._take_snapshot(self.root)

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, res = self._create_mock_handler(server)
            handler._handle_api_events(slug)

            self.assertIsNotNone(res["json"])
            self.assertEqual(len(res["json"]["events"]), 2)

            # Must not have created last_run or current directory
            self.assertFalse((self.root / "current" / slug).exists())
            self.assertFalse((self.root / "last_run" / slug).exists())

            snapshot_after = self._take_snapshot(self.root)
            self.assertEqual(
                snapshot_before["entries"],
                snapshot_after["entries"],
                "GET /api/project/<slug>/events mutated file tree or file mtimes",
            )
            self.assertEqual(
                snapshot_before["db_states"],
                snapshot_after["db_states"],
                "GET /api/project/<slug>/events altered database rows or schema",
            )
        finally:
            server.shutdown()

    def test_get_sessions_and_status_idempotency(self) -> None:
        """GET /api/status and GET sessions must be strictly non-mutating."""
        slug = "sessions_test_proj"
        curr_dir = self.root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)
        sdb = SessionDB(curr_dir / "session.db")
        sid = sdb.create_session(slug, "/tmp/proj")
        sdb.record_event(sid, "EDIT", "code.py", "fix")
        sdb.close()
        self._prime_wal_db(curr_dir / "session.db")

        time.sleep(0.05)
        snapshot_before = self._take_snapshot(self.root)

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, res = self._create_mock_handler(server)
            handler._handle_api_status()
            self.assertIsNotNone(res["json"])

            handler._handle_api_sessions(slug)
            self.assertIsNotNone(res["json"])
            self.assertEqual(len(res["json"]["sessions"]), 1)

            snapshot_after = self._take_snapshot(self.root)
            self.assertEqual(
                snapshot_before["entries"],
                snapshot_after["entries"],
                "GET status / sessions mutated filesystem entries or mtimes",
            )
            self.assertEqual(
                snapshot_before["db_states"],
                snapshot_after["db_states"],
                "GET status / sessions mutated database internal state",
            )
        finally:
            server.shutdown()

    def test_history_only_project_remains_history_only(self) -> None:
        """Querying a history-only project repeatedly across all GET routes must never create current/<slug>/."""
        slug = "pure_history_proj"
        hist_run = self.root / "history" / slug / "run_20260901_090000_111111"
        hist_run.mkdir(parents=True, exist_ok=True)
        (hist_run / "project.meta").write_text("project_name=pure_history_proj\ntarget_path=/tmp/test\n", encoding="utf-8")
        sdb = SessionDB(hist_run / "session.db")
        sid = sdb.create_session("pure_history_proj", "/tmp/test")
        sdb.record_event(sid, "EDIT", "test.py", "diff")
        sdb.close()

        current_slug = self.root / "current" / slug
        last_run_slug = self.root / "last_run" / slug

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, _ = self._create_mock_handler(server)

            for _ in range(5):
                handler._handle_api_projects()
                handler._handle_api_events("pure_history_proj")
                handler._handle_api_sessions("pure_history_proj")
                handler._handle_api_project_specs("pure_history_proj")
                handler._handle_api_project_milestones("pure_history_proj")
                ctx = resolve_project_context("pure_history_proj", self.root)
                meta = read_project_meta(self.root, "pure_history_proj")

                self.assertFalse(current_slug.exists(), "current/<slug>/ was created for history-only project")
                self.assertFalse(last_run_slug.exists(), "last_run/<slug>/ was created for history-only project")
                self.assertEqual(ctx["tier"], "history")
                self.assertEqual(meta["project_name"], "pure_history_proj")
        finally:
            server.shutdown()

    def test_no_implicit_wal_checkpoint_on_read(self) -> None:
        """Read queries on a database with an active WAL file must not trigger implicit checkpointing or modify WAL."""
        slug = "wal_checkpoint_test"
        curr_dir = self.root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)
        db_path = curr_dir / "session.db"

        # Create DB and leave transactions in the WAL file
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, project_name TEXT, target_path TEXT, start_time TEXT, end_time TEXT, status TEXT);")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id INTEGER, event_type TEXT, timestamp TEXT, first_file_touched TEXT, summary TEXT, ai_summary TEXT, executor TEXT, burst_num INTEGER, commit_hash TEXT, is_rolled_back INTEGER, pushed_in_milestone_id INTEGER);")
        conn.execute("CREATE TABLE patches (id INTEGER PRIMARY KEY, event_id INTEGER, file_path TEXT, diff_content TEXT);")
        conn.execute("INSERT INTO sessions VALUES (1, 'wal_checkpoint_test', '/tmp', '2026-09-01T00:00:00Z', NULL, 'ACTIVE');")
        for i in range(10):
            conn.execute("INSERT INTO events VALUES (?, 1, 'EDIT', '2026-09-01T00:00:00Z', 'f.py', 'edit', NULL, NULL, 1, NULL, 0, NULL);", (i + 1,))
        conn.commit()

        # Keep a writer connection open so the WAL file is actively populated and held open
        wal_path = curr_dir / "session.db-wal"
        self.assertTrue(wal_path.exists(), "WAL file was expected to exist")

        wal_stat_before = wal_path.stat()
        db_stat_before = db_path.stat()

        time.sleep(0.05)

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, res = self._create_mock_handler(server)

            # Perform multiple reads
            for _ in range(5):
                handler._handle_api_events("wal_checkpoint_test")
                handler._handle_api_sessions("wal_checkpoint_test")
                handler._handle_api_projects()

            wal_stat_after = wal_path.stat()
            db_stat_after = db_path.stat()

            # WAL file mtime and size must NOT have been modified by reads
            self.assertEqual(
                wal_stat_before.st_mtime_ns,
                wal_stat_after.st_mtime_ns,
                "WAL file mtime was altered during GET queries (implicit write/checkpoint occurred)",
            )
            self.assertEqual(
                wal_stat_before.st_size,
                wal_stat_after.st_size,
                "WAL file size changed during GET queries",
            )
            self.assertEqual(
                db_stat_before.st_mtime_ns,
                db_stat_after.st_mtime_ns,
                "DB file mtime was altered during GET queries",
            )
        finally:
            server.shutdown()
            conn.close()

    def test_explicit_system_reconcile_post(self) -> None:
        """POST /api/system/reconcile must be the explicit path that consolidates and archives inactive workers."""
        slug = "stale_to_reconcile"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text("project_name=stale_to_reconcile\ntarget_path=/tmp\n", encoding="utf-8")
        sdb = SessionDB(proj_dir / "session.db")
        sid = sdb.create_session("stale_to_reconcile", "/tmp")
        eid = sdb.record_event(sid, "EDIT", "a.py", "diff")
        sdb.record_patch(eid, "a.py", "+patch")
        sdb.close()

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler, res = self._create_mock_handler(server)

            # First GET /api/projects does NOT archive
            handler._handle_api_projects()
            self.assertTrue((self.root / "current" / slug).exists(), "GET unexpectedly archived project")

            # Explicit POST /api/system/reconcile DOES archive
            handler._handle_api_system_reconcile()
            self.assertIsNotNone(res["json"])
            self.assertTrue(res["json"]["success"])
            self.assertIn(slug, res["json"]["reconciled_archives"])
            self.assertFalse((self.root / "current" / slug).exists(), "POST /api/system/reconcile did not archive project")
            self.assertTrue((self.root / "last_run" / slug).exists(), "Project not moved to last_run/")
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
