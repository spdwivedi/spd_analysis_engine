Step_02: Add Max Burst Limit
ID: S01-M09-P01-Step02

Status: DONE

Depends on: S01-M09-P01-Step01

1. Purpose
Enforce a 60-second maximum burst ceiling to force bundle sealing even under continuous, non-stop edits.

2. Scope
spd_analysis_engine/scripts/event_bundler.py

3. Verification (Done when...)
Continuous event stream triggers a sealed burst at 60 seconds without waiting for quiet period.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/event_bundler.py).
- **Test Verification:** Covered by passing test suite (tests/test_event_bundler_safety.py).
