Step_04: Prevent Context Directory Synthesis
ID: S01-M02-P01-Step04

Status: DONE

Depends on: S01-M02-P01-Step03

1. Purpose
Prevent resolve_project_context() from synthesizing current/<slug>/ directories during query evaluation.

2. Scope
spd_analysis_engine/scripts/storage/project_storage.py, spd_analysis_engine/scripts/execution/scaffolder.py

3. Verification (Done when...)
Inspecting historical projects never creates new folders inside current/.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py, scripts/server/db_queries.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
