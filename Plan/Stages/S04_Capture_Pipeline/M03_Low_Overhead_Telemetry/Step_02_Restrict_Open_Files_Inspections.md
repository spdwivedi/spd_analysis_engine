Step_02: Restrict Open Files Inspections
ID: S04-M03-P01-Step02

Status: DONE

Depends on: S04-M03-P01-Step01

1. Purpose
Restrict expensive open_files() inspection calls to allowlisted IDE/editor processes at reduced frequency.

2. Scope
spd_analysis_engine/scripts/watcher_proc.py

3. Verification (Done when...)
Unrelated background processes are never probed with open_files() calls.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/watcher_proc.py, scripts/shell/tracker.py).
- **Test Verification:** Covered by passing test suite (tests/test_process_telemetry_redesign.py).
