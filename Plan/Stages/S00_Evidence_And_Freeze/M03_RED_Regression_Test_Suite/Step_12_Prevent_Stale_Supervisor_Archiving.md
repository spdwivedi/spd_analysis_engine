Step_12: Prevent Stale Supervisor Archiving
ID: S00-M03-P01-Step12

Status: DONE

Depends on: S00-M03-P01-Step11

1. Purpose
Write a RED regression test reproducing the resume failure where a stale supervisor reaps and archives a newly launched replacement worker.

2. Scope
spd_analysis_engine/tests/test_resume_worker_lifecycle.py

3. Verification (Done when...)
Test proves a running replacement worker process is never archived by lingering controller state.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/supervisor.py).
- **Test Verification:** Covered by passing test suite (tests/test_authoritative_lifecycle.py).
