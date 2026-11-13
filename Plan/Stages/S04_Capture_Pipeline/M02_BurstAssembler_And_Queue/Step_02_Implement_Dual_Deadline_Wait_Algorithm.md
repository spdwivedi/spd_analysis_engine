Step_02: Implement Dual Deadline Wait Algorithm
ID: S04-M02-P01-Step02

Status: DONE

Depends on: S04-M02-P01-Step01

1. Purpose
Implement dual-deadline wait calculation: due = min(last_event + quiet_period, first_event + max_burst).

2. Scope
spd_analysis_engine/scripts/execution/burst_assembler.py

3. Verification (Done when...)
Burst seals on quiet period during sparse activity and seals on max_burst ceiling under continuous editing.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/burst_assembler.py, scripts/core/change_processor.py).
- **Test Verification:** Covered by passing test suite (tests/test_burst_assembler_and_processor.py).
