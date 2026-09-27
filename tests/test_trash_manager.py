"""
tests/test_trash_manager.py
===========================
Comprehensive Test Suite for Granular Deletion and Trash Bin (Soft-Delete & Restore) Engine.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.trash_manager import (
    TrashManager,
    SCOPE_SELECTIVE_BURSTS,
    SCOPE_METADATA_ONLY,
    SCOPE_LOCAL_SESSION,
    SCOPE_SESSION_AND_SHADOW_GIT,
    SCOPE_COMPLETE_ERASE,
)
from scripts.storage_rotator import SessionDB, update_project_meta, read_project_meta
from scripts.web_server import EngineWebServer


class TestTrashManagerCore(unittest.TestCase):
    """Unit tests for TrashManager deletion scopes, manifests, restore, and purge."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_trash_test_"))
        self.current_dir = self.temp_dir / "current"
        self.current_dir.mkdir(parents=True, exist_ok=True)
        self.trash_mgr = TrashManager(engine_root=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_project(self, name: str = "demo_proj") -> Path:
        proj_dir = self.current_dir / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        db_path = proj_dir / "session.db"
        db = SessionDB(db_path)
        sid = db.create_session(name, str(proj_dir))
        eid1 = db.record_event(sid, "EDIT", "app.py", "Burst 1")
        db.record_patch(eid1, "app.py", "+print('burst 1')\n")
        eid2 = db.record_event(sid, "EDIT", "index.html", "Burst 2")
        db.record_patch(eid2, "index.html", "+<h1>burst 2</h1>\n")
        eid3 = db.record_event(sid, "EDIT", "style.css", "Burst 3")
        db.record_patch(eid3, "style.css", "+body { margin: 0; }\n")
        db.close()

        (proj_dir / "worker.log").write_text("Worker running\n", encoding="utf-8")
        (proj_dir / "worker.status.json").write_text(json.dumps({"alive": False}), encoding="utf-8")
        update_project_meta(self.temp_dir, name, remote_url="https://github.com/org/repo.git", remote_branch="main")
        return proj_dir

    def test_scope_selective_bursts(self) -> None:
        """Verify SCOPE_SELECTIVE_BURSTS backs up and deletes specific bursts."""
        proj_dir = self._create_mock_project("selective_proj")
        res = self.trash_mgr.move_to_trash("selective_proj", scope=SCOPE_SELECTIVE_BURSTS, burst_ids=[1, 3])
        self.assertTrue(res["success"])
        self.assertEqual(res["deleted_bursts_count"], 2)

        # Check DB: only burst #2 should remain
        db = SessionDB(proj_dir / "session.db")
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM events ORDER BY id ASC")
        remaining = [r["id"] for r in cur.fetchall()]
        self.assertEqual(remaining, [2])
        db.close()

        # Check manifest in trash
        trash_items = self.trash_mgr.list_trash()
        self.assertEqual(len(trash_items), 1)
        item = trash_items[0]
        self.assertEqual(item["scope"], SCOPE_SELECTIVE_BURSTS)
        self.assertEqual(item["metadata"]["burst_ids"], [1, 3])

        # Test restore
        restored = self.trash_mgr.restore(item["trash_id"])
        self.assertTrue(restored["success"])

        # Check DB after restore: bursts 1, 2, 3 must be present
        db = SessionDB(proj_dir / "session.db")
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM events ORDER BY id ASC")
        all_bursts = [r["id"] for r in cur.fetchall()]
        self.assertEqual(all_bursts, [1, 2, 3])
        db.close()

    def test_scope_metadata_only(self) -> None:
        """Verify SCOPE_METADATA_ONLY clears project.meta and runtime state."""
        self._create_mock_project("meta_proj")
        res = self.trash_mgr.move_to_trash("meta_proj", scope=SCOPE_METADATA_ONLY)
        self.assertTrue(res["success"])

        meta = read_project_meta(self.temp_dir, "meta_proj")
        self.assertEqual(meta, {})

        trash_items = self.trash_mgr.list_trash()
        self.assertEqual(len(trash_items), 1)
        self.assertEqual(trash_items[0]["scope"], SCOPE_METADATA_ONLY)

        # Restore
        restored = self.trash_mgr.restore(trash_items[0]["trash_id"])
        self.assertTrue(restored["success"])
        restored_meta = read_project_meta(self.temp_dir, "meta_proj")
        self.assertEqual(restored_meta.get("remote_url"), "https://github.com/org/repo.git")

    def test_scope_local_session(self) -> None:
        """Verify SCOPE_LOCAL_SESSION moves session.db and logs to trash."""
        proj_dir = self._create_mock_project("session_proj")
        self.assertTrue((proj_dir / "session.db").exists())
        self.assertTrue((proj_dir / "worker.log").exists())

        res = self.trash_mgr.move_to_trash("session_proj", scope=SCOPE_LOCAL_SESSION)
        self.assertTrue(res["success"])
        self.assertFalse((proj_dir / "session.db").exists())
        self.assertFalse((proj_dir / "worker.log").exists())

        trash_items = self.trash_mgr.list_trash()
        self.assertEqual(len(trash_items), 1)
        self.assertEqual(trash_items[0]["scope"], SCOPE_LOCAL_SESSION)

        # Restore
        restored = self.trash_mgr.restore(trash_items[0]["trash_id"])
        self.assertTrue(restored["success"])
        self.assertTrue((proj_dir / "session.db").exists())
        self.assertTrue((proj_dir / "worker.log").exists())

    def test_scope_complete_erase_and_purge(self) -> None:
        """Verify SCOPE_COMPLETE_ERASE removes all project folders and purge removes from trash."""
        proj_dir = self._create_mock_project("erase_proj")
        self.assertTrue(proj_dir.exists())

        res = self.trash_mgr.move_to_trash("erase_proj", scope=SCOPE_COMPLETE_ERASE)
        self.assertTrue(res["success"])
        self.assertFalse(proj_dir.exists())

        trash_items = self.trash_mgr.list_trash()
        self.assertEqual(len(trash_items), 1)
        trash_id = trash_items[0]["trash_id"]

        # Purge item
        purge_res = self.trash_mgr.purge(trash_id)
        self.assertTrue(purge_res)
        self.assertEqual(len(self.trash_mgr.list_trash()), 0)

    def test_purge_with_readonly_git_objects(self) -> None:
        """Verify purge cleanly unlocks and removes git objects marked as read-only."""
        trash_id = "20260925_042000_git_readonly_proj_git"
        entry_dir = self.trash_mgr.trash_dir / trash_id
        entry_dir.mkdir(parents=True, exist_ok=True)

        # Create nested git object structure
        obj_dir = entry_dir / "shadow_git" / "objects" / "1f"
        obj_dir.mkdir(parents=True, exist_ok=True)
        pack_dir = entry_dir / "shadow_git" / "objects" / "pack"
        pack_dir.mkdir(parents=True, exist_ok=True)

        obj_file = obj_dir / "0a52fc920fd623a1082ba579c4bcccb3935e98"
        obj_file.write_text("git binary blob mock", encoding="utf-8")
        pack_file = pack_dir / "pack-12345678.idx"
        pack_file.write_text("git pack index mock", encoding="utf-8")

        # Mark all files and dirs as read-only
        os.chmod(obj_file, stat.S_IREAD)
        os.chmod(pack_file, stat.S_IREAD)

        # Confirm read-only was applied (Windows sets S_IWRITE bit to 0)
        self.assertTrue(entry_dir.exists())

        # Purge must unlock and completely delete
        res = self.trash_mgr.purge(trash_id)
        self.assertTrue(res["success"])
        self.assertFalse(entry_dir.exists())

    def test_orphan_trash_fallback_manifest(self) -> None:
        """Verify list_trash synthesizes fallback manifest for directories lacking manifest.json."""
        orphan_id = "20260925_043000_orphan_project_session"
        orphan_dir = self.trash_mgr.trash_dir / orphan_id
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "session.db").write_text("dummy db", encoding="utf-8")

        items = self.trash_mgr.list_trash()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["trash_id"], orphan_id)
        self.assertEqual(item["project_name"], "orphan_project")
        self.assertEqual(item["scope"], SCOPE_LOCAL_SESSION)
        self.assertTrue(item["folder_size_bytes"] > 0)

        # Purge orphan directory
        res = self.trash_mgr.purge(orphan_id)
        self.assertTrue(res["success"])
        self.assertFalse(orphan_dir.exists())
        self.assertEqual(len(self.trash_mgr.list_trash()), 0)


class TestTrashWebServerEndpoints(unittest.TestCase):
    """Integration test suite for Web Server project delete and trash bin REST endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_web_trash_test_"))
        cls.current_dir = cls.temp_dir / "current"
        cls.current_dir.mkdir(parents=True, exist_ok=True)
        cls.ai_data_dir = cls.temp_dir / "ai_data"
        cls.ai_data_dir.mkdir(parents=True, exist_ok=True)
        (cls.ai_data_dir / "models_config.json").write_text(json.dumps({
            "preferred_provider": "heuristic",
            "gemini_api_key": "",
        }), encoding="utf-8")

        # Create dummy project
        cls.proj_dir = cls.current_dir / "web_demo"
        cls.proj_dir.mkdir(parents=True, exist_ok=True)
        db = SessionDB(cls.proj_dir / "session.db")
        sid = db.create_session("web_demo", str(cls.proj_dir))
        eid = db.record_event(sid, "EDIT", "index.js", "Init JS")
        db.record_patch(eid, "index.js", "+console.log('hi');\n")
        db.close()

        # Start web server
        cls.server = EngineWebServer(
            engine_root=cls.temp_dir,
            host="127.0.0.1",
            port=0,
            web_dir=cls.temp_dir / "web",
        )
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _delete(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="DELETE")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_delete_project_and_trash_lifecycle(self) -> None:
        """Verify /api/project/<name>/delete, /api/trash, restore, and purge-all."""
        # 1. Initially trash is empty
        t_init = self._get("/api/trash")
        self.assertTrue(t_init["success"])
        self.assertEqual(t_init["count"], 0)

        # 2. Soft-delete project
        del_res = self._post("/api/project/web_demo/delete", {
            "scope": "local_session",
        })
        self.assertTrue(del_res["success"])
        self.assertEqual(del_res["project"], "web_demo")

        # 3. Check trash has 1 item
        t_after = self._get("/api/trash")
        self.assertTrue(t_after["success"])
        self.assertEqual(t_after["count"], 1)
        trash_id = t_after["items"][0]["trash_id"]

        # 4. Restore trash
        rest_res = self._post(f"/api/trash/restore/{trash_id}", {})
        self.assertTrue(rest_res["success"])

        # Check file is back
        self.assertTrue((self.proj_dir / "session.db").exists())

        # Check trash is now 0
        t_after_rest = self._get("/api/trash")
        self.assertEqual(t_after_rest["count"], 0)

        # 5. Delete again and test purge-all
        self._post("/api/project/web_demo/delete", {"scope": "complete_erase"})
        self.assertEqual(self._get("/api/trash")["count"], 1)

        p_all = self._delete("/api/trash/purge-all")
        self.assertTrue(p_all["success"])
        self.assertEqual(p_all["purged_count"], 1)
        self.assertEqual(self._get("/api/trash")["count"], 0)

    def test_web_api_purge_url_encoded_and_remaining_count(self) -> None:
        """Verify DELETE /api/trash/purge/<trash_id> handles URL encoding and returns remaining_count."""
        # Create two mock items in trash
        trash_dir = self.temp_dir / "trash"
        item1 = trash_dir / "20260925_044000_first%20space_session"
        item1.mkdir(parents=True, exist_ok=True)
        (item1 / "manifest.json").write_text(json.dumps({
            "trash_id": "20260925_044000_first%20space_session",
            "project_name": "first space",
            "scope": "local_session",
        }), encoding="utf-8")

        item2 = trash_dir / "20260925_044100_second_session"
        item2.mkdir(parents=True, exist_ok=True)
        (item2 / "manifest.json").write_text(json.dumps({
            "trash_id": "20260925_044100_second_session",
            "project_name": "second",
            "scope": "local_session",
        }), encoding="utf-8")

        # Purge first item with URL encoded path
        encoded_id = "20260925_044000_first%2520space_session"
        del_res = self._delete(f"/api/trash/purge/{encoded_id}")
        self.assertTrue(del_res["success"])
        self.assertEqual(del_res["remaining_count"], 1)

        # Purge second item
        del_res2 = self._delete("/api/trash/purge/20260925_044100_second_session")
        self.assertTrue(del_res2["success"])
        self.assertEqual(del_res2["remaining_count"], 0)


if __name__ == "__main__":
    unittest.main()
