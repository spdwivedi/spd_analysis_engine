Step_04: Correct IST Heartbeat Parsing
ID: S00-M03-P01-Step04

Status: DONE

Depends on: S00-M03-P01-Step03

1. Purpose
Write a RED regression test proving that heartbeat timestamps recorded with IST (+05:30) offsets are not erroneously evaluated as UTC Z timestamps.

2. Scope
spd_analysis_engine/tests/test_heartbeat_timezones.py

3. Verification (Done when...)
Test asserts heartbeat age calculation yields accurate elapsed seconds regardless of local offset formatting.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/time_utils.py).
- **Test Verification:** Covered by passing test suite (tests/test_safe_worker_shutdown.py).
