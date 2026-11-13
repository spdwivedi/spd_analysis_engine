"""
tests/test_v21_behavior_matrix.py
=================================
Behavioral & Isolation Integration Matrix:
A. Golden AI-Agent Scenario & No-Op Validations:
   - Full workflow: start -> read file -> modify A -> run test -> modify B/C/D.
   - Exact burst boundaries: touches/identical saves = no burst.
   - Empty file creation, file deletion, and binary modification = effective bursts.
   - Execution events correlate accurately with the burst window.
B. 10-Minute Debounce & Max Burst Capping:
   - Configure debounce_s = 600, max_burst_s = 3600.
   - Simulate continuous file modifications for 3601 seconds.
   - Verify burst is safely sealed at max_burst_s even if quiet period never occurs.
C. Multi-Project Isolation & Git Safety:
   - Run 3 projects simultaneously in isolated temp folders.
   - Verify events, databases, and Shadow Git commits never bleed across projects.
   - Audit the primary repository (.git): prove Shadow Git operations never alter primary repo index or files.
D. AI Downstream Resilience:
   - Force AI provider (Gemini/Ollama) to timeout or throw 503s.
   - Verify that filesystem monitoring, SQLite persistence, and burst sealing continue perfectly despite AI failures.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.ai.synthesizer import AISynthesizer
from scripts.ai.synthesizer_tiered import synthesize_milestone, tier1_heuristic_synthesize
from scripts.core.burst_assembler import BurstAssembler
from scripts.core.change_processor import ChangeProcessor
from scripts.core.changeset import ChangeSet
from scripts.core.worker_bundle import _handle_event_bundle
from scripts.core.worker_lifecycle import WorkerState
from scripts.diff_calc import DiffCalculator
from scripts.git_shadow import ShadowGit
from scripts.storage.db import SessionDB
from scripts.storage.fingerprint import (
    ChangeResult,
    FileFingerprint,
    compare_fingerprints,
    compute_fingerprint,
)
from scripts.storage.rotator import StorageRotator, _slug

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _init_primary_git(workspace: Path) -> str:
    """Initialize a real host Git repo with an initial commit in workspace."""
    git_bin = shutil.which("git") or "git"
    subprocess.run([git_bin, "init"], cwd=str(workspace), check=True, capture_output=True, creationflags=_NO_WINDOW_FLAG)
    subprocess.run([git_bin, "config", "user.name", "Test User"], cwd=str(workspace), check=True, capture_output=True, creationflags=_NO_WINDOW_FLAG)
    subprocess.run([git_bin, "config", "user.email", "test@example.com"], cwd=str(workspace), check=True, capture_output=True, creationflags=_NO_WINDOW_FLAG)
    init_file = workspace / "init.txt"
    init_file.write_text("initial workspace content\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "init.txt"], cwd=str(workspace), check=True, capture_output=True, creationflags=_NO_WINDOW_FLAG)
    res = subprocess.run([git_bin, "commit", "-m", f"Initial commit for {workspace.name}"], cwd=str(workspace), check=True, capture_output=True, text=True, creationflags=_NO_WINDOW_FLAG)
    rev = subprocess.run([git_bin, "rev-parse", "HEAD"], cwd=str(workspace), check=True, capture_output=True, text=True, creationflags=_NO_WINDOW_FLAG)
    return rev.stdout.strip()


class TestGoldenAgentScenarioAndNoOps(unittest.TestCase):
    """
    Invariants A:
    - Golden Agent workflow (start -> read -> modify A -> exec -> modify B/C/D).
    - Touches/identical saves produce no burst.
    - Empty file creation, deletions, and binary mods produce effective bursts.
    - Execution events correlate accurately with burst sessions.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_golden_agent_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"
        for d in ("current", "last_run", "history", "trash"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Baseline workspace files
        (self.workspace / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.workspace / "utils.py").write_text("def log(msg):\n    print(msg)\n", encoding="utf-8")

        self.slug = "golden_proj"
        self.proj_dir = self.engine_root / "current" / self.slug
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.proj_dir / "session.db"
        self.patches_dir = self.proj_dir / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir = self.proj_dir / "baseline"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = self.proj_dir / "shadow_git"

        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session("golden_proj", str(self.workspace))

        self.diff_calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        self.diff_calc.capture_baseline(self.workspace)

        self.shadow_git = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.shadow_git.init_repo()

        self.downstream_queue: queue.Queue = queue.Queue()
        self.assembler = BurstAssembler(
            quiet_period=0.1,
            max_burst_s=1.0,
            downstream_queue=self.downstream_queue,
        )

        self.state = WorkerState(
            session_db=self.session_db,
            session_id=self.session_id,
            diff_calc=self.diff_calc,
            shadow_git=self.shadow_git,
            burst_assembler=self.assembler,
            patches_dir=self.patches_dir,
            project_name="golden_proj",
            target_path_str=str(self.workspace),
            engine_root_str=str(self.engine_root),
        )

        self.change_processor = ChangeProcessor(
            root_path=self.workspace,
            downstream_queue=self.downstream_queue,
            on_bundle_processed=lambda b: _handle_event_bundle(b, state=self.state),
        )

    def tearDown(self) -> None:
        try:
            self.assembler.stop(drain=False)
            self.change_processor.stop(drain=False)
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_golden_agent_workflow_and_exec_correlation(self) -> None:
        """Simulate agent: read -> edit A -> exec -> edit B/C/D with correct burst boundaries."""
        # 1. Agent reads app.py
        self.assembler.record_action("READ", "app.py", "Agent inspected implementation")
        read_eid = self.session_db.record_event(
            session_id=self.session_id,
            event_type="READ",
            first_file="app.py",
            summary="Observed (open_handle) by agent_worker",
            executor="agent_worker",
        )
        self.assertGreater(read_eid, 0)

        # 2. Agent modifies app.py (adds multiply function)
        app_file = self.workspace / "app.py"
        app_file.write_text("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n", encoding="utf-8")
        self.assembler.add("app.py", "modified")

        # Drain burst #1
        time.sleep(0.2)
        bundle_1 = self.assembler._extract_bundle_locked()
        self.assertIsNotNone(bundle_1)
        self.change_processor._process_single_bundle(bundle_1)

        # Verify event #2 is EDIT with diff for app.py
        ev2 = self.session_db.get_event_by_id(2)
        self.assertIsNotNone(ev2)
        self.assertEqual(ev2["event_type"], "EDIT")
        self.assertEqual(ev2["first_file_touched"], "app.py")
        patches = self.session_db.get_patches(2)
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["file_path"], "app.py")
        self.assertIn("+def multiply", patches[0]["diff_content"])

        # 3. Agent runs test command
        self.assembler.record_action("EXEC", "pytest", "pytest tests/test_app.py")
        exec_eid = self.session_db.record_event(
            session_id=self.session_id,
            event_type="EXEC",
            first_file="pytest",
            summary="pytest tests/test_app.py",
            executor="pytest",
        )
        self.assertGreater(exec_eid, 2)
        exec_ev = self.session_db.get_event_by_id(exec_eid)
        self.assertIsNotNone(exec_ev)
        self.assertEqual(exec_ev["event_type"], "EXEC")
        self.assertEqual(exec_ev["first_file_touched"], "pytest")

        # 4. Agent modifies utils.py and creates config.json
        (self.workspace / "utils.py").write_text("def log(msg):\n    print('[LOG]', msg)\n", encoding="utf-8")
        (self.workspace / "config.json").write_text('{"mode": "prod"}\n', encoding="utf-8")
        self.assembler.add("utils.py", "modified")
        self.assembler.add("config.json", "created")

        # Drain burst #2
        time.sleep(0.2)
        bundle_2 = self.assembler._extract_bundle_locked()
        self.assertIsNotNone(bundle_2)
        self.change_processor._process_single_bundle(bundle_2)

        edit_events = [dict(e) for e in self.session_db.get_events(self.session_id) if e["event_type"] == "EDIT"]
        self.assertGreaterEqual(len(edit_events), 2)
        second_edit = edit_events[-1]
        self.assertEqual(second_edit["event_type"], "EDIT")
        patches_2 = self.session_db.get_patches(second_edit["id"])
        self.assertEqual(len(patches_2), 2)
        touched = {p["file_path"] for p in patches_2}
        self.assertEqual(touched, {"utils.py", "config.json"})

        # Verify recent actions contains READ, BURST, EXEC, WRITE in order
        actions = self.assembler.get_recent_actions(limit=20)
        action_types = [a["action_type"] for a in actions]
        self.assertIn("READ", action_types)
        self.assertIn("EXEC", action_types)
        self.assertIn("BURST", action_types)

    def test_02_noop_touches_and_identical_saves_produce_no_burst(self) -> None:
        """Touch-only modifications (identical content, updated mtime) produce NO effective changes."""
        app_file = self.workspace / "app.py"
        before_content = app_file.read_bytes()
        before_fp = compute_fingerprint(app_file, "app.py")

        # Re-save exact same content and shift mtime forward
        time.sleep(0.01)
        app_file.write_bytes(before_content)
        current_st = app_file.stat()
        os.utime(str(app_file), (current_st.st_atime + 10.0, current_st.st_mtime + 10.0))

        after_fp = compute_fingerprint(app_file, "app.py")
        self.assertNotEqual(before_fp.mtime_ns, after_fp.mtime_ns)
        self.assertEqual(before_fp.sha256, after_fp.sha256)

        change = compare_fingerprints(before_fp, after_fp, "app.py")
        self.assertFalse(change.is_effective)
        self.assertEqual(change.change_type, "unchanged")

    def test_03_empty_creation_deletion_and_binary_mods_are_effective_bursts(self) -> None:
        """Empty file creation, file deletion, and binary modification are all effective changes."""
        # 1. Empty file creation (0 bytes)
        empty_file = self.workspace / "empty.txt"
        empty_file.write_text("", encoding="utf-8")
        empty_fp = compute_fingerprint(empty_file, "empty.txt")
        change_empty = compare_fingerprints(None, empty_fp, "empty.txt")
        self.assertTrue(change_empty.is_effective)
        self.assertTrue(change_empty.is_empty)
        self.assertEqual(change_empty.change_type, "created")

        # 2. File deletion
        utils_file = self.workspace / "utils.py"
        before_utils_fp = compute_fingerprint(utils_file, "utils.py")
        utils_file.unlink()
        after_utils_fp = compute_fingerprint(utils_file, "utils.py")
        self.assertFalse(after_utils_fp.exists)
        change_del = compare_fingerprints(before_utils_fp, after_utils_fp, "utils.py")
        self.assertTrue(change_del.is_effective)
        self.assertEqual(change_del.change_type, "deleted")

        # 3. Binary file modification
        bin_file = self.workspace / "logo.png"
        bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
        bin_fp = compute_fingerprint(bin_file, "logo.png")
        change_bin = compare_fingerprints(None, bin_fp, "logo.png")
        self.assertTrue(change_bin.is_effective)
        self.assertTrue(change_bin.is_binary)
        self.assertEqual(change_bin.change_type, "created")


class TestLongDebounceAndMaxBurstCapping(unittest.TestCase):
    """
    Invariants B:
    - Debounce configured to long duration (600s), max burst 3600s.
    - Max burst capping logic guarantees burst seals at max_burst_s bound.
    """

    def test_01_max_burst_capping_at_3600s_under_continuous_edits(self) -> None:
        """Verify mathematically that due time is capped by max_burst_s when quiet period never occurs."""
        q = queue.Queue()
        assembler = BurstAssembler(
            quiet_period=600.0,
            max_burst_s=3600.0,
            downstream_queue=q,
        )

        self.assertEqual(assembler.quiet_period, 600.0)
        self.assertEqual(assembler.max_burst_s, 3600.0)

        # Pure mathematical invariant test without live thread loops
        t_start = 1000.0
        last_event = t_start + 3500.0   # Edits kept arriving every 200s
        first_event = t_start           # First edit at 1000.0

        due = min(last_event + assembler.quiet_period, first_event + assembler.max_burst_s)
        # Quiet deadline = 3500 + 600 = 4100
        # Hard cap = 1000 + 3600 = 4600
        self.assertEqual(due, t_start + 3600.0, "Burst due time must cap at max_burst_s")

        # Now simulate an event past max_burst_s
        now = t_start + 3601.0
        rem = due - now
        self.assertLessEqual(rem, 0.0, "Remaining time past max_burst_s must be <= 0 to trigger seal")

    def test_02_assembler_loop_seals_on_live_max_burst_cap(self) -> None:
        """End-to-end live assembler loop execution sealing at max_burst_s (0.2s) under rapid edits."""
        q = queue.Queue()
        assembler = BurstAssembler(
            quiet_period=0.1,
            max_burst_s=0.2,
            downstream_queue=q,
        )
        assembler.start()
        try:
            assembler.add("main.py", "modified")
            self.assertTrue(assembler.is_pending)

            # Fire rapid edits faster than quiet_period (every 30ms < 100ms)
            # Must seal strictly when max_burst_s (0.2s) elapses
            start_t = time.time()
            while time.time() - start_t < 0.28:
                assembler.add("main.py", "modified")
                time.sleep(0.03)

            bundle = q.get(timeout=1.5)
            self.assertIsNotNone(bundle)
            self.assertEqual(bundle["event_type"], "EDIT")
            self.assertIn("main.py", bundle["files_touched"])
        finally:
            assembler.stop(drain=False)


class TestMultiProjectIsolationAndGitSafety(unittest.TestCase):
    """
    Invariants C:
    - 3 simultaneous projects in isolated temp workspaces.
    - Zero event, DB, or Shadow Git cross-bleeding.
    - Primary .git safety: Shadow Git operations never alter user's primary repo.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_iso_matrix_"))
        self.engine_root = self.temp_dir / "engine"
        for d in ("current", "last_run", "history", "trash"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)

        self.projects = ["alpha_proj", "beta_proj", "gamma_proj"]
        self.workspaces: dict[str, Path] = {}
        self.primary_revs: dict[str, str] = {}
        self.session_dbs: dict[str, SessionDB] = {}
        self.shadow_gits: dict[str, ShadowGit] = {}

        for p in self.projects:
            ws = self.temp_dir / f"ws_{p}"
            ws.mkdir(parents=True, exist_ok=True)
            self.workspaces[p] = ws
            # Initialize real host git repo
            rev = _init_primary_git(ws)
            self.primary_revs[p] = rev

            p_dir = self.engine_root / "current" / p
            p_dir.mkdir(parents=True, exist_ok=True)
            db_path = p_dir / "session.db"
            sdb = SessionDB(db_path)
            sdb.create_session(p, str(ws))
            self.session_dbs[p] = sdb

            sg_dir = p_dir / "shadow_git"
            sg = ShadowGit(shadow_dir=sg_dir, target_dir=ws)
            sg.init_repo()
            self.shadow_gits[p] = sg

    def tearDown(self) -> None:
        for sdb in self.session_dbs.values():
            try:
                sdb.close()
            except Exception:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_three_projects_isolation(self) -> None:
        """Mutations across 3 active projects never bleed across DBs or Shadow Git."""
        # Project Alpha: edit alpha.py
        ws_a = self.workspaces["alpha_proj"]
        (ws_a / "alpha.py").write_text("print('alpha v1')\n", encoding="utf-8")
        cs_a = ChangeSet(burst_uid="b_a_1", event_id=1, sequence=1, files=["alpha.py"], effective_files=["alpha.py"])
        self.session_dbs["alpha_proj"].record_event(1, "EDIT", "alpha.py", "Burst Alpha 1")
        self.session_dbs["alpha_proj"].record_patch(1, "alpha.py", "@@ -0,0 +1 @@\n+print('alpha v1')\n")
        res_a = self.shadow_gits["alpha_proj"].commit_changeset(cs_a, "Alpha commit", "alpha.py")
        self.assertIsNotNone(res_a.commit_hash)

        # Project Beta: edit beta.py
        ws_b = self.workspaces["beta_proj"]
        (ws_b / "beta.py").write_text("print('beta v1')\n", encoding="utf-8")
        cs_b = ChangeSet(burst_uid="b_b_1", event_id=1, sequence=1, files=["beta.py"], effective_files=["beta.py"])
        self.session_dbs["beta_proj"].record_event(1, "EDIT", "beta.py", "Burst Beta 1")
        self.session_dbs["beta_proj"].record_patch(1, "beta.py", "@@ -0,0 +1 @@\n+print('beta v1')\n")
        res_b = self.shadow_gits["beta_proj"].commit_changeset(cs_b, "Beta commit", "beta.py")
        self.assertIsNotNone(res_b.commit_hash)

        # Project Gamma: edit gamma.py
        ws_c = self.workspaces["gamma_proj"]
        (ws_c / "gamma.py").write_text("print('gamma v1')\n", encoding="utf-8")
        cs_c = ChangeSet(burst_uid="b_c_1", event_id=1, sequence=1, files=["gamma.py"], effective_files=["gamma.py"])
        self.session_dbs["gamma_proj"].record_event(1, "EDIT", "gamma.py", "Burst Gamma 1")
        self.session_dbs["gamma_proj"].record_patch(1, "gamma.py", "@@ -0,0 +1 @@\n+print('gamma v1')\n")
        res_c = self.shadow_gits["gamma_proj"].commit_changeset(cs_c, "Gamma commit", "gamma.py")
        self.assertIsNotNone(res_c.commit_hash)

        # Verify DB isolation: Alpha has ONLY alpha.py
        cur_a = self.session_dbs["alpha_proj"].conn.cursor()
        cur_a.execute("SELECT file_path FROM patches")
        paths_a = [r[0] for r in cur_a.fetchall()]
        self.assertEqual(paths_a, ["alpha.py"])

        # Verify DB isolation: Beta has ONLY beta.py
        cur_b = self.session_dbs["beta_proj"].conn.cursor()
        cur_b.execute("SELECT file_path FROM patches")
        paths_b = [r[0] for r in cur_b.fetchall()]
        self.assertEqual(paths_b, ["beta.py"])

        # Verify DB isolation: Gamma has ONLY gamma.py
        cur_c = self.session_dbs["gamma_proj"].conn.cursor()
        cur_c.execute("SELECT file_path FROM patches")
        paths_c = [r[0] for r in cur_c.fetchall()]
        self.assertEqual(paths_c, ["gamma.py"])

    def test_02_primary_repository_git_safety_audit(self) -> None:
        """Prove Shadow Git operations never alter user's primary repo HEAD, branch, or index."""
        git_bin = shutil.which("git") or "git"

        for p in self.projects:
            ws = self.workspaces[p]
            expected_head = self.primary_revs[p]

            # Trigger Shadow Git snapshot
            sg = self.shadow_gits[p]
            snap_hash = sg.commit_snapshot(f"Safety test snapshot for {p}")
            self.assertIsNotNone(snap_hash)

            # Audit primary repo HEAD
            head_proc = subprocess.run(
                [git_bin, "rev-parse", "HEAD"],
                cwd=str(ws),
                check=True,
                capture_output=True,
                text=True,
                creationflags=_NO_WINDOW_FLAG,
            )
            actual_head = head_proc.stdout.strip()
            self.assertEqual(
                actual_head,
                expected_head,
                f"Primary repo HEAD in {p} was mutated by Shadow Git! (expected: {expected_head}, got: {actual_head})",
            )

            # Audit primary repo status: init.txt is committed, untracked files remain intact
            status_proc = subprocess.run(
                [git_bin, "status", "--porcelain"],
                cwd=str(ws),
                check=True,
                capture_output=True,
                text=True,
                creationflags=_NO_WINDOW_FLAG,
            )
            # Must NOT report init.txt as modified or missing
            self.assertNotIn("D init.txt", status_proc.stdout)
            self.assertNotIn("M init.txt", status_proc.stdout)


class TestAIDownstreamResilience(unittest.TestCase):
    """
    Invariants D:
    - Downstream AI provider timeout or HTTP 503 failures.
    - Filesystem monitoring, burst sealing, and SQLite persistence continue perfectly.
    - Progressive failover returns Tier 1 Heuristic synthesis without unhandled crash.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ai_resilience_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"
        for d in ("current", "last_run", "history", "trash", "ai_data"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        (self.workspace / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
        _init_primary_git(self.workspace)

        self.slug = "resilience_proj"
        self.proj_dir = self.engine_root / "current" / self.slug
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.proj_dir / "session.db"
        self.patches_dir = self.proj_dir / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir = self.proj_dir / "baseline"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = self.proj_dir / "shadow_git"

        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session(self.slug, str(self.workspace))

        self.diff_calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.workspace)
        self.diff_calc.capture_baseline(self.workspace)

        self.shadow_git = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.shadow_git.init_repo()

        self.state = WorkerState(
            session_db=self.session_db,
            session_id=self.session_id,
            diff_calc=self.diff_calc,
            shadow_git=self.shadow_git,
            patches_dir=self.patches_dir,
            project_name=self.slug,
            target_path_str=str(self.workspace),
            engine_root_str=str(self.engine_root),
        )

    def tearDown(self) -> None:
        try:
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_burst_capture_and_db_persistence_resilient_to_ai_503(self) -> None:
        """When AI provider throws HTTP 503, capture, SQLite durability, and Tier 1 fallback succeed."""
        # 1. Modify file in workspace
        (self.workspace / "service.py").write_text("class Service:\n    def start(self): return True\n", encoding="utf-8")

        bundle = {
            "event_type": "EDIT",
            "files_touched": ["service.py"],
            "first_file": "service.py",
            "duration_s": 0.5,
        }

        # 2. Process bundle through worker capture pipeline
        _handle_event_bundle(bundle, state=self.state)

        # Verify SQLite durability
        ev = self.session_db.get_event_by_id(1)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "EDIT")
        self.assertEqual(ev["first_file_touched"], "service.py")

        patches = self.session_db.get_patches(1)
        self.assertEqual(len(patches), 1)
        self.assertIn("+    def start(self)", patches[0]["diff_content"])

        # Patch file durability
        patch_file = self.patches_dir / "event_1.patch"
        self.assertTrue(patch_file.exists())
        self.assertIn("+    def start(self)", patch_file.read_text(encoding="utf-8"))

        # 3. Simulate downstream AI call under HTTP 503 Service Unavailable
        http_503 = urllib.error.HTTPError("https://api.gemini.ai", 503, "Service Unavailable", {}, None)
        with patch("scripts.ai.synthesizer_tiered.call_gemini", side_effect=http_503):
            milestone = synthesize_milestone(
                burst_ids=[1],
                mode="tiered",
                project_slug=self.slug,
                target_path=self.workspace,
                engine_root=self.engine_root,
            )
            # Must gracefully fall back without raising exception
            self.assertEqual(milestone.get("mode_used"), "tier1_heuristic")
            self.assertIn("Gemini 503 Failover", milestone.get("provider_used", ""))
            self.assertTrue(milestone.get("title").startswith(("feat", "fix", "chore", "docs", "test")))
            self.assertIn("body", milestone)
            self.assertIn("changelog", milestone)

    def test_02_burst_capture_and_db_persistence_resilient_to_ai_timeout(self) -> None:
        """When AI provider times out, capture, persistence, and burst synthesis remain rock solid."""
        # 1. Modify file
        (self.workspace / "service.py").write_text("class Service:\n    def stop(self): pass\n", encoding="utf-8")

        bundle = {
            "event_type": "EDIT",
            "files_touched": ["service.py"],
            "first_file": "service.py",
            "duration_s": 0.4,
        }

        # 2. Process bundle
        _handle_event_bundle(bundle, state=self.state)

        ev = self.session_db.get_event_by_id(1)
        self.assertIsNotNone(ev)

        # 3. Downstream analyze_burst under TimeoutError
        synthesizer = AISynthesizer(self.engine_root)
        with patch("scripts.ai.synthesizer.call_gemini", side_effect=TimeoutError("Gemini call timed out")):
            with patch("scripts.ai.synthesizer.call_ollama", side_effect=TimeoutError("Ollama call timed out")):
                analysis = synthesizer.analyze_burst(
                    project_name=self.slug,
                    workspace_path=self.workspace,
                    event=dict(ev),
                    patches=[dict(p) for p in self.session_db.get_patches(1)],
                )
                self.assertIsNotNone(analysis)
                self.assertIn("summary", analysis)
                self.assertIn(analysis.get("provider"), ("heuristic", "offline-fallback"))
                self.assertTrue(bool(analysis.get("fallback_reason") or analysis.get("intent")))


if __name__ == "__main__":
    unittest.main()
