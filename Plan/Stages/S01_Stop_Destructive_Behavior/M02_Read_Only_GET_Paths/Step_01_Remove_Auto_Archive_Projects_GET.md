Step_01: Remove Auto Archive from Projects GET
ID: S01-M02-P01-Step01

Status: DONE

Depends on: S01-M01-P01-Step03

1. Purpose
Remove automatic project archiving side-effects from the GET /api/projects handler.

2. Scope
spd_analysis_engine/scripts/server/routes_projects.py, spd_analysis_engine/scripts/web_server.py

3. Verification (Done when...)
GET /api/projects returns project list without modifying directories, moving files, or writing status.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/routes_events.py, scripts/server/db_queries.py).
- **Test Verification:** Covered by passing test suite (tests/test_read_idempotency.py).
