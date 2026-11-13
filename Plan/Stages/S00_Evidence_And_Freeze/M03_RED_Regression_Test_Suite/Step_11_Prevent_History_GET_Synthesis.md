Step_11: Prevent History GET Directory Synthesis
ID: S00-M03-P01-Step11

Status: DONE

Depends on: S00-M03-P01-Step10

1. Purpose
Write a RED regression test proving that querying a history-only project via GET /api/project/<slug> does not create a phantom current/<slug>/ directory.

2. Scope
spd_analysis_engine/tests/test_history_query_isolation.py

3. Verification (Done when...)
Test asserts current/<slug>/ does not exist after repeated read queries to historical project records.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
