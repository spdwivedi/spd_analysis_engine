"""
tests/test_cumulative_history_and_specs.py
=========================================
Unit and integration tests for:
1. Cumulative project history consolidation across runs (no data wiped on resume).
2. IDE internal background command & shell noise suppression.
3. Persistent project metadata (GitHub remote URL, branch, push status).
4. Project Configuration & Specs REST endpoint (/api/project/<name>/specs).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.event_bundler import EventBundler
from scripts.shell_interceptor import is_ignored_command
from scripts.storage_rotator import (
    SessionDB,
    StorageRotator,
    consolidate_project_history,
    read_project_meta,
    update_project_meta,
)
from scripts.watcher_proc import ProcessInspector
from scripts.web_server import EngineWebServer
from scripts.trash_manager import TrashManager


class TestCumulativeHistoryAndSpecs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_cumul_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ide_noise_suppression(self):
        """Verify IDE internal background commands and files are completely ignored."""
        # Shell interceptor command filtering
        ignored_commands = [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command & { . 'c:\Users\user\.vscode\extensions\ms-vscode.powershell\shellIntegration.ps1' }",
            r"node c:\users\user\.vscode\extensions\jsonServerMain.js --stdio",
            r"node c:\users\user\.vscode\extensions\dbaeumer.vscode-eslint\server\out\eslintServer.js",
            r"node c:\users\user\.vscode\extensions\typescript-language-features\dist\tsserver.js",
            r"java -jar c:\users\user\.vscode\extensions\salesforce.apex-language-server\apex-jorje-lsp.jar",
            r"git credential-manager get",
            r"git-credential-wincred get",
        ]
        for cmd in ignored_commands:
            self.assertTrue(is_ignored_command(cmd), f"Should ignore command: {cmd}")

        allowed_commands = [
            "python manage.py runserver",
            "git status",
            "npm run build",
            "pytest tests/",
            "node server.js",
        ]
        for cmd in allowed_commands:
            self.assertFalse(is_ignored_command(cmd), f"Should NOT ignore command: {cmd}")

        # ProcessInspector handle filtering
        pw = ProcessInspector(target_path=self.engine_root)
        self.assertTrue(pw._is_ignored(Path(r"c:\users\user\.vscode\extensions\salesforce.apex-language-server\foo.txt")))
        self.assertTrue(pw._is_ignored(Path(r"c:\project\.sf\config.json")))
        self.assertTrue(pw._is_ignored(Path(r"c:\project\.sfdx\org.json")))
        self.assertTrue(pw._is_ignored(Path(r"c:\project\test.tmp")))
        self.assertFalse(pw._is_ignored(Path(r"c:\project\src\main.py")))

    def test_project_meta_persistence(self):
        """Verify project.meta stores and updates project settings across tiers."""
        meta = read_project_meta(self.engine_root, "demo_proj")
        self.assertEqual(meta, {})

        update_project_meta(
            self.engine_root,
            "demo_proj",
            target_path="d:/dev/demo",
            remote_url="https://github.com/org/demo.git",
            remote_branch="main",
            last_push_status="Success",
            last_push_time="2026-09-24T23:00:00+05:30",
            last_push_commit="a1b2c3d4e5f6",
        )

        read_back = read_project_meta(self.engine_root, "demo_proj")
        self.assertEqual(read_back["target_path"], "d:/dev/demo")
        self.assertEqual(read_back["remote_url"], "https://github.com/org/demo.git")
        self.assertEqual(read_back["remote_branch"], "main")
        self.assertEqual(read_back["last_push_status"], "Success")
        self.assertEqual(read_back["last_push_commit"], "a1b2c3d4e5f6")

    def test_cumulative_history_consolidation(self):
        """Verify multiple historical session runs are seamlessly consolidated into a canonical database."""
        slug = "my_app"
        # 1. Create run 1 in history
        run1_dir = self.engine_root / "history" / slug / "run_20260920_100000"
        run1_dir.mkdir(parents=True, exist_ok=True)
        db1 = SessionDB(run1_dir / "session.db")
        s1 = db1.create_session("my_app", "/tmp/app")
        db1.conn.execute("UPDATE sessions SET start_time = '2026-09-20T10:00:00Z' WHERE id = ?", (s1,))
        db1.conn.commit()
        ev1 = db1.record_event(s1, "EDIT", "app.py", "Burst 1 edit")
        db1.record_patch(ev1, "app.py", "@@ -1 +1 @@\n-old\n+new")
        db1.close()

        # 2. Create run 2 in last_run
        last_dir = self.engine_root / "last_run" / slug
        last_dir.mkdir(parents=True, exist_ok=True)
        db2 = SessionDB(last_dir / "session.db")
        s2 = db2.create_session("my_app", "/tmp/app")
        db2.conn.execute("UPDATE sessions SET start_time = '2026-09-21T10:00:00Z' WHERE id = ?", (s2,))
        db2.conn.commit()
        ev2 = db2.record_event(s2, "EDIT", "utils.py", "Burst 2 edit")
        db2.record_patch(ev2, "utils.py", "@@ -1 +1 @@\n-x\n+y")
        db2.close()

        # 3. Create run 3 in current
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)
        db3 = SessionDB(curr_dir / "session.db")
        s3 = db3.create_session("my_app", "/tmp/app")
        db3.conn.execute("UPDATE sessions SET start_time = '2026-09-22T10:00:00Z' WHERE id = ?", (s3,))
        db3.conn.commit()
        ev3 = db3.record_event(s3, "EDIT", "main.py", "Burst 3 edit")
        db3.record_patch(ev3, "main.py", "@@ -1 +1 @@\n+added")
        db3.close()

        # Consolidate history
        canon_path = consolidate_project_history(self.engine_root, slug)
        self.assertIsNotNone(canon_path)
        self.assertTrue(canon_path.exists())

        # Verify all 3 sessions and 3 events are present
        conn = sqlite3.connect(canon_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sessions")
        sess_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events")
        ev_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM patches")
        patch_count = cur.fetchone()[0]
        conn.close()

        self.assertEqual(sess_count, 3)
        self.assertEqual(ev_count, 3)
        self.assertEqual(patch_count, 3)

        # Idempotency check: running consolidation again shouldn't duplicate entries
        consolidate_project_history(self.engine_root, slug)
        conn = sqlite3.connect(canon_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        ev_count_after = cur.fetchone()[0]
        conn.close()
        self.assertEqual(ev_count_after, 3)

    def test_project_specs_api(self):
        """Verify GET /api/project/<name>/specs returns project configuration details."""
        server = EngineWebServer(engine_root=self.engine_root, port=8801)
        actual_port = server.start(background=True)
        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            # Create a project in last_run with project.meta
            proj_dir = self.engine_root / "last_run" / "specs_proj"
            proj_dir.mkdir(parents=True, exist_ok=True)
            update_project_meta(
                self.engine_root,
                "specs_proj",
                target_path="d:/workspace/test",
                remote_url="https://github.com/myorg/specs-test.git",
                remote_branch="main",
                last_push_status="Synced",
                last_push_time="2026-09-24T23:30:00+05:30",
            )

            req = urllib.request.Request(f"{base_url}/api/project/specs_proj/specs")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["project"], "specs_proj")
                self.assertEqual(data["target_path"], "d:/workspace/test")
                self.assertEqual(data["remote_url"], "https://github.com/myorg/specs-test.git")
                self.assertEqual(data["remote_branch"], "main")
                self.assertEqual(data["last_push_status"], "Synced")
                self.assertEqual(data["debounce"], 3.5)
                self.assertTrue(data["powers"]["track_edits"])
                self.assertTrue(data["powers"]["track_reads"])
                self.assertTrue(data["powers"]["track_exec"])
                self.assertTrue(data["powers"]["shadow_git"])
        finally:
            server.shutdown()

    def test_debounce_window_persistence_and_no_clobber(self):
        """Verify custom debounce window persists and is not clobbered by default 3.5s updates."""
        update_project_meta(
            self.engine_root,
            "debounce_proj",
            target_path="d:/workspace/debounce_proj",
            debounce_window=300.0,
            ide_profile="antigravity",
            powers={"track_edits": True, "track_reads": False, "track_exec": True, "shadow_git": True},
        )

        meta = read_project_meta(self.engine_root, "debounce_proj")
        self.assertEqual(meta["debounce_window"], "300.0")
        self.assertEqual(meta["debounce"], "300.0")
        self.assertEqual(meta["monitored_ai_env"], "Google Antigravity")
        powers = json.loads(meta["powers"])
        self.assertFalse(powers["track_reads"])
        self.assertTrue(powers["track_edits"])

        # Update without debounce - debounce_window must remain 300.0
        update_project_meta(self.engine_root, "debounce_proj", status="stopped")
        meta2 = read_project_meta(self.engine_root, "debounce_proj")
        self.assertEqual(meta2["debounce_window"], "300.0")

        # Update with default 3.5 without explicit_debounce - must NOT overwrite custom 300.0
        update_project_meta(self.engine_root, "debounce_proj", debounce=3.5)
        meta3 = read_project_meta(self.engine_root, "debounce_proj")
        self.assertEqual(meta3["debounce_window"], "300.0")

        # Update with explicit_debounce=True - must overwrite
        update_project_meta(self.engine_root, "debounce_proj", debounce=3.5, explicit_debounce=True)
        meta4 = read_project_meta(self.engine_root, "debounce_proj")
        self.assertEqual(meta4["debounce_window"], "3.5")

    def test_scope3_session_targeted_delete(self):
        """Verify Scope 3 allows soft-deleting a specific session (e.g. Session 2) while keeping other sessions."""
        proj_slug = "multi_session_proj"
        proj_dir = self.engine_root / "current" / proj_slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        db_path = proj_dir / "session.db"
        patches_dir = proj_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)

        db = SessionDB(db_path)
        s1 = db.create_session(proj_slug, str(proj_dir))
        e1 = db.record_event(s1, "EDIT", "file1.py", "S1 edit")
        db.record_patch(e1, "file1.py", "diff s1")
        (patches_dir / f"event_{e1}.patch").write_text("patch 1 content", encoding="utf-8")

        s2 = db.create_session(proj_slug, str(proj_dir))
        e2 = db.record_event(s2, "EDIT", "file2.py", "S2 edit (fragmented burst)")
        db.record_patch(e2, "file2.py", "diff s2")
        (patches_dir / f"event_{e2}.patch").write_text("patch 2 content", encoding="utf-8")
        db.close()

        update_project_meta(self.engine_root, proj_slug, target_path=str(proj_dir), debounce_window=300.0)

        trash_mgr = TrashManager(self.engine_root)
        res = trash_mgr.delete_project(proj_slug, scope="local_session", session_id=s2)
        self.assertTrue(res["success"])
        self.assertEqual(res["session_id"], s2)

        # Check session.db: Session 1 still exists, Session 2 is deleted
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM sessions WHERE id = ?", (s1,))
        self.assertIsNotNone(cur.fetchone())
        cur.execute("SELECT id FROM sessions WHERE id = ?", (s2,))
        self.assertIsNone(cur.fetchone())
        cur.execute("SELECT id FROM events WHERE session_id = ?", (s2,))
        self.assertEqual(len(cur.fetchall()), 0)
        conn.close()

        # Patch 1 still exists on disk, Patch 2 removed
        self.assertTrue((patches_dir / f"event_{e1}.patch").exists())
        self.assertFalse((patches_dir / f"event_{e2}.patch").exists())

        # Metadata still intact with debounce_window
        meta = read_project_meta(self.engine_root, proj_slug)
        self.assertEqual(meta["debounce_window"], "300.0")


if __name__ == "__main__":
    unittest.main()
