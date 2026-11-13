Step_01: Decouple Adaptive Process Scanner
ID: S04-M03-P01-Step01

Status: DONE

Depends on: S04-M02-P01-Step05

1. Purpose
Decouple process telemetry scanner so that telemetry delays never block event persistence.

2. Scope
spd_analysis_engine/scripts/watcher_proc.py, spd_analysis_engine/scripts/execution/change_processor.py

3. Verification (Done when...)
Simulated telemetry slowdowns do not delay BurstAssembler sealing or SQLite event commits.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/watcher_proc.py, scripts/shell/tracker.py).
- **Test Verification:** Covered by passing test suite (tests/test_process_telemetry_redesign.py).
