# Step 05 Wire Modern Controller And Single Writer
ID: S09-M01-P0-Step05
Status: DONE

## 1. Objective & Scope
B-5: Wire LifecycleController, ReconcilerDaemon, and ProjectWriter into production entry points; unify root and data/ engine.db paths per ADR-020.

- **Target Files:** `start.py, scripts/server/server_daemon.py, scripts/core/supervisor.py, scripts/storage/writer.py`
- **Test Verification:** `tests/test_authoritative_lifecycle.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_authoritative_lifecycle.py`.
- Syntax verification passes: `python -m py_compile start.py`.

## 3. Execution Summary
- **Implementation:**
  - `start.py`: In `cmd_ui`, instantiated and bound authoritative `LifecycleController` and started `ReconcilerDaemon` with clean shutdown on SIGINT.
  - `scripts/core/supervisor.py`: Bound `LifecycleController` to `Supervisor` and `StorageRotator.controller`, delegated `reap_dead_workers()` to `LifecycleController.reconcile()`, wired `start_reaper()` to start `ReconcilerDaemon`, and cleanly stopped it in `shutdown()`. Maintained 495 lines <= 500 lines.
  - `scripts/server/server_daemon.py`: Exposed `controller` property and wired `ai_db` to the canonical `controller.db_path` (`data/engine.db`).
  - `scripts/controller/controller.py`: Canonicalized `engine.db` path to `self.engine_root / "data" / "engine.db"` per ADR-020.
  - `scripts/storage/writer.py`: Remediated F-2.4 shutdown races in `ProjectWriter`: fixed `submit_critical`/`submit_best_effort` stop guard (`_is_stopping or _stop_event`), bounded `drain()` wait on `unfinished_tasks`, and prevented post-join checkpoints while the writer thread is still active.
  - `scripts/ai/controller_db.py` & `scripts/controller/jobs.py`: Unified schema harmonization for `jobs` and `ai_analyses` tables in `data/engine.db` with safe column filtering.
- **Test Verification:**
  - Added `test_production_wiring_unified_engine_db_and_reconciler_daemon` to `tests/test_authoritative_lifecycle.py`.
  - Ran `python -m unittest tests.test_authoritative_lifecycle tests.test_single_writer_architecture -v` (15/15 tests passed in 5.8s).
  - Verified `python -m py_compile start.py` compiles cleanly.

