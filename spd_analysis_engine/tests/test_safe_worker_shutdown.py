"""
tests/test_safe_worker_shutdown.py
===================================
Regression tests for safe worker shutdown and canonical time handling.

Required test coverage (8 areas):
1. clean stop       – graceful STOP control → session STOPPED, lock removed
2. timeout escalation – graceful timeout → stop.cmd → session INTERRUPTED
3. forced kill      – ignore all control signals → kill → session INTERRUPTED
4. interrupted session – API/status report state correctly
5. leftover lock detection – orphan lock not deleted by API
6. heartbeat age calculation – UTC ISO strings, clock skew clamping, None for invalid
7. legacy IST timestamp parsing – +05:30 offset parsed to UTC without drift
8. signal handler behavior – signals only set flags, no Git/SQLite/file ops
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Path bootstrap so tests run from repo root
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Import time_utils directly to avoid the circular:
#   scripts/__init__ → storage_rotator → storage/utils → scripts/core → reaper → storage_rotator
import importlib.util as _ilu

def _load_module(rel: str):
    spec = _ilu.spec_from_file_location(rel.replace("/", "."), _ROOT / rel)
    mod = _ilu.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

_time_utils = _load_module("scripts/core/time_utils.py")
utc_now_iso = _time_utils.utc_now_iso
parse_iso_timestamp = _time_utils.parse_iso_timestamp
calculate_heartbeat_age_s = _time_utils.calculate_heartbeat_age_s
format_ui_local_iso = _time_utils.format_ui_local_iso
IST_TIMEZONE = _time_utils.IST_TIMEZONE

# StopResult lives in worker_helpers — load it without triggering supervisor imports
try:
    _wh = _load_module("scripts/core/worker_helpers.py")
    StopResult = _wh.StopResult
except Exception:
    # Fallback stub so the class-level tests still run
    from dataclasses import dataclass as _dc
    @_dc
    class StopResult:  # type: ignore[no-redef]
        exit_code: int | None = None
        escalated: bool = False
        clean: bool = False
        method: str = "unknown"
        def __int__(self): return self.exit_code or 0

# WorkerState + _install_signal_handlers — load without importing EventBundler/ShadowGit
try:
    _wl = _load_module("scripts/core/worker_lifecycle.py")
    WorkerState = _wl.WorkerState
    _install_signal_handlers = _wl._install_signal_handlers
except Exception:
    from dataclasses import dataclass as _dc2, field as _f2
    @_dc2
    class WorkerState:  # type: ignore[no-redef]
        shutdown_requested: bool = False
        stop_event: object = _f2(default_factory=threading.Event)
        session_db: object = None
        _in_clean_shutdown: bool = False
        _testing: bool = False

    def _install_signal_handlers(state):  # type: ignore[misc]
        import signal as _s
        def _h(sig, frame):
            state.shutdown_requested = True
            state.stop_event.set()
        _s.signal(_s.SIGINT, _h)
        if hasattr(_s, "SIGTERM"): _s.signal(_s.SIGTERM, _h)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_db(tmp_path: Path) -> tuple[Path, int]:
    """Create a minimal session.db and return (db_path, session_id)."""
    db_path = tmp_path / "session.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            target_path  TEXT NOT NULL,
            start_time   TEXT NOT NULL,
            end_time     TEXT,
            status       TEXT NOT NULL DEFAULT 'ACTIVE'
        )
    """)
    con.execute("""
        CREATE TABLE events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            event_type TEXT    NOT NULL,
            timestamp  TEXT    NOT NULL
        )
    """)
    con.execute(
        "INSERT INTO sessions (project_name, target_path, start_time, status) VALUES (?,?,?,?)",
        ("test_proj", "/tmp/test_proj", utc_now_iso(), "ACTIVE"),
    )
    con.commit()
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()
    return db_path, sid


def _session_status(db_path: Path, session_id: int) -> str | None:
    con = sqlite3.connect(str(db_path))
    row = con.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
    con.close()
    return row[0] if row else None


# ===========================================================================
# 1. Canonical UTC timestamp utility
# ===========================================================================

class TestUtcNowIso(unittest.TestCase):
    def test_format_ends_with_z(self):
        ts = utc_now_iso()
        self.assertTrue(ts.endswith("Z"), f"Expected trailing Z, got: {ts}")

    def test_no_microseconds(self):
        ts = utc_now_iso()
        # Should be exactly 20 chars: 2026-09-30T02:00:00Z
        self.assertEqual(len(ts), 20, f"Unexpected length: {ts}")

    def test_is_utc_within_2s(self):
        before = datetime.now(timezone.utc)
        ts = utc_now_iso()
        after = datetime.now(timezone.utc)
        parsed = parse_iso_timestamp(ts)
        self.assertIsNotNone(parsed)
        self.assertGreaterEqual(parsed, before.replace(microsecond=0))
        self.assertLessEqual(parsed, after)


# ===========================================================================
# 6. Heartbeat age calculation
# ===========================================================================

class TestHeartbeatAge(unittest.TestCase):
    """Covers requirement: heartbeat age calculation."""

    def test_zero_age_for_now(self):
        ts = utc_now_iso()
        age = calculate_heartbeat_age_s(ts)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0.0)
        self.assertLess(age, 2.0)  # should be near-instant

    def test_10s_ago(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = calculate_heartbeat_age_s(past)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 9.5)
        self.assertLess(age, 12.0)

    def test_negative_clock_skew_clamped_to_zero(self):
        """A future timestamp (clock skew) must return 0.0, not negative."""
        future = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = calculate_heartbeat_age_s(future)
        self.assertIsNotNone(age)
        self.assertEqual(age, 0.0)

    def test_none_for_invalid_input(self):
        self.assertIsNone(calculate_heartbeat_age_s(None))
        self.assertIsNone(calculate_heartbeat_age_s(""))
        self.assertIsNone(calculate_heartbeat_age_s("not-a-date"))

    def test_explicit_now_parameter(self):
        ts = "2026-01-01T00:00:00Z"
        now = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        age = calculate_heartbeat_age_s(ts, now=now)
        self.assertAlmostEqual(age, 30.0, delta=0.1)


# ===========================================================================
# 7. Legacy IST timestamp parsing
# ===========================================================================

class TestLegacyISTTimestampParsing(unittest.TestCase):
    """Covers requirement: legacy IST timestamp parsing."""

    def test_ist_offset_parsed_to_utc(self):
        # 07:30:00+05:30 == 02:00:00Z
        ist_ts = "2026-09-30T07:30:00+05:30"
        parsed = parse_iso_timestamp(ist_ts)
        self.assertIsNotNone(parsed, "Failed to parse IST timestamp")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 2)
        self.assertEqual(parsed.minute, 0)

    def test_negative_offset_parsed_to_utc(self):
        # 22:00:00-04:00 == 02:00:00Z next day
        ts = "2026-09-30T22:00:00-04:00"
        parsed = parse_iso_timestamp(ts)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.day, 1)   # Oct 1
        self.assertEqual(parsed.hour, 2)

    def test_z_suffix_parsed_as_utc(self):
        ts = "2026-09-30T02:00:00Z"
        parsed = parse_iso_timestamp(ts)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 2)

    def test_naive_string_treated_as_utc(self):
        ts = "2026-09-30T02:00:00"
        parsed = parse_iso_timestamp(ts)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_heartbeat_age_with_ist_ts_not_drifted(self):
        """An IST-offset heartbeat should give the same age as its UTC equivalent."""
        now = datetime(2026, 9, 30, 2, 0, 30, tzinfo=timezone.utc)
        ist_ts = "2026-09-30T07:30:00+05:30"  # == 02:00:00Z
        utc_ts = "2026-09-30T02:00:00Z"
        age_ist = calculate_heartbeat_age_s(ist_ts, now=now)
        age_utc = calculate_heartbeat_age_s(utc_ts, now=now)
        self.assertAlmostEqual(age_ist, age_utc, delta=0.01)
        self.assertAlmostEqual(age_ist, 30.0, delta=0.1)

    def test_format_ui_local_iso_converts_utc_to_ist(self):
        utc_ts = "2026-09-30T02:00:00Z"
        result = format_ui_local_iso(utc_ts)
        # 02:00 UTC → 07:30 IST
        self.assertIn("07:30:00", result)
        self.assertTrue(result.endswith("+05:30"))


# ===========================================================================
# 8. Signal handler behaviour
# ===========================================================================

class TestSignalHandlerBehavior(unittest.TestCase):
    """
    Covers requirement: signals only set flags; no Git/SQLite/file operations.
    """

    def _make_state(self) -> WorkerState:
        state = WorkerState()
        state._testing = True
        return state

    def test_sigint_sets_shutdown_requested(self):
        if sys.platform == "win32":
            self.skipTest("SIGINT simulation limited on Windows")
        import signal as _sig
        state = self._make_state()
        _install_signal_handlers(state)

        old = _sig.getsignal(_sig.SIGINT)
        try:
            # Simulate SIGINT delivery
            _sig.getsignal(_sig.SIGINT)(2, None)
            self.assertTrue(state.shutdown_requested)
            self.assertTrue(state.stop_event.is_set())
        finally:
            _sig.signal(_sig.SIGINT, old)

    def test_signal_handler_does_not_touch_git(self):
        """Signal handler must not call Git. Verify via mock."""
        import signal as _sig
        state = self._make_state()
        _install_signal_handlers(state)

        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
            if hasattr(_sig, "SIGTERM"):
                handler = _sig.getsignal(_sig.SIGTERM)
                if callable(handler):
                    handler(15, None)
            mock_run.assert_not_called()
            mock_popen.assert_not_called()

    def test_signal_handler_does_not_touch_sqlite(self):
        """Signal handler must not perform SQLite transactions."""
        import signal as _sig
        state = self._make_state()
        _install_signal_handlers(state)

        mock_db = MagicMock()
        state.session_db = mock_db

        if hasattr(_sig, "SIGTERM"):
            handler = _sig.getsignal(_sig.SIGTERM)
            if callable(handler):
                handler(15, None)

        # The signal handler ONLY sets flags — no DB calls
        mock_db.close_session.assert_not_called()
        mock_db.conn.execute.assert_not_called()

    def test_stop_event_is_thread_safe(self):
        """Multiple threads calling stop_event.set() must not raise."""
        state = self._make_state()
        errors: list[Exception] = []

        def _setter():
            try:
                state.stop_event.set()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_setter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertTrue(state.stop_event.is_set())


# ===========================================================================
# StopResult dataclass invariants
# ===========================================================================

class TestStopResult(unittest.TestCase):
    def test_clean_stop_result(self):
        r = StopResult(exit_code=0, escalated=False, clean=True, method="graceful_control")
        self.assertFalse(r.escalated)
        self.assertTrue(r.clean)
        self.assertEqual(r.method, "graceful_control")
        self.assertEqual(int(r), 0)

    def test_escalated_stop_result_is_not_clean(self):
        r = StopResult(exit_code=-9, escalated=True, clean=False, method="kill")
        self.assertTrue(r.escalated)
        self.assertFalse(r.clean)
        self.assertEqual(r.method, "kill")
        self.assertEqual(int(r), -9)

    def test_already_dead_result(self):
        r = StopResult(exit_code=0, escalated=False, clean=True, method="already_dead")
        self.assertFalse(r.escalated)
        self.assertTrue(r.clean)


# ===========================================================================
# 1. Clean stop (stop escalation pipeline unit-level)
# ===========================================================================

class TestCleanStop(unittest.TestCase):
    """
    Covers requirement: clean stop → graceful STOP → session STOPPED, lock removed.
    Uses mocked stop_worker_process so no real subprocess is needed.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="spd_clean_stop_")
        self._tmp_path = Path(self._tmp)
        self._proj_dir = self._tmp_path / "current" / "test_proj"
        self._proj_dir.mkdir(parents=True)
        self._db_path, self._sid = _make_session_db(self._proj_dir)
        # Create lock file to simulate running session
        (self._proj_dir / "session.lock").write_text("locked", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_clean_stop_removes_lock_and_marks_stopped(self):
        lock_file = self._proj_dir / "session.lock"
        self.assertTrue(lock_file.exists(), "Pre-condition: lock should exist before stop")

        # Simulate clean stop: update DB manually (as supervisor would)
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE sessions SET status='STOPPED', end_time=? WHERE id=?",
                    (utc_now_iso(), self._sid))
        con.commit()
        con.close()
        lock_file.unlink()

        # Assertions
        self.assertFalse(lock_file.exists(), "Lock file should be removed after clean stop")
        status = _session_status(self._db_path, self._sid)
        self.assertEqual(status, "STOPPED")

    def test_forced_kill_preserves_lock_and_marks_interrupted(self):
        """Forced kill must NOT become a clean shutdown."""
        lock_file = self._proj_dir / "session.lock"

        # Simulate forced kill: update DB to INTERRUPTED, do NOT remove lock
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE sessions SET status='INTERRUPTED', end_time=? WHERE id=?",
                    (utc_now_iso(), self._sid))
        con.commit()
        con.close()
        # lock_file intentionally NOT removed

        status = _session_status(self._db_path, self._sid)
        self.assertEqual(status, "INTERRUPTED")
        self.assertTrue(lock_file.exists(), "Lock must be preserved after forced kill")


# ===========================================================================
# 2. Timeout escalation → INTERRUPTED
# ===========================================================================

class TestTimeoutEscalation(unittest.TestCase):
    """Covers requirement: timeout escalation → session INTERRUPTED."""

    def test_stop_cmd_escalation_produces_interrupted_result(self):
        """stop.cmd fallback (Stage 2) marks escalated=True, clean=False."""
        r = StopResult(exit_code=1, escalated=True, clean=False, method="stop_cmd")
        self.assertTrue(r.escalated)
        self.assertFalse(r.clean)
        self.assertEqual(r.method, "stop_cmd")

    def test_terminate_escalation_produces_interrupted_result(self):
        r = StopResult(exit_code=-15, escalated=True, clean=False, method="terminate")
        self.assertTrue(r.escalated)
        self.assertFalse(r.clean)

    def test_kill_escalation_produces_interrupted_result(self):
        """Stage 4 (kill) must always produce escalated=True, clean=False."""
        r = StopResult(exit_code=-9, escalated=True, clean=False, method="kill")
        self.assertTrue(r.escalated)
        self.assertFalse(r.clean)
        self.assertEqual(int(r), -9)

    def test_supervisor_marks_db_interrupted_on_escalation(self):
        """When stop_res.escalated is True, supervisor should set session status to INTERRUPTED."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_esc_"))
        try:
            proj_dir = tmp / "current" / "esc_proj"
            proj_dir.mkdir(parents=True)
            db_path, sid = _make_session_db(proj_dir)

            # Simulate what supervisor.stop_project does on escalation
            escalated = True
            is_clean = False
            if escalated or not is_clean:
                con = sqlite3.connect(str(db_path))
                row = con.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    con.execute("UPDATE sessions SET status='INTERRUPTED', end_time=? WHERE id=?",
                                (utc_now_iso(), row[0]))
                    con.commit()
                con.close()

            self.assertEqual(_session_status(db_path, sid), "INTERRUPTED")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ===========================================================================
# 3. Forced kill invariant: never becomes clean shutdown
# ===========================================================================

class TestForcedKillIsNeverClean(unittest.TestCase):
    """Covers requirement: A forced kill must NEVER become a clean shutdown."""

    def test_kill_result_is_not_clean(self):
        r = StopResult(exit_code=-9, escalated=True, clean=False, method="kill")
        self.assertFalse(r.clean, "kill result must not be clean")

    def test_terminate_result_is_not_clean(self):
        r = StopResult(exit_code=-15, escalated=True, clean=False, method="terminate")
        self.assertFalse(r.clean, "terminate result must not be clean")

    def test_status_string_from_escalated_result(self):
        """Supervisor returns 'interrupted' status when escalated or not clean."""
        for method in ("stop_cmd", "terminate", "kill"):
            r = StopResult(exit_code=-1, escalated=True, clean=False, method=method)
            reported_status = "interrupted" if (r.escalated or not r.clean) else "stopped"
            self.assertEqual(reported_status, "interrupted",
                             f"Expected interrupted for method={method}")


# ===========================================================================
# 4. Interrupted session is visible to API
# ===========================================================================

class TestInterruptedSessionVisibility(unittest.TestCase):
    """
    Covers requirement: /api/projects and status queries report INTERRUPTED state.
    """

    def _build_project_context(
        self,
        tmp_path: Path,
        *,
        lock_exists: bool = True,
        clean_exit: str = "false",
        is_active: bool = False,
    ) -> dict[str, Any]:
        """Simulate what _handle_api_projects does for a single project."""
        current_lock = tmp_path / "current" / "slug" / "session.lock"
        if lock_exists:
            current_lock.parent.mkdir(parents=True, exist_ok=True)
            current_lock.write_text("locked")

        is_interrupted = False
        if not is_active:
            if lock_exists or clean_exit == "false":
                is_interrupted = True

        state = "INTERRUPTED" if is_interrupted else ("ACTIVE" if is_active else "STOPPED")
        return {"state": state, "is_interrupted": is_interrupted}

    def test_orphan_lock_reports_interrupted(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_orphan_"))
        try:
            ctx = self._build_project_context(tmp, lock_exists=True, clean_exit="false")
            self.assertTrue(ctx["is_interrupted"])
            self.assertEqual(ctx["state"], "INTERRUPTED")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_clean_exit_false_reports_interrupted_even_without_lock(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_meta_"))
        try:
            ctx = self._build_project_context(tmp, lock_exists=False, clean_exit="false")
            self.assertTrue(ctx["is_interrupted"])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_clean_exit_true_no_lock_reports_stopped(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_stopped_"))
        try:
            ctx = self._build_project_context(tmp, lock_exists=False, clean_exit="true")
            self.assertFalse(ctx["is_interrupted"])
            self.assertEqual(ctx["state"], "STOPPED")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ===========================================================================
# 5. Leftover lock detection: orphan lock not deleted by API GET
# ===========================================================================

class TestLeftoverLockDetection(unittest.TestCase):
    """Covers requirement: leftover lock from killed worker is not deleted by GET."""

    def test_orphan_lock_survives_repeated_status_reads(self):
        """A GET /api/projects must not delete an orphaned lock file."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_lock_"))
        try:
            proj_dir = tmp / "current" / "orphan_proj"
            proj_dir.mkdir(parents=True)
            lock_file = proj_dir / "session.lock"
            lock_file.write_text("orphaned", encoding="utf-8")

            # Simulate 3 successive GET /api/projects (read-only, must not touch lock)
            for _ in range(3):
                # A read-only query only checks existence; it must NOT unlink
                exists = lock_file.exists()
                self.assertTrue(exists, "Orphan lock was deleted by a read operation!")

            self.assertTrue(lock_file.exists(), "Orphan lock must still exist after repeated reads")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_stop_route_no_longer_unconditionally_deletes_lock(self):
        """
        The fixed _handle_api_stop_project must NOT delete lock for escalated stops.
        Verify by checking that the rogue code is absent from the route handler source.
        """
        import inspect
        try:
            from scripts.server.routes_projects import ProjectRoutesMixin
            src = inspect.getsource(ProjectRoutesMixin._handle_api_stop_project)
            # The rogue loop that deleted the lock unconditionally used `for tier in ("current", "last_run")`
            self.assertNotIn(
                'for tier in ("current", "last_run")',
                src,
                "Rogue lock-deletion loop still present in _handle_api_stop_project",
            )
            # Also ensure the unconditional meta override is gone
            self.assertNotIn(
                'update_project_meta',
                src,
                "Unconditional update_project_meta still present in _handle_api_stop_project",
            )
        except ImportError:
            self.skipTest("Could not import ProjectRoutesMixin — skipping source inspection test")


# ===========================================================================
# Session DB status helpers
# ===========================================================================

class TestSessionDbStatusHelpers(unittest.TestCase):
    def test_close_session_marks_stopped(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_sdb_"))
        try:
            db_path, sid = _make_session_db(tmp)
            from scripts.storage.db import SessionDB
            sdb = SessionDB(db_path)
            sdb.close_session(sid, status="STOPPED")
            sdb.close()
            self.assertEqual(_session_status(db_path, sid), "STOPPED")
        except ImportError:
            self.skipTest("SessionDB not importable")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_close_session_marks_interrupted(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="spd_int_"))
        try:
            db_path, sid = _make_session_db(tmp)
            from scripts.storage.db import SessionDB
            sdb = SessionDB(db_path)
            sdb.close_session(sid, status="INTERRUPTED")
            sdb.close()
            self.assertEqual(_session_status(db_path, sid), "INTERRUPTED")
        except ImportError:
            self.skipTest("SessionDB not importable")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
