Step_05: Throttle Polling On Hidden Tab
ID: S06-M02-P01-Step05

Status: DONE

Depends on: S06-M02-P01-Step04

1. Purpose
Throttle or pause background polling when document.hidden === true to conserve CPU and network resources.

2. Scope
spd_analysis_engine/web/js/eco_mode.js, spd_analysis_engine/web/js/stream.js

3. Verification (Done when...)
Background tabs reduce API poll rates to idle intervals.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/web/js/api/events.js, web/js/components/windowed_list.js).
- **Test Verification:** Covered by passing test suite (tests/test_frontend_incremental.py).
