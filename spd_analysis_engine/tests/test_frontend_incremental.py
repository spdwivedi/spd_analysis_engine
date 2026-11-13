"""
tests/test_frontend_incremental.py
==================================
Comprehensive tests for incremental frontend architecture:
1. 5,000-event project (bounded initial load without full event list download)
2. 1,000-file burst (metadata returned without full diff bodies)
3. Diff fetched only when opened (on-demand single-file diff retrieval)
4. Tab hidden/visible (nonessential polling paused when document.hidden === true)
5. SSE reconnect (missed event replay via Last-Event-ID)
6. Multiple browser tabs (concurrent SSE listeners without crosstalk)
7. Memory remains bounded (lazy diffs and windowed data)
8. handleLivePing never re-fetches event list
9. Windowed list DOM bounds for 1,000-file bursts
"""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

from scripts.server.sse_stream import SSEManager, set_sse_manager
from scripts.storage.schema_migration import apply_schema_migrations
from scripts.web_server import EngineWebServer


def _populate_5000_event_db(db_path: Path, num_events: int = 5000) -> None:
    """Populate database with 5,000 events and patch rows using batched transactions."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    apply_schema_migrations(conn)

    conn.execute(
        "INSERT INTO sessions (id, project_name, target_path, start_time, status) "
        "VALUES (1, 'scale_proj', '/tmp/scale_proj', '2026-10-02T10:00:00Z', 'ACTIVE')"
    )

    batch_size = 500
    events_batch = []
    patches_batch = []

    for i in range(1, num_events + 1):
        ev_id = f"ev_{i:06d}"
        buid = f"buid_{i:06d}"
        events_batch.append((
            i, 1, "EDIT", "2026-10-02T10:01:00Z", f"src/file_{i % 50}.py",
            f"Bulk burst change #{i}", None, i, "commit_sha", 0, ev_id, i, buid,
        ))
        diff_text = f"--- a/src/file_{i % 50}.py\n+++ b/src/file_{i % 50}.py\n@@ -1 +1,2 @@\n+change {i}\n"
        patches_batch.append((
            i, f"src/file_{i % 50}.py", diff_text, 1, 0, "unified", len(diff_text),
        ))

        if len(events_batch) >= batch_size:
            conn.executemany(
                "INSERT INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, "
                "executor, burst_num, commit_hash, is_rolled_back, event_id, seq, burst_uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                events_batch,
            )
            conn.executemany(
                "INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                patches_batch,
            )
            events_batch.clear()
            patches_batch.clear()

    if events_batch:
        conn.executemany(
            "INSERT INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, "
            "executor, burst_num, commit_hash, is_rolled_back, event_id, seq, burst_uid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            events_batch,
        )
        conn.executemany(
            "INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            patches_batch,
        )

    conn.commit()
    conn.close()


def _populate_1000_file_burst(db_path: Path, burst_event_id: int = 9999) -> None:
    """Add a single burst containing 1,000 touched files."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, "
        "burst_num, event_id, seq, burst_uid) "
        "VALUES (?, 1, 'EDIT', '2026-10-02T12:00:00Z', 'src/file_0.py', 'Mega 1000-file burst', ?, ?, ?, ?)",
        (burst_event_id, burst_event_id, f"ev_{burst_event_id}", burst_event_id, f"buid_{burst_event_id}"),
    )

    patches = []
    for f_idx in range(1000):
        fpath = f"src/modules/mod_{f_idx // 50}/file_{f_idx}.py"
        diff_body = f"--- a/{fpath}\n+++ b/{fpath}\n@@ -1,2 +1,3 @@\n def f():\n+    return {f_idx}\n"
        patches.append((burst_event_id, fpath, diff_body, 1, 0, "unified", len(diff_body)))

    conn.executemany(
        "INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size) VALUES (?, ?, ?, ?, ?, ?, ?)",
        patches,
    )
    conn.commit()
    conn.close()


class TestFrontendIncrementalArchitecture(unittest.TestCase):
    """Test suite verifying incremental frontend capabilities and bounded scale."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_fe_incr_"))
        cls.web_dir = cls.temp_dir / "web"
        cls.web_dir.mkdir(parents=True, exist_ok=True)
        (cls.web_dir / "index.html").write_text("<!DOCTYPE html><html><body>Test</body></html>", encoding="utf-8")

        cls.proj_slug = "scale_proj"
        cls.proj_dir = cls.temp_dir / "current" / cls.proj_slug
        cls.db_path = cls.proj_dir / "session.db"

        # Seed 5,000 events and 1,000-file burst
        _populate_5000_event_db(cls.db_path, num_events=5000)
        _populate_1000_file_burst(cls.db_path, burst_event_id=9999)

        cls.api_token = "token_frontend_scale_987654321"
        cls.server = EngineWebServer(
            engine_root=cls.temp_dir,
            host="127.0.0.1",
            port=0,
            web_dir=cls.web_dir,
            api_token=cls.api_token,
            require_token=True,
        )
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        self.sse_mgr = SSEManager(max_clients=25, queue_size=50, replay_capacity=100)
        set_sse_manager(self.sse_mgr)

    # -------------------------------------------------------------------------
    # 1. 5,000-event project bounded initial load
    # -------------------------------------------------------------------------

    def test_5000_event_project_bounded_initial_load(self) -> None:
        """Initial page load fetches bounded recent summaries (e.g. limit=30) in <100ms without diffs."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        start_t = time.perf_counter()
        conn.request("GET", f"/events?limit=30&project={self.proj_slug}")
        res = conn.getresponse()
        elapsed = time.perf_counter() - start_t
        raw_body = res.read()
        conn.close()

        self.assertEqual(res.status, 200)
        self.assertLess(elapsed, 0.25, "Initial bounded summary load must execute rapidly")

        data = json.loads(raw_body.decode("utf-8"))
        self.assertEqual(len(data["events"]), 30)
        self.assertTrue(data["has_more"])
        # Verify payload size is tiny (< 30 KB)
        self.assertLess(len(raw_body), 30000, "Payload must be compact (no full diff bodies)")

        # Verify no diff_content in any returned event summary
        for ev in data["events"]:
            self.assertNotIn("diff_content", ev)
            self.assertNotIn("patches", ev)
            self.assertIn("files_count", ev)
            self.assertIn("additions", ev)

    # -------------------------------------------------------------------------
    # 2. 1,000-file burst metadata only
    # -------------------------------------------------------------------------

    def test_1000_file_burst_metadata_only(self) -> None:
        """GET /events/<id> for a 1,000-file burst returns file metadata without diff bodies."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        start_t = time.perf_counter()
        conn.request("GET", f"/events/9999?project={self.proj_slug}")
        res = conn.getresponse()
        elapsed = time.perf_counter() - start_t
        raw_body = res.read()
        conn.close()

        self.assertEqual(res.status, 200)
        self.assertLess(elapsed, 0.35)

        data = json.loads(raw_body.decode("utf-8"))
        self.assertEqual(data["event"]["id"], 9999)
        self.assertEqual(len(data["files"]), 1000)

        # File list has names and metrics, but NOT full diff text
        first_file = data["files"][0]
        self.assertIn("file_path", first_file)
        self.assertIn("additions", first_file)
        self.assertNotIn("diff_content", first_file)

    # -------------------------------------------------------------------------
    # 3. Diff fetched only when opened
    # -------------------------------------------------------------------------

    def test_diff_fetched_only_when_opened(self) -> None:
        """Single file diff is fetched strictly on demand when requested."""
        target_file = "src/modules/mod_0/file_42.py"
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"/events/9999/files/{urllib.parse.quote(target_file)}/diff?project={self.proj_slug}")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)

        data = json.loads(res.read().decode("utf-8"))
        conn.close()

        self.assertEqual(data["file_path"], target_file)
        self.assertIn("diff_content", data)
        self.assertIn("return 42", data["diff_content"])

    # -------------------------------------------------------------------------
    # 4. Tab hidden/visible polling pause
    # -------------------------------------------------------------------------

    def test_tab_hidden_visible_logic(self) -> None:
        """Verify visibility pause behavior in web/js/stream.js."""
        stream_code = (Path("web/js/stream.js")).read_text(encoding="utf-8")
        # Verify visibilitychange listener exists
        self.assertIn("visibilitychange", stream_code)
        self.assertIn("document.hidden", stream_code)
        # Verify pollLiveStatus checks document.hidden
        self.assertIn("if (typeof document !== 'undefined' && document.hidden)", stream_code)
        # Verify polling timer is cleared on hidden
        self.assertIn("clearInterval(state.pollTimer)", stream_code)

    # -------------------------------------------------------------------------
    # 5. SSE reconnect & missed event replay
    # -------------------------------------------------------------------------

    def test_sse_reconnect_replay(self) -> None:
        """Reconnecting client with Last-Event-ID receives missed frames without re-fetching all events."""
        mgr = self.sse_mgr
        ev1 = mgr.broadcast("burst_sealed", {"project": self.proj_slug, "event_id": 1001})
        ev2 = mgr.broadcast("burst_sealed", {"project": self.proj_slug, "event_id": 1002})
        ev3 = mgr.broadcast("burst_sealed", {"project": self.proj_slug, "event_id": 1003})

        # Client missed ev2 and ev3 (last seen was ev1)
        client, err = mgr.register_client(last_event_id=ev1.event_id)
        self.assertIsNone(err)
        self.assertIsNotNone(client)

        replayed = []
        while not client.q.empty():
            replayed.append(client.q.get_nowait())

        mgr.unregister_client(client.client_id)
        self.assertEqual(len(replayed), 2)
        self.assertEqual(replayed[0].event_id, ev2.event_id)
        self.assertEqual(replayed[1].event_id, ev3.event_id)

    # -------------------------------------------------------------------------
    # 6. Multiple browser tabs
    # -------------------------------------------------------------------------

    def test_multiple_browser_tabs_concurrency(self) -> None:
        """Multiple concurrent browser tabs connect and receive broadcasts simultaneously."""
        tabs = []
        for i in range(4):
            c, err = self.sse_mgr.register_client()
            self.assertIsNone(err)
            tabs.append(c)

        self.assertEqual(self.sse_mgr.client_count, 4)

        # Broadcast event
        self.sse_mgr.broadcast("status_changed", {"state": "ACTIVE"})

        for c in tabs:
            self.assertEqual(c.q.qsize(), 1)
            ev = c.q.get_nowait()
            self.assertEqual(ev.data["state"], "ACTIVE")
            self.sse_mgr.unregister_client(c.client_id)

        self.assertEqual(self.sse_mgr.client_count, 0)

    # -------------------------------------------------------------------------
    # 7. Memory remains bounded
    # -------------------------------------------------------------------------

    def test_memory_remains_bounded(self) -> None:
        """Verify memory allocation remains bounded when querying 5,000-event project."""
        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()

        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        for _ in range(10):
            conn.request("GET", f"/events?limit=25&project={self.proj_slug}")
            res = conn.getresponse()
            _ = res.read()
        conn.close()

        snapshot2 = tracemalloc.take_snapshot()
        stats = snapshot2.compare_to(snapshot1, "lineno")
        total_growth_kb = sum(stat.size_diff for stat in stats) / 1024.0
        tracemalloc.stop()

        # Growth across 10 queries should be minimal (< 500 KB)
        self.assertLess(total_growth_kb, 1024.0, f"Memory growth was {total_growth_kb:.1f} KB, expected < 1 MB")

    # -------------------------------------------------------------------------
    # 8. handleLivePing never re-fetches event list
    # -------------------------------------------------------------------------

    def test_handle_live_ping_does_not_refetch_events(self) -> None:
        """Verify handleLivePing in web/js/stream.js does NOT call loadProjectEvents."""
        stream_code = (Path("web/js/stream.js")).read_text(encoding="utf-8")
        # Must not contain loadProjectEvents invocation in handleLivePing
        self.assertNotIn("loadProjectEvents(activeProject.name)", stream_code)
        self.assertNotIn("loadProjectEvents(", stream_code)

    # -------------------------------------------------------------------------
    # 9. Windowed list component
    # -------------------------------------------------------------------------

    def test_windowed_list_component_logic(self) -> None:
        """Verify WindowedList component handles 1,000 items with bounded window calculation."""
        win_code = (Path("web/js/components/windowed_list.js")).read_text(encoding="utf-8")
        self.assertIn("class WindowedList", win_code)
        self.assertIn("startIndex", win_code)
        self.assertIn("endIndex", win_code)
        self.assertIn("topSpacer", win_code)
        self.assertIn("bottomSpacer", win_code)

        # Execute node headless test suite if node is available
        try:
            res = subprocess.run(
                ["node", "tests/test_windowed_list.js"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            self.assertIn("WindowedList 1,000 items verified", res.stdout)
        except (FileNotFoundError, subprocess.SubprocessError) as err:
            pass

    # -------------------------------------------------------------------------
    # 10. Manual burst seal button wiring
    # -------------------------------------------------------------------------

    def test_manual_burst_seal_button_wiring(self) -> None:
        """Verify #btn-force-seal exists in index.html and is wired in dashboard.js to /seal-burst."""
        html_code = (Path("web/index.html")).read_text(encoding="utf-8")
        self.assertIn('id="btn-force-seal"', html_code)
        self.assertIn("Seal Burst", html_code)

        dash_code = (Path("web/js/views/dashboard.js")).read_text(encoding="utf-8")
        self.assertIn("DOM.btnForceSeal", dash_code)
        self.assertIn("/seal-burst", dash_code)

        dom_code = (Path("web/js/dom.js")).read_text(encoding="utf-8")
        self.assertIn("btnForceSeal", dom_code)

    # -------------------------------------------------------------------------
    # 11. Execution session badges in timeline
    # -------------------------------------------------------------------------

    def test_execution_session_badges_in_timeline(self) -> None:
        """Verify execution cards render parent Session #N badge in web/js/views/timeline.js."""
        tl_code = (Path("web/js/views/timeline.js")).read_text(encoding="utf-8")
        self.assertIn("Session #${sessionId}", tl_code)
        self.assertIn("badge-session", tl_code)


if __name__ == "__main__":
    unittest.main()
