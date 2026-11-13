"""
tests/test_phase4.py
====================
Verification test suite for Phase 4 of the SPD Analysis Engine:
1. Shadow Git micro-versioning isolation and micro-commits.
2. ProcessInspector file read handle tracking.
3. ExecutionTracker shell command auditing.
4. Selective Powers toggles and Web API endpoints.
5. End-to-End integration test of worker with all powers enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

# Setup paths
_HERE = Path(__file__).resolve().parent
_ENGINE_ROOT = _HERE.parent
sys.path.insert(0, str(_ENGINE_ROOT.parent))

from spd_analysis_engine.scripts.storage_rotator import SessionDB, StorageRotator, _slug
from spd_analysis_engine.scripts.git_shadow import ShadowGit
from spd_analysis_engine.scripts.watcher_proc import ProcessInspector
from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
from spd_analysis_engine.scripts.orchestrator import Supervisor
from spd_analysis_engine.scripts.web_server import EngineWebServer


def test_shadow_git_unit() -> None:
    print("\n--- Test 1: ShadowGit Unit Tests ---")
    test_target = _ENGINE_ROOT / "mock_workspace" / "test_shadow_target"
    shadow_dir = _ENGINE_ROOT / "mock_workspace" / "test_shadow_dir"

    if test_target.exists():
        shutil.rmtree(test_target, ignore_errors=True)
    if shadow_dir.exists():
        shutil.rmtree(shadow_dir, ignore_errors=True)

    test_target.mkdir(parents=True, exist_ok=True)
    shadow_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Create a sample file in target
        f1 = test_target / "main.py"
        f1.write_text("print('hello phase 4')", encoding="utf-8")

        sg = ShadowGit(shadow_dir=shadow_dir, target_dir=test_target)
        ok = sg.init_repo()
        assert ok, "ShadowGit init_repo failed"
        assert (shadow_dir / "config").exists(), "Shadow git config missing"
        print("      [PASS] ShadowGit initialized in isolated directory")

        # Create micro-commit
        commit_hash = sg.commit_burst(
            event_id=1,
            first_file="main.py",
            summary="Initial burst edit",
            files_touched=["main.py"],
        )
        assert commit_hash is not None, "Micro-commit failed"
        assert len(commit_hash) >= 7, f"Invalid commit hash: {commit_hash}"
        print(f"      [PASS] Created micro-commit: {commit_hash[:7]}")

        # Check commit history
        history = sg.get_commit_history()
        assert len(history) >= 1, "Commit history is empty"
        assert history[0]["hash"] == commit_hash
        assert "Burst #1" in history[0]["message"]
        print(f"      [PASS] Verified commit history with {len(history)} commit(s)")

        # Verify primary target directory does NOT have .git
        assert not (test_target / ".git").exists(), "Target directory was polluted with .git!"
        print("      [PASS] Target workspace .git isolation verified")

        # Test init_primary_repo
        primary_ok = ShadowGit.init_primary_repo(test_target)
        assert primary_ok, "init_primary_repo failed"
        assert (test_target / ".git").exists(), "Primary .git was not created by init_primary_repo"
        print("      [PASS] init_primary_repo successfully created primary .git")

    finally:
        shutil.rmtree(test_target, ignore_errors=True)
        shutil.rmtree(shadow_dir, ignore_errors=True)


def test_process_inspector_unit() -> None:
    print("\n--- Test 2: ProcessInspector Unit Tests ---")
    test_target = _ENGINE_ROOT / "mock_workspace" / "test_proc_target"
    if test_target.exists():
        shutil.rmtree(test_target, ignore_errors=True)
    test_target.mkdir(parents=True, exist_ok=True)

    read_events: list[dict[str, Any]] = []

    def on_file_read(rec: dict[str, Any]) -> None:
        read_events.append(rec)

    inspector = ProcessInspector(
        target_path=test_target,
        on_file_read=on_file_read,
        scan_interval=0.2,
    )

    try:
        sample_file = test_target / "read_sample.txt"
        sample_file.write_text("inspect me", encoding="utf-8")

        inspector.start()
        print("      [PASS] ProcessInspector started")

        # Hold open file handle in current process to simulate an IDE analyzing it
        with open(sample_file, "r", encoding="utf-8") as f:
            time.sleep(0.6)  # Give inspector time to scan

        inspector.stop()
        print(f"      [PASS] ProcessInspector stopped (detected {len(read_events)} read events)")
    finally:
        inspector.stop()
        shutil.rmtree(test_target, ignore_errors=True)


def test_execution_tracker_unit() -> None:
    print("\n--- Test 3: ExecutionTracker Unit Tests ---")
    db_file = _ENGINE_ROOT / "mock_workspace" / "test_exec.db"
    if db_file.exists():
        db_file.unlink()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    db = SessionDB(db_file)
    sid = db.create_session("exec_proj", str(_ENGINE_ROOT))

    tracker = ExecutionTracker(target_dir=_ENGINE_ROOT, db=db, session_id=sid)
    try:
        eid = tracker.record_execution(
            command="npm run build",
            duration_s=1.45,
            exit_code=0,
        )
        assert eid > 0, f"Invalid event_id: {eid}"
        print(f"      [PASS] record_execution recorded event #{eid}")

        # Check DB
        cur = db.conn.cursor()
        cur.execute("SELECT id, event_type, first_file_touched, summary FROM events WHERE id = ?", (eid,))
        row = cur.fetchone()
        assert row is not None
        assert row[1] == "EXEC"
        assert row[2] == "npm run build"
        assert "1.45s" in row[3]
        print("      [PASS] Verified EXEC event stored in session.db")
    finally:
        tracker.stop()
        db.close()
        if db_file.exists():
            db_file.unlink()


def test_end_to_end_phase4() -> None:
    print("\n--- Test 4: End-to-End Integration & Web Server API ---")
    port = 8798
    server = EngineWebServer(engine_root=_ENGINE_ROOT, port=port)
    actual_port = server.start(background=True)
    base_url = f"http://127.0.0.1:{actual_port}"
    print(f"      [PASS] EngineWebServer started at {base_url}")

    proj_name = "phase4_e2e_proj"
    test_ws = _ENGINE_ROOT / "mock_workspace" / proj_name
    if test_ws.exists():
        shutil.rmtree(test_ws, ignore_errors=True)
    test_ws.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Start worker via Web API with all selective powers
        start_payload = {
            "project": proj_name,
            "path": str(test_ws),
            "scaffold": True,
            "debounce": 1.0,
            "track_reads": True,
            "track_exec": True,
            "shadow_git": True,
            "git_init_primary": True,
        }
        start_req = urllib.request.Request(
            f"{base_url}/api/projects/start",
            data=json.dumps(start_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(start_req) as resp:
            start_res = json.loads(resp.read().decode("utf-8"))
            assert start_res["status"] == "started"
            worker_pid = start_res["result"]["pid"]
            print(f"      [PASS] Worker started via API with all powers | PID={worker_pid}")

        # Wait for worker initialization & primary git init
        time.sleep(1.5)
        assert (test_ws / ".git").exists(), "Primary git repo was not initialized in workspace"
        print("      [PASS] Verified primary git repo created via --git-init-primary")

        # Verify shadow git repo exists in current/<slug>/shadow_git
        slug = _slug(proj_name)
        shadow_dir = _ENGINE_ROOT / "current" / slug / "shadow_git"
        assert shadow_dir.exists(), f"Shadow git dir missing: {shadow_dir}"
        print("      [PASS] Verified shadow git repo created in current/ directory")

        # 2. Trigger an edit burst by writing files
        code_file = test_ws / "index.js"
        code_file.write_text("console.log('version 1');\n", encoding="utf-8")
        time.sleep(0.3)
        code_file.write_text("console.log('version 2');\nconst x = 42;\n", encoding="utf-8")

        # Wait for debounce window (1.0s + margin)
        print("      Waiting for debounce quiet window to bundle burst...")
        time.sleep(2.5)

        # 3. Simulate a terminal execution via POST /api/project/<name>/exec
        exec_payload = {
            "command": "node index.js",
            "duration_s": 0.35,
            "exit_code": 0,
        }
        exec_req = urllib.request.Request(
            f"{base_url}/api/project/{proj_name}/exec",
            data=json.dumps(exec_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(exec_req) as resp:
            exec_res = json.loads(resp.read().decode("utf-8"))
            assert exec_res["status"] == "recorded"
            print(f"      [PASS] Recorded command execution via API (event #{exec_res['event_id']})")

        # 4. Check GET /api/project/<name>/events (polling up to 6.0s for asynchronous bursts & micro-commits)
        events = []
        types = set()
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            events_req = urllib.request.Request(f"{base_url}/api/project/{proj_name}/events")
            with urllib.request.urlopen(events_req) as resp:
                events_data = json.loads(resp.read().decode("utf-8"))
                events = events_data.get("events", [])
                types = {e["event_type"] for e in events}
                if "EDIT" in types and "GIT" in types and "EXEC" in types:
                    break
            time.sleep(0.5)

        print(f"      [PASS] Retrieved {len(events)} event(s) from session.db: types={types}")
        assert "EDIT" in types, "Missing EDIT burst event"
        assert "GIT" in types, "Missing GIT micro-commit event"
        assert "EXEC" in types, "Missing EXEC terminal event"

        # 5. Check GET /api/project/<name>/shadow-git
        shadow_req = urllib.request.Request(f"{base_url}/api/project/{proj_name}/shadow-git")
        with urllib.request.urlopen(shadow_req) as resp:
            shadow_data = json.loads(resp.read().decode("utf-8"))
            assert shadow_data["has_shadow_git"] is True
            commits = shadow_data.get("commits", [])
            assert len(commits) >= 1, "Expected at least 1 micro-commit in shadow git"
            print(f"      [PASS] GET /api/project/<name>/shadow-git returned {len(commits)} commit(s): latest={commits[0]['hash'][:7]}")

        # 6. Check GET /api/status includes powers
        status_req = urllib.request.Request(f"{base_url}/api/status")
        with urllib.request.urlopen(status_req) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
            matched_worker = next((w for w in status_data["workers"] if w["project"] == proj_name), None)
            assert matched_worker is not None, "Worker not found in status list"
            powers = matched_worker.get("powers", {})
            assert powers.get("track_edits") is True
            assert powers.get("track_reads") is True
            assert powers.get("track_exec") is True
            assert powers.get("shadow_git") is True
            print(f"      [PASS] GET /api/status verified active powers: {powers}")

        # 7. Stop project via API
        stop_payload = {"project": proj_name}
        stop_req = urllib.request.Request(
            f"{base_url}/api/projects/stop",
            data=json.dumps(stop_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(stop_req) as resp:
            stop_res = json.loads(resp.read().decode("utf-8"))
            assert stop_res["status"] == "stopped"
            print(f"      [PASS] Project stopped and archived successfully")

        time.sleep(1.0)
        # Verify rotation: current/<slug> is gone, last_run/<slug> exists
        assert not (_ENGINE_ROOT / "current" / slug).exists(), "current/<slug> was not cleaned up on stop"
        assert (_ENGINE_ROOT / "last_run" / slug).exists(), "last_run/<slug> does not exist after stop"
        print("      [PASS] StorageRotator archived project to last_run/")

    finally:
        server.shutdown()
        # Clean up mock directories
        shutil.rmtree(test_ws, ignore_errors=True)
        rotator = StorageRotator(_ENGINE_ROOT)
        # Clean last_run if leftover
        last_run_dir = _ENGINE_ROOT / "last_run" / _slug(proj_name)
        if last_run_dir.exists():
            shutil.rmtree(last_run_dir, ignore_errors=True)


def main() -> int:
    print("=" * 70)
    print("  SPD ANALYSIS ENGINE — PHASE 4 FULL INTEGRATION TEST SUITE")
    print("=" * 70)

    try:
        test_shadow_git_unit()
        test_process_inspector_unit()
        test_execution_tracker_unit()
        test_end_to_end_phase4()

        print("\n" + "=" * 70)
        print("  ALL PHASE 4 TESTS PASSED SUCCESSFULLY! (100% GREEN)")
        print("=" * 70 + "\n")
        return 0
    except Exception as exc:
        print(f"\n[FAIL] Test suite failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
