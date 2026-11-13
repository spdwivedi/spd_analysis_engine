"""
tests/test_api_contract_events_sse.py
=====================================
Comprehensive tests verifying:
1. API contract table, alias registration, and parameter extractors
2. Events API (bounded summaries, detail without diff, diff on demand, legacy bounds)
3. Reconnect with Last-Event-ID
4. Missed events replay
5. Bounded client queue & drop counter
6. Multiple concurrent SSE clients
7. Slow client resilience (no server blocking)
8. Client disconnect cleanup
9. Unauthorized SSE rejection
10. Large project bounded handling
"""

from __future__ import annotations

import http.client
import json
import queue
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

from scripts.server.api_contract import (
    API_CONTRACT,
    match_route,
    get_api_contract_catalog,
)
from scripts.server.sse_stream import (
    SSEManager,
    SSEClient,
    SSEEvent,
    get_sse_manager,
    set_sse_manager,
    MAX_SSE_CLIENTS,
)
from scripts.web_server import EngineWebServer
from scripts.storage.schema_migration import apply_schema_migrations


def _init_test_project_db(db_path: Path, num_events: int = 20) -> None:
    """Create a fully migrated test session.db with sessions, events, and patches."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    apply_schema_migrations(conn)
    conn.execute(
        "INSERT INTO sessions (id, project_name, target_path, start_time, status) "
        "VALUES (1, 'test_proj', '/tmp/test_proj', '2026-10-02T10:00:00Z', 'ACTIVE')"
    )

    for i in range(1, num_events + 1):
        ev_id_str = f"ev_{i:04d}"
        burst_uid = f"buid_{i:04d}"
        conn.execute(
            "INSERT INTO events (id, session_id, event_type, timestamp, first_file_touched, summary, "
            "executor, burst_num, commit_hash, is_rolled_back, event_id, seq, burst_uid) "
            "VALUES (?, 1, 'EDIT', '2026-10-02T10:01:00Z', 'src/app.py', ?, NULL, ?, 'abc123', 0, ?, ?, ?)",
            (i, f"Summary for burst {i}", i, ev_id_str, i, burst_uid),
        )

        diff1 = f"--- a/src/app.py\n+++ b/src/app.py\n@@ -1,3 +1,4 @@\n def run():\n+    # Burst {i}\n     pass\n"
        diff2 = f"--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1,2 +1,3 @@\n def test_run():\n+    assert True\n"
        conn.execute("INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size) VALUES (?, 'src/app.py', ?, 1, 0, 'unified', 80)", (i, diff1))
        conn.execute("INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size) VALUES (?, 'tests/test_app.py', ?, 1, 0, 'unified', 65)", (i, diff2))

    conn.commit()
    conn.close()


class TestApiContractAndEventsSSE(unittest.TestCase):
    """Test suite for API contract, Events API, and SSE streaming."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_contract_sse_test_"))
        cls.web_dir = cls.temp_dir / "web"
        cls.web_dir.mkdir(parents=True, exist_ok=True)
        (cls.web_dir / "index.html").write_text("<html><body>Test</body></html>", encoding="utf-8")

        # Create active project in current/
        cls.proj_slug = "test_project"
        cls.proj_dir = cls.temp_dir / "current" / cls.proj_slug
        cls.db_path = cls.proj_dir / "session.db"
        _init_test_project_db(cls.db_path, num_events=120)

        cls.api_token = "auth_token_secret_1234567890"
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
        # Reset SSE manager before tests if needed
        self.sse_mgr = SSEManager(max_clients=MAX_SSE_CLIENTS, queue_size=50, replay_capacity=100)
        set_sse_manager(self.sse_mgr)

    # -------------------------------------------------------------------------
    # 1. API Contract & Routing Tests
    # -------------------------------------------------------------------------

    def test_api_contract_catalog_structure(self) -> None:
        """Verify API contract table defines methods, paths, schemas, and classifications."""
        catalog = get_api_contract_catalog()
        self.assertGreater(len(catalog), 20)

        for route in catalog:
            self.assertIn("method", route)
            self.assertIn("path", route)
            self.assertIn("handler_name", route)
            self.assertIn("classification", route)
            self.assertIn(route["classification"], ("mutation", "read"))
            self.assertIn("request_schema", route)
            self.assertIn("response_schema", route)

    def test_route_matching_and_aliases(self) -> None:
        """Verify match_route matches canonical paths, aliases, and extracts path parameters."""
        # 1. Canonical /events
        r, params = match_route("GET", "/events")
        self.assertIsNotNone(r)
        self.assertEqual(r.handler_name, "_handle_api_events_summary")

        # 2. Alias /api/events
        r, params = match_route("GET", "/api/events")
        self.assertIsNotNone(r)
        self.assertEqual(r.handler_name, "_handle_api_events_summary")

        # 3. Single event: /events/42
        r, params = match_route("GET", "/events/42")
        self.assertIsNotNone(r)
        self.assertEqual(r.handler_name, "_handle_api_event_detail")
        self.assertEqual(params.get("id"), "42")

        # 4. Multi-segment file diff: /events/42/files/src/utils/math.py/diff
        r, params = match_route("GET", "/events/42/files/src/utils/math.py/diff")
        self.assertIsNotNone(r)
        self.assertEqual(r.handler_name, "_handle_api_event_file_diff")
        self.assertEqual(params.get("id"), "42")
        self.assertEqual(params.get("file_path"), "src/utils/math.py")

        # 5. Legacy aliases: /api/projects/my_proj/events vs /api/project/my_proj/events
        r1, params1 = match_route("GET", "/api/projects/my_proj/events")
        r2, params2 = match_route("GET", "/api/project/my_proj/events")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertEqual(r1.handler_name, "_handle_api_events")
        self.assertEqual(r2.handler_name, "_handle_api_events")
        self.assertEqual(params1.get("name"), "my_proj")
        self.assertEqual(params2.get("name"), "my_proj")

    # -------------------------------------------------------------------------
    # 2. Events API Tests (Bounded summaries, no default diff, on-demand diff)
    # -------------------------------------------------------------------------

    def test_events_summary_endpoint(self) -> None:
        """GET /events returns bounded summaries without full diff bodies."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"/events?limit=10&project={self.proj_slug}")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)

        data = json.loads(res.read().decode("utf-8"))
        conn.close()

        self.assertIn("events", data)
        self.assertEqual(len(data["events"]), 10)
        self.assertTrue(data["has_more"])

        first = data["events"][0]
        self.assertEqual(first["id"], 1)
        self.assertIn("first_file_touched", first)
        self.assertIn("files_count", first)
        self.assertEqual(first["files_count"], 2)
        # CRITICAL: diff_content must NOT be present in summary
        self.assertNotIn("diff_content", first)
        self.assertNotIn("patches", first)

    def test_single_event_detail_endpoint(self) -> None:
        """GET /events/<id> returns event metadata and file list without diff bodies."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"/events/5?project={self.proj_slug}")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)

        data = json.loads(res.read().decode("utf-8"))
        conn.close()

        self.assertIn("event", data)
        self.assertIn("files", data)
        self.assertEqual(data["event"]["id"], 5)
        self.assertEqual(len(data["files"]), 2)

        file1 = data["files"][0]
        self.assertEqual(file1["file_path"], "src/app.py")
        self.assertIn("additions", file1)
        # CRITICAL: diff_content must NOT be present in files list
        self.assertNotIn("diff_content", file1)

    def test_event_file_diff_endpoint(self) -> None:
        """GET /events/<id>/files/<path>/diff returns diff content on demand."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"/events/5/files/src/app.py/diff?project={self.proj_slug}")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)

        data = json.loads(res.read().decode("utf-8"))
        conn.close()

        self.assertEqual(data["file_path"], "src/app.py")
        self.assertIn("diff_content", data)
        self.assertIn("# Burst 5", data["diff_content"])
        self.assertEqual(data["additions"], 1)

    def test_large_project_bounded_result(self) -> None:
        """Verify queries against large dataset enforce limit and pagination."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        # Page 1: limit 50
        conn.request("GET", f"/events?limit=50&project={self.proj_slug}")
        res1 = conn.getresponse()
        data1 = json.loads(res1.read().decode("utf-8"))
        self.assertEqual(len(data1["events"]), 50)
        self.assertTrue(data1["has_more"])
        last_id = data1["events"][-1]["id"]

        # Page 2: since_id = 50, limit 50
        conn.request("GET", f"/events?since_id={last_id}&limit=50&project={self.proj_slug}")
        res2 = conn.getresponse()
        data2 = json.loads(res2.read().decode("utf-8"))
        conn.close()

        self.assertEqual(len(data2["events"]), 50)
        self.assertEqual(data2["events"][0]["id"], 51)

    # -------------------------------------------------------------------------
    # 3. SSE Tests (Reconnection, Replay, Bounded Queue, Disconnect, Auth)
    # -------------------------------------------------------------------------

    def test_unauthorized_sse(self) -> None:
        """Connecting to SSE without API token returns 401 Unauthorized."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/stream")
        res = conn.getresponse()
        self.assertEqual(res.status, 401)
        conn.close()

        # Connect with invalid token
        conn2 = http.client.HTTPConnection("127.0.0.1", self.port)
        conn2.request("GET", "/api/stream", headers={"X-API-Token": "invalid_wrong_token"})
        res2 = conn2.getresponse()
        self.assertEqual(res2.status, 401)
        conn2.close()

    def test_reconnect_with_last_event_id_and_missed_events_replay(self) -> None:
        """Verify reconnect with Last-Event-ID replays missed events from buffer in order."""
        # Broadcast initial 3 events before client connects
        self.sse_mgr.broadcast("status_changed", {"project": "p1", "state": "INDEXING"})
        self.sse_mgr.broadcast("burst_sealed", {"project": "p1", "burst_uid": "b1", "files_count": 2})
        self.sse_mgr.broadcast("job_progress", {"job_id": "j1", "progress": 0.5})

        # Register client with Last-Event-ID = 1 (meaning it saw event #1, missed #2 and #3)
        client, err = self.sse_mgr.register_client(last_event_id=1)
        self.assertIsNone(err)
        self.assertIsNotNone(client)

        replayed: list[SSEEvent] = []
        while not client.q.empty():
            replayed.append(client.q.get_nowait())

        self.sse_mgr.unregister_client(client.client_id)

        # Must have received events #2 and #3
        self.assertEqual(len(replayed), 2)
        self.assertEqual(replayed[0].event_id, 2)
        self.assertEqual(replayed[0].event, "burst_sealed")
        self.assertEqual(replayed[1].event_id, 3)
        self.assertEqual(replayed[1].event, "job_progress")

    def test_bounded_client_queue_and_drop_counter(self) -> None:
        """Client with bounded queue drops oldest frames on overflow and increments drop counter."""
        client = SSEClient("client_bounded", max_queue_size=5)

        for i in range(1, 16):
            ev = SSEEvent(event_id=i, event="test_evt", data={"num": i})
            client.put_event(ev)

        self.assertEqual(client.q.qsize(), 5)
        self.assertEqual(client.dropped_events, 10)

        # The 5 items left in queue must be the 5 most recent (11..15)
        items = []
        while not client.q.empty():
            items.append(client.q.get_nowait().data["num"])
        self.assertEqual(items, [11, 12, 13, 14, 15])

    def test_multiple_clients_broadcast(self) -> None:
        """Verify multiple active SSE clients receive broadcasted events concurrently."""
        client1, _ = self.sse_mgr.register_client()
        client2, _ = self.sse_mgr.register_client()
        client3, _ = self.sse_mgr.register_client()

        self.sse_mgr.broadcast_burst_sealed("p1", "b_100", 3)
        self.sse_mgr.broadcast_ai_completed("p1", "Analyzed updates")

        for c in (client1, client2, client3):
            self.assertEqual(c.q.qsize(), 2)
            ev1 = c.q.get_nowait()
            ev2 = c.q.get_nowait()
            self.assertEqual(ev1.event, "burst_sealed")
            self.assertEqual(ev2.event, "ai_completed")
            self.sse_mgr.unregister_client(c.client_id)

    def test_slow_client_resilience(self) -> None:
        """A slow client that stops reading does not block fast clients or server."""
        fast_client, _ = self.sse_mgr.register_client(max_queue_size=50)
        slow_client, _ = self.sse_mgr.register_client(max_queue_size=3)

        # Broadcast 10 events
        for i in range(10):
            self.sse_mgr.broadcast("status_changed", {"i": i})

        # Fast client received all 10
        self.assertEqual(fast_client.q.qsize(), 10)
        # Slow client bounded at 3, dropped 7
        self.assertEqual(slow_client.q.qsize(), 3)
        self.assertEqual(slow_client.dropped_events, 7)

        self.sse_mgr.unregister_client(fast_client.client_id)
        self.sse_mgr.unregister_client(slow_client.client_id)

    def test_client_cap_enforcement(self) -> None:
        """Verify SSE manager caps clients at MAX_SSE_CLIENTS."""
        mgr = SSEManager(max_clients=3)
        c1, _ = mgr.register_client()
        c2, _ = mgr.register_client()
        c3, _ = mgr.register_client()
        self.assertEqual(mgr.client_count, 3)

        # 4th client is rejected
        c4, err = mgr.register_client()
        self.assertIsNone(c4)
        self.assertIn("Maximum concurrent SSE clients", err)

        # Unregistering one makes room
        mgr.unregister_client(c1.client_id)
        c5, err5 = mgr.register_client()
        self.assertIsNotNone(c5)
        self.assertIsNone(err5)

    def test_client_disconnect_cleanup(self) -> None:
        """Verify client disconnect frees slot in SSEManager."""
        initial_count = self.sse_mgr.client_count
        client, _ = self.sse_mgr.register_client()
        self.assertEqual(self.sse_mgr.client_count, initial_count + 1)

        self.sse_mgr.unregister_client(client.client_id)
        self.assertEqual(self.sse_mgr.client_count, initial_count)

    def test_live_sse_http_stream(self) -> None:
        """End-to-end HTTP test: connect to /api/stream with token and receive broadcast event."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", self.port))

        req = (
            f"GET /api/stream HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"X-API-Token: {self.api_token}\r\n"
            f"Connection: keep-alive\r\n\r\n"
        )
        sock.sendall(req.encode("utf-8"))

        # Read until headers and initial connected frame
        buffer = b""
        start_time = time.time()
        while b"event: connected" not in buffer and (time.time() - start_time < 3.0):
            chunk = sock.recv(1024)
            if not chunk:
                break
            buffer += chunk

        self.assertIn(b"200 OK", buffer)
        self.assertIn(b"text/event-stream", buffer)
        self.assertIn(b"event: connected", buffer)

        # Now trigger an SSE broadcast in server
        EngineWebServer.broadcast_sse("job_progress", {"job_id": "job_live_test", "progress": 0.85})

        # Read socket until job_progress event arrives
        while b"event: job_progress" not in buffer and (time.time() - start_time < 3.0):
            chunk = sock.recv(1024)
            if not chunk:
                break
            buffer += chunk

        self.assertIn(b"event: job_progress", buffer)
        self.assertIn(b"job_live_test", buffer)

        # Cleanly disconnect
        sock.close()
        # Allow server thread to detect disconnect
        time.sleep(0.1)

    def test_live_sse_http_reconnect_last_event_id(self) -> None:
        """End-to-end HTTP test: connect to /api/stream providing Last-Event-ID header and receive replayed frames."""
        # Broadcast two events
        ev1 = get_sse_manager().broadcast("status_changed", {"project": "p1", "state": "STOPPING"})
        ev2 = get_sse_manager().broadcast("status_changed", {"project": "p1", "state": "STOPPED"})

        # Connect with Last-Event-ID pointing to before ev2
        last_id = ev1.event_id
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", self.port))

        req = (
            f"GET /api/stream HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"X-API-Token: {self.api_token}\r\n"
            f"Last-Event-ID: {last_id}\r\n"
            f"Connection: keep-alive\r\n\r\n"
        )
        sock.sendall(req.encode("utf-8"))

        buffer = b""
        start_time = time.time()
        while b"STOPPED" not in buffer and (time.time() - start_time < 3.0):
            chunk = sock.recv(1024)
            if not chunk:
                break
            buffer += chunk

        self.assertIn(b"200 OK", buffer)
        self.assertIn(b"event: status_changed", buffer)
        self.assertIn(b"STOPPED", buffer)
        sock.close()
        time.sleep(0.1)

    def test_live_action_broadcast_and_client_receipt(self) -> None:
        """Verify internal loopback endpoint and broadcast_live_action broadcast to SSE clients."""
        client, _ = self.sse_mgr.register_client()
        self.assertIsNotNone(client)

        # 1. Direct SSE manager broadcast
        ev = self.sse_mgr.broadcast_live_action("test_proj", "WRITE", path="src/main.py", details="Modified 12 bytes")
        self.assertEqual(ev.event, "live_action")
        received = client.q.get_nowait()
        self.assertEqual(received.event, "live_action")
        self.assertEqual(received.data["path"], "src/main.py")

        # 2. HTTP POST /api/internal/live-action loopback IPC
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        body = json.dumps({"project": "test_proj", "event_type": "WRITE", "path": "src/ipc.py"})
        conn.request("POST", "/api/internal/live-action", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()

        ipc_ev = client.q.get_nowait()
        self.assertEqual(ipc_ev.event, "live_action")
        self.assertEqual(ipc_ev.data["path"], "src/ipc.py")
        self.sse_mgr.unregister_client(client.client_id)

        # 3. Verify frontend listener in web/js/stream.js
        stream_js = Path("web/js/stream.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener('live_action'", stream_js)


if __name__ == "__main__":
    unittest.main()

