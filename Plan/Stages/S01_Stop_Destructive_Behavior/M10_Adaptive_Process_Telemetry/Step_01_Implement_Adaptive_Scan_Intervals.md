Step_01: Implement Adaptive Scan Intervals
ID: S01-M10-P01-Step01

Status: DONE

Depends on: S01-M09-P01-Step02

1. Purpose
Replace aggressive 0.18s polling with adaptive scan intervals (0.5s to 2.0s+) scaling dynamically with scan duration.

2. Scope
spd_analysis_engine/scripts/watcher_proc.py, spd_analysis_engine/scripts/telemetry/tracker.py

3. Verification (Done when...)
Idle CPU consumption drops below 2%, and scan interval increases dynamically under heavy system load.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/shell/tracker.py, scripts/watcher_proc.py).
- **Test Verification:** Covered by passing test suite (tests/test_process_telemetry_safety.py).
