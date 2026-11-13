Step_02: Enforce Config Range Constraints
ID: S01-M04-P01-Step02

Status: DONE

Depends on: S01-M04-P01-Step01

1. Purpose
Enforce constraints: 0.5 <= debounce_s <= 3600 and max_burst_s >= debounce_s across all config consumers.

2. Scope
spd_analysis_engine/scripts/execution/project_config.py, spd_analysis_engine/scripts/event_bundler.py

3. Verification (Done when...)
Values outside boundaries raise descriptive ValidationError exceptions with fallback to defaults.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/project_config.py).
- **Test Verification:** Covered by passing test suite (tests/test_project_config.py).
