# Step 03 Fix Rollback IPC Bridge
ID: S09-M01-P0-Step03
Status: DONE

## 1. Objective & Scope
B-3: Fix broken capture-pause bridge: replace missing send_control_command with send_worker_command, fix SEAL to call flush_now(), and add pause/resume to ProjectWatcher.

- **Target Files:** `scripts/storage/rollback.py, scripts/project_worker.py, scripts/watcher_fs.py`
- **Test Verification:** `tests/test_rollback_and_autopush.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_rollback_and_autopush.py`.
- Syntax verification passes: `python -m py_compile scripts/storage/rollback.py`.

## 3. Execution Summary
- **Implementation:** Implemented `send_control_command(engine_root, slug, command)` wrapper in `scripts/controller/control_channel.py` resolving worker control info and dispatching through `send_worker_command`. Replaced bare exception swallows in `scripts/storage/rollback.py` with debug logging and verified worker IPC integration. In `scripts/project_worker.py`, fixed `SEAL` command handler to call `flush_now()` on `BurstAssembler`, and wired `PAUSE`/`RESUME` across both watcher and change processor. Added thread-safe `pause()` and `resume()` methods and `_paused` gating to `ProjectWatcher` and `_WatchdogEventHandler` in `scripts/watcher_fs.py`, and added `pause()` and `resume()` to `ChangeProcessor` in `scripts/core/change_processor.py`. All target files remain `<= 500 lines` (`control_channel.py`: 354, `project_worker.py`: 197, `watcher_fs.py`: 298, `change_processor.py`: 346, `rollback.py`: 479).
- **Test Verification:** Added `test_10_rollback_ipc_bridge_and_watcher_pause_resume` to `tests/test_rollback_and_autopush.py` validating that watcher/processor pause suppresses file capture, and that `send_control_command` delivers `PAUSE`, `SEAL`, and `RESUME` during rollback. All tests in `test_rollback_and_autopush.py`, `test_v9_crash_rollback_and_lap.py`, and `test_v10_physical_rollback_and_selective_trash.py` passed with 0 failures.

