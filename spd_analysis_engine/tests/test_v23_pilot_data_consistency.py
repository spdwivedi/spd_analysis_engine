"""
tests/test_v23_pilot_data_consistency.py
=========================================
Comprehensive pilot data consistency regression suite.

Verifies the strict invariant:
For every burst that creates an effective Git commit:
    Git changed files ≈ SQLite ≈ ChangeSet ≈ API ≈ UI

Tested Scenarios:
1. One text file change
2. One empty file creation
3. One file deletion
4. One binary file change
5. One file rename
6. One .gitignore change
7. Pilot project historical event consistency (Burst #4)
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.core.change_processor import ChangeProcessor
from scripts.core.changeset import ChangeSet
from scripts.diff_calc import DiffCalculator
from scripts.git.shadow_commit import get_shadow_git_env
from scripts.git_shadow import ShadowGit
from scripts.storage.compatibility_reader import read_event_detail, read_events_summary
from scripts.storage.db import SessionDB


def simulate_timeline_ui_render(ev: dict[str, Any], git_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Python reference simulation of the logic in web/js/views/timeline.js.
    Mirrors:
      - entrypointFile resolution
      - patchList resolution with ev.patches -> ev.files -> ev.first_file_touched fallbacks
      - gitTagHtml resolution
      - fileChipsHtml generation
      - totalPatchedCount computation
    """
    git_events = git_events or []
    entrypoint_file = ev.get("first_file_touched")
    if not entrypoint_file and ev.get("patches"):
        entrypoint_file = ev["patches"][0].get("file_path")
    elif not entrypoint_file and ev.get("files"):
        f0 = ev["files"][0]
        entrypoint_file = f0.get("file_path") if isinstance(f0, dict) else f0

    patch_list: list[dict[str, Any]] = []
    if isinstance(ev.get("patches"), list) and len(ev["patches"]) > 0:
        patch_list = ev["patches"]
    elif isinstance(ev.get("files"), list) and len(ev["files"]) > 0:
        patch_list = [f if isinstance(f, dict) else {"file_path": f} for f in ev["files"]]
    elif ev.get("first_file_touched"):
        patch_list = [{"file_path": ev["first_file_touched"]}]

    # Match git event
    eid = ev.get("id") or 0
    git_match = next((g for g in git_events if g.get("id") == eid + 1 or (ev.get("summary") and ev["summary"] in (g.get("summary") or ""))), None)
    commit_hash_val = ev.get("commit_hash") or (git_match.get("commit_hash") if git_match else None)
    git_tag_text = commit_hash_val[:7] if commit_hash_val else None

    # File chips
    chips = []
    for p in patch_list:
        fp = p.get("file_path", "")
        is_entry = (fp == entrypoint_file)
        chips.append(f"chip:{fp}{':entry' if is_entry else ''}")
    file_chips_str = " ".join(chips)

    total_patched = ev.get("files_count") if (isinstance(ev.get("files_count"), int) and ev["files_count"] > 0) else len(patch_list)
    has_empty_hint = (len(chips) == 0)
    has_zero_patched = (total_patched == 0)

    return {
        "entrypoint": entrypoint_file,
        "git_tag": git_tag_text,
        "patch_list_len": len(patch_list),
        "total_patched": total_patched,
        "chips": chips,
        "file_chips_str": file_chips_str,
        "has_empty_hint": has_empty_hint,
        "has_zero_patched": has_zero_patched,
    }


class TestPilotDataConsistency(unittest.TestCase):
    """Verify consistency across SQLite, ChangeSet, Shadow Git, API, and UI."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="spd_consistency_")
        self.ws_dir = Path(self.temp_dir) / "workspace"
        self.ws_dir.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = Path(self.temp_dir) / "shadow_git"
        self.db_path = Path(self.temp_dir) / "session.db"

        self.shadow_git = ShadowGit(self.shadow_dir, self.ws_dir)
        self.shadow_git.init_repo()

        self.session_db = SessionDB(self.db_path)
        self.session_id = self.session_db.create_session("test_proj", str(self.ws_dir))
        self.baseline_dir = Path(self.temp_dir) / "baseline"
        self.diff_calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.ws_dir)
        self.downstream_queue = queue.Queue()
        self.change_processor = ChangeProcessor(
            root_path=self.ws_dir,
            downstream_queue=self.downstream_queue,
        )

    def tearDown(self) -> None:
        try:
            self.session_db.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _execute_burst(self, files_touched: list[str]) -> tuple[dict[str, Any], ChangeSet, str]:
        """Process changes, record to SessionDB, commit to Shadow Git, and link."""
        bundle_in = {
            "files_touched": files_touched,
            "files": files_touched,
            "first_file": files_touched[0],
        }
        self.change_processor._process_single_bundle(bundle_in)

        changeset = bundle_in.get("changeset")
        effective_files = bundle_in.get("effective_changes", files_touched)

        diffs = []
        for fp in files_touched:
            diff_text = self.diff_calc.compute_diff(fp)
            diffs.append((fp, diff_text))
            abs_p = self.ws_dir / fp
            content = abs_p.read_text(encoding="utf-8", errors="replace") if abs_p.is_file() else None
            self.diff_calc.update_baseline(fp, content)

        summary = f"Burst edit across {len(effective_files)} file(s)"
        eid = self.session_db.record_event(
            self.session_id,
            "EDIT",
            files_touched[0],
            summary,
            effective_files=json.dumps(effective_files),
        )
        for fp, dtext in diffs:
            self.session_db.record_patch(eid, fp, dtext)

        burst_res = self.shadow_git.commit_changeset(changeset, summary=summary, first_file=files_touched[0])
        commit_hash = burst_res.commit_hash or ""

        if commit_hash:
            self.session_db.conn.execute(
                "UPDATE events SET commit_hash = ?, git_state = ? WHERE id = ?",
                (commit_hash, burst_res.state, eid),
            )
            self.session_db.conn.commit()
            self.session_db.record_event(
                self.session_id,
                "GIT",
                files_touched[0],
                f"Micro-commit {commit_hash[:7]}: {summary}",
                commit_hash=commit_hash,
            )

        return bundle_in, changeset, commit_hash

    def _get_git_changed_files(self, commit_hash: str) -> list[str]:
        res = self.shadow_git._run_git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit_hash)
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]

    def test_01_text_file_change(self) -> None:
        """1. Text file modification: verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        tf = self.ws_dir / "service.py"
        tf.write_text("def run():\n    return 1\n", encoding="utf-8")
        self.change_processor.manifest.scan_all()
        self.diff_calc.update_baseline("service.py", tf.read_text(encoding="utf-8"))

        tf.write_text("def run():\n    return 2\n# update\n", encoding="utf-8")
        _, cs, commit_hash = self._execute_burst(["service.py"])

        self.assertIn("service.py", cs.effective_files)
        git_files = self._get_git_changed_files(commit_hash)
        self.assertIn("service.py", git_files)

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertEqual(edit_ev["files_count"], 1)
        self.assertIn("service.py", edit_ev["files"])
        self.assertEqual(edit_ev["commit_hash"], commit_hash)

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])
        self.assertEqual(ui["total_patched"], 1)
        self.assertIn("chip:service.py:entry", ui["chips"])

    def test_02_empty_file_creation(self) -> None:
        """2. Empty file creation (0-byte): verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        (self.ws_dir / "__init__.py").touch()
        _, cs, commit_hash = self._execute_burst(["__init__.py"])

        self.assertIn("__init__.py", cs.effective_files)
        git_files = self._get_git_changed_files(commit_hash)
        self.assertIn("__init__.py", git_files)

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertEqual(edit_ev["files_count"], 1)
        self.assertIn("__init__.py", edit_ev["files"])

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])
        self.assertEqual(ui["total_patched"], 1)

    def test_03_file_deletion(self) -> None:
        """3. File deletion: verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        df = self.ws_dir / "obsolete.py"
        df.write_text("def old(): pass\n", encoding="utf-8")
        self.change_processor.manifest.scan_all()
        cs_init = ChangeSet("burst_init", 1, 1, ["obsolete.py"], ["obsolete.py"])
        self.shadow_git.commit_changeset(cs_init, summary="Initial obsolete")
        self.diff_calc.update_baseline("obsolete.py", df.read_text(encoding="utf-8"))

        df.unlink()
        _, cs, commit_hash = self._execute_burst(["obsolete.py"])

        self.assertIn("obsolete.py", cs.effective_files)
        git_files = self._get_git_changed_files(commit_hash)
        self.assertIn("obsolete.py", git_files)

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertIn("obsolete.py", edit_ev["files"])

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])

    def test_04_binary_change(self) -> None:
        """4. Binary file change: verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        bf = self.ws_dir / "asset.bin"
        bf.write_bytes(b"\x00\x01\x02\x03\xff")
        self.change_processor.manifest.scan_all()
        cs_init = ChangeSet("burst_init", 1, 1, ["asset.bin"], ["asset.bin"])
        self.shadow_git.commit_changeset(cs_init, summary="Initial binary")

        bf.write_bytes(b"\x00\x01\x02\x03\xaa\xbb\xcc")
        _, cs, commit_hash = self._execute_burst(["asset.bin"])

        self.assertIn("asset.bin", cs.effective_files)
        git_files = self._get_git_changed_files(commit_hash)
        self.assertIn("asset.bin", git_files)

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertIn("asset.bin", edit_ev["files"])

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])

    def test_05_file_rename(self) -> None:
        """5. File rename: verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        rf_old = self.ws_dir / "worker_old.py"
        content = "# Worker logic\ndef do_work(): return True\n"
        rf_old.write_text(content, encoding="utf-8")
        self.change_processor.manifest.scan_all()
        cs_init = ChangeSet("burst_init", 1, 1, ["worker_old.py"], ["worker_old.py"])
        self.shadow_git.commit_changeset(cs_init, summary="Init rename source")
        self.diff_calc.update_baseline("worker_old.py", content)

        rf_old.unlink()
        rf_new = self.ws_dir / "worker_new.py"
        rf_new.write_text(content, encoding="utf-8")
        _, cs, commit_hash = self._execute_burst(["worker_old.py", "worker_new.py"])

        git_files = self._get_git_changed_files(commit_hash)
        self.assertTrue(any("worker" in f for f in git_files))

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertGreaterEqual(edit_ev["files_count"], 1)

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])

    def test_06_gitignore_change(self) -> None:
        """6. .gitignore change: verify SQLite ≈ ChangeSet ≈ Git ≈ API ≈ UI."""
        gi = self.ws_dir / ".gitignore"
        gi.write_text("*.tmp\n*.log\n", encoding="utf-8")
        self.change_processor.manifest.scan_all()
        cs_init = ChangeSet("burst_init", 1, 1, [".gitignore"], [".gitignore"])
        self.shadow_git.commit_changeset(cs_init, summary="Init gitignore")
        self.diff_calc.update_baseline(".gitignore", "*.tmp\n*.log\n")

        gi.write_text("*.tmp\n*.log\n*.bak\n.env\n", encoding="utf-8")
        _, cs, commit_hash = self._execute_burst([".gitignore"])

        self.assertIn(".gitignore", cs.effective_files)
        git_files = self._get_git_changed_files(commit_hash)
        self.assertIn(".gitignore", git_files)

        summaries, _ = read_events_summary(self.session_db.conn)
        edit_ev = next(s for s in summaries if s["event_type"] == "EDIT")
        self.assertIn(".gitignore", edit_ev["files"])
        self.assertEqual(edit_ev["commit_hash"], commit_hash)

        ui = simulate_timeline_ui_render(edit_ev)
        self.assertFalse(ui["has_empty_hint"])
        self.assertFalse(ui["has_zero_patched"])
        self.assertEqual(ui["total_patched"], 1)
        self.assertIn("chip:.gitignore:entry", ui["chips"])

    def test_07_pilot_project_historical_event_consistency(self) -> None:
        """7. Verify Burst #4 in actual pilot_app database resolves files and never reports 0 touched."""
        pilot_db = Path("current/pilot_app/session.db")
        if not pilot_db.exists():
            self.skipTest("pilot_app/session.db not present on disk")

        conn = SessionDB(pilot_db).conn
        self.assertIsNotNone(conn)
        summaries, _ = read_events_summary(conn)
        ev4 = next((s for s in summaries if s["id"] == 4), None)
        self.assertIsNotNone(ev4)

        self.assertEqual(ev4["first_file_touched"], ".gitignore")
        self.assertEqual(ev4["files_count"], 1)
        self.assertEqual(ev4["files"], [".gitignore"])
        self.assertEqual(ev4["commit_hash"], "e2c97ce4d166e1f002b9aa42a8b90d61e26f6928")

        ui = simulate_timeline_ui_render(ev4)
        self.assertFalse(ui["has_empty_hint"], "UI must not report 'No files touched'")
        self.assertFalse(ui["has_zero_patched"], "UI must not report '0 files patched'")
        self.assertEqual(ui["total_patched"], 1)
        self.assertIn("chip:.gitignore:entry", ui["chips"])
        self.assertEqual(ui["git_tag"], "e2c97ce")
