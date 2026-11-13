Step_02: Remove Consolidate History from Events GET
ID: S01-M02-P01-Step02

Status: DONE

Depends on: S01-M02-P01-Step01

1. Purpose
Remove consolidate_project_history() and DDL/checkpoint executions from ordinary event retrieval routes.

2. Scope
spd_analysis_engine/scripts/server/routes_events.py, spd_analysis_engine/scripts/storage/history.py

3. Verification (Done when...)
GET requests for events execute pure SELECT queries with zero schema changes or WAL truncations.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py, scripts/server/db_queries.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
