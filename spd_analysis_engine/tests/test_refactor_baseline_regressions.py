"""
tests/test_refactor_baseline_regressions.py
===========================================
Regression tests for already-confirmed architectural failures in the SPD Analysis Engine.

These tests assert the expected, correct system behaviors. Because the underlying
bugs are currently present in the implementation baseline, these tests are expected
to FAIL (RED) until the corresponding refactorings are implemented.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure engine package is importable
ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(ENGINE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT.parent))
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from scripts.core.supervisor import Supervisor
from scripts.core.worker_entry import _WorkerEntry
from scripts.core.reaper import ReaperDaemon
from scripts.core.process_sweeper import sweep_project_processes
from scripts.diff_calc import DiffCalculator
from scripts.event_bundler import EventBundler
from scripts.git_shadow import ShadowGit
from scripts.server.server_daemon import EngineWebServer
from scripts.server.request_handler import _EngineRequestHandler
from scripts.storage.db import SessionDB
from scripts.storage.meta import resolve_project_context, read_project_meta, synthesize_project_meta
from scripts.storage.history import consolidate_project_history
from scripts.storage.rotator import StorageRotator, _slug


class TestBaselineRegressions(unittest.TestCase):
    """Suite of 17 regression tests for confirmed baseline failures (RED tests)."""

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

    # -----------------------------------------------------------------------
    # 1. Only one Supervisor in UI mode
    # -----------------------------------------------------------------------
    def test_regression_01_only_one_supervisor_in_ui_mode(self) -> None:
        """In UI mode, EngineWebServer must not create a duplicate Supervisor instance or extra Reaper thread."""
        sup = Supervisor(self.root)
        try:
            server = EngineWebServer(engine_root=self.root, port=0)
            # The server must reuse the existing supervisor rather than instantiating a duplicate
            self.assertIs(
                server.supervisor,
                sup,
                "EngineWebServer created a redundant Supervisor instance instead of reusing the active one",
            )
        finally:
            sup._reaper.stop()

    # -----------------------------------------------------------------------
    # 2. GET /api/projects must not mutate state
    # -----------------------------------------------------------------------
    def test_regression_02_get_api_projects_does_not_mutate_state(self) -> None:
        """GET /api/projects must not auto-archive or move directories out of current/."""
        slug = "lingering_proj"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.meta").write_text("project_name=lingering_proj\n", encoding="utf-8")

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
            handler.server = MagicMock()
            handler.server.engine_web_server = server
            handler._send_json = MagicMock()

            # Calling GET handler
            handler._handle_api_projects()

            # The inactive project should NOT have been auto-archived by a GET request
            self.assertTrue(
                (self.root / "current" / slug).exists(),
                "GET /api/projects mutated filesystem state by auto-archiving current/lingering_proj",
            )
            self.assertFalse(
                (self.root / "last_run" / slug).exists(),
                "GET /api/projects unexpectedly moved project to last_run/",
            )
        finally:
            server.shutdown()

    # -----------------------------------------------------------------------
    # 3. GET /api/project/<slug>/events must not invoke consolidation/mutation
    # -----------------------------------------------------------------------
    def test_regression_03_events_get_does_not_invoke_consolidation_mutation(self) -> None:
        """GET /api/project/<name>/events must be purely read-only and not invoke consolidation."""
        slug = "read_only_proj"
        hist_run = self.root / "history" / slug / "run_20260901_000000_111111"
        hist_run.mkdir(parents=True, exist_ok=True)
        db_path = hist_run / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("read_only_proj", str(self.root))
        sdb.record_event(sid, "EDIT", "app.py", "initial edit")
        sdb.close()

        server = EngineWebServer(engine_root=self.root, port=0)
        try:
            handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
            handler.server = MagicMock()
            handler.server.engine_web_server = server
            handler._send_json = MagicMock()
            handler._send_error = MagicMock()

            with patch("scripts.server.routes_events.consolidate_project_history") as mock_consolidate:
                handler._handle_api_events("read_only_proj")
                mock_consolidate.assert_not_called()
        finally:
            server.shutdown()

    # -----------------------------------------------------------------------
    # 4. Heartbeat written in IST is incorrectly interpreted as Z
    # -----------------------------------------------------------------------
    def test_regression_04_heartbeat_written_in_ist_interpreted_correctly(self) -> None:
        """Worker status timestamp with IST timezone (+05:30) must be parsed without error."""
        slug = "ist_worker"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Worker writes timestamp in IST
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(ist_tz).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        status_file = proj_dir / "worker.status.json"
        status_file.write_text(
            json.dumps({"pid": os.getpid(), "project": slug, "session_id": 1, "ts": now_ist}),
            encoding="utf-8",
        )

        sup = Supervisor(self.root)
        try:
            entry = _WorkerEntry(
                name=slug,
                target_path=str(self.root),
                pid=54321,
                log_path=proj_dir / "worker.log",
                started_at_iso=now_ist,
            )
            sup._registry[slug] = entry

            with patch.object(sup, "_verify_worker_pid", return_value=True), patch.object(entry, "is_alive", return_value=True):
                statuses = sup.get_status(slug)
                self.assertEqual(len(statuses), 1)
                hb = statuses[0].get("last_heartbeat_s")
                self.assertIsNotNone(
                    hb,
                    "last_heartbeat_s is None because strptime failed on IST +05:30 timezone offset",
                )
                self.assertLess(hb, 10.0, f"Heartbeat age {hb}s indicates timezone drift (interpreted as UTC)")
        finally:
            sup._reaper.stop()

    # -----------------------------------------------------------------------
    # 5. Basename substring process matching is unsafe
    # -----------------------------------------------------------------------
    def test_regression_05_basename_substring_process_matching_unsafe(self) -> None:
        """Process sweeper must not match processes whose name merely contains slug as a substring."""
        target_slug = "demo"
        other_slug = "demo_extended"

        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 98765,
            "name": "python.exe",
            "cmdline": ["python.exe", "scripts/project_worker.py", "--name", other_slug, "--path", str(self.root)],
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            matched = sweep_project_processes(slug=target_slug, force_kill=False)
            self.assertNotIn(
                98765,
                matched,
                "Process sweeper incorrectly matched 'demo_extended' when sweeping for 'demo' due to substring matching",
            )

    # -----------------------------------------------------------------------
    # 6. Difflib patch output is rejected by git apply --check
    # -----------------------------------------------------------------------
    def test_regression_06_difflib_patch_accepted_by_git_apply_check(self) -> None:
        """DiffCalculator output must produce a clean unified diff accepted by git apply --check."""
        repo_dir = self.root / "git_test_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, capture_output=True, check=True)

        target_file = repo_dir / "module.py"
        target_file.write_text("def hello():\n    print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "module.py"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, capture_output=True, check=True)

        baseline_dir = self.root / "current" / "git_test_repo" / "baseline"
        calc = DiffCalculator(baseline_dir=baseline_dir, target_dir=repo_dir)
        calc.capture_baseline(repo_dir)

        # Modify file
        target_file.write_text("def hello():\n    print('hello world')\n", encoding="utf-8")
        patch_str = calc.compute_diff("module.py")

        # Checkout module.py back to initial commit so git apply tests against the baseline state
        subprocess.run(["git", "checkout", "module.py"], cwd=repo_dir, capture_output=True, check=True)

        res = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch_str.encode("utf-8"),
            cwd=repo_dir,
            capture_output=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"git apply --check rejected patch output with code {res.returncode}: {res.stderr.decode('utf-8', errors='replace')}",
        )

    # -----------------------------------------------------------------------
    # 7. EventBundler timer race
    # -----------------------------------------------------------------------
    def test_regression_07_event_bundler_timer_race_does_not_steal_pending_event(self) -> None:
        """Adding an event while timer callback is executing must not steal the event into the expiring bundle."""
        dispatched_bundles: list[dict] = []
        bundler = EventBundler(
            callback=lambda b: dispatched_bundles.append(b),
            quiet_period=1.0,
        )

        bundler.add_event("modified", "file1.py")

        # Simulate timer thread reaching _on_timer_fired while add_event arrives concurrently
        orig_extract = bundler._extract_bundle

        def _racing_extract():
            bundler.add_event("modified", "file2_racing.py")
            return orig_extract()

        with patch.object(bundler, "_extract_bundle", side_effect=_racing_extract):
            bundler._on_timer_fired()

        self.assertEqual(len(dispatched_bundles), 1)
        flushed_files = dispatched_bundles[0].get("files_touched", [])
        self.assertNotIn(
            "file2_racing.py",
            flushed_files,
            "Timer race: newly added event was prematurely stolen and flushed by the expiring timer callback",
        )

    # -----------------------------------------------------------------------
    # 8. Shared SQLite concurrent writer corruption/race
    # -----------------------------------------------------------------------
    def test_regression_08_shared_sqlite_concurrent_writers(self) -> None:
        """Concurrent threads writing to SessionDB must not crash with OperationalError."""
        db_path = self.root / "current" / "concurrent_db" / "session.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        sdb = SessionDB(db_path)
        sid = sdb.create_session("concurrent_db", str(self.root))

        errors: list[Exception] = []

        def _worker(thread_id: int) -> None:
            try:
                for i in range(15):
                    sdb.record_event(
                        session_id=sid,
                        event_type="EDIT",
                        first_file=f"file_{thread_id}.py",
                        summary=f"edit {i} by thread {thread_id}",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        sdb.close()
        self.assertEqual(len(errors), 0, f"Concurrent SQLite writes crashed with errors: {errors}")

    # -----------------------------------------------------------------------
    # 9. Duplicate READ events
    # -----------------------------------------------------------------------
    def test_regression_09_duplicate_read_events(self) -> None:
        """Emitting multiple identical READ events for the same file in a session must deduplicate."""
        db_path = self.root / "current" / "read_dedup" / "session.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        sdb = SessionDB(db_path)
        sid = sdb.create_session("read_dedup", str(self.root))

        # Simulate 5 repetitive handle reads within the same polling window
        for _ in range(5):
            sdb.record_event(
                session_id=sid,
                event_type="READ",
                first_file="src/index.js",
                summary="Inspected by cursor.exe (PID 100)",
            )

        cur = sdb.conn.cursor()
        cur.execute("SELECT count(*) FROM events WHERE event_type = 'READ' AND first_file_touched = 'src/index.js'")
        count = cur.fetchone()[0]
        sdb.close()

        self.assertEqual(
            count,
            1,
            f"Expected 1 deduplicated READ event, but found {count} duplicate rows in session.db",
        )

    # -----------------------------------------------------------------------
    # 10. 2,000-file Git operation
    # -----------------------------------------------------------------------
    def test_regression_10_git_operation_2000_files(self) -> None:
        """ShadowGit.commit_burst must handle 2,000 files without exceeding Windows command line length limit."""
        target_dir = self.root / "large_repo"
        target_dir.mkdir(parents=True, exist_ok=True)
        shadow_dir = self.root / "current" / "large_repo" / "shadow_git"

        # Create 2,000 files
        files_touched = []
        for i in range(2000):
            rel_name = f"sub/file_{i:04d}.txt"
            p = target_dir / rel_name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content {i}\n", encoding="utf-8")
            files_touched.append(rel_name)

        shadow = ShadowGit(shadow_dir=shadow_dir, target_dir=target_dir)
        shadow.init_repo()

        commit_hash = shadow.commit_burst(
            event_id=1,
            first_file="sub/file_0000.txt",
            summary="Bulk modification of 2000 files",
            files_touched=files_touched,
        )
        self.assertIsNotNone(
            commit_hash,
            "ShadowGit.commit_burst failed or crashed due to command-line argument overflow on 2,000 files",
        )

    # -----------------------------------------------------------------------
    # 11. GET on a history-only project creating current/<slug>/
    # -----------------------------------------------------------------------
    def test_regression_11_get_history_only_project_creates_current_dir(self) -> None:
        """Querying a history-only project must not create current/<slug>/ on disk."""
        slug = "history_only_proj"
        run_dir = self.root / "history" / slug / "run_20260901_100000_000001"
        run_dir.mkdir(parents=True, exist_ok=True)
        sdb = SessionDB(run_dir / "session.db")
        sdb.create_session("history_only_proj", str(self.root))
        sdb.close()

        current_slug_dir = self.root / "current" / slug
        self.assertFalse(current_slug_dir.exists())

        # Resolve context (which is executed during GET /api/project/<name>/specs, etc.)
        ctx = resolve_project_context(slug, self.root)

        self.assertFalse(
            current_slug_dir.exists(),
            "resolve_project_context created current/<slug>/ directory for a history-only project",
        )

    # -----------------------------------------------------------------------
    # 12. Stale Supervisor archiving a replacement worker
    # -----------------------------------------------------------------------
    def test_regression_12_stale_supervisor_archiving_replacement_worker(self) -> None:
        """ReaperDaemon must not archive current/<slug> if a replacement worker is running with a new PID."""
        slug = "replacement_proj"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        sup = Supervisor(self.root)
        try:
            # Active replacement worker PID (a real running process)
            active_pid = os.getpid()
            (proj_dir / "worker.pid").write_text(f"{active_pid}\n2026-09-30T00:00:00Z", encoding="utf-8")

            # Stale entry pointing to a dead PID
            dead_entry = _WorkerEntry(
                name=slug,
                target_path=str(self.root),
                pid=99999999,
                log_path=proj_dir / "worker.log",
                started_at_iso="2026-09-01T00:00:00Z",
            )
            sup._registry[slug] = dead_entry

            # Execute the reaper archiving logic
            sup.reap_dead_workers()

            self.assertTrue(
                (self.root / "current" / slug).exists(),
                "Stale supervisor archived active replacement worker from current/ to last_run/",
            )
        finally:
            sup._reaper.stop()

    # -----------------------------------------------------------------------
    # 13. Duplicate event collapse from second-resolution timestamps
    # -----------------------------------------------------------------------
    def test_regression_13_duplicate_event_collapse_second_resolution(self) -> None:
        """Consolidation must not collapse distinct events that occurred in the same second."""
        slug = "burst_events"
        run_dir = self.root / "history" / slug / "run_20260901_100000_100000"
        run_dir.mkdir(parents=True, exist_ok=True)
        db_path = run_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("burst_events", str(self.root))

        # Two distinct events with identical second-resolution timestamp and identical summary
        same_ts = "2026-09-30T01:00:00Z"
        sdb._execute(
            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary) VALUES (?, ?, ?, ?, ?)",
            (sid, "EDIT", same_ts, "main.py", "File modified"),
        )
        sdb._execute(
            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary) VALUES (?, ?, ?, ?, ?)",
            (sid, "EDIT", same_ts, "main.py", "File modified"),
        )
        sdb.close()

        # Consolidate into canonical db
        canonical = consolidate_project_history(self.root, slug)
        self.assertIsNotNone(canonical)

        conn = sqlite3.connect(str(canonical))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM events WHERE timestamp = ?", (same_ts,))
        count = cur.fetchone()[0]
        conn.close()

        self.assertEqual(
            count,
            2,
            f"Consolidation collapsed distinct events with identical second-resolution timestamp into {count} event(s)",
        )

    # -----------------------------------------------------------------------
    # 14. Worker startup blocking before PID/status publication
    # -----------------------------------------------------------------------
    def test_regression_14_worker_startup_publishes_status_before_blocking(self) -> None:
        """Worker must write worker.status.json before heavy synchronous baseline capture."""
        from scripts.core.worker_lifecycle import wire_worker, WorkerState

        target_dir = self.root / "heavy_ws"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "file1.txt").write_text("hello", encoding="utf-8")

        state = WorkerState()
        args = MagicMock()
        args.name = "heavy_project"
        args.path = str(target_dir)
        args.engine_root = str(self.root)
        args.scaffold = False
        args.git_init_primary = False
        args.debounce = 3.5
        args.track_reads = False
        args.track_exec = False
        args.shadow_git = False
        args.ide_profile = "antigravity"
        args.ide_custom_marker = None
        args.is_new_project = False

        status_existed_before_baseline = [False]

        orig_capture = DiffCalculator.capture_baseline

        def _mock_capture(self_dc, target_path=None):
            status_file = self.root / "current" / "heavy_project" / "worker.status.json"
            status_existed_before_baseline[0] = status_file.exists()
            return orig_capture(self_dc, target_path)

        with patch.object(DiffCalculator, "capture_baseline", _mock_capture):
            wire_worker(args, state=state)

        self.assertTrue(
            status_existed_before_baseline[0],
            "worker.status.json was not published before synchronous baseline capture was executed",
        )

    # -----------------------------------------------------------------------
    # 15. Unbounded worker.log
    # -----------------------------------------------------------------------
    def test_regression_15_unbounded_worker_log(self) -> None:
        """worker.log setup must use a bounded rotating handler rather than unbounded append."""
        from scripts.project_worker import _setup_logging
        import logging.handlers

        log_path = self.root / "current" / "log_proj" / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _setup_logging(log_path)

        # Check handlers on root logger
        has_bounded_handler = any(
            isinstance(h, (logging.handlers.RotatingFileHandler, logging.handlers.TimedRotatingFileHandler))
            for h in logging.root.handlers
        )
        self.assertTrue(
            has_bounded_handler,
            "worker.log uses unbounded open(log_path, 'a') StreamHandler with no size limit or rotation",
        )

    # -----------------------------------------------------------------------
    # 16. Worker importing server/API/AI credential machinery
    # -----------------------------------------------------------------------
    def test_regression_16_worker_import_isolation(self) -> None:
        """Importing project_worker must not import server or AI credential modules."""
        code = (
            "import sys\n"
            "import scripts.project_worker\n"
            "leaked = [m for m in sys.modules if m.startswith(('scripts.server', 'scripts.ai'))]\n"
            "if leaked:\n"
            "    print('LEAKED:', leaked)\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"project_worker leaked server/AI imports: {res.stdout.strip()}",
        )

    # -----------------------------------------------------------------------
    # 17. README / API route drift
    # -----------------------------------------------------------------------
    def test_regression_17_readme_api_route_drift(self) -> None:
        """Routes documented in scripts/server/README.md must match request_handler.py implementation."""
        readme_path = ENGINE_ROOT / "scripts" / "server" / "README.md"
        readme_text = readme_path.read_text(encoding="utf-8")

        # README documents POST /api/project/start and GET /api/events/stream
        self.assertIn("/api/project/start", readme_text)
        self.assertIn("/api/events/stream", readme_text)

        # But request_handler only accepts /api/projects/start and /api/stream
        req_handler_path = ENGINE_ROOT / "scripts" / "server" / "request_handler.py"
        req_handler_text = req_handler_path.read_text(encoding="utf-8")

        has_project_start = '"/api/project/start"' in req_handler_text or "'/api/project/start'" in req_handler_text
        has_events_stream = '"/api/events/stream"' in req_handler_text or "'/api/events/stream'" in req_handler_text

        self.assertTrue(
            has_project_start and has_events_stream,
            "Route drift: scripts/server/README.md documents /api/project/start and /api/events/stream, "
            "but request_handler.py only implements /api/projects/start and /api/stream",
        )


if __name__ == "__main__":
    unittest.main()
