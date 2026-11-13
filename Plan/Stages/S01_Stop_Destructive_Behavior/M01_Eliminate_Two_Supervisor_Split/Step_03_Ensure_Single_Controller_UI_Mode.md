Step_03: Ensure Single Controller in UI Mode
ID: S01-M01-P01-Step03

Status: DONE

Depends on: S01-M01-P01-Step02

1. Purpose
Refactor start.py ui to initialize exactly one controller instance and coordinate clean server shutdown.

2. Scope
spd_analysis_engine/start.py

3. Verification (Done when...)
Starting and stopping UI mode creates exactly one controller and reaps all child worker processes cleanly.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/supervisor.py, scripts/controller/controller.py).
- **Test Verification:** Covered by passing test suite (tests/test_authoritative_lifecycle.py).
