Step_01: Deprecate Obsolete Modules
ID: S08-M01-P01-Step01

Status: DONE

Depends on: S07-M02-P01-Step04

1. Purpose
Deprecate and remove obsolete timer bundlers, duplicate suppression logic, zombie sweepers, and synthesis mixins.

2. Scope
spd_analysis_engine/scripts/event_bundler.py, spd_analysis_engine/scripts/execution/process_sweeper.py

3. Verification (Done when...)
Obsolete modules are archived or replaced with compatibility stubs issuing deprecation warnings.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/reaper.py, scripts/server/routes_health.py).
- **Test Verification:** Covered by passing test suite (tests/test_v12_health_and_diagnostics.py).
