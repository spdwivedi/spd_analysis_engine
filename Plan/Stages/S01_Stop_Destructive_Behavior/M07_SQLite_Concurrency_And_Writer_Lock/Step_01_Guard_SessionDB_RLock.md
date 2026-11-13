Step_01: Guard SessionDB RLock
ID: S01-M07-P01-Step01

Status: DONE

Depends on: S01-M06-P01-Step03

1. Purpose
Protect shared SessionDB connections with an RLock and add transactional context managers to prevent concurrent write collisions.

2. Scope
spd_analysis_engine/scripts/storage/db.py

3. Verification (Done when...)
Concurrent multithreaded test executes 1,000 writes across 8 threads without sqlite3.OperationalError: database locked.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/storage/db.py).
- **Test Verification:** Covered by passing test suite (tests/test_session_db_concurrency.py).
