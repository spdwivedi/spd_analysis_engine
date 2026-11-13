Step_02: Mark Escalated Stops Interrupted
ID: S01-M05-P01-Step02

Status: DONE

Depends on: S01-M05-P01-Step01

1. Purpose
Ensure workers stopped via forced termination or kill signals are marked INTERRUPTED instead of cleanly stopped.

2. Scope
spd_analysis_engine/scripts/execution/worker_lifecycle.py, spd_analysis_engine/scripts/storage/history.py

3. Verification (Done when...)
Session status reflects INTERRUPTED on forced kill and preserves uncommitted transaction records.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/worker_lifecycle.py, scripts/core/time_utils.py).
- **Test Verification:** Covered by passing test suite (tests/test_safe_worker_shutdown.py).
