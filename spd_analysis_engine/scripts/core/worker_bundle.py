"""
scripts/core/worker_bundle.py
=============================
Burst event bundle processing, patch/diff persistence, and zero-idle heartbeat loop.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from .worker_lifecycle import WorkerState, worker_state, _write_status_file, _emit_json, _utcnow_iso
from .changeset import ChangeSet
from .faults import check_fault
from ..diff_calc import is_binary_file
from ..git.shadow_commit import GitState, record_git_op_state, update_git_op_state

logger = logging.getLogger(__name__)


_live_action_queue: queue.Queue[bytes] = queue.Queue(maxsize=1000)
_loopback_worker_thread: threading.Thread | None = None
_loopback_lock = threading.Lock()


def _ensure_loopback_worker() -> None:
    """Ensure single background daemon thread is running to service live action loopback queue."""
    global _loopback_worker_thread
    if _loopback_worker_thread is not None and _loopback_worker_thread.is_alive():
        return
    with _loopback_lock:
        if _loopback_worker_thread is not None and _loopback_worker_thread.is_alive():
            return

        def _worker_loop() -> None:
            conn: http.client.HTTPConnection | None = None
            cached_port = 8765
            while True:
                payload = _live_action_queue.get()
                try:
                    port_val = os.environ.get("SPD_PORT") or os.environ.get("PORT") or "8765"
                    try:
                        port = int(port_val)
                    except ValueError:
                        port = 8765
                    if conn is None or port != cached_port:
                        cached_port = port
                        if conn:
                            try: conn.close()
                            except Exception: pass
                        conn = http.client.HTTPConnection("127.0.0.1", cached_port, timeout=0.2)
                    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
                    conn.request("POST", "/api/internal/live-action", body=payload, headers=headers)
                    resp = conn.getresponse()
                    resp.read()
                except Exception:
                    if conn:
                        try: conn.close()
                        except Exception: pass
                        conn = None
                finally:
                    _live_action_queue.task_done()

        _loopback_worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="live-action-ipc-worker"
        )
        _loopback_worker_thread.start()


def emit_live_action_sse(
    rel_path: str = "",
    event_type: str = "UPDATE",
    project_name: str = "",
    action_type: str = "",
    path: str = "",
    command: str = "",
    details: str = "",
    **extra: Any,
) -> None:
    """
    Broadcast real-time live action over SSE:
    1. Local in-process broadcast via SSEManager if active.
    2. Non-blocking queue loopback HTTP POST to EngineWebServer /api/internal/live-action.
    """
    eff_path = path or rel_path
    eff_type = action_type or event_type or "UPDATE"
    proj = project_name or getattr(worker_state, "project_name", "") or ""

    # 1. In-process broadcast if available
    try:
        from ..server.sse_stream import get_sse_manager
        mgr = get_sse_manager()
        mgr.broadcast_live_action(
            project=proj,
            action_type=eff_type,
            path=eff_path,
            command=command,
            details=details,
        )
    except Exception:
        pass

    # 2. Non-blocking enqueue to background loopback HTTP worker
    payload = json.dumps({
        "project": proj,
        "event_type": eff_type,
        "action_type": eff_type,
        "path": eff_path,
        "command": command,
        "details": details,
        "timestamp": _utcnow_iso(),
    }).encode("utf-8")

    _ensure_loopback_worker()
    try:
        _live_action_queue.put_nowait(payload)
    except (queue.Full, Exception):
        pass


def on_burst_assembler_action(act: dict[str, Any]) -> None:
    """Callback triggered by BurstAssembler on any activity (WRITE, READ, EXEC, BURST)."""
    emit_live_action_sse(
        action_type=act.get("action_type") or "WRITE",
        path=act.get("file_path") or "",
        details=act.get("details") or "",
        command=act.get("command") or "",
    )



def _handle_event_bundle(bundle: dict[str, Any], state: WorkerState = worker_state) -> None:
    """
    Callback dispatched by EventBundler when a quiet period elapses.

    Processes all files touched during the burst:
    1. Computes unified diffs against baseline cache.
    2. Updates the baseline cache for subsequent bursts.
    3. Records an EDIT event in SessionDB.
    4. Records individual file patches in SessionDB.
    5. Writes an atomic combined .patch file to current/<slug>/patches/event_<id>.patch.
    6. Micro-commits burst changes to ShadowGit if enabled.
    7. Emits event_bundled JSON log to worker.log.
    """
    if state.session_db is None or state.session_id is None or state.diff_calc is None:
        logger.warning("Dropped event bundle: session_db or diff_calc not initialized")
        return

    files_touched = bundle.get("files_touched", [])
    if not files_touched:
        return

    first_file = bundle.get("first_file")
    duration_s = bundle.get("duration_s", 0.0)

    # Compute diffs and update baseline for all files in bundle
    file_diffs: list[tuple[str, str]] = []
    target_root = Path(state.target_path_str)
    is_initial = bundle.get("is_initial", False)

    ignore_policy = (
        getattr(state, "ignore_policy", None)
        or getattr(state.project_watcher, "ignore_policy", None)
        or getattr(state.diff_calc, "ignore_policy", None)
    )
    if ignore_policy is None and state.target_path_str:
        try:
            from ..storage.ignore_policy import IgnorePolicy
            ignore_policy = IgnorePolicy(target_root)
        except Exception:
            ignore_policy = None

    for rel_path in files_touched:
        try:
            if ignore_policy is not None and not ignore_policy.should_diff(rel_path):
                # Sensitive files are strictly metadata-only: redact diff content and do NOT update disk baseline
                redacted_diff = f"--- a/{rel_path}\n+++ b/{rel_path}\n@@ -0,0 +0,0 @@\n [REDACTED SENSITIVE FILE]\n"
                file_diffs.append((rel_path, redacted_diff))
                continue

            diff_text = state.diff_calc.compute_diff(rel_path, old_content="" if is_initial else None)
            file_diffs.append((rel_path, diff_text))

            abs_path = target_root / Path(rel_path)
            if abs_path.is_file():
                if is_binary_file(abs_path):
                    curr_content = None
                else:
                    try:
                        curr_content = abs_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        curr_content = None
            else:
                curr_content = None

            if curr_content is not None or not abs_path.is_file():
                state.diff_calc.update_baseline(rel_path, curr_content)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error computing diff / updating baseline for %s: %s", rel_path, exc)

    summary = f"Burst edit across {len(files_touched)} file(s) ({duration_s:.1f}s)"
    eff_list = bundle.get("effective_changes") or list(files_touched)
    burst_uid = bundle.get("burst_uid") or f"burst_{bundle.get('event_id') or 1}"
    try:
        event_id = state.session_db.record_event(
            session_id=state.session_id,
            event_type="EDIT",
            first_file=first_file,
            summary=summary,
            burst_uid=burst_uid,
            effective_files=json.dumps(eff_list),
        )
        bundler = state.burst_assembler or state.event_bundler
        if bundler:
            bundler.record_action(
                "BURST",
                first_file or "workspace",
                details=f"Burst #{event_id} ({len(files_touched)} files)",
            )
            _write_status_file(state)
        emit_live_action_sse(
            project_name=state.project_name or "",
            action_type="BURST",
            path=first_file or "workspace",
            details=f"Burst #{event_id} ({len(files_touched)} files)",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to record event in SessionDB: %s", exc)
        return

    combined_patch_lines: list[str] = []
    for rel_path, diff_text in file_diffs:
        try:
            state.session_db.record_patch(event_id, rel_path, diff_text if diff_text else None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to record patch in DB for %s: %s", rel_path, exc)
        if diff_text:
            combined_patch_lines.append(diff_text)

    if state.patches_dir is not None:
        try:
            state.patches_dir.mkdir(parents=True, exist_ok=True)
            patch_file = state.patches_dir / f"event_{event_id}.patch"
            if combined_patch_lines:
                patch_content = "\n".join(p.rstrip("\n") for p in combined_patch_lines) + "\n"
            else:
                patch_content = f"# Event {event_id}: No text diff detected\n"
            patch_file.write_text(patch_content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write patch file for event %d: %s", event_id, exc)

    check_fault("after_event_durability")

    if state.shadow_git is not None:
        changeset = bundle.get("changeset")
        if not isinstance(changeset, ChangeSet):
            staged_files = [
                f for f in files_touched
                if ignore_policy is None or ignore_policy.should_stage(f)
            ]
            changeset = ChangeSet(
                burst_uid=bundle.get("burst_uid") or f"burst_{event_id}",
                event_id=event_id,
                sequence=event_id,
                files=staged_files,
                effective_files=bundle.get("effective_changes") or list(files_touched),
            )
        else:
            changeset.event_id = event_id
            if ignore_policy is not None:
                changeset.files = [f for f in changeset.files if ignore_policy.should_stage(f)]

        db = state.session_db
        op_id = record_git_op_state(
            conn=db,
            event_id=event_id,
            burst_uid=changeset.burst_uid,
            status=GitState.RUNNING,
            files=changeset.files,
            effective_files=changeset.effective_files,
        )

        try:
            burst_res = state.shadow_git.commit_changeset(
                changeset=changeset,
                summary=summary,
                first_file=first_file,
            )
            update_git_op_state(
                conn=db,
                op_id=op_id,
                status=burst_res.state,
                commit_hash=burst_res.commit_hash,
                error_message=burst_res.error,
            )

            if burst_res.commit_hash:
                state.session_db.record_event(
                    session_id=state.session_id,
                    event_type="GIT",
                    first_file=first_file,
                    summary=f"Micro-commit {burst_res.commit_hash[:7]}: {summary}",
                    commit_hash=burst_res.commit_hash,
                )
                if state.session_db:
                    state.session_db.update_event_git_attribution(
                        event_id, burst_res.commit_hash, burst_res.state
                    )
                logger.info("ShadowGit burst committed | hash=%s", burst_res.commit_hash[:7])
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to create ShadowGit micro-commit (event preserved): %s", exc)
            update_git_op_state(
                conn=db,
                op_id=op_id,
                status=GitState.FAILED,
                error_message=str(exc),
            )

    _emit_json(
        "event_bundled",
        {"event_id": event_id, "first_file": first_file, "files_count": len(files_touched), "files": files_touched, "duration_s": duration_s, "patches_written": len(combined_patch_lines)},
        state=state,
    )

    logger.info(
        "[BURST_SEALED] Event bundled | id=%d | files=%d | entrypoint=%s | patches=%d",
        event_id,
        len(files_touched),
        first_file,
        len(combined_patch_lines),
    )



def trigger_initial_ingestion(state: WorkerState = worker_state) -> None:
    """
    Trigger instant baseline ingestion burst (Burst #1) for all tracked files.
    Only executed on the very first monitoring run of a new project.
    """
    if state.session_db is None or state.diff_calc is None or state.session_id is None:
        return
    cached_text = list(state.diff_calc._cache.keys())
    cached_binary = list(getattr(state.diff_calc, "_binary_files", set()))
    files = sorted(set(cached_text + cached_binary))

    ignore_policy = getattr(state.project_watcher, "ignore_policy", None) or getattr(state.diff_calc, "ignore_policy", None)
    if ignore_policy is not None:
        files = [f for f in files if ignore_policy.should_stage(f)]

    if not files:
        return

    # Populate manifest if available
    manifest = getattr(state.project_watcher, "manifest", None)
    if manifest is not None:
        for f in files:
            manifest.record_file(f)

    logger.info("Triggering initial baseline ingestion for %d file(s)", len(files))
    now_iso = _utcnow_iso()
    bundle = {
        "event_type": "EDIT",
        "files_touched": files,
        "first_file": files[0],
        "duration_s": 0.0,
        "is_initial": True,
        "events": [
            {
                "file_path": f,
                "event_type": "created",
                "first_seen_mono": time.monotonic(),
                "first_seen_iso": now_iso,
                "last_seen_mono": time.monotonic(),
                "edit_count": 1,
            }
            for f in files
        ],
        "started_at": now_iso,
        "flushed_at": now_iso,
    }
    _handle_event_bundle(bundle, state=state)


def _run_heartbeat(interval: float = 2.0, state: WorkerState = worker_state) -> None:
    """
    Main blocking loop: maintain live status and check IPC commands.

    Zero-Idle Logging Policy:
    Heartbeat ticks are NOT written to worker.log to ensure 0 bytes added
    when the IDE sits idle. worker.status.json is updated in-place for live metrics.
    """
    logger.info("Worker monitoring loop started (interval=%.1fs)", interval)
    last_meta_mtime: float = 0.0
    meta_path = (state.pid_file.parent / "project.meta") if state.pid_file is not None else None

    while not state.shutdown_requested:
        _write_status_file(state)

        # Check metadata hot-reload (updates shared configuration object)
        if meta_path is not None and meta_path.exists():
            try:
                mtime = meta_path.stat().st_mtime
                if mtime > last_meta_mtime:
                    last_meta_mtime = mtime
                    if getattr(state, "config", None) is not None:
                        changed = state.config.reload_from_meta(meta_path)
                        bundler = state.burst_assembler or state.event_bundler
                        if changed and bundler is not None:
                            bundler.quiet_period = state.config.debounce_s
                            bundler.configured_quiet_period = state.config.debounce_s
                            bundler.max_burst_s = state.config.max_burst_s
                            logger.info(
                                "[HOT_RELOAD] Updated configuration for project '%s' (debounce=%.2fs, max_burst=%.2fs)",
                                state.project_name,
                                state.config.debounce_s,
                                state.config.max_burst_s,
                            )
                    else:
                        from .project_config import parse_meta_text
                        meta_data = parse_meta_text(meta_path.read_text(encoding="utf-8"))
                        new_db_val = meta_data.get("debounce_window") or meta_data.get("debounce")
                        if new_db_val is not None:
                            new_db = float(new_db_val)
                            bundler = state.burst_assembler or state.event_bundler
                            if bundler is not None:
                                curr_db = getattr(bundler, "quiet_period", None)
                                if curr_db is not None and abs(curr_db - new_db) > 1e-4:
                                    bundler.quiet_period = new_db
                                    bundler.configured_quiet_period = new_db
                                    logger.info(
                                        "[HOT_RELOAD] Updated debounce quiet period to %.2fs for project '%s'",
                                        new_db,
                                        state.project_name,
                                    )
            except Exception as e_reload:
                logger.debug("Metadata hot-reload check skipped: %s", e_reload)

        elapsed = 0.0
        while elapsed < interval and not state.shutdown_requested and not (state.stop_event.is_set() if hasattr(state, "stop_event") else False):
            if state.pid_file is not None:
                ctrl_cmd = state.pid_file.parent / "worker.control"
                if ctrl_cmd.exists():
                    try:
                        content = ctrl_cmd.read_text(encoding="utf-8")
                        ctrl_cmd.unlink()
                    except Exception:
                        content = "STOP"
                    if "STOP" in content.upper():
                        logger.info("Received worker control STOP command via IPC")
                        state.shutdown_requested = True
                        if hasattr(state, "stop_event"):
                            state.stop_event.set()
                        break

                stop_cmd = state.pid_file.parent / "stop.cmd"
                if stop_cmd.exists():
                    try:
                        stop_cmd.unlink()
                    except Exception:
                        pass
                    logger.info("Received stop.cmd fallback command")
                    state.shutdown_requested = True
                    if hasattr(state, "stop_event"):
                        state.stop_event.set()
                    break

                seal_cmd = state.pid_file.parent / "seal_burst.cmd"
                if seal_cmd.exists():
                    try:
                        seal_cmd.unlink()
                    except Exception:
                        pass
                    bundler = state.burst_assembler or state.event_bundler
                    if bundler is not None:
                        bundler.flush_burst_now()
                        _write_status_file(state)
            time.sleep(0.1)
            elapsed += 0.1
