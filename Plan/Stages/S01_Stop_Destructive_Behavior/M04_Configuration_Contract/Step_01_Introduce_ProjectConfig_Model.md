Step_01: Introduce ProjectConfig Model
ID: S01-M04-P01-Step01

Status: DONE

Depends on: S01-M03-P01-Step03

1. Purpose
Create a validated ProjectConfig dataclass standardizing debounce_s (3.5 default) and max_burst_s (60.0 default).

2. Scope
spd_analysis_engine/scripts/execution/project_config.py

3. Verification (Done when...)
ProjectConfig instantiates with validated defaults and rejects invalid types or negative intervals.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/project_config.py).
- **Test Verification:** Covered by passing test suite (tests/test_project_config.py).
