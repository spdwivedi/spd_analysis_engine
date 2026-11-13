Step_03: Disable Auto Synthesize on Reads
ID: S01-M02-P01-Step03

Status: DONE

Depends on: S01-M02-P01-Step02

1. Purpose
Enforce auto_synthesize=False for all ordinary read paths and isolate metadata repair behind explicit administrative endpoints.

2. Scope
spd_analysis_engine/scripts/storage/meta.py, spd_analysis_engine/scripts/server/routes_projects.py

3. Verification (Done when...)
Reading a project with missing metadata returns 404 or partial data without creating synthetic project.meta.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py, scripts/server/db_queries.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
