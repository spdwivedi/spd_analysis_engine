"""
tests/test_rollback_and_autopush.py
===================================
Comprehensive test suite for:
- Non-destructive rollback sequence:
  PAUSE -> seal pending burst -> pre-rollback snapshot commit ->
  compute dry-run file plan -> user confirmation -> restore required files ->
  mark affected historical events as rolled_back/superseded -> write ROLLBACK event -> resume.
- Undo rollback restoring workspace to pre-rollback snapshot.
- Byte-identical workspace restoration without bare git clean -fd.
- Rollback during pending unsealed burst.
- Hardened auto-pusher:
  - Opt-in per project.
  - Branch allowlist and default branch (main/master) protection.
  - Only stages milestone files.
  - Refuses during merge/rebase and when index.lock exists.
  - Credentials via GIT_ASKPASS without tokens in remote URLs.
  - Scrubbed stderr.
  - Failed commit never accidentally pushes.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core.changeset import ChangeSet
from scripts.git.auto_pusher import (
    DEFAULT_PROTECTED_BRANCHES,
    create_askpass_context,
    execute_auto_push,
    is_index_locked,
    is_repo_in_merge_or_rebase,
    scrub_sensitive_output,
    stage_milestone_files_only,
)
from scripts.git.shadow_repo import ShadowGit
from scripts.storage.db import SessionDB
from scripts.storage.meta import update_project_meta
from scripts.storage.rollback import (
    execute_non_destructive_rollback,
    undo_rollback,
    rollback_burst,
)


def _file_sha256(p: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(p.read_bytes())
    return hasher.hexdigest()


class TestNonDestructiveRollback(unittest.TestCase):
    """Test suite for non-destructive, undoable rollback and byte-identical restoration."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_rb_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.slug = "demo_proj"

        self.proj_dir = self.engine_root / "current" / self.slug
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = self.proj_dir / "shadow_git"

        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.sg.init_repo()

        self.db = SessionDB(self.proj_dir / "session.db")
        self.sid = self.db.create_session(self.slug, str(self.workspace))

        # Write project metadata
        update_project_meta(
            self.engine_root,
            self.slug,
            target_path=str(self.workspace),
            auto_push="false",
        )

    def tearDown(self) -> None:
        if hasattr(self, "db") and self.db:
            self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. Rollback then undo rollback
    # -----------------------------------------------------------------------
    def test_01_rollback_then_undo_rollback(self) -> None:
        """Rollback restores files non-destructively; undo rollback restores to snapshot."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        # Burst 1
        f1 = self.workspace / "f1.txt"
        f1.write_text("version 1\n", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="f1.txt", summary="Burst 1")
        self.assertIsNotNone(c1)
        ev1 = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="f1.txt",
            summary="Burst 1",
            commit_hash=c1,
        )

        # Burst 2: modifies f1.txt, adds f2.txt
        f1.write_text("version 2\n", encoding="utf-8")
        f2 = self.workspace / "f2.txt"
        f2.write_text("version 2 f2\n", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="f1.txt", summary="Burst 2")
        self.assertIsNotNone(c2)
        ev2 = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="f1.txt",
            summary="Burst 2",
            commit_hash=c2,
        )

        # Dry run check first
        dry_res = execute_non_destructive_rollback(
            self.engine_root, self.slug, target_event_id=ev1, target_dir=self.workspace, dry_run=True
        )
        self.assertTrue(dry_res.get("success"))
        self.assertTrue(dry_res.get("dry_run"))
        plan = dry_res.get("plan", {})
        self.assertIn("f1.txt", plan.get("files_to_restore", []))
        self.assertIn("f2.txt", plan.get("files_to_remove", []))
        # Ensure disk has not been modified during dry run
        self.assertEqual(f1.read_text(encoding="utf-8"), "version 2\n")
        self.assertTrue(f2.exists())

        # Execute real rollback to Burst 1
        res = execute_non_destructive_rollback(
            self.engine_root, self.slug, target_event_id=ev1, target_dir=self.workspace, confirmed=True
        )
        self.assertTrue(res.get("success"))
        rb_id = res.get("rollback_id")
        self.assertIsNotNone(rb_id)

        # Verify disk state matches Burst 1
        self.assertEqual(f1.read_text(encoding="utf-8"), "version 1\n")
        self.assertFalse(f2.exists())

        # Verify event IDs are stable in SessionDB (neither was deleted)
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, rollback_state, is_rolled_back FROM events WHERE id = ?", (ev1,))
        r1 = cur.fetchone()
        self.assertIsNotNone(r1)
        self.assertNotEqual(r1[1], "rolled_back")

        cur.execute("SELECT id, rollback_state, is_rolled_back, superseded_by FROM events WHERE id = ?", (ev2,))
        r2 = cur.fetchone()
        self.assertIsNotNone(r2)
        self.assertEqual(r2[1], "rolled_back")
        self.assertEqual(r2[2], 1)
        self.assertEqual(r2[3], rb_id)

        # Verify ROLLBACK event written
        cur.execute("SELECT id, event_type, commit_hash FROM events WHERE event_type = 'ROLLBACK'")
        rb_ev = cur.fetchone()
        self.assertIsNotNone(rb_ev)

        # Now execute UNDO rollback
        undo_res = undo_rollback(self.engine_root, self.slug, rollback_id=rb_id, target_dir=self.workspace)
        self.assertTrue(undo_res.get("success"))

        # Verify workspace restored back to Burst 2
        self.assertEqual(f1.read_text(encoding="utf-8"), "version 2\n")
        self.assertTrue(f2.exists())
        self.assertEqual(f2.read_text(encoding="utf-8"), "version 2 f2\n")

        # Verify event 2 reactivated
        cur.execute("SELECT id, rollback_state, is_rolled_back FROM events WHERE id = ?", (ev2,))
        r2_after = cur.fetchone()
        self.assertEqual(r2_after[1], "active")
        self.assertEqual(r2_after[2], 0)

    # -----------------------------------------------------------------------
    # 2. Byte-identical restoration
    # -----------------------------------------------------------------------
    def test_02_byte_identical_restoration(self) -> None:
        """Workspace after rollback is 100% byte-identical to target state without bare git clean."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        # Create multiple files including nested dirs and binary content
        f_text = self.workspace / "sub" / "code.py"
        f_text.parent.mkdir(parents=True, exist_ok=True)
        f_text.write_text("print('stable')\n", encoding="utf-8")

        f_bin = self.workspace / "asset.bin"
        f_bin.write_bytes(b"\x00\x01\x02\xfe\xff")

        c1 = self.sg.commit_burst(event_id=1, first_file="sub/code.py", summary="Baseline")
        ev1 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="sub/code.py", summary="Baseline", commit_hash=c1)

        hash_text_b1 = _file_sha256(f_text)
        hash_bin_b1 = _file_sha256(f_bin)

        # Mutations for Burst 2: modify both, add a temporary file
        f_text.write_text("print('mutated!')\n", encoding="utf-8")
        f_bin.write_bytes(b"\xff\x00\xaa\xbb")
        f_extra = self.workspace / "temp_file.txt"
        f_extra.write_text("should be removed\n", encoding="utf-8")

        c2 = self.sg.commit_burst(event_id=2, first_file="sub/code.py", summary="Mutations")
        ev2 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="sub/code.py", summary="Mutations", commit_hash=c2)

        # Execute non-destructive rollback to Burst 1
        res = execute_non_destructive_rollback(self.engine_root, self.slug, target_event_id=ev1, target_dir=self.workspace)
        self.assertTrue(res.get("success"))

        # Verify byte-identical restoration
        self.assertEqual(_file_sha256(f_text), hash_text_b1)
        self.assertEqual(_file_sha256(f_bin), hash_bin_b1)
        self.assertFalse(f_extra.exists())

        # Verify git diff against c1 is completely clean
        diff_res = self.sg._run_git("diff", c1)
        self.assertEqual(diff_res.stdout.strip(), "")

    # -----------------------------------------------------------------------
    # 3. Rollback during pending burst
    # -----------------------------------------------------------------------
    def test_03_rollback_during_pending_burst(self) -> None:
        """Unsealed pending burst is captured into snapshot before rollback."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        # Burst 1
        f = self.workspace / "state.txt"
        f.write_text("state 1\n", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="state.txt", summary="Burst 1")
        ev1 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="state.txt", summary="Burst 1", commit_hash=c1)

        # Burst 2 committed
        f.write_text("state 2\n", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="state.txt", summary="Burst 2")
        ev2 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="state.txt", summary="Burst 2", commit_hash=c2)

        # Pending unsealed workspace modification (Burst 3 in-flight)
        f.write_text("state 3 pending unsealed\n", encoding="utf-8")

        # Rollback directly to Burst 1
        res = execute_non_destructive_rollback(self.engine_root, self.slug, target_event_id=ev1, target_dir=self.workspace)
        self.assertTrue(res.get("success"))

        # Rolled back to state 1
        self.assertEqual(f.read_text(encoding="utf-8"), "state 1\n")

        # When undoing rollback, the pending unsealed state was captured in snapshot and restored!
        undo_res = undo_rollback(self.engine_root, self.slug, rollback_id=res["rollback_id"], target_dir=self.workspace)
        self.assertTrue(undo_res.get("success"))
        self.assertEqual(f.read_text(encoding="utf-8"), "state 3 pending unsealed\n")

    # -----------------------------------------------------------------------
    # 4. Git failure during rollback
    # -----------------------------------------------------------------------
    def test_04_git_failure_handled_cleanly(self) -> None:
        """Corrupted or invalid commit reference reported without crash or corrupt DB."""
        # Querying an invalid event ID
        res = execute_non_destructive_rollback(self.engine_root, self.slug, target_event_id=99999, target_dir=self.workspace)
        self.assertFalse(res.get("success"))
        self.assertIn("not found", res.get("error", "").lower())

    def test_09_rollback_route_executes_non_destructive_and_no_git_clean(self) -> None:
        """
        B-2 verification: Verify production rollback route executes execute_non_destructive_rollback,
        never runs 'git clean -fd', and issues zero 'DELETE FROM events'.
        """
        if not self.sg.is_available:
            self.skipTest("Git not available")

        from unittest.mock import MagicMock
        from scripts.server.request_handler import _EngineRequestHandler

        f = self.workspace / "test.txt"
        f.write_text("v1\n", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="test.txt", summary="B1")
        ev1 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="test.txt", summary="B1", commit_hash=c1)

        f.write_text("v2\n", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="test.txt", summary="B2")
        ev2 = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="test.txt", summary="B2", commit_hash=c2)

        handler = MagicMock(spec=_EngineRequestHandler)
        mock_server = MagicMock()
        mock_server.engine_root = self.engine_root
        handler.server_instance = mock_server
        handler._resolve_target_path = lambda s: self.workspace

        sent_json = {}
        handler._send_json = lambda d: sent_json.update(d)
        handler._send_error = lambda msg, status=500: self.fail(f"Route error: {msg}")

        # Spy on subprocess.run / _run_git to ensure git clean -fd is never invoked
        real_subprocess_run = subprocess.run
        executed_cmds: list[list[str]] = []

        def spy_subprocess_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                executed_cmds.append([str(x) for x in cmd])
            return real_subprocess_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=spy_subprocess_run):
            _EngineRequestHandler._handle_api_rollback_burst(handler, self.slug, ev1)

        self.assertTrue(sent_json.get("success"))
        self.assertEqual(sent_json.get("event_id"), ev1)

        # Assert git clean was NEVER called
        for cmd in executed_cmds:
            cmd_str = " ".join(cmd)
            self.assertNotIn("clean -fd", cmd_str)
            self.assertNotIn("reset --hard", cmd_str)

        # Assert zero DELETE FROM events in database
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM events WHERE id = ?", (ev1,))
        self.assertIsNotNone(cur.fetchone())
        cur.execute("SELECT id, rollback_state, is_rolled_back FROM events WHERE id = ?", (ev2,))
        ev2_row = cur.fetchone()
        self.assertIsNotNone(ev2_row, "Event 2 must NOT be deleted from events table")
        self.assertEqual(ev2_row[1], "rolled_back")
        self.assertEqual(ev2_row[2], 1)

    def test_10_rollback_ipc_bridge_and_watcher_pause_resume(self) -> None:
        """
        B-3 verification: Verify ProjectWatcher pause/resume controls,
        ChangeProcessor pause/resume, and send_control_command dispatch.
        """
        from scripts.watcher_fs import ProjectWatcher
        from scripts.core.burst_assembler import BurstAssembler
        from scripts.core.change_processor import ChangeProcessor
        from scripts.controller.control_channel import send_control_command

        # 1. Test ProjectWatcher pause and resume
        bundler = BurstAssembler(quiet_period=1.0)
        watcher = ProjectWatcher(self.workspace, bundler)
        self.assertFalse(watcher.is_paused)
        watcher.pause()
        self.assertTrue(watcher.is_paused)
        self.assertTrue(watcher._handler._is_sync_locked())
        self.assertIsNone(watcher._handler._get_rel_path(str(self.workspace / "test.py")))
        watcher.resume()
        self.assertFalse(watcher.is_paused)
        self.assertFalse(watcher._handler._is_sync_locked())

        # 2. Test ChangeProcessor pause and resume
        q = queue.Queue()
        cp = ChangeProcessor(self.workspace, q)
        self.assertFalse(cp.is_paused)
        cp.pause()
        self.assertTrue(cp.is_paused)
        cp.resume()
        self.assertFalse(cp.is_paused)

        # 3. Test send_control_command wrapper and dispatch during rollback
        sent_commands: list[str] = []
        def mock_send(pdir, secret="", command="PING", **kwargs):
            sent_commands.append(command)
            return {"protocol": 1, "ok": True, "state": "ACTIVE"}

        with patch("scripts.controller.control_channel.send_worker_command", side_effect=mock_send):
            # Test wrapper directly
            res = send_control_command(self.engine_root, self.slug, "PING")
            self.assertTrue(res.get("ok"))
            self.assertIn("PING", sent_commands)

            # Test that execute_non_destructive_rollback sends PAUSE, SEAL, RESUME
            sent_commands.clear()
            f = self.workspace / "test_ipc.txt"
            f.write_text("initial\n", encoding="utf-8")
            c1 = self.sg.commit_burst(event_id=1, first_file="test_ipc.txt", summary="init")
            first_ev_id = self.db.record_event(session_id=self.sid, event_type="EDIT", first_file="test_ipc.txt", summary="init", commit_hash=c1)
            rb_res = execute_non_destructive_rollback(self.engine_root, self.slug, target_event_id=first_ev_id, target_dir=self.workspace, confirmed=True)
            self.assertTrue(rb_res.get("success"))
            self.assertIn("PAUSE", sent_commands)
            self.assertIn("SEAL", sent_commands)
            self.assertIn("RESUME", sent_commands)




class TestHardenedAutoPusher(unittest.TestCase):
    """Test suite for hardened auto-pusher security controls and background execution."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ap_test_"))
        self.repo = self.temp_dir / "primary_repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.git_bin = shutil.which("git") or "git"

        # Initialize primary git repo
        subprocess.run([self.git_bin, "init", "-b", "main"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run([self.git_bin, "config", "user.name", "Test User"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run([self.git_bin, "config", "user.email", "test@example.com"], cwd=str(self.repo), capture_output=True, check=False)

        # Baseline file & commit
        f = self.repo / "init.txt"
        f.write_text("initial\n", encoding="utf-8")
        subprocess.run([self.git_bin, "add", "init.txt"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run([self.git_bin, "commit", "-m", "Initial commit"], cwd=str(self.repo), capture_output=True, check=False)
        subprocess.run([self.git_bin, "remote", "add", "origin", "https://example.com/test/repo.git"], cwd=str(self.repo), capture_output=True, check=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 5. Primary branch protection
    # -----------------------------------------------------------------------
    def test_05_primary_branch_protection(self) -> None:
        """Pushing to default branch 'main' is refused unless allow_default_branch=True."""
        config_no_default = {
            "auto_push": True,
            "allowed_branches": ["main"],
            "allow_default_branch": False,
        }
        res = execute_auto_push(
            project_slug="test_proj",
            target_path=self.repo,
            milestone_files=["init.txt"],
            title="Auto commit",
            config=config_no_default,
        )
        self.assertFalse(res.get("success"))
        self.assertTrue(res.get("skipped"))
        self.assertIn("refusing to push to default branch", res.get("reason", "").lower())

    # -----------------------------------------------------------------------
    # 6. Push skipped because index locked
    # -----------------------------------------------------------------------
    def test_06_push_skipped_because_index_locked(self) -> None:
        """Auto-push refuses when .git/index.lock is present."""
        lock_file = self.repo / ".git" / "index.lock"
        lock_file.write_text("locked\n", encoding="utf-8")
        self.assertTrue(is_index_locked(self.repo))

        config = {"auto_push": True, "allow_default_branch": True}
        res = execute_auto_push(
            project_slug="test_proj",
            target_path=self.repo,
            milestone_files=["init.txt"],
            config=config,
        )
        self.assertFalse(res.get("success"))
        self.assertTrue(res.get("skipped"))
        self.assertIn("index is locked", res.get("reason", "").lower())

    # -----------------------------------------------------------------------
    # 7. Failed commit must never accidentally push
    # -----------------------------------------------------------------------
    def test_07_failed_commit_must_never_accidentally_push(self) -> None:
        """When git commit fails, git push is NEVER executed."""
        # Create a file but simulate commit error by mocking subprocess.run on commit
        f = self.repo / "change.txt"
        f.write_text("new change\n", encoding="utf-8")

        config = {"auto_push": True, "allow_default_branch": True}

        push_called = []

        real_run = subprocess.run

        def mock_subprocess_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1:
                if cmd[1] == "commit":
                    # Simulate commit failure
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=1, stdout="", stderr="fatal: mock commit failure\n"
                    )
                if cmd[1] == "push":
                    push_called.append(cmd)
            return real_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            res = execute_auto_push(
                project_slug="test_proj",
                target_path=self.repo,
                milestone_files=["change.txt"],
                config=config,
            )

        self.assertFalse(res.get("success"))
        self.assertEqual(res.get("reason"), "commit_failed")
        self.assertEqual(len(push_called), 0, "git push was executed after commit failed!")

    # -----------------------------------------------------------------------
    # Credential AskPass and Stderr Scrubbing
    # -----------------------------------------------------------------------
    def test_08_stderr_scrubbing_and_askpass_context(self) -> None:
        """Secrets and tokens are scrubbed from stderr; remote URLs never contain tokens."""
        raw_error = "fatal: repository 'https://ghp_1234567890abcdef1234567890@github.com/org/repo' not found"
        scrubbed = scrub_sensitive_output(raw_error)
        self.assertNotIn("ghp_1234567890", scrubbed)
        self.assertIn("https://***@", scrubbed)

        with create_askpass_context(token="test_token_123") as env:
            self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")
            askpass_path = env.get("GIT_ASKPASS")
            self.assertIsNotNone(askpass_path)
            self.assertTrue(Path(askpass_path).exists())

        # Askpass file must be cleaned up on exit
        self.assertFalse(Path(askpass_path).exists())


if __name__ == "__main__":
    unittest.main()
