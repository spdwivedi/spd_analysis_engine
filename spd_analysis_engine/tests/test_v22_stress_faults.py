"""
tests/test_v22_stress_faults.py
================================
Stress, fault-injection, and performance audit for the SPD Analysis Engine.

Invariants Verified:
A. 2,000-File Stress & Delta Extraction:
   - Total SQLite write latency for 2,000 file patches.
   - Shadow Git commit latency using pathspec-from-file NUL.
   - Peak process memory (RSS) delta.
   - Headless simulation of frontend WindowedList / diff viewer logic.
B. Deterministic Fault-Injection Campaign:
   - Crash during BurstAssembler queue handoff.
   - Crash inside SessionDB transaction during patch recording.
   - Crash during ShadowGit.commit_burst / commit_changeset.
   - Invariants: PRAGMA integrity_check == ok, git fsck == ok, prior durable events preserved.
C. Database Growth & Retention Projection:
   - 5,000 events + patches accelerated workload simulation.
   - Physical disk growth (session.db + session.db-wal) in KB.
   - Retention projections for 7-day, 30-day, and 90-day retention in MB.
"""
from __future__ import annotations

import gc
import json
import math
import os
import queue
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import psutil

from scripts.core.burst_assembler import BurstAssembler
from scripts.core.changeset import ChangeSet
from scripts.core.faults import InjectedFaultError, fault_injection
from scripts.git.shadow_commit import record_git_op_state, recover_unfinished_git_ops
from scripts.git_shadow import ShadowGit
from scripts.storage.db import SessionDB

TELEMETRY_REPORT: dict[str, Any] = {}


class HeadlessWindowedList:
    """Headless simulation of web/js/components/windowed_list.js."""

    def __init__(self, items: list[dict[str, Any]], item_height: int = 42, buffer: int = 10, view_height: int = 400) -> None:
        self.items = items
        self.item_height = item_height
        self.buffer = buffer
        self.view_height = view_height
        self.top_spacer_px: int = 0
        self.bottom_spacer_px: int = 0
        self.rendered_elements: list[dict[str, Any]] = []

    def render_window(self, scroll_top: int = 0) -> list[dict[str, Any]]:
        total = len(self.items)
        if total == 0:
            self.rendered_elements = []
            self.top_spacer_px = 0
            self.bottom_spacer_px = 0
            return self.rendered_elements

        visible_count = math.ceil(self.view_height / self.item_height)
        raw_start = math.floor(scroll_top / self.item_height)
        start_index = max(0, raw_start - self.buffer)
        end_index = min(total, raw_start + visible_count + self.buffer)

        self.top_spacer_px = start_index * self.item_height
        self.bottom_spacer_px = max(0, (total - end_index) * self.item_height)
        self.rendered_elements = [
            {"index": i, "path": self.items[i].get("path", ""), "row_dom": f"<div class='row'>{i}</div>"}
            for i in range(start_index, end_index)
        ]
        return self.rendered_elements


def tearDownModule() -> None:
    """Print clean telemetry report upon test suite completion."""
    if not TELEMETRY_REPORT:
        return
    rep = TELEMETRY_REPORT
    print("\n" + "=" * 80)
    print("       SPD ENGINE STRESS, FAULT INJECTION & PERFORMANCE AUDIT REPORT")
    print("=" * 80)
    if "stress" in rep:
        s = rep["stress"]
        print("\n[AREA A: 2,000-FILE STRESS & DELTA EXTRACTION]")
        print(f"  - Files Processed:             {s['files_count']:,} files")
        print(f"  - Shadow Git Commit Latency:   {s['git_latency_s']:.3f} s (pathspec-from-file NUL)")
        print(f"  - SQLite Patch Write Latency:  {s['db_latency_s']:.3f} s (2,000 patches in WAL mode)")
        print(f"  - Peak Process RSS Delta:      {s['rss_delta_mb']:+.2f} MB")
        print(f"  - Windowed List Bound:         {s['windowed_max_elements']} items active (vs {s['files_count']:,} unwindowed)")
        print(f"  - Frontend Scroll Memory:      Stable (drift: {s['scroll_rss_drift_mb']:+.2f} MB)")

    if "faults" in rep:
        f = rep["faults"]
        print("\n[AREA B: DETERMINISTIC FAULT-INJECTION CAMPAIGN]")
        for pt, data in f.items():
            print(f"  - {pt:30}: Trapped={data['trapped']} | DB Integrity={data['db_integrity']} | Git fsck={data['git_fsck']} | Prior Preserved={data['preserved']}")

    if "growth" in rep:
        g = rep["growth"]
        print("\n[AREA C: DATABASE GROWTH & RETENTION PROJECTION]")
        print(f"  - Simulated Batch:             {g['events_count']:,} events + patches")
        print(f"  - Physical Size (Active WAL):  session.db: {g['db_kb']:.1f} KB | session.db-wal: {g['wal_kb']:.1f} KB (Total: {g['total_kb']:.1f} KB)")
        print(f"  - Post-Checkpoint DB Size:     {g['post_chk_kb']:.1f} KB")
        print(f"  - Empirical Mean Cost:         {g['avg_bytes_per_event']:.1f} bytes / event")
        print("  - Retention Projections (Heavy Workload @ 500 events/day):")
        print(f"      *  7-Day Projected Size:   {g['proj_7d_500']:.2f} MB")
        print(f"      * 30-Day Projected Size:   {g['proj_30d_500']:.2f} MB")
        print(f"      * 90-Day Projected Size:   {g['proj_90d_500']:.2f} MB")
        print("  - Retention Projections (Peak Workload @ 1,000 events/day):")
        print(f"      *  7-Day Projected Size:   {g['proj_7d_1000']:.2f} MB")
        print(f"      * 30-Day Projected Size:   {g['proj_30d_1000']:.2f} MB")
        print(f"      * 90-Day Projected Size:   {g['proj_90d_1000']:.2f} MB")
    print("=" * 80 + "\n")


class TestTwoThousandFileStressAndDelta(unittest.TestCase):
    """Area A: 2,000-File Stress & Delta Extraction."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="spd_stress_")
        self.ws_dir = Path(self.temp_dir) / "workspace"
        self.ws_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = Path(self.temp_dir) / "shadow_git"
        self.db_path = Path(self.temp_dir) / "session.db"
        self.shadow_git = ShadowGit(self.shadow_dir, self.ws_dir)
        self.shadow_git.init_repo()
        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session("stress_proj", str(self.ws_dir))

    def tearDown(self) -> None:
        try:
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_two_thousand_file_stress_and_delta_extraction(self) -> None:
        """Measure 2,000 modified files commit latency, SQLite patch write latency, RSS delta, and windowed list stability."""
        process = psutil.Process()
        gc.collect()
        rss_start = process.memory_info().rss

        file_count = 2000
        file_names = [f"file_{i:04d}.py" for i in range(file_count)]
        for i, fn in enumerate(file_names):
            (self.ws_dir / fn).write_text(f"# File {i}\ndef handle():\n    return {i}\n", encoding="utf-8")

        cs_init = ChangeSet("burst_init", 1, 1, file_names, file_names)
        res_init = self.shadow_git.commit_changeset(cs_init, summary="Initial 2000 files")
        self.assertEqual(res_init.state, "committed")

        for i, fn in enumerate(file_names):
            (self.ws_dir / fn).write_text(f"# File {i}\ndef handle():\n    return {i + 1}\n# delta append\n", encoding="utf-8")

        cs_mod = ChangeSet("burst_stress_2000", 2, 2, file_names, file_names)
        t0_git = time.perf_counter()
        res_git = self.shadow_git.commit_changeset(cs_mod, summary="Stress 2000 modified files")
        git_latency = time.perf_counter() - t0_git

        self.assertEqual(res_git.state, "committed")
        self.assertIsNotNone(res_git.commit_hash)

        t0_db = time.perf_counter()
        with self.session_db.transaction() as cur:
            eid = self.session_db.record_event(
                self.session_id, "EDIT", "file_0000.py", "Stress 2000 files", commit_hash=res_git.commit_hash, burst_uid="burst_stress_2000"
            )
            for i, fn in enumerate(file_names):
                self.session_db.record_patch(
                    eid, fn, f"@@ -1,2 +1,3 @@\n+# Edit {i}\n", adds=1, dels=0, kind="unified"
                )
        db_latency = time.perf_counter() - t0_db

        rss_peak = process.memory_info().rss
        rss_delta_mb = (rss_peak - rss_start) / (1024 * 1024)

        changeset_summary = [{"path": fn, "adds": 1, "dels": 0} for fn in file_names]
        windowed_list = HeadlessWindowedList(changeset_summary, item_height=42, buffer=10, view_height=400)

        rss_before_scroll = process.memory_info().rss
        max_elements = 0
        for offset in [0, 500 * 42, 1000 * 42, 1500 * 42, 2000 * 42]:
            rendered = windowed_list.render_window(offset)
            max_elements = max(max_elements, len(rendered))
            self.assertLessEqual(len(rendered), 35)

        rss_after_scroll = process.memory_info().rss
        scroll_drift_mb = (rss_after_scroll - rss_before_scroll) / (1024 * 1024)

        TELEMETRY_REPORT["stress"] = {
            "files_count": file_count,
            "git_latency_s": git_latency,
            "db_latency_s": db_latency,
            "rss_delta_mb": rss_delta_mb,
            "windowed_max_elements": max_elements,
            "scroll_rss_drift_mb": scroll_drift_mb,
        }

        self.assertLess(git_latency, 15.0)
        self.assertLess(db_latency, 2.0)
        self.assertLessEqual(max_elements, 35)


class TestDeterministicFaultInjectionCampaign(unittest.TestCase):
    """Area B: Deterministic Fault-Injection Campaign."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="spd_faults_")
        self.ws_dir = Path(self.temp_dir) / "workspace"
        self.ws_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = Path(self.temp_dir) / "shadow_git"
        self.db_path = Path(self.temp_dir) / "session.db"
        self.shadow_git = ShadowGit(self.shadow_dir, self.ws_dir)
        self.shadow_git.init_repo()
        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session("fault_proj", str(self.ws_dir))
        TELEMETRY_REPORT.setdefault("faults", {})

    def tearDown(self) -> None:
        try:
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_fault_during_burst_assembler_queue_handoff(self) -> None:
        """Crash during BurstAssembler queue handoff leaves prior durable events intact."""
        (self.ws_dir / "prior.py").write_text("prior code\n", encoding="utf-8")
        cs_prior = ChangeSet("burst_prior", 1, 1, ["prior.py"], ["prior.py"])
        res_prior = self.shadow_git.commit_changeset(cs_prior, summary="Prior durable commit")
        eid_prior = self.session_db.record_event(self.session_id, "EDIT", "prior.py", "Prior event", commit_hash=res_prior.commit_hash)
        self.session_db.record_patch(eid_prior, "prior.py", "@@ -0,0 +1 @@\n+prior code\n", adds=1, dels=0)

        class FaultyQueue(queue.Queue):
            def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
                raise InjectedFaultError("Simulated crash during BurstAssembler queue handoff")

        faulty_q = FaultyQueue()
        assembler = BurstAssembler(downstream_queue=faulty_q, quiet_period=0.05)
        assembler.add("crashed_file.py", "modified")

        with self.assertRaises(InjectedFaultError):
            assembler.flush_now()

        # Engine restart & reconciliation
        self.session_db.close()
        restarted_db = SessionDB(self.db_path)
        restarted_git = ShadowGit(self.shadow_dir, self.ws_dir)
        restarted_git.recover_pending_ops(restarted_db.conn)

        db_check = restarted_db.conn.execute("PRAGMA integrity_check").fetchall()
        fsck = restarted_git._run_git("fsck")
        ev_prior = restarted_db.get_event_by_id(eid_prior)
        restarted_db.close()

        self.assertEqual(db_check[0][0], "ok")
        self.assertEqual(fsck.returncode, 0)
        self.assertIsNotNone(ev_prior)
        self.assertEqual(ev_prior["summary"], "Prior event")

        TELEMETRY_REPORT["faults"]["Burst Queue Handoff"] = {
            "trapped": True, "db_integrity": db_check[0][0], "git_fsck": "clean" if fsck.returncode == 0 else "error", "preserved": "100%"
        }

    def test_02_fault_inside_sessiondb_transaction_during_patch_recording(self) -> None:
        """Crash inside SessionDB transaction during patch recording safely rolls back."""
        (self.ws_dir / "prior.py").write_text("prior code\n", encoding="utf-8")
        eid_prior = self.session_db.record_event(self.session_id, "EDIT", "prior.py", "Prior durable event")
        self.session_db.record_patch(eid_prior, "prior.py", "@@ -0,0 +1 @@\n+prior code\n", adds=1, dels=0)

        with self.assertRaises(InjectedFaultError):
            with fault_injection("inside_db_transaction", action="raise"):
                with self.session_db.transaction() as cur:
                    cur.execute(
                        "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, seq, event_id) VALUES (?, ?, ?, ?, ?, ?)",
                        (self.session_id, "EDIT", "2026-10-06T00:00:00Z", "aborted.py", 2, "evt_aborted")
                    )
                    self.session_db.record_patch(2, "aborted.py", "corrupt patch")

        # Engine restart & reconciliation
        self.session_db.close()
        restarted_db = SessionDB(self.db_path)
        db_check = restarted_db.conn.execute("PRAGMA integrity_check").fetchall()
        fsck = self.shadow_git._run_git("fsck")
        ev_prior = restarted_db.get_event_by_id(eid_prior)
        ev_aborted = restarted_db.get_event_by_id("evt_aborted")
        restarted_db.close()

        self.assertEqual(db_check[0][0], "ok")
        self.assertEqual(fsck.returncode, 0)
        self.assertIsNotNone(ev_prior)
        self.assertIsNone(ev_aborted)

        TELEMETRY_REPORT["faults"]["DB Transaction Crash"] = {
            "trapped": True, "db_integrity": db_check[0][0], "git_fsck": "clean" if fsck.returncode == 0 else "error", "preserved": "100%"
        }

    def test_03_fault_during_shadow_git_commit_burst(self) -> None:
        """Crash during ShadowGit commit burst triggers clean replay during recovery."""
        (self.ws_dir / "prior.py").write_text("prior\n", encoding="utf-8")
        cs1 = ChangeSet("burst_1", 1, 1, ["prior.py"], ["prior.py"])
        res1 = self.shadow_git.commit_changeset(cs1, summary="Prior commit")
        eid1 = self.session_db.record_event(self.session_id, "EDIT", "prior.py", "Prior commit", commit_hash=res1.commit_hash)

        (self.ws_dir / "feature.py").write_text("feature\n", encoding="utf-8")
        eid2 = self.session_db.record_event(self.session_id, "EDIT", "feature.py", "Feature commit")
        cs2 = ChangeSet("burst_2", eid2, 2, ["feature.py"], ["feature.py"])
        record_git_op_state(self.session_db.conn, eid2, "burst_2", "pending", ["feature.py"], ["feature.py"])

        with self.assertRaises(InjectedFaultError):
            with fault_injection("before_git_commit", action="raise"):
                self.shadow_git.commit_changeset(cs2, summary="Feature commit")

        # Engine restart & reconciliation
        self.session_db.close()
        restarted_db = SessionDB(self.db_path)
        restarted_git = ShadowGit(self.shadow_dir, self.ws_dir)
        recovered = restarted_git.recover_pending_ops(restarted_db.conn)

        db_check = restarted_db.conn.execute("PRAGMA integrity_check").fetchall()
        fsck = restarted_git._run_git("fsck")
        ev_prior = restarted_db.get_event_by_id(eid1)
        restarted_db.close()

        self.assertGreaterEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["state"], "committed")
        self.assertEqual(db_check[0][0], "ok")
        self.assertEqual(fsck.returncode, 0)
        self.assertIsNotNone(ev_prior)

        TELEMETRY_REPORT["faults"]["Shadow Git Commit Crash"] = {
            "trapped": True, "db_integrity": db_check[0][0], "git_fsck": "clean" if fsck.returncode == 0 else "error", "preserved": "100%"
        }


class TestDatabaseGrowthAndRetentionProjection(unittest.TestCase):
    """Area C: Database Growth & Retention Projection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="spd_growth_")
        self.db_path = Path(self.temp_dir) / "session.db"
        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session("growth_proj", self.temp_dir)

    def tearDown(self) -> None:
        try:
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_database_growth_and_retention_projection(self) -> None:
        """Simulate 5,000 events+patches accelerated workload and project 7d, 30d, 90d retention sizes."""
        batch_count = 5000
        event_batch = [
            (
                self.session_id,
                "EDIT",
                "2026-10-06T02:00:00Z",
                f"src/module_{i % 50}.py",
                f"Refactor business handler iteration {i}",
                "agent",
                f"hash_{i:08x}",
                0,
                i,
                f"evt_{i:06d}",
                i,
                f"burst_{i}",
                f"src/module_{i % 50}.py",
            )
            for i in range(1, batch_count + 1)
        ]
        patch_batch = [
            (
                i,
                f"src/module_{i % 50}.py",
                f"@@ -10,5 +10,8 @@\n- old_{i}()\n+ new_{i}()\n+ validate_{i}()\n",
                2,
                1,
                "unified",
                75,
                f"hpre_{i}",
                f"hpost_{i}",
                0,
            )
            for i in range(1, batch_count + 1)
        ]

        with self.session_db.transaction() as cur:
            cur.executemany(
                "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, executor, commit_hash, is_rolled_back, burst_num, event_id, seq, burst_uid, effective_files) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                event_batch,
            )
            cur.executemany(
                "INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size, hash_before, hash_after, truncated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                patch_batch,
            )

        wal_path = Path(self.temp_dir) / "session.db-wal"
        db_kb = self.db_path.stat().st_size / 1024.0
        wal_kb = wal_path.stat().st_size / 1024.0 if wal_path.exists() else 0.0
        total_kb = db_kb + wal_kb
        avg_bytes_per_event = (total_kb * 1024.0) / batch_count

        self.session_db.checkpoint()
        post_chk_kb = self.db_path.stat().st_size / 1024.0

        daily_mb_500 = (500 * avg_bytes_per_event) / (1024.0 * 1024.0)
        daily_mb_1000 = (1000 * avg_bytes_per_event) / (1024.0 * 1024.0)

        TELEMETRY_REPORT["growth"] = {
            "events_count": batch_count,
            "db_kb": db_kb,
            "wal_kb": wal_kb,
            "total_kb": total_kb,
            "post_chk_kb": post_chk_kb,
            "avg_bytes_per_event": avg_bytes_per_event,
            "proj_7d_500": daily_mb_500 * 7,
            "proj_30d_500": daily_mb_500 * 30,
            "proj_90d_500": daily_mb_500 * 90,
            "proj_7d_1000": daily_mb_1000 * 7,
            "proj_30d_1000": daily_mb_1000 * 30,
            "proj_90d_1000": daily_mb_1000 * 90,
        }

        chk = self.session_db.conn.execute("PRAGMA integrity_check").fetchall()
        self.assertEqual(chk[0][0], "ok")
        self.assertLess(daily_mb_500 * 90, 100.0)
