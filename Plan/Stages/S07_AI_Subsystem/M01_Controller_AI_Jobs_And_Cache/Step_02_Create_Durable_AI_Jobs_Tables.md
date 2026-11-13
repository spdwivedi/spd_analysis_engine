Step_02: Create Durable AI Jobs Tables
ID: S07-M01-P01-Step02

Status: DONE

Depends on: S07-M01-P01-Step01

1. Purpose
Create durable jobs and ai_analyses tables in engine.db with execution lease and status tracking.

2. Scope
spd_analysis_engine/scripts/ai/controller_db.py

3. Verification (Done when...)
AI analysis tasks persist in SQLite and survive engine restarts without loss.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/ai/controller_executor.py, scripts/ai/cache_manager.py).
- **Test Verification:** Covered by passing test suite (tests/test_controller_ai_execution.py).
