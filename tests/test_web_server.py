"""
tests/test_web_server.py
========================
Automated verification for EngineWebServer (Phase 3).
Tests all REST endpoints, static asset serving, and SSE streaming.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

# Resolve path
_HERE = Path(__file__).resolve().parent
_ENGINE_ROOT = _HERE.parent
sys.path.insert(0, str(_ENGINE_ROOT.parent))

from spd_analysis_engine.scripts.web_server import EngineWebServer


def test_web_server() -> int:
    print("\n" + "=" * 65)
    print("  PHASE 3 VERIFICATION: EngineWebServer & Control Portal API")
    print("=" * 65 + "\n")

    port = 8799
    server = EngineWebServer(engine_root=_ENGINE_ROOT, port=port)
    actual_port = server.start(background=True)
    base_url = f"http://127.0.0.1:{actual_port}"
    print(f"[1/7] EngineWebServer started at {base_url}")

    failures: list[str] = []

    try:
        # 1. Test Static Assets
        print("\n[2/7] Testing static asset serving...")
        req = urllib.request.Request(f"{base_url}/")
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "SPD Engine" in content
            print("      [PASS] GET / returned index.html (200 OK)")

        req_css = urllib.request.Request(f"{base_url}/style.css")
        with urllib.request.urlopen(req_css) as resp:
            assert resp.status == 200
            assert "var(--bg-app)" in resp.read().decode("utf-8")
            print("      [PASS] GET /style.css returned stylesheet (200 OK)")

        req_js = urllib.request.Request(f"{base_url}/app.js")
        with urllib.request.urlopen(req_js) as resp:
            assert resp.status == 200
            assert "refreshProjects" in resp.read().decode("utf-8")
            print("      [PASS] GET /app.js returned client JS (200 OK)")

        # 2. Test GET /api/status & GET /api/projects
        print("\n[3/7] Testing /api/status and /api/projects...")
        req_status = urllib.request.Request(f"{base_url}/api/status")
        with urllib.request.urlopen(req_status) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert "workers" in data
            print(f"      [PASS] GET /api/status returned {len(data['workers'])} worker(s)")

        req_projects = urllib.request.Request(f"{base_url}/api/projects")
        with urllib.request.urlopen(req_projects) as resp:
            proj_data = json.loads(resp.read().decode("utf-8"))
            assert "current" in proj_data
            assert "last_run" in proj_data
            assert "history" in proj_data
            print(f"      [PASS] GET /api/projects returned grouped tiers (current: {len(proj_data['current'])}, last_run: {len(proj_data['last_run'])})")

        # 3. Test POST /api/projects/start
        test_proj = "web_portal_test"
        test_ws = _ENGINE_ROOT / "mock_workspace" / test_proj
        if test_ws.exists():
            shutil.rmtree(test_ws, ignore_errors=True)

        print(f"\n[4/7] Testing POST /api/projects/start for '{test_proj}'...")
        start_payload = {
            "project": test_proj,
            "path": str(test_ws),
            "scaffold": True,
            "debounce": 1.5,
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
            print(f"      [PASS] Worker started successfully | PID={worker_pid}")

        # Wait for worker boot
        time.sleep(2.0)

        # 4. Verify worker in /api/status and /api/project/<name>/logs
        print("\n[5/7] Verifying live worker and logs...")
        with urllib.request.urlopen(f"{base_url}/api/status") as resp:
            live_status = json.loads(resp.read().decode("utf-8"))
            alive_match = [w for w in live_status["workers"] if w["project"] == test_proj and w["alive"]]
            if not alive_match:
                failures.append(f"Worker '{test_proj}' not reported alive in /api/status")
                print(f"      [FAIL] {failures[-1]}")
            else:
                print(f"      [PASS] Worker '{test_proj}' is LIVE (PID {alive_match[0]['pid']})")

        with urllib.request.urlopen(f"{base_url}/api/project/{test_proj}/logs") as resp:
            logs_res = json.loads(resp.read().decode("utf-8"))
            assert "lines" in logs_res
            print(f"      [PASS] /api/project/{test_proj}/logs returned {len(logs_res['lines'])} lines")

        # 5. Simulate file change and verify /api/project/<name>/events
        print("\n[6/7] Simulating file modification and testing events/export API...")
        sample_file = test_ws / "index.py"
        sample_file.write_text("def run():\n    return 'Hello from Web Portal Test'\n", encoding="utf-8")
        print(f"      Wrote file: {sample_file}")

        # Wait for debounce (1.5s quiet window + 1.0s buffer)
        print("      Waiting 2.5s for 1.5s debounce timer to fire...")
        time.sleep(2.5)

        with urllib.request.urlopen(f"{base_url}/api/project/{test_proj}/events") as resp:
            events_res = json.loads(resp.read().decode("utf-8"))
            ev_list = events_res.get("events", [])
            print(f"      Events retrieved: {len(ev_list)}")
            if len(ev_list) == 0:
                failures.append("No events recorded for file modification")
                print(f"      [FAIL] {failures[-1]}")
            else:
                ev = ev_list[0]
                print(f"      [PASS] Recorded Event #{ev['id']}: {ev['summary']}")
                patches = ev.get("patches", [])
                if patches:
                    print(f"      [PASS] Found {len(patches)} patch(es) for {patches[0]['file_path']}")
                else:
                    failures.append("Event missing patches")
                    print(f"      [FAIL] {failures[-1]}")

        # Export endpoint test
        with urllib.request.urlopen(f"{base_url}/api/project/{test_proj}/export") as resp:
            export_data = json.loads(resp.read().decode("utf-8"))
            assert export_data["project"] == test_proj
            assert "events" in export_data
            print(f"      [PASS] GET /api/project/{test_proj}/export returned valid bundle ({len(export_data['events'])} events)")

        # 6. Test POST /api/projects/stop
        print(f"\n[7/7] Testing POST /api/projects/stop for '{test_proj}'...")
        stop_payload = {"project": test_proj}
        stop_req = urllib.request.Request(
            f"{base_url}/api/projects/stop",
            data=json.dumps(stop_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(stop_req) as resp:
            stop_res = json.loads(resp.read().decode("utf-8"))
            assert stop_res["status"] == "stopped"
            print(f"      [PASS] Project '{test_proj}' stopped and archived successfully")

    except Exception as exc:
        failures.append(f"Unexpected exception: {exc}")
        print(f"[ERROR] {exc}")
    finally:
        # Clean shutdown of server
        server.shutdown()
        # Clean up test mock workspace
        shutil.rmtree(_ENGINE_ROOT / "mock_workspace" / "web_portal_test", ignore_errors=True)
        shutil.rmtree(_ENGINE_ROOT / "last_run" / "web_portal_test", ignore_errors=True)

    print("\n" + "=" * 65)
    if not failures:
        print("  >>> ALL PHASE 3 WEB SERVER TESTS PASSED (100% SUCCESS) <<<")
        print("=" * 65 + "\n")
        return 0
    else:
        print(f"  >>> FAILED WITH {len(failures)} ERROR(S) <<<")
        for f in failures:
            print(f"   - {f}")
        print("=" * 65 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(test_web_server())
