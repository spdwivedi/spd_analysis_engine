"""
scripts/core/worker_bundle.py
=============================
Burst event bundle processing, patch/diff persistence, and zero-idle heartbeat loop.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

try:
    from .worker_lifecycle import WorkerState, worker_state, _write_status_file, _emit_json
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.worker_lifecycle import (
            WorkerState,
            worker_state,
            _write_status_file,
            _emit_json,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.core.worker_lifecycle import (
            WorkerState,
            worker_state,
            _write_status_file,
            _emit_json,
        )

logger = logging.getLogger(__name__)


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

    for rel_path in files_touched:
        try:
            diff_text = state.diff_calc.compute_diff(rel_path)
            file_diffs.append((rel_path, diff_text))

            abs_path = target_root / Path(rel_path)
            if abs_path.is_file():
                try:
                    curr_content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    curr_content = None
            else:
                curr_content = None

            state.diff_calc.update_baseline(rel_path, curr_content)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error computing diff / updating baseline for %s: %s", rel_path, exc)

    summary = f"Burst edit across {len(files_touched)} file(s) ({duration_s:.1f}s)"
    try:
        event_id = state.session_db.record_event(
            session_id=state.session_id,
            event_type="EDIT",
            first_file=first_file,
            summary=summary,
        )
        if state.event_bundler:
            state.event_bundler.record_action(
                "BURST",
                first_file or "workspace",
                details=f"Burst #{event_id} ({len(files_touched)} files)",
            )
            _write_status_file(state)
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

    if state.shadow_git is not None:
        try:
            commit_hash = state.shadow_git.commit_burst(
                event_id=event_id,
                first_file=first_file,
                summary=summary,
                files_touched=files_touched,
            )
            if commit_hash:
                state.session_db.record_event(
                    session_id=state.session_id,
                    event_type="GIT",
                    first_file=first_file,
                    summary=f"Micro-commit {commit_hash[:7]}: {summary}",
                )
                logger.info("ShadowGit burst committed | hash=%s", commit_hash[:7])

                for rel_p in files_touched:
                    try:
                        delta = state.shadow_git.get_delta_diff(rel_p, commit_hash=commit_hash)
                        if delta:
                            state.session_db.update_patch_diff(event_id, rel_p, delta)
                    except Exception as e_delta:
                        logger.debug("Could not compute delta diff for %s: %s", rel_p, e_delta)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to create ShadowGit micro-commit: %s", exc)

    _emit_json(
        "event_bundled",
        {
            "event_id": event_id,
            "first_file": first_file,
            "files_count": len(files_touched),
            "files": files_touched,
            "duration_s": duration_s,
            "patches_written": len(combined_patch_lines),
        },
        state=state,
    )

    logger.info(
        "[BURST_SEALED] Event bundled | id=%d | files=%d | entrypoint=%s | patches=%d",
        event_id,
        len(files_touched),
        first_file,
        len(combined_patch_lines),
    )


def _run_heartbeat(interval: float = 2.0, state: WorkerState = worker_state) -> None:
    """
    Main blocking loop: maintain live status and check IPC commands.

    Zero-Idle Logging Policy:
    Heartbeat ticks are NOT written to worker.log to ensure 0 bytes added
    when the IDE sits idle. worker.status.json is updated in-place for live metrics.
    """
    logger.info("Worker monitoring loop started (interval=%.1fs)", interval)
    while not state.shutdown_requested:
        _write_status_file(state)

        elapsed = 0.0
        while elapsed < interval and not state.shutdown_requested:
            if state.pid_file is not None:
                seal_cmd = state.pid_file.parent / "seal_burst.cmd"
                if seal_cmd.exists():
                    try:
                        seal_cmd.unlink()
                    except Exception:
                        pass
                    if state.event_bundler is not None:
                        state.event_bundler.flush_burst_now()
                        _write_status_file(state)
            time.sleep(0.1)
            elapsed += 0.1
