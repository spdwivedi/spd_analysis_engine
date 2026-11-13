Step_03: Implement Unified UTC Utility
ID: S01-M05-P01-Step03

Status: DONE

Depends on: S01-M05-P01-Step02

1. Purpose
Standardize timestamp generation on utc_now_iso() and store UTC ISO 8601 strings in all databases and logs.

2. Scope
spd_analysis_engine/scripts/execution/time_utils.py, spd_analysis_engine/scripts/storage/db.py

3. Verification (Done when...)
All persisted dates end with Z or +00:00, and heartbeat delta comparisons evaluate correctly across time zones.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/worker_lifecycle.py, scripts/core/time_utils.py).
- **Test Verification:** Covered by passing test suite (tests/test_safe_worker_shutdown.py).
