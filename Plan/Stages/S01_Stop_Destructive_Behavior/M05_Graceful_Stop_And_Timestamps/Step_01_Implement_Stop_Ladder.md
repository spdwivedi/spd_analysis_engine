Step_01: Implement Stop Ladder
ID: S01-M05-P01-Step01

Status: DONE

Depends on: S01-M04-P01-Step03

1. Purpose
Implement staged shutdown: Worker control STOP signal -> 20s grace period -> stop.cmd fallback -> SIGTERM -> SIGKILL.

2. Scope
spd_analysis_engine/scripts/execution/supervisor.py, spd_analysis_engine/scripts/execution/worker_lifecycle.py

3. Verification (Done when...)
Worker processes shut down cleanly within grace period; unresponsive workers are escalated and terminated.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/worker_lifecycle.py, scripts/core/time_utils.py).
- **Test Verification:** Covered by passing test suite (tests/test_safe_worker_shutdown.py).
