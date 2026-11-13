Step_03: Fetch Diffs On Demand
ID: S06-M02-P01-Step03

Status: DONE

Depends on: S06-M02-P01-Step02

1. Purpose
Fetch file diffs on-demand only when a user expands a specific file accordion or item in the diff viewer.

2. Scope
spd_analysis_engine/web/js/views/diff_viewer.js

3. Verification (Done when...)
Diff API endpoint is queried exclusively on user accordion click.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/web/js/api/events.js, web/js/components/windowed_list.js).
- **Test Verification:** Covered by passing test suite (tests/test_frontend_incremental.py).
