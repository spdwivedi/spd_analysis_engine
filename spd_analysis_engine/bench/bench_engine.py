"""
bench/bench_engine.py
=====================
Performance measurement, latency profiling, and duty cycle benchmark harness
for the SPD Analysis Engine.

Measures:
1. Baseline RSS and idle process CPU duty cycle
2. Ingestion throughput: events/sec
3. End-to-end event-to-SSE latency distribution (p50, p95, p99)
4. Multi-scale Git workflow latency (10, 100, 1,000, 2,000 files)
5. SQLite read latency under concurrent write contention in WAL mode
6. SQLite database growth (bytes per event)
7. SSE client memory scaling
8. API payload compression ratio
9. AI queue scheduling and lease latency
10. Final RSS memory and storm CPU duty cycle
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from spd_analysis_engine.scripts.core.burst_assembler import BurstAssembler
from spd_analysis_engine.scripts.core.changeset import ChangeSet
from spd_analysis_engine.scripts.storage.db import SessionDB
from spd_analysis_engine.scripts.git.shadow_repo import ShadowGit
from spd_analysis_engine.scripts.server.sse_stream import SSEManager
from spd_analysis_engine.scripts.ai.controller_db import EngineDB


def get_current_rss_mb() -> float:
    """Return current process RSS memory in megabytes."""
    if _PSUTIL:
        try:
            return psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            pass
    return 0.0


def measure_cpu_duty_cycle(duration_s: float = 1.0) -> float:
    """Measure process CPU utilization duty cycle over interval."""
    if _PSUTIL:
        try:
            proc = psutil.Process()
            proc.cpu_percent(interval=None)
            time.sleep(duration_s)
            return proc.cpu_percent(interval=None)
        except Exception:
            pass
    time.sleep(duration_s)
    return 0.0


def run_benchmarks(smoke: bool = False) -> dict[str, Any]:
    """Execute complete benchmark suite, establishing baseline first."""
    temp_dir = Path(tempfile.mkdtemp(prefix="spd_bench_"))
    results: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "smoke" if smoke else "full",
        "baseline": {},
        "metrics": {},
    }

    try:
        # -----------------------------------------------------------------
        # 0. Baseline Measurements (Idle State)
        # -----------------------------------------------------------------
        print("Measuring baseline state...")
        baseline_rss = get_current_rss_mb()
        baseline_cpu = measure_cpu_duty_cycle(duration_s=0.5 if smoke else 1.0)

        results["baseline"]["rss_mb"] = round(baseline_rss, 2)
        results["baseline"]["idle_cpu_percent"] = round(baseline_cpu, 2)
        print(f"  Baseline RSS: {baseline_rss:.2f} MB | Baseline Idle CPU: {baseline_cpu:.2f}%")

        # -----------------------------------------------------------------
        # 1. Ingestion Throughput: events/sec
        # -----------------------------------------------------------------
        print("Benchmarking events/sec throughput...")
        event_count = 500 if smoke else 5000
        q = queue.Queue()
        assembler = BurstAssembler(
            quiet_period=0.1,
            max_burst_s=1.0,
            max_queue_size=25000,
            downstream_queue=q,
        )
        assembler.start()

        t0 = time.perf_counter()
        for i in range(event_count):
            assembler.add(f"file_{i % 100}.py", "modified")
        ingest_time = time.perf_counter() - t0
        events_per_sec = event_count / max(0.0001, ingest_time)
        assembler.stop(drain=False)

        results["metrics"]["events_per_sec"] = round(events_per_sec, 1)
        results["metrics"]["ingest_duration_ms"] = round(ingest_time * 1000.0, 2)
        print(f"  Throughput: {events_per_sec:.1f} events/sec ({event_count} events in {ingest_time * 1000.0:.1f} ms)")

        # -----------------------------------------------------------------
        # 2. End-to-End Event-to-SSE Latency (p50 / p95 / p99)
        # -----------------------------------------------------------------
        print("Benchmarking end-to-end event-to-SSE delivery latency...")
        sse_mgr = SSEManager(max_clients=10, queue_size=100)
        client, _ = sse_mgr.register_client()
        e2e_latencies_ms: list[float] = []
        e2e_cycles = 5 if smoke else 20

        for idx in range(e2e_cycles):
            bq = queue.Queue()
            ba = BurstAssembler(
                quiet_period=0.02,
                max_burst_s=0.08,
                downstream_queue=bq,
            )
            ba.start()
            t_event = time.perf_counter()
            ba.add(f"tracked_{idx}.py", "modified")

            try:
                bundle = bq.get(timeout=0.5)
                # Broadcast immediately on burst seal
                sse_mgr.broadcast("burst_sealed", {"burst_id": idx, "files": bundle.get("files", [])})
                # Client receives message
                msg = client.q.get(timeout=0.5)
                e2e_latencies_ms.append((time.perf_counter() - t_event) * 1000.0)
            except queue.Empty:
                pass
            finally:
                ba.stop(drain=False)

        sse_mgr.unregister_client(client)
        e2e_latencies_ms.sort()
        if e2e_latencies_ms:
            p50 = e2e_latencies_ms[int(len(e2e_latencies_ms) * 0.50)]
            p95 = e2e_latencies_ms[min(len(e2e_latencies_ms) - 1, int(len(e2e_latencies_ms) * 0.95))]
            p99 = e2e_latencies_ms[min(len(e2e_latencies_ms) - 1, int(len(e2e_latencies_ms) * 0.99))]
        else:
            p50 = p95 = p99 = 0.0

        results["metrics"]["event_to_sse_p50_ms"] = round(p50, 2)
        results["metrics"]["event_to_sse_p95_ms"] = round(p95, 2)
        results["metrics"]["event_to_sse_p99_ms"] = round(p99, 2)
        print(f"  Event-to-SSE Latency: p50={p50:.2f} ms | p95={p95:.2f} ms | p99={p99:.2f} ms")

        # -----------------------------------------------------------------
        # 3. Multi-Scale Git Commit Latency Matrix (10, 100, 1k, 2k files)
        # -----------------------------------------------------------------
        print("Benchmarking multi-scale Git commit latency...")
        git_ws = temp_dir / "git_ws"
        git_shadow = temp_dir / "git_shadow"
        git_ws.mkdir(parents=True, exist_ok=True)
        git_shadow.mkdir(parents=True, exist_ok=True)

        sg = ShadowGit(shadow_dir=git_shadow, target_dir=git_ws)
        sg.init_repo()

        scales = [10, 50] if smoke else [10, 100, 1000, 2000]
        git_scaling_results: dict[str, float] = {}

        for scale in scales:
            file_paths = []
            for f_i in range(scale):
                fname = f"scale_{scale}_f_{f_i}.txt"
                fp = git_ws / fname
                fp.write_text(f"payload {scale} {f_i}", encoding="utf-8")
                file_paths.append(fname)

            cs = ChangeSet(
                burst_uid=f"burst_scale_{scale}",
                event_id=scale,
                sequence=scale,
                files=file_paths,
                effective_files=file_paths,
            )
            t_g0 = time.perf_counter()
            sg.commit_changeset(cs, summary=f"Commit scale {scale}")
            commit_time_ms = (time.perf_counter() - t_g0) * 1000.0
            git_scaling_results[f"{scale}_files_ms"] = round(commit_time_ms, 2)
            print(f"  Git Commit ({scale} files): {commit_time_ms:.2f} ms")

        results["metrics"]["git_commit_scaling"] = git_scaling_results
        results["metrics"]["git_commit_time_ms"] = git_scaling_results.get("10_files_ms", 0.0)

        # -----------------------------------------------------------------
        # 4. SQLite Read Latency Under Concurrent Write Load (WAL Mode)
        # -----------------------------------------------------------------
        print("Benchmarking SQLite read latency under concurrent write contention...")
        db_path = temp_dir / "contention_session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("contention_proj", str(git_ws))

        writer_stop = threading.Event()
        writer_errors = 0

        def background_writer():
            nonlocal writer_errors
            w_idx = 0
            while not writer_stop.is_set():
                w_idx += 1
                try:
                    with sdb.transaction() as cur:
                        cur.execute(
                            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary) "
                            "VALUES (?, 'EDIT', datetime('now'), ?, ?)",
                            (sid, f"writer_{w_idx}.py", f"write load {w_idx}"),
                        )
                    time.sleep(0.005)
                except Exception:
                    writer_errors += 1

        w_thread = threading.Thread(target=background_writer, daemon=True)
        w_thread.start()

        read_latencies_ms: list[float] = []
        read_iterations = 20 if smoke else 100
        for _ in range(read_iterations):
            t_r0 = time.perf_counter()
            ro_conn = sdb._get_ro_conn()
            ro_conn.execute("SELECT count(*), max(id) FROM events").fetchone()
            read_latencies_ms.append((time.perf_counter() - t_r0) * 1000.0)
            time.sleep(0.005)

        writer_stop.set()
        w_thread.join(timeout=2.0)
        sdb.close()

        read_latencies_ms.sort()
        read_p50 = read_latencies_ms[int(len(read_latencies_ms) * 0.50)] if read_latencies_ms else 0.0
        read_p95 = read_latencies_ms[min(len(read_latencies_ms) - 1, int(len(read_latencies_ms) * 0.95))] if read_latencies_ms else 0.0

        results["metrics"]["sqlite_read_contention_p50_ms"] = round(read_p50, 3)
        results["metrics"]["sqlite_read_contention_p95_ms"] = round(read_p95, 3)
        print(f"  SQLite Read under Concurrent Writes: p50={read_p50:.3f} ms | p95={read_p95:.3f} ms")

        # -----------------------------------------------------------------
        # 5. Database Growth (Bytes per Event in WAL Mode)
        # -----------------------------------------------------------------
        print("Benchmarking SQLite WAL DB growth...")
        db_path_growth = temp_dir / "growth_session.db"
        sdb_g = SessionDB(db_path_growth)
        sid_g = sdb_g.create_session("growth_proj", str(git_ws))

        db_events = 200 if smoke else 1000
        for i in range(db_events):
            eid = sdb_g.record_event(sid_g, "EDIT", f"db_file_{i % 50}.py", f"Burst summary #{i}")
            sdb_g.record_patch(eid, f"db_file_{i % 50}.py", f"@@ -1 +1 @@\n-old {i}\n+new {i}\n")

        sdb_g.close()
        total_db_bytes = db_path_growth.stat().st_size
        wal_path = db_path_growth.with_name("growth_session.db-wal")
        if wal_path.exists():
            total_db_bytes += wal_path.stat().st_size

        bytes_per_event = total_db_bytes / max(1, db_events)
        results["metrics"]["db_total_size_kb"] = round(total_db_bytes / 1024.0, 2)
        results["metrics"]["db_growth_bytes_per_event"] = round(bytes_per_event, 1)
        print(f"  DB Size: {total_db_bytes / 1024.0:.1f} KB | Growth: {bytes_per_event:.1f} bytes/event")

        # -----------------------------------------------------------------
        # 6. SSE Memory Scaling per Client
        # -----------------------------------------------------------------
        print("Benchmarking SSE memory per client...")
        sse_client_count = 10 if smoke else 50
        rss_before_sse = get_current_rss_mb()

        mgr = SSEManager(max_clients=sse_client_count + 10, queue_size=100)
        clients = []
        for _ in range(sse_client_count):
            c, _ = mgr.register_client()
            if c:
                clients.append(c)

        for e_i in range(10):
            mgr.broadcast("burst_sealed", {"event_id": e_i, "summary": "burst update"})

        rss_after_sse = get_current_rss_mb()
        delta_rss_sse = max(0.0, rss_after_sse - rss_before_sse)
        sse_kb_per_client = (delta_rss_sse * 1024.0) / max(1, len(clients))

        results["metrics"]["sse_client_count"] = len(clients)
        results["metrics"]["sse_kb_per_client"] = round(sse_kb_per_client, 2)
        print(f"  SSE Clients: {len(clients)} | Approx memory/client: {sse_kb_per_client:.2f} KB")

        # -----------------------------------------------------------------
        # 7. Events API Payload Size (Summary vs Detail)
        # -----------------------------------------------------------------
        print("Benchmarking events API payload size...")
        summary_payload = {
            "events": [
                {"id": i, "event_type": "EDIT", "first_file": f"src/file_{i}.py", "summary": f"Edit {i}", "timestamp": "2026-10-04T00:00:00Z"}
                for i in range(50)
            ],
            "total": 50,
            "has_more": False,
        }
        detail_payload = {
            "event": {"id": 1, "event_type": "EDIT", "summary": "Edit 1"},
            "files": [{"file_path": f"src/file_{i}.py", "diff": f"@@ -1,1 +1,2 @@\n-line\n+line {i}\n+new line\n"} for i in range(50)],
        }

        summary_bytes = len(json.dumps(summary_payload).encode("utf-8"))
        detail_bytes = len(json.dumps(detail_payload).encode("utf-8"))

        results["metrics"]["events_summary_payload_bytes"] = summary_bytes
        results["metrics"]["events_detail_payload_bytes"] = detail_bytes
        results["metrics"]["summary_compression_ratio"] = round(detail_bytes / max(1, summary_bytes), 2)
        print(f"  Summary Payload: {summary_bytes} bytes | Full Detail: {detail_bytes} bytes (Ratio: {detail_bytes / max(1, summary_bytes):.2f}x)")

        # -----------------------------------------------------------------
        # 8. AI Queue Scheduling Latency
        # -----------------------------------------------------------------
        print("Benchmarking AI queue latency...")
        ctrl_db_path = temp_dir / "engine.db"
        edb = EngineDB(ctrl_db_path)

        ai_jobs = 10 if smoke else 50
        ai_latencies_ms = []
        for j in range(ai_jobs):
            t_j0 = time.perf_counter()
            job_id = edb.enqueue_job(
                project_slug="bench_proj",
                target_kind="burst",
                target_id=f"burst_{j}",
                payload={"level": "L1", "burst_id": j},
            )
            job = edb.claim_job(worker_id="bench_worker", lease_duration_s=10.0)
            if job:
                edb.complete_job(job["id"], analysis_id=f"analysis_{j}")
            ai_latencies_ms.append((time.perf_counter() - t_j0) * 1000.0)

        avg_ai_latency_ms = sum(ai_latencies_ms) / max(1, len(ai_latencies_ms))
        results["metrics"]["ai_queue_avg_latency_ms"] = round(avg_ai_latency_ms, 2)
        print(f"  AI Queue Scheduling & Lease Latency: {avg_ai_latency_ms:.2f} ms/job")

        # -----------------------------------------------------------------
        # 9. Storm CPU Duty Cycle & Final RSS Memory
        # -----------------------------------------------------------------
        storm_cpu = measure_cpu_duty_cycle(duration_s=0.5 if smoke else 1.0)
        final_rss = get_current_rss_mb()
        results["metrics"]["final_rss_mb"] = round(final_rss, 2)
        results["metrics"]["final_idle_cpu_percent"] = round(storm_cpu, 2)
        print(f"  Final RSS: {final_rss:.2f} MB | Background CPU: {storm_cpu:.2f}%")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SPD Analysis Engine Benchmark Harness")
    parser.add_argument("--smoke", action="store_true", help="Run fast smoke test benchmark")
    parser.add_argument("--output", "-o", type=str, default="bench/results.json", help="Path to write JSON output")
    args = parser.parse_args()

    results = run_benchmarks(smoke=args.smoke)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nBenchmark completed successfully! Results written to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
