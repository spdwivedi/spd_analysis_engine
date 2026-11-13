Step_03: Enforce Single ChangeProcessor Consumer
ID: S04-M02-P01-Step03

Status: DONE

Depends on: S04-M02-P01-Step02

1. Purpose
Ensure each project has exactly one ordered ChangeProcessor consumer to prevent overlapping burst processing.

2. Scope
spd_analysis_engine/scripts/execution/change_processor.py

3. Verification (Done when...)
Bursts N and N+1 are strictly serialized; parallel burst processing within a project is impossible.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/burst_assembler.py, scripts/core/change_processor.py).
- **Test Verification:** Covered by passing test suite (tests/test_burst_assembler_and_processor.py).
