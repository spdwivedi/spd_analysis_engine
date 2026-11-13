Step_03: Replace Substring Process Scanning
ID: S01-M06-P01-Step03

Status: DONE

Depends on: S01-M06-P01-Step02

1. Purpose
Replace naive command line substring searching in process sweepers with exact attribute matching.

2. Scope
spd_analysis_engine/scripts/execution/process_sweeper.py

3. Verification (Done when...)
Sweeper never terminates collateral processes whose names contain the target project string.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/spawner.py, scripts/core/worker_helpers.py).
- **Test Verification:** Covered by passing test suite (tests/test_worker_identity_propagation.py).
