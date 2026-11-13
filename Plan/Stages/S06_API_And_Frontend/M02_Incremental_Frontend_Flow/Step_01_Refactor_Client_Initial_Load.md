Step_01: Refactor Client Initial Load
ID: S06-M02-P01-Step01

Status: DONE

Depends on: S06-M01-P01-Step05

1. Purpose
Refactor frontend initialization: load project summary, fetch recent event summaries, and establish SSE stream.

2. Scope
spd_analysis_engine/web/js/app.js, spd_analysis_engine/web/js/views/timeline.js

3. Verification (Done when...)
Initial page load finishes quickly without requesting full diffs for all historical bursts.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/web/js/api/events.js, web/js/components/windowed_list.js).
- **Test Verification:** Covered by passing test suite (tests/test_frontend_incremental.py).
