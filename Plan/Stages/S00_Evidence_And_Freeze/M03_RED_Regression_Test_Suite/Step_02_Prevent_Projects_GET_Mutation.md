Step_02: Prevent GET Projects State Mutation
ID: S00-M03-P01-Step02

Status: DONE

Depends on: S00-M03-P01-Step01

1. Purpose
Write a RED regression test verifying that GET /api/projects does not perform auto-archiving or disk mutations.

2. Scope
spd_analysis_engine/tests/test_api_projects_readonly.py

3. Verification (Done when...)
Test asserts directory hashes/mtimes remain identical before and after executing GET /api/projects.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
